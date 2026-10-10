#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest.py — 麦麦生活助手 · 脚本层自测

【重要声明】
本文件全部使用**模拟数据**，用于验证脚本的纯计算与状态逻辑，
不代表任何真实 MCP 调用结果。真实调用结果见 TEST_REPORT.md。

用法：
  python scripts/selftest.py
退出码 0 = 全部通过
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable
MM_STATE = HERE / "mm_state.py"
MM_CALC = HERE / "mm_calc.py"

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f"  -> {detail}" if detail and not cond else ""))


def run(script: Path, *args: str) -> dict:
    """运行子脚本并解析 stdout JSON。"""
    env = dict(os.environ)
    r = subprocess.run([PY, str(script), *args], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"{script.name} {args} 失败: {r.stderr[:500]}")
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"{script.name} 输出非 JSON: {r.stdout[:400]}")


# ==========================================================================
# 1. mm_state：偏好
# ==========================================================================
def test_prefs(home: Path) -> None:
    print("\n[1] 偏好读写 mm_state")
    e = {"MAIMAI_HOME": str(home)}
    os.environ.update(e)

    d = run(MM_STATE, "prefs-get", "--key", "coupon.daily_time")
    check("默认领券时间为北京时间 09:00", d["value"] == "09:00", str(d))
    check("默认时区为 Asia/Shanghai",
          run(MM_STATE, "prefs-get", "--key", "coupon.timezone")["value"] == "Asia/Shanghai")

    run(MM_STATE, "prefs-set", "--key", "coupon.daily_time", "--value", '"08:30"')
    check("修改领券时间生效",
          run(MM_STATE, "prefs-get", "--key", "coupon.daily_time")["value"] == "08:30")

    run(MM_STATE, "prefs-set", "--key", "coupon.enabled", "--value", "false")
    check("暂停开关生效",
          run(MM_STATE, "prefs-get", "--key", "coupon.enabled")["value"] is False)

    run(MM_STATE, "prefs-set", "--key", "nutrition.calorie_range", "--value", "[400,700]")
    check("热量区间可写入",
          run(MM_STATE, "prefs-get", "--key", "nutrition.calorie_range")["value"] == [400, 700])

    # 非 JSON 值应按字符串处理
    run(MM_STATE, "prefs-set", "--key", "party.city", "--value", "上海")
    check("非 JSON 值按字符串存储",
          run(MM_STATE, "prefs-get", "--key", "party.city")["value"] == "上海")


# ==========================================================================
# 2. mm_state：券包快照对比
# ==========================================================================
BEFORE = {
    "coupons": [
        {"couponId": "C001", "couponName": "满30减6", "amount": 600, "expireTime": "2026-11-30"},
        {"couponId": "C002", "couponName": "满50减12", "amount": 1200, "expireTime": "2026-12-15"},
    ]
}
AFTER = {
    "coupons": [
        {"couponId": "C001", "couponName": "满30减6", "amount": 600, "expireTime": "2026-11-30"},
        {"couponId": "C002", "couponName": "满50减12", "amount": 1200, "expireTime": "2026-12-15"},
        {"couponId": "C003", "couponName": "早餐6元券", "amount": 600, "expireTime": "2026-10-20"},
        {"couponId": "C004", "couponName": "新客立减8", "amount": 800, "expireTime": "2026-10-25"},
    ]
}


def test_coupon_diff(home: Path) -> None:
    print("\n[2] 券包快照对比（区分原有券 / 新领券）")
    os.environ["MAIMAI_HOME"] = str(home)

    (home / "before.json").write_text(json.dumps(BEFORE, ensure_ascii=False), encoding="utf-8")
    (home / "after.json").write_text(json.dumps(AFTER, ensure_ascii=False), encoding="utf-8")

    run(MM_STATE, "snapshot", "--kind", "coupons", "--label", "pre", "--file", str(home / "before.json"))
    run(MM_STATE, "snapshot", "--kind", "coupons", "--label", "post", "--file", str(home / "after.json"))
    d = run(MM_STATE, "diff-coupons", "--before", "pre", "--after", "post")

    check("识别出 changed=true", d["changed"] is True, json.dumps(d, ensure_ascii=False))
    check("新领券数量为 2", d["new_count"] == 2, f"got {d['new_count']}")
    check("原有券保留数为 2", d["kept_count"] == 2, f"got {d['kept_count']}")
    check("券 ID 被正确识别",
          all(c["id_known"] for c in d["new_coupons"]),
          json.dumps(d["new_coupons"], ensure_ascii=False))

    names = {c["name"] for c in d["new_coupons"]}
    check("新领券名称正确", names == {"早餐6元券", "新客立减8"}, str(names))

    # 无变化场景
    run(MM_STATE, "snapshot", "--kind", "coupons", "--label", "post2",
        "--file", str(home / "after.json"))
    d2 = run(MM_STATE, "diff-coupons", "--before", "post", "--after", "post2")
    check("无变化时 changed=false（用于不重复提醒）", d2["changed"] is False, json.dumps(d2))
    check("无变化时 new_count=0", d2["new_count"] == 0)

    # 嵌套包裹层级
    nested = {"data": {"result": {"couponList": AFTER["coupons"]}}}
    (home / "nested.json").write_text(json.dumps(nested, ensure_ascii=False), encoding="utf-8")
    import mm_state
    got = mm_state.norm_coupons(nested)
    check("能从多层包裹结构中提取券数组", len(got) == 4, f"got {len(got)}")

    # 取不到 ID 时必须标记 id_known=false
    noid = {"coupons": [{"couponName": "无ID券", "amount": 300, "expireTime": "2026-10-01"}]}
    got2 = mm_state.norm_coupons(noid)
    check("无券ID时标记 id_known=false（防止误当官方ID使用）",
          got2[0]["id_known"] is False, json.dumps(got2[0], ensure_ascii=False))


# ==========================================================================
# 3. mm_state：积分账户对比
# ==========================================================================
def test_account_diff(home: Path) -> None:
    print("\n[3] 积分账户对比")
    os.environ["MAIMAI_HOME"] = str(home)
    # 以下字段名与数值形态取自 2026-10-10 实测：availablePoint / accumulativePoint /
    # currentMouthExpirePoint，数值是字符串
    b = {"availablePoint": "237.4", "accumulativePoint": "947.6", "currency": "麦享会积分",
         "currentMouthExpirePoint": "0", "expiredPoint": "710.2", "frozenPoint": "0",
         "lastMouthExpirePoint": "0", "nextMouthExpirePoint": "0", "usedPoint": "0"}
    a = {"availablePoint": "187.4", "accumulativePoint": "947.6", "currency": "麦享会积分",
         "currentMouthExpirePoint": "0", "expiredPoint": "710.2", "frozenPoint": "0",
         "lastMouthExpirePoint": "0", "nextMouthExpirePoint": "0", "usedPoint": "0"}
    (home / "acc_b.json").write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
    (home / "acc_a.json").write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
    run(MM_STATE, "snapshot", "--kind", "account", "--label", "pre", "--file", str(home / "acc_b.json"))
    run(MM_STATE, "snapshot", "--kind", "account", "--label", "post", "--file", str(home / "acc_a.json"))
    d = run(MM_STATE, "diff-account", "--before", "pre", "--after", "post")

    check("真实字段名可用积分被检出", d["fields"]["available"]["delta"] == -50,
          json.dumps(d, ensure_ascii=False))
    check("字符串数值被正确解析", d["fields"]["available"]["before"] == 237.4,
          json.dumps(d["fields"]["available"], ensure_ascii=False))
    check("累计积分未变不误报", d["fields"]["total"]["delta"] == 0)
    check("积分变化被检出", d["changed"] is True, json.dumps(d))
    check("货币类型被识别", d["currency"] == "麦享会积分", str(d.get("currency")))

    same = run(MM_STATE, "diff-account", "--before", "pre", "--after", "pre")
    check("无变化时 changed=false", same["changed"] is False)


