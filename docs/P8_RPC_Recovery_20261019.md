# P8 native RPC exception containment and crash recovery

The 2026-10-19 run `P8-PILOT-8015d293c1114563b671cd9c560ce746`
ended with Windows exit `0xC0000409` (fast-fail subcode 7).
The matching minidump contains a C++ `clmdep_msgpack::parse_error` on
the private CARLA client's rpclib server receive path. Its original
`server_session::do_read()` let unpacking exceptions escape the Asio worker.
Python `try/except` and `finally` cannot recover a terminated process.
The source of the malformed bytes has not been established.

## Changes

- `tools/build_rpc_guard.py` patches the pinned upstream `server_session.cc`.
  It checks RPC envelopes before accessing array fields, catches MessagePack
  decode and dispatch exceptions, closes only the affected connection and
  emits `NATIVE_RPC_REJECTED` with a reason and peer address/port.
  It never retries an RPC command or changes driving, hazard or input policy.
- `tools/build_noa_tm.py` links that replacement object ahead of the original
  `rpc.lib`. ABI headers, original library, upstream source and Asio dependencies
  are hash checked. The existing lane-change patch is unchanged. PDB and link
  map are retained under `data/noa_tm_build` for subsequent crash analysis.
- `run_pilot.cmd` runs a standard-library supervisor which launches the existing
  resistance-preserving worker with the same arguments and inherited stdin.
  The parent never imports CARLA/pygame, polls the wheel or controls a vehicle.
  Console output and native stderr are copied to a per-launch diagnostic log.
- After a registered worker exits with a nonterminal session, the supervisor
  preserves the original manifest, marks the session `ABORTED`, records the
  interrupted stage and last saved timestamp, copies pending gaze files into
  the exported evidence and creates a ZIP. Incomplete raw records are preserved
  exactly but excluded from summary calculations. No missing time, hazard
  outcome, completed M2 or simulator cleanup is invented.
- A Windows notice remains until the operator acknowledges it (OK/Space).
  It states the interruption, storage result and paths. It asks the operator
  to stop Unreal Play and check the scene; the parent does not manipulate
  unknown/orphaned actors or automatically resume an interrupted trial.
  Export failure is visible and leaves originals intact.

Windows virtual-environment Python uses a redirector, so the registered Python
PID can differ from `Popen.pid`. Registration is bound to the unique launch
token and output root. The supervisor waits for the launcher and verifies that
the registered interpreter has exited before reading files for recovery.

## Running and evidence

The command is unchanged:

```powershell
cd C:\dev\carla
.\run_pilot.cmd
```

- Per-launch stdout/stderr: `data/p8_operator/_supervisor/<launch-id>/console.log`
- Registration and exit code: the same directory's `run.json` and `exit.json`
- Recovered run: `data/p8_operator/<run-id>/session_manifest.json`
- Recovery detail: `<run-id>/crash_recovery/report.json`
- Original manifest and pending gaze: `<run-id>/crash_recovery/`
- ZIP: `data/p8_operator/_exports/<run-id>.zip`

Older stopped runs can be recovered into a **separate copy**:

```powershell
& 'C:\dev\carla-native-tm-cancel-20261012\.venv-p8\Scripts\python.exe' -B -m tools.recover_pilot 'data/p8_operator/<run-id>' --worker-exited --exit-code -1073740791
```

Use the actual exit code. Never use this command on an active run. Copies go to
`data/p8_operator/_recovered/<run-id>` and ZIPs to `_recovered/_exports/`.
Existing copies are never overwritten.
If ZIP publication failed after a copy was recovered, repeat with the recovered
directory as the source and add `--retry-export`; this only retries ZIP export.

## Verification and limits

`tools.verify_rpc_guard` uses its own loopback test server, not CARLA ports.
It injects reserved MessagePack bytes, invalid envelopes, invalid parameter
types and handler failures, while checking a persistent healthy connection.
It also checks fragmented requests, notifications and disconnected partial
requests. The native build reruns the 32-case LocalizationStage regression.

`tests/test_p8_crash_recovery.py` uses actual subprocess `os._exit`, including
the signed Windows native exit code, to bypass `finally`. It checks raw-byte
preservation, truncated records, ZIP contents, retry after export failure,
normal exit codes and rejection of unrelated run registrations.

This contains the identified native exception path. It cannot prevent power
loss, forced termination of the supervisor, operating-system/GPU failure or
all possible native memory errors. Data not flushed before a crash cannot be
recovered. Physical wheel/vehicle and full-duration live-session verification
remain necessary. No Unreal binaries or external Python installations are
modified.

Verified on 2026-10-19: 72 malformed-connection cases and 32 native lane-change
cases passed. Full offline run: 2,358 passed, 2 skipped, 5 failed. All five
failures concern pre-existing cockpit camera/mirror-layout expectations and
reproduce with the original installed native client (without this RPC patch).
Those camera/layout files and tests were not changed by this work. Detailed
results: `data/rpc_recovery_verification/tests.txt` and
`baseline_view_tests.txt`. The project native library hash is
`2af89add3e333ffcca316a5d99ece6313677c14fee3af35f89bed41a353a59e3`.
After final supervisor refinements, all 15 focused recovery tests passed,
including console encoding, signed native exit status, a cut telemetry field,
and failure to write the exit diagnostic. `run_pilot.cmd --help` also passed
through the actual supervisor/worker/bootstrap without opening driving or input.

The interrupted 8015 run was recovered in `_recovered` with the original
22 files verified byte-for-byte against the preserved copy (the original
manifest is retained under `crash_recovery`). ZIP integrity passed; its
25 entries include the three pending M2 gaze files. The original run folder
was not changed; the recovered manifest is `ABORTED` with last saved session
time 1194.891 seconds, rather than a completed M2 record.

Build prerequisite added to the existing verified native build: matching
Asio headers in `D:\CARLA_0.9.16_Source\Build\rpclib-src\dependencies\include`.
The upstream server session and license are retained in
`native/noa_tm/rpc_guard`; all generated outputs stay in this project.
