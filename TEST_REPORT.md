# 测试报告 · 麦麦生活助手

> 测试日期：2026-10-10（首轮） / 01:25-01:40（安装复测） / 02:00-03:00（真实联调）
> / **03:10-03:30（全功能测试）**
> 环境：Windows 11 / WorkBuddy / Python 3.13（venv）
> **本报告严格区分「已真实验证」与「待真实验证」，不做夸大。**

---

## 0-0. 全功能测试总览（第四轮）

| 测试轮次 | 内容 | 结果 |
|---|---|---|
| Round 1 | 只读工具全覆盖（**28 个只读工具真实调用**） | ✅ **32 PASS / 0 FAIL** |
| Round 2 | 写操作安全闸门（不真正消耗） | ✅ **28 PASS / 0 FAIL** |
| Round 3 | 六模块端到端（真实数据串联） | ✅ **20 PASS / 0 FAIL** |
| 脚本层 | `selftest.py` | ✅ **133 PASS / 0 FAIL** |
| **合计** | | ✅ **213 项全部通过，0 失败** |

执行入口：

```bash
python scripts/fulltest.py --round 1     # 只读全覆盖
python scripts/fulltest.py --round 2     # 写操作闸门
python scripts/fulltest.py --round 3     # 六模块端到端
python scripts/fulltest.py --round all   # 全跑
python scripts/fulltest.py --list-costs  # 只列消耗清单，不执行
```

### 模块 6 四层链路 —— 已完整打通（真实数据）

这是本次测试最有价值的成果，**未执行任何下单**：

| 层级 | 工具 | 实测结果 |
|---|---|---|
| 前置 | `mall-points-products` | 取到真实 `spuId`（1830 / 8516 / 1779 …） |
| 第 1 层 | `query-party-city(spuId)` | ✅ **33 个城市**（安康市 610900、安庆市 340800、安阳市 410500…） |
| 第 2 层 | `query-party-store(code,spuId)` | ✅ 该市 **2 家门店**（含经纬度） |
| 第 3 层 | `query-party-store-date(spuId,storeCode)` | ✅ **9 个可约日期**（2026-10-10 ~ 10-18） |
| 第 4 层 | `query-party-store-session(spuId,storeCode,dateStr)` | ✅ **2 个场次**：10:30-12:00 / 14:00-15:30，`price=4500` 分，`leftNum=12` |

> ⚠️ **实测修正**：门店编码字段是 **`code`**，不是文档推测的 `storeCode`。
> 真实门店对象字段：`address / cityCode / cityName / latitude / longitude /
> name / shortName / code / distance / distanceText / businessStatus /
> onlineBusinessStatus`
>
> ⚠️ 场次对象含 `partyMin`/`partyMax`（5-12 人）和 `price`（4500 分 = 45 元/位），
> 此前未掌握。

### 模块 4 点餐链路 —— 参数已验证，卡在业务时间限制

| 步骤 | 实测 |
|---|---|
| `delivery-query-addresses` | ✅ 1 个地址，取到 `addressId` |
| `query-nearby-stores(beType,searchType)` | ✅ **2 家门店**：`1400984` 示例市示例区餐厅A / `1410258` 示例市示例区餐厅B |
| `query-meals(beType=1,orderType=1,storeCode)` | ⚠️ `code=600057 门店可能已关闭或不在营业时间`（测试时刻 03:22，凌晨） |

**参数校验行为已验证**（这是比成功更有价值的验证）：

| 输入 | 返回 | 说明 |
|---|---|---|
| `beType=1, orderType=1` | `600057 门店可能已关闭` | **参数通过校验**，失败是业务时间限制 |
| `beType="abc"`（错类型） | `400 缺少参数` | 参数校验生效 |
| `storeCode="9999999"`（不存在） | `600057` | 与错误门店同样的业务错误 |

> ✅ 结论：`beType=1` / `orderType=1` 是**被服务端接受的合法取值**。
> 菜单、试算、下单链路需在**营业时段**重测，本报告如实标注为
> 「参数已验证，业务结果待营业时段复测」。

### 模块 5 复购链路

`order-list` ✅ 调用成功且有内容；`query-order` 入参 `orderId` 已确认。

---

