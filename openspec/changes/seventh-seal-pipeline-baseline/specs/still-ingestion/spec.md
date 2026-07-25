## ADDED Requirements

### Requirement: Naming convention validation
The system SHALL only accept files in `stills/raw/` matching
`<shot_id>_v<N>.<ext>` where `<shot_id>` is a real id from `shots.yaml` and
`<ext>` is one of the configured allowed extensions. A file that does not
match this pattern, references an unknown shot id, or uses a disallowed
extension SHALL be reported as rejected with a specific reason, and SHALL
NOT abort validation of the remaining files.

#### Scenario: Misnamed file
- **WHEN** a file in `stills/raw/` does not match the `<shot_id>_v<N>.<ext>`
  pattern
- **THEN** it SHALL be reported as rejected with a naming-convention error,
  and every other file in the directory SHALL still be validated

#### Scenario: Unknown shot id
- **WHEN** a file's parsed `<shot_id>` does not match any id in `shots.yaml`
- **THEN** it SHALL be reported as rejected with an unknown-shot-id error

### Requirement: Minimum resolution check is advisory, not blocking
The system SHALL warn — but SHALL NOT reject — an otherwise validly-named
still whose long edge is below the configured minimum resolution, since the
still is still usable and only risks upscaling during the Ken Burns move.

#### Scenario: Still below minimum resolution
- **WHEN** a validly-named still's long edge is smaller than
  `ingest.min_long_edge_px`
- **THEN** it SHALL be recorded as valid, with a warning noting the actual
  and minimum long-edge pixel values

### Requirement: Change-detection validation cache
The system SHALL cache validation results per filename keyed by a
combination of file size and modification time, so that re-running
ingestion after adding new files does not re-validate files that have not
changed. Entries for files no longer present SHALL be removed from the
cache.

#### Scenario: Re-running ingestion with no new files
- **WHEN** ingestion runs again and no file in `stills/raw/` has changed
  since the last run
- **THEN** no file SHALL be re-validated, and the run SHALL report all files
  as unchanged

#### Scenario: A previously ingested file is deleted
- **WHEN** a file recorded in the validation cache is no longer present in
  `stills/raw/`
- **THEN** its cache entry SHALL be removed on the next ingestion run
