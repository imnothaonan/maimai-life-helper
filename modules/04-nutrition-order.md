# 模块 4 · 营养点餐与麦乐送

## 涉及工具

> 实测入参（2026-10-10 从 `tools/list` 拉取，`*` 为必填）。

| 工具 | 类型 | 实测入参 |
|---|---|---|
| `delivery-query-addresses` | 只读 | **无入参** |
| `delivery-create-address` | **写** | `address`*, `addressDetail`*, `city`*, `contactName`*, `phone`*, `gender` |
| `delivery-query-stores` | 只读 | `addressId`*, `beType`* |
| `query-nearby-stores` | 只读 | `beType`*, `searchType`*, `city`, `keyword` |
| `query-store-coupons` | 只读 | `beType`*, `orderType`*, `storeCode`* |
| `query-meals` | 只读 | `beType`*, `orderType`*, `storeCode`* |
| `query-meal-detail` | 只读 | `beType`*, `code`*, `orderType`*, `storeCode`* |
| `list-nutrition-foods` | 只读 | **无入参** |
| `calculate-price` | 只读（试算） | 同 `create-order` 减 `addressId` |
| `create-order` | **写（产生支付）** | `beType`*, `orderType`*, `storeCode`* + `items`/`addressId`/`gmServiceCode`/`needTableware`/`remark`/`reservationDate`/`takeWayCode`/`withOrder` |
| `query-order` | 只读 | `orderId`* |
| `cancel-order` | **写（不可逆）** | `orderId`*, `cancelReasonCode`* |
| `order-list` | 只读 | **无入参** |
| `query-meal-assistance` | 只读 | `storeCode`*（其余可选） |
| `query-promotions` | 只读 | `beType`*, `orderType`*, `storeCode`*（README 未提及） |
| `query-survey-coupon` | 只读 | `orderId`*（README 未提及，评价返券） |

> ✅ **实测证实**：`create-order` 的 `needTableware`（餐具）、`remark`（备注）、
> `reservationDate`（预约时间）、`takeWayCode`（取餐方式）**都是真实存在的独立字段**，
> 不是独立工具 —— 与版本日志的推断一致。
>
> ⚠️ `cancel-order` 需要 `cancelReasonCode`，**不能空着调用**，需先确认原因码来源。
>
> ✅ **beType / orderType 枚举值已实测确认**，见 `SKILL.md` §1.5 速查表，不要再猜：
>
> | 场景 | beType | orderType | beCode |
> |---|---|---|---|
> | 到店自取 | 1 | 1 | **不传** |
> | 得来速 | 5 | 1 | 必传 |
> | 麦乐送 | 2 | 2 | 必传 |
> | 团餐 | 6 | 2 | 必传 |

---

## 完整流程（6 步主路径）

> ⚠️ **以下是必选的全部步骤**。不要自行加步骤。
> 可选增强见下方「可选增强」，只在用户明确要求时做。

```
1. 确定就餐方式与门店
   - 到店：读 geo 偏好（见「位置记忆」），无则问城市 → 写入 geo.*
           query-nearby-stores(searchType=2, city, keyword) → 让用户选门店
   - 外送：delivery-query-addresses → **按地址纪律让用户选地址**
           delivery-query-stores(addressId, beType=2) → 选门店

2. menu.py fetch 拉菜单（1 次 MCP，自动落盘缓存）
   python scripts/menu.py fetch --store <code> --order-type <1|2> --be-type <1|2|5>

3. menu.py candidates 本地筛候选（0 次 MCP）
   python scripts/menu.py candidates --budget <分> --limit 3
   配 mm_calc.py match-nutrition 标注热量（匹配不上标未知）

4. menu.py price 批量试算（≤1 次 MCP，items 数组传全部候选）
   python scripts/menu.py price --codes <c1>,<c2>,<c3>

5. 【展示候选 + 成本 + 优惠构成 → 等用户确认】

6. 过幂等闸门下单
   ledger-begin --kind create-order --op-key <幂等键>
   create-order → mm_calc.py actual → ledger-finish --status confirmed
```

**关键约束**：
- `calculate-price` 的 `items` 是数组，**一次可试算多个商品**。
  实测曾逐个编码试算 24 次，占全程 token 60% 以上 —— **严禁这样做**。
- 候选必须从**菜单单价字段**筛出，不能用试算反推价格。
- `query-meal-detail` **不含价格字段**，只在用户问「套餐能换什么」时调。

### 可选增强（仅用户明确要求时做）

