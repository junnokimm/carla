# Existing P5 connected to the single P8 operator

Current patched-runtime command and validation limits:
[P8_Patched_TM_LaneChange.md](P8_Patched_TM_LaneChange.md).
Historical execution commands below do not select the new validation environment.

## Current TM override

Phase2 adds explicit module time windows and general left/right requests:
[current contract and full command](P8_Phase2_P5_LaneChange_TrafficLights.md).
Z is now a general LEFT request, not rejection; optional R rejects P5. X confirms
by turning the right indicator on. Patched-TM one-shot assistance is connected only
under the EMPTY_REHEARSAL_ONLY gate; physical behavior remains UNVERIFIED.

[P8_Traffic_Manager_Integration.md](P8_Traffic_Manager_Integration.md) defines the
current operator contract and supersedes custom-control/activation-speed-hold
statements below. P8 uses default-speed TM; it never arms the P5 custom lateral
route reference. X OFF-to-ON confirms the existing right-exit recommendation,
Optional R rejects it; no separate confirmation button is required. A confirmed assisted request is
withheld until coordinator guards pass, then uses the same one-shot TM submission
path as general requests. Physical P5 observations continue and
are distinct from assistance status. Original manifests and Exit47 Fork Fix remain.

## Status and authority

The V5 Source of Truth is unchanged. This connects the existing P5 mechanism to
`python -m src.p8.operator_cli`; no new driving program, controller, writer,
route or research policy was created. Code connection and offline tests are not
CARLA physical validation or Participant readiness.

`config/town04_exit_routes_dev_v3.json` remains a DEVELOPMENT artifact, not an
automatically selected or approved experiment default. Existing exit47 fork logic
and actual map/spawn/footprint preflight remain in the original P5/Runner code.

## Connection contract

| Stage | Selection | Runtime | Identity |
|---|---|---|---|
| Training | Optional explicit training manifest/route | Existing600-second training, F8 preserved; TRAINING exit context | Distinct training event; CSV module remains blank |
| M1 | Explicit module manifest/route | One600-second Runner; initially MANUAL | `{run}:MODULE_1_RUNNING:{route}` |
| M2 | Same module selection and assignment route | One780-second Runner; assigned initial mode | `{run}:MODULE_2_RUNNING:{route}` |

The module template is independent of M1 SuRT assignment. Its route must equal
the assignment row's `route_id`; mismatches fail before registration claims. For
explicit non-CSV conditions, the selected route replaces only the unconfigured
placeholder. Training may use a different explicitly selected route, but it must
be compatible with the same explicitly chosen spawn. No alternate spawn is selected.

One configured invocation binds one existing Coordinator/view binding and event
ID. Existing one-run-per-stage protection remains. Module P5 with a shortened
`--drive-duration` is rejected before UI/registration. The configuration whitelist
permits P5 with NoA-only activation-speed hold only in exact nominal M1/M2 or
validated TRAINING context; P6, legacy high-speed-development P5 and development-
validation bypasses remain excluded. All existing control and safety limits remain.

## Explicit settings, no research defaults

Module pair: `--exit-route-manifest`, `--exit-route-id`.
Optional training pair: `--training-exit-route-manifest`, `--training-exit-route-id`.
When either is selected, all of these are required without CLI defaults:

- `--exit-navigation-trigger-distance-m`
- `--exit-response-timeout-s`
- `--exit-lateral-onset-threshold-m`

Module P5 additionally requires `--m1-exit-open-s`, `--m1-exit-close-s`,
`--m2-exit-open-s`, and `--m2-exit-close-s`, without defaults. Eligibility uses
active-drive elapsed time plus original route conditions. Missing windows reject
startup; no arrival in-window records NOT_PRESENTED rather than invented guidance.
Training retains its separate configuration without inheriting the M1 window.

Both templates reuse those explicit behavior values. Orphan behavior arguments
without route selection fail. File/route existence is checked offline; actual
Town04 compatibility and vehicle fit remain existing runtime checks, not live
checks performed in this work. No implicit five-second response window, C/R keys,
route or spawn is promoted to final policy.

Existing internal geometry limits, lane-3 to lane-4 envelope and0.05 steering-onset
value remain P5 development parameters. The original `p5_exit_manifest` preserves
`approval_claimed=false` and `development_values_not_policy=true`. Explicit input
does not itself establish approval.

## Behavior and evidence

- Existing successful display submission establishes t0 and freezes actual mode.
  M1 branches by that mode. M2 MANUAL keeps manual navigation even when permitted
  N activation creates its existing protocol deviation.
- In final P8, assisted confirmation records `ASSISTED_EXECUTION_BLOCKED` without
  arming the route reference. The original arming path remains in standalone P5 diagnostics.
- EXIT/MISSED comes from original observation/fork logic, not keys. Neither ends
  the600/780 Runner. Physical continuation/rejoin remains unverified.
- Activation-speed capture retains the same snapshot speed and timestamp. In P5
  combinations it avoids premature event flush/telemetry writes before older P5
  receipt events are queued; the existing chronological writer handles ordering.
  No timestamp clamping or NoA target/control algorithm change was introduced.
- All P5 fields stay in the common research CSV: identities, route/version, t0,
  mode at t0, decisions/input times, indicator/steering/lateral/crossing observations,
  outcomes and reasons. Unknown values remain null/blank.
