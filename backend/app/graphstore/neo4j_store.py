from collections import defaultdict

from neo4j import GraphDatabase

from ..kg_types import ENTITY_LABELS, REL_TYPES
from .base import GraphStore
from .memory import MemoryStore


class Neo4jStore(GraphStore):
    name = "neo4j"

    def __init__(self, uri: str, user: str, password: str) -> None:
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    @classmethod
    def connect(cls, uri: str | None = None, user: str | None = None, password: str | None = None):
        from ..config import settings

        uri = uri or settings.neo4j_uri
        user = user or settings.neo4j_user
        password = password or settings.neo4j_password
        store = cls(uri, user, password)
        store.driver.verify_connectivity()
        store._ensure_constraints()
        return store

    def close(self) -> None:
        self.driver.close()

    def _ensure_constraints(self) -> None:
        with self.driver.session() as s:
            s.run(
                "CREATE CONSTRAINT entity_id IF NOT EXISTS "
                "FOR (e:Entity) REQUIRE e.id IS UNIQUE"
            )

    def reset(self) -> None:
        with self.driver.session() as s:
            s.run("MATCH (n) DETACH DELETE n")

    def upsert_entity(self, entity_id: str, label: str, props: dict) -> None:
        if label not in ENTITY_LABELS:
            raise ValueError(f"非法实体类型：{label}")
        clean = {k: v for k, v in props.items() if v is not None}
        with self.driver.session() as s:
            s.run(
                f"MERGE (e:Entity {{id:$id}}) "
                f"SET e:{label}, e += $props",
                id=entity_id,
                props=clean,
            )

    def upsert_relation(
        self, source_id: str, rel_type: str, target_id: str, props: dict | None = None
    ) -> None:
        if rel_type not in REL_TYPES:
            raise ValueError(f"非法关系类型：{rel_type}")
        with self.driver.session() as s:
            s.run(
                f"MATCH (a:Entity {{id:$sid}}), (b:Entity {{id:$tid}}) "
                f"MERGE (a)-[r:{rel_type}]->(b) "
                f"ON CREATE SET r += $props",
                sid=source_id,
                tid=target_id,
                props=props or {},
            )

    def stats(self) -> dict:
        with self.driver.session() as s:
            nodes = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            rels = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            by_label = defaultdict(int)
            for rec in s.run("MATCH (n) UNWIND labels(n) AS l RETURN l AS l, count(*) AS c"):
                if rec["l"] != "Entity":
                    by_label[rec["l"]] = rec["c"]
            by_rel = {
                rec["t"]: rec["c"]
                for rec in s.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c")
            }
        return {
            "backend": self.name,
            "nodes": nodes,
            "relations": rels,
            "by_label": dict(by_label),
            "by_rel": dict(by_rel),
        }

    @staticmethod
    def _node_dict(node) -> dict:
        d = dict(node)
        labels = [l for l in node.labels if l != "Entity"]
        d["label"] = labels[0] if labels else "Entity"
        d.setdefault("aliases", [])
        d["id"] = node.get("id")
        return d

    def find_entities(self, name: str, label: str | None = None, limit: int = 10) -> list[dict]:
        label_filter = f":{label}" if label else ""
        cypher = (
            f"MATCH (n{label_filter}) WHERE n.name=$name OR $name IN n.aliases "
            f"RETURN n LIMIT $limit"
        )
        with self.driver.session() as s:
            rows = s.run(cypher, name=name, limit=limit).value("n")
        return [self._node_dict(n) for n in rows]

    def neighbors(
        self,
        entity_ids: list[str],
        rel_types: list[str] | None = None,
        hops: int = 2,
        limit: int = 200,
        direction: str = "out",
    ) -> dict:
        arrow = {"out": "-[r*1..%d]->", "in": "<-[r*1..%d]-",
                 "both": "-[r*1..%d]-"}[direction] % hops
        rel_filter = (
            " AND ALL(x IN r WHERE TYPE(x) IN $rel_types)" if rel_types else "")
        cypher = (
            f"MATCH path=(a:Entity){arrow}(b) "
            "WHERE a.id IN $ids" + rel_filter + " "
            "WITH nodes(path) AS ns, relationships(path) AS rs "
            "UNWIND ns AS n UNWIND rs AS e "
            "RETURN collect(DISTINCT n) AS nodes, collect(DISTINCT e) AS edges"
        )
        with self.driver.session() as s:
            rec = s.run(cypher, ids=entity_ids, rel_types=rel_types or []).single()
            seed_rec = s.run("MATCH (n:Entity) WHERE n.id IN $ids RETURN n", ids=entity_ids)
            nodes = [self._node_dict(n) for n in rec["nodes"]]
            nodes += [self._node_dict(r["n"]) for r in seed_rec]
            dedup = {n["id"]: n for n in nodes if n.get("id")}
            edges = [
                {
                    "source": e.start_node.get("id"),
                    "target": e.end_node.get("id"),
                    "rel": e.type,
                }
                for e in rec["edges"]
            ][:limit]
        return {"nodes": list(dedup.values()), "edges": edges}

    def get_entities(self, ids: list[str]) -> dict:
        if not ids:
            return {}
        with self.driver.session() as s:
            rows = s.run("MATCH (n:Entity) WHERE n.id IN $ids RETURN n", ids=ids).value("n")
        return {n.get("id"): self._node_dict(n) for n in rows}

    def outgoing(self, entity_id: str, rel_types: list[str] | None = None) -> list[str]:
        if rel_types:
            cypher = (
                "MATCH (a:Entity {id:$id})-[r]->(b:Entity) "
                "WHERE TYPE(r) IN $rel_types RETURN b.id AS id")
            with self.driver.session() as s:
                return [r["id"] for r in s.run(cypher, id=entity_id, rel_types=rel_types)]
        with self.driver.session() as s:
            return [r["id"] for r in s.run(
                "MATCH (:Entity {id:$id})-->(b:Entity) RETURN b.id AS id", id=entity_id)]

    def sample_subgraph(self, limit: int = 120) -> dict:
        with self.driver.session() as s:
            ids = [r["id"] for r in s.run("MATCH (n) RETURN n.id AS id LIMIT $lim", lim=limit)]
        return self.neighbors(ids, hops=1, limit=limit * 3) if ids else {"nodes": [], "edges": []}

    def causal_paths(self, defect_name: str) -> dict:
        """定向 Cypher：分别取 1 跳（工序/物料/原因）与 2 跳（原因→对策），
        避免变长路径混合后 Python 端截断丢失关键单跳边。"""
        defects = self.find_entities(defect_name, label="Defect", limit=5)
        if not defects:
            return {"root": None, "nodes": [], "edges": [],
                    "answer": {"causes": [], "countermeasures": [],
                               "processes": [], "materials": []}}
        root = defects[0]
        rid = root["id"]
        edges, node_ids = [], set()

        def fetch(rel: str, two_hop: bool = False):
            if two_hop:
                cypher = (
                    "MATCH (a:Entity {id:$id})-[:CAUSED_BY]->(c)-[r]->(b:Entity) "
                    "WHERE type(r)=$rel RETURN r AS r, b AS b")
            else:
                cypher = (
                    "MATCH (a:Entity {id:$id})-[r]->(b:Entity) "
                    "WHERE type(r)=$rel RETURN r AS r, b AS b")
            with self.driver.session() as s:
                for rec in s.run(cypher, id=rid, rel=rel):
                    b = self._node_dict(rec["b"])
                    node_ids.add(b["id"])
                    edges.append({"source": rec["r"].start_node.get("id"),
                                 "target": b["id"], "rel": rel})
                    yield b

        processes = list(fetch("OCCURS_AT"))
        materials = list(fetch("INDUCED_BY_MATERIAL"))
        causes = list(fetch("CAUSED_BY"))
        countermeasures = list(fetch("ADDRESSED_BY", two_hop=True))

        node_ids.add(rid)
        nodes = self._node_dict_list(list(node_ids))
        return {
            "root": root, "nodes": nodes, "edges": edges,
            "answer": {"causes": causes, "countermeasures": countermeasures,
                       "processes": processes, "materials": materials},
        }

    def _node_dict_list(self, ids: list[str]) -> list[dict]:
        if not ids:
            return []
        with self.driver.session() as s:
            rows = s.run("MATCH (n:Entity) WHERE n.id IN $ids RETURN n", ids=ids).value("n")
        out, seen = [], set()
        for n in rows:
            d = self._node_dict(n)
            if d["id"] not in seen:
                seen.add(d["id"])
                out.append(d)
        return out

    def raw_cypher(self, cypher: str) -> dict:
        with self.driver.session() as s:
            result = s.run(cypher)
            records = [dict(r) for r in result]
            summary = result.consume().counters

        def _conv(v):
            if hasattr(v, "labels"):
                return self._node_dict(v)
            if hasattr(v, "type") and hasattr(v, "start_node"):
                return {"rel": v.type, "source": v.start_node.get("id"), "target": v.end_node.get("id")}
            if isinstance(v, list):
                return [_conv(x) for x in v]
            return v

        rows = [{k: _conv(v) for k, v in r.items()} for r in records]
        return {
            "columns": list(records[0].keys()) if records else [],
            "rows": rows[:500],
            "nodes_created": summary.nodes_created,
            "relationships_created": summary.relationships_created,
        }


def fallback_memory() -> MemoryStore:
    return MemoryStore()
