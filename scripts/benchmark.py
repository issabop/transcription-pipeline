"""Compare Whisper model sizes on this machine: load time, speed, and (with a reference) word error rate.

    python -m scripts.benchmark samples/jfk.flac --models base small large-v3-turbo \
        --reference "And so my fellow Americans, ask not what your country can do for you, ask what you can do for your country."
"""

import argparse
import re
import tempfile
import time
from pathlib import Path

from app.config import settings
from app.pipeline.audio import to_wav
from app.pipeline.ingest import probe
from app.pipeline.transcribe import WhisperTranscriber


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Word-level edit distance (substitutions + insertions + deletions) divided by the reference length."""
    ref, hyp = normalise(reference), normalise(hypothesis)
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        current = [i]
        for j, h in enumerate(hyp, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (r != h)))
        previous = current
    return previous[-1] / len(ref)


def normalise(text: str) -> list[str]:
    return re.sub(r"[^\w\s']", " ", text.lower()).split()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("audio", type=Path)
    parser.add_argument("--models", nargs="+", default=["base", "small", "large-v3-turbo"])
    parser.add_argument("--language", default=None, help="e.g. en or ur; auto-detect if not set")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--compute-type", default="auto")
    parser.add_argument("--reference", help="the correct transcript, to compute the word error rate")
    args = parser.parse_args()

    duration = probe(args.audio).duration
    with tempfile.TemporaryDirectory() as tmp:
        wav = to_wav(args.audio, Path(tmp) / "audio.wav")
        rows = []
        for model in args.models:
            started = time.perf_counter()
            engine = WhisperTranscriber(model, settings.model_dir, args.device, args.compute_type, settings.beam_size, 1)
            loaded = time.perf_counter()
            raw = engine.transcribe(wav, args.language, on_progress=lambda _: None)
            finished = time.perf_counter()
            text = " ".join(s.text.strip() for s in raw.segments)
            wer = f"{word_error_rate(args.reference, text):.1%}" if args.reference else "-"
            seconds = finished - loaded
            rows.append((model, f"{engine.device}/{engine.compute_type}", f"{loaded - started:.1f}s",
                         f"{seconds:.1f}s", f"{duration / seconds:.1f}x", raw.language, wer))
            print(f"\n[{model}] {text}\n", flush=True)

    print(f"Audio: {args.audio.name}, {duration:.1f}s\n")
    print("| Model | Device | Load | Transcribe | Speed (x real time) | Language | WER |")
    print("|---|---|---|---|---|---|---|")
    for row in rows:
        print("| " + " | ".join(row) + " |")


if __name__ == "__main__":
    main()
