"""One end-to-end check with a real Whisper model. Slow: run with `pytest -m slow`."""

from pathlib import Path

import pytest

from app.config import Settings
from app.pipeline.audio import to_wav
from app.pipeline.process import build_transcript
from app.pipeline.transcribe import WhisperTranscriber

SAMPLE = Path(__file__).parent.parent / "samples" / "jfk.flac"


@pytest.mark.slow
def test_transcribes_the_jfk_sample(tmp_path):
    config = Settings(_env_file=None)
    engine = WhisperTranscriber("base", config.model_dir, "auto", "auto", beam_size=5, workers=1)
    progress = []
    raw = engine.transcribe(to_wav(SAMPLE, tmp_path / "jfk.wav"), None, progress.append)
    transcript = build_transcript(raw, pause_threshold=1.5)

    assert transcript.language == "en"
    assert "ask not what your country can do for you" in transcript.text.lower()
    assert transcript.segments[0].start < 1 and transcript.segments[-1].end == pytest.approx(11, abs=1)
    assert progress and progress[-1] > 0.9
