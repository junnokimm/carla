# P8 P5 lane-change and common-loop diagnosis

## Scope

Normal MANUAL/NoA acceleration after the sticky-control repair was confirmed by
the user. This work preserves `sticky_control=true`, checked manual/preparation/
shutdown batches, `due_tick_cue=False`, TM defaults, driver priority, existing
P5 paths, P6/P7 implementation and participant-readiness blocks. No CARLA vehicle
was spawned or driven, and no native source or wheel was changed here.

Three hypotheses were checked separately: common client-loop/RPC cost rather
than TM-only cost; Python progress/topology evidence errors; native force-request
consumption followed by an internal gate or missing cached adjacency. The first
two have bounded source/log/offline evidence. The exact native non-entry cause
remains UNVERIFIED.

## Actual run evidence

Run: `P8-STICKY-2c168872845147619faf2e1076a7337a` under `data/p8_operator`.
The matching research-events CSV is the source, not a different lane-change run.
Original rows and timestamps have not been modified.

| CSV line | Session seconds | Observation |
|---|---:|---|
| 11 | 4.750 | Patched extension SHA256 `636D249A...F3CA471`, Local process PID20984, TM port8000 attestation |
| 646 | 61.594 | LEFT request `lane-request:2163704671000000:2` submitted, generation7 |
| 650/651 | 61.781 | IN_PROGRESS despite source aligned; source error +0.002455m, target error +3.502455m, target not entered |
| 750/751 | 65.750 | GEOMETRY_CONTINUITY_UNAVAILABLE; crossing-inside frame absent |
| 752 | 65.750 observation | Raw traffic observation at frame46148385 is road1184/lane-2, rather than source road39 |
| 1974 | 145.813 | LEFT request `lane-request:2163788906000000:5` submitted, generation10 |
| 1981/1982 | 146.063 | IN_PROGRESS while source aligned; source error -0.035554m, target error +3.464453m |
| 2039/2040 | 147.906 | GEOMETRY_CONTINUITY_UNAVAILABLE, target still not entered |
| 2041 | same failing frame | Ego still road41/lane-2; this is not evidence that the ego road changed |

The first failure is consistent with the exact-identity guard rejecting the
observed road39 -> road1184 transition. The second can involve lost/changed adjacent
geometry, but the old failure payload discarded the relevant raw neighbor fields;
its specific predicate cannot be reconstructed from the existing record. Lane ID
remaining -2 does not prove road/section or target-lane continuity.

The logged attestation and submission establish the selected process/library and
Python API submission path. They do not establish which worker cycle consumed the
force request, whether TM found the requested SimpleWaypoint, or physical motion.

## Measured common cost and bounded optimization

Read-only aggregation of 4,453 `traffic_light_observation` records used
`actor_observation_completed_host_monotonic_ns - actor_observation_started_host_monotonic_ns`.

| Committed TM authority / actor observation | Count | Mean ms | Max ms |
|---|---:|---:|---:|
| false (normal MANUAL path), no actor, not at light | 717 | 32.591353 | 109 |
| true (NOA_ACTIVE), no actor, not at light | 3214 | 32.888612 | 141 |
| true, actor present, at light | 522 | 0.415709 | 31 |

These are complete actor-observation spans, not isolated getter timings and not
display FPS. The cost is present in both control modes. It is not evidence that
manual batch delivery alone causes the common slowdown.

Local CARLA source explains an avoidable lookup:

- `LibCarla/source/carla/client/Vehicle.cpp:130`: the affecting-light flag is cached.
- `Vehicle.cpp:134`: GetTrafficLight resolves the cached actor ID through World.
- `client/World.cpp:107`: GetActor resolves the actor description.
- `client/detail/Episode.cpp:113`: a missing actor description invokes GetActorsById;
  an absent actor is not inserted into the cache, so a subsequent lookup repeats it.

The Python observer now skips `get_traffic_light()` when the cached affecting-light
flag is false. It still records the observation and cached signal state. Skipped
actor information is explicitly `NOT_QUERIED_NOT_AT_LIGHT`, with null actor fields
and UNKNOWN relations, not a fabricated successful actor lookup. The true branch
retains its existing actor/state/waypoint observations. No traffic-light or TM
control policy changes.

Expected effect: one unnecessary actor lookup avoided per no-light observation.
The measured ~32ms spans identify an affected cost, but do not establish a precise
FPS increase. Actual improvement, other bottlenecks and instrumentation overhead
require a subsequent equivalent P8 run. In particular, map parsing is not blamed:
CARLA 0.9.16 Simulator::GetCurrentMap already caches the map until it changes.

## Performance evidence in the same P8 program

The operator enables existing camera/performance diagnostics without changing the
camera composition, resolutions, sensor rates, HUD/mirror layout or 60FPS limiter.
One `performance_summary` event is written through the common event queue per
viewer run, including partial/error runs when the logger remains writable.

Distinct quantities:

| Quantity | New summary field / meaning |
|---|---|
| CARLA World cadence | `cadence.world_frames_per_wall_second`, and separately `world_frames_per_sim_second` |
| Pygame display cadence | `cadence.completed_display_flips_per_wall_second` |
| Driver-loop cadence | `cadence.completed_driver_loops_per_wall_second` |
| Sensor arrival | `camera.active_drive_window.roles.<role>.callbacks_per_wall_second` and host arrival intervals |

