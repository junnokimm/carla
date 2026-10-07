# DriverView rear mirror and AOI layout validation

## Scope and structure

Previously, full DriverView spawned `front/left/right` RGB cameras and drew two 320x180 overlays at the upper corners. Research front-only independently spawned one front camera. Full mode now spawns `front/rear/left/right`; front-only still spawns only one front camera and draws no mirrors. No new full-mode CLI flag was invented: omit `--front-camera-only` to select full mode.

Changes are limited to mirror sensor/feed/composition, camera diagnostics metadata, tests and validation tools. No NoA controller, research CSV writer, P5 exit logic, route, gain, safety cap, prime or shutdown behavior was changed.

## Geometry and candidate AOIs

Default window is 1280x720. Coordinates are content-area pixels, `(x, y, width, height)`, excluding the operating-system window border.

| Region | Rectangle | Capture |
|---|---|---|
| Rear mirror | `(480,100,320,90)` | 640x180, FOV 90 |
| Left mirror | `(24,561,240,135)` | Existing 480x270, FOV 100 |
| Right mirror | `(1016,561,240,135)` | Existing 480x270, FOV 100 |
| Front | Full 1280x720 underlying feed | Existing 1280x720 |

`calculate_layout(config)` returns immutable `ScreenRect` values for rendering and future AOI consumers. Its `bounds`, `position` and `size` properties share one definition; rendering does not duplicate the coordinates. Layout is computed from configured window dimensions, side dimensions/margin and `RearMirrorConfig`. These are candidate AOIs, not final Tobii calibration or validity criteria. A future forward AOI must exclude mirror rectangles rather than treating the entire background as a non-overlapping forward AOI.

The rear camera is vehicle-relative `x=-2.5, y=0, z=1.3, yaw=180`, looking directly behind the vehicle. It is distinct from side yaw -150/+150 and has no lateral offset. The native rear image has the same aspect ratio as its overlay. All mirror feeds use horizontal reflection, consistent with the existing camera-based side mirrors.

Protected configuration remains:

- Front transform `(1.4,0,1.3,pitch=-2)`, FOV 100.
- Cockpit transform `(0.10,-0.35,1.20,pitch=-1.5)`, FOV 105.
- HUD anchor `(0.62,0.62)`, mode-text vertical ratio `0.58`.
- Existing side-camera transforms and sensor resolutions.
- V toggles/recreates only the front camera. Rear/side sensors remain unchanged.

## Files

- `src/scenario/driver_view.py`: rear config/sensor, four-feed composition, shared layout.
- `src/scenario/mirror_layout.py`: immutable rectangle/layout calculation.
- `src/scenario/research_noa_view.py`: full composition diagnostic label becomes `front_rear_left_right_4_rgb`; one-camera path retained.
- `src/scenario/research_camera_metadata.py`: rear requested sensor metadata.
- DriverView/cockpit/HUD and research camera tests were updated for four feeds and extended for cleanup, disjoint rectangles, orientation and protected configuration.
- `tools/validate_mirror_layout.py`: stationary front/full/front capture and measurement through the existing Research runner.
- `DESIGN.md`: candidate mirror placement notes.

## Verification

- Baseline: 759 tests passed.
- Initial focused red phase: 12 expected failures for the new camera/layout contract.
- Final full suite: **764 passed in 2.48s**.
- Ruff check of changed production/test/tool files: passed.
- `git diff --check`: passed; preexisting CRLF conversion warnings remain.
- LSP unavailable: basedpyright is not installed and its installation was previously declined. No clean static type-check claim.
- Unit tests cover rear orientation/attributes, default/custom layout, four-camera and partial-fourth-camera cleanup, all mirror horizontal flips, front-only isolation, unchanged front/cockpit/HUD values and V preserving mirror actors.

### Actual CARLA screenshots

Final evidence directory: `data/mirror_validation/20261007T041525Z-0a0ad2/`

- `full/cockpit.png`: real CARLA four-camera composite after using the normal V event to select cockpit.
- `full/rear_native.png`: actual rear sensor image, horizontally mirrored as displayed.
- `front_a/cockpit.png`, `front_b/cockpit.png`: front-only comparisons.
- Each run has stdout, existing telemetry/events CSV and camera diagnostics. `source_manifest.json` and `source_unchanged.txt=True` bind evidence to execution source. No executable source was edited after the final run.

