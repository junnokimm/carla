# P6 Measurement And Tablet Backend

## Scope and status

Synthetic gaze, timing, sidecar persistence, AOI classification, tablet state, HTTP transport, and a static HTML/JavaScript client are implemented. The operator subsequently reported successful real Tobii Pro Nano discovery and capture. The archived five-window cockpit run can be reanalyzed descriptively, but its software clock fit is not timing validation and it is not AOI/calibration accuracy approval. Tablet model and OS remain undecided. See [bounded cockpit AOI validation](P6_AOI_Validation.md) and [Tobii connection preparation](P6_Tobii_Connection_Preparation_20261007.md).

Integrated study REAL mode still fails closed with the existing `BLOCKED_MODEL_SDK_UNCONFIRMED` gate; development discovery/capture is a separate explicit command, not automatic study activation. Synthetic records always contain `synthetic: true`; synthetic mode is never a fallback from real mode. Candidate AOI version `p6-candidate-unvalidated-2` matches the user-approved +480 rear-mirror offset: `(960, 100, 320, 90)` at 1280x720, ending exactly at x=1280 without clipping. Display/client mapping flags remain false and external tablet AOI status remains `NOT_TESTED` until measured mapping is supplied.

## Runtime binding

`--p6-config PATH` is optional and defaults off. Integrated P6 requires the existing persisted study-run arguments. `ResearchNoARunner.session()` opens the research log first, then gives P6 the exact `SessionClock.origin`; real-source preflight occurs before `spawn_actor()`.

P6 subscription and tablet socket startup occur only after automation interaction initialization succeeds. During shutdown, the existing NoA manual transition and maximum-brake command run first. P6 then unsubscribes, drains and closes its writer, closes tablet transport, and reports worker errors without changing driving control policy. Gaze writer close is bounded (five seconds by default): timeout raises while the live worker still owns the file. A completed drain exclusively writes `<gaze-sidecar>.shutdown.json` with `DRAINED` or `WRITE_FAILED`, written/unconfirmed/overflow ranges, pending/in-flight state and file closure. Its persistence basis is Python flush, not power-loss durability. Existing viewer, hero, and research CSV cleanup remains authoritative.

Sidecars are created exclusively and separately from vehicle CSV files:

- `p6_gaze_<run_id>.jsonl`
- `p6_tablet_<run_id>.jsonl`

The gaze metadata record includes the immutable session origin, affine clock provenance, all synchronization pairs, rate, drift, residual bounds, AOI version, and fusion policy. Fitted and verified are distinct: physical mappings remain `NOT_VALIDATED`, so verified acquisition session time stays null even when a bounded estimate can be reported. Each gaze record preserves bilateral coordinates and validity, invalid reasons, raw source timestamp/unit/clock kind, source metadata, receipt monotonic/UTC/index, mapped acquisition time or null, diagnostic estimated time or null, AOI, and duplicate/backward/gap flags. Queue overflow is represented by count, first/last source sequence, and first/last receipt monotonic time.

## Gaze source interface

`GazeSource` exposes only:

- `discover()`
- `select(identifier)`
- `calibrate()`
- `subscribe(callback)`
- `unsubscribe()`
- `status`

The study runtime currently uses `SyntheticGazeSource`, which has its own cadence thread. Its calibration status is `SDK_SUCCESS_SYNTHETIC` while AOI validation remains false. `TobiiProcessSource` implements the same source methods for bounded development capture through private subprocess stdout IPC. SDK calls stay in a separate Windows x64 CPython 3.10 worker; P6 decoding/writing stays in Python 3.12. `calibrate()` fails explicitly until calibration is verified; external Eye Tracker Manager calibration does not silently mark the source or AOIs as approved. No automatic REAL-mode wiring or synthetic fallback has been added.

## Tablet UI API

The server defaults to loopback and an ephemeral port. Configuration rejects non-loopback binding without an explicit token; use a random token for deployment. The index and static assets are public. API GET and POST routes require `Authorization: Bearer <token>`; only `/media` additionally accepts a `token` query parameter. The client takes its token from the URL fragment and removes the fragment from browser history. JSON bodies are limited to 32768 bytes.

Routes:

- `GET /api/state`
- `POST /api/ack`
- `POST /api/touch`
- `POST /api/pause`
- `POST /api/resume`
- `POST /api/video-metadata`
- `POST /api/media-ended`
- `POST /api/error`
- `POST /api/sync-mark`
- `POST /api/reset`
- `GET /media` with single byte-range support
- `GET /` and `GET /assets/<path>` when a static directory is configured

`GET /api/state` returns `run_id`, `run_epoch`, current trial UUID/session epoch, circles, condition, module, stage, media filename/version/existence/SHA-256, pause state, and error. Browser `performance.now()` and `timeOrigin` are preserved. Browser-mapped session time remains null with `RECEIPT_ONLY_UNVERIFIED`, including after five or more one-way sync marks. The generic gaze affine-mapping utility does not establish browser synchronization; a validated browser clock handshake and display timing measurement remain separate work.

