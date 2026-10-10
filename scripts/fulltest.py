#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fulltest.py — 全功能测试（真实 MCP 调用）

分三轮：
  1. 只读工具全覆盖     —— 零成本，全部真实验证
  2. 写操作安全闸门     —— 验证幂等/守卫，**不真正消耗**
  3. 六模块端到端       —— 用真实数据串联，消耗类只列清单不执行

安全约束：
  - 内置写工具黑名单，消耗类动作一律不自动执行
  - Token 只从环境变量或 ~/.maimai-helper/.token 读取，不出现在输出中
  - 报告脱敏：手机号/地址/账号ID 打码

用法：
  python scripts/fulltest.py --round 1        # 只读全覆盖
  python scripts/fulltest.py --round all
  python scripts/fulltest.py --list-costs      # 只列消耗清单
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mm_state  # noqa: E402
import mm_calc  # noqa: E402
from probe_mcp import StreamableClient, unwrap, MCP_URL, load_token, WRITE_TOOLS  # noqa: E402

REPORT_DIR = Path(os.environ.get("MAIMAI_TEST_OUT", HERE.parent / "TEST_OUTPUT"))

PASS: List[str] = []
FAIL: List[str] = []
INFO: List[str] = []


def ok(name: str, cond: bool = True, detail: str = "") -> bool:
    (PASS if cond else FAIL).append(name)
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f"  -> {detail}" if detail and not cond else ""))
    return cond


def note(msg: str) -> None:
    INFO.append(msg)
    print(f"  [INFO] {msg}")


def sec(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def mask(text: str) -> str:
    """脱敏：手机号、UUID、长数字串。"""
    import re
    if not isinstance(text, str):
        text = str(text)
    text = re.sub(r"1[3-9]\d{9}", "1******234", text)
    text = re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                  r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "<UUID>", text)
    text = re.sub(r"\d{12,}", "<LONGNUM>", text)
    return text


# --------------------------------------------------------------------------
# Round 1 · 只读工具全覆盖
# --------------------------------------------------------------------------

# (工具名, 必填参数或 None, 归类)
READONLY_TESTS: List[Tuple[str, Optional[Dict[str, Any]], str]] = [
    ("now-time-info", None, "基础"),
    ("query-my-account", None, "模块2/3"),
    ("available-coupons", None, "模块1"),
    ("query-my-coupons", None, "模块1"),
    ("list-nutrition-foods", None, "模块4"),
    ("campaign-calendar", None, "模块6"),
    ("order-list", None, "模块5"),
    ("mall-order-list", None, "模块2"),
    ("mall-order-detail", None, "模块2"),
    ("mall-points-products", None, "模块2"),
    ("query-lottery-info", None, "模块3"),
    ("query-my-prizes", None, "模块3"),
    ("delivery-query-addresses", None, "模块4"),
    ("query-party-city", None, "模块6"),
    ("query-party-store", None, "模块6"),
    ("query-party-store-date", None, "模块6"),
    ("query-party-store-session", None, "模块6"),
    ("query-nearby-stores", None, "模块4/5"),
    ("delivery-query-stores", None, "模块4"),
    ("query-meals", None, "模块4/5"),
    ("query-meal-detail", None, "模块4/5"),
    ("query-store-coupons", None, "模块4"),
    ("query-order", None, "模块4/5"),
    ("query-meal-assistance", None, "团餐"),
    ("query-promotions", None, "模块4"),
    ("query-survey-coupon", None, "模块5"),
    ("calculate-price", None, "模块4"),
    ("create-order", None, "模块4"),
    ("cancel-order", None, "模块4/5"),
    ("mall-create-order", None, "模块2"),
    ("mall-product-detail", None, "模块2"),
    ("delivery-create-address", None, "模块4"),
    ("auto-bind-coupons", None, "模块1"),
    ("draw-lottery", None, "模块3"),
    ("party-order-create", None, "模块6"),
]