The screenshots were opened and inspected. In this stationary Town04 spawn-107 cockpit view, the rear image shows the unobstructed center-rear road, not the car body. The rear overlay sits above the forward road; side overlays occupy the lower left/right margins. Central road/lane markings, the vehicle instrument cluster and the existing HUD remain visible. This is a checked layout for this view and resolution, not a claim covering all roads, seating positions or final AOI suitability.

The P5 logic was not enabled during these stationary comparisons. Non-overlap with its unchanged top-center text area was checked geometrically in tests and against the user's supplied reference screenshot; no P5 behavior was modified.

### Performance, front-only / full / front-only

Each run requested 8 seconds, used unchanged control configuration with Module 2 MANUAL and scripted brake 0.5, and made zero active NoA control steps. Editor was restored/nonminimized; no world/OS/GPU/editor settings were changed. Quality was not established.

| Metric | Front A | Full | Front B |
|---|---:|---:|---:|
| RGB sensors | 1 | 4 | 1 |
| World frames / wall second | 63.63 | **32.19** | 62.65 |
| Scheduler updates / wall second | 29.03 | **14.31** | 28.55 |
| Driver-loop mean, ms | 34.10 | **69.19** | 34.62 |
| Render mean, ms | 2.19 | **2.94** | 2.16 |
| Maximum observed speed, km/h | 0 | 0.078 | 0.061 |

Full-run callbacks were front 264, rear 269, left 266 and right 264. This confirms all four live feeds. Camera counts are concurrent owned feeds; the V toggle recreates front once, so lifetime actor creations differ.

This A/B/A result shows a material total cost of full mirrors compared with front-only. It does NOT isolate rear-camera cost from the two side cameras, since the comparison is 1 versus 4, not old 3 versus new 4. The max loop intervals include first-frame V camera recreation and one screenshot save; this is a short validation, not a steady-state benchmark or proof of sufficient moving NoA control cadence. No performance tuning outside the mirror scope was done.

An earlier batch `20261007T041359Z-9c9fac` stopped before full-view execution because the existing transmission prime rejected a still-settling vehicle at 2.578km/h. That failed record is preserved. The validation tool now waits passively, within the existing timeout and stationary threshold, before entering the unchanged prime. It sends no settling control command and does not relax the guard. The subsequent complete A/B/A batch succeeded. Final independent actor check: 0 vehicles, 0 RGB sensors.

## Commands

The exact stationary screenshot/performance comparison used here:

```powershell
& ".venv\Scripts\python.exe" -m tools.validate_mirror_layout
```

For a full-mirror manual live view, omit `--front-camera-only`. Use V to select the unchanged cockpit camera. This is developer preview, not active NoA validation; retain braking/normal exit precautions.

```powershell
& ".venv\Scripts\python.exe" -m src.scenario.research_noa `
  --live-smoke --spawn-index 107 --duration 8 --camera-diagnostics `
  --automation-module MODULE_2 --automation-condition MANUAL `
  --activation-center-tolerance-m 0.2 --activation-heading-tolerance-rad 0.1 `
  --driver-brake-threshold 0.05 --button-deactivation enabled `
  --target-speed-kmh 10 --speed-deadband-kmh 0.2 `
  --acceleration-gain 0.1 --braking-gain 0.1 --integral-gain 0.02 `
  --max-throttle 0.4 --max-brake 0.5 `
  --lateral-error-gain 0.2 --heading-error-gain 0.5 `
  --lateral-deadband-m 0.1 --heading-deadband-rad 0.05 --max-steering 0.15
```

If the normal CLI prime rejects settling, do not weaken it; use the stationary validation tool or retry once the setup is suitable. Add `--front-camera-only` only when intentionally returning to the single-feed development view.

## Remaining concerns

- Full mode roughly halved world/loop cadence in this short stationary comparison. Moving control cadence and experiment performance need separate validation before adopting four-camera mode for participant runs.
- Exact mirror sizes/placement and forward AOI definition still need local seating/display/Tobii checks. Current rectangles are stable code-defined candidates, not final AOI approval.
- Camera-based views are not physical cockpit reflection. The rear exterior center camera is intentional, and only its observed rear coverage in this scene was validated.
- The existing CARLA client 0.9.16 / simulator 294096e compatibility warning remains.
