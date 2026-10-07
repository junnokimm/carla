# V5-P5 implementation and validation, 2026-10-07 KST

## Changes made

### Authority and baseline

- Read `CARLA_Experiment_Design_v5_Source_of_Truth.md` and `P3_P4_Integration_Validation_20261007.md` before implementation. No professor-original PDF or internal-manual DOCX was found in this worktree or attached to this input. The user's quoted original requirements are the primary P5 specification; the reconstruction is not claimed as the original.
- Development P5 means exit guidance/confirmed lane change, not the differently numbered MD Phase V5-5. The SOT was not rewritten. No substantive P5 requirement conflict was found; numbering and document availability are distinct issues.
- Current HEAD was queried: `104491ab9cd78b6cbc56b3a999568b4f9d611932`, with extensive preexisting modified and untracked files. Baseline native tests: 635 passed. No branch/stash/worktree/history restoration or commit/push/reset occurred.
- This memo supersedes intermediate counts and the pending UI-hook notes in `P5_Backend_Validation_20261007.md`.

### Actual route and reference

- Current manifest: `config/town04_exit_routes_dev_v3.json`; semantic SHA-256 `31cce59e7a37b423af4f338674b6a7c1187145554ca1b9314428944fc05c93cc`.
- Town04 was inspected through the native CARLA map API, without generating a new map or changing an actor. These are DEVELOPMENT routes, not approved experiment routes.
- Source lane -3 to immediately-right -4 is the supported maneuver. The area has three through lanes plus an auxiliary fourth lane; this does not establish a uniformly three-lane experiment route. Other initial lanes and multi-lane crossing are not generalized or silently approved.
- Exit 39: `39/0/-4 -> 1191/0/-4 -> 1191/1/-2 -> 33/0/+2`. Missed branch includes `1184/0/-3` and `1184/0/-4`, continuing to road 40. Sampled exit continuation reaches road 47/0/+6 at XY approximately (-16.137, -19.091).
- Exit 47: `47/0/-4 -> 782/0/-4 -> 782/1/-2 -> 34/0/+2`. Missed branch includes `774/0/-3` and `774/0/-4`, continuing to road 48. Sampled continuation reaches 1097/1/+2 at XY approximately (70.799, 6.370).
- Route distances are cumulative polyline stations, not straight-line distance to the exit. A forward cursor and route corridor check reject regression/off-route projection. Failed projection is unknown/unmatched, not successful reuse of stale progress.
- Guidance, response eligibility and physical lane movement have separate station envelopes. Exit 39: navigation 86.108–1086.108 m, early confirmation through 985.983 m, physical blend 985.983–1061.065 m. Exit 47: navigation starts at 151.671 m, fork 1151.671 m, physical blend 1051.605–1126.671 m. Thus the normal guidance begins 1000 m before the fork; it was explicitly tested against the real manifest, not just a short fixture.
- Confirmation arms the source-lane/transition/exit reference. Steering still comes from the existing bounded lateral calculation and the existing single control writer. No teleport, autopilot, Traffic Manager, gain change or second apply_control loop was introduced.
- Runtime validation checks geometry spacing/tangent criteria, map anchors/corridor, right adjacency, right-change permission, direction and footprint fit. The native read-only validation used a labeled 1.90 m-wide bounding-box fixture because no live vehicle was present. Actual spawned-vehicle fit and tracking remain live checks.
- V1 is preserved as rejected evidence: its 8.732 m dogleg fails validation. V2 corrected geometry but was superseded by V3's separate navigation/confirmation/maneuver semantics. Use V3, not archived intermediate manifests.

### Guidance and decisions

- Common production text is `N km 앞 출구, 우측 차로로`. Assisted presentations add the right arrow and configured confirmation/rejection labels; manual presentations do not show them. Existing cameras, mirror locations and HUD colors are retained.
- M1 branch is selected from canonical mode at first successful display submission, not SuRT assignment. M2 Manual never starts automatic exit assistance, including its still-permitted developer NoA activation. M2 NoA cannot arm while actual NoA is OFF.
- `DriverView._prepare_frame()` freezes the rendered presentation; immediately after successful `pygame.display.flip()`, `_after_display_flip()` acknowledges that exact snapshot once per event ID. Both manual and assisted guidance get t0. Draw/flip failure or a hidden HUD cannot create a presentation record. This is API submission time, not photon time.
- Development confirmation/rejection keys are C/R, configurable and checked against driving/NoA keys, including case-insensitive collisions. Fresh KEYDOWN/KEYUP edges are used; repeats, held/prepressed inputs, unrelated window/mouse events and indicators do not create confirmation. The existing scripted DriverInput path remains distinct from physical input.
- Reject/no response do not arm the reference. Driver brake/steering and mode changes retain precedence; assistance is cancelled without automatically resuming an old route. Physical observations can still record the later exit or miss. Accepted input, actual movement and traversal outcome are separate.
- One event is owned by a module runner context; training uses a separate TRAINING phase/context and null module/condition. This is reusable functionality, not a new 55-minute orchestrator.

