# Transcription Pipeline

[![CI](https://github.com/issabop/transcription-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/issabop/transcription-pipeline/actions/workflows/ci.yml)

A service that turns speech into a timestamped transcript and prepares it for downstream use: segments with start and end times, word timestamps, detected language, pauses, speaking stats, and SRT, WebVTT or plain-text exports.

**Live demo: https://transcribe.issaumer.com**. Upload a file or record from the microphone, or use the API via [/docs](https://transcribe.issaumer.com/docs). The demo accepts files up to 100 MB and 30 minutes.

Built with **FastAPI**, **ffmpeg** and **faster-whisper**, with a small React + shadcn/ui front end.

## Contents

- [API](#api)
- [How it works](#how-it-works)
- [Design decisions](#design-decisions)
- [Answers to the assignment questions](#answers-to-the-assignment-questions)
- [Model choice and benchmarks](#model-choice-and-benchmarks)
- [Configuration](#configuration)
- [Tests](#tests)
- [Limitations](#limitations)
- [Running locally](#running-locally)

## API

```bash
# Upload: returns 202 and a job id immediately
curl -F "file=@samples/jfk.flac" https://transcribe.issaumer.com/v1/transcriptions

# Poll until "status" is "done"
curl https://transcribe.issaumer.com/v1/transcriptions/<id>

# Export
curl "https://transcribe.issaumer.com/v1/transcriptions/<id>/transcript?format=srt"
```

| Endpoint | Description |
|---|---|
| `POST /v1/transcriptions` | Multipart upload: `file`, plus optional `language` (e.g. `en`, `ur`; auto-detected if omitted). Returns `202` with the job and a `Location` header |
| `GET /v1/transcriptions/{id}` | Status (`queued`, `processing`, `done`, `failed`), progress, and the transcript once done |
| `GET /v1/transcriptions/{id}/transcript` | `format=srt`, `vtt` or `txt`; with `txt`, `pauses=true` adds `[pause 2.1s]` markers |
| `GET /v1/languages` | Supported language codes |
| `GET /health` | Liveness, model, device and limits |

A finished job (trimmed):

```json
{
  "id": "e1f76dbadd654e2997586abcbf7d4ce3",
  "status": "done",
  "progress": 1.0,
  "audio": { "duration": 11.0, "format": "flac", "codec": "flac", "sample_rate": 44100, "channels": 1 },
  "result": {
    "language": "en",
    "language_probability": 0.94,
    "duration": 11.0,
    "text": "And so, my fellow Americans, ask not what your country can do for you, ask what you can do for your country.",
    "segments": [
      { "id": 0, "start": 0.0, "end": 10.34, "text": "And so, my fellow Americans, ...", "words": [{ "start": 0.0, "end": 0.3, "text": "And" }] }
    ],
    "pauses": [],
    "stats": { "word_count": 22, "speech_seconds": 10.34, "speech_ratio": 0.94, "words_per_minute": 127.7 }
  }
}
```

Errors return `{"detail": "..."}` with `413` (too large), `415` (not audio), `422` (too long or unsupported language), `404` (unknown job), `409` (not finished) or `429` (queue full).

## How it works

```
upload ──► ingest ──► queue ──► worker: audio ──► transcribe ──► process ──► SQLite ──► result / export
          (validate)  (202)            (ffmpeg)    (Whisper)     (clean, pauses, stats)
```

| Module | Responsibility |
|---|---|
| `app/pipeline/ingest.py` | Streams the upload to disk in 1 MB chunks under a random name, validates it with ffprobe, and enforces the size and duration limits |
| `app/pipeline/audio.py` | Converts any input to 16 kHz mono 16-bit WAV with ffmpeg |
| `app/pipeline/transcribe.py` | The `Transcriber` interface and the faster-whisper implementation (VAD, word timestamps, GPU when available) |
| `app/pipeline/process.py` | Clean-up, pause detection and stats |
| `app/pipeline/export.py` | SRT, WebVTT and plain text |
| `app/jobs.py` | Queue, worker pool, retries and recovery |
| `app/storage.py` | Job records in SQLite; audio files while a job runs |
| `app/main.py` | The HTTP API, which also serves the built front end |
| `frontend/` | React + Vite + shadcn/ui page, built to static files (no Node at runtime) |

## Design decisions

- **The engine is behind an interface.** The pipeline depends on `Transcriber.transcribe()`, not on Whisper. Another model, a hosted API or a test fake is a one-file change.
- **Content is validated, not the extension.** ffprobe must find a real audio stream, and anything ffmpeg can decode is accepted, including the audio track of a video.
- **One normalised format.** Every input becomes 16 kHz mono WAV, so the rest of the pipeline handles a single format. ffmpeg is the only decoder; the model receives the samples directly.
- **Asynchronous jobs.** Transcription can take minutes, so uploads return `202` immediately and progress is polled. The model is loaded once and shared by the workers.
- **Failures are classified.** Undecodable audio fails immediately; other errors are retried with exponential backoff, up to three attempts.
- **Jobs survive restarts.** State is persisted, interrupted jobs are re-queued on startup, a job interrupted on its last attempt is failed rather than retried forever, and workers are idempotent.
- **Privacy by default.** Audio is deleted when a job finishes; only the transcript is kept.
- **Pauses come from word timestamps,** so they're found inside segments as well as between them.
- **Configuration comes from environment variables,** so the same image runs anywhere with different limits or models.
- **One container.** The Dockerfile builds the front end in a Node stage and ships only Python, ffmpeg, the model and static files. The live demo is built from this same Dockerfile.

## Answers to the assignment questions

**1–3. Accept a file, transcribe it, return timestamps per segment.** `POST /v1/transcriptions` accepts the file (the web page can also record from the microphone and upload the recording). A background job transcribes it, and the result contains segments with `start`/`end` in seconds plus word-level timestamps, as JSON, SRT, VTT or text.

**4. How do you handle different audio formats?** First, validate by content: ffprobe must find an audio stream with a readable duration, so renamed, empty or non-audio files are rejected with `415`. For browser recordings, whose WebM header has no duration, it's read from the packet timestamps. Then normalise: ffmpeg converts everything to 16 kHz mono 16-bit PCM WAV, the format Whisper was trained on. Supported inputs are anything ffmpeg decodes: WAV, MP3, M4A/AAC, FLAC, OGG/Opus, WebM, and the audio track of video files (MP4, MOV, MKV, …). ffmpeg runs with a timeout, and broken files fail with a clear message.

**5. How do you deal with long audio files?** Uploads are streamed to disk, so memory stays flat. Transcription runs as a background job with progress reporting, so no request is held open. Whisper processes audio in 30-second windows while keeping absolute timestamps, so there's no manual stitching, and voice activity detection skips silence. A configurable maximum duration protects the service. At larger scale, I would split files at silences and transcribe the chunks in parallel across workers, offsetting each chunk's timestamps when merging.

**6. How would you handle concurrent uploads?** Uploads are accepted immediately and queued. A fixed pool of workers shares one loaded model, since transcription is compute-bound and unlimited parallelism would slow every job down. The queue is bounded, and the API returns `429` when it's full. At scale: separate API and worker processes connected by Redis (Celery or RQ), workers autoscaled on queue depth, and uploads sent straight to object storage through pre-signed URLs.

**7. How would you store audio and transcripts?** Here, SQLite holds job state and transcripts, and audio sits on a volume only while a job runs. In production: audio in object storage (S3/GCS) with a lifecycle rule for deletion, and jobs and transcripts in Postgres (JSONB segments, full-text search). Both stores are behind small classes (`JobStore`, `AudioStore`), so the change stays local.

**8. How do you retry or recover failed transcriptions?** Errors are classified: permanent ones (undecodable audio) fail immediately with a reason, and transient ones are retried with exponential backoff up to a limit. Every state change and attempt count is persisted, so unfinished jobs are re-queued after a crash or redeploy, jobs that keep crashing are failed instead of looping, and idempotent workers ignore duplicate deliveries. In production I'd add a dead-letter queue, alerting on failure rates, and a manual retry endpoint.

**9. How would you expose this as an API?** As a versioned REST API using the asynchronous job pattern: `POST` returns `202 Accepted` with a `Location` header, `GET` polls the job, and a separate endpoint serves subtitle exports. OpenAPI docs are generated at `/docs`. For production: API keys or OAuth with per-key rate limits, webhooks as an alternative to polling, pre-signed uploads for very large files, and request IDs for tracing.

## Model choice and benchmarks

faster-whisper runs OpenAI's Whisper on CTranslate2: int8 on CPU, float16 on GPU, picked automatically. It's open source and runs locally (no per-minute cost, and audio never leaves the server), and it covers ~100 languages. Faster models such as NVIDIA Parakeet only cover English and European languages.

Among the Whisper sizes, the default was chosen by measurement with `scripts/benchmark.py` (110 s of English speech, 4-core CPU, int8, word error rate against the known text):

| Model | Transcribe 110 s | Speed | WER |
|---|---|---|---|
| base | 22.1 s | 5.0x real time | 20.0% |
| small | 67.9 s | 1.6x real time | 0.5% |
| **large-v3-turbo** | 97.9 s | 1.1x real time | **0.0%** |

`base` silently dropped whole phrases on longer audio. `large-v3-turbo` was word-perfect, still faster than real time on CPU, and much stronger outside English, so it's the default. Any size can be selected with `PIPELINE_MODEL_SIZE`.

## Configuration

Environment variables (see `.env.example`):

| Variable | Default | |
|---|---|---|
| `PIPELINE_MODEL_SIZE` | `large-v3-turbo` | Any Whisper size, e.g. `base`, `small`, `large-v3` |
| `PIPELINE_DEVICE` / `PIPELINE_COMPUTE_TYPE` | `auto` / `auto` | GPU with float16 if available, otherwise CPU with int8 |
| `PIPELINE_MAX_UPLOAD_MB` | `500` | Upload size limit |
| `PIPELINE_MAX_DURATION_MINUTES` | `60` | Audio length limit |
| `PIPELINE_WORKERS` | `1` | Concurrent transcriptions |
| `PIPELINE_MAX_PENDING_JOBS` | `20` | Queue size before `429` |
| `PIPELINE_MAX_ATTEMPTS` | `3` | Attempts for transient failures |
| `PIPELINE_PAUSE_THRESHOLD_SECONDS` | `1.5` | Shortest gap reported as a pause |

## Tests

```bash
pip install -r requirements-dev.txt
pytest            # fast suite, using a fake engine and audio generated with ffmpeg
pytest -m slow    # end-to-end with a real Whisper model on samples/jfk.flac
```

The suite covers upload validation, format conversion (including browser recordings), clean-up, pauses, stats, exports, the full API flow, retries and backoff, permanent failures, and recovery after a restart. GitHub Actions runs it on every push, along with the front-end type check, build and lint, and a Docker build.

## Limitations

- **Single process.** State is persisted, but the queue is in memory, so the service scales vertically. Horizontal scaling needs a shared queue (see Q6).
- **No authentication** on the demo (see Q9).
- **One language per file.** Whisper detects the language from the first ~30 s and writes the whole transcript in it, so with mixed-language speech the other language may come out translated. Passing `language` controls this.
- **Fillers** such as "um" are usually omitted by Whisper. Pauses are reported instead.
- **No speaker diarisation.** It could be added with a model such as pyannote, aligned to the word timestamps.

## Running locally

With Docker (the model is baked into the image):

```bash
docker build -t transcription-pipeline .
docker run -p 8000:8000 -v transcripts:/app/data transcription-pipeline
```

Without Docker (Python 3.12, ffmpeg and Node 20+):

```bash
cd frontend && npm ci && npm run build && cd ..
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

Then open http://localhost:8000.

`samples/jfk.flac` is the public-domain clip used by Whisper's own test suite.
