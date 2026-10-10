# 模块 2 · 积分兑换（麦麦商城）

## 涉及工具

| 工具 | 类型 | 说明 |
|---|---|---|
| `query-my-account` | 只读 | 可用积分 / 累计 / 冻结 / 即将过期 |
| `mall-points-products` | 只读 | 商城商品列表（积分兑换或现金购买） |
| `mall-product-detail` | 只读 | 商品详情：图片、积分、有效期、说明、**使用条件** |
| `mall-create-order` | **写（消耗积分）** | 积分校验 + 扣减 + 发券/库存扣减 |
| `mall-order-list` | 只读 | 近一年兑换/购买订单 |
| `mall-order-detail` | 只读 | 订单详情、支付积分、状态 |

> 官方说明：`mall-points-products` **不含**「用积分兑换的第三方兑换码」类商品。
> 用户想换的如果是第三方码，本 Skill **查不到**，应如实说明。

---

## 流程

```
1. query-my-account                    → 确认积分余额
2. mall-points-products                → 取候选
3. mall-product-detail（逐个候选）      → 取有效期、使用条件、说明
4. 生成推荐：积分成本 / 是否够用 / 有效期 / 使用条件 / 类型
5. 【展示成本 → 等用户明确确认】
6. ledger-begin --kind mall-create-order --op-key <幂等键>
7. mall-create-order
8. ledger-finish --status confirmed
9. 汇报：兑换订单号、券码/物流、实际扣除积分
```

---

## 推荐输出格式

对每个候选**必须**给出这五项，缺一不可：

| 字段 | 来源 | 缺失时 |
|---|---|---|
| 商品名 + 图片 | `mall-product-detail` | 标「未提供」 |
| **积分成本** | 商品详情 | 标「未提供」，不估算 |
| **是否够用** | `points_affordable` | 积分余额未知时标 `unknown` |
| **有效期** | `describe-expiry` | 标「未提供」 |
| **使用条件** | 商品详情说明 | 标「未提供」，**不得替官方补充规则** |

核算工具：

```bash
# 有效期 + 是否临近
python scripts/mm_calc.py expiry "2026-12-31"

# 积分够不够（脚本输出 affordable / remain_after）
python scripts/mm_calc.py cents 12000    # 积分换算辅助
```

余额对比：

```bash
python scripts/mm_state.py snapshot --kind account --label pre  --file account_before.json
python scripts/mm_state.py snapshot --kind account --label post --file account_after.json
python scripts/mm_state.py diff-account --before pre --after post
```

---

## 确认卡片（执行前必须展示）

```
即将使用积分兑换：

  商品：      <商品名>
  所需积分：  <N> 分
  当前余额：  <M> 分
  兑换后剩余：<M-N> 分
  有效期：    <日期或"未提供">
  使用条件：  <官方说明原文，未提供则明确写"未提供">

  ⚠️ 积分一经扣减通常不可退回。确认兑换请回复「确认兑换 <商品名>」。
```

---

## 超时处理

`mall-create-order` 超时 → **绝不能直接重试**（会重复扣积分）：

```bash
# 1) 记账为未知
python scripts/mm_state.py ledger-finish --kind mall-create-order --op-key <key> --status unknown

# 2) 用只读工具核验是否已生成订单
#    mall-order-list（看最新一条）→ mall-order-detail（核对商品与积分）

# 3) 已生成 → status=confirmed，按订单号汇报，不要再兑换
#    未生成 → status=rolled_back，重新 ledger-begin 后再试
```

---

## 边界

- 虚拟商品通常发**券码**，实物商品涉及**库存与物流**。两者预期不同，
  汇报时不要混为一谈。
- 商品详情里的「使用条件」是官方规则原文，**不要改写或推测适用范围**。
- 积分余额、冻结积分、即将过期积分一律以 `query-my-account` 实时返回为准，
  不用历史记录里的旧值做判断。
