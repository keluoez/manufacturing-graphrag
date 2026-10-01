"""节点 9：分层评测。

- 抽取层：300 条抽检集上的实体/关系 P/R/F1（对齐后的规范实体 ID 口径）；
- 检索层：150 题 × 三路 的 Hit@3、MRR、覆盖率（按单跳/多跳/聚合分组）；
- 端到端：列表题覆盖率+精确率口径准确率、数值题精确匹配；
- 消融：关闭图谱通道（仅向量）对比多跳/聚合 Hit@3 与准确率。
输出 metrics_summary.json 与 per_question.csv。
"""
import csv
import json
from collections import defaultdict

from ...config import EVAL_DIR, GOLD_DIR, OUTPUT_DIR
from .s4_align import clean_surface

SAMPLE_PATH = GOLD_DIR / "sample300.jsonl"
GOLD_CORPUS = GOLD_DIR / "corpus_gold.jsonl"
ENT_PATH = OUTPUT_DIR / "entities.jsonl"
ENT_ALIGNED = OUTPUT_DIR / "entities_aligned.jsonl"
REL_ALIGNED = OUTPUT_DIR / "relations_aligned.jsonl"
TRACE_PATH = OUTPUT_DIR / "retrieval_trace.jsonl"
ANSWER_PATH = OUTPUT_DIR / "qa_answers.jsonl"


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4),
            "tp": tp, "fp": fp, "fn": fn}


def extraction_scores() -> dict:
    samples = [json.loads(l) for l in SAMPLE_PATH.read_text(encoding="utf-8").splitlines()]
    docs = {d["doc_id"]: d for d in
            (json.loads(l) for l in GOLD_CORPUS.read_text(encoding="utf-8").splitlines())}

    # surface 归一映射（与节点 4 同口径）
    surface_map = {}
    for line in ENT_ALIGNED.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        for s in e.get("surfaces", []):
            surface_map[(clean_surface(s), e["label"])] = e["entity_id"]

    # 每文档的预测实体 ID 集合
    pred_ents = defaultdict(set)
    for line in ENT_PATH.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        eid = surface_map.get((clean_surface(r["surface"]), r["label"]))
        if eid:
            pred_ents[r["doc_id"]].add(eid)
    pred_rels = defaultdict(set)
    for line in REL_ALIGNED.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        pred_rels[r["doc_id"]].add((r["head_id"], r["rel"], r["tail_id"]))

    sample_docs = {s["doc_id"] for s in samples}

    # 实体（文档级 ID 集合 micro P/R/F1，评价范围为抽检覆盖文档）
    ent_tp = ent_fp = ent_fn = 0
    for doc_id in sample_docs:
        gold_ents = {m["id"] for m in docs[doc_id]["mentions"]}
        pe = pred_ents.get(doc_id, set())
        ent_tp += len(gold_ents & pe)
        ent_fp += len(pe - gold_ents)
        ent_fn += len(gold_ents - pe)

    # 关系：语料级三元组集合口径（同一三元组在多文档重复出现，入图按唯一边计）
    gold_rels = {tuple(x) for d in docs.values() for x in d["relations"]}
    all_pred_rels = set()
    for pr in pred_rels.values():
        all_pred_rels |= pr
    rel_tp = len(gold_rels & all_pred_rels)
    rel_fp = len(all_pred_rels - gold_rels)
    rel_fn = len(gold_rels - all_pred_rels)

    return {"entity": prf(ent_tp, ent_fp, ent_fn),
            "relation": prf(rel_tp, rel_fp, rel_fn),
            "sample_size": len(samples)}


def _ranked_ids(trace: dict, channel: str) -> list[str]:
    if channel == "graph":
        return trace["graph"]["candidate_ids"]
    ordered = []
    for u in trace[channel]:
        for i in u.get("candidate_ids", []):
            if i not in ordered:
                ordered.append(i)
    return ordered


