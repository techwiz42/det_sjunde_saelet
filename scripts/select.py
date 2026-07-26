#!/usr/bin/env python3
"""Mark the winning take for a shot: select.py <shot_id> <version>.

Validates the requested version was actually ingested (present in
stills/raw/ under common.RAW_NAME_RE), then records the choice in
select.json and copies (or, with --symlink, links) that file into
stills/selected/<shot_id>.<ext> -- replacing any prior selection for that
shot. Fails loudly and touches nothing on disk if the shot id or version
don't exist.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shot_id", help="shot id from shots.yaml")
    parser.add_argument("version", type=int, help="version number to select, e.g. 2 for <shot_id>_v2.<ext>")
    parser.add_argument("--symlink", action="store_true", help="symlink into stills/selected/ instead of copying")
    args = parser.parse_args(argv)

    config = common.load_config()
    shots_doc = common.load_shots(config)

    shot = common.shot_by_id(shots_doc, args.shot_id)
    if shot is None:
        print(f"error: no shot with id {args.shot_id!r} in shots.yaml", file=sys.stderr)
        return 1

    versions = common.raw_versions_for_shot(config, args.shot_id)
    match = next((path for v, path in versions if v == args.version), None)
    if match is None:
        available = ", ".join(str(v) for v, _ in versions) or "none"
        print(
            f"error: no ingested version {args.version} for shot {args.shot_id!r} "
            f"(available versions: {available}). Run scripts/ingest.py first if you "
            f"just dropped a new file into stills/raw/.",
            file=sys.stderr,
        )
        return 1

    ext = match.suffix
    dst = common.path_for(config, "stills_selected_dir") / f"{args.shot_id}{ext}"
    common.copy_or_symlink(match, dst, use_symlink=args.symlink)

    select_data = common.load_select(config)
    select_data[args.shot_id] = {"version": args.version, "ext": ext}
    common.save_select(config, select_data)

    verb = "symlinked" if args.symlink else "copied"
    print(f"[{args.shot_id}] selected v{args.version} -- {verb} to {dst.relative_to(common.ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