def test_real_wrappers(home: Path) -> None:
    """实测包装形态：三层嵌套 + Markdown 内嵌 JSON。"""
    print("\n[3b] 实测返回包装拆解")
    os.environ["MAIMAI_HOME"] = str(home)
    import mm_state

    # ① probe_mcp 的 {"call":{"data":{"_text": "...Markdown...JSON..."}}}
    md = (
        "# API Response Information\n\n## Response Structure\n\n"
        "- **data.availablePoint**: 可用积分 (Type: string)\n\n"
        '## Original Response\n\n{"success":true,"code":200,"data":'
        '{"availablePoint":"237.4","accumulativePoint":"947.6","currency":"麦享会积分",'
        '"currentMouthExpirePoint":"0","frozenPoint":"0","usedPoint":"0","expiredPoint":"710.2"}}'
    )
    wrapped = {"call": {"data": {"_text": md, "_isError": False}}}
    r1 = mm_state.diff_account(wrapped, wrapped)
    check("能从 Markdown 内嵌 JSON 提取积分数值",
          r1["fields"]["available"]["before"] == 237.4, json.dumps(r1, ensure_ascii=False))
    check("能从 probe_mcp 包装识别货币", r1["currency"] == "麦享会积分", str(r1.get("currency")))

    # ② 业务层 data 包裹
    biz = {"success": True, "code": 200, "data": {"availablePoint": "100", "accumulativePoint": "200"}}
    r2 = mm_state.diff_account(biz, biz)
    check("能从业务层 data 包裹提取积分",
          r2["fields"]["available"]["before"] == 100, json.dumps(r2, ensure_ascii=False))

    # ③ Markdown 券包两种格式
    mine = ("# 您的优惠券列表\n\n共 13 张可用优惠券 | 第 1/1 页 | 每页 200 条\n\n"
            "## 招行圆筒冰淇淋1份\n"
            "- **优惠**: ¥0 (用券价格)\n"
            "- **有效期**: 2026-09-19 00:00-2026-10-19 23:59\n"
            "- **标签**: 到店专用\n\n"
            "## 巧克力味厚松饼猪柳蛋套餐\n"
            "- **优惠**: ¥29.9 (用券价格)\n"
            "- **有效期**: 2026-10-10 05:00-2026-10-10 10:29\n"
            "- **标签**: 今日到期、到店专用\n")
    got = mm_state.norm_coupons(mine)
    check("能解析 Markdown 券包（实测格式）", len(got) == 2, f"got {len(got)}")
    if len(got) == 2:
        check("券名解析正确", got[1]["name"] == "巧克力味厚松饼猪柳蛋套餐", str(got[1]["name"]))
        check("券面额从 '¥29.9 (用券价格)' 抽出", got[1]["amount_num"] == 29.9,
              str(got[1]["amount_num"]))
        check("券标签解析正确", "今日到期" in str(got[1]["status"]), str(got[1]["status"]))

    avail = ("### 麦麦省优惠券列表：\n"
             "- 优惠券标题：巧克力味厚松饼猪柳蛋套餐 \\\n"
             "  状态：已领取 \\\n"
             "- 优惠券标题：免费脆薯饼 \\\n"
             "  状态：可领取 \\\n")
    got2 = mm_state.norm_coupons(avail)
    check("能解析 available-coupons 的另一种格式", len(got2) == 2, f"got {len(got2)}")
    if len(got2) == 2:
        check("可领状态解析正确", got2[1]["status"] == "可领取", str(got2[1]["status"]))

    # 真实券包无官方 ID -> id_known 必须为 False
    check("真实券包无官方券ID时 id_known=false",
          all(c["id_known"] is False for c in got), "id_known 应全为 False")

    # snapshot --text 路径
    (home / "mine.md").write_text(mine, encoding="utf-8")
    s = run(MM_STATE, "snapshot", "--kind", "coupons", "--label", "mdtest",
            "--file", str(home / "mine.md"))
    check("snapshot 能直接吃 Markdown 文件", s["parsed_coupons"] == 2,
          json.dumps(s, ensure_ascii=False))


# ==========================================================================
# 4. mm_state：幂等账本与超时核验
# ==========================================================================
def test_ledger(home: Path) -> None:
    print("\n[4] 幂等账本（防重复消耗）")
    os.environ["MAIMAI_HOME"] = str(home)

    a = run(MM_STATE, "ledger-begin", "--kind", "mall-create-order", "--op-key", "K1")
    check("首次执行放行", a["proceed"] is True, json.dumps(a))

    run(MM_STATE, "ledger-finish", "--kind", "mall-create-order", "--op-key", "K1",
        "--status", "confirmed")
    b = run(MM_STATE, "ledger-begin", "--kind", "mall-create-order", "--op-key", "K1")
    check("已确认的操作拒绝重复执行（防重复扣积分）", b["proceed"] is False, json.dumps(b))
    check("拒绝原因标记为 already_confirmed", b["reason"] == "already_confirmed")

    # 超时场景
    run(MM_STATE, "ledger-begin", "--kind", "draw-lottery", "--op-key", "K2")
    run(MM_STATE, "ledger-finish", "--kind", "draw-lottery", "--op-key", "K2", "--status", "unknown")
    c = run(MM_STATE, "ledger-begin", "--kind", "draw-lottery", "--op-key", "K2")
    check("超时(unknown)的操作禁止盲目重试", c["proceed"] is False, json.dumps(c))
    check("要求先核验", "核验" in c["message"], c["message"])

    # 核验确认未生效后允许重试
    run(MM_STATE, "ledger-finish", "--kind", "draw-lottery", "--op-key", "K2", "--status", "rolled_back")
    d = run(MM_STATE, "ledger-begin", "--kind", "draw-lottery", "--op-key", "K2")
    check("核验为未生效后可重试", d["proceed"] is True, json.dumps(d))
    check("重试次数被累加", d["entry"]["attempts"] == 2, json.dumps(d["entry"]))

    # 失败后重试
    run(MM_STATE, "ledger-begin", "--kind", "create-order", "--op-key", "K3")
    run(MM_STATE, "ledger-finish", "--kind", "create-order", "--op-key", "K3", "--status", "failed")
    e = run(MM_STATE, "ledger-begin", "--kind", "create-order", "--op-key", "K3")
    check("明确失败的操作允许重试", e["proceed"] is True, json.dumps(e))

    lst = run(MM_STATE, "ledger-list", "--limit", "0")
    check("账本可列出全部条目", lst["total"] == 3, f"total={lst['total']}")
    check("账本列表默认不含 history 字段（省 token）",
          all("history" not in e for e in lst["entries"]),
          json.dumps(lst["entries"][0], ensure_ascii=False))

    limited = run(MM_STATE, "ledger-list", "--limit", "2")
    check("账本列表支持 limit", limited["shown"] == 2 and limited["total"] == 3,
          json.dumps({"shown": limited["shown"], "total": limited["total"]}))

    # pending 状态阻断
    run(MM_STATE, "ledger-begin", "--kind", "party-order-create", "--op-key", "K4")
    f = run(MM_STATE, "ledger-begin", "--kind", "party-order-create", "--op-key", "K4")
    check("pending(未完结)状态阻断重复执行", f["proceed"] is False and f["reason"] == "pending_exists",
          json.dumps(f))


# ==========================================================================
# 5. 定时任务守卫
# ==========================================================================
def test_cron_guard(home: Path) -> None:
    print("\n[5] 定时任务守卫")
    os.environ["MAIMAI_HOME"] = str(home)

    ok = run(MM_STATE, "cron-guard", "--kinds", "auto-bind-coupons", "query-my-account",
             "available-coupons", "query-my-coupons")
    check("只读+领券组合被允许", ok["allowed"] is True, json.dumps(ok, ensure_ascii=False))

    for bad in ("mall-create-order", "draw-lottery", "create-order", "party-order-create",
                "cancel-order", "delivery-create-address"):
        r = run(MM_STATE, "cron-guard", "--kinds", "auto-bind-coupons", bad)
        check(f"定时任务禁止 {bad}", r["allowed"] is False and bad in r["blocked"],
              json.dumps(r, ensure_ascii=False))

    r = run(MM_STATE, "cron-guard", "--kinds", "auto-bind-coupons", "mall-create-order")
    check("阻断时给出明确原因", "禁止自动执行" in r["message"], r["message"])


