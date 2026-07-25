#!/usr/bin/env python3
"""shots.yaml -> prompts/*.txt: one paste-ready prompt block per shot.

Each output file is nothing but the prompt text itself -- meant to be
selected and pasted whole into the free image generator's UI, so no
headers, metadata, or comments are written into it.

A prompt block is built as:

    <anchor text for each name in shot['characters'], in order>

    <shot['image_prompt']>

    <config['style_suffix']>

By default, only shots that do NOT yet have a selected still get their
prompt (re)written -- once you've picked a winning take there's usually
nothing left to paste. Use --force to regenerate everything (e.g. after
editing an anchor block) or --shot to target one shot regardless of its
selection state.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


def build_prompt_text(config: dict, anchors: dict, shot: dict) -> str:
    missing = [c for c in shot["characters"] if c not in anchors]
    if missing:
        raise ValueError(
            f"shot {shot['id']!r} references undefined anchor(s) {missing!r} -- "
            f"add them under shots.yaml: anchors: first"
        )
    anchor_lines = [anchors[name].strip() for name in shot["characters"]]
    anchor_block = ". ".join(a.rstrip(".") for a in anchor_lines)
    if anchor_block:
        anchor_block += "."

    scene = shot["image_prompt"].strip()
    style_suffix = config["style_suffix"].strip()

    parts = [p for p in (anchor_block, scene, style_suffix) if p]
    return "\n\n".join(parts) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="regenerate every shot's prompt, even if a still is already selected")
    parser.add_argument("--shot", help="only emit the prompt for this shot id, regardless of selection state")
    args = parser.parse_args(argv)

    config = common.load_config()
    shots_doc = common.load_shots(config)
    anchors = shots_doc["anchors"]
    select_data = common.load_select(config)

    common.path_for(config, "prompts_dir").mkdir(parents=True, exist_ok=True)

    targets = shots_doc["shots"]
    if args.shot:
        targets = [s for s in targets if s["id"] == args.shot]
        if not targets:
            print(f"error: no shot with id {args.shot!r} in shots.yaml", file=sys.stderr)
            return 1

    written, skipped, errors = 0, 0, 0
    for shot in targets:
        shot_id = shot["id"]

        if not args.force and not args.shot:
            still = common.selected_still_path(config, shot_id, select_data)
            if still is not None and still.exists():
                skipped += 1
                continue

        try:
            text = build_prompt_text(config, anchors, shot)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            errors += 1
            continue

        out_path = common.prompt_path(config, shot_id)
        out_path.write_text(text, encoding="utf-8")
        written += 1
        print(f"wrote {out_path.relative_to(common.ROOT)}")

    print(f"\n{written} written, {skipped} skipped (already have a selected still), {errors} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
