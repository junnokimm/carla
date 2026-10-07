# P5 exit-47 fork failure: evidence, correction and moving revalidation

## Cause established before changing production code

The reported run was read from `data/p5_live_validation/research_events_p5-confirm-20261007-122848.csv` and its matching telemetry CSV. Counts: 2343 event rows and 2326 telemetry rows. The recorded single-route semantic hash `baafba3d9264eb16e6e010bde128821ef2e2c779f230f1b8cdc4318d0a8d97e8` matches the current V3 exit-47 route. The manifest, controller gains, output caps and development duration override were not changed by this fix.

The original event sequence was confirmed: initial NoA ON 4.688s, presentation 4.766s, confirmation 7.344s, movement onset 14.594s, target boundary crossing 25.688s, MISSED 48.750s. No driver interruption precedes MISSED; button deactivation is later at 66.031s.

The direct native CARLA map probe showed that `(782,0,-4)` and `(774,0,-4)` begin at effectively the same XY, approximately `(15.253177, -47.238476)`. The intended exit continues through `(782,1,-2)` to `(34,0,+2)`. The V3 sampled reference correctly takes that exit. Its projection at the through-labelled common fork pose is station 1151.941244m and only 0.002157m from the intended reference.

The actual coordinator was exercised with that native geometry before editing: confirmation armed the reference; observing identity `(774,0,-4)` immediately produced `COMPLETED/MISSED` and cancelled it, even though the pose was still on the exit corridor. This is a lifecycle bug triggered by ambiguous map identity, not evidence that a vehicle has physically missed the branch.

At a native through pose 15m downstream, the restored ordinary lane follower produced steering 0. Keeping the exit reference produced steering +0.15 using the existing lateral formula and cap. Thus premature cancellation removes the steering needed to enter the exit.

Speed integration provides supporting, not exact-position, evidence: spawn 107 projects to station 1038.500364m; the fork is 113.170894m ahead. Integrated original telemetry distance up to MISSED is 113.451890m. The old CSV does not contain XY/reference state, so its entire past trajectory cannot be reconstructed exactly. The direct source/map reproduction and new trace supply the causal evidence.

### Requested cause classification

| Candidate | Finding |
|---|---|
| Route geometry/reference itself wrong | Not supported for this failure. Hash matches and the sampled path selects 782/34. Moving replay subsequently followed it successfully. |
| Reference released on lane-change completion | Not at tL or ordinary target-lane entry. The movement flag clears there, but the reference remains armed. It is incorrectly released by the early MISSED completion at the shared fork. |
| Through branch selected at fork | Physical consequence of returning to ordinary lane geometry after early completion; the serialized exit reference itself does not choose 774. |
| Projection/cursor problem | Not the cause established for the reported 48.750s failure. A separate immediate-confirm startup regression was encountered during revalidation and retained as a known limitation below. |
| Lateral controller cannot follow exit curvature | Not supported by successful corrected run: actual exit entry, maximum absolute steering 0.077410, unchanged 0.15 cap. This does not establish all-route dynamic validity. |
| Other confirmed cause | CARLA overlapping fork identities were treated as mutually exclusive branch evidence. |

## Minimal correction

- Keep the existing route/reference, controller and single writer.
- Expose observed distance to the intended exit reference and its lane half-width from the existing geometric projection. Route-match/cursor validity is still separate; no timestamp or station clamping was introduced.
- A through identity alone no longer establishes MISSED. The observed center must also be geometrically outside the intended exit lane corridor, using its existing width rather than a new arbitrary distance threshold. Unknown distance is not failure evidence.
- Actual, separated through travel still completes as MISSED and cancels the reference. True exit entry still completes as EXIT from observed exit geometry/identity. This is not a log-only relabel.
- Added road/section/lane, projected station and reference-distance/half-width fields to existing P5 observation event payloads so a future lifecycle decision can be audited from raw data.
- Existing event writer, deferred writes, driver priority, brake/mode cancellation, actual-speed abort, prime, shutdown brake and actuator bounds remain unchanged.

Changed production files:

- `src/experiment/exit_assistance_types.py`
- `src/experiment/exit_assistance_observation.py`
- `src/experiment/exit_assistance_event.py`
- `src/scenario/research_exit_runtime.py`

Tests/tool:

- New `tests/test_p5_fork_reference.py`: native fork coordinates, retained steering/reference, truly separated MISSED, unknown geometry, and continuation through lane completion/fork/exit.
- Existing `test_p5_oracle_blockers.py` and `test_p5_final_contracts.py`: through-path fixtures now explicitly provide measured geometric separation; assertions still require the original correct outcomes. Assisted decision preservation additionally requires an actual completion event.
- New `tools/validate_p5_exit47_fix.py`: bounded moving validation and passive in-memory control trace, saved on exit. No second vehicle-control writer.

## Regression and live evidence

- Before editing: **755 passed**.
- New bug regressions before fix: **2 failed, 1 passed**; failure was premature completion/reference release at the actual shared fork and with unknown separation.
- Final full suite: **759 passed in 2.35s**.
- Ruff check over changed Python files: passed.
- LSP unavailable: basedpyright is not installed and installation was previously declined. No clean type-check claim.

### First scripted attempt, safely aborted

`data/p5_live_validation/p5-exit47-fix-20261007T034308Z-b984db/`

