"""Timeline/timing math for assemble.py -- pure functions, no ffmpeg involved."""
import pytest

import assemble as asm


def seq(*shots):
    """shots: list of (id, duration, cut) tuples, cut in ('hard', 'crossfade')."""
    return [{"id": i, "duration": d, "cut": c} for i, d, c in shots]


# --- compute_timeline ---------------------------------------------------------

def test_empty_sequence():
    assert asm.compute_timeline([], 0.5) == []


def test_single_shot():
    timeline = asm.compute_timeline(seq(("a", 6.0, "crossfade")), 0.5)
    assert len(timeline) == 1
    assert timeline[0] == {"id": "a", "start": 0.0, "end": 6.0, "transition": "none", "crossfade_used": 0.0}


def test_all_hard_cuts_just_add_durations():
    timeline = asm.compute_timeline(
        seq(("a", 5.0, "hard"), ("b", 7.0, "hard"), ("c", 4.0, "hard")), 0.5
    )
    assert [t["start"] for t in timeline] == [0.0, 5.0, 12.0]
    assert [t["end"] for t in timeline] == [5.0, 12.0, 16.0]
    assert all(t["crossfade_used"] == 0.0 for t in timeline)
    assert timeline[-1]["end"] == pytest.approx(16.0)


def test_all_crossfades_overlap_by_crossfade_seconds():
    cf = 0.5
    timeline = asm.compute_timeline(
        seq(("a", 5.0, "crossfade"), ("b", 7.0, "crossfade"), ("c", 4.0, "crossfade")), cf
    )
    # shot b starts cf seconds before a ends
    assert timeline[1]["start"] == pytest.approx(5.0 - cf)
    assert timeline[1]["end"] == pytest.approx(5.0 - cf + 7.0)
    # shot c starts cf seconds before b ends
    assert timeline[2]["start"] == pytest.approx(timeline[1]["end"] - cf)
    assert timeline[2]["end"] == pytest.approx(timeline[2]["start"] + 4.0)
    # total runtime is less than the sum of durations by 2 * cf (two overlaps)
    total_naive = 5.0 + 7.0 + 4.0
    assert timeline[-1]["end"] == pytest.approx(total_naive - 2 * cf)


def test_mixed_hard_and_crossfade():
    cf = 0.5
    timeline = asm.compute_timeline(
        seq(("a", 5.0, "crossfade"), ("b", 7.0, "hard"), ("c", 4.0, "crossfade")), cf
    )
    assert timeline[0]["start"] == 0.0
    assert timeline[0]["end"] == 5.0
    # hard cut: b starts exactly where a ends, no overlap
    assert timeline[1]["start"] == pytest.approx(5.0)
    assert timeline[1]["end"] == pytest.approx(12.0)
    assert timeline[1]["crossfade_used"] == 0.0
    # crossfade: c overlaps the tail of b
    assert timeline[2]["start"] == pytest.approx(12.0 - cf)
    assert timeline[2]["crossfade_used"] == pytest.approx(cf)


def test_crossfade_clamped_to_shorter_neighbor():
    # configured crossfade (3s) exceeds the 2s slate duration -- must clamp
    # to the shorter of the two neighboring durations, never go negative
    timeline = asm.compute_timeline(
        seq(("a", 6.0, "crossfade"), ("slate", 2.0, "crossfade")), 3.0
    )
    assert timeline[1]["crossfade_used"] == pytest.approx(2.0)
    assert timeline[1]["start"] == pytest.approx(6.0 - 2.0)


def test_first_shot_transition_ignored_even_if_marked_hard():
    # cut on the first shot is meaningless (nothing precedes it) -- must not error
    timeline = asm.compute_timeline(seq(("a", 6.0, "hard")), 0.5)
    assert timeline[0]["transition"] == "none"
    assert timeline[0]["start"] == 0.0


# --- resolve_music_segments ---------------------------------------------------

def _seq_with_cues(cues_by_id):
    return [{"id": i, "music_cue": cues_by_id.get(i)} for i in ["a", "b", "c", "d"]]


def test_no_cues_returns_empty():
    sequence = _seq_with_cues({})
    timeline = asm.compute_timeline(
        [{"id": s["id"], "duration": 5.0, "cut": "crossfade"} for s in sequence], 0.5
    )
    assert asm.resolve_music_segments(sequence, timeline) == []


def test_single_cue_runs_to_end_of_film():
    sequence = _seq_with_cues({"b": "theme.mp3"})
    timeline = asm.compute_timeline(
        [{"id": s["id"], "duration": 5.0, "cut": "hard"} for s in sequence], 0.5
    )
    segments = asm.resolve_music_segments(sequence, timeline)
    assert len(segments) == 1
    assert segments[0]["file"] == "theme.mp3"
    assert segments[0]["start"] == timeline[1]["start"]
    assert segments[0]["end"] == timeline[-1]["end"]


def test_second_cue_ends_the_first_segment():
    sequence = _seq_with_cues({"a": "intro.mp3", "c": "outro.mp3"})
    timeline = asm.compute_timeline(
        [{"id": s["id"], "duration": 5.0, "cut": "hard"} for s in sequence], 0.5
    )
    segments = asm.resolve_music_segments(sequence, timeline)
    assert len(segments) == 2
    assert segments[0] == {"file": "intro.mp3", "start": timeline[0]["start"], "end": timeline[2]["start"]}
    assert segments[1] == {"file": "outro.mp3", "start": timeline[2]["start"], "end": timeline[-1]["end"]}


# --- format_timecode -----------------------------------------------------------

def test_format_timecode_basic():
    assert asm.format_timecode(0.0) == "00:00:00.000"
    assert asm.format_timecode(65.25) == "00:01:05.250"
    assert asm.format_timecode(3661.5) == "01:01:01.500"


def test_format_timecode_never_negative():
    assert asm.format_timecode(-1.0) == "00:00:00.000"
