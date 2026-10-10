# MCP 接入说明

本项目**不自建 MCP Server**，全部业务能力来自麦当劳中国官方 MCP Server。

> **本文档已按 2026-10-10 真实联调结果更新。**
> 实测拉取的工具清单见 [`TOOLS_VERIFIED.md`](TOOLS_VERIFIED.md)（由脚本从服务端实时导出）。
> 凡与官方 README 不一致处，均以**实测**为准并明确标注。

---

## 一、使用的官方 MCP Server

| 项 | 值 |
|---|---|
| 名称 | 麦当劳 MCP Server（`mcd-mcp`） |
| 接入地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP |
| 鉴权方式 | 请求头 `Authorization: Bearer <MCP_TOKEN>` |
| **实测 serverInfo** | **`mcd-mcp v1.0.0`** |
| **实测协议版本** | **`2024-11-05`** |
| 服务提供方 | 麦当劳中国 |
| 开放平台 | https://open.mcd.cn/mcp |
| 官方仓库 | https://github.com/M-China/mcd-mcp-server |
| 官方错误码 | `401` Token 无效/过期；`429` 限流（>600 次/分钟） |
| 服务范围 | 中国大陆地区（不含港澳台） |

### ⚠️ 配置陷阱：必须带 `Bearer ` 前缀

实测结果：

| 配置 | 响应 |
|---|---|
| `"Authorization": "<TOKEN>"` | `{"code":"400003","msg":"校验鉴权authToken必填!"}` |
| `"Authorization": "Bearer <TOKEN>"` | ✅ 握手成功，返回 `mcd-mcp v1.0.0` |
| 完全不带 Authorization 头 | `HTTP 403`（WAF 拦截） |

官方 README 虽写了 `Bearer`，但很容易漏掉这个前缀。
漏掉时的报错信息（"authToken必填"）具有误导性 —— 它看起来像 Token 缺失，
实际是**格式**不对。

### 配置文件

