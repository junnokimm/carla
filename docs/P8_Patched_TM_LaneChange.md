# P8 patched TM lane-change integration

## Pilot update — 2026-10-18

`run_pilot.cmd` now loads the project-private client in `native/noa_tm/carla`
before importing CARLA. Its hash is pinned in `src/p8/noa_tm.py` and checked
again by the existing Traffic Manager ownership attestor. The original Python
environments, installed CARLA clients and diagnostic profile are unchanged.
Use this launcher for the behavior below; older direct entrypoints still load
their installed client. M2 assignment remains `--m2 1` (MANUAL) or `--m2 2` (NoA).

- Training, M1 and M2 share the same indicator request/completion observer.
  During active NoA, a fresh keyboard Z/X or wheel indicator edge requests one
  adjacent lane. Completion requires observed target-lane identity, crossing,
  center/heading and vehicle-fit evidence; it clears the selected indicator and
  retains NoA. Repeated held inputs do not request multiple lanes.
- Brake/steering/N deactivation thresholds, activation gates, manual input and
  control ownership are unchanged. A signal never overrides driver priority.
- Forced native target advancement is bounded by the existing 20 m ego-to-first-
  waypoint reset threshold. This prevents a valid lane-change plan from being
  discarded on the next native cycle. Explicit requests bypass the random-change
  cooldown; the Python executor still admits only one maneuver at a time.
- The world-wide EMPTY_REHEARSAL restriction is removed from normal submissions.
  Other module actors no longer veto all requests. Static adjacent-lane legality
  checks and existing TM collision settings remain. This is not a new gap/TTC
  controller or a guarantee of an available neighboring lane. The separate
  diagnostic preflight retains its empty-world requirement.
- Indicator edges are applied before NoA request dispatch, including sub-threshold
  brake input. A delayed signal readback waits up to one second without blocking
  the driver loop or sending duplicate force calls. Expiry records BLOCKED and
  keeps driving; a deferred P5 failure also records its existing terminal event.
- Temporary missing/invalid observation geometry can resume only against the
  original verified topology. Events retain UNVERIFIED and OBSERVATION_RESUMED;
  unrelated topology and stale activation generations cannot manufacture completion.
- Exit confirmation retains P5 correlation when accepted. An expired/unavailable
  recommendation does not swallow a normal fresh signal in NoA; it is recorded
  as GENERAL. Explicit exit rejection and driver/manual priority still take precedence.

The native regression executes the actual LocalizationStage against offline
Town04, without a server: the original library fails target retention and the
patched library passes 32 direction/speed/position cases. Python regressions
also cover input through completion with the new client and private TS-XW SDL.
Evidence is in `data/noa_tm_build/`. Live simulator motion and steering-wheel
hardware have not been exercised by this implementation task.

The older contracts below describe the previous build. In particular, their
empty-world-only admission, permanently latched temporary observation failures
and lack of signal-readback waiting are superseded for the pilot launcher.

Current FPS/progress investigation and recovery contract:
[P8_P5_FPS_Diagnosis.md](P8_P5_FPS_Diagnosis.md).

## Current contract

The only operating entrypoint remains `python -m src.p8.operator_cli` from
`C:\dev\carla`. This document supersedes the older request-only/BLOCKED execution
descriptions in the historical P8 integration documents. Source of Truth, route
manifests, original research logs, P6/P7 implementations and participant-readiness
blocks are unchanged.

General Z/X requests extend the V5 exit-only scope by explicit developer request;
research-principal approval of that expanded experimental scope remains pending.

Code and Fake-I/O verification are not CARLA physical validation. No vehicle was
spawned or driven in this implementation task. P5 as a complete exit/rejoin
feature is not declared complete.

## Protected and selected environments

- Original P8 environment: `C:\dev\carla\.venv`, unchanged.
- Native build/test environment: `C:\dev\carla-native-tm-cancel-20261012\.venv`, unchanged.
- P8 validation environment: `C:\dev\carla-native-tm-cancel-20261012\.venv-p8`.
- P8 code: the same current `C:\dev\carla` working tree, not a second implementation.
- Python: CPython 3.12 Windows x64; application dependencies use `requirements.txt`.
- Approved wheel: `C:\dev\carla-native-tm-cancel-20261012\artifacts\wheel-final\carla-0.9.16-cp312-cp312-win_amd64.whl`.
- Wheel SHA256: `6A949D62EBFDF1B93669F8BCE5E7CB453580440783E7DE44016E8761822653CE`.
- Loaded extension SHA256: `636D249A6898FEB4A6A8D166917DDDE04F26B70DAB9E95C1D3D50E9ACF3CA471`.

