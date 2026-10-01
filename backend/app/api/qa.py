import json

from fastapi import APIRouter, HTTPException

from ..config import GOLD_DIR, OUTPUT_DIR
from ..retrieval.engine import get_engine

router = APIRouter(prefix="/api/qa", tags=["qa"])


@router.post("/ask")
def ask(body: dict):
    query = (body or {}).get("query", "").strip()
    if not query:
        raise HTTPException(400, "query 不能为空")
    engine = get_engine()
    bm = engine.bm25_search(query, k=3)
    vec = engine.vector_search(query, k=3)
    gres = engine.graph_query(query)

    graph_ids = []
    for arr in gres["answer"].values():
        for e in arr:
            if e["id"] not in graph_ids:
                graph_ids.append(e["id"])
    graph_ids = [i for i in graph_ids if not i.startswith("CMT_") and not i.startswith("CMS_")]
    vec_ids = engine.entities_in_text(" ".join(h["text"] for h in vec), None)
    hybrid_ids = graph_ids + [i for i in dict.fromkeys(vec_ids) if i not in graph_ids][:3]

    def names_of(ids):
        return engine.names(ids)

    return {
        "query": query,
        "links": [{"id": h["id"], "label": h["label"], "surface": h["surface"],
                   "method": h["method"]} for h in gres["links"]],
        "bm25": [{"doc_id": h["doc_id"], "score": h["score"], "text": h["text"]} for h in bm],
        "vector": [{"doc_id": h["doc_id"], "score": h["score"], "text": h["text"]} for h in vec],
        "graph": {
            "answer": {k: names_of([e["id"] for e in v]) for k, v in gres["answer"].items()},
            "subgraph": gres["subgraph"],
            "triples": gres["triples"],
        },
        "hybrid_answer": names_of(hybrid_ids[:10]),
    }


@router.get("/questions")
def questions(qtype: str | None = None):
    path = GOLD_DIR / "questions150.jsonl"
    if not path.exists():
        raise HTTPException(404, "问题集未生成，请先运行节点 7")
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    if qtype:
        rows = [r for r in rows if r["qtype"] == qtype]
    return {"items": rows, "total": len(rows)}


@router.get("/answers")
def answers(limit: int = 20):
    path = OUTPUT_DIR / "qa_answers.jsonl"
    if not path.exists():
        raise HTTPException(404, "问答结果未生成，请先运行节点 8")
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()][:limit]
    return {"items": rows, "total": limit}
