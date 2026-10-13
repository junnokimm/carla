# Tobii Pro Nano: connection preparation, not hardware validation

## Subsequent operator update

The operator reports that `.venv-tobii` / Python 3.10 x64 and SDK 2.1.0 are now
installed and real Nano discovery/capture succeeded. An archived five-window run is
available for descriptive reanalysis, but its clock fit is rejected by the current
minimum-span rule and all timing/AOI/hardware accuracy remains unvalidated. The
earlier missing-environment notes below are historical, not current blockers.
The next tool and exact command are in [P6_AOI_Validation.md](P6_AOI_Validation.md).

## Authority and scope

The V5 Source of Truth remains authoritative, especially sections 9, 10 and 11.
These tools are internal development smoke tests, not participant sessions.
Tablet model, OS and final transport/platform remain undecided. Nothing here
changes NoA/P5, vehicle control ownership, camera transforms or FOV.

The current production rear-mirror formula is center +480, not +500.
At 1280x720 its rectangle is `(960, 100, 320, 90)` and right edge is 1280.
At 1600x900 it is `(1120, 125, 320, 90)`. The candidate AOI JSON shares
the default mirror rectangles; version 2 still has all physical mapping flags false.
Geometric consistency is not approval of gaze accuracy.

## Official compatibility findings (checked 2026-10-07)

