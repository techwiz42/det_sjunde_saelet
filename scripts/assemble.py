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
# Video is folded ITERATIVELY, two clips at a time, each step's result
# written to (and the previous step's discarded from) a scratch temp
# directory -- not as one filter_complex spanning every shot at once. A
# single-graph attempt at the full 45-shot sequence was killed by the
# kernel OOM killer (exit code -9) on this machine: ffmpeg has to keep every
# branch of an N-input graph live simultaneously, which scales with film
# length. Folding pairwise caps peak memory at "two clips" regardless of
# how long the film gets, at the cost of some re-encoding overhead and
# ~2x ffmpeg invocations (one per shot to normalize it, one per join) --
# correctness and boundedness over raw speed, matching this project's own
# build-tooling precedent elsewhere.

FOLD_CRF = 12  # intermediate re-encodes use a higher-quality CRF than the
               # final deliverable's 18, since each fold is a re-encode
               # generation and these are scratch files anyway, not takes.


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

    cmd += ["-c:v", "libx264", "-crf", str(FOLD_CRF), "-pix_fmt", "yuv420p", "-r", str(fps), str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def fold_video_clips(clip_paths: list[Path], timeline: list[dict], fps: int, tmp_dir: Path) -> Path:
    """Iteratively fold normalized clips (parallel to timeline) pairwise
    into a single video-only file. Each step's offset/duration math reuses
    compute_timeline's own numbers directly (t['start'], t['crossfade_used'])
    rather than re-deriving them, since the running clip always starts at
    the same t=0 the global timeline does -- nothing is ever trimmed off
    its front, so global and local timeline coordinates stay identical."""
    current_path = clip_paths[0]
    for i in range(1, len(clip_paths)):
        t = timeline[i]
        out_path = tmp_dir / f"running_{i:03d}.mp4"
        if t["transition"] == "hard":
            filter_complex = f"[0:v][1:v]concat=n=2:v=1:a=0,settb=1/{fps}[out]"
        else:
            filter_complex = (
                f"[0:v][1:v]xfade=transition={XFADE_TRANSITION}:"
                f"duration={t['crossfade_used']:.3f}:offset={t['start']:.3f},settb=1/{fps}[out]"
            )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(current_path), "-i", str(clip_paths[i]),
            "-filter_complex", filter_complex, "-map", "[out]",
            "-c:v", "libx264", "-crf", str(FOLD_CRF), "-pix_fmt", "yuv420p", "-r", str(fps),
            str(out_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        current_path = out_path
    return current_path


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

    try:
        with tempfile.TemporaryDirectory(prefix="assemble_") as tmp:
            tmp_dir = Path(tmp)

            print(f"normalizing {len(sequence)} shot(s)...")
            clip_paths = [render_normalized_clip(shot, config, tmp_dir, i) for i, shot in enumerate(sequence)]

            print(f"folding {len(clip_paths)} clip(s) (pairwise, bounded memory)...")
            folded_video = fold_video_clips(clip_paths, timeline, fps, tmp_dir)

            audio_input_args, audio_filters, audio_label = build_audio_graph(
                sequence, timeline, music_segments, config, audio_input_start=1, total_duration=total_duration
            )
            filter_complex = ";".join(audio_filters)

            cmd = (
                ["ffmpeg", "-y", "-i", str(folded_video)]
                + audio_input_args
                + ["-filter_complex", filter_complex]
                + ["-map", "0:v", "-map", f"[{audio_label}]"]
                + ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(fps)]
                + ["-c:a", "aac", "-b:a", "192k"]
                + ["-t", f"{total_duration:.3f}"]
                + [str(out_path)]
            )
            print("muxing final audio...")
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
