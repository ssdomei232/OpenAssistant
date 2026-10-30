from __future__ import annotations

from tests.conftest import AUTH, enroll_voiceprint, tone_wave, wav_bytes


def test_voiceprint_enroll_verify_delete(client):
    response = enroll_voiceprint(client, tone_wave(4.0, "user"), threshold=60)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enrolled"] is True
    assert body["duration_s"] == 4.0

    me = client.get("/api/v1/me", headers=AUTH).json()
    assert me["voiceprint"]["enrolled"] is True
    assert me["voiceprint"]["threshold"] == 60.0

    same = client.post(
        "/api/v1/me/voiceprint/verify",
        headers=AUTH,
        files={"file": ("same.wav", wav_bytes(tone_wave(2.0, "user")), "audio/wav")},
    )
    assert same.status_code == 200
    assert same.json()["matched"] is True
    assert same.json()["similarity"] > 60

    different = client.post(
        "/api/v1/me/voiceprint/verify",
        headers=AUTH,
        files={"file": ("other.wav", wav_bytes(tone_wave(2.0, "other")), "audio/wav")},
    )
    assert different.status_code == 200
    assert different.json()["matched"] is False

    deleted = client.delete("/api/v1/me/voiceprint", headers=AUTH)
    assert deleted.status_code == 200
    assert client.get("/api/v1/me/voiceprint", headers=AUTH).json()["enrolled"] is False


def test_voiceprint_too_short(client):
    response = enroll_voiceprint(client, tone_wave(1.0, "user"))
    assert response.status_code == 422
    assert response.json()["detail"] == "too_short"


def test_voiceprint_verify_requires_enrollment(client):
    response = client.post(
        "/api/v1/me/voiceprint/verify",
        headers=AUTH,
        files={"file": ("v.wav", wav_bytes(tone_wave(2.0, "user")), "audio/wav")},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "voiceprint_not_enrolled"


def test_voiceprint_invalid_audio(client):
    response = client.post(
        "/api/v1/me/voiceprint",
        headers=AUTH,
        files={"file": ("bad.wav", b"not an audio file", "audio/wav")},
    )
    assert response.status_code == 422
