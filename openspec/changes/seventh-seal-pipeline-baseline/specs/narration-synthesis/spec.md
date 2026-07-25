## ADDED Requirements

### Requirement: Text-to-speech generation per shot
For every shot whose `narration` text is non-empty, the system SHALL
synthesize speech audio and write it to `audio/narration/<shot_id>.wav`.
Shots with empty or whitespace-only narration SHALL be treated as
intentionally silent and SHALL NOT produce a narration file.

#### Scenario: Shot with narration text
- **WHEN** a shot's `narration` field is non-empty
- **THEN** `audio/narration/<shot_id>.wav` SHALL be created containing
  synthesized speech of that text

#### Scenario: Shot with empty narration
- **WHEN** a shot's `narration` field is empty or whitespace-only
- **THEN** no narration file SHALL be created or expected for that shot, and
  its state computation SHALL NOT require one

### Requirement: Config-driven engine selection
The narration engine SHALL be selected entirely by `config.yaml`'s
`tts.engine` value (`edge_tts` by default), such that switching to a
different engine (e.g. an OpenAI `tts-1-hd`/`onyx` backed engine) requires
changing only that configuration value, with no changes to `shots.yaml`, the
calling convention of the narration script, or any other script that
consumes `audio/narration/*.wav`.

#### Scenario: Default configuration
- **WHEN** `tts.engine` is `edge_tts`
- **THEN** narration SHALL be synthesized using the free edge-tts engine
  with the voice/rate/pitch given under `tts.edge_tts`

#### Scenario: Switching engines
- **WHEN** `tts.engine` is changed to `openai`
- **THEN** narration SHALL be synthesized using the `tts.openai.model` /
  `tts.openai.voice` configuration instead, without requiring any other
  script in the pipeline to change how it locates or uses
  `audio/narration/*.wav`

### Requirement: Idempotent regeneration
Re-running narration synthesis SHALL NOT re-synthesize a shot whose
narration text has not changed since its `.wav` was last generated, unless
explicitly forced.

#### Scenario: Re-running with no text changes
- **WHEN** narration synthesis runs again and no shot's `narration` text has
  changed since its `.wav` was generated
- **THEN** no existing narration `.wav` file SHALL be regenerated
