#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
menu.py — 菜单抓取 / 候选筛选 / 批量试算

存在理由（实测依据）：
  2026-10-10 实测点餐流程调用 28 次MCP，其中 `calculate-price` 占 24 次
  （占全程 token 60%+）。根因是「逐个编码试算反推价格」。
  本脚本把 `calculate-price` 的 items 数组用起来，一次传入多个商品，
  **把试算压到 ≤2 次**。

三条子命令：
  fetch      1 次 MCP：拉菜单并落盘缓存
  candidates 0 次 MCP：纯本地按预算筛候选
  price      1 次 MCP：items 数组批量试算

安全约束：
  - 写工具黑名单：本脚本**不含**任何下单/取消接口，只能读+试算
  - Token 只从环境变量或 ~/.maimai-helper/.token 读取，不出现在输出中

用法：
  python scripts/menu.py fetch --store 1410258 --order-type 2 --be-type 2 --be-code 141025802
  python scripts/menu.py candidates --budget 4000 --limit 3
  python scripts/menu.py price --codes 901406,901440,521966
  python scripts/menu.py price --codes 9900016511:2,521966     # :N 指定份数
  python scripts/menu.py candidates --budget 4000 --brief
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

# 菜单缓存 TTL：菜单每天变化不大，但套餐会轮换
MENU_TTL_HOURS = 24

# 商品名/单价持久缓存（跨会话复用）
#
# 为什么需要（2026-10-10 实测）：query-meals 被 4000 字符截断，
# 详情表拿不到 → 菜单里 44 个品项**名字和价格全是 null**。
# 要认识它们只能逐个试算，实测为此白白多花 7 次 MCP 调用。
#
# 有了这个缓存：试算过一次的编码，名字与价格永久记住，
# 下次直接参与本地筛选，不再重复试算。
PRICE_CACHE_FILE = "price_cache.json"

# 单批试算的实测安全上限：超过这个数 calculate-price 响应会超 4000 字符被截断
SAFE_BATCH = 5

# 兜底常量：配送费与打包费（实测示例市门店：配送 600，打包 80~250）
# **仅用于本地初筛**，最终金额一律以 calculate-price 为准。
DEFAULT_DELIVERY_CENTS = 600
DEFAULT_PACKING_CENTS = 110


# 输出是否缩进。默认 False —— menu.py 的输出是给程序/模型消费的，
# 紧凑 JSON 比 indent=2 省约 40% 字符（实测 1117 -> 约 650）。
# 需要人读时传 --pretty。
_OUT_PRETTY = False


def _print(obj: Any, brief: bool = False) -> None:
    """输出。

    ## 为什么默认紧凑（2026-10-10 实测性能优化）

    实测：单次 Python 启动约 **1060 ms**；脚本输出 338~1117 字符，
    其中 `indent=2` 的缩进空白约占 40%。

    `menu.py` 的输出是给**程序/模型**消费的，不是给人看的，
    因此默认单行紧凑 JSON。需要人读时传 `--pretty`。
    """
    if brief and isinstance(obj, dict):
        obj = _slim(obj)
    if _OUT_PRETTY:
        print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))
    else:
        print(json.dumps(obj, ensure_ascii=False, separators=(",", ":"),
                         default=str))


def _slim(x: Any) -> Any:
    """brief 模式：递归去掉 None 与空列表，并把候选项压成必要字段。"""
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            if v is None:
                continue
            if k == "single_candidates" and isinstance(v, list):
                out[k] = [{
                    "code": i.get("code"),
                    "name": i.get("name"),
                    "price_cents": i.get("price_cents"),
                    "est_total_cents": i.get("est_total_cents"),
                    "category": (i.get("category") or "").replace("\n", ""),
                    "tags": i.get("tags"),
                    "reason": i.get("_reason"),
                } for i in v]
                continue
            out[k] = _slim(v)
        return out
    if isinstance(x, list):
        return [_slim(i) for i in x]
    return x


def menu_cache_path(store_code: str, order_type: int, be_type: int) -> Path:
    return mm_state.home() / f"menu_{store_code}_{order_type}_{be_type}.json"


def _unwrap_json(raw: Any) -> Any:
    """剥出业务数据本体。

    ⚠️ 实测麦当劳 MCP 多数工具返回「Markdown 双段 + 内嵌 JSON」，
    mm_calc._unwrap 对这类返回会失败（它只认纯 dict/JSON）。
    这里先试 _extract_json，再退回 _unwrap，两种格式都能吃。
    """
    txt = raw.get("_text", "") if isinstance(raw, dict) else raw
    if isinstance(txt, str) and txt:
        got = mm_calc._extract_json(txt)
        if got is not None:
            src = got
            # 再剥一层业务 data
            if isinstance(src, dict) and isinstance(src.get("data"), (dict, list)):
                return src
            return src
    return mm_calc._unwrap(raw)


def _cache_fresh(p: Path) -> bool:
    if not p.exists():
        return False
    age_h = (mm_state.datetime.now() - mm_state.datetime.fromtimestamp(
        p.stat().st_mtime)).total_seconds() / 3600
    return age_h < MENU_TTL_HOURS


# --------------------------------------------------------------------------
# 商品名 / 单价持久缓存
# --------------------------------------------------------------------------

def price_cache_path() -> Path:
    return mm_state.home() / PRICE_CACHE_FILE


def load_price_cache() -> Dict[str, Dict[str, Any]]:
    """读取 code -> {name, price_cents} 缓存。"""
    d = mm_state.read_json(price_cache_path(), {}) or {}
    items = d.get("items")
    return items if isinstance(items, dict) else {}


def save_price_cache(cache: Dict[str, Dict[str, Any]]) -> None:
    mm_state.atomic_write_json(price_cache_path(), {
        "updated_at": mm_state.iso_now(), "count": len(cache), "items": cache,
    })


