# P8 P5 topology-continuous lane-change observation

## Scope and status

This change fixes only the P5/general lane-change observer. It does not change
Traffic Manager execution, vehicle control, assignment, timing, hardware, exit
routing, P6, or P7. No CARLA connection, server startup, actor operation, or live
driving was performed. Physical success remains **UNVERIFIED** until a user run.

## Original evidence reverified

Source artifacts:

- `data/p8_operator/P8-7e5e430633494a1b86a87639c483b0d0/research_events_P8-7e5e430633494a1b86a87639c483b0d0.csv`
- `data/p8_operator/P8-7e5e430633494a1b86a87639c483b0d0/research_telemetry_P8-7e5e430633494a1b86a87639c483b0d0.csv`
- `data/p8_operator/P8-7e5e430633494a1b86a87639c483b0d0/session_manifest.json`

The CSV was parsed with Python's standard `csv` and `json` modules, without
Pandas. The original event data establishes:

| Request | Request evidence | Boundary failure evidence |
|---|---|---|
| `lane-request:5572390000000:1` | LEFT at session 2521.000; baseline `(50,0,-2)` to `(50,0,-1)`; pose `(148.9625244140625,-371.23004150390625,-3.8934645652770996 deg)` | Submitted at 2521.094/frame 351377. At 2522.063/frame 351424 the coherent current/left/right identities were `(42,0,-2)`, `(42,0,-1)`, `(42,0,-3)` at pose `(160.85845947265625,-372.72357177734375,-8.710611343383789 deg)`, but status became `UNVERIFIED:GEOMETRY_CONTINUITY_UNAVAILABLE`. Telemetry first recorded lane `-1` later at 2522.422/frame 351439 with the left signal still on. |
| `lane-request:5634406000000:4` | LEFT at session 2583.016; baseline `(38,0,-2)` to `(38,0,-1)`; pose `(159.02932739257812,14.523477554321289,-178.9929656982422 deg)` | Submitted at 2583.094/frame 354035. At 2584.688/frame 354106 the coherent current/left/right identities were `(1091,0,-2)`, `(1091,0,-1)`, `(1091,0,-3)` at pose `(127.56879425048828,13.964872360229492,-178.96539306640625 deg)`, but the same unverified status latched. |

The four subsequent `REJECTED_ONGOING` records were independently confirmed:

- 2532.922: `lane-request:5584312000000:2`, RIGHT, blocked by request `...:1`.
- 2541.110: `lane-request:5592500000000:3`, RIGHT, blocked by request `...:1`.
- 2586.985: `lane-request:5638375000000:5`, LEFT, blocked by request `...:4`.
- 2590.750: `lane-request:5642140000000:6`, RIGHT, blocked by request `...:4`.

The manifest labels this development run `ACTUAL_BOUNDED_OPERATOR`, while the
CSV completeness entries remain `DEV_STUB`, `NOT_HARDWARE_VALIDATED`, and
`software_artifacts_complete=false`. The evidence proves the observer defect,
not a physically validated completion.

## Root cause and design

`reconstruct_lane_change_geometry()` accepted only the immutable request-time
source and target `(road_id, section_id, lane_id)` values. A normal connected
road or section boundary therefore returned no geometry even when the current,
left, and right lanes changed coherently as a pair. `LaneChangeTracker` then
latched `GEOMETRY_CONTINUITY_UNAVAILABLE`, retained the pending native request,
and correctly rejected later requests as ongoing.

The fix captures one immutable identity graph from `carla.Map.get_topology()`
when `CarlaLanePairTopology` is built. Graph nodes are exact
`(road_id, section_id, lane_id)` tuples. Directed edges are the exact topology
pairs returned by CARLA; no XY distance, waypoint sampling interval, timeout,
retry, time-based success, or ID-only mapping participates in continuity proof.

For each observation, the map waypoint is resolved once before geometry is
reconstructed. Source and target identities then advance as a pair. Every edge
must have one source successor, one target successor, and singleton reverse
predecessors that point back to the prior pair. Every visited node must be
non-junction and preserve reciprocal immediate lateral adjacency in the requested
direction. A fork, merge, cycle before the observed pair, missing edge, missing or
wrong lateral neighbor, unavailable lookup, or captured identity mismatch fails
closed. A validated zero-edge pair is accepted as the current topology context,
but does not itself prove physical movement; the unchanged geometric completion
rules provide that proof.

This matches native `Map::GenerateTopology()` semantics in the authorized
read-only CARLA source: it enumerates drivable lanes and emits their exact
`GetSuccessors()` edges. The graph is frozen at request capture so later mutable
map/fake state cannot rewrite the proof basis. Submission-time lookup loss is
recorded immediately as `UNVERIFIED:SUBMISSION_TOPOLOGY_UNAVAILABLE` rather than
being silently discarded.