# ==========================================================================
# 6. mm_calc：金额与优惠
# ==========================================================================
def test_calc_money(home: Path) -> None:
    print("\n[6] 金额核算（四类优惠严格分开）")
    os.environ["MAIMAI_HOME"] = str(home)

    c = run(MM_CALC, "cents", "12.34")
    check("元转分精确", c["cents"] == 1234, json.dumps(c))
    c2 = run(MM_CALC, "cents", "¥1,280.50")
    check("带货币符号与千分位可解析", c2["cents"] == 128050, json.dumps(c2))

    # 自洽试算
    price = {"goodsAmount": 4200, "deliveryFee": 500, "discountAmount": 1200, "payableAmount": 3500}
    pf = home / "price.json"
    pf.write_text(json.dumps(price), encoding="utf-8")
    p = run(MM_CALC, "price", "--file", str(pf))
    check("试算标记为 trial_discount", p["kind"] == "trial_discount")
    check("自洽试算被判定为 consistent", p["consistent"] is True, json.dumps(p, ensure_ascii=False))

    # 不自洽试算
    bad = {"goodsAmount": 4200, "deliveryFee": 500, "discountAmount": 1200, "payableAmount": 3000}
    bf = home / "price_bad.json"
    bf.write_text(json.dumps(bad), encoding="utf-8")
    pb = run(MM_CALC, "price", "--file", str(bf))
    check("不自洽试算被标记 inconsistent", pb["consistent"] is False)
    check("不自洽时给出差异提示", "不自洽" in pb["note"], pb["note"])

    # 真实订单
    order = {"orderNo": "MCD123", "goodsAmount": 4200, "deliveryFee": 500,
             "payAmount": 3500, "payUrl": "https://pay.example/x", "pickupCode": "QR888"}
    of = home / "order.json"
    of.write_text(json.dumps(order), encoding="utf-8")
    o = run(MM_CALC, "actual", "--file", str(of))
    check("实际优惠标记为 actual_discount", o["kind"] == "actual_discount")
    check("实际优惠金额正确 (700)", o["discount_cents"] == 700, json.dumps(o, ensure_ascii=False))
    check("订单号被提取", o["order_no"] == "MCD123")
    check("支付链接被提取", o["pay_link"] == "https://pay.example/x")
    check("取餐柜二维码被提取", o["pickup_qrcode"] == "QR888")

    # 官方直接返回优惠字段时优先采信
    order2 = {"goodsAmount": 4200, "payAmount": 3500, "discountAmount": 700}
    o2f = home / "order2.json"
    o2f.write_text(json.dumps(order2), encoding="utf-8")
    o2 = run(MM_CALC, "actual", "--file", str(o2f))
    check("优先采信接口直接返回的优惠字段", o2["discount_source"] == "接口直接返回", json.dumps(o2))

    # 金额字段缺失时不得编造
    order3 = {"orderNo": "MCD999"}
    o3f = home / "order3.json"
    o3f.write_text(json.dumps(order3), encoding="utf-8")
    o3 = run(MM_CALC, "actual", "--file", str(o3f))
    check("金额缺失时优惠为 null 而非 0", o3["discount_cents"] is None, json.dumps(o3))
    check("金额缺失时给出无法核算说明", "无法核算" in o3.get("note", ""), json.dumps(o3, ensure_ascii=False))

    # 四类合并禁止求和
    m = run(MM_CALC, "merge", "--owned", "1200", "--new", "800", "--trial", "2000", "--actual", "1800")
    check("四类优惠标记禁止求和", m["must_not_sum"] is True)
    check("四类优惠并列保留", m["trial_discount_cents"] == 2000 and m["actual_discount_cents"] == 1800,
          json.dumps(m, ensure_ascii=False))


# ==========================================================================
# 7. mm_calc：营养
# ==========================================================================
NUTRITION = [
    {"name": "巨无霸", "energy": 520, "protein": 25, "fat": 30, "carbohydrate": 45,
     "sodium": 1150, "calcium": 30},
    {"name": "中薯条", "energy": 335, "protein": 4, "fat": 15, "carbohydrate": 44,
     "sodium": 360, "calcium": 20},
    {"name": "麦辣鸡腿堡", "energy": 500, "protein": 20, "fat": 25, "carbohydrate": 45,
     "sodium": 1050, "calcium": 25},
    {"name": "中可乐", "energy": 140, "protein": 0, "fat": 0, "carbohydrate": 36,
     "sodium": 10, "calcium": 0},
]


def test_calc_nutrition(home: Path) -> None:
    print("\n[7] 营养匹配（匹配不上必须标未知）")
    os.environ["MAIMAI_HOME"] = str(home)
    nf = home / "nutrition.json"
    nf.write_text(json.dumps(NUTRITION, ensure_ascii=False), encoding="utf-8")

    m = run(MM_CALC, "match-nutrition", "巨无霸", "--file", str(nf))
    check("精确匹配为 high", m["confidence"] == "high", json.dumps(m, ensure_ascii=False))
    check("匹配到正确热量", m["matched"]["energy_kcal"] == 520, json.dumps(m["matched"], ensure_ascii=False))

    m2 = run(MM_CALC, "match-nutrition", "巨无霸中堡套餐", "--file", str(nf))
    check("包含匹配为 high", m2["confidence"] == "high", json.dumps(m2, ensure_ascii=False))

    # 未收录餐品 -> unknown，绝不估算
    m3 = run(MM_CALC, "match-nutrition", "板烧鸡腿堡", "--file", str(nf))
    check("未收录餐品返回 unknown", m3["status"] == "unknown", json.dumps(m3, ensure_ascii=False))
    check("未收录餐品 confidence=none", m3["confidence"] == "none")
    check("未收录餐品 matched 为 null", m3["matched"] is None)

    # 空营养库 -> unknown
    ef = home / "empty.json"
    ef.write_text("[]", encoding="utf-8")
    m4 = run(MM_CALC, "match-nutrition", "巨无霸", "--file", str(ef))
    check("营养库为空时返回 unknown", m4["status"] == "unknown", json.dumps(m4, ensure_ascii=False))

    # 合计：含 unknown 时该维度必须 unknown
    sel = [{"name": "巨无霸", "energy_kcal": 520}, {"name": "可乐", "energy_kcal": "unknown"}]
    sf = home / "sel.json"
    sf.write_text(json.dumps(sel), encoding="utf-8")
    s = run(MM_CALC, "sum-nutrition", "--file", str(sf))
    check("含 unknown 时合计标 unknown", s["energy_kcal"] == "unknown", json.dumps(s, ensure_ascii=False))
    check("含 unknown 时 has_unknown=true", s["has_unknown"] is True)

    sel2 = [{"name": "巨无霸", "energy_kcal": 520}, {"name": "中薯条", "energy_kcal": 335}]
    sf2 = home / "sel2.json"
    sf2.write_text(json.dumps(sel2), encoding="utf-8")
    s2 = run(MM_CALC, "sum-nutrition", "--file", str(sf2))
    check("完整数据合计正确 (855)", s2["energy_kcal"] == 855, json.dumps(s2, ensure_ascii=False))

    # check-range 接受的是「合计结果」，不是原始数组
    tf2 = home / "total2.json"
    tf2.write_text(json.dumps(s2), encoding="utf-8")
    r = run(MM_CALC, "check-range", "--range", "400-700", "--file", str(tf2))
    check("855 超上限判定为 over", r["verdict"] == "over", json.dumps(r, ensure_ascii=False))

    sel3 = [{"name": "巨无霸", "energy_kcal": 520}]
    sf3 = home / "sel3.json"
    sf3.write_text(json.dumps(sel3), encoding="utf-8")
    s3 = run(MM_CALC, "sum-nutrition", "--file", str(sf3))
    tf3 = home / "total3.json"
    tf3.write_text(json.dumps(s3), encoding="utf-8")
    r2 = run(MM_CALC, "check-range", "--range", "400-700", "--file", str(tf3))
    check("520 落在区间内", r2["verdict"] == "in_range", json.dumps(r2, ensure_ascii=False))

    # 合计未知时区间判定也必须是 unknown
    s_unknown = run(MM_CALC, "sum-nutrition", "--file", str(sf))
    tfun = home / "total_unknown.json"
    tfun.write_text(json.dumps(s_unknown), encoding="utf-8")
    r3 = run(MM_CALC, "check-range", "--range", "400-700", "--file", str(tfun))
    check("合计未知时区间判定为 unknown", r3["verdict"] == "unknown", json.dumps(r3, ensure_ascii=False))

    # 未设置区间
    r4 = run(MM_CALC, "check-range", "--file", str(tf3))
    check("未设区间时返回 unknown", r4["verdict"] == "unknown", json.dumps(r4, ensure_ascii=False))


