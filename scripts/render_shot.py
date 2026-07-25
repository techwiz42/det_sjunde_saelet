#!/usr/bin/env python3
"""One selected still -> one graded Ken Burns clip in takes/.

Pipeline per shot: scale/crop the still to fill the frame, apply the Ken
Burns move (zoompan, eased in and out, never linear), downscale back to
target resolution, bake in the monochrome grade (desaturate + slight
contrast lift + gentle vignette), and write an untouched-forever
`<shot_id>_take<N>.mp4` -- never overwrites an existing take.

Effective clip duration is computed BEFORE rendering (not left for
assemble.py to fix up later, since assemble.py only concatenates
already-rendered clips): if the shot has narration and that narration's
audio runs longer than shots.yaml's stated duration, the clip is rendered
at narration_length + timing.min_padding_after_narration instead, and the
override is logged to stdout. render_shot.py's own clips carry no audio --
narration and music are mixed in by assemble.py.
"""
from __future__ import annotations

import argparse
import math
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

PAN_ZOOM_HEADROOM = 1.15  # fixed zoom-in "slack" that pan/tilt moves travel within


# --- pure timing/motion math (unit tested in tests/test_render_shot_timing.py) ---

def sine_ease(p: float) -> float:
    """Ease-in-out, 0..1 -> 0..1. Slow at both ends, matches a raised cosine."""
    return 0.5 - 0.5 * math.cos(math.pi * p)


def signed_sine_ease(p: float) -> float:
    """Ease-in-out, 0..1 -> -1..1. Same easing, symmetric around 0."""
    return -math.cos(math.pi * p)


def effective_duration(shot_duration: float, narration_duration: float | None, min_padding: float) -> tuple[float, bool]:
    """Returns (duration_to_render, overridden).

    If there's no narration, or it fits inside the shot's stated duration,
    the stated duration wins unchanged. Otherwise the clip is extended to
    narration_duration + min_padding and `overridden` is True.
    """
    if narration_duration is None:
        return shot_duration, False
    needed = narration_duration + min_padding
    if needed > shot_duration:
        return needed, True
    return shot_duration, False


def frame_count(duration: float, fps: int) -> int:
    return max(1, round(duration * fps))


# --- ffmpeg expression building ---------------------------------------------

def build_motion_exprs(motion_type: str, amount: float, d_frames: int) -> tuple[str, str, str]:
    """Returns (z_expr, x_expr, y_expr) for ffmpeg's zoompan filter.

    `on` is zoompan's output-frame counter (0-indexed); expressions are
    written as absolute functions of `on`, not recursive (`zoom+=...`)
    accumulation, so there's no drift and no dependency on frame order.
    """
    d_m1 = max(d_frames - 1, 1)
    p = f"(min(on,{d_m1})/{d_m1})"
    eased = f"(0.5-0.5*cos(PI*{p}))"        # sine_ease(p), 0..1
    signed_eased = f"(-cos(PI*{p}))"        # signed_sine_ease(p), -1..1

    if motion_type == "hold":
        raise ValueError("build_motion_exprs should not be called for hold shots")

    if motion_type == "push_in":
        z = f"1+{amount}*{eased}"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif motion_type == "push_out":
        z = f"(1+{amount})-{amount}*{eased}"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif motion_type in ("pan_left", "pan_right", "tilt_up"):
        z = f"{PAN_ZOOM_HEADROOM}"
        if motion_type == "pan_right":
            x = f"iw/2-(iw/zoom/2)+({amount}*iw/2)*{signed_eased}"
            y = "ih/2-(ih/zoom/2)"
        elif motion_type == "pan_left":
            x = f"iw/2-(iw/zoom/2)-({amount}*iw/2)*{signed_eased}"
            y = "ih/2-(ih/zoom/2)"
        else:  # tilt_up: window moves toward the top of the frame over time
            x = "iw/2-(iw/zoom/2)"
            y = f"ih/2-(ih/zoom/2)-({amount}*ih/2)*{signed_eased}"
    else:
        raise ValueError(f"unknown motion type {motion_type!r}")

    return z, x, y


FULL_VIGNETTE_ANGLE = "PI/5"  # ffmpeg vignette's own default -- a strong, well-tested vignette

def clamp_unit(x: float) -> float:
    return max(0.0, min(1.0, x))


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


# --- rendering ---------------------------------------------------------------