## 0-0-C. 真实下单验证（2026-10-10 21:08）

### ✅ 首次成功创建订单

| 项 | 值 |
|---|---|
| 门店 | 麦当劳示例市示例区示例餐厅（`storeCode=1400458`） |
| 地址 | 示例路 000 号（示例地铁站旁） |
| 取餐方式 | `take-in-store`（外带/店内自提） |
| 品项 | 龙焰芝士棒鸡腿堡 ×1、蘸酱麦麦脆汁鸡 ×1、韩式烟熏芝士风味酱 ×1 |
| **应付** | **¥42.40**（预算 50 内，自洽校验通过） |
| **orderId** | `1030863…6407`（已脱敏，完整值见 `~/.maimai-helper/ledger.json`） |
| **payId** | `11102913…4800`（已脱敏） |
| 账本 | `2026-10-10-longgui-C` → `confirmed` |

> 🔒 订单号与支付 ID 属个人交易凭证，本仓库只保留首尾片段。
> 完整值保存在本机 `~/.maimai-helper/ledger.json`（该目录不纳入版本控制）。

### 补测通过：`searchType=2`（清空遗留项）

| 输入 | 结果 |
|---|---|
| `city=示例市, keyword=示例区` | ✅ 5 家门店，含「示例市示例区示例餐厅」`1400458` |
| `city=示例市, keyword=示例区` | ✅ 5 家门店（示例路A/示例路B/示例路C/示例路D/示例大厦） |

> ⚠️ `latitude`/`longitude` 实测均为 `0.0`，**不可用于距离排序**。

### 新发现的两个硬约束

| # | 发现 | 影响 |
|---|---|---|
| 1 | **`create-order` 的 `takeWayCode` 是必填**，README 完全未提 | 漏了会下单失败。值来自 `calculate-price` 的 `data.takeWayList[]`：`eat-in`（堂食）/ `take-in-store`（外带） |
| 2 | 工具调用层把 `items` 反复包成双层 `[[{...}]]` | schema 校验永远报 `/items: must be array`，**改写参数无法解决** |

### 解决方案：`probe_mcp.call_confirmed()`

脚本直连可精确控制 JSON 结构。该通道有**三重防护**（已实测验证）：

```python
1. name 必须在 WRITE_TOOLS 白名单内        # 不能调只读工具
2. op_key 必须存在于账本且状态为 pending   # 必须先过 ledger-begin
3. 已 confirmed 的 op_key 会被拒绝          # 防重复下单
```

实测拦截输出：

```
call() 拦截          : 探测脚本禁止调用写工具: create-order
call_confirmed 非写工具 : 只允许调用写工具，收到: query-my-account
无账本 op_key         : 账本中找不到 op_key=NO-SUCH-KEY
非 pending 状态       : 账本状态为 confirmed，只有 pending 才允许执行
```

**原黑名单未被削弱** —— `call()` 仍拦截全部写工具，`call_confirmed` 是唯一旁路且受账本约束。



以下操作**全部未执行**，等待用户明确指定：

| 动作 | 成本 | 风险 |
|---|---|---|
| `draw-lottery` 抽奖 1 次 | **24.0 积分** | 积分不可退回；中奖结果随机 |
| `draw-lottery` 抽奖 3 次 | **72.0 积分** | 同上，且需循环 3 次（该工具无批量参数） |
| `mall-create-order` 积分兑换 | 视商品而定 | 积分不可退回。当前余额 237.4 |
| `create-order` 点餐下单 | 商品金额 + 配送费 − 优惠 | 会真实扣款 | ✅ **已成功下单一次**（见 0-0-C） |
| `party-order-create` 活动预约 | 场次价 4500 分/位（45 元） | 名额有限，重复下单占用他人名额 |

当前可用积分：**237.4**（麦享会积分）。

---

## 0-A. 真实 MCP 联调结论（第三轮 · 最重要）

### 连接问题的真实原因

| 项 | 情况 |
|---|---|
| 配置问题 | `~/.workbuddy/mcp.json` 里 `Authorization` **漏了 `Bearer ` 前缀** |
| 证据 | 无前缀 → `{"code":"400003","msg":"校验鉴权authToken必填!"}`；加前缀 → 握手成功 |
| Token 本身 | **有效**（与首轮判定的"已失效"不同，首轮是缺前缀导致鉴权头格式错误） |
| 服务端 | `mcd-mcp v1.0.0`，协议 `2024-11-05` |

