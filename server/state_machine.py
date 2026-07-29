"""状态机：流转规则的单一实现，MCP 工具与 HTTP API 共用。"""

VALID_STATUSES = {"backlog", "todo", "doing", "review", "waiting_decision", "done"}

# 流转表：当前状态 -> 允许流转到的状态集合
TRANSITIONS = {
    "backlog": {"todo"},
    "todo": {"doing", "backlog"},
    "doing": {"review", "todo", "waiting_decision"},
    "review": {"done", "todo"},
    "waiting_decision": {"todo"},
    "done": set(),  # 终态
}


class TransitionError(Exception):
    """非法流转或不满足附加条件。"""


def validate_transition(current: str, new: str, note: str | None = None,
                        pending_question: dict | None = None) -> None:
    """校验一次流转，非法则抛 TransitionError。

    附加条件：
    - review -> todo：note（验收反馈）必填
    - doing -> waiting_decision：pending_question 必填
    """
    if current not in VALID_STATUSES:
        raise TransitionError(f"未知的当前状态: {current}")
    if new not in VALID_STATUSES:
        raise TransitionError(f"未知的目标状态: {new}")
    if new == current:
        raise TransitionError(f"状态未变化: {current}")
    if new not in TRANSITIONS[current]:
        raise TransitionError(f"非法流转: {current} -> {new}")
    if current == "review" and new == "todo" and not (note and note.strip()):
        raise TransitionError("review 退回 todo 必须附验收反馈（note）")
    if new == "waiting_decision" and not pending_question:
        raise TransitionError("转 waiting_decision 必须提供 pending_question")
