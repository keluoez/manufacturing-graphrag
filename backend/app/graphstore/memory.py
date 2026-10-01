import json
from collections import defaultdict

from ..config import DATA_DIR
from ..kg_types import ENTITY_LABELS, REL_TYPES
from .base import GraphStore

GRAPH_DUMP = DATA_DIR / "graph" / "memory_graph.json"


class MemoryStore(GraphStore):
    name = "memory"

    @classmethod
    def load_or_create(cls) -> "MemoryStore":
        store = cls()
        if GRAPH_DUMP.exists():
            data = json.loads(GRAPH_DUMP.read_text(encoding="utf-8"))
            for n in data.get("nodes", []):
                store.entities[n["id"]] = n
            for e in data.get("edges", []):
                key = (e["source"], e["rel"], e["target"])
                store.edge_keys.add(key)
                store.edges_out[e["source"]].append(dict(e))
                store.edges_in[e["target"]].append(dict(e))
        return store

    def persist(self) -> None:
        GRAPH_DUMP.parent.mkdir(parents=True, exist_ok=True)
        GRAPH_DUMP.write_text(json.dumps({
            "nodes": list(self.entities.values()),
            "edges": [{"source": s, "target": ed["target"], "rel": ed["rel"]}
                      for s, eds in self.edges_out.items() for ed in eds],
        }, ensure_ascii=False), encoding="utf-8")

    def __init__(self) -> None:
        self.entities: dict[str, dict] = {}
        self.edges_out: dict[str, list[dict]] = defaultdict(list)
        self.edges_in: dict[str, list[dict]] = defaultdict(list)
        self.edge_keys: set[tuple] = set()

    def reset(self) -> None:
        self.entities.clear()
        self.edges_out.clear()
        self.edges_in.clear()
        self.edge_keys.clear()

    def upsert_entity(self, entity_id: str, label: str, props: dict) -> None:
        if label not in ENTITY_LABELS:
            raise ValueError(f"非法实体类型：{label}")
        node = self.entities.setdefault(
            entity_id, {"id": entity_id, "label": label, "name": "", "aliases": []}
        )
        node["label"] = label
        for k, v in props.items():
            if k == "aliases":
                merged = list(dict.fromkeys(list(node.get("aliases", [])) + list(v)))
                node["aliases"] = merged
            else:
                node[k] = v

    def upsert_relation(
        self, source_id: str, rel_type: str, target_id: str, props: dict | None = None
    ) -> None:
        if rel_type not in REL_TYPES:
            raise ValueError(f"非法关系类型：{rel_type}")
        if source_id not in self.entities or target_id not in self.entities:
            return
        key = (source_id, rel_type, target_id)
        if key in self.edge_keys:
            return
        self.edge_keys.add(key)
        edge = {"source": source_id, "target": target_id, "rel": rel_type}
        if props:
            edge.update(props)
        self.edges_out[source_id].append(edge)
        self.edges_in[target_id].append(edge)

    def stats(self) -> dict:
        by_label: dict[str, int] = defaultdict(int)
        by_rel: dict[str, int] = defaultdict(int)
        for e in self.entities.values():
            by_label[e["label"]] += 1
        for edges in self.edges_out.values():
            for ed in edges:
                by_rel[ed["rel"]] += 1
        return {
            "backend": self.name,
            "nodes": len(self.entities),
            "relations": len(self.edge_keys),
            "by_label": dict(by_label),
            "by_rel": dict(by_rel),
        }

    def find_entities(self, name: str, label: str | None = None, limit: int = 10) -> list[dict]:
        hits = []
        for e in self.entities.values():
            if label and e["label"] != label:
                continue
            names = [e.get("name", "")] + list(e.get("aliases", []))
            if name in names or any(name in n or n in name for n in names if n):
                hits.append(dict(e))
                if len(hits) >= limit:
                    break
        return hits

    def neighbors(
        self,
        entity_ids: list[str],
        rel_types: list[str] | None = None,
        hops: int = 2,
        limit: int = 200,
        direction: str = "out",
    ) -> dict:
        keep_nodes: dict[str, dict] = {}
        keep_edges: list[dict] = []
        frontier = [i for i in entity_ids if i in self.entities]
        visited = set()
        for _ in range(hops):
            next_frontier: list[str] = []
            for nid in frontier:
                if nid in visited:
                    continue
                visited.add(nid)
                if nid in self.entities:
                    keep_nodes[nid] = dict(self.entities[nid])
                bundles = []
                if direction in ("out", "both"):
                    bundles += [("out", ed) for ed in self.edges_out.get(nid, [])]
                if direction in ("in", "both"):
                    bundles += [("in", ed) for ed in self.edges_in.get(nid, [])]
                for _d, ed in bundles:
                    if rel_types and ed["rel"] not in rel_types:
                        continue
                    keep_edges.append(dict(ed))
                    dst = ed["target"] if _d == "out" else ed["source"]
                    if dst in self.entities:
                        keep_nodes[dst] = dict(self.entities[dst])
                    next_frontier.append(dst)
                if len(keep_edges) >= limit:
                    break
            frontier = next_frontier
            if not frontier or len(keep_edges) >= limit:
                break
        return {"nodes": list(keep_nodes.values()), "edges": keep_edges[:limit]}

    def outgoing(self, entity_id: str, rel_types: list[str] | None = None) -> list[str]:
        return [ed["target"] for ed in self.edges_out.get(entity_id, [])
                if not rel_types or ed["rel"] in rel_types]

    def get_entities(self, ids: list[str]) -> dict:
        return {i: dict(self.entities[i]) for i in ids if i in self.entities}

    def sample_subgraph(self, limit: int = 120) -> dict:
        ids = list(self.entities.keys())[:limit]
        return self.neighbors(ids, hops=1, limit=limit * 3)

    def causal_paths(self, defect_name: str) -> dict:
        defects = self.find_entities(defect_name, label="Defect", limit=5)
        if not defects:
            return {"root": None, "nodes": [], "edges": [], "answer": {}}
        root = defects[0]
        sub = self.neighbors(
            [root["id"]],
            rel_types=["CAUSED_BY", "ADDRESSED_BY", "OCCURS_AT", "INDUCED_BY_MATERIAL"],
            hops=2,
        )
        answer: dict[str, list[dict]] = {
            "causes": [],
            "countermeasures": [],
            "processes": [],
            "materials": [],
        }
        cause_ids = set()
        for ed in sub["edges"]:
            if ed["source"] == root["id"]:
                tgt = self.entities.get(ed["target"])
                if not tgt:
                    continue
                if ed["rel"] == "CAUSED_BY":
                    answer["causes"].append(dict(tgt))
                    cause_ids.add(tgt["id"])
                elif ed["rel"] == "OCCURS_AT":
                    answer["processes"].append(dict(tgt))
                elif ed["rel"] == "INDUCED_BY_MATERIAL":
                    answer["materials"].append(dict(tgt))
        for ed in sub["edges"]:
            if ed["source"] in cause_ids and ed["rel"] == "ADDRESSED_BY":
                tgt = self.entities.get(ed["target"])
                if tgt:
                    answer["countermeasures"].append(dict(tgt))
        return {"root": dict(root), **sub, "answer": answer}
