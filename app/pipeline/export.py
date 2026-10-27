"""Stage 5: render a finished transcript as subtitles (SRT, WebVTT) or plain text."""

from app.models import Transcript


def to_srt(transcript: Transcript) -> str:
    blocks = [
        f"{i}\n{timestamp(s.start, ',')} --> {timestamp(s.end, ',')}\n{s.text}\n"
        for i, s in enumerate(transcript.segments, start=1)
    ]
    return "\n".join(blocks)


def to_vtt(transcript: Transcript) -> str:
    blocks = [f"{timestamp(s.start, '.')} --> {timestamp(s.end, '.')}\n{s.text}\n" for s in transcript.segments]
    return "WEBVTT\n\n" + "\n".join(blocks)


def to_txt(transcript: Transcript, include_pauses: bool = False) -> str:
    """One segment per line, optionally with `[pause 2.1s]` markers placed where each pause was.

    A pause inside a segment goes inline, between the two words. One between segments gets its own line.
    """
    if not include_pauses:
        return "".join(f"{s.text}\n" for s in transcript.segments)
    pause_after = {p.start: p for p in transcript.pauses}  # keyed by the end time of the word before it
    lines = []
    for s in transcript.segments:
        # Insert markers into the segment's own text rather than re-joining the words, because Whisper
        # splits some words into pieces ("take" + "-home") and joining them with spaces would break them.
        text, cursor, trailing = s.text, 0, None
        for i, w in enumerate(s.words):
            found = text.find(w.text, cursor)
            if found == -1:
                continue
            cursor = found + len(w.text)
            if pause := pause_after.get(w.end):
                if i == len(s.words) - 1:
                    trailing = pause
                else:
                    marker = f" {_marker(pause)}"
                    text = text[:cursor] + marker + text[cursor:]
                    cursor += len(marker)
        lines.append(text)
        if trailing:
            lines.append(_marker(trailing))
    return "\n".join(lines) + "\n"


def _marker(pause) -> str:
    return f"[pause {pause.duration:.1f}s]"


def timestamp(seconds: float, separator: str) -> str:
    """12.5 -> '00:00:12,500' (SRT uses a comma before the milliseconds, WebVTT a dot)."""
    total_ms = round(seconds * 1000)
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}{separator}{ms:03}"
