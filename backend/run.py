import argparse
import shutil
import sys

from app.config import (BACKEND_DIR, CORPUS_DIR, DATA_DIR, EVAL_DIR, GOLD_DIR,
                        INDEX_DIR, OUTPUT_DIR, ensure_dirs, settings)


def cmd_serve(args):
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8010, reload=args.reload)


def cmd_node(args):
    from app import db
    from app.pipeline import runner

    ensure_dirs()
    db.init_db()
    if args.node_id == "all":
        runner.run_all(args.start_from)
    else:
        runner.run_node(args.node_id)


def cmd_init(args):
    from app import db

    ensure_dirs()
    db.init_db()
    print("SQLite 已初始化：data/app.db")


def cmd_reset(args):
    """清除全部运行时产物（保留本体与代码），回到未运行状态。"""
    targets = [CORPUS_DIR, GOLD_DIR, OUTPUT_DIR, INDEX_DIR, EVAL_DIR,
               DATA_DIR / "app.db", DATA_DIR / "graph"]
    for p in targets:
        if p.is_dir():
            shutil.rmtree(p)
            print(f"删除目录 {p.relative_to(BACKEND_DIR)}")
        elif p.exists():
            p.unlink()
            print(f"删除文件 {p.relative_to(BACKEND_DIR)}")
    from app import db

    ensure_dirs()
    db.init_db()
    print("重置完成：可运行  python run.py node all  全量运行")


def main():
    parser = argparse.ArgumentParser(description="制造工艺质量知识图谱平台 - 命令行")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve", help="启动 FastAPI 后端")
    p_serve.add_argument("--reload", action="store_true")
    p_serve.set_defaults(func=cmd_serve)

    p_node = sub.add_parser("node", help="运行一个流水线节点（all=按顺序全跑）")
    p_node.add_argument("node_id")
    p_node.add_argument("--start-from", default=None)
    p_node.set_defaults(func=cmd_node)

    p_init = sub.add_parser("init", help="初始化数据目录与 SQLite")
    p_init.set_defaults(func=cmd_init)

    p_reset = sub.add_parser("reset", help="清除全部运行时产物")
    p_reset.set_defaults(func=cmd_reset)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
