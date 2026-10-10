#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_mcp.py — 麦当劳 MCP Server 能力探测（只读）

用途：
  在开发期一次性拉取官方 MCP 的 tools/list 真实 schema 与若干只读工具的真实返回，
  用于建立「能力映射」，避免按工具名臆造参数。

安全约束：
  - Token 只从环境变量 MAIMAI_TOKEN 或 ~/.maimai-helper/.token 读取
  - 本脚本源码、日志、输出均不含 Token
  - 默认只跑只读工具；写操作需显式 --allow-write 且本脚本仍会二次拦截

用法：
  python probe_mcp.py --list-tools
  python probe_mcp.py --call now-time-info
  python probe_mcp.py --readonly-batch          # 一键跑只读样本
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

MCP_URL = "https://mcp.mcd.cn"
PROTO = "2024-11-05"

# 写操作黑名单：即使传了 --allow-write 也拒绝，防止探测脚本误下单
WRITE_TOOLS = {
    "auto-bind-coupons", "mall-create-order", "draw-lottery",
    "create-order", "cancel-order", "party-order-create",
    "delivery-create-address",
}

# 只读样本：覆盖六个模块，尽量拿到真实返回结构
READONLY_BATCH = [
    "now-time-info",
    "list-nutrition-foods",
    "available-coupons",
    "query-my-coupons",
    "query-my-account",
    "campaign-calendar",
    "mall-points-products",
    "mall-order-list",
    "query-lottery-info",
    "query-my-prizes",
    "order-list",
    "query-party-city",
]


def load_token() -> str:
    tok = os.environ.get("MAIMAI_TOKEN") or os.environ.get("MCD_MCP_TOKEN")
    if tok:
        return tok.strip()
    p = Path.home() / ".maimai-helper" / ".token"
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    sys.exit("未找到 Token：请设置环境变量 MAIMAI_TOKEN，或写入 ~/.maimai-helper/.token")


