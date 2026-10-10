---
name: maimai-life-helper
description: >
  麦麦生活助手：基于麦当劳官方 MCP 的六个模块组合助手。覆盖自动领券、积分兑换、积分抽奖、
  营养点餐与麦乐送、常用餐单与预约取餐、主题活动预约下单。
  当用户提到「麦麦省券 / 领券 / 麦当劳 / MCD / 麦乐送 / 积分兑换 / 积分抽奖 /
  巨无霸 / 派乐 / 麦麦商城 / 主题活动 / 派对 / 品鉴会 / 预约取餐 / 来点一份 /
  减脂餐热量 / 我的券包 / 积分不够」，或要求组合多个需求（如「按预算和营养选餐，
  顺便看看券和积分能怎么用，送到我常去的地址」）时使用本 Skill。
license: MIT
metadata:
  author: "maimai-life-helper contributors"
  version: "1.0.0"
  mcp-server: "https://mcp.mcd.cn"
---

# 麦麦生活助手

> 本 Skill 不自建 MCP Server，全部能力来自麦当劳中国官方 MCP Server
> `https://mcp.mcd.cn`（Streamable HTTP）。本文件只负责**意图路由**与**执行纪律**。

---

## 0. 前置条件（每次会话先确认）

| 检查项 | 做法 | 不通过时 |
|---|---|---|
| MCP 已连接 | 已授权连接器里应能看到 `mcd-mcp` 的工具 | 停止，提示用户按 `SETUP.md` 配置，**不要伪造任何返回** |
| Token | 由连接器持有 | 同上；**绝不要求用户把 Token 贴在对话里** |

**未连接 MCP 时，唯一正确的行为是告知用户去配置，不要编造券、积分、菜单或价格。**

> ⚠️ **配置陷阱（实测）**：`Authorization` 请求头**必须带 `Bearer ` 前缀**。
> 漏掉前缀时接口返回 `校验鉴权authToken必填!` ——
> 这个报错看起来像 Token 缺失，实际是格式不对，容易误判为「Token 失效」。
> 正确写法：`"Authorization": "Bearer <TOKEN>"`。

---

## 1. 参数获取铁律（最高优先级）

> 官方 README **只提供工具名与文字描述，不提供参数 schema**。
> 版本日志里提到的能力（如「套餐替换」「餐具选择」「取餐柜二维码」）
> **没有独立工具**，已实测确认它们是 `create-order` 的入参字段
> （`needTableware` / `remark` / `reservationDate` / `takeWayCode`）。

**本项目已于 2026-10-10 完成真实联调**，实测拉取的工具清单见
[`TOOLS_VERIFIED.md`](TOOLS_VERIFIED.md)。实测发现三处与 README 不符：

| # | README | 实测 |
|---|---|---|
| 1 | 工具 36 个 | **35 个** |
| 2 | `query-partystore-date` | **`query-party-store-date`** |
| 3 | `query-partystore-session` | **`query-party-store-session`** |

**照 README 拼写调用主题活动工具会直接失败。** 因此：

1. **调用任何工具前，先读取该工具的真实入参 schema**（以你实际看到的为准）。
2. **禁止按工具名或 README 推测参数名、枚举值、嵌套结构或必填性。**
3. schema 与本文描述冲突时，**以 schema 为准**，并在回复里说明哪里不一致。
4. `beType` / `orderType` 等必填枚举值官方未提供，**首次使用先用只读工具试探**。

### 返回格式同样不能假设（实测）

**没有任何工具返回纯 JSON。** 实测形态：

| 类型 | 形态 |
|---|---|
| 积分类 | `{"data":{"availablePoint":"237.4"}}` — **数值是字符串** |
| 券包 | **Markdown**：`## 券名` + `- **优惠**: ¥29.9` |
| 营养库 | **表格字符串**：`"[160]{productName,...}:"` + CSV 行 |
| 抽奖 | **Markdown 双段**，且**被 4000 字符截断** |

必须走 `mm_state.py` / `mm_calc.py` / `menu.py` 的专用解析器，不要自己手搓 JSON 解析。

---

## 1.5 已验证参数速查表（省掉试错，直接照用）

以下组合是**实测验证过的**，不要重新猜、重新试。

### 场景 → 参数组合

