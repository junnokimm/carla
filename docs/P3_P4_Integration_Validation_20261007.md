# P3/P4 integration boundary validation (2026-10-07 KST)

## Changes made

- Scope: development P3 operation / P4 session, assignment and logging only. P5 was not started. No PI/lateral law, tuning, simulation-time, prime, fallback, control bounds or single-writer change. No commits, resets, branch switches, deletions or project-wide formatting. Preexisting dirty/untracked work was retained.
- Read `docs/CARLA_Experiment_Design_v5_Source_of_Truth.md` first. This is a reconstruction, not the professor's original. No original v5 document or standalone internal test manual was found in the project. The explicit requirements in the request were used for those missing documents. No scoped AGENTS.md was found.
- HEAD: `104491ab9cd78b6cbc56b3a999568b4f9d611932`, dirty. Native runtime: Windows, Python 3.12.10, pygame 2.6.1, CARLA client 0.9.16 / simulator 294096e. The compatibility warning remains.

### R1: control before supplementary persistence

`DriverView.run()` collects research input and updates canonical interaction state, runs the scheduler/manual command, then invokes `after_control_applied()`. Auxiliary mode/light handling is also after research control. Availability refresh while MANUAL, buffered automation-event writes and telemetry capture run afterward. Active steering does not trigger the manual activation gate. No thread, second writer or asynchronous framework was added.

Native failing-first evidence initially showed the missing post-control contract; final real-view regression uses the actual runner and pygame-event collection with synthetic map 0.1s, light 0.3s, telemetry 0.2s and event-write 0.2s delays. Brake application and disengagement retain the input-receipt clock reading. This does not independently reproduce the original report's exact pre-fix 0.6s numeric trace: that value is synthetic, not CARLA data.

### R2: occurrence times and provenance

Requests are emitted before the gate, transitions after successful runtime commit. Persistence captures host timestamps immediately and defers disk writes. Events no longer inherit previous observations' CARLA frames. Queued events drain before later telemetry; the shared SessionClock still rejects backwards readings, without clamping.

Telemetry speed uses `snapshot.find(vehicle_id).get_velocity()`, like the PI observation path. The native mismatch regression used snapshot speed 18 versus getter speed 360 km/h; these are synthetic values. The retained control, transform/lane and light getters are explicitly identified by a `telemetry_schema` event as independent post-snapshot host acquisitions. Their enclosing interval is snapshot-capture completion through row host completion. The raw CARLA frame applies to snapshot speed only, not those independent getters or new events. This is interval provenance, not a per-getter sensor timestamp.

Oracle review also identified delayed shutdown timestamps. A native regression observed shutdown 1.4s versus actual commit 1.0s when pending writes were delayed. Shutdown now captures its timestamp at MANUAL commit, before brake-finally completion and pending disk writes; this regression passes.

### R3: M2 NoA start rejection

Only successful initialization reaches normal `stage_start` and viewer execution. Rejection preserves the gate and reason, raises `AutomationInitializationError`, emits `stage_start_failure`, and runs cleanup. `stage_start_attempt` is distinct from actual module start. M1 starts MANUAL; M2 Manual's later developer activation remains permitted.

Native failing-first runner evidence was a missing exception/viewer-entry rejection. Final native tests assert no normal stage_start/viewer entry on failure. A safe live injected-lane-change rejection was also executed; it is NOT an actual lane-change test.

### R4: original input error

Python 3.12.10 reproduced `DriverInput(throttle=nan)` being replaced by a contextlib `TypeError` while assigning `__traceback__`. `DriverInputError` is now a normal mutable typed exception. Native runner tests preserve the original exception, shutdown brake 0.5, viewer close once and vehicle destroy once. No live NaN input was injected.

Simultaneous initialization and logging failure initially replaced the primary error too. Initialization now uses the same primary-error-preserving flush handling as post-control cleanup. Normal logging failures still propagate and trigger safe shutdown.

## Files changed

Production:

- `src/experiment/automation_interaction.py`
- `src/experiment/automation_interaction_types.py`
- `src/scenario/research_noa_persistence.py`
- `src/scenario/research_noa_session.py`
- `src/scenario/research_noa_lifecycle.py`
- `src/scenario/research_noa_runtime.py`
- `src/scenario/research_noa_types.py`
- `src/scenario/research_noa_diagnostics.py`
- `src/scenario/research_noa_smoke.py`
- `src/scenario/driver_view.py`
- `src/scenario/research_noa_view.py`
- `src/scenario/driver_hud.py`

