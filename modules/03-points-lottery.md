# 模块 3 · 积分抽奖

## 涉及工具

> 实测入参（2026-10-10 从 `tools/list` 拉取），README 完全未提供。

| 工具 | 类型 | 实测入参 | 说明 |
|---|---|---|---|
| `query-lottery-info` | 只读 | **无入参** | 活动状态、奖品、消耗规则、可用资源 |
| `draw-lottery` | **写（消耗积分）** | **无入参** | **单次抽奖**，需多次则循环调用 |
| `query-my-prizes` | 只读 | `pageNum`, `pageSize`（**都是字符串**） | 中奖记录 |

> ⚠️ `draw-lottery` **没有次数参数** —— 官方描述说「执行一次积分抽奖」。
> 用户要抽 N 次就必须调用 N 次，每次都要过幂等闸门。
> 这一点必须提前告知用户，不能假装有批量接口。

## 实测返回字段（2026-10-10）

`query-lottery-info` 真实返回（部分）：

```
activityName          活动名称
activityStatusText    活动状态文案（未开始/进行中/已结束/活动不存在）
drawPoint             单次所需积分（字符串，如 "24"）
drawTypeText          消耗规则（消耗积分抽奖 / 消耗任务完成次数 / 先消耗次数再消耗积分抽奖）
availablePoint        当前可用积分（字符串）
availableTimes        剩余可抽次数（integer）
drawDecision          服务端算好的资源决策
  ├─ resourceEligible   资源是否够本次抽奖
  ├─ reason             不足原因
  └─ nextConsumption    本次实际消耗 {type: POINTS/CHANCES, text: "本次将消耗 24 积分"}
prizes[]              奖品列表，仅含 name / imageUrl / typeText
```

**关键事实：`prizes[]` 里没有概率字段。** 官方确实不提供中奖概率，
因此本项目 `probability` 恒为 `unknown` —— 这是**如实反映**，不是解析失败。

> 另注意：`drawDecision` 是**服务端自己的判断**，本项目会原样透传给用户，
> 不自行替代或推翻它的结论。

---

## 流程

```
1. query-lottery-info                  → 整理活动信息
2. mm_calc.py lottery                  → 结构化奖品 + 成本试算
3. 【展示：每次消耗 × 次数 = 总消耗 / 奖品列表 / 概率（照实）→ 等确认】
4. ledger-begin --kind draw-lottery --op-key <幂等键，含 times>
5. draw-lottery（按用户指定次数）
6. 汇报每一抽结果
7. ledger-finish --status confirmed
8. query-my-prizes                     → 核验中奖记录
```

---

## 成本试算（脚本强制不推算概率）

```bash
python scripts/mm_calc.py lottery --times 3 --file lottery_info.json
```

输出包含：

- `cost_per_draw` / `total_cost`：官方消耗规则换算
- `balance` / `affordable` / `remain_after`：能否负担
- `prizes[].probability`：**官方返回则透传，未返回一律 `unknown`**
- `note`：官方未给概率时明确写入「本 Skill 不会自行推算或估算中奖率」

### 中奖率红线

- 官方返回 `probability` / `rate` / `chance` 等字段 → **原样透传**，可换算百分比但要注明来源。
- 官方**未返回** → 只能写「**官方未提供中奖概率**」。
- ❌ 绝对禁止根据奖品档位、剩余库存、场次发布时间等自行反推概率。
- ❌ 禁止使用「大概」「一般」「据说」等措辞暗示概率。

---

## 确认卡片

```
即将消耗积分抽奖：

  活动：      <活动名 / 状态>
  单次消耗：  <N> 分（或 N 次机会）
  抽奖次数：  <用户明确指定的次数>
  总消耗：    <N×次数>
  当前可用：  <M>
  抽后剩余：  <M - N×次数>

  奖品与概率（以官方返回为准）：
  <逐条列出：奖品名 / 档位 / 官方概率或"官方未提供">

  ⚠️ 积分/次数一经消耗不退回。确认抽奖请回复「确认抽奖 <次数>次」。
```

> 次数**只按用户明确指定的数字**执行。不主动加码、不做「再抽一次更划算」类引导。
>
> ⚠️ 实测 `draw-lottery` **无入参**，所以「抽 N 次」= 循环调用 N 次。
> 每一次消耗都要在确认卡片里写明（N 次 × 单次消耗 = 总消耗），
> 并在超时后逐个核验，不能整批重跑。

---

## 超时处理

`draw-lottery` 超时 → 先核验，**不要补抽**：

```bash
python scripts/mm_state.py ledger-finish --kind draw-lottery --op-key <key> --status unknown
```

1. `query-my-prizes`（分页、倒序）→ 看是否新增对应奖品
2. `query-my-account` → 看积分是否已扣
3. 已扣分或已中奖 → `status=confirmed`，如实汇报，**不补抽**
4. 完全无变化 → `status=rolled_back`，询问用户是否重试

> 因为 `draw-lottery` 无批量参数，循环抽奖时**每一抽都要独立的 op-key**
> （如 `draw-20261010-01`、`draw-20261010-02`）。

---

## 边界

- `draw-lottery` **无入参**，单次调用。次数靠循环，**不能靠参数**。
- 奖品可能需要后续兑换（如券码需去商城下单），要在汇报里说清下一步。
- 「谢谢参与」是正常结果，照实说明，不安慰、不追加抽奖。