The native artifact package version is still `0.9.16`; the hash identifies the
patch. Runtime verifies the loaded extension, then IPv4 and IPv6 listening-port
ownership before and after obtaining the TM and before force submission. A foreign
PID, missing ownership or query failure does not authorize execution. A listener
owned by the same process may be reused across Training/M1/M2; the TM is not
globally shut down between stages. The loaded extension check and host PID check
are separate: loading the wheel alone does not patch a remote TM host.

Exclusive use of the selected TM port by this P8 process remains an operating
prerequisite. PID checks cannot prohibit another external client from later
connecting to the owned TM and changing settings. No such concurrent client is
supported. Native concurrent Stop/Reset/shutdown is not part of the verified
registration/force/batch contract.

## Request admission and control authority

- Z OFF-to-ON requests LEFT; X OFF-to-ON requests RIGHT.
- Existing accepted-edge filtering handles repeat, held keys and OFF transitions.
- Native mapping is LEFT=False, RIGHT=True, verified in the compiled native branch.
  Actual simulator motion direction remains UNVERIFIED.
- Requests require active NoA and its current activation generation. No queue or
  automatic retry exists. Consumed request IDs and old activation generations are
  rejected. An ongoing request is not replaced by another same/opposite request.
- Existing adjacent-lane Driving/direction/marking checks are reused. Junctions,
  different road/section neighbors, missing resources and invalid baseline geometry
  are not admitted.
- `EMPTY_REHEARSAL_ONLY`: the actor inventory must contain the ego and no other
  `vehicle.*`, `walker.*` or `static.prop.*` actor. Missing/failed inventory or any
  such other actor blocks the request without sending a force command.
- This deliberately conservative, world-wide exclusion has no distance/TTC
  threshold. It is a point-in-time rehearsal admission gate, not a safe-gap test
  or protection against actors appearing after submission.
- `force_lane_change` still bypasses some TM lane-entry risk checks. Pending
  cleanup is not a dynamic collision-safety proof. P7 traffic conditions stay blocked.

TM default speed, lane following, signal response and normal collision response
are retained. Automatic lane changes remain disabled, random lane-change rates
remain zero, and TM automatic light updates remain disabled. No custom lateral
controller or additional control writer is introduced.

TM-spawned hero blueprints require `sticky_control=true`. After TM deregistration,
the existing manual, neutral/reset, transmission-preparation and shutdown-brake
controls use one checked `client.apply_batch_sync([ApplyVehicleControl(...)], False)`
callback built from the already-open client and owned hero ID. This bypasses the
per-vehicle Python client's identical-control cache without adding a tick, retry,
writer thread or control computation. Exactly one response for the owned actor is
required; missing, extra, error and wrong-actor responses fail closed. RPC
exceptions propagate unchanged and no `Vehicle.apply_control` fallback is attempted.

Non-TM diagnostic spawning retains its original direct vehicle-control behavior.
Brake, steering and N use the existing controller thresholds/reasons and deregister
before the existing manual writer runs. Pending generation is invalidated before
native deregistration. The canonical automation state and the scheduler both block
manual batches while NoA is active; this does not rely on the legacy display mode.
The legacy P-key reset path is filtered by the research view and is normally
unreachable, but if called directly it uses the same callback and the same NoA gate.
Reactivation uses the unchanged activation gate without a count limit.

Read-only source inspection explains the transport risk but does not prove the
physical root cause: `WheeledVehicleAIController.cpp:131-146` inserts a default
control on a nonsticky, non-autopilot tick; `CarlaWheeledVehicle.cpp:334-344` resets
input priority after processing; and client `Vehicle.cpp:55-60` suppresses an
identical cached control while `GetControl` does not refresh that client cache.
The cache-separated Fake regression models only those control-delivery semantics.
It is not a drivetrain, speed, frame-by-frame or loaded-binary physics model.

