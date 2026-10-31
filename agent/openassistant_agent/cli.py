"""命令行入口（``python -m openassistant_agent``）。

子命令：

* ``run``        常驻录音/切片/上传；
* ``status``     打印配置与队列统计；
* ``flush``      立即上传一次；
* ``config``     查看/写入配置（JSON/TOML）；
* ``selftest``   合成音频跑通 VAD/切片/入队/mock 上传/BLE 全链路；
* ``ble-serve``  仅运行 BLE GATT 服务（``--mock`` 无需蓝牙）。
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .ble import BleManager, BlessBleServer, MockBleTransport
from .capture import AudioSourceError, SyntheticSource, create_source
from .config import AgentConfig, ConfigError
from .service import AgentService
from .spool import SpoolStore
from .uploader import HttpTransport, MockTransport, Uploader

__all__ = ["build_parser", "main"]

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(level: str = "INFO") -> None:
    numeric = getattr(logging, str(level).upper(), logging.INFO)
    logging.basicConfig(level=numeric, format=LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openassistant-agent",
        description="OpenAssistant 树莓派录音端",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config",
        default=None,
        help="配置文件路径（默认 /etc/openassistant/agent.toml 或 $OA_CONFIG）",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="日志级别：DEBUG/INFO/WARNING/ERROR",
    )
    parser.add_argument(
        "--timezone",
        default=None,
        help="覆盖配置中的时区（IANA 名称，如 Asia/Shanghai、UTC）",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub: argparse.ArgumentParser) -> argparse.ArgumentParser:
        """让 --config/--log-level/--timezone 也可以写在子命令之后。"""
        sub.add_argument("--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        sub.add_argument("--log-level", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        sub.add_argument("--timezone", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        return sub

    run = add_common(subparsers.add_parser("run", help="常驻运行：采集 -> 切片 -> 上传"))
    run.add_argument("--source", choices=["device", "file", "synthetic"], default=None)
    run.add_argument("--input", default=None, help="--source file 时的 wav 路径")
    run.add_argument("--duration", type=float, default=None, help="运行秒数后优雅退出")
    run.add_argument("--max-blocks", type=int, default=None, help="处理 N 个音频块后退出")
    run.add_argument("--no-upload", action="store_true", help="只入队，不上传")
    run.add_argument("--upload-interval", type=float, default=10.0)
    run.add_argument("--no-signals", action="store_true", help="不安装 SIGTERM 处理器")

    status = add_common(subparsers.add_parser("status", help="打印配置与队列统计"))
    status.add_argument("--json", action="store_true", help="输出 JSON")

    flush = add_common(subparsers.add_parser("flush", help="立即上传一次队列"))
    flush.add_argument("--limit", type=int, default=None)
    flush.add_argument("--max-rounds", type=int, default=5)
    flush.add_argument("--json", action="store_true")

    config = add_common(subparsers.add_parser("config", help="查看/写入配置"))
    config_sub = config.add_subparsers(dest="config_action")
    get_parser = config_sub.add_parser("get", help="打印配置（可指定 KEY）")
    get_parser.add_argument("key", nargs="?", default=None)
    get_parser.add_argument("--json", action="store_true")
    set_parser = config_sub.add_parser("set", help="设置一项配置并写回文件")
    set_parser.add_argument("key", help="支持点号路径，如 upload.retry_max")
    set_parser.add_argument("value")
    set_parser.add_argument("--json", action="store_true")
    config_sub.add_parser("path", help="打印生效的配置文件路径")

    selftest = add_common(subparsers.add_parser("selftest", help="无硬件全链路自检"))
    selftest.add_argument("--workdir", default=None, help="工作目录（默认临时目录）")
    selftest.add_argument("--keep", action="store_true", help="保留自检产生的文件")
    selftest.add_argument("--json", action="store_true", help="只输出 JSON")

    ble = add_common(subparsers.add_parser("ble-serve", help="仅运行 BLE GATT 管理通道"))
    ble.add_argument("--name", default="OpenAssistant", help="BLE 设备名")
    ble.add_argument("--adapter", default=None, help="蓝牙适配器，如 hci0")
    ble.add_argument("--mock", action="store_true", help="使用内存 mock 传输")
    ble.add_argument("--duration", type=float, default=None, help="运行秒数后退出")

    return parser


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _load_config(args: argparse.Namespace, **overrides: Any) -> AgentConfig:
    if getattr(args, "timezone", None):
        overrides["timezone"] = args.timezone
    config = AgentConfig.load(
        args.config, overrides={key: value for key, value in overrides.items() if value is not None}
    )
    if getattr(args, "log_level", None):
        config.log_level = str(args.log_level)
    return config


def _apply_runtime_config(service: AgentService, config: AgentConfig) -> None:
    """BLE 写入的新配置生效：切进程时区 + 替换运行中的 service 配置。"""
    config.apply_timezone()
    service.config = config


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False))


def _resolve_key(config: AgentConfig, key: str) -> Any:
    target: Any = config
    for part in key.split("."):
        if isinstance(target, AgentConfig) or hasattr(target, part):
            target = getattr(target, part, None)
        elif isinstance(target, dict):
            target = target.get(part)
        else:
            raise ConfigError(f"unknown config key: {key}")
        if target is None:
            raise ConfigError(f"unknown config key: {key}")
    if is_dataclass(target):
        return asdict(target)
    return target


def _nested_override(key: str, value: str) -> dict[str, Any]:
    parts = key.split(".")
    nested: Any = value
    for part in reversed(parts):
        nested = {part: nested}
    return nested


def _prepare_service(config: AgentConfig, *, source: str | None, input_path: str | None) -> AgentService:
    if source:
        config.record.source = source
    if input_path:
        config.record.input_path = input_path
    config.validate()
    config.apply_timezone()
    audio_source = create_source(
        config,
        source=config.record.source,
        input_path=config.record.input_path or None,
    )
    service = AgentService(config, source=audio_source)
    service.setup()
    return service


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    config = _load_config(args)
    configure_logging(config.log_level)
    service = _prepare_service(config, source=args.source, input_path=args.input)

    timer: threading.Timer | None = None
    if args.duration:
        timer = threading.Timer(float(args.duration), service.request_stop)
        timer.daemon = True
        timer.start()

    summary: dict[str, Any] = {}
    try:
        summary = service.run(
            max_blocks=args.max_blocks,
            upload=not args.no_upload,
            install_signals=not args.no_signals,
            final_flush=not args.no_upload,
        )
    except KeyboardInterrupt:  # pragma: no cover - 交互式 Ctrl+C
        service.request_stop()
    except AudioSourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if timer is not None:
            timer.cancel()
        service.close()

    print(json.dumps({"ok": True, **summary}, ensure_ascii=False, default=str))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = _load_config(args)
    configure_logging(config.log_level)
    payload: dict[str, Any] = {
        "device_id": config.device_id,
        "server_url": config.server_url,
        "config_path": config.config_path,
        "spool_path": config.spool_path,
        "timezone": config.timezone,
        "record": {
            "device": config.record.device,
            "sample_rate": config.record.sample_rate,
            "channels": config.record.channels,
            "block_ms": config.record.block_ms,
            "source": config.record.source,
        },
        "vad": {
            "aggressiveness": config.vad.aggressiveness,
            "min_speech_ms": config.vad.min_speech_ms,
            "min_silence_ms": config.vad.min_silence_ms,
            "max_segment_s": config.vad.max_segment_s,
        },
        "upload": {
            "endpoint": config.upload.endpoint,
            "retry_max": config.upload.retry_max,
            "offline_buffer": config.upload.offline_buffer,
        },
    }
    try:
        spool = SpoolStore(config.spool_path)
        payload["queue"] = spool.stats()
        payload["recent"] = [item.to_dict() for item in spool.list(limit=5)]
        spool.close()
    except Exception as exc:
        payload["queue"] = {"error": f"{type(exc).__name__}: {exc}"}

    if args.json:
        _print_json(payload)
        return 0

    print(f"device_id     : {payload['device_id']}")
    print(f"server_url    : {payload['server_url']}")
    print(f"config_path   : {payload['config_path']}")
    print(f"spool_path    : {payload['spool_path']}")
    print(f"timezone      : {payload['timezone']}")
    print(f"source        : {payload['record']['source']} ({payload['record']['device']})")
    queue = payload.get("queue", {})
    if "error" in queue:
        print(f"queue         : unavailable ({queue['error']})")
    else:
        print(
            "queue         : total={total} pending={pending} uploading={uploading} "
            "uploaded={uploaded} failed={failed} bytes={pending_bytes}".format(**queue)
        )
    print(f"upload        : retry_max={payload['upload']['retry_max']} "
          f"offline_buffer={payload['upload']['offline_buffer']} "
          f"endpoint={payload['upload']['endpoint']}")
    return 0


def cmd_flush(args: argparse.Namespace) -> int:
    config = _load_config(args)
    configure_logging(config.log_level)
    spool = SpoolStore(config.spool_path)
    transport = HttpTransport(
        config.server_url,
        config.api_key,
        endpoint=config.upload.endpoint,
        health_endpoint=config.upload.health_endpoint,
        timeout_s=config.upload.timeout_s,
    )
    uploader = Uploader(spool, transport, config)
    try:
        summary = uploader.flush(max_rounds=args.max_rounds, limit=args.limit)
    finally:
        uploader.close()
        spool.close()
    if args.json:
        _print_json(summary)
    else:
        print(
            "uploaded={uploaded} failed={failed} pending={pending} offline={offline}".format(
                **summary
            )
        )
    return 0 if not summary.get("failed") else 1


def cmd_config(args: argparse.Namespace) -> int:
    action = args.config_action or "get"
    config = _load_config(args)
    configure_logging(config.log_level)

    if action == "path":
        print(config.config_path)
        return 0

    if action == "get":
        key = getattr(args, "key", None)
        if key:
            try:
                value = _resolve_key(config, key)
            except ConfigError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            if args.json or not isinstance(value, (dict, list)):
                print(json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value)
            else:
                _print_json(value)
            return 0
        _print_json(config.to_dict())
        return 0

    key = args.key
    old_value = None
    try:
        old_value = _resolve_key(config, key)
    except ConfigError:
        old_value = None
    try:
        config.merge_dict(_nested_override(key, args.value))
        config.validate()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        new_value = _resolve_key(config, key)
    except ConfigError:  # pragma: no cover - 合并成功必然可解析
        new_value = None
    path = config.save(args.config or None)
    if args.json:
        _print_json(
            {"saved": str(path), "key": key, "before": old_value, "after": new_value}
        )
    else:
        print(f"saved {key}: {old_value!r} -> {new_value!r} ({path})")
    return 0


def _writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".write-probe-{uuid.uuid4().hex[:8]}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _unique_dir(parent: Path, prefix: str = "oa-selftest-") -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    for _ in range(20):
        candidate = parent / f"{prefix}{uuid.uuid4().hex[:10]}"
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:  # pragma: no cover - 极小概率
            continue
    raise ConfigError(f"cannot create a unique directory under {parent}")


def _selftest_workdir(explicit: str | None) -> Path:
    """自检工作目录：优先系统临时目录，不可写时退回仓库内目录。"""
    if explicit:
        path = Path(explicit)
        if not _writable_dir(path):
            raise ConfigError(f"workdir is not writable: {path}")
        return path
    temp_root = Path(tempfile.gettempdir())
    if _writable_dir(temp_root):
        try:
            return _unique_dir(temp_root)
        except ConfigError:
            pass
    fallback_root = Path.cwd() / ".openassistant-selftest"
    if not _writable_dir(fallback_root):
        raise ConfigError(
            "no writable selftest directory (system temp and cwd are both read-only)"
        )
    return _unique_dir(fallback_root)


def cmd_selftest(args: argparse.Namespace) -> int:
    config = _load_config(args)
    configure_logging("WARNING" if args.json else config.log_level)
    workdir = _selftest_workdir(args.workdir)
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    try:
        # 1) 合成音频 -> VAD 切片 -> 落盘 -> 入队 -> mock 上传
        online_dir = workdir / "online"
        cfg = config.copy()
        cfg.spool_path = str(online_dir)
        cfg.record.source = "synthetic"
        cfg.vad.min_speech_ms = min(cfg.vad.min_speech_ms, 300)
        cfg.vad.min_silence_ms = min(cfg.vad.min_silence_ms, 400)
        cfg.vad.max_segment_s = min(cfg.vad.max_segment_s, 10.0)
        transport = MockTransport(healthy=True)
        service = AgentService(cfg, transport=transport, upload_interval_s=0.0)
        service.setup()
        source = SyntheticSource(
            sample_rate=cfg.record.sample_rate,
            block_ms=cfg.record.block_ms,
            pattern=[("speech", 1.2), ("silence", 0.6), ("speech", 1.0), ("silence", 0.5)],
        )
        summary = service.run(source=source, upload=True, install_signals=False)
        queue = service.spool.stats() if service.spool else {}
        uploaded = len(transport.calls)
        check("vad_segments", summary["segments"] >= 1, summary)
        check("spool_uploaded", queue.get("uploaded", 0) >= 1, queue)
        check("upload_calls", uploaded >= 1, {"calls": uploaded})
        check("multipart_meta", bool(transport.calls and transport.calls[0]["meta"].get("sha256")),
              transport.calls[0]["meta"] if transport.calls else None)
        if transport.calls:
            meta = transport.calls[0]["meta"]
            check(
                "meta_contract",
                all(key in meta for key in (
                    "client_segment_id", "device_id", "recorded_at", "duration_s",
                    "sample_rate", "channels", "sha256", "vad", "trigger",
                )),
                sorted(meta),
            )
        files = list((online_dir / "segments").glob("*.wav"))
        check("wav_files", len(files) >= 1, [str(p) for p in files])

        # 2) 离线暂存：health 探测失败时只入队
        offline_dir = workdir / "offline"
        cfg_offline = cfg.copy()
        cfg_offline.spool_path = str(offline_dir)
        offline_transport = MockTransport(healthy=False)
        offline_service = AgentService(
            cfg_offline, transport=offline_transport, upload_interval_s=0.0
        )
        offline_service.setup()
        offline_summary = offline_service.run(
            source=SyntheticSource(
                sample_rate=cfg.record.sample_rate,
                block_ms=cfg.record.block_ms,
                pattern=[("speech", 1.0), ("silence", 0.5)],
            ),
            upload=True,
            install_signals=False,
        )
        offline_queue = offline_service.spool.stats() if offline_service.spool else {}
        check("offline_segments", offline_summary["segments"] >= 1, offline_summary)
        check("offline_buffered", offline_queue.get("pending", 0) >= 1, offline_queue)
        check("offline_no_upload", len(offline_transport.calls) == 0, offline_queue)

        # 3) BLE mock：配置写入 + 状态读取 + 队列命令
        manager = BleManager(
            config=service.config,
            spool=service.spool,
            status_provider=service.status,
            uploader=service.uploader,
            on_config_applied=lambda cfg: _apply_runtime_config(service, cfg),
            config_saver=lambda cfg: str(workdir / "config.toml"),
        )
        ble = MockBleTransport(manager)
        write_result = ble.write_config({"upload": {"retry_max": 7}, "device_id": "selftest"})
        status = ble.read_status()
        queue_list = ble.write_queue({"action": "list", "limit": 3})
        check("ble_config", bool(write_result and write_result.get("ok")), write_result)
        check(
            "ble_status",
            status.get("device_id") == "selftest" and "queue" in status,
            status,
        )
        check("ble_queue", bool(queue_list.get("ok")), queue_list)
        service.close()
        offline_service.close()
    except Exception as exc:  # pragma: no cover - 自检必须给出结构化失败
        checks.append(
            {"name": "exception", "ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        )

    result = {
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
        "workdir": str(workdir),
        "agent_version": __version__,
        "config_path": config.config_path,
    }
    if args.json:
        _print_json(result)
    else:
        print(f"OpenAssistant Agent selftest (v{__version__})")
        for check in checks:
            print(f"  [{'PASS' if check['ok'] else 'FAIL'}] {check['name']}")
            if not check["ok"]:
                print(f"         {json.dumps(check['detail'], ensure_ascii=False, default=str)}")
        print(f"result: {'OK' if result['ok'] else 'FAILED'}  (workdir: {workdir})")

    if not args.keep and args.workdir is None:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0 if result["ok"] else 1


def cmd_ble_serve(args: argparse.Namespace) -> int:
    config = _load_config(args)
    configure_logging(config.log_level)
    service = AgentService(config)
    service.setup()
    assert service.spool is not None
    manager = BleManager(
        config=config,
        spool=service.spool,
        status_provider=service.status,
        uploader=service.uploader,
        on_config_applied=lambda cfg: _apply_runtime_config(service, cfg),
    )

    if args.mock:
        transport = MockBleTransport(manager, device_name=args.name)
        transport.write_config({"device_id": config.device_id})
        payload = {
            "mode": "mock",
            "device_name": args.name,
            "status": transport.read_status(),
            "config": transport.read_config(),
        }
        _print_json(payload)
        service.close()
        return 0

    if not BlessBleServer.available():
        print(
            "error: bless 未安装，无法启动真实 BLE GATT Server。"
            "请 pip install bless，或使用 ble-serve --mock 测试协议。",
            file=sys.stderr,
        )
        service.close()
        return 2

    server = BlessBleServer(manager, device_name=args.name, adapter=args.adapter)
    stop_event = threading.Event()
    thread = threading.Thread(target=server.run, kwargs={"stop_event": stop_event}, daemon=True)
    thread.start()
    print(f"BLE GATT server started: {args.name} ({manager.config.device_id})", flush=True)
    try:
        if args.duration:
            time.sleep(float(args.duration))
        else:
            while thread.is_alive():
                time.sleep(0.5)
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        stop_event.set()
        thread.join(timeout=5.0)
        service.close()
    return 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = None
    try:
        config = _load_config(args)
        # 在配置日志前切换进程时区：systemd 默认 UTC 时也能输出本地时间日志
        config.apply_timezone()
        configure_logging(args.log_level or config.log_level)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    handlers = {
        "run": cmd_run,
        "status": cmd_status,
        "flush": cmd_flush,
        "config": cmd_config,
        "selftest": cmd_selftest,
        "ble-serve": cmd_ble_serve,
    }
    handler = handlers.get(args.command)
    if handler is None:  # pragma: no cover - argparse 已限制
        parser.error(f"unknown command: {args.command}")
        return 2
    try:
        return int(handler(args) or 0)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except AudioSourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: cannot initialize agent runtime: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