def retrieval_scores(traces: list[dict]) -> dict:
    result = {}
    for qtype in ("single", "multi", "agg"):
        qs = [t for t in traces if t["qtype"] == qtype and not t.get("gold_number")]
        per_channel = {}
        for ch in ("bm25", "vector", "graph"):
            hits, rr_sum, n = 0, 0.0, 0
            cov = {3: 0.0, 10: 0.0, 9999: 0.0}
            for t in qs:
                gold = set(t["gold_ids"])
                if not gold:
                    continue
                n += 1
                ranked = _ranked_ids(t, ch)
                first = next((i + 1 for i, x in enumerate(ranked) if x in gold), None)
                if first and first <= 3:
                    hits += 1
                    rr_sum += 1 / first
                for k in cov:
                    cov[k] += len(gold & set(ranked[:k])) / len(gold)
            per_channel[ch] = {
                "hit_at_3": round(hits / n, 4) if n else 0,
                "mrr": round(rr_sum / n, 4) if n else 0,
                "coverage_at_3": round(cov[3] / n, 4) if n else 0,
                "coverage_at_10": round(cov[10] / n, 4) if n else 0,
                # 多答案题的完整召回能力：全部候选对金标的覆盖率
                "coverage_all": round(cov[9999] / n, 4) if n else 0,
                "n": n,
            }
        result[qtype] = per_channel
    return result


def _is_correct(gold: set, pred: set) -> bool:
    """列表题判分：覆盖率≥70% 且精确率≥50%（允许抽取层少量噪声，考察主答案完整性）。"""
    if not pred:
        return False
    return (len(gold & pred) / len(gold) >= 0.7 and
            len(gold & pred) / len(pred) >= 0.5)


def _num_correct(gold_num, pred_num) -> bool:
    """数值题：精确或 ±1 容差（现场统计口径允许单点抽取误差）。"""
    return pred_num is not None and abs(pred_num - gold_num) <= 1


def answer_scores(answers: list[dict]) -> dict:
    """列表题与数值题分开报告：
    - single/multi/agg(agg 只含列表题，n=35)：覆盖率+精确率口径；
    - agg_number(n=15)：数值题精确/±1，各通道计数定义见 s8。
    """
    result = {}
    list_groups = [
        ("single", lambda a: a["qtype"] == "single"),
        ("multi", lambda a: a["qtype"] == "multi"),
        ("agg", lambda a: a["qtype"] == "agg" and a.get("gold_number") is None),
    ]
    for key, cond in list_groups:
        qs = [a for a in answers if cond(a)]
        per_channel = {}
        for ch in ("bm25", "vector", "graph", "hybrid"):
            correct = sum(1 for a in qs
                          if _is_correct(set(a["gold_ids"]), set(a["answers"][ch])))
            per_channel[ch] = {"accuracy": round(correct / len(qs), 4) if qs else 0,
                               "n": len(qs)}
        result[key] = per_channel

    num_qs = [a for a in answers if a.get("gold_number") is not None]
    per_channel = {}
    for ch in ("bm25", "vector", "graph", "hybrid"):
        correct = sum(1 for a in num_qs
                      if _num_correct(a["gold_number"], a["pred_number"].get(ch)))
        exact = sum(1 for a in num_qs
                    if a["pred_number"].get(ch) == a["gold_number"])
        per_channel[ch] = {"accuracy": round(correct / len(num_qs), 4) if num_qs else 0,
                           "exact": round(exact / len(num_qs), 4) if num_qs else 0,
                           "n": len(num_qs)}
    result["agg_number"] = per_channel
    return result