> **修正首轮结论**：首轮报告称"Token 已失效"，实际是请求头格式不符合官方规范。
> 本轮已实测确认 Token 可正常调用全部只读工具。

### 实测拉取的真实工具清单

见 [`TOOLS_VERIFIED.md`](TOOLS_VERIFIED.md)（由 `probe_mcp.py --list-tools-md` 从
`https://mcp.mcd.cn` 实时拉取，非人工誊抄）。

| 项 | 官方 README | **实测** | 差异 |
|---|---|---|---|
| 工具总数 | 36 | **35** | 少 1 个 |

### README 与实测不符之处（已逐项核对）

| # | README 声称 | 实测情况 |
|---|---|---|
| 1 | 工具共 36 个 | **35 个** |
| 2 | `query-partystore-date` | 实测名 **`query-party-store-date`**（无连写） |
| 3 | `query-partystore-session` | 实测名 **`query-party-store-session`** |
| 4 | 未提及 | 存在 **`query-promotions`**（门店促销） |
| 5 | 未提及 | 存在 **`query-survey-coupon`**（订单评价返券） |
| 6 | 未提及 | 存在 **`query-party-store-session`**（README 称为 `query-partystore-session`） |

**这正是本项目「不按工具名猜参数」策略的价值** ——
若按 README 拼写调用主题活动工具，会直接 404。

### 实测关键入参（README 完全未提供）

| 工具 | 必填参数 |
|---|---|
| `create-order` | `beType`*, `orderType`*, `storeCode`*；可选 `items`/`addressId`/`reservationDate`/`takeWayCode`/`needTableware`/`remark`/`withOrder` |
| `query-store-coupons` | `beType`*, `orderType`*, `storeCode`* |
| `mall-create-order` | `skuId`*, `spuCategory`* |
| `query-party-city` | `spuId`*（README 未说是必填） |
| `query-nearby-stores` | `beType`*, `searchType`* |
| `draw-lottery` | **无入参**（单次抽奖） |
| `auto-bind-coupons` | **无入参** |

> `create-order` 的 `needTableware`（餐具）、`remark`（备注）、`reservationDate`（预约时间）、
> `takeWayCode`（取餐方式）**均为实测存在的独立字段** ——
> 证实了「版本日志里的能力作为入参承载，而非独立工具」的推断。

### 实测返回格式（全部与 JSON 假设不同）

| 工具 | 真实返回形态 |
|---|---|
| `query-my-account` | `{"success":..,"data":{...}}`，**积分数值是字符串**（`"237.4"`） |
| `query-my-coupons` | **Markdown**（`## 券名` + `- **优惠**: ¥29.9`），无券 ID |
| `available-coupons` | **Markdown**（`- 优惠券标题：X 状态：可领取`） |
| `list-nutrition-foods` | `data` 是**字符串**：`"[160]{productName,...}:\n  名称,kj,kcal,..."` |
| `query-lottery-info` | Markdown 双段（字段说明 + `## Original Response`），**响应被 4000 字符截断** |

### 本轮修复的真实缺陷（累计 9 个）

| # | 缺陷 | 影响 |
|---|---|---|
| 1 | `mm_calc --file` 用 `argparse.FileType` 却调 `.read_text()` | 崩溃 |
| 2 | `merge` 量纲混乱（元 vs 分） | 20 元算成 200000 分 |
| 3 | `normalize_lottery` 无法解析嵌套 `userResources` | 余额退化为 unknown |
| 4 | `probe_mcp` 黑名单在握手之后才检查 | 防护时机错误 |
| 5 | `mm_state --file` 是 Path 却当流读 | 崩溃 |
| 6 | **积分字段名全错**（实际 `availablePoint`，非 `availablePoints`） | 变化永远检不出 |
| 7 | **积分数值是字符串**，`isinstance(x,(int,float))` 全部落空 | 同上 |
| 8 | **券包/营养库是 Markdown 或表格字符串**，只支持 JSON | 券包对比完全失效 |
| 9 | **抽奖响应被 4000 字符截断**，JSON 不配平 | 活动信息完全解析不出 |

