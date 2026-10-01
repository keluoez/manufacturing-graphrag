import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from .. import db
from ..config import settings
from ..pipeline import runner

router = APIRouter(prefix="/api", tags=["pipeline"])


@router.get("/health")
def health():
    from ..graphstore import get_store

    store = get_store()
    return {
        "status": "ok",
        "llm_provider": settings.llm_provider,
        "embedding_provider": settings.embedding_provider,
        "graph_backend": store.name,
    }


@router.get("/nodes")
def list_nodes():
    return {"running": runner.is_running(), "nodes": runner.node_states()}


@router.post("/nodes/{node_id}/run")
def run_node(node_id: str, payload: dict | None = None):
    from ..pipeline.meta import NODE_INDEX

    if node_id not in NODE_INDEX:
        return JSONResponse({"detail": f"未知节点：{node_id}"}, status_code=404)
    try:
        runner.run_node_async(node_id, payload or {})
    except RuntimeError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=409)
    return {"submitted": True, "node": node_id}


@router.post("/pipeline/run-all")
def run_all(payload: dict | None = None):
    start_from = (payload or {}).get("start_from")
    try:
        runner.run_all_async(start_from)
    except RuntimeError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=409)
    return {"submitted": True, "start_from": start_from}


@router.get("/logs")
def get_logs(after: int = 0, node_id: str | None = None):
    rows = db.logs_after(after)
    if node_id:
        rows = [r for r in rows if r["node_id"] == node_id]
    return {"logs": rows}


@router.get("/events")
async def events(request: Request):
    async def gen():
        last_log = 0
        last_states = ""
        while True:
            if await request.is_disconnected():
                break
            rows = db.logs_after(last_log)
            for r in rows:
                last_log = r["id"]
                yield {"event": "log", "data": json.dumps(r, ensure_ascii=False)}
            states = json.dumps(
                {"running": runner.is_running(), "nodes": runner.node_states()},
                ensure_ascii=False,
            )
            if states != last_states:
                last_states = states
                yield {"event": "state", "data": states}
            await asyncio.sleep(0.6)

    return EventSourceResponse(gen())


@router.get("/store/stats")
def store_stats():
    from ..graphstore import get_store

    return get_store().stats()


@router.post("/store/reset")
def store_reset():
    from ..graphstore import get_store

    store = get_store()
    store.reset()
    return {"reset": True, "backend": store.name}
