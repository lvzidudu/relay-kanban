---
name: kanban
description: Relay：独立于工作区的本地任务看板（~/.kanban/），按功能维度聚合跨对话上下文。管理任务的添加、捞取执行、验收与归档。当用户输入 /todo、/kanban，或提到"看板""待办""捞任务""验收任务"时使用；每次新对话开场对账、对话收尾沉淀遗留任务时也应使用。
---

# Relay 任务看板

数据目录 `~/.kanban/`：`tasks/` 存活跃任务（每任务一个 md）、`archive/YYYY-MM/` 存归档、`BOARD.md` 为索引、`counter.txt` 为 ID 计数。

操作方式按优先级降级（Quest 等会话不加载用户注册的 MCP，降级是常态而非异常）：

1. **kanban MCP 工具**（add_task / list_tasks / get_task / update_status / append_task_log / edit_task / discard_task）
2. **HTTP API**（MCP 不可用时首选）：服务常驻 http://localhost:7654 ，用 curl 调用，状态机由服务端校验，BOARD.md 自动重建：
   - `GET /api/tasks?status=&keyword=&file_path=` 列任务；`GET /api/tasks/{id}` 读全文
   - `POST /api/tasks` 建任务（body: title/description/status/priority/unattended/workspace/files/tags）
   - `PATCH /api/tasks/{id}` 状态流转（body: status/note）；`PUT /api/tasks/{id}` 编辑
   - `POST /api/tasks/{id}/log` 追加时间线（body: entry/source）；`POST /api/tasks/{id}/discard` 废弃（body: reason）
   - `GET /api/schedule` 读定时配置；`PUT /api/schedule` 写（body: enabled/time/max_per_run）
3. **直接文件读写**（仅当 7654 也不可达时）：需自行遵守下方状态机并重新生成 BOARD.md

## 任务文件格式

文件名 `T001-简短标题.md`。frontmatter：

```yaml
---
id: T001            # T + 三位自增，取自 counter.txt
title: 重构登录模块
status: todo         # backlog/todo/doing/review/waiting_decision/done
priority: normal     # high/normal/low
unattended: false    # true 才允许无人定时执行
workspace: /path/to/repo   # 可选，关联代码仓库
files: [src/auth/login.ts] # 涉及文件，检索钥匙，执行中持续补充
tags: [auth]
created: 2026-07-28
updated: 2026-07-28
---
```

正文两个固定分区：

- `## 任务描述`：做什么 + 验收标准
- `## 时间线`：**只追加，不修改**。每条格式 `### YYYY-MM-DD HH:mm 来源`，内容分三类条目：决策（含理由）、改动（文件 + 摘要）、遗留（未尽事项，重要遗留应另建任务）

## 状态机（必须严格遵守）

| 当前 | 可流转到 | 条件 |
|---|---|---|
| backlog | todo | 想清楚后提升 |
| todo | doing / backlog | |
| doing | review / todo / waiting_decision | 转 waiting_decision 须写 pending_question |
| review | done / todo | 退回 todo 必须附验收反馈并写入时间线 |
| waiting_decision | todo | 决策结果先写入时间线 |
| done | 终态 | 文件移入 archive/YYYY-MM/ |

其余流转一律非法（如 backlog→doing、review→doing），必须拒绝。

状态机外的两个操作：

- **编辑（edit_task）**：可改标题/描述/优先级/unattended/workspace/files/tags；时间线只追加不可改，状态只能走 update_status
- **废弃（discard_task）**：软删除，任意活跃状态可用，**reason 必填**（写入时间线后归档）。与 done 的区别：done 是验收通过，只能从 review 走；废弃是不再需要，不经验收

## 命令

### /todo <内容>
一句话快速入板，不打断当前对话主线。默认 status=todo；内容含糊、显然没想清楚的建议用户放 backlog。只需 title + 一句话描述，不追问细节。

### /kanban run [id]
捞任务执行：

1. 无 id 时取 todo 列 priority 最高、最早创建的任务（定时无人场景只取 unattended: true 的）
2. 转 doing → 读任务文件全文恢复上下文（时间线里有历史决策与验收反馈）
3. 执行任务（workspace 字段指向的仓库）
4. 执行中每个关键决策/阶段改动**当场**追加时间线，并把新触碰的文件补进 files
5. 结束：汇总本轮改动与遗留写入时间线 → 转 review
6. 执行中遇到需用户拍板的问题：人在场直接问；无人场景调用 request_decision（不可用则写遗留后转回 todo）

### /kanban plan [计划来源]
计划落板：将 plan 产物（本对话生成的 spec 文件，或用户指定的计划文档）中的未执行条目结构化写入看板：

1. 逐条目提取：title（条目标题）+ description（做什么 + 该条目的验收标准，末尾附计划来源引用：spec 文件路径或计划标题）+ files（从 plan 上下文推断的涉及文件）+ workspace
2. 已完成/进行中的条目不入板；目标与验收标准清晰的进 todo，方向性/模糊条目进 backlog
3. 入板前列出"条目 → 目标列"清单让用户确认一次，同意后逐条 add_task；属于已有任务边界内的条目改为 append_task_log 写入该任务时间线
4. 触发时机：除用户主动输入外，对话收尾时若存在含未执行条目的 plan，应主动建议执行本命令