脱敏示例见仓库根目录 `mcp-config.example.json`：

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": {
        "Authorization": "Bearer ${MCD_MCP_TOKEN}"
      }
    }
  }
}
```

**只有环境变量占位符，不含任何真实 Token。**

### Token 管理原则（本项目严格遵守）

- Token **不写入**源码、脚本、日志、运行记录、公开文件
- Token **不要求用户贴在对话里**；由 WorkBuddy 已授权连接器持有
- 本地调试时如需保存，仅可放在 `~/.maimai-helper/.token`（用户主目录，不在项目内，
  且已在 `.gitignore` 中排除），或用环境变量 `MAIMAI_TOKEN`
- 一旦发现 Token 出现在对话记录/日志中，**应视为已泄露，需在官方控制台重新申请**

---

## 二、Tool 清单与能力映射

### ⚠️ README 与实测的三处差异

| # | README | **实测** | 影响 |
|---|---|---|---|
| 1 | 工具共 **36** 个 | **35** 个 | 数目不符 |
| 2 | `query-partystore-date` | **`query-party-store-date`** | 按 README 调用会 404 |
| 3 | `query-partystore-session` | **`query-party-store-session`** | 同上 |
| 4 | 未提及 | 存在 **`query-promotions`** | README 漏了 |
| 5 | 未提及 | 存在 **`query-survey-coupon`** | README 漏了 |

**这直接验证了本项目「不按工具名猜参数」的设计决策 —— 若照 README 拼写调用主题活动工具，必然失败。**

### 实测入参速查（`*` 为必填）

#### 模块 1 · 自动领券

| Tool | 实测入参 |
|---|---|
| `available-coupons` | 无入参 |
| `query-my-coupons` | `page`(string), `pageSize`(string) |
| `auto-bind-coupons` | **无入参** ✅ 与官方描述一致 |
| `query-my-account` | 无入参 |
| `now-time-info` | 无入参 |

#### 模块 2 · 积分兑换

| Tool | 实测入参 |
|---|---|
| `mall-points-products` | `catRuleIds`(string) |
| `mall-product-detail` | `spuId`*(integer) |
| `mall-create-order` | `skuId`*(integer), `spuCategory`*(string), `count`, `addressId` |
| `mall-order-list` | `lastId`(number), `size`(integer) |
| `mall-order-detail` | `orderId`(string) |

#### 模块 3 · 积分抽奖

| Tool | 实测入参 |
|---|---|
| `query-lottery-info` | 无入参 |
| `draw-lottery` | **无入参** —— 单次抽奖，N 次需循环调用 |
| `query-my-prizes` | `pageNum`(string), `pageSize`(string) |

#### 模块 4 · 营养点餐与麦乐送

| Tool | 实测入参 |
|---|---|
| `list-nutrition-foods` | 无入参 |
| `delivery-query-addresses` | 无入参 |
| `delivery-create-address` | `address`*, `addressDetail`*, `city`*, `contactName`*, `phone`*, `gender` |
| `delivery-query-stores` | `addressId`*, `beType`* |
| `query-nearby-stores` | `beType`*, `searchType`*, `city`, `keyword` |
| `query-store-coupons` | `beType`*, `orderType`*, `storeCode`* |
| `query-meals` | `beType`*, `orderType`*, `storeCode`* |
| `query-meal-detail` | `beType`*, `code`*, `orderType`*, `storeCode`* |
| `calculate-price` | `beType`*, `orderType`*, `storeCode`* + `items`/`gmServiceCode`/`needTableware`/`reservationDate`/`withOrder` |
| `create-order` | `beType`*, `orderType`*, `storeCode`* + `items`/`addressId`/`gmServiceCode`/`needTableware`/`remark`/`reservationDate`/`takeWayCode`/`withOrder` |
| `query-order` | `orderId`* |
| `cancel-order` | `orderId`*, `cancelReasonCode`* |
| `order-list` | 无入参 |
| `query-meal-assistance` | `storeCode`* |
| `query-promotions` | `beType`*, `orderType`*, `storeCode`* |
| `query-survey-coupon` | `orderId`* |

#### ✅ `searchType=2` 已补测通过（2026-10-10 20:58）

| 项 | 实测结果 |
|---|---|
| `searchType=2` + `city` + `keyword` | ✅ **可用**，按位置搜店正常返回 |
| 示例 | `city=示例市, keyword=示例区` → **5 家门店**（含「示例市示例区示例餐厅」`1400458`） |
| 返回字段 | `storeName` / `storeCode` / `address`（**`latitude`/`longitude` 实测为 `0.0`**，不可用于距离计算） |
| `beType=1` 的 `beCode` | 实测**不存在**于返回中，符合工具描述（`beType=1` 本就不返回） |

> ✅ 这条链路现已可用于 `geo` 偏好：
> `query-nearby-stores(searchType=2, city=<geo.city>, keyword=<geo.district>)`

#### ⚠️ `takeWayCode` 是 `create-order` 必填项（README 未提）

从 `calculate-price` 返回的 `data.takeWayList[]` 取值：

| code | 含义 |
|---|---|
| `eat-in` | 堂食（店内用餐） |
| `take-in-store` | 外带（店内自提）—— **「到店自取」用这个** |

`orderType=1`（到店/得来速）**必传**，`orderType=2`（外送/团餐）不传。

#### ⚠️ 下单工具调用的已知问题（实测）

通过客户端工具调用层调 `create-order` 时，`items` 数组会被**反复包成双层** `[[{...}]]`，
导致 schema 校验始终报 `/items: must be array`，改写参数无法解决。

**变通**：用 `probe_mcp.call_confirmed()` 走脚本直连，JSON 结构精确可控。
该通道有**三重防护**：仅限写工具白名单 + 必须存在 `pending` 账本记录 + 非 `pending` 拒绝。
实测成功创建订单（见 `TEST_REPORT.md`）。

#### 模块 6 · 主题活动

| Tool | 实测入参 |
|---|---|
| `campaign-calendar` | `specifiedDate`(string) |
| `query-party-city` | **`spuId`*(number)** —— README 未说明必填 |
| `query-party-store` | `code`*, `latitude`, `longitude`, `spuId` |
| `query-party-store-date` | `spuId`*, `storeCode`* |
| `query-party-store-session` | `dateStr`*, `spuId`*, `storeCode`* |
| `party-order-create` | **`partyType`*** + 14 个可选（`code`/`count`/`dateStr`/`id`/`leftNum`/`partyTimeInfo`/`skuId`/`spuId`/`storeCode`/`timeEnd`/`timeStart`） |

> **`spuId` 是主题活动的隐藏前置**：四个 `query-party-*` 都依赖它，
> 必须先从 `mall-points-products` 或 `mall-product-detail` 取得。

### 模块 5 · 常用餐单与预约取餐

本模块**不引入新工具**，而是组合既有工具：

- 餐单保存/复用 → 本地偏好（`mm_state.py prefs-set`）+ `query-meals` 校验 + `calculate-price` 核价
- 从近期订单复购 → `order-list` → `query-order` → 重新核价 → `create-order`
- 预约取餐 → `query-nearby-stores` + `create-order.reservationDate`
- 餐具 / 备注 → `create-order.needTableware` / `create-order.remark`
- 取餐方式（到店自取/得来速）→ `create-order.takeWayCode`
- 会员卡加购 → `create-order.withOrder`（**疑似承载，需下单确认**）
- 取餐柜二维码 → 从 `create-order` / `query-order` 返回提取（`mm_calc.py actual`）

> ✅ **实测证实**：餐具、备注、预约时间、取餐方式**确实都是 `create-order` 的入参字段**，
> 而非独立工具。这与官方版本日志的推断一致。

---

## 三、版本日志能力 → 实测承载方式（已验证）

官方版本日志提到以下能力，**工具表里没有对应的独立工具**。
2026-10-10 实测已确认它们作为 `create-order` 的**入参字段**存在：

| 版本 | 日志描述 | **实测确认** |
|---|---|---|
| 1.0.4 | 得来速车道取餐 | ✅ `create-order.takeWayCode` |
| 1.0.4 | 全部点餐场景预约 | ✅ `create-order.reservationDate` |
| 1.0.5 | 套餐替换 | `query-meal-detail.code` + `create-order.items`（待下单确认结构） |
| 1.0.6 | 随单购买麦金卡/早餐卡 | ⚠️ `create-order.withOrder`(object) 疑似承载，**待下单确认** |
| 1.0.9 | 餐具选择 | ✅ `create-order.needTableware`(boolean) |
| 1.0.9 | 麦乐送订单备注 | ✅ `create-order.remark`(string) |
| 1.0.9 | 取餐柜二维码 | 待下单后从 `query-order` 返回确认字段名 |

> ✅ 这批推断已由真实 schema 证实，不再是「工程推断」。
> 唯一仍未确认的是**取餐柜二维码字段名**与**会员卡加购的具体结构**。

---

## 四、返回格式实测（全部与 JSON 假设不同）

这是本项目开发中最关键的发现 —— **没有任何一个工具返回纯 JSON 结构**：

| 工具 | 真实返回形态 |
|---|---|
| `query-my-account` | `{"success":..,"data":{..}}`，**积分数值是字符串**（`"237.4"`） |
| `query-my-coupons` | **Markdown**：`## 券名` + `- **优惠**: ¥29.9 (用券价格)` |
| `available-coupons` | **Markdown**：`- 优惠券标题：X 状态：可领取` |
| `list-nutrition-foods` | `data` 是**字符串**：`"[160]{productName,...}:\n  名称,kj,kcal,..."` |
| `query-lottery-info` | **Markdown 双段**：字段说明 + `## Original Response`，**且被 4000 字符截断** |
| 多数其他工具 | `{"success":true,"code":200,"datetime":..,"traceId":..,"data":{..}}` |

