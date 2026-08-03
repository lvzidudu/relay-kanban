"""状态机与存储层单元测试。存储测试通过 monkeypatch 重定向数据目录到 tmp_path。"""
import json

import pytest

from server import dingtalk, embedding, state_machine, storage
from server.state_machine import TransitionError, validate_transition


# ---------- 状态机：流转表逐格覆盖 ----------

ALL = ["backlog", "todo", "doing", "review", "waiting_decision", "done"]
LEGAL = {
    ("backlog", "todo"),
    ("todo", "doing"), ("todo", "backlog"),
    ("doing", "review"), ("doing", "todo"), ("doing", "waiting_decision"),
    ("review", "done"), ("review", "todo"),
    ("waiting_decision", "todo"),
}


@pytest.mark.parametrize("cur", ALL)
@pytest.mark.parametrize("new", ALL)
def test_transition_table(cur, new):
    kwargs = {}
    if (cur, new) == ("review", "todo"):
        kwargs["note"] = "验收反馈"
    if new == "waiting_decision":
        kwargs["pending_question"] = {"question": "q"}
    if (cur, new) in LEGAL:
        validate_transition(cur, new, **kwargs)  # 不应抛出
    else:
        with pytest.raises(TransitionError):
            validate_transition(cur, new, **kwargs)


def test_review_reject_requires_note():
    with pytest.raises(TransitionError, match="验收反馈"):
        validate_transition("review", "todo")
    with pytest.raises(TransitionError, match="验收反馈"):
        validate_transition("review", "todo", note="  ")


def test_waiting_decision_requires_question():
    with pytest.raises(TransitionError, match="pending_question"):
        validate_transition("doing", "waiting_decision")


def test_unknown_status_rejected():
    with pytest.raises(TransitionError):
        validate_transition("todo", "shipped")
    with pytest.raises(TransitionError):
        validate_transition("archived", "todo")


# ---------- 存储层 ----------