Module 1 `NO_SURT` is `BLANK`; `SURT` uses development circle stimuli and has no feedback/reward path. An accepted touch records correctness and starts a new trial; duplicate, stale, pre-show, and paused touches are retained with dispositions. Module 2 `MANUAL` and `NOA_L2` select the same explicit offline video. `prepare`, `START_REQUESTED`, `STIMULUS_SHOWN`, and `MEDIA_PLAYING` are distinct. Media cannot be prepared when absent, and browser metadata must establish duration >= 780 seconds, muted, non-looping, playback rate 1.0 before playing acknowledgement. Early ending before 780 seconds while playing records `MEDIA_EARLY_END`.

## Commands

```powershell
& ".venv\Scripts\python.exe" -m src.p6.cli preflight --config config/p6_synthetic_example.json
& ".venv\Scripts\python.exe" -m src.p6.cli preflight --config config/p6_real_blocked_example.json
& ".venv\Scripts\python.exe" -m src.p6.cli validate --config config/p6_synthetic_example.json
& ".venv\Scripts\python.exe" -m src.p6.cli synthetic --output data/p6_cli.jsonl --run-id development-p6 --samples 60 --sample-hz 60
& ".venv\Scripts\python.exe" -m src.p6.cli calibration-guide
& ".venv\Scripts\python.exe" -m src.p6.cli serve --config config/p6_synthetic_example.json --run-id development-tablet
$stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")
& ".venv\Scripts\python.exe" -B -m tools.tobii.clock_diagnostics --output "data\clock_python312_$stamp.json"
& ".venv-tobii\Scripts\python.exe" -B -m tools.tobii.clock_diagnostics --output "data\clock_python310_$stamp.json"
& ".venv\Scripts\python.exe" -B -m tools.tobii.reanalyze_validation --input-dir data\p6_aoi_validation\20261007T125812Z-80e2548d --output-dir "data\p6_aoi_reanalysis\offline-$stamp" --aoi config\p6_aoi_candidate.json
```

`calibration-guide` emits two-second AOI prompt instructions and five sync-marker requests. It does not claim or perform real device calibration. The standalone `serve` command supplies the bundled static client, configures the requested module/condition, and persists tablet events through `TabletEventSidecarWriter` during the run. The integrated `P6Runtime` instead serializes its tablet ledger at finalization; crash-safe streaming persistence and automatic experiment-to-tablet module/trial orchestration are not established by the current integration tests.

## Recovery verification and remaining gates

- Browser/Playwright QA was deliberately omitted for this recovery. No additional agent/background task was launched. Direct Python HTTP checks used a short-lived loopback server, two-second request timeouts, and explicit server shutdown.
- The earlier recovery reproduced `803 passed / 3 failed`. The user subsequently finalized the production offset at +480. Expectations now use x=960 at width 1280 and x=1120 at width 1600; the current production layout was not changed by the Tobii preparation work. A candidate-geometry test checks all three mirror rectangles while retaining unverified physical mappings.
- Direct HTTP QA passed 29 requests covering bundled static assets, API authentication, blank/SURT state, shown/touch/next-trial, pause/resume, duplicate/stale events, reset, receipt-only sync marks, video metadata/playing, byte ranges, and early-ending errors. The media transport used a non-video fixture; it does not prove video decoding or playback.
- Unit/integration coverage includes synthetic gaze records, fitted-versus-verified clock behavior, phased clock collection, shared production/validation AOI coordinates, queue overflow, bounded writer shutdown/reporting, real-source fail-closed behavior before vehicle spawn, and shutdown integration using fake CARLA objects. This is not a live CARLA/Tobii/tablet validation.
- The client source implements state polling, circle input, video playback requests, visibility events, and sync marks. Actual browser execution, paint/input timing, reconnect/reload behavior, and tablet playback remain unverified. In particular, `crypto.randomUUID()` requires a secure-context deployment check before non-loopback tablet access; a plain LAN HTTP URL must not be assumed sufficient.
- Tobii work remaining: run the bounded physical cockpit AOI sequence after ETM setup/calibration with all five manual marks, retain `START`/`MIDDLE`/`END` clock pairs, and compare beginning/end offset and drift with clock diagnostics from both Python environments. Then inspect descriptive gaze distributions before any separate mapping approval. The separate Python 3.10/SDK environment exists and raw acquisition success is operator-reported; this does not establish timing or AOI accuracy. External-tablet tracking remains unverified. Do not silently move the approved mirror layout.
- Tablet work remaining: select hardware/OS later, establish authenticated LAN/secure-context delivery, supply and validate the final offline video, verify touch/playback/visibility/reconnect behavior on the device, and validate experiment lifecycle/persistence integration. Tablet model/OS remain undecided.
