# P8 P5 user-driven physical validation

## Status and scope

Physical LEFT/RIGHT lane-change completion is **UNVERIFIED**. This work does not
start CARLA, connect to the simulator, move actors, rebuild native code, or replace
the wheel. Offline topology tests cannot establish steering or physical motion.
Use only the existing `src.p8.operator_cli` entrypoint for the next user run.

## Read-only environment verification

The following interpreter was inspected without CARLA RPC:

- `C:\dev\carla-native-tm-cancel-20261012\.venv-p8\Scripts\python.exe`
- CPython 3.12.10
- Loaded extension: `.venv-p8\Lib\site-packages\carla\libcarla.cp312-win_amd64.pyd`
- SHA256: `636D249A6898FEB4A6A8D166917DDDE04F26B70DAB9E95C1D3D50E9ACF3CA471`
- Current `python -m src.p8.operator_cli --help` succeeds.

This does not attest a running TM listener or server. The existing runtime hash,
listener-owner, actor-inventory, activation-generation and single-submit checks
must still pass at execution time. Do not substitute `C:\dev\carla\.venv` for the
approved live interpreter or bypass a failed attestation.

## Native path inspected without modification

All references below are relative to
`C:\dev\carla-native-tm-cancel-20261012\source\LibCarla\source\carla\trafficmanager`.

- `TrafficManagerLocalLifecycle.h:45`: force state is stored only for a registered
  actor while holding the lifecycle lock.
- `LocalizationStage.cpp:115`: the worker obtains the actor's force request before
  testing lane-change admission.
- `LocalizationStage.cpp:146`: buffer-front junction and previous-change distance
  admission can prevent assignment even after request consumption. The existing
  native distance rule is not a new Python timeout and is not changed here.
- `LocalizationStage.cpp:439`: forced direction selects the corresponding cached
  left/right waypoint only if present.
- `LocalizationStage.cpp:448`: a selected change-over point is advanced along the
  target lane; `LocalizationStage.cpp:166` replaces the actor's waypoint buffer.
- `MotionPlanStage.cpp:158`: the target in that buffer determines angular error;
  PID output becomes `ApplyVehicleControl` at line 217.
- `TrafficManagerLocal.cpp:182`: the worker's lifecycle lock covers the stages and
  batch submission. `TrafficManagerLocal.cpp:236` releases it only after the batch
  callback returns. Unregister removes the actor and clears force state under the
  same gate (`TrafficManagerLocalLifecycle.h:33`).

The current Python submission event is not a native assignment receipt. Existing
logs do not show whether a particular worker cycle rejected a request at junction,
cooldown, or missing cached adjacency. A subsequent request immediately after
observer completion can still encounter the native previous-change distance gate.
Do not fabricate a successful result, automatically retry, or silently alter this
gate. Capture the next user run before deciding whether native changes are needed.

If motion remains absent despite a valid continuous observer, a separate native
approval request must identify the failed predicate. A minimal diagnostic proposal
would expose actor/request-correlated consumption, rejection reason and target
assignment in Parameters/LocalizationStage and the binding. Any behavior change,
rebuild, new wheel hash and physical acceptance tests require separate approval.

## User-run command

First independently confirm a safe empty Town04 road and that the chosen TM port
is dedicated to this P8 process. Do not stop or interfere with another user's
session to make these prerequisites true. The command below has **not** been run.

It preserves the referenced run's DEV conditions (`NO_SURT`, `MANUAL`) and leaves
exit routing unconfigured, so this validation isolates general Z/X requests.
It is not a participant run or an exit/rejoin validation. Training is 600 seconds;
omitting `--drive-duration` retains M1 600 seconds and M2 780 seconds.

```powershell
Set-Location C:\dev\carla
$python = 'C:\dev\carla-native-tm-cancel-20261012\.venv-p8\Scripts\python.exe'
$spawn = [int](Read-Host 'Verified safe empty Town04 Driving-lane spawn index')
$tmPort = [int](Read-Host 'Dedicated Traffic Manager port for this P8 process')
$tag = [guid]::NewGuid().ToString('N')
$run = "P8-LC-$tag"

& $python -m src.p8.operator_cli `
  --mode dev-rehearsal `
  --participant-id "DEV-LC-$tag" --run-id $run `
  --m1 NO_SURT --m2 MANUAL `
  --ack-dev-live --spawn-index $spawn --traffic-manager-port $tmPort `
  --training-duration 600 --output-root data/p8_operator
```

Use Training or M1's existing freely selectable NoA for the sequence; do not alter
the M2 assignment to perform it. Preserve normal stage durations and existing UI
progression. Abort if safety or simulator state makes continuation inappropriate.

## Acceptance sequence and artifacts

1. Activate NoA with N and confirm actual NOA_ACTIVE state.
2. Press and release Z once. Do not hold/repeat it to force movement.
3. Observe real LEFT movement, target-lane entry and center/heading alignment.
4. Confirm the left indicator turns off and its sound ends automatically.
5. Press and release X once; independently verify real RIGHT movement, alignment,
   automatic right-indicator and sound termination.
6. Verify a later fresh request is accepted without recovery. Also exercise a
   normal road/section boundary when safely available; do not deliberately enter
   a junction or ambiguous branch to manufacture a success.
7. Separately test brake/steering override and normal reactivation. An uncertain
   request remains uncertain; use existing N/brake/steering recovery rather than
   repeated force requests. Do not infer completion from timeout or lane ID alone.

Provide the complete new `data/p8_operator/$run` directory, especially:

- `research_events_<run>.csv`
- `research_telemetry_<run>.csv`
- `session_manifest.json`
- Console errors, plus approximate input times and a short video if possible.

Analysis will correlate each request ID/generation/direction with submission frame,
source/target pair, observed pair/pose, movement and boundary frames, alignment,
completion, signal auto-off and subsequent request. Video/audio is useful because
control or light-state readback alone does not prove the displayed indicator or
audible output. Preserve raw files unchanged, including failed and aborted runs.

Success is not declared until the user-run evidence confirms LEFT, RIGHT,
automatic signal/audio termination and follow-up requests. Any surviving native
or topology failure will be handled from that evidence, not inferred away.
