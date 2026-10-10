# 模块 1 · 自动领券（麦麦省券）

## 涉及工具

> 实测入参（2026-10-10 从 `tools/list` 拉取）。

| 工具 | 类型 | 实测入参 | 说明 |
|---|---|---|---|
| `available-coupons` | 只读 | **无入参** | 当前可领取的券 |
| `query-my-coupons` | 只读 | `page`, `pageSize`（**字符串**） | 账户已有的券 |
| `auto-bind-coupons` | 写 | **无入参** | 一键领取所有可用券 |
| `query-my-account` | 只读 | **无入参** | 积分账户 |
| `now-time-info` | 只读 | **无入参** | 当前时间 |

> ✅ 实测确认 `auto-bind-coupons` **确实无入参**，与官方描述一致。

## 实测返回格式（2026-10-10）

**`query-my-coupons` → Markdown，不是 JSON：**

```
# 您的优惠券列表

共 13 张可用优惠券 | 第 1/1 页 | 每页 200 条

## 招行圆筒冰淇淋1份
- **优惠**: ¥0 (用券价格)
- **有效期**: 2026-09-19 00:00-2026-10-19 23:59 ...
- **标签**: 到店专用

## 巧克力味厚松饼猪柳蛋套餐
- **优惠**: ¥29.9 (用券价格)
- **有效期**: 2026-10-10 05:00-2026-10-10 10:29 周六 05:00-10:29
- **领取时间**: 5日前收到
- **标签**: 今日到期、到店专用
```

**`available-coupons` → 另一种 Markdown：**

```
### 麦麦省优惠券列表：
- 优惠券标题：巧克力味厚松饼猪柳蛋套餐 \
  状态：已领取 \
  优惠券图片：<img src="...">
```

> ⚠️ **实测关键发现：券类返回完全不含官方券 ID。**
> 因此跨天对比只能靠「券名 + 面额 + 有效期」组合指纹，
> `id_known` 恒为 `false`。这是官方数据现状，不是本项目的缺陷 ——
> 但也意味着**无法用券 ID 做后续操作**，需要券码时只能让用户从 APP 查看。

---

## 场景 A：一次性领券

### 流程（4 步）

```
1. query-my-coupons  → 存快照 label=pre      ← 判定基准
2. ledger-begin --kind auto-bind-coupons --op-key <YYYY-MM-DD>
3. auto-bind-coupons                          ← 唯一写操作
4. query-my-coupons  → 存快照 label=post     ← 核验
   ledger-finish --status confirmed
   diff-coupons --before pre --after post    ← 报变化
```

> **已废弃的三个步骤**（实测证明无收益，删除以省 token）：
>
> | 原步骤 | 删除原因 |
> |---|---|
> | ~~`run-status` 去重~~ | 交互式会话不会并发重跑；定时任务场景才需要 |
> | ~~`available-coupons` 先查~~ | `auto-bind-coupons` **无入参全量领取**，这次查询不影响任何决策 |
> | ~~`query-my-account` 前后各一次~~ | **领券不改积分**，diff-account 恒为空，2 次调用纯浪费 |
>
> 需要查积分时直接单次调用 `query-my-account` 即可，不必做前后快照。

### 判定「真的领到了」

**不能凭 `auto-bind-coupons` 的返回值下结论**，必须回查券包：

```bash
python scripts/mm_state.py snapshot --kind coupons --label pre --file before.json
python scripts/mm_state.py snapshot --kind coupons --label post --file after.json
python scripts/mm_state.py diff-coupons --before pre --after post
```

- `new_count > 0` → 确实新增
- `new_count == 0` → 可能已领过 / 当日无新券，**不重复提醒**

> 若 `auto-bind-coupons` 返回的券 ID 在 `query-my-coupons` 里查不到，
> 说明领取**未生效**。此时把账本置 `unknown`，先核验再决定是否重试。

---

## 场景 B：定时自动领券

### 配置

```bash
# 默认北京时间每天 09:00；修改：
python scripts/mm_state.py prefs-set --key coupon.daily_time --value '"08:30"'
python scripts/mm_state.py prefs-set --key coupon.timezone --value '"Asia/Shanghai"'

# 暂停 / 恢复
python scripts/mm_state.py prefs-set --key coupon.enabled --value false
python scripts/mm_state.py prefs-set --key coupon.enabled --value true
```

WorkBuddy 定时任务配置（示例）：

```
名称：麦麦每日领券
频率：每天 09:00（Asia/Shanghai）
类型：recurring
提示词：执行「麦麦生活助手」的模块 1 自动领券流程：
        1) 运行 python scripts/mm_state.py run-status，若今日已成功领券则直接结束
        2) 查询 query-my-account 与 available-coupons（只读）
        3) 若有可领券：存 pre 快照 → 调用 auto-bind-coupons → 查 query-my-coupons 存 post 快照
        4) 用 mm_state.py diff-coupons 判断是否有新券
        5) 仅当 new_count > 0 时，向用户汇报新增券名与面额；无变化则静默，只写运行记录
        严禁调用任何消耗积分或下单类工具。
```

调度前必须过守卫：

```bash
python scripts/mm_state.py cron-guard --kinds auto-bind-coupons query-my-account available-coupons
```

### 无变化不提醒

`coupon.notify_only_on_change = true`（默认）时，
`new_count == 0` **不推送消息**，只写 `runs/YYYY-MM-DD.jsonl`：

```bash
python scripts/mm_state.py run-status
```

---

## 场景 C：查券包 / 积分

| 用户说 | 工具 | 输出 |
|---|---|---|
| 「我有哪些券」 | `query-my-coupons` | 按面额/有效期排序，标出临近过期 |
| 「我的券包变了吗」 | pre/post 快照对比 | 只列新增与消失 |
| 「我还有多少积分」 | `query-my-account` | 可用/累计/冻结/即将过期 |
| 「积分会过期吗」 | `query-my-account` | 字段「即将过期积分」以官方返回为准 |

有效期解析（识别临近过期）：

```bash
python scripts/mm_calc.py expiry "2026-11-30"
```

`days_left <= 7` 时 `urgent=true`，应主动提示。

---

## 边界与注意事项

- **券的 `id` 字段名随版本可能变化**。`mm_state.py` 用候选键宽松匹配
  （`couponId` / `coupon_id` / `id` / `voucherId` / `code`）。
  ✅ **实测已确认真实返回不含任何券 ID**，所以线上跑 `id_known` 几乎总为 `false`。
  这时指纹退化为「名称+金额+有效期」组合，**仍可用于跨天对比**，
  但**不能**当作官方券 ID 去做后续操作。
- 限流：官方限制 **600 次/分钟**。批量查询时串行调用，别并发轰击。
- 领券失败/超时时**先查券包**再决定是否重试，不要连续调用 `auto-bind-coupons`。
- `query-my-coupons` 有分页参数（字符串类型），实测账号共 13 张券在第 1/1 页。
  券多时需翻页，否则对比基线不完整。
