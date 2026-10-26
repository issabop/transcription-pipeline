"""Stage 1: only real audio within the limits gets through, and rejected files leave nothing behind."""

import io
import subprocess

import pytest

from app.pipeline.ingest import MB, AudioTooLong, FileTooLarge, NotAudio, ingest
from tests.conftest import make_audio


def run_ingest(data: bytes, tmp_path, max_mb: int = 5, max_seconds: float = 60):
    return ingest(io.BytesIO(data), tmp_path / "uploads", max_mb * MB, max_seconds)


def assert_nothing_left(tmp_path):
    assert not any((tmp_path / "uploads").iterdir())


@pytest.mark.parametrize("name", ["tone.wav", "tone.mp3", "tone.flac", "tone.ogg", "tone.m4a"])
def test_accepts_common_audio_formats(tmp_path, name):
    audio = make_audio(tmp_path / name, seconds=3)
    path, info = run_ingest(audio.read_bytes(), tmp_path)
    assert path.exists()
    assert info.duration == pytest.approx(3, abs=0.1)


def test_accepts_the_audio_track_of_a_video(tmp_path):
    video = make_audio(tmp_path / "clip.mp4", extra=("-f", "lavfi", "-i", "testsrc=d=2:s=64x64", "-shortest"))
    _, info = run_ingest(video.read_bytes(), tmp_path)
    assert info.codec == "aac"


@pytest.mark.parametrize(
    ("data", "message"),
    [(b"", "empty"), (b"this is a text file renamed to .mp3", "not a readable")],
)
def test_rejects_files_that_are_not_audio(tmp_path, data, message):
    with pytest.raises(NotAudio, match=message):
        run_ingest(data, tmp_path)
    assert_nothing_left(tmp_path)


def test_rejects_a_file_with_no_audio_track(tmp_path):
    image = tmp_path / "image.png"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=d=1:s=64x64", "-frames:v", "1", str(image)], check=True
    )
    with pytest.raises(NotAudio, match="no audio track"):
        run_ingest(image.read_bytes(), tmp_path)
    assert_nothing_left(tmp_path)


def test_rejects_audio_over_the_duration_limit(tmp_path):
    audio = make_audio(tmp_path / "long.mp3", seconds=90)
    with pytest.raises(AudioTooLong):
        run_ingest(audio.read_bytes(), tmp_path, max_seconds=60)
    assert_nothing_left(tmp_path)


def test_stops_reading_once_over_the_size_limit(tmp_path):
    with pytest.raises(FileTooLarge):
        run_ingest(b"x" * (2 * MB), tmp_path, max_mb=1)
    assert_nothing_left(tmp_path)
