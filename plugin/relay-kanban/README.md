# Relay 任务看板（relay-kanban）

对话是易失的，任务是持久的。Relay 把 AI 代理的工作单元从"对话流"迁移到"任务流"：每个任务是一个 Markdown 文件（frontmatter + 只追加的时间线），由严格状态机驱动（backlog → todo → doing → review → done），数据存本地 `~/.kanban/`。人负责发布任务与验收，agent 负责捞任务接力执行。

## 包含内容

| 组件 | 路径 | 说明 |
|---|---|---|
| Skill | `skills/kanban/SKILL.md` | /todo、/kanban run/plan/review 工作流、开场对账、状态机与写入规范 |
| Rules | `rules/`（3 条，alwaysApply） | kanban-boot（开场简报）、kanban-capture（话题闭合即捕获）、kanban-writeback（改动回写时间线） |
| MCP | `mcp.json` | kanban MCP 服务（本地 stdio，**含占位路径，需按 CONNECTORS.md 完成本地安装后替换**） |

## 未打包内容（在源仓库中）

- `server/`：MCP 服务端 + HTTP API + 看板 Web UI 的 Python 实现——本地 stdio 进程需要 venv 与依赖，无法随插件直接运行，通过源仓库 `install.sh` 一次性安装（见 `CONNECTORS.md`）
- `install.sh` / `templates/` / `tests/`：安装脚本与开发资产，随源仓库分发

## 安装

1. 安装本插件（skill + rules 即刻生效）
2. 按 `CONNECTORS.md` 完成 MCP 服务端本地安装并替换 `mcp.json` 占位路径

未完成第 2 步时 skill 自动降级：kanban MCP → HTTP API（localhost:7654）→ 直接文件读写。

## 来源

- 源仓库：kanban-system（`skill/kanban/SKILL.md` 与 `rules/*.md` 原样复制，未改写内容）
- Logo：`assets/avatar.svg` 为本插件生成的原创图标（看板三列 + 接力箭头）

## 校验

- `python3 scripts/validate_qoder_plugin.py plugin/relay-kanban`（create-plugin 离线校验器）：见仓库分发说明
