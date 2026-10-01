"""节点 8：多跳归因问答 - 向量（开放语义）+ 图谱（关系推理）双路融合。

输出每题四路结构化答案（bm25 / vector / graph / hybrid）、数值题预测、
自然语言答复与证据（chunk 溯源 + 子图三元组）。
"""
import json

from ...config import OUTPUT_DIR, settings
from ...retrieval.engine import get_engine, reset_engine
from ..nodes.s7_retrieve import INTENT_LABELS

TRACE_PATH = OUTPUT_DIR / "retrieval_trace.jsonl"
ANSWER_PATH = OUTPUT_DIR / "qa_answers.jsonl"

ANSWER_SYS = """你是制造工艺质量工程师。基于提供的【图谱路径】与【文档片段】回答问题，
只依据证据作答，分条列出对策/原因并标注来源类型；证据不足要明确说明。不超过 200 字。"""


def _flatten(units):
    out = []
    for u in units:
        for i in u.get("candidate_ids", []):
            if i not in out:
                out.append(i)
    return out


def _names(engine, ids):
    return engine.names(ids)


def compose_answer(engine, query, gres, vec_hits):
    """mock/模板化自然语言答复（图谱路径优先）。"""
    ans = gres["answer"]
    lines = [f"问题：{query}"]
    if ans["processes"]:
        lines.append("责任工序：" + "、".join(e["name"] for e in ans["processes"]))
    if ans["causes"]:
        lines.append("可能原因：" + "；".join(e["name"] for e in ans["causes"][:6]))
    if ans["countermeasures"]:
        lines.append("对策：" + "；".join(e["name"] for e in ans["countermeasures"][:6]))
    if ans["defects"] and not ans["countermeasures"]:
        lines.append("相关不良：" + "、".join(e["name"] for e in ans["defects"][:8]))
    if not any(ans.values()):
        if vec_hits:
            lines.append("图谱未命中，参考语义相近文档：" + vec_hits[0]["text"][:120].replace("\n", " "))
        else:
            lines.append("未检索到有效证据。")
    return "\n".join(lines)


def run(ctx):
    reset_engine()
    engine = get_engine()
    traces = [json.loads(l) for l in TRACE_PATH.read_text(encoding="utf-8").splitlines()]

    answers = []
    for t in traces:
        bm_ids = _flatten(t["bm25"])
        vec_ids = _flatten(t["vector"])
        graph_ids = t["graph"]["candidate_ids"]
        # 融合：图谱优先；向量仅补充与问题意图同类型、且出现在 top3 的少量候选
        intent_labels = set(INTENT_LABELS.get(t["intent"], []))
        supplement = []
        for i in vec_ids:
            if i in graph_ids or i in supplement:
                continue
            ent = engine.get_entities([i]).get(i)
            if ent and ent["label"] in intent_labels:
                supplement.append(i)
            if len(supplement) >= 3:
                break
        hybrid_ids = graph_ids + supplement

        # 数值题：候选 Defect 计数（融合路用图谱结构计数）
        pred_number = None
        if t["intent"] == "process_defect_count":
            pred_number = len(graph_ids)

        # 图谱证据子图（重新取一次用于自然语言答复与溯源）
        gres = engine.graph_query(t["query"])
        vec_hits = engine.vector_search(t["query"], k=3)
        natural = compose_answer(engine, t["query"], gres, vec_hits)

        # 自然语言答复：默认用确定性模板（评测只判结构化答案）；
        # 需要 LLM 生成自然语言答复时在 .env 设 QA_LLM_NATURAL=true
        if getattr(settings, "qa_llm_natural", False) and settings.llm_provider != "mock":
            try:
                from ...llm import llm

                name_map = {n["id"]: n["name"] for n in engine.names(
                    [x for ed in gres["triples"][:20] for x in (ed["source"], ed["target"])])}
                triple_text = "\n".join(
                    f"{name_map.get(ed['source'], ed['source'])} —{ed['rel']}→ "
                    f"{name_map.get(ed['target'], ed['target'])}" for ed in gres["triples"][:20])
                chunk_text = "\n".join(f"【{h['doc_id']}】{h['text'][:200]}" for h in vec_hits[:2])
                context = f"【图谱路径】\n{triple_text}\n\n【文档片段】\n{chunk_text}"
                natural = llm.chat(
                    [{"role": "system", "content": ANSWER_SYS},
                     {"role": "user", "content": context + "\n\n问题：" + t["query"]}],
                    temperature=0.1).strip()
            except Exception as exc:
                ctx.log(f"LLM 答案生成失败，回退模板：{t['qid']} {str(exc)[:80]}", "warning")

        answers.append({
            "qid": t["qid"], "qtype": t["qtype"], "intent": t["intent"],
            "query": t["query"], "gold_ids": t["gold_ids"], "gold_number": t.get("gold_number"),
            "answers": {
                "bm25": bm_ids, "vector": vec_ids,
                "graph": graph_ids, "hybrid": hybrid_ids,
            },
            "pred_number": {"bm25": len(set(bm_ids)), "vector": len(set(vec_ids)),
                            "graph": pred_number, "hybrid": pred_number},
            "evidence_chunks": [{"doc_id": h["doc_id"], "chunk_id": h["chunk_id"]}
                                for h in vec_hits],
            "evidence_triples": [
                {"source": ed["source"], "target": ed["target"], "rel": ed["rel"]}
                for ed in gres["triples"][:30]],
            "natural_answer": natural,
            "answer_names": {
                "graph": _names(engine, graph_ids)[:10],
                "hybrid": _names(engine, hybrid_ids)[:10],
            },
        })

    ANSWER_PATH.write_text(
        "\n".join(json.dumps(a, ensure_ascii=False) for a in answers), encoding="utf-8")
    ctx.log(f"归因问答完成：{len(answers)} 题，四路结构化答案 + 融合自然语言答复已落盘")
    return {"questions": len(answers)}
