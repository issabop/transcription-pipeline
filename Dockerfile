# Stage 1: build the web page (React + shadcn/ui) into static files
FROM node:24-slim AS web
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Stage 2: the service itself. Node isn't included; only the built page is copied in.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg decodes and converts every upload
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# Download the model at build time, so the container starts ready and never needs the internet.
ARG MODEL_SIZE=large-v3-turbo
ENV PIPELINE_MODEL_SIZE=${MODEL_SIZE} \
    PIPELINE_MODEL_DIR=/app/models \
    PIPELINE_DATA_DIR=/app/data
RUN python -c "import faster_whisper, os; faster_whisper.download_model(os.environ['PIPELINE_MODEL_SIZE'], cache_dir='/app/models')"
ENV HF_HUB_OFFLINE=1

COPY app ./app
COPY --from=web /build/app/static ./app/static

# Run as an unprivileged user; /app/data is the only place it writes to.
RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown app /app/data
USER app
VOLUME /app/data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# One process: the background workers live inside it, next to the loaded model.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
