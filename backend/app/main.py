from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .api import data as data_api
from .api import evalapi as eval_api
from .api import graph as graph_api
from .api import pipeline as pipeline_api
from .api import qa as qa_api
from .config import ensure_dirs, settings
from .retrieval.engine import reset_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    db.init_db()
    from .graphstore import get_store

    store = get_store()
    db.add_log(
        None,
        "info",
        f"后端启动：LLM={settings.llm_provider}，Embedding={settings.embedding_provider}，图库={store.name}",
    )
    yield


app = FastAPI(title="制造工艺质量知识图谱平台", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(pipeline_api.router)
app.include_router(graph_api.router)
app.include_router(qa_api.router)
app.include_router(eval_api.router)
app.include_router(data_api.router)


@app.get("/")
def root():
    return {"name": "kg-manufacturing", "docs": "/docs"}
