"""Stages 4 and 5: clean-up, pauses, stats and the export formats."""

from app.pipeline.export import timestamp, to_srt, to_txt, to_vtt
from app.pipeline.process import build_transcript
from app.pipeline.transcribe import RawTranscript
from tests.conftest import FAKE_SEGMENTS


def transcript():
    return build_transcript(RawTranscript("en", 0.98765, 10.0, FAKE_SEGMENTS), pause_threshold=1.5)


def test_cleans_whitespace_and_drops_empty_segments():
    t = transcript()
    assert [s.text for s in t.segments] == ["Hello there.", "This is a test.", "After a pause."]
    assert [s.id for s in t.segments] == [0, 1, 2]
    assert t.text == "Hello there. This is a test. After a pause."
    assert t.language_probability == 0.988


def test_keeps_cleaned_word_timestamps():
    words = transcript().segments[0].words
    assert [(w.start, w.end, w.text) for w in words] == [(0.0, 0.8, "Hello"), (0.9, 2.0, "there.")]


def test_finds_pauses_between_and_inside_segments():
    t = transcript()
    # between segments (the empty one was dropped), inside "a ... test.", and exactly at the 1.5 s threshold
    assert [(p.start, p.end, p.duration) for p in t.pauses] == [(2.0, 4.0, 2.0), (4.8, 6.4, 1.6), (7.0, 8.5, 1.5)]


def test_stats():
    s = transcript().stats
    assert s.word_count == 9
    assert s.speech_seconds == 6.5
    assert s.speech_ratio == 0.65
    assert s.words_per_minute == round(9 / (6.5 / 60), 1)


def test_timestamp_formats():
    assert timestamp(3725.5, ",") == "01:02:05,500"
    assert timestamp(0.0005, ".") == "00:00:00.000"
    assert timestamp(59.9996, ".") == "00:01:00.000"


def test_srt():
    assert to_srt(transcript()).startswith("1\n00:00:00,000 --> 00:00:02,000\nHello there.\n\n2\n")


def test_vtt():
    assert to_vtt(transcript()).startswith("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello there.\n")


def test_txt_with_and_without_pauses():
    t = transcript()
    assert to_txt(t) == "Hello there.\nThis is a test.\nAfter a pause.\n"
    assert to_txt(t, include_pauses=True) == (
        "Hello there.\n[pause 2.0s]\nThis is a [pause 1.6s] test.\n[pause 1.5s]\nAfter a pause.\n"
    )
