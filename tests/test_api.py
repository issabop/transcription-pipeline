"""The API end to end, with the fake engine: upload, poll, download, and every error case."""

import threading

import pytest

from app.main import STATIC_DIR

from tests.conftest import FakeTranscriber, wait_for


def upload(client, path, **form):
    with path.open("rb") as f:
        return client.post("/v1/transcriptions", files={"file": (path.name, f)}, data=form)


def test_full_flow(make_client, wav, settings):
    client = make_client()
    response = upload(client, wav, language="en")
    assert response.status_code == 202
    job = response.json()
    assert response.headers["location"] == f"/v1/transcriptions/{job['id']}"
    assert job["status"] in ("queued", "processing", "done")

    job = wait_for(client, job["id"])
    assert job["status"] == "done"
    assert job["progress"] == 1.0
    assert job["result"]["text"] == "Hello there. This is a test. After a pause."
    assert len(job["result"]["pauses"]) == 3

    srt = client.get(f"/v1/transcriptions/{job['id']}/transcript", params={"format": "srt"})
    assert srt.headers["content-type"].startswith("application/x-subrip")
    assert srt.text.startswith("1\n00:00:00,000 --> 00:00:02,000")
    txt = client.get(f"/v1/transcriptions/{job['id']}/transcript", params={"pauses": True})
    assert "This is a [pause 1.6s] test." in txt.text

    # Audio is deleted once the job is done; only the transcript is kept.
    assert not any((settings.data_dir / "uploads").iterdir())
    assert not any((settings.data_dir / "work").iterdir())


def test_empty_language_means_auto_detect(make_client, wav):
    client = make_client()
    assert upload(client, wav, language="").json()["language"] is None


def test_unknown_job_is_404(make_client):
    assert make_client().get("/v1/transcriptions/nope").status_code == 404


def test_transcript_before_done_is_409(make_client, wav):
    gate = threading.Event()  # holds the fake engine until released
    client = make_client(FakeTranscriber(gate=gate))
    job = upload(client, wav).json()
    assert client.get(f"/v1/transcriptions/{job['id']}/transcript").status_code == 409
    gate.set()
    assert wait_for(client, job["id"])["status"] == "done"


def test_lists_supported_languages(make_client):
    assert make_client().get("/v1/languages").json() == ["en", "ur"]


def test_rejects_unsupported_language(make_client, wav):
    response = upload(make_client(), wav, language="xx")
    assert response.status_code == 422
    assert "Unsupported language" in response.json()["detail"]


def test_rejects_non_audio_with_415(make_client, tmp_path):
    fake = tmp_path / "fake.mp3"
    fake.write_text("not audio")
    assert upload(make_client(), fake).status_code == 415


def test_rejects_oversized_upload_with_413(make_client, tmp_path):
    big = tmp_path / "big.wav"
    big.write_bytes(b"x" * (7 * 1024 * 1024))  # the test settings allow 5 MB
    response = upload(make_client(), big)
    assert response.status_code == 413


def test_full_queue_is_429(make_client, wav):
    assert upload(make_client(max_pending_jobs=0), wav).status_code == 429


def test_health_reports_limits(make_client):
    health = make_client().get("/health").json()
    assert health["status"] == "ok"
    assert (health["max_upload_mb"], health["max_duration_minutes"]) == (5, 1)


@pytest.mark.skipif(not STATIC_DIR.is_dir(), reason="web page not built (cd frontend && npm run build)")
def test_serves_the_web_page_without_hiding_the_api(make_client):
    client = make_client()
    assert "<title>Transcription Pipeline</title>" in client.get("/").text
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/docs").status_code == 200
