"""The FastAPI app: routes, error handling, and starting the background workers."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings, settings
from app.jobs import JobRunner
from app.models import Job
from app.pipeline.export import to_srt, to_txt, to_vtt
from app.pipeline.ingest import MB, AudioTooLong, FileTooLarge, IngestError, NotAudio, ingest
from app.pipeline.transcribe import Transcriber, WhisperTranscriber
from app.storage import AudioStore, JobStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# The web page, built from frontend/ (see the Dockerfile). Without it the API still works.
STATIC_DIR = Path(__file__).parent / "static"

# The pipeline raises plain Python errors; only this file knows about HTTP status codes.
INGEST_ERROR_STATUS = {FileTooLarge: 413, NotAudio: 415, AudioTooLong: 422}

EXPORTS = {
    "srt": ("application/x-subrip", to_srt),
    "vtt": ("text/vtt", to_vtt),
    "txt": ("text/plain", to_txt),
}


def create_app(config: Settings = settings, transcriber: Transcriber | None = None) -> FastAPI:
    """Build the app. Tests pass their own settings and a fake transcriber."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = transcriber or WhisperTranscriber(
            config.model_size, config.model_dir, config.device, config.compute_type, config.beam_size, config.workers
        )
        app.state.transcriber = engine
        app.state.store = JobStore(config.data_dir / "jobs.db")
        app.state.audio = AudioStore(config.data_dir)
        app.state.runner = JobRunner(
            app.state.store, app.state.audio, engine, config.workers, config.max_attempts, config.pause_threshold_seconds
        )
        app.state.runner.start()
        yield
        app.state.runner.stop()

    app = FastAPI(
        title=config.app_name,
        description="Upload audio, get back a timestamped transcript. Jobs run in the background: poll for the result.",
        lifespan=lifespan,
    )

    @app.exception_handler(IngestError)
    def ingest_error(request: Request, exc: IngestError) -> JSONResponse:
        return JSONResponse(status_code=INGEST_ERROR_STATUS[type(exc)], content={"detail": str(exc)})

    @app.middleware("http")
    async def reject_oversized_bodies(request: Request, call_next):
        """Refuse a request that declares a too-large body before any of it is read.

        The chunked copy in ingest.py still enforces the limit if the header is missing or wrong.
        """
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > config.max_upload_mb * MB + MB:
            return JSONResponse(status_code=413, content={"detail": f"File is larger than the {config.max_upload_mb} MB limit"})
        return await call_next(request)

    @app.get("/health")
    def health(request: Request) -> dict[str, str | int]:
        """Liveness check for Docker and the hosting platform, plus what this instance accepts."""
        engine = request.app.state.transcriber
        return {
            "status": "ok",
            "model": getattr(engine, "model_size", "custom"),
            "device": getattr(engine, "device", "unknown"),
            "max_upload_mb": config.max_upload_mb,
            "max_duration_minutes": config.max_duration_minutes,
        }

    @app.get("/v1/languages")
    def languages(request: Request) -> list[str]:
        """Language codes the model can transcribe, for the `language` field."""
        return sorted(request.app.state.transcriber.supported_languages)

    @app.post("/v1/transcriptions", status_code=202, response_model=Job)
    def create_transcription(
        request: Request,
        response: Response,
        file: UploadFile,
        language: Annotated[str | None, Form(description="ISO code such as 'en' or 'ur'. Leave empty to auto-detect.")] = None,
    ) -> Job:
        """Upload an audio or video file. Returns a job straight away; transcription runs in the background."""
        state = request.app.state
        language = language or None  # an empty form field means auto-detect
        if language and language not in state.transcriber.supported_languages:
            raise HTTPException(422, f"Unsupported language '{language}'")
        if state.store.count_pending() >= config.max_pending_jobs:
            raise HTTPException(429, "Too many jobs in the queue, try again in a few minutes")

        path, info = ingest(file.file, state.audio.upload_dir, config.max_upload_mb * MB, config.max_duration_minutes * 60)
        job = state.store.create(path.name, language, info)
        state.runner.submit(job.id)
        response.headers["Location"] = f"/v1/transcriptions/{job.id}"
        return job

    @app.get("/v1/transcriptions/{job_id}", response_model=Job)
    def get_transcription(request: Request, job_id: str) -> Job:
        """Job status and progress. Once `status` is `done`, `result` holds the transcript."""
        return _find_job(request, job_id)

    @app.get("/v1/transcriptions/{job_id}/transcript", response_class=PlainTextResponse)
    def download_transcript(
        request: Request,
        job_id: str,
        format: Literal["srt", "vtt", "txt"] = "txt",
        pauses: Annotated[bool, Query(description="txt only: mark pauses as [pause 2.1s]")] = False,
    ) -> PlainTextResponse:
        """The finished transcript as subtitles (SRT, WebVTT) or plain text."""
        job = _find_job(request, job_id)
        if job.status != "done":
            raise HTTPException(409, f"Transcript not ready, the job is {job.status}")
        media_type, render = EXPORTS[format]
        body = render(job.result, pauses) if format == "txt" else render(job.result)
        return PlainTextResponse(
            body,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{job_id}.{format}"'},
        )

    # Registered last, so the API routes above always take precedence over static files.
    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="web")

    return app


def _find_job(request: Request, job_id: str) -> Job:
    job = request.app.state.store.get(job_id)
    if job is None:
        raise HTTPException(404, "No transcription with that id")
    return job


app = create_app()