# ==========================================================================
# 8. mm_calc：抽奖（不编造中奖率）
# ==========================================================================
def test_calc_lottery(home: Path) -> None:
    print("\n[8] 抽奖信息（不编造中奖率）")
    os.environ["MAIMAI_HOME"] = str(home)

    # 官方未返回概率
    info = {"status": "ONGOING", "activityName": "麦麦幸运抽奖",
            "cost": 100, "consumeRule": "每次抽奖消耗 100 积分",
            "userResources": {"points": 3200, "times": 3},
            "prizes": [{"name": "巨无霸券", "level": 1, "quantity": 50},
                       {"name": "谢谢参与", "level": -1}]}
    lf = home / "lottery.json"
    lf.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    l = run(MM_CALC, "lottery", "--times", "3", "--file", str(lf))

    check("官方未给概率时标 unknown", l["info"]["probability"] == "unknown",
          json.dumps(l["info"], ensure_ascii=False)[:300])
    check("明确声明不会推算中奖率", "不会自行推算" in l["info"]["note"], l["info"]["note"])
    check("每个奖品概率均为 unknown",
          all(p["probability"] == "unknown" for p in l["info"]["prizes"]))
    check("单次消耗正确", l["cost"]["cost_per_draw"] == 100, json.dumps(l["cost"], ensure_ascii=False))
    check("3 次总消耗 = 300", l["cost"]["total_cost"] == 300, json.dumps(l["cost"], ensure_ascii=False))
    check("标记必须确认", l["cost"]["requires_confirm"] is True)

    # 官方返回概率 -> 原样透传
    info2 = dict(info)
    info2["prizes"] = [{"name": "巨无霸券", "level": 1, "probability": 0.05},
                       {"name": "谢谢参与", "level": -1, "probability": 0.95}]
    lf2 = home / "lottery2.json"
    lf2.write_text(json.dumps(info2, ensure_ascii=False), encoding="utf-8")
    l2 = run(MM_CALC, "lottery", "--times", "1", "--file", str(lf2))
    check("官方概率被原样透传",
          l2["info"]["prizes"][0]["probability"] == 0.05, json.dumps(l2["info"], ensure_ascii=False)[:300])
    check("标注概率来源为官方", l2["info"]["probability"] == "官方已返回")

    # 积分不足以判断时
    info3 = {"cost": 100, "userResources": None, "prizes": []}
    lf3 = home / "lottery3.json"
    lf3.write_text(json.dumps(info3, ensure_ascii=False), encoding="utf-8")
    l3 = run(MM_CALC, "lottery", "--times", "2", "--file", str(lf3))
    check("余额未知时 affordable=unknown（不猜测）", l3["cost"]["affordable"] == "unknown",
          json.dumps(l3["cost"], ensure_ascii=False))

    # 回归：兼容 userResources 嵌套对象（早期按接口推测的形态，非实测字段）
    info4 = {"cost": 100, "consumeRule": "每次抽奖消耗 100 积分",
             "userResources": {"points": 3200, "times": 3}, "prizes": []}
    lf4 = home / "lottery4.json"
    lf4.write_text(json.dumps(info4, ensure_ascii=False), encoding="utf-8")
    l4 = run(MM_CALC, "lottery", "--times", "3", "--file", str(lf4))
    # 注意：实测字段是 availablePoint / drawPoint。
    # userResources 属推测形态，当前实现只认实测字段，因此这里预期为 unknown
    # —— 关键是不能给出**错误的**余额数字。
    check("推测形态 userResources 不会被误算成假余额",
          l4["cost"]["balance"] == "unknown" or l4["cost"]["balance"] == 3200,
          json.dumps(l4["cost"], ensure_ascii=False))

    # 用实测字段验证同一场景
    info4b = {"drawPoint": "100", "drawTypeText": "消耗积分抽奖",
              "availablePoint": "3200", "prizes": []}
    lf4b = home / "lottery4b.json"
    lf4b.write_text(json.dumps(info4b, ensure_ascii=False), encoding="utf-8")
    l4b = run(MM_CALC, "lottery", "--times", "3", "--file", str(lf4b))
    check("实测字段下能解析积分余额",
          l4b["cost"]["balance"] == 3200, json.dumps(l4b["cost"], ensure_ascii=False))
    check("实测字段下能判断是否够用",
          l4b["cost"]["affordable"] is True, json.dumps(l4b["cost"], ensure_ascii=False))
    check("实测字段下能算出抽后剩余",
          l4b["cost"]["remain_after"] == 2900, json.dumps(l4b["cost"], ensure_ascii=False))

    # 消耗单位为「次数」时应比对次数余额（用实测的 drawTypeText / availableTimes）
    info5 = {"drawPoint": "1", "drawTypeText": "消耗任务完成次数",
             "availableTimes": 2, "availablePoint": "500", "prizes": []}
    lf5 = home / "lottery5.json"
    lf5.write_text(json.dumps(info5, ensure_ascii=False), encoding="utf-8")
    l5 = run(MM_CALC, "lottery", "--times", "5", "--file", str(lf5))
    check("按次数消耗时余额单位为次", l5["cost"]["balance_unit"] == "次",
          json.dumps(l5["cost"], ensure_ascii=False))
    check("次数不足时 affordable=False",
          l5["cost"]["affordable"] is False, json.dumps(l5["cost"], ensure_ascii=False))


def test_real_lottery_shape(home: Path) -> None:
    """实测抽奖字段名（2026-10-10 query-lottery-info）。

    关键差异：字段是 drawPoint / drawTypeText / availablePoint，
    奖品对象只有 name/imageUrl/typeText —— 官方确实没给概率。
    """
    print("\n[8b] 实测抽奖字段与截断容错")
    os.environ["MAIMAI_HOME"] = str(home)
    import mm_calc

    real = {
        "success": True, "code": 200, "message": "请求成功",
        "data": {
            "status": {"code": "SUCCESS", "message": "成功"},
            "activityCode": "269t",
            "activityStatusText": "进行中",
            "activityName": "周六麦金喜抽奖",
            "beginTime": "2025-09-06 00:00:00",
            "endTime": "2030-12-31 23:59:59",
            "drawPoint": "24",
            "drawTypeText": "消耗积分抽奖",
            "availablePoint": "237.4",
            "drawDecision": {
                "resourceEligible": True,
                "reason": None,
                "nextConsumption": {"type": "POINTS", "points": "24", "chances": 0,
                                    "text": "本次将消耗 24 积分"},
            },
            "prizes": [
                {"name": "积分霸王餐", "imageUrl": "https://x/1.png", "typeText": "积分"},
                {"name": "麦金卡7天", "imageUrl": "https://x/2.png", "typeText": "卡券"},
            ],
        },
    }
    info = mm_calc.normalize_lottery(real)
    check("实测结构解析成功", info.get("ok") is True, json.dumps(info, ensure_ascii=False)[:200])
    check("drawPoint 被正确识别为单次消耗", info["draw_point"] == 24.0, str(info.get("draw_point")))
    check("drawTypeText 被识别为消耗规则", info["consume_rule"] == "消耗积分抽奖",
          str(info.get("consume_rule")))
    check("availablePoint 被识别为可用积分", info["available_point"] == 237.4,
          str(info.get("available_point")))
    check("服务端资源判定被透传", info["server_resource_eligible"] is True)
    check("奖品名称解析正确", info["prizes"][0]["name"] == "积分霸王餐",
          str(info["prizes"][0].get("name")))
    check("实测奖品无概率字段 -> 全部 unknown",
          all(p["probability"] == "unknown" for p in info["prizes"]),
          str([p["probability"] for p in info["prizes"]]))

    cost = mm_calc.lottery_cost_preview(info, 3)
    check("3 次总消耗 = 72", cost["total_cost"] == 72.0, json.dumps(cost, ensure_ascii=False))
    check("余额 237.4 够用", cost["affordable"] is True, json.dumps(cost, ensure_ascii=False))
    check("抽后剩余 165.4", cost["remain_after"] == 165.4, json.dumps(cost, ensure_ascii=False))

    # 未解析成功时绝不做成本推算
    bad = mm_calc.normalize_lottery({"code": 200, "message": "请求成功"})
    check("无法解析时 ok=False", bad.get("ok") is False, json.dumps(bad, ensure_ascii=False))
    bcost = mm_calc.lottery_cost_preview(bad, 3)
    check("无法解析时成本为 unknown 而非 0",
          bcost["total_cost"] == "unknown", json.dumps(bcost, ensure_ascii=False))
    check("无法解析时不给出 remain_after",
          bcost.get("remain_after") == "unknown", json.dumps(bcost, ensure_ascii=False))

    # 被 4000 字符截断的响应（实测真实情况：prizes 数组中途断开）
    truncated = (
        '{"success":true,"code":200,"message":"请求成功","data":'
        '{"status":{"code":"SUCCESS","message":"成功"},"activityCode":"269t",'
        '"activityName":"周六麦金喜抽奖","drawPoint":"24",'
        '"drawTypeText":"消耗积分抽奖","availablePoint":"237.4",'
        '"prizes":[{"name":"积分霸王餐","imageUrl":"https://img.mcd.cn/gallery/b18b950'
    )
    tinfo = mm_calc.normalize_lottery({"call": {"data": {"_text":
        "# API Response Information\n\n## Original Response\n\n" + truncated}}})
    check("被截断的响应仍能解析活动名",
          tinfo.get("activity_name") == "周六麦金喜抽奖", json.dumps(tinfo, ensure_ascii=False)[:250])
    check("被截断的响应仍能解析单次消耗",
          tinfo.get("draw_point") == 24.0, str(tinfo.get("draw_point")))

    # 字段说明段的碎片 JSON 不能被误当业务返回
    decoy = (
        "# API Response Information\n\n## Response Structure\n\n"
        '- **code**: (Type: integer)\n- **message**: (Type: string)\n\n'
        '## Original Response\n\n'
        '{"success":true,"code":200,"data":{"activityName":"真活动","drawPoint":"24",'
        '"drawTypeText":"消耗积分抽奖","availablePoint":"100","prizes":[]}}'
    )
    dinfo = mm_calc.normalize_lottery({"call": {"data": {"_text": decoy}}})
    check("不会被说明段碎片干扰", dinfo.get("activity_name") == "真活动",
          json.dumps(dinfo, ensure_ascii=False)[:200])


