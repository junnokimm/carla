# V5-P7 Traffic + Hazard: development foundation

## Authority, status and exclusions

`CARLA_Experiment_Design_v5_Source_of_Truth.md` sections 5, 7.5, 8 and 11 are
authoritative. This is a **software foundation and bounded development smoke**,
not P7 completion, participant authorization, or a validated hazard scenario.

| Status | Current scope |
|---|---|
| IMPLEMENTED | Typed hazard state machine, role-scoped actors, station trigger, response/TTC records, seeded traffic selection, exclusive occurrence claims, bounded dry/live entrypoints |
| SOFTWARE TESTED | Fake-CARLA actor lifecycle and full adapter loop, raw response edges, undefined TTC cases, common P4 CSV context, no ego actuation, Module 1 exclusion and Module 2 once-only behavior |
| DEV PARAMETER | Every value in `config/p7_dev_static_smoke.json`, kinematic actor motion and operational guards |
| LIVE VALIDATION REQUIRED | Installed asset availability, real camera occlusion/reveal, ground placement, motion quality, collision mesh/sensor behavior, cleanup and renderer timing |
| TBD BY PILOT | Final density, trigger station/distance/TTC, positions, obstacle model/size, lead speed/profile, meaningful response thresholds and rescue boundaries |
| IMPLEMENTED OFFLINE | P8 duration-boundary continuation keeps the same viewer input/control/render loop alive before pygame teardown, invokes this `HazardScenario` with injected geometry/actors, and uses common FINAL_HAZARD logging and owned cleanup |
| BLOCKED | Participant/moving-ego physical backend, authoritative first-visible-frame measurement, approved final rescue policy, and production P8 authorization |

No CARLA server connection/live run, Unreal Editor interaction, Tobii hardware run,
or browser/Playwright task was performed by this final offline validation. The full
offline suite passed 971 tests on 2026-10-08. P7 changes did not alter P6 approval
gates, TABLET AOI, or P1-P5 gains/caps. Separate P6 work revised clock-fit numerical
conditioning while timing and AOI approvals remain blocked.

## Architecture and ownership

```text
Existing P4 StudyRunContext / SegmentContext / SessionClock / TimestampEnvelope
    -> HazardEventLogger -> existing ResearchCsvLogger

TrafficScenario -> seeded candidate selection -> owned TRAFFIC actors
HazardScenario  -> Module/phase + durable occurrence gate
               -> predefined station trigger
               -> HazardActors port -> owned LEAD + CARGO actors
Raw DriverInput + world observations -> response / clearance / TTC metrics
```

`src/p7/` owns this implementation. It reuses `PlanarPose`, lane-relative geometry
and `RoutePoint` value objects, **not ego/P5 lane-change execution**. The existing
DriverView read-only preview is reused with the current cockpit transform/FOV and
mirror layout (+480 rear offset). No controller, camera definition, P5 route,
assignment schema, or production NoA state semantics are modified.

The hazard port has no ego brake/steer/throttle method. Actor registration rejects
the ego ID before mutation. Only newly owned actor IDs can be moved or destroyed.
Generic traffic cleanup is role-scoped and cannot remove lead/cargo or foreign
vehicles. The collision sensor is read-only and attached to ego; it is owned and
removed separately. DriverView owns/cleans its own cameras.

## Hazard state machine and eligibility

```text
IDLE -> ARMED -> TRIGGERED -> LEAD_MOVING_ASIDE
     -> HAZARD_REVEALED -> RESPONDED (if a new raw response is observed)
     -> COMPLETED
Any active stage -> ABORTED on invalidity/failure
```

- Module 1 does not reserve, spawn or run a hazard sequence.
- Module 2 must be in `FINAL_HAZARD_ASSESSMENT`; ordinary Module 2 attention phase
  cannot arm. P8 now connects the nominal 13-minute module's pre-shutdown handoff to
  an injected loop; the default operator has no approved plan and remains blocked.
- The DEV command explicitly shortcuts directly to the final-hazard phase and
  records `DEV_ONLY_NOT_A_13_MINUTE_MODULE_RUN`.
