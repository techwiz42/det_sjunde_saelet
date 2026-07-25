#!/usr/bin/env python3
"""Scan stills/raw/, validate naming + minimum resolution, record results.

Validation per file:
  - filename matches <shot_id>_v<N>.<ext> (common.RAW_NAME_RE)
  - <shot_id> is a real shot in shots.yaml
  - <ext> is one of config['ingest']['allowed_extensions']
  - long edge (max(width, height)) >= config['ingest']['min_long_edge_px'],
    otherwise WARN (not a hard failure -- the still is still usable, it may
    just force an upscale during the Ken Burns move)

Results are cached in stills/raw/.ingest_catalog.json, keyed by filename with
(size, mtime) as the change-detection key, so re-running ingest.py after
adding a handful of new files doesn't re-decode every image that was already
validated. This file is local bookkeeping only -- selection state still
lives in select.json, and status.py computes shot state by scanning the
filesystem directly rather than trusting this cache.

Never raises on a single bad file: an unparseable name, an unknown shot id,
or an unreadable image is reported and skipped, and the scan continues.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

CATALOG_NAME = ".ingest_catalog.json"


def load_catalog(raw_dir: Path) -> dict:
    path = raw_dir / CATALOG_NAME
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_catalog(raw_dir: Path, catalog: dict) -> None:
    path = raw_dir / CATALOG_NAME
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(path)


def validate_file(path: Path, known_shot_ids: set, allowed_ext: list, min_long_edge: int) -> dict:
    """Returns a catalog record: {valid, shot_id, version, warnings, error}."""
    m = common.RAW_NAME_RE.match(path.name)
    if not m:
        return {"valid": False, "error": f"does not match <shot_id>_v<N>.ext naming convention"}

    shot_id = m.group("shot_id")
    version = int(m.group("version"))
    ext = m.group("ext").lower()

    if shot_id not in known_shot_ids:
        return {"valid": False, "shot_id": shot_id, "version": version,
                "error": f"unknown shot id {shot_id!r} -- not in shots.yaml"}

    if ext not in allowed_ext:
        return {"valid": False, "shot_id": shot_id, "version": version,
                "error": f"extension {ext!r} not in allowed_extensions {allowed_ext!r}"}

    warnings = []
    try:
        with Image.open(path) as im:
            width, height = im.size
    except Exception as e:
        return {"valid": False, "shot_id": shot_id, "version": version,
                "error": f"could not read image: {e}"}

    long_edge = max(width, height)
    if long_edge < min_long_edge:
        warnings.append(
            f"long edge {long_edge}px < minimum {min_long_edge}px -- "
            f"Ken Burns move may upscale this still"
        )

    return {
        "valid": True, "shot_id": shot_id, "version": version,
        "width": width, "height": height, "warnings": warnings,
    }


def main(argv=None) -> int:
    config = common.load_config()
    shots_doc = common.load_shots(config)
    known_shot_ids = {s["id"] for s in shots_doc["shots"]}
    allowed_ext = config["ingest"]["allowed_extensions"]
    min_long_edge = config["ingest"]["min_long_edge_px"]

    raw_dir = common.path_for(config, "stills_raw_dir")
    raw_dir.mkdir(parents=True, exist_ok=True)

    catalog = load_catalog(raw_dir)

    seen_names = set()
    new_valid, new_warned, new_invalid, unchanged = [], [], [], 0

    for entry in sorted(raw_dir.iterdir()):
        if not entry.is_file() or entry.name == CATALOG_NAME or entry.name.startswith("."):
            continue
        seen_names.add(entry.name)

        stat = entry.stat()
        change_key = f"{stat.st_size}:{int(stat.st_mtime)}"
        cached = catalog.get(entry.name)
        if cached and cached.get("_change_key") == change_key:
            unchanged += 1
            continue

        record = validate_file(entry, known_shot_ids, allowed_ext, min_long_edge)
        record["_change_key"] = change_key
        catalog[entry.name] = record

        if not record["valid"]:
            new_invalid.append((entry.name, record["error"]))
        elif record["warnings"]:
            new_warned.append((entry.name, record["warnings"]))
        else:
            new_valid.append(entry.name)

    # Drop catalog entries for files the user has since removed.
    stale = [name for name in catalog if name not in seen_names]
    for name in stale:
        del catalog[name]

    save_catalog(raw_dir, catalog)

    print(f"scanned {len(seen_names)} file(s) in {raw_dir.relative_to(common.ROOT)}")
    print(f"  {unchanged} unchanged since last ingest")
    if new_valid:
        print(f"  {len(new_valid)} newly valid:")
        for name in new_valid:
            print(f"    {name}")
    if new_warned:
        print(f"  {len(new_warned)} newly valid with warnings:")
        for name, warnings in new_warned:
            for w in warnings:
                print(f"    {name}: {w}")
    if new_invalid:
        print(f"  {len(new_invalid)} rejected:")
        for name, error in new_invalid:
            print(f"    {name}: {error}")
    if stale:
        print(f"  {len(stale)} removed from catalog (file no longer present): {', '.join(stale)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
