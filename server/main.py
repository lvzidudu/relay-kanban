"""入口：注册 MCP 工具（stdio） + 后台线程起 HTTP 服务（看板 UI + REST API）。

运行方式：
    python -m server.main            # stdio MCP + localhost:7654 Web UI
    python -m server.main --web-only # 仅起 Web UI（调试用）
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import threading
import time
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from . import dingtalk, embedding, storage
from .state_machine import TransitionError

WEB_DIR = Path(__file__).parent / "web"
HTTP_PORT = 7654

mcp = FastMCP("kanban")


# ---------- MCP 工具 ----------

@mcp.tool()
def add_task(title: str, description: str, status: str = "todo",
             priority: str = "normal", unattended: bool = False,
             workspace: str = "", files: list[str] | None = None,
             tags: list[str] | None = None) -> str:
    """新建看板任务。status 仅允许 backlog/todo；返回任务 ID。
    files 传该任务涉及的代码文件路径列表（跨对话检索的关键索引）。"""
    task_id = storage.add_task(title, description, status=status, priority=priority,
                               unattended=unattended, workspace=workspace or None,
                               files=files, tags=tags)
    return json.dumps({"task_id": task_id}, ensure_ascii=False)


@mcp.tool()
def list_tasks(status: str = "", keyword: str = "", file_path: str = "",
               include_archived: bool = False) -> str:
    """列出任务摘要。可按 status 过滤；keyword 匹配标题/标签/正文；
    file_path 按涉及文件反查任务（二次修改场景先用它定位历史任务）；
    include_archived=True 时连带已归档任务（done/discarded）一起返回。"""
    tasks = storage.list_tasks(status=status or None, keyword=keyword or None,
                               file_path=file_path or None,
                               include_archived=include_archived)
    return json.dumps(tasks, ensure_ascii=False)


def _search_enriched(query: str, limit: int, include_archived: bool = False) -> list[dict]:
    """语义检索并补全任务摘要字段；索引中残留的孤儿任务静默跳过。"""
    enriched = []
    for r in embedding.semantic_search(query, limit=limit,
                                       include_archived=include_archived):
        try:
            task = storage.get_task(r["id"])
        except storage.TaskNotFound:
            continue
        enriched.append({**r, "title": task["title"], "status": task["status"],
                         "priority": task["priority"], "tags": task["tags"]})
    return enriched


@mcp.tool()
def search_tasks(query: str, limit: int = 5, include_archived: bool = False) -> str:
    """语义搜索任务：用自然语言描述你要找的任务，返回最相关的结果。
    适用于回忆历史任务、用词不确定的场景；include_archived=True 时连带
    已归档任务一起搜（找历史决策/二次修改场景建议开启）。
    精确搜索请用 list_tasks 的 keyword 参数。"""
    results = _search_enriched(query, limit, include_archived=include_archived)
    if not results:
        return json.dumps({"results": [], "hint": "语义索引不可用或未安装 sentence-transformers，"
                                                 "请回退到 list_tasks 的 keyword 精确搜索"},
                          ensure_ascii=False)
    return json.dumps({"results": results}, ensure_ascii=False)


@mcp.tool()
def get_task(task_id: str) -> str:
    """读取任务完整内容（含时间线全文），执行任务前必须调用以恢复上下文。"""
    return json.dumps(storage.get_task(task_id), ensure_ascii=False)


@mcp.tool()
def update_status(task_id: str, new_status: str, note: str = "") -> str:
    """状态流转（内置状态机校验）。review 退回 todo 时 note（验收反馈）必填；
    转 done 自动归档。转 waiting_decision 请改用 request_decision。"""
    try:
        result = storage.update_status(task_id, new_status, note=note or None)
    except (TransitionError, storage.TaskNotFound, ValueError) as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def append_task_log(task_id: str, entry: str, source: str = "对话") -> str:
    """向任务时间线追加一条记录（markdown 片段：决策/改动/遗留）。
    对话中涉及某任务的关键决策或改动后应当场调用。"""
    try:
        result = storage.append_task_log(task_id, entry, source=source)
    except storage.TaskNotFound as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def edit_task(task_id: str, title: str = "", description: str = "",
              priority: str = "", unattended: bool | None = None,
              workspace: str | None = None, files: list[str] | None = None,
              tags: list[str] | None = None) -> str:
    """编辑任务元信息与任务描述（空值表示不修改）。时间线只追加不可改，
    状态流转请用 update_status。"""
    try:
        result = storage.edit_task(
            task_id, title=title or None, description=description or None,
            priority=priority or None, unattended=unattended,
            workspace=workspace, files=files, tags=tags)
    except (storage.TaskNotFound, ValueError) as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def discard_task(task_id: str, reason: str) -> str:
    """废弃任务（软删除）：原因写入时间线后移入归档。不经过 review 验收，
    用于不再需要的任务；reason 必填。完成验收请走 update_status 到 done。"""
    try:
        result = storage.discard_task(task_id, reason)
    except (storage.TaskNotFound, ValueError) as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def request_decision(task_id: str, question: str, options: list[str] | None = None) -> str:
    """无人执行遇决策点时调用：任务转 waiting_decision 并发钉钉单聊消息请求用户决策。
    需要 ~/.kanban/config.json 已配置钉钉凭证。"""
    pq = {"question": question, "options": options or []}
    try:
        storage.update_status(task_id, "waiting_decision", pending_question=pq)
    except (TransitionError, storage.TaskNotFound) as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    sent = dingtalk.send_decision_card(task_id, question, options or [])
    return json.dumps({"id": task_id, "status": "waiting_decision",
                       "dingtalk_sent": sent}, ensure_ascii=False)


@mcp.tool()
def wait_for_decision(task_id: str, timeout_sec: int = 600) -> str:
    """同步快路径：阻塞等待用户在钉钉上的决策，超时自动降级为异步（保持 waiting_decision）。"""
    decision = dingtalk.wait_for_decision(task_id, timeout_sec)
    if decision is None:
        return json.dumps({"id": task_id, "result": "timeout",
                           "hint": "已降级为异步，用户回复后任务将自动退回 todo"},
                          ensure_ascii=False)
    return json.dumps({"id": task_id, "result": "decided", "decision": decision},
                      ensure_ascii=False)


# ---------- HTTP API（与 MCP 工具共用 storage/state_machine） ----------

async def page_index(request: Request):
    return FileResponse(WEB_DIR / "index.html")


async def api_list_tasks(request: Request):
    q = request.query_params
    tasks = storage.list_tasks(status=q.get("status"), keyword=q.get("keyword"),
                               file_path=q.get("file_path"),
                               include_archived=q.get("include_archived") in ("1", "true"))
    return JSONResponse(tasks)


async def api_search_tasks(request: Request):
    q = request.query_params
    query = (q.get("query") or "").strip()
    if not query:
        return JSONResponse({"error": "query 必填"}, status_code=400)
    try:
        limit = int(q.get("limit", 5))
    except ValueError:
        return JSONResponse({"error": "limit 需为整数"}, status_code=400)
    results = _search_enriched(query, limit,
                               include_archived=q.get("include_archived") in ("1", "true"))
    if not results:
        return JSONResponse({"results": [], "hint": "语义索引不可用或未安装 sentence-transformers"})
    return JSONResponse({"results": results})


async def api_get_task(request: Request):
    try:
        return JSONResponse(storage.get_task(request.path_params["task_id"]))
    except storage.TaskNotFound as e:
        return JSONResponse({"error": str(e)}, status_code=404)


async def api_create_task(request: Request):
    body = await request.json()
    try:
        task_id = storage.add_task(
            body["title"], body.get("description", ""),
            status=body.get("status", "todo"), priority=body.get("priority", "normal"),
            unattended=bool(body.get("unattended", False)),
            workspace=body.get("workspace") or None,
            files=body.get("files") or None, tags=body.get("tags") or None)
    except (ValueError, KeyError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"task_id": task_id}, status_code=201)


async def api_patch_task(request: Request):
    body = await request.json()
    if "status" not in body:
        return JSONResponse({"error": "仅支持状态流转，需提供 status"}, status_code=400)
    try:
        result = storage.update_status(request.path_params["task_id"], body["status"],
                                       note=body.get("note"))
    except storage.TaskNotFound as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except TransitionError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return JSONResponse(result)


async def api_edit_task(request: Request):
    body = await request.json()
    try:
        result = storage.edit_task(
            request.path_params["task_id"],
            title=body.get("title"), description=body.get("description"),
            priority=body.get("priority"), unattended=body.get("unattended"),
            workspace=body.get("workspace"), files=body.get("files"),
            tags=body.get("tags"))
    except storage.TaskNotFound as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(result)


async def api_discard_task(request: Request):
    body = await request.json()
    try:
        result = storage.discard_task(request.path_params["task_id"], body.get("reason", ""))
    except storage.TaskNotFound as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(result)


async def api_request_decision(request: Request):
    """HTTP 降级路径：等价于 MCP request_decision 工具。
    任务转 waiting_decision + 发钉钉决策消息。"""
    body = await request.json()
    task_id = request.path_params["task_id"]
    question = body.get("question", "")
    options = body.get("options") or []
    if not question:
        return JSONResponse({"error": "question 必填"}, status_code=400)
    pq = {"question": question, "options": options}
    try:
        storage.update_status(task_id, "waiting_decision", pending_question=pq)
    except storage.TaskNotFound as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except TransitionError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    sent = dingtalk.send_decision_card(task_id, question, options)
    return JSONResponse({"id": task_id, "status": "waiting_decision",
                         "dingtalk_sent": sent})


async def api_append_log(request: Request):
    body = await request.json()
    if not body.get("entry"):
        return JSONResponse({"error": "entry 必填"}, status_code=400)
    try:
        result = storage.append_task_log(request.path_params["task_id"],
                                         body["entry"], source=body.get("source", "UI"))
    except storage.TaskNotFound as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return JSONResponse(result)


async def api_get_schedule(request: Request):
    return JSONResponse(storage.get_schedule())


async def api_put_schedule(request: Request):
    body = await request.json()
    try:
        cfg = storage.save_schedule(bool(body.get("enabled", False)),
                                    body.get("time", ""),
                                    body.get("max_per_run", 0))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(cfg)


app = Starlette(routes=[
    Route("/", page_index),
    Route("/api/tasks", api_list_tasks, methods=["GET"]),
    Route("/api/tasks", api_create_task, methods=["POST"]),
    Route("/api/search", api_search_tasks, methods=["GET"]),
    Route("/api/schedule", api_get_schedule, methods=["GET"]),
    Route("/api/schedule", api_put_schedule, methods=["PUT"]),
    Route("/api/tasks/{task_id}", api_get_task, methods=["GET"]),
    Route("/api/tasks/{task_id}", api_patch_task, methods=["PATCH"]),
    Route("/api/tasks/{task_id}", api_edit_task, methods=["PUT"]),
    Route("/api/tasks/{task_id}/decision", api_request_decision, methods=["POST"]),
    Route("/api/tasks/{task_id}/discard", api_discard_task, methods=["POST"]),
    Route("/api/tasks/{task_id}/log", api_append_log, methods=["POST"]),
])


# ---------- 定时兜底（可选）：到点未见定时链触发时发钉钉提醒 ----------

WATCHDOG_GRACE_MIN = 30


def _schedule_watchdog() -> None:
    """每分钟检查：配置启用且超过执行时刻宽限期后，若存在 unattended 待办
    但任务文件自执行时刻起无任何写入（定时链疑似断链），发钉钉提醒；每天至多一次。"""
    while True:
        try:
            cfg = storage.get_schedule()
            if cfg.get("enabled") and dingtalk.enabled():
                now = dt.datetime.now()
                hh, mm = map(int, str(cfg["time"]).split(":"))
                due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                today = now.date().isoformat()
                if (now >= due + dt.timedelta(minutes=WATCHDOG_GRACE_MIN)
                        and cfg.get("last_reminded") != today
                        and storage.has_unattended_todo()
                        and not storage.tasks_touched_since(due)):
                    sent = dingtalk.send_markdown(
                        "看板定时任务疑似未触发",
                        (f"### 看板定时任务疑似未触发\n\n"
                         f"配置的执行时刻 {cfg['time']} 已过 {WATCHDOG_GRACE_MIN} 分钟，"
                         f"todo 列仍有 unattended 任务且无执行痕迹。\n\n"
                         f"请在对话中打开新会话（开场对账会自动补排），"
                         f"或手动执行 `/kanban run`。"))
                    if sent:
                        storage.mark_schedule_reminded(today)
        except Exception:
            pass  # 兜底线程不影响主服务
        time.sleep(60)


def start_watchdog() -> None:
    threading.Thread(target=_schedule_watchdog, daemon=True, name="kanban-watchdog").start()


def _run_web_with_retry() -> None:
    # 端口可能被残留调试进程短暂占用，绑定失败时重试而非直接退出
    for _ in range(30):
        config = uvicorn.Config(app, host="127.0.0.1", port=HTTP_PORT, log_level="warning")
        server = uvicorn.Server(config)
        try:
            server.run()
            return
        except SystemExit:
            pass
        time.sleep(2)


def start_web(background: bool) -> None:
    if background:
        threading.Thread(target=_run_web_with_retry, daemon=True, name="kanban-web").start()
    else:
        config = uvicorn.Config(app, host="127.0.0.1", port=HTTP_PORT, log_level="warning")
        uvicorn.Server(config).run()


def main() -> None:
    storage.ensure_dirs()
    storage.rebuild_board()
    embedding.ensure_index()  # 索引缺失/模型不匹配时重建；未装 embed 依赖时 no-op
    dingtalk.start_stream_client()  # 无 config.json 时静默跳过
    start_watchdog()  # 定时兜底提醒，未启用定时/未配钉钉时空转
    if "--web-only" in sys.argv:
        start_web(background=False)
    else:
        start_web(background=True)
        mcp.run()  # stdio，阻塞


if __name__ == "__main__":
    main()
