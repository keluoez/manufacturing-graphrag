from .base import GraphStore
from .memory import MemoryStore

_store: GraphStore | None = None


def get_store() -> GraphStore:
    global _store
    if _store is not None:
        return _store

    from ..config import settings
    from .. import db

    try:
        from .neo4j_store import Neo4jStore

        _store = Neo4jStore.connect(
            settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        db.add_log(None, "info", f"已连接 Neo4j：{settings.neo4j_uri}")
    except Exception as exc:
        if settings.graphstore_auto_fallback:
            _store = MemoryStore.load_or_create()
            db.add_log(
                None,
                "warning",
                f"Neo4j 不可用（{type(exc).__name__}: {exc}），已加载内存图库快照"
                f"（节点 {len(_store.entities)}）；装好 Docker 后重跑入图节点即可切换 Neo4j",
            )
        else:
            raise
    return _store


def reset_store() -> None:
    global _store
    _store = None


__all__ = ["GraphStore", "MemoryStore", "get_store", "reset_store"]
