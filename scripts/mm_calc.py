#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mm_calc.py — 麦麦生活助手 · 试算与核算层

核心原则：
  1. 金额一律用「分」（整数）计算，绝不用浮点累加，避免 0.1+0.2 类误差。
  2. 四类优惠严格分开，互不合并、互不冒充：
       owned_coupon  原有券（账户里本来就有的）
       new_coupon    本次自动领到的新券
       trial_discount 试算优惠（calculate-price 的预估，未下单）
       actual_discount 实际订单优惠（create-order 返回的实付口径）
  3. 营养数据匹配不上必须显式标注 unknown，禁止用估算值冒充实测值。
  4. 抽奖概率只透传官方返回值；官方未返回则标 unknown，绝不自行编造中奖率。
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Any, Dict, List, Optional, Tuple

UNKNOWN = "unknown"


# --------------------------------------------------------------------------
# 金额工具
# --------------------------------------------------------------------------

def to_cents(value: Any) -> Optional[int]:
    """把金额（数字或 "12.50" / "¥12.5"）转成整数分。无法解析返回 None。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value * 100 if abs(value) > 100000 else value  # 已是分的情况
    if isinstance(value, float):
        return int(round(value * 100))
    s = str(value).strip()
    s = re.sub(r"[¥￥,\s]", "", s)
    if not s:
        return None
    try:
        return int(round(float(s) * 100))
    except ValueError:
        return None


def cents_str(cents: Optional[int]) -> str:
    if cents is None:
        return UNKNOWN
    sign = "-" if cents < 0 else ""
    c = abs(cents)
    return f"{sign}{c // 100}.{c % 100:02d}"


def _get(d: Any, *names: str, default: Any = None) -> Any:
    if not isinstance(d, dict):
        return default
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


def _balanced_json_span(text: str, start: int) -> Optional[str]:
    """从 text[start] 处的 '{' 或 '[' 开始，取出**括号配平**的完整 JSON 片段。

    朴素的「找到第一个配平括号」会被字符串里的嵌套括号骗到：
    真实抽奖返回里 prizes[].name 是对象，文本说明段又有单独的 {...}，
    直接数括号会在中途截断，返回空对象。
    这里做字符串感知扫描：跳过字符串字面量内的括号。
    """
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    quote = ""
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _salvage_truncated_json(seg: str) -> Any:
    """从不完整/被截断的 JSON 片段里抢救出可用的部分。

    实测（2026-10-10 query-lottery-info）：接口返回的 Markdown 里
    Original Response 段的 JSON **可能本身就不完整** —— prizes 数组中途结束，
    整体括号不配平，直接 json.loads 必然失败。

    做法：扫描到末尾后，把所有**未闭合的容器**按相反顺序补上，
    并补掉可能未闭合的字符串。这样截断在数组中间的返回也能解析出
    已完整给出的字段（如 activityName / drawPoint / availablePoint）。

    实测：query-lottery-info 的响应被上游按 4000 字符截断，
    prizes 数组中途断开，直接 json.loads 必然失败。
    """
    start = seg.find("{")
    if start < 0:
        return None

    stack: List[str] = []      # 待闭合的容器，存闭合符
    in_str = False
    esc = False
    best: Optional[Dict[str, Any]] = None

    def _try(cand_text: str) -> Optional[Dict[str, Any]]:
        try:
            obj = json.loads(cand_text)
        except json.JSONDecodeError:
            return None
        return obj if isinstance(obj, dict) and obj else None

    for i in range(start, len(seg)):
        ch = seg[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch in "{[":
            stack.append("}" if ch == "{" else "]")
            continue
        if ch in "}]":
            if not stack:
                continue
            stack.pop()
            if not stack:
                got = _try(seg[start:i + 1])
                if got and len(got) > len(best or {}):
                    best = got
            continue

    if best:
        return best

    # 走到这里说明顶层没闭合（被截断）。按栈的逆序补齐后重试。
    tail_idx = len(seg)
    # 若末尾卡在未闭合字符串里，先补一个引号
    if in_str:
        body = seg[start:] + '"' + "".join(reversed(stack))
    else:
        # 截断点可能是 "key": "value" 这种半截值，补空串更安全
        # 先尝试直接闭合
        body = seg[start:] + "".join(reversed(stack))
        got = _try(body)
        if got:
            return got
        # 再尝试把末尾残缺的值补成空串
        cut = max(body.rfind(","), body.rfind(":"))
        if cut > 0:
            body2 = body[:cut + 1] + '"' + "".join(reversed(stack))
            got = _try(body2)
            if got:
                return got
            body3 = body[:cut + 1] + "null" + "".join(reversed(stack))
            got = _try(body3)
            if got:
                return got
        return None

    return _try(body)


def _score_candidate(obj: Any) -> int:
    """给候选 JSON 打分，分数越高越可能是真正的业务返回。

    实测踩过的坑：MCP 的 Markdown 里有**两段** JSON 形态的内容 ——
    字段说明段里散落着 {"code": ..., "message": ...} 之类的碎片，
    真正的业务数据在 "## Original Response" 之后。
    只取"第一个能解析的 JSON"会抓到说明段的碎片，导致后续全部字段为空。
    """
    if not isinstance(obj, dict):
        return -1
    score = 0
    if "data" in obj and isinstance(obj.get("data"), (dict, list)):
        score += 10          # 业务层特征
        inner = obj["data"]
        if isinstance(inner, dict):
            # 注意：len() 对 int 会抛 TypeError，必须先判类型
            score += min(len(inner), 8)
            if "prizes" in inner:
                score += 20   # 抽奖活动的决定性字段
            if "availablePoint" in inner:
                score += 10   # 积分账户特征
            if "storeCode" in inner or "items" in inner:
                score += 5
    if "success" in obj:
        score += 3
    if len(obj) <= 2 and set(obj.keys()) <= {"code", "message", "datetime", "traceId"}:
        score -= 15          # 典型空壳
    return score


def _extract_json(text: str) -> Any:
    """从 Markdown 文本里抠出内嵌 JSON（实测 MCP 用这种双段格式）。

    会收集所有候选并按 _score_candidate 择优，而不是「第一个能解析的」。
    """
    candidates: List[Any] = []

    # 优先扫描 "## Original Response" 段
    idx = text.find("## Original Response")
    segments: List[Tuple[str, int]] = []
    if idx >= 0:
        tail = text[idx + len("## Original Response"):].strip()
        if tail.startswith("```"):
            tail = tail[3:]
            if tail.lstrip().lower().startswith("json"):
                tail = tail.lstrip()[4:]
        if tail.endswith("```"):
            tail = tail[:-3]
        segments.append((tail.strip(), 1000))   # 高优先级
    segments.append((text, 0))                  # 全文兜底

    for seg, _prio in segments:
        if not seg:
            continue
        for i, ch in enumerate(seg):
            if ch not in "{[":
                continue
            span = _balanced_json_span(seg, i)
            if not span:
                continue
            try:
                obj = json.loads(span)
            except json.JSONDecodeError:
                continue
            candidates.append(obj)

    # 评分择优。注意：即使有碎片候选，若最好分数不足以判定为业务返回，
    # 仍要尝试「截断抢救」—— 实测抽奖接口响应被上游按 4000 字符截断，
    # 整体不配平，只会产出低分碎片。
    best = max(candidates, key=_score_candidate) if candidates else None
    if best is not None and _score_candidate(best) > 0:
        return best

    # 抢救路径
    for seg, _prio in segments:
        if not seg:
            continue
        got = _salvage_truncated_json(seg)
        if got is not None and _score_candidate(got) > 0:
            return got

    return best if best is not None else None


def _unwrap(obj: Any) -> Any:
    """剥掉 MCP 外层包装，拿到业务数据本体。

    实测有三层：
      1. {"call": {"data": {"_text": "<Markdown>"}}}  （probe_mcp 形态）
      2. {"success":true,"code":200,"data":{...}}      （业务形态）
      3. {...}                                          （数据本体）
    """
    cur = obj
    for _ in range(6):
        if isinstance(cur, str):
            j = _extract_json(cur)
            if j is None:
                return cur
            cur = j
            continue
        if isinstance(cur, (list, int, float)) or cur is None:
            return cur
        if not isinstance(cur, dict):
            return cur
        if isinstance(cur.get("_text"), str):
            cur = cur["_text"]
            continue
        if "call" in cur and isinstance(cur["call"], dict):
            cur = cur["call"].get("data", cur["call"])
            continue
        # 业务层 data 包装。
        # 注意不能见到 data 就剥：{"code":200,"message":"请求成功","data":null}
        # 这种空 data 剥了会丢信息；且要防止剥到只剩 code/message 的空壳。
        d = cur.get("data")
        if isinstance(d, (dict, list)) and len(d) > 0:
            cur = d
            continue
        return cur
    return cur


def dig_amount_candidates(raw: Any) -> Dict[str, Optional[int]]:
    """从 calculate-price / create-order 的返回里按候选键名抓金额。

    只做提取，不做推断。抓不到就留 None，由上层标 unknown。
    已接入 _unwrap，支持 MCP 的 Markdown + JSON 双段包装。

    ⚠️ 实测（2026-10-10）：麦当劳的实际字段名是
    `productPrice` / `deliveryPrice` / `packingPrice` / `discount` / `price`，
    原先的候选列表里**一个都没有**，导致 `mm_calc.py price` 对真实返回
    全部抓成 None。现已把这些实测名补到候选首位。
    """
    src = _unwrap(raw)
    if isinstance(src, str):
        return {"goods_total": None, "delivery_fee": None,
                "packing_fee": None, "discount": None, "payable": None}
    return {
        "goods_total": to_cents(_get(src, "productPrice", "goodsAmount",
                                     "goodsTotal", "totalPrice",
                                     "commodityAmount", "subTotal", "total")),
        "delivery_fee": to_cents(_get(src, "deliveryPrice", "deliveryFee",
                                      "shippingFee", "freight",
                                      "deliveryAmount")),
        "packing_fee": to_cents(_get(src, "packingPrice", "packingFee",
                                     "packageFee", "packingAmount")),
        "discount": to_cents(_get(src, "discount", "discountAmount",
                                  "reduceAmount", "preferentialAmount",
                                  "discountTotal", "preferAmount")),
        "payable": to_cents(_get(src, "price", "payableAmount", "payAmount",
                                 "actualAmount", "shouldPay", "totalPayable",
                                 "payPrice")),
    }


def classify_trial_discount(
    goods_total_cents: Optional[int],
    discount_cents: Optional[int],
    payable_cents: Optional[int],
    delivery_fee_cents: Optional[int] = None,
    packing_fee_cents: Optional[int] = None,
) -> Dict[str, Any]:
    """校验试算结果的自洽性。

    自洽条件：商品总额 - 优惠 + 配送费 + 打包费 == 应付

    ⚠️ **打包费必须有**（2026-10-10 实测修正）：
    原先公式漏了 `packingPrice`，到店自取场景打包费恒为 0 所以看不出来；
    一切到外卖（麦乐送）就必定误报「不自洽」——
    实测外卖 ¥35.6 的单子被错判为不自洽，差 1.10 元正好是打包费。

    不自洽时不强行纠正，只报告差异，由上层决定是否信任该次试算。
    """
    out: Dict[str, Any] = {
        "goods_total_cents": goods_total_cents,
        "discount_cents": discount_cents,
        "delivery_fee_cents": delivery_fee_cents,
        "packing_fee_cents": packing_fee_cents,
        "payable_cents": payable_cents,
        "kind": "trial_discount",
        "confirmed_by": "未下单前均为试算",
        "consistent": None,
        "note": "",
    }
    if goods_total_cents is None or payable_cents is None:
        out["note"] = "缺少商品总额或应付金额，无法核对"
        return out

    expected = (goods_total_cents - (discount_cents or 0)
                + (delivery_fee_cents or 0) + (packing_fee_cents or 0))
    diff = expected - payable_cents
    out["consistent"] = (diff == 0)
    out["expected_payable_cents"] = expected
    out["diff_cents"] = diff
    if diff != 0:
        out["note"] = (
            f"试算口径不自洽：按商品总额-优惠+配送费+打包费应为 {cents_str(expected)}，"
            f"接口返回应付 {cents_str(payable_cents)}，差 {cents_str(diff)} 元。"
            "以下单后实际返回为准。"
        )
    return out


def extract_actual_discount(order_raw: Any) -> Dict[str, Any]:
    """从 create-order / query-order 返回里提取实际优惠。

    口径说明：实际优惠 = 商品总额 - 实付金额（含配送费时需注意口径差异），
    这里同时保留接口直接给出的优惠字段（若有），优先采信接口。
    """
    src = _unwrap(order_raw)
    if not isinstance(src, dict):
        return {"kind": "actual_discount", "note": "无法解析订单返回结构",
                "discount_cents": None, "goods_total_cents": None, "payable_cents": None}

    goods = to_cents(_get(src, "goodsAmount", "goodsTotal", "totalPrice", "commodityAmount"))
    paid = to_cents(_get(src, "payAmount", "paidAmount", "actualPay", "payableAmount", "shouldPay", "totalPayable", "total"))
    direct = to_cents(_get(src, "discountAmount", "discount", "reduceAmount", "preferentialAmount"))
    fee = to_cents(_get(src, "deliveryFee", "shippingFee", "freight"))

    out: Dict[str, Any] = {
        "kind": "actual_discount",
        "goods_total_cents": goods,
        "payable_cents": paid,
        "delivery_fee_cents": fee,
        "discount_source": None,
        "discount_cents": None,
        "order_no": _get(src, "orderNo", "orderId", "orderCode", "id"),
        "pay_link": _get(src, "payUrl", "payLink", "paymentUrl", "payUrlText", "link"),
        "pickup_qrcode": _get(src, "pickupCode", "pickupQrCode", "pickupCodeUrl", "qrCode", "lockerCode"),
    }

    if direct is not None:
        out["discount_cents"] = direct
        out["discount_source"] = "接口直接返回"
    elif goods is not None and paid is not None:
        calc = goods - paid
        out["discount_cents"] = calc if calc > 0 else 0
        out["discount_source"] = "商品总额-实付推算"
        if fee:
            out["note"] = f"含配送费 {cents_str(fee)} 元；实际优惠口径可能为「商品总额-实付-配送费」，下单前请核对"
    else:
        out["note"] = "接口未返回足够金额字段，实际优惠无法核算"
    return out


def merge_discount_sources(
    owned_coupons_cents: int = 0,
    new_coupons_cents: int = 0,
    trial_discount_cents: Optional[int] = None,
    actual_discount_cents: Optional[int] = None,
) -> Dict[str, Any]:
    """把四类优惠并列输出，**不做求和**。

    求和会把「试算」和「实付」重复计算，也会把未核验的新券当成已用优惠。
    """
    return {
        "owned_coupon_cents": owned_coupons_cents,
        "new_coupon_cents": new_coupons_cents,
        "trial_discount_cents": trial_discount_cents,
        "actual_discount_cents": actual_discount_cents,
        "must_not_sum": True,
        "explanation": (
            "以上四类含义不同，禁止相加："
            "原有券/新领券是「账户里可用的券面额」，"
            "试算优惠是「未下单时的预估」，实际订单优惠是「下单后的真实让利」。"
            "以实际订单优惠为准。"
        ),
    }


# --------------------------------------------------------------------------
# 营养
# --------------------------------------------------------------------------

NUTRITION_COLUMNS = ["productName", "nutritionDescription", "energyKj", "energyKcal",
                     "protein", "fat", "carbohydrate", "sodium", "calcium"]


def parse_nutrition_payload(raw: Any) -> List[Dict[str, Any]]:
    """解析 list-nutrition-foods 的真实返回。

    实测（2026-10-10）格式有两层：
      1. 外层 {"success":true,"data":"[160]{productName,...}:\\n  猪柳麦满分,null,1288,..."}
         —— data 是**字符串**，不是数组
      2. 字符串里的表头 `[160]{col1,col2,...}:` 后跟每行一条 CSV，空格分隔行首

    实测字段名：productName / nutritionDescription / energyKj / energyKcal /
              protein / fat / carbohydrate / sodium / calcium

    注意：实测响应会被上游按 4000 字符截断（160 条只能拿到一部分）。
    截断不影响已完整行的解析 —— 最后一行可能残缺，跳过即可。
    """
    src = _unwrap(raw)
    text: Optional[str] = None
    if isinstance(src, str):
        text = src
    elif isinstance(src, dict):
        for k in ("data", "result", "list", "items"):
            if isinstance(src.get(k), str):
                text = src[k]
                break
            if isinstance(src.get(k), list):
                return [r for r in src[k] if isinstance(r, dict)]
    if text is None:
        return []

    lines = text.splitlines()
    cols: List[str] = []
    rows: List[Dict[str, Any]] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("[") and "{" in s:
            head = s[s.find("{") + 1:s.rfind("}")]
            cols = [c.strip() for c in head.split(",")]
            continue
        if s.endswith(":") and not cols:
            continue
        if not cols:
            continue
        # 行首可能有 '-' 或 '*' 之类的前缀符号
        s2 = s.lstrip("-*· ").strip()
        parts = s2.split(",")
        if len(parts) < 2:
            continue
        # 行数不足说明被截断，丢弃
        if len(parts) < len(cols):
            continue
        rec: Dict[str, Any] = {}
        for i, c in enumerate(cols):
            v = parts[i].strip()
            rec[c] = v if v not in ("", "null", "NULL", "-") else None
        if rec.get("productName"):
            rows.append(rec)

    # 字段名归一化到 mm_calc 内部使用的键
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({
            "name": r.get("productName"),
            "nutritionDescription": r.get("nutritionDescription"),
            "energyKj": r.get("energyKj"),
            "energyKcal": r.get("energyKcal"),
            "protein": r.get("protein"),
            "fat": r.get("fat"),
            "carbohydrate": r.get("carbohydrate"),
            "sodium": r.get("sodium"),
            "calcium": r.get("calcium"),
        })
    return out


def norm_nutrition(row: Dict[str, Any]) -> Dict[str, Any]:
    """归一化一条营养数据。

    字段名做宽松匹配（优先实测的 energyKcal / productName）；任一关键指标缺失
    即标 unknown，不用 0 冒充「不含」。
    """
    name = _get(row, "name", "productName", "foodName", "mealName", "food", "title",
                default=UNKNOWN)

    kcal_raw = _get(row, "energyKcal", "energy_kcal", "energy", "calories", "kcal")
    kcal: Any = UNKNOWN
    if kcal_raw is not None:
        try:
            kcal = int(round(float(kcal_raw)))
        except (TypeError, ValueError):
            kcal = UNKNOWN

    def num(*names: str) -> Any:
        v = _get(row, *names)
        if v is None or v == "" or v == UNKNOWN:
            return UNKNOWN
        try:
            f = float(v)
        except (TypeError, ValueError):
            return UNKNOWN
        return round(f, 1)

    # 同时兼容「已归一化的下划线键」与「实测的驼峰键」，两种输入都能直接累加
    return {
        "name": name,
        "energy_kcal": kcal,
        "energy_key": "energyKcal" if _get(row, "energyKcal") is not None else None,
        "energy_kj": _get(row, "energyKj", "energy_kj"),
        "protein_g": num("protein", "proteinG", "protein_g"),
        "fat_g": num("fat", "fatG", "fat_g"),
        "carb_g": num("carbohydrate", "carb", "carbs", "carbG", "carb_g"),
        "sodium_mg": num("sodium", "salt", "sodiumMg", "sodium_mg", "na"),
        "calcium_mg": num("calcium", "calciumMg", "calcium_mg", "ca"),
        "complete": kcal != UNKNOWN,
    }


def _norm_tokens(s: str) -> List[str]:
    return [t for t in re.split(r"[\s,，、/]+", (s or "").strip().lower()) if t]


def match_nutrition(
    meal_name: str,
    nutrition_rows: List[Dict[str, Any]],
    portion_hint: Optional[str] = None,
    fuzzy: bool = True,
) -> Dict[str, Any]:
    """把点餐餐品匹配到营养库条目。

    严格原则：
      - 精确命中（含完整名称包含）才算 high 置信
      - 仅分词部分重叠 -> medium，且必须提示需人工确认份量
      - 完全不命中 -> unknown，返回空结果，**不允许估算填充**
    """
    if not nutrition_rows:
        return {
            "status": UNKNOWN,
            "confidence": "none",
            "reason": "营养库为空（list-nutrition-foods 未返回数据）",
            "matched": None,
        }

    # 传入的可能是原始实测字段（productName/energyKcal），先归一化
    norm = [norm_nutrition(r) for r in nutrition_rows if isinstance(r, dict)]
    q = (meal_name or "").strip()
    ql = q.lower()
    qt = set(_norm_tokens(q))

    # 1) 名称完全相等
    for row in norm:
        rn = str(row.get("name") or "").strip().lower()
        if rn and rn == ql:
            return {
                "status": "ok",
                "confidence": "high",
                "matched": row,
                "reason": "名称完全匹配",
                "portion_confirmed": False if portion_hint is None else True,
            }

    # 2) 名称包含关系
    for row in norm:
        rn = str(row.get("name") or "").strip().lower()
        if rn and (rn in ql or ql in rn):
            return {
                "status": "ok",
                "confidence": "high",
                "matched": row,
                "reason": "名称互相包含",
                "portion_confirmed": portion_hint is not None,
            }

    if not fuzzy:
        return {"status": UNKNOWN, "confidence": "none", "reason": "无匹配", "matched": None}

    # 3) 分词重叠
    best = None
    best_score = 0.0
    for row in norm:
        rn = str(row.get("name") or "").strip().lower()
        rt = set(_norm_tokens(rn))
        if not rt or not qt:
            continue
        inter = qt & rt
        if not inter:
            continue
        score = len(inter) / max(len(qt), len(rt))
        if score > best_score:
            best, best_score = row, score

    if best is not None and best_score >= 0.5:
        return {
            "status": "partial",
            "confidence": "medium",
            "matched": best,
            "reason": f"分词部分重叠（相似度 {best_score:.2f}），需确认是否为同一餐品及份量",
            "portion_confirmed": False,
        }

    return {
        "status": UNKNOWN,
        "confidence": "none",
        "reason": f"营养库中没有与「{q}」对应的条目，标注未知，不做估算",
        "matched": None,
    }


def sum_nutrition(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """累加一组营养值。任一项含 unknown 则该维度合计为 unknown。

    入参可以是已归一化的行（energy_kcal），也可以是实测原始行
    （productName/energyKcal 等）—— 内部统一先归一化。
    """
    norm = [norm_nutrition(r) for r in rows if isinstance(r, dict)]
    keys = ["energy_kcal", "protein_g", "fat_g", "carb_g", "sodium_mg", "calcium_mg"]
    out: Dict[str, Any] = {}
    unknown_fields: List[str] = []
    for k in keys:
        vals = [r.get(k) for r in norm]
        if not vals or any(v == UNKNOWN or v is None for v in vals):
            out[k] = UNKNOWN
            unknown_fields.append(k)
        else:
            try:
                s = sum(float(v) for v in vals)
                out[k] = round(s) if k == "energy_kcal" else round(s, 1)
            except (TypeError, ValueError):
                out[k] = UNKNOWN
                unknown_fields.append(k)
    out["items"] = len(norm)
    out["has_unknown"] = bool(unknown_fields)
    if unknown_fields:
        out["unknown_fields"] = unknown_fields
        out["note"] = "合计值缺失，请按单项逐一核对，不要当作完整营养估算"
    return out


def check_nutrition_range(total: Dict[str, Any], calorie_range: Optional[List[int]] = None) -> Dict[str, Any]:
    """判断营养合计是否落在用户要求区间。

    区间未知或合计未知 -> verdict=unknown，不猜。
    """
    if not calorie_range or len(calorie_range) != 2:
        return {"verdict": UNKNOWN, "reason": "未设置热量区间"}
    if total.get("energy_kcal") in (None, UNKNOWN):
        return {"verdict": UNKNOWN, "reason": "热量合计未知，无法判定"}
    lo, hi = calorie_range
    v = total["energy_kcal"]
    if lo <= v <= hi:
        return {"verdict": "in_range", "energy_kcal": v, "range": [lo, hi]}
    over = v > hi
    return {
        "verdict": "over" if over else "under",
        "energy_kcal": v,
        "range": [lo, hi],
        "diff_kcal": v - hi if over else v - lo,
        "reason": f"超出区间上限 {abs(v - hi)} 千卡" if over else f"低于区间下限 {abs(v - lo)} 千卡",
    }


# --------------------------------------------------------------------------
# 积分 / 兑换
# --------------------------------------------------------------------------

def points_affordable(points: Optional[int], cost: Optional[int]) -> Dict[str, Any]:
    if points is None or cost is None:
        return {"affordable": UNKNOWN, "reason": "积分余额或商品所需积分为空"}
    return {
        "affordable": points >= cost,
        "points_balance": points,
        "cost": cost,
        "remain_after": points - cost,
    }


def describe_expiry(expire: Any) -> Dict[str, Any]:
    """把有效期转成可读描述 + 是否临近。无法解析就标 unknown。"""
    if expire in (None, "", UNKNOWN):
        return {"raw": expire, "readable": UNKNOWN, "urgent": UNKNOWN}
    s = str(expire)
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", s)
    if not m:
        return {"raw": s, "readable": s, "urgent": UNKNOWN}
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        from datetime import datetime, timezone, timedelta
        exp = datetime(y, mo, d, tzinfo=timezone(timedelta(hours=8)))
        now = datetime.now(timezone(timedelta(hours=8)))
        days = (exp - now).days
        return {
            "raw": s,
            "readable": f"{y}-{mo:02d}-{d:02d}",
            "days_left": days,
            "urgent": days <= 7,
            "expired": days < 0,
        }
    except ValueError:
        return {"raw": s, "readable": s, "urgent": UNKNOWN}


# --------------------------------------------------------------------------
# 抽奖概率：只透传，不编造
# --------------------------------------------------------------------------

# 实测字段名（2026-10-10，来自 query-lottery-info 真实返回）：
#   drawPoint          单次所需积分（字符串，如 "24"）
#   drawTypeText       消耗规则文案（消耗积分抽奖 / 消耗任务完成次数 / 先消耗次数再消耗积分抽奖）
#   availablePoint     当前可用积分（字符串）
#   availableTimes     剩余可抽次数（integer）
#   drawDecision       服务端算好的资源决策，含 resourceEligible / nextConsumption
#   prizes[].name      奖品名称（**注意：奖品对象里没有概率字段**）
#
# 奖品字段实测只有 name / imageUrl / typeText —— 官方确实未返回中奖概率，
# 因此 probability 恒为 unknown，这是符合事实的结果，不是解析失败。
LOTTERY_PRIZE_FIELDS = ("probability", "prob", "rate", "chance", "odds", "winRate",
                        "probabilityValue", "中奖概率")


def _num_any(v: Any) -> Optional[float]:
    """把字符串或数字统一转成数值（实测积分类字段都是字符串）。"""
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


def normalize_lottery(info_raw: Any) -> Dict[str, Any]:
    """整理抽奖活动信息。

    官方若未提供中奖概率，probability 字段一律为 unknown，
    并在 note 中明确「官方未返回概率，本 Skill 不做推算」。
    """
    src = _unwrap(info_raw)
    if not isinstance(src, dict):
        return {"ok": False, "reason": "无法解析抽奖活动返回结构", "probability": UNKNOWN}

    # 活动号/活动名是这一接口的必备字段。都没有说明剥包剥过头了（只剥到
    # {"code":200,"message":...} 空壳），必须明确报未解析，而不是把 unknown
    # 当成结果继续算 —— 那会让成本试算变成假数字。
    if not any(k in src for k in ("activityCode", "activityName", "prizes", "drawPoint")):
        return {
            "ok": False,
            "reason": "返回中未找到活动字段（activityCode/activityName/prizes/drawPoint），"
                      "无法解析抽奖活动信息",
            "probability": UNKNOWN,
            "raw_keys": sorted(src.keys())[:12],
        }

    # 业务状态：data.status.code == SUCCESS 才算正常
    status_obj = _get(src, "status", default={})
    status_code = _get(status_obj, "code") if isinstance(status_obj, dict) else status_obj
    status_msg = _get(status_obj, "message") if isinstance(status_obj, dict) else None

    prizes_raw = _get(src, "prizes", "prizeList", "rewardList", default=[])
    prizes: List[Dict[str, Any]] = []
    if isinstance(prizes_raw, list):
        for p in prizes_raw:
            if not isinstance(p, dict):
                continue
            prob = _get(p, *LOTTERY_PRIZE_FIELDS)
            pnum = _num_any(prob)
            prizes.append({
                "name": _get(p, "name", "prizeName", "title", default=UNKNOWN),
                "type_text": _get(p, "typeText", "type", "prizeType"),
                "image_url": _get(p, "imageUrl", "image"),
                # 只在官方真的返回了概率字段时才取值
                "probability": round(pnum, 6) if pnum is not None else UNKNOWN,
            })

    has_explicit_prob = any(p["probability"] != UNKNOWN for p in prizes)
    decision = _get(src, "drawDecision", default={})
    draw_point = _num_any(_get(src, "drawPoint", "cost", "pointCost"))

    return {
        "ok": True,
        "status_code": status_code if status_code is not None else UNKNOWN,
        "status_message": status_msg,
        "activity_name": _get(src, "activityName", "name", "title", default=UNKNOWN),
        "activity_status_text": _get(src, "activityStatusText", default=None),
        "activity_code": _get(src, "activityCode"),
        "begin_time": _get(src, "beginTime"),
        "end_time": _get(src, "endTime"),
        "consume_rule": _get(src, "drawTypeText", "consumeRule", default=UNKNOWN),
        "draw_point": draw_point if draw_point is not None else UNKNOWN,
        "available_point": _num_any(_get(src, "availablePoint")),
        "available_times": _get(src, "availableTimes"),
        # 服务端自己的资源判断，优先采信
        "server_decision": decision if isinstance(decision, dict) else None,
        "server_resource_eligible": (
            decision.get("resourceEligible") if isinstance(decision, dict) else None),
        "server_next_consumption": (
            decision.get("nextConsumption") if isinstance(decision, dict) else None),
        "prizes": prizes,
        "probability": "官方已返回" if has_explicit_prob else UNKNOWN,
        "note": "" if has_explicit_prob
        else ("官方返回的奖品仅含名称/类型/图片，未提供中奖概率；"
              "本 Skill 不会自行推算或估算中奖率。"),
    }


def lottery_cost_preview(info: Dict[str, Any], times: int) -> Dict[str, Any]:
    """抽奖成本试算。times 来自用户明确指定，不做放大建议。

    消耗单位由 drawTypeText 决定（实测取值）：
      「消耗积分抽奖」/「先消耗次数再消耗积分抽奖」→ 按可用积分判断
      「消耗任务完成次数」→ 按可用次数判断
    混合规则下以服务端 drawDecision.resourceEligible 为准。
    """
    if not info.get("ok"):
        return {
            "times": times,
            "cost_per_draw": UNKNOWN,
            "total_cost": UNKNOWN,
            "balance": UNKNOWN,
            "balance_unit": UNKNOWN,
            "affordable": UNKNOWN,
            "remain_after": UNKNOWN,
            "reason": info.get("reason", "抽奖信息未解析成功，不做任何成本推算"),
            "requires_confirm": True,
        }

    per = info.get("draw_point")
    per = per if isinstance(per, (int, float)) else None
    total = per * times if per is not None else None

    out: Dict[str, Any] = {
        "times": times,
        "cost_per_draw": per if per is not None else UNKNOWN,
        "total_cost": total if total is not None else UNKNOWN,
        "consume_rule": info.get("consume_rule", UNKNOWN),
    }

    rule = str(info.get("consume_rule") or "")
    by_times_only = ("消耗任务完成次数" in rule) and ("积分" not in rule)
    pts = info.get("available_point")
    times_left = info.get("available_times")

    if by_times_only and isinstance(times_left, (int, float)) and total is not None:
        out["balance"] = times_left
        out["balance_unit"] = "次"
        out["affordable"] = times_left >= total
        out["remain_after"] = times_left - total
    elif isinstance(pts, (int, float)) and total is not None:
        out["balance"] = pts
        out["balance_unit"] = "积分"
        out["affordable"] = pts >= total
        out["remain_after"] = round(pts - total, 4)
    else:
        out["balance"] = pts if isinstance(pts, (int, float)) else UNKNOWN
        out["balance_unit"] = UNKNOWN
        out["affordable"] = UNKNOWN
        out["reason"] = "官方返回中未解析到可用积分/次数，无法判断是否够用"

    # 服务端已给出结论时一并透传，让用户看到官方口径
    elig = info.get("server_resource_eligible")
    if elig is not None:
        out["server_resource_eligible"] = elig
        out["server_next_consumption"] = info.get("server_next_consumption")

    out["requires_confirm"] = True
    return out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print(o: Any) -> None:
    print(json.dumps(o, ensure_ascii=False, indent=2, sort_keys=False, default=str))


def _load(a: argparse.Namespace) -> Any:
    """读取 --json 字面量或 --file 文件。

    注意：--file 用的是 argparse.FileType，argparse 已经把文件打开成流对象，
    这里必须用 .read()，不能再调 .read_text()。
    """
    if getattr(a, "json", None):
        return json.loads(a.json)
    f = getattr(a, "file", None)
    if f is not None:
        data = f.read()
        if isinstance(data, bytes):
            data = data.decode("utf-8", errors="replace")
        return json.loads(data)
    return None


def cmd_price(a: argparse.Namespace) -> None:
    raw = _load(a)
    got = dig_amount_candidates(raw)
    out = classify_trial_discount(
        got["goods_total"], got["discount"], got["payable"],
        got["delivery_fee"], got["packing_fee"]
    )
    out["raw_extracted"] = got
    brief = getattr(a, "brief", False)
    out["display"] = {
        "goods_total": cents_str(got["goods_total"]),
        "delivery_fee": cents_str(got["delivery_fee"]),
        "packing_fee": cents_str(got["packing_fee"]),
        "discount": cents_str(got["discount"]),
        "payable": cents_str(got["payable"]),
    }
    # --brief：只输出 display 与自洽结论，不重复打印 raw_extracted
    # （实测原先把同一批金额打印两遍，纯浪费 token）
    _print(out if not brief else {
        "display": out["display"], "consistent": out["consistent"],
        "expected_payable_cents": out.get("expected_payable_cents"),
        "diff_cents": out.get("diff_cents"), "note": out.get("note", ""),
    })


def cmd_actual(a: argparse.Namespace) -> None:
    _print(extract_actual_discount(_load(a)))


def cmd_merge(a: argparse.Namespace) -> None:
    """四个参数单位统一为「分」（与 --owned/--new 保持一致）。

    注意：这里不再做元->分转换。券面额与优惠金额在本 Skill 内一律以分存储，
    任何元单位数值应先经 `mm_calc.py cents` 转换后再传入，
    否则会出现 owned=1200 分、trial=200000 分这类量纲错误。
    """
    def as_cents(v: Any) -> Optional[int]:
        if v in (None, "", "unknown"):
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    _print(merge_discount_sources(
        owned_coupons_cents=as_cents(a.owned) or 0,
        new_coupons_cents=as_cents(a.new) or 0,
        trial_discount_cents=as_cents(a.trial),
        actual_discount_cents=as_cents(a.actual),
    ))


def cmd_match(a: argparse.Namespace) -> None:
    raw = _load(a)
    rows = parse_nutrition_payload(raw) if not isinstance(raw, list) else raw
    if isinstance(rows, dict):
        rows = rows.get("list") or rows.get("data") or rows.get("items") or []
    if isinstance(rows, str):
        rows = parse_nutrition_payload(rows)
    _print(match_nutrition(a.meal, [r for r in rows if isinstance(r, dict)], a.portion))


def cmd_sum(a: argparse.Namespace) -> None:
    raw = _load(a)
    rows = raw
    if not isinstance(rows, list):
        rows = parse_nutrition_payload(raw)
    if isinstance(rows, dict):
        rows = rows.get("list") or rows.get("data") or rows.get("items") or []
    _print(sum_nutrition([r for r in rows if isinstance(r, dict)]))


def cmd_nutrition_all(a: argparse.Namespace) -> None:
    """列出解析到的营养条目概览（调试真实格式时用）。

    --brief 只输出「名称+热量」，避免每条 12 个字段的输出膨胀。
    """
    raw = _load(a)
    rows = parse_nutrition_payload(raw)
    norm = [norm_nutrition(r) for r in rows]
    sample = norm[: (a.limit or 5)]
    if getattr(a, "brief", False):
        sample = [{"name": x["name"], "kcal": x["energy_kcal"]} for x in sample]
    _print({"parsed_count": len(rows), "sample": sample})


def cmd_range(a: argparse.Namespace) -> None:
    total = _load(a)
    rng = None
    if a.range:
        rng = [int(x) for x in a.range.split("-")]
    _print(check_nutrition_range(total, rng))


def cmd_lottery(a: argparse.Namespace) -> None:
    info = normalize_lottery(_load(a))
    times = int(a.times or 1)
    _print({"info": info, "cost": lottery_cost_preview(info, times)})


def cmd_expiry(a: argparse.Namespace) -> None:
    _print(describe_expiry(a.value))


def cmd_cents(a: argparse.Namespace) -> None:
    c = to_cents(a.value)
    _print({"input": a.value, "cents": c, "display": cents_str(c)})


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mm_calc", description="麦麦生活助手 · 试算与核算层")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("price", help="解析 calculate-price 返回（试算）")
    sp.add_argument("--json"); sp.add_argument("--file", type=argparse.FileType("r"))
    sp.add_argument("--brief", action="store_true",
                    help="只输出 display 与自洽结论（不再重复打印 raw_extracted）")
    sp.set_defaults(func=cmd_price)

    sa = sub.add_parser("actual", help="解析 create-order/query-order 返回（实际）")
    sa.add_argument("--json"); sa.add_argument("--file", type=argparse.FileType("r"))
    sa.set_defaults(func=cmd_actual)

    sm = sub.add_parser("merge", help="并列输出四类优惠（禁止求和）")
    sm.add_argument("--owned"); sm.add_argument("--new")
    sm.add_argument("--trial"); sm.add_argument("--actual")
    sm.set_defaults(func=cmd_merge)

    sn = sub.add_parser("match-nutrition", help="餐品匹配营养数据")
    sn.add_argument("meal")
    sn.add_argument("--portion")
    sn.add_argument("--json"); sn.add_argument("--file", type=argparse.FileType("r"))
    sn.set_defaults(func=cmd_match)

    ss = sub.add_parser("sum-nutrition", help="累加营养")
    ss.add_argument("--json"); ss.add_argument("--file", type=argparse.FileType("r"))
    ss.set_defaults(func=cmd_sum)

    sa2 = sub.add_parser("nutrition-all", help="解析营养库并输出概览")
    sa2.add_argument("--json"); sa2.add_argument("--file", type=argparse.FileType("r"))
    sa2.add_argument("--limit", type=int, default=5)
    sa2.add_argument("--brief", action="store_true", help="只输出名称与热量")
    sa2.set_defaults(func=cmd_nutrition_all)

    sr = sub.add_parser("check-range", help="判断热量是否在区间内")
    sr.add_argument("--json"); sr.add_argument("--file", type=argparse.FileType("r"))
    sr.add_argument("--range", help="如 400-700")
    sr.set_defaults(func=cmd_range)

    sl = sub.add_parser("lottery", help="整理抽奖信息与成本")
    sl.add_argument("--times", default="1")
    sl.add_argument("--json"); sl.add_argument("--file", type=argparse.FileType("r"))
    sl.set_defaults(func=cmd_lottery)

    se = sub.add_parser("expiry", help="解析有效期")
    se.add_argument("value")
    se.set_defaults(func=cmd_expiry)

    sc = sub.add_parser("cents", help="金额转分")
    sc.add_argument("value")
    sc.set_defaults(func=cmd_cents)
    return p


def main() -> int:
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
