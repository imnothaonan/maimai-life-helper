#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mm_state.py — 麦麦生活助手 · 状态与账本层

职责：
  1. 固定工作目录下的偏好读写（preferences）
  2. 运行记录（runs）：每次自动领券留痕
  3. 快照（snapshots）：券包 / 积分 的前后对比基线
  4. 账本（ledger）：写操作幂等控制，超时后先核验再决定是否重试
  5. 去重指纹（fingerprint）：券/奖品/订单的稳定标识，跨天对比

设计约束：
  - 纯标准库，无第三方依赖
  - 不读写、不打印任何 Token；凭据一律由 WorkBuddy 已授权连接器持有
  - 所有涉及金额的计算走 mm_calc.py（分为单位），本文件只存原始值

固定工作目录优先级：
  1. 环境变量 MAIMAI_HOME
  2. <用户目录>/.maimai-helper
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

CST = timezone(timedelta(hours=8))

SCHEMA_VERSION = 1

# 写操作类别 -> 是否属于「消耗资源」类动作
# 消耗类动作在定时任务中禁止自动执行，必须人工确认。
MUTATING_KINDS = {
    "auto-bind-coupons",   # 领券：不消耗积分，但属于写操作
    "mall-create-order",   # 积分兑换：消耗积分
    "draw-lottery",        # 抽奖：消耗积分/次数
    "create-order",        # 点餐下单：产生支付
    "cancel-order",        # 取消订单
    "party-order-create",  # 主题活动下单
    "delivery-create-address",  # 新增地址
}

# 允许定时任务自动执行的动作白名单（只读 + 领券）
CRON_ALLOWED_KINDS = {
    "auto-bind-coupons",
    "available-coupons",
    "query-my-coupons",
    "query-my-account",
    "now-time-info",
    "campaign-calendar",
    "list-nutrition-foods",
    "mall-points-products",
    "mall-order-list",
    "query-lottery-info",
    "order-list",
}

# 状态机：pending -> (confirmed | failed | unknown | rolled_back)
VALID_STATUSES = {"pending", "confirmed", "failed", "unknown", "rolled_back"}


# --------------------------------------------------------------------------
# 基础设施
# --------------------------------------------------------------------------