Tests and validation artifacts:

- Updated `tests/research_noa_fakes.py` to honor the real post-control hook and snapshot interface.
- Updated `tests/test_automation_interaction_boundaries.py` for request-before-gate ordering and explicit post-control availability observation; assertions were not weakened.
- Added `tests/test_r1_r4_runtime_boundaries.py`.
- Added `tests/test_p3_p4_real_view_boundary.py`.
- Added `tests/test_p3_p4_shutdown_timing.py`.
- Added `tests/test_research_noa_hud.py`.
- Added `tools/validate_p3_p4_stationary.py` and this report.
- Preserved new live artifacts under `data/p3_p4_validation/` and render evidence under `.omo/evidence/research-noa-hud/`.

`csv_logger.py` and `session_clock.py` were inspected, not modified by this task. Their existing dirty content was retained.

## Verification results

### Native automated checks

- `.venv/Scripts/python.exe -m pytest -q`: **635 passed in 1.66s**.
- Ruff check passed for all task-touched Python files. No project-wide formatting was applied.
- `git diff --check` passed; existing CRLF conversion warnings remain.
- LSP diagnostics could not run: basedpyright is absent and installation had previously been declined. A clean type-check is NOT claimed.
- Covered actual runner cleanup, initialization failure, delayed event/telemetry work, original input error, N-key collection in real ResearchLiveDriverView.run, actual canonical HUD source, backwards clock rejection, logging errors, assignment/ID/schema protection, PI/lateral/transmission/bounds/fresh-run/reactivation regressions.
- HUD rendered MANUAL and NOA_ACTIVE with existing font/colors; no legacy autopilot activation. Images were inspected at `.omo/evidence/research-noa-hud/manual.png` and `noa-active.png`. These are synthetic render evidence, not physical button validation.
- Synthetic saved-event timeline: ON at 90–210 and 360–480 yields 240/600 = 40%. It is NOT a 600s drive or 55-minute run.
- Separate child process called os._exit(9) after writing: assignment and request rows survived without logger close. This is process-abort CSV evidence, not a live CARLA forced-shutdown trial or power-loss durability guarantee.

### Live runs and actual opened files

Batch directory:
`data/p3_p4_validation/20261006T175624Z-74afe2f3/`

1. `manual_logging_on/stdout.txt`: M2 Manual-style front-only, scripted constant brake 0.5, target 10, D 0.2, Kacc/Kbrake 0.1, Ki 0.02, throttle cap 0.4, brake cap 0.5; lateral 0.2/0.5, deadbands 0.1m/0.05rad, steering cap 0.15. Requested 6s, measured session span 6.937s; no active commands, observed speed 0, lane -4 unchanged.
2. Opened `manual_logging_on/research_telemetry_20261006T175624Z-74afe2f3-manual_logging_on.csv`: 10 data rows, speed/throttle/steer 0 and brake 0.5.
3. Opened `manual_logging_on/research_events_20261006T175624Z-74afe2f3-manual_logging_on.csv`: 7 data rows. Examples: initial_state `AVAILABLE|MANUAL` at 9.500s; stage_start at 9.828s; availability `UNAVAILABLE:DRIVER_BRAKE_ACTIVE` at 9.843s; normal stage_end at 16.093s. These are session-origin seconds, including setup before module start.
4. `manual_logging_off/stdout.txt`: preflight failed before viewer execution: gear 0 with measured speed 0.137147km/h exceeded the existing stationary prime threshold. No guard was bypassed. No valid ON/OFF paired-cost comparison was obtained. OFF intentionally has no research CSV.

Independent safe rejection directory:
`data/p3_p4_validation/rejected-start-9414bf3a/`