### P3/P4 data boundaries

- P5 uses the existing event CSV and common session clock. All pending P3/P5 events are stably ordered by original host timestamp before the existing writer drains them, after control. No timestamp clamp or separate writer.
- Vehicle speed and transform come from the labeled actor snapshot. Control/indicator reads remain independent host acquisitions; do not treat their values as exact same-frame measurements. P5 preserves actual observation frame/time and records frame gaps.
- tL is a DEVELOPMENT vehicle-center crossing of a verified paired target boundary before the fork, not any lane_id/road_id change. Missing geometry stays unknown. Manual tD is not a mental decision: movement is `MANUAL_CHANGE_OBSERVED_PROXY`; terminal unchanged traversal is `MANUAL_NOCHANGE`. Assisted REJECT/NORESPONSE is not overwritten by manual classification when the exit is missed.
- Exit is observed downstream route identity/geometry, never confirmation alone. Shutdown/time cap/error is interruption/not reached, not automatically no response. No forced turn-back or session termination is introduced on a missed exit.

### Explicit development choices, not professor-approved values

1000 m nominal guidance; 5 s response timeout; C/R keyboard buttons; 0.15 m observed lateral-onset threshold; vehicle-center tL; source -3 to target -4; sampled development routes and validation geometry limits (5 m sampling, maximum 1.25x sample spacing and 0.2 rad tangent discrepancy). These are metadata/config values, not final experiment policy. The existing low-speed PI/lateral configuration and 20 s / actual-speed safeguards are unchanged.

## Files changed

New production modules:

- `src/experiment/exit_route.py`
- `src/experiment/exit_assistance.py`
- `src/experiment/exit_assistance_types.py`
- `src/experiment/exit_assistance_policy.py`
- `src/experiment/exit_assistance_observation.py`
- `src/experiment/exit_assistance_event.py`
- `src/vehicle/carla_exit_route.py`
- `src/scenario/research_exit_config.py`
- `src/scenario/research_exit_runtime.py`
- `src/scenario/research_exit_view.py`
- `src/scenario/research_exit_hud.py`
- `src/scenario/research_exit_ledger.py`

Existing integration files modified:

- `src/experiment/automation_interaction_types.py`, `src/experiment/noa_runtime.py`
- `src/vehicle/carla_noa_simulation_control.py`
- `src/scenario/driver_hud.py`, `driver_view.py`, `research_noa_view.py`
- `src/scenario/research_noa_config.py`, `research_noa_cli.py`, `research_noa_runtime.py`, `research_noa_session.py`, `research_noa_types.py`, `research_noa_persistence.py`
- Existing fake/CLI/snapshot compatibility tests, including `tests/research_noa_fakes.py`, `tests/test_research_noa_cli.py`, `tests/test_r1_r4_runtime_boundaries.py`.

New validation/configuration:

- `config/town04_exit_routes_dev_v1.json`, `_v2.json`, `_v3.json` (V3 current; prior files preserved)
- `tests/test_p5_exit_assistance.py`, `test_p5_boundary_contracts.py`, `test_p5_oracle_blockers.py`, `test_p5_route_reference.py`, `test_p5_navigation_contract.py`, `test_p5_runner_integration.py`, `test_p5_exit_ledger.py`, `test_p5_driver_view_binding.py`, `test_p5_real_driver_view_binding.py`, `test_p5_final_contracts.py`
- `tools/export_town04_exit_routes.py`, `tools/validate_p5_readonly.py`
- This memo, `P5_Backend_Validation_20261007.md`, `DESIGN.md`, `.debug-journal.md` and evidence artifacts. Debug history is retained, not an active instrumentation hook.

## Verification results

### Commands and outcomes

- `.venv/Scripts/python.exe -m pytest -q`: **728 passed in 1.88 s**, versus 635 at task start.
- `ruff check` over new P5 production modules, touched integration modules, exporter and final QA files: passed. Final targeted check after the last fixes also passed.
- `ruff format --check` over 12 core P5 modules: **12 files already formatted**. This is a scoped result, not a repository-wide formatting claim.
- `compileall -q src tools`: passed during integration. Final changed modules were subsequently imported/executed by the final suite and validation tool.
- `git diff --check`: passed; preexisting CRLF conversion warnings remain.
- `python -m src.scenario.research_noa --help`: passed; actual P5 options are available. No invented CLI keys are documented.
- LSP/type checking: unavailable because basedpyright is not installed and prior installation permission was declined. No clean static type-check claim.

### Coverage matrix

| Case | Native unit/integration | Scripted CARLA motion | Local physical |
|---|---|---|---|
| M1 NO_SURT/SURT, t0 ON, confirm/reject/no response | Covered through branch/decision parameterizations | Not run | PENDING |
| M1 NO_SURT/SURT, t0 OFF, manual branch | Covered; movement/no-change fixtures | Not run | PENDING |
| M2 NoA ON; M2 Manual OFF | Covered; actual-mode and no-auto-arm assertions | Not run | PENDING |
| Same along-route guidance; actual 1000 m manifest trigger | Covered | Read-only map/progress only | PENDING |
| Repeated frames, prepress/held/repeat, late response, non-key events | Covered, including real pygame event objects | Not run | PENDING |
| Brake/steering/mode change, errors, cleanup, no auto resume | Covered with actual runner seams and failure fixtures | Not run | PENDING |
| Route/reference sign, source/target transition, road-change false tL | Covered; native map validation | Read-only geometry only | PENDING |
| Training/new context, CSV reconstruction, clocks, writer order | Covered | No P5 moving CSV | PENDING |