## Observation and automatic signal cancellation

Request, submission, observed progress, completion, interruption and unverified
execution are distinct records. A successful force call proves only submission.
An exception may mean delivery uncertainty; it is not retried.

The observer uses persisted snapshot poses and lane transforms, full
road/section/lane identities for both lanes, and advancing CARLA frames. Samples
acquired before submission do not establish progress. Duplicate frames add no new
evidence; frame regression, missing geometry or unproven topology changes latch
UNVERIFIED rather than manufacture completion.

Raw motion alone remains OBSERVED. IN_PROGRESS additionally requires an observed
outside-to-inside target-corridor transition using its existing lane half-width.
Source references move with the road rather than treating the original
tangent as the entire road. Completion requires a source-outside/target-inside
boundary interval, actual target identity and the existing activation gate's
center/heading/rotated-bounding-box fit calculation. No new numeric tolerance or
timeout is introduced. Boundary frames describe an observed interval, not an
invented exact physical crossing timestamp.

Only normal completed observations clear the requested blinker bit. LowBeam,
Brake and the opposite blinker are preserved. Getter, setter and readback outcomes
are recorded separately; command return is not light-state readback proof. The
existing driver-loop audio poll follows observation, and inactive audio stops the
current click and resets its schedule. Automatic OFF creates no keyboard edge.
Failure/rejection/interruption do not introduce a new signal-cancellation policy.
UNVERIFIED requests retain their lock until acknowledged native deregistration
through the existing N/brake/steer path. Rejected requests identify the pending
request and required recovery; reactivation still needs the original gate and a
fresh signal edge. No automatic retry or timeout-based unlock is performed.

Events remain in the existing CSV event queue with Participant/Run/SessionClock
context: `lane_change_request`, `lane_change_lifecycle`,
`lane_change_geometric_sample`, `lane_change_signal_autooff`. The records carry
request/context/P5 identity, mode, static eligibility, generation, actual timestamps,
geometry and observed boundary interval. Missing values are not replaced by success.

## P5

The outer exit observer withholds candidate P5 RIGHT input from GENERAL dispatch.
Only the coordinator's accepted confirmation transition invokes the shared TM
submission callback. R, timeout, stale route observations and driver priority are
processed first. Held confirmation does not submit again, and an existing GENERAL
maneuver cannot be resubmitted as P5.

The TM callback replaces Custom route-reference arming. Non-TM diagnostic behavior
is retained. Context GENERAL has no P5 event ID; context P5 retains `lc_event_id`.
Actual maneuver state, not the entire confirmed exit lifecycle, gates subsequent
lane requests. Navigation, module time windows and Exit47 observation remain.

Adjacent-lane completion is not EXIT_CONFIRMED. Exit-road entry and post-exit
continuation/rejoin remain separate existing observations and physically UNVERIFIED.
TM branch selection is not redesigned or guaranteed by this patch.

## Phase A P5 offline correctness update (2026-10-13)

The observer now fails closed if target-lane geometry reaches its completion
criterion after the TM activation generation has already changed. It records
`UNVERIFIED` with `STALE_ACTIVATION_GENERATION_AT_COMPLETION`, retains the observed
target-lane evidence, does not clear the requested indicator, and does not claim a
normal completed maneuver. This is an observer/executor race correction, not proof
that native cancellation or physical motion succeeded.

Each geometric sample and terminal lifecycle record now includes the observed target
lane center pose, lane width, configured existing center/heading tolerances, and the
specific source/target alignment failure when present. These are diagnostics for a
future physical run; they add no threshold and cannot identify a native or physics
failure without live frame evidence.

The P5 route observer emits `lc_exit_entry_observed` on the first manifest-defined
exit-segment identity. This is separate from confirmation, lane-change completion,
and terminal `lc_completed` with outcome `EXIT`. Both exit-entry and terminal-exit
records state `highway_rejoin_status=NOT_CONFIGURED`; the current development
manifests contain no validated rejoin boundary, so no rejoin success is claimed.

## Offline verification commands

