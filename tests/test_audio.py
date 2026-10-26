"""Stage 2: every input comes out as 16 kHz mono 16-bit WAV."""

import pytest

from app.pipeline.audio import ConversionError, to_wav
from app.pipeline.ingest import probe
from tests.conftest import make_audio


@pytest.mark.parametrize("name", ["stereo.mp3", "stereo.flac", "stereo.ogg"])
def test_converts_to_16k_mono_wav(tmp_path, name):
    source = make_audio(tmp_path / name, seconds=2, extra=("-ac", "2", "-ar", "44100"))
    wav = to_wav(source, tmp_path / "out" / "converted.wav")
    info = probe(wav)
    assert (info.codec, info.sample_rate, info.channels) == ("pcm_s16le", 16_000, 1)
    assert info.duration == pytest.approx(2, abs=0.1)


def test_broken_file_raises_and_leaves_no_output(tmp_path):
    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"\xff\xfb" + b"\x00" * 100)
    target = tmp_path / "out.wav"
    with pytest.raises(ConversionError):
        to_wav(broken, target)
    assert not target.exists()
