# 模块 6 · 主题活动（派对 / 品鉴会）

## 涉及工具

> ⚠️ **实测更正（2026-10-10）**：官方 README 写的 `query-partystore-date` /
> `query-partystore-session` **拼写有误**。实测 `tools/list` 返回的真实工具名是
> `query-party-store-date` / `query-party-store-session`（中间有连字符）。
> **以实测名为准**，详见 `TOOLS_VERIFIED.md`。

| 工具 | 类型 | 实测入参 | 说明 |
|---|---|---|---|
| `campaign-calendar` | 只读 | `specifiedDate`(可选) | 当月营销活动日历 |
| `query-party-city` | 只读 | **`spuId`\* 必填** | 可参与城市；spuId 需先从 `mall-points-products` / `mall-product-detail` 取 |
| `query-party-store` | 只读 | `code`\*, `latitude`, `longitude`, `spuId` | 城市下的门店；经纬度来自上一步 |
| `query-party-store-date` | 只读 | `spuId`\*, `storeCode`\* | 门店可预约日期 |
| `query-party-store-session` | 只读 | `spuId`\*, `storeCode`\*, `dateStr`\* | 指定日期的场次 |
| `party-order-create` | **写（产生支付）** | **`partyType`\* 必填**，另 14 个可选 | 主题活动下单 |

> **`query-party-city` 的 `spuId` 是必填**，但官方 README 未提及。
> 必须先查商城商品拿到 spuId 才能走通城市筛选 —— 这是实测发现的隐藏前置。

---

## 渐进式筛选（每一步都让用户选，不要一次问完）

```
1. mall-points-products / mall-product-detail  →  先拿 spuId（隐藏前置，实测必填）
2. campaign-calendar        → 看当月有什么活动（派对 / 品鉴会 / 其他）
3. 用户选定活动类型
4. query-party-city (spuId) → 【让用户选城市】
5. query-party-store        → 【让用户选门店】
6. query-party-store-date   → 【让用户选日期】
7. query-party-store-session → 【让用户选场次】
8. 【展示完整条件 + 费用 → 等确认】
9. ledger-begin --kind party-order-create --op-key <幂等键>
10. party-order-create
11. ledger-finish --status confirmed
12. 汇报：订单号、支付链接、活动详情
```

**每一步都要停下来等用户选**。不要自己替用户挑城市、门店或场次 ——
活动座位是有限的，选错无法挽回。

---

## 场次售罄处理

`query-party-store-session` 返回的场次可能已满。
- 照实说明该场次不可选。
- **不要**自动跳到其他场次，列出可选场次让用户自己定。
- 若返回中有剩余名额字段（如 `leftNum`），如实引用；没有就标「未提供」。

> 实测：`party-order-create` 有 `leftNum` 参数，说明服务端确实有库存概念，
> 但**具体剩余名额是否在查询接口返回里展示，需实际有活动时确认**。

---

## 确认卡片

```
即将预约主题活动：

  活动类型：  <派对 / 品鉴会>
  活动名称：  <官方名称>
  城市：      <城市>
  门店：      <门店名 / 地址>
  日期：      <日期>
  场次：      <时间段>
  参与人数：  <N>
  费用：      ¥XX.XX（若免费需明确写"免费"）
  支付方式：  <下单后返回支付链接>

  ⚠️ 活动名额有限，下单后不可随意取消。
  确认预约请回复「确认预约」。
```

---

## 超时处理

`party-order-create` 超时：

```bash
python scripts/mm_state.py ledger-finish --kind party-order-create --op-key <key> --status unknown
```

1. 用 `query-order` / `order-list` 核验是否已生成活动订单
2. 已生成 → `confirmed`，返回订单号与支付链接
3. 未生成 → `rolled_back`，询问用户是否重试

活动场次是**有限资源**，重复下单会占用他人名额，
因此**超时后绝不自动重试**，必须核验 + 确认。

---

## 与模块 1 的联动

- `campaign-calendar` 同时是**领券模块**的资讯来源：
  有新活动上线往往伴随新券。定时任务里可以查日历作为只读侦察，
  但**不要**因此自动下单或预约。
- 用户问「最近有什么活动」时，先 `campaign-calendar` 看当月全貌，
  再按需深入 `query-party-*`。

---

## 边界

- `query-party-*` 是**逐层收窄**的，必须按顺序调用：
  城市 → 门店 → 日期 → 场次，缺前一步就拿不到下一步的入参。
- **工具名以实测为准**：官方 README 的 `query-partystore-date` /
  `query-partystore-session` 拼写有误，实测为 `query-party-store-date` /
  `query-party-store-session`。
- **`spuId` 是隐藏前置**：`query-party-city` / `query-party-store` /
  `query-party-store-date` / `query-party-store-session` 都依赖它，
  须先从 `mall-points-products` 或 `mall-product-detail` 获取。
- 费用、包含内容、注意事项以 `party-order-create` 返回与活动说明为准，
  **不要替官方补充活动规则**。
- 若某城市/门店查不到活动，如实说「该范围暂无可预约活动」，
  不要编造场次。