| 场景 | `beType` | `orderType` | `beCode` |
|---|---|---|---|
| 到店自取 | `1` | `1` | **不传** |
| 得来速车道取餐 | `5` | `1` | **必传** |
| 麦乐送外送 | `2` | `2` | **必传** |
| 企业团餐 | `6` | `2` | **必传** |

> ⚠️ **`beType=1` 时传 `beCode` 会报错**（工具描述明载）。
> 实测曾因反复猜参数导致同一接口调 3 次失败，白烧 token。

### 无入参工具（不用传任何参数）

`now-time-info` / `query-my-account` / `available-coupons` / `auto-bind-coupons` /
`delivery-query-addresses` / `order-list` / `mall-order-list` / `mall-points-products` /
`query-lottery-info` / `query-my-prizes` / `list-nutrition-foods` / `draw-lottery` /
`campaign-calendar`

### 三条容易踩的硬限制

| 限制 | 后果 |
|---|---|
| `delivery-query-stores` 入参**只有** `addressId` + `beType` | 外送**无法**用经纬度定位，必须先有账号地址 |
| `query-meal-detail` **不含价格字段** | 拿它查价格是白调，价格只在 `calculate-price` |
| `query-store-coupons` 在外送场景实测返回空 | 不必每次调，券已在模块 1 领过 |
| **`create-order` 的 `takeWayCode` 必传** | 取自 `calculate-price` 返回的 `data.takeWayList[].code`，README 未提 |

### `takeWayCode` 取值（实测）

`calculate-price` 的 `takeWayList` 会返回可选值，原样取用：

| code | 含义 |
|---|---|
| `eat-in` | 堂食（店内用餐） |
| `take-in-store` | 外带（店内自提）——「到店自取」用这个 |

**到店/得来速（`orderType=1`）必传，外送（`orderType=2`）不传。**

### ⚠️ 下单工具调用的已知问题（2026-10-10 实测）

通过工具调用层调 `create-order` 时，`items` 数组会被**反复包成双层** `[[{...}]]`，
导致 schema 校验始终报 `/items: must be array`，**改写参数无法解决**。

**变通方式**（已实现并验证可用）：

```bash
# 1. 先过幂等闸门
python scripts/mm_state.py ledger-begin --kind create-order --op-key <key>

# 2. 用 probe_mcp 的 call_confirmed 通道执行（精确控制 JSON 结构）
#    三重防护：仅限写工具白名单 + 必须有 pending 账本记录 + 非 pending 拒绝

# 3. 拿到结果后收尾
python scripts/mm_state.py ledger-finish --kind create-order --op-key <key> --status confirmed
```

`probe_mcp.call()` 的黑名单**仍然生效**，`call_confirmed` 是唯一旁路且受账本约束。

---

## 1.7 支付环节：必须输出二维码，不要只给链接

### 为什么

支付链接 `payH5Url` 长约 95 字符，让用户复制粘贴到手机极不现实。
**扫码才是正常交互。**

### ⚠️ 二维码内容必须是 `jumpToApp`，**不是** `scanToPay`

**这是本项目最容易搞错的一处，2026-10-10 逆向前端 JS 才查清。**

麦当劳有两个支付相关页面：

| URL | 面向 | 实际行为 |
|---|---|---|
| `/mcp/scanToPay?orderId=X` | **桌面** | 页面里**再渲染一个二维码** |
| `/mcp/jumpToApp?orderId=X` | **手机** | 唤起麦当劳 APP |

依据是 `cdn.mcd.cn/m/build/static/js/main.*.js` 里路由
`/mcp/scanToPay` 对应的组件（压缩后名 `kn`）：

```js
const {search} = useLocation()
const t = new URLSearchParams(search)
const n = t.get("orderId") || ""
const r = t.get("orderCategory") || ""
let i = `https://${host}/mcp/jumpToApp?orderId=${n}`
if (r === "ECS") i += `&orderCategory=${r}`
return <QRCodeSVG value={i}/>          // ← 页面上又画了一个二维码
```

**踩过的坑**：把 `scanToPay` 做成二维码给用户用手机扫，
手机上打开的仍是那个「桌面页」，于是**又显示一个二维码** ——
用户看到的现象是「一个麦当劳的链接，不是支付」。

**正确做法**：

```bash
# 直接给订单号，脚本自动构造正确的二维码内容
python scripts/mm_qr.py --order-id <orderId> --format inline --out pay.svg

