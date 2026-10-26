"""Request and response shapes."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AudioInfo(BaseModel):
    """What ffprobe found inside an uploaded file."""

    duration: float = Field(description="Length in seconds")
    format: str = Field(description="Container format, e.g. 'mp3' or 'wav'")
    codec: str = Field(description="Audio codec, e.g. 'mp3' or 'pcm_s16le'")
    sample_rate: int
    channels: int


class Word(BaseModel):
    start: float
    end: float
    text: str


class Segment(BaseModel):
    id: int
    start: float = Field(description="Seconds from the start of the audio")
    end: float
    text: str
    words: list[Word]


class Pause(BaseModel):
    start: float
    end: float
    duration: float


class Stats(BaseModel):
    word_count: int
    speech_seconds: float = Field(description="Total time covered by speech segments")
    speech_ratio: float = Field(description="Speech time divided by audio length (0 to 1)")
    words_per_minute: float = Field(description="Words per minute of speech")


class Transcript(BaseModel):
    language: str
    language_probability: float
    duration: float
    text: str
    segments: list[Segment]
    pauses: list[Pause]
    stats: Stats


JobStatus = Literal["queued", "processing", "done", "failed"]


class Job(BaseModel):
    id: str
    status: JobStatus
    language: str | None = Field(description="Language requested, or null for auto-detect")
    progress: float = Field(description="0 to 1")
    attempts: int
    error: str | None
    audio: AudioInfo
    result: Transcript | None
    created_at: datetime
    updated_at: datetime
