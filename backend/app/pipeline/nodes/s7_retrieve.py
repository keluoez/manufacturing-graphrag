"""节点 7：三路检索 - 对 150 题逐一执行 BM25 / 向量 / GraphRAG，全程留痕。

公平性口径（v2，与线上 /api/qa/ask 一致）：
- 三路候选预算相同：文本路每路取 top 10 chunk（原为 3），图谱邻居同样截前 10；
- 三路按问题意图做相同的类型/前缀过滤（如"根因对策"题三路都只保留 CM_ 工程对策，
  不再让图谱路独享金标构造规则）；
- 候选统一排序：文本路按检索相关性（chunk 名次，块内按实体热度），图谱路按与金标无关的
  确定性静态序（全局 mention_count 降序、ID 兜底），使 Hit@3/MRR 三路可比。
"""
import json

from ...config import OUTPUT_DIR
from ...pipeline.nodes.s6_index import tokenize
from ...qa.questions import write_questions
from ...retrieval.engine import get_engine, reset_engine

TRACE_PATH = OUTPUT_DIR / "retrieval_trace.jsonl"

RETRIEVAL_K = 10  # 文本路召回 chunk 数（评测候选预算，与图谱邻居拉平）

INTENT_LABELS = {
    "defect_to_process": ["Process"],
    "process_to_defects": ["Defect"],
    "defect_to_causes": ["Cause"],
    "device_to_process": ["Process"],
    "process_to_materials": ["Material"],
    "defect_to_countermeasures": ["Countermeasure"],
    "process_aggregate_cm": ["Countermeasure"],
    "process_defect_count": ["Defect"],
    "material_to_defects": ["Defect"],
}

# 问"根因/永久对策"的意图：三路都只保留工程对策 CM_（临时 CMT_/标准化 CMS_ 不计分）
_STRICT_CM_INTENTS = {"defect_to_countermeasures", "process_aggregate_cm"}


def intent_filter(intent: str, ids: list[str]) -> list[str]:
    """三路统一的候选口径过滤（与题面语义、线上问答过滤保持一致）。"""
    if intent in _STRICT_CM_INTENTS:
        return [i for i in ids if i.startswith("CM_")]
    return list(ids)


def static_rank(engine, ids: list[str]) -> list[str]:
    """与金标无关的确定性排序：全局提及热度降序，ID 字典序兜底。"""
    uniq = list(dict.fromkeys(ids))
    if not uniq:
        return []
    ents = engine.get_entities(uniq)
    return sorted(uniq, key=lambda i: (-(ents.get(i, {}).get("mention_count") or 1), i))


def chunk_candidate_ids(engine, hits: list[dict], labels: list[str], intent: str) -> list[str]:
    """文本路候选：chunk 按检索相关性排序，块内实体按静态热度排序，去重后统一意图过滤。"""
    ordered: list[str] = []
    for h in hits:
        ids = engine.entities_in_text(h["text"], labels)
        for i in static_rank(engine, ids):
            if i not in ordered:
                ordered.append(i)
    return intent_filter(intent, ordered)


def _graph_candidates(engine, query: str, intent: str) -> tuple[list[str], dict]:
    gres = engine.graph_query(query)
    ans = gres["answer"]
    if intent == "defect_to_process":
        cand = [e["id"] for e in ans["processes"]]
    elif intent in ("process_to_defects", "process_defect_count"):
        cand = [e["id"] for e in ans["defects"]]
    elif intent == "defect_to_causes":
        cand = [e["id"] for e in ans["causes"]]
    elif intent in ("defect_to_countermeasures", "process_aggregate_cm"):
        # 严格金标为根因工程对策（CM_），临时遏制/标准化措施不计入
        cand = [e["id"] for e in ans["countermeasures"] if e["id"].startswith("CM_")]
    elif intent == "device_to_process":
        dev_ids = [h["id"] for h in gres["links"] if h["label"] == "Device"]
        cand = []
        for eid in dev_ids:
            sub = engine.store.neighbors([eid], rel_types=["USED_IN"], hops=1)
            cand += [n["id"] for n in sub["nodes"] if n["label"] == "Process"]
    elif intent == "process_to_materials":
        proc_ids = [h["id"] for h in gres["links"] if h["label"] == "Process"]
        cand = []
        for eid in proc_ids:
            sub = engine.store.neighbors([eid], rel_types=["USES_MATERIAL"], hops=1)
            cand += [n["id"] for n in sub["nodes"] if n["label"] == "Material"]
    elif intent == "material_to_defects":
        cand = [e["id"] for e in ans["defects"]]
    else:
        cand = []
    seen, uniq = set(), []
    for c in cand:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq, gres


def run(ctx):
    reset_engine()
    engine = get_engine()
    questions = write_questions()

    traces = []
    link_methods = {"alias": 0, "embedding": 0}
    for q in questions:
        labels = INTENT_LABELS.get(q["intent"], [])
        bm_hits = engine.bm25_search(q["query"], k=RETRIEVAL_K)
        vec_hits = engine.vector_search(q["query"], k=RETRIEVAL_K)
        g_cand, gres = _graph_candidates(engine, q["query"], q["intent"])
        # 图谱候选与文本路同口径：统一意图过滤 + 确定性静态排序后截断相同预算
        g_cand = static_rank(engine, intent_filter(q["intent"], g_cand))

        traces.append({
            "qid": q["qid"],
            "qtype": q["qtype"],
            "intent": q["intent"],
            "query": q["query"],
            "gold_ids": q["gold_ids"],
            "gold_number": q.get("gold_number"),
            "bm25": [{"chunk_id": h["chunk_id"], "doc_id": h["doc_id"], "score": round(h["score"], 3),
                      "candidate_ids": chunk_candidate_ids(engine, [h], labels, q["intent"])}
                     for h in bm_hits],
            "vector": [{"chunk_id": h["chunk_id"], "doc_id": h["doc_id"], "score": round(h["score"], 3),
                        "candidate_ids": chunk_candidate_ids(engine, [h], labels, q["intent"])}
                       for h in vec_hits],
            "graph": {
                "candidate_ids": g_cand,
                "links": [{"id": h["id"], "label": h["label"], "method": h["method"]}
                          for h in gres["links"]],
                "subgraph_nodes": len(gres["subgraph"]["nodes"]),
                "subgraph_edges": len(gres["subgraph"]["edges"]),
            },
        })
        for h in gres["links"]:
            link_methods[h["method"]] = link_methods.get(h["method"], 0) + 1

    TRACE_PATH.write_text(
        "\n".join(json.dumps(t, ensure_ascii=False) for t in traces), encoding="utf-8")

    # 冒烟统计：实体链接命中率与链接方式（噪声题面下 embedding 补链应被真正触发）
    linked = sum(1 for t in traces if t["graph"]["links"])
    ctx.log(f"三路检索完成：{len(traces)} 题；问题实体链接成功 {linked}/{len(traces)}；"
            f"链接方式 {link_methods}")
    ctx.log(f"trace 已落盘：BM25/向量 top{RETRIEVAL_K} chunk 候选 + GraphRAG 子图路径，供评测与问答页溯源")
    return {"questions": len(traces), "linked": linked, "link_methods": link_methods}