The prior implementation verification remains historical: **1439 passed in
238.91 s** in `.venv-p8`. Current sticky-control/checked-delivery regression
verification: **1461 passed in 239.60 s** in the same `.venv-p8`; dedicated delivery,
manual writer, lifecycle and three-way takeover coverage: **19 passed in 0.37 s**.
The initial sticky/spawn and shutdown-batch regressions failed before the fix
(false instead of true; zero batches instead of one). The expanded regressions
also distinguish the client comparison cache from server control, and cover the
preparation -> TM -> manual -> shutdown sequence with one reused client.
`ruff check .` and `git diff --check` passed (existing CRLF warnings remain).
Python LSP remained unavailable because
basedpyright is not installed and installation was previously declined.
The native executable passed its five scenarios. The direct Fake-I/O driver
observed LEFT=False and RIGHT=True, each progressing through REQUESTED,
SUBMITTED_TO_TM, IN_PROGRESS, COMPLETED and selected signal OFF; P5 produced one
RIGHT force call and no Custom reference arm. Real Windows IPv4/IPv6 socket tests
confirmed listener parsing without starting a TM. Python LSP was unavailable
because basedpyright is not installed and installation was previously declined.

The driver applies an accepted signal before normal NoA request dispatch and
verifies the selected light is observed ON before force submission. Simultaneous
driver overrides retain their priority path; failed signal application does not
submit a force command. P5 decision time remains the input decision time, while a
failed assisted submission is timestamped only after its callback returns.

Run from `C:\dev\carla`:

```powershell
$python = "C:\dev\carla-native-tm-cancel-20261012\.venv-p8\Scripts\python.exe"
& $python -m pytest -q
& $python -m ruff check .
git diff --check
```

Native regression executable (does not connect to CARLA):
`C:\dev\carla-native-tm-cancel-20261012\build-native\tests\native\Release\tm_cancel_native_tests.exe`.
Native tests prove compiled request-state/shared-lock/direction contracts, not
real RPC latency or physical-frame control application. Python tests use explicit
fake attestation/inventory boundaries; real Windows ownership parsing is separately
exercised using ordinary local IPv4/IPv6 sockets, not a CARLA TM instance.
Likewise, a successful Python batch response proves command-response association,
not physical application on a simulator frame, vehicle acceleration or normalized
simulation/wall-clock behavior.

## Requested no-P5 development retry command (not executed here)

Allow the old operator session to close normally before retrying. Every retry uses
a new participant ID, a new run ID and a newly spawned vehicle. Do not kill a
process automatically. Port 8000 must remain exclusive to this patched P8 process;
if ownership or availability is uncertain, stop rather than terminating a listener.
Sticky configuration applies only before spawn; an existing nonsticky vehicle is
not retroactively changed. This retry intentionally omits P5 exit configuration
and must not be reported as complete P5 exit/rejoin validation.

```powershell
Set-Location C:/dev/carla
$python = "C:\dev\carla-native-tm-cancel-20261012\.venv-p8\Scripts\python.exe"
$participant = "P8-DEV-$([guid]::NewGuid().ToString('N'))"
$run = "P8-NO-P5-$([guid]::NewGuid().ToString('N'))"
& $python -m src.p8.operator_cli `
  --mode dev-rehearsal --participant-id $participant --run-id $run `
  --m1 NO_SURT --m2 NOA_L2 --ack-dev-live --spawn-index 212 `
  --traffic-manager-port 8000 --training-duration 600 `
  --output-root data/p8_operator
```

No `--drive-duration` override: Training600 -> external Tobii calibration prompt ->
M1600 -> facilitator-managed REST -> M2780 -> results. F8 training completion,
V viewpoint, mirror configuration and stage ownership remain unchanged.

Before any physical claim, verify actual LEFT/RIGHT motion, alignment and light/audio
OFF, brake/steering/N handover, reactivation with no stale replay, signal response,
P5 exit-road entry and subsequent continuation. General traffic and the final P7
obstacle require later independent validation; empty-road success cannot release
participant readiness. The native patch does not bound takeover RPC latency.

## Return to the preserved environment

The original `.venv` was not upgraded. Its existing CARLA extension will not pass
the new P8 patched-runtime attestation; this is intentional, not a silent fallback
to unpatched execution. No binary restoration is needed. Do not install the new
wheel into the original environment until separately approved.
