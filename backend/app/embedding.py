import hashlib
import json

import numpy as np

from . import db
from .config import settings


class Embedder:
    def __init__(self) -> None:
        self._client = None
        self._local = None

    @property
    def provider(self) -> str:
        return settings.embedding_provider

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 1), dtype="float32")
        if settings.embedding_provider in ("zhipu", "openai"):
            return self._embed_api(texts)
        if settings.embedding_provider == "local":
            return self._embed_local(texts)
        return self._embed_hash(texts)

    def _embed_api(self, texts: list[str]) -> np.ndarray:
        vecs: list[list[float] | None] = [None] * len(texts)
        miss_idx: list[int] = []
        miss_text: list[str] = []
        for i, t in enumerate(texts):
            key = "emb:" + hashlib.sha1(
                f"{settings.embedding_model}:{t}".encode("utf-8")
            ).hexdigest()
            cached = db.cache_get(key, table="embed_cache")
            if cached is not None:
                vecs[i] = json.loads(cached)
            else:
                miss_idx.append(i)
                miss_text.append(t)

        if miss_text:
            if not settings.embedding_api_key or "在此填入" in settings.embedding_api_key:
                return self._embed_hash(texts)
            if self._client is None:
                from openai import OpenAI

                self._client = OpenAI(
                    base_url=settings.embedding_base_url,
                    api_key=settings.embedding_api_key,
                    timeout=60,
                )
            # 智谱 embedding 接口单批最多 64 条
            BATCH = 64
            for start in range(0, len(miss_text), BATCH):
                bt = miss_text[start:start + BATCH]
                bi = miss_idx[start:start + BATCH]
                resp = self._client.embeddings.create(
                    model=settings.embedding_model, input=bt)
                for j, be in enumerate(resp.data):
                    vec = be.embedding
                    vecs[bi[j]] = vec
                    key = "emb:" + hashlib.sha1(
                        f"{settings.embedding_model}:{bt[j]}".encode("utf-8")
                    ).hexdigest()
                    db.cache_put(key, json.dumps(vec), table="embed_cache")

        return np.asarray(vecs, dtype="float32")

    def _embed_local(self, texts: list[str]) -> np.ndarray:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            return self._embed_hash(texts)
        if self._local is None:
            self._local = SentenceTransformer("BAAI/bge-small-zh-v1.5")
        return self._local.encode(texts, normalize_embeddings=True).astype("float32")

    @staticmethod
    def _embed_hash(texts: list[str]) -> np.ndarray:
        from sklearn.feature_extraction.text import HashingVectorizer

        vec = HashingVectorizer(
            analyzer="char_wb",
            ngram_range=(2, 3),
            n_features=1024,
            norm="l2",
            alternate_sign=False,
        )
        return vec.transform(texts).toarray().astype("float32")


embedder = Embedder()