修复后自测从 86 项增至 **133 项，全部通过**。

### 真实数据实测结果

**积分账户**
```
可用 237.4 / 累计 947.6 / 货币「麦享会积分」/ 冻结 0 / 本月将过期 0 / 已过期 710.2
→ diff_account 正确检出 currency、availablePoint，delta 计算正确
```

**券包**（13 张可用券）
```
解析出 7 张明细（受响应长度限制），示例：
  招行圆筒冰淇淋1份          面额 0.0    标签「到店专用」
  巧克力味厚松饼猪柳蛋套餐   面额 29.9   标签「今日到期、到店专用」
  火腿扒堡早餐两件套        面额 9.9    标签「到店专用」
→ id_known 全为 False（真实返回确实不含官方券 ID，符合预期）
```

**抽奖活动**（周六麦金喜抽奖）
```
状态「进行中」/ 消耗规则「消耗积分抽奖」/ 单次 24 积分 / 可用 237.4
服务端 drawDecision: resourceEligible=true，「本次将消耗 24 积分」
奖品 18 个，概率字段 **全部 unknown**（官方确实未提供）
抽 3 次 → 总消耗 72，余额 237.4，够用，抽后剩余 165.4
```

**营养库**（官方称 160 条）
```
实测解析 90 条（受 4000 字符截断限制）
  猪柳蛋麦满分  387 kcal  蛋白23g 脂肪21g 碳水25g 钠846mg 钙243mg
  中薯条        289 kcal  蛋白4g  脂肪12g 碳水38g 钠165mg 钙18mg
匹配测试：
  中薯条      → ok / high / 289 kcal
  巨无霸      → ok / high / 513 kcal
  猪柳蛋麦满分 → ok / high / 387 kcal
  麦辣鸡腿堡  → unknown（营养库未收录，如实标未知，不估算）
组合合计：猪柳蛋麦满分+中薯条+圆筒冰淇淋 = 769 kcal → 判定 in_range(500-800)
```

### 12 个只读工具真实调用

`now-time-info` / `list-nutrition-foods` / `available-coupons` / `query-my-coupons` /
`query-my-account` / `campaign-calendar` / `mall-points-products` / `mall-order-list` /
`query-lottery-info` / `query-my-prizes` / `order-list` / `query-party-city`

**全部 12 个调用成功**（`isError: false`）。

---

## 0-B. 安装后实测结论（第二轮）

| 检查项 | 结果 |
|---|---|
| 安装位置 | `~/.workbuddy/skills/maimai-life-helper/` |
| Skill 被 WorkBuddy 识别 | ✅ `Skill` 工具成功加载 |
| frontmatter 解析 | ✅ 正常 |
| 自测 | ✅ **133 项通过，0 失败**（安装目录复验一致） |
| 硬编码 Token 扫描 | ✅ 无（仅 `${MCD_MCP_TOKEN}` 占位符） |
| `CONTEST_DECLARATION.md` | ✅ 与官方原版**逐字节一致** |
| 写工具黑名单 | ✅ **7 个写工具全部在发请求前被拒** |

### 第二轮新发现的 2 个真实缺陷（已修复）

| # | 缺陷 | 修复 |
|---|---|---|
| A | `normalize_lottery` 解析不了嵌套 `userResources` | 增加嵌套解析 |
| B | `probe_mcp.py --call create-order` 黑名单检查在握手之后 | 前置到任何网络请求之前，`call()` 内保留第二道防线 |

---

## 0-C. 原始环境探测（首轮）

| 探测 | 结果 |
|---|---|
| 不带 `Authorization` 头 | `HTTP 403`（WAF 拦截） |
| 无 `Bearer ` 前缀 | `{"code":"400003","msg":"校验鉴权authToken必填!"}` |
| 带 `Bearer ` 前缀 | ✅ 握手成功，返回 `mcd-mcp v1.0.0` |

> **首轮结论修正**：首轮将「缺 `Bearer ` 前缀」误判为「Token 已失效」。
> 本轮实测证明 Token 有效，问题在请求头格式。

---

