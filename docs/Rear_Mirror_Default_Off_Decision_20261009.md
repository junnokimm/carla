# Rear mirror default-off team decision

## Decision

The driver view now omits the rear RGB sensor and rear overlay by default. The
front, left and right sensors retain their existing resolutions, FOV values,
vehicle-relative mounts, positions and horizontal-mirror behavior. Setting
`DriverViewConfig.rear_mirror_enabled=True` restores the retained rear sensor,
transform, capture settings, rectangle and draw order. `front_camera_only=True`
continues to take precedence and creates only the front sensor.

## Source-of-truth deviation

The v5 Source of Truth requires a rear-mirror AOI. Default rear-disabled operation
is an explicit team-approved deviation from that requirement, not a revision of the
original source document. Before participant use, the study protocol and AOI plan
must be reconciled and versioned through the study's approval process.

## AOI handling

The existing `config/p6_aoi_candidate.json` remains unchanged. The separate
`config/p6_aoi_rear_disabled_candidate_v1.json` retains the historical rear
rectangle for traceability but marks it unavailable with `rear_available=false`.
That rectangle classifies as `NO_AOI`; it is not reassigned to forward, other, a
side mirror, or a manufactured zero gaze coordinate. Existing layouts that omit
`rear_available` continue to treat rear as available.

Both candidate files remain unvalidated. This decision and its offline tests make
no claim about physical display mapping, eye-tracker accuracy, participant safety,
or research validity.
