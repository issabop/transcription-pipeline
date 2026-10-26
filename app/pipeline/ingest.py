"""Stage 1: accept an upload, save it to disk, and check that it really is audio."""

import json
import subprocess
import uuid
from pathlib import Path
from typing import BinaryIO

from app.models import AudioInfo

MB = 1024 * 1024
CHUNK_SIZE = MB  # copy 1 MB at a time, so memory use stays flat for any file size
PROBE_TIMEOUT_SECONDS = 30


class IngestError(Exception):
    """The upload was rejected. The message is safe to show to the caller."""


class FileTooLarge(IngestError):
    pass


class NotAudio(IngestError):
    pass


class AudioTooLong(IngestError):
    pass


def ingest(source: BinaryIO, upload_dir: Path, max_bytes: int, max_seconds: float) -> tuple[Path, AudioInfo]:
    """Save the upload and validate it. Nothing is left on disk if it's rejected."""
    path = save_upload(source, upload_dir, max_bytes)
    try:
        info = probe(path)
        if info.duration > max_seconds:
            raise AudioTooLong(f"Audio is longer than the {max_seconds / 60:g} minute limit")
    except IngestError:
        path.unlink(missing_ok=True)
        raise
    return path, info


def save_upload(source: BinaryIO, upload_dir: Path, max_bytes: int) -> Path:
    """Copy the upload to disk in chunks, stopping as soon as it goes over the size limit.

    The file gets a random name: the client's filename is never used on disk.
    """
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / uuid.uuid4().hex
    size = 0
    try:
        with path.open("wb") as out:
            while chunk := source.read(CHUNK_SIZE):
                size += len(chunk)
                if size > max_bytes:
                    raise FileTooLarge(f"File is larger than the {max_bytes // MB} MB limit")
                out.write(chunk)
        if size == 0:
            raise NotAudio("File is empty")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def probe(path: Path) -> AudioInfo:
    """Ask ffprobe what's really inside the file. The extension is never trusted."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise NotAudio("Timed out while reading the file") from None
    if result.returncode != 0:
        raise NotAudio("File is not a readable audio or video file")

    data = json.loads(result.stdout)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if audio is None:
        raise NotAudio("File has no audio track")
    duration = float(data.get("format", {}).get("duration") or 0) or duration_from_packets(path)
    if duration <= 0:
        raise NotAudio("Could not read the audio duration")

    return AudioInfo(
        duration=round(duration, 3),
        format=data["format"]["format_name"],
        codec=audio["codec_name"],
        sample_rate=int(audio["sample_rate"]),
        channels=int(audio["channels"]),
    )


def duration_from_packets(path: Path) -> float:
    """Length from the last audio packet's timestamp, for files whose header has no duration.

    Browser recordings (MediaRecorder WebM) are written as a stream, so the length is never filled in.
    This reads packet timestamps only; nothing is decoded.
    """
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "packet=pts_time,duration_time", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return 0.0
    ends = []
    for line in result.stdout.splitlines():
        pts, _, length = line.strip().strip(",").partition(",")
        try:
            ends.append(float(pts) + float(length or 0))
        except ValueError:
            continue
    return max(ends, default=0.0)
