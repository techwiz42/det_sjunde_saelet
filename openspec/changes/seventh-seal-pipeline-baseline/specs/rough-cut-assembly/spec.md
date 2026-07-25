## ADDED Requirements

### Requirement: Ordered assembly with graceful gap-filling
The system SHALL assemble a single output video by concatenating, in
ascending `publish_order`, either each shot's selected take (if the shot's
state is `ready`) or a 2-second slate card showing the shot's id on a black
background (for any shot not in state `ready`). A build SHALL always
complete and produce an output file regardless of how many shots are not
`ready`.

#### Scenario: All shots ready
- **WHEN** every shot in `shots.yaml` is in state `ready`
- **THEN** the assembled output SHALL contain every shot's rendered take, in
  `publish_order`, with no slate cards

#### Scenario: Some shots not ready
- **WHEN** one or more shots are not in state `ready`
- **THEN** the assembled output SHALL still be produced, with a 2-second
  slate card showing that shot's id standing in at that shot's position in
  the sequence

### Requirement: Crossfade by default, hard cut on request
By default, consecutive shots in the assembled output SHALL be joined with
the configured crossfade duration (in frames). A shot marked `cut: hard` in
`shots.yaml` SHALL instead be joined to the preceding shot with a hard cut
(no crossfade).

#### Scenario: Default transition
- **WHEN** a shot does not set `cut: hard`
- **THEN** it SHALL be joined to the previous shot with the configured
  crossfade duration

#### Scenario: Hard cut at an act break
- **WHEN** a shot sets `cut: hard`
- **THEN** it SHALL be joined to the previous shot with an instantaneous cut
  and no crossfade

### Requirement: Narration and music mixing
The assembled output's audio SHALL mix each `ready` shot's narration (when
present) at that shot's position in the timeline, together with any music
cue: a shot with a non-null `music_cue` SHALL start that track playing from
that shot's position onward, continuing under subsequent shots until
another `music_cue` starts a new track or the film ends.

#### Scenario: Shot with narration
- **WHEN** a `ready` shot has a narration file
- **THEN** that narration SHALL be audible during that shot's position in
  the assembled output

#### Scenario: Music cue starts a track
- **WHEN** a shot sets `music_cue` to a filename present in `audio/music/`
- **THEN** that track SHALL begin playing at that shot's start and continue
  under subsequent shots until a later `music_cue` or the end of the film

### Requirement: EDL export
Every assembly run SHALL also write a plain-text EDL-style log alongside the
output video, listing for every cut in the sequence: the shot id, its
in/out timecodes in the final output, and the audio sources (narration
file, active music track, or "slate" / "none") active during that cut.

#### Scenario: Assembling a rough cut
- **WHEN** an assembly run produces `build/rough_cut_v<N>.mp4`
- **THEN** it SHALL also produce a text log (e.g.
  `build/rough_cut_v<N>.edl.txt`) with one entry per shot giving its id,
  in/out timecodes, and audio sources

### Requirement: Never-overwrite build naming
Every assembly run SHALL write its output to a new file named
`<prefix>_v<N>.mp4` where `<N>` is one greater than the highest existing
version for that prefix in `build/`; an existing build output SHALL never be
overwritten by a subsequent assembly run.

#### Scenario: Re-running assembly
- **WHEN** `build/rough_cut_v1.mp4` already exists and assembly is run again
- **THEN** the new output SHALL be written as `build/rough_cut_v2.mp4`, and
  `build/rough_cut_v1.mp4` SHALL remain unchanged
