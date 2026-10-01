from fastapi import APIRouter, HTTPException, Query

from ..config import OUTPUT_DIR
from ..graphstore import get_store
from ..kg_types import LABEL_CN, REL_CN

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/stats")
def stats():
    store = get_store()
    s = store.stats()
    s["label_cn"] = LABEL_CN
    s["rel_cn"] = REL_CN
    return s


@router.get("/sample")
def sample(limit: int = Query(120, le=600)):
    return get_store().sample_subgraph(limit=limit)


@router.get("/entity")
def entity(name: str, label: str | None = None):
    hits = get_store().find_entities(name, label, limit=10)
    if not hits:
        raise HTTPException(404, "未找到实体")
    return {"name": name, "hits": hits}


@router.get("/causal")
def causal(name: str):
    return get_store().causal_paths(name)


@router.post("/cypher")
def cypher(body: dict):
    stmt = (body or {}).get("cypher", "")
    if not stmt.strip():
        raise HTTPException(400, "cypher 不能为空")
    store = get_store()
    if store.name != "neo4j":
        raise HTTPException(409, "当前为内存图库，请先启动 Docker 版 Neo4j 并重跑入图节点")
    return store.raw_cypher(stmt)