| 增强 | 触发条件 |
|---|---|
| `list-nutrition-foods` + `match-nutrition` | 用户提「热量/卡路里/营养」 |
| `query-store-coupons` | 用户问「这个店能用什么券」 |
| `query-meal-detail` | 用户问「套餐里能换什么」 |
| `delivery-create-address` | 用户要求新增地址 |
| `mm_state.py` 存偏好 | 用户说「记住我的偏好」 |

---

## 地址选择纪律（无条件执行）

**每次点餐都必须先列出全部地址，即使只有一个。**

照 MCP 返回的表格字段**原样转述**，不要删列：

```
你的配送地址：
  1. <完整地址>
  2. <完整地址>

用哪个地址配送？
```

等用户明确回复序号后再继续。

**禁止**：
- ❌ 只有 1 个地址时静默跳过确认
- ❌ 替用户挑选「看起来最近」的地址
- ❌ 直接复用上次地址

---

## 位置记忆（到店场景）

位置**由用户口述并记忆，不做任何自动定位**。

> ⚠️ 本 Skill **没有** IP 自动定位能力。实测 IP 定位在本机会给出
> 北京/深圳两个互相矛盾的错误结果（与开发者实际所在城市也不符），
> 双源矛盾时无法自证正确，而点餐会产生真实支出，
> 静默送错城市代价大于"定不了"。因此**不做自动猜测**。

### 读偏好

```bash
python scripts/mm_state.py prefs-get --key geo
```

| 情况 | 做法 |
|---|---|
| 有 `city` | 带入门店搜索，并告知：「按【示例市 · 示例区】找门店？不对就说一声」 |
| 无 `city` | 问「你在哪个城市？」→ 写入 `geo.*` → 继续 |

### 写偏好

```bash
python scripts/mm_state.py prefs-set --key geo.city     --value '"示例市"'
python scripts/mm_state.py prefs-set --key geo.district --value '"示例区"'
python scripts/mm_state.py prefs-set --key geo.note     --value '"某某地铁站附近"'
python scripts/mm_state.py prefs-set --key geo.source   --value '"user_stated"'
```

### 诚实性约束

- `geo` **只用于**填 `query-nearby-stores` 的 `city` / `keyword`
- **禁止**用 `geo` 推测或构造配送地址
- 对用户只能说「**你之前说过**在 XX」，**禁止**说「**已定位到**你在 XX」
- `geo.source` 固定为 `user_stated`，文档与代码均注明这是**用户口述**而非自动定位
- 遵守 `geo-map-compliance-guard` 规范（本类位置服务请求强制生效）

### 外送不走 geo

`delivery-query-stores` 入参**只有 `addressId` + `beType`**（已核实 schema，
无经纬度入口），因此外送**必须**走 `delivery-query-addresses` + 地址纪律。
`geo` 仅服务于到店搜店。

---

## 旧版 18 步流程（已失效，仅供追溯）

> ❌ **已废弃**，不要再按这个走。
> 废弃原因：无试算收敛约束导致 `calculate-price` 被逐个调用 24 次；
> `query-store-coupons` 实测返回空；`query-meal-detail` 被误用于查价格。
> 现已被上方「6 步主路径」取代。

<details>
<summary>点击查看已失效的旧流程</summary>

```
1. 解析用户约束  2. 存偏好  3. delivery-query-addresses  4. delivery-query-stores
5. query-meals  6. list-nutrition-foods  7. match-nutrition  8. sum-nutrition
9. 生成候选  10. query-store-coupons  11. calculate-price  12. mm_calc.py price
13. 展示确认  14. ledger-begin  15. create-order  16. mm_calc.py actual
17. ledger-finish  18. 交付
```

</details>


---

## 营养匹配（核心诚实性要求）

### ⚠️ 实测返回格式（2026-10-10）——不是 JSON

`list-nutrition-foods` 的 `data` 字段是一个**字符串**：

```
[160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
  猪柳蛋麦满分,null,1618,387,23,21,25,846,243
  中薯条,null,1210,289,4,12,38,165,18
  麦乐鸡5块,null,890,213,12,12,13,422,9
  ...
```

即「表头带条目数 + 每行一条 CSV」。**实测官方称 160 条，但响应被上游按
4000 字符截断，只能取到约 90 条** —— 所以营养匹配存在天然覆盖缺口，
查不到时标未知是**正常且必要**的，不能靠猜。

### 匹配工具