The script initially confirmed immediately after presentation at 4.703s, unlike the user's approximately 2.58s response. It hit the existing `route projection regressed behind cursor` guard at 4.750s during initial settling. The trace shows a submillimeter backwards movement near the starting pose. The original error, ERROR interruption, MANUAL shutdown and shutdown brake were preserved. This guard was NOT relaxed. Only the validation script's confirmation timing was changed to 2.5s after presentation, matching the reported trial more closely. This is not a newly mandated participant-response policy.

### Successful final moving replay

Directory: `data/p5_live_validation/p5-exit47-fix-20261007T034405Z-9c7f0c/`

- Same spawn 107, M2 NOA_L2, route V3 exit-47, 10km/h target, unchanged gains/caps.
- Explicit `--development-validation --duration 90`; no general smoke-cap change.
- Preflight: editor restored/nonminimized, idle world 99.487Hz, no existing vehicles. World settings were observed, not changed.
- Confirmation was SCRIPTED through the existing DriverInput source, not a new physical C-key test. It waited 2.5s after presentation. After completion it supplied brake 0.5 through that same driver-input path and remained stopped until duration end.

| Event | Session seconds |
|---|---:|
| Initial NOA_ACTIVE | 4.672 |
| Presentation | 4.735 |
| CONFIRM | 7.250 |
| Actual lateral movement onset | 14.578 |
| Target-boundary crossing | 25.703 |
| Actual road 34/0/+2, EXIT | **66.297** |
| Scripted brake disengagement | 66.328 |
| Normal stage end | 94.719 |

There are 101 new observation records labelled road 774 during the shared fork, from 48.766s through 51.625s. Distance to the exit reference is approximately 0.1006–0.1792m, inside the 1.75m half-width, so the route correctly remains active. The control trace then uses reference road 782 while actually turning right. At EXIT, vehicle XY is `(33.556187, -90.612930)`; an independent CARLA map lookup resolves that saved physical pose to **34/0/+2**. Exit-reference error there is 0.215295m. This verifies branch entry rather than merely changing the outcome text.

- Event CSV: `research_events_p5-exit47-fix-20261007T034405Z-9c7f0c.csv`, **2972 data rows**, nondecreasing raw host times.
- Telemetry CSV: `research_telemetry_p5-exit47-fix-20261007T034405Z-9c7f0c.csv`, **2955 data rows**; final speed 0, throttle 0, brake 0.5, lane +2.
- `control_trace.csv`: **2049 control samples** with vehicle XY/yaw, reference XY/road/section/lane, lateral/heading errors, commanded controls and P5 status.
- Maximum observed speed **10.263786km/h**; maximum throttle **0.4**; maximum absolute steering **0.077410**; shutdown brake **0.5**.
- World during run **65.643Hz**; mean driver-loop interval **30.468ms**, max **71.286ms**.
- `stdout.txt` includes exact arguments, HEAD+dirty, prime/shutdown report and diagnostics. `source_manifest.json` records execution-source hashes; `source_unchanged True`. No executable source changed after this successful run.
- Final independent actor check found no remaining vehicles.

## Same-spawn manual-C revalidation command

Keep the editor restored with Play active. Confirm readiness again; do not minimize or change engine settings to run this command. Use a fresh run ID. The parameters are development values, not final experiment settings.

```powershell
$run = "p5-exit47-recheck-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
& ".venv\Scripts\python.exe" -m src.scenario.research_noa `
  --live-smoke --development-validation --duration 90 `
  --spawn-index 107 --front-camera-only --camera-diagnostics `
  --assignment-file data/p5_live_dev_assignment.csv `
  --participant-id DEV-P5-LIVE --run-id $run `
  --output-dir "data/p5_live_validation/$run" `
  --automation-module MODULE_2 `
  --interaction-stage development/p5-exit47-recheck `
  --activation-center-tolerance-m 0.2 --activation-heading-tolerance-rad 0.1 `
  --driver-brake-threshold 0.05 --button-deactivation enabled `
  --exit-route-manifest config/town04_exit_routes_dev_v3.json `
  --exit-route-id town04-exit-47 --exit-event-id "$run-exit" `
  --exit-confirm-key c --exit-reject-key r `
  --target-speed-kmh 10 --speed-deadband-kmh 0.2 `
  --acceleration-gain 0.1 --braking-gain 0.1 --integral-gain 0.02 `
  --max-throttle 0.4 --max-brake 0.5 `
  --lateral-error-gain 0.2 --heading-error-gain 0.5 `
  --lateral-deadband-m 0.1 --heading-deadband-rad 0.05 --max-steering 0.15
```

Press C after the recommendation appears, within the existing development response window. Unlike the scripted tool, this command does not automatically brake after EXIT. The repeatable scripted trace run is `python -m tools.validate_p5_exit47_fix` using the lab interpreter.

## Remaining live validation

- A corrected-version manual/remote C-key repeat remains pending; the original user trial already demonstrated that input, while this corrected replay used scripted confirmation.
- Immediate confirmation during initial settling can still hit the existing strict cursor-regression guard. It is a separate recorded issue, not the reported fork cause, and was not hidden by weakening the guard.
- Long continuation beyond exit entry was not validated: the successful script intentionally braked after road 34 entry. Full connector/highway rejoin, exit-39, manual missed, reject/no-response live matrix and final physical controls remain separate checks.
- No inference that all possible exit curvature is supported, that the final three-lane experiment route is approved, or that P2/P6–P8 are complete.
