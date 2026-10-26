"""Stage 2: convert any audio or video file to the one format the model expects."""

import logging
import subprocess
from pathlib import Path

SAMPLE_RATE = 16_000  # Whisper was trained on 16 kHz mono audio
CONVERT_TIMEOUT_SECONDS = 600

log = logging.getLogger(__name__)


class ConversionError(Exception):
    """ffmpeg couldn't decode the file. Retrying won't help."""


def to_wav(source: Path, target: Path) -> Path:
    """Decode `source` into 16 kHz, mono, 16-bit PCM WAV at `target`.

    `-vn` drops any video track and `-nostdin` stops ffmpeg from waiting for keyboard input.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg", "-nostdin", "-v", "error", "-y",
        "-i", str(source),
        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le",
        str(target),
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=CONVERT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        target.unlink(missing_ok=True)
        raise ConversionError("Timed out while converting the audio") from None
    if result.returncode != 0:
        target.unlink(missing_ok=True)
        log.warning("ffmpeg failed on %s: %s", source.name, result.stderr.decode(errors="replace").strip())
        raise ConversionError("Could not decode the audio")
    return target