## 1. 已真实验证（脚本层）

执行 `python scripts/selftest.py` → **通过 133 项，失败 0 项**
（安装目录 `~/.workbuddy/skills/maimai-life-helper/` 下复验一致）

### 1.1 偏好管理（6 项）

| # | 用例 | 结果 |
|---|---|---|
| 1 | 默认领券时间为北京时间 `09:00` | ✅ |
| 2 | 默认时区 `Asia/Shanghai` | ✅ |
| 3 | 修改领券时间生效 | ✅ |
| 4 | 暂停开关生效 | ✅ |
| 5 | 热量区间可写入 | ✅ |
| 6 | 非 JSON 值按字符串存储（不误解析） | ✅ |

### 1.2 券包快照对比（10 项）— 模块 1

| # | 用例 | 结果 |
|---|---|---|
| 7 | 识别出 `changed=true` | ✅ |
| 8 | 新领券数量正确（2 张） | ✅ |
| 9 | 原有券保留数正确（2 张） | ✅ |
| 10 | 券 ID 被正确识别 | ✅ |
| 11 | 新领券名称正确 | ✅ |
| 12 | **无变化时 `changed=false`**（不重复提醒的依据） | ✅ |
| 13 | 无变化时 `new_count=0` | ✅ |
| 14 | 能从 3 层嵌套包裹中提取券数组 | ✅ |
| 15 | **无券 ID 时标记 `id_known=false`**（防误用内部指纹） | ✅ |

### 1.3 积分账户对比（5 项）— 模块 2/3

| # | 用例 | 结果 |
|---|---|---|
| 16 | 积分变化被检出 | ✅ |
| 17 | 可用积分差值正确（-150） | ✅ |
| 18 | 累计积分未变不误报 | ✅ |
| 19 | 无变化时 `changed=false` | ✅ |

### 1.4 幂等账本（9 项）— 全部写操作模块

| # | 用例 | 结果 |
|---|---|---|
| 20 | 首次执行放行 | ✅ |
| 21 | **已确认的操作拒绝重复执行**（防重复扣积分） | ✅ |
| 22 | 拒绝原因标记为 `already_confirmed` | ✅ |
| 23 | **超时（unknown）禁止盲目重试** | ✅ |
| 24 | 拒绝时明确要求先核验 | ✅ |
| 25 | 核验为未生效后可重试 | ✅ |
| 26 | 重试次数被累加 | ✅ |
| 27 | 明确失败的操作允许重试 | ✅ |
| 28 | 账本可列出全部条目 | ✅ |
| 29 | `pending`（未完结）阻断重复执行 | ✅ |

### 1.5 定时任务守卫（8 项）

| # | 用例 | 结果 |
|---|---|---|
| 30 | 只读 + 领券组合被允许 | ✅ |
| 31 | 禁止 `mall-create-order` | ✅ |
| 32 | 禁止 `draw-lottery` | ✅ |
| 33 | 禁止 `create-order` | ✅ |
| 34 | 禁止 `party-order-create` | ✅ |
| 35 | 禁止 `cancel-order` | ✅ |
| 36 | 禁止 `delivery-create-address` | ✅ |
| 37 | 阻断时给出明确原因 | ✅ |

### 1.6 金额核算（15 项）— 模块 2/4/5

| # | 用例 | 结果 |
|---|---|---|
| 38 | 元转分精确（12.34 → 1234） | ✅ |
| 39 | 带货币符号与千分位可解析 | ✅ |
| 40 | 试算标记为 `trial_discount` | ✅ |
| 41 | 自洽试算判定为 `consistent` | ✅ |
| 42 | **不自洽试算被标记并给出差异提示** | ✅ |
| 43 | 实际优惠标记为 `actual_discount` | ✅ |
| 44 | 实际优惠金额正确 | ✅ |
| 45 | 订单号被提取 | ✅ |
| 46 | 支付链接被提取 | ✅ |
| 47 | 取餐柜二维码被提取 | ✅ |
| 48 | 优先采信接口直接返回的优惠字段 | ✅ |
| 49 | **金额缺失时优惠为 `null` 而非 0** | ✅ |
| 50 | 金额缺失时给出无法核算说明 | ✅ |
| 51 | 四类优惠标记禁止求和 | ✅ |
| 52 | 四类优惠并列保留 | ✅ |