# 只想看两个 URL 分别是什么
python scripts/mm_qr.py --format pay-url --order-id <orderId>
```

`orderCategory` **只有企业团餐（ECS）才传**，麦乐送/到店不传。

### 为什么必须本地生成（安全红线）

> ⚠️ **绝对禁止**把支付链接送给第三方二维码 API
> （`api.qrserver.com` / `chart.googleapis.com` / `quickchart.io` 等）。
>
> 支付链接**携带订单凭证**，发给外部服务等同于泄露订单。
> 本项目已 vendored segno 的编码核心（`scripts/_vendor/segno/`，纯标准库），
> **全程离线生成，不产生任何外部请求。**

### 用法

```bash
# 推荐：给订单号，自动用 jumpToApp 作为二维码内容
python scripts/mm_qr.py --order-id <orderId> \
  --format inline --out pay.svg      # 内联渲染（Widget/HTML）
python scripts/mm_qr.py --order-id <orderId> \
  --format png --out pay.png --scale 11   # 存档/发送

# ASCII（纯文本场景兜底，浅色终端加 --invert）
python scripts/mm_qr.py --order-id <orderId> --format ascii --invert
```


**通用展示要求**：

1. 用**白底卡片**包住二维码（深色主题下二维码必须深色块 + 浅色底才能扫）
2. 静区保持 ≥ 4 格（`--border` 默认 4，**不要改小**）
3. 二维码旁附订单摘要（门店 / 品项 / 取餐方式 / 金额）
4. SVG 渲染必须带 `shape-rendering="crispEdges"`，否则缩放后模块边缘模糊

### ⚠️ 必须用内联 SVG，不要用 `<img>` 指向本地文件

**实测结论（2026-10-10）**：用 `<img src="...">` 指向本地 PNG，
**无论路径怎么写都渲染失败**（图片裂开，只剩 alt 文字）：

| 试过的写法 | 结果 |
|---|---|
| `/e/tencent_claw/pay_qr.png`（Git Bash 风格） | ❌ 裂开 |
| `E:/tencent_claw/pay_qr.png`（Windows 绝对路径） | ❌ 裂开 |

文件本身没问题（539×539 有效 PNG、可读、就在工作区内）——
是宿主对本地路径的改写没有生效。**不要再试路径写法了，纯浪费时间。**

**正确做法：内联 SVG，完全绕开文件加载。**

```bash
python scripts/mm_qr.py "<支付链接>" --format inline --out pay_qr.svg
```

输出的 SVG **自包含**（无 `src` / `href` / 任何外部资源），
整段标记直接嵌进 Widget HTML 即可，**渲染成功率 100%**。

体积已优化：用 stroke 横线 + 行内相对 `m` 链式移动，
41×41 矩阵从 6146 字符压到 **2809 字符**（降 54%），整体约 3KB。

### ⚠️ 内联 SVG 的路径必须验证（会静默画歪）

手写压缩的 path 一旦游标处理有误，**二维码看起来正常但扫不出来**——
比裂图更危险。`--format inline` 内置反向重建校验：

```bash
# 输出会带 verified 字段
# "verified": {"ok": true, "diff": 0, "finders": {"tl": true, "tr": true, "bl": true}}
```

它把 path 反向解析成位图，与原矩阵**逐模块比对**，并检查三个定位图案。

> 实测教训：写这个验证器时，**验证器自己**漏了「画完 `h` 要更新游标」，
> 误报 734 个模块不一致。修正后才确认真实差异为 0。
> 因此「验证器能识别破坏路径」也有独立测试 —— 光有验证器不够，
> 还得证明它真的会报错。


### ⚠️ `payH5Url` 只能在 `create-order` 时拿到

实测 `query-order` 的响应**被 4000 字符截断**，`payH5Url` 与 `lockerQrCode`
都在截断之后，**回头查是拿不到的**。

因此：**下单成功后必须立即用返回的 `orderId` + `payId` 生成二维码**，
不要指望之后再查。

### `lockerQrCode`（取餐柜二维码）

`query-order` 的字段说明里提到 `lockerQrCode`（取餐柜扫码），
提示用 `![alt](lockerQrCode)` 渲染 —— 说明它本身就是一个**图片 URL**，
直接展示即可，**不需要二次生成二维码**。

⚠️ 但实测该字段位于 4000 字符截断之后，**尚未取得真实值**，待营业高峰后复测。

---

## 1.6 调用次数红线（超出就是浪费）

| 操作 | 上限 | 违反后果 |
|---|---|---|
| `calculate-price` | **≤2 次** | 必须用 `menu.py price` 批量传 `items` 数组，**禁止逐个编码试算反推价格** |
| `query-meal-detail` | 仅用户主动问「能换什么」 | 用于查价格是纯浪费 |
| `query-store-coupons` | 0（外送场景） | 实测返回空 |
| 同一工具失败重试 | 1 次 | 连续失败先检查参数表，不是重试 |
| `list-nutrition-foods` | 走缓存 | 静态数据，24h 内不重复拉 |

**`menu.py` 的正确用法**：

```bash
# ★ 首选：一站式（1 次进程，1~2 次 MCP）
#   拉菜单 → 按意图筛候选 → 批量认品名与单价 → 输出紧凑清单
python scripts/menu.py plan --store <code> --order-type 2 --be-type 2 \
  --be-code <bc> --budget 4000 --want 炸鸡 --limit 5