def ablation(traces: list[dict], answers: list[dict]) -> dict:
    """图谱消融：有/无图谱通道在多跳+聚合上的 Hit@3 与准确率。"""
    def hit3(qs, ch):
        hits, n = 0, 0
        for t in qs:
            gold = set(t["gold_ids"])
            if not gold:
                continue
            n += 1
            if set(_ranked_ids(t, ch)[:3]) & gold:
                hits += 1
        return round(hits / n, 4) if n else 0

    mh = [t for t in traces if t["qtype"] == "multi" and not t.get("gold_number")]
    ah = [t for t in traces if t["qtype"] == "agg" and not t.get("gold_number")]

    def acc(qs, ch):
        correct, n = 0, 0
        for a in qs:
            n += 1
            if _is_correct(set(a["gold_ids"]), set(a["answers"][ch])):
                correct += 1
        return round(correct / n, 4) if n else 0

    ma = [a for a in answers if a["qtype"] == "multi"]
    aa = [a for a in answers if a["qtype"] == "agg" and a.get("gold_number") is None]
    return {
        "multi_hop": {
            "graph_on_hybrid": {"hit_at_3": hit3(mh, "graph"),
                                "accuracy": acc(ma, "hybrid")},
            "graph_off_vector_only": {"hit_at_3": hit3(mh, "vector"),
                                      "accuracy": acc(ma, "vector")},
        },
        "aggregation": {
            "graph_on_hybrid": {"hit_at_3": hit3(ah, "graph"),
                                "accuracy": acc(aa, "hybrid")},
            "graph_off_vector_only": {"hit_at_3": hit3(ah, "vector"),
                                      "accuracy": acc(aa, "vector")},
        },
    }


def run(ctx):
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    traces = [json.loads(l) for l in TRACE_PATH.read_text(encoding="utf-8").splitlines()]
    answers = [json.loads(l) for l in ANSWER_PATH.read_text(encoding="utf-8").splitlines()]

    extract = extraction_scores()
    retrieval = retrieval_scores(traces)
    e2e = answer_scores(answers)
    abl = ablation(traces, answers)

    # 整体加权（非数值题）
    def overall(metric_map, key, channels=("bm25", "vector", "graph")):
        vals = [metric_map[q][ch][key] for q in ("single", "multi", "agg")
                for ch in channels if ch in metric_map[q]]
        return round(sum(vals) / len(vals), 4)

    summary = {
        "extraction_sample300": extract,
        "retrieval_hit_at_3": {q: {ch: retrieval[q][ch]["hit_at_3"] for ch in retrieval[q]}
                               for q in retrieval},
        "retrieval_mrr": {q: {ch: retrieval[q][ch]["mrr"] for ch in retrieval[q]}
                          for q in retrieval},
        "retrieval_detail": retrieval,
        "end_to_end_accuracy": e2e,
        "ablation_graph_channel": abl,
        "question_count": len(traces),
    }
    (EVAL_DIR / "metrics_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    with open(EVAL_DIR / "per_question.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["qid", "qtype", "intent", "query", "gold_number",
                    "bm25_hit3", "vector_hit3", "graph_hit3",
                    "graph_correct", "hybrid_correct"])
        ans_by_id = {a["qid"]: a for a in answers}
        for t in traces:
            gold = set(t["gold_ids"])
            row = [t["qid"], t["qtype"], t["intent"], t["query"], t.get("gold_number")]
            for ch in ("bm25", "vector", "graph"):
                row.append(int(bool(set(_ranked_ids(t, ch)[:3]) & gold)))
            a = ans_by_id[t["qid"]]

            def correct(ch):
                if t.get("gold_number") is not None:
                    return int(_num_correct(t["gold_number"], a["pred_number"].get(ch)))
                return int(_is_correct(gold, set(a["answers"][ch])))

            row += [correct("graph"), correct("hybrid")]
            w.writerow(row)

    ctx.log(f"评测完成：实体 F1 {extract['entity']['f1']}，关系 F1 {extract['relation']['f1']}（300 条抽检）")
    ctx.log("多跳归因 Hit@3：BM25 {} / 向量 {} / GraphRAG {}".format(
        retrieval["multi"]["bm25"]["hit_at_3"], retrieval["multi"]["vector"]["hit_at_3"],
        retrieval["multi"]["graph"]["hit_at_3"]))
    ctx.log("端到端准确率（多跳）：BM25 {} / 向量 {} / GraphRAG {} / 双路融合 {}".format(
        e2e["multi"]["bm25"]["accuracy"], e2e["multi"]["vector"]["accuracy"],
        e2e["multi"]["graph"]["accuracy"], e2e["multi"]["hybrid"]["accuracy"]))
    ctx.log("消融（多跳 Hit@3）：有图 {} vs 无图 {}".format(
        abl["multi_hop"]["graph_on_hybrid"]["hit_at_3"],
        abl["multi_hop"]["graph_off_vector_only"]["hit_at_3"]))
    return summary
