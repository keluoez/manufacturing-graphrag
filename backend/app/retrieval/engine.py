"""三路检索引擎：BM25 / 向量 RAG / GraphRAG（实体链接 + 多跳子图）。

全部从节点 6 落盘的索引加载；图库经 graphstore 抽象（内存/Neo4j 同构）。
"""
import json
import pickle
from functools import lru_cache

import faiss
import numpy as np

from ..config import INDEX_DIR
from ..embedding import embedder
from ..graphstore import get_store
from ..pipeline.nodes.s4_align import clean_surface
from ..pipeline.nodes.s6_index import tokenize
from .chunking import load_chunks


class RetrievalEngine:
    def __init__(self) -> None:
        self.chunks = {c["chunk_id"]: c for c in load_chunks()}
        with open(INDEX_DIR / "bm25.pkl", "rb") as f:
            pack = pickle.load(f)
        self.bm25 = pack["bm25"]
        self.chunk_ids = pack["chunk_ids"]
        self.faiss = faiss.deserialize_index(
            np.frombuffer((INDEX_DIR / "chunks.faiss").read_bytes(), dtype="uint8"))
        self.alias_table = json.loads((INDEX_DIR / "entity_alias.json").read_text(encoding="utf-8"))
        self.name_rows = json.loads((INDEX_DIR / "entity_names.json").read_text(encoding="utf-8"))
        self.name_faiss = faiss.deserialize_index(
            np.frombuffer((INDEX_DIR / "entity_names.faiss").read_bytes(), dtype="uint8"))
        self.store = get_store()

    def get_entities(self, ids: list[str]) -> dict:
        return self.store.get_entities(ids)

    def names(self, ids: list[str]) -> list[dict]:
        uniq = list(dict.fromkeys(ids))
        ents = self.store.get_entities(uniq)
        return [{"id": i, "name": ents[i].get("name", i),
                 "label": ents[i].get("label")} for i in uniq if i in ents]

    # ------------------------------------------------------------ 实体链接
    def link_entities(self, query: str, labels: list[str] | None = None) -> list[dict]:
        q = clean_surface(query)
        hits = []
        seen = set()
        for surface in sorted(self.alias_table.keys(), key=len, reverse=True):
            pos = q.find(surface)
            if pos >= 0:
                for eid, label in self.alias_table[surface]:
                    if labels and label not in labels:
                        continue
                    if eid not in seen:
                        seen.add(eid)
                        hits.append({"id": eid, "label": label, "surface": surface,
                                     "method": "alias", "start": pos})
        # embedding 语义补链（短问题无精确别名时）
        if not any(h["label"] in ("Defect", "Process", "Material", "Device") for h in hits):
            v = embedder.embed([query])
            v = v / (np.linalg.norm(v) + 1e-9)
            D, I = self.name_faiss.search(np.ascontiguousarray(v.astype("float32")), 5)
            for dist, idx in zip(D[0], I[0]):
                row = self.name_rows[idx]
                if labels and row["label"] not in labels:
                    continue
                if row["id"] not in seen and float(dist) >= 0.45:
                    seen.add(row["id"])
                    hits.append({"id": row["id"], "label": row["label"],
                                 "surface": row["name"], "method": "embedding", "score": float(dist)})
        return hits

    def entities_in_text(self, text: str, labels: list[str] | None = None) -> list[str]:
        q = clean_surface(text)
        out, seen = [], set()
        for surface in sorted(self.alias_table.keys(), key=len, reverse=True):
            if q.find(surface) >= 0:
                for eid, label in self.alias_table[surface]:
                    if labels and label not in labels:
                        continue
                    if eid not in seen:
                        seen.add(eid)
                        out.append(eid)
        return out

    # ------------------------------------------------------------ BM25
    def bm25_search(self, query: str, k: int = 3) -> list[dict]:
        scores = self.bm25.get_scores(tokenize(query))
        idx = np.argsort(-scores)[:k]
        out = []
        for i in idx:
            cid = self.chunk_ids[int(i)]
            out.append({"chunk_id": cid, "score": float(scores[i]),
                        "doc_id": self.chunks[cid]["doc_id"],
                        "text": self.chunks[cid]["text"][:400]})
        return out

    # ------------------------------------------------------------ 向量
    def vector_search(self, query: str, k: int = 3) -> list[dict]:
        v = embedder.embed([query])
        v = v / (np.linalg.norm(v) + 1e-9)
        D, I = self.faiss.search(np.ascontiguousarray(v.astype("float32")), k)
        out = []
        for dist, i in zip(D[0], I[0]):
            cid = self.chunk_ids[int(i)]
            out.append({"chunk_id": cid, "score": float(dist),
                        "doc_id": self.chunks[cid]["doc_id"],
                        "text": self.chunks[cid]["text"][:400]})
        return out

    # ------------------------------------------------------------ GraphRAG
    def graph_query(self, query: str) -> dict:
        links = self.link_entities(query)
        defects = [h for h in links if h["label"] == "Defect"]
        processes = [h for h in links if h["label"] == "Process"]
        materials = [h for h in links if h["label"] == "Material" and not h["id"].startswith("MAT_PD")]
        products = [h for h in links if h["id"].startswith("MAT_PD")]
        # 问题含具体产品时，原因/对策按产品代码过滤（产品-工序-参数化实体）
        codes = [h["id"].removeprefix("MAT_") for h in products]

        def in_scope(eid: str) -> bool:
            return not codes or any(f"_{c}_" in eid for c in codes)

        answer = {"causes": [], "countermeasures": [], "processes": [],
                  "defects": [], "materials": [], "devices": []}
        triples, subgraph = [], {"nodes": [], "edges": []}

        if defects:
            for h in defects:
                if codes:
                    # 带产品约束的定向 2 跳：缺陷 →(本产品)原因 →(本产品)对策/工序/物料
                    res = self._scoped_causal(h["id"], codes)
                    # 注意：_scoped_causal 的 nodes/edges 嵌在 res["subgraph"] 内，
                    # 与 causal_paths（顶层展开）结构不同，不能直接传整个 res
                    subgraph = self._merge_subgraph(subgraph, res["subgraph"])
                    answer["causes"] += res["answer"]["causes"]
                    answer["countermeasures"] += res["answer"]["countermeasures"]
                    answer["processes"] += res["answer"]["processes"]
                    answer["materials"] += res["answer"]["materials"]
                    triples += res["triples"]
                else:
                    res = self.store.causal_paths(h["surface"])
                    if not res.get("root"):
                        continue
                    subgraph = self._merge_subgraph(subgraph, res)
                    answer["causes"] += res["answer"].get("causes", [])
                    answer["countermeasures"] += res["answer"].get("countermeasures", [])
                    answer["processes"] += res["answer"].get("processes", [])
                    answer["materials"] += res["answer"].get("materials", [])
                    triples += res["edges"]
        elif processes:
            for h in processes:
                # 反向：工序 <- OCCURS_AT <- 不良
                sub_in = self.store.neighbors(
                    [h["id"]], rel_types=["OCCURS_AT", "USES_MATERIAL"],
                    hops=1, limit=100, direction="in")
                subgraph = self._merge_subgraph(subgraph, sub_in)
                triples += sub_in["edges"]
                nodes_by_id = {n["id"]: n for n in sub_in["nodes"]}
                defect_ids = [e["source"] for e in sub_in["edges"] if e["rel"] == "OCCURS_AT"]
                answer["defects"] += [nodes_by_id[i] for i in defect_ids if i in nodes_by_id]
                # 正向：不良 → 原因 → 对策
                if defect_ids:
                    sub_out = self.store.neighbors(
                        defect_ids, rel_types=["CAUSED_BY", "ADDRESSED_BY"],
                        hops=2, limit=400, direction="out")
                    subgraph = self._merge_subgraph(subgraph, sub_out)
                    triples += sub_out["edges"]
                    n2 = {n["id"]: n for n in sub_out["nodes"]}
                    answer["countermeasures"] += [
                        n2[e["target"]] for e in sub_out["edges"]
                        if e["rel"] == "ADDRESSED_BY" and e["target"] in n2
                        and in_scope(e["target"])]
                # 工序 -> 物料
                sub_mat = self.store.neighbors(
                    [h["id"]], rel_types=["USES_MATERIAL"], hops=1, limit=50)
                subgraph = self._merge_subgraph(subgraph, sub_mat)
                triples += sub_mat["edges"]
                n3 = {n["id"]: n for n in sub_mat["nodes"]}
                answer["materials"] += [n3[e["target"]] for e in sub_mat["edges"]
                                        if e["target"] in n3]
        elif materials:
            for h in materials:
                # 反向：物料 <- INDUCED_BY_MATERIAL <- 不良
                sub_in = self.store.neighbors(
                    [h["id"]], rel_types=["INDUCED_BY_MATERIAL"],
                    hops=1, limit=100, direction="in")
                subgraph = self._merge_subgraph(subgraph, sub_in)
                triples += sub_in["edges"]
                nodes_by_id = {n["id"]: n for n in sub_in["nodes"]}
                defect_ids = [e["source"] for e in sub_in["edges"]
                              if e["rel"] == "INDUCED_BY_MATERIAL"]
                answer["defects"] += [nodes_by_id[i] for i in defect_ids if i in nodes_by_id]
                if defect_ids:
                    sub_out = self.store.neighbors(
                        defect_ids, rel_types=["OCCURS_AT", "CAUSED_BY", "ADDRESSED_BY"],
                        hops=3, limit=400, direction="out")
                    subgraph = self._merge_subgraph(subgraph, sub_out)
                    triples += sub_out["edges"]
                    n2 = {n["id"]: n for n in sub_out["nodes"]}
                    answer["processes"] += [n2[e["target"]] for e in sub_out["edges"]
                                            if e["rel"] == "OCCURS_AT" and e["target"] in n2]
                    answer["countermeasures"] += [
                        n2[e["target"]] for e in sub_out["edges"]
                        if e["rel"] == "ADDRESSED_BY" and e["target"] in n2
                        and in_scope(e["target"])]
        else:
            # 退化：用语义链接到的任意实体取邻居
            if links:
                sub = self.store.neighbors([links[0]["id"]], hops=2, limit=100)
                subgraph = self._merge_subgraph(subgraph, sub)
                triples = sub["edges"]

        # 去重 answer
        for k in answer:
            dedup = {e["id"]: e for e in answer[k]}
            answer[k] = list(dedup.values())
        triples = list({(e["source"], e["rel"], e["target"]): e for e in triples}.values())
        return {"links": links, "answer": answer, "triples": triples, "subgraph": subgraph}

    def _scoped_causal(self, defect_id: str, codes: list[str]) -> dict:
        """缺陷 →(产品过滤)原因 →(产品过滤)对策，同时回收工序与物料。"""
        store = self.store

        def in_scope(eid: str) -> bool:
            return any(f"_{c}_" in eid for c in codes)

        root = store.get_entities([defect_id]).get(defect_id)
        nodes = {defect_id: root} if root else {}
        edges = []
        cause_ids = [i for i in store.outgoing(defect_id, ["CAUSED_BY"]) if in_scope(i)]
        nodes.update(store.get_entities(cause_ids))
        for cid in cause_ids:
            edges.append({"source": defect_id, "target": cid, "rel": "CAUSED_BY"})
        cm_ids = set()
        for cid in cause_ids:
            for t in store.outgoing(cid, ["ADDRESSED_BY"]):
                if in_scope(t) and t not in cm_ids:
                    cm_ids.add(t)
                    edges.append({"source": cid, "target": t, "rel": "ADDRESSED_BY"})
        proc_ids = store.outgoing(defect_id, ["OCCURS_AT"])
        mat_ids = store.outgoing(defect_id, ["INDUCED_BY_MATERIAL"])
        rest_ids = list(cm_ids | set(proc_ids) | set(mat_ids))
        nodes.update(store.get_entities(rest_ids))
        for i in proc_ids:
            edges.append({"source": defect_id, "target": i, "rel": "OCCURS_AT"})
        for i in mat_ids:
            edges.append({"source": defect_id, "target": i, "rel": "INDUCED_BY_MATERIAL"})
        cms = [nodes[i] for i in cm_ids if i in nodes]
        procs = [nodes[i] for i in proc_ids if i in nodes]
        mats = [nodes[i] for i in mat_ids if i in nodes]
        return {
            "root": root, "answer": {
                "causes": [nodes[i] for i in cause_ids],
                "countermeasures": cms, "processes": procs, "materials": mats},
            "triples": edges,
            "subgraph": {"nodes": list(nodes.values()), "edges": edges},
        }

    @staticmethod
    def _merge_subgraph(a: dict, b: dict) -> dict:
        nodes = {n["id"]: n for n in a.get("nodes", [])}
        for n in b.get("nodes", []):
            nodes[n["id"]] = n
        edges = {(e["source"], e["rel"], e["target"]): e for e in a.get("edges", [])}
        for e in b.get("edges", []):
            edges[(e["source"], e["rel"], e["target"])] = e
        return {"nodes": list(nodes.values()), "edges": list(edges.values())}

    # ------------------------------------------------------------ 双路融合
    def hybrid_answer(self, query: str, k: int = 3) -> dict:
        gres = self.graph_query(query)
        vhits = self.vector_search(query, k)
        labels = ["Countermeasure", "Cause", "Process", "Defect", "Material", "Device"]
        vec_ids = []
        for h in vhits:
            vec_ids += self.entities_in_text(h["text"], labels)
        graph_ids = set()
        for arr in gres["answer"].values():
            graph_ids.update(e["id"] for e in arr)
        # 图谱优先，向量补充（开放语义兜底）
        fused = list(graph_ids) + [i for i in dict.fromkeys(vec_ids) if i not in graph_ids]
        return {"graph": gres, "vector_hits": vhits, "answer_ids": fused}


_engine: RetrievalEngine | None = None


def get_engine() -> RetrievalEngine:
    global _engine
    if _engine is None:
        _engine = RetrievalEngine()
    return _engine


def reset_engine() -> None:
    global _engine
    _engine = None
