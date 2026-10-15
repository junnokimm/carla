# P8 native Traffic Manager diagnostic run

This procedure is only for `DEV_REHEARSAL`. It selects the isolated candidate-J
CPython 3.12 environment and does not weaken the normal P8 binary attestation.
Do not use this path for participant sessions.

## Preconditions

- Start the intended CARLA simulator separately and load the approved empty-road rehearsal map.
- Ensure no vehicle, walker, or static prop exists before P8 spawns its ego vehicle.
- Ensure no other process owns the selected Traffic Manager port.
- Keep all CARLA clients and Traffic Manager requests inside this one operator process.

## Attest the interpreter and extension

Run from `C:\dev\carla` in PowerShell:

```powershell
$Python = 'C:\dev\carla-native-tm-cancel-20261012\.venv-p8-diagnostic\Scripts\python.exe'
$Libcarla = 'C:\dev\carla-native-tm-cancel-20261012\.venv-p8-diagnostic\Lib\site-packages\carla\libcarla.cp312-win_amd64.pyd'
$ExpectedLibcarlaSha256 = 'E4B2BAD4D70DF255C1123E7064602B1B010E34F740638B19BC2608478D479721'

if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Libcarla).Hash -ne $ExpectedLibcarlaSha256) {
    throw 'Diagnostic libcarla SHA256 mismatch'
}

& $Python -c "import pathlib,sys,carla.libcarla as lib; expected=pathlib.Path(r'$Libcarla').resolve(); actual=pathlib.Path(lib.__file__).resolve(); assert pathlib.Path(sys.executable).resolve()==pathlib.Path(r'$Python').resolve(); assert actual==expected; print(sys.version); print(actual)"
if ($LASTEXITCODE -ne 0) { throw 'Diagnostic interpreter/path attestation failed' }
```

The application repeats exact path, SHA-256, and local listener ownership checks
before constructing `carla.Client`. Any mismatch fails closed.

## Run the operator

Create fresh identifiers for every attempt. Retain `--mode dev-rehearsal`,
the acknowledgement, and the diagnostic switch:

```powershell
$ParticipantId = [guid]::NewGuid().ToString('D').ToLowerInvariant()
$RunId = [guid]::NewGuid().ToString('D').ToLowerInvariant()
$SpawnIndex = 212
$TrafficManagerPort = 8000
if ($TrafficManagerPort -eq 0) { throw 'Traffic Manager port 0 is not allowed' }
if (Get-NetTCPConnection -State Listen -LocalPort $TrafficManagerPort -ErrorAction SilentlyContinue) {
    throw "Traffic Manager port $TrafficManagerPort already has a listener"
}
$OutputRoot = 'data\p8_operator_native_tm_diagnostic'

& $Python -m src.p8.operator_cli `
    --mode dev-rehearsal `
    --participant-id $ParticipantId `
    --run-id $RunId `
    --m1 NO_SURT `
    --m2 NOA_L2 `
    --ack-dev-live `
    --spawn-index $SpawnIndex `
    --traffic-manager-port $TrafficManagerPort `
    --native-tm-diagnostics `
    --native-tm-diagnostics-capacity 1024 `
    --native-tm-diagnostics-interval 0.5 `
    --training-duration 600 `
    --output-root $OutputRoot