- `OccurrenceRegistry` atomically creates an exclusive participant-keyed claim
  in a **shared store**, default `data/p7_occurrences`. A new run ID does not reset
  a claim for the same participant. An attempted setup after reservation consumes
  the claim even if setup fails, preventing automatic relocation/re-exposure.
- The DEV CLI only generates `DEV-P7-<unique-run-id>` identities, so fresh development
  runs are repeatable without weakening the same-participant once-only contract.
  Do not use DEV identities as actual participant IDs. A future study must use
  one authoritative fixed occurrence-store location; deleting/changing the store
  is not an approved reset procedure.

### Trigger semantics

`EGO_ROUTE_STATION` is the domain's default research-oriented trigger. It is tested
in software, but moving study operation is not enabled by the live CLI.

The supplied static smoke config explicitly selects `DEV_LEAD_ROUTE_STATION`:
the ego is parked while the lead reaches station 22 m in the preflighted corridor.
This is a development substitute for testing the reveal sequence, not the final
participant trigger. Trigger source, reason, threshold and observed station are
logged. Neither trigger is based on elapsed wall time, AOI, tablet attention,
eye validity, or any other gaze value. The observation contract has no gaze input.

Both triggers use a local station frame tied to the observed road waypoint, not
a guessed OpenDRIVE `s` direction. The short corridor must be straight, near-level,
non-junction, on a consistent road/section/lane, with an unambiguous continuation.
The lead needs a permitted same-direction right Driving lane throughout the path.
Invalid/already-passed trigger locations, branches and incompatible geometry fail
closed. No automatic alternate road, target lane or trigger location is selected.

## Lead, cargo and reveal

