# Relay 任务看板：安装与连接说明

本插件包含 skill（/kanban 工作流）与三条 always-on 规则，装完即生效；但 **kanban MCP 服务是本地 stdio Python 进程，需要额外一次本地安装**，否则 skill 会自动降级到 HTTP API / 直接文件读写。

## 1. 安装 MCP 服务端（一次性）

要求 Python >= 3.10（或已安装 [uv](https://github.com/astral-sh/uv)）：

```bash
git clone https://code.alibaba-inc.com/lg-qingxingzhou/kanban-system.git && cd kanban-system
./install.sh            # 建 venv + 装依赖 + 初始化 ~/.kanban/
./install.sh --launchd  # 可选：把 Web UI 注册为 macOS 开机常驻服务
```

## 2. 填写 mcp.json 真实路径

插件内 `mcp.json` 使用了占位符 `<KANBAN_REPO>`，需替换为你机器上 kanban-system 仓库的**绝对路径**（`install.sh` 结尾会打印带真实路径的完整配置）：

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

注册后可用工具：`add_task` / `list_tasks` / `get_task` / `update_status` / `append_task_log` / `edit_task` / `discard_task` / `request_decision` / `wait_for_decision`。MCP 启动时会同进程在 7654 端口拉起看板 Web UI（http://localhost:7654）。

## 3. 可选能力

- **钉钉决策升级**：无人执行遇决策点时发钉钉单聊。需自行创建钉钉企业内部应用（Stream 模式机器人），将 AppKey / AppSecret / userId 写入 `~/.kanban/config.json`（勿提交到任何 git 仓库），并安装 `dingtalk-stream requests`。详见仓库 README。
- **定时无人执行**：在看板 UI 的“⏰ 定时”表单配置，落盘 `~/.kanban/schedule.json`；首次点火需经 Qoder 定时任务入口手动创建（skill 的开场对账会给出指引）。

## 数据与隐私

- 所有任务数据存本地 `~/.kanban/`，无任何远程上报。
- `~/.kanban/config.json`（钉钉凭据）由用户自管，插件与仓库均不包含凭据。
