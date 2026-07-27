## Context

This is a solo-operator, local batch pipeline with no server component: two
YAML files (`config.yaml`, `shots.yaml`) and a handful of scripts under
`scripts/`, run by hand in a loop (`status.py` → `emit_prompts.py` → manual
image generation → `ingest.py` → `select.py` → `narrate.py` →
`render_shot.py` → `assemble.py`). `scripts/common.py`, `emit_prompts.py`,
`ingest.py`, `status.py`, and `render_shot.py` are already written and
working; `select.py`, `narrate.py`, and `assemble.py` are designed here but
not yet implemented. The design choices below cover both — where a decision
is already implemented, that's noted explicitly.

## Goals / Non-Goals

**Goals:**
- A single source of truth for "what state is shot X in" that can never
  drift from the filesystem (no cache that has to be invalidated correctly).
- A rendering approach that produces genuinely subtle, professional-reading
  Ken Burns motion and an honest, tunable monochrome grade — not a
  first-guess ffmpeg filter chain that happens to run.
- A clean seam between "free now" (edge-tts) and "paid later" (OpenAI
  tts-1-hd/onyx) that costs one config value, not a code change.
- Every failure mode (missing still, missing narration, bad ffmpeg filter)
  degrades one shot, never the whole build.

**Non-Goals:**
- No web UI, no database, no multi-user concurrency — this is a single
  operator's local tool.
- No image generation automation — that stays manual and outside the
  pipeline by explicit project mandate.
- No general-purpose video editor feature set (no ripple edits, no
  nested timelines, no non-linear reordering beyond editing `publish_order`
  in `shots.yaml`).

## Decisions

### 1. Shot state is computed live, never cached
**Decision:** `common.compute_state()` re-derives each shot's state from
`shots.yaml` + `select.json` + a fresh directory scan on every call. The one
piece of cached data in the whole system is `ingest.py`'s
`.ingest_catalog.json`, and that cache stores only *validation results*
(dimensions, warnings) keyed by `(size, mtime)` — never shot state or
selection status.
**Why over alternatives:** A persisted state cache (e.g., a `catalog.json`
written by `status.py` and read by `render_shot.py`) would need careful
invalidation every time a file is dropped into `stills/raw/` or
`audio/narration/` by hand outside the pipeline — exactly the kind of
silent-drift bug this project's own conventions (see `CLAUDE.md`-equivalent
guidance carried into `openspec/config.yaml`'s context) warn against. Live
computation is slightly more filesystem I/O per run, which is irrelevant at
45-shot scale.

### 2. Ken Burns motion: absolute-frame zoompan expressions, not incremental
**Decision (implemented):** `render_shot.py` builds zoompan `z`/`x`/`y`
expressions as closed-form functions of `on` (zoompan's output frame
counter), using a sine ease (`0.5 - 0.5*cos(pi*p)`) or its signed variant
for pans/tilts, rather than the common incremental idiom (`z='zoom+0.001'`).
**Why:** Incremental expressions accumulate rounding error and are sensitive
to frame-count mismatches between the intended and actual output length.
An absolute function of `on`, clamped at the final frame
(`min(on, d_frames-1)`), is exact, trivially unit-testable in pure Python
(no ffmpeg invocation needed), and holds cleanly at the last eased value if
ffmpeg ever emits one extra frame due to rounding.
**Alternative considered:** Driving motion via keyframed `sendcmd`/`zoompan`
combos — rejected as materially more complex for no behavioral benefit at
this project's scale.

