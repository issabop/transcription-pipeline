"""Settings, read from environment variables (or a local .env file) with safe defaults."""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PIPELINE_")

    app_name: str = "Transcription Pipeline"
    data_dir: Path = Path("data")

    # Upload limits
    max_upload_mb: int = 500
    max_duration_minutes: int = 60

    # Speech-to-text engine
    model_size: str = "large-v3-turbo"
    model_dir: Path = Path("models")
    device: Literal["auto", "cpu", "cuda"] = "auto"
    compute_type: str = "auto"  # auto = float16 on GPU, int8 on CPU
    beam_size: int = 5

    # Background jobs
    workers: int = 1
    max_pending_jobs: int = 20
    max_attempts: int = 3

    # Processing
    pause_threshold_seconds: float = 1.5


settings = Settings()
