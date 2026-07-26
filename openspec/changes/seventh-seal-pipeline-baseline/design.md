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

### 6. Assembly transitions: iterative pairwise fold, not one filter graph
**Decision (implemented):** `assemble.py` computes the whole film's cut
timeline as a pure function (`compute_timeline`) over an ordered list of
`(shot_id, duration, cut_mode)` tuples — independent of ffmpeg, unit tested
in `tests/test_assemble_timing.py` — then realizes it in two passes:
1. Every shot (take or slate) is normalized to the project's fps/resolution/
   format/timebase as its own small file (`render_normalized_clip`).
2. Those clips are folded **pairwise**, in timeline order, with ffmpeg's
   `xfade` (crossfade) or `concat` (hard cut) filter: each fold reads the
   running cut-so-far plus the next clip and writes a new running file,
   discarding the previous one. Only the audio mix (`amix`/`adelay`, plus
   the running video's final mux) touches every shot's data in one command.

**Why over a single filter_complex spanning all N shots:** that was the
first implementation, and a real 45-shot run of it was killed by the
kernel OOM killer (`returncode -9`) — a filter graph with every shot as a
live input has to keep all of them decodable simultaneously, so peak memory
scales with film length. The iterative fold caps peak memory at "two clips"
regardless of how long the film gets, which is the actual requirement for a
tool meant to grow to feature length. Confirmed against both a 3-shot
slate-only smoke test (succeeded, crossfade timings matched
`compute_timeline`'s numbers exactly) and the full 45-shot catalog (the
architecture completes normalize+fold correctly; on this project's specific
host, individual fold steps have also been killed by *host-wide* memory
pressure from ~36 unrelated Docker containers competing for the same
15GB — an environmental condition, not a defect in this design, and outside
what any in-process memory bound can fully protect against).

**Trade-off accepted:** roughly `2N - 1` ffmpeg invocations for an N-shot
film (N normalizes + N-1 folds) instead of 1, and each fold is a real
re-encode generation (mitigated by using a higher-quality intermediate CRF
than the final deliverable's, since only the last generation is the actual
delivered file) — more wall-clock and more re-encoding than a single graph,
in exchange for memory use that never depends on film length. Consistent
with this project's own precedent elsewhere (companion tool's `build.py`:
"Both normalize() and the final concat ALWAYS re-encode, never
stream-copy... correctness over speed here").

### 7. EDL as plain text, not a broadcast-standard CMX3600 file
**Decision:** The EDL export is a simple, readable text log (shot id, in/out
timecodes, audio sources) rather than a CMX3600-conformant EDL.
**Why:** Nothing downstream of this pipeline consumes a broadcast EDL (no
NLE round-trip is in scope); a plain log that's easy to eyeball while
reviewing a rough cut satisfies the actual need at far less complexity.

## Risks / Trade-offs

- **[Risk]** This project's host is a shared, heavily-loaded server (other
  projects' Docker containers and backend processes routinely leave under
  200MB of the machine's 15GB genuinely free). Even a memory-bounded ffmpeg
  step can be OOM-killed by host-wide pressure that has nothing to do with
  this pipeline. → **Mitigation:** `assemble.py`'s failure path writes the
  full failing command + ffmpeg stderr to `build/assemble_last_error.log`
  (not a truncated tail) specifically so a host-pressure kill
  (`returncode -9`, no ffmpeg error text) is easy to tell apart from an
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
- **[Risk]** A 45-shot rough cut with per-shot `xfade` folding is O(n)
  sequential ffmpeg filter stages, which can get slow to re-render in full.
  → **Mitigation:** out of scope for this baseline; acceptable at current
  scale (a few hundred seconds of output), revisit only if it becomes a
  measured bottleneck.

## Migration Plan
Not applicable — greenfield project, no existing users or data to migrate.

## Open Questions
- Exact audio ducking behavior when a music track and narration overlap
  (e.g., duck music under narration vs. simple additive mix) is left to
  `assemble.py`'s implementation; the spec requires both to be audible but
  doesn't mandate a specific ducking curve.
