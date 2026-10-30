"""Server management CLI.

Usage:
    python -m app.cli create-user --name 小明 [--key oa_xxx]
    python -m app.cli list-users
    python -m app.cli seed-demo [--api-key oa_xxx] [--reset]
    python -m app.cli process-pending
    python -m app.cli show-config
    python -m app.cli run [--host 0.0.0.0] [--port 8000]
"""
from __future__ import annotations

import argparse
import json
import sys

from .config import get_settings
from .db import SessionLocal, init_db
from .models import User
from .security import create_user
from .utils import hash_api_key


def cmd_create_user(args: argparse.Namespace) -> int:
    init_db()
    with SessionLocal() as db:
        user, api_key = create_user(db, display_name=args.name, api_key=args.key, aliases=args.aliases or [])
        db.commit()
        print(f"user_id: {user.id}")
        print(f"display_name: {user.display_name}")
        print(f"api_key: {api_key}")
        print("请把 api_key 填入 App 设置（服务端只保存摘要）。")
    return 0


def cmd_list_users(_args: argparse.Namespace) -> int:
    init_db()
    with SessionLocal() as db:
        users = db.query(User).all()
        if not users:
            print("(no users)")
            return 0
        for user in users:
            print(f"{user.id}\t{user.display_name}\t{user.timezone}\talias={user.aliases}")
    return 0


def cmd_seed_demo(args: argparse.Namespace) -> int:
    from .demo import seed_demo

    init_db()
    if args.reset:
        from .db import Base, engine

        Base.metadata.drop_all(bind=engine)
        init_db()
    with SessionLocal() as db:
        result = seed_demo(db, api_key=args.api_key)
        db.commit()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_process_pending(_args: argparse.Namespace) -> int:
    from .services.pipeline import pending_segment_ids, process_segment

    init_db()
    processed = []
    for segment_id in pending_segment_ids(limit=100):
        processed.append(process_segment(segment_id))
    print(json.dumps({"processed": processed}, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_show_config(_args: argparse.Namespace) -> int:
    settings = get_settings()
    data = settings.model_dump()
    for key in ("llm_api_key", "dingtalk_webhook", "dingtalk_secret", "bootstrap_api_key", "api_key_pepper"):
        if data.get(key):
            data[key] = "***"
    data["storage_dir"] = str(data["storage_dir"])
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def cmd_api_key_hash(args: argparse.Namespace) -> int:
    print(hash_api_key(args.key, get_settings().api_key_pepper))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openassistant-server", description="OpenAssistant Server CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-user", help="create a user and print its API key")
    p.add_argument("--name", default="我")
    p.add_argument("--key", default=None, help="explicit API key (otherwise generated)")
    p.add_argument("--aliases", nargs="*", default=None, help="user aliases / names")
    p.set_defaults(func=cmd_create_user)

    p = sub.add_parser("list-users", help="list users")
    p.set_defaults(func=cmd_list_users)

    p = sub.add_parser("seed-demo", help="create demo data (users, cards, schedule, meetings...)")
    p.add_argument("--api-key", default=None)
    p.add_argument("--reset", action="store_true", help="drop all tables first")
    p.set_defaults(func=cmd_seed_demo)

    p = sub.add_parser("process-pending", help="process queued audio segments")
    p.set_defaults(func=cmd_process_pending)

    p = sub.add_parser("show-config", help="print effective settings")
    p.set_defaults(func=cmd_show_config)

    p = sub.add_parser("api-key-hash", help="print the sha256 hash stored for an API key")
    p.add_argument("key")
    p.set_defaults(func=cmd_api_key_hash)

    p = sub.add_parser("run", help="run the API server")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
