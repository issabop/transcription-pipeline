"""Chunked uploads: a big file arrives in small pieces, so no single request is slow enough to hit a proxy timeout.

The client declares the file size, sends the bytes in order with their offset, then completes the upload.
Resending a chunk is safe: the file is rewritten from that offset.
"""

import json
import re
import time
import uuid
from pathlib import Path

from app.pipeline.ingest import MB, FileTooLarge, NotAudio

CHUNK_SIZE = 2 * MB  # a few seconds per request, even on a slow connection
STALE_AFTER_SECONDS = 24 * 3600
_UPLOAD_ID = re.compile(r"[0-9a-f]{32}")  # ids end up in file paths, so nothing else is accepted


class UploadError(Exception):
    """The request can't be applied to this upload. The message is safe to show to the caller."""


class UploadNotFound(UploadError):
    pass


class OffsetMismatch(UploadError):
    pass


class UploadIncomplete(UploadError):
    pass


class ChunkedUploads:
    """Partial files on disk (`{id}.part`) plus their declared size (`{id}.json`)."""

    def __init__(self, root: Path, max_bytes: int):
        self.root = root
        self.max_bytes = max_bytes
        root.mkdir(parents=True, exist_ok=True)

    def create(self, size: int) -> str:
        if size <= 0:
            raise NotAudio("File is empty")
        if size > self.max_bytes:
            raise FileTooLarge(f"File is larger than the {self.max_bytes // MB} MB limit")
        upload_id = uuid.uuid4().hex
        self._data(upload_id).touch()
        self._meta(upload_id).write_text(json.dumps({"size": size}))
        return upload_id

    def write(self, upload_id: str, offset: int, data: bytes) -> int:
        """Write one chunk at `offset` and return the number of bytes received so far."""
        size = self._declared_size(upload_id)
        path = self._data(upload_id)
        received = path.stat().st_size
        if offset > received:
            raise OffsetMismatch(f"Chunk starts at {offset}, but only {received} bytes have arrived")
        if offset + len(data) > size:
            raise FileTooLarge("Chunk goes past the declared file size")
        with path.open("r+b") as f:
            f.truncate(offset)  # a resent chunk replaces whatever was written from this point on
            f.seek(offset)
            f.write(data)
        return offset + len(data)

    def finish(self, upload_id: str, target: Path) -> Path:
        """Move the complete file to `target`. Fails if any bytes are missing."""
        size = self._declared_size(upload_id)
        path = self._data(upload_id)
        received = path.stat().st_size
        if received != size:
            raise UploadIncomplete(f"Received {received} of {size} bytes")
        target.parent.mkdir(parents=True, exist_ok=True)
        path.replace(target)
        self._meta(upload_id).unlink(missing_ok=True)
        return target

    def remove_stale(self) -> None:
        """Delete uploads that were started but never completed."""
        cutoff = time.time() - STALE_AFTER_SECONDS
        for path in self.root.iterdir():
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)

    def _declared_size(self, upload_id: str) -> int:
        if not _UPLOAD_ID.fullmatch(upload_id) or not self._meta(upload_id).exists():
            raise UploadNotFound("No upload with that id")
        return json.loads(self._meta(upload_id).read_text())["size"]

    def _data(self, upload_id: str) -> Path:
        return self.root / f"{upload_id}.part"

    def _meta(self, upload_id: str) -> Path:
        return self.root / f"{upload_id}.json"
