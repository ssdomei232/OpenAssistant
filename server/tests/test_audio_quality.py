"""音频质量门控：静音片段跳过、ASR 字幕幻觉过滤、极短音频不做 pyannote 分离。"""
from __future__ import annotations

import warnings

import numpy as np

from app.services.pipeline import looks_like_hallucination
from tests.conftest import (
    AUTH,
    SpectralHistogramEmbedding,
    build_test_providers,
    concat,
    enroll_voiceprint,
    tone_wave,
    upload_segment,
)


def test_looks_like_hallucination():
    assert looks_like_hallucination("中文字幕志愿者 杨茜茜")
    assert looks_like_hallucination("请不吝点赞 订阅 转发 打赏支持明镜与点点栏目")
    assert looks_like_hallucination("感谢观看")
    assert not looks_like_hallucination("帮我记一下明天下午三点和周磊开会。")
    assert not looks_like_hallucination("这个方案我觉得可以")
    assert looks_like_hallucination("   ")


def test_near_silent_audio_is_skipped_without_asr(client):
    from app.providers import set_providers
    from app.providers.push import RecordingPush

    set_providers(
        build_test_providers(
            user_text="中文字幕志愿者 杨茜茜",
            other_text="请不吝点赞 订阅 转发",
            push=RecordingPush(),
        )
    )
    response = upload_segment(
        client,
        np.zeros(16000 * 2, dtype=np.float32),
        client_segment_id="seg-silence",
        process_now=True,
    )
    assert response.status_code == 202
    segment_id = response.json()["id"]
    segment = client.get(f"/api/v1/audio/segments/{segment_id}", headers=AUTH).json()
    assert segment["status"] == "done"
    assert segment["card_id"] is None
    assert segment["error"] == "low_level_audio"
    assert client.get("/api/v1/cards", headers=AUTH).json()["items"] == []


def test_hallucinated_transcript_does_not_create_card(client):
    from app.providers import set_providers
    from app.providers.push import RecordingPush

    set_providers(
        build_test_providers(
            user_text="中文字幕志愿者 杨茜茜",
            other_text="请不吝点赞 订阅 转发 打赏支持明镜与点点栏目",
            push=RecordingPush(),
        )
    )
    enroll_voiceprint(client, tone_wave(3.0, "user"))
    response = upload_segment(
        client,
        concat(tone_wave(2.0, "user"), tone_wave(2.0, "other")),
        client_segment_id="seg-hallucination",
        process_now=True,
    )
    assert response.status_code == 202
    segment_id = response.json()["id"]
    segment = client.get(f"/api/v1/audio/segments/{segment_id}", headers=AUTH).json()
    assert segment["status"] == "done"
    assert segment["card_id"] is None
    assert client.get("/api/v1/cards", headers=AUTH).json()["items"] == []


# ── 极短音频：跳过 pyannote 分离 + 声纹 embedding 反射填充 ──────────────────
class _BoomDiarizer:
    name = "boom"

    def diarize(self, samples, sr):
        raise AssertionError("极短音频不应调用 pyannote 分离")


class _RecordingSpeaker:
    """记录每次 embedding 的输入长度，其余行为与离线谱特征一致。"""

    name = "test-recording"

    def __init__(self) -> None:
        self.chunk_sizes: list[int] = []
        self._inner = SpectralHistogramEmbedding()

    def embed(self, samples, sr):
        self.chunk_sizes.append(int(samples.size))
        return self._inner.embed(samples, sr)


def test_very_short_audio_skips_diarization_and_pads_embedding(client):
    from app.providers import set_providers

    providers = build_test_providers(user_text="帮我查天气。", other_text="")
    providers.diarization = _BoomDiarizer()
    speaker = _RecordingSpeaker()
    providers.speaker = speaker
    set_providers(providers)
    enroll_voiceprint(client, tone_wave(3.0, "user"))

    # 0.4s < OA_DIARIZATION_MIN_SECONDS 默认 1.0s
    response = upload_segment(
        client,
        tone_wave(0.4, "user"),
        client_segment_id="seg-ultra-short",
    )
    assert response.status_code == 202, response.text
    segment = client.get(f"/api/v1/audio/segments/{response.json()['id']}", headers=AUTH).json()
    assert segment["status"] == "done"
    assert segment["card_id"], segment
    assert speaker.chunk_sizes, "should still embed the (single) diarized span"
    assert all(size >= int(0.5 * 16000) for size in speaker.chunk_sizes)


def test_embed_for_pads_short_segment_to_min_length():
    from app.providers.speaker import embed_for

    class _Capture:
        def __init__(self) -> None:
            self.sizes: list[int] = []

        def embed(self, samples, sr):
            self.sizes.append(int(samples.size))
            return [0.0]

    capture = _Capture()
    embed_for(np.zeros(16000, dtype=np.float32), 16000, 0.0, 0.2, capture)
    assert capture.sizes == [8000]


def test_quiet_numerical_warnings_only_suppresses_known_noise():
    from app.providers.numerics import quiet_numerical_warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with quiet_numerical_warnings():
            warnings.warn("std(): degrees of freedom is <= 0", UserWarning)
            warnings.warn("Mean of empty slice", RuntimeWarning)
            warnings.warn("invalid value encountered in divide", RuntimeWarning)
        warnings.warn("some other warning", UserWarning)

    assert len(caught) == 1
    assert str(caught[0].message) == "some other warning"