@pytest.fixture(autouse=True)
def _no_real_model(monkeypatch):
    """默认禁用真实 embedding（即使环境装了 sentence-transformers），
    避免存储层测试经写入钩子触发模型加载；embed_env 会显式覆盖。"""
    monkeypatch.setattr(embedding, "_HAS_ST", False)


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "KANBAN_DIR", tmp_path)
    monkeypatch.setattr(storage, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(storage, "ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(storage, "BOARD_FILE", tmp_path / "BOARD.md")
    monkeypatch.setattr(storage, "COUNTER_FILE", tmp_path / "counter.txt")
    monkeypatch.setattr(storage, "SCHEDULE_FILE", tmp_path / "schedule.json")
    storage.ensure_dirs()
    return tmp_path


def test_add_and_get(kanban_home):
    tid = storage.add_task("重构登录", "改成 JWT\n验收：单测通过", files=["src/auth/login.ts"])
    assert tid == "T001"
    t = storage.get_task(tid)
    assert t["title"] == "重构登录"
    assert t["status"] == "todo"
    assert "## 任务描述" in t["content"] and "## 时间线" in t["content"]
    # ID 自增
    assert storage.add_task("任务二", "x") == "T002"


def test_add_rejects_bad_status(kanban_home):
    with pytest.raises(ValueError):
        storage.add_task("t", "d", status="doing")


def test_list_filters(kanban_home):
    storage.add_task("登录模块", "auth 相关", files=["src/auth/login.ts"], tags=["auth"])
    storage.add_task("支付模块", "pay 相关", status="backlog")
    assert len(storage.list_tasks()) == 2
    assert len(storage.list_tasks(status="backlog")) == 1
    # file_path 后缀匹配：二次修改场景反查
    hits = storage.list_tasks(file_path="auth/login.ts")
    assert len(hits) == 1 and hits[0]["title"] == "登录模块"
    assert len(storage.list_tasks(keyword="pay")) == 1


def test_priority_ordering(kanban_home):
    storage.add_task("低", "x", priority="low")
    storage.add_task("高", "x", priority="high")
    assert [t["title"] for t in storage.list_tasks()] == ["高", "低"]


def test_status_flow_and_archive(kanban_home):
    tid = storage.add_task("任务", "描述")
    storage.update_status(tid, "doing")
    storage.update_status(tid, "review")
    # 退回必须带 note，且写入时间线
    with pytest.raises(TransitionError):
        storage.update_status(tid, "todo")
    storage.update_status(tid, "todo", note="边界条件有 bug")
    assert "边界条件有 bug" in storage.get_task(tid)["content"]
    # 走到 done：文件归档，tasks/ 目录清空；get_task 回退到 archive/ 可读，
    # 但写操作（状态流转/追加时间线）仍拒绝
    storage.update_status(tid, "doing")
    storage.update_status(tid, "review")
    storage.update_status(tid, "done")
    assert storage.get_task(tid)["status"] == "done"
    with pytest.raises(storage.TaskNotFound):
        storage.update_status(tid, "todo", note="x")
    with pytest.raises(storage.TaskNotFound):
        storage.append_task_log(tid, "- 追加")
    archived = list((kanban_home / "archive").rglob("T001-*.md"))
    assert len(archived) == 1
    # 默认列表不含归档；include_archived=True 时可见
    assert storage.list_tasks() == []
    listed = storage.list_tasks(include_archived=True)
    assert [t["id"] for t in listed] == [tid] and listed[0]["status"] == "done"


def test_append_log(kanban_home):
    tid = storage.add_task("任务", "描述")
    storage.append_task_log(tid, "- 决策：改用 JWT，理由：xxx", source="对话#a3f2")
    content = storage.get_task(tid)["content"]
    assert "对话#a3f2" in content and "改用 JWT" in content


def test_edit_task(kanban_home):
    tid = storage.add_task("旧标题", "旧描述")
    storage.append_task_log(tid, "- 决策：历史记录一条")
    storage.edit_task(tid, title="新标题", description="新描述", priority="high",
                      unattended=True, files=["a.py"], tags=["x"])
    t = storage.get_task(tid)
    assert t["title"] == "新标题" and t["priority"] == "high" and t["unattended"]
    assert t["files"] == ["a.py"] and t["tags"] == ["x"]
    # 描述已替换，时间线原样保留
    assert "新描述" in t["content"] and "旧描述" not in t["content"]
    assert "历史记录一条" in t["content"]
    # 标题变更后文件已重命名，旧文件不残留
    names = [p.name for p in (kanban_home / "tasks").glob("T001-*.md")]
    assert names == ["T001-新标题.md"]
    with pytest.raises(ValueError):
        storage.edit_task(tid, priority="urgent")


# ---------- Summary 区块（T020：覆盖式压缩，控制捞任务上下文） ----------

def test_summary_lifecycle(kanban_home):
    tid = storage.add_task("任务", "描述")
    t = storage.get_task(tid)
    # 新任务自带空 Summary 区块
    assert "## Summary" in t["content"] and t["summary"] is None
    storage.append_task_log(tid, "- 历史时间线一条")
    # 覆盖式回写：summary 可读，描述与时间线不受影响
    storage.update_summary(tid, "- 决策：改用 JWT\n- 进度：50%")
    t = storage.get_task(tid)
    assert t["summary"] == "- 决策：改用 JWT\n- 进度：50%"
    assert "描述" in t["content"] and "历史时间线一条" in t["content"]
    # 再次覆盖：旧 summary 被替换，时间线仍不受影响
    storage.update_summary(tid, "- 新结论")
    t = storage.get_task(tid)
    assert t["summary"] == "- 新结论" and "改用 JWT" not in t["content"]
    assert "历史时间线一条" in t["content"]
    # 空字符串清空
    storage.update_summary(tid, "")
    assert storage.get_task(tid)["summary"] is None


def test_summary_legacy_file_compat(kanban_home):
    # 旧格式（无 Summary 区块）能正常解析，回写后升级为三段式
    tid = storage.add_task("旧任务", "旧描述")
    path = next((kanban_home / "tasks").glob("T001-*.md"))
    path.write_text(path.read_text().replace("## Summary\n\n", ""), encoding="utf-8")
    t = storage.get_task(tid)
    assert t["summary"] is None and "旧描述" in t["content"]
    storage.update_summary(tid, "补写的结论")
    t = storage.get_task(tid)
    assert t["summary"] == "补写的结论" and "旧描述" in t["content"]


def test_edit_task_keeps_summary(kanban_home):
    tid = storage.add_task("任务", "描述")
    storage.update_summary(tid, "摘要内容")
    storage.edit_task(tid, description="新描述")
    t = storage.get_task(tid)
    assert t["summary"] == "摘要内容" and "新描述" in t["content"]


def test_discard_task(kanban_home):
    tid = storage.add_task("不要了的任务", "x")
    # 无原因拒绝
    with pytest.raises(ValueError):
        storage.discard_task(tid, "")
    storage.discard_task(tid, "需求已变更")
    # get_task 回退到 archive/ 仍可读（只读恢复上下文），状态为 discarded
    assert storage.get_task(tid)["status"] == "discarded"
    archived = list((kanban_home / "archive").rglob("T001-*.md"))
    assert len(archived) == 1
    text = archived[0].read_text()
    assert "discarded" in text and "需求已变更" in text


def test_board_rebuild_idempotent(kanban_home):
    storage.add_task("任务A", "x")
    storage.rebuild_board()
    first = (kanban_home / "BOARD.md").read_text()
    storage.rebuild_board()
    second = (kanban_home / "BOARD.md").read_text()
    # 除时间戳行外内容一致（幂等）
    strip = lambda s: "\n".join(l for l in s.splitlines() if not l.startswith("> 最后更新"))
    assert strip(first) == strip(second)
    assert "任务A" in first


# ---------- 钉钉回复容错 ----------

def _make_waiting(title="任务"):
    tid = storage.add_task(title, "x")
    storage.update_status(tid, "doing")
    storage.update_status(tid, "waiting_decision", pending_question={"question": "q"})
    return tid


def test_reply_loose_id_formats(kanban_home):
    tid = _make_waiting()
    # 小写、无前导零
    reply = dingtalk._handle_reply("t1 用方案A")
    assert tid in reply and "退回待办" in reply
    t = storage.get_task(tid)
    assert t["status"] == "todo" and "用方案A" in t["content"]
    # 带空格与全角冒号
    storage.update_status(tid, "doing")
    storage.update_status(tid, "waiting_decision", pending_question={"question": "q"})
    assert "退回待办" in dingtalk._handle_reply("T 001：用方案B")
    assert "用方案B" in storage.get_task(tid)["content"]


def test_reply_id_without_decision(kanban_home):
    tid = _make_waiting()
    reply = dingtalk._handle_reply("T001")
    assert "缺少决策内容" in reply
    assert storage.get_task(tid)["status"] == "waiting_decision"  # 未误流转


def test_reply_no_id_single_waiting_auto_match(kanban_home):
    tid = _make_waiting()
    reply = dingtalk._handle_reply("直接用方案C")
    assert tid in reply
    t = storage.get_task(tid)
    assert t["status"] == "todo" and "直接用方案C" in t["content"]


def test_reply_no_id_none_or_multiple_waiting(kanban_home):
    assert "没有等待决策" in dingtalk._handle_reply("用方案D")
    t1, t2 = _make_waiting("任务一"), _make_waiting("任务二")
    reply = dingtalk._handle_reply("用方案D")
    assert t1 in reply and t2 in reply and "带上任务 ID" in reply
    # 两个任务均未被误流转
    assert storage.get_task(t1)["status"] == "waiting_decision"
    assert storage.get_task(t2)["status"] == "waiting_decision"


def test_reply_unknown_task(kanban_home):
    assert "不存在或已归档" in dingtalk._handle_reply("T999 用方案E")


def test_reply_feeds_wait_for_decision(kanban_home):
    tid = _make_waiting()
    dingtalk._handle_reply(f"{tid} 用方案F")
    assert dingtalk.wait_for_decision(tid, timeout_sec=3) == "用方案F"


# ---------- 定时配置 ----------

def test_schedule_defaults_when_missing(kanban_home):
    cfg = storage.get_schedule()
    assert cfg == {"enabled": False, "time": "10:00", "max_per_run": 3}


def test_schedule_save_and_reload(kanban_home):
    storage.save_schedule(True, "08:30", 5)
    cfg = storage.get_schedule()
    assert cfg["enabled"] is True
    assert cfg["time"] == "08:30" and cfg["max_per_run"] == 5
    assert cfg["updated"]


@pytest.mark.parametrize("time_str,n", [
    ("25:00", 3), ("0830", 3), ("8:30", 3), ("", 3),  # 非法时刻
    ("08:30", 0), ("08:30", 11),                       # 上限越界
])
def test_schedule_validation(kanban_home, time_str, n):
    with pytest.raises(ValueError):
        storage.save_schedule(True, time_str, n)


def test_schedule_save_preserves_last_reminded(kanban_home):
    storage.save_schedule(True, "09:00", 3)
    storage.mark_schedule_reminded("2026-07-29")
    cfg = storage.save_schedule(False, "09:00", 3)
    assert cfg["last_reminded"] == "2026-07-29"


def test_schedule_watchdog_helpers(kanban_home):
    import datetime as dt
    assert not storage.has_unattended_todo()
    storage.add_task("无人任务", "x", unattended=True)
    assert storage.has_unattended_todo()
    # 刚写入的任务文件应被认为"有执行痕迹"
    assert storage.tasks_touched_since(dt.datetime.now() - dt.timedelta(minutes=1))
    assert not storage.tasks_touched_since(dt.datetime.now() + dt.timedelta(minutes=1))


# ---------- 语义检索（mock 模型编码，不依赖真实 sentence-transformers） ----------

@pytest.fixture
def embed_env(kanban_home, monkeypatch):
    """模拟 embed 依赖已安装：_encode 按关键词返回确定性伪向量。"""
    np = pytest.importorskip("numpy")
    monkeypatch.setattr(embedding, "_HAS_ST", True)

    def fake_encode(texts):
        vecs = []
        for t in texts:
            if "auth" in t:
                vecs.append([1.0, 0.0, 0.0])
            elif "pay" in t:
                vecs.append([0.0, 1.0, 0.0])
            else:
                vecs.append([0.0, 0.0, 1.0])
        return np.asarray(vecs, dtype=np.float32)

    monkeypatch.setattr(embedding, "_encode", fake_encode)
    return np


def test_task_to_text_includes_summary():
    # Summary（执行结论）纳入 embedding 输入，时间线仍排除
    task = {"title": "T", "tags": [], "content":
            "## 任务描述\n\ndesc\n\n## Summary\n\n重要结论XYZ\n\n## 时间线\n\n噪声内容"}
    text = embedding._task_to_text(task)
    assert "重要结论XYZ" in text and "噪声内容" not in text
    assert "## Summary" not in text


def test_semantic_search_returns_results(embed_env):
    t_auth = storage.add_task("登录模块重构", "auth 相关改造")
    t_pay = storage.add_task("支付对账", "pay 渠道对账")
    hits = embedding.semantic_search("找一下 auth 相关的任务", limit=2)
    assert [h["id"] for h in hits] == [t_auth, t_pay]
    assert hits[0]["score"] == pytest.approx(1.0, abs=1e-3)
    assert hits[0]["score"] > hits[1]["score"]


def test_semantic_search_degrades_gracefully(kanban_home, monkeypatch):
    monkeypatch.setattr(embedding, "_HAS_ST", False)
    assert embedding.semantic_search("任意查询") == []
    embedding.upsert_task({"id": "T001", "title": "x", "content": ""})  # no-op 不抛异常
    embedding.ensure_index()
    assert not (kanban_home / "index").exists()


def test_upsert_and_archive_flag(embed_env, kanban_home):
    t1 = storage.add_task("auth 任务", "x")
    t2 = storage.add_task("pay 任务", "x")
    meta = json.loads((kanban_home / "index" / "meta.json").read_text())
    vectors = embed_env.load(kanban_home / "index" / "vectors.npy")
    assert meta["task_ids"] == [t1, t2]
    assert meta["row_map"] == {t1: 0, t2: 1}
    assert vectors.shape[0] == 2
    # 废弃后向量保留、打 archived 标；默认检索不命中，include_archived 时可搜到
    storage.discard_task(t1, "不需要了")
    meta = json.loads((kanban_home / "index" / "meta.json").read_text())
    vectors = embed_env.load(kanban_home / "index" / "vectors.npy")
    assert meta["task_ids"] == [t1, t2] and vectors.shape[0] == 2
    assert meta["archived_ids"] == [t1]
    assert [h["id"] for h in embedding.semantic_search("auth", limit=5)] == [t2]
    hits = embedding.semantic_search("auth", limit=5, include_archived=True)
    assert [h["id"] for h in hits] == [t1, t2]


def test_remove_task_physically(embed_env, kanban_home):
    t1 = storage.add_task("auth 任务", "x")
    t2 = storage.add_task("pay 任务", "x")
    embedding.remove_task(t1)
    meta = json.loads((kanban_home / "index" / "meta.json").read_text())
    vectors = embed_env.load(kanban_home / "index" / "vectors.npy")
    assert meta["task_ids"] == [t2] and vectors.shape[0] == 1


def test_edit_task_reembeds(embed_env):
    tid = storage.add_task("auth 任务", "x")
    storage.edit_task(tid, title="pay 任务", description="改做支付")
    hits = embedding.semantic_search("pay 相关", limit=1)
    assert hits[0]["id"] == tid and hits[0]["score"] == pytest.approx(1.0, abs=1e-3)


def test_done_task_archived_in_index(embed_env, kanban_home):
    tid = storage.add_task("auth 任务", "x")
    storage.update_status(tid, "doing")
    storage.update_status(tid, "review")
    storage.update_status(tid, "done")
    # 向量保留 + archived 标；默认搜不到，include_archived 时能搜到
    meta = json.loads((kanban_home / "index" / "meta.json").read_text())
    assert meta["task_ids"] == [tid] and meta["archived_ids"] == [tid]
    assert embedding.semantic_search("auth", limit=5) == []
    hits = embedding.semantic_search("auth", limit=5, include_archived=True)
    assert [h["id"] for h in hits] == [tid]


def test_index_persistence(embed_env, kanban_home, monkeypatch):
    t1 = storage.add_task("auth 任务", "x")
    # 重新从磁盘加载：数据完整，无需重建
    vectors, meta = embedding._load_index()
    assert meta["model"] == embedding.MODEL_NAME
    assert meta["task_ids"] == [t1] and vectors.shape[0] == 1
    # ensure_index 在索引完好时不触发全量重建
    called = []
    monkeypatch.setattr(embedding, "build_index", lambda tasks: called.append(1))
    embedding.ensure_index()
    assert called == []


def test_index_corruption_does_not_block_writes(embed_env, kanban_home):
    storage.add_task("auth 任务", "x")
    (kanban_home / "index" / "meta.json").write_text("{broken")
    # 索引损坏时搜索降级为空，核心写入不受影响
    assert embedding.semantic_search("auth") == []
    t2 = storage.add_task("pay 任务", "x")
    assert storage.get_task(t2)["title"] == "pay 任务"