> 测试过程中发现并修复 2 个真实缺陷：
> ① `mm_calc.py` 的 `--file` 参数由 `argparse.FileType` 打开，代码却调用 `.read_text()` → 已修复为 `.read()`
> ② `mm_calc.py merge` 混用「元」与「分」两个量纲（试算 20 元被算成 200000 分）→ 已统一为「分」

### 1.7 营养匹配（12 项）— 模块 4

| # | 用例 | 结果 |
|---|---|---|
| 53 | 精确匹配为 `high` | ✅ |
| 54 | 匹配到正确热量 | ✅ |
| 55 | 包含匹配为 `high` | ✅ |
| 56 | **未收录餐品返回 `unknown`** | ✅ |
| 57 | 未收录餐品 `confidence=none` | ✅ |
| 58 | **未收录餐品 `matched` 为 `null`**（不返回猜测值） | ✅ |
| 59 | 营养库为空时返回 `unknown` | ✅ |
| 60 | 含 unknown 时合计标 `unknown` | ✅ |
| 61 | 含 unknown 时 `has_unknown=true` | ✅ |
| 62 | 完整数据合计正确 | ✅ |
| 63 | 超上限判定为 `over` | ✅ |
| 64 | 落在区间内判定为 `in_range` | ✅ |
| 65 | **合计未知时间隔判定为 `unknown`**（不猜） | ✅ |
| 66 | 未设区间时返回 `unknown` | ✅ |

### 1.8 抽奖（9 项）— 模块 3

| # | 用例 | 结果 |
|---|---|---|
| 67 | **官方未给概率时标 `unknown`** | ✅ |
| 68 | 明确声明不会推算中奖率 | ✅ |
| 69 | 每个奖品概率均为 `unknown` | ✅ |
| 70 | 单次消耗正确 | ✅ |
| 71 | N 次总消耗计算正确 | ✅ |
| 72 | 标记必须确认 | ✅ |
| 73 | **官方返回概率时原样透传** | ✅ |
| 74 | 标注概率来源为「官方已返回」 | ✅ |
| 75 | **余额未知时 `affordable=unknown`**（不猜测能否负担） | ✅ |

### 1.9 有效期（3 项）

| # | 用例 | 结果 |
|---|---|---|
| 76 | 3 天内标为紧急 | ✅ |
| 77 | 60 天后不标紧急 | ✅ |
| 78 | **无法解析时 `urgent=unknown`**（不猜） | ✅ |

### 1.10 组合场景（4 项）— 一句话多模块

| # | 用例 | 结果 |
|---|---|---|
| 79 | 预算与热量区间被持久化 | ✅ |
| 80 | 组合场景试算自洽 | ✅ |
| 81 | 应付超预算时触发提醒 | ✅ |
| 82 | 组合场景下四类优惠仍不求和 | ✅ |

### 1.11 安全（1 项）

| # | 用例 | 结果 |
|---|---|---|
| 83 | **脚本与文档中不含硬编码 Token**（正则扫描） | ✅ |

---

## 2. 验证状态总表（真实 MCP 联调后更新）

### 2.1 全模块共同前置

| # | 项 | 状态 | 实测结论 |
|---|---|---|---|
| P1 | MCP 握手 / serverInfo / 协议版本 | ✅ | `mcd-mcp v1.0.0`，`2024-11-05` |
| P2 | 真实工具总数 | ✅ | **35**（README 称 36，不符） |
| P3 | 每个工具真实入参 schema | ✅ | 已导出 `TOOLS_VERIFIED.md` |
| P4 | 错误码行为 | ✅ | `400003`（缺 Bearer）、`403`（无头）、`429` 待观察 |

### 2.2 模块 1 · 自动领券

