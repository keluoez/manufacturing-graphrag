from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"
DATA_DIR = BACKEND_DIR / "data"

CORPUS_DIR = DATA_DIR / "corpus"
GOLD_DIR = DATA_DIR / "gold"
OUTPUT_DIR = DATA_DIR / "outputs"
INDEX_DIR = DATA_DIR / "index"
EVAL_DIR = DATA_DIR / "eval"
SCHEMA_DIR = DATA_DIR / "schema"
SEED_DIR = BACKEND_DIR / "app" / "pipeline" / "seeds"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: str = "mock"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"

    embedding_provider: str = "tfidf"
    embedding_base_url: str = "https://open.bigmodel.cn/api/paas/v4/"
    embedding_api_key: str = ""
    embedding_model: str = "embedding-3"
    embed_dim: int = 2048

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "kg12345678"
    graphstore_auto_fallback: bool = True

    seed: int = 42
    llm_cache_enabled: bool = True
    llm_disable_thinking: bool = True  # deepseek-v4 推理模型：结构化抽取任务关闭思考，10x 提速
    llm_extract_max_tokens: int = 16000  # 通知单等长文档 JSON 输出需要更大窗口，分块抽取后仍保留
    qa_llm_natural: bool = False  # 节点8批量评测时是否调 LLM 生成自然语言（评测只判结构化答案，默认关省额度）
    extract_concurrency: int = 4
    align_sim_threshold: float = 0.82
    corpus_size: int = 200


settings = Settings()


def ensure_dirs() -> None:
    for p in [
        DATA_DIR,
        CORPUS_DIR,
        GOLD_DIR,
        OUTPUT_DIR,
        INDEX_DIR,
        EVAL_DIR,
        SCHEMA_DIR,
    ]:
        p.mkdir(parents=True, exist_ok=True)