# 用户选定后：只对这一个方案试算（1 次 MCP）
python scripts/menu.py price --store <code> --order-type 2 --be-type 2 \
  --be-code <bc> --codes <c1>:2,<c2>
```

底层子命令（一般不用单独调，`plan` 已封装）：

| 子命令 | MCP | 用途 |
|---|---|---|
| `fetch` | 1 | 拉菜单并缓存（24h TTL） |
| `plan` | 1~2 | **一站式**：fetch + 筛候选 + 批量认价 |
| `identify` | 1 | 批量认品名+单价（每批 ≤5），写入持久缓存 |
| `price` | 1 | 试算指定组合的准确金额 |
| `candidates` | 0 | 纯本地按预算筛（用缓存） |

### ⚠️ 性能实测：真正的瓶颈是「进程启动」不是 MCP

| 指标 | 实测 |
|---|---|
| 单次 Python 启动 | **约 1060 ms** |
| 脚本输出体积 | 紧凑 613 字符 / 缩进 850 字符（**缩进多 39%**） |
| `plan` 冷启动（菜单缓存空） | **2695 ms** |
| `plan` 热缓存（同一天第二次） | **1126 ms**（0 次 MCP） |

**结论**：

1. **把多步合成一次调用** —— 原先 `fetch`+`identify`+`candidates`+`price`
   四次进程启动约 4.2 秒是纯浪费。`plan` 合并为一次。
2. **输出默认紧凑**（`separators` 去掉空格），比 `indent=2` 省约 39% 字符。
   需要人读时加 `--pretty`。
3. **缓存优先**：菜单 24h、价格**永久**。同一天第二次点餐 `plan` 只需 1.1 秒、0 次 MCP。

### ⚠️ 中文意图必须做同义词展开

实测坑：用户说「想吃炸鸡」，但 `--want 炸鸡` 只匹配到「蘸蘸炸鸡」——
因为「**麦辣鸡腿汉堡**」「龙焰鸡腿堡」「双层脆鸡堡」里
**没有连续的「炸鸡」二字**，纯子串匹配把整片炸鸡类商品漏掉了。

`menu.py` 内置 `WANT_SYNONYMS`，`--want 炸鸡` 会自动展开成
`炸鸡 / 鸡腿堡 / 鸡排 / 鸡块 / 鸡翅 / 脆汁鸡 / 麦香鸡 / 麦乐鸡`，
按「任一命中」筛选。展开后同一次查询从 1 个候选变成 6 个。

### ⚠️ 认价候选要从**全量池**里挑，不能从已匹配池里挑

实现 `plan` 的认价循环时踩的坑：为了按名字筛选，
必须先认名字；但「未知项」如果从**已按 want 匹配的池**里找，
那个池几乎为空 → 认价循环永远不启动（实测 `mcp_calls: 0`，一个候选都没多）。

正确做法：候选从 `full_pool()`（仅排除忌口）里挑，
按 `_plausible()` 打分排序，认完再重筛。

### 一次点餐的标准调用次数（目标 ≤6）

| 步骤 | MCP | 进程 |
|---|---|---|
| 1. 取配送地址 / 建地址 | 0~1 | 1 |
| 2. 查可配送门店 | 1 | 1 |
| 3. `menu plan`（含 fetch + 认价） | 1~2 | **1** |
| 4. 用户选定 → `menu price` | 1 | 1 |
| 5. `create-order` | 1 | 1 |
| 6. 生成支付二维码 | **0** | 1（可合并） |
| **合计** | **4~6** | **约 5** |

**反面教材**（实测曾跑到约 20 次 MCP）：逐个试算认商品 + 每个组合都试算 +
重复拉菜单 + 参数猜错重试 + 每步单独起一个进程。





---

## 2. 六模块路由表

按需加载，**不要一次读完全部**：

| 用户意图关键词 | 加载文档 |
|---|---|
| 领券、券包、麦麦省券、定时领券、今天有什么券 | `modules/01-auto-coupon.md` |
| 积分兑换、麦麦商城、积分换、兑换订单 | `modules/02-points-mall.md` |
| 抽奖、奖品、中奖记录、积分抽奖 | `modules/03-points-lottery.md` |
| 点餐、麦乐送、外送、热量、卡路里、营养、预算、预算内吃什么 | `modules/04-nutrition-order.md` |
| 常用餐单、复购、再来一单、预约取餐、自取、得来速、餐具、备注、取餐柜 | `modules/05-usual-order.md` |
| 主题活动、派对、品鉴会、约会、聚会、门店活动 | `modules/06-party-event.md` |

**组合需求**：一句话涉及多个模块时（例如「按我的预算和营养范围选餐，看看优惠券和
积分兑换能否用上，送到我选择的地址」），按此顺序拆解并**分阶段确认**：

```
只读侦察（不花钱） → 生成候选（标注成本） → 用户确认 → 逐个执行写操作 → 汇总回执
```

只读阶段可以并行；写操作必须逐个确认，禁止打包一次性授权。

---

## 3. 金额与优惠纪律（模块 2/4/5 通用）

**四类优惠必须分开陈述，任何情况下不得相加：**

| 类型 | 含义 | 何时产生 |
|---|---|---|
| 原有券 | 账户里本来就有的券面额 | `query-my-coupons` |
| 新领券 | 本次自动领到、尚未使用 | `auto-bind-coupons` + 快照对比 |
| 试算优惠 | 未下单的预估让利 | `calculate-price` |
| 实际订单优惠 | 下单后的真实让利 | `create-order` / `query-order` |

用脚本核算，不要心算：

```bash
# 解析试算结果并校验自洽性
python scripts/mm_calc.py price --file calc.json