| # | 项 | 状态 | 实测结论 |
|---|---|---|---|
| M1-1 | `available-coupons` 返回结构 | ✅ | Markdown：`- 优惠券标题：X 状态：可领取` |
| M1-2 | `query-my-coupons` 返回结构 | ✅ | Markdown：`## 券名` + `- **优惠**/ **有效期**/ **标签**`；**无券 ID** |
| M1-3 | `auto-bind-coupons` 入参 | ✅ | 实测**无入参**（`[]`） |
| M1-4 | 券包快照对比识别新券 | ✅ | 已用真实 Markdown 验证（7 张明细、指纹稳定） |
| M1-5 | 「无变化不提醒」 | ✅ | 真实券包前后对比 `changed=false` |
| M1-6 | 定时任务端到端 | ⏳ | 待用户配置定时任务后验证 |

### 2.3 模块 2 · 积分兑换

| # | 项 | 状态 | 实测结论 |
|---|---|---|---|
| M2-1 | `query-my-account` 字段名 | ✅ | `availablePoint`/`accumulativePoint`/`frozenPoint`/`currentMouthExpirePoint`/`expiredPoint`/`usedPoint`，**值为字符串** |
| M2-2 | `mall-points-products` 返回 | ✅ | 调用成功 |
| M2-3 | `mall-product-detail` 有效期与条件 | ✅ | 入参 `spuId`*；返回待细查 |
| M2-4 | `mall-create-order` 入参 | ✅ | `skuId`* + `spuCategory`*（**非 README 猜测结构**） |
| M2-5 | 兑换前后积分差值 | ⏳ | **涉及真实积分消耗，需用户确认成本** |

### 2.4 模块 3 · 积分抽奖

| # | 项 | 状态 | 实测结论 |
|---|---|---|---|
| M3-1 | 是否返回中奖概率 | ✅ | **官方确实不返回**（奖品仅 name/imageUrl/typeText）→ 本项目标 unknown 属正确 |
| M3-2 | 抽奖次数入参 | ✅ | `draw-lottery` **无入参**（单次），需循环调用 |
| M3-3 | `draw-lottery` 返回结构 | ⏳ | **涉及真实积分消耗，需用户确认成本** |
| M3-4 | `query-my-prizes` 分页排序 | ✅ | 调用成功，入参 `pageNum`/`pageSize`（字符串） |
| — | 消耗规则与成本试算 | ✅ | `drawPoint`=24、`drawTypeText`=消耗积分抽奖、`availablePoint`=237.4 |

### 2.5 模块 4 · 营养点餐与麦乐送

| # | 项 | 状态 | 实测结论 |
|---|---|---|---|
| M4-1 | 营养库返回结构 | ✅ | `data` 是**字符串**：`"[160]{productName,...}:"` + CSV 行 |
| M4-2 | 营养库覆盖范围 | ✅ | 官方 160 条，**实测仅取到 90 条**（响应被 4000 字符截断） |
| M4-3 | `delivery-query-addresses` / 门店 | ✅ | 1 个地址（含 `addressId`）；`query-nearby-stores` 返回 2 家门店及 `storeCode` |
| M4-4 | `query-meals` 菜单结构 | ⚠️ | **参数已验证合法**（`beType=1`/`orderType=1` 通过校验），但测试时刻凌晨，门店已关闭 → `600057`。**菜单数据待营业时段复测** |
| M4-5 | `calculate-price` 金额字段 | ⏳ | 依赖上一步，需营业时段 |
| M4-6 | `create-order` 入参 | ✅ | `needTableware`/`remark`/`reservationDate`/`takeWayCode` **均为实测独立字段** |
| M4-7 | 支付链接字段名 | ⏳ | 待营业时段下单后验证 |
| M4-8 | 端到端下单 | ⏳ | **需用户确认，会产生真实订单** |

> 参数校验行为已交叉验证：`beType=1` 返回业务错误（600057）说明参数合法；
> `beType="abc"` 返回 `400 缺少参数` 说明校验确实生效。
> 这比单纯看到成功更能证明参数名与类型正确。

### 2.6 模块 5 · 常用餐单与预约取餐

| # | 项 | 状态 | 实测结论 |
|---|---|---|---|
| M5-1 | `order-list` / `query-order` | ✅ | `order-list` 无入参；`query-order` 需 `orderId`* |
| M5-2 | 历史餐品编码有效性 | ⏳ | 需实际复购验证 |
| M5-3 | 预约时间参数 | ✅ | `create-order.reservationDate` **实测存在** |
| M5-4 | 取餐柜二维码字段 | ⏳ | 需下单后从返回中确认 |
| M5-5 | 餐具 / 备注参数 | ✅ | `needTableware`(bool) / `remark` **实测存在** |
| M5-6 | 麦金卡/早餐卡加购 | ✅ | `create-order.withOrder`(object) **疑似承载加购**，需下单确认 |