### 由此产生的三条硬性实现要求

1. **积分等数值字段是字符串** → 必须先转数值再做差值运算，
   否则 `isinstance(x,(int,float))` 全部落空，变化永远检不出来。
2. **券包与营养库是 Markdown / 表格字符串** → 必须有专用解析器，
   只支持 JSON 会让模块 1 和模块 4 的核心功能完全失效。
3. **响应会被 4000 字符截断** → 营养库 160 条只能取到约 90 条，
   抽奖奖品列表中途断开。解析器必须能**从截断的 JSON 中抢救已完整字段**，
   并对「取不到」如实标 `unknown`。

`mm_calc.py` / `mm_state.py` 已实现上述全部处理，并有对应回归测试。

### 一个好消息：券类返回不含官方券 ID

`query-my-coupons` 只给券名、面额、有效期、标签，**没有任何券 ID 字段**。
因此本项目的 `id_known` 恒为 `false` —— 这是**如实反映官方数据现状**，
不是缺陷。但也意味着无法用券 ID 做后续操作，需要券码时只能引导用户从 APP 查看。

---

## 四、调用流程（以「一句话多模块」为例）

用户输入：
> 「按我的预算和营养范围选餐，看看优惠券和积分兑换能否用上，送到我选择的地址。」

```
阶段 1 · 只读侦察（不产生任何消费）
  query-my-account            → 积分余额
  query-my-coupons            → 原有券        ── 快照 pre
  available-coupons           → 可领券        ── 判断是否值得先领
  delivery-query-addresses    → 备选地址      ── 多地址则让用户选
  mall-points-products        → 积分可兑换项  ── 判断预算内能否用上积分
  （若 auto-bind-coupons 需执行，先过幂等闸门 + 用户确认）

阶段 2 · 生成候选（标注成本，不消费）
  list-nutrition-foods + query-meals
  mm_calc.py match-nutrition → 逐品匹配（匹配不上标"未知"）
  mm_calc.py sum-nutrition   → 组合合计（unknown 不参与估算）
  delivery-query-stores → query-store-coupons
  calculate-price            → 试算
  mm_calc.py price           → 校验试算自洽性
  mm_calc.py merge           → 四类优惠并列，不求和

阶段 3 · 确认（分项逐个确认，禁止打包授权）
  展示：门店/商品/营养/试算金额/优惠构成/四类优惠区分
  等用户明确回复

阶段 4 · 执行写操作（逐个过幂等闸门）
  ledger-begin --kind create-order --op-key <幂等键>
  create-order                → 支付链接
  mm_calc.py actual           → 解析实际优惠（≠ 试算优惠）
  ledger-finish --status confirmed

阶段 5 · 超时分支
  ledger-finish --status unknown
  query-order / order-list    → 核验是否已生成
  已生成 → confirmed（不重复下单）
  未生成 → rolled_back → 询问是否重试
```

