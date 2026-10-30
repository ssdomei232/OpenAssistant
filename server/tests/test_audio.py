from __future__ import annotations

from tests.conftest import AUTH


def test_audio_upload_is_idempotent(client):
    from tests.conftest import tone_wave, upload_segment

    samples = tone_wave(1.5, "user")
    first = upload_segment(client, samples, client_segment_id="idem-1", process_now=False)
    assert first.status_code == 202
    assert first.json()["duplicate"] is False

    second = upload_segment(client, samples, client_segment_id="idem-1", process_now=False)
    assert second.status_code == 202
    assert second.json()["duplicate"] is True
    assert second.json()["id"] == first.json()["id"]

    segments = client.get("/api/v1/audio/segments?limit=10", headers=AUTH).json()["items"]
    assert len(segments) == 1


def test_audio_checksum_mismatch(client):
    from tests.conftest import tone_wave, upload_segment

    response = upload_segment(
        client,
        tone_wave(1.0, "user"),
        client_segment_id="bad-checksum",
        extra_meta={"sha256": "0" * 64},
        process_now=False,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "checksum_mismatch"


def test_audio_missing_client_segment_id(client):
    from tests.conftest import tone_wave, upload_segment

    response = upload_segment(client, tone_wave(1.0, "user"), client_segment_id="", process_now=False)
    assert response.status_code == 422
    assert response.json()["detail"] == "missing_client_segment_id"


def test_reprocess_endpoint(client, providers):
    from tests.conftest import concat, enroll_voiceprint, tone_wave, upload_segment

    enroll_voiceprint(client, tone_wave(3.0, "user"))
    response = upload_segment(
        client,
        concat(tone_wave(2.0, "user"), tone_wave(2.0, "other")),
        client_segment_id="reprocess-1",
        process_now=True,
    )
    segment = response.json()
    first_card = client.get(f"/api/v1/audio/segments/{segment['id']}", headers=AUTH).json()["card_id"]
    assert first_card

    reprocessed = client.post(f"/api/v1/audio/segments/{segment['id']}/reprocess", headers=AUTH)
    assert reprocessed.status_code == 200
    assert reprocessed.json()["status"] == "done"
    second_card = reprocessed.json()["card_id"]
    assert second_card and second_card != first_card
    old = client.get(f"/api/v1/cards/{first_card}", headers=AUTH).json()
    assert old["deleted_at"] is not None


def test_segment_unknown_id(client):
    assert client.get("/api/v1/audio/segments/seg_missing", headers=AUTH).status_code == 404
    assert client.get("/api/v1/audio/seg_missing/file", headers=AUTH).status_code == 404