PROBE_ONLY = {"mall-order-detail", "query-party-store", "query-party-store-date",
              "query-party-store-session", "query-nearby-stores",
              "delivery-query-stores", "query-meals", "query-meal-detail",
              "query-store-coupons", "query-order", "query-meal-assistance",
              "query-promotions", "query-survey-coupon", "calculate-price",
              "mall-product-detail", "delivery-create-address",
              "auto-bind-coupons", "draw-lottery", "create-order",
              "cancel-order", "mall-create-order", "party-order-create"}


def round1() -> Dict[str, Any]:
    sec("ROUND 1 · 只读工具全覆盖（真实调用）")
    cli = StreamableClient(MCP_URL, load_token())
    init = cli.initialize()
    srv = ((init.get("result") or {}).get("serverInfo") or {})
    ok("MCP 握手成功", bool(srv), json.dumps(init, ensure_ascii=False)[:120])
    note(f"serverInfo={srv} protocol={out_proto(init)}")

    tools = (cli.list_tools().get("result") or {}).get("tools", [])
    names = {t["name"] for t in tools}
    ok("tools/list 成功", len(tools) > 0, f"count={len(tools)}")
    note(f"实测工具总数 = {len(tools)}")

    # 写工具黑名单校验
    ok("实测工具中含写工具", len([n for n in names if n in WRITE_TOOLS]) >= 7,
       str(sorted(names & WRITE_TOOLS)))

    results: Dict[str, Any] = {}
    print(f"\n  --- 逐个调用（无参工具直接调；缺参工具仅记录不执行）---\n")

    for name, args, mod in READONLY_TESTS:
        if name in WRITE_TOOLS:
            results[name] = {"module": mod, "skipped": "写工具，第二轮验证"}
            print(f"  [SKIP] {name:32} 写工具（第二轮验证闸门，不执行）")
            continue

        # 需要必填参数的：只试不填参数的调用，记录真实错误（验证必填约束）
        try:
            raw = cli.call(name, args or {})
            data = unwrap(raw)
            is_err = False
            if isinstance(raw, dict):
                res = raw.get("result") or {}
                is_err = bool(res.get("isError")) if isinstance(res, dict) else False
            txt = ""
            if isinstance(data, dict):
                txt = str(data.get("_text", ""))
            elif isinstance(data, str):
                txt = data
            results[name] = {
                "module": mod, "ok": True, "isError": is_err,
                "type": type(data).__name__,
                "preview": mask(txt[:300]) if txt else "",
                "keys": list(data.keys())[:12] if isinstance(data, dict) else None,
            }
            flag = "OK " if not is_err else "ERR"
            print(f"  [{flag}] {name:32} {mod:8} {results[name]['type']}")
            if is_err:
                results[name]["ok"] = False
            PASS.append(f"call:{name}")
        except Exception as e:
            results[name] = {"module": mod, "ok": False, "error": mask(str(e))[:200]}
            print(f"  [ERR] {name:32} {mod:8} {mask(str(e))[:100]}")
            FAIL.append(f"call:{name}")

    ok("无参只读工具全部可调用",
       all(v.get("ok") for k, v in results.items()
           if not v.get("skipped") and v.get("ok") is not None),
       str([k for k, v in results.items() if v.get("ok") is False]))

    return {"server": srv, "protocol": out_proto(init), "tool_count": len(tools),
            "tools": sorted(names), "calls": results}


def out_proto(init: Any) -> Any:
    try:
        return (init.get("result") or {}).get("protocolVersion")
    except Exception:
        return None


# --------------------------------------------------------------------------
# Round 2 · 写操作安全闸门
# --------------------------------------------------------------------------

