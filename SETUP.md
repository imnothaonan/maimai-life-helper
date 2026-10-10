# 安装与接入

## 前置条件

- WorkBuddy（官方合作伙伴，赛事推荐开发环境）
- 有效的麦当劳 MCP Token
- Python 3.9+（仅用于辅助脚本，**无第三方依赖**）

---

## 第一步：申请 MCP Token

1. 打开 https://open.mcd.cn/mcp
2. 右上角【登录】→ 手机号验证
3. 登录后右上角变为【控制台】→ 点击
4. 点【激活】按钮
5. 同意服务协议
6. 复制 MCP Token

> ⚠️ Token 等同于账号凭证。**不要贴在任何对话窗口、群聊或公开仓库里。**

---

## 第二步：在 WorkBuddy 中接入 MCP

左侧边栏【专家·技能·连接器】→【自定义连接器】→【配置MCP】，
粘贴以下 JSON，把 `YOUR_MCP_TOKEN` 替换为你的 Token：

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

保存 → 回到【自定义连接器】→ **启用** `mcd-mcp`。

### 验证接入是否成功

在对话框中问：「用麦当劳 MCP 查一下现在的时间」，
若返回真实时间即接入成功。

### 常见错误

| 现象 | 原因 | 处理 |
|---|---|---|
| `401` 鉴权码不存在或已失效 | Token 错误/过期 | 回控制台重新激活 |
| `429` 限流 | 超过 600 次/分钟 | 降低频率，串行调用 |
| 看不到 `mcd-mcp` 工具 | 未启用 | 到自定义连接器页启用 |
| 连接器列表里找不到自定义入口 | 版本过旧 | 升级 WorkBuddy |

---

## 第三步：安装 Skill

### 方式 A：直接复制（最简单）

```bash
# macOS / Linux / Git Bash
cp -r maimai-life-helper ~/.workbuddy/skills/

# Windows PowerShell
Copy-Item -Recurse -Force .\maimai-life-helper "$env:USERPROFILE\.workbuddy\skills\"
```

### 方式 B：作为项目级 Skill

放到项目的 `.workbuddy/skills/` 下，仅当前项目可用。

### 验证安装

重启 WorkBuddy 会话后问：「你有哪些 skill？」，
应能看到 `maimai-life-helper`。

---

## 第四步：首次自检（可选）

无需 Token 即可运行，验证脚本层逻辑：

```bash
cd maimai-life-helper
python scripts/selftest.py
```

预期输出：`通过 86 项，失败 0 项`。

> 自测使用**模拟数据**，不代表真实 MCP 调用结果。

---

## 第五步（可选）：拉取真实工具 schema

Token 有效时，可导出真实 schema 供开发核对：

```bash
# Linux / macOS / Git Bash
export MAIMAI_TOKEN=<你的 token>
python scripts/probe_mcp.py --list-tools --out tools.json
python scripts/probe_mcp.py --readonly-batch --out sample.json

# Windows PowerShell
$env:MAIMAI_TOKEN = "<你的 token>"
python scripts\probe_mcp.py --list-tools --out tools.json
```

或把 Token 写入 `~/.maimai-helper/.token`（该目录**不在项目内**，
且已在 `.gitignore` 中排除）。

> `probe_mcp.py` 内置**写工具黑名单**，即使误传参数也不会调用
> 领券/兑换/抽奖/下单/取消/预约类工具。

---

## 配置定时自动领券

### 方式 A：对话中配置（推荐）

直接对 WorkBuddy 说：

> 「设置每天早上 9 点自动帮我领麦当劳的券，只领券，不要下单也不要抽奖。」

### 方式 B：手动创建定时任务

```json
{
  "name": "麦麦每日领券",
  "scheduleType": "recurring",
  "rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
  "prompt": "执行「麦麦生活助手」模块 1 的自动领券流程：\n1. 运行 python scripts/mm_state.py run-status，若今日已成功领券则直接结束\n2. 只读调用 query-my-account 与 available-coupons\n3. 若有可领券：存 pre 快照 → 调用 auto-bind-coupons → 调用 query-my-coupons 存 post 快照\n4. 用 mm_state.py diff-coupons --before pre --after post 判断是否有新券\n5. 仅当 new_count > 0 时汇报新增券名与面额；无变化则静默，只写运行记录\n6. 严禁调用任何消耗积分或下单类工具"
}
```

### 修改时间 / 暂停

```bash
# 改为 08:30
python scripts/mm_state.py prefs-set --key coupon.daily_time --value '"08:30"'

# 暂停
python scripts/mm_state.py prefs-set --key coupon.enabled --value false

# 恢复
python scripts/mm_state.py prefs-set --key coupon.enabled --value true
```

### 定时任务纪律

定时任务**只允许**只读查询与自动领券。以下动作一律禁止无人值守执行：

```
mall-create-order   积分兑换（消耗积分）
draw-lottery        抽奖（消耗积分/次数）
create-order        下单（产生支付）
cancel-order        取消订单
party-order-create  活动预约
delivery-create-address  新增地址
```

配置前可自行校验：

```bash
python scripts/mm_state.py cron-guard --kinds auto-bind-coupons query-my-account available-coupons
```

---

## 设置个人偏好

```bash
# 热量区间
python scripts/mm_state.py prefs-set --key nutrition.calorie_range --value '[400,700]'

# 预算（单位：分）
python scripts/mm_state.py prefs-set --key nutrition.budget_cents --value '3500'

# 口味偏好
python scripts/mm_state.py prefs-set --key nutrition.preferences --value '["不辣","多蔬菜"]'

# 忌口
python scripts/mm_state.py prefs-set --key nutrition.avoid --value '["花生","海鲜"]'

# 常用活动城市
python scripts/mm_state.py prefs-set --key party.city --value '"上海"'

# 查看全部偏好
python scripts/mm_state.py prefs-get
```

---

## 常见问题

**Q：为什么 Skill 里不写死各工具的参数？**
A：官方 README 未提供参数 schema。写死就是臆造。本项目改为运行时读取真实 schema，
以 schema 为准；README 与 schema 冲突时以 schema 为准并说明差异。

**Q：为什么每次领券前后都要查一次券包？**
A：`auto-bind-coupons` 的返回值不足以证明领取成功。回查券包做快照对比，
才能确认真的新增了券，也才能实现「无变化不重复提醒」和超时核验。

**Q：为什么下单要过账本？**
A：下单、兑换、抽奖都会真实消耗资源。超时后盲目重试会造成重复扣积分或重复下单。
账本强制「先核验，再决定是否重试」。

**Q：营养数据对不上怎么办？**
A：直接标「未知」。营养库给的是标准份，加料或改套餐后数值会变，
按比例外推属于编造。`mm_calc.py match-nutrition` 会返回 `status: unknown`。

**Q：会未经确认就花钱吗？**
A：不会。所有消耗积分或产生支付的动作都必须先展示具体内容与成本，
拿到用户明确确认后才执行，且执行前过幂等闸门。

**Q：Token 会保存在项目里吗？**
A：不会。项目内没有任何 Token。`mcp-config.example.json` 只用
`${MCD_MCP_TOKEN}` 环境变量占位符；`selftest.py` 会扫描源码确认无残留。