The official [CARLA 0.9.16 blueprint list](https://carla.readthedocs.io/en/0.9.16/bp_library/)
contains `static.prop.box01`, `box02`, `box03` and the selected vehicle blueprints.
This confirms documented candidates, **not their presence in this particular server**.
The runtime checks the actual blueprint library. No editor asset work is required.

- Lead candidate: `vehicle.mercedes.coupe_2020`.
- Cargo candidate: `static.prop.box01`, stationary and inanimate.
- Only explicit box01/02/03 candidates are accepted by this DEV config boundary.
  Walker/person actors cannot be used as cargo.
- Cargo is placed on the sampled road using its actual bounding-box height and
  then frozen. Position, orientation and speed are monitored for unexpected drift.
- Lead/background vehicles are kinematic: physics is disabled **on these owned
  NPCs only**, and poses follow simulation-time paths. This trades physical realism
  for explicit, reproducible DEV motion. It is not a physics-validated steering
  controller and does not use Traffic Manager or ego NoA lane-changing logic.
- The lead uses a quintic lateral blend with zero endpoint lateral rate. Start,
  observed lateral onset, target lane and completion are distinct events.

Before arming, projected bounding rectangles from the current cockpit reference
must place the cargo rectangle within the lead rectangle. Setup poses, cargo
extent and projected rectangles are logged. Pretrigger lateral motion or full
geometric clearance aborts. Reveal is recorded when the projected bounds become
disjoint after trigger.

The live adapter reads and stores each ego/lead/cargo **local** `carla.BoundingBox`
once during successful hazard actor preparation. The stored box includes its local
center, local rotation and extent. Every observation still reads current actor
transforms, locations, rotations, velocities and alive state, then recomputes world
vertices from the stored local box and current transform. This assumes an actor's
mesh/scale/local bounding-box geometry is fixed for that actor's P7 lifetime; P7 has
no in-run mesh, scale or local-box mutation path. The cache belongs to the per-run
`CarlaHazardActors` instance, is not published on failed setup, and is not global or
reused by a new run/world. Generic traffic is not cached: spawn height uses one
local-box read per traffic actor and retains only the derived lift.

**This is a geometric proxy, not rendered visibility.** A vehicle bounding box
includes transparent/empty regions and does not prove pixel occlusion. `reveal_t`
is marked `PROJECTED_BOUNDS_DISJOINT_CANDIDATE`; `hazard_visible_t` stays null.
Actual first-visible camera/display frame instrumentation and human review remain
required. Do not analyze these DEV latencies as physical response latencies.

If the required cargo blueprint or usable bounding box is absent, the run aborts
and cleans owned actors. Fallback is an **operator-reviewed config change** to an
available allowed box candidate, or a documented asset blocker. There is no silent
substitution, pedestrian fallback, alternate location retry, or success claim.

## Traffic foundation

- Enable/disable, isolated random seed, count, blueprint, speed and ego exclusion
  are configurable. Count is a DEV count, not a final density.
- Map spawn candidates are sorted before seeded selection; input enumeration order
  does not alter the selected IDs. Candidate preparation scans the full finite map list.
- Invalid paths are logged/rejected during candidate preparation. Once selected,
  a failed actor spawn aborts rather than secretly relocating the actor.
- The entire sampled trajectory avoids the reserved hazard corridor and configured
  protected zones, with candidate spacing and ego exclusion.
- The same config/seed policy is used in Module 1 and Module 2; no road, target ego
  speed or route guidance is changed to create a condition difference.
- `protected_zones` can reserve P5/exit regions. The example is empty and logs that
  fact; it is **not a claim that actual P5 geometry has been cleared**. The domain
  also aborts for an authoritative `p5_active=True` observation. The static smoke
  assumes no P5/driving owner is operating ego, rather than pretending to observe
  that owner's internal state.

Seeded selection and pose profiles are reproducible given the same map, config and
simulation timestamps. Asynchronous engine/render timing, available spawn slots and
mesh variants are not claimed to be bitwise deterministic.

## Measurements and time semantics

Every event uses existing P4 common CSV fields: participant/run/module/phase,
session elapsed time, host monotonic/UTC, and CARLA frame/time when available.
Setup events timestamp orchestration observations/requests, not undocumented exact
server execution instants. Collision payloads retain the sensor frame/time in
addition to their host observation timestamp. Cleanup may have host-only time.

Raw samples are logged from ARMED onward, including lead/ego station, lead lateral
offset, target lane, clear/completion proxies, driver input and source, speed,
clearance, relative closing speed and TTC. Pre-existing brake/steer is not relabeled
as a new post-reveal response: release and a new edge are required. Unknown input
is not replaced with applied NoA/vehicle controls.

- Raw brake onset: sampled zero-to-positive service brake edge.
- Raw steering onset: sampled zero-to-nonzero steering edge.
- First raw response and its latency use geometric reveal as an explicit DEV
  reference. Separate candidate thresholds produce candidate response timing.
- Final `first_meaningful_driver_response_t` remains null/TBD.
- The static live smoke reads existing `DriverInput` keyboard semantics but **does
  not apply them to ego**. Its source is `DEV_KEYBOARD_INTENT_NOT_APPLIED_TO_EGO`.
  S/A/D may exercise the logging pipeline, not validate braking/steering dynamics.
- Existing ResearchCsvLogger telemetry separately records actual applied ego
  controls; those are not inferred to be raw participant input.

Distance/minimum distance are signed **world-bounding-box longitudinal clearance**,
not an unlabeled Euclidean center distance. Local bounding-box offsets and world
rotation are included through world vertices. TTC is the supported constant-velocity
longitudinal clearance model, only for verified collision-course geometry and
positive closing speed. Nonclosing, nonpositive clearance, nonfinite inputs,
noncollision course, or unverified lateral motion yield null with a reason. The
live observer does not invent longitudinal TTC during lateral relative motion.
`ttc_at_response`, minimum TTC and minimum clearance are retained when defined.
For the parked-ego smoke, null TTC with `NOT_CLOSING` is expected.

### Timing evidence boundary

The completed development run at
`data/p7_development/p7-retest-20261008T075051335Z/` observed two traffic actors,
a parked ego, real occlusion/right movement/reveal, 34 telemetry samples, 344 ms
median telemetry spacing, 2.92 Hz observation rate, 29.17 Hz world rate and a
10-frame observation delta. It did not record display FPS. A separate post-run,
no-camera measurement observed `actor.bounding_box` at 10.003 ms median and
10.938 ms maximum with 10 frame advances for 10 reads; transform reads reached
0.007 ms maximum with zero frame advances. Those measurements were not collected
inside the completed run, so they do not establish an in-run call cost, speedup,
display FPS or P7 completion. The cache change has offline count/geometry evidence
only until the unchanged live smoke below is repeated under controlled conditions.

## No warnings, no intervention, and safety gap

There is no TOR, collision-warning event/HMI, hazard banner, automatic ego braking,
automatic ego steering, autopilot toggle or ego transform command in P7.
The normal DriverView imagery/HUD is reused; no hazard-specific prompt is added.

Actor loss, obstacle drift, blocked lead path, route invalidity, P5 conflict,
pretrigger reveal, stale world/cameras and failed setup abort the development run.
Collision/road departure additionally produce `experiment_termination_requested`.
P7 **does not fulfill that request by writing ego controls**.

V5's final automatic rescue must be bound to the existing primary ego/session owner
after its criteria are agreed. That binding is BLOCKED/P8 work, not silently claimed
complete here. Moving/participant live operation is therefore not enabled. Aborting
the static tool removes its own actors; it does not brake or destroy the external ego.

The P8 offline execution seam accepts a separately injected safety owner. A P7
`termination_requested` produces `REQUESTED`, followed by either
`RETURNED_NOT_PHYSICAL_SUCCESS` or `FAILED`. Return from that command is deliberately
not recorded as physical-stop success. No production safety owner or road boundary is
defined by this seam.

## P8 execution-loop contract

`src/p8/operator_hazard_contracts.py` defines the explicit plan, approval provenance,
backend, resources, cleanup evidence, and safety-owner contracts. `src/p8/operator_hazard.py`
drives the existing state machine. It does not call the standalone DEV P7 program.

- Default `OperatorRuntimeConfig.hazard_execution=None` means no scenario execution.
- An injected plan identifies policy source, approval reference, and backend-readiness
  source. There is no physical execution scope: plans are accepted only in
  `DEV_REHEARSAL` with an injected test client, injected viewer, and injected input.
- The backend receives the same live `ResearchNoASession`, world, Ego, viewer/sensor
  owner, control bundle, persistence, logger, and clock retained from Module 2.
- There is no loop timeout, time/station conjunction, lead path/speed default, response
  threshold promotion, rescue boundary, or 300-second observation cap in P8.
- The backend supplies geometry only. The continuation combines it with the actual
  `DriverInput` collected by the existing viewer frame and the timestamp captured by
  existing post-control persistence, then drives `HazardScenario.arm/update`.
- Persistence flushes M2 at the 780-second boundary, rebinds its segment and
  `ResearchEventRecorder`, and captures exactly once per continued frame. Actual
  first-visible frame stays null.
- Backend cleanup owns only its Lead/Cargo/sensors. Normal session shutdown still owns
  viewer and Ego, preserving one cleanup path for each resource.

Offline tests use `OwnedActors` with fake Lead/Cargo and an injected fake observation
backend through a real fake-world `ResearchNoASession`. This proves lifecycle plumbing,
not kinematic or physical moving-lead suitability.

## DEV parameters, not final research values

| Parameter | Example DEV value |
|---|---:|
| Traffic seed / count | 7 / 2 |
| Traffic speed / ego exclusion | 2 m/s / 25 m |
| Lead initial station / trigger station | 12 m / 22 m |
| Cargo station | 45 m |
| Lead speed / lane-change duration | 3 m/s / 3 s |
| Post-reveal observation | 5 s |
| Lateral-onset candidate | 0.05 m |
| Brake / steer candidate thresholds | 0.1 / 0.05 |
| Corridor length / sampling | 90 m / 2 m |

Additional operational DEV guards include 20 s maximum smoke duration, 12 maximum
traffic actors, traffic speed <=5 m/s, corridor <=200 m, straightness/pitch 3 degrees,
0.35 m centerline deviation, a 5 m lead-clearance planning margin, 6 m foreign-actor
traffic proximity, 0.15 m lead completion tolerance, and cargo drift tolerances
0.005 m / 0.01 degree / 0.005 m/s. The static observer requires ego speed <=0.05 m/s.
Camera startup/freshness limits are 5/1 s, snapshot stall/gap limits 2/0.5 s, and RPC
timeout 2 s. These protect this bounded tool; none is a final experiment rescue,
attention, meaningful-response or pass/fail threshold.

## Commands

Use the existing CARLA Python 3.12 `.venv`; no package/environment changes are needed.
Run from `C:\dev\carla`.

### Safe unattended dry run (default; no CARLA server or UI)

```powershell
& ".\.venv\Scripts\python.exe" -B -m tools.p7_smoke `
  --config "config\p7_dev_static_smoke.json" --mode dry --duration 20
```

Dry observations and timestamps are explicitly marked `DRY_FAKE_INPUTS`; twenty
virtual seconds run without waiting twenty wall seconds. They are not live evidence.
Use `--module MODULE_1` to exercise traffic-only eligibility in software.

### Tomorrow: optional read-only catalog / existing vehicle IDs

```powershell
& ".\.venv\Scripts\python.exe" -B -m tools.p7_smoke --mode catalog
```

Catalog mode is read-only: it connects only to inspect the map, available blueprints,
existing vehicle IDs and role names. With no `--ego-id` it lists candidates and exits
0 without corridor approval. To diagnose one externally owned parked vehicle, use:

```powershell
& ".\.venv\Scripts\python.exe" -B -m tools.p7_smoke --mode catalog `
  --ego-id ([int](Read-Host "Existing parked CARLA ego actor ID")) `
  --config "config\p7_dev_static_smoke.json" --host 127.0.0.1 --port 2000
```

This adds the configured corridor length and reports `COMPATIBLE` or the first
explicit failure reason. An incompatible corridor returns exit 2. Catalog mode does
not spawn, move, control, attach sensors to, or destroy any actor.

### Tomorrow: exact short static live smoke

Prerequisites: CARLA/UE4Editor Play is running and unpaused, asynchronous world;
an existing externally parked vehicle is centered on a straight, near-level road
with at least 90 m of compatible continuation and a clear permitted right lane.
No other driving/P5 owner should move ego. The tool never creates/parks ego for you.

```powershell
& ".\.venv\Scripts\python.exe" -B -m tools.p7_smoke `
  --config "config\p7_dev_static_smoke.json" `
  --mode live --ack-dev-only `
  --ego-id ([int](Read-Host "Existing parked CARLA ego actor ID")) `
  --host 127.0.0.1 --port 2000 --duration 20
```

ESC/window close/Ctrl+C ends the smoke. No server is started by this command.
The active-loop cap is 20 seconds; bounded RPC cleanup may take additional time.
An incomplete sequence, incompatible corridor, absent asset or blocked spawn is a
failure report, not permission to claim a successful live run.

### Outputs and operator checks

`data/p7_development/<unique-run-id>/` contains existing-format research event and
telemetry CSVs plus `summary.json`. Files/directories are exclusive. The shared
occurrence path is recorded; use a fresh DEV run ID for each development attempt.

Visually check, without approving participant realism:

1. Cargo is initially genuinely hidden by the lead, rather than merely inside its
   projected bounding rectangle; no early visible edge/window transparency leak.
2. Lead moves forward and smoothly into the correct clear adjacent lane; no
   unintended teleport/jitter, floating or underground geometry.
3. Cargo remains fixed and becomes visible; compare the view with the geometry
   event but do not equate them automatically.
4. Traffic count/placement/motion is plausible enough for the next DEV test and
   does not enter the reserved hazard/exit regions.
5. Ego stays under its original ownership; no hazard warning/TOR/brake/steer occurs.
6. Exit leaves ego/foreign actors intact and removes all owned actors/sensors.

Return summary + event CSV + telemetry CSV and operator notes/screenshots if useful.
If the run blocks, return the exact reason; do not bypass guards or relabel missing
assets/occlusion failures as success. Collision mesh behavior, moving-driver response,
authoritative display timing, final P5 protection regions and rescue-owner binding
need separate controlled follow-up before participant use.
