"""音频质量门控：静音片段跳过、Whisper 字幕幻觉过滤。"""
from __future__ import annotations

import numpy as np

from app.services.pipeline import looks_like_hallucination
from tests.conftest import (
    AUTH,
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
