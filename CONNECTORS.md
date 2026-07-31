# Relay 任务看板：安装与连接说明

本插件只包含"声明层"（skill + mcp 配置）。**完整体验（MCP 工具 + 看板 UI + always-on 规则）需要再执行一次仓库的 install.sh**，全自动、无需人工介入，可直接交给 agent 执行。

## 一条命令装齐全部（推荐）

要求 Python >= 3.10（或已安装 [uv](https://github.com/astral-sh/uv)）：

```bash
git clone https://code.alibaba-inc.com/lg-qingxingzhou/kanban-system.git && cd kanban-system && ./install.sh
```

install.sh 自动完成：建 venv → 装依赖 → 部署 skill + 三条 always-on rules 到 ~/.qoder/ → 初始化 ~/.kanban/ → **把 kanban MCP 自动写入 ~/.qoder/mcp.json**（已有内容则合并，先备份；无需手动填路径）。

可选：`./install.sh --launchd` 额外把看板 Web UI 注册为 macOS 开机常驻服务。

## 装完验证（agent 可逐条自检）

1. `cat ~/.qoder/mcp.json` → 含 `"kanban"` 条目，command 指向仓库 `.venv/bin/python`
2. `ls ~/.qoder/rules/` → 含 kanban-boot.md / kanban-capture.md / kanban-writeback.md
3. 新对话输入 `/kanban` → 可调 MCP 工具：`add_task` / `list_tasks` / `get_task` / `update_status` / `append_task_log` / `edit_task` / `discard_task` / `request_decision` / `wait_for_decision`
4. MCP 启动时同进程在 7654 端口拉起看板 Web UI（http://localhost:7654）

## mcp.json 占位说明（仅手动配置时需要管）

插件内的 `mcp.json` 模板用了占位符 `<KANBAN_REPO>`——**走上面的 install.sh 会自动写入真实路径，不需要手动处理**。只有你坚持手动配置时才需要把它替换为仓库绝对路径：

```json
{
  "mcpServers": {
    "kanban": {
      "command": "/绝对路径/kanban-system/.venv/bin/python",
      "args": ["-m", "server.main"],
      "cwd": "/绝对路径/kanban-system"
    }
  }
}
```

## 可选能力

- **钉钉决策升级**：无人执行遇决策点时发钉钉单聊。需自行创建钉钉企业内部应用（Stream 模式机器人），将 AppKey / AppSecret / userId 写入 `~/.kanban/config.json`（勿提交到任何 git 仓库），并安装 `dingtalk-stream requests`。详见仓库 README。
- **定时无人执行**：在看板 UI 的“⏰ 定时”表单配置，落盘 `~/.kanban/schedule.json`；首次点火需经 Qoder 定时任务入口手动创建（skill 的开场对账会给出指引）。

## 数据与隐私

- 所有任务数据存本地 `~/.kanban/`，无任何远程上报。
- `~/.kanban/config.json`（钉钉凭据）由用户自管，插件与仓库均不包含凭据。