---

## 五、限流与稳定性

| 约束 | 本项目做法 |
|---|---|
| 600 次/分钟 | 串行调用；定时任务每日仅 2-4 次只读调用 |
| `401` 鉴权失败 | 立即停止并提示重新配置，**不重试**（重试无意义且可能触发风控） |
| `429` 限流 | 降低频率、串行重试；领券类操作在 429 后**先查券包再决定是否重试** |
| 超时 | 一律先进账本 `unknown`，走核验分支 |

---

## 六、实测记录

见 `TEST_REPORT.md`。其中明确区分：

- ✅ **已真实验证**：脚本层逻辑（86 项自测，含幂等、超时核验、四类优惠隔离、营养未知标注）
- ⏳ **待真实验证**：全部依赖 MCP 实时返回的流程（因开发环境未取得有效 MCP Token）

能力探测脚本见 `scripts/probe_mcp.py`，Token 有效时可一键拉取真实 schema 与只读样本：

```bash
export MAIMAI_TOKEN=<你的 token>        # 或写入 ~/.maimai-helper/.token
python scripts/probe_mcp.py --list-tools --out tools.json
python scripts/probe_mcp.py --readonly-batch --out sample.json
```

> `probe_mcp.py` 内置写工具黑名单，即使误传参数也不会调用下单/兑换/抽奖类工具。