Fixtures and fake vehicles do not establish vehicle dynamics. Existing PI/lateral/P3/P4/camera/prime/ownership tests remain in the full regression suite. Old assertions were not dropped to hide failures; representation changes (post-control hook, actor snapshot transform, presentation acknowledgement) are tested explicitly.

### Final native CARLA read-only evidence

Directory: `data/p5_validation/20261007T012253Z-d0df5f46/`

Reproduction: `.venv/Scripts/python.exe -m tools.validate_p5_readonly`.

- `stdout.txt`: editor minimized `[True]`, Town04, world **2.999 Hz**, asynchronous/rendering on, vehicles empty. No window movement, OS/GPU/engine settings changes, actor spawn/control or restart.
- Both V3 routes passed real-map validation. Final semantic hash matches the manifest above.
- `source_manifest.json` records current source/test/tool hashes. `stdout.txt` ends with `source_unchanged True`. No executable source changed after this run.
- Because the editor remained minimized and slow, no active or stationary P5 CARLA driving was attempted; the prior P3/P4 live results were not relabeled as P5 results. The earliest baseline was the native 635-test run; active spawn settling/prime remains a future gate.
- `manual_utf8.png` and `noa_utf8.png` were rendered from production-generated Korean text and inspected: manual common guidance only, assisted arrow and C/R labels, no clipping. These are synthetic pygame surfaces, not live road screenshots. Initial `manual.png`/`noa.png` captured a PowerShell-stdin fixture encoding error; they are retained but are not the accepted render evidence.

### CSV reconstruction actually opened

`research_events_SYNTHETIC-P5-CONFIRM.csv` in the final evidence directory contains a clearly labeled synthetic timeline using the real coordinator, logger and ledger reader. It has 8 data rows, including assignment and validation-origin metadata.

| Event | Synthetic session seconds |
|---|---:|
| Internal draft | 0.100 |
| Presented t0, noa_state_t0=NOA_ACTIVE | 0.200 |
| Confirm receipt tD | 0.300 |
| Confirm decision commit | 0.310 |
| Observed lateral movement | 0.400 |
| Vehicle-center boundary tL | 0.500 |
| Observed EXIT outcome | 0.600 |

The reader reconstructs `CONFIRM / EXIT`, with t0=200000000 ns and tL=500000000 ns. Unknown indicator/steering and CARLA frame/time are null/blank rather than fabricated. These are NOT human response times, vehicle motion or actual screen submission measurements; real-view submission semantics are established by separate pygame integration tests.

Field mapping: participant/run/module/session seconds remain common CSV columns; lc_event_id, frozen noa_state_t0, decision and event-specific host timestamps reside in JSON event_value. Convert payload host ns with the assignment row's common host origin to obtain session time. Preserve decision receipt and commit separately. Row order, event_type and source frame fields permit reconstruction of gaps/interruption instead of substituting success or zero. The fixture's inherited assignment route label is a fixture label; its P5 route_id/version are explicitly carried in the event payload.

## Remaining concerns and next work

- **Not final P5 experiment readiness.** Dynamic tracking over the blended path, legal-boundary crossing under the actual vehicle footprint, live display/input latency, exit and missed-route continuation under control, and every manual/NoA live case remain unverified.
- The 20 s cap at 10 km/h covers at most about 55.6 m, while the lane-change reference is approximately 75 m and normal guidance starts 1 km upstream. Use separate pre-positioned development runs after environment readiness, not longer duration, faster speed, teleport during driving or loosened steering limits.
- Research owner must confirm final module-to-route assignments, initial lane/number of moves, timing relative to module length/hazard, response deadline/late-response policy, physical buttons, manual tD proxy, onset/tL/completion criteria and mode-change/recommendation policy. The current 5 s/C/R/cancellation choices are explicit development behavior.
- M2 Manual intermediate activation policy remains unresolved; no new lockout was added. Final P3 button/thresholds and remaining brake/reactivation/steering/M2-initial-ON live/physical checks remain pending.
- P2 mirrors/AOI/final screen/performance, P6–P8, traffic/hazard, SuRT/Tobii and 55-minute orchestration are not completed by this task.
- Current routes include an auxiliary fourth lane and have not been approved as the professor's final three-lane road. Exported graph continuity is not dynamic trajectory validation.
- **Next single task:** with the operator restoring (not minimizing) the editor while Play remains active, measure current cadence and perform a separately positioned, bounded P5 confirmation/cancellation motion validation on V3, retaining unique CSV and source hashes. Do not proceed to active control if cadence remains unsuitable.