def remember_products(products: List[Dict[str, Any]],
                      store: Optional[str] = None) -> int:
    """把试算返回的 品名/单价 记入持久缓存。返回新增或更新的条数。

    ⚠️ 单价只在**单件试算**时可当作该商品的独立价；批量试算里
    带回的 subtotal 仍是该商品自身小计，可以安全记录。
    """
    cache = load_price_cache()
    n = 0
    for p in products:
        code = p.get("code")
        name = p.get("name")
        if not code or not name:
            continue
        # 打包服务费等自动附加项不入缓存
        if "服务费" in name or "打包" in name:
            continue
        rec = cache.get(code) or {}
        rec["name"] = name
        # qty=1 时 subtotal 即单价；qty>1 时换算
        sub = p.get("subtotal_cents")
        qty = p.get("qty") or 1
        if sub and qty:
            rec["price_cents"] = int(round(sub / qty))
        if store:
            rec["store"] = store
        rec["seen_at"] = mm_state.iso_now()
        cache[code] = rec
        n += 1
    if n:
        save_price_cache(cache)
    return n


# --------------------------------------------------------------------------
# 菜单解析
# --------------------------------------------------------------------------

def _salvage_menu(text: str) -> Tuple[List[Any], Dict[str, Any]]:
    """从**被截断**的 query-meals 响应里抢救 categories 与 meals 详情表。

    ## 为什么必须单独抢救（2026-10-10 实测根因）

    query-meals 响应被上游按 4000 字符截断，导致根对象 `{...}` 不配平、
    整体 `json.loads` 失败。此时 `mm_calc._extract_json` 只能拿到
    「最后一个完整的碎片对象」—— 而分类对象 `{"name":..,"meals":[..]}`
    恰好是完整且带业务字段的碎片，会被评分选中并当成整个返回。

    结果：`parse_menu` 拿到的是单个分类而非 categories 数组，解析出 0 条。
    实测 `orderType=1` 时截断点不同、侥幸能解析出 40 条，
    `orderType=2` 时截断点刚好落在分类里 → 解析出 0 条。
    **同一份代码、同一个门店，只因为截断位置不同就一个工作一个不工作。**

    因此这里不依赖通用解析器，直接按 query-meals 的已知结构定位：
    逐元素扫描 `"categories":[...]`，收集**完整配平**的对象。

    Returns: (categories, meals_detail_map)
    """
    cats: List[Any] = []
    detail: Dict[str, Any] = {}

    # --- categories 数组 ---
    key = '"categories"'
    pos = text.find(key)
    if pos >= 0:
        arr_start = text.find("[", pos + len(key))
        if arr_start >= 0:
            i = arr_start + 1
            n = len(text)
            while i < n:
                while i < n and text[i] in " \t\r\n,":
                    i += 1
                if i >= n or text[i] != "{":
                    break
                span = mm_calc._balanced_json_span(text, i)
                if not span:
                    break            # 遇到被截断的对象，停止
                try:
                    cats.append(json.loads(span))
                except json.JSONDecodeError:
                    break
                i += len(span)

    # --- meals 详情表（可能整体被截断，逐个键抢救）---
    key2 = '"meals"'
    pos2 = text.find(key2)
    if pos2 >= 0:
        obj_start = text.find("{", pos2 + len(key2))
        if obj_start >= 0:
            span = mm_calc._balanced_json_span(text, obj_start)
            if span:
                try:
                    got = json.loads(span)
                    if isinstance(got, dict):
                        detail = got
                except json.JSONDecodeError:
                    span = None
            if span is None:
                # 详情表被截断：逐个 "code":{...} 抢救完整项
                i = obj_start + 1
                n = len(text)
                while i < n:
                    while i < n and text[i] in " \t\r\n,":
                        i += 1
                    if i >= n or text[i] != '"':
                        break
                    q = text.find('"', i + 1)
                    if q < 0:
                        break
                    k = text[i + 1:q]
                    j2 = q + 1
                    while j2 < n and text[j2] in " \t\r\n:":
                        j2 += 1
                    if j2 >= n or text[j2] != "{":
                        break
                    sp = mm_calc._balanced_json_span(text, j2)
                    if not sp:
                        break
                    try:
                        detail[k] = json.loads(sp)
                    except json.JSONDecodeError:
                        break
                    i = j2 + len(sp)

    return cats, detail


def parse_menu(raw: Any) -> List[Dict[str, Any]]:
    """解析 query-meals 返回，产出扁平餐品列表。

    实测结构（2026-10-10）：
      {"data": {"categories": [{"name": "人气热卖",
                                 "meals": [{"code": "9900016490",
                                            "tags": ["韩式辣椒黄油风味"]}]}],
                "meals": {"<code>": {"name": ..., "currentPrice": ...}}}}

    ⚠️ 三个实测坑：
      1. `_unwrap` 会把 data 层剥掉，传进来可能已是 data 本体，也可能还包着 data。
      2. 响应常被上游按 4000 字符截断 → 根对象不配平 → 通用解析器只能拿到
         碎片（分类对象），导致解析出 0 条。**必须走 `_salvage_menu` 定点抢救。**
      3. 详情表被截断 → 只有编码没有价格，标 `price_unknown=True`，
         金额一律走 `calculate-price` 试算。

    绝不猜价格。查不到就是查不到。
    """
    src = _unwrap_json(raw)

    txt = raw.get("_text", "") if isinstance(raw, dict) else (
        raw if isinstance(raw, str) else "")
    cats: List[Any] = []
    detail_map: Dict[str, Any] = {}

    if isinstance(src, dict):
        data = src
        if "categories" not in data and isinstance(src.get("data"), dict):
            data = src["data"]
        if isinstance(data, dict):
            if isinstance(data.get("categories"), list):
                cats = data["categories"]
            if isinstance(data.get("meals"), dict):
                detail_map = data["meals"]

    # 通用解析拿不到 categories 时，走定点抢救
    if not cats and txt:
        cats, detail_map2 = _salvage_menu(txt)
        if detail_map2:
            detail_map = detail_map2

    out: List[Dict[str, Any]] = []
    seen = set()

    def add(code: str, tags: List[str], cat: str) -> None:
        if not code or code in seen:
            return
        seen.add(code)
        det = detail_map.get(code) or {}
        price = det.get("currentPrice")
        orig = det.get("originalPrice")
        try:
            price_c = int(price) if price not in (None, "") else None
        except (TypeError, ValueError):
            price_c = None
        try:
            orig_c = int(orig) if orig not in (None, "") else None
        except (TypeError, ValueError):
            orig_c = None
        out.append({
            "code": code,
            "name": det.get("name"),
            "category": cat,
            "tags": tags or [],
            "price_cents": price_c,
            "original_cents": orig_c,
            # 没有详情就是没有价格，标记出来，绝不拿原价当现价
            "price_unknown": price_c is None,
            "discount_type": det.get("discountType"),
            "with_order": det.get("canWithOrder"),
        })

    if isinstance(cats, list):
        for c in cats:
            if not isinstance(c, dict):
                continue
            cat_name = c.get("name") or ""
            for m in (c.get("meals") or []):
                if isinstance(m, dict):
                    add(m.get("code"), m.get("tags") or [], cat_name)

    # 详情表里可能有 categories 未列出的单品
    for code, det in detail_map.items():
        if isinstance(det, dict) and code not in seen:
            add(code, [], det.get("category") or "")

    return out


