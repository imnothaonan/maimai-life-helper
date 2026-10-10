# 脚本说明

全部**纯 Python 标准库**，无第三方依赖。Python 3.9+。

| 脚本 | 职责 | 是否调用 MCP |
|---|---|---|
| `mm_state.py` | 偏好、运行记录、快照、幂等账本、定时守卫 | 否 |
| `mm_calc.py` | 金额核算、四类优惠、营养匹配、抽奖成本 | 否 |
| `menu.py` | **菜单抓取 / 候选筛选 / 批量试算** | **是（只读+试算）** |
| `mm_qr.py` | **本地生成二维码（支付链接）** | **否（全离线）** |
| `probe_mcp.py` | 拉取真实 schema 与只读样本 | **是（只读）** |
| `selftest.py` | 脚本层自测（183 项，模拟数据） | 否 |
| `fulltest.py` | 全功能测试（三轮，真实调用） | 是（只读） |

**所有脚本都不读写、不打印任何 Token。**

---

## mm_qr.py · 二维码生成（新增）

### 为什么需要

支付链接 `payH5Url` 约 95 字符，让用户复制粘贴到手机不现实，**扫码才是正常交互**。

### 为什么必须本地生成（安全红线）

> ⚠️ **绝对禁止**把支付链接送给第三方二维码 API
> （`api.qrserver.com` / `chart.googleapis.com` / `quickchart.io`）。
> `payH5Url` **携带订单凭证**，发给外部服务等同泄露订单。

本项目 vendored 了 segno 1.6.6 的编码核心（BSD-3-Clause）：

```
scripts/_vendor/segno/
  __init__.py   本项目写的轻量封装（暴露 make()）
  encoder.py    segno 原生编码器（QR 纠错/掩码/格式信息）
  consts.py     常量表
  LICENSE       BSD-3-Clause（Copyright 2016-2025, Lars Heuer）
```

**只保留 encoder + consts**（省略 writers/helpers/cli），
已实测确认二者**仅依赖 Python 标准库**，符合项目「零第三方依赖」原则。
图像渲染由 `mm_qr.py` 自行实现（约 60 行）。

### 用法

```bash
# ★ 内联 SVG（推荐给 Widget/HTML，自带校验，成功率 100%）
python scripts/mm_qr.py "<url>" --format inline --out pay_qr.svg

# PNG（保存/发送用）
python scripts/mm_qr.py "<url>" --format png --out pay_qr.png --scale 11

# SVG（独立文件，非内联紧凑格式）
python scripts/mm_qr.py "<url>" --format svg --out qr.svg

# ASCII（纯文本兜底，浅色终端加 --invert）
python scripts/mm_qr.py "<url>" --format ascii --invert
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--scale` | 8 | 每个模块的像素数 |
| `--border` | 4 | 静区格数，**QR 规范要求 ≥4，不要改小** |
| `--error` | `m` | 纠错级别 l/m/q/h，M 约 15% 容错 |

### 实测尺寸参考

| URL 长度 | QR 版本 | 矩阵 |
|---|---|---|
| 95 字符（真实支付链接） | v6 | 41×41 |
| 146 字符 | v8 | 49×49 |

### 展示要求

1. 用**白底卡片**包住二维码（深色主题下必须深色块 + 浅色底才能扫）
2. 二维码旁附订单摘要（门店 / 品项 / 取餐方式 / 金额）
3. SVG 必须带 `shape-rendering="crispEdges"`，否则缩放后边缘模糊

### ⚠️ 必须用内联 SVG，不要用 `<img>` 指向本地文件

实测：`<img src="...">` 指向本地 PNG，**无论路径怎么写都渲染失败**：

| 写法 | 结果 |
|---|---|
| `/e/tencent_claw/pay_qr.png`（Git Bash 风格） | ❌ 裂开 |
| `E:/tencent_claw/pay_qr.png`（Windows 绝对路径） | ❌ 裂开 |

文件本身没问题（有效 PNG、可读、在工作区内）—— 是宿主对本地路径的改写没生效。
**不要再试路径写法。**

**正确做法**：

```bash
python scripts/mm_qr.py "<url>" --format inline --out pay_qr.svg
```

输出的 SVG 自包含（无 `src`/`href`），整段嵌进 Widget HTML 即可，**成功率 100%**。

体积优化：stroke 横线 + 行内相对 `m` 链式移动，
41×41 矩阵 6146 → **2809 字符**（降 54%），整体约 3KB。

### ⚠️ 内联 SVG 必须验证（会静默画歪）

压缩过的 path 若游标处理有误，**二维码看起来正常却扫不出来** —— 比裂图更危险。

`--format inline` 自动跑反向重建校验（path → 位图 → 与矩阵逐模块比对）：

```json
"verified": {"ok": true, "diff": 0, "finders": {"tl": true, "tr": true, "bl": true}}
```

