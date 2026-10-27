"""Chunked uploads: big files sent in pieces, validated and queued exactly like a direct upload."""

import os
import time

import pytest

from app.uploads import CHUNK_SIZE, STALE_AFTER_SECONDS, ChunkedUploads, UploadNotFound
from tests.conftest import make_audio, wait_for


def start(client, size):
    return client.post("/v1/uploads", json={"size": size})


def send(client, upload_id, data, chunk=CHUNK_SIZE):
    for offset in range(0, len(data), chunk):
        response = client.put(f"/v1/uploads/{upload_id}", params={"offset": offset}, content=data[offset : offset + chunk])
        assert response.status_code == 200, response.text
        assert response.json() == {"received": min(offset + chunk, len(data))}


def test_upload_in_chunks_then_transcribe(make_client, tmp_path, settings):
    client = make_client()
    data = make_audio(tmp_path / "talk.wav", seconds=40).read_bytes()  # ~1.3 MB, sent as 3 chunks
    session = start(client, len(data)).json()
    assert session["chunk_size"] == CHUNK_SIZE

    send(client, session["id"], data, chunk=500_000)
    response = client.post(f"/v1/uploads/{session['id']}/complete", json={"language": "en"})
    assert response.status_code == 202
    assert response.headers["location"] == f"/v1/transcriptions/{session['id']}"
    job = wait_for(client, session["id"])
    assert job["status"] == "done"
    assert job["audio"]["duration"] == 40
    assert not any((settings.data_dir / "partial").iterdir())  # nothing left behind


def test_resending_a_chunk_is_safe(make_client, wav):
    client = make_client()
    data = wav.read_bytes()
    upload_id = start(client, len(data)).json()["id"]
    half = len(data) // 2
    client.put(f"/v1/uploads/{upload_id}", params={"offset": 0}, content=data[:half])
    # The response was "lost", so the client sends the same chunk again
    assert client.put(f"/v1/uploads/{upload_id}", params={"offset": 0}, content=data[:half]).json() == {"received": half}
    client.put(f"/v1/uploads/{upload_id}", params={"offset": half}, content=data[half:])
    assert client.post(f"/v1/uploads/{upload_id}/complete", json={}).status_code == 202


def test_a_gap_is_rejected(make_client, wav):
    client = make_client()
    upload_id = start(client, len(wav.read_bytes())).json()["id"]
    response = client.put(f"/v1/uploads/{upload_id}", params={"offset": 1000}, content=b"x" * 10)
    assert response.status_code == 409


def test_completing_with_missing_bytes_is_rejected(make_client, wav):
    client = make_client()
    data = wav.read_bytes()
    upload_id = start(client, len(data)).json()["id"]
    client.put(f"/v1/uploads/{upload_id}", params={"offset": 0}, content=data[:100])
    response = client.post(f"/v1/uploads/{upload_id}/complete", json={})
    assert response.status_code == 409
    assert "Received 100 of" in response.json()["detail"]


def test_size_limits(make_client):
    client = make_client()  # test settings allow 5 MB
    assert start(client, 6 * 1024 * 1024).status_code == 413
    assert start(client, 0).status_code == 415
    upload_id = start(client, 10).json()["id"]
    assert client.put(f"/v1/uploads/{upload_id}", params={"offset": 0}, content=b"x" * 11).status_code == 413
    assert client.put(f"/v1/uploads/{upload_id}", params={"offset": 0}, content=b"x" * (CHUNK_SIZE + 1)).status_code == 413


def test_assembled_file_is_validated_like_a_direct_upload(make_client, settings):
    client = make_client()
    data = b"this is not audio" * 100
    upload_id = start(client, len(data)).json()["id"]
    send(client, upload_id, data)
    response = client.post(f"/v1/uploads/{upload_id}/complete", json={})
    assert response.status_code == 415
    assert not any((settings.data_dir / "uploads").iterdir())


def test_unknown_or_malformed_ids_are_404(make_client):
    client = make_client()
    assert client.put("/v1/uploads/" + "a" * 32, params={"offset": 0}, content=b"x").status_code == 404
    assert client.post("/v1/uploads/nope/complete", json={}).status_code == 404


def test_ids_that_could_escape_the_folder_are_refused(tmp_path):
    uploads = ChunkedUploads(tmp_path / "partial", max_bytes=1024)
    for bad in ("../../app/main", r"..\..\x", "A" * 32, "a" * 31):
        with pytest.raises(UploadNotFound):
            uploads.write(bad, 0, b"x")


def test_language_and_queue_checks_apply(make_client, wav):
    client = make_client()
    upload_id = start(client, len(wav.read_bytes())).json()["id"]
    send(client, upload_id, wav.read_bytes())
    assert client.post(f"/v1/uploads/{upload_id}/complete", json={"language": "xx"}).status_code == 422


def test_abandoned_uploads_are_cleaned_up(tmp_path):
    uploads = ChunkedUploads(tmp_path, max_bytes=1024)
    old, fresh = uploads.create(10), uploads.create(10)
    long_ago = time.time() - STALE_AFTER_SECONDS - 60
    for suffix in (".part", ".json"):
        os.utime(tmp_path / f"{old}{suffix}", (long_ago, long_ago))
    uploads.remove_stale()
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"{fresh}.json", f"{fresh}.part"]