# --------------------------------------------------------------------------
# 子命令 1：fetch
# --------------------------------------------------------------------------

def cmd_fetch(a: argparse.Namespace) -> None:
    from probe_mcp import StreamableClient, unwrap, MCP_URL, load_token

    args: Dict[str, Any] = {
        "storeCode": a.store,
        "orderType": a.order_type,
        "beType": a.be_type,
    }
    if a.be_code:
        # 实测 beType=1 时传 beCode 会报错，必须条件传
        if a.be_type != 1:
            args["beCode"] = a.be_code
        else:
            print("[warn] beType=1 不应传 beCode，已忽略", file=sys.stderr)

    cli = StreamableClient(MCP_URL, load_token())
    cli.initialize()
    raw = unwrap(cli.call("query-meals", args))

    items = parse_menu(raw)
    p = menu_cache_path(a.store, a.order_type, a.be_type)
    mm_state.atomic_write_json(p, {
        "store": a.store, "order_type": a.order_type, "be_type": a.be_type,
        "fetched_at": mm_state.iso_now(),
        "count": len(items), "items": items,
    })

    known = sum(1 for x in items if not x["price_unknown"])
    result = {
        "store": a.store, "order_type": a.order_type, "be_type": a.be_type,
        "cached": str(p),
        "count": len(items),
        "price_known": known,
        "price_unknown": len(items) - known,
    }
    if len(items) - known == len(items) and items:
        result["_note"] = ("菜单响应被截断，未取到价格表。"
                           "候选筛选需按分类/标签而非价格，金额一律靠 menu price 试算。")
    _print(result, brief=a.brief)


# --------------------------------------------------------------------------
# 子命令 2：candidates（纯本地，0 次 MCP）
# --------------------------------------------------------------------------

def load_menu(store: str, order_type: int, be_type: int,
              with_cache: bool = True) -> List[Dict[str, Any]]:
    """读菜单缓存。`with_cache=True` 时把持久价格缓存合并进来。

    合并的意义：query-meals 截断导致菜单里名字/价格是 null，
    但只要历史上试算过一次，就能从 price_cache 补回，
    于是本地筛选立刻可用，**不需要再花调用去认识它**。
    """
    p = menu_cache_path(store, order_type, be_type)
    if not p.exists():
        raise SystemExit(
            f"未找到菜单缓存：{p}\n请先执行 menu.py fetch --store {store} "
            f"--order-type {order_type} --be-type {be_type}"
        )
    d = mm_state.read_json(p, {}) or {}
    items = d.get("items") or []

    if not with_cache:
        return items

    cache = load_price_cache()
    if not cache:
        return items
    merged = []
    for it in items:
        rec = cache.get(it.get("code"))
        if rec:
            it = dict(it)
            if rec.get("name") and not it.get("name"):
                it["name"] = rec["name"]
            # ⚠️ 价格以 price_cache 为准，**无条件覆盖**菜单里的值。
            # price_cache 存的是**单价**（已按 qty 归一），
            # 而菜单回填历史上曾把「小计」误写成单价 ——
            # 实测 9900016511 单价 ¥12.9，按 2 份下单后菜单里变成 ¥25.8。
            # 只在 price_unknown 时才覆盖会漏掉这种已被写脏的情况。
            if rec.get("price_cents"):
                if it.get("price_cents") != rec["price_cents"]:
                    it["from_cache"] = True
                it["price_cents"] = rec["price_cents"]
                it["price_unknown"] = False
        merged.append(it)
    return merged