Native topology also emits a terminal lane's start/end pair even when both
waypoints have the same identity. Those records remain graph node samples so
junction and lateral-adjacency metadata are retained, but they are not inserted
as directed self-edges. Thus a legitimate `A->B` transition followed by terminal
`B->B` metadata has predecessor set `{A}` rather than the false merge `{A,B}`.

After topology proof, the existing completion rules are unchanged: requested
lateral movement, target-boundary outside-to-inside transition, actual target
entry, vehicle bounding-box center tolerance, and heading alignment are all still
required. The existing per-frame vehicle lateral-travel check remains independent
of OpenDRIVE road/section identity changes.

## Regression evidence

Red before the graph implementation:

```text
20 failed, 3 passed
Failures exposed Waypoint.next() dependence, missing capture identity checks,
silent submission lookup loss, and curved multi-road traversal failure.
```

Green focused graph and lane-change regression evidence:

```text
24 passed in 0.26s
85 passed in 0.47s
1570 passed in 28.86s
Ruff: All checks passed
Ruff format: 11 files already formatted
```

The focused tests cover:

- synthetic LEFT and RIGHT graph fixtures using the logged `50->42` and
  `38->1091` identities, plus synthetic section and multi-edge transitions;
- serverless `carla.Map("highway_loop_3lane", xodr)` LEFT and RIGHT traversal
  over curved roads `1->2->3`, with all 24 exact topology edges checked;
- completion release, selected-signal auto-off, and a fresh opposite-direction
  follow-up request;
- fail-closed intermediate junction, dead fork, merge, wrong/missing adjacency,
  disconnected cycle, unavailable lookup, capture mismatch, and metadata-only
  identity changes;
- terminal source and target `A->B` plus `B->B` topology normalization without
  weakening real fork or merge rejection;
- existing wrong-side/neighbor identity, bounding-box, center, heading, movement,
  override, reactivation, stale generation, auto-off, and recovery behavior.

Python LSP diagnostics could not run because `basedpyright` is not installed and
its installation was previously declined. Runtime tests and Ruff are green.

## Changed files

- `src/vehicle/carla_lane_topology.py`
- `src/vehicle/carla_lane_graph.py`
- `src/vehicle/carla_adjacent_lanes.py`
- `src/scenario/lane_change_lifecycle.py`
- `src/scenario/lane_change_observation_results.py`
- `src/scenario/lane_change_request.py`
- `src/scenario/lane_change_request_controller.py`
- `src/scenario/research_lane_change.py`
- `src/experiment/noa_runtime.py` (map topology protocol only)
- `tests/lane_change_topology_fakes.py`
- `tests/test_noa_runtime.py`
- `tests/test_lane_change_topology_continuity.py`
- `tests/test_lane_change_topology_real_map.py`
- `tests/test_lane_change_topology_rejection.py`
- `tests/test_lane_change_execution_lifecycle.py`
- `docs/P8_P5_Topology_Continuity_Fix.md`

## Required physical validation checklist

Use the existing approved live-run environment and command; do not create a new
controller or alternate program. For at least one LEFT and one RIGHT request:

The exact user-run command, verified interpreter/hash, read-only native findings,
and required raw artifacts are in [P8_P5_Physical_Validation.md](P8_P5_Physical_Validation.md).
Final parent verification: full suite **1570 passed (28.72s)**, repository Ruff
passed, and `compileall -q src tests` passed. The approved interpreter also ran
the serverless real Map graph and synthetic LEFT/RIGHT observer driver: both
completed, released pending state, cleared the selected signal, and accepted a
fresh opposite-direction request. These remain non-physical tests.

1. Preserve the original event and telemetry CSVs and session manifest.
2. Confirm one `lane_change_request` with `disposition=REQUESTED`, exact source and
   target road/section/lane identities, request ID, baseline pose, and direction.
3. Confirm `SUBMITTED_TO_TM` with submission host time and CARLA frame.
4. At every road/section boundary, confirm coherent current and requested-side
   adjacent identities and poses continue as `OBSERVED` or `IN_PROGRESS`, with no
   `GEOMETRY_CONTINUITY_UNAVAILABLE`.
5. Confirm `IN_PROGRESS` follows real requested-direction movement and target
   corridor entry, not an ID-only transition.
6. Confirm exactly one `COMPLETED` record has target identity, source alignment
   failure, target center/heading success, outside/inside frames, and non-null
   geometry values.
7. Confirm the selected signal auto-off event occurs after completion and preserves
   the opposite blinker and other light flags.
8. Submit a second fresh request without override/recovery and confirm it is
   `REQUESTED`/`SUBMITTED_TO_TM`, not `REJECTED_ONGOING`.
9. Repeat with a deliberate manual override and reactivation to confirm stale
   generation rejection and a later fresh request remain unchanged.
10. Treat any junction, fork, merge, missing topology option, multiple topology
    options, or ID-only change as fail-closed and retain its original evidence.

Until this checklist is satisfied by actual user driving, physical completion,
signal behavior, and follow-up availability remain **UNVERIFIED**.
