"""节点 4：规则后处理 + 实体对齐消歧。

流程：
1) 规则后处理：空白/全半角清洗、单位标准化（℃/度、m/s/米每秒 等）；
2) 别名表精确归一：制造别名规范表（抽取词典之外的完整别名）；
3) 一字符纠错：错别字（如 全捡→全检、X光捡→X光检）按编辑距离 1 找回；
4) embedding 相似度：同类型、同产品上下文的 surface 做最近邻聚类，
   阈值（ALIGN_SIM_THREAD，默认 0.82）以上合并；
5) 仍无法归并的 surface 建为新实体（EX_ 前缀），计入孤立/新增质量指标。
"""
import hashlib
import json
import re
from collections import defaultdict

import numpy as np

from ...config import GOLD_DIR, OUTPUT_DIR, settings
from ...embedding import embedder
from ..seeds import knowledge_base as kbmod
from ...kg_types import ENTITY_LABELS

ENT_IN = OUTPUT_DIR / "entities.jsonl"
REL_IN = OUTPUT_DIR / "relations.jsonl"
ENT_OUT = OUTPUT_DIR / "entities_aligned.jsonl"
REL_OUT = OUTPUT_DIR / "relations_aligned.jsonl"
AUDIT_OUT = OUTPUT_DIR / "align_audit.json"

# 单位标准化（属性/文本后处理用）
UNIT_NORM = {
    "摄氏度": "℃", "度": "℃", "米每秒": "m/s", "毫米": "mm", "微米": "μm",
    "兆帕": "MPa", "千帕": "kPa", "公斤": "kg",
}
FULL2HALF = {ord("　"): ord(" ")}
FULL2HALF.update({
    ord(c): chr(ord(c) - 0xFEE0)
    for c in "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
})


def clean_surface(s: str) -> str:
    s = s.translate(FULL2HALF).strip()
    s = re.sub(r"\s+", "", s)
    return s


def normalize_units(text: str) -> str:
    for k, v in UNIT_NORM.items():
        text = text.replace(k, v)
    return text


def edit1_candidates(surface: str, candidates: list[str]) -> str | None:
    """编辑距离恰好 1 的候选（只针对短名，避免长句误纠）。"""
    if len(surface) > 12:
        return None

    def lev1(a: str, b: str) -> bool:
        if abs(len(a) - len(b)) > 1:
            return False
        if len(a) == len(b):
            return sum(x != y for x, y in zip(a, b)) == 1
        if len(a) + 1 == len(b):
            a, b = b, a
        for i in range(len(a)):
            if a[:i] + a[i + 1:] == b:
                return True
        return False

    for c in candidates:
        if lev1(surface, c):
            return c
    return None