def cmd_candidates(a: argparse.Namespace) -> None:
    """筛候选。**不调 MCP**。

    两种模式（实测菜单常被 4000 字符截断，详情表可能拿不到）：

    **有价格时** —— 按预算精算：
        商品价 + 估算配送 + 估算打包 ≤ 预算 → 进候选池
        同时尝试「两个便宜的凑」组合

    **无价格时**（price_unknown）—— 按标签排序给推荐：
        实测此时仍能拿到分类和 tags，可据此挑出大概率划算的品项，
        金额一律交给 `menu price` 试算确认。
    """
    items = load_menu(a.store, a.order_type, a.be_type)

    prefer = [t.lower() for t in (a.tags or [])]
    avoid = [t.lower() for t in (a.avoid or [])]

    def tag_hit(it: Dict[str, Any], words: List[str]) -> bool:
        if not words:
            return False
        blob = (it.get("name") or "") + " " + ",".join(it.get("tags") or [])
        return any(w in blob.lower() for w in words)

    pool = [x for x in items if not tag_hit(x, avoid)]

    budget = a.budget
    overhead = a.overhead if a.overhead is not None else (
        DEFAULT_DELIVERY_CENTS + DEFAULT_PACKING_CENTS)

    has_price = [x for x in pool if not x["price_unknown"]]
    no_price = [x for x in pool if x["price_unknown"]]

    result: Dict[str, Any] = {
        "store": a.store,
        "budget_cents": budget,
        "overhead_estimate_cents": overhead,
        "price_mode": "exact" if has_price else "tag_only",
    }

    if has_price:
        fits = []
        for x in has_price:
            total = x["price_cents"] + overhead
            if budget is None or total <= budget:
                y = dict(x)
                y["est_total_cents"] = total
                fits.append(y)
        fits.sort(key=lambda z: -z["est_total_cents"])
        result["single_candidates"] = fits[:a.limit]

        combos: List[Dict[str, Any]] = []
        if budget:
            # 单件占预算 70% 以上就不适合再配第二样（会挤掉运费和打包）。
            # 用 <= 而非 <，避免恰好卡在门槛的商品被误排除。
            cheap = sorted([x for x in has_price if x["price_cents"] <= budget * 0.7],
                           key=lambda z: z["price_cents"])
            for i, x in enumerate(cheap[:8]):
                for y2 in cheap[i + 1:i + 4]:
                    tot = x["price_cents"] + y2["price_cents"] + overhead
                    if tot <= budget:
                        combos.append({
                            "type": "combo",
                            "items": [
                                {"code": x["code"], "name": x["name"],
                                 "price_cents": x["price_cents"]},
                                {"code": y2["code"], "name": y2["name"],
                                 "price_cents": y2["price_cents"]},
                            ],
                            "est_total_cents": tot,
                        })
            combos.sort(key=lambda c: -c["est_total_cents"])
        result["combo_candidates"] = combos[:a.limit]
        result["price_unknown_count"] = len(no_price)
        if budget and fits:
            result["_note"] = "金额为本地估算，真实金额以 menu price 试算为准。"

    else:
        # 无价格模式：按「划算标签」优先级排序
        PRIORITY = ["超值", "人气", "畅销单品", "全球爆款", "全天供应",
                    "原肉整切", "浓郁", "风味"]
        def score(x: Dict[str, Any]) -> int:
            tags = x.get("tags") or []
            s = 0
            for i, kw in enumerate(PRIORITY):
                if kw in tags:
                    s += len(PRIORITY) - i
            # 单品优先于套餐（预算紧时更灵活）
            if "单品" in tags:
                s += 2
            return s

        ranked = sorted(no_price, key=lambda z: -score(z))
        picked: List[Dict[str, Any]] = []
        seen_cat = set()
        for x in ranked:
            cat = x.get("category") or ""
            # 尽量覆盖多个分类，避免推荐出一堆同类
            if cat not in seen_cat or len(picked) < 2:
                picked.append(dict(x))
                seen_cat.add(cat)
            if len(picked) >= a.limit * 2:
                break

        for c in picked:
            c["_reason"] = "标签命中: " + ",".join(
                t for t in (c.get("tags") or []) if t in PRIORITY) or "热门"
        result["single_candidates"] = picked[:a.limit * 2]
        result["price_unknown_count"] = len(no_price)
        result["_note"] = (
            "菜单响应被 4000 字符截断，未取到价格表，**无法按预算精算**。"
            "以上按标签/分类排序给出推荐，真实金额必须用 "
            "`menu price --codes <c1>,<c2>,<c3>` 一次批量试算确认。")

    # 营养标注（若缓存了营养库）
    nut_rows = load_nutrition_cache()
    if nut_rows and a.nutrition:
        for c in result.get("single_candidates", []):
            m = mm_calc.match_nutrition(c.get("name") or "", nut_rows)
            c["nutrition"] = {
                "status": m["status"],
                "energy_kcal": (m.get("matched") or {}).get("energy_kcal")
                if m.get("matched") else mm_calc.UNKNOWN,
                "confidence": m.get("confidence"),
            }

    _print(result, brief=a.brief)


# --------------------------------------------------------------------------
# 子命令 3：price（1 次 MCP，items 数组批量试算）
# --------------------------------------------------------------------------

