"""Shared helpers for the Det sjunde sälet pipeline scripts.

Single source of truth for: config/shots loading, path resolution, the
raw-stills naming convention, and the per-shot state machine used by
status.py (and consulted by render_shot.py / select.py so every script
agrees on what "ready" means). Deliberately has no cache file of its own --
state is always derived live from shots.yaml + select.json + the actual
contents of stills/raw, stills/selected, audio/narration, and takes/, so
there is nothing here that can go stale.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

RAW_NAME_RE = re.compile(r"^(?P<shot_id>[A-Za-z0-9]+_[A-Za-z0-9_]+)_v(?P<version>\d+)(?P<ext>\.[A-Za-z0-9]+)$")
TAKE_NAME_RE = re.compile(r"^(?P<shot_id>[A-Za-z0-9]+_[A-Za-z0-9_]+)_take(?P<n>\d+)\.mp4$")
BUILD_NAME_RE = re.compile(r"^(?P<prefix>.+)_v(?P<n>\d+)\.mp4$")

STATES = ("needs_prompt", "awaiting_still", "needs_select", "ready", "blocked")


# --- config / shots -----------------------------------------------------

def load_config(path: Path | None = None) -> dict:
    path = path or (ROOT / "config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["_root"] = ROOT
    return cfg


def path_for(config: dict, key: str) -> Path:
    """Resolve a config['paths'][key] entry to an absolute path under ROOT."""
    rel = config["paths"][key]
    return ROOT / rel


def load_shots(config: dict) -> dict:
    path = path_for(config, "shots_file")
    with open(path, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if not doc or "shots" not in doc:
        raise ValueError(f"{path} has no top-level 'shots' key")
    return doc


def shot_by_id(shots_doc: dict, shot_id: str) -> dict | None:
    for shot in shots_doc["shots"]:
        if shot["id"] == shot_id:
            return shot
    return None


def narration_required(shot: dict) -> bool:
    return bool((shot.get("narration") or "").strip())


# --- select.json ----------------------------------------------------------

def load_select(config: dict) -> dict:
    path = path_for(config, "select_file")
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_select(config: dict, data: dict) -> None:
    path = path_for(config, "select_file")
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(path)


# --- stills/raw -------------------------------------------------------------

def all_raw_files(config: dict) -> dict[str, list[tuple[int, Path]]]:
    """Scan stills/raw/ once; return {shot_id: [(version, path), ...]} sorted by version."""
    raw_dir = path_for(config, "stills_raw_dir")
    out: dict[str, list[tuple[int, Path]]] = {}
    if not raw_dir.is_dir():
        return out
    for entry in sorted(raw_dir.iterdir()):
        if not entry.is_file():
            continue
        m = RAW_NAME_RE.match(entry.name)
        if not m:
            continue
        shot_id = m.group("shot_id")
        version = int(m.group("version"))
        out.setdefault(shot_id, []).append((version, entry))
    for versions in out.values():
        versions.sort(key=lambda t: t[0])
    return out


def raw_versions_for_shot(config: dict, shot_id: str) -> list[tuple[int, Path]]:
    return all_raw_files(config).get(shot_id, [])


# --- stills/selected --------------------------------------------------------

def selected_still_path(config: dict, shot_id: str, select_data: dict | None = None) -> Path | None:
    """Path to the selected still for shot_id, or None if nothing is selected.

    Does not check existence on disk -- caller decides what a missing file means.
    """
    select_data = select_data if select_data is not None else load_select(config)
    entry = select_data.get(shot_id)
    if not entry:
        return None
    ext = entry.get("ext")
    if not ext:
        return None
    return path_for(config, "stills_selected_dir") / f"{shot_id}{ext}"


# --- prompts ------------------------------------------------------------

def prompt_path(config: dict, shot_id: str) -> Path:
    return path_for(config, "prompts_dir") / f"{shot_id}.txt"


# --- narration ------------------------------------------------------------

def narration_path(config: dict, shot_id: str) -> Path:
    return path_for(config, "narration_dir") / f"{shot_id}.wav"


# --- takes (never overwritten; incrementing suffix) --------------------------

def existing_takes(config: dict, shot_id: str) -> list[tuple[int, Path]]:
    takes_dir = path_for(config, "takes_dir")
    out = []
    if not takes_dir.is_dir():
        return out
    for entry in takes_dir.iterdir():
        m = TAKE_NAME_RE.match(entry.name)
        if m and m.group("shot_id") == shot_id:
            out.append((int(m.group("n")), entry))
    out.sort(key=lambda t: t[0])
    return out


def latest_take(config: dict, shot_id: str) -> Path | None:
    takes = existing_takes(config, shot_id)
    return takes[-1][1] if takes else None


def next_take_path(config: dict, shot_id: str) -> Path:
    takes = existing_takes(config, shot_id)
    next_n = (takes[-1][0] + 1) if takes else 1
    return path_for(config, "takes_dir") / f"{shot_id}_take{next_n}.mp4"


# --- build/ (never overwritten; incrementing suffix) --------------------------

def next_build_path(config: dict, prefix: str = "rough_cut") -> Path:
    build_dir = path_for(config, "build_dir")
    build_dir.mkdir(parents=True, exist_ok=True)
    best = 0
    for entry in build_dir.iterdir():
        m = BUILD_NAME_RE.match(entry.name)
        if m and m.group("prefix") == prefix:
            best = max(best, int(m.group("n")))
    return build_dir / f"{prefix}_v{best + 1}.mp4"


# --- per-shot state machine ------------------------------------------------

def compute_state(config: dict, shot: dict, select_data: dict, raw_by_shot: dict) -> tuple[str, list[str]]:
    """Returns (state, notes) where state is one of STATES.

    Precedence, cheapest/earliest pipeline stage first:
      1. needs_prompt    -- prompts/<id>.txt not emitted yet
      2. awaiting_still  -- prompt emitted, nothing ingested in stills/raw yet
      3. needs_select    -- stills ingested, none chosen in select.json
      4. blocked         -- a selection exists but its file is missing on disk,
                            or narration text is set but the narration wav is missing
      5. ready           -- selected still present on disk, and narration
                            (if any) present on disk
    """
    shot_id = shot["id"]
    notes: list[str] = []

    if not prompt_path(config, shot_id).exists():
        return "needs_prompt", notes

    if not raw_by_shot.get(shot_id):
        return "awaiting_still", notes

    still_path = selected_still_path(config, shot_id, select_data)
    if still_path is None:
        notes.append(f"{len(raw_by_shot[shot_id])} take(s) ingested, none selected")
        return "needs_select", notes

    if not still_path.exists():
        notes.append(f"select.json points at {still_path.name}, which is missing on disk")
        return "blocked", notes

    if narration_required(shot) and not narration_path(config, shot_id).exists():
        notes.append("narration text set but narration wav not generated")
        return "blocked", notes

    return "ready", notes


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def copy_or_symlink(src: Path, dst: Path, use_symlink: bool = False) -> None:
    ensure_parent(dst)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    if use_symlink:
        dst.symlink_to(src.resolve())
    else:
        shutil.copy2(src, dst)