if ($LASTEXITCODE -ne 0) { throw "P8 diagnostic run failed with exit code $LASTEXITCODE" }
```

Capacity must be `1..4096`. The interval must be finite and within
`0.05..5.0` seconds. Tuning arguments are rejected unless diagnostics are
enabled.

Do not pass `--drive-duration` for this nominal workflow. The operator applies
the protocol durations: Training 600 seconds, Module 1 600 seconds, and Module
2 780 seconds. `NO_SURT`/`NOA_L2` preserves the failed diagnostic rehearsal's
development assignment, not a participant allocation. Spawn 212 and TM port 8000
are the requested rehearsal settings; this document does not certify their live
availability. Do not add a speed override or shorten the module windows.

## Startup inventory barrier

Diagnostics still require exactly one `vehicle.*`, with the spawned ego ID,
and no other `vehicle.*`, `walker.*`, or `static.prop.*`. The P5 submission-time
gate is unchanged. Only a missing ego with no conflicting inventory is retried.

The passive wait budget is `min(--timeout, 5.0)` seconds, using the existing P8
default timeout of 5 seconds as an internal ceiling. Each `wait_for_tick` receives
only the remaining positive monotonic budget. A returned frame must be newer
than the previous observation, and the next local snapshot must have reached
that frame. A stale frame, query error, conflicting actor, or expired deadline
fails closed. The application never calls `world.tick()` or changes World
settings; the existing P8 rejection of synchronous Worlds is unchanged.

The deadline is checked around observations and waits. `get_actors()` can fetch
uncached descriptions through an RPC governed by the existing client timeout;
an in-flight RPC or filesystem write cannot be preempted by this Python deadline.
This is a bounded passive-wait policy, not a hard real-time latency guarantee.
It runs before diagnostic enable and the active-drive window. Session clock and
nominal experiment durations are not changed.

The existing Event CSV receives `p8_native_tm_inventory` for each observation
and terminal wait result, with ego ID, vehicle IDs/types/roles, prohibited
IDs/types, local frame brackets, host monotonic time, attempt, elapsed time,
budget, outcome, reason, and query error when applicable. `PASSED` means the
original invariant passed, not that other clients are excluded. Local frame
brackets are not an atomic World lock. Keep external clients excluded.

The historic `P8-TMDIAG-20261014-145223` run lacks these inventory details.
Streaming visibility delay remains a plausible explanation, not a proven
retrospective diagnosis of that run.

## User-run physical checklist

No CARLA connection or physical drive is part of the offline preparation.
After starting the intended simulator yourself and meeting the preconditions:

1. Run the single operator command above and check the Event CSV for a final
   inventory `PASSED`, then the diagnostic sidecar header/initial receipt.
2. Verify Training starts. Use the existing **N** NoA toggle, then request left
   and right lane changes with **Z/X** where the existing safety gates permit.
3. Observe actual lane crossing/completion, automatic indicator extinguishing,
   NoA OFF/override, and continuity. A submitted request alone is not completion.
4. Correlate `force_accepted`/`force_stored`/`force_consumed`, `lane_change_target`,
   the next `localization_precheck`, `buffer_mutation`, `motion_control`, and
   batch outcomes using native session/request/cycle/actor identities. Inspect
   retained versus removed buffer fronts, target waypoints, PID inputs/outputs,
   and actual batch responses. Do not substitute receipt time for native time.
5. Finish or abort using the existing operator controls; retain partial data on
   any error. Never bypass inventory, attestation, or TM safety gates.

The command does not invent exit routes or eligibility windows. Scheduled P5
exit/rejoin requires the separately verified existing P5 CLI configuration.
Module-2 final-hazard readiness/continuation gates and participant restrictions
remain unchanged; this diagnostic readiness is not full experiment approval.

## Inspect artifacts

Each stage creates, exclusively, a file named:

```text
native_tm_diagnostics.<stage_label>.ego-<actor_id>.jsonl
```

Every row contains `participant_id`, `run_id`, originating stage, current
measurement phase, host monotonic/UTC receipt time, and projected session
elapsed time. Polls produce bracketed `snapshot_receipt` rows and previously
unseen `native_record` rows, keyed by `(trace_session, event_sequence)`. Native
fields remain unchanged under `native_payload`. A periodic receipt also carries
the cached CARLA frame and simulation timestamp captured after control and
telemetry persistence. `READ_RECEIPT_ONLY_NO_NATIVE_CLOCK_MAPPING` explicitly
means these host/CARLA labels do not map the native steady clock.

On successful finalization the last sidecar row is `final_summary` after the
enabled final read, disable, and frozen read. Its
`artifact_durability=PENDING_STREAM_CLOSE` explicitly limits it to the point
before stream closure. It is not proof that closure succeeded. After
runner-context cleanup, the common research event stream
receives `p8_native_tm_diagnostics_status` with the sidecar path, actor, stage,
current phase, trace session, counters, coverage status, first error, and
secondary `cleanup_errors`. Use that post-close event as the authoritative
completion status; a missing event or missing summary means finalization is
unverified, even if a provisional sidecar summary says `COMPLETE`.

KeyboardInterrupt during construction/header, enable/initial read, polling,
or finalization preserves the original exception and attempts the remaining
owned cleanup, including disable when enable was attempted and stream closure.
Viewer/ego cleanup continues across caught cleanup interrupts. Capture
interruptions latch `FAILED_OBSERVER`; normal driving is never resumed.
Repeated interrupts can still strike between Python instructions, and forced
process termination/Windows TerminateProcess cannot guarantee Python cleanup.
Flush is not a guarantee against power loss or OS/filesystem failure.

The run directory is `<output-root>\<run-id>\`, for example:

```text
data\p8_operator_native_tm_diagnostic\<run-id>\
  native_tm_diagnostics.<stage_label>.ego-<actor_id>.jsonl
  research_events_<run-id>.csv
  research_telemetry_<run-id>.csv
  session_manifest.json
```

Retain all stage sidecars and the complete run folder, including any additional
camera/performance artifacts. An inventory failure occurs before collector open
and therefore has no native JSONL; its cause is in the Event CSV and abort
manifest. Re-run with fresh IDs, never by deleting/overwriting an old run.

Interpret the three loss counters independently:

- `overwritten`: the bounded native ring replaced older committed records.
- `contention_dropped`: a producer refused to wait for the native trace lock.
- `internal_dropped`: native diagnostic publication failed for another internal reason.

Coverage status is `COMPLETE` only when all reads/writes/cleanup succeeded and
all loss counters are zero. `UNKNOWN_OVERWRITE` means the bounded ring reports
overwriting without fabricating an exact unseen-event count.
`INCOMPLETE_NATIVE_DROP` means contention or internal native drops occurred.
`FAILED_OBSERVER` means schema, read, write, polling, disable, summary, or stream
cleanup failed. A missing event is not evidence that the event did not occur
when coverage is not complete. The trace is observational and does not
establish physical lane-change validation.

Normal P8 runs omit `--native-tm-diagnostics` and continue to require the
protected default libcarla SHA-256
`636D249A6898FEB4A6A8D166917DDDE04F26B70DAB9E95C1D3D50E9ACF3CA471`.
To return to that environment, use
`C:\dev\carla-native-tm-cancel-20261012\.venv-p8\Scripts\python.exe` and omit
all three native-diagnostic CLI options. No wheel reinstall, environment mutation,
source reset, or deletion of diagnostic runs is needed.
