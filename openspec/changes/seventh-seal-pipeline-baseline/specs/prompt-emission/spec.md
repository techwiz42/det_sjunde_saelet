## ADDED Requirements

### Requirement: Paste-ready prompt composition
For each shot, the system SHALL compose a prompt file consisting of exactly:
the anchor text for every key in the shot's `characters` list (in list
order, joined into flowing prose), followed by the shot's `image_prompt`,
followed by the project's global style suffix — separated by blank lines,
with no additional headers, metadata, or comments, so the file's entire
contents can be selected and pasted directly into an image generator.

#### Scenario: Shot with two characters
- **WHEN** a shot lists `characters: [antonius, death]`
- **THEN** the emitted prompt SHALL contain the `antonius` anchor text,
  then the `death` anchor text, then the shot's scene text, then the style
  suffix, in that order

#### Scenario: Shot with no characters
- **WHEN** a shot's `characters` list is empty
- **THEN** the emitted prompt SHALL contain only the scene text followed by
  the style suffix, with no empty anchor section

### Requirement: Selective regeneration by default
By default, the system SHALL only write a prompt file for a shot that does
not yet have a selected still on disk, leaving prompts for already-decided
shots untouched. The system SHALL also support an explicit override to
regenerate every shot's prompt regardless of selection state, and an
explicit override to target one named shot regardless of its selection
state.

#### Scenario: Shot already has a selected still
- **WHEN** prompt emission runs without any override flags and a shot has a
  selected still on disk
- **THEN** that shot's prompt file SHALL NOT be rewritten, and the shot
  SHALL be reported as skipped

#### Scenario: Forced regeneration after an anchor edit
- **WHEN** prompt emission runs with its force override
- **THEN** every shot's prompt file SHALL be rewritten, including shots that
  already have a selected still