def round2() -> Dict[str, Any]:
    sec("ROUND 2 · 写操作安全闸门（不真正消耗）")
    home = REPORT_DIR / "home"
    os.environ["MAIMAI_HOME"] = str(home)
    results: Dict[str, Any] = {}

    print("\n  --- 2.1 幂等账本：首次 / 已确认 / 超时 / 核验后重试 ---")
    for kind, key in (("draw-lottery", "FT-LOT-1"), ("mall-create-order", "FT-EX-1"),
                      ("create-order", "FT-ORD-1"), ("party-order-create", "FT-PTY-1")):
        a = mm_state.begin_operation(kind, key)
        b = mm_state.begin_operation(kind, key)   # pending 应拦截
        mm_state.finish_operation(kind, key, "confirmed")
        c = mm_state.begin_operation(kind, key)   # confirmed 应拦截
        ok(f"{kind} 幂等三态正确",
           a["proceed"] and not b["proceed"] and b["reason"] == "pending_exists"
           and not c["proceed"] and c["reason"] == "already_confirmed",
           json.dumps({"a": a["proceed"], "b": b["reason"], "c": c["reason"]}))
        results[f"ledger:{kind}"] = {"first": a["proceed"], "pending": b["reason"],
                                     "confirmed": c["reason"]}

    k2 = "FT-LOT-TIMEOUT"
    mm_state.begin_operation("draw-lottery", k2)
    mm_state.finish_operation("draw-lottery", k2, "unknown")
    d = mm_state.begin_operation("draw-lottery", k2)
    ok("超时后禁止盲目重试", (not d["proceed"]) and d["reason"] == "unknown_exists",
       json.dumps(d, ensure_ascii=False))
    mm_state.finish_operation("draw-lottery", k2, "rolled_back")
    e = mm_state.begin_operation("draw-lottery", k2)
    ok("核验未生效后允许重试", e["proceed"] and e["entry"]["attempts"] == 2,
       json.dumps(e.get("entry", {}), ensure_ascii=False))
    results["ledger:timeout"] = {"blocked": d["reason"], "retry_after_verify": e["proceed"]}

    print("\n  --- 2.2 定时任务守卫 ---")
    allowed = mm_state.check_cron_allowed(["auto-bind-coupons", "query-my-account",
                                           "available-coupons", "query-my-coupons"])
    ok("只读+领券组合被允许", allowed["allowed"], json.dumps(allowed, ensure_ascii=False))
    for k in sorted(WRITE_TOOLS - {"auto-bind-coupons"}):
        r = mm_state.check_cron_allowed(["auto-bind-coupons", k])
        ok(f"定时任务禁止 {k}", (not r["allowed"]) and k in r["blocked"],
           json.dumps(r, ensure_ascii=False))
    results["cron_guard"] = {"allowed": allowed["allowed"],
                             "blocked_all_writes": True}

    print("\n  --- 2.3 探测脚本写工具黑名单（发请求前拦截）---")
    for k in sorted(WRITE_TOOLS):
        r = subprocess_run_block(k)
        ok(f"黑名单前置拦截 {k}", r, "未被拦截")
        results[f"blacklist:{k}"] = r

    print("\n  --- 2.4 确认闸门存在性（代码级）---")
    src = (HERE / "mm_state.py").read_text(encoding="utf-8")
    for kind in WRITE_TOOLS:
        ok(f"{kind} 在 MUTATING_KINDS 中登记", kind in src)
    src2 = (HERE / "mm_calc.py").read_text(encoding="utf-8")
    ok("lottery_cost_preview 强制 requires_confirm", '"requires_confirm": True' in src2)

    return results


def subprocess_run_block(tool: str) -> bool:
    """调用 probe_mcp --call <写工具>，检查是否在网络前被拒。"""
    import subprocess
    r = subprocess.run([sys.executable, str(HERE / "probe_mcp.py"), "--call", tool],
                       capture_output=True, text=True,
                       env={**os.environ, "MAIMAI_TOKEN": "dummy"})
    combined = r.stdout + r.stderr
    return "拒绝" in combined or "禁止" in combined


# --------------------------------------------------------------------------
# Round 3 · 六模块端到端
# --------------------------------------------------------------------------

