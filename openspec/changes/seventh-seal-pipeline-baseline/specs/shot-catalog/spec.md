## ADDED Requirements

### Requirement: Shot definition schema
`shots.yaml` SHALL define a top-level `anchors` mapping of character-key to
fixed physical-description text, and a top-level `shots` list where each
entry has: `id` (unique string), `act` (int), `publish_order` (unique int),
`production_priority` (`high`/`medium`/`low`), `duration` (float seconds),
`characters` (list of anchor keys appearing in the shot), `image_prompt`
(scene-only text — setting/action/camera/light, never a character's physical
description), `motion` (`type` + `amount`), `narration` (text, may be empty
for a silent shot), `music_cue` (filename or null), and an optional `cut:
hard` marking a hard cut into that shot.

#### Scenario: Shot references an undefined anchor
- **WHEN** a shot's `characters` list includes a key not present in `anchors`
- **THEN** any script that composes a prompt for that shot SHALL fail loudly
  with an error naming the shot and the missing anchor key, rather than
  emitting a prompt silently missing that character's description

### Requirement: Live per-shot state computation
The system SHALL compute each shot's state as exactly one of `needs_prompt`,
`awaiting_still`, `needs_select`, `blocked`, or `ready`, derived only from
`shots.yaml`, `select.json`, and the current contents of `stills/raw/`,
`stills/selected/`, and `audio/narration/` at the moment of computation. No
component SHALL persist a cached state value that could disagree with the
filesystem.

#### Scenario: Prompt not yet emitted
- **WHEN** `prompts/<shot_id>.txt` does not exist
- **THEN** the shot's state SHALL be `needs_prompt`

#### Scenario: Prompt emitted, no stills ingested
- **WHEN** `prompts/<shot_id>.txt` exists and no ingested raw versions exist
  for that shot id
- **THEN** the shot's state SHALL be `awaiting_still`

#### Scenario: Stills ingested, none selected
- **WHEN** one or more raw versions exist for the shot but `select.json` has
  no entry for it
- **THEN** the shot's state SHALL be `needs_select`

#### Scenario: Selected file missing from disk
- **WHEN** `select.json` has an entry for the shot but the referenced file
  under `stills/selected/` does not exist
- **THEN** the shot's state SHALL be `blocked`

#### Scenario: Narration required but not generated
- **WHEN** the shot's `narration` text is non-empty, a selected still exists
  on disk, but `audio/narration/<shot_id>.wav` does not exist
- **THEN** the shot's state SHALL be `blocked`

#### Scenario: Fully ready shot
- **WHEN** a selected still exists on disk and, if the shot's `narration` is
  non-empty, its narration `.wav` also exists on disk
- **THEN** the shot's state SHALL be `ready`

### Requirement: Selection persistence
Selections SHALL be recorded in `select.json`, keyed by shot id, recording at
minimum the chosen version number and file extension. This file is the only
source of truth for "which take won" — it SHALL NOT be inferred from
directory listing order or filenames alone.

#### Scenario: No selections yet
- **WHEN** `select.json` does not exist on disk
- **THEN** the system SHALL treat this identically to an existing file with
  no entries, rather than raising an error

### Requirement: Human-readable catalog report
The system SHALL provide a command that prints every shot's id, act,
production priority, computed state, and whether a rendered take exists, and
a summary count per state. It SHALL also support writing this same
information to `CATALOG.md` on request, without mutating any other file.

#### Scenario: Regenerating the catalog file
- **WHEN** the catalog report is invoked with its write flag
- **THEN** `CATALOG.md` SHALL be overwritten with the current state snapshot,
  grouped by act, and no other file under the project SHALL be modified
