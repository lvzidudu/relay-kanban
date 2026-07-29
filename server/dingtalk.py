"""钉钉决策升级（P3）：Stream 客户端 + 单聊卡片发送 + 回复写回。

依赖 ~/.kanban/config.json：{"appKey": "...", "appSecret": "...", "userId": "..."}
未配置时所有函数静默降级（不影响 MVP/P1/P2 功能）。

Stream 模式：本地进程主动向钉钉建立长连接接收用户回复，无需公网回调地址。
"""
from __future__ import annotations

import json
import re
import threading
import time

from . import storage
from .state_machine import TransitionError

CONFIG_FILE = storage.KANBAN_DIR / "config.json"

# task_id -> 决策结果，Stream 回调线程写入，wait_for_decision 轮询读取
_decisions: dict[str, str] = {}
_lock = threading.Lock()


def _load_config() -> dict | None:
    if not CONFIG_FILE.exists():
        return None
    try:
        cfg = json.loads(CONFIG_FILE.read_text())
    except json.JSONDecodeError:
        return None
    if all(cfg.get(k) for k in ("appKey", "appSecret", "userId")):
        return cfg
    return None


def enabled() -> bool:
    return _load_config() is not None


def send_markdown(title: str, text: str) -> bool:
    """发送单聊 markdown 消息（决策卡片/兜底提醒共用）。未配置钉钉时返回 False。"""
    cfg = _load_config()
    if cfg is None:
        return False
    try:
        import requests

        # 1. 获取 access token（v1 接口，个人企业内部应用可用）
        resp = requests.get("https://oapi.dingtalk.com/gettoken",
                            params={"appkey": cfg["appKey"], "appsecret": cfg["appSecret"]},
                            timeout=10)
        token = resp.json().get("access_token")
        if not token:
            return False

        # 2. 机器人单聊消息（sampleMarkdown，用户直接回复文本即可）
        body = {
            "robotCode": cfg["appKey"],
            "userIds": [cfg["userId"]],
            "msgKey": "sampleMarkdown",
            "msgParam": json.dumps({"title": title, "text": text}, ensure_ascii=False),
        }
        resp = requests.post(
            "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend",
            headers={"x-acs-dingtalk-access-token": token}, json=body, timeout=10)
        return resp.status_code == 200
    except Exception:
        return False


def send_decision_card(task_id: str, question: str, options: list[str]) -> bool:
    """发送单聊决策消息。成功返回 True；未配置钉钉时返回 False（任务仍留在 waiting_decision，
    等待用户在对话或 UI 中人工决策）。"""
    opts = "\n".join(f"- {o}" for o in options) if options else "（自由回复）"
    return send_markdown(
        f"任务 {task_id} 需要你的决策",
        (f"### 任务 {task_id} 需要你的决策\n\n{question}\n\n"
         f"**选项：**\n{opts}\n\n"
         f"请直接回复：`{task_id} 你的决策`"))


def wait_for_decision(task_id: str, timeout_sec: int = 600) -> str | None:
    """阻塞轮询决策结果，超时返回 None（降级为异步）。"""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        with _lock:
            if task_id in _decisions:
                return _decisions.pop(task_id)
        time.sleep(2)
    return None


def _handle_reply(text: str) -> str:
    """解析用户回复 → 写回时间线 → 任务退回 todo。返回回复给用户的提示文本。

    容错：任务 ID 宽松匹配（大小写/无前导零/带分隔符，如 t1、T 001、T001：）；
    未带 ID 且仅一个任务在等决策时自动匹配；任何失败均回复引导而非静默丢弃。"""
    text = text.strip()
    if not text:
        return ""
    m = re.match(r"^[Tt][\s-]*0*(\d+)[\s：:，,]*", text)
    if m:
        task_id = f"T{int(m.group(1)):03d}"
        decision = text[m.end():].strip()
        if not decision:
            return f"已识别任务 {task_id}，但缺少决策内容，请回复：{task_id} 你的决策"
    else:
        waiting = storage.list_tasks(status="waiting_decision")
        if not waiting:
            return "当前没有等待决策的任务，本条回复未处理。"
        if len(waiting) > 1:
            ids = "、".join(t["id"] for t in waiting)
            return (f"有多个任务在等待决策（{ids}），请带上任务 ID 回复，"
                    f"如：{waiting[0]['id']} 你的决策")
        task_id, decision = waiting[0]["id"], text
    try:
        storage.append_task_log(task_id, f"- 决策（钉钉回复）：{decision}", source="钉钉")
        storage.update_status(task_id, "todo")
    except storage.TaskNotFound:
        return f"任务 {task_id} 不存在或已归档，请检查 ID 后重新回复。"
    except TransitionError:
        return (f"任务 {task_id} 当前不在等待决策状态，决策已记入时间线但未流转，"
                f"请在看板或对话中处理。")
    with _lock:
        _decisions[task_id] = decision
    return f"已收到决策，任务 {task_id} 已退回待办队列。"


def start_stream_client() -> None:
    """启动 Stream 长连接后台线程。未配置钉钉或未安装 dingtalk-stream 时静默跳过。"""
    cfg = _load_config()
    if cfg is None:
        return
    try:
        import dingtalk_stream
    except ImportError:
        return

    class _Handler(dingtalk_stream.ChatbotHandler):
        async def process(self, callback):
            msg = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
            text = (msg.text.content or "").strip() if msg.text else ""
            if text:
                reply = _handle_reply(text)
                if reply:
                    self.reply_text(reply, msg)
            return dingtalk_stream.AckMessage.STATUS_OK, "OK"

    def _run():
        credential = dingtalk_stream.Credential(cfg["appKey"], cfg["appSecret"])
        client = dingtalk_stream.DingTalkStreamClient(credential)
        client.register_callback_handler(
            dingtalk_stream.chatbot.ChatbotMessage.TOPIC, _Handler())
        client.start_forever()

    threading.Thread(target=_run, daemon=True, name="kanban-dingtalk").start()
