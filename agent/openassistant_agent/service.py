"""常驻主服务：采集 -> VAD -> 切片 -> 落盘 -> 入队 -> 周期上传。

``AgentService`` 把各模块串成一条流：

* 音源块流按 VAD 帧长重新分帧，逐帧判定后交给 :class:`Segmenter`；
* 每个含人声片段写成 wav（``spool_path/segments/<id>.wav``），计算 sha256，
  带 ``vad`` 元数据写入 SQLite 队列；
* 主循环按 ``upload_interval_s`` 触发上传；网络不可用时继续入队；
* 启动时把上次遗留的 ``uploading`` 重置为 ``pending``（断电恢复）；
* 支持 SIGTERM/SIGINT 优雅退出：收尾当前片段、尽量 flush 队列。
"""

from __future__ import annotations

import hashlib
import logging
import signal
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .capture import AudioSource, create_source, write_wav
from .config import AgentConfig
from .segmenter import Segmenter, SpeechSegment
from .spool import SpoolItem, SpoolStore
from .uploader import HttpTransport, Uploader
from .vad import FrameVAD, create_vad

__all__ = ["AgentService"]

logger = logging.getLogger(__name__)


class AgentService:
    """录音/切片/上传常驻服务。"""

    def __init__(
        self,
        config: AgentConfig,
        *,
        source: AudioSource | None = None,
        vad: FrameVAD | None = None,
        spool: SpoolStore | None = None,
        uploader: Uploader | None = None,
        transport: Any | None = None,
        clock: Callable[[], float] = time.time,
        logger_: logging.Logger | None = None,
        upload_interval_s: float = 10.0,
    ) -> None:
        self.config = config
        self.log = logger_ or logger
        self.clock = clock
        self.upload_interval_s = float(upload_interval_s)

        self.source = source
        self.vad = vad
        self.spool = spool
        self.uploader = uploader
        self.transport = transport

        self.segmenter: Segmenter | None = None
        self.root = Path(config.spool_path)
        self.segments_dir = self.root / "segments"

        self._pending_pcm = b""
        self._stop = threading.Event()
        self._started_at = self.clock()
        self._recording_started_at: datetime | None = None
        self._recording = False
        self._running = False
        self._last_upload_at = 0.0
        self._seq = 0
        self.segments_total = 0
        self.uploaded_total = 0
        self._setup_done = False
        self._signal_installed = False

    # -- 装配 ------------------------------------------------------------
    def setup(self) -> "AgentService":
        """打开队列、恢复中断上传、构造 VAD/切片器/上传器。幂等。"""
        if self._setup_done:
            return self
        self.config.validate()
        self.root.mkdir(parents=True, exist_ok=True)
        self.segments_dir.mkdir(parents=True, exist_ok=True)

        if self.spool is None:
            self.spool = SpoolStore(self.root, clock=self.clock)
        recovered = self.spool.reset_uploading()
        if recovered:
            self.log.info("queue reconciliation: %s uploading -> pending", recovered)

        if self.vad is None:
            self.vad = create_vad(self.config, sample_rate=self.config.record.sample_rate)
        self.segmenter = Segmenter(
            self.config.vad,
            sample_rate=self.config.record.sample_rate,
            channels=self.config.record.channels,
        )

        if self.uploader is None:
            transport = self.transport or HttpTransport(
                self.config.server_url,
                self.config.api_key,
                endpoint=self.config.upload.endpoint,
                health_endpoint=self.config.upload.health_endpoint,
                timeout_s=self.config.upload.timeout_s,
            )
            self.uploader = Uploader(self.spool, transport, self.config, clock=self.clock)

        self._setup_done = True
        return self

    # -- 采集主循环 ------------------------------------------------------
    def process_block(self, block: bytes) -> list[SpeechSegment]:
        """把任意长度的 PCM 块送进 VAD/切片器，返回完成的片段。"""
        if self.segmenter is None:
            raise RuntimeError("setup() must be called before process_block()")
        assert self.vad is not None
        frame_bytes = self.segmenter.frame_bytes
        pending = self._pending_pcm + block
        produced: list[SpeechSegment] = []
        while len(pending) >= frame_bytes:
            frame = pending[:frame_bytes]
            pending = pending[frame_bytes:]
            produced.extend(self.segmenter.push(frame, self.vad.is_speech(frame)))
        self._pending_pcm = pending
        return produced

    def flush(self) -> list[SpeechSegment]:
        """流结束/退出时收尾：补零最后不足一帧的数据并闭合片段。"""
        if self.segmenter is None:
            return []
        produced: list[SpeechSegment] = []
        if self._pending_pcm:
            frame_bytes = self.segmenter.frame_bytes
            padded = self._pending_pcm + b"\x00" * (frame_bytes - len(self._pending_pcm))
            assert self.vad is not None
            produced.extend(self.segmenter.push(padded, self.vad.is_speech(padded)))
            self._pending_pcm = b""
        produced.extend(self.segmenter.flush())
        return produced

    def ingest_segment(self, segment: SpeechSegment, *, trigger: str = "auto") -> SpoolItem:
        """写 wav 到 spool 目录并入队。"""
        self.setup()
        assert self.spool is not None
        wav_bytes = segment.to_wav_bytes()
        digest = hashlib.sha256(wav_bytes).hexdigest()

        recorded_at, client_id = self._next_identity(segment)
        path = self.segments_dir / f"{client_id}.wav"
        write_wav(path, segment.pcm, segment.sample_rate, segment.channels)
        # 以落盘文件为准重新计算（与上传时的校验和一致）
        file_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert file_digest == digest

        item = self.spool.enqueue(
            client_segment_id=client_id,
            recorded_at=recorded_at,
            duration_s=segment.duration_s,
            path=path,
            sha256=digest,
            speech_ratio=segment.speech_ratio,
            vad={
                "speech_ratio": round(segment.speech_ratio, 4),
                "segments": [[round(a, 3), round(b, 3)] for a, b in segment.speech_spans],
            },
            trigger=trigger,
        )
        self.segments_total += 1
        self.log.info(
            "segment %s queued: %.2fs speech_ratio=%.2f file=%s",
            client_id,
            segment.duration_s,
            segment.speech_ratio,
            path,
        )
        return item

    def _next_identity(self, segment: SpeechSegment) -> tuple[str, str]:
        base = self._recording_started_at or datetime.now().astimezone()
        recorded = base + timedelta(seconds=segment.start)
        recorded_at = recorded.astimezone().isoformat(timespec="seconds")
        stamp = recorded.astimezone().strftime("%Y%m%dT%H%M%S")
        assert self.spool is not None
        while True:
            self._seq += 1
            client_id = f"seg-{stamp}-{self._seq:04d}"
            if self.spool.get_by_client_id(client_id) is None:
                return recorded_at, client_id

    # -- 上传 ------------------------------------------------------------
    def tick_upload(self, *, force: bool = False, limit: int | None = None) -> dict[str, Any]:
        """上传一批（由主循环周期调用）。"""
        self.setup()
        assert self.uploader is not None
        now = self.clock()
        if not force and now - self._last_upload_at < self.upload_interval_s:
            return {"skipped": True, "reason": "not_due"}
        self._last_upload_at = now
        try:
            summary = self.uploader.upload_pending(limit=limit)
        except Exception as exc:  # 上传失败不应中断录音
            self.log.exception("upload round failed: %s", exc)
            return {"error": f"{type(exc).__name__}: {exc}"}
        self.uploaded_total += int(summary.get("uploaded", 0))
        return summary

    def flush_queue(self, *, max_rounds: int = 5, limit: int | None = None) -> dict[str, Any]:
        """立即把所有到期记录上传完（CLI ``flush`` / 退出前调用）。"""
        self.setup()
        assert self.uploader is not None
        summary = self.uploader.flush(max_rounds=max_rounds, limit=limit)
        self.uploaded_total += int(summary.get("uploaded", 0))
        return summary

    # -- 运行/停止 -------------------------------------------------------
    def request_stop(self) -> None:
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def install_signal_handlers(self) -> None:
        """SIGTERM/SIGINT -> 优雅退出（非主线程/不支持时忽略）。"""
        if self._signal_installed:
            return

        def handler(signum: int, _frame: Any) -> None:
            self.log.info("received signal %s, shutting down", signum)
            self.request_stop()

        try:
            signal.signal(signal.SIGTERM, handler)
            signal.signal(signal.SIGINT, handler)
            self._signal_installed = True
        except (ValueError, AttributeError, OSError):  # pragma: no cover - 非主线程
            self.log.debug("cannot install signal handlers in this context")

    def run(
        self,
        *,
        source: AudioSource | None = None,
        stop_event: threading.Event | None = None,
        max_blocks: int | None = None,
        upload: bool = True,
        install_signals: bool = False,
        final_flush: bool = True,
    ) -> dict[str, Any]:
        """主循环（阻塞），返回本次运行摘要。"""
        self.setup()
        active_source = source or self.source or create_source(self.config)
        self.source = active_source
        if stop_event is not None:
            # 外部 stop_event 与内部标志取并集
            def watch() -> None:
                stop_event.wait()
                self._stop.set()

            threading.Thread(target=watch, name="agent-stop-watch", daemon=True).start()
        if install_signals:
            self.install_signal_handlers()

        blocks = 0
        produced = 0
        self._running = True
        self._recording = True
        self._recording_started_at = datetime.now().astimezone()
        self._last_upload_at = 0.0
        self.log.info(
            "agent started: device=%s source=%s server=%s",
            self.config.device_id,
            type(active_source).__name__,
            self.config.server_url or "(unset)",
        )

        try:
            active_source.open()
            while not self._stop.is_set():
                block = active_source.read_block()
                if not block:
                    break
                blocks += 1
                for segment in self.process_block(block):
                    self.ingest_segment(segment)
                    produced += 1
                if upload and self.uploader is not None:
                    summary = self.tick_upload()
                    if summary.get("attempted"):
                        self.log.debug("upload tick: %s", summary)
                if max_blocks is not None and blocks >= int(max_blocks):
                    break
        finally:
            self._recording = False
            try:
                for segment in self.flush():
                    self.ingest_segment(segment)
                    produced += 1
            except Exception:  # pragma: no cover - 收尾失败仍需退出
                self.log.exception("failed to flush segmenter")
            try:
                active_source.close()
            except Exception:  # pragma: no cover
                self.log.debug("error closing audio source", exc_info=True)
            self._running = False

        summary: dict[str, Any] = {
            "blocks": blocks,
            "segments": produced,
            "queue": self.spool.stats() if self.spool is not None else {},
        }
        if upload and final_flush and self.uploader is not None:
            try:
                if self._stop.is_set():
                    # 收到 SIGTERM：只尝试一批，避免退出被网络阻塞
                    summary["upload"] = self.tick_upload(force=True)
                else:
                    summary["upload"] = self.flush_queue(max_rounds=3)
            except Exception:  # pragma: no cover - 退出阶段不抛异常
                self.log.exception("final upload flush failed")
        self.log.info(
            "agent stopped: blocks=%s segments=%s queue=%s",
            blocks,
            produced,
            summary["queue"] if "queue" in summary else {},
        )
        return summary

    # -- 状态 ------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        queue: dict[str, Any] = {}
        if self.spool is not None:
            try:
                queue = self.spool.stats()
            except Exception:  # pragma: no cover - DB 异常不应影响 BLE 读取
                self.log.exception("cannot read queue stats")
        online = False
        if self.uploader is not None:
            try:
                online = bool(self.uploader.online)
            except Exception:  # pragma: no cover
                online = False
        return {
            "device_id": self.config.device_id,
            "wifi_connected": online,
            "queue": queue,
            "recording": self._recording,
            "uptime_s": round(self.clock() - self._started_at, 1),
            "retry_max": self.config.upload.retry_max,
            "offline_buffer": self.config.upload.offline_buffer,
            "server_url": self.config.server_url,
            "source": type(self.source).__name__ if self.source is not None else None,
            "segments_total": self.segments_total,
            "uploaded_total": self.uploaded_total,
        }

    def close(self) -> None:
        self._stop.set()
        if self.source is not None:
            try:
                self.source.close()
            except Exception:  # pragma: no cover
                pass
        if self.uploader is not None:
            try:
                self.uploader.close()
            except Exception:  # pragma: no cover
                pass
        if self.spool is not None:
            self.spool.close()
        self._setup_done = False
