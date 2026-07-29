"""存储层：任务文件读写、ID 分配、BOARD.md 生成、归档。

任务文件 = frontmatter(YAML) + 正文（## 任务描述 / ## 时间线）。
不引入 python-frontmatter 之外的重依赖；文件即数据库，BOARD.md 每次变更后全量重建。
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import frontmatter

from . import state_machine

KANBAN_DIR = Path.home() / ".kanban"
TASKS_DIR = KANBAN_DIR / "tasks"
ARCHIVE_DIR = KANBAN_DIR / "archive"
BOARD_FILE = KANBAN_DIR / "BOARD.md"
COUNTER_FILE = KANBAN_DIR / "counter.txt"
SCHEDULE_FILE = KANBAN_DIR / "schedule.json"

SCHEDULE_DEFAULTS = {"enabled": False, "time": "10:00", "max_per_run": 3}

BOARD_COLUMNS = ["backlog", "todo", "doing", "review", "waiting_decision"]
COLUMN_LABELS = {
    "backlog": "backlog（积压）",
    "todo": "todo（待办）",
    "doing": "doing（进行中）",
    "review": "review（审核中）",
    "waiting_decision": "waiting_decision（等待决策）",
}
PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}


class TaskNotFound(Exception):
    pass


def ensure_dirs() -> None:
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    if not COUNTER_FILE.exists():
        COUNTER_FILE.write_text("1\n")


def _next_id() -> str:
    n = int(COUNTER_FILE.read_text().strip() or "1")
    COUNTER_FILE.write_text(f"{n + 1}\n")
    return f"T{n:03d}"


def _safe_title(title: str) -> str:
    # 文件名片段：去掉路径分隔符等危险字符，截断
    cleaned = re.sub(r"[/\\:*?\"<>|\s]+", "-", title.strip()).strip("-")
    return cleaned[:40] or "untitled"


def _task_path(task_id: str) -> Path:
    matches = list(TASKS_DIR.glob(f"{task_id}-*.md"))
    if not matches:
        raise TaskNotFound(f"任务不存在: {task_id}")
    return matches[0]


def _load(path: Path) -> frontmatter.Post:
    return frontmatter.load(str(path))


def _dump(post: frontmatter.Post, path: Path) -> None:
    path.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")


def _now_date() -> str:
    return dt.date.today().isoformat()


def _now_stamp() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


# ---------- 查询 ----------

def list_tasks(status: str | None = None, keyword: str | None = None,
               file_path: str | None = None) -> list[dict]:
    """返回任务摘要列表，按 (优先级, 创建日期) 排序。"""
    ensure_dirs()
    result = []
    for p in sorted(TASKS_DIR.glob("T*-*.md")):
        post = _load(p)
        meta = post.metadata
        if status and meta.get("status") != status:
            continue
        if file_path:
            files = [str(f) for f in (meta.get("files") or [])]
            if not any(f.endswith(file_path) or file_path.endswith(f) for f in files):
                continue
        if keyword:
            hay = " ".join([
                str(meta.get("title", "")),
                " ".join(str(t) for t in (meta.get("tags") or [])),
                post.content,
            ]).lower()
            if keyword.lower() not in hay:
                continue
        result.append({
            "id": meta.get("id"),
            "title": meta.get("title"),
            "status": meta.get("status"),
            "priority": meta.get("priority", "normal"),
            "unattended": bool(meta.get("unattended", False)),
            "workspace": meta.get("workspace"),
            "files": meta.get("files") or [],
            "tags": meta.get("tags") or [],
            "created": str(meta.get("created", "")),
            "updated": str(meta.get("updated", "")),
            "content": post.content,
        })
    result.sort(key=lambda t: (PRIORITY_ORDER.get(t["priority"], 1), t["created"], t["id"]))
    return result


def get_task(task_id: str) -> dict:
    """返回完整任务：摘要字段 + 全文内容。"""
    path = _task_path(task_id)
    post = _load(path)
    meta = post.metadata
    return {
        "id": meta.get("id"),
        "title": meta.get("title"),
        "status": meta.get("status"),
        "priority": meta.get("priority", "normal"),
        "unattended": bool(meta.get("unattended", False)),
        "workspace": meta.get("workspace"),
        "files": meta.get("files") or [],
        "tags": meta.get("tags") or [],
        "pending_question": meta.get("pending_question"),
        "created": str(meta.get("created", "")),
        "updated": str(meta.get("updated", "")),
        "content": post.content,
    }


# ---------- 写入 ----------

def add_task(title: str, description: str, status: str = "todo",
             priority: str = "normal", unattended: bool = False,
             workspace: str | None = None, files: list[str] | None = None,
             tags: list[str] | None = None) -> str:
    if status not in ("backlog", "todo"):
        raise ValueError("新任务 status 仅允许 backlog 或 todo")
    if priority not in PRIORITY_ORDER:
        raise ValueError(f"非法 priority: {priority}")
    ensure_dirs()
    task_id = _next_id()
    meta = {
        "id": task_id,
        "title": title,
        "status": status,
        "priority": priority,
        "unattended": unattended,
        "created": _now_date(),
        "updated": _now_date(),
    }
    if workspace:
        meta["workspace"] = workspace
    if files:
        meta["files"] = files
    if tags:
        meta["tags"] = tags
    content = f"## 任务描述\n\n{description.strip()}\n\n## 时间线\n"
    post = frontmatter.Post(content, **meta)
    _dump(post, TASKS_DIR / f"{task_id}-{_safe_title(title)}.md")
    rebuild_board()
    return task_id


def update_status(task_id: str, new_status: str, note: str | None = None,
                  pending_question: dict | None = None) -> dict:
    path = _task_path(task_id)
    post = _load(path)
    current = post.metadata.get("status")
    state_machine.validate_transition(current, new_status, note=note,
                                      pending_question=pending_question)
    post.metadata["status"] = new_status
    post.metadata["updated"] = _now_date()
    if new_status == "waiting_decision":
        pq = dict(pending_question)
        pq.setdefault("sent_at", _now_stamp())
        post.metadata["pending_question"] = pq
    elif current == "waiting_decision":
        post.metadata.pop("pending_question", None)
    if note and note.strip():
        post.content = _append_timeline(
            post.content, f"状态流转 {current} -> {new_status}", [f"- 备注：{note.strip()}"])
    if new_status == "done":
        month_dir = ARCHIVE_DIR / dt.date.today().strftime("%Y-%m")
        month_dir.mkdir(parents=True, exist_ok=True)
        _dump(post, month_dir / path.name)
        path.unlink()
    else:
        _dump(post, path)
    rebuild_board()
    return {"id": task_id, "status": new_status}


def append_task_log(task_id: str, entry: str, source: str = "对话") -> dict:
    path = _task_path(task_id)
    post = _load(path)
    post.content = _append_timeline(post.content, source, [entry.strip()])
    post.metadata["updated"] = _now_date()
    _dump(post, path)
    return {"id": task_id, "appended": True}


def edit_task(task_id: str, title: str | None = None, description: str | None = None,
              priority: str | None = None, unattended: bool | None = None,
              workspace: str | None = None, files: list[str] | None = None,
              tags: list[str] | None = None) -> dict:
    """编辑任务元信息与任务描述。时间线只追加不可改，status 走 update_status。"""
    path = _task_path(task_id)
    post = _load(path)
    if priority is not None:
        if priority not in PRIORITY_ORDER:
            raise ValueError(f"非法 priority: {priority}")
        post.metadata["priority"] = priority
    if title is not None and title.strip():
        post.metadata["title"] = title.strip()
    if unattended is not None:
        post.metadata["unattended"] = bool(unattended)
    if workspace is not None:
        if workspace.strip():
            post.metadata["workspace"] = workspace.strip()
        else:
            post.metadata.pop("workspace", None)
    if files is not None:
        post.metadata["files"] = files
    if tags is not None:
        post.metadata["tags"] = tags
    if description is not None:
        # 只替换「## 任务描述」分区，时间线原样保留
        idx = post.content.find("## 时间线")
        timeline = post.content[idx:] if idx >= 0 else "## 时间线\n"
        post.content = f"## 任务描述\n\n{description.strip()}\n\n{timeline}"
    post.metadata["updated"] = _now_date()
    # 标题变更时同步重命名文件，保持文件名可读
    new_path = TASKS_DIR / f"{task_id}-{_safe_title(post.metadata['title'])}.md"
    if new_path != path:
        path.unlink()
    _dump(post, new_path)
    rebuild_board()
    return {"id": task_id, "updated": True}


def discard_task(task_id: str, reason: str) -> dict:
    """废弃任务（软删除）：废弃原因写入时间线后移入归档，保留可追溯性。
    与 done 不同：不要求经过 review 验收，任意活跃状态可废弃，但必须给原因。"""
    if not (reason and reason.strip()):
        raise ValueError("废弃任务必须提供原因（reason）")
    path = _task_path(task_id)
    post = _load(path)
    post.content = _append_timeline(post.content, "废弃", [f"- 废弃原因：{reason.strip()}"])
    post.metadata["status"] = "discarded"
    post.metadata["updated"] = _now_date()
    month_dir = ARCHIVE_DIR / dt.date.today().strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    _dump(post, month_dir / path.name)
    path.unlink()
    rebuild_board()
    return {"id": task_id, "status": "discarded"}


def _append_timeline(content: str, source: str, lines: list[str]) -> str:
    block = f"\n### {_now_stamp()} {source}\n\n" + "\n".join(lines) + "\n"
    return content.rstrip() + "\n" + block


# ---------- 定时配置（UI 意图层，对话开场对账消费） ----------

def get_schedule() -> dict:
    """读取定时配置，缺失字段用默认值补齐；文件不存在/损坏时返回默认配置。"""
    cfg = dict(SCHEDULE_DEFAULTS)
    if SCHEDULE_FILE.exists():
        try:
            data = json.loads(SCHEDULE_FILE.read_text())
            if isinstance(data, dict):
                cfg.update(data)
        except json.JSONDecodeError:
            pass
    return cfg


def save_schedule(enabled: bool, time_str: str, max_per_run: int) -> dict:
    """校验并落盘定时配置。time 为 HH:MM，max_per_run 限 1-10。"""
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time_str or ""):
        raise ValueError(f"非法执行时刻（需 HH:MM）: {time_str}")
    if not isinstance(max_per_run, int) or not 1 <= max_per_run <= 10:
        raise ValueError(f"单轮上限需为 1-10 的整数: {max_per_run}")
    ensure_dirs()
    cfg = get_schedule()  # 保留 last_reminded 等附加字段
    cfg.update({"enabled": bool(enabled), "time": time_str,
                "max_per_run": max_per_run, "updated": _now_stamp()})
    SCHEDULE_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    return cfg


def mark_schedule_reminded(date_str: str) -> None:
    """记录兜底提醒已发送日期，避免同一天重复提醒。"""
    cfg = get_schedule()
    cfg["last_reminded"] = date_str
    SCHEDULE_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")


def has_unattended_todo() -> bool:
    return any(t["unattended"] for t in list_tasks(status="todo"))


def tasks_touched_since(ts: dt.datetime) -> bool:
    """判断 ts 之后是否有任务文件被写过（含归档），作为定时链已触发的证据。"""
    ensure_dirs()
    paths = list(TASKS_DIR.glob("T*-*.md")) + list(ARCHIVE_DIR.glob("*/T*-*.md"))
    return any(p.stat().st_mtime >= ts.timestamp() for p in paths)


# ---------- BOARD.md ----------

def rebuild_board() -> None:
    ensure_dirs()
    by_status: dict[str, list[dict]] = {c: [] for c in BOARD_COLUMNS}
    for t in list_tasks():
        if t["status"] in by_status:
            by_status[t["status"]].append(t)
    lines = [
        "# 任务看板",
        "",
        "> 本文件为索引，由写入方（agent 或 kanban MCP）在每次状态变更后重新生成。请勿手工编辑。",
        f"> 最后更新：{_now_stamp()}",
    ]
    for col in BOARD_COLUMNS:
        lines += ["", f"## {COLUMN_LABELS[col]}", ""]
        tasks = by_status[col]
        if not tasks:
            lines.append("（空）")
        for t in tasks:
            flags = []
            if t["priority"] != "normal":
                flags.append(t["priority"])
            if t["unattended"]:
                flags.append("unattended")
            suffix = f"（{', '.join(flags)}）" if flags else ""
            lines.append(f"- {t['id']} {t['title']}{suffix}")
    BOARD_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
