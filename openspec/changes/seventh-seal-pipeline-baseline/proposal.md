## Why

"Det sjunde sälet" needs a production pipeline that turns a 45-shot treatment
into a finished rough cut using only free tools: a human generating stills by
hand in a free web UI, and a set of scripts handling everything downstream of
that (prompt authoring, ingestion, grading/motion, narration, assembly). No
such pipeline exists yet as a specified system — four of its seven stages
were built first, directly from an informal brief, without a spec baseline to
check them against or to guide the remaining three. This change establishes
that baseline retroactively for what's built, and specifies what's left.

## What Changes

- Formalize the shot catalog and its 5-state machine (`needs_prompt` /
  `awaiting_still` / `needs_select` / `ready` / `blocked`), computed live from
  `shots.yaml` + `select.json` + the filesystem, with no persisted cache that
  could go stale.
- Formalize prompt emission: splicing per-character anchor text and a fixed
  style suffix onto each shot's scene-only prompt.
- Formalize still ingestion: naming/resolution validation with a
  change-detection cache so re-ingesting unchanged files is cheap.
- Formalize shot rendering: eased (never linear) Ken Burns motion, a fixed
  monochrome grade, and narration-aware effective shot duration.
- Specify (not yet implemented) take selection, narration synthesis, and
  final assembly (crossfades/hard cuts, narration+music mix, slate cards for
  gaps, EDL export).

No breaking changes — this is a greenfield baseline, nothing previously
specified is being altered.

## Capabilities

### New Capabilities
- `shot-catalog`: `shots.yaml` schema (anchors + per-shot fields), `select.json`,
  the 5-state model, and `CATALOG.md` generation via `status.py`.
- `prompt-emission`: `emit_prompts.py` — anchor splicing, style suffix,
  selective (re)generation.
- `still-ingestion`: `ingest.py` — naming convention, minimum-resolution
  warning, validation cache.
- `shot-rendering`: `render_shot.py` — Ken Burns easing, monochrome grade,
  narration-driven effective duration, never-overwrite take naming.
- `take-selection`: `select.py` — marking a winning take, populating
  `stills/selected/` and `select.json`. **Not yet implemented.**
- `narration-synthesis`: `narrate.py` — shots.yaml narration text to
  `audio/narration/*.wav`, engine-swappable (edge-tts now, OpenAI
  tts-1-hd/onyx later) via one config value. **Not yet implemented.**
- `rough-cut-assembly`: `assemble.py` — ordered concatenation, crossfade/hard
  cut rules, narration+music mixing, slate cards for non-`ready` shots, EDL
  export, never-overwrite build naming. **Not yet implemented.**

### Modified Capabilities
(none — greenfield baseline)

## Impact

- Affected code: `scripts/common.py`, `scripts/emit_prompts.py`,
  `scripts/ingest.py`, `scripts/status.py`, `scripts/render_shot.py` (existing,
  now speced retroactively); `scripts/select.py`, `scripts/narrate.py`,
  `scripts/assemble.py` (not yet written).
- Affected data: `shots.yaml`, `select.json`, `config.yaml`, and every
  directory under `stills/`, `audio/`, `takes/`, `build/`.
- No external dependencies beyond what's already in `requirements.txt`
  (pyyaml, pillow, edge-tts, pytest) and system `ffmpeg`/`ffprobe`.
