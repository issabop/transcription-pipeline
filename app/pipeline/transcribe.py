"""Stage 3: speech to text. The rest of the app only knows the `Transcriber` interface."""

import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import ctranslate2
import numpy as np
from faster_whisper import WhisperModel


@dataclass(frozen=True)
class RawWord:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class RawSegment:
    start: float
    end: float
    text: str
    words: list[RawWord]


@dataclass(frozen=True)
class RawTranscript:
    """Exactly what the engine produced, before any clean-up."""

    language: str
    language_probability: float
    duration: float
    segments: list[RawSegment]


ProgressCallback = Callable[[float], None]


class Transcriber(Protocol):
    """Anything that can turn a 16 kHz mono WAV into timed segments."""

    supported_languages: list[str]

    def transcribe(self, audio: Path, language: str | None, on_progress: ProgressCallback) -> RawTranscript: ...


class WhisperTranscriber:
    """faster-whisper (Whisper running on CTranslate2). The model is loaded once and reused."""

    def __init__(self, model_size: str, model_dir: Path, device: str, compute_type: str, beam_size: int, workers: int):
        self.device, self.compute_type = resolve_device(device, compute_type)
        self.model_size = model_size
        self.beam_size = beam_size
        self.model = WhisperModel(
            model_size,
            device=self.device,
            compute_type=self.compute_type,
            download_root=str(model_dir),
            num_workers=workers,  # lets that many jobs use the model at the same time
        )
        self.supported_languages = self.model.supported_languages

    def transcribe(self, audio: Path, language: str | None, on_progress: ProgressCallback) -> RawTranscript:
        # vad_filter skips silence: faster, and stops Whisper inventing text over quiet parts.
        # word_timestamps costs ~5-10% more time and gives exact pause positions, even mid-segment.
        # Long audio is handled inside the model in 30 s windows, with timestamps kept absolute.
        segments, info = self.model.transcribe(
            read_wav(audio), language=language, beam_size=self.beam_size, vad_filter=True, word_timestamps=True
        )
        result = []
        for segment in segments:  # a generator: each segment is decoded as we ask for it
            words = [RawWord(w.start, w.end, w.word) for w in segment.words or []]
            result.append(RawSegment(segment.start, segment.end, segment.text, words))
            if info.duration:
                on_progress(min(segment.end / info.duration, 1.0))
        return RawTranscript(info.language, info.language_probability, info.duration, result)


def read_wav(path: Path) -> np.ndarray:
    """Load the 16 kHz mono 16-bit WAV from stage 2 as the float samples Whisper takes.

    ffmpeg has already decoded the file, so there's no need for faster-whisper to decode it again.
    """
    with wave.open(str(path), "rb") as wav:
        frames = wav.readframes(wav.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def resolve_device(device: str, compute_type: str) -> tuple[str, str]:
    """Use the GPU when there is one, otherwise the CPU, and the fastest precision for each."""
    if device == "auto":
        device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"
    return device, compute_type