def render_one(config: dict, shot: dict, still_path: Path) -> Path:
    fps = config["fps"]
    W, H = config["resolution"]["width"], config["resolution"]["height"]
    min_padding = config["timing"]["min_padding_after_narration"]
    supersample = config["motion"]["supersample"]

    narration_wav = common.narration_path(config, shot["id"])
    narration_duration = ffprobe_duration(narration_wav) if narration_wav.exists() else None

    duration, overridden = effective_duration(shot["duration"], narration_duration, min_padding)
    if overridden:
        print(
            f"[{shot['id']}] narration ({narration_duration:.2f}s) exceeds shots.yaml "
            f"duration ({shot['duration']:.2f}s) -- extending render to {duration:.2f}s"
        )

    d_frames = frame_count(duration, fps)
    motion_type = shot["motion"]["type"]
    amount = shot["motion"]["amount"]

    grade = config["grade"]
    eq = f"eq=saturation={grade['saturation']}:contrast={grade['contrast']}:brightness={grade['brightness']}:gamma={grade['gamma']}"
    main_filters = []
    if motion_type == "hold":
        main_filters.append(f"scale={W}:{H}:force_original_aspect_ratio=increase")
        main_filters.append(f"crop={W}:{H}")
    else:
        ss_w, ss_h = W * supersample, H * supersample
        z, x, y = build_motion_exprs(motion_type, amount, d_frames)
        main_filters.append(f"scale={ss_w}:{ss_h}:force_original_aspect_ratio=increase")
        main_filters.append(f"crop={ss_w}:{ss_h}")
        main_filters.append(f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={ss_w}x{ss_h}:fps={fps}")
        main_filters.append(f"scale={W}:{H}")
    main_filters.append(eq)
    chain = ",".join(main_filters)

    # ffmpeg's `vignette` filter has no linear strength control -- angle is a
    # narrow, nonlinear knob and even its gentlest useful setting reads as a
    # heavy hooded vignette. Instead: vignette a copy at a fixed, well-tested
    # angle, then blend it against the un-vignetted frame at config's 0..1
    # vignette_strength, which is an honest linear "how much" control.
    vignette_strength = clamp_unit(grade["vignette_strength"]) if grade["vignette"] else 0.0
    if vignette_strength > 0.0:
        filter_complex = (
            f"[0:v]{chain},format=yuv420p[graded];"
            f"[graded]split=2[base][tovig];"
            f"[tovig]vignette=angle={FULL_VIGNETTE_ANGLE}:mode=forward[vig];"
            f"[base][vig]blend=all_mode=normal:all_opacity={vignette_strength:.3f}:shortest=1[out]"
        )
    else:
        filter_complex = f"[0:v]{chain},format=yuv420p[out]"

    out_path = common.next_take_path(config, shot["id"])
    common.ensure_parent(out_path)

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-framerate", str(fps), "-i", str(still_path),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-frames:v", str(d_frames),
        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(fps),
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all-ready", action="store_true", help="render every shot in state 'ready' that has no take yet")
    parser.add_argument("--shot", help="render a single shot by id (must have a selected still)")
    parser.add_argument("--redo", action="store_true", help="render even if a take already exists (still never overwrites -- adds the next _takeN)")
    args = parser.parse_args(argv)

    if not args.all_ready and not args.shot:
        parser.error("pass --all-ready or --shot <id>")

    config = common.load_config()
    shots_doc = common.load_shots(config)
    select_data = common.load_select(config)
    raw_by_shot = common.all_raw_files(config)

    targets = shots_doc["shots"]
    if args.shot:
        targets = [s for s in targets if s["id"] == args.shot]
        if not targets:
            print(f"error: no shot with id {args.shot!r} in shots.yaml", file=sys.stderr)
            return 1

    rendered, skipped, blocked = 0, 0, 0
    for shot in targets:
        shot_id = shot["id"]
        state, notes = common.compute_state(config, shot, select_data, raw_by_shot)

        if args.all_ready and state != "ready":
            continue

        if state not in ("ready", "blocked"):
            print(f"[{shot_id}] skipped -- state is {state!r} ({'; '.join(notes) or 'no still selected'})")
            blocked += 1
            continue

        still_path = common.selected_still_path(config, shot_id, select_data)
        if still_path is None or not still_path.exists():
            print(f"[{shot_id}] blocked -- no usable selected still on disk")
            blocked += 1
            continue

        if not args.redo and common.latest_take(config, shot_id) is not None:
            skipped += 1
            continue

        try:
            out_path = render_one(config, shot, still_path)
        except subprocess.CalledProcessError as e:
            print(f"[{shot_id}] ffmpeg failed: {e.stderr.decode(errors='replace')[-500:]}", file=sys.stderr)
            blocked += 1
            continue

        rendered += 1
        print(f"[{shot_id}] rendered {out_path.relative_to(common.ROOT)}")

    print(f"\n{rendered} rendered, {skipped} already had a take, {blocked} blocked/skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
