# 模块 5 · 常用餐单与预约取餐

## 涉及工具

| 工具 | 类型 | 说明 |
|---|---|---|
| `order-list` | 只读 | 近期待店/外送历史订单（非商城订单） |
| `query-order` | 只读 | 订单详情、状态、取餐柜二维码 |
| `query-meals` | 只读 | 当前门店在售菜单 |
| `query-meal-detail` | 只读 | 套餐组成、**可替换项** |
| `query-nearby-stores` | 只读 | 附近门店 |
| `query-store-coupons` | 只读 | 门店可用券 |
| `calculate-price` | 只读（试算） | 核价 |
| `create-order` | **写（产生支付）** | 下单 |
| `cancel-order` | **写（不可逆）** | 取消 |

---

## 版本日志里的能力去哪了（重要）

v1.0.4 ~ v1.0.9 提到这些能力，**但官方 README 的工具表里没有独立工具**：

| 版本日志描述 | 最可能的承载方式 |
|---|---|
| 得来速车道取餐 | `create-order` 的取餐方式参数 |
| 全部点餐场景预约 | `create-order` 的预约时间参数 |
| 更换套餐内商品组合 | 经 `query-meal-detail` 查询后，在 `create-order` 里指定替换项 |
| 部分餐品特制 | `create-order` 的定制参数（可能分字段） |
| 餐具选择 | `create-order` 的餐具参数 |
| 麦乐送订单备注 | `create-order` 的备注参数 |
| 取餐柜二维码 | **`query-order` / `create-order` 返回字段**（官方描述：「返回取餐柜二维码」） |
| 随单购买麦金卡/早餐卡 | `create-order` 的加购项参数 |
| 展示餐品优惠价格 | `query-meals` / `calculate-price` 返回字段 |

> ⚠️ 以上全部是**基于 README 描述的推断，不是实测结论**。
> 真实参数名与取值必须查 MCP 实际 schema；若 schema 中不存在，
> 应如实告知「当前 MCP 未开放该能力」，**不得臆造**。

---

## 流程 A：保存/复用常用餐单

### 保存

用户说「记住这个」「以后常点这个」时：

```bash
python scripts/mm_state.py prefs-set --key "orders.favorite_<别名>" --value '<餐单JSON>'
```

餐单 JSON 建议结构：

```json
{
  "alias": "工作日午餐",
  "store_fingerprint": "<门店标识>",
  "store_name": "<门店名>",
  "meal_type": "dine_in_takeout",
  "items": [{"mealCode": "<编码>", "qty": 1, "note": ""}]
}
```

> 复购时**仍要重新核验**：`query-meals` 确认在售 → `calculate-price` 确认价格。
> 编码可能已下架或涨价，**不能直接用历史订单的价格下单**。

### 复购

```
1. prefs-get 取回餐单
2. query-nearby-stores（或用户指定门店）
3. query-meals                     → 校验每个 item 是否仍在售
4. 报告变化：已下架 / 已涨价 / 已停售
5. query-store-coupons             → 当前可用券
6. calculate-price                 → 重新核价
7. 【展示差异 + 新价格 → 等确认】
8. create-order
```

## 流程 B：从近期订单复购

```
1. order-list                      → 列出近期订单（附订单号、时间、品项摘要）
2. 用户选定某单
3. query-order                     → 取明细
4. 核验门店与地址：
   - 外送复购需要 addressId，**必须先调 delivery-query-addresses 取**
     （历史订单里没有可直接复用的 addressId）
   - 然后按 modules/04 的「地址选择纪律」让用户确认地址
   - 到店复购用 query-nearby-stores（优先读 geo 偏好）
5. 核验在售 → menu.py fetch（缓存）+ menu.py candidates
6. menu.py price                   → 重新核价（不复用历史价格）
7. 展示「上次 ¥XX → 本次 ¥YY，差异原因」
8. ledger-begin → create-order → ledger-finish
```

> ⚠️ **addressId 来源**：`query-order` 返回的历史订单**不含可复用的 addressId**。
> 外送复购必须重新走 `delivery-query-addresses` 拿地址 ID，
> 并同样适用 `modules/04` 的「地址选择纪律」（每次都问，不静默复用）。

---

## 流程 C：预约取餐

```
1. 确定门店（query-nearby-stores）
2. query-meals                     → 选品
3. query-meal-detail（若套餐需替换）
4. calculate-price                 → 核价
5. 确认取餐方式：预约 / 到店自取 / 得来速
6. 确认预约时间 → 【展示成本 → 等确认】
7. ledger-begin --kind create-order --op-key <幂等键>
8. create-order（按 schema 传取餐方式与预约时间）
9. ledger-finish --status confirmed
10. 汇报：取餐柜二维码（若返回）、取餐号、预约时段
```

**取餐柜二维码**：若 `query-order` 或 `create-order` 返回中含
`pickupCode` / `qrCode` 之类字段，用 `mm_calc.py actual` 提取：

```bash
python scripts/mm_calc.py actual --file order.json
# → pickup_qrcode 字段
```

若返回中**没有**该字段，如实说明「当前订单未返回取餐柜二维码」，
不要编造链接或图片地址。

---

## 确认卡片

```
即将创建到店/预约取餐订单：

  门店：    <门店名>
  取餐方式：<预约取餐 / 到店自取 / 得来速>
  预约时段：<日期 时间段>（若为预约）
  商品：    <逐项列出>
  合计：    ¥XX.XX
  优惠构成：原有券 ¥X.XX / 新领券 ¥X.XX / 试算 ¥X.XX
  餐具：    <按用户要求，否则默认不填>
  备注：    <按用户要求，否则不填>

  ⚠️ 会员卡/麦金卡加购仅在你明确要求时才会加入，本次未加入。
  确认下单请回复「确认下单」。
```

---

## 会员卡加购纪律

官方 v1.0.6 起支持随单购买麦金卡/早餐卡。

- ❌ **不要主动**添加任何会员卡加购项。
- ✅ 仅当用户明确说「加一张麦金卡」「顺便买早餐卡」时，才加入候选并**单独列出价格**。
- 加入后必须在确认卡片里**单列一行**金额，让用户看清这项额外支出。

---

## 边界

- 历史订单里可能有已停售餐品，复购前必须 `query-meals` 校验。
- 预约时段的可选项以 `create-order` schema 为准；**没有的时段不要承诺**。
- 特制、餐具、备注均以 schema 实际字段为准；schema 无对应字段时明确告知不支持。
- 复购**永远重新核价**，历史价格只作对比参考。