也可单独调用：

```python
import mm_qr
svg = mm_qr.to_inline_svg(url)
mm_qr.verify_inline_svg(url, svg)   # 返回 {ok, diff, n, finders}
```

> 实测教训：验证器**自己**曾漏掉「画完 `h` 要更新游标」，误报 734 个模块不一致；
> 修正后确认真实差异为 0。所以自测里专门加了「验证器能识别破坏路径」一条 ——
> 光有验证器不够，得证明它真的会报错。

### ⚠️ 支付链接只在 `create-order` 时能拿到

实测 `query-order` 响应**被 4000 字符截断**，`payH5Url` 在截断之后，**回头查拿不到**。
下单成功后必须**立即**用返回的 `orderId` + `payId` 生成二维码。

拼装格式（实测有效，curl 返回 HTTP 200 + 标题「麦当劳点餐」）：

```
https://m.mcd.cn/mcp/scanToPay?orderId=<orderId>&payId=<payId>
```


---

## menu.py · 菜单与试算（新增）

存在理由：实测点餐曾调用 `calculate-price` **24 次**（占全程 token 60%+），
根因是没有「试算收敛」约束，只能逐个编码试算反推价格。
本脚本用 `calculate-price` 的 `items` **数组**能力，一次传入多个商品。

| 子命令 | MCP 调用 | 作用 |
|---|---|---|
| `menu fetch` | **1 次** | 拉菜单并落盘缓存 `~/.maimai-helper/menu_<store>_<orderType>_<beType>.json` |
| `menu candidates` | **0 次** | 纯本地按预算筛候选 |
| `menu price` | **1 次** | `items` 数组批量试算，一次拿多个商品真实价格 |
| `menu nutrition-cache` | **1 次** | 拉营养库并缓存（24h TTL） |

```bash
# 1 次 MCP：拉菜单并缓存
python scripts/menu.py fetch --store 1410258 --order-type 2 --be-type 2 --be-code 141025802

# 0 次 MCP：本地按预算筛候选（40 元）
python scripts/menu.py candidates --store 1410258 --order-type 2 --be-type 2 \
  --budget 4000 --limit 3 --nutrition

# 1 次 MCP：批量试算（≤10 个编码）
python scripts/menu.py price --store 1410258 --order-type 2 --be-type 2 \
  --be-code 141025802 --codes 901406,921147,920112
```

### 实测要点

| 事项 | 说明 |
|---|---|
| **菜单常被截断** | `query-meals` 响应常按 4000 字符截断，详情表（名称+价格）可能拿不到 |
| **无价格时标 `price_unknown`** | 绝不拿原价冒充现价；此时 `candidates` 改按**标签/分类**排序推荐 |
| **试算会回填缓存** | `menu price` 返回的 `productName`/`subtotal` 会写回菜单缓存，逐步补全被截断字段 |
| **beType=1 不传 beCode** | 传了会报错，脚本已自动忽略 |

### 调用次数红线

- `menu price --codes` 一次最多 10 个，超出直接拒绝并提示收窄候选
- `menu.py` **不含任何下单/取消/兑换/抽奖接口**，源码层面杜绝


---

## mm_state.py · 状态与账本层

固定工作目录 `~/.maimai-helper/`（可用 `MAIMAI_HOME` 覆盖）：

```
preferences.json          偏好
runs/YYYY-MM-DD.jsonl     每日运行记录
snapshots/                快照（kind_label_date.json）
ledger.json               写操作幂等账本
```

```bash
python scripts/mm_state.py home
python scripts/mm_state.py prefs-get [--key coupon.daily_time]
python scripts/mm_state.py prefs-set --key coupon.daily_time --value '"08:30"'
python scripts/mm_state.py prefs-set --key coupon.enabled --value false

# 位置记忆（只由用户口述，不做自动定位）
python scripts/mm_state.py prefs-set --key geo.city --value '"示例市"'
python scripts/mm_state.py prefs-get --key geo

python scripts/mm_state.py snapshot --kind coupons --label pre  --file before.json
python scripts/mm_state.py snapshot --kind coupons --label post --file after.json
python scripts/mm_state.py diff-coupons --before pre --after post
python scripts/mm_state.py diff-account --before pre --after post

python scripts/mm_state.py ledger-begin  --kind mall-create-order --op-key K1
python scripts/mm_state.py ledger-finish --kind mall-create-order --op-key K1 --status confirmed
python scripts/mm_state.py ledger-get    --kind mall-create-order --op-key K1
python scripts/mm_state.py ledger-list --limit 10    # 默认只出摘要，不含 history

python scripts/mm_state.py cron-guard --kinds auto-bind-coupons query-my-account
python scripts/mm_state.py run-status
```

### `geo` 偏好块（位置记忆）

