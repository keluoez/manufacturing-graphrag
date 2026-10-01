import csv
import io
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..config import EVAL_DIR

router = APIRouter(prefix="/api/eval", tags=["eval"])


@router.get("/summary")
def summary():
    path = EVAL_DIR / "metrics_summary.json"
    if not path.exists():
        raise HTTPException(404, "评测结果未生成，请先运行节点 9")
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/per-question")
def per_question():
    path = EVAL_DIR / "per_question.csv"
    if not path.exists():
        raise HTTPException(404, "逐题明细未生成，请先运行节点 9")
    text = path.read_text(encoding="utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return {"items": list(reader)}