World frame advancement is not Unreal editor/render/GPU FPS. Display flips are
not sensor arrivals or photon timing. Existing logs do not contain all four
measurements; telemetry row cadence must not be relabeled as display FPS.

Operation counters include mean/max wall duration and counts for input collection,
input observer, scheduler, checked manual batch RPC wait, post-control, snapshot/
actor/control/light getters, waypoint and neighbor queries, traffic-light lookup,
JSON serialization and event/telemetry writing. Render diagnostics retain camera
callback duration, conversion, resize, mirror flip, blit, draw, display.flip and
limiter spans. Operation and render timing are partitioned by canonical control
mode at operation completion. Overlapping spans are not additive.

The operation window surrounds viewer.run; preparation and shutdown costs are not
folded into active-loop operation means. Callback arrival windows are explicit.
Unavailable timing values remain null, not measured zero. Summary serialization/
writing is excluded from its own captured counters. Secondary summary failures
must not replace the original driving error. Performance counters are in-memory;
no callback thread writes research files and no per-frame summary writer is added.
Failed timed operations retain their elapsed spans, but failed display flips and
incomplete loops do not count as completed cadence. Invalid World frame/time
provenance yields PARTIAL with null World-derived rates; valid host-only rates
remain separate. Legacy `operation_timing()` retains its lifetime scope, and the
older smoke-report elapsed interval includes setup outside the viewer window;
those similarly named legacy rates must not be equated with this active summary.

## Truthful progress and safe uncertain-request recovery

- Millimeter signed motion and sustained pre-boundary motion remain raw OBSERVED
  geometry; they no longer imply IN_PROGRESS.
- IN_PROGRESS requires an observed outside-to-inside target corridor transition,
  using the existing lane half-width, together with requested-direction motion.
- Completion still requires the requested target identity, center/heading/bounding
  box fit and observed boundary evidence. A skipped-frame sample may go directly
  to COMPLETED; no intermediate timestamp is invented.
- Road references follow the observed road heading; the original submission
  tangent is not used as the entire future road.
- Geometry failure retains the first reason and logs expected source/target plus
  actual current/left/right identities, raw position, heading and acquisition frame.
  Invalid numeric observations are null; unproved reconstructed geometry stays null.
- UNVERIFIED is not converted into COMPLETED and does not authorize another force.
  Rejected requests include the active request ID, latched reason, unknown physical
  outcome and `recovery_inputs=N_OR_BRAKE_OR_STEER`.
- Before deregistration, submission is blocked and the old generation invalidated;
  native-flight metadata and the Python tracker are retained until deregistration
  returns successfully. A failed acknowledgment, including finalization, cannot
  silently release that lock.
- Safe recovery is explicit existing driver disengagement (N/brake/steer), followed
  by normal gated reactivation and a fresh indicator OFF-to-ON edge. Signals are
  not automatically cleared on failure. No retry, arbitrary timeout, automatic NoA
  disengagement or lane-ID-only rebinding was introduced.

This deliberately does not infer continuity across every road/section boundary.
That requires a validated unique source/target topology correspondence, which
the fixed identities and existing run evidence do not supply.

## Why native non-entry remains UNVERIFIED

The preserved native code consumes/removes the force request in
`Parameters::GetForceLaneChange` before LocalizationStage checks buffer-front
junction, previous-change cooldown and available cached adjacent SimpleWaypoint.
Python map eligibility is not proof of that cached TM neighbor. When a valid
change-over point is selected, the TM buffer is replaced and MotionPlanStage uses
it; no private Python steering controller is involved.

Any of these gates can explain a returned call without target entry, but no
per-request consume/assignment/drop receipt is exposed by the current binary.
LEFT=False / RIGHT=True is preserved; direction reversal is not used as a workaround.

We did not add active `get_next_action` / `get_all_actions` polling. Their local
implementation reads worker-owned buffers without the registration mutex, and
absence of a lane-change action cannot distinguish queued, consumed-and-dropped
or no-longer-assigned requests.

If approved separately, the next native diagnostic change should provide an
actor/request-token receipt with QUEUED, CONSUMED, ASSIGNED or DROPPED, exact gate
reason and worker frame, exposed through a synchronized immutable receipt getter.
Potential files are Parameters, LocalizationStage, TrafficManagerLocal and the
TM/Python receipt binding. It would require a new isolated client wheel and
explicit runtime hash approval, not an unapproved replacement of the existing
wheel. No such native change or build was performed here.

## Preserved boundaries

Source of Truth, raw logs, original environments, approved wheel, native patch,
normal sticky/batch acceleration path, default speed, driver override, P5 exit
observations and P6/P7 contracts remain. A preexisting untracked debug journal
was accidentally deleted by a worker, then restored byte-for-byte using the
task-start snapshot; Git blob `0283e716806512a9c98340fd980f3d9b13c94172` matched.

Physical lane-change success, complete P5 exit/rejoin, post-change screen FPS and
P7 traffic safety remain UNVERIFIED. Participant readiness remains blocked.