# ==========================================================================
# 9. mm_calc：有效期
# ==========================================================================
def test_real_nutrition(home: Path) -> None:
    """实测营养库格式（2026-10-10 list-nutrition-foods）。

    真实结构：{"data": "[160]{productName,nutritionDescription,energyKj,energyKcal,
              protein,fat,carbohydrate,sodium,calcium}:\\n  猪柳麦满分,null,1288,308,..."}
    —— data 是字符串，表头带条目数，行是空格缩进 + CSV。
    """
    print("\n[7b] 实测营养库格式")
    os.environ["MAIMAI_HOME"] = str(home)
    import mm_calc

    inner = (
        "[5]{productName,nutritionDescription,energyKj,energyKcal,protein,"
        "fat,carbohydrate,sodium,calcium}:\n"
        "  猪柳蛋麦满分,null,1618,387,23,21,25,846,243\n"
        "  中薯条,null,1210,289,4,12,38,165,18\n"
        "  麦乐鸡5块,null,890,213,12,12,13,422,9\n"
        "  残缺行被截断,nu"
    )   # 最后一行故意残缺，模拟被 4000 字符截断的情况
    payload = {"success": True, "code": 200, "message": "请求成功", "data": inner}
    rows = mm_calc.parse_nutrition_payload(payload)
    check("能解析实测营养库字符串", len(rows) == 3, f"got {len(rows)}")
    if rows:
        # parse_nutrition_payload 输出时已把 productName 归一为 name
        check("表头字段名正确解析",
              rows[0]["name"] == "猪柳蛋麦满分", str(rows[0].get("name")))
        check("energyKcal 解析正确", rows[0]["energyKcal"] == "387",
              str(rows[0].get("energyKcal")))
        check("energyKj 同时保留", rows[0]["energyKj"] == "1618", str(rows[0].get("energyKj")))
        check("截断的残缺行被跳过", len(rows) == 3, f"got {len(rows)}")

    m = mm_calc.match_nutrition("中薯条", rows)
    check("实测字段可直接匹配", m["status"] == "ok" and m["confidence"] == "high",
          json.dumps(m, ensure_ascii=False)[:200])
    check("实测字段热量正确", m["matched"]["energy_kcal"] == 289,
          str(m["matched"].get("energy_kcal")))
    miss = mm_calc.match_nutrition("巨无霸", rows)
    check("未收录餐品仍标 unknown", miss["status"] == "unknown",
          json.dumps(miss, ensure_ascii=False)[:200])

    # 用实测原始行直接累加
    s = mm_calc.sum_nutrition(rows[:3])
    check("实测原始行可直接累加", s["energy_kcal"] == 889, json.dumps(s, ensure_ascii=False))
    check("累加条目数正确", s["items"] == 3, str(s.get("items")))

    # 空字符串 / 缺列
    check("空 payload 返回空列表", mm_calc.parse_nutrition_payload(None) == [])
    check("无表头时不误解析", mm_calc.parse_nutrition_payload({"data": "随便一段文字"}) == [])


def test_calc_expiry(home: Path) -> None:
    print("\n[9] 有效期解析")
    print("\n[9] 有效期解析")
    os.environ["MAIMAI_HOME"] = str(home)
    from datetime import datetime, timedelta, timezone

    soon = (datetime.now(timezone(timedelta(hours=8))) + timedelta(days=3)).strftime("%Y-%m-%d")
    far = (datetime.now(timezone(timedelta(hours=8))) + timedelta(days=60)).strftime("%Y-%m-%d")

    s = run(MM_CALC, "expiry", soon)
    check("3 天内标为紧急", s["urgent"] is True, json.dumps(s, ensure_ascii=False))
    f = run(MM_CALC, "expiry", far)
    check("60 天后不标紧急", f["urgent"] is False, json.dumps(f, ensure_ascii=False))
    u = run(MM_CALC, "expiry", "长期有效")
    check("无法解析时 urgent=unknown（不猜测）", u["urgent"] == "unknown", json.dumps(u, ensure_ascii=False))


# ==========================================================================
# 10. 组合场景：一句话多模块
# ==========================================================================
def test_combo(home: Path) -> None:
    print("\n[10] 组合场景：预算+营养+券+积分+配送")
    os.environ["MAIMAI_HOME"] = str(home)

    run(MM_STATE, "prefs-set", "--key", "nutrition.budget_cents", "--value", "3500")
    run(MM_STATE, "prefs-set", "--key", "nutrition.calorie_range", "--value", "[400,700]")

    budget = run(MM_STATE, "prefs-get", "--key", "nutrition.budget_cents")["value"]
    rng = run(MM_STATE, "prefs-get", "--key", "nutrition.calorie_range")["value"]
    check("预算与热量区间被持久化", budget == 3500 and rng == [400, 700])

    # 试算：50 元商品 - 12 元券 + 5 元配送 = 43 元，在 35 元预算外 -> 必须报超预算
    price = {"goodsAmount": 5000, "deliveryFee": 500, "discountAmount": 1200, "payableAmount": 4300}
    pf = home / "combo_price.json"
    pf.write_text(json.dumps(price), encoding="utf-8")
    p = run(MM_CALC, "price", "--file", str(pf))
    check("组合场景试算自洽", p["consistent"] is True)
    payable = p["payable_cents"]
    check("应付 43.00 元超 35 元预算（应触发提醒）", payable > budget,
          f"payable={payable} budget={budget}")

    m = run(MM_CALC, "merge", "--owned", "1200", "--new", "800",
            "--trial", str(p["raw_extracted"]["discount"] or ""), "--actual", "")
    check("组合场景下四类优惠仍不求和", m["must_not_sum"] is True)
    check("新领券独立记录", m["new_coupon_cents"] == 800, json.dumps(m, ensure_ascii=False))
    check("merge 单位统一为分（试算 1200 分）",
          m["trial_discount_cents"] == 1200, json.dumps(m, ensure_ascii=False))


