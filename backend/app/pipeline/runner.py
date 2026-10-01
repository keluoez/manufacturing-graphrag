import importlib
import threading
import traceback

from .. import db
from ..config import settings
from .meta import NODE_INDEX, NODE_META


class RunContext:
    def __init__(self, node_id: str, params: dict | None = None) -> None:
        self.node_id = node_id
        self.params = params or {}
        self.settings = settings
        self._store = None

    def log(self, message: str, level: str = "info") -> None:
        db.add_log(self.node_id, level, message)

    @property
    def store(self):
        if self._store is None:
            from ..graphstore import get_store

            self._store = get_store()
        return self._store


_lock = threading.Lock()
_thread: threading.Thread | None = None


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()


def run_node(node_id: str, params: dict | None = None) -> dict:
    if not _lock.acquire(blocking=False):
        raise RuntimeError("已有流水线任务在运行，请等待完成")
    meta = NODE_INDEX[node_id]
    run_id = db.start_run(node_id)
    db.add_log(node_id, "info", f"▶ 开始执行节点：{meta['name']}")
    try:
        module = importlib.import_module(meta["module"])
        ctx = RunContext(node_id, params)
        stats = module.run(ctx) or {}
        db.finish_run(run_id, "success", stats)
        db.add_log(node_id, "info", f"✔ 节点完成：{stats}")
        return stats
    except Exception as exc:
        db.finish_run(run_id, "failed", {}, f"{type(exc).__name__}: {exc}")
        db.add_log(node_id, "error", traceback.format_exc(limit=4))
        raise
    finally:
        _lock.release()


def run_all(start_from: str | None = None) -> None:
    started = start_from is None
    for meta in NODE_META:
        if not started and meta["id"] == start_from:
            started = True
        if started:
            run_node(meta["id"])


def _wrap(fn, *args) -> None:
    global _thread
    try:
        fn(*args)
    except Exception:
        pass
    finally:
        _thread = None


def run_node_async(node_id: str, params: dict | None = None) -> None:
    global _thread
    if is_running():
        raise RuntimeError("已有流水线任务在运行")
    _thread = threading.Thread(
        target=_wrap, args=(run_node, node_id, params), daemon=True
    )
    _thread.start()


def run_all_async(start_from: str | None = None) -> None:
    global _thread
    if is_running():
        raise RuntimeError("已有流水线任务在运行")
    _thread = threading.Thread(target=_wrap, args=(run_all, start_from), daemon=True)
    _thread.start()


def node_states() -> list[dict]:
    result = []
    for meta in NODE_META:
        latest = db.latest_run(meta["id"])
        state = latest["status"] if latest else "pending"
        result.append(
            {
                "id": meta["id"],
                "step": meta["step"],
                "name": meta["name"],
                "subtitle": meta["subtitle"],
                "phase": meta["phase"],
                "desc": meta["desc"],
                "outputs": meta["outputs"],
                "state": state,
                "latest": latest,
            }
        )
    return result
