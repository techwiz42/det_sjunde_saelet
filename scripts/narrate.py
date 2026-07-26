#!/usr/bin/env python3
"""shots.yaml narration text -> audio/narration/*.wav.

Engine is selected entirely by config.yaml's tts.engine -- switching to a
different engine later (e.g. OpenAI tts-1-hd/onyx) is a one-value config
change, not a code change. Only edge_tts is implemented; any other engine
value fails loudly rather than silently falling back to edge-tts.

Shots with empty/whitespace-only narration are intentionally silent: no wav
is created, and any stale wav left over from a previous (now-empty)
narration line is removed.

Regeneration is idempotent: a small cache
(audio/narration/.narration_cache.json) keys each shot's synthesized wav to
a hash of (engine, voice config, narration text), so unchanged shots aren't
resynthesized on every run. --force bypasses the cache.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

CACHE_NAME = ".narration_cache.json"


def load_cache(narration_dir: Path) -> dict:
    path = narration_dir / CACHE_NAME
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_cache(narration_dir: Path, cache: dict) -> None:
    path = narration_dir / CACHE_NAME
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(path)


def voice_config_key(config: dict) -> str:
    engine = config["tts"]["engine"]
    if engine == "edge_tts":
        c = config["tts"]["edge_tts"]
        return f"edge_tts|{c['voice']}|{c['rate']}|{c['pitch']}"
    if engine == "openai":
        c = config["tts"]["openai"]
        return f"openai|{c['model']}|{c['voice']}"
    raise ValueError(f"unknown tts.engine {engine!r}")


def text_hash(config: dict, narration_text: str) -> str:
    composite = f"{voice_config_key(config)}\n{narration_text.strip()}"
    return hashlib.sha256(composite.encode("utf-8")).hexdigest()


async def _synthesize_edge_tts(text: str, out_path: Path, voice: str, rate: str, pitch: str) -> None:
    import edge_tts

    tmp_mp3 = out_path.with_suffix(".tmp.mp3")
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await communicate.save(str(tmp_mp3))

    subprocess.run(
        ["ffmpeg", "-y", "-i", str(tmp_mp3), "-ar", "44100", "-ac", "1", str(out_path)],
        check=True, capture_output=True,
    )
    tmp_mp3.unlink(missing_ok=True)


def synthesize(config: dict, shot_id: str, text: str, out_path: Path) -> None:
    engine = config["tts"]["engine"]
    if engine == "edge_tts":
        c = config["tts"]["edge_tts"]
        asyncio.run(_synthesize_edge_tts(text, out_path, c["voice"], c["rate"], c["pitch"]))
    elif engine == "openai":
        raise NotImplementedError(
            "tts.engine is 'openai' but the OpenAI tts-1-hd/onyx synthesis path "
            "is not implemented yet -- this is a deliberate placeholder, not a "
            "bug. Set tts.engine back to 'edge_tts' in config.yaml, or implement "
            "the OpenAI path in narrate.py before using it."
        )
    else:
        raise ValueError(f"unknown tts.engine {engine!r} in config.yaml")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="resynthesize every shot's narration, ignoring the cache")
    parser.add_argument("--shot", help="only synthesize this shot id, regardless of the cache")
    args = parser.parse_args(argv)

    config = common.load_config()
    shots_doc = common.load_shots(config)
    narration_dir = common.path_for(config, "narration_dir")
    narration_dir.mkdir(parents=True, exist_ok=True)

    cache = load_cache(narration_dir)

    targets = shots_doc["shots"]
    if args.shot:
        targets = [s for s in targets if s["id"] == args.shot]
        if not targets:
            print(f"error: no shot with id {args.shot!r} in shots.yaml", file=sys.stderr)
            return 1

    synthesized, skipped, cleaned, errors = 0, 0, 0, 0
    for shot in targets:
        shot_id = shot["id"]
        out_path = common.narration_path(config, shot_id)

        if not common.narration_required(shot):
            if out_path.exists():
                out_path.unlink()
                print(f"[{shot_id}] removed stale narration (narration text is now empty)")
                cleaned += 1
            cache.pop(shot_id, None)
            continue

        text = shot["narration"].strip()
        current_hash = text_hash(config, text)
        cached_hash = cache.get(shot_id, {}).get("text_hash")

        if not args.force and cached_hash == current_hash and out_path.exists():
            skipped += 1
            continue

        try:
            synthesize(config, shot_id, text, out_path)
        except (subprocess.CalledProcessError, NotImplementedError, ValueError) as e:
            print(f"[{shot_id}] narration synthesis failed: {e}", file=sys.stderr)
            errors += 1
            continue

        cache[shot_id] = {"text_hash": current_hash}
        synthesized += 1
        print(f"[{shot_id}] synthesized {out_path.relative_to(common.ROOT)}")

    save_cache(narration_dir, cache)

    print(f"\n{synthesized} synthesized, {skipped} unchanged, {cleaned} cleaned up, {errors} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