class StreamableClient:
    """最小 Streamable HTTP MCP 客户端（标准库实现）。"""

    def __init__(self, url: str, token: str, timeout: int = 60):
        self.url = url
        self.token = token
        self.timeout = timeout
        self.session_id: Optional[str] = None
        self._id = 0

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _headers(self) -> Dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
            "MCP-Protocol-Version": PROTO,
            "User-Agent": "maimai-life-helper-probe/1.0",
        }
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
                if sid:
                    self.session_id = sid
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"HTTP {e.code}: {detail}") from None
        except urllib.error.URLError as e:
            raise RuntimeError(f"网络错误: {e.reason}") from None

        return self._parse(raw)

    @staticmethod
    def _parse(raw: str) -> Dict[str, Any]:
        raw = raw.strip()
        if not raw:
            return {}
        # SSE 格式
        if raw.startswith("event:") or raw.startswith("data:"):
            for line in raw.splitlines():
                if line.startswith("data:"):
                    chunk = line[5:].strip()
                    if chunk and chunk != "[DONE]":
                        try:
                            return json.loads(chunk)
                        except json.JSONDecodeError:
                            continue
            return {"_raw_sse": raw[:2000]}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw[:2000]}

    def initialize(self) -> Dict[str, Any]:
        res = self._post({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
            "params": {
                "protocolVersion": PROTO,
                "capabilities": {},
                "clientInfo": {"name": "maimai-life-helper-probe", "version": "1.0.0"},
            },
        })
        # 通知 initialized
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        except Exception:
            pass
        return res

    def list_tools(self) -> Dict[str, Any]:
        return self._post({"jsonrpc": "2.0", "id": self._next_id(),
                           "method": "tools/list", "params": {}})

    def call(self, name: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # 第二道防线：即使绕过 main() 的前置检查，这里仍会拦下写工具
        if name in WRITE_TOOLS:
            raise RuntimeError(
                f"探测脚本禁止调用写工具: {name}（下单/兑换/抽奖/领券/取消/预约）"
            )
        return self._post({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
            "params": {"name": name, "arguments": args or {}},
        })

    def call_confirmed(self, name: str, args: Dict[str, Any],
                       op_key: str) -> Dict[str, Any]:
        """写工具专用通道：必须携带已过幂等闸门的 op_key。

        为什么需要这个通道（2026-10-10 实测）：
          工具调用层会把 items 数组反复包成双层 [[{...}]]，
          导致 create-order 的 schema 校验始终报 "must be array"，
          该问题在客户端侧无法通过改写参数解决。
          脚本直连可以精确控制 JSON 结构。

        三重安全约束：
          1. name 必须在 WRITE_TOOLS 白名单内（不存在"任意工具都能调"的口子）
          2. 必须传入 op_key，且该 op_key 必须已在账本中处于 pending 状态
             —— 即已经走过 mm_state.py ledger-begin 的幂等闸门
          3. 只能调用一次，账本状态由调用方在拿到结果后 ledger-finish 收尾
        """
        if name not in WRITE_TOOLS:
            raise RuntimeError(
                f"call_confirmed 只允许调用写工具，收到: {name}"
            )
        import mm_state as _st

        entry = _st.get_operation(name, op_key)
        if not entry:
            raise RuntimeError(
                f"账本中找不到 op_key={op_key}，请先执行 ledger-begin --kind {name}"
            )
        if entry.get("status") != "pending":
            raise RuntimeError(
                f"账本状态为 {entry.get('status')}，只有 pending 才允许执行。"
                f"（confirmed 说明已执行过，重复调用会造成重复下单）"
            )

        return self._post({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
            "params": {"name": name, "arguments": args},
        })


def unwrap(res: Dict[str, Any]) -> Any:
    """把 MCP content 数组还原成 Python 对象。"""
    if not isinstance(res, dict):
        return res
    if "error" in res:
        return {"_error": res["error"]}
    r = res.get("result", res)
    if isinstance(r, dict) and isinstance(r.get("content"), list):
        texts = []
        for c in r["content"]:
            if isinstance(c, dict) and c.get("type") == "text":
                texts.append(c.get("text", ""))
        merged = "\n".join(texts)
        try:
            return json.loads(merged)
        except json.JSONDecodeError:
            return {"_text": merged[:4000], "_isError": r.get("isError", False)}
    return r


def shape_of(obj: Any, depth: int = 0, max_depth: int = 4) -> Any:
    """提取返回结构骨架（键名 + 类型），不保留全部业务数据。"""
    if depth > max_depth:
        return "..."
    if isinstance(obj, dict):
        return {k: shape_of(v, depth + 1, max_depth) for k, v in list(obj.items())[:40]}
    if isinstance(obj, list):
        return [shape_of(obj[0], depth + 1, max_depth)] if obj else []
    return type(obj).__name__


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-tools", action="store_true")
    ap.add_argument("--list-tools-md", help="把工具清单写成 Markdown 文件")
    ap.add_argument("--call", help="调用一个只读工具")
    ap.add_argument("--args", default="{}", help="调用参数 JSON")
    ap.add_argument("--readonly-batch", action="store_true")
    ap.add_argument("--shape-only", action="store_true", help="只输出结构骨架")
    ap.add_argument("--out", help="把结果写入文件")
    a = ap.parse_args()

    # 【安全】黑名单必须在建立连接之前检查。
    # 早先版本在握手之后才校验，导致误传写工具名时虽最终会被拒，
    # 但已经发出了网络请求 —— 属于防护时机错误，这里前置为硬拦截。
    if a.call and a.call in WRITE_TOOLS:
        sys.exit(f"拒绝：{a.call} 属写工具，探测脚本在任何网络请求发出前即拒绝调用。")

    cli = StreamableClient(MCP_URL, load_token())
    init = cli.initialize()
    srv = ((init.get("result") or {}).get("serverInfo") or {}) if isinstance(init, dict) else {}
    out: Dict[str, Any] = {"serverInfo": srv,
                           "protocolVersion": (init.get("result") or {}).get("protocolVersion")
                           if isinstance(init, dict) else None}

    if a.list_tools or a.list_tools_md:
        tools = (cli.list_tools().get("result") or {}).get("tools", [])
        out["tool_count"] = len(tools)
        out["tools"] = tools
        if a.list_tools_md:
            md = ["# 麦当劳 MCP Server 实测工具清单", "",
                  f"- 服务端: `{srv.get('name', '?')}` v{srv.get('version', '?')}",
                  f"- 协议版本: `{out['protocolVersion']}`",
                  f"- 工具总数: **{len(tools)}**", "",
                  "> 本清单由 `probe_mcp.py --list-tools-md` 从 `https://mcp.mcd.cn` 实测拉取，"
                  "非人工誊抄。", "", "| # | 工具名 | 描述 | 入参 |", "|---|---|---|---|"]
            for i, t in enumerate(tools, 1):
                desc = (t.get("description") or "").replace("\n", " ").strip()
                schema = t.get("inputSchema") or {}
                props = schema.get("properties") or {}
                req = set(schema.get("required") or [])
                if props:
                    sig = ", ".join(
                        f"`{k}`{'*' if k in req else ''}:{v.get('type', '?')}"
                        for k, v in props.items())
                else:
                    sig = "无入参"
                md.append(f"| {i} | `{t.get('name')}` | {desc} | {sig} |")
            Path(a.list_tools_md).write_text("\n".join(md), encoding="utf-8")
            print(f"[ok] 工具清单已写入 {a.list_tools_md}（{len(tools)} 个工具）")

    if a.call:
        name = a.call
        if name in WRITE_TOOLS:
            sys.exit(f"拒绝：{name} 是写工具，探测脚本不调用")
        try:
            raw = cli.call(name, json.loads(a.args))
            data = unwrap(raw)
            out["call"] = {"tool": name, "args": json.loads(a.args),
                           "data": shape_of(data) if a.shape_only else data}
        except Exception as e:
            out["call"] = {"tool": name, "error": str(e)}

    if a.readonly_batch:
        batch: Dict[str, Any] = {}
        for name in READONLY_BATCH:
            try:
                data = unwrap(cli.call(name, {}))
                batch[name] = {
                    "ok": True,
                    "shape": shape_of(data),
                    "sample": None if a.shape_only else _trim(data),
                }
            except Exception as e:
                batch[name] = {"ok": False, "error": str(e)}
        out["batch"] = batch

    text = json.dumps(out, ensure_ascii=False, indent=2, default=str)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"[ok] 结果已写入 {a.out}")
        print(f"[ok] serverInfo={out['serverInfo']} toolCount={out.get('tool_count')}")
    else:
        print(text)
    return 0


def _trim(d: Any, n: int = 2) -> Any:
    """列表只留前 n 项，避免输出爆炸。"""
    if isinstance(d, list):
        return [_trim(x, n) for x in d[:n]]
    if isinstance(d, dict):
        return {k: _trim(v, n) for k, v in d.items()}
    return d


if __name__ == "__main__":
    raise SystemExit(main())