| 字段 | 示例 | 说明 |
|---|---|---|
| `geo.city` | `"示例市"` | 城市 |
| `geo.district` | `"示例区"` | 区县 |
| `geo.note` | `"某某地铁站附近"` | 自由备注 |
| `geo.source` | `"user_stated"` | **固定值**，标明来源是用户口述 |

> ⚠️ **本 Skill 没有 IP 自动定位能力。** 实测本机 IP 定位会给出
> 北京/深圳两个互相矛盾的错误结果（与开发者实际所在城市也不符），
> 双源矛盾时无法自证正确，而点餐会产生真实支出，
> 静默送错城市代价大于"定不了"。因此位置**只由用户口述并记忆**。
>
> `geo` 仅用于填 `query-nearby-stores` 的 `city`/`keyword`，
> **不得**用于推测或构造配送地址；外送链路只能走 `addressId`。

### 幂等账本状态机

```
ledger-begin ──> pending
                   │
     ┌─────────────┼──────────────┐
     ▼             ▼              ▼
 confirmed      unknown         failed
 拒绝重复        禁止重试        允许重试
 必须先核验
     │             │
     │        核验未生效
     │             ▼
     └────── rolled_back ──> 可重新 begin
```

`ledger-finish --status` 可选：`confirmed` / `failed` / `unknown` / `rolled_back`

### 设计要点

- 状态文件**原子写**（临时文件 + `os.replace`），中断不损坏
- 文件损坏时改名保留现场并告警，不静默吞掉
- 券 ID 用候选键宽松匹配（`couponId` / `coupon_id` / `id` / `voucherId` / `code`），
  都取不到时用「名称+金额+有效期」生成指纹并标 `id_known=false`
- 支持 3 层嵌套返回结构（`data.result.couponList` 等）的券数组提取

---

## mm_calc.py · 试算与核算层

```bash
# 金额（内部一律「分」）
python scripts/mm_calc.py cents "12.34"

# 试算（calculate-price 返回）
python scripts/mm_calc.py price  --file calculate_price.json

# 实际订单（create-order / query-order 返回）
python scripts/mm_calc.py actual --file order.json

# 四类优惠并列（单位：分，禁止求和）
python scripts/mm_calc.py merge --owned 1200 --new 800 --trial 1200 --actual 1100

# 营养
python scripts/mm_calc.py match-nutrition "巨无霸" --file nutrition.json
python scripts/mm_calc.py sum-nutrition   --file selected.json
python scripts/mm_calc.py check-range --range 400-700 --file total.json

# 抽奖
python scripts/mm_calc.py lottery --times 3 --file lottery_info.json

# 有效期
python scripts/mm_calc.py expiry "2026-11-30"
```

> `check-range` 接收的是 `sum-nutrition` 的**输出**，不是原始数组。

### 设计要点

- `to_cents` / `cents_str`：整数分运算，杜绝浮点误差
- `classify_trial_discount`：校验「商品总额 − 优惠 + 配送费 = 应付」是否自洽，
  不自洽则报差异，**不强行纠正**
- `extract_actual_discount`：优先采信接口直接返回的优惠字段，
  缺失时才用「商品总额 − 实付」推算并标注来源
- 金额字段完全缺失 → 返回 `null` + 说明，**不用 0 冒充**
- `match_nutrition`：三档置信度（high / medium / none），
  无匹配返回 `status: unknown` 且 `matched: null`
- `sum_nutrition`：任一项 unknown → 该维度合计为 unknown
- `normalize_lottery`：官方未返回概率 → `probability: unknown` +
  「本 Skill 不会自行推算或估算中奖率」
- `merge_discount_sources`：恒返回 `must_not_sum: true`

---

## probe_mcp.py · 能力探测

```bash
export MAIMAI_TOKEN=<token>       # 或写入 ~/.maimai-helper/.token
python scripts/probe_mcp.py --list-tools --out tools.json
python scripts/probe_mcp.py --list-tools-md tools.md
python scripts/probe_mcp.py --call now-time-info
python scripts/probe_mcp.py --readonly-batch --out sample.json
python scripts/probe_mcp.py --call query-meals --args '{"storeCode":"123"}' --shape-only
```

**安全**：内置写工具黑名单（`auto-bind-coupons` / `mall-create-order` /
`draw-lottery` / `create-order` / `cancel-order` / `party-order-create` /
`delivery-create-address`），即使误传参数也**拒绝调用**。

Token 仅从环境变量或 `~/.maimai-helper/.token` 读取，
不硬编码、不写入输出。

---

## selftest.py · 自测

```bash
python scripts/selftest.py
```

**86 项**，全部使用**模拟数据**，验证纯计算与状态逻辑。
不代表任何真实 MCP 调用结果。

覆盖：偏好、券包对比、积分对比、幂等账本、定时守卫、金额核算、
营养匹配、抽奖、有效期、组合场景、Token 残留扫描。

退出码 0 = 全部通过。
