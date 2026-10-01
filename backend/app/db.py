import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterable

from .config import DATA_DIR

DB_PATH = DATA_DIR / "app.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS node_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    stats TEXT,
    message TEXT
);
CREATE TABLE IF NOT EXISTS run_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_cache (
    cache_key TEXT PRIMARY KEY,
    response TEXT NOT NULL,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS embed_cache (
    cache_key TEXT PRIMARY KEY,
    response TEXT NOT NULL,
    ts TEXT NOT NULL
);
"""


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        con.executescript(_SCHEMA)
        con.commit()


@contextmanager
def get_conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def query(sql: str, args: Iterable[Any] = ()) -> list[dict]:
    with get_conn() as con:
        rows = con.execute(sql, tuple(args)).fetchall()
        return [dict(r) for r in rows]


def query_one(sql: str, args: Iterable[Any] = ()) -> dict | None:
    rows = query(sql, args)
    return rows[0] if rows else None


def execute(sql: str, args: Iterable[Any] = ()) -> int:
    with get_conn() as con:
        cur = con.execute(sql, tuple(args))
        return cur.lastrowid


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def add_log(node_id: str | None, level: str, message: str) -> int:
    return execute(
        "INSERT INTO run_logs(node_id, ts, level, message) VALUES (?,?,?,?)",
        (node_id, _now(), level, message),
    )


def logs_after(last_id: int, limit: int = 200) -> list[dict]:
    return query(
        "SELECT id, node_id, ts, level, message FROM run_logs WHERE id > ? ORDER BY id LIMIT ?",
        (last_id, limit),
    )


def start_run(node_id: str) -> int:
    return execute(
        "INSERT INTO node_runs(node_id, status, started_at) VALUES (?,?,?)",
        (node_id, "running", _now()),
    )


def finish_run(run_id: int, status: str, stats: dict | None = None, message: str = "") -> None:
    execute(
        "UPDATE node_runs SET status=?, finished_at=?, stats=?, message=? WHERE id=?",
        (status, _now(), json.dumps(stats or {}, ensure_ascii=False), message, run_id),
    )


def latest_run(node_id: str) -> dict | None:
    row = query_one(
        "SELECT id, status, started_at, finished_at, stats, message FROM node_runs "
        "WHERE node_id=? ORDER BY id DESC LIMIT 1",
        (node_id,),
    )
    if row and row.get("stats"):
        try:
            row["stats"] = json.loads(row["stats"])
        except json.JSONDecodeError:
            pass
    return row


def cache_get(key: str, table: str = "llm_cache") -> str | None:
    return query_one(
        f"SELECT response FROM {table} WHERE cache_key=?", (key,)
    ) and query_one(f"SELECT response FROM {table} WHERE cache_key=?", (key,))["response"]


def cache_put(key: str, value: str, table: str = "llm_cache") -> None:
    execute(
        f"INSERT OR REPLACE INTO {table}(cache_key, response, ts) VALUES (?,?,?)",
        (key, value, _now()),
    )


def reset_runtime_tables() -> None:
    with get_conn() as con:
        con.execute("DELETE FROM node_runs")
        con.execute("DELETE FROM run_logs")
        con.commit()