# 解析真实订单优惠
python scripts/mm_calc.py actual --file order.json

# 并列展示四类（脚本内已强制禁止求和）
python scripts/mm_calc.py merge --owned 1200 --new 800 --trial 2000 --actual 1800
```

**金额一律用整数分计算**，`mm_calc.py` 已封装 `to_cents` / `cents_str`，禁止浮点累加。

---

## 4. 写操作闸门（模块 2/3/4/5/6 通用）

以下动作**必须**先展示「具体内容 + 成本」并获得用户明确确认，才可执行：

`mall-create-order`（消耗积分）、`draw-lottery`（消耗积分/次数）、
`create-order`（产生支付）、`party-order-create`（产生支付）、
`cancel-order`（不可逆）、`delivery-create-address`

执行前必须过幂等闸门：

```bash
# 1) 登记（同一 op-key 若已存在会拒绝重复执行）
python scripts/mm_state.py ledger-begin --kind mall-create-order --op-key <幂等键>

# 2) 展示确认卡片，等待用户回复

# 3) 执行后立刻收尾
python scripts/mm_state.py ledger-finish --kind mall-create-order --op-key <幂等键> --status confirmed
```

**超时/中断处理铁律**：

- 调用超时 → **先标记 `unknown`，绝不盲目重试**。
  用 `ledger-finish --status unknown` 记录，
  再通过只读工具核验实际结果（查订单 / 查券包 / 查奖品），
  确认未生效后才用 `--status rolled_back` 并重新 `ledger-begin`。
- 领券超时同理：先比对券包快照，**没有新券才算未领**，不要重复调用。

---

## 5. 定时任务纪律

定时任务**只允许**只读查询与自动领券：

```
允许：available-coupons / query-my-coupons / query-my-account / now-time-info /
      campaign-calendar / list-nutrition-foods / mall-points-products /
      mall-order-list / query-lottery-info / order-list / auto-bind-coupons
