## 1. Shot catalog & state machine (implemented)

- [x] 1.1 `shots.yaml` schema: top-level `anchors` + 45-shot `shots` list (id, act, publish_order, production_priority, duration, characters, image_prompt, motion, narration, music_cue, optional `cut: hard`)
- [x] 1.2 `scripts/common.py`: config/shots/`select.json` loading, path resolution helpers
- [x] 1.3 `scripts/common.py`: `compute_state()` implementing the 5-state precedence (needs_prompt → awaiting_still → needs_select → blocked → ready)
- [x] 1.4 `scripts/status.py`: per-shot table printout; `--write` regenerates `CATALOG.md`

## 2. Prompt emission (implemented)

- [x] 2.1 `scripts/emit_prompts.py`: anchor splicing + scene text + style suffix composition
- [x] 2.2 Default skip-if-already-selected behavior, with `--force` and `--shot <id>` overrides
- [x] 2.3 Loud failure on a shot referencing an undefined anchor key

## 3. Still ingestion (implemented)

- [x] 3.1 `scripts/ingest.py`: `<shot_id>_v<N>.<ext>` naming validation, unknown-shot-id and disallowed-extension rejection
- [x] 3.2 Minimum-resolution check as a non-blocking warning
- [x] 3.3 `(size, mtime)`-keyed validation cache (`.ingest_catalog.json`) with stale-entry pruning

## 4. Shot rendering (implemented)

- [x] 4.1 Pure timing functions: `sine_ease`, `signed_sine_ease`, `effective_duration`, `frame_count`
- [x] 4.2 zoompan expression builder for `push_in` / `push_out` / `pan_left` / `pan_right` / `tilt_up` / `hold`
- [x] 4.3 Monochrome grade (desaturate + contrast) with strength-blended vignette
- [x] 4.4 Never-overwrite take naming (`<shot_id>_take<N>.mp4`)
- [ ] 4.5 `tests/test_render_shot_timing.py` covering 4.1's pure functions (frame-count rounding, no-narration/fits/overruns cases for `effective_duration`, ease curve boundary values at p=0/0.5/1)

## 5. Take selection (not yet implemented)

- [ ] 5.1 `scripts/select.py` CLI: `select.py <shot_id> <version>`, validated against ingested raw files (`common.raw_versions_for_shot`)
- [ ] 5.2 Write/update the shot's `select.json` entry (version + extension)
- [ ] 5.3 Copy the chosen file into `stills/selected/<shot_id>.<ext>`, replacing any prior selection for that shot
- [ ] 5.4 Fail loudly (non-zero exit, specific error) on an unknown shot id or a version with no matching ingested file

## 6. Narration synthesis (not yet implemented)

- [ ] 6.1 `scripts/narrate.py`: iterate shots with non-empty `narration` text
- [ ] 6.2 edge-tts synthesis path using `config.yaml`'s `tts.edge_tts` (voice/rate/pitch) → `audio/narration/<shot_id>.wav`
- [ ] 6.3 `tts.engine` dispatch seam: `edge_tts` implemented now; `openai` path raises a clear "not implemented yet" error rather than silently falling back to edge-tts
- [ ] 6.4 `.narration_cache.json`: hash-of-narration-text change detection so unchanged shots aren't re-synthesized; `--force` override

## 7. Rough-cut assembly (not yet implemented)

- [ ] 7.1 Pure timeline function: given ordered `(shot_id, duration, cut_mode)` tuples + `crossfade_frames`, compute each cut's in/out timecodes and transition type
- [ ] 7.2 `tests/test_assemble_timing.py` covering 7.1 (all-crossfade, all-hard-cut, and mixed sequences; total-duration accounting for crossfade overlap)
- [ ] 7.3 Slate-card generation (2s, shot id on black) for any shot not in state `ready`
- [ ] 7.4 ffmpeg filter-complex builder: `xfade` for crossfades, concat for hard cuts, driven by 7.1's timeline
- [ ] 7.5 Audio mix: per-shot narration + `music_cue` track start/continue-until-next-cue logic
- [ ] 7.6 Plain-text EDL export alongside the output video
- [ ] 7.7 Never-overwrite build naming (`build/rough_cut_v<N>.mp4`)

## 8. Verification

- [ ] 8.1 `python -m pytest tests/` passes (render_shot + assemble timing tests)
- [ ] 8.2 `python scripts/status.py` run end-to-end against real (non-test-fixture) project state, confirm the catalog reflects reality
- [ ] 8.3 `README.md`'s working loop re-checked against every script's final CLI flags