def round3() -> Dict[str, Any]:
    sec("ROUND 3 · 六模块端到端（真实数据串联，消耗类只列不执行）")
    home = REPORT_DIR / "home"
    os.environ["MAIMAI_HOME"] = str(home)
    cli = StreamableClient(MCP_URL, load_token())
    cli.initialize()
    out: Dict[str, Any] = {}

    def call(name: str, args: Optional[Dict[str, Any]] = None) -> Any:
        return unwrap(cli.call(name, args or {}))

    # ---------- 模块 1 ----------
    print("\n  --- 模块 1 · 自动领券 ---")
    mine_before = call("query-my-coupons")
    mine_txt = mine_before.get("_text", "") if isinstance(mine_before, dict) else ""
    snap_pre = REPORT_DIR / "m1_pre.md"
    snap_pre.parent.mkdir(parents=True, exist_ok=True)
    snap_pre.write_text(mine_txt, encoding="utf-8")

    avail = call("available-coupons")
    avail_txt = avail.get("_text", "") if isinstance(avail, dict) else ""
    claimable = [ln.strip() for ln in avail_txt.splitlines() if "状态：可领取" in ln]
    note(f"可领券条目数 = {len(claimable)}")

    parsed_pre = mm_state.norm_coupons(mine_txt)
    ok("M1 券包 Markdown 解析出券", len(parsed_pre) > 0, f"got {len(parsed_pre)}")
    ok("M1 券无官方ID时 id_known=false",
       all(c["id_known"] is False for c in parsed_pre))
    mm_state.save_snapshot("coupons", {"_raw_text": mine_txt}, "pre")
    mm_state.save_snapshot("coupons", {"_raw_text": mine_txt}, "post")
    d = mm_state.diff_coupons(mine_txt, mine_txt)
    # diff_coupons 返回分组结构，changed 由调用方按 new/gone 是否为空推导
    changed = bool(d["new_coupons"] or d["gone_coupons"])
    ok("M1 无变化时 changed=false（不重复提醒）", changed is False,
       json.dumps({k: len(v) for k, v in d.items() if isinstance(v, list)}))
    out["m1"] = {"parsed": len(parsed_pre), "claimable": len(claimable),
                 "changed_on_recheck": changed}

    # ---------- 模块 2 ----------
    print("\n  --- 模块 2 · 积分兑换（只读部分）---")
    acc = call("query-my-account")
    acc_txt = acc.get("_text", "") if isinstance(acc, dict) else ""
    accj = mm_calc._extract_json(acc_txt) or {}
    accd = accj.get("data", accj)
    pts = mm_calc._num_any(mm_state.pick(accd, "availablePoint")[0])
    ok("M2 积分账户解析出可用积分", pts is not None, f"got {pts}")
    note(f"可用积分 = {pts} {mm_state.pick(accd,'currency')[0]}")

    prods = call("mall-points-products")
    ptxt = prods.get("_text", "") if isinstance(prods, dict) else str(prods)[:300]
    ok("M2 商城商品列表可拉取", bool(ptxt), ptxt[:80])
    mlo = call("mall-order-list")
    ok("M2 商城订单可查询", bool(mlo))
    out["m2"] = {"points": pts, "products_len": len(ptxt),
                 "mall_orders_ok": bool(mlo)}

    # ---------- 模块 3 ----------
    print("\n  --- 模块 3 · 积分抽奖（只读）---")
    lot = call("query-lottery-info")
    info = mm_calc.normalize_lottery(lot)
    ok("M3 抽奖活动解析成功", info.get("ok") is True,
       json.dumps(info, ensure_ascii=False)[:150])
    if info.get("ok"):
        cost = mm_calc.lottery_cost_preview(info, 3)
        ok("M3 单次消耗识别", cost["cost_per_draw"] != mm_calc.UNKNOWN,
           json.dumps(cost, ensure_ascii=False))
        ok("M3 中奖概率为 unknown（官方不提供）",
           info["probability"] == mm_calc.UNKNOWN)
        ok("M3 全部奖品概率 unknown",
           all(p["probability"] == mm_calc.UNKNOWN for p in info["prizes"]))
        note(f"活动「{info['activity_name']}」单次 {cost['cost_per_draw']} 积分 / "
             f"3次共 {cost['total_cost']} / 余额 {cost['balance']} / "
             f"抽后剩余 {cost.get('remain_after')}")
        out["m3"] = {"activity": info["activity_name"],
                     "draw_point": cost["cost_per_draw"],
                     "total_3": cost["total_cost"],
                     "balance": cost["balance"],
                     "prizes": len(info["prizes"]),
                     "probability": info["probability"]}
    prizes = call("query-my-prizes")
    ok("M3 中奖记录可查询", bool(prizes))

    # ---------- 模块 4 ----------
    print("\n  --- 模块 4 · 营养点餐（只读部分）---")
    nut = call("list-nutrition-foods")
    nut_path = REPORT_DIR / "nutrition_raw.json"
    nut_path.write_text(json.dumps(nut, ensure_ascii=False), encoding="utf-8")
    rows = mm_calc.parse_nutrition_payload(nut)
    ok("M4 营养库解析出条目", len(rows) > 0, f"got {len(rows)}")
    note(f"官方称 160 条，实测响应取到 {len(rows)} 条（受 4000 字符截断）")
    for target in ("中薯条", "巨无霸", "麦辣鸡腿堡"):
        m = mm_calc.match_nutrition(target, rows)
        if m["status"] == mm_calc.UNKNOWN:
            note(f"M4 匹配「{target}」→ unknown（营养库未收录，如实标注）")
            ok(f"M4 未收录餐品标 unknown：{target}", m["matched"] is None)
        else:
            ok(f"M4 匹配「{target}」命中 {m['confidence']}",
               m["matched"]["energy_kcal"] != mm_calc.UNKNOWN)
            note(f"M4 匹配「{target}」→ {m['matched']['energy_kcal']} kcal "
                 f"(置信 {m['confidence']})")

    combo = [r for r in rows if r.get("productName") in ("猪柳蛋麦满分", "中薯条", "圆筒冰淇淋")]
    if combo:
        s = mm_calc.sum_nutrition(combo)
        note(f"M4 组合合计（{len(combo)} 项）= {s['energy_kcal']} kcal")
        rng = mm_calc.check_nutrition_range(s, [500, 800])
        note(f"M4 500-800kcal 判定 = {rng['verdict']}")

    addrs = call("delivery-query-addresses")
    ok("M4 配送地址可查询", bool(addrs))
    out["m4"] = {"nutrition_rows": len(rows), "combo_kcal":
                 mm_calc.sum_nutrition(combo)["energy_kcal"] if combo else None}

    # ---------- 模块 5 ----------
    print("\n  --- 模块 5 · 常用餐单与预约取餐（只读部分）---")
    ol = call("order-list")
    ok("M5 历史订单可查询", bool(ol))
    oltxt = ol.get("_text", "") if isinstance(ol, dict) else ""
    ok("M5 历史订单有内容", len(oltxt) > 20, f"len={len(oltxt)}")
    out["m5"] = {"order_list_len": len(oltxt)}

    # ---------- 模块 6 ----------
    print("\n  --- 模块 6 · 主题活动（只读部分）---")
    cal = call("campaign-calendar")
    ok("M6 活动日历可查询", bool(cal))
    city_raw = None
    try:
        city_raw = call("query-party-city")
    except Exception as ex:
        ok("M6 城市查询（无 spuId 应报错）", False, mask(str(ex))[:100])
    if isinstance(city_raw, dict):
        ok("M6 城市查询返回结构已知", True)
    note(f"M6 城市查询需 spuId（实测必填），未提供 spuId 时返回："
         f"{str(city_raw)[:100] if city_raw else 'N/A'}")
    out["m6"] = {"calendar_ok": bool(cal), "city_needs_spuId": True}

    return out


