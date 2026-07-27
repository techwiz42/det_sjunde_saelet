#!/usr/bin/env python3
"""Assemble the rough cut: concat selected takes in publish_order.

For every shot in shots.yaml, ascending publish_order:
  - if the shot is `ready`, its latest rendered take is used
  - otherwise, a 2-second slate card (shot id on black) stands in, and the
    build continues regardless of how many shots aren't ready

Shots crossfade by config.yaml's crossfade_frames by default; a shot with
`cut: hard` in shots.yaml is joined to the previous shot with a hard cut
instead. Narration is placed at each shot's actual position in the
timeline; a music_cue starts that track playing from its shot onward, until
another cue or the end of the film. Never overwrites -- each run writes the
next build/<prefix>_v<N>.mp4, plus a plain-text EDL alongside it.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from render_shot import ffprobe_duration  # noqa: E402

SLATE_DURATION = 2.0
XFADE_TRANSITION = "fade"


# --- pure timeline math (unit tested in tests/test_assemble_timing.py) -------

def compute_timeline(sequence: list[dict], crossfade_seconds: float) -> list[dict]:
    """sequence: ordered list of {"id", "duration", "cut"} where cut is
    "hard" or "crossfade" (the transition INTO that shot; ignored for the
    first entry, which always starts the timeline at t=0 with no transition).

    Returns a parallel list of {"id", "start", "end", "transition",
    "crossfade_used"} -- crossfade_used is the actual overlap applied (0.0
    for hard cuts and for the first shot), clamped so it never exceeds
    either neighboring shot's own duration.
    """
    if not sequence:
        return []

    timeline = []
    prev_end = 0.0
    prev_duration = None
    for i, shot in enumerate(sequence):
        duration = shot["duration"]
        if i == 0:
            start = 0.0
            transition = "none"
            cf_used = 0.0
        else:
            transition = shot["cut"]
            if transition == "hard":
                start = prev_end
                cf_used = 0.0
            else:
                cf_used = min(crossfade_seconds, prev_duration, duration)
                start = prev_end - cf_used
        end = start + duration
        timeline.append({
            "id": shot["id"], "start": start, "end": end,
            "transition": transition, "crossfade_used": cf_used,
        })
        prev_end = end
        prev_duration = duration
    return timeline


def resolve_music_segments(sequence: list[dict], timeline: list[dict]) -> list[dict]:
    """Returns [{"file", "start", "end"}, ...] -- each music_cue starts a
    segment at its shot's timeline start; a segment ends where the next cue
    starts, or at the end of the whole timeline if there is no next cue.
    """
    cues = [
        (timeline[i]["start"], shot["music_cue"])
        for i, shot in enumerate(sequence)
        if shot.get("music_cue")
    ]
    if not cues:
        return []
    film_end = timeline[-1]["end"]
    segments = []
    for i, (start, file) in enumerate(cues):
        end = cues[i + 1][0] if i + 1 < len(cues) else film_end
        segments.append({"file": file, "start": start, "end": end})
    return segments


# --- gathering real data ------------------------------------------------------

def build_sequence(config: dict, shots_doc: dict) -> list[dict]:
    select_data = common.load_select(config)
    raw_by_shot = common.all_raw_files(config)
    music_dir = common.path_for(config, "music_dir")

    sequence = []
    for shot in sorted(shots_doc["shots"], key=lambda s: s["publish_order"]):
        state, notes = common.compute_state(config, shot, select_data, raw_by_shot)
        ready = state == "ready"

        take_path = common.latest_take(config, shot["id"]) if ready else None
        if ready and take_path is None:
            # selected + narrated but never rendered -- not an error, just not buildable yet
            ready = False
            notes = notes + ["no rendered take yet"]

        duration = ffprobe_duration(take_path) if take_path else SLATE_DURATION

        narration_path = common.narration_path(config, shot["id"])
        narration = narration_path if (ready and narration_path.exists()) else None

        music_cue = shot.get("music_cue")
        if music_cue and not (music_dir / music_cue).exists():
            print(f"[{shot['id']}] warning: music_cue {music_cue!r} not found in {music_dir.relative_to(common.ROOT)}/ -- ignoring this cue")
            music_cue = None

        sequence.append({
            "id": shot["id"],
            "duration": duration,
            "cut": "hard" if shot.get("cut") == "hard" else "crossfade",
            "ready": ready,
            "state": state,
            "notes": notes,
            "take_path": take_path,
            "narration_path": narration,
            "music_cue": music_cue,
        })
    return sequence


# --- ffmpeg command construction ---------------------------------------------
#
# Video is assembled from small, individually-bounded pieces -- never by
# re-encoding a growing "running cut so far". An earlier version folded
# clips ITERATIVELY, feeding the accumulated running clip plus the next shot
# into ffmpeg at every step; that was itself a fix for a first attempt
# (single filter_complex spanning all N shots) that the kernel OOM killer
# reliably killed. But the iterative fold has the same defect one level
# down: each step still fully decodes and re-encodes the ENTIRE
# accumulated-so-far clip, so total work (and peak resident set, on a
# memory-constrained host) grows with film length, not with "two clips" as
# originally assumed -- confirmed by two real 45-shot runs dying at the same
# ~70-80s mark regardless of which fold step that landed on. See design.md
# decision #6 for the full history.
#
# This version only ever re-encodes ONE SHOT'S OWN DURATION at a time:
#   - each shot's non-overlapping middle ("solo") segment is trimmed from its
#     already-normalized clip -- a few seconds of work, independent of the
#     shot's position in the film
#   - each crossfade is rendered as its own tiny standalone clip (just the
#     `crossfade_frames`-long overlap window, blended via xfade with
#     offset=0), not as a fold over the running cut
#   - the ordered solo/transition segments are joined with ffmpeg's concat
#     demuxer using `-c copy` -- a stream copy, not a re-encode, so this step
#     is fast and its memory use doesn't depend on total duration either
# Total per-invocation work is now bounded by max(shot duration,
# crossfade_frames) regardless of how long the film gets, and the number of
# ffmpeg invocations is O(N) instead of O(N) fold-steps-each-processing-
# O(N)-of-footage.
#
# The final audio/video mux used to reintroduce exactly the bug this whole
# design exists to avoid: it re-encoded the WHOLE concatenated film in one
# libx264 pass (to go from a high-bitrate FINAL_CRF-quality intermediate down
# to a smaller deliverable bitrate), i.e. one ffmpeg invocation whose cost
# again scaled with total film length -- confirmed as the actual OOM site
# (returncode -9) on a 6+ minute cut. Fix: every intermediate segment is now
# encoded directly at the SAME crf as the deliverable (FINAL_CRF), so the
# final step has no bitrate reduction left to do and can just stream-copy
# both video and audio -- no decode, no encode, memory footprint independent
# of film length. Trade-off: solo/transition clips that pass through more
# than one re-encode generation (e.g. a shot trimmed AND crossfaded) lose a
# little more quality than the old two-tier scheme would have cost them; for
# a rough-cut tool that's the right side of the trade against reliably
# dying on this host.

FINAL_CRF = 18  # every re-encode in the pipeline -- intermediate (solo
                # trims, transition clips) and deliverable alike -- uses this
                # same crf, so the final mux never has to re-encode to
                # change quality/bitrate and can be a pure stream copy.

# libx264's default preset builds a lookahead buffer (rc-lookahead, default
# ~40 frames) sized for rate-control quality, not memory economy. `ultrafast`
# + a single thread + a small explicit lookahead trades some encode
# efficiency (irrelevant for scratch intermediates that are each only a few
# seconds long) for a smaller, more predictable memory footprint on this
# project's often memory-constrained shared host.
LOW_MEM_ENCODE_ARGS = ["-preset", "ultrafast", "-threads", "1", "-x264-params", "rc-lookahead=10"]


def build_slate_input_args(fps: int, w: int, h: int) -> list[str]:
    return ["-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:d={SLATE_DURATION}:r={fps}"]


def render_normalized_clip(shot: dict, config: dict, tmp_dir: Path, idx: int) -> Path:
    """Normalize one sequence entry (take or slate) to the project's
    fps/resolution/format/timebase, video-only, written to tmp_dir."""
    fps = config["fps"]
    W, H = config["resolution"]["width"], config["resolution"]["height"]
    out_path = tmp_dir / f"clip_{idx:03d}.mp4"

    if shot["ready"]:
        vf = f"fps={fps},scale={W}:{H},format=yuv420p,setsar=1,settb=1/{fps}"
        cmd = ["ffmpeg", "-y", "-i", str(shot["take_path"]), "-vf", vf, "-an"]
    else:
        safe_text = shot["id"].replace("'", "’").replace(":", "\\:")
        vf = (
            f"format=yuv420p,setsar=1,settb=1/{fps},"
            f"drawtext=text='{safe_text}':fontcolor=white:fontsize=40:"
            f"x=(w-text_w)/2:y=(h-text_h)/2"
        )
        cmd = ["ffmpeg", "-y"] + build_slate_input_args(fps, W, H) + ["-vf", vf, "-an"]

    cmd += ["-c:v", "libx264", "-crf", str(FINAL_CRF)] + LOW_MEM_ENCODE_ARGS + ["-pix_fmt", "yuv420p", "-r", str(fps), str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def trim_solo_segment(clip_path: Path, start: float, end: float, fps: int, tmp_dir: Path, idx: int) -> Path:
    """Re-encode just [start, end) of one shot's own normalized clip -- work
    bounded by that one shot's duration, never the accumulated film."""
    out_path = tmp_dir / f"solo_{idx:03d}.mp4"
    vf = f"trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,settb=1/{fps}"
    cmd = [
        "ffmpeg", "-y", "-i", str(clip_path), "-vf", vf, "-an",
        "-c:v", "libx264", "-crf", str(FINAL_CRF),
    ] + LOW_MEM_ENCODE_ARGS + ["-pix_fmt", "yuv420p", "-r", str(fps), str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def build_transition_clip(prev_clip: Path, prev_duration: float, next_clip: Path, cf: float, fps: int, tmp_dir: Path, idx: int) -> Path:
    """Render just the crossfade overlap window (cf seconds) between two
    neighboring shots' own clips as its own tiny standalone clip -- not a
    fold over the running cut. Trims each side to exactly the overlap
    window first, so xfade can blend them at offset=0."""
    out_path = tmp_dir / f"trans_{idx:03d}.mp4"
    prev_start = max(0.0, prev_duration - cf)
    filter_complex = (
        f"[0:v]trim=start={prev_start:.3f}:end={prev_duration:.3f},setpts=PTS-STARTPTS,fps={fps}[pa];"
        f"[1:v]trim=start=0:end={cf:.3f},setpts=PTS-STARTPTS,fps={fps}[pb];"
        f"[pa][pb]xfade=transition={XFADE_TRANSITION}:duration={cf:.3f}:offset=0,settb=1/{fps}[out]"
    )
    cmd = [
        "ffmpeg", "-y", "-i", str(prev_clip), "-i", str(next_clip),
        "-filter_complex", filter_complex, "-map", "[out]",
        "-c:v", "libx264", "-crf", str(FINAL_CRF),
    ] + LOW_MEM_ENCODE_ARGS + ["-pix_fmt", "yuv420p", "-r", str(fps), str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def build_video_segments(sequence: list[dict], clip_paths: list[Path], timeline: list[dict], fps: int, tmp_dir: Path) -> list[Path]:
    """Build the ordered list of small video-only segments (each shot's
    non-overlapping 'solo' body, plus a tiny standalone clip per crossfade)
    whose concatenation reproduces compute_timeline's full timeline exactly
    -- without ever re-encoding more than one shot's worth of footage at a
    time, regardless of film length."""
    n = len(clip_paths)
    segments = []
    for i in range(n):
        duration = sequence[i]["duration"]
        head_trim = timeline[i]["crossfade_used"]
        tail_trim = timeline[i + 1]["crossfade_used"] if i + 1 < n else 0.0

        if i > 0 and timeline[i]["transition"] == "crossfade":
            segments.append(build_transition_clip(
                clip_paths[i - 1], sequence[i - 1]["duration"], clip_paths[i],
                timeline[i]["crossfade_used"], fps, tmp_dir, i,
            ))

        solo_start = head_trim
        solo_end = max(solo_start, duration - tail_trim)
        solo_duration = solo_end - solo_start
        if solo_duration <= 0.02:
            print(
                f"warning: [{sequence[i]['id']}] fully consumed by adjacent crossfades "
                f"({head_trim:.2f}s + {tail_trim:.2f}s >= {duration:.2f}s duration) -- "
                f"no solo segment emitted",
                file=sys.stderr,
            )
            continue

        if head_trim == 0.0 and tail_trim == 0.0:
            segments.append(clip_paths[i])  # whole clip already is the solo segment
        else:
            segments.append(trim_solo_segment(clip_paths[i], solo_start, solo_end, fps, tmp_dir, i))
    return segments


def concat_segments(segments: list[Path], tmp_dir: Path) -> Path:
    """Join pre-normalized video-only segments with the concat demuxer and
    `-c copy` -- a stream copy, not a re-encode, since every segment already
    shares identical codec/format/timebase (render_normalized_clip /
    trim_solo_segment / build_transition_clip all encode to the same spec).
    Memory use here doesn't depend on total duration either."""
    list_path = tmp_dir / "concat_list.txt"
    list_path.write_text("".join(f"file '{seg.resolve()}'\n" for seg in segments), encoding="utf-8")
    out_path = tmp_dir / "folded.mp4"
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def mix_audio_track(sequence: list[dict], timeline: list[dict], music_segments: list[dict], config: dict, total_duration: float, tmp_dir: Path) -> Path:
    """Mix narration + music into a single standalone audio file, in its own
    ffmpeg process with no video involved. The final mux step used to build
    this same amix filter graph (up to one input per shot's narration) in
    the SAME ffmpeg invocation that was also decoding and re-encoding the
    whole film's video -- on this host's tight, shared memory budget that
    combination is what got the process OOM-killed. Isolating the many-input
    audio mix here means it runs, finishes, and frees its memory before the
    video re-encode ever starts."""
    input_args, filters, label = build_audio_graph(
        sequence, timeline, music_segments, config, audio_input_start=0, total_duration=total_duration
    )
    out_path = tmp_dir / "audio_mix.m4a"
    cmd = (
        ["ffmpeg", "-y"] + input_args
        + ["-filter_complex", ";".join(filters)]
        + ["-map", f"[{label}]"]
        + ["-c:a", "aac", "-b:a", "192k"]
        + ["-t", f"{total_duration:.3f}"]
        + [str(out_path)]
    )
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def build_audio_graph(sequence: list[dict], timeline: list[dict], music_segments: list[dict], config: dict, audio_input_start: int, total_duration: float) -> tuple[list[str], list[str], str]:
    input_args = ["-f", "lavfi", "-i", "anullsrc=channel_layout=mono:sample_rate=44100"]
    filters = [f"[{audio_input_start}:a]atrim=duration={total_duration:.3f}[abase]"]
    mix_labels = ["abase"]

    next_idx = audio_input_start + 1
    for i, shot in enumerate(sequence):
        if not shot["narration_path"]:
            continue
        input_args += ["-i", str(shot["narration_path"])]
        label = f"anarr{i}"
        delay_ms = round(max(0.0, timeline[i]["start"]) * 1000)
        filters.append(f"[{next_idx}:a]adelay=delays={delay_ms}:all=1[{label}]")
        mix_labels.append(label)
        next_idx += 1

    music_dir = common.path_for(config, "music_dir")
    for i, seg in enumerate(music_segments):
        input_args += ["-stream_loop", "-1", "-i", str(music_dir / seg["file"])]
        label = f"amus{i}"
        seg_duration = max(0.01, seg["end"] - seg["start"])
        delay_ms = round(seg["start"] * 1000)
        filters.append(f"[{next_idx}:a]atrim=0:{seg_duration:.3f},adelay=delays={delay_ms}:all=1[{label}]")
        mix_labels.append(label)
        next_idx += 1

    inputs_ref = "".join(f"[{lbl}]" for lbl in mix_labels)
    filters.append(f"{inputs_ref}amix=inputs={len(mix_labels)}:duration=longest:normalize=0[aout]")
    return input_args, filters, "aout"


# --- EDL ----------------------------------------------------------------------

def format_timecode(seconds: float) -> str:
    seconds = max(0.0, seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(int(m), 60)
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def build_edl(sequence: list[dict], timeline: list[dict]) -> str:
    lines = ["shot_id\tin\tout\ttransition_in\taudio"]
    for shot, t in zip(sequence, timeline):
        audio_bits = []
        if shot["narration_path"]:
            audio_bits.append(f"narration:{shot['narration_path'].name}")
        if shot["music_cue"]:
            audio_bits.append(f"music_cue:{shot['music_cue']}")
        if not shot["ready"]:
            audio_bits.append("slate")
        audio = ",".join(audio_bits) if audio_bits else "none"
        transition = t["transition"] if t["transition"] == "hard" else f"crossfade({t['crossfade_used']:.2f}s)"
        if t["transition"] == "none":
            transition = "none"
        lines.append(f"{shot['id']}\t{format_timecode(t['start'])}\t{format_timecode(t['end'])}\t{transition}\t{audio}")
    return "\n".join(lines) + "\n"


# --- main ----------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default="rough_cut", help="build output filename prefix (default: rough_cut)")
    args = parser.parse_args(argv)

    config = common.load_config()
    shots_doc = common.load_shots(config)

    sequence = build_sequence(config, shots_doc)
    if not sequence:
        print("error: shots.yaml has no shots", file=sys.stderr)
        return 1

    crossfade_seconds = config["crossfade_frames"] / config["fps"]
    timeline = compute_timeline(
        [{"id": s["id"], "duration": s["duration"], "cut": s["cut"]} for s in sequence],
        crossfade_seconds,
    )
    music_segments = resolve_music_segments(sequence, timeline)

    not_ready = [s["id"] for s in sequence if not s["ready"]]
    if not_ready:
        print(f"{len(not_ready)} shot(s) not ready -- using slate cards: {', '.join(not_ready)}")

    total_duration = timeline[-1]["end"]
    out_path = common.next_build_path(config, args.prefix)
    fps = config["fps"]

    # Scratch lives on-disk under build/, not in the default tempdir --
    # tempfile.TemporaryDirectory() with no `dir=` resolves to /tmp, which on
    # this host is a tmpfs mount (RAM, not disk). A build's intermediate
    # clips (normalized shots, solo trims, transition clips, the folded
    # video) would otherwise consume real memory for the whole run, on top
    # of whatever the concurrent ffmpeg processes themselves need -- exactly
    # the kind of avoidable pressure that turns a tight host into an OOM
    # kill. It also means a run that DOES get killed (SIGKILL skips
    # TemporaryDirectory's cleanup handler) leaves its leftovers on disk,
    # not in RAM, so they don't shrink headroom for the next attempt.
    scratch_root = common.path_for(config, "build_dir") / ".scratch"
    scratch_root.mkdir(exist_ok=True)
    leftovers = list(scratch_root.glob("assemble_*"))
    if leftovers:
        print(f"cleaning up {len(leftovers)} leftover scratch dir(s) from a previous interrupted run...")
        for d in leftovers:
            shutil.rmtree(d, ignore_errors=True)

    try:
        with tempfile.TemporaryDirectory(prefix="assemble_", dir=scratch_root) as tmp:
            tmp_dir = Path(tmp)

            print(f"normalizing {len(sequence)} shot(s)...")
            clip_paths = [render_normalized_clip(shot, config, tmp_dir, i) for i, shot in enumerate(sequence)]

            print(f"building {len(clip_paths)} shot segment(s) + transitions...")
            segments = build_video_segments(sequence, clip_paths, timeline, fps, tmp_dir)

            print(f"concatenating {len(segments)} segment(s) (stream copy)...")
            folded_video = concat_segments(segments, tmp_dir)

            print("mixing audio track (narration + music, video-free pass)...")
            audio_track = mix_audio_track(sequence, timeline, music_segments, config, total_duration, tmp_dir)

            cmd = (
                ["ffmpeg", "-y", "-i", str(folded_video), "-i", str(audio_track)]
                + ["-map", "0:v", "-map", "1:a"]
                + ["-c:v", "copy", "-c:a", "copy"]
                + ["-t", f"{total_duration:.3f}"]
                + [str(out_path)]
            )
            print("muxing final video + audio (stream copy, no re-encode)...")
            subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        debug_log = common.path_for(config, "build_dir") / "assemble_last_error.log"
        debug_log.write_text(
            f"command:\n{' '.join(str(c) for c in e.cmd)}\n\n"
            f"returncode: {e.returncode}\n\n"
            f"--- stderr ---\n{e.stderr.decode(errors='replace')}\n",
            encoding="utf-8",
        )
        print(
            f"ffmpeg failed (returncode {e.returncode}) -- full log written to "
            f"{debug_log.relative_to(common.ROOT)}",
            file=sys.stderr,
        )
        return 1

    edl_path = out_path.with_suffix(".edl.txt")
    edl_path.write_text(build_edl(sequence, timeline), encoding="utf-8")

    print(f"wrote {out_path.relative_to(common.ROOT)} ({total_duration:.1f}s)")
    print(f"wrote {edl_path.relative_to(common.ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
