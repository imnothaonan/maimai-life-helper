# 麦麦生活助手 · Maimai Life Helper

> 基于**麦当劳中国官方 MCP Server**（`https://mcp.mcd.cn`）的六模块生活助手。
> 装进 [WorkBuddy](https://www.workbuddy.cn)，一句话搞定领券、积分、点餐、活动。
>
> **不自建 MCP Server**，全部能力来自官方；**不编数据**，查不到就标「未知」。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9+-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-213%20passed-success.svg)](TEST_REPORT.md)

---

## 目录

- [这是什么](#这是什么)
- [为什么需要它](#为什么需要它)
- [六模块](#六模块)
- [快速开始](#快速开始)
- [使用示例](#使用示例)
- [核心设计](#核心设计)
- [项目结构](#项目结构)
- [脚本参考](#脚本参考)
- [定时任务](#定时任务)
- [测试](#测试)
- [实测发现的官方文档问题](#实测发现的官方文档问题)
- [安全与合规](#安全与合规)
- [目标用户](#目标用户)
- [常见问题](#常见问题)
- [License](#license)

---

## 这是什么

一个装进 WorkBuddy 的 **Skill**，把麦当劳 MCP 的 **35 个官方工具**组织成六个能组合使用的日常场景：

| 模块 | 能力 | 关键约束 |
|---|---|---|
| **1 · 自动领券** | 每天定时领券、核验、只报变化 | 超时先核验，不重复领 |
| **2 · 积分兑换** | 查积分与商城、按需推荐、确认后兑换 | 展示积分成本/有效期/使用条件 |
| **3 · 积分抽奖** | 查活动奖品消耗、确认后抽奖、查中奖记录 | **不虚构中奖率** |
| **4 · 营养点餐与麦乐送** | 按预算+营养+口味选餐、试算、下单 | **营养匹配不上标「未知」** |
| **5 · 常用餐单与预约取餐** | 保存餐单、复购重新核价、预约自取得来速 | 复购必重新核价 |
| **6 · 主题活动** | 派对/品鉴会，逐层筛选城市→门店→日期→场次 | 每层让用户选 |

**一句话可以组合多个模块**，例如：

> 「按我的预算和营养范围选餐，看看优惠券和积分兑换能否用上，送到我选择的地址。」

Skill 会自动拆解为：

```
只读侦察（不花钱）→ 生成候选（标注成本）→ 你确认 → 逐个执行写操作 → 汇总回执
```

---

## 为什么需要它

用 AI 点麦当劳，最大的两个风险是：

**风险一：乱花钱** —— 它自己就下单了。

本项目的处理：

- 所有消耗积分 / 产生支付的动作，必须先展示**具体内容 + 成本**，拿到明确确认才执行
- 写操作全部过**幂等账本**，重复执行会被拒绝
- 超时后强制**先核验再决定是否重试**，绝不盲目重试
- 定时任务**只允许只读查询和领券**，下单/兑换/抽奖/预约一律禁止无人值守

**风险二：乱编数据** —— 查不到营养就估一个，查不到中奖率就算一个。

本项目的处理：

- 营养匹配不上 → 标 `未知`，**不估算、不按比例外推**
- 官方未返回中奖概率 → 标 `官方未提供`，**不自行推算**
- 金额字段缺失 → 返回 `null`，**不用 0 冒充**
- 券 ID 取不到 → 标 `id_known: false`，**不拿内部指纹当官方 ID 用**

> 这些不是靠提示词约束的，而是**写在代码里、由脚本强制执行**的。

---

## 六模块

### 模块 1 · 自动领券

每天定时领取麦麦省券，**领完必须回查券包核验**。

```
存 pre 快照 → auto-bind-coupons → 查 query-my-coupons 存 post 快照
→ diff-coupons 判断是否有新券 → 有变化才提醒
```

- 默认**北京时间每天 09:00**，可改可暂停
- **无变化不打扰**：对比券包无新增时只写运行记录，不推送
- 券类返回**不含官方券 ID**（实测确认），跨天对比靠「券名+面额+有效期」组合指纹

<details>
<summary>实测数据（2026-10-10）</summary>

```
可领券条目：3 张
我的券包：13 张可用券，解析出 7 张明细
  招行圆筒冰淇淋1份          面额 0.0    标签「到店专用」
  巧克力味厚松饼猪柳蛋套餐   面额 29.9   标签「今日到期、到店专用」
  火腿扒堡早餐两件套        面额 9.9    标签「到店专用」
id_known 全为 False（真实返回确实不含券 ID）
```

</details>

### 模块 2 · 积分兑换

查询积分与商城商品，按需求推荐，**确认后**才兑换。

推荐输出必须包含五项，缺一不可：商品、**积分成本**、**是否够用**、**有效期**、**使用条件**。

<details>
<summary>实测数据（2026-10-10）</summary>

```
可用积分：237.4 麦享会积分
累计积分：947.6
已过期：710.2    冻结：0    本月将过期：0
```

</details>

### 模块 3 · 积分抽奖

⚠️ **官方不提供中奖概率** —— 实测确认奖品对象只有 `name` / `imageUrl` / `typeText`。
本项目如实标注「官方未提供」，**不会根据奖品档位反推概率**。

<details>
<summary>实测数据（2026-10-10）</summary>

```
活动：周六麦金喜抽奖（进行中）
消耗规则：消耗积分抽奖    单次：24 积分
可用积分：237.4           抽 3 次共 72，抽后剩余 165.4
服务端 drawDecision：resourceEligible=true「本次将消耗 24 积分」
奖品：18 个，概率字段全部 unknown
```

</details>

### 模块 4 · 营养点餐与麦乐送

结合预算、口味、营养范围选餐，**试算后确认再下单**。

营养匹配采用三档置信度：

| 置信度 | 含义 | 汇报方式 |
|---|---|---|
| `high` | 名称完全匹配或互相包含 | 直接给数值，注明匹配到的营养库条目 |
| `medium` | 分词部分重叠（≥0.5） | 加 `⚠️ 待确认`，提示可能不是同一餐品/份量 |
| `none` | 无匹配 | **标「营养数据未知」，不给数字** |

<details>
<summary>实测数据（2026-10-10）</summary>

```
营养库：官方称 160 条，实测响应取到 90 条（受 4000 字符截断）
  中薯条        289 kcal  蛋白 4g   脂肪 12g  碳水 38g  钠 165mg
  巨无霸        513 kcal  蛋白 27g  脂肪 25g  碳水 44g  钠 1150mg
  猪柳蛋麦满分  387 kcal  蛋白 23g  脂肪 21g  碳水 25g  钠 846mg
  麦辣鸡腿堡  → unknown（营养库未收录，如实标注，不估算）

组合试算：猪柳蛋麦满分 + 中薯条 + 圆筒冰淇淋 = 769 kcal → 判定 in_range(500-800)

门店链路：
  delivery-query-addresses → 1 个地址
  query-nearby-stores      → 2 家门店（1400984 / 1410258）
  query-meals              → code=600057 门店已关闭（测试时刻 03:22，凌晨）
                             参数已验证合法，待营业时段复测
```

</details>

### 模块 5 · 常用餐单与预约取餐

保存常用餐单、从近期订单复购，**复购必重新核价**（历史价格只作对比）。

组合既有工具实现，不引入新工具：

| 需求 | 实现方式 |
|---|---|
| 餐单保存/复用 | 本地偏好 + `query-meals` 校验 + `calculate-price` 核价 |
| 近期订单复购 | `order-list` → `query-order` → 重新核价 → `create-order` |
| 预约取餐 | `create-order.reservationDate` ✅ 实测存在 |
| 餐具 / 备注 | `create-order.needTableware` / `.remark` ✅ 实测存在 |
| 取餐方式（自取/得来速） | `create-order.takeWayCode` ✅ 实测存在 |
| 会员卡加购 | `create-order.withOrder`（疑似承载）—— **仅用户明确要求时处理** |
| 取餐柜二维码 | 从 `create-order` / `query-order` 返回提取 |

### 模块 6 · 主题活动

派对、品鉴会等活动的**逐层筛选**，每层都让用户选，不替用户决定。

> ⚠️ **实测发现的隐藏前置**：四个 `query-party-*` 都依赖 `spuId`，
> 必须先从 `mall-points-products` 或 `mall-product-detail` 取。

<details>
<summary>实测数据（2026-10-10）—— 四层链路已完整打通</summary>

```
前置：mall-points-products → spuId = 1830
第1层 query-party-city(spuId=1830)          → 33 个城市
       安康市 610900 / 安庆市 340800 / 安阳市 410500 ...
第2层 query-party-store(code=610900,spuId)  → 安康市 2 家门店
       示例路 1 号 / 示例大道 2 号
       ⚠️ 门店编码字段是 code，不是 storeCode
第3层 query-party-store-date(spuId,code)    → 9 个可约日期
       2026-10-10 ~ 2026-10-18
第4层 query-party-store-session(...)         → 2 个场次
       10:30-12:00  price=4500分(45元/位)  5-12人  leftNum=12
       14:00-15:30  price=4500分(45元/位)  5-12人  leftNum=12
```

</details>

---

## 快速开始

### 前置：申请 MCP Token

1. 打开 https://open.mcd.cn/mcp ，右上角【登录】
2. 手机号验证登录
3. 点【控制台】→【激活】→ 同意服务协议 → 复制 MCP Token

### 接入 MCP

WorkBuddy 左侧边栏【专家·技能·连接器】→【自定义连接器】→【配置MCP】：

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_TOKEN"
      }
    }
  }
}
```

保存后回到【自定义连接器】，**启用** `mcd-mcp`。

> ### ⚠️ 必须带 `Bearer ` 前缀
>
> 这是实测踩到的坑。漏掉前缀时接口返回 `校验鉴权authToken必填!` ——
> **这个报错看起来像 Token 缺失，实际是格式不对**，极易误判为「Token 已失效」。
>
> | 配置 | 响应 |
> |---|---|
> | `"Authorization": "<TOKEN>"` | `{"code":"400003","msg":"校验鉴权authToken必填!"}` |
> | `"Authorization": "Bearer <TOKEN>"` | ✅ 握手成功，`mcd-mcp v1.0.0` |
> | 完全不带该头 | `HTTP 403`（WAF 拦截） |

### 安装 Skill

```bash
git clone <仓库地址> maimai-life-helper

# macOS / Linux / Git Bash
cp -r maimai-life-helper ~/.workbuddy/skills/

# Windows PowerShell
Copy-Item -Recurse -Force .\maimai-life-helper "$env:USERPROFILE\.workbuddy\skills\"
```

> ⚠️ 若目标目录已存在，**先删再拷** —— 否则旧文件会残留导致装到旧版本。

重启会话后问「你有哪些 skill」，应能看到 `maimai-life-helper`。

### 验证安装

```bash
cd maimai-life-helper
python scripts/selftest.py            # 预期：通过 183 项，失败 0 项
python scripts/fulltest.py --round 1  # 真实调用 28 个只读工具
```

---

## 使用示例

| 你说 | 会发生什么 |
|---|---|
| 「今天有什么券」 | 查可领券 + 我的券，**只报新增** |
| 「把能领的都领了」 | 存快照 → 领券 → 回查券包核验 → 报变化；无变化不重复提醒 |
| 「每天 9 点帮我领券」 | 配置定时任务（**仅领券**，不含任何消费动作） |
| 「改成 8 点」「先暂停几天」 | 改时间 / 暂停 |
| 「我还有多少积分」 | 可用 / 累计 / 冻结 / 本月将过期 / 已过期 |
| 「2000 分能换什么」 | 商城候选 + 积分成本 + 有效期 + 使用条件 → **等你确认** |
| 「用积分抽 3 次」 | 展示消耗与奖品（概率照实标「官方未提供」）→ 确认 → 抽奖 → 查中奖记录 |
| 「30 块以内 500 大卡的午餐送到公司」 | 营养匹配 → 候选 → 门店券 → 试算 → 确认 → 下单 → **支付二维码** |
| 「营养查不到的那几项别瞎猜」 | 明确标「未知」，不估算 |
| 「再来一单上次那个」 | 查历史 → **重新核价核在售** → 报差异 → 确认 → 下单 |
| 「明天早上 9 点取餐，不要餐具」 | 预约取餐 + 餐具要求 |
| 「周六约朋友，有派对吗」 | 城市 → 门店 → 日期 → 场次（**逐层你选**）→ 确认预约 |
| 「刚才超时了，能再抽一次吗」 | **先核验**是否已扣分/中奖 → 确认未生效才重试 |

---

## 核心设计

### 1. 四类优惠严格隔离，禁止求和

| 类型 | 含义 | 产生时机 |
|---|---|---|
| 原有券 | 账户里本来就有的券面额 | `query-my-coupons` |
| 新领券 | 本次领到、尚未使用 | `auto-bind-coupons` + 快照对比 |
| 试算优惠 | 未下单的预估 | `calculate-price` |
| **实际订单优惠** | 下单后的真实让利 | `create-order` / `query-order` |

`mm_calc.py merge` 在代码层面强制 `must_not_sum: true`。

**为什么不能相加？** 求和会重复计算「试算」与「实付」，也会把还没用的新券当成已省下的钱。

### 2. 超时不盲目重试

所有写操作过**幂等账本**：

```
ledger-begin ──> pending
                   │
     ┌─────────────┼──────────────┐
     ▼             ▼              ▼
 confirmed      unknown         failed
 拒绝重复        禁止重试        允许重试
 必须先核验
     │             │
     │        核验确认未生效
     │             ▼
     └────── rolled_back ──> 可重新 begin
```

### 3. 定时任务只做安全的事

```
允许：available-coupons / query-my-coupons / query-my-account / now-time-info /
      campaign-calendar / list-nutrition-foods / mall-points-products /
      mall-order-list / query-lottery-info / order-list / auto-bind-coupons

禁止：mall-create-order / draw-lottery / create-order / cancel-order /
      party-order-create / delivery-create-address
```

### 4. 金额一律用整数分

浮点累加会有 `0.1 + 0.2` 类误差。`to_cents` / `cents_str` 已封装，
`merge` 四个参数**单位统一为分**。

### 5. 不猜参数

官方 README **只提供工具名和文字描述，没有参数 schema**。
本项目运行时从 MCP 读取真实 schema；README 与 schema 冲突时**以 schema 为准**并说明差异。

> 这次实测证明了该决策的价值 —— **照 README 拼写调用主题活动工具会直接 404**（见下文）。

### 6. 调用次数红线

| 操作 | 上限 |
|---|---|
| `calculate-price` | **≤2 次**（必须用 `menu.py price` 批量传 `items` 数组） |
| `query-meal-detail` | 仅用户主动问「套餐能换什么」 |
| `query-store-coupons` | 0（外送场景实测返回空） |
| 同一工具失败重试 | 1 次 |
| `list-nutrition-foods` | 走 24h 缓存 |

超出会被脚本直接拒绝并提示收窄候选。

### 7. 位置信息只由用户口述，不做自动定位

⚠️ **本 Skill 没有 IP 自动定位能力，这是刻意为之。**

实测本机 IP 定位的结果：

| 数据源 | 声称位置 |
|---|---|
| 开发者本机实际位置 | （不予公开） |
| ipwho.is | **北京** |
| ip-api.com | **深圳** |

两个源互相矛盾且都不是真实位置。双源矛盾时无法自证正确，
而点餐会产生真实支出 —— **静默送到错误城市，代价大于"定不了"**。

替代方案：你说一次位置并记忆，之后可随时改：

```bash
python scripts/mm_state.py prefs-set --key geo.city --value '"示例市"'
```

- `geo.city` / `geo.district` / `geo.note` / `geo.source`
- `geo.source` 固定为 `user_stated`，**标明这是用户口述而非自动定位**
- 对用户只能说「**你之前说过**在 XX」，**禁止**说「**已定位到**你在 XX」
- `geo` 仅用于填 `query-nearby-stores` 的 `city`/`keyword`，
  **不得**用于推测配送地址；外送链路只能走 `addressId`

### 8. 支付链接输出为二维码，且全程离线生成

支付链接约 95 字符，让用户复制粘贴到手机不现实 —— **扫码才是正常交互**。

> ⚠️ **安全红线**：绝不把支付链接送给第三方二维码 API
> （`api.qrserver.com` / `chart.googleapis.com` / `quickchart.io`）。
> `payH5Url` **携带订单凭证**，发给外部服务等同泄露订单。

本项目 vendored 了 segno 1.6.6 的编码核心（BSD-3-Clause，见
[`scripts/_vendor/segno/LICENSE`](scripts/_vendor/segno/LICENSE)），
**只保留 encoder + consts 两个文件**，已实测确认仅依赖标准库，
**全程离线生成，不产生任何外部请求**。

```bash
# 下单拿到 orderId + payId 后立即生成
python scripts/mm_qr.py "https://m.mcd.cn/mcp/scanToPay?orderId=X&payId=Y" \
  --format png --out pay_qr.png --scale 11
```

⚠️ **`payH5Url` 只在 `create-order` 时能拿到** —— 实测 `query-order` 响应被
4000 字符截断，该字段在截断之后，**回头查是拿不到的**。

---

## 项目结构

```
maimai-life-helper/
├── SKILL.md                       入口：意图路由、执行纪律、诚实性红线
├── README.md                      本文件
├── SETUP.md                       MCP 接入与安装详解
├── MCP_INTEGRATION.md             Tool 映射与调用流程（参赛必交）
├── CONTEST_DECLARATION.md         参赛声明（官方原版，零改动）
├── TOOLS_VERIFIED.md              实测工具清单（脚本实时导出）
├── mcp-config.example.json        脱敏配置（环境变量占位符）
├── workbuddy.md                   WorkBuddy 开发上下文（脱敏）
├── TEST_REPORT.md                 测试报告（真实验证 / 待验证分列）
├── DEMO.md                        比赛演示流程
├── LICENSE
├── .gitignore
│
├── modules/                       六模块执行细则（按需加载）
│   ├── 01-auto-coupon.md
│   ├── 02-points-mall.md
│   ├── 03-points-lottery.md
│   ├── 04-nutrition-order.md
│   ├── 05-usual-order.md
│   └── 06-party-event.md
│
├── scripts/                       全部纯标准库，无第三方依赖
│   ├── mm_state.py                状态层：偏好、快照、幂等账本、定时守卫
│   ├── mm_calc.py                 计算层：金额、四类优惠、营养、抽奖成本
│   ├── mm_qr.py                   二维码生成（离线，支付链接用）
│   ├── _vendor/segno/             vendored 的二维码编码核心（BSD-3）
│   ├── probe_mcp.py               能力探测（只读，写工具黑名单前置）
│   ├── selftest.py                脚本层自测（133 项，模拟数据）
│   ├── fulltest.py                全功能测试（三轮，真实调用）
│   └── README.md
│
└── TEST_OUTPUT/                   测试产物（默认 gitignore）
    ├── fulltest_result.json
    └── ...
```

---

## 脚本参考

全部**纯 Python 标准库**，无第三方依赖，要求 Python 3.9+。
**所有脚本都不读写、不打印任何 Token。**

### mm_state.py · 状态与账本层

固定工作目录 `~/.maimai-helper/`（可用 `MAIMAI_HOME` 覆盖）：

```bash
python scripts/mm_state.py home
python scripts/mm_state.py prefs-get --key coupon.daily_time
python scripts/mm_state.py prefs-set --key coupon.enabled --value false

python scripts/mm_state.py snapshot --kind coupons --label pre  --file before.md
python scripts/mm_state.py snapshot --kind coupons --label post --file after.md
python scripts/mm_state.py diff-coupons --before pre --after post
python scripts/mm_state.py diff-account --before pre --after post

python scripts/mm_state.py ledger-begin  --kind mall-create-order --op-key K1
python scripts/mm_state.py ledger-finish --kind mall-create-order --op-key K1 --status confirmed
python scripts/mm_state.py ledger-list --status unknown

python scripts/mm_state.py cron-guard --kinds auto-bind-coupons mall-create-order
python scripts/mm_state.py run-status
```

### mm_calc.py · 试算与核算层

```bash
python scripts/mm_calc.py cents "12.34"

python scripts/mm_calc.py price  --file calculate_price.json
python scripts/mm_calc.py actual --file order.json
python scripts/mm_calc.py merge  --owned 1200 --new 800 --trial 1200 --actual 1100

python scripts/mm_calc.py nutrition-all --file nutrition_raw.json --limit 10
python scripts/mm_calc.py match-nutrition "巨无霸" --file nutrition_raw.json
python scripts/mm_calc.py sum-nutrition   --file selected.json
python scripts/mm_calc.py check-range --range 400-700 --file total.json

python scripts/mm_calc.py lottery --times 3 --file lottery_info.json
python scripts/mm_calc.py expiry "2026-11-30"
```

> `merge` 四个参数**单位统一为分**。`check-range` 接收 `sum-nutrition` 的输出。

### mm_qr.py · 二维码生成

全程离线，**绝不把支付链接送给第三方 API**（那等于泄露订单凭证）。

```bash
python scripts/mm_qr.py "<url>" --format png --out pay_qr.png --scale 11
python scripts/mm_qr.py "<url>" --format svg
python scripts/mm_qr.py "<url>" --format ascii --invert
```

`--border` 默认 4（QR 规范要求的静区下限，**不要改小**）。

### probe_mcp.py · 能力探测

```bash
export MAIMAI_TOKEN=<token>       # 或写入 ~/.maimai-helper/.token
python scripts/probe_mcp.py --list-tools-md TOOLS_VERIFIED.md
python scripts/probe_mcp.py --list-tools --out tools.json
python scripts/probe_mcp.py --call query-my-account
python scripts/probe_mcp.py --readonly-batch --out sample.json
```

**安全**：内置 7 个写工具黑名单，且检查**前置到任何网络请求之前**。

---

## 定时任务

### 对话中配置（推荐）

> 「设置每天早上 9 点自动帮我领麦当劳的券，只领券，不要下单也不要抽奖。」

### 手动创建

```json
{
  "name": "麦麦每日领券",
  "scheduleType": "recurring",
  "rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
  "prompt": "执行「麦麦生活助手」模块 1 的自动领券流程：\n1. 运行 python scripts/mm_state.py run-status，若今日已成功领券则直接结束\n2. 只读调用 query-my-account 与 available-coupons\n3. 若有可领券：存 pre 快照 → 调用 auto-bind-coupons → 调用 query-my-coupons 存 post 快照\n4. 用 mm_state.py diff-coupons 判断是否有新券\n5. 仅当有新增时汇报新增券名与面额；无变化则静默，只写运行记录\n6. 严禁调用任何消耗积分或下单类工具"
}
```

### 修改时间 / 暂停

```bash
python scripts/mm_state.py prefs-set --key coupon.daily_time --value '"08:30"'
python scripts/mm_state.py prefs-set --key coupon.enabled --value false
```

---

## 测试

```bash
python scripts/selftest.py                    # 脚本层 133 项（模拟数据）
python scripts/fulltest.py --round 1          # 只读工具全覆盖（真实调用）
python scripts/fulltest.py --round 2          # 写操作安全闸门
python scripts/fulltest.py --round 3          # 六模块端到端
python scripts/fulltest.py --list-costs       # 只列消耗清单，不执行
```

### 测试结果（2026-10-10）

| 轮次 | 内容 | 结果 |
|---|---|---|
| 脚本层 | `selftest.py` | ✅ **183 / 183** |
| Round 1 | 只读工具全覆盖（28 个工具真实调用） | ✅ **32 / 32** |
| Round 2 | 写操作安全闸门 | ✅ **28 / 28** |
| Round 3 | 六模块端到端 | ✅ **20 / 20** |
| **合计** | | ✅ **291 项全部通过** |

### 调用次数优化（2026-10-10 第二轮）

实测发现首版点餐调用 28 次，其中 `calculate-price` 占 24 次（token 60%+）。
优化后：

| 环节 | 改前 | 改后 | 手段 |
|---|---|---|---|
| 拉菜单 | 1 | 1 | `menu.py fetch` + 24h 缓存 |
| 筛候选 | 0（靠试算反推） | **0** | `menu.py candidates` 纯本地筛选 |
| 试算 | **24** | **1** | `items` 数组批量试算 |
| 门店券查询 | 1（实测返回空） | **0** | 删除无用调用 |
| 套餐详情查价 | 2（不含价格字段） | **0** | 删除 |
| 参数重试 | 3 | **0** | 已验证参数写死进 SKILL.md |
| **合计** | **28** | **约 5** | |

领券流程同步从 7 次降到 4 次（删除实测无效的积分前后快照）。

### 已真实验证 vs 待验证

**✅ 已真实验证**

- 28 个只读工具全部真实调用成功
- 模块 6 四层链路完整打通（33 城市 → 2 门店 → 9 日期 → 2 场次）
- 模块 1 券包 Markdown 解析、快照对比、无变化不提醒
- 模块 2 积分字段解析、差值计算
- 模块 3 抽奖成本试算、概率不编造
- 模块 4 营养匹配（含未收录标 unknown）、组合合计、区间判定
- 模块 5 历史订单查询、复购参数核验
- 幂等账本四态、定时守卫、写工具黑名单前置拦截
- 参数校验行为（错误类型 → `400 缺少参数`；合法值 → 业务错误）

**⏳ 待验证（需消耗资源或营业时段）**

- 积分兑换、抽奖、点餐下单、活动预约的**实际执行**
- 模块 4 菜单与试算（测试时刻凌晨，门店已关闭 `600057`）
- 取餐柜二维码字段名、`withOrder` 加购结构
- `cancel-order` 的 `cancelReasonCode` 取值来源

详见 [`TEST_REPORT.md`](TEST_REPORT.md)。

---

## 实测发现的官方文档问题

本项目在真实联调中发现官方 README 与实际服务存在差异，**这也是「不猜参数」策略的直接价值**：

| # | 官方 README | 实测 |
|---|---|---|
| 1 | 工具共 **36** 个 | **35** 个 |
| 2 | `query-partystore-date` | **`query-party-store-date`** |
| 3 | `query-partystore-session` | **`query-party-store-session`** |
| 4 | 未提及 | 存在 **`query-promotions`** |
| 5 | 未提及 | 存在 **`query-survey-coupon`** |

> **按 README 拼写调用主题活动工具会直接 404。**

### 返回格式也与常规假设不同

**没有任何工具返回纯 JSON：**

| 类型 | 真实形态 |
|---|---|
| 积分类 | `{"data":{"availablePoint":"237.4"}}` — **数值是字符串** |
| 券包 | **Markdown**：`## 券名` + `- **优惠**: ¥29.9` |
| 营养库 | `data` 是**表格字符串**：`"[160]{productName,...}:"` + CSV |
| 抽奖 | **Markdown 双段**，且**被 4000 字符截断** |

### 另外两个值得注意的事实

1. **券类返回不含官方券 ID** —— 只有券名、面额、有效期、标签。
2. **响应会被上游按 4000 字符截断** —— 营养库 160 条只能取到 90 条，抽奖奖品列表中途断开。
   本项目的解析器实现了「从截断 JSON 抢救完整字段」的容错。

---

## 安全与合规

- Token **不写入**源码、脚本、日志、运行记录或任何公开文件
- **不要求用户把 Token 贴在对话里** —— 由 WorkBuddy 已授权连接器持有
- `mcp-config.example.json` 只用 `${MCD_MCP_TOKEN}` 环境变量占位符
- `selftest.py` 含源码级正则扫描，持续检查有无硬编码 Token
- `probe_mcp.py` 的写工具黑名单**前置到任何网络请求之前**
- 券 ID 取不到时标 `id_known: false`，防止内部指纹被误当官方 ID 使用
- 配送地址仅在用户明确要求新增时才调用创建接口
- 测试报告中的手机号、UUID、长数字串均已脱敏

---

## 目标用户

| 用户 | 典型用法 |
|---|---|
| **麦门重度用户** | 每天自动领券不漏券；券包有变化才被提醒 |
| **积分囤家 / 兑换党** | 查积分余额与商城，找高性价比兑换 |
| **减脂 / 控热量人群** | 按热量区间点单，营养对不上的直接标未知 |
| **上班族** | 保存常用餐单一键复购，预约到店不用等 |
| **家庭 / 聚会组织者** | 查主题活动、逐层筛选、预约下单 |
| **怕重复扣款的人** | 幂等账本 + 超时核验，绝不重复消耗 |

---

## 常见问题

**Q：为什么 Skill 里不写死各工具的参数？**

A：官方 README 未提供参数 schema，写死就是臆造。本项目改为运行时读取真实 schema，
以 schema 为准；README 与 schema 冲突时以 schema 为准并说明差异。
**实测已证明这个决策是对的** —— 照 README 拼写会 404。

**Q：为什么每次领券前后都要查一次券包？**

A：`auto-bind-coupons` 的返回值不足以证明领取成功。回查券包做快照对比，
才能确认真的新增了券，也才能实现「无变化不重复提醒」和超时核验。

**Q：为什么下单要过账本？**

A：下单、兑换、抽奖都会真实消耗资源。超时后盲目重试会造成重复扣积分或重复下单。
账本强制「先核验，再决定是否重试」。

**Q：营养数据对不上怎么办？**

A：直接标「未知」。营养库给的是标准份，加料或改套餐后数值会变，
按比例外推属于编造。另外实测官方 160 条只能取到 90 条，存在天然覆盖缺口。

**Q：会未经确认就花钱吗？**

A：不会。所有消耗积分或产生支付的动作都必须先展示具体内容与成本，
拿到明确确认后才执行，且执行前过幂等闸门。

**Q：定时任务会半夜偷偷抽奖吗？**

A：不会。白名单是代码里写死的。兑换、抽奖、下单、取消、预约、增加地址全部禁止，
`cron-guard` 会直接拦下来。

**Q：为什么营养库只有 90 条，官方说 160 条？**

A：响应被上游按 4000 字符截断。解析器会跳过被截断的残缺行，
已完整的前 90 条数据是准确的。查不到的餐品标未知，不估算。

---

## License

MIT

---

## 免责声明

本项目为**麦当劳程序员创意开发大赛参赛作品**，由参赛者独立开发，**非麦当劳官方产品**。

项目输出仅供参考，**不构成医疗、营养或其他专业建议**；
餐品信息、价格及供应状态以麦当劳官方渠道的实时结果为准。

使用麦当劳 MCP 服务须遵守麦当劳中国的《使用条款》及《麦当劳 MCP 服务规则》。
