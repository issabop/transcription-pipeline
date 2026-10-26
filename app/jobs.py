"""Background work: a queue, a pool of worker threads, retries, and recovery after a restart."""

import logging
import queue
import threading

from app.pipeline.audio import ConversionError, to_wav
from app.pipeline.process import build_transcript
from app.pipeline.transcribe import Transcriber
from app.storage import AudioStore, JobStore

log = logging.getLogger(__name__)


class JobRunner:
    def __init__(
        self,
        store: JobStore,
        audio: AudioStore,
        transcriber: Transcriber,
        workers: int,
        max_attempts: int,
        pause_threshold: float,
        retry_base_delay: float = 2.0,
    ):
        self.store = store
        self.audio = audio
        self.transcriber = transcriber
        self.workers = workers
        self.max_attempts = max_attempts
        self.pause_threshold = pause_threshold
        self.retry_base_delay = retry_base_delay
        self.queue: queue.Queue[str | None] = queue.Queue()

    def start(self) -> None:
        """Pick up anything left over from the last run, then start the workers."""
        for job in self.store.unfinished():
            if job.status == "processing" and job.attempts >= self.max_attempts:
                # It was interrupted on its last allowed attempt (e.g. the server crashed mid-job).
                self._fail(job.id, f"Transcription failed after {job.attempts} attempts")
            else:
                self.store.update(job.id, status="queued", progress=0)
                self.queue.put(job.id)
        for i in range(self.workers):
            threading.Thread(target=self._work, name=f"worker-{i}", daemon=True).start()

    def stop(self) -> None:
        for _ in range(self.workers):
            self.queue.put(None)  # one stop signal per worker

    def submit(self, job_id: str) -> None:
        self.queue.put(job_id)

    def _work(self) -> None:
        while (job_id := self.queue.get()) is not None:
            self._run(job_id)

    def _run(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if job is None or job.status != "queued":
            return  # already handled: a job id that ends up in the queue twice only runs once
        attempt = job.attempts + 1
        self.store.update(job_id, status="processing", attempts=attempt, progress=0, error=None)
        try:
            wav = to_wav(self.audio.upload_path(job_id), self.audio.wav_path(job_id))
            raw = self.transcriber.transcribe(
                wav, job.language, on_progress=lambda p: self.store.update(job_id, progress=round(p, 3))
            )
            transcript = build_transcript(raw, self.pause_threshold)
        except ConversionError as exc:
            # The file itself is the problem, so another attempt would fail the same way.
            self._fail(job_id, str(exc))
        except Exception:
            log.exception("Job %s failed on attempt %d", job_id, attempt)
            if attempt < self.max_attempts:
                delay = self.retry_base_delay * 2 ** (attempt - 1)  # 2 s, 4 s, 8 s ...
                self.store.update(job_id, status="queued", error=f"Attempt {attempt} failed, retrying in {delay:g}s")
                threading.Timer(delay, self.queue.put, args=[job_id]).start()
            else:
                self._fail(job_id, f"Transcription failed after {attempt} attempts")
        else:
            self.store.update(job_id, status="done", progress=1.0, result=transcript)
            self.audio.delete(job_id)

    def _fail(self, job_id: str, reason: str) -> None:
        self.store.update(job_id, status="failed", error=reason)
        self.audio.delete(job_id)