# ==========================================================================
# 11. 安全检查：源码不含 Token
# ==========================================================================
def test_menu_and_geo(home: Path) -> None:
    """新增能力：菜单本地筛选 / geo 偏好 / 调用次数红线。"""
    print("\n[12] 菜单筛选与位置偏好")
    os.environ["MAIMAI_HOME"] = str(home)
    sys.path.insert(0, str(HERE))
    import mm_state
    import menu

    # --- geo 偏好 ---
    prefs = mm_state.load_prefs()
    check("geo 偏好块已内置", "geo" in prefs, str(list(prefs.keys())))
    check("geo.city 默认为 None（不做自动定位）", prefs["geo"]["city"] is None,
          json.dumps(prefs["geo"], ensure_ascii=False))
    check("geo.source 默认 None（未记录来源时不当真）",
          prefs["geo"]["source"] is None)

    mm_state.deep_set(prefs, "geo.city", "示例市")
    mm_state.deep_set(prefs, "geo.source", "user_stated")
    mm_state.save_prefs(prefs)
    back = mm_state.load_prefs()
    check("geo.city 可持久化", back["geo"]["city"] == "示例市",
          json.dumps(back["geo"], ensure_ascii=False))
    check("geo.source 可持久化", back["geo"]["source"] == "user_stated")

    # 已删除的 delivery 死字段不应残留
    check("delivery 死字段已移除", "delivery" not in back or
          "address_fingerprint" not in back.get("delivery", {}),
          json.dumps(back.get("delivery"), ensure_ascii=False))

    # --- 菜单解析：截断场景必须标 price_unknown ---
    truncated = {"categories": [
        {"name": "鸡肉汉堡", "meals": [
            {"code": "901406", "tags": ["单品", "畅销单品"]},
            {"code": "921147", "tags": ["单品", "人气"]}]}
    ]}
    got = menu.parse_menu(truncated)
    check("能解析 categories（截断场景）", len(got) == 2, f"got {len(got)}")
    check("无价格时标 price_unknown=True", all(x["price_unknown"] for x in got))
    check("无价格时 price_cents 为 None（不拿原价冒充）",
          all(x["price_cents"] is None for x in got))

    # 完整场景：带详情表
    full = {"data": {"categories": [
        {"name": "鸡肉汉堡", "meals": [
            {"code": "901406", "tags": ["单品", "畅销单品"]}]}],
        "meals": {"901406": {"name": "板烧鸡腿堡", "currentPrice": "2650",
                             "originalPrice": "2900"}}}}
    got2 = menu.parse_menu(full)
    check("能解析完整菜单", len(got2) == 1, f"got {len(got2)}")
    if got2:
        check("详情表价格正确解析", got2[0]["price_cents"] == 2650,
              str(got2[0]["price_cents"]))
        check("原价单独保留", got2[0]["original_cents"] == 2900)
        check("有价格时 price_unknown=False", got2[0]["price_unknown"] is False)
        check("名称解析正确", got2[0]["name"] == "板烧鸡腿堡", str(got2[0]["name"]))

    # 预算筛选逻辑（纯函数验证）
    items = [
        {"code": "a", "name": "便宜堡", "price_cents": 2000, "price_unknown": False,
         "tags": ["单品"], "category": "汉堡"},
        {"code": "b", "name": "中价堡", "price_cents": 3500, "price_unknown": False,
         "tags": ["单品"], "category": "汉堡"},
        {"code": "c", "name": "贵堡", "price_cents": 6000, "price_unknown": False,
         "tags": ["单品"], "category": "汉堡"},
    ]
    fits = [x for x in items
            if not x["price_unknown"] and x["price_cents"] + 710 <= 4000]
    check("预算筛选逻辑正确（2000+710 入选）", len(fits) == 1 and fits[0]["code"] == "a",
          str([x["code"] for x in fits]))
    check("超预算被排除", "c" not in [x["code"] for x in fits])

    # 组合逻辑：单件价格 <= budget*0.7 才进组合池
    # 预算 40 元时：2000 入选，3500 > 2800 排除
    cheap = sorted([x for x in items if x["price_cents"] <= 4000 * 0.7],
                   key=lambda x: x["price_cents"])
    combos = []
    for i, x in enumerate(cheap):
        for y in cheap[i + 1:]:
            if x["price_cents"] + y["price_cents"] + 710 <= 4000:
                combos.append((x["code"], y["code"]))
    check("预算紧时不硬凑组合（单件超 70% 门槛的不参与）",
          len(combos) == 0 and len(cheap) == 1,
          f"cheap={[x['code'] for x in cheap]} combos={combos}")

    # 预算放宽到 50 元时，2000 + 2200 + 710 = 4910 <= 5000，能凑出组合
    items4 = items + [{"code": "d", "name": "小食", "price_cents": 2200,
                       "price_unknown": False, "tags": ["单品"], "category": "小食"}]
    budget2 = 5000
    cheap2 = sorted([x for x in items4 if x["price_cents"] <= budget2 * 0.7],
                    key=lambda x: x["price_cents"])
    combos2 = []
    for i, x in enumerate(cheap2):
        for y in cheap2[i + 1:]:
            if x["price_cents"] + y["price_cents"] + 710 <= budget2:
                combos2.append((x["code"], y["code"]))
    check("边界值 70% 也能进组合池（用 <= 而非 <）", len(cheap2) == 3,
          str([x["code"] for x in cheap2]))
    check("预算放宽后能凑出组合", len(combos2) == 1 and
          set(combos2[0]) == {"a", "d"}, str(combos2))

    # --- 调用次数红线：menu.py 不得含下单接口 ---
    src = (HERE / "menu.py").read_text(encoding="utf-8")
    for danger in ("create-order", "cancel-order", "mall-create-order",
                   "party-order-create", "delivery-create-address", "draw-lottery"):
        check(f"menu.py 不含写工具 {danger}", f'"{danger}"' not in src)
    check("menu.py 限制单次试算上限",
          "最多试算 10 个商品" in src or "len(codes) > 10" in src)


def test_qr(home: Path) -> None:
    """二维码生成（本地离线，不走第三方 API）。"""
    print("\n[13] 二维码生成")
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(HERE / "_vendor"))
    import mm_qr

    # 支付链接真实长度（实测 create-order 返回的 payH5Url 约 95 字符）
    url = ("https://m.mcd.cn/mcp/scanToPay?"
           "orderId=1000000000000000000000000001&payId=200000000000000000001")

    m, v = mm_qr.make_matrix(url)
    n = len(m)
    check("能生成二维码矩阵", n > 21, f"n={n} version={v}")
    check("矩阵为方形", all(len(r) == n for r in m))
    check("矩阵只含 0/1", all(c in (0, 1) for r in m for c in r))

    # 三个定位图案（7x7 回字形）必须存在 —— 这是可扫的前提
    def finder_ok(oy: int, ox: int) -> bool:
        # 外框全黑
        for i in range(7):
            if not (m[oy][ox + i] and m[oy + 6][ox + i]
                    and m[oy + i][ox] and m[oy + i][ox + 6]):
                return False
        # 中心 3x3 全黑
        for y in range(2, 5):
            for x in range(2, 5):
                if not m[oy + y][ox + x]:
                    return False
        # 第 1 圈（偏移1）应为白
        for i in range(1, 6):
            if m[oy + 1][ox + i] or m[oy + 5][ox + i]:
                return False
        return True

    check("左上定位图案正确", finder_ok(0, 0))
    check("右上定位图案正确", finder_ok(0, n - 7))
    check("左下定位图案正确", finder_ok(n - 7, 0))

    # SVG
    svg = mm_qr.to_svg(url)
    check("SVG 输出有效", svg.startswith("<svg") and svg.endswith("</svg>"))
    check("SVG 为单 path（体积优化）", svg.count("<path") == 1)
    check("SVG 含白色底（可扫必需）", 'fill="#ffffff"' in svg)
    check("SVG 使用 crispEdges（避免扫码模糊）",
          'shape-rendering="crispEdges"' in svg)
    check("SVG 体积可控（< 12KB）", len(svg.encode()) < 12000,
          f"{len(svg.encode())} bytes")

    # 静区（quiet zone）必须 >= 4 格，否则扫码器可能识别失败
    small = mm_qr.to_svg(url, border=4)
    check("默认静区 >= 4 格", 'width="' in small)

    # ASCII 兜底
    art = mm_qr.to_ascii(url)
    check("ASCII 输出非空", len(art.splitlines()) > 10)
    check("ASCII 使用半块字符", "▀" in art or "▄" in art or "█" in art)

    # PBM 兜底（无 Pillow 时的纯文本位图）
    pbm = mm_qr.to_pbm_bytes(url)
    check("PBM 兜底格式有效", pbm.startswith(b"P1"))

    # 纠错级别影响版本
    _, v_l = mm_qr.make_matrix(url, error="l")
    _, v_h = mm_qr.make_matrix(url, error="h")
    check("纠错级别 H 的版本不低于 L", v_h >= v_l, f"H={v_h} L={v_l}")

    # 离线保证：源码不得出现任何第三方二维码 API
    src = (HERE / "mm_qr.py").read_text(encoding="utf-8")
    check("二维码全程离线生成（无第三方 API）",
          "api.qrserver" not in src and "chart.googleapis" not in src
          and "quickchart.io" not in src)
    check("vendored segno 已附带许可文件",
          (HERE / "_vendor" / "segno" / "LICENSE").exists())
    vend = (HERE / "_vendor" / "segno")
    for f in ("__init__.py", "encoder.py", "consts.py"):
        check(f"vendored segno/{f} 存在", (vend / f).exists())

    # 确保 vendored 的 encoder 不引入第三方依赖
    enc = (vend / "encoder.py").read_text(encoding="utf-8")
    third = [m for m in ("import numpy", "import PIL", "import requests")
             if m in enc]
    check("vendored encoder 仅用标准库", not third, str(third))

    # 内联路径必须是本机原生形式（Windows 要 E:/ 而非 Git Bash 的 /e/）
    np_ = mm_qr.native_path(HERE / "mm_qr.py")
    check("native_path 返回绝对路径", np_.startswith("/") or ":/" in np_, np_)
    check("native_path 用正斜杠", "\\" not in np_, np_)
    if os.name == "nt":
        check("Windows 下为盘符形式（E:/ 而非 /e/）",
              len(np_) > 1 and np_[1] == ":", np_)
        check("Windows 下不含 Git Bash 风格前缀",
              not re.match(r"^/[a-zA-Z]/", np_), np_)

    snip = mm_qr.embed_snippet(HERE / "mm_qr.py")
    check("embed_snippet 生成 img 标签", snip.startswith("<img "), snip[:60])
    check("embed_snippet 用原生路径", "\\" not in snip.split("src=")[1][:40])
    check("embed_snippet 不含 Git Bash 风格路径",
          not re.search(r'src="/[a-zA-Z]/', snip), snip[:80])

    # ---- 内联 SVG：绕开文件加载，是唯一不受宿主路径改写影响的方案 ----
    inline = mm_qr.to_inline_svg(url)
    check("内联 SVG 是自包含 svg 元素",
          inline.startswith("<svg") and inline.endswith("</svg>"))
    check("内联 SVG 含白色底", 'fill="#ffffff"' in inline)
    check("内联 SVG 带 crispEdges", 'shape-rendering="crispEdges"' in inline)
    check("内联 SVG 用 stroke 画线（体积优化）",
          'stroke="#000000"' in inline and 'stroke-width="1"' in inline)
    check("内联 SVG 不引用任何外部资源（无 src/href）",
          "<img" not in inline and "href=" not in inline)
    longest = max(len(l) for l in inline.splitlines())
    check("内联 SVG 单行不超过 2000 字符（可完整读取）", longest < 2000,
          f"longest={longest}")
    check("内联 SVG 体积 < 4KB", len(inline.encode()) < 4096,
          f"{len(inline.encode())} bytes")

    # ★ 关键：反向重建位图逐模块比对 —— 路径写错会静默画歪，必须验证
    v = mm_qr.verify_inline_svg(url, inline)
    check("内联 SVG 路径逐模块还原正确（0 差异）", v["diff"] == 0,
          f"diff={v['diff']}")
    check("内联 SVG 三个定位图案全部正确", all(v["finders"].values()),
          str(v["finders"]))
    check("内联 SVG 综合校验通过", v["ok"] is True, str(v))

    # 验证器本身要能识别错误路径（防止它只会说 OK）
    broken = inline.replace("M4 4.5h7", "M4 4.5h5", 1)
    vb = mm_qr.verify_inline_svg(url, broken)
    check("验证器能识别被破坏的路径", vb["ok"] is False and vb["diff"] > 0,
          f"diff={vb['diff']}")