def cmd_price(a: argparse.Namespace) -> None:
    """批量试算。**items 数组一次传多个商品**，而不是循环调用。

    实测曾用 24 次单点试算导致 token 爆炸；这里压缩为 1 次。
    """
    from probe_mcp import StreamableClient, unwrap, MCP_URL, load_token

    # 支持 "code:qty" 语法，例如 "9900016511:2,521966:1"
    # 组合方案常需要同一商品 2 份（如两份炸鸡），没有数量支持就只能拆成多次试算
    parsed: List[Tuple[str, int]] = []
    for token in (c.strip() for c in a.codes.split(",")):
        if not token:
            continue
        if ":" in token:
            code, _, q = token.partition(":")
            try:
                qty = int(q)
            except ValueError:
                raise SystemExit(f"数量格式错误: {token}（应形如 code:2）")
            if qty < 1:
                raise SystemExit(f"数量必须 >= 1: {token}")
        else:
            code, qty = token, 1
        parsed.append((code.strip(), qty))

    if not parsed:
        raise SystemExit("请用 --codes 指定至少一个商品编码")
    if len(parsed) > 10:
        raise SystemExit(
            f"一次最多试算 10 个商品（收到 {len(parsed)}）。"
            "请先收窄候选再试算 —— 这是调用次数红线。")
    # 实测：商品数 > ~6 时 calculate-price 响应会超过 4000 字符被截断，
    # 导致解析失败。提前给出可操作提示，避免调用方反复试错。
    if len(parsed) > 6:
        print(f"[warn] 一次试算 {len(parsed)} 个商品，响应可能被 4000 字符截断；"
              "建议拆成每批 <=5 个", file=sys.stderr)

    items = [{"productCode": c, "quantity": q} for c, q in parsed]

    args: Dict[str, Any] = {
        "storeCode": a.store,
        "orderType": a.order_type,
        "beType": a.be_type,
        "items": items,
        "needTableware": a.tableware,
    }
    if a.be_code and a.be_type != 1:
        args["beCode"] = a.be_code

    cli = StreamableClient(MCP_URL, load_token())
    cli.initialize()
    raw = unwrap(cli.call("calculate-price", args))

    # ⚠️ 实测 calculate-price 返回是「Markdown 双段 + 内嵌 JSON」，
    # mm_calc._unwrap 对它会失败（它只认纯 dict/JSON）。
    # 必须走 _extract_json。
    txt = raw.get("_text", "") if isinstance(raw, dict) else str(raw)
    src = mm_calc._extract_json(txt) or mm_calc._unwrap(raw)
    data = (src or {}).get("data") if isinstance(src, dict) else None
    if not isinstance(data, dict):
        print("[err] calculate-price 返回无法解析，原文前 400 字：", file=sys.stderr)
        print(str(txt)[:400], file=sys.stderr)
        raise SystemExit(1)

    # 汇总
    products = []
    for p in (data.get("productList") or []):
        products.append({
            "code": p.get("productCode"),
            "name": p.get("productName"),
            "qty": p.get("quantity"),
            "subtotal_cents": p.get("subtotal"),
            "original_subtotal_cents": p.get("originalSubtotal"),
        })

    # 记住品名与单价，下次不必再花钱认它
    remembered = remember_products(products, store=a.store)

    # 回填菜单缓存：calculate-price 会返回真实商品名与单价，
    # 正好补上 query-meals 被截断时缺失的字段（下次 candidates 就有名称了）
    try:
        items_cache = load_menu(a.store, a.order_type, a.be_type)
        idx = {x["code"]: x for x in items_cache}
        changed = False
        for p in products:
            if not isinstance(p.get("code"), str):
                continue
            # 打包服务费之类的自动项不入菜单
            if p["code"] in idx:
                tgt = idx[p["code"]]
                if p.get("name") and not tgt.get("name"):
                    tgt["name"] = p["name"]
                    tgt["price_unknown"] = False
                    changed = True
                # ⚠️ 必须除以数量再写回 —— 实测踩坑：
                # 按 2 份试算时 subtotal=2580，直接写回会把**小计**当成**单价**，
                # 导致「蘸蘸炸鸡」在菜单里从 ¥12.9 变成 ¥25.8。
                # price_cache 里存的是归一后的单价，回填口径必须一致。
                sub = p.get("subtotal_cents")
                q = p.get("qty") or 1
                if sub and q:
                    unit = int(round(sub / q))
                    if tgt.get("price_cents") != unit:
                        tgt["price_cents"] = unit
                        tgt["price_unknown"] = False
                        changed = True
        if changed:
            mm_state.atomic_write_json(
                menu_cache_path(a.store, a.order_type, a.be_type),
                {"store": a.store, "order_type": a.order_type,
                 "be_type": a.be_type, "fetched_at": mm_state.iso_now(),
                 "count": len(items_cache), "items": items_cache})
    except SystemExit:
        pass

    result = {
        "store": a.store,
        "goods_cents": data.get("productPrice"),
        "delivery_cents": data.get("deliveryPrice"),
        "packing_cents": data.get("packingPrice"),
        "discount_cents": data.get("discount"),
        "payable_cents": data.get("price"),
        "original_cents": data.get("originalPrice"),
        "items": products,
        "item_count": len(items),
        "remembered": remembered,
        "_note": "真实金额，以此为准；预估/试算优惠与实际优惠不可混用。",
    }

    # 自洽校验（复用 mm_calc）
    check = mm_calc.classify_trial_discount(
        goods_total_cents=data.get("productPrice"),
        discount_cents=data.get("discount"),
        payable_cents=data.get("price"),
        delivery_fee_cents=data.get("deliveryPrice"),
        packing_fee_cents=data.get("packingPrice"),
    )
    result["consistent"] = check.get("consistent")

    if a.brief:
        result = {k: v for k, v in result.items() if k != "_note"}
    _print(result)


# --------------------------------------------------------------------------
# 营养库缓存（供 candidates 标注热量）
# --------------------------------------------------------------------------

NUTRITION_CACHE = mm_state.home() / "nutrition_cache.json"


def load_nutrition_cache() -> List[Dict[str, Any]]:
    p = NUTRITION_CACHE
    if not p.exists():
        return []
    try:
        age_h = (mm_state.datetime.now() - mm_state.datetime.fromtimestamp(
            p.stat().st_mtime)).total_seconds() / 3600
        if age_h >= 24:
            return []
    except OSError:
        return []
    d = mm_state.read_json(p, {}) or {}
    return mm_calc.parse_nutrition_payload(d)


def cmd_nutrition_cache(a: argparse.Namespace) -> None:
    """拉取营养库并缓存（24h TTL）。静态数据，不需要每次拉。"""
    from probe_mcp import StreamableClient, unwrap, MCP_URL, load_token

    cli = StreamableClient(MCP_URL, load_token())
    cli.initialize()
    raw = unwrap(cli.call("list-nutrition-foods"))
    rows = mm_calc.parse_nutrition_payload(raw)
    mm_state.atomic_write_json(NUTRITION_CACHE, {
        "fetched_at": mm_state.iso_now(), "count": len(rows), "raw": raw,
    })
    _print({"cached": str(NUTRITION_CACHE), "count": len(rows),
            "_note": "官方称 160 条，但响应被 4000 字符截断，实测通常只有 ~90 条。"},
           brief=a.brief)


# --------------------------------------------------------------------------