### /kanban review
逐条过 review 列：展示任务的本轮改动摘要 → 用户验收。通过 → done（归档）；不通过 → 反馈写入时间线 → 退回 todo。

## 开场对账（每次新对话开始时）

1. 读 BOARD.md，若有 todo/review/waiting_decision 任务，简报一句："当前 N 条待办、M 条待验收，要处理吗？"（看板为空则完全静默）
2. 定时链检测（只读，不依赖 schedule MCP——普通对话中该 MCP 不可用是常态）：读 `~/.kanban/schedule.json`（UI 的定时配置意图，`GET /api/schedule` 亦可），再读 Qoder 本地定时存储 `~/Library/Application Support/Qoder/User/globalStorage/aicoding.aicoding-agent/schedule/tasks.v1.json`（**只读，严禁直写**），筛出 status 为 pending 且触发时刻在未来的看板定时任务（title 含"看板"）：
   - `enabled: true` 且有 pending 任务 → 链活着，静默跳过（时刻/上限与配置不一致无需处理：链下次触发时会自读 schedule.json 对齐）
   - `enabled: true` 且无 pending 任务 → 冷启动/断链：schedule MCP 可用时（Quest 会话）按 `time` 直接补排；不可用时向用户输出点火指引——给出完整 prompt 模板（见"定时执行"）与建议时刻，请用户经 Qoder 定时任务入口手动创建首个任务
   - `enabled: false` 且有 pending 任务 → schedule MCP 可用则删除；不可用则提示一句：该任务下次触发时读到 enabled=false 会自行终止，可不处理，也可手动取消
   - `schedule.json` 不存在 → 存在 unattended 任务且无 pending 任务时，提示用户可在看板 UI 配置定时
   执行了动作（补排/删除/输出指引）后向用户一句话简报
3. 补录扫描（当前工作区是 git 仓库时执行）：以最近一次对话写入看板的时间为起点（取各任务 frontmatter `updated` 的最大值），运行 `git log --since=<起点> --name-only --pretty=format:` 收集近期改动文件，与 `~/.kanban/tasks/` 下所有任务 `files` 字段的并集做差集。存在未被任何任务关联的改动文件时，按文件路径/主题匹配候选任务，输出补录建议（文件 → 建议任务）并询问用户；同意后用 edit_task 将文件补进该任务 files，并 append_task_log 追加一条补录说明；匹配不到任务的文件提示用户可入板或忽略。差集为空则静默跳过

## 对话收尾沉淀（对话自然结束/话题完成时）

检查本次对话是否产生：未执行的计划条目（含 plan/spec 产物，成批的走 /kanban plan 结构化落板）、"下一阶段"安排、想清楚但没做的事。有则询问用户一句后写入看板（明确的进 todo，模糊的进 backlog）。

## 四个强制写入时机

1. 任务开始执行时：绑定任务 ID，读时间线
2. 关键决策/阶段改动后：当场追加，不攒到最后
3. 本轮执行结束时：汇总改动 + 遗留
4. 任何对话收尾时：本次对话若触碰了某任务 files/主题覆盖的内容，必须回写该任务时间线（用 list_tasks 按 file_path 或 keyword 反查）

## 定时执行（P2，链式续期）

配置层在看板 UI（“⏰ 定时”表单，落盘 `~/.kanban/schedule.json`，字段：`enabled`/`time`/`max_per_run`），`schedule.json` 是唯一事实源；**对齐由定时链自身完成**：每次触发时自读该配置，开关/时刻/上限的变更都在下一次触发时生效（时刻变更即次日生效），不依赖对话干预。唯一需要人工的是冷启动点火（创建首个定时任务），由开场对账第 2 步检测并给指引。定时任务的 prompt 模板（goalEnabled 视任务而定）：

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

要点：任务间串行且各自独立回写，前一个失败不阻断后一个（失败的写遗留后退回 todo）。

链上模板升级（硬规则）：凡在定时触发的会话中执行本 skill（收到"循环执行 /kanban run"类 prompt），续期时一律以本章节最新模板 + schedule.json 当前值为准，**忽略触发 prompt 里的固定时刻与"原样复制"类旧指令**——保证旧链自动迁移到新配置。

断链兜底双保险：开场对账第 2 步负责检测与点火指引（Quest 会话内可直接补排）；server 内置 watchdog 在配置时刻过后 30 分钟仍无执行痕迹时发钉钉提醒（需已配置钉钉，每日至多一次）。

## 决策升级（P3，钉钉）

无人执行遇决策点时调用 request_decision(task_id, question, options)：任务转 waiting_decision、发钉钉卡片、本轮正常结束。需要秒级往返时可用 wait_for_decision(task_id, timeout_sec)，超时自动降级为异步。用户在钉钉的回复由 MCP 服务写回时间线并将任务退回 todo，下次捞任务时带决策继续。
