# Transcription Pipeline

A small service that turns audio into a timestamped transcript and prepares it for downstream use. Upload a file and get back segments with start and end times, word timestamps, detected language, pauses and speaking stats, plus SRT, WebVTT or plain-text exports.

**Live demo: https://transcribe.issaumer.com**. Upload a file in the browser, or try the API at [/docs](https://transcribe.issaumer.com/docs). Nothing to install.

Built with **FastAPI**, **ffmpeg** and **faster-whisper** (open-source Whisper), with a small React + shadcn/ui page on top. It runs on CPU and uses a GPU automatically when there is one.

## Contents

- [Using it](#using-it)
- [How it works](#how-it-works)
- [Design decisions](#design-decisions)
- [Answers to the assignment questions](#answers-to-the-assignment-questions)
- [Benchmarks](#benchmarks)
- [Configuration](#configuration)
- [Tests](#tests)
- [Limitations and next steps](#limitations-and-next-steps)
- [Running it yourself](#running-it-yourself)

## Using it

**In the browser:** open the [live demo](https://transcribe.issaumer.com), pick a file, and watch the progress. The result shows the language, stats, every segment with its timestamps (pauses inline if you want them) and download links.

**From the API:**

```bash
# 1. Upload: returns 202 with a job id straight away
curl -F "file=@samples/jfk.flac" -F "language=en" https://transcribe.issaumer.com/v1/transcriptions

# 2. Poll until status is "done"
curl https://transcribe.issaumer.com/v1/transcriptions/<id>

# 3. Download as subtitles or text
curl "https://transcribe.issaumer.com/v1/transcriptions/<id>/transcript?format=srt"
curl "https://transcribe.issaumer.com/v1/transcriptions/<id>/transcript?format=txt&pauses=true"
```

A finished job (trimmed):

```json
{
  "id": "e1f76dbadd654e2997586abcbf7d4ce3",
  "status": "done",
  "progress": 1.0,
  "attempts": 1,
  "audio": { "duration": 11.0, "format": "flac", "codec": "flac", "sample_rate": 44100, "channels": 1 },
  "result": {
    "language": "en",
    "language_probability": 0.939,
    "duration": 11.0,
    "text": "And so my fellow Americans, ask not what your country can do for you, ask what you can do for your country.",
    "segments": [
      {
        "id": 0, "start": 0.0, "end": 11.0,
        "text": "And so my fellow Americans, ask not what ...",
        "words": [{ "start": 0.0, "end": 0.3, "text": "And" }, "..."]
      }
    ],
    "pauses": [],
    "stats": { "word_count": 22, "speech_seconds": 11.0, "speech_ratio": 1.0, "words_per_minute": 120.0 }
  }
}
```

| Endpoint | What it does |
|---|---|
| `POST /v1/transcriptions` | Upload a file (`file`, optional `language` such as `en` or `ur`). Returns **202** and a job, with a `Location` header |
| `GET /v1/transcriptions/{id}` | Status (`queued`, `processing`, `done`, `failed`), progress, and the transcript when done |
| `GET /v1/transcriptions/{id}/transcript` | `format=srt`, `vtt` or `txt`; `pauses=true` adds `[pause 2.1s]` markers to `txt` |
| `GET /health` | Liveness, plus the model, device and upload limits in use |

Errors are JSON `{"detail": "..."}` with a meaningful status: **413** too large, **415** not audio, **422** too long or an unsupported language, **404** unknown job, **409** not finished yet, **429** queue full.

## How it works

```
upload ──► ingest ──► job queue ──► worker: audio ──► transcribe ──► process ──► SQLite
           (validate)  (202 + id)          (ffmpeg)    (Whisper)     (clean,       │
                                                                     pauses,       ▼
                                                                     stats)    GET result / export
```

Each stage is one file in `app/pipeline/`, in the order the audio flows:

| File | Stage |
|---|---|
| `ingest.py` | Streams the upload to disk in 1 MB chunks under a random name, then asks **ffprobe** what is really inside. Enforces the size and duration limits |
| `audio.py` | **ffmpeg** converts anything (MP3, M4A, FLAC, OGG, even a video's soundtrack) to 16 kHz mono 16-bit WAV, which is what Whisper expects |
| `transcribe.py` | The `Transcriber` interface and the faster-whisper engine: voice activity detection, word timestamps, GPU when available |
| `process.py` | Clean-up, pause detection from word gaps, and stats |
| `export.py` | SRT, WebVTT and plain text |

Around them: `jobs.py` (queue, workers, retries, recovery), `storage.py` (SQLite and audio files) and `main.py` (the API). The page lives in `frontend/` (React, Vite, Tailwind and shadcn/ui components). It's built into static files that FastAPI serves, so it's one container and one URL, with no Node at runtime.

## Design decisions

- **The engine sits behind an interface.** The pipeline only knows `Transcriber.transcribe()`. Swapping in another model or a hosted API, or a fake for tests, touches one file.
- **faster-whisper with `large-v3-turbo` as the default.** It covers 99 languages, including Urdu and mixed English/Urdu speech, which faster English-only models (e.g. NVIDIA Parakeet) can't handle. faster-whisper runs Whisper on CTranslate2 with int8 quantisation on CPU. The default was picked by measuring (see [Benchmarks](#benchmarks)).
- **Never trust the extension.** ffprobe has to find a real audio stream with a duration. A renamed text file is rejected, while a video's soundtrack is accepted.
- **ffmpeg is the only decoder.** The model receives samples read straight from the converted WAV, so audio is decoded once. This also sidesteps a PyAV/faster-whisper incompatibility found during the build.
- **Asynchronous jobs.** Transcription can take minutes, so uploads return `202` immediately and the work happens in the background with visible progress. The model is loaded once and shared.
- **Temporary and permanent failures are treated differently.** Undecodable audio fails immediately. Anything else is retried with exponential backoff (2 s, 4 s), up to 3 attempts.
- **Jobs survive restarts.** State lives in SQLite. On startup, interrupted jobs are queued again, unless they were already on their last attempt (so a file that crashes the server can't loop forever). The worker skips any job that isn't `queued`, so a duplicate queue entry is harmless.
- **Privacy by default.** Audio is deleted as soon as a job finishes; only the transcript is kept.
- **Pauses come from word timestamps.** Gaps between segments alone missed pauses inside a segment. Word timestamps cost about 5–10% more time and make pause positions exact.
- **Configuration lives in the environment** (pydantic-settings), so the same image runs anywhere with different limits or models.

## Answers to the assignment questions

**1–3. Accept a file, transcribe it, return timestamps per segment.** `POST /v1/transcriptions` accepts the upload. A background job transcribes it with Whisper, and the result has segments with `start`/`end` in seconds (plus word-level timestamps), available as JSON, SRT, VTT or text.

**4. How do you handle different audio formats?** Two steps. First, *validation by content*: ffprobe must find an audio stream, so the extension is ignored. Second, *normalisation*: ffmpeg converts every input to 16 kHz mono 16-bit PCM WAV, the format Whisper was trained on. Anything ffmpeg can decode works, including video files. Broken files fail with a clear message instead of a crash, and ffmpeg runs with a timeout.

**5. How do you deal with long audio files?**
- **Not blocking the request:** the upload returns at once and the client polls a job with a progress percentage.
- **Memory:** uploads are streamed to disk in chunks.
- **Model context:** Whisper processes audio in 30 s windows and keeps absolute timestamps, so there's no hand-made chunk stitching that could cut words in half or misalign timestamps.
- **Speed:** voice activity detection skips silence.
- **Guardrail:** a configurable maximum duration (60 min by default).
- **At larger scale:** split long files at silences into chunks, transcribe them in parallel on several workers or GPUs, and shift each chunk's timestamps by its offset when merging.

**6. How would you handle concurrent uploads?** Uploads are accepted immediately and queued. A fixed pool of workers (`PIPELINE_WORKERS`) shares one loaded model, because transcription is CPU/GPU-bound and running everything at once would only make every job slower. The queue is capped (`PIPELINE_MAX_PENDING_JOBS`), and the API answers `429` when it's full instead of falling over. At scale, I'd split the API from the workers: the API puts jobs on Redis (Celery/RQ), and GPU workers scale with the queue length. Uploads would go straight to object storage through pre-signed URLs, so the API never handles large bodies.

**7. How would you store audio and transcripts?** Here: SQLite for job state and transcripts (JSON), and a local volume for audio, which is deleted once the job ends. In production: audio in object storage (S3/GCS) with a lifecycle rule that deletes it after N days, and job metadata and transcripts in Postgres (JSONB for segments, full-text search over the text). Large transcripts or exports could also go to object storage. Storage sits behind small classes (`JobStore`, `AudioStore`), so the swap is local.

**8. How do you retry or recover failed transcriptions?**
- **Classify errors:** permanent (bad audio) fails at once with a reason; temporary errors retry with exponential backoff, up to `PIPELINE_MAX_ATTEMPTS`.
- **Persist state:** every transition (`queued → processing → done/failed`) and the attempt count are saved, so after a crash or redeploy unfinished jobs are re-queued automatically.
- **Poison jobs:** a job that was interrupted on its last attempt is marked failed rather than retried forever.
- **Duplicate deliveries:** workers are idempotent, so a duplicate queue entry does nothing.
- **In production:** a dead-letter queue for final failures, alerts on failure rate, and a manual "retry" endpoint.

**9. How would you expose this as an API?** A versioned REST API using the asynchronous job pattern: `POST /v1/transcriptions` → `202 Accepted` + `Location`; `GET` the job to poll; a separate export endpoint for subtitle formats. FastAPI generates the OpenAPI docs at `/docs`. Next I'd add API-key or OAuth authentication with per-key rate limits, webhooks (a callback URL notified when the job finishes) as an alternative to polling, pre-signed upload URLs for very large files, and request IDs in logs for tracing.

## Benchmarks

Measured with `scripts/benchmark.py` on a laptop CPU (Intel i5-1145G7, 4 cores, int8), on 110 s of English speech (the JFK sample repeated 10 times), with word error rate against the known text:

| Model | Load | Transcribe 110 s | Speed | WER |
|---|---|---|---|---|
| base | 0.9 s | 22.1 s | 5.0x real time | 20.0% |
| small | 1.3 s | 67.9 s | 1.6x real time | 0.5% |
| **large-v3-turbo** | 4.3 s | 97.9 s | 1.1x real time | **0.0%** |

`base` is fast, but on longer audio it lost its place and **dropped a whole phrase twice**. `large-v3-turbo` was word-perfect while still running faster than real time on a laptop CPU, and it's much stronger on languages other than English, which is why it's the default. On a GPU it runs many times faster with no change in settings.

Very short clips are relatively slower: Whisper always processes a full 30-second window, so an 11 s clip costs about as much as 30 s of audio (around 25 s on this CPU). To reproduce:

```bash
python -m scripts.benchmark samples/jfk.flac --models base small large-v3-turbo \
  --reference "And so my fellow Americans, ask not what your country can do for you, ask what you can do for your country."
```

## Configuration

Every setting is an environment variable with a sensible default (see `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `PIPELINE_MODEL_SIZE` | `large-v3-turbo` | `tiny`, `base`, `small`, `medium`, `large-v3`, `large-v3-turbo` |
| `PIPELINE_DEVICE` | `auto` | `auto` (GPU if available), `cpu` or `cuda` |
| `PIPELINE_COMPUTE_TYPE` | `auto` | `float16` on GPU, `int8` on CPU |
| `PIPELINE_BEAM_SIZE` | `5` | Higher is slightly more accurate and slower |
| `PIPELINE_MAX_UPLOAD_MB` | `500` | Upload size limit |
| `PIPELINE_MAX_DURATION_MINUTES` | `60` | Audio length limit |
| `PIPELINE_WORKERS` | `1` | Jobs transcribed at the same time |
| `PIPELINE_MAX_PENDING_JOBS` | `20` | Queue size before uploads get `429` |
| `PIPELINE_MAX_ATTEMPTS` | `3` | Attempts for temporary failures |
| `PIPELINE_PAUSE_THRESHOLD_SECONDS` | `1.5` | Shortest gap reported as a pause |
| `PIPELINE_DATA_DIR` / `PIPELINE_MODEL_DIR` | `data` / `models` | Where jobs, audio and models live |

## Tests

```bash
pip install -r requirements-dev.txt
pytest              # 39 fast tests, about 7 s, using a fake engine and audio generated with ffmpeg
pytest -m slow      # one end-to-end test with a real Whisper model on samples/jfk.flac
```

The fast tests cover every upload rejection, format conversion, clean-up, pauses, stats, every export format, the full API flow, retries with backoff, permanent failures, and recovery after a restart.

## Limitations and next steps

- **One process.** The queue lives in memory (state is persisted in SQLite), so the service scales vertically. Horizontal scaling means moving to Redis-backed workers, as described in Q6.
- **No authentication.** Fine for a demo; see Q9 for what production needs.
- **CPU speed.** A large model on a small CPU server runs at around real time. A GPU is many times faster and is used automatically when present.
- **Fillers ("um", "uh") are mostly not transcribed.** Whisper was trained on cleaned-up transcripts and tends to drop them. Pauses are detected reliably; fillers would need a verbatim-tuned model or prompting.
- **Urdu is written in Urdu script.** Romanised Urdu would need a transliteration step after transcription (e.g. an LLM rewriting each segment while keeping its timestamps).
- **No speaker labels** (who spoke when). That would need a diarisation model such as pyannote, merged with the word timestamps.

## Running it yourself

Not needed to try it (see the live demo above), but here's how.

### With Docker

```bash
docker build -t transcription-pipeline .
docker run -p 8000:8000 -v transcripts:/app/data transcription-pipeline
```

Open http://localhost:8000 for the upload page, or http://localhost:8000/docs for the interactive API docs. The model is downloaded during the build, so the container starts ready and works offline. Use `--build-arg MODEL_SIZE=base` for a smaller, faster image.

### Without Docker

Needs Python 3.12, ffmpeg on the PATH, and Node 20+ to build the page.

```bash
cd frontend && npm ci && npm run build && cd ..   # builds the page into app/static
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

The model is downloaded into `models/` on first start. Without the page build, the API and `/docs` still work.

`samples/jfk.flac` is the public-domain JFK inaugural address excerpt that Whisper's own test suite uses.
