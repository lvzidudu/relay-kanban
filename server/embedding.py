"""语义检索层：sentence-transformers 向量化 + numpy cosine 检索。

设计约束：
- optional dependency（extras: embed）。未安装 sentence-transformers/numpy 时
  所有入口静默降级（返回空结果 / no-op），绝不向调用方抛异常
- 索引持久化到 ~/.kanban/index/（vectors.npy + meta.json），增量更新，
  服务重启后直接从文件加载
- 只索引活跃任务：done/discard 时从索引移除（与 storage 写入点挂钩保持一致，
  重建时也只遍历 tasks/，避免重启后归档任务"复活"进索引）
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json

try:
    import numpy as np
    _HAS_NP = True
except ImportError:  # pragma: no cover
    np = None
    _HAS_NP = False

# 只探测不导入：sentence_transformers 连带 torch，模块级导入会拖慢服务启动数秒级
_HAS_ST = importlib.util.find_spec("sentence_transformers") is not None

from . import storage

MODEL_NAME = "all-MiniLM-L6-v2"
_model = None


def _available() -> bool:
    return _HAS_NP and _HAS_ST


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer  # 懒导入，首次编码时才加载
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def _encode(texts: list[str]):
    """文本 -> (n, dim) float32 向量矩阵。测试中会被 mock 掉。"""
    return np.asarray(_get_model().encode(texts), dtype=np.float32)


# ---------- 路径（动态读 storage.KANBAN_DIR，随测试重定向） ----------

def _index_dir():
    return storage.KANBAN_DIR / "index"


def _vectors_path():
    return _index_dir() / "vectors.npy"


def _meta_path():
    return _index_dir() / "meta.json"


# ---------- 文本提取 ----------

def _task_to_text(task: dict) -> str:
    """标题 + 标签 + 任务描述区块（不含时间线）拼接为 embedding 输入。"""
    content = task.get("content") or ""
    idx = content.find("## 时间线")
    desc = content[:idx] if idx >= 0 else content
    desc = desc.replace("## 任务描述", "").strip()
    parts = [
        str(task.get("title") or ""),
        " ".join(str(t) for t in (task.get("tags") or [])),
        desc,
    ]
    return "\n".join(p for p in parts if p)


# ---------- 索引读写 ----------

def _load_index():
    """返回 (vectors, meta)；文件缺失/损坏/模型不匹配时返回 (None, None)。"""
    if not (_vectors_path().exists() and _meta_path().exists()):
        return None, None
    try:
        meta = json.loads(_meta_path().read_text())
        vectors = np.load(_vectors_path())
    except Exception:
        return None, None
    task_ids = meta.get("task_ids") or []
    if meta.get("model") != MODEL_NAME or len(task_ids) != vectors.shape[0]:
        return None, None
    return vectors, meta


def _save_index(vectors, task_ids: list[str]) -> None:
    _index_dir().mkdir(parents=True, exist_ok=True)
    np.save(_vectors_path(), np.asarray(vectors, dtype=np.float32))
    meta = {
        "model": MODEL_NAME,
        "updated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "task_ids": list(task_ids),
        "row_map": {tid: i for i, tid in enumerate(task_ids)},
    }
    _meta_path().write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")


# ---------- 对外接口 ----------

def build_index(tasks: list[dict]) -> None:
    """全量重建索引。"""
    if not _available():
        return
    task_ids = [t["id"] for t in tasks]
    if task_ids:
        vectors = _encode([_task_to_text(t) for t in tasks])
    else:
        vectors = np.zeros((0, 0), dtype=np.float32)
    _save_index(vectors, task_ids)


def ensure_index() -> None:
    """启动时调用：索引存在且模型匹配则跳过，否则从活跃任务全量重建。
    任何异常自行吞掉，不影响服务启动。"""
    if not _available():
        return
    try:
        vectors, meta = _load_index()
        if vectors is not None:
            return
        build_index(storage.list_tasks())
    except Exception:
        pass


def upsert_task(task: dict) -> None:
    """新增或更新单个任务的向量（增量）；索引缺失/损坏时顺带全量重建。"""
    if not _available():
        return
    vectors, meta = _load_index()
    if vectors is None:
        build_index(storage.list_tasks())
        return
    vec = _encode([_task_to_text(task)])
    task_ids = list(meta["task_ids"])
    row = meta["row_map"].get(task["id"])
    if row is not None:
        vectors[row] = vec[0]
    elif vectors.shape[0] == 0:
        vectors = vec
        task_ids = [task["id"]]
    else:
        vectors = np.vstack([vectors, vec])
        task_ids.append(task["id"])
    _save_index(vectors, task_ids)


def remove_task(task_id: str) -> None:
    """从索引移除任务（done 归档 / 废弃时调用）。仅需 numpy。"""
    if not _HAS_NP:
        return
    vectors, meta = _load_index()
    if vectors is None:
        return
    row = meta["row_map"].get(task_id)
    if row is None:
        return
    vectors = np.delete(vectors, row, axis=0)
    task_ids = [t for t in meta["task_ids"] if t != task_id]
    _save_index(vectors, task_ids)


def semantic_search(query: str, limit: int = 5) -> list[dict]:
    """语义检索：返回 [{"id", "score"}]，按 cosine 相似度降序。
    索引不可用/为空时返回空列表（调用方回退到 keyword 搜索）。"""
    if not _available():
        return []
    vectors, meta = _load_index()
    if vectors is None or vectors.shape[0] == 0:
        return []
    q = _encode([query])[0]
    denom = np.linalg.norm(vectors, axis=1) * np.linalg.norm(q) + 1e-10
    sims = vectors @ q / denom
    order = np.argsort(-sims)[: max(1, int(limit))]
    return [{"id": meta["task_ids"][i], "score": round(float(sims[i]), 4)}
            for i in order]
