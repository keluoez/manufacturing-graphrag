from abc import ABC, abstractmethod


class GraphStore(ABC):
    name = "base"

    @abstractmethod
    def reset(self) -> None: ...

    @abstractmethod
    def upsert_entity(self, entity_id: str, label: str, props: dict) -> None: ...

    @abstractmethod
    def upsert_relation(
        self, source_id: str, rel_type: str, target_id: str, props: dict | None = None
    ) -> None: ...

    @abstractmethod
    def stats(self) -> dict: ...

    @abstractmethod
    def sample_subgraph(self, limit: int = 120) -> dict: ...

    @abstractmethod
    def neighbors(
        self,
        entity_ids: list[str],
        rel_types: list[str] | None = None,
        hops: int = 2,
        limit: int = 200,
        direction: str = "out",
    ) -> dict: ...

    @abstractmethod
    def find_entities(self, name: str, label: str | None = None, limit: int = 10) -> list[dict]: ...

    @abstractmethod
    def causal_paths(self, defect_name: str) -> dict: ...

    def outgoing(self, entity_id: str, rel_types: list[str] | None = None) -> list[str]:
        """直接出邻实体 ID。"""
        raise NotImplementedError

    def get_entities(self, ids: list[str]) -> dict:
        """id -> 实体字典（批量）。"""
        raise NotImplementedError

    def raw_cypher(self, cypher: str) -> dict:  # pragma: no cover
        raise NotImplementedError("当前图库不支持自定义 Cypher（仅 Neo4j 模式支持）")

    def persist(self) -> None:
        """持久化（内存图库写盘；Neo4j 为 no-op）。"""
        return None