- `stdout.txt`: `EXPECTED_INITIALIZATION_REJECTION ... LANE_CHANGE_IN_PROGRESS`; `source_unchanged True`.
- Opened `research_events_rejected-start-9414bf3a.csv`: 8 data rows. At 11.938s: auto_request NOA_ACTIVE, availability `UNAVAILABLE:LANE_CHANGE_IN_PROGRESS`, activation_failure `LANE_CHANGE_IN_PROGRESS`, initial_state `UNAVAILABLE|MANUAL`. At 12.282s: `stage_start_failure ... FAILURE:AutomationInitializationError`. No normal stage_start, normal stage_end or successful activation.
- Opened `research_telemetry_rejected-start-9414bf3a.csv`: 1 pre-start observation, speed 0 / brake 0.5. It is not normal module exposure.
- Both live event files have host-only event times, no falsely associated CARLA frame. Both CSV pairs have matching participant/run IDs and nondecreasing within-file session times.
- Final read-only actor check found no remaining vehicles. The first batch's immediate post-destroy count of 1 was a cached/asynchronous observation, not proof of permanent leakage.
- Unique run paths, exclusive creation, existing files preserved. The first batch stopped on the OFF preflight error, so its final source_unchanged.txt was not produced. Its `source_manifest.json` was independently compared with final current source/test/tool hashes and matched. The rejection run's manifest matched too. No executable source was modified after these live runs.

### Measured low-performance boundaries

All host operation spans use perf_counter / QueryPerformanceCounter. The session duration separately uses the session monotonic clock; no cross-clock absolute subtraction is performed.

| Measurement, recording ON | Result |
|---|---:|
| Idle world before run | 3.000Hz |
| World during run | 2.739Hz |
| Research loop cadence mean / max | 735.004 / 1059.961ms |
| Complete per-iteration camera loop mean | 693.769ms |
| Input observer mean | 0.017ms |
| Post-control work mean | 344.797ms |
| Persistence light getter mean | 343.654ms |
| Persistence map getter mean | 0.053ms |
| Telemetry write including flush mean | 0.114ms |
| Event write including flush mean | 0.041ms |
| Audio block mean | 346.216ms |
| Audio light RPC subsequent-call mean / max | 389.449 / 704.586ms |
| Audio update subsequent-call mean | 0.002ms |
| Scheduler mean | 0.015ms |
| Render mean | 1.989ms |

The two loop means have different windows: cadence between consecutive loop starts excludes the incomplete last interval; camera loop timing covers every completed iteration through FPS limiter. Post-control includes availability plus event/telemetry work; light/map/write are nested components. Draw includes image preparation. Do not sum parent and child durations. There are two light-query sites, persistence and audio. The observed blocking is substantial; these data do not establish why the engine/RPC is slow or causally attribute the prior 0.99s loop entirely to P4.

Environment: Town04, asynchronous mode, rendering on, variable delta, substepping on. UE4Editor, Chrome and OpenCode processes were observed. Foreground-window API returned an empty title, so editor foreground/background was not established. Quality is UNKNOWN. No OS/GPU/editor settings were changed and no unrelated process was stopped/restarted.

## Remaining concerns

- Active live M1 OFF → ON → brake → reactivation, successful M2 NoA initial ON, and active steering were intentionally withheld in the repeatably low-cadence environment. They are automated/fake-hardware regressions only. No physical steering wheel/button/pedal validation is claimed.
- Real vehicle straddling/lane-change rejection was not live-tested. The safe live rejection used an injected flag; the geometric gate remains covered by automated tests.
- Synchronous post-control persistence still delays the next event poll. This change fixes the current iteration's priority and occurrence timestamps, not hard-real-time latency. The optional external automation_event_sink is still synchronous and was unset in live runs; a slow custom callback must not be introduced into safety runs.
- M2 Manual later activation policy is unresolved: v5 reconstruction says assigned-mode driving while common eligibility/reactivation is also stated. No new lockout was added. N is a development key; final physical button, center/heading/brake thresholds and steering-disengagement policy require research-owner confirmation.
- Operator action: establish editor foreground/background and quality, investigate the roughly 3Hz idle engine and long light RPCs in a controlled setup, verify stable cadence plus stationary prime conditions, then rerun the missing active cases at unchanged caps. Do not call an OFF-only speedup a repaired research path.
- No P5 exit/navigation/automatic lane change, mirrors, SuRT/Tobii, traffic/hazard, assignment-table generator, controller tuning, 600s live or 55-minute integration was implemented. This is not completion of P2 or the experiment.
- The 10/12 continuous two-module/rest/hazard/questionnaire flow and all four combinations with real files, and 10/13–16 internal tests, remain broader scheduled work.
