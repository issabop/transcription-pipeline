"""Retries, permanent failures and recovery after a restart, driven through the job runner."""

import io
import time

from app.jobs import JobRunner
from app.models import AudioInfo
from app.pipeline.ingest import MB, ingest
from app.storage import AudioStore, JobStore
from tests.conftest import FakeTranscriber


def runner_for(settings, transcriber, max_attempts=3):
    store = JobStore(settings.data_dir / "jobs.db")
    audio = AudioStore(settings.data_dir)
    runner = JobRunner(store, audio, transcriber, workers=1, max_attempts=max_attempts,
                       pause_threshold=1.5, retry_base_delay=0.01)
    return store, audio, runner


def add_job(store, audio, wav):
    path, info = ingest(io.BytesIO(wav.read_bytes()), audio.upload_dir, 5 * MB, 60)
    return store.create(path.name, None, info)


def run_until_finished(runner, store, job_id, timeout=10):
    """Start the runner (which picks up every queued job) and wait for this one."""
    runner.start()
    deadline = time.monotonic() + timeout
    while (job := store.get(job_id)).status not in ("done", "failed"):
        assert time.monotonic() < deadline, job
        time.sleep(0.02)
    runner.stop()
    return job


def test_temporary_failures_are_retried(settings, wav):
    engine = FakeTranscriber(failures=2)
    store, audio, runner = runner_for(settings, engine)
    job = add_job(store, audio, wav)
    job = run_until_finished(runner, store, job.id)
    assert job.status == "done"
    assert job.attempts == 3
    assert job.error is None


def test_gives_up_after_max_attempts(settings, wav):
    engine = FakeTranscriber(failures=99)
    store, audio, runner = runner_for(settings, engine)
    job = add_job(store, audio, wav)
    job = run_until_finished(runner, store, job.id)
    assert job.status == "failed"
    assert job.attempts == 3
    assert "after 3 attempts" in job.error
    assert not audio.upload_path(job.id).exists()


def test_undecodable_audio_fails_without_retrying(settings, wav):
    engine = FakeTranscriber()
    store, audio, runner = runner_for(settings, engine)
    job = add_job(store, audio, wav)
    audio.upload_path(job.id).write_bytes(b"\xff\xfb" + b"\x00" * 100)  # corrupted after upload
    job = run_until_finished(runner, store, job.id)
    assert job.status == "failed"
    assert job.attempts == 1
    assert engine.calls == 0
    assert job.error == "Could not decode the audio"


def test_unfinished_jobs_resume_after_a_restart(settings, wav):
    store, audio, runner = runner_for(settings, FakeTranscriber())
    queued = add_job(store, audio, wav)
    interrupted = add_job(store, audio, wav)
    store.update(interrupted.id, status="processing", attempts=1, progress=0.4)  # the server died mid-job

    run_until_finished(runner, store, interrupted.id)  # start() alone must pick both up
    assert store.get(queued.id).status == "done"
    assert store.get(interrupted.id).attempts == 2


def test_job_interrupted_on_its_last_attempt_is_failed(settings, wav):
    store, audio, runner = runner_for(settings, FakeTranscriber(), max_attempts=2)
    job = add_job(store, audio, wav)
    store.update(job.id, status="processing", attempts=2)
    job = run_until_finished(runner, store, job.id)
    assert job.status == "failed"
    assert not audio.upload_path(job.id).exists()


def test_job_round_trips_through_sqlite(settings):
    store = JobStore(settings.data_dir / "jobs.db")
    info = AudioInfo(duration=1.5, format="wav", codec="pcm_s16le", sample_rate=16000, channels=1)
    created = store.create("abc", "ur", info)
    assert store.get("abc") == created
    assert store.count_pending() == 1