def home() -> Path:
    """固定工作目录。"""
    env = os.environ.get("MAIMAI_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".maimai-helper").resolve()


def _ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def now_cst() -> datetime:
    return datetime.now(CST)


def iso_now() -> str:
    return now_cst().isoformat(timespec="seconds")


def today_str() -> str:
    return now_cst().strftime("%Y-%m-%d")


def atomic_write_json(path: Path, payload: Any) -> None:
    """原子写：先写临时文件再替换，避免中断导致状态文件损坏。"""
    _ensure_dir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        # 状态文件损坏时不静默吞掉：改名保留现场，返回默认值
        try:
            bak = path.with_suffix(path.suffix + f".corrupt-{int(now_cst().timestamp())}")
            path.rename(bak)
            print(f"[warn] 状态文件损坏，已保留为 {bak.name}", file=sys.stderr)
        except OSError:
            pass
        return default


def fingerprint(*parts: Any) -> str:
    """稳定指纹：用于跨天识别「同一张券 / 同一个奖品 / 同一笔订单」。

    只取业务标识字段，不含时间戳等易变字段，保证同一天多次运行结果一致。
    """
    cleaned = []
    for p in parts:
        if p is None:
            cleaned.append("")
        elif isinstance(p, (dict, list)):
            cleaned.append(json.dumps(p, ensure_ascii=False, sort_keys=True))
        else:
            cleaned.append(str(p).strip())
    raw = "|".join(cleaned)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def pick(d: Any, *names: str, default: Any = None) -> Any:
    """从 dict 中按候选键名顺序取第一个存在的值。

    MCP 返回字段名可能随版本变化，这里做宽松匹配而不是写死某个键。
    返回 (值, 命中的键名)；都没命中时返回 (default, None)。
    """
    if not isinstance(d, dict):
        return default, None
    for n in names:
        if n in d and d[n] is not None:
            return d[n], n
    return default, None


def _parse_coupon_markdown(text: str) -> List[Dict[str, Any]]:
    """解析券包的 Markdown 返回（实测真实格式）。

    实测 2026-10-10：`query-my-coupons` 返回的不是 JSON，而是 Markdown：

        # 您的优惠券列表
        共 13 张可用优惠券 | 第 1/1 页 | 每页 200 条

        ## 招行圆筒冰淇淋1份
        - **优惠**: ¥0 (用券价格)
        - **有效期**: 2026-09-19 00:00-2026-10-19 23:59 ...
        - **领取时间**: 5日前收到
        - **标签**: 到店专用

    `available-coupons` 则是另一种格式：

        - 优惠券标题：巧克力味厚松饼猪柳蛋套餐 \\
          状态：可领取 \\
          优惠券图片：\\n    <img src="...">

    两种格式都要支持，且**都不提供官方券 ID** —— 这正是 id_known=false 的场景。
    """
    out: List[Dict[str, Any]] = []
    if not text or not isinstance(text, str):
        return out

    lines = text.splitlines()
    total_match = re.search(r"共\s*(\d+)\s*张可用优惠券", text)
    total_available = int(total_match.group(1)) if total_match else None

    # --- 格式 A：query-my-coupons 的「## 标题 + - 字段」 ---
    i = 0
    while i < len(lines):
        m = re.match(r"^##\s+(.+?)\s*$", lines[i])
        if m:
            name = m.group(1).strip()
            cur: Dict[str, Any] = {"couponName": name}
            j = i + 1
            while j < len(lines) and not lines[j].startswith("##"):
                fm = re.match(r"^-\s*\*\*(.+?)\*\*\s*[:：]\s*(.+?)\s*$", lines[j])
                if fm:
                    k, v = fm.group(1).strip(), fm.group(2).strip()
                    if "优惠" in k:
                        cur["couponAmount"] = v
                    elif "有效期" in k:
                        cur["expireTime"] = v
                    elif "标签" in k:
                        cur["labels"] = v
                        cur["status"] = v
                    elif "领取" in k:
                        cur["receiveTime"] = v
                    else:
                        cur[k] = v
                j += 1
            cur["_total_available"] = total_available
            out.append(cur)
            i = j
        else:
            i += 1

    if out:
        return out

    # --- 格式 B：available-coupons 的「- 优惠券标题 / 状态」 ---
    cur: Optional[Dict[str, Any]] = None
    for ln in lines:
        tm = re.match(r"^\s*-\s*优惠券标题[:：]\s*(.+?)\s*\\?\s*$", ln)
        if tm:
            if cur:
                out.append(cur)
            cur = {"couponName": tm.group(1).strip()}
            continue
        sm = re.match(r"^\s*状态[:：]\s*(.+?)\s*\\?\s*$", ln)
        if sm and cur is not None:
            cur["status"] = sm.group(1).strip()
    if cur:
        out.append(cur)
    return out


def norm_coupon(c: Dict[str, Any]) -> Dict[str, Any]:
    """把券对象归一化为稳定结构，用于跨天对比。

    券 id 候选键涵盖多个版本命名；取不到时退化为「名称+金额+有效期」组合指纹，
    并标记 id_known=False，提醒下游不要把它当作可核验的官方券 ID。

    实测确认：麦当劳 MCP 的券类返回**不含官方券 ID**，
    因此线上运行时 id_known 几乎总为 False，属预期行为而非缺陷。
    """
    cid, cid_key = pick(c, "couponId", "coupon_id", "id", "voucherId", "code")
    name, _ = pick(c, "couponName", "name", "title", "couponTitle", default="")
    amount, _ = pick(c, "couponAmount", "amount", "discount", "value", "couponAmountDesc")
    exp, _ = pick(c, "expireTime", "endTime", "validEnd", "expireAt", "end_date")
    status, _ = pick(c, "status", "state", default=None)

    # 金额可能是「¥29.9 (用券价格)」这类带说明的文本，抽数字部分
    amount_num = None
    if amount is not None:
        am = re.search(r"(\d+(?:\.\d+)?)", str(amount).replace(",", ""))
        if am:
            amount_num = float(am.group(1))

    fp = fingerprint(cid if cid else None, name, amount, exp)

    return {
        "fingerprint": fp,
        "coupon_id": cid,
        "id_known": cid is not None and cid != "",
        "id_key": cid_key,
        "name": name,
        "amount": amount,
        "amount_num": amount_num,
        "expire": exp,
        "status": status,
        "labels": c.get("labels"),
        "raw": c,
    }


def norm_coupons(raw: Any) -> List[Dict[str, Any]]:
    """从任意层级的返回结构里挖出券数组。

    支持三种实测形态：
      1. JSON 结构（含 coupons/couponList/data 等包裹）
      2. Markdown 文本（query-my-coupons / available-coupons 的真实格式）
      3. 已经是 list[dict]
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        return [norm_coupon(x) for x in _parse_coupon_markdown(raw)]
    if isinstance(raw, list):
        return [norm_coupon(x) for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        # 显式的 _raw_text 包装（snapshot --text / --file 存文本时的形态）
        if isinstance(raw.get("_raw_text"), str):
            got = norm_coupons(raw["_raw_text"])
            if got:
                return got
        # probe_mcp 的 {"_text": ..., "_isError": ...} 包装
        if isinstance(raw.get("_text"), str):
            got = _parse_coupon_markdown(raw["_text"])
            if got:
                return [norm_coupon(x) for x in got]
        for key in ("coupons", "couponList", "list", "records", "items", "data", "result"):
            if key in raw:
                got = norm_coupons(raw[key])
                if got:
                    return got
        # 单个券对象
        if any(k in raw for k in ("couponId", "coupon_id", "couponName", "name")):
            return [norm_coupon(raw)]
    return []


# --------------------------------------------------------------------------
# 偏好
# --------------------------------------------------------------------------

DEFAULT_PREFS: Dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "coupon": {
        # 默认北京时间每天 09:00
        "daily_time": "09:00",
        "timezone": "Asia/Shanghai",
        "enabled": True,
        # 无变化时不提醒
        "notify_only_on_change": True,
    },
    "nutrition": {
        "budget_cents": None,
        "calorie_range": None,     # 例如 [400, 700]
        "protein_g_min": None,
        "preferences": [],          # 口味偏好关键词
        "avoid": [],                # 忌口
    },
    # 位置信息：**只由用户口述**，不做自动定位。
    # 实测本机 IP 定位会给出北京/深圳两个互相矛盾的错误结果
    # （与开发者实际所在城市也不符），双源矛盾时无法自证正确，
    # 而点餐会产生真实支出，故不使用 IP 猜测。
    "geo": {
        "city": None,          # 例如 "示例市"
        "district": None,      # 例如 "示例区"
        "note": None,          # 自由备注，例如 "某某地铁站附近"
        "source": None,        # 固定 "user_stated"，标明来源是用户口述
        "updated_at": None,
    },
    "points": {
        "max_spend_per_exchange": None,
    },
    "confirm": {
        # 涉及消耗的动作必须确认
        "require_confirm_consume": True,
        "require_confirm_order": True,
    },
    "party": {
        "city": None,
        "store": None,
    },
}


def load_prefs() -> Dict[str, Any]:
    p = home() / "preferences.json"
    prefs = read_json(p, None)
    if not isinstance(prefs, dict):
        prefs = json.loads(json.dumps(DEFAULT_PREFS))
    else:
        # 补齐新增字段，向后兼容
        for k, v in DEFAULT_PREFS.items():
            if k not in prefs:
                prefs[k] = json.loads(json.dumps(v))
            elif isinstance(v, dict) and isinstance(prefs[k], dict):
                for kk, vv in v.items():
                    prefs[k].setdefault(kk, vv)
    return prefs


def save_prefs(prefs: Dict[str, Any]) -> Path:
    prefs["schema_version"] = SCHEMA_VERSION
    prefs["updated_at"] = iso_now()
    p = home() / "preferences.json"
    atomic_write_json(p, prefs)
    return p


def deep_set(obj: Dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = obj
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


# --------------------------------------------------------------------------
# 运行记录 / 快照
# --------------------------------------------------------------------------

def append_run(record: Dict[str, Any]) -> Path:
    d = _ensure_dir(home() / "runs")
    p = d / f"{today_str()}.jsonl"
    rec = dict(record)
    rec.setdefault("ts", iso_now())
    rec.setdefault("date", today_str())
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return p


def read_runs(date: Optional[str] = None) -> List[Dict[str, Any]]:
    d = home() / "runs"
    date = date or today_str()
    p = d / f"{date}.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def save_snapshot(kind: str, payload: Any, label: str) -> Path:
    """快照命名：snapshot_<kind>_<label>_<date>.json

    label 用 pre/post 区分操作前后，例如领券前 pre、领券后 post。
    """
    d = _ensure_dir(home() / "snapshots")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", label)
    p = d / f"{kind}_{safe}_{today_str()}.json"
    atomic_write_json(p, {
        "kind": kind,
        "label": label,
        "date": today_str(),
        "ts": iso_now(),
        "payload": payload,
    })
    return p


def load_snapshot(kind: str, label: str, date: Optional[str] = None) -> Optional[Dict[str, Any]]:
    d = home() / "snapshots"
    date = date or today_str()
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", label)
    p = d / f"{kind}_{safe}_{date}.json"
    snap = read_json(p, None)
    if isinstance(snap, dict):
        return snap
    # 允许 label 为空时取该 kind 当天最新一份
    if label in ("", "latest"):
        cands = sorted(d.glob(f"{kind}_*_{date}.json"))
        if cands:
            return read_json(cands[-1], None)
    return None


# --------------------------------------------------------------------------
# 快照对比：区分「原有券」与「新领券」
# --------------------------------------------------------------------------

def diff_coupons(before: Any, after: Any) -> Dict[str, Any]:
    """对比领券前后的券包。

    返回：
      new_coupons   新增券（指纹不在 before 里）
      kept_coupons  原有券（指纹在 before 里）
      gone_coupons  消失的券（before 有 after 没有，可能已用或过期）
    """
    b = {c["fingerprint"]: c for c in norm_coupons(before)}
    a = {c["fingerprint"]: c for c in norm_coupons(after)}
    new = [a[k] for k in a if k not in b]
    kept = [a[k] for k in a if k in b]
    gone = [b[k] for k in b if k not in a]
    return {"new_coupons": new, "kept_coupons": kept, "gone_coupons": gone}


def _unwrap_api(obj: Any) -> Any:
    """剥掉 MCP 的外层包装，拿到业务数据本体。

    实测真实返回有三层：
      1. probe_mcp 的 {"call": {"data": {"_text": "..."}}}，_text 内是
         「## Response Structure 说明 + ## Original Response 后跟 JSON」
      2. 业务层 {"success":true,"code":200,"data":{...}}
      3. 数据层 {...}

    这里逐层剥到最内层的 dict。
    """
    cur = obj
    for _ in range(6):  # 最多剥 6 层，防止异常结构导致死循环
        if isinstance(cur, str):
            j = _extract_embedded_json(cur)
            if j is not None:
                cur = j
                continue
            return cur
        if isinstance(cur, list):
            return cur
        if not isinstance(cur, dict):
            return cur
        # probe_mcp 的 _text 包装
        if isinstance(cur.get("_text"), str):
            cur = cur["_text"]
            continue
        # probe_mcp 的 call.data 包装
        if "call" in cur and isinstance(cur["call"], dict):
            cur = cur["call"].get("data", cur["call"])
            continue
        # 业务层 data 包装（仅当 data 里命中已知账户字段时才剥，避免误剥）
        if isinstance(cur.get("data"), (dict, list)) and _looks_like_account(cur["data"]):
            cur = cur["data"]
            continue
        return cur
    return cur


def _looks_like_account(d: Any) -> bool:
    if not isinstance(d, dict):
        return False
    keys = set(d.keys())
    return bool(keys & {"availablePoint", "accumulativePoint", "frozenPoint",
                        "availablePoints", "accountId", "currency"})


def _extract_embedded_json(text: str) -> Any:
    """从 Markdown 文本里抠出内嵌的 JSON。

    实测格式：
        ## Original Response
        {"success":true,"code":200,...}
    """
    idx = text.find("## Original Response")
    if idx >= 0:
        tail = text[idx + len("## Original Response"):].strip()
        if tail.startswith("```"):
            tail = tail[3:]
            if tail.startswith("json"):
                tail = tail[4:]
        # 去掉可能的结尾围栏
        if tail.endswith("```"):
            tail = tail[:-3]
        tail = tail.strip()
        if tail.startswith(("{", "[")):
            try:
                return json.loads(tail)
            except json.JSONDecodeError:
                pass
    # 退而求其次：找第一段完整的 JSON 对象
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start >= 0:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == opener:
                    depth += 1
                elif text[i] == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            break
    return None


def _num(v: Any) -> Optional[float]:
    """把接口返回值转成数值。

    实测：麦当劳 MCP 的积分类字段返回的是**字符串**（如 "237.4"），
    不是数字。直接用 isinstance(x, (int,float)) 判断会全部落空，
    导致积分变化永远检不出来。
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _fmt_num(x: float) -> Any:
    """数值输出：整数去掉 .0，保持与接口一致的展示习惯。"""
    return int(x) if x == int(x) else x


# 实测字段名（2026-10-10，来自 query-my-account 真实返回）：
#   availablePoint / accumulativePoint / frozenPoint /
#   currentMouthExpirePoint / nextMouthExpirePoint / usedPoint
# 前置候选为本项目早期按猜测写的，实际接口未使用，保留以兼容后续版本可能改名。
ACCOUNT_FIELD_MAP = [
    ("available", ("availablePoint", "availablePoints", "available", "usable", "point", "points")),
    ("total", ("accumulativePoint", "accumulatedPoint", "totalPoints", "total", "累计积分")),
    ("frozen", ("frozenPoint", "frozenPoints", "frozen", "冻结积分")),
    ("expiring", ("currentMouthExpirePoint", "currentMonthExpirePoint",
                  "nextMouthExpirePoint", "expiringPoints", "willExpire", "即将过期积分")),
    ("used", ("usedPoint", "usedPoints", "used")),
    ("expired", ("expiredPoint", "expiredPoints", "expired")),
]


def diff_account(before: Any, after: Any) -> Dict[str, Any]:
    """对比积分账户变化。只报数值变化，不猜测原因。

    实测真实结构（query-my-account, 2026-10-10）：
      {"success":true,"code":200,"data":{"availablePoint":"237.4", ...}}
    注意两点：
      1. 账户字段嵌套在 data 下，需要拆包
      2. 数值是**字符串**（"237.4"），不是数字
    """
    before = _unwrap_api(before)
    after = _unwrap_api(after)
    if not isinstance(before, dict) or not isinstance(after, dict):
        return {"changed": False, "note": "缺少可对比的账户快照"}

    out: Dict[str, Any] = {"changed": False, "fields": {}, "currency": None}
    b_cur, _ = pick(before, "currency")
    a_cur, _ = pick(after, "currency")
    if a_cur or b_cur:
        out["currency"] = a_cur or b_cur

    for key, candidates in ACCOUNT_FIELD_MAP:
        bv_raw, bk = pick(before, *candidates)
        av_raw, ak = pick(after, *candidates)
        bv = _num(bv_raw)
        av = _num(av_raw)
        entry: Dict[str, Any] = {
            "before": _fmt_num(bv) if bv is not None else None,
            "after": _fmt_num(av) if av is not None else None,
            "delta": None,
            "field_key": ak or bk,
        }
        if bv is not None and av is not None:
            delta = av - bv
            # 浮点去噪：0.1+0.2 类误差在积分场景会造成误报
            entry["delta"] = _fmt_num(round(delta, 6))
            if abs(delta) > 1e-9:
                out["changed"] = True
        out["fields"][key] = entry
    return out


# --------------------------------------------------------------------------
# 账本：幂等 + 超时核验
# --------------------------------------------------------------------------

def ledger_path() -> Path:
    return _ensure_dir(home()) / "ledger.json"


def load_ledger() -> Dict[str, Any]:
    led = read_json(ledger_path(), None)
    if not isinstance(led, dict):
        led = {"schema_version": SCHEMA_VERSION, "entries": {}}
    led.setdefault("entries", {})
    return led


def save_ledger(led: Dict[str, Any]) -> None:
    led["updated_at"] = iso_now()
    atomic_write_json(ledger_path(), led)


def begin_operation(kind: str, op_key: str, detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """登记一次写操作。同一 (kind, op_key) 已存在则拒绝重复执行。

    op_key 由调用方生成，例如「2026-10-10-auto-bind」或订单场景的幂等键。
    返回：
      proceed=True  -> 允许执行
      proceed=False -> 已有记录，需先核验（reason 说明）
    """
    led = load_ledger()
    key = f"{kind}::{op_key}"
    prev = led["entries"].get(key)

    if prev:
        if prev.get("status") == "pending":
            # 上一次没写完结果 —— 可能超时。禁止直接重跑，要求先核验。
            return {
                "proceed": False,
                "reason": "pending_exists",
                "message": f"{kind} 在 {prev.get('started_at')} 有一笔未完结记录，"
                           f"必须先核验实际结果（查询订单/券包/奖品），确认未生效后才可重跑。",
                "entry": prev,
            }
        if prev.get("status") == "unknown":
            return {
                "proceed": False,
                "reason": "unknown_exists",
                "message": f"{kind} 上一笔结果未知（疑似超时）。先核验，再决定是否重跑。",
                "entry": prev,
            }
        if prev.get("status") == "confirmed":
            return {
                "proceed": False,
                "reason": "already_confirmed",
                "message": f"{kind} 已于 {prev.get('finished_at')} 成功执行，重复执行会重复扣减。",
                "entry": prev,
            }
        # failed / rolled_back -> 允许重跑，但保留历史
        led["entries"][key] = {
            "kind": kind,
            "op_key": op_key,
            "status": "pending",
            "started_at": iso_now(),
            "finished_at": None,
            "attempts": int(prev.get("attempts", 1)) + 1,
            "detail": detail or {},
            "history": prev.get("history", []) + [
                {"status": prev.get("status"), "finished_at": prev.get("finished_at")}
            ],
        }
        save_ledger(led)
        return {"proceed": True, "reason": "retry_after_failure", "entry": led["entries"][key]}

    led["entries"][key] = {
        "kind": kind,
        "op_key": op_key,
        "status": "pending",
        "started_at": iso_now(),
        "finished_at": None,
        "attempts": 1,
        "detail": detail or {},
        "history": [],
    }
    save_ledger(led)
    return {"proceed": True, "reason": "new", "entry": led["entries"][key]}


def finish_operation(kind: str, op_key: str, status: str, result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把账本条目置为终态。

    status:
      confirmed   已确认生效
      failed      明确失败，可重跑
      unknown     超时/中断，结果不明 —— 必须核验
      rolled_back 已确认未生效（如已核验订单不存在）
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"非法状态: {status}")
    if status == "pending":
        raise ValueError("pending 不是终态，请用 finish 或重新 begin")

    led = load_ledger()
    key = f"{kind}::{op_key}"
    entry = led["entries"].get(key)
    if entry is None:
        entry = {"kind": kind, "op_key": op_key, "attempts": 0, "history": []}
    entry["status"] = status
    entry["finished_at"] = iso_now()
    if result is not None:
        entry["result"] = result
    led["entries"][key] = entry
    save_ledger(led)
    return entry


def get_operation(kind: str, op_key: str) -> Optional[Dict[str, Any]]:
    return load_ledger()["entries"].get(f"{kind}::{op_key}")


def check_cron_allowed(kinds: List[str]) -> Dict[str, Any]:
    """定时任务守卫：只允许白名单动作。

    明确禁止消耗积分、下单、预约类动作在无人值守时自动执行。
    """
    blocked = [k for k in kinds if k in MUTATING_KINDS and k not in CRON_ALLOWED_KINDS]
    return {
        "allowed": not blocked,
        "blocked": blocked,
        "message": ("定时任务禁止自动执行以下消耗/下单类动作: " + ", ".join(blocked))
        if blocked else "全部动作在定时任务白名单内",
    }


# --------------------------------------------------------------------------
# 今日是否已领券
# --------------------------------------------------------------------------

def coupon_run_today() -> Dict[str, Any]:
    """检查今天是否已经跑过领券。返回 attempts 与 changed 标记。"""
    runs = [r for r in read_runs() if r.get("module") == "coupon"]
    if not runs:
        return {"ran": False, "attempts": 0, "changed": None}
    return {
        "ran": True,
        "attempts": len(runs),
        "changed": any(r.get("changed") for r in runs),
        "latest": runs[-1],
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))


def cmd_home(_a: argparse.Namespace) -> None:
    _print({"home": str(home()), "exists": home().exists()})


def cmd_prefs_get(a: argparse.Namespace) -> None:
    prefs = load_prefs()
    if a.key:
        cur: Any = prefs
        for p in a.key.split("."):
            cur = cur.get(p) if isinstance(cur, dict) else None
        _print({"key": a.key, "value": cur})
    else:
        _print(prefs)


def cmd_prefs_set(a: argparse.Namespace) -> None:
    prefs = load_prefs()
    try:
        val = json.loads(a.value)
    except json.JSONDecodeError:
        # 允许直接传字符串
        val = a.value
    deep_set(prefs, a.key, val)
    p = save_prefs(prefs)
    _print({"saved": str(p), "key": a.key, "value": val})


def cmd_snapshot(a: argparse.Namespace) -> None:
    """保存快照。

    支持三种输入：
      --file X.json   JSON 文件
      --json '{...}'  JSON 字面量
      --text "..."    原始文本（Markdown 或 JSON 片段），实测麦当劳 MCP 多为 Markdown
    """
    if getattr(a, "text", None):
        payload: Any = {"_raw_text": a.text}
    elif a.file:
        # --file 声明为 Path（不是 FileType），所以这里要自己读
        raw = a.file.read_text(encoding="utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            # 允许直接存文本
            payload = {"_raw_text": raw}
    else:
        payload = json.loads(a.json)
    p = save_snapshot(a.kind, payload, a.label)
    got = norm_coupons(payload) if a.kind == "coupons" else None
    _print({"saved": str(p), "parsed_coupons": len(got) if got is not None else None})


def cmd_diff_coupons(a: argparse.Namespace) -> None:
    b = load_snapshot("coupons", a.before, a.date) if a.before != "-" else None
    a_ = load_snapshot("coupons", a.after, a.date) if a.after != "-" else None
    res = diff_coupons(
        (b or {}).get("payload"),
        (a_ or {}).get("payload"),
    )
    res["changed"] = bool(res["new_coupons"]) or bool(res["gone_coupons"])
    _print({
        "changed": res["changed"],
        "new_count": len(res["new_coupons"]),
        "kept_count": len(res["kept_coupons"]),
        "gone_count": len(res["gone_coupons"]),
        "new_coupons": [_slim_coupon(c) for c in res["new_coupons"]],
        "gone_coupons": [_slim_coupon(c) for c in res["gone_coupons"]],
    })


def _slim_coupon(c: Dict[str, Any]) -> Dict[str, Any]:
    return {k: c.get(k) for k in ("fingerprint", "coupon_id", "id_known", "name", "amount", "expire")}


def cmd_diff_account(a: argparse.Namespace) -> None:
    b = load_snapshot("account", a.before, a.date) if a.before != "-" else None
    a_ = load_snapshot("account", a.after, a.date) if a.after != "-" else None
    res = diff_account((b or {}).get("payload"), (a_ or {}).get("payload"))
    _print(res)


def cmd_ledger_begin(a: argparse.Namespace) -> None:
    detail = json.loads(a.detail) if a.detail else None
    _print(begin_operation(a.kind, a.op_key, detail))


def cmd_ledger_finish(a: argparse.Namespace) -> None:
    """收尾写操作。

    默认只回状态摘要；--full 回显完整 entry（含 history 累积）。
    纯写操作回显整个账本条目会随使用膨胀，实测浪费 token。
    """
    result = json.loads(a.result) if a.result else None
    e = finish_operation(a.kind, a.op_key, a.status, result)
    if getattr(a, "full", False):
        _print(e)
    else:
        _print({
            "op_key": a.op_key, "kind": a.kind,
            "status": (e or {}).get("status", a.status),
            "attempts": (e or {}).get("attempts"),
            "finished_at": (e or {}).get("finished_at"),
        })


def cmd_ledger_get(a: argparse.Namespace) -> None:
    _print(get_operation(a.kind, a.op_key) or {"found": False})


def cmd_ledger_list(a: argparse.Namespace) -> None:
    """列出账本条目。

    默认只返回最近 10 条的摘要 —— 实测账本会随使用线性膨胀，
    全量返回 history 数组会浪费大量 token。用 --limit 0 取全部。
    """
    led = load_ledger()
    items = list(led["entries"].values())
    if a.status:
        items = [i for i in items if i.get("status") == a.status]
    items.sort(key=lambda x: x.get("started_at") or "", reverse=True)

    total = len(items)
    limit = getattr(a, "limit", 10) or 0
    shown = items if limit <= 0 else items[:limit]

    # 摘要模式：不含 history / result / detail，避免输出膨胀
    slim = [{
        "op_key": i.get("op_key"),
        "kind": i.get("kind"),
        "status": i.get("status"),
        "attempts": i.get("attempts"),
        "started_at": i.get("started_at"),
        "finished_at": i.get("finished_at"),
    } for i in shown]

    out: Dict[str, Any] = {
        "total": total,
        "shown": len(slim),
        "entries": slim,
    }
    if limit > 0 and total > limit:
        out["_note"] = f"仅显示最近 {limit} 条（共 {total} 条）。--limit 0 查看全部。"
    _print(out)


def cmd_cron_guard(a: argparse.Namespace) -> None:
    _print(check_cron_allowed(a.kinds))


def cmd_run_status(_a: argparse.Namespace) -> None:
    _print({"coupon": coupon_run_today(), "all_today_count": len(read_runs())})


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mm_state", description="麦麦生活助手 · 状态与账本层")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("home", help="打印固定工作目录").set_defaults(func=cmd_home)

    pg = sub.add_parser("prefs-get", help="读取偏好")
    pg.add_argument("--key", help="点号路径，如 coupon.daily_time")
    pg.set_defaults(func=cmd_prefs_get)

    ps = sub.add_parser("prefs-set", help="写入偏好")
    ps.add_argument("--key", required=True)
    ps.add_argument("--value", required=True, help="JSON 字面量；非 JSON 则按字符串处理")
    ps.set_defaults(func=cmd_prefs_set)

    psn = sub.add_parser("snapshot", help="保存快照")
    psn.add_argument("--kind", required=True, help="coupons / account / orders / prizes ...")
    psn.add_argument("--label", required=True, help="pre / post / latest")
    psn.add_argument("--file", type=Path, help="JSON 文件")
    psn.add_argument("--json", help="JSON 字面量")
    psn.add_argument("--text", help="原始文本（Markdown 或 JSON 片段）")
    psn.set_defaults(func=cmd_snapshot)

    dc = sub.add_parser("diff-coupons", help="对比券包")
    dc.add_argument("--before", required=True, help="pre / - (无)")
    dc.add_argument("--after", required=True, help="post / - (无)")
    dc.add_argument("--date", help="YYYY-MM-DD，默认今天")
    dc.set_defaults(func=cmd_diff_coupons)

    da = sub.add_parser("diff-account", help="对比积分账户")
    da.add_argument("--before", required=True)
    da.add_argument("--after", required=True)
    da.add_argument("--date")
    da.set_defaults(func=cmd_diff_account)

    lb = sub.add_parser("ledger-begin", help="登记写操作（幂等闸门）")
    lb.add_argument("--kind", required=True, choices=sorted(MUTATING_KINDS))
    lb.add_argument("--op-key", required=True)
    lb.add_argument("--detail")
    lb.set_defaults(func=cmd_ledger_begin)

    lf = sub.add_parser("ledger-finish", help="写操作收尾")
    lf.add_argument("--kind", required=True, choices=sorted(MUTATING_KINDS))
    lf.add_argument("--op-key", required=True)
    lf.add_argument("--status", required=True,
                    choices=["confirmed", "failed", "unknown", "rolled_back"])
    lf.add_argument("--result")
    lf.add_argument("--full", action="store_true",
                    help="回显完整 entry（含 history），默认只输出摘要")
    lf.set_defaults(func=cmd_ledger_finish)

    lg = sub.add_parser("ledger-get", help="查询单条账本")
    lg.add_argument("--kind", required=True)
    lg.add_argument("--op-key", required=True)
    lg.set_defaults(func=cmd_ledger_get)

    ll = sub.add_parser("ledger-list", help="列出账本（默认最近 10 条摘要）")
    ll.add_argument("--status", choices=sorted(VALID_STATUSES))
    ll.add_argument("--limit", type=int, default=10,
                    help="最多返回条数，0 表示全部")
    ll.set_defaults(func=cmd_ledger_list)

    cg = sub.add_parser("cron-guard", help="定时任务守卫：校验动作白名单")
    cg.add_argument("--kinds", nargs="+", required=True)
    cg.set_defaults(func=cmd_cron_guard)

    sub.add_parser("run-status", help="今日运行状态").set_defaults(func=cmd_run_status)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
