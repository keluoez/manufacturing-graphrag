"""节点 1：Schema 设计 - 加载领域本体并做闭域校验，版本化落盘。"""
import json
import shutil

import yaml

from ...config import BACKEND_DIR, OUTPUT_DIR, SCHEMA_DIR
from ...kg_types import ENTITY_LABELS, REL_TYPES, LABEL_CN, REL_CN

CANONICAL = BACKEND_DIR / "data" / "ontology.yaml"


def run(ctx):
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    raw = yaml.safe_load(CANONICAL.read_text(encoding="utf-8"))

    ent_labels = [e["label"] for e in raw["entities"]]
    rel_types = [r["type"] for r in raw["relations"]]

    assert sorted(ent_labels) == sorted(ENTITY_LABELS), (
        f"本体实体类型与代码闭域不一致：{ent_labels} vs {ENTITY_LABELS}"
    )
    assert sorted(rel_types) == sorted(REL_TYPES), (
        f"本体关系类型与代码闭域不一致：{rel_types} vs {REL_TYPES}"
    )
    for r in raw["relations"]:
        assert r["domain"] in ENTITY_LABELS, f"关系 {r['type']} 定义域非法：{r['domain']}"
        assert r["range"] in ENTITY_LABELS, f"关系 {r['type']} 值域非法：{r['range']}"

    out_path = SCHEMA_DIR / "ontology.yaml"
    shutil.copyfile(CANONICAL, out_path)

    required_props = {
        e["label"]: [p["name"] for p in e["properties"] if p.get("required")]
        for e in raw["entities"]
    }
    version_info = {
        "version": raw["version"],
        "name": raw["name"],
        "entity_count": len(ent_labels),
        "relation_count": len(rel_types),
        "entities": {l: LABEL_CN[l] for l in ENTITY_LABELS},
        "relations": {r: REL_CN[r] for r in REL_TYPES},
        "required_props": required_props,
        "query_templates": [q["name"] for q in raw.get("query_templates", [])],
        "path": str(out_path),
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "schema_version.json").write_text(
        json.dumps(version_info, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    ctx.log(f"本体版本 {raw['version']} 校验通过并落盘：{out_path}")
    ctx.log(f"实体 {len(ent_labels)} 类：{ '、'.join(LABEL_CN[l] for l in ent_labels) }")
    ctx.log(f"关系 {len(rel_types)} 类；闭域约束与 kg_types 一致；Cypher 模板 {len(version_info['query_templates'])} 个")
    return {
        "version": raw["version"],
        "entity_types": len(ent_labels),
        "relation_types": len(rel_types),
        "query_templates": len(version_info["query_templates"]),
    }
