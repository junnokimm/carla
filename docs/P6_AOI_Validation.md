# Bounded Tobii / CARLA cockpit AOI evidence

## Status and safety boundary

V5 Source of Truth sections 10 and 11 govern this development tool. It does not
create a participant run, vehicle, traffic, route, hazard, or tablet integration.
The operator reports successful Nano discovery and real capture. The archived
`20261007T125812Z-80e2548d` run also contains five completed interaction windows.
Its descriptive AOI counts can be recomputed offline, but its clock evidence is
not validated and neither the archive nor reanalysis approves physical mapping,
calibration, AOI accuracy or participant use.

The `.venv` Python 3.12 CARLA process and `.venv-tobii` Python 3.10 SDK process stay
separate. No SDK import occurs in the CARLA process. There is no synthetic fallback.
Production `AoiLayout.classify()` and its validity gate are unchanged. The tool
never writes the input AOI file or sets any production approval flag.

## Preconditions

1. UE4Editor/CARLA is running the intended map in Play mode, unpaused, with an
   asynchronously ticking world. The tool never calls `world.tick()` or changes
   world settings.
2. An existing vehicle is parked externally and remains parked. Supply its exact
   actor ID. The tool never spawns/destroys a vehicle, applies brake/throttle/steer,
   toggles autopilot/lights, or takes single-writer vehicle control ownership.
   Do not run another interactive driving client that can move this actor.
3. Prepare ETM's physical display setup and the intended participant calibration
   separately. Know which Windows monitor corresponds to that ETM display.
4. Nano is connected; use the actual discovered serial and explicit SDK Python path.
5. Keep the entire Pygame client on one monitor. Current surface/candidate geometry
   is 1280x720. Do not resize/move/change DPI after confirming the monitor.

If an actor ID is needed, this optional read-only query lists existing vehicles:

```powershell
& .\.venv\Scripts\python.exe -B -c "import carla; c=carla.Client('127.0.0.1',2000); c.set_timeout(2.0); print([(v.id,v.type_id) for v in c.get_world().get_actors().filter('vehicle.*')])"
```

An empty list means an externally parked vehicle must be prepared first. This
validator intentionally does not create or park one for the operator.

## Live command

From `C:\dev\carla`, set `$serial` to the discovered Nano serial and `$actorId` to
the existing parked vehicle ID, then run:

```powershell
& ".\.venv\Scripts\python.exe" -B -m tools.tobii.aoi_validation `
  --tobii-python ".\.venv-tobii\Scripts\python.exe" `
  --tobii-serial $serial `
  --aoi "config\p6_aoi_candidate.json" `
  --actor-id $actorId `
  --host 127.0.0.1 --port 2000 `
  --max-seconds 120
```

The live-window deadline includes preparation. It defaults to 120 seconds and is
bounded to 20-180 seconds; every measured segment is fixed at 2 seconds. Existing
gaze smoke remains limited to 10-20 seconds. Only the explicit worker `validate`
action permits the longer bounded session and cooperative STOP over private stdin.

`--preflight-only` checks Tobii discovery/serial selection without CARLA access.
It is not AOI validation and records that the sequence was not run. `--run-id`
can name an evidence directory, but reuse of an existing directory is rejected.

## Operator interaction

The frontend design router and existing `DESIGN.md` guide the preparation overlay:
existing HUD colors, Malgun Gothic text, no animation, score or pass/fail feedback.

1. Wait for four live camera feeds and fresh real gaze samples. Click/focus the
   Pygame window if necessary. This is the existing `DriverView` camera/composition
   path, selecting its current cockpit transform/FOV without changing those values.
   Existing speed/gear rendering is read-only; no driving-mode ownership is claimed.
2. Compare the monitor identity/bounds shown in the preparation overlay with the
   actual ETM-configured physical display. Only if they match, press **C**.
   This records an operator assertion, not scientific mapping approval.
3. Prepare to look at **FORWARD** and press/release **Space**. The overlay disappears
   before the next frame flip. Maintain gaze for about 2 seconds.
4. Repeat for **REAR_MIRROR → LEFT_MIRROR → RIGHT_MIRROR**.
5. For **MISSING**, prepare to close the eyes or look outside tracker range, then
   press/release Space. Resume normal gaze after the 2-second interval.
