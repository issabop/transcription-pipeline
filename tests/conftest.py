"""Shared test helpers: a fake engine, real audio files made with ffmpeg, and an app wired to both."""

import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.pipeline.transcribe import RawSegment, RawTranscript, RawWord


def words(*items: tuple[float, float, str]) -> list[RawWord]:
    return [RawWord(start, end, f" {text}") for start, end, text in items]


# Pauses of at least 1.5 s: 2.0 s between segments, 1.6 s inside the third one, and exactly 1.5 s before the last.
FAKE_SEGMENTS = [
    RawSegment(0.0, 2.0, " Hello   there.", words((0.0, 0.8, "Hello"), (0.9, 2.0, "there."))),
    RawSegment(2.1, 4.0, " ", []),  # empty: should be dropped
    RawSegment(4.0, 7.0, " This is a test.", words((4.0, 4.3, "This"), (4.4, 4.6, "is"), (4.7, 4.8, "a"), (6.4, 7.0, "test."))),
    RawSegment(8.5, 10.0, " After a pause.", words((8.5, 8.9, "After"), (9.0, 9.1, "a"), (9.2, 10.0, "pause."))),
]


class FakeTranscriber:
    """Returns fixed segments instantly. Can fail a set number of times, or wait until released."""

    supported_languages = ["en", "ur"]

    def __init__(self, failures: int = 0, gate: threading.Event | None = None):
        self.failures = failures
        self.gate = gate
        self.calls = 0

    def transcribe(self, audio: Path, language, on_progress) -> RawTranscript:
        self.calls += 1
        assert audio.suffix == ".wav" and audio.exists()
        if self.gate:
            self.gate.wait(timeout=10)
        if self.calls <= self.failures:
            raise RuntimeError("simulated temporary failure")
        on_progress(0.5)
        return RawTranscript("en", 0.98, 10.0, FAKE_SEGMENTS)


def make_audio(path: Path, seconds: float = 2, extra: tuple[str, ...] = ()) -> Path:
    """A real audio file (a sine tone) in whatever format the extension says."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=d={seconds}", *extra, str(path)],
        check=True,
    )
    return path


def wait_for(client: TestClient, job_id: str, timeout: float = 10) -> dict:
    """Poll a job until it finishes, like a real client would."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/v1/transcriptions/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {job}")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", max_upload_mb=5, max_duration_minutes=1, _env_file=None)


@pytest.fixture
def make_client(settings: Settings):
    """Build a client around any transcriber. `with` runs startup, so the workers start."""
    clients = []

    def build(transcriber=None, **overrides) -> TestClient:
        config = settings.model_copy(update=overrides)
        client = TestClient(create_app(config, transcriber or FakeTranscriber()))
        client.__enter__()
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def wav(tmp_path: Path) -> Path:
    return make_audio(tmp_path / "tone.wav")
