"""节点 6：双路索引构建 - BM25（jieba 分词）+ faiss 向量索引 + 实体检索索引。"""
import json
import pickle

import faiss
import jieba
import numpy as np
from rank_bm25 import BM25Okapi

from ...config import INDEX_DIR, OUTPUT_DIR, settings
from ...embedding import embedder
from ..seeds import knowledge_base as kbmod
from ...pipeline.nodes.s4_align import clean_surface
from ...retrieval.chunking import build_chunks


def tokenize(text: str) -> list[str]:
    toks = jieba.lcut(text.strip())
    return [t for t in toks if t.strip()]


def run(ctx):
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    chunks = build_chunks()
    texts = [c["text"] for c in chunks]
    ctx.log(f"切块完成：{len(chunks)} 个 chunk（SOP 章节 / 异常单不良块 / 通知单条目）")

    # 1) BM25
    tokenized = [tokenize(t) for t in texts]
    bm25 = BM25Okapi(tokenized)
    with open(INDEX_DIR / "bm25.pkl", "wb") as f:
        pickle.dump({"bm25": bm25, "chunk_ids": [c["chunk_id"] for c in chunks]}, f)

    # 2) 向量（批处理）
    vecs = embedder.embed(texts)
    vecs = vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)
    dim = vecs.shape[1]
    faiss_index = faiss.IndexFlatIP(dim)
    faiss_index.add(np.ascontiguousarray(vecs))
    # faiss C++ fopen 不支持中文路径，序列化为字节后用 Python 写入
    (INDEX_DIR / "chunks.faiss").write_bytes(
        faiss.serialize_index(faiss_index).tobytes())
    (INDEX_DIR / "chunks.dim").write_text(str(dim), encoding="utf-8")

    # 3) 实体链接索引：全量别名表 + 实体名向量
    kb = kbmod.build_kb(settings.seed)
    kbmod.build_scenarios(kb, settings.seed)
    alias_table: dict[str, list[tuple[str, str]]] = {}
    for eid, e in kb["entities"].items():
        alias_table.setdefault(clean_surface(e.name), []).append((eid, e.label))
        for a in e.aliases:
            alias_table.setdefault(clean_surface(a), []).append((eid, e.label))
    # 对齐阶段新增实体也纳入
    aligned_path = OUTPUT_DIR / "entities_aligned.jsonl"
    for line in aligned_path.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        for s in r.get("surfaces", []):
            alias_table.setdefault(clean_surface(s), []).append((r["entity_id"], r["label"]))
    with open(INDEX_DIR / "entity_alias.json", "w", encoding="utf-8") as f:
        json.dump(alias_table, f, ensure_ascii=False)

    # 实体名向量（用于问题实体链接的语义兜底）
    name_items = [(eid, e.name, e.label) for eid, e in kb["entities"].items()]
    name_vecs = embedder.embed([n for _, n, _ in name_items])
    name_vecs = name_vecs / (np.linalg.norm(name_vecs, axis=1, keepdims=True) + 1e-9)
    nindex = faiss.IndexFlatIP(name_vecs.shape[1])
    nindex.add(np.ascontiguousarray(name_vecs))
    (INDEX_DIR / "entity_names.faiss").write_bytes(
        faiss.serialize_index(nindex).tobytes())
    with open(INDEX_DIR / "entity_names.json", "w", encoding="utf-8") as f:
        json.dump([{"id": i, "name": n, "label": l} for i, n, l in name_items],
                  f, ensure_ascii=False)

    stats = {
        "chunks": len(chunks),
        "bm25_tokens": sum(len(t) for t in tokenized),
        "vector_dim": dim,
        "entity_alias_surfaces": len(alias_table),
        "entity_name_index": len(name_items),
        "embedding_provider": settings.embedding_provider,
    }
    ctx.log(f"索引完成：BM25 {stats['bm25_tokens']} 词项；向量 {len(chunks)}×{dim}；实体别名 {len(alias_table)} surface")
    return stats