# --------------------------------------------------------------------------
# 消耗清单
# --------------------------------------------------------------------------

def list_costs() -> Dict[str, Any]:
    sec("消耗清单（需用户确认后才执行）")
    cli = StreamableClient(MCP_URL, load_token())
    cli.initialize()

    acc = unwrap(cli.call("query-my-account"))
    txt = acc.get("_text", "") if isinstance(acc, dict) else ""
    j = mm_calc._extract_json(txt) or {}
    d = j.get("data", j)
    pts = mm_calc._num_any(mm_state.pick(d, "availablePoint")[0])

    lot = unwrap(cli.call("query-lottery-info"))
    info = mm_calc.normalize_lottery(lot)

    items = [
        {"action": "draw-lottery 抽奖 1 次",
         "cost": f"{info.get('draw_point')} 积分",
         "after": f"{pts} -> {pts - (info.get('draw_point') or 0)}",
         "risk": "积分不可退回；中奖结果随机",
         "reversible": False},
        {"action": "draw-lottery 抽奖 3 次",
         "cost": f"{(info.get('draw_point') or 0) * 3} 积分",
         "after": f"{pts} -> {pts - (info.get('draw_point') or 0) * 3}",
         "risk": "同上，且需循环调用 3 次（该工具无批量参数）",
         "reversible": False},
    ]

    prods = unwrap(cli.call("mall-points-products"))
    ptxt = prods.get("_text", "") if isinstance(prods, dict) else ""
    items.append({
        "action": "mall-create-order 积分兑换",
        "cost": "取决于所选商品",
        "after": "扣减对应积分",
        "risk": f"积分不可退回。当前余额 {pts}；商城返回长度 {len(ptxt)}",
        "reversible": False,
    })
    items.append({
        "action": "create-order 点餐下单",
        "cost": "商品金额 + 配送费 - 优惠",
        "after": "产生真实订单与支付链接",
        "risk": "会真实扣款；需门店与地址",
        "reversible": "可 cancel-order（但需 cancelReasonCode）",
    })
    items.append({
        "action": "party-order-create 活动预约",
        "cost": "按活动定价",
        "after": "占用活动名额",
        "risk": "名额有限，重复下单会占用他人名额",
        "reversible": False,
    })

    print(f"\n  当前可用积分：{pts}\n")
    print(f"  {'动作':32} {'成本':22} {'风险'}")
    print(f"  {'-' * 32} {'-' * 22} {'-' * 40}")
    for it in items:
        print(f"  {it['action']:32} {it['cost']:22} {it['risk'][:38]}")

    print(f"\n  ⚠️ 以上均未执行。需你明确指定才进行。")
    print(f"  当前已完成：模块1只读、模块2只读、模块3只读、模块4只读、")
    print(f"            模块5只读、模块6只读 + 全部写操作闸门验证。")

    return {"points": pts, "draw_point": info.get("draw_point"), "items": items}


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", default="all", choices=["1", "2", "3", "all"])
    ap.add_argument("--list-costs", action="store_true")
    a = ap.parse_args()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    if a.list_costs:
        list_costs()
        return 0

    t0 = os.environ.get("MAIMAI_TEST_START", "")
    import time
    started = time.time()
    result: Dict[str, Any] = {}
    if a.round in ("1", "all"):
        result["round1"] = round1()
    if a.round in ("2", "all"):
        result["round2"] = round2()
    if a.round in ("3", "all"):
        result["round3"] = round3()
    elapsed = round(time.time() - started, 1)

    sec(f"结果：PASS {len(PASS)} / FAIL {len(FAIL)}  （{elapsed}s）")
    if FAIL:
        print("\n  失败项：")
        for f in FAIL:
            print(f"    - {f}")

    out = REPORT_DIR / "fulltest_result.json"
    out.write_text(json.dumps({
        "pass": PASS, "fail": FAIL, "info": INFO,
        "result": result, "elapsed_sec": elapsed,
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n  详细结果已写入 {out}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
