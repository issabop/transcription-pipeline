"""Where things are kept: jobs and transcripts in SQLite, audio as files on disk while a job needs it."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.models import AudioInfo, Job, Transcript

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    language    TEXT,
    progress    REAL NOT NULL DEFAULT 0,
    attempts    INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    audio       TEXT NOT NULL,  -- AudioInfo as JSON
    result      TEXT,           -- Transcript as JSON, once done
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
)
"""


class JobStore:
    """Job records in a single SQLite file. Each call opens its own connection, so worker threads can share it."""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")  # readers don't block the writer
            db.execute(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:  # commits on success, rolls back on error
                yield db
        finally:
            db.close()

    def create(self, job_id: str, language: str | None, audio: AudioInfo) -> Job:
        now = _now()
        with self._connect() as db:
            db.execute(
                "INSERT INTO jobs (id, status, language, audio, created_at, updated_at) VALUES (?, 'queued', ?, ?, ?, ?)",
                (job_id, language, audio.model_dump_json(), now, now),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> Job | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _to_job(row) if row else None

    def update(self, job_id: str, **fields) -> None:
        if isinstance(fields.get("result"), Transcript):
            fields["result"] = fields["result"].model_dump_json()
        fields["updated_at"] = _now()
        columns = ", ".join(f"{name} = ?" for name in fields)  # names come from our code, never from users
        with self._connect() as db:
            db.execute(f"UPDATE jobs SET {columns} WHERE id = ?", (*fields.values(), job_id))

    def count_pending(self) -> int:
        with self._connect() as db:
            return db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'processing')").fetchone()[0]

    def unfinished(self) -> list[Job]:
        """Jobs that were queued or mid-way when the server last stopped, oldest first."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM jobs WHERE status IN ('queued', 'processing') ORDER BY created_at"
            ).fetchall()
        return [_to_job(row) for row in rows]


class AudioStore:
    """Audio files on disk: the original upload, and the converted WAV while a job runs."""

    def __init__(self, data_dir: Path):
        self.upload_dir = data_dir / "uploads"
        self.work_dir = data_dir / "work"

    def upload_path(self, job_id: str) -> Path:
        return self.upload_dir / job_id

    def wav_path(self, job_id: str) -> Path:
        return self.work_dir / f"{job_id}.wav"

    def delete(self, job_id: str) -> None:
        """Audio isn't kept once a job has finished: only the transcript is."""
        self.upload_path(job_id).unlink(missing_ok=True)
        self.wav_path(job_id).unlink(missing_ok=True)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        status=row["status"],
        language=row["language"],
        progress=row["progress"],
        attempts=row["attempts"],
        error=row["error"],
        audio=AudioInfo.model_validate_json(row["audio"]),
        result=Transcript.model_validate_json(row["result"]) if row["result"] else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
