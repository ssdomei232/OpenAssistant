"""Server management CLI.

Usage:
    python -m app.cli create-user --name 小明 [--key oa_xxx]
    python -m app.cli list-users
    python -m app.cli set-timezone Asia/Shanghai [--user-id usr_xxx]
    python -m app.cli seed-demo [--api-key oa_xxx] [--reset]
    python -m app.cli process-pending
    python -m app.cli merge-cards [--lookback 1800] [--dry-run]
    python -m app.cli show-config
    python -m app.cli run [--host 0.0.0.0] [--port 8000]
"""
from __future__ import annotations

import argparse
import json
import sys

from .config import get_settings, normalize_timezone
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


def cmd_set_timezone(args: argparse.Namespace) -> int:
    """修改用户时区（不改 .env，适合快速纠正已有用户的历史数据）。"""
    try:
        tz_name = normalize_timezone(args.timezone)
    except ValueError as exc:
        print(str(exc))
        return 2
    init_db()
    with SessionLocal() as db:
        if args.user_id:
            users = [db.get(User, args.user_id)]
            if users[0] is None:
                print(f"user not found: {args.user_id}")
                return 1
        else:
            users = list(db.query(User).all())
        if not users:
            print("(no users)")
            return 0
        for user in users:
            user.timezone = tz_name
        db.commit()
        print(
            json.dumps(
                {"updated": len(users), "timezone": tz_name, "user_ids": [u.id for u in users]},
                ensure_ascii=False,
            )
        )
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
    from .services.pipeline import (
        flush_transcribed_conversations,
        pending_segment_ids,
        requeue_stuck_segments,
        transcribe_segment,
    )

    init_db()
    recovered = requeue_stuck_segments()
    transcribed = [transcribe_segment(segment_id) for segment_id in pending_segment_ids(limit=100)]
    # 手动补处理时不等待合并窗口：把所有已转写片段按时间相邻关系立即统一总结
    summarized = flush_transcribed_conversations(force=True)
    print(
        json.dumps(
            {"requeued_stuck": recovered, "transcribed": transcribed, "summarized": summarized},
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


def cmd_requeue_failed(args: argparse.Namespace) -> int:
    from .services.pipeline import requeue_failed_segments

    init_db()
    count = requeue_failed_segments(args.user_id)
    print(json.dumps({"requeued_failed": count, "status": "queued"}, ensure_ascii=False))
    return 0


def cmd_requeue_stuck(_args: argparse.Namespace) -> int:
    from .services.pipeline import requeue_stuck_segments

    init_db()
    count = requeue_stuck_segments()
    print(json.dumps({"requeued_stuck": count, "status": "queued"}, ensure_ascii=False))
    return 0


def _gpu_report() -> dict:
    """torch/CUDA 诊断：GPU 是否对 torch 可见（ASR / 声纹模型都依赖它）。"""
    try:
        import torch  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return {"torch": None, "error": f"{type(exc).__name__}: {exc}"}
    info: dict = {"torch": torch.__version__, "torch_cuda": torch.version.cuda or None}
    try:
        available = bool(torch.cuda.is_available())
    except Exception as exc:  # noqa: BLE001
        available = False
        info["error"] = f"{type(exc).__name__}: {exc}"
    info["cuda_available"] = available
    if available:
        try:
            count = torch.cuda.device_count()
            info["device_count"] = count
            info["devices"] = [torch.cuda.get_device_name(index) for index in range(count)]
        except Exception:  # noqa: BLE001 - 设备名查询失败不影响主信息
            pass
    else:
        info["hint"] = (
            "torch 看不到 GPU：当前安装的是 CPU 版 wheel，或驱动与 CUDA 版本不匹配。"
            "GPU 主机请安装 CUDA 版 torch，例如 "
            "pip install torch --index-url https://download.pytorch.org/whl/cu132"
        )
    return info


def cmd_doctor(_args: argparse.Namespace) -> int:
    """打印服务端运行诊断：DB、GPU、provider 模式、队列计数。"""
    from sqlalchemy import func, select

    from .db import SessionLocal
    from .models import AudioSegment, Card
    from .providers import build_providers

    init_db()
    settings = get_settings()
    providers = build_providers(settings)
    with SessionLocal() as db:
        counts = dict(
            db.execute(select(AudioSegment.status, func.count(AudioSegment.id)).group_by(AudioSegment.status)).all()
        )
        cards = db.scalar(select(func.count(Card.id))) or 0
    report = {
        "database": settings.database_url,
        "storage_dir": str(settings.storage_dir),
        "scheduler_enabled": settings.scheduler_enabled,
        "worker_enabled": settings.worker_enabled,
        "gpu": _gpu_report(),
        "providers": providers.modes,
        "audio_segments": {k: int(v) for k, v in counts.items()},
        "cards": int(cards),
        "hint": "若 queued>0 且 worker_enabled=true，请确认 Server 进程的 /api/v1/health.worker.alive=true",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_merge_cards(args: argparse.Namespace) -> int:
    """让 AI 复盘近期卡片并合并同一对话（手动触发，用于补合并）。"""
    from .services.card_merge import merge_recent_cards

    init_db()
    result = merge_recent_cards(
        user_id=args.user_id,
        lookback_s=args.lookback,
        dry_run=args.dry_run,
        force=True,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def _try_import(module: str) -> bool:
    try:
        __import__(module)
        return True
    except Exception:  # noqa: BLE001 - 依赖缺失/环境损坏都视为不可用
        return False


def _download_funasr_models(settings) -> list[str]:
    """触发 FunASR 模型下载（ModelScope / HuggingFace，缓存到 OA_HF_HOME 或默认缓存）。"""
    from funasr import AutoModel  # type: ignore

    kwargs: dict[str, object] = {
        "model": settings.funasr_model,
        "hub": settings.funasr_hub,
        "device": "cpu",
        "disable_update": False,
    }
    if settings.funasr_vad_model:
        kwargs["vad_model"] = settings.funasr_vad_model
    if settings.funasr_punc_model:
        kwargs["punc_model"] = settings.funasr_punc_model
    print(
        f"downloading [asr] funasr {settings.funasr_model} "
        f"(vad={settings.funasr_vad_model or '-'}, punc={settings.funasr_punc_model or '-'}, hub={settings.funasr_hub}) ...",
        flush=True,
    )
    AutoModel(**kwargs)
    return [
        name
        for name in (settings.funasr_model, settings.funasr_vad_model, settings.funasr_punc_model)
        if name
    ]


def cmd_download_models(args: argparse.Namespace) -> int:
    """预下载 ASR（FunASR，或回退 faster-whisper）与 pyannote 模型。"""
    settings = get_settings()
    only = args.only
    repos: list[tuple[str, str, str | None]] = []
    failed: list[str] = []

    if only in ("all", "asr"):
        use_funasr = _try_import("funasr") and settings.asr_provider in ("auto", "funasr")
        if use_funasr:
            try:
                _download_funasr_models(settings)
                print("  OK -> ModelScope/HuggingFace cache")
            except Exception as exc:  # noqa: BLE001
                failed.append(f"funasr:{settings.funasr_model}")
                print(f"  FAILED: {type(exc).__name__}: {exc}")
        elif _try_import("faster_whisper"):
            # 老部署回退：按 faster-whisper 模型名映射到对应的 HF repo
            try:
                from faster_whisper.utils import _MODELS

                repo = _MODELS.get(settings.whisper_model, settings.whisper_model)
                repos.append(("asr", repo, None))
            except Exception as exc:  # noqa: BLE001
                print(f"skip ASR model: {exc}")
        else:
            print("skip ASR model: neither funasr nor faster-whisper is installed")

    if only in ("all", "pyannote"):
        if settings.hf_token:
            for repo in (
                settings.diarization_model,
                "pyannote/segmentation-3.0",
                settings.embedding_model,
                "pyannote/wespeaker-voxceleb-resnet34-LM",
            ):
                repos.append(("pyannote", repo, settings.hf_token))
        else:
            print("skip pyannote models: OA_HF_TOKEN is empty")

    if repos:
        from huggingface_hub import snapshot_download

        for kind, repo, token in repos:
            print(f"downloading [{kind}] {repo} ...", flush=True)
            try:
                path = snapshot_download(repo_id=repo, token=token)
                print(f"  OK -> {path}")
            except Exception as exc:  # noqa: BLE001
                failed.append(repo)
                print(f"  FAILED: {type(exc).__name__}: {exc}")
    if failed:
        print("\n以下模型下载失败（FunASR 可尝试 OA_FUNASR_HUB=ms；HF 可尝试 OA_HF_ENDPOINT=https://hf-mirror.com 后重试）：")
        for repo in failed:
            print("  -", repo)
        return 1
    print("\n模型下载完成 ✅")
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

    p = sub.add_parser("set-timezone", help="update timezone for all users (or one user)")
    p.add_argument("timezone", help="IANA timezone name, e.g. Asia/Shanghai or UTC")
    p.add_argument("--user-id", default=None, help="only update this user id")
    p.set_defaults(func=cmd_set_timezone)

    p = sub.add_parser("seed-demo", help="create demo data (users, cards, schedule, meetings...)")
    p.add_argument("--api-key", default=None)
    p.add_argument("--reset", action="store_true", help="drop all tables first")
    p.set_defaults(func=cmd_seed_demo)

    p = sub.add_parser("process-pending", help="process queued audio segments (also recovers stuck processing)")
    p.set_defaults(func=cmd_process_pending)

    p = sub.add_parser("requeue-failed", help="reset failed audio segments back to queued")
    p.add_argument("--user-id", default=None)
    p.set_defaults(func=cmd_requeue_failed)

    p = sub.add_parser("requeue-stuck", help="reset stuck 'processing' segments back to queued")
    p.set_defaults(func=cmd_requeue_stuck)

    p = sub.add_parser("merge-cards", help="ask the LLM to merge recently added cards of the same conversation")
    p.add_argument("--user-id", default=None)
    p.add_argument("--lookback", type=float, default=None, help="only cards created within N seconds")
    p.add_argument("--dry-run", action="store_true", help="only print AI-proposed groups, do not merge")
    p.set_defaults(func=cmd_merge_cards)

    p = sub.add_parser("doctor", help="print runtime diagnostics (queue counts, provider modes)")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("download-models", help="pre-download ASR (FunASR) / pyannote models (supports HF mirror)")
    p.add_argument("--only", choices=["all", "asr", "pyannote"], default="all")
    p.set_defaults(func=cmd_download_models)

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