def run(ctx):
    seed = settings.seed
    threshold = settings.align_sim_threshold
    kb = kbmod.build_kb(seed)
    kbmod.build_scenarios(kb, seed)

    # 完整别名归一表 surface -> (entity_id, name)（对齐用全量别名，独立于抽取词典）
    alias_map: dict[str, tuple[str, str, str]] = {}
    canon_by_label: dict[str, list[tuple[str, str]]] = defaultdict(list)
    product_tokens = set()
    for _pid, pname, short, _kind in kbmod.PRODUCTS:
        product_tokens.add(pname)
        product_tokens.add(short)
    for eid, e in kb["entities"].items():
        alias_map[clean_surface(e.name)] = (eid, e.name, e.label)
        for a in e.aliases:
            alias_map.setdefault(clean_surface(a), (eid, e.name, e.label))
        canon_by_label[e.label].append((eid, e.name))

    def product_ctx(surface: str) -> str | None:
        for tok in product_tokens:
            if tok and tok in surface:
                return tok
        return None

    ent_rows = [json.loads(l) for l in ENT_IN.read_text(encoding="utf-8").splitlines()]
    rel_rows = [json.loads(l) for l in REL_IN.read_text(encoding="utf-8").splitlines()]

    # 文档 -> 产品（EX 单产品文档；LLM 抽取的通用原因需按文档产品对齐到参数化实体）
    doc_product: dict[str, str] = {}
    surface_docs: dict[tuple[str, str], set[str]] = defaultdict(set)
    gold_docs = [json.loads(l) for l in GOLD_DIR.joinpath(
        "corpus_gold.jsonl").read_text(encoding="utf-8").splitlines()]
    for d in gold_docs:
        if d.get("product"):
            doc_product[d["doc_id"]] = d["product"]
    for r in ent_rows:
        surface_docs[(clean_surface(r["surface"]), r["label"])].add(r["doc_id"])

    # 聚合 surface（后处理后的规范文本）
    surf_stat: dict[tuple[str, str], dict] = {}
    for r in ent_rows:
        key = (clean_surface(r["surface"]), r["label"])
        st = surf_stat.setdefault(key, {"raw_surfaces": set(), "count": 0})
        st["raw_surfaces"].add(r["surface"])
        st["count"] += 1

    # 待 embedding 对齐的 surface（按标签分组）
    unresolved: dict[str, list[str]] = defaultdict(list)
    mapping: dict[tuple[str, str], dict] = {}
    method_count = defaultdict(int)

    # 规范名按类型索引（后缀归一用）
    name_by_label: dict[str, list[str]] = defaultdict(list)
    for eid, e in kb["entities"].items():
        name_by_label[e.label].append(e.name)

    for (surface, label), st in surf_stat.items():
        hit = alias_map.get(surface)
        if hit:
            mapping[(surface, label)] = {"entity_id": hit[0], "canonical": hit[1], "method": "alias"}
            method_count["alias"] += 1
            continue
        # 后缀归一：surface（≥8字）唯一地作为某规范名后缀（去产品前缀复述）
        suffix_hit = None
        if len(surface) >= 8:
            matches = [n for n in name_by_label.get(label, []) if n.endswith(surface)]
            if len(matches) == 1:
                cname = matches[0]
                eid = next(e.id for e in kb["entities"].values() if e.name == cname)
                suffix_hit = (eid, cname)
        if suffix_hit:
            mapping[(surface, label)] = {
                "entity_id": suffix_hit[0], "canonical": suffix_hit[1], "method": "suffix"}
            method_count["suffix"] = method_count.get("suffix", 0) + 1
            continue
        # 错别字恢复：①已知混淆字反向替换（适用长原因句）②短名编辑距离 1
        fixed_hit = None
        for wrong, right in (("渡", "度"), ("湿", "温"), ("捡", "检"), ("旱", "焊"),
                             ("交", "胶"), ("青", "清"), ("遍", "偏"), ("夕", "锡"), ("庒", "压")):
            if wrong in surface:
                corrected = surface.replace(wrong, right)
                hit2 = alias_map.get(corrected)
                if hit2 and hit2[2] == label:
                    fixed_hit = hit2
                    break
        if not fixed_hit:
            cand_names = [name for _id, name in canon_by_label.get(label, []) if len(name) <= 12]
            cand_names += [a for a, (_i, _n, lab) in alias_map.items() if lab == label and len(a) <= 12]
            fixed = edit1_candidates(surface, list(set(cand_names)))
            if fixed and clean_surface(fixed) in alias_map:
                c = alias_map[clean_surface(fixed)]
                fixed_hit = c if c[2] == label else None
        if fixed_hit:
            mapping[(surface, label)] = {
                "entity_id": fixed_hit[0], "canonical": fixed_hit[1], "method": "typo_fix"}
            method_count["typo_fix"] += 1
            continue
        unresolved[label].append(surface)

    # embedding 聚类：
    #  - 无文档产品上下文的 surface：同标签全局最近邻 + 产品保护
    #  - LLM 抽取的通用原因/对策（出现在 EX 单产品文档）：按文档产品限定候选，
    #    对齐到该产品的参数化实体，避免跨产品断裂
    product_entities: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for eid, e in kb["entities"].items():
        if e.label in ("Cause", "Countermeasure") and "_PD_" in eid:
            # 统一为 PD_T01 形式，与 doc_product 去掉 MAT_ 前缀后的代码一致
            code = "PD_" + eid.split("_PD_")[1].split("_")[0]
            product_entities[(e.label, code)].append((eid, e.name))

    embed_merges = 0
    # (surface,label,product_code) -> 映射（文档产品级，优先于全局 mapping）
    product_mapping: dict[tuple[str, str, str], dict] = {}
    embed_cache_vec: dict[str, np.ndarray] = {}

    def _vec(texts: list[str]) -> np.ndarray:
        miss = [t for t in texts if t not in embed_cache_vec]
        if miss:
            vv = embedder.embed(miss)
            for t, v in zip(miss, vv):
                embed_cache_vec[t] = v
        return np.asarray([embed_cache_vec[t] for t in texts], dtype="float32")

    for label, surfaces in unresolved.items():
        if not surfaces:
            continue
        names = [n for _i, n in canon_by_label.get(label, [])]
        if not names:
            continue
        global_vecs = _vec(surfaces + names)
        global_vecs = global_vecs / (np.linalg.norm(global_vecs, axis=1, keepdims=True) + 1e-9)
        s_vec, n_vec = global_vecs[:len(surfaces)], global_vecs[len(surfaces):]
        sims = s_vec @ n_vec.T
        for i, sf in enumerate(surfaces):
            # 该 surface 出现文档涉及的产品
            doc_ids = surface_docs.get((sf, label), set())
            codes = {doc_product[d].removeprefix("MAT_") for d in doc_ids
                     if d in doc_product}
            # 原因/对策：通用 surface 出现在多个产品文档时，为每个出现过的产品
            # 分别匹配该产品的参数化实体（0.68 阈值 + top1 唯一最优）
            scoped_any = False
            if sf == "回流温度曲线设置不当":
                ctx.log(f"DBG sf codes={len(codes)} cands_sample="
                        f"{len(product_entities.get((label, next(iter(codes))), []))}")
            if label in ("Cause", "Countermeasure") and codes:
                for code in codes:
                    cands = product_entities.get((label, code), [])
                    if not cands:
                        continue
                    cv = _vec([sf] + [n for _i, n in cands])
                    cv = cv / (np.linalg.norm(cv, axis=1, keepdims=True) + 1e-9)
                    cs = cv[0] @ cv[1:].T
                    order = np.argsort(-cs)
                    jj, score = int(order[0]), float(cs[order[0]])
                    # 原因阈值 0.68+margin；对策 LLM 改写幅度大，放宽到 0.60 取 top1
                    margin = score - (float(cs[order[1]]) if len(order) > 1 else 0)
                    need_score = 0.60 if label == "Countermeasure" else 0.68
                    need_margin = 0.0 if label == "Countermeasure" else 0.03
                    if score >= need_score and margin >= need_margin:
                        eid, cname = cands[jj]
                        product_mapping[(sf, label, code)] = {
                            "entity_id": eid, "canonical": cname,
                            "method": "embedding_product", "sim": round(score, 3)}
                        scoped_any = True
            if scoped_any:
                embed_merges += 1
                continue

            j = int(np.argmax(sims[i]))
            score = float(sims[i, j])
            sp, np_ = product_ctx(sf), product_ctx(names[j])
            context_ok = (
                (sp is None and np_ is None)
                or (sp is not None and np_ is not None and sp == np_)
                or (sp is not None and np_ is None)
            )
            if score >= threshold and context_ok:
                eid, cname = canon_by_label[label][j]
                mapping[(sf, label)] = {"entity_id": eid, "canonical": cname,
                                        "method": "embedding", "sim": round(score, 3)}
                embed_merges += 1
            else:
                new_id = "EX_%s_%s" % (
                    label, hashlib.md5(sf.encode("utf-8")).hexdigest()[:10].upper())
                mapping[(sf, label)] = {"entity_id": new_id, "canonical": sf, "method": "new",
                                        "sim": round(score, 3)}
    method_count["embedding"] = embed_merges
    method_count["embedding_product"] = len(product_mapping)
    method_count["new"] = sum(1 for v in mapping.values() if v["method"] == "new")
    ctx.log(f"DEBUG product_mapping={len(product_mapping)} unresolved_surfaces="
            f"{sum(len(v) for v in unresolved.values())}")

    def resolve_all(surface: str, label: str, doc_id: str | None = None) -> list[dict]:
        """实体 surface 解析（可能多个）：
        - 文档有产品：精确到该产品参数化实体，回退全局
        - 文档无产品（如 NTC 多条目）：扇出该 surface 的全部产品级映射 + 全局
        """
        s = clean_surface(surface)
        if doc_id and doc_id in doc_product:
            code = doc_product[doc_id].removeprefix("MAT_")
            pm = product_mapping.get((s, label, code))
            return [pm] if pm else [mapping[(s, label)]]
        pms = [pm for (sf, lab, _c), pm in product_mapping.items()
               if sf == s and lab == label]
        gm = mapping.get((s, label))
        if gm:
            pms.append(gm)
        if not pms:
            raise KeyError((s, label))
        # 去重
        seen, out = set(), []
        for pm in pms:
            if pm["entity_id"] not in seen:
                seen.add(pm["entity_id"])
                out.append(pm)
        return out

    # 收集所有被引用的（含产品级）映射，构建实体输出
    def _add_aligned(aligned: dict, eid: str, label: str, mp: dict, surfaces):
        rec = aligned.setdefault(eid, {
            "entity_id": eid, "label": label,
            "canonical": mp["canonical"], "surfaces": [],
            "mention_count": 0, "method": mp["method"],
        })
        rec["surfaces"].extend(surfaces)
        rec["mention_count"] += len(surfaces)
        if mp["method"] == "new":
            rec["is_new"] = True
        elif mp["method"].startswith("embedding"):
            rec.setdefault("aligned_sims", []).append(mp.get("sim"))

    aligned: dict[str, dict] = {}
    for (surface, label), st in surf_stat.items():
        # 全局映射（产品级匹配成功的 surface 无全局映射，跳过）
        gm = mapping.get((surface, label))
        if gm:
            _add_aligned(aligned, gm["entity_id"], label, gm,
                         sorted(st["raw_surfaces"]))
        # 该 surface 在产品级映射中指向的各产品参数化实体
        for (sf, lab, code), pm in product_mapping.items():
            if sf == surface and lab == label:
                _add_aligned(aligned, pm["entity_id"], label, pm,
                             sorted(st["raw_surfaces"]))
    for rec in aligned.values():
        rec["surfaces"] = sorted(set(rec["surfaces"]))
    ENT_OUT.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in aligned.values()),
        encoding="utf-8")

    # 关系端点归一（按关系所在文档的产品上下文解析）
    rel_aligned, dropped = [], 0
    seen_edges = set()
    for r in rel_rows:
        doc_id = r["doc_id"]
        try:
            hms = resolve_all(r["head"], r["head_label"], doc_id)
            tms = resolve_all(r["tail"], r["tail_label"], doc_id)
        except KeyError:
            dropped += 1
            continue
        for hm in hms:
            for tm in tms:  # 无产品文档扇出（笛卡尔，但去重后仅新增产品级边）
                edge = (hm["entity_id"], r["rel"], tm["entity_id"])
                if edge in seen_edges or hm["entity_id"] == tm["entity_id"]:
                    continue
                seen_edges.add(edge)
                rel_aligned.append({
                    "head_id": hm["entity_id"], "rel": r["rel"],
                    "tail_id": tm["entity_id"], "doc_id": doc_id,
                })
    REL_OUT.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rel_aligned),
        encoding="utf-8")

    # 质量指标：新增实体率、孤立节点率（无任何边）、合并压缩比
    new_entities = [r for r in aligned.values() if r.get("is_new")]
    linked = {e for e1, _r, e2 in seen_edges for e in (e1, e2)}
    orphans = [rid for rid in aligned if rid not in linked]
    by_label = defaultdict(int)
    for r in aligned.values():
        by_label[r["label"]] += 1

    audit = {
        "threshold": threshold,
        "embedding_provider": settings.embedding_provider,
        "surface_count": len(surf_stat),
        "aligned_entity_count": len(aligned),
        "method_count": dict(method_count),
        "new_entity_count": len(new_entities),
        "new_entity_rate": round(len(new_entities) / max(len(aligned), 1), 4),
        "orphan_node_count": len(orphans),
        "orphan_rate": round(len(orphans) / max(len(aligned), 1), 4),
        "relation_count": len(rel_aligned),
        "relation_dropped": dropped,
        "by_label": dict(by_label),
    }
    AUDIT_OUT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    ctx.log(f"对齐完成：{len(surf_stat)} 个 surface → {len(aligned)} 个规范实体；关系 {len(rel_aligned)} 条（丢弃 {dropped}）")
    ctx.log(f"归一方式：别名 {method_count['alias']} / 纠错 {method_count['typo_fix']} / "
            f"embedding {embed_merges} / 新增 {method_count['new']}；"
            f"新增率 {audit['new_entity_rate']:.2%}，孤立率 {audit['orphan_rate']:.2%}")
    return audit