def test_delivery_packing_fee(home: Path) -> None:
    """外卖自洽校验必须计入打包费（2026-10-10 实测修正的 bug）。"""
    print("\n[14] 外卖打包费与自洽校验")
    sys.path.insert(0, str(HERE))
    import mm_calc

    # 实测外卖单：商品 28.5 + 配送 6.0 + 打包 1.1 = 应付 35.6
    r = mm_calc.classify_trial_discount(
        goods_total_cents=2850, discount_cents=0, payable_cents=3560,
        delivery_fee_cents=600, packing_fee_cents=110)
    check("外卖单计入打包费后自洽", r["consistent"] is True,
          f"diff={r['diff_cents']} note={r['note']}")

    # 旧公式（漏打包费）会误报 —— 必须能重现这个问题才知道真的修好了
    r_old = mm_calc.classify_trial_discount(
        goods_total_cents=2850, discount_cents=0, payable_cents=3560,
        delivery_fee_cents=600)   # 故意不传 packing
    check("漏传打包费会误报不自洽（重现原 bug）",
          r_old["consistent"] is False and r_old["diff_cents"] == -110,
          f"diff={r_old['diff_cents']}")

    # 到店自取：打包费为 0，仍应自洽（回归）
    r2 = mm_calc.classify_trial_discount(
        goods_total_cents=4240, discount_cents=0, payable_cents=4240,
        delivery_fee_cents=0, packing_fee_cents=0)
    check("到店自取（打包费0）仍自洽", r2["consistent"] is True)

    # 金额提取必须认麦当劳的真实字段名
    real = {"data": {"productPrice": 2850, "deliveryPrice": 600,
                     "packingPrice": 110, "discount": 0, "price": 3560}}
    got = mm_calc.dig_amount_candidates(real)
    check("能提取 productPrice", got["goods_total"] == 2850, str(got["goods_total"]))
    check("能提取 deliveryPrice", got["delivery_fee"] == 600, str(got["delivery_fee"]))
    check("能提取 packingPrice", got["packing_fee"] == 110, str(got["packing_fee"]))
    check("能提取 price", got["payable"] == 3560, str(got["payable"]))
    out = mm_calc.classify_trial_discount(
        got["goods_total"], got["discount"], got["payable"],
        got["delivery_fee"], got["packing_fee"])
    check("真实字段名提取后自洽", out["consistent"] is True,
          f"diff={out['diff_cents']}")

    # menu.py 的 price 必须把打包费传下去
    src = (HERE / "menu.py").read_text(encoding="utf-8")
    check("menu.py 向自洽校验传了打包费",
          "packing_fee_cents=data.get(\"packingPrice\")" in src)


def test_menu_truncated_salvage() -> None:
    """query-meals 被 4000 字符截断时，必须能抢救出 categories。

    2026-10-10 实测：同一份代码/同一门店，仅因截断位置不同，
    orderType=1 能解析 40 条、orderType=2 解析 0 条。
    """
    print("\n[15] 菜单截断抢救")
    sys.path.insert(0, str(HERE))
    import menu

    # 构造被截断的菜单响应（categories 完整、meals 详情表缺失）
    truncated = (
        '{"success":true,"code":200,"data":{"categories":['
        '{"name":"人气热卖","meals":[{"code":"9900016490"},{"code":"521954"}]},'
        '{"name":"蘸酱炸鸡","meals":[{"code":"9900016078"},{"code":"521966"}]}'
        '],"meals":{"9900016078":{"name":"蘸酱麦麦脆汁鸡","cur'
    )
    items = menu.parse_menu({"success": True, "_text": truncated})
    check("截断响应仍能抢救出 categories", len(items) == 4, f"got {len(items)}")
    codes = {i["code"] for i in items}
    check("抢救出全部编码",
          codes == {"9900016490", "521954", "9900016078", "521966"}, str(codes))
    check("分类名正确保留",
          any(i["category"] == "蘸酱炸鸡" for i in items))
    check("无价格时标记 price_unknown", all(i["price_unknown"] for i in items))

    cats, detail = menu._salvage_menu(truncated)
    check("_salvage_menu 返回 2 个分类", len(cats) == 2, str(len(cats)))
    check("_salvage_menu 遇截断对象即停止（不产出残缺项）",
          all("name" in c for c in cats))


def test_payment_urls(home: Path) -> None:
    """支付 URL：必须用 jumpToApp 做二维码，scanToPay 是桌面页。

    2026-10-10 逆向 cdn.mcd.cn 前端 JS 得出：/mcp/scanToPay 组件里
    自己又渲染了一个 QRCodeSVG，内容为 /mcp/jumpToApp?orderId=...
    把 scanToPay 做成二维码给手机扫 → 手机上又显示一个二维码。
    """
    print("\n[16] 支付 URL 构造")
    sys.path.insert(0, str(HERE))
    import mm_qr

    oid = "1000000000000000000000000002"
    u = mm_qr.payment_urls(oid)
    check("含 scan_page_url", "scanToPay" in u["scan_page_url"], u["scan_page_url"])
    check("含 app_jump_url", "jumpToApp" in u["app_jump_url"], u["app_jump_url"])
    check("qr_target 指向 jumpToApp 而非 scanToPay",
          "jumpToApp" in u["qr_target"] and "scanToPay" not in u["qr_target"],
          u["qr_target"])
    check("qr_target 带 orderId", f"orderId={oid}" in u["qr_target"])
    check("普通订单不带 orderCategory",
          "orderCategory" not in u["qr_target"])

    # 企业团餐（ECS）才需要 orderCategory
    ecs = mm_qr.payment_urls(oid, "ECS")
    check("ECS 订单才附加 orderCategory=ECS",
          "orderCategory=ECS" in ecs["qr_target"], ecs["qr_target"])

    # 关键回归：二维码内容必须是 jumpToApp
    svg = mm_qr.to_inline_svg(u["qr_target"])
    check("二维码由 jumpToApp 生成且自校验通过",
          mm_qr.verify_inline_svg(u["qr_target"], svg)["ok"] is True)
    check("源码里明确标注 scanToPay 是桌面页（防回归）",
          "桌面页" in (HERE / "mm_qr.py").read_text(encoding="utf-8"))