禁止：mall-create-order / draw-lottery / create-order / cancel-order /
      party-order-create / delivery-create-address
```

配置定时任务前用守卫校验：

```bash
python scripts/mm_state.py cron-guard --kinds auto-bind-coupons query-my-account
```

默认领券时间：**北京时间每天 09:00**。用户可改可暂停：

```bash
python scripts/mm_state.py prefs-set --key coupon.daily_time --value '"08:30"'
python scripts/mm_state.py prefs-set --key coupon.enabled --value false
```

**无变化不打扰**：对比券包与积分无变化时，只写运行记录，不推送提醒。
判断方式见 `modules/01-auto-coupon.md`，脚本：

```bash
python scripts/mm_state.py diff-coupons --before pre --after post
python scripts/mm_state.py run-status
```

---

## 6. 诚实性红线

以下行为**严禁**，违反即视为未完成交付：

- ❌ 编造券、积分、菜单、价格、营养数据、中奖率、订单号。
- ❌ 把模拟数据说成真实 MCP 返回。演示/自测数据必须显式标注
  `【模拟数据，非真实 MCP 返回】`。
- ❌ 把试算优惠说成实际优惠，或把新领券面额说成已省下的钱。
- ❌ 营养数据匹配不上却给估算值 → 必须标 `未知`。
  （`mm_calc.py match-nutrition` 会返回 `status: unknown`，照实转述。）
- ❌ 官方未返回中奖概率时自行推算 → 必须标 `官方未提供`。
  （`mm_calc.py normalize-lottery` 已内置该约束。）
- ❌ 未连接 MCP 时用「一般来说麦当劳是…」代替真实查询。

---

## 7. 状态与工作目录

固定工作目录：`~/.maimai-helper/`（可用环境变量 `MAIMAI_HOME` 覆盖）

```
preferences.json          偏好：领券时间、预算、热量区间、口味、地址、确认开关
runs/YYYY-MM-DD.jsonl     每日运行记录（自动领券留痕，用于去重）
snapshots/                券包/积分前后快照（pre / post）
ledger.json               写操作幂等账本（防重复扣减）
```

Token **不进此目录**，由连接器自行管理。

查看当前状态：

```bash
python scripts/mm_state.py home
python scripts/mm_state.py prefs-get
python scripts/mm_state.py ledger-list --status unknown    # 待核验的写操作
python scripts/mm_state.py run-status
```

---

## 8. 用户常见一句话示例

| 用户说 | 动作 |
|---|---|
| 「今天有什么券」 | `available-coupons` + `query-my-coupons` → 对比券包 → 只报新增 |
| 「把能领的都领了」 | 快照 pre → `auto-bind-coupons` → 快照 post → 核验 → 报变化 |
| 「每天 9 点帮我领券」 | 写偏好 + 建定时任务（仅领券） |
| 「2000 分能换什么」 | `query-my-account` + `mall-points-products` → 列表 + 有效期 + 条件 → 等确认 |
| 「用积分抽一次」 | `query-lottery-info` → 展示消耗与奖品（概率照实）→ 确认 → `draw-lottery` |
| 「30 块以内 500 大卡的午餐送到公司」 | 营养库匹配 → 生成候选 → `delivery-query-stores` → `query-meals` → `calculate-price` → 确认 → `create-order` |
| 「再来一单上次那个」 | `order-list` → 复购前重新 `calculate-price` 核价 → 确认 → `create-order` |
| 「周六约朋友吃饭，有派对吗」 | `campaign-calendar` + `query-party-city/store/date/session` → 确认 → `party-order-create` |

更多示例见 `README.md`。

---

## 9. 参考文件

- `SETUP.md` — MCP 接入与安装
- `modules/*.md` — 六模块执行细则
- `scripts/README.md` — 脚本说明
- `MCP_INTEGRATION.md` — 能力映射与实测记录（参赛必交）
- `TEST_REPORT.md` — 六模块测试报告（真实验证 / 待验证分列）
- `DEMO.md` — 比赛演示流程
