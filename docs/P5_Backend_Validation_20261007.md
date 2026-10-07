# P5 nonvisual backend validation (2026-10-07 KST)

> Intermediate backend record. Final UI integration, 728-test result, final evidence paths and limits are in `P5_Implementation_Validation_20261007.md`.

## Implemented boundary

- One versioned exit event per configured runner session, with a distinct `TRAINING` context and event ID when the same physical exit is reused.
- Internal navigation draft is separate from `mark_presented()`. The canonical `t0` and actual mode are frozen only when `ResearchExitViewBinding.mark_presented()` is called.
- C/R are development defaults and are validated against reserved driving/UI keys and the dynamic NoA key. Turn indicators are recorded but never interpreted as confirmation.
- Confirmation only arms `SwitchableRouteLaneGeometrySource`; `SimulationTimeCarlaNoAControlBackend` remains the only automatic control writer. The ordinary current-lane adapter remains the activation gate and inactive backend reference.
- Driver brake, steering, observed movement, and canonical mode changes cancel assistance without automatic resume. Manual, reject, and no-response paths never arm route control.
- P5 events share `ResearchNoAPersistence`'s chronological FIFO with automation events. P5 finish is queued before shutdown automation events and the final flush.
- Event JSON preserves null unknowns and separates input receipt, decision commit, indicator onset, steering onset, observed lateral onset, center-boundary crossing, completion, outcome, and termination reason. CARLA frame/time are attached only to post-control observation samples.

## Development route manifest

`config/town04_exit_routes_dev_v3.json` preserves the read-only Town04 geometry from validated v2 and adds explicit navigation, confirmation, and physical-maneuver envelopes. V1 remains rejected because its 8.732 m transition dogleg violates the development geometry contract; v2 remains preserved as the geometry-validation artifact.

```powershell
& ".venv\Scripts\python.exe" -m tools.export_town04_exit_routes --output config/town04_exit_routes_dev_v3.json
```

Observed client/simulator versions were CARLA API 0.9.16 and simulator `294096e`; the existing compatibility warning remained. No actor, world setting, window, or control command was changed. The world contained actors during validation, so the earlier empty-actor observation was not repeated or claimed.

- Manifest version: `town04-exits-dev-v3`
- Semantic route SHA-256: `31cce59e7a37b423af4f338674b6a7c1187145554ca1b9314428944fc05c93cc`
- Sample interval: 5 m
- Source/target envelope: lane -3 to lane -4 only
- `town04-exit-39`: 288 points, fork distance 1086.107673 m, maximum spacing 5.122495 m, maximum tangent delta 0.046724 rad; exit `39/0/-4 -> 1191/1/-2 -> 33/0/+2`; distinct through segment `1184/0/-4`
- `town04-exit-47`: 284 points, fork distance 1151.671258 m, maximum spacing 5.340620 m, maximum tangent delta 0.047380 rad; exit `47/0/-4 -> 782/1/-2 -> 34/0/+2`; distinct through segment `774/0/-4`
- Navigation begins at `fork_distance_m - 1000 m` on the matched source lane. Confirmation is permitted from that point through the last safe commit station at physical maneuver onset. `initiation_start_m`/`initiation_end_m` describe only the later physical lane-change envelope. Immediate-right paired geometry is therefore required for physical movement and tL measurement, not for upstream navigation, t0, or route arming.
- The JSON includes compressed ordered road/section/lane links, sampled XY/yaw/lane-width points, through and exit identities, and the verified semantic hash. Runtime construction also checks representative source, target, and exit anchors against the currently loaded map, including identity, driving-lane type, width, tangent, immediate-right adjacency, direction, and vehicle fit.
- Final read-only runtime validation checked every maneuver-envelope sample plus exit anchors against the current `Carla/Maps/Town04` waypoint graph. No user-owned vehicle existed, so validation used an explicitly labeled 1.90 m-wide `carla.BoundingBox` footprint fixture; no actor was spawned or controlled.

The 1000 m navigation trigger, 5 s response timeout, 0.15 m lateral-onset threshold, 0.05 steering threshold, and C/R keys are explicit development configuration, logged as `p5_exit_manifest`, and do not claim research-owner approval. No experiment speed was added or changed; the 20 s live cap remains.

## Lead-owned pygame hookup

The runner exposes `ResearchNoASession.exit_view_binding` when P5 is configured. The lead must make only these visual-loop connections:

1. Add the binding or equivalent callbacks to `ResearchDriverViewConfig`.
2. Call `presentation = binding.prepare_presentation()` and render `presentation.navigation_text`; render confirm/reject affordances only when `presentation.recommendation` is true. A `None` result means no exit presentation is available. Continue rendering the returned navigation snapshot after a decision, with its controls hidden by the presentation state.
3. Immediately after the first successful `pygame.display.flip()` containing a recommendation, call `binding.mark_presented(presentation)` exactly once with that same immutable snapshot. Stale snapshots are rejected. This captures a host occurrence timestamp and makes no photon/display-latency claim.
4. In KEYDOWN handling, compare against `ord(binding.config.confirm_key)` and `ord(binding.config.reject_key)` and set the matching `DriverInput.exit_confirm_requested` / `exit_reject_requested` for that iteration only. KEYUP/repeat/held state remains lead-owned; do not pass held booleans as fresh edges.
5. Do not map Z/X indicators to confirmation and do not call `vehicle.apply_control()` from the view binding.

Without this lead-owned hook, drafts and route observations run, but no event can acquire `t0` or accept a decision. Visual presentation and physical input validation are therefore explicitly unverified here.

## Native evidence

- Initial red: the boundary-contract suite reproduced eight ordering, lifecycle, timestamp, presentation, and route-reference failures before correction (`8 failed, 4 passed`).
- Focused lifecycle/route/runner/ledger, navigation-contract, untouched lead final-contract, Oracle blocker, and CLI-default matrix: 75 passed.
- Full native regression: 725 passed.
- Manual real-module lifecycle observed `COMPLETED / CONFIRM / EXIT` with one arm and one completion cancellation. Manual real CSV/`SessionClock` draining observed `assignment, first, second, later` for deliberately out-of-order pending timestamps, preserving equal-time enqueue order without clamping.
- Manual real v3 route-39 navigation observed `DRAFT -> CONFIRMED` at `fork - 1000 m`, froze `t0=20`, and armed route point 19 while paired target geometry was unavailable. A fresh coordinator first observed beyond the 985.983227 m confirmation cutoff remained `WAITING` with no presentation or arm.
- Ruff check passed on all P5 and touched integration files.
- Ruff format check passed on all P5 and touched integration files; `git diff --check` passed. The optional no-excuse checker is not part of the repository toolchain and could not be run through its required `uv` launcher because `uv` is unavailable. A direct invocation reported only module-size/advisory issues in the P5 state machine, tests, exporter, and pre-existing P3/P4 integration modules plus existing test-fixture typing conventions; no broad refactor was performed as part of this boundary correction.
- basedpyright LSP diagnostics were unavailable because the server is not installed and installation had previously been declined; no clean LSP/type-check claim is made.
