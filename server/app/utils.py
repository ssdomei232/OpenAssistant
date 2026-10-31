"""Small shared helpers: ids, time, hashing, audio decoding."""
from __future__ import annotations

import hashlib
import io
import math
import os
import secrets
import time
import wave
from datetime import datetime, timedelta, timezone
from typing import Annotated, Iterable

import numpy as np
from pydantic import PlainSerializer

UTC = timezone.utc

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32


def new_id(prefix: str) -> str:
    """Time-sortable, URL-safe identifier: ``<prefix>_<26 chars>``."""
    ts = int(time.time() * 1000)
    ts_chars = []
    for _ in range(10):
        ts_chars.append(_ALPHABET[ts & 0x1F])
        ts >>= 5
    rand = "".join(secrets.choice(_ALPHABET) for _ in range(16))
    return f"{prefix}_{''.join(reversed(ts_chars))}{rand}"


def now_utc() -> datetime:
    """Naive UTC datetime, used for storage everywhere."""
    return datetime.now(UTC).replace(tzinfo=None)


def utc_from(dt: datetime | None) -> datetime | None:
    """Normalise any datetime to naive UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


def ensure_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def iso_utc(dt: datetime | None) -> str | None:
    aware = ensure_aware(dt)
    return aware.isoformat().replace("+00:00", "Z") if aware else None


def _serialize_dt(dt: datetime | None) -> str | None:
    return iso_utc(dt)


UTCDateTime = Annotated[datetime, PlainSerializer(_serialize_dt, return_type=str)]


# ── hashing / keys ────────────────────────────────────────────────────────
def hash_api_key(api_key: str, pepper: str) -> str:
    return hashlib.sha256(f"{pepper}:{api_key}".encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    return "oa_" + secrets.token_urlsafe(32)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── time helpers ──────────────────────────────────────────────────────────
def day_bounds(dt: datetime, tz_offset_minutes: int = 0) -> tuple[datetime, datetime]:
    """UTC day bounds for a local day (offset in minutes east of UTC)."""
    local = dt + timedelta(minutes=tz_offset_minutes)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start = start_local - timedelta(minutes=tz_offset_minutes)
    return start, start + timedelta(days=1)


def human_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} 秒"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} 分 {sec} 秒" if sec else f"{minutes} 分钟"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分"


# ── audio ─────────────────────────────────────────────────────────────────
def decode_audio(data: bytes, target_sr: int = 16000) -> tuple[np.ndarray, int]:
    """Decode audio bytes to mono float32 numpy samples.

    Supports WAV out of the box (stdlib ``wave``); for other containers the
    optional ``soundfile`` dependency is used. Returns ``(samples, sample_rate)``
    where samples are float32 in ``[-1, 1]``.
    """
    samples, sr = _decode_wave(data)
    if samples is None:
        samples, sr = _decode_soundfile(data)
    if samples is None:
        raise ValueError("unsupported_audio_format")
    samples = np.asarray(samples, dtype=np.float32)
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    if sr != target_sr:
        samples = resample_audio(samples, sr, target_sr)
        sr = target_sr
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak > 0:
        samples = samples / max(peak, 1.0) if peak > 1.0 else samples
    return samples.astype(np.float32), sr


def _decode_wave(data: bytes) -> tuple[np.ndarray | None, int]:
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            sr = wf.getframerate()
            channels = wf.getnchannels()
            width = wf.getsampwidth()
            raw = wf.readframes(wf.getnframes())
    except Exception:
        return None, 0
    if width == 1:
        arr = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        arr = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        ints = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        ints = np.where(ints >= 1 << 23, ints - (1 << 24), ints)
        arr = ints.astype(np.float32) / float(1 << 23)
    elif width == 4:
        arr = np.frombuffer(raw, dtype="<i4").astype(np.float32) / float(1 << 31)
    else:
        return None, sr
    if channels > 1:
        arr = arr.reshape(-1, channels).mean(axis=1)
    return arr, sr


def _decode_soundfile(data: bytes) -> tuple[np.ndarray | None, int]:
    try:
        import soundfile as sf  # type: ignore

        samples, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=False)
        return np.asarray(samples), int(sr)
    except Exception:
        return None, 0


def _lowpass_kernel(cutoff_hz: float, sample_rate: int, num_taps: int | None = None) -> np.ndarray:
    """Windowed-sinc 低通 FIR，用于降采样前的抗混叠滤波。"""
    cutoff_hz = min(cutoff_hz, sample_rate / 2.0 * 0.99)
    if num_taps is None:
        # 过渡带约 1/8 cutoff，Hamming 窗下约 50 dB 阻带
        num_taps = int(round(8.0 * sample_rate / max(cutoff_hz, 1.0)))
    num_taps = max(15, num_taps | 1)  # 奇数抽头保证线性相位
    n = np.arange(num_taps) - (num_taps - 1) / 2.0
    normalized_cutoff = cutoff_hz / sample_rate
    kernel = 2.0 * normalized_cutoff * np.sinc(2.0 * normalized_cutoff * n) * np.hamming(num_taps)
    total = kernel.sum()
    return (kernel / total).astype(np.float64) if total else kernel


def resample_audio(samples: np.ndarray, sr: int, target_sr: int) -> np.ndarray:
    """高质量重采样：降采样先做抗混叠低通再插值，升采样用线性插值。

    典型场景：Agent 以 48 kHz 上传（DJI Mic Mini 原生采样率），Server 内部统一
    转成 16 kHz 供 FunASR / 声纹模型使用。直接线性抽取会把 8 kHz 以上的
    能量折叠到可听频段（混叠），因此这里先低通到目标 Nyquist 以下再抽取。
    """
    if sr == target_sr or samples.size == 0:
        return samples.astype(np.float32, copy=False)
    x = np.asarray(samples, dtype=np.float64)
    if target_sr < sr:
        # 留 10% 过渡带：cutoff 低于目标 Nyquist
        cutoff = 0.45 * target_sr
        kernel = _lowpass_kernel(cutoff, sr)
        filtered = np.convolve(x, kernel, mode="same")
        factor = sr / float(target_sr)
        target_len = int(round(x.size * target_sr / float(sr)))
        positions = np.arange(target_len, dtype=np.float64) * factor
        out = np.interp(positions, np.arange(x.size, dtype=np.float64), filtered)
        return out.astype(np.float32)
    # 升采样：线性插值即可满足内部 16 kHz 处理需求
    return resample_linear(x.astype(np.float32), sr, target_sr)


def resample_linear(samples: np.ndarray, sr: int, target_sr: int) -> np.ndarray:
    if sr == target_sr or samples.size == 0:
        return samples
    duration = samples.size / sr
    target_len = int(round(duration * target_sr))
    if target_len <= 1:
        return samples[:target_len]
    x_old = np.linspace(0.0, duration, num=samples.size, endpoint=False)
    x_new = np.linspace(0.0, duration, num=target_len, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.float32)


def encode_wav(samples: np.ndarray, sr: int = 16000) -> bytes:
    """Encode mono float32 samples as 16-bit PCM WAV bytes."""
    pcm = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue()


def slice_audio(samples: np.ndarray, sr: int, start: float, end: float) -> np.ndarray:
    a = max(0, int(start * sr))
    b = min(samples.size, int(end * sr))
    return samples[a:b]


def rms(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def db(value: float) -> float:
    return 20.0 * math.log10(max(value, 1e-9))


def iter_windows(samples: np.ndarray, sr: int, window_s: float, hop_s: float | None = None):
    """Yield ``(start_s, end_s, window_samples)`` for a sliding window."""
    hop_s = hop_s or window_s
    win = max(1, int(window_s * sr))
    hop = max(1, int(hop_s * sr))
    for start in range(0, max(1, samples.size - win + 1), hop):
        chunk = samples[start : start + win]
        if chunk.size == 0:
            break
        yield start / sr, (start + chunk.size) / sr, chunk


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def cosine_similarity(a: Iterable[float], b: Iterable[float]) -> float:
    va = np.asarray(list(a), dtype=np.float64)
    vb = np.asarray(list(b), dtype=np.float64)
    if va.size == 0 or vb.size == 0 or va.size != vb.size:
        return 0.0
    na = np.linalg.norm(va)
    nb = np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# ── lightweight secret obfuscation ────────────────────────────────────────
def _keystream(secret_key: str, nonce: bytes, length: int) -> bytes:
    out = b""
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(secret_key.encode("utf-8") + nonce + counter.to_bytes(4, "big")).digest()
        counter += 1
    return out[:length]


def encrypt_secret(plaintext: str, secret_key: str) -> str:
    """Encrypt a short secret for storage (XOR stream + nonce, base64).

    This keeps device WiFi passwords out of plain text in the database without
    pulling in a crypto dependency.  For production, point this at a KMS or
    replace it with AES-GCM.
    """
    if not plaintext:
        return ""
    import base64

    salt = sha256_str(secret_key).encode("utf-8")
    nonce = hashlib.sha256(salt + plaintext.encode("utf-8")).digest()[:12]
    stream = _keystream(secret_key, nonce, len(plaintext.encode("utf-8")))
    payload = bytes(a ^ b for a, b in zip(plaintext.encode("utf-8"), stream))
    return base64.urlsafe_b64encode(nonce + payload).decode("ascii")

def decrypt_secret(ciphertext: str, secret_key: str) -> str:
    if not ciphertext:
        return ""
    import base64

    try:
        raw = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
    except Exception:
        return ""
    nonce, payload = raw[:12], raw[12:]
    stream = _keystream(secret_key, nonce, len(payload))
    return bytes(a ^ b for a, b in zip(payload, stream)).decode("utf-8", errors="replace")


def sha256_str(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

