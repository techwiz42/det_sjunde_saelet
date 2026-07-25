## ADDED Requirements

### Requirement: Eased Ken Burns motion
The system SHALL animate every non-`hold` motion type (`push_in`,
`push_out`, `pan_left`, `pan_right`, `tilt_up`) with a sine ease-in-out
curve over the clip's full frame count — slow at the start and end of the
move, fastest at the midpoint. The system SHALL NOT move the zoom or pan
position at a constant (linear) rate, and a `hold` shot SHALL have no zoom
or pan motion at all.

#### Scenario: push_in shot
- **WHEN** a shot has `motion.type: push_in` and `motion.amount: 0.08`
- **THEN** the rendered clip's zoom level SHALL start at 1.0, end at 1.08,
  and SHALL change more slowly in the first and last tenth of the clip's
  duration than at its midpoint

#### Scenario: hold shot
- **WHEN** a shot has `motion.type: hold`
- **THEN** the rendered clip SHALL show no zoom or pan change across its
  full duration

### Requirement: Fixed monochrome grade
Every rendered clip SHALL be desaturated to full grayscale, have a slight
contrast lift applied, and — when the vignette option is enabled — have a
vignette blended in at a configurable linear strength between fully absent
(0) and the full effect (1). No other stylization (color grading, film
grain, letterboxing, or similar) SHALL be applied by the rendering step.

#### Scenario: Default grade settings
- **WHEN** a shot is rendered with the project's default grade configuration
- **THEN** the output SHALL be fully desaturated, SHALL show a mild contrast
  increase relative to the source still, and SHALL show a gentle brightness
  falloff toward the corners with no crushed-black vignette edge

#### Scenario: Vignette disabled
- **WHEN** the grade configuration disables the vignette
- **THEN** the rendered clip SHALL show no corner darkening at all

### Requirement: Narration-aware effective duration
Before rendering, the system SHALL determine the clip's actual duration by
comparing the shot's stated `duration` against its narration audio length
(if a narration file exists for that shot): if the narration, plus the
configured minimum padding, exceeds the stated duration, the clip SHALL be
rendered at `narration_length + padding` instead, and the override SHALL be
logged; narration audio SHALL never be clipped by keeping the shorter,
stated duration.

#### Scenario: Narration fits within the stated duration
- **WHEN** a shot's narration audio is shorter than `duration - padding`
- **THEN** the clip SHALL be rendered at the stated `duration`, and no
  override SHALL be logged

#### Scenario: Narration overruns the stated duration
- **WHEN** a shot's narration audio length plus the configured padding
  exceeds the stated `duration`
- **THEN** the clip SHALL be rendered at `narration_length + padding`, and
  this override SHALL be logged, naming the shot, the stated duration, and
  the duration actually used

### Requirement: Never-overwrite take naming
Every render SHALL be written to a new file named `<shot_id>_take<N>.mp4`
where `<N>` is one greater than the highest existing take number for that
shot id; an existing take file SHALL never be overwritten by a subsequent
render of the same shot.

#### Scenario: Re-rendering a shot that already has a take
- **WHEN** a shot already has `<shot_id>_take1.mp4` and is rendered again
- **THEN** the new render SHALL be written as `<shot_id>_take2.mp4`, and
  `<shot_id>_take1.mp4` SHALL remain unchanged on disk