6. **ESC**, closing the window or Ctrl+C aborts and preserves partial evidence.
   Holding Space cannot automatically start the next segment.

During each measured interval there is no validation prompt, countdown, target
marker or gaze cursor. The START timestamp is taken after the clean frame's
`pygame.display.flip()` returns. This is a software presentation boundary,
**not measured photon onset**. END uses the planned two-second cutoff. ABORT
truncates an active interval at the observed abort time, before camera cleanup.

Movement, stale camera/gaze, confirmed geometry changes, focus loss, worker failure
and the deadline abort rather than silently continue. The 0.05 m/s stationary
guard, 1-second camera freshness, 2-second gaze freshness and 10-second startup
deadline are operational safety/liveness guards, not research AOI pass thresholds.
CARLA RPC timeout is 2 seconds. The tool stops/destroys only its own four cameras.
Vehicle motion must be stopped externally; aborting this tool does not brake it.

## Coordinate mapping

The tool obtains the actual Pygame HWND with `get_wm_info()`, then uses a
per-monitor-v2 DPI-aware **thread** for `GetClientRect`, `ClientToScreen`,
`MonitorFromWindow`, `GetMonitorInfoW`, `GetSystemMetrics` and `GetDpiForWindow`.
The previous thread DPI context is restored on exit. No system DPI setting changes.

For normalized display gaze `(nx, ny)` and the operator-confirmed monitor:

```text
screen_x = monitor.left + nx * monitor.width
screen_y = monitor.top  + ny * monitor.height
render_x = (screen_x - client_screen.left) * surface.width / client_screen.width
render_y = (screen_y - client_screen.top)  * surface.height / client_screen.height
```

Virtual-desktop bounds are evidence, not the normalization reference. Monitor
offsets, negative desktop origins and physical-client/render scaling are explicit.
Outside-display/client points are invalid, never clamped into an AOI. A client
straddling monitors is rejected. Windows geometry is re-observed; movement/resize/
DPI change after C aborts. Pixels alone do not prove ETM physical-display identity.

Production and validation share `DisplayMapping.preview_to_client()` for display
normalization, client bounds and render scaling, together with the same `Rect` and
`Point` types. Production `DisplayMapping.to_client()` retains the display/client
approval gate and delegates to that shared geometry path only when both flags are
verified. Validation calls `preview_to_client()` directly for candidate evidence;
it does not forge or bypass production approval flags.
Candidate classification reuses the existing region containment logic and immutable
candidate rectangles. It excludes Tablet and Other, even if another file contains
those regions. Production classification is logged separately and remains
`INVALID_LAYOUT` for valid gaze under the unverified candidate file.

Rear `(960,100,320,90)`, left `(24,561,240,135)` and right `(1016,561,240,135)` are
checked against the current production layout before rendering. No mirror is moved.

## Outputs

```text
data/p6_aoi_validation/<UTC-and-unique-id>/
  raw_gaze.jsonl                 # complete SDK-worker wire records + session origin
  gaze_with_candidate_aoi.jsonl  # validation markers, coordinates, validity, candidate class
  events.jsonl                   # PREPARE, START, END, ABORT, operator confirmation
  summary.json                   # descriptive metrics and acquisition/runtime errors
  mapping_evidence.json          # actual geometry, monitor assertion, device metadata
  p6_gaze.jsonl                  # existing P6 writer output, including production AOI gate
```

Files and run directories are created exclusively. Raw wire records are queued
without blocking the UI and flushed by a dedicated bounded logger. Overflow is
reported and aborts. ESC/disconnect keep the raw stream and events; conversion and
summary then run without CARLA. A hard process/power kill can leave a truncated
last line or lack the final summary; do not treat that as completed validation.

Samples preserve the complete `raw_sdk_json`, identity, bilateral coordinates and
SDK validity, original device/system timestamps and host callback clocks. Candidate
rows add expected target/index/start/elapsed times, normalized valid-eye-mean input,
transformed render-client coordinates, classification/missing reason and production
AOI. No fused gaze is presented as an SDK measurement; the validation fusion policy
is explicit. SDK invalid does not distinguish blink from every other loss cause.