def cmd_identify(a: argparse.Namespace) -> None:
    """一次批量「认识」若干编码：返回品名 + 单价，并写入持久缓存。

    ## 存在理由（2026-10-10 实测）

    菜单被截断时 44 个品项**名字全是 null**，要认识它们只能试算。
    实测为了认 9 个炸鸡候选，逐个调用花掉 7 次 MCP。
    本子命令明确按 `SAFE_BATCH` 分批，**一次认一批**并落缓存，
    下次连这批都不用再花调用。

    与 `menu price` 的区别：本命令只为「认名字/拿单价」，
    不关心配送费与打包费，输出更精简。
    """
    parsed: List[Tuple[str, int]] = []
    for token in (t.strip() for t in a.codes.split(",")):
        if not token:
            continue
        code, _, q = token.partition(":")
        try:
            qty = int(q) if q else 1
        except ValueError:
            raise SystemExit(f"数量格式错误: {token}")
        parsed.append((code.strip(), max(1, qty)))
    if not parsed:
        raise SystemExit("请用 --codes 指定编码")

    cache = load_price_cache()
    todo = [c for c, _ in parsed if c not in cache or not cache[c].get("name")]

    if not todo:
        out = [{"code": c, "name": cache[c].get("name"),
                "price_cents": cache[c].get("price_cents"), "from": "cache"}
               for c, _ in parsed]
        _print({"identified": len(out), "mcp_calls": 0, "items": out,
                "_note": "全部命中缓存，未产生任何 MCP 调用。"})
        return

    if len(todo) > SAFE_BATCH:
        print(f"[warn] 一次最多认 {SAFE_BATCH} 个（实测超过会触发 4000 字符截断）。"
              f"收到 {len(todo)} 个，仅处理前 {SAFE_BATCH} 个；"
              f"其余请下一批。", file=sys.stderr)
        todo = todo[:SAFE_BATCH]

    from probe_mcp import StreamableClient, unwrap, MCP_URL, load_token

    args: Dict[str, Any] = {
        "storeCode": a.store, "orderType": a.order_type, "beType": a.be_type,
        "items": [{"productCode": c, "quantity": 1} for c in todo],
        "needTableware": False,
    }
    if a.be_code and a.be_type != 1:
        args["beCode"] = a.be_code

    cli = StreamableClient(MCP_URL, load_token())
    cli.initialize()
    raw = unwrap(cli.call("calculate-price", args))
    txt = raw.get("_text", "") if isinstance(raw, dict) else str(raw)
    src = mm_calc._extract_json(txt) or mm_calc._unwrap(raw)
    data = (src or {}).get("data") if isinstance(src, dict) else None
    if not isinstance(data, dict):
        print("[err] 无法解析返回", file=sys.stderr)
        print(str(txt)[:300], file=sys.stderr)
        raise SystemExit(1)

    prods = [{"code": p.get("productCode"), "name": p.get("productName"),
              "qty": p.get("quantity"), "subtotal_cents": p.get("subtotal")}
             for p in (data.get("productList") or [])]
    remember_products(prods, store=a.store)

    cache = load_price_cache()
    out = []
    for c, _ in parsed:
        rec = cache.get(c) or {}
        out.append({"code": c, "name": rec.get("name"),
                    "price_cents": rec.get("price_cents"),
                    "from": "cache" if c not in todo else "fetched"})

    _print({
        "identified": len([x for x in out if x["name"]]),
        "mcp_calls": 1,
        "requested": len(parsed),
        "fetched": len(todo),
        "items": out,
    })


# 中文点餐意图的同义词展开
#
# 为什么需要（2026-10-10 实测）：用户说「想吃炸鸡」，
# 但「麦辣鸡腿汉堡」「龙焰鸡腿堡」「蘸蘸炸鸡」里**只有后者含连续的「炸鸡」二字**，
# 纯子串匹配会把整片炸鸡类商品漏掉。
# 这里把常见意图展开成一组词，按「任一命中」匹配。
WANT_SYNONYMS: Dict[str, List[str]] = {
    "炸鸡": ["炸鸡", "鸡腿堡", "鸡排", "鸡块", "鸡翅", "脆汁鸡", "麦香鸡", "麦乐鸡"],
    "鸡": ["鸡"],
    "汉堡": ["汉堡", "鸡腿堡", "牛堡", "鱼堡", "麦辣鸡腿", "板烧"],
    "牛肉": ["牛肉", "牛堡", "巨无霸", "双层牛"],
    "鱼": ["鱼", "麦香鱼", "鳕鱼"],
    "小食": ["小食", "薯条", "鸡块", "鸡翅", "派", "拼盘", "脆薯"],
    "薯条": ["薯条", "脆薯"],
    "饮料": ["可乐", "饮品", "汽水", "咖啡", "茶"],
    "咖啡": ["咖啡", "拿铁", "美式"],
    "冰淇淋": ["冰淇淋", "麦旋风", "新地", "圆筒"],
    "甜品": ["冰淇淋", "麦旋风", "新地", "圆筒", "派"],
    "早餐": ["早餐", "松饼", "猪柳", "满福堡"],
    "套餐": ["套餐", "三件套", "四件套", "单人餐", "双人餐"],
    "便宜": ["超值", "单品"],
}


def expand_wants(wants: List[str]) -> List[str]:
    """把用户意图词展开成同义词组，避免中文子串匹配漏项。"""
    out: List[str] = []
    for w in wants:
        w = (w or "").strip().lower()
        if not w:
            continue
        out.append(w)
        for k, syns in WANT_SYNONYMS.items():
            if k in w or w in k:
                out.extend(s.lower() for s in syns)
    # 去重保序
    seen = set()
    res = []
    for w in out:
        if w not in seen:
            seen.add(w)
            res.append(w)
    return res


def _plausible(it: Dict[str, Any], wants: List[str]) -> int:
    """给「这条可能相关」打分，用于**名字未知时**猜该先认哪一批。

    query-meals 截断后绝大多数品项名字为 null，没法直接按名字筛选。
    在认名字之前，只能靠分类与 tags 推断相关性：
      分类「鸡肉汉堡/卷」「蘸酱炸鸡」、标签「单品/超值」等。
    分数高的先认，命中目标品的概率更大，减少批次。
    """
    b = ((it.get("name") or "") + " " + (it.get("category") or "") + " "
         + ",".join(it.get("tags") or [])).lower()
    s = 0
    for w in wants:
        if w and w in b:
            s += 50
    # 与「吃」相关的常见词（名字未知时的弱信号）
    for kw in ("炸鸡", "鸡", "小食", "蘸", "汉堡", "翅", "堡", "卷"):
        if kw in b:
            s += 6
    tags = it.get("tags") or []
    if "单品" in tags:
        s += 5              # 单品比套餐更容易压在预算内
    if "超值" in tags:
        s += 3
    if "人气" in tags or "畅销单品" in tags or "全球爆款" in tags:
        s += 3
    return s


