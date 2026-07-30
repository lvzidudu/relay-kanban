---
alwaysApply: true
---

对话中修改了代码或做出技术决策时，结束前检查 ~/.kanban/tasks 是否有 files/主题关联的任务（可用 kanban MCP 的 list_tasks 按 file_path 或 keyword 反查），有则将本轮决策与改动摘要通过 append_task_log 追加进该任务时间线。若本轮改动已实质完成该任务的目标，回写后询问用户是否将其流转至 review。