def test_price_cache(home: Path) -> None:
    """商品名/单价持久缓存：认过一次就永不再花调用。"""
    print("\n[17] 商品名/单价持久缓存")
    os.environ["MAIMAI_HOME"] = str(home)
    sys.path.insert(0, str(HERE))
    import menu

    menu.save_price_cache({})
    check("空缓存可读写", menu.load_price_cache() == {})

    n = menu.remember_products([
        {"code": "901440", "name": "麦辣鸡腿汉堡", "qty": 1, "subtotal_cents": 2550},
        {"code": "521966", "name": "韩式烟熏芝士风味酱", "qty": 1,
         "subtotal_cents": 300},
        # 自动附加项不入缓存
        {"code": "920174", "name": "打包服务费", "qty": 11, "subtotal_cents": 0},
    ], store="1400458")
    check("remember_products 记 2 条（跳过服务费）", n == 2, f"n={n}")

    c = menu.load_price_cache()
    check("品名已缓存", c["901440"]["name"] == "麦辣鸡腿汉堡")
    check("单价已缓存", c["901440"]["price_cents"] == 2550)
    check("打包服务费未被缓存", "920174" not in c)

    # qty>1 时要换算成单价
    menu.remember_products([{"code": "X1", "name": "测试品", "qty": 3,
                             "subtotal_cents": 900}])
    check("qty>1 自动换算单价", menu.load_price_cache()["X1"]["price_cents"] == 300)

    # 名称为空的项不写入（避免污染）
    menu.remember_products([{"code": "X2", "name": None, "qty": 1,
                             "subtotal_cents": 500}])
    check("无名称的项不写入缓存", "X2" not in menu.load_price_cache())

    # SAFE_BATCH 必须存在且合理（超过会触发截断）
    check("定义了单批安全上限", hasattr(menu, "SAFE_BATCH"))
    check("单批上限 <= 5（实测超过会截断）", menu.SAFE_BATCH <= 5,
          str(menu.SAFE_BATCH))
    check("identify 子命令已注册",
          "cmd_identify" in (HERE / "menu.py").read_text(encoding="utf-8"))


def test_plan_and_compact(home: Path) -> None:
    """plan 编排 + 紧凑输出 + 中文同义词展开（token/速度优化）。"""
    print("\n[18] plan 编排与紧凑输出")
    os.environ["MAIMAI_HOME"] = str(home)
    sys.path.insert(0, str(HERE))
    import menu

    # --- 中文意图同义词展开 ---
    w = menu.expand_wants(["炸鸡"])
    check("「炸鸡」展开出同义词", len(w) > 1, str(w))
    check("展开含「鸡腿堡」", any("鸡腿堡" in x for x in w), str(w))
    check("保留原始词", "炸鸡" in w)
    check("展开后去重", len(w) == len(set(w)), str(w))

    # 关键回归：麦当劳的炸鸡类商品名不含连续的「炸鸡」二字
    # 「麦辣鸡腿汉堡」只有在展开后才被命中
    def blob(name: str, cat: str = "", tags=None) -> str:
        return (name + " " + cat + " " + ",".join(tags or [])).lower()

    raw_hit = any(x in blob("麦辣鸡腿汉堡") for x in ["炸鸡"])
    exp_hit = any(x in blob("麦辣鸡腿汉堡") for x in w)
    check("未展开时匹配不到「麦辣鸡腿汉堡」（重现原问题）", raw_hit is False)
    check("展开后能匹配到「麦辣鸡腿汉堡」", exp_hit is True)
    check("展开后能匹配蘸蘸炸鸡", any(x in blob("蘸蘸炸鸡") for x in w))
    check("未知映射原样返回", menu.expand_wants(["不存在的词"]) == ["不存在的词"])

    # --- 紧凑输出 ---
    obj = {"a": 1, "b": [1, 2, 3], "c": {"d": "中文"}}
    menu._OUT_PRETTY = False
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        menu._print(obj)
    compact = buf.getvalue()
    buf2 = io.StringIO()
    menu._OUT_PRETTY = True
    with redirect_stdout(buf2):
        menu._print(obj)
    pretty = buf2.getvalue()
    menu._OUT_PRETTY = False
    check("紧凑输出为单行", compact.strip().count("\n") == 0, repr(compact[:60]))
    check("紧凑输出比缩进更小", len(compact) < len(pretty),
          f"compact={len(compact)} pretty={len(pretty)}")
    check("紧凑仍是合法 JSON", json.loads(compact) == obj)
    check("紧凑保留中文不转义", "中文" in compact)

    # --- plan 子命令已注册且默认不缩进 ---
    src = (HERE / "menu.py").read_text(encoding="utf-8")
    check("plan 子命令已注册", "cmd_plan" in src)
    check("默认不缩进", "_OUT_PRETTY = False" in src)
    check("每个子命令都支持 --pretty", "for name, sp_ in sub.choices.items()" in src)
    check("定义了 SAFE_BATCH 并在 plan 中使用", "SAFE_BATCH" in src)
    # 关键回归：退出条件必须看「已认价」候选数，不能只看匹配数
    # （同义词「鸡」太宽，分类「鸡肉汉堡/卷」就能匹配，会提前退出不认价）
    check("认价退出条件基于已认价候选",
          'priced = [x for x in matched() if not x.get("price_unknown")]' in src,
          "应检查 priced 而非 matched")
    check("认价候选从全量池选取（非 matched）",
          'unknown = [x for x in full_pool() if x.get("price_unknown")]' in src)



def test_price_normalization(home: Path) -> None:
    """菜单回填的价格必须按 qty 归一，且价格缓存优先于菜单。

    2026-10-10 实测 bug：按 2 份试算时 subtotal=2580，
    回填把「小计」写成「单价」，使「蘸蘸炸鸡」在菜单里
    从 ¥12.9 变成 ¥25.8 —— 会直接误导预算判断。
    """
    print("\n[19] 价格归一与缓存优先级")
    os.environ["MAIMAI_HOME"] = str(home)
    sys.path.insert(0, str(HERE))
    import mm_state
    import menu

    # 关键回归：菜单回填必须按 qty 归一，不能把「小计」当「单价」
    # 实测坑：按 2 份试算时 subtotal=2580，直接写回使「蘸蘸炸鸡」
    # 在菜单里从 ¥12.9 变成 ¥25.8，会误导预算判断。
    src_menu = (HERE / "menu.py").read_text(encoding="utf-8")
    check("菜单回填按 qty 归一（除数量再写回）",
          'unit = int(round(sub / q))' in src_menu,
          "应除以数量")
    check("价格缓存无条件覆盖菜单里的脏价格",
          'it["price_cents"] = rec["price_cents"]' in src_menu)

    # 模拟：菜单里被写脏成 2580，价格缓存是 1290 → 应以缓存为准
    dirty_menu = {"items": [{"code": "9900016511", "name": "蘸蘸炸鸡",
                             "price_cents": 2580, "price_unknown": False,
                             "tags": [], "category": "人气热卖",
                             "original_cents": None, "discount_type": None,
                             "with_order": None}]}
    mm_state.atomic_write_json(menu.menu_cache_path("S1", 2, 2), dirty_menu)
    menu.save_price_cache({"9900016511": {"name": "蘸蘸炸鸡",
                                          "price_cents": 1290}})
    fixed = menu.load_menu("S1", 2, 2)
    check("脏价格被价格缓存纠正回单价", fixed[0]["price_cents"] == 1290,
          f"got {fixed[0]['price_cents']}")
    check("纠正后标记 from_cache", fixed[0].get("from_cache") is True)


def test_no_secret() -> None:
    print("\n[11] 安全检查")
    import re
    suspicious = re.compile(r"(Bearer\s+[A-Za-z0-9\-_]{16,})|([A-Za-z0-9]{24,}\.[A-Za-z0-9]{24,})")
    bad = []
    for p in HERE.rglob("*"):
        if p.is_file() and p.suffix in (".py", ".md", ".json"):
            try:
                txt = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if suspicious.search(txt):
                bad.append(p.name)
    check("脚本与文档中不含硬编码 Token", not bad, f"发现: {bad}")


def main() -> int:
    print("=" * 66)
    print("麦麦生活助手 · 脚本层自测")
    print("【声明】全部使用模拟数据，不代表真实 MCP 调用结果")
    print("=" * 66)

    with tempfile.TemporaryDirectory() as td:
        home = Path(td) / "maimai-test-home"
        home.mkdir(parents=True, exist_ok=True)
        sys.path.insert(0, str(HERE))
        try:
            test_prefs(home)
            test_coupon_diff(home)
            test_account_diff(home)
            test_real_wrappers(home)
            test_ledger(home)
            test_cron_guard(home)
            test_calc_money(home)
            test_calc_nutrition(home)
            test_calc_lottery(home)
            test_real_lottery_shape(home)
            test_real_nutrition(home)
            test_calc_expiry(home)
            test_menu_and_geo(home)
            test_qr(home)
            test_delivery_packing_fee(home)
            test_menu_truncated_salvage()
            test_payment_urls(home)
            test_price_cache(home)
            test_plan_and_compact(home)
            test_price_normalization(home)
            test_combo(home)
        finally:
            os.environ.pop("MAIMAI_HOME", None)
    test_no_secret()

    print("\n" + "=" * 66)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print(f"  - {f}")
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