```bash
# 解析真实营养库并查看覆盖情况（先看这个能确认匹配上限）
python scripts/mm_calc.py nutrition-all --file nutrition_raw.json --limit 10

# 单品匹配：匹配不上返回 status=unknown，不估算
python scripts/mm_calc.py match-nutrition "巨无霸" --file nutrition_raw.json

# 组合合计：任一项 unknown 则该维度标 unknown
python scripts/mm_calc.py sum-nutrition --file selected.json

# 是否在区间内
python scripts/mm_calc.py check-range --range 400-700 --file total.json
```

### 实测匹配效果

| 餐品 | 结果 |
|---|---|
| 中薯条 | `ok` / `high` / 289 kcal |
| 巨无霸 | `ok` / `high` / 513 kcal |
| 猪柳蛋麦满分 | `ok` / `high` / 387 kcal |
| **麦辣鸡腿堡** | **`unknown`** —— 营养库未收录，如实标未知 |

### 匹配结果三档处理

| `confidence` | 含义 | 汇报方式 |
|---|---|---|
| `high` | 名称完全匹配或互相包含 | 直接给出数值，并注明「匹配到《<营养库名称>》」 |
| `medium` | 分词部分重叠（相似度 ≥0.5） | 数值加 `⚠️ 待确认`：可能不是同一餐品/份量，需用户确认 |
| `none` | 无匹配 | **标「营养数据未知」**，不给数字 |

### 份量问题

营养库给的是**标准份**。用户加料、换规格、改套餐组合后营养会变。
此时必须说明：「以下为标准份营养，实际以你选择的规格为准」。
无法确定时标「未知」，**不要按比例自行外推**。

### 报告格式

```
营养估算（基于官方营养库标准份）
  猪柳蛋麦满分   387 kcal  蛋白 23g  脂肪 21g  碳水 25g  钠 846mg   [匹配:高]
  中薯条         289 kcal  蛋白 4g   脂肪 12g  碳水 38g  钠 165mg   [匹配:高]
  麦辣鸡腿堡     营养数据未知                                          [未知]
  ─────────────────────────────────────────────────────
  合计           676 kcal（麦辣鸡腿堡未计入，实际更高）
  目标区间       400-700 kcal  →  达标（但未计未知项）

  ⚠️ 未匹配到营养数据的品项已标注"未知"，未做任何估算。
  ⚠️ 官方营养库共 160 条，本次响应只返回约 90 条，存在覆盖缺口。
```

---

## 金额与优惠（四类严格分开）

```bash
python scripts/mm_calc.py price  --file calculate_price.json
python scripts/mm_calc.py merge  --owned 1200 --new 800 --trial 2000 --actual 1800
```

`price` 子命令会校验**自洽性**：
商品总额 − 优惠 + 配送费 是否等于应付。不自洽会给出差异与提示，
此时**以 `create-order` 的实付为准**，并向用户说明差异。

---

## 确认卡片

```
即将创建麦乐送订单：

  门店：    <门店名 / 距离 / 配送费>
  地址：    <用户选定的地址>
  商品：
    · <品名> ×<数量>   ¥<单价>
    · <品名> ×<数量>   ¥<单价>
  商品总额：      ¥XX.XX
  配送费：        ¥X.XX
  优惠构成：
    · 原有券：    ¥X.XX
    · 新领券：    ¥X.XX
    · 试算优惠：  ¥X.XX（未下单前为预估）
  应付（试算）：  ¥XX.XX
  营养：<合计 or 含未知项说明>

  ⚠️ 下单后将生成支付链接，确认请回复「确认下单」。
```

---

## 超时与取消

**`create-order` 超时**：

```bash
python scripts/mm_state.py ledger-finish --kind create-order --op-key <key> --status unknown
```

1. `query-order`（若有订单号）或 `order-list` → 核验是否已生成
2. 已生成 → `confirmed`，返回支付链接，**不要重复下单**
3. 未生成 → `rolled_back`，询问是否重试

**取消订单**（用户明确要求时）：

1. `query-order` 查当前状态，确认可取消
2. 展示订单号 + 取消后果
3. 确认后 `ledger-begin --kind cancel-order` → `cancel-order` → 收尾

---

## 边界

- `calculate-price` 是**试算**，不产生订单。「试算优惠」≠「实际省下的钱」。
- 门店不同，菜单与价格不同。**必须先 `query-meals` 再谈价格**，
  不能拿通用菜单报价。
- 优惠券是**门店维度**的（`query-store-coupons`），不是账户全局通用。
- `delivery-create-address` 涉及用户住址，**仅在用户明确要求新增时执行**。