- A manifest exit-segment identity now records `lc_exit_entry_observed`; only the
  existing terminal route observation records `lc_completed`/`EXIT`. These records
  carry `highway_rejoin_status=NOT_CONFIGURED` because the development manifests do
  not define a validated rejoin boundary. Neither event is inferred from lane-change
  completion.
- Lane-change diagnostics retain target center pose, heading, width, existing
  alignment tolerances and the observed alignment failure. If generation changes
  before executor completion acknowledgement, the lifecycle is `UNVERIFIED`, not
  `COMPLETED`, and automatic indicator OFF is not attempted.
- Per-stage summaries compare complete live event type/time/payload evidence with
  persisted records, checking identities, duplicates and contradictory/missing data.
  Live evidence is stably ordered by original timestamps; Coordinator events are
  not modified. Training study-route and actual exit-route metadata are distinct.

| Classification | Meaning |
|---|---|
| NOT_CONFIGURED | No P5 selected for this stage |
| CONFIGURED_NOT_RUN | Selected, but no live runtime/evidence |
| CONFIGURED_NOT_PRESENTED | Runtime/manifest present, no actual t0 |
| PRESENTED | Actual t0, no qualifying decision/completion |
| DECISION_RECORDED | Includes REJECT/NORESPONSE, without invented physical outcome |
| NORMAL_MISSED | Consistent completed MISSED with actual presentation |
| EXIT_CONFIRMED | Consistent observed EXIT, not just a confirmation key |
| PARTICIPANT_INTERRUPTED | User exit or existing driver-priority/mode-change interruption |
| BLOCKED | Assisted execution unavailable; a later observed EXIT/MISSED is retained separately |
| INCOMPLETE | Missing, duplicated, mismatched or conflicting evidence |
| TECHNICAL_ERROR | Actual setup/runtime/cleanup/evidence failure |

Time-cap alone is not participant interruption. Reject/no-response/miss never
causes an outcome-only P8 Abort. An abort after an observed EXIT retains that
observation without claiming normal completion. Failures have separate exit slots;
unknown drive metrics are not fabricated as zero-valued ModuleRunResult records.
Primary errors/Ctrl-C remain primary, with secondary errors retained separately.
P6/P7/readiness still prevent overall participant-completion claims.

## UNVERIFIED / BLOCKED research boundaries

- Development routes include an auxiliary fourth lane. Three-lane suitability,
  final module-route assignment and training route need research-owner approval.
- M1 late-window and M2 middle/late exit placement, and separation before final
  hazard, are not proven by a valid manifest. No location or timing was invented.
- Historical exit47 success was a bounded low-speed scripted-confirm test, not
  nominal P8 validation. Long continuation/rejoin and physical input/display timing
  require validation through this single P8 program at nominal durations.
- M2 initial NoA now registers TM after stationary preparation, without capturing0
  as a target. Actual launch remains unverified, not proof from offline registration.
- Tablet is independent physical equipment, with no software link added or assumed.
  External task/video operation and physical validity remain operator responsibilities.
  Existing P6 Tobii, P7 hazard/rescue and Participant readiness are unchanged.

## Single final-duration command

Required selections must be decided first; blank/unknown mandatory values are not
replaced with examples. Module route ID must match the existing assignment CSV row.

```powershell
Set-Location C:\dev\carla
$assignment = Read-Host "Existing assignment CSV path"
$participant = Read-Host "Assigned participant ID"
$spawn = [int](Read-Host "Verified common spawn index")
$manifest = Read-Host "Explicit module route manifest path"
$route = Read-Host "Module route ID matching assignment"
$navigation = [double](Read-Host "Confirmed common navigation distance in metres")
$response = [double](Read-Host "Confirmed response timeout in seconds")
$lateral = [double](Read-Host "Confirmed lateral onset threshold in metres")
$m1Open = [double](Read-Host "Researcher-selected M1 eligibility open seconds")
$m1Close = [double](Read-Host "Researcher-selected M1 eligibility close seconds, <=600")
$m2Open = [double](Read-Host "Researcher-selected M2 eligibility open seconds")
$m2Close = [double](Read-Host "Researcher-selected M2 eligibility close seconds, <=780")
$tmPort = [int](Read-Host "Dedicated default-settings Traffic Manager port")
$trainingOptions = @()
$trainingManifest = Read-Host "Training route manifest, or Enter if unresolved"
if ($trainingManifest) {
    $trainingRoute = Read-Host "Training route ID compatible with the same spawn"
    $trainingOptions = @("--training-exit-route-manifest", $trainingManifest,
                         "--training-exit-route-id", $trainingRoute)
}
$run = "P8-$([guid]::NewGuid().ToString('N'))"
& ".\.venv\Scripts\python.exe" -m src.p8.operator_cli `
  --mode dev-rehearsal --participant-id $participant --run-id $run `
  --assignment-file $assignment --ack-dev-live --spawn-index $spawn `
  --traffic-manager-port $tmPort --training-duration 600 `
  --exit-route-manifest $manifest --exit-route-id $route `
  --exit-navigation-trigger-distance-m $navigation `
  --exit-response-timeout-s $response --exit-lateral-onset-threshold-m $lateral `
  --m1-exit-open-s $m1Open --m1-exit-close-s $m1Close `
  --m2-exit-open-s $m2Open --m2-exit-close-s $m2Close `
  @trainingOptions
```

This uses the same program and600/600/780 timing, not a separate DEV driving system.
`dev-rehearsal` is the executable authorization mode, not a Participant bypass.
Unresolved training selection stays NOT_CONFIGURED. No shortened duration, fixed
cruise speed or route default is supplied.
