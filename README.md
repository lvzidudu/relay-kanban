# Relay 任务看板系统

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

对话是易失的，任务是持久的。Relay 把 AI 代理的工作单元从"对话流"迁移到"任务流"：每个任务是一个 Markdown 文件（frontmatter + 只追加的时间线），由严格状态机驱动（backlog → todo → doing → review → done）。代理通过 MCP 工具、HTTP API 或直接文件读写三级降级接入；人通过 Web UI 建任务、验收、配置定时；无人值守时支持链式定时执行与钉钉决策升级。

Relay：独立于工作区的本地任务看板，按功能维度聚合跨对话上下文，人负责发布任务与验收，agent 负责捞任务接力执行。

[查看包含完整界面与流程截图的单文件项目介绍](relay-kanban-intro-share.html)

## 核心能力

- **任务级持久上下文**：Task + State Machine + Timeline 将需求、决策、代码改动与验收反馈沉淀为可版本化的 Markdown 文件。
- **跨会话接力**：新会话通过任务摘要、时间线和文件索引恢复上下文，不依赖单次对话窗口。
- **人机验收闭环**：`todo → doing → review → done` 强状态机；验收退回必须附带反馈并写入时间线。
- **三级接入与降级**：Agent 可经 MCP、HTTP API 或直接文件读写接入；上层能力不可用时仍能维护任务数据。
- **无人值守与决策升级**：支持定时捞取任务、单轮上限、断链提醒，以及可选的钉钉异步决策回写。

- 数据目录：`~/.kanban/`（任务文件 + BOARD.md 索引，可自行 git 管理）
- Skill：`~/.qoder/skills/kanban/SKILL.md`（源文件在本仓库 `skills/kanban/`，修改后需重新复制部署）
- 服务：MCP 工具（stdio） + 看板 Web UI（http://localhost:7654），同进程同数据源

## 安装与启动