New captures retain bracketed SDK-system/Python-host clock pairs at `START`, every
bounded `MIDDLE` interval, and `END`. A finite affine estimate is only **fitted**;
it remains `timing_quality_status=NOT_VALIDATED`, so `mapping_verified` is false and
it is not promoted to acquisition truth. Segment association uses mapped acquisition
time only after verification. Otherwise it uses explicitly labeled callback receipt
time as a diagnostic, never delayed IPC dequeue time. Unsegmented samples are retained
as `NOT_MEASURED` and excluded from target metrics. Recording-start/end offset and
end-drift fields are null unless the corresponding phase evidence exists.

Per target the summary gives total samples, any-/both-eye SDK-valid counts/ratios,
classifiable count, expected hit count/ratio, confusion counts, invalid/missing
count, no-valid-eye count and median combined normalized coordinates. Hit ratio
denominator is **all samples in that target's interval**. For MISSING, an expected
hit means neither eye is SDK-valid, not merely valid gaze outside an AOI. Zero
samples produce null ratios, never a successful quality result.

`DONE` means the five interaction windows finished, not that AOI accuracy passed.
There is no numerical pass threshold. Production display/client flags,
`aoi_validated` and `hardware_validated` remain false; tablet status remains
`NOT_TESTED`. Approval is a separate researcher/operator decision.

## Offline diagnostics and immutable reanalysis

The diagnostics CLIs do not connect to Tobii or CARLA. Run the local clock probe in
both existing environments; each output path must be new:

```powershell
$stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")
& ".\.venv\Scripts\python.exe" -B -m tools.tobii.clock_diagnostics --output "data\clock_python312_before_$stamp.json"
& ".\.venv-tobii\Scripts\python.exe" -B -m tools.tobii.clock_diagnostics --output "data\clock_python310_before_$stamp.json"
```

Reanalysis also requires a new directory outside the immutable source archive:

```powershell
$stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")
& ".\.venv\Scripts\python.exe" -B -m tools.tobii.reanalyze_validation `
  --input-dir "data\p6_aoi_validation\20261007T125812Z-80e2548d" `
  --output-dir "data\p6_aoi_reanalysis\offline-$stamp" `
  --aoi "config\p6_aoi_candidate.json"
```

The current offline result rejects the legacy five-pair fit with
`INSUFFICIENT_CLOCK_SPAN`: the pair span is `0.063458 s`, below the `1.0 s` minimum.
That one-second minimum is a numerical-conditioning DEV floor for attempting an
affine fit, not a researcher-approved timing-accuracy or acceptance threshold.
The legacy rate `987.8062012756704 ns/us` and drift
`-12193.798724329574 ppm` are diagnostics only. Callback-minus-legacy medians move
from about `29.78 ms` at the start to `454.60 ms` at the end, and the last sample is
about `469.75 ms`; callback receipt is not acquisition truth. All new-method accuracy,
cross-process origin, display/client mapping, AOI and hardware statuses remain
`NOT_VALIDATED`/false.

The next physical clock/AOI check must use the live command above and complete all
five manually initiated screen-visible targets (at least five button marks). Retain
the worker's `START`/`MIDDLE`/`END` pairs so beginning/end offset and drift can be
reported, and run the two local diagnostics again to new `*_after.json` paths. The
Python 3.10 worker and Python 3.12 session metadata must both be returned. These
measurements still do not establish photon timing or physical accuracy by themselves.

## Return evidence and remaining physical checks

After running, provide `summary.json`, `mapping_evidence.json` and `events.jsonl`;
retain both raw and classified JSONL for investigation. Also report the ETM display
selection/calibration procedure, seating position, whether the four feeds stayed
live, and which region was actually viewed in each segment. If sharing outside the
lab, redact serial/host-identifying fields as required by lab policy.

Still unverified by this implementation session: live Tobii plus the CARLA cockpit,
physical monitor identity, calibration accuracy, real AOI hit distributions, real
disconnect behavior during this sequence, and photon/physical timing. Tablet/Other
approval and research-quality thresholds remain out of scope. Browser/Playwright,
moving CARLA validation and background/general agents were not used.