def cmd_plan(a: argparse.Namespace) -> None:
    """一站式：拉菜单 → 本地筛候选 → 一次认价，返回紧凑候选清单。

    ## 存在理由（2026-10-10 实测）

    实测单次 Python 启动约 **1060 ms**。原先走完「选餐」要依次调用
    fetch / identify / candidates / price —— 4 次进程启动约 4.2 秒纯开销，
    且每次都要把中间结果写进上下文（token 也有成本）。

    本命令把这几步合并成**一次进程**：
      1. 菜单缓存新鲜 → 直接用（**0 次 MCP**）
         不新鲜/缺失 → fetch（1 次 MCP）
      2. 按 --want 关键词 + 预算本地筛候选（0 次 MCP）
      3. 一次 calculate-price 批量认候选的品名与单价（1 次 MCP，≤SAFE_BATCH 个）

    合计 **1~2 次 MCP、1 次进程启动**，输出单行紧凑 JSON。

    之后用户选定，只需再跑一次 `menu price` 拿最终金额。
    """
    store = a.store

    # ---- 1. 菜单：缓存优先 ----
    mcp_calls = 0
    cache_p = menu_cache_path(store, a.order_type, a.be_type)
    need_fetch = not _cache_fresh(cache_p)

    if need_fetch and not a.no_fetch:
        from probe_mcp import StreamableClient, unwrap, MCP_URL, load_token

        args: Dict[str, Any] = {"storeCode": store,
                                "orderType": a.order_type, "beType": a.be_type}
        if a.be_code and a.be_type != 1:
            args["beCode"] = a.be_code
        cli = StreamableClient(MCP_URL, load_token())
        cli.initialize()
        raw = unwrap(cli.call("query-meals", args))
        items = parse_menu(raw)
        mm_state.atomic_write_json(cache_p, {
            "store": store, "order_type": a.order_type, "be_type": a.be_type,
            "fetched_at": mm_state.iso_now(),
            "count": len(items), "items": items})
        mcp_calls += 1

    items = load_menu(store, a.order_type, a.be_type)

    # ---- 2. 本地筛候选 ----
    wants = expand_wants(a.want or [])
    avoid = [w.lower() for w in (a.avoid or [])]

    def blob(it: Dict[str, Any]) -> str:
        return ((it.get("name") or "") + " " + (it.get("category") or "") + " "
                + ",".join(it.get("tags") or [])).lower()

    def full_pool() -> List[Dict[str, Any]]:
        """未按 want 过滤的池（只排除忌口）。"""
        return [x for x in items
                if not (avoid and any(w in blob(x) for w in avoid))]

    def matched() -> List[Dict[str, Any]]:
        """按 want 匹配的池；没有 want 时等于 full_pool。"""
        p_ = full_pool()
        if not wants:
            return p_
        return [x for x in p_ if any(w in blob(x) for w in wants)]

    def pool_now() -> List[Dict[str, Any]]:
        m = matched()
        return m if m else full_pool()

    overhead = a.overhead if a.overhead is not None else (
        DEFAULT_DELIVERY_CENTS + DEFAULT_PACKING_CENTS)

    def est(it: Dict[str, Any]) -> Optional[int]:
        p = it.get("price_cents")
        return (p + overhead) if p is not None else None

    def rank(it: Dict[str, Any]) -> Tuple[int, int]:
        e = est(it)
        if e is not None:
            return (0 if (a.budget is None or e <= a.budget) else 2, e)
        return (1, 0)

    # ---- 3. 认价（关键：先认名字，否则按名字筛选会漏掉全部未知项）----
    #
    # 实测坑：44 个品项里 35 个名字是 null，`--want 炸鸡` 只能命中
    # 已缓存过名字的 1 个，等于筛不出来。
    # 因此这里**先认一批，再按 want 重筛**；命中不足时继续认下一批。
    rounds = 0
    while not a.no_fetch and rounds < max(1, a.identify_rounds):
        # 退出条件必须是「**已认价**且匹配的候选够数」。
        # ⚠️ 不能只看匹配数：同义词展开后有「鸡」这类宽词，
        # 分类「鸡肉汉堡/卷」就能匹配上，但那些项名字/价格仍是 null，
        # 等于没认出任何东西就退出了（实测清空缓存后踩到这个坑：
        # mcp_calls:1、unknown_left:49，一个都没认）。
        priced = [x for x in matched() if not x.get("price_unknown")]
        if len(priced) >= a.limit:
            break
        # 候选从**全量池**里选，不能从 matched() 里选 ——
        # matched() 几乎为空时，从里面找「未知项」永远是空。
        unknown = [x for x in full_pool() if x.get("price_unknown")]
        if not unknown:
            break
        # 认哪些？优先「可能相关」的：分类/标签与 want 沾边，其次按评分
        unknown.sort(key=lambda x: -_plausible(x, wants))
        batch = unknown[:SAFE_BATCH]
        if not batch:
            break

        from probe_mcp import StreamableClient, unwrap, MCP_URL, load_token

        args2: Dict[str, Any] = {
            "storeCode": store, "orderType": a.order_type, "beType": a.be_type,
            "items": [{"productCode": x["code"], "quantity": 1} for x in batch],
            "needTableware": False}
        if a.be_code and a.be_type != 1:
            args2["beCode"] = a.be_code
        cli2 = StreamableClient(MCP_URL, load_token())
        cli2.initialize()
        raw2 = unwrap(cli2.call("calculate-price", args2))
        t2 = raw2.get("_text", "") if isinstance(raw2, dict) else str(raw2)
        src2 = mm_calc._extract_json(t2) or mm_calc._unwrap(raw2)
        d2 = (src2 or {}).get("data") if isinstance(src2, dict) else None
        if not isinstance(d2, dict):
            break
        prods = [{"code": p.get("productCode"), "name": p.get("productName"),
                  "qty": p.get("quantity"), "subtotal_cents": p.get("subtotal")}
                 for p in (d2.get("productList") or [])]
        remember_products(prods, store=store)
        mcp_calls += 1
        rounds += 1
        items = load_menu(store, a.order_type, a.be_type)

    # ---- 输出紧凑候选 ----
    pool = pool_now()
    pool.sort(key=rank)
    cands = []
    for it in pool:
        if a.budget is not None:
            e = est(it)
            if e is not None and e > a.budget:
                continue
        cands.append({
            "code": it["code"],
            "name": it.get("name"),
            "price_cents": it.get("price_cents"),
            "total_cents": est(it),
            "cat": (it.get("category") or "").replace("\n", ""),
            "tags": it.get("tags") or [],
        })
        if len(cands) >= a.limit:
            break

    out: Dict[str, Any] = {
        "store": store,
        "budget_cents": a.budget,
        "overhead_cents": overhead,
        "candidates": cands,
        "mcp_calls": mcp_calls,
        "menu_total": len(items),
        "unknown_left": sum(1 for x in items if x.get("price_unknown")),
    }
    if not cands:
        out["_hint"] = ("无候选。可放宽 --budget、去掉 --want，"
                        "或先 menu fetch 刷新菜单。")
    elif any(c["price_cents"] is None for c in cands):
        out["_hint"] = "仍有未认价候选（每批最多 %d 个），可再跑一次 plan 认余下。" % SAFE_BATCH
    else:
        out["_hint"] = "金额为「单价+估算配送打包」，最终请用 menu price 试算确认。"

    _print(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="菜单抓取 / 候选筛选 / 批量试算")
    ap.add_argument("--brief", action="store_true", help="精简输出")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # fetch
    f = sub.add_parser("fetch", help="拉菜单并缓存（1 次 MCP）")
    f.add_argument("--store", required=True)
    f.add_argument("--order-type", type=int, default=1, choices=[1, 2])
    f.add_argument("--be-type", type=int, required=True, choices=[1, 2, 5, 6])
    f.add_argument("--be-code", help="beType=5/2/6 时必传")
    f.add_argument("--brief", action="store_true")
    f.set_defaults(func=cmd_fetch)

    # candidates
    c = sub.add_parser("candidates", help="本地筛候选（0 次 MCP）")
    c.add_argument("--store", required=True)
    c.add_argument("--order-type", type=int, default=1, choices=[1, 2])
    c.add_argument("--be-type", type=int, required=True, choices=[1, 2, 5, 6])
    c.add_argument("--budget", type=int, help="预算，单位分")
    c.add_argument("--limit", type=int, default=3)
    c.add_argument("--tags", nargs="*", help="口味偏好关键词")
    c.add_argument("--avoid", nargs="*", help="忌口关键词")
    c.add_argument("--nutrition", action="store_true", help="标注热量（需先缓存营养库）")
    c.add_argument("--overhead", type=int, help="配送+打包估算（分），默认 710")
    c.add_argument("--brief", action="store_true")
    c.set_defaults(func=cmd_candidates)

    # price
    p = sub.add_parser("price", help="批量试算（1 次 MCP）")
    p.add_argument("--store", required=True)
    p.add_argument("--codes", required=True, help="逗号分隔，最多 10 个")
    p.add_argument("--order-type", type=int, default=1, choices=[1, 2])
    p.add_argument("--be-type", type=int, required=True, choices=[1, 2, 5, 6])
    p.add_argument("--be-code")
    p.add_argument("--tableware", action="store_true", help="需要餐具")
    p.add_argument("--brief", action="store_true")
    p.set_defaults(func=cmd_price)

    # nutrition-cache
    n = sub.add_parser("nutrition-cache", help="拉取营养库并缓存（1 次 MCP）")
    n.add_argument("--brief", action="store_true")
    n.set_defaults(func=cmd_nutrition_cache)

    # identify
    # plan：一站式（fetch + 筛选 + 认价）
    pl = sub.add_parser("plan",
                        help="一站式：拉菜单→筛候选→批量认价（1~2 次 MCP，1 次进程）")
    pl.add_argument("--store", required=True)
    pl.add_argument("--order-type", type=int, default=1, choices=[1, 2])
    pl.add_argument("--be-type", type=int, required=True, choices=[1, 2, 5, 6])
    pl.add_argument("--be-code")
    pl.add_argument("--budget", type=int, help="预算（分）")
    pl.add_argument("--want", nargs="*", help="想要的关键词，如 炸鸡")
    pl.add_argument("--avoid", nargs="*", help="忌口关键词")
    pl.add_argument("--limit", type=int, default=5)
    pl.add_argument("--overhead", type=int, help="配送+打包估算（分），默认 710")
    pl.add_argument("--no-fetch", action="store_true",
                    help="只用缓存，完全不发 MCP（离线跑/调试用）")
    pl.add_argument("--identify-rounds", type=int, default=2,
                    help="最多认几批（每批 %d 个），默认 2" % SAFE_BATCH)
    pl.set_defaults(func=cmd_plan)

    i2 = sub.add_parser("identify",
                        help=f"批量认品名+单价并落缓存（1 次 MCP，每批 ≤{SAFE_BATCH}）")
    i2.add_argument("--store", required=True)
    i2.add_argument("--codes", required=True, help="逗号分隔，形如 901440,521966")
    i2.add_argument("--order-type", type=int, default=1, choices=[1, 2])
    i2.add_argument("--be-type", type=int, required=True, choices=[1, 2, 5, 6])
    i2.add_argument("--be-code")
    i2.add_argument("--brief", action="store_true")
    i2.set_defaults(func=cmd_identify)

    # 每个子命令都支持 --pretty（argparse 要求它出现在子命令之后）
    for name, sp_ in sub.choices.items():
        sp_.add_argument("--pretty", action="store_true",
                         help="缩进输出（人读用；默认紧凑单行，省约 40% 字符）")

    a = ap.parse_args()

    global _OUT_PRETTY
    _OUT_PRETTY = bool(getattr(a, "pretty", False))

    a.func(a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