要求 Python >= 3.10（或已安装 [uv](https://github.com/astral-sh/uv)，脚本会自动用它装受管 Python 3.12）：

```bash
git clone https://github.com/lvzidudu/relay-kanban.git
cd relay-kanban
./install.sh            # 建 venv + 装依赖 + 部署 skill/rules + 初始化 ~/.kanban/
./install.sh --launchd  # 可选：额外把 Web UI 注册为 macOS 开机常驻服务
```

脚本结尾会打印带真实路径的 MCP 注册配置，粘进 Qoder 即可（见下节）。手动调试：

```bash
# 仅起 Web UI（调试）
.venv/bin/python -m server.main --web-only

# 跑测试（需先装 pytest：.venv/bin/pip install pytest）
.venv/bin/python -m pytest -q
```

## 在 Qoder 中注册 MCP（P1 必做，手动一次）

**注意：Qoder 有两层 MCP 配置，agent 会话（含 Quest）只读 `~/.qoder/mcp.json`**（`mcpServers` 格式）；设置界面可能写入的 `~/Library/Application Support/Qoder/User/mcp.json`（`servers` 格式）是 IDE 编辑器层，只注册在那里会出现"服务进程在跑、Web UI 能开，但对话里提示 kanban MCP 不可用"的现象。

在 `~/.qoder/mcp.json` 的 `mcpServers` 中添加（`install.sh` 结尾会打印带你机器真实路径的版本）：

```json
{
  "kanban": {
    "command": "<仓库绝对路径>/.venv/bin/python",
    "args": ["-m", "server.main"],
    "cwd": "<仓库绝对路径>"
  }
}
```

注册后 Qoder 对话即可调用工具：`add_task` / `list_tasks` / `get_task` / `update_status` / `append_task_log` / `edit_task` / `discard_task` / `request_decision` / `wait_for_decision`。MCP 启动时会同时在 7654 端口拉起看板 UI。

## 全局 Rules（MVP 必做，共三条，install.sh 自动部署）

skill 只在被唤起时生效，"随手开的对话"需要 always-on 规则兜底。三条规则源文件在本仓库 `rules/`，`install.sh` 会部署到 `~/.qoder/rules/`（已存在则跳过，不覆盖本地修改），`alwaysApply: true`：

**kanban-boot.md（开场简报，开场对账的 always-on 入口）：**

```
每次新对话的首轮回复前，先读 ~/.kanban/BOARD.md（读不到时可用 kanban MCP 的
list_tasks 或 GET http://localhost:7654/api/tasks 代替）：若存在 todo/review/
waiting_decision 任务，在回答正题前先一句话简报"当前 N 条待办、M 条待验收，
要处理吗？"；看板为空则完全静默，不提看板。简报后若用户要处理，或需要执行
定时对齐与补录扫描等完整开场对账流程，加载 kanban skill 执行。本规则仅在对话
首轮生效，后续轮次不重复简报。
```

> 为什么需要这条：开场对账的完整流程写在 SKILL.md 里，但 skill 是按需触发的——新对话首句通常与看板无关，skill 不会被加载，对账便不会发生。这条 rule 只承担最轻的简报（一次文件读取），重的定时对齐 + git 补录扫描仍由它引导加载 skill 执行，避免每轮对话背上全套对账开销。

**kanban-writeback.md（回写兜底）：**

```
对话中修改了代码或做出技术决策时，结束前检查 ~/.kanban/tasks 是否有 files/主题
关联的任务（可用 kanban MCP 的 list_tasks 按 file_path 或 keyword 反查），有则
将本轮决策与改动摘要通过 append_task_log 追加进该任务时间线。若本轮改动已实质
完成该任务的目标，回写后询问用户是否将其流转至 review。
```

**kanban-capture.md（话题闭合即捕获，不依赖 skill 唤起、不赌对话何时结束）：**

```
每当一个话题或交付闭合时（任务完成交付、用户表示满意、用户切换新话题、用户
表达结束意图），当场检查该话题是否产生了：未执行的计划条目、"下一阶段"
安排、想清楚但未做的事项、执行任务过程中发现的超出当前任务范围的重要问题。
不要等对话结束再统一处理（对话何时结束不可预知，最后一轮之后没有执行机会）。
若有，向用户简要确认一句，用户同意后通过 kanban MCP 的 add_task 写入看板
（~/.kanban/）：目标与验收标准明确的进 todo，模糊想法进 backlog；若属于某个
已有任务边界内的小尾巴，则写入该任务时间线而非新建。入板只需 title 加一句话
描述，不追问细节。
```

## 定时捞任务（P2，链式续期）

定时配置在看板 UI 的“⏰ 定时”表单里设置（执行时刻/开关/单轮上限，落盘 `~/.kanban/schedule.json`），该文件是唯一事实源：**定时链每次触发时自读它完成对齐**，开关/时刻/上限变更都在下一次触发时生效，不依赖对话干预。唯一需要人工的是冷启动点火（普通对话调不了 schedule MCP，需经 Qoder 定时任务入口手动创建首个任务）：开场对账会只读 Qoder 本地定时存储检测链是否存活，断链时给出点火指引。首个定时任务的 prompt 用：

```
先读 ~/.kanban/schedule.json：若 enabled 为 false，本轮直接结束（不执行不续期）。
否则循环执行 /kanban run（仅限 unattended: true 的任务）：每次完整处理一个任务
（转 doing → 执行 → 回写时间线 → 转 review）后再捞下一个，直到 todo 列没有
unattended 任务或本轮已处理 N 个（N 取 schedule.json 的 max_per_run，读不到则 3；
单轮上限，防止上下文膨胀）。执行完毕后，无论成败，调用 schedule MCP 给自己创建
下一次定时任务：时刻取 schedule.json 的 time（次日，读不到则 10:00）；prompt 取
kanban skill SKILL.md「定时执行」章节的最新模板（不要复制本段旧文，使模板升级
能沿链传导）。若 todo 列没有 unattended 任务，直接续期即可。
```

断链兜底：kanban skill 的开场对账负责定时链检测与点火指引（Quest 会话内可直接补排）；server 内置 watchdog 在配置时刻过后 30 分钟仍无执行痕迹时发钉钉提醒（需已配置钉钉，每日至多一次）。开场对账同时负责补录扫描（git 近期改动文件与看板 files 索引取差集，发现漏写的改动提示补录）；成批的计划条目可用 /kanban plan 结构化落板（详见 SKILL.md）。

## 钉钉决策升级（P3，可选）

无人执行遇到决策点时，可通过钉钉机器人发送单聊请求。配置步骤：

1. 在[钉钉开放平台](https://open.dingtalk.com/)创建应用并添加机器人能力。
2. 将消息接收模式设为 **Stream 模式**，申请机器人单聊消息权限。
3. 记录 AppKey、AppSecret 和接收人的 userId。
4. 安装可选依赖：`.venv/bin/pip install '.[dingtalk]'`。

**通用配置：**

将凭据写入 `~/.kanban/config.json`（勿提交到任何 git 仓库）：

```json
{ "appKey": "...", "appSecret": "...", "userId": "..." }
```

配置存在时 MCP 服务自动启用 Stream 客户端；用户在钉钉回复 `T001 决策内容`，决策自动写回任务时间线并将任务退回 todo。回复容错：ID 宽松匹配（t1、T 001：等均可）；未带 ID 且仅一个任务在等决策时自动匹配；解析/流转失败会回复引导而非静默丢弃。未配置时相关功能静默降级，不影响其他能力。

> 安全提示：`~/.kanban/config.json` 含机器人凭据，禁止提交到 Git、截图分享或写入任务时间线。泄露后应立即在钉钉开放平台轮换 AppSecret。

## 状态机

```
backlog -> todo -> doing -> review -> done（归档至 archive/YYYY-MM/）
            ^        |        |
            |        v        v（退回必附验收反馈）
            +---- waiting_decision（无人执行遇决策点）-> todo
```

非法流转会被 MCP / HTTP API 拒绝（HTTP 422）。

状态机外的两个操作：`edit_task`（编辑元信息与描述，时间线不可改）、`discard_task`（废弃 = 软删除，reason 必填，写入时间线后归档，不经 review 验收）。直接归档（绕过验收的 done）故意不提供。

## Plugin 安装说明

仓库根目录包含标准 Plugin 声明，但通过插件包安装时通常只能获得 skill 与 MCP 配置等"声明层"；**完整体验仍需克隆本仓库并执行 `install.sh`**。能力分层如下：

| 能力 | 仅装插件 | 插件 + install.sh |
|---|---|---|
| `/kanban` `/todo` 对话命令（skill） | ✅ | ✅ |
| 9 个 MCP 任务工具（add_task / list_tasks 等） | ❌ 无服务端，skill 降级到直接读写 ~/.kanban/ 文件 | ✅ |
| 看板 Web UI（http://localhost:7654 拖拽验收） | ❌ | ✅ |
| 三条 always-on 规则（开场简报/话题捕获/改动回写） | 取决于宿主是否支持 rules | ✅ |
| 钉钉决策升级 / 定时无人执行 | ❌ | ✅（钉钉需额外配置，见下方章节） |

**补齐完整体验（一次性，可直接交给 agent 执行）**：

```bash
git clone https://github.com/lvzidudu/relay-kanban.git
cd relay-kanban && ./install.sh
```

install.sh 会自动完成全部步骤，**无需人工介入**：建 venv（优先 uv，否则系统 Python ≥ 3.10）→ 装依赖 → 部署 skill + rules 到 ~/.qoder/ → 初始化 ~/.kanban/ 数据目录 → **把 kanban MCP 自动注册进 ~/.qoder/mcp.json**（已有内容则合并，先备份）。

装完验证（agent 可逐条自检）：

1. `cat ~/.qoder/mcp.json` → 应含 `"kanban"` 条目（command 指向仓库 .venv）
2. `ls ~/.qoder/rules/` → 应含 kanban-boot.md / kanban-capture.md / kanban-writeback.md
3. 新开对话输入 `/kanban` → skill 被触发，MCP 工具可调（首次调用时 MCP 进程自动拉起，同时在 7654 端口起看板 UI）
4. 浏览器开 http://localhost:7654 → 看到看板列（若未启动，跑 `.venv/bin/python -m server.main --web-only` 或用 `./install.sh --launchd` 注册常驻）

已装过插件会不会冲突？不会——skill 文件同名覆盖（内容一致），rule 已存在则跳过不覆盖，mcp.json 只新增/更新 kanban 一个条目。只想补 rules 不想动其他（比如 MCP 已手动配好），用 `./install.sh --rules-only`。

## 阶段验证

以下数据来自作者个人真实使用，截至 2026-08-07，用于验证任务级上下文方案的可行性，不代表多人生产环境基准：

| 指标 | 结果 | 口径 |
|---|---:|---|
| 累计承载任务 | 44 | 在 Relay 中创建并实际推进的任务 |
| 跨会话接力率 | 17% | 至少由两个独立 Agent 会话连续处理的任务占比 |
| 决策可追溯率 | 29% | 时间线中沉淀了关键技术或产品决策的任务占比 |
| HTTP 调用延迟 | P50 约 6 ms | 本地服务主动调用，不含模型推理时间 |
| 基础上下文开销 | 单会话约 1K Token | 开场简报与任务恢复的基础注入量 |

阶段数据表明，Relay 可以用较小的即时延迟与上下文开销换取跨会话任务连续性。当前样本仍以个人使用为主，后续将继续补充多人协作、任务完成率与端到端 Token 收益评测。

## 看板 UI

零构建单文件（`server/web/index.html`，SortableJS 拖拽），与 MCP 同进程同数据源，30s 轮询保持与对话侧同步：

- **拖拽流转**：拖拽时目标列高亮；非法流转被服务端拒绝并 toast 提示。review 退回 todo 弹内联弹窗强制填验收反馈；拖入 doing 弹窗提示看板不会自动执行，可一键复制 `/kanban run Txxx` 到对话触发（看板只改状态标记，真正执行入口只有对话 `/kanban run` 与定时无人执行）
- **任务详情**：描述区轻量 markdown 渲染；时间线渲染为结构化组件（竖线+节点+时间戳+来源徽标）；提供编辑/废弃入口，废弃原因走内联弹窗必填（全站无 prompt()）
- **检索**：页头 keyword 搜索（后端匹配标题/标签/正文，300ms 防抖）+ tag/优先级下拉过滤（tag 选项从任务动态收集）
- **卡片**：状态色条、相对时间、时间线条目数徽标；任务 ID 点击复制（快路径：复制后到对话中 `/kanban run Txxx`）；review 列卡片直接提供通过归档/退回按钮

## 目录结构

```
server/
├── main.py           # 入口：MCP 工具注册 + HTTP API + Web 托管
├── storage.py        # 任务文件读写、ID 分配、BOARD.md 重建、归档
├── state_machine.py  # 流转规则单一实现
├── dingtalk.py       # P3：Stream 客户端 + 单聊发送（config.json 存在时启用）
└── web/index.html    # 看板 UI（零构建，能力见上节）
skills/kanban/SKILL.md # kanban skill 源文件（install.sh 部署到 ~/.qoder/skills/kanban/；也是插件的 skills 组件）
rules/                # 三条 always-on 规则源文件（install.sh --rules-only 单独部署；ACA 插件当前不支持 rules 组件自动部署，详见上方 "ACA 插件用户须知"）
.qoder-plugin/plugin.json # 标准 Plugin 清单（仓库根目录即插件根，声明 skills/rules/mcpServers）
mcp.json              # 插件 MCP 配置（含 <KANBAN_REPO> 占位路径，安装说明见 CONNECTORS.md）
CONNECTORS.md         # 插件使用者的 MCP 服务端安装与连接说明
install.sh            # 一键安装：venv + 依赖 + skill/rules 部署 + MCP 配置打印；--rules-only 仅部署 rules（ACA 插件用户补装用）
templates/BOARD.md    # 空看板模板
tests/test_kanban.py  # 状态机流转表逐格覆盖 + 存储层 + 钉钉回复容错
```
