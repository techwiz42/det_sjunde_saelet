## ADDED Requirements

### Requirement: Explicit winner selection
The system SHALL provide a command that takes a shot id and a version
number, verifies that an ingested raw still with that shot id and version
exists, records the choice in `select.json`, and copies (or links) that
still into `stills/selected/` under the shot's id. Selection SHALL fail
loudly if the requested shot id or version does not exist among ingested
stills, rather than silently selecting nothing.

#### Scenario: Selecting a valid ingested version
- **WHEN** a user selects a shot id and version that exist among ingested
  raw stills
- **THEN** `select.json` SHALL record that shot id with the chosen version
  and file extension, and `stills/selected/<shot_id>.<ext>` SHALL be created
  or updated to match the chosen file

#### Scenario: Selecting a version that was never ingested
- **WHEN** a user selects a shot id and version with no matching file in
  `stills/raw/`
- **THEN** the command SHALL fail with an error naming the shot id and
  version, and SHALL NOT modify `select.json` or `stills/selected/`

### Requirement: Re-selection replaces the prior choice cleanly
Selecting a new version for a shot that already has a selection SHALL
replace both the `select.json` entry and the file in `stills/selected/`
for that shot id, leaving exactly one selected still per shot at all times.

#### Scenario: Changing a shot's selected take
- **WHEN** a shot already has version 1 selected and the user selects
  version 2 instead
- **THEN** `select.json`'s entry for that shot SHALL be updated to version
  2, and `stills/selected/<shot_id>.<ext>` SHALL be overwritten to contain
  version 2's image, with no leftover reference to version 1
