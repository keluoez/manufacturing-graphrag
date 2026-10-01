"""节点 5：Neo4j 幂等入图（Neo4j 不可用时写同构内存图库）。

- 实体 MERGE upsert（id 唯一约束在 Neo4jStore 连接时建立）；
- 关系 MERGE 幂等写入，端点缺失自动跳过；
- 入图前后做数量校验与孤立节点统计，结果落盘 graph_stats.json。
"""
import json

from ...config import OUTPUT_DIR
from ..seeds import knowledge_base as kbmod
from ...kg_types import LABEL_CN, REL_CN

ENT_PATH = OUTPUT_DIR / "entities_aligned.jsonl"
REL_PATH = OUTPUT_DIR / "relations_aligned.jsonl"
STATS_PATH = OUTPUT_DIR / "graph_stats.json"


def run(ctx):
    entities = [json.loads(l) for l in ENT_PATH.read_text(encoding="utf-8").splitlines()]
    relations = [json.loads(l) for l in REL_PATH.read_text(encoding="utf-8").splitlines()]

    store = ctx.store
    store.reset()

    # 种子属性补全：规范名、别名等（对齐实体 canonical 已含规范名；补充 KB 属性）
    seed = None
    kb = kbmod.build_kb(42)
    kbmod.build_scenarios(kb, 42)
    seed = kb["entities"]

    written_ent = 0
    for e in entities:
        eid, label = e["entity_id"], e["label"]
        props = {
            "name": e["canonical"],
            "aliases": e.get("surfaces", []),
            "mention_count": e.get("mention_count", 1),
            "align_method": e.get("method", "alias"),
            "is_new_entity": bool(e.get("is_new")),
        }
        if eid in seed:
            extra = seed[eid].attrs or {}
            for k in ("factor_type", "action_type", "severity", "model", "line",
                      "spec", "unit", "seq", "std_params"):
                if k in extra:
                    props[k] = extra[k]
        store.upsert_entity(eid, label, props)
        written_ent += 1

    written_rel, skipped = 0, 0
    for r in relations:
        try:
            store.upsert_relation(r["head_id"], r["rel"], r["tail_id"])
            written_rel += 1
        except Exception:
            skipped += 1

    stats = store.stats()
    # 孤立节点率（基于实际入图结构）
    linked = set()
    sub = store.sample_subgraph(limit=100000)
    for ed in sub["edges"]:
        linked.add(ed["source"])
        linked.add(ed["target"])
    orphans = stats["nodes"] - len({n["id"] for n in sub["nodes"] if n["id"] in linked})
    stats["orphan_nodes"] = max(orphans, 0)
    stats["written_entities"] = written_ent
    stats["written_relations"] = written_rel
    stats["relations_skipped"] = skipped
    stats["label_cn"] = LABEL_CN
    stats["rel_cn"] = REL_CN

    STATS_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    store.persist()
    ctx.log(f"入图完成（后端：{stats['backend']}）：节点 {stats['nodes']}，关系 {stats['relations']}，跳过断端关系 {skipped}")
    ctx.log("分类统计：" + "，".join(f"{LABEL_CN.get(k, k)} {v}" for k, v in stats["by_label"].items()))
    return stats
