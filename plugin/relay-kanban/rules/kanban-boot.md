---
alwaysApply: true
---

每次新对话的首轮回复前，先读 ~/.kanban/BOARD.md（读不到时可用 kanban MCP 的 list_tasks 或 GET http://localhost:7654/api/tasks 代替）：若存在 todo/review/waiting_decision 任务，在回答正题前先一句话简报"当前 N 条待办、M 条待验收，要处理吗？"；看板为空则完全静默，不提看板。简报后若用户要处理，或需要执行定时对齐与补录扫描等完整开场对账流程，加载 kanban skill 执行。本规则仅在对话首轮生效，后续轮次不重复简报。