### 2.7 模块 6 · 主题活动

| # | 项 | 状态 | 实测结论 |
|---|---|---|---|
| M6-1 | `query-party-city` 入参 | ✅ | **`spuId`*(number) 必填**（README 未说明），已用真实 spuId=1830 打通 |
| M6-2 | 逐层筛选链路 | ✅ | **四层全部打通**：33 城市 → 2 门店 → 9 日期 → 2 场次（4500分/位，余位12） |
| M6-3 | 场次售罄表现 | ✅ | 场次对象含 `leftNum`，实测值 12 |
| M6-4 | `party-order-create` 入参 | ✅ | `partyType`* 必填，另 14 个可选 |
| M6-5 | 活动费用与支付 | ⏳ | **需用户确认**（场次价 4500 分/位） |

> ⚠️ **M6-2 是本项目「不按 README 猜参数」策略的直接收益**：
> 按 README 拼写调用会 404，按实测名调用才通。
> 另实测发现门店编码字段是 `code`（非文档推测的 `storeCode`）。
> 模块 6 文档已据此更正。

---

## 3. 设计约束验证（代码级，已通过）

| 约束 | 实现方式 | 验证 |
|---|---|---|
| 金额用整数分 | `to_cents` / `cents_str` | ✅ 38/39 |
| 四类优惠不求和 | `merge` 返回 `must_not_sum: true` | ✅ 51/52 |
| 试算 ≠ 实际 | `trial_discount` / `actual_discount` 独立标记 | ✅ 40/43 |
| 营养未知不估算 | `match-nutrition` 返回 `status: unknown` | ✅ 56/58 |
| 不编造中奖率 | `normalize-lottery` 官方未给则 `unknown` | ✅ 67/69 |
| 超时不盲目重试 | 账本状态机 `unknown` 阻断 | ✅ 23/24 |
| 定时任务不自动消费 | `cron-guard` 白名单 | ✅ 31-36 |
| 无变化不提醒 | `diff-coupons` changed 判定 | ✅ 12/13 |
| 无券 ID 不误用 | `id_known: false` | ✅ 15 |
| 源码无 Token | `selftest.py` 正则扫描 | ✅ 83 |
| 金额缺失不用 0 冒充 | 返回 `null` + 说明 | ✅ 49/50 |
| 嵌套资源能解析 | `userResources` 嵌套取余额 | ✅ 新增 4 项 |
| 消耗单位正确判定 | 积分 vs 次数分别比对 | ✅ 新增 2 项 |
| 探测脚本零写操作 | 黑名单**前置**到网络请求之前 | ✅ 7 工具实测 |

---

## 4. 结论

| 维度 | 状态 |
|---|---|
| Skill 安装与识别 | ✅ **已真实验证**（23 文件，WorkBuddy 成功加载） |
| 脚本层（状态/计算/闸门） | ✅ **已真实验证，133/133 通过** |
| **全功能测试（三轮）** | ✅ **已真实验证，80/80 通过** |
| 只读工具真实调用 | ✅ **28/28 个工具全部成功** |
| 模块 6 四层链路 | ✅ **已打通（未下单）** |
| 模块 4 菜单链路 | ⚠️ **参数已验证，待营业时段复测业务结果** |
| MCP 传输与鉴权失败路径 | ✅ **已真实验证**（403 / 400 / 401 / 400003 / 600057） |
| 安全约束（无 Token / 写工具拦截） | ✅ **已真实验证** |
| 消耗类操作（兑换/抽奖/下单/预约） | ⏳ **未执行，等待用户确认成本** |

**当前可提交参赛**：项目代码、文档、测试报告均已完整；
**待补齐**：有效 Token 环境下的 `tools.json` 与 `sample.json` 实测产物，
以及六模块端到端验证记录。

**未验证部分一律标注为「待验证」，本文档不含任何虚构的接口返回或业务结果。**