### 3. Vignette strength via blend, not `vignette`'s `angle` parameter
**Decision (implemented):** Render two branches — the graded frame, and the
same frame with `vignette=angle=PI/5` (ffmpeg's own default, a strong,
well-tested vignette) — then `blend` them at `all_opacity=<vignette_strength>`,
giving a true linear 0..1 strength control.
**Why over alternatives:** ffmpeg's `vignette` filter has no linear
"strength" knob; its `angle` parameter is narrow and nonlinear, and even
angle values intended to be "gentle" (up to `PI/2`, its practical maximum)
still crushed corners toward black in testing. Blending against the
un-vignetted frame at a configured opacity is the standard workaround and
makes "gentle vignette" (the project's explicit requirement) actually
achievable and tunable.

### 4. Narration duration determines render duration, computed before rendering
**Decision (implemented):** `render_shot.py` probes the shot's narration
`.wav` (if present) via `ffprobe` and computes
`effective_duration(shot_duration, narration_duration, min_padding)` — a
pure function, unit tested — before ever invoking ffmpeg's render. If
narration overruns, the take is rendered at the longer duration and the
override is logged.
**Why here, not in `assemble.py`:** `assemble.py` only concatenates
already-rendered takes; it has no way to retroactively lengthen a clip.
Duration reconciliation has to happen before the expensive render step, not
after.
**Consequence for `narrate.py`:** narration must exist (or be confirmed
absent) before `render_shot.py --all-ready` runs — enforced structurally by
the state machine, since a shot with non-empty `narration` text isn't
`ready` until its `.wav` exists (shot-catalog spec, "Narration required but
not generated" scenario).

### 5. `narrate.py`'s own idempotency cache (design for not-yet-built script)
**Decision:** `narrate.py` will maintain a small cache (e.g.
`audio/narration/.narration_cache.json`) mapping shot id → hash of the
narration text that produced the current `.wav`, mirroring `ingest.py`'s
change-detection pattern. Re-running `narrate.py` recomputes the hash for
every shot with non-empty narration and only re-synthesizes when the hash
differs from the cache (or `--force` is passed).
**Why:** Without this, every `narrate.py` run would re-synthesize all 45
shots' audio even when only one line of dialogue changed — slow, and (once
the OpenAI engine exists) not free. Hashing the source text is simpler and
more honest than mtime-based detection, since the text lives inside
`shots.yaml`, not as a standalone file with its own mtime.

### 6. Assembly transitions: per-shot solo segments + tiny transition clips, joined by stream-copy concat
**Decision (implemented):** `assemble.py` computes the whole film's cut
timeline as a pure function (`compute_timeline`) over an ordered list of
`(shot_id, duration, cut_mode)` tuples — independent of ffmpeg, unit tested
in `tests/test_assemble_timing.py` — then realizes it in three passes:
1. Every shot (take or slate) is normalized to the project's fps/resolution/
   format/timebase as its own small file (`render_normalized_clip`).
2. For each shot, `build_video_segments` trims its clip down to just the
   non-overlapping "solo" body (`trim_solo_segment` — a plain `-vf trim`
   re-encode of *that one shot's own duration*, nothing else), and for each
   crossfade, `build_transition_clip` renders just the `crossfade_frames`
   overlap window as its own standalone clip (each side trimmed to exactly
   the overlap first, then `xfade`'d at `offset=0`). A shot untouched by any
   crossfade reuses its normalized clip directly — no extra re-encode.
3. The ordered solo/transition segments are joined with ffmpeg's concat
   demuxer and `-c copy` (`concat_segments`) — a stream copy, not a
   re-encode, since every segment already shares identical codec/format/
   timebase. Only the audio mix (`amix`/`adelay`, plus the final video mux)
   touches every shot's data in one command.

**History — two prior designs, both scale with film length despite
looking bounded:**
- **v1: single filter_complex spanning all N shots.** A real 45-shot run
  was killed by the kernel OOM killer (`returncode -9`) — a filter graph
  with every shot as a live input has to keep all of them decodable
  simultaneously, so peak memory scales with film length.
- **v2: iterative pairwise fold.** Replaced v1 on the theory that folding
  two clips at a time caps peak memory at "two clips" regardless of film
  length. It doesn't: each fold step still fully decodes and re-encodes the
  entire *accumulated-so-far* running clip, not just the two nominal
  inputs, so total work (and, on a memory-constrained host, peak resident
  set) still scales with position in the film. Confirmed directly: two
  independent real 45-shot runs — one with ~1GB more host memory headroom
  than the other — both died at the same ~70-80s mark of accumulated
  duration (fold step 9 and step 10 respectively), not at a fixed step
  count. That "same duration, different step" signature is what rules out
  pure host-wide pressure as the sole explanation and points at the design
  itself.

**Why v3 (this decision) actually is bounded:** no ffmpeg invocation ever
touches more than one shot's own duration (solo trim), the fixed
`crossfade_frames` window (transition clips), or does a real decode/encode
of the full timeline at all (the final concat is a stream copy). Per-
invocation cost is therefore independent of N and of position in the film —
verified against the full 45-shot catalog completing normalize + segment-
build + concat without incident on this project's host, at whatever memory
headroom happened to be available at the time (not a specially-freed-up
window).

**Trade-off accepted:** roughly `3N` ffmpeg invocations in the worst case
(N normalizes + up to N solo trims + up to N-1 transition clips) before the
one stream-copy concat and the one final audio mux/mux-out — more
invocations than v2, but each one is now cheap and flat-cost, so total
wall-clock is lower in practice, not higher. Consistent with this project's
own precedent elsewhere (companion tool's `build.py`: "Both normalize() and
the final concat ALWAYS re-encode, never stream-copy... correctness over
speed here") for the parts that must be re-encoded (solo trims, transitions)
— stream-copy is used only for the final join, where no filtering is
happening and none is needed.

### 7. EDL as plain text, not a broadcast-standard CMX3600 file
**Decision:** The EDL export is a simple, readable text log (shot id, in/out
timecodes, audio sources) rather than a CMX3600-conformant EDL.
**Why:** Nothing downstream of this pipeline consumes a broadcast EDL (no
NLE round-trip is in scope); a plain log that's easy to eyeball while
reviewing a rough cut satisfies the actual need at far less complexity.

## Risks / Trade-offs

- **[Risk]** This project's host is a shared, heavily-loaded server (other
  projects' Docker containers and backend processes routinely leave under
  200MB of the machine's 15GB genuinely free). Even a per-shot-bounded
  ffmpeg step can in principle be OOM-killed by host-wide pressure that has
  nothing to do with this pipeline. → **Mitigation:** decision #6 (v3)
  keeps every individual ffmpeg invocation's cost bounded by one shot's own
  duration or the fixed crossfade window, never by total film length, which
  is what actually matters on a host this constrained — v1 and v2 both
  looked bounded but weren't. `assemble.py`'s failure path still writes the
  full failing command + ffmpeg stderr to `build/assemble_last_error.log`
  (not a truncated tail) so that if host pressure ever does kill a step
  (`returncode -9`, no ffmpeg error text), it's easy to tell apart from an
  actual filter-graph or argument bug. No further mitigation belongs in this
  project — freeing host memory is an operational decision for whoever
  manages the other workloads on that machine, not something a single
  pipeline's code should work around by, e.g., silently retrying.
- **[Risk]** ffmpeg's `zoompan`+`xfade` combination is filter-graph-heavy;
  large filter_complex strings are harder to debug than a chain of `-vf`
  flags. → **Mitigation:** each render/assembly step logs the exact filter
  graph it built on failure, and the timing math that drives it is unit
  tested independent of ffmpeg, so a filter-graph bug is easy to isolate
  from a timing bug.
- **[Risk]** edge-tts is an unofficial wrapper around a Microsoft consumer
  service, not a stable published API — it could break upstream without
  notice. → **Mitigation:** this is exactly why `tts.engine` exists as a
  config seam; the project accepts this risk in exchange for zero cost, per
  explicit project mandate.
- **[Risk]** A 45-shot rough cut involves roughly `3N` ffmpeg invocations
  (decision #6, v3) before the final concat and audio mux, which can get
  slow to re-render in full even though each invocation is individually
  cheap. → **Mitigation:** out of scope for this baseline; acceptable at
  current scale (a few hundred seconds of output), revisit only if it
  becomes a measured bottleneck.

## Migration Plan
Not applicable — greenfield project, no existing users or data to migrate.

## Open Questions
- Exact audio ducking behavior when a music track and narration overlap
  (e.g., duck music under narration vs. simple additive mix) is left to
  `assemble.py`'s implementation; the spec requires both to be audible but
  doesn't mandate a specific ducking curve.