1. [Supported trackers](https://developer.tobiipro.com/tobiiprosdk/eyetrackercompatibility.html)
   explicitly includes Tobii Pro Nano; firmware compatibility must still be checked
   for the actual unit.
2. [Supported platforms/languages](https://developer.tobiipro.com/tobiiprosdk/platform-and-language.html)
   lists Windows 10/11 **64-bit** and **Python 3.10**. Python 3.12 is not the
   documented supported binding for this SDK release.
3. [Official Python package](https://pypi.org/project/tobii-research/2.1.0/)
   is maintained by TobiiProSDK/Tobii AB. The Windows wheel is
   `tobii_research-2.1.0-cp310-cp310-win_amd64.whl` (no CPython 3.12 wheel).
   Its published SHA-256 is
   `9b6a09cde04225a3fd63a0c6264810f65fa20192f98c6f318577ae5848a46bc3`.
4. [Getting started](https://developer.tobiipro.com/python/python-getting-started.html)
   and [step-by-step](https://developer.tobiipro.com/python/python-step-by-step-guide.html)
   specify `tobii_research.find_all_eyetrackers()` and
   `EyeTracker.subscribe_to(EYETRACKER_GAZE_DATA, callback, as_dictionary=True)` /
   `unsubscribe_from(...)`. Discovery does not require us to start calibration,
   change display configuration, or change gaze output frequency.
5. [Eye Tracker Manager integration](https://developer.tobiipro.com/eyetrackermanager/etm-sdk-integration.html)
   documents physical display setup (`--mode=displayarea`) and user calibration
   (`--mode=usercalibration`), explicit serial/address and screen selection.
   ETM is the prepared calibration route; it is not a tablet platform decision.
   SDK-native calibration is available through
   [ScreenBasedCalibration](https://developer.tobiipro.com/tobii.research/python/reference/2.1.0.3-alpha-gd8b35e1b/classtobii__research_1_1ScreenBasedCalibration.html):
   enter mode, present targets, collect data, compute/apply, leave mode.
   This work does not fabricate a target-presentation UI or call collect without
   actual displayed targets.
6. [Timestamps/timing](https://developer.tobiipro.com/commonconcepts/timestamp-and-timing.html):
   `device_time_stamp` is the device clock; `system_time_stamp` is the SDK's
   host-clock time for acquisition, synchronized by the SDK. Both are microseconds
   from arbitrary origins, **not Unix time**. `get_system_time_stamp()` queries the
   SDK host clock. Neither clock is assumed identical to Python monotonic time.

The reference API URL contains its published alpha/build suffix; package pinning
uses the stable PyPI 2.1.0 wheel, not an invented matching alpha package.

## Historical environment checks and manual setup

Read-only checks found:

- `py -0p`: only Python 3.12, at
  `C:\Users\nexta\AppData\Local\Programs\Python\Python312\python.exe`.
- CARLA `.venv`: Python 3.12.10 x64; `tobii_research` absent.
- No Python 3.10 was found in the launcher or checked standard Python/uv locations.
- The default ETM executable path was absent. A custom ETM installation has not
  been ruled out; `--etm` accepts its actual executable path.
- No system Python, SDK, package manager, or ETM was installed by this work.
  The existing CARLA environment was not modified.

Python 3.10 remains the SDK prerequisite, but the operator reports that the separate
environment now exists. Do not reinstall it as part of validation and do not run the
SDK inside CARLA's `.venv`. These commands are retained only as historical setup:

```powershell
winget install --id Python.Python.3.10 --exact --source winget --scope user --architecture x64
py -3.10 -c "import sys, struct; print(sys.version); print(struct.calcsize('P') * 8)"
py -3.10 -m venv .venv-tobii
& .\.venv-tobii\Scripts\python.exe -m pip install --only-binary=:all: -r tools/tobii/requirements.txt
```

Proceed only if the interpreter reports 3.10 and 64 bits. Read-only `winget show`
confirmed this package currently offers the PSF Python 3.10.11 amd64 installer,
not the latest source-only Python 3.10 security release;
follow lab IT policy for the approved 3.10 distribution. Do not downgrade 3.12.
Install ETM through Tobii's official installer when preparing display/calibration.

## A. Read-only discovery

After the separate environment is ready and Nano is connected:

```powershell
& .\.venv-tobii\Scripts\python.exe -B -m tools.tobii.discovery_smoke
```

Alternatively use the existing Python as a supervisor only:

```powershell
& .\.venv\Scripts\python.exe -B -m tools.tobii.discovery_smoke --python .venv-tobii/Scripts/python.exe
```

Discovery has a 10-second child-process deadline. It does not import CARLA,
spawn vehicles, calibrate, subscribe to gaze, or alter device settings.
Output includes `TOBII_DISCOVERY=<status>`, actual device count and each SDK-provided
model, serial number, address, device name and firmware version.
`OK` requires at least one discovered Nano. `NO_DEVICE`, `NO_NANO`,
`SDK_NOT_INSTALLED`, `UNSUPPORTED_RUNTIME`, `SDK_IMPORT_FAILED`, `SDK_ERROR`,
`TIMEOUT` and runtime/worker failures exit nonzero. Discovery success would mean
only discovery, never calibration or research-quality gaze validation.

## B. Calibration preparation

First configure the actual display using ETM's physical setup, measuring the
screen and tracker position. Select the real ETM screen number, not a guessed one.
Close a pre-existing ETM instance before interpreting the launched process exit code.

```powershell
# Dry command plan only; does not open ETM or calibrate.
& .\.venv-tobii\Scripts\python.exe -B -m tools.tobii.calibration_smoke --serial <DISCOVERED_SERIAL> --screen <ETM_SCREEN_NUMBER>
# Only after display setup and operator readiness:
& .\.venv-tobii\Scripts\python.exe -B -m tools.tobii.calibration_smoke --serial <DISCOVERED_SERIAL> --screen <ETM_SCREEN_NUMBER> --execute --output data/tobii_calibration_001.json
```

`--execute` has a 180-second external-tool deadline and creates a new result file
without overwriting. ETM exit 0 is recorded as `ETM_EXIT_OK_NOT_AOI_VALIDATED`;
cancellation/failure/missing ETM are explicit. The operator must inspect ETM's
actual result. Raw smoke records remain `calibration_status=NOT_VERIFIED`, because
ETM process exit alone does not establish this session's physical AOI accuracy.

## C/D. Bounded raw gaze and JSONL

```powershell
& .\.venv-tobii\Scripts\python.exe -B -m tools.tobii.gaze_smoke --serial <DISCOVERED_SERIAL> --seconds 15 --output data/tobii_raw_001.jsonl
```

Allowed duration is 10-20 seconds. A supervisor terminates a stalled SDK process
after duration +10 seconds. No raw samples means `NO_SAMPLES`, never success.
Disconnect/SDK stream error and bounded-queue drop counts remain in the output.
Each emitted record is flushed and the output is exclusively created, retaining
partial data on timeout. A killed worker can leave an incomplete final line;
conversion rejects malformed input rather than pretending the recording is complete.

Fields preserve the SDK dictionary **before** normalization as `raw_sdk_json`:

- `device_time_stamp`, `system_time_stamp`;
- `left_gaze_point_on_display_area`, `right_gaze_point_on_display_area`;
- `left_gaze_point_validity`, `right_gaze_point_validity`;
- all additional SDK dictionary fields, including pupil/origin data when present.

The envelope adds host monotonic/wall receipt clocks, callback sequence, actual
device identity, calibration state and `synthetic=false`. `raw_sdk_json` is a string
so SDK NaN/Infinity values remain recoverable inside that string while the outer
JSONL remains valid JSON. Nonfinite coordinates become invalid downstream, not
fabricated valid gaze. There is no SDK combined-gaze field invented here.
Invalid samples are not labeled blink versus range loss without additional evidence.

## E/F. Existing P6 session mapping, writer and AOI observation

```powershell
& .\.venv\Scripts\python.exe -B -m tools.tobii.import_gaze --input data/tobii_raw_001.jsonl --output data/p6_tobii_001.jsonl --run-id development-tobii-001 --aoi config/p6_aoi_candidate.json
```

By default this uses the raw smoke file's session origin. To join an actual study
session later, supply both `--origin-monotonic-ns` and `--origin-utc-ns` from that
exact session; do not use the import time. Clock pairs bracket SDK host-clock reads
with worker Python monotonic timestamps on the same PC. New captures record `START`,
periodic `MIDDLE`, and `END` phases. The affine mapping stores pairs/residuals/drift
provenance, but a finite fit is distinct from verification: timing quality is always
`NOT_VALIDATED`, `mapping_verified` is false, and receipt timestamps remain
diagnostics rather than acquisition truth. Device time is preserved separately while
SDK system time is the mapped source. Original raw JSONL retains bracket bounds and
terminal/drop status; keep it with the converted sidecar. Recording-start/end offset
and end-drift are absent unless those phase records exist.

Before and after the next physical run, write new local diagnostics from both
interpreters (the command never imports the SDK or connects to hardware):

```powershell
$stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")
& .\.venv\Scripts\python.exe -B -m tools.tobii.clock_diagnostics --output "data\clock_python312_before_$stamp.json"
& .\.venv-tobii\Scripts\python.exe -B -m tools.tobii.clock_diagnostics --output "data\clock_python310_before_$stamp.json"
# Run the exact five-target cockpit command in P6_AOI_Validation.md.
& .\.venv\Scripts\python.exe -B -m tools.tobii.clock_diagnostics --output "data\clock_python312_after_$stamp.json"
& .\.venv-tobii\Scripts\python.exe -B -m tools.tobii.clock_diagnostics --output "data\clock_python310_after_$stamp.json"
```

The physical run must contain at least five manual screen/button marks and preserve
the phased pairs needed to report beginning/end offset and drift. Both interpreter
diagnostics and the physical archive remain `NOT_VALIDATED` until reviewed; none of
these commands measures photon onset.

`TobiiProcessSource` implements discovery, explicit serial selection, status,
subscribe/unsubscribe and raw-sample delivery via private stdout IPC. Native SDK
calls never enter the CARLA interpreter. Subscribe starts a reader thread and
returns; worker failure/EOF changes source status, with no vehicle control action.
Stop uses bounded waiting. This source is a bounded development adapter, **not yet
wired into the study runtime**: integrated REAL mode stays fail-closed, and
`calibrate()` explicitly refuses to claim calibration success. No synthetic fallback.

Candidate AOI currently returns `INVALID_LAYOUT` because physical mapping is
unverified, even though mirror geometry is consistent. After physical setup,
use a separately versioned measured AOI file and observe:

1. Forward ~2 s; rear mirror ~2 s; left mirror ~2 s; right mirror ~2 s.
2. External tablet ~2 s, only if it is actually trackable.
3. Close eyes; look outside tracking range; return to the screen.
4. Check raw validity, missing/no-AOI codes and correct reacquisition. Do not map
   unknown/outside samples to Other. Other requires a predefined region.

External tablet mapping stays `NOT_TESTED`. SDK validity alone cannot identify
the cause of all missing samples. AOI, cross-process timing, real disconnects and
actual calibration remain physical validation gates.

## Initial preparation evidence limits (before the operator update)

Software tests exercise fake SDK discovery, SDK absence, unsupported interpreter,
raw identity/timestamp preservation, invalid coordinates, duplicate/backward time,
partial disconnect capture, process stop/EOF and existing P6 writer conversion.
They are not Tobii hardware tests. Python 3.10 syntax is checked separately;
actual 3.10/native-wheel execution is pending installation. Browser/Playwright,
physical tablet QA and moving CARLA validation are excluded from this work.

Direct CLI QA observed `UNSUPPORTED_RUNTIME` on the real current Python 3.12,
`RUNTIME_UNAVAILABLE` for a missing executable, help output, and rejection of a
zero-second gaze duration. A supported-runtime metadata stub exercised the actual
absent-import path (`SDK_NOT_INSTALLED`); a fake SDK exercised `NO_DEVICE`.
Neither is a physical discovery attempt. Calibration dry-run returned `NOT_RUN`.
A short fake SDK capture was saved and converted through the public import CLI:
one partial sample and its extra raw pupil field survived, disconnect retained a
nonzero result, candidate classification stayed invalid, and hardware validation
remained false. Temporary QA files were removed by their temporary-directory scope.
