from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from ..config import CORPUS_DIR, DATA_DIR, EVAL_DIR, GOLD_DIR, OUTPUT_DIR

router = APIRouter(prefix="/api/data", tags=["data"])

ALLOWED = {
    "corpus": CORPUS_DIR,
    "gold": GOLD_DIR,
    "outputs": OUTPUT_DIR,
    "eval": EVAL_DIR,
}


@router.get("/files")
def files(bucket: str = Query(..., description="corpus/gold/outputs/eval")):
    base = ALLOWED.get(bucket)
    if not base:
        raise HTTPException(400, "非法 bucket")
    items = []
    for p in sorted(base.glob("*")):
        if p.is_file():
            items.append({"name": p.name, "size": p.stat().st_size})
    return {"bucket": bucket, "items": items}


@router.get("/doc/{name}", response_class=PlainTextResponse)
def doc(name: str):
    path = (CORPUS_DIR / name).resolve()
    if not str(path).startswith(str(CORPUS_DIR.resolve())) or not path.exists():
        raise HTTPException(404, "文档不存在")
    return path.read_text(encoding="utf-8")
