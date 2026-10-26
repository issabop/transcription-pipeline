"""Stage 4: turn the raw model output into a clean transcript that's ready for downstream use."""

from itertools import pairwise

from app.models import Pause, Segment, Stats, Transcript, Word
from app.pipeline.transcribe import RawTranscript


def build_transcript(raw: RawTranscript, pause_threshold: float) -> Transcript:
    segments = clean_segments(raw)
    return Transcript(
        language=raw.language,
        language_probability=round(raw.language_probability, 3),
        duration=round(raw.duration, 2),
        text=" ".join(s.text for s in segments),
        segments=segments,
        pauses=find_pauses(segments, pause_threshold),
        stats=compute_stats(segments, raw.duration),
    )


def clean_segments(raw: RawTranscript) -> list[Segment]:
    """Collapse stray whitespace, drop segments with no text and number the rest."""
    segments = []
    for s in raw.segments:
        text = " ".join(s.text.split())
        if not text:
            continue
        words = [Word(start=round(w.start, 2), end=round(w.end, 2), text=w.text.strip()) for w in s.words if w.text.strip()]
        segments.append(Segment(id=len(segments), start=round(s.start, 2), end=round(s.end, 2), text=text, words=words))
    return segments


def find_pauses(segments: list[Segment], threshold: float) -> list[Pause]:
    """A silence between two words that lasts at least `threshold` seconds, inside a segment or between two."""
    words = [w for s in segments for w in s.words]
    return [
        Pause(start=a.end, end=b.start, duration=round(b.start - a.end, 2))
        for a, b in pairwise(words)
        if b.start - a.end >= threshold
    ]


def compute_stats(segments: list[Segment], duration: float) -> Stats:
    words = sum(len(s.text.split()) for s in segments)
    speech = sum(s.end - s.start for s in segments)
    return Stats(
        word_count=words,
        speech_seconds=round(speech, 2),
        speech_ratio=round(speech / duration, 3) if duration else 0.0,
        words_per_minute=round(words / (speech / 60), 1) if speech else 0.0,
    )
