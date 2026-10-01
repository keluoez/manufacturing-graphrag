"""文档切块：SOP 按章节、异常单按不良块、通知单按条目；块边界保留实体偏移。"""
import json
import re
from pathlib import Path

from ..config import CORPUS_DIR, GOLD_DIR, OUTPUT_DIR

CHUNK_PATH = OUTPUT_DIR / "chunks.jsonl"


def _split_blocks(text: str, pattern: str):
    """按正则切分，返回 (chunk_text, start_offset)。"""
    out = []
    matches = list(re.finditer(pattern, text))
    if not matches:
        return [(text.strip(), 0)]
    # 首块（抬头）并入第一块
    first = matches[0].start()
    head = text[:first].strip()
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.start():end].strip()
        prefix = head if i == 0 else ""
        chunk = (prefix + "\n" + body).strip()
        out.append((chunk, m.start() - (len(head) + 1 if i == 0 and head else 0)))
    return out


def chunk_document(doc_id: str, text: str) -> list[dict]:
    chunks = []
    if doc_id.startswith("SOP-"):
        # 按一、二、三…章节切块
        secs = list(re.finditer(r"\n?[一二三四五六]、[^\n]*\n", text))
        head = text[: secs[0].start()] if secs else ""
        bounds = [s.start() for s in secs] + [len(text)]
        for i, s in enumerate(secs):
            body = (head + "\n" + text[s.start():bounds[i + 1]]).strip() if i == 0 \
                else text[s.start():bounds[i + 1]].strip()
            if len(body) > 40:
                chunks.append({"text": body, "start": s.start()})
    elif doc_id.startswith("EX-"):
        for body, off in _split_blocks(text, r"\n(?=\d+\.\s责任工序：)"):
            chunks.append({"text": body, "start": max(off, 0)})
    elif doc_id.startswith("NTC-"):
        for body, off in _split_blocks(text, r"\n(?=\d+\.\s适用产品\s)"):
            if "适用产品" in body:
                chunks.append({"text": body, "start": max(off, 0)})
    return chunks


def build_chunks() -> list[dict]:
    """切块并把对齐后的实体 mention 关联到块（供检索答案抽取与溯源）。"""
    gold = {}
    for line in (GOLD_DIR / "corpus_gold.jsonl").read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        gold[d["doc_id"]] = d

    all_chunks = []
    cid = 0
    for path in sorted(CORPUS_DIR.glob("*.txt")):
        doc_id = path.stem
        text = path.read_text(encoding="utf-8")
        for ch in chunk_document(doc_id, text):
            all_chunks.append({
                "chunk_id": f"C{cid:05d}",
                "doc_id": doc_id,
                "genre": doc_id.split("-")[0],
                "text": ch["text"],
            })
            cid += 1

    CHUNK_PATH.write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in all_chunks),
        encoding="utf-8")
    return all_chunks


def load_chunks() -> list[dict]:
    return [json.loads(l) for l in CHUNK_PATH.read_text(encoding="utf-8").splitlines()]
