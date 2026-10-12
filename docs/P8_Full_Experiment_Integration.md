# V5-P8 full-flow orchestration foundation

Phase2 current additions: [P5 timing, general indicator requests, traffic-light evidence](P8_Phase2_P5_LaneChange_TrafficLights.md).
Module P5 now requires explicit M1/M2 eligibility windows. General Z/X requests
are logged independently of P5; actual lane-change execution remains BLOCKED.
Actor-specific signal-ignore0 is reasserted, but red-light stopping is UNVERIFIED.

## Current authority: Traffic Manager replacement

The final operator now uses TM, not the historical custom speed-hold path below.
See [P8_Traffic_Manager_Integration.md](P8_Traffic_Manager_Integration.md) for the
current control contract, explicit V5 changes, API limitations, full-duration
command and physical-validation blockers. No P8 110 km/h guard or required
`--noa-max-throttle` remains. Steering disengages; exit confirmation uses indicators.
P5 assisted execution is BLOCKED; physical driving is UNVERIFIED; readiness stays blocked.
Sections describing activation-speed hold or NoA110 below are historical records,
not current operator instructions. Standalone custom-controller diagnostics remain.

## Current operator workflow: simplified driving execution

The sections below retain the earlier diagnostic-mode history. The current
interactive entrypoint is **`python -m src.p8.operator_cli`**, not a new chain of
`p8_smoke` subprocesses. See [P8_Field_Coverage_Matrix.md](P8_Field_Coverage_Matrix.md)
for every V5 section 11 field and its connected/separate/missing boundary.

The latest user-confirmed workflow supersedes the earlier administrative UI.
The same `src.p8.orchestrator.ExperimentSession` and `Stage` remain authoritative.
Training, M1 and M2 borrow one context, logger and SessionClock; each Runner owns
its vehicle/viewer lifecycle. Training is one continuous planned-600-second run.
Module execution now defaults to one **600-second M1 run and one 780-second M2 run**.
Duration completion is not full experimental completion or participant readiness.
Explicitly configured P5 exits are connected; route approval, P6 and moving hazard
remain BLOCKED/UNVERIFIED as applicable. No new controller, writer, route or hazard
scenario was introduced.

### Implemented command (not executed against CARLA in this work)

For the P5-connected full-duration command, required explicit route/behavior
selections, classification and validation limits, see
[P8_P5_Operator_Integration.md](P8_P5_Operator_Integration.md). The basic command
below intentionally leaves P5 NOT_CONFIGURED; it is not a P5 validation command.

From the repository root, after independently checking the live empty-road map,
spawn, and simulator state:

```powershell
$safeSpawnIndex = [int](Read-Host "Verified safe Driving-lane spawn index")
$tmPort = [int](Read-Host "Dedicated default-settings Traffic Manager port")
$tag = [guid]::NewGuid().ToString("N")
& ".\.venv\Scripts\python.exe" -m src.p8.operator_cli `
  --mode dev-rehearsal --participant-id "DEV-$tag" --run-id "P8-$tag" `
  --m1 NO_SURT --m2 NOA_L2 --ack-dev-live --spawn-index $safeSpawnIndex `
  --traffic-manager-port $tmPort `
  --training-duration 600 --output-root data/p8_operator
```

The ID/assignment is supplied through CLI and registered automatically. No paper
identity matching or registration confirmation screen is performed. For an
existing assignment table use `--assignment-file <path>` instead of `--m1/--m2`;
the supplied participant ID must match a CSV row. Existing IDs/runs are rejected,
not overwritten or automatically resumed. `--rear-mirror` explicitly restores ON;
omitting it means OFF. This boolean differs from the older research diagnostic
CLI's `--rear-mirror enabled` syntax. Restoring a camera does not validate AOI data.

### Historical: NoA activation-speed hold (superseded for P8)

The integrated operator no longer accepts `--noa-desired-speed-kmh`. No fixed
cruise default is selected: the target is pending (`None`) until an actual
successful NOA_ACTIVE transition. `--noa-max-throttle` still specifies the existing
actuator cap, not a target speed. P8 also has no `--dev-speed-limit-kmh` or
`--ack-high-speed-dev`; unrelated diagnostic entrypoints remain unchanged.

`OperatorRuntimeConfig` owns one immutable `NoAControlConfig` and one existing
`ResearchSpeedPolicy(110.0, noa_only=True)`. Training, M1 and M2 pass those exact
objects to the existing `ResearchNoARunConfig` / Runner / Session. Speed checks
use the actual committed MANUAL/NOA_ACTIVE state, not DEV/Participant status.

On a successful transition, after the existing backend/gate commit and before the
first control tick, the runtime captures the existing CARLA snapshot observation.
Its measured speed becomes the target of the same longitudinal PI instance.
Retargeting clears the existing integral/time state without changing gains or
lateral control. Rejected requests create no capture and leave the previous
target untouched. Disengagement/reactivation uses a new snapshot and resets PI
state again; the target is not continuously updated while NoA remains active.

The existing research event CSV records `activation_speed_hold`, containing
captured speed, applied target, APPLIED/BLOCKED status and the observation's common
host/CARLA timestamp. `auto_state` and the target event share that envelope; pending
causal events are flushed before later violation telemetry/events. The initial
`speed_policy_effective` record describes pending configuration, not an already
applied target. CSV columns and raw timestamp meanings are preserved.

MANUAL has **no numeric speed cap**, including at INITIAL and FINAL, but speed is
still observed/logged and NaN/Inf still fails closed. MANUAL throttle remains1.0.
NoA checks actual observed speed before a backend control step: exactly110km/h
is permitted, above110km/h enters the existing fail-closed shutdown. M2 initially
ON is checked immediately after initialization and before the drive; brake
disengagement changes to MANUAL before the scheduler check. FINAL uses actual mode.
Captured targets are finite and within[0,110]; NoA throttle cap is in(0,1].
The110 ceiling is not a cruise default or proof against physical overshoot between
observations. Gains, brake/steering caps and physics are unchanged.

The effective policy payload records `noa_only:true`, target source
`ACTIVATION_SNAPSHOT` and profile `NOA_ONLY_ACTIVATION_SPEED_HOLD`, with
`DEV_UNVALIDATED` validation status. This is a policy
scope label, not another controller or participant authorization. CSV columns and
raw timestamps remain unchanged. Participant remains unconditionally blocked by
readiness. The older diagnostic modes below retain their own legacy/common guards.

#### Edge cases, not invented starting-speed policies

- At a measured standstill the captured target is0, which the existing PI supports.
  It is not a default/minimum speed, special parking brake, or automatic launch.
- M2 NOA_L2 actually starts from internal MANUAL runtime state and initializes
  through the same successful NoA transition after vehicle preparation. A stationary
  capture therefore targets0; software NoA ownership does not prove moving driving.
  A physically valid rolling start/launch protocol is not provided or assumed.
- Exactly110 is accepted as a captured value. Above110 or a non-finite snapshot
  is recorded BLOCKED with no applied target, then follows the existing speed-error
  shutdown. No speed-based activation gate, clamping or new recovery rule was added.
- A later overspeed while holding any target still reaches the unchanged guard.
  Physical speed retention and safe overspeed recovery remain unverified.

### Vehicle preparation before participant input

The reported Run `P8-655916c440cb4fb2ae035a3d0e0d246e` records successful training
followed by M1 startup failure: Gear0 at3.905859375km/h. The old call path read
speed and called stationary priming immediately, without preparing a moving
newly spawned Gear0 vehicle. The raw record proves the observed state and failed
precondition, not the physical cause of spawn motion or actor interference.

Each existing Runner still spawns and owns a fresh vehicle and viewer. Before
participant driving, moving Gear0 now receives zero throttle and the existing
configured brake in automatic mode. The code waits for advancing CARLA frames,
reads the owned actor's snapshot velocity, and requires the unchanged0.1km/h
stationary bound. It reuses the existing connection `--timeout` as the preparation
deadline; it does not sleep, increase the threshold, teleport, change blueprint or
alter vehicle physics. If automatic forward gear engages during settling, the
code still waits for the stationary frame and skips manual priming.

Otherwise stationary Gear0 uses the existing Gear1 prime and physics-derived
0.5–1.0s confirmation window. Before driving, a zero-throttle/brake-hold automatic
command is confirmed on a fresh frame with positive gear and no reverse/manual
shift. Already automatic forward gear keeps the no-prime path. These commands are
sequential preparation by the existing vehicle owner, not a second control loop.
Preparation is outside `active_drive_seconds` and does not shorten the600/780
requested drive windows. Common clock/logger/IDs and actor teardown are unchanged.

Preparation timeout, missing/stale frames, missing actor, non-finite speed,
reverse/invalid gear, failed control RPC or failed automatic confirmation remain
explicit technical failures. They do not enter the participant loop as success.
See `.omo/evidence/p8-vehicle-preparation-verification.md` for offline evidence.

### Historical: NoA110 recovery (guard removed from P8 TM path)

Ordinary MANUAL speed variation and rejected in-drive N requests remain non-aborting;
the activation gate and its failure reasons are unchanged. This does **not** yet
provide non-interrupting recovery after actual NoA speed exceeds110. The existing
guard executes before PI output, and accumulated PI integral can still request
positive throttle above the ceiling. Removing the guard, adding an overspeed
throttle/brake override, or automatically switching to MANUAL would require an
additional approved recovery policy. None was invented here.

Thus the existing `>110` NoA fail-closed behavior remains, including a successful
N activation from MANUAL already above110. No speed-based activation gate was
added. A rejected gate request during driving stays MANUAL and is logged; inability
to initialize the required M2 NOA_L2 start condition remains a startup prerequisite
failure rather than silently substituting MANUAL for that assigned condition.
Safe physical recovery, permitted overshoot semantics and that high-speed N case
require research/safety policy decisions and user-side CARLA validation.

### Unchanged participant-facing sequence

1. Command execution validates arguments/readiness and registers IDs, then starts
   training automatically. No Registration, Consent/Eligibility or Competency page;
   no F5, 1–5, A or R approval input.
2. Training defaults to 600 seconds. **F8** ends training normally, including before
   600 seconds; ESC/QUIT remain aborts. Normal expiry also ends the single run.
   `--training-duration 10` is an explicit short development run, not 600 seconds
   of completed training. No repeated runs are accumulated.
3. After shutdown, sensors/actor cleanup and display restoration, the Tobii external
   calibration instruction appears. Space records only an external-procedure
   boundary and displays the exact M1 instruction. It does not verify gaze/AOI/time.
4. M1 instruction Space starts one continuous600-second run, initially MANUAL
   with N freely available. Its end is recorded PARTIAL, then REST starts without
   an extra completion or approval screen.
5. REST recommends approximately2 minutes, managed by the facilitator. A fresh
   Space immediately displays the exact M2 instruction, even before120 seconds.
   Countdown and forced waiting are removed; a separate fresh Space starts M2.
6. M2 instruction Space starts one continuous780-second run in its assigned initial mode. MANUAL still permits N;
   actual active transitions remain protocol deviations, without forced OFF.
7. After M2, unavailable moving hazard is recorded NOT RUN/SKIPPED internally,
   external post-procedure responsibility is recorded, and the saved-data end screen
   remains visible until closed. No hazard, survey, debrief or data-use input screen.

Consent/eligibility, skill assessment and post procedures remain researcher-managed
external responsibilities. Stage-level execution-policy records carry
`operator_approved:false`, `real_completion_inferred:false` and external/unverified
scope. Consent/post `SKIPPED` means **software collection not performed**, not that
the paper procedure was skipped or completed. No automatic per-field consent,
competency, survey or data-use receipts/answers are generated. Paper data-use
decision/deletion duties remain external; no decision or deletion is inferred.

V5 training content is unchanged: facilitator-led manual ~3 min, NoA ~3 min,
SuRT ~3 min; ON/OFF, brake-disengage/reactivate and exit confirm/reject; no hazard
practice. Software does not force this time allocation or certify proficiency.
Training can reuse existing P5 practice when its own route is explicitly selected.
Unspecified practice geometry/operating conditions remain blocked. Tablet/SuRT is
external physical equipment, not a new software link or automatic completion claim.

| Execution kind | Requested/default duration | Current boundary |
|---|---|---|
| Training | Default/planned 600 s; configurable 1–600 s; F8 normal early finish | Training-only duration context; shared explicit speed settings; one continuous run |
| Explicit short module override | `--drive-duration` 1–60 s | Partial only; omitted option selects nominal times |
| M1 nominal | Default 600 s | One continuous Runner call; explicitly configured P5 evidence is observed and classified |
| Rest | Recommended120 s | Operator-controlled Space; actual duration logged without substitution |
| M2 nominal | Default 780 s | One continuous Runner call; explicit P5 connected, P6/P7 and physical readiness still blocked |

Operator REST records `minimum_s=0`, `recommended_duration_s=120`, and its actual
start/end/duration using the same SessionClock. The two-minute research procedure
is facilitator-managed, not removed. Diagnostic dry/accelerated timing profiles
retain their existing contracts. Holding/repeating Space cannot skip the separate
Module2 instruction acknowledgment.

Training retains its existing persisted TRAINING context and600-second cap.
The new `operator_nominal_module` context requires matching assignment/module,
normal module phase, exact running-stage labels, NoA-only policy and exactly600
or780 seconds. It excludes training/P6/development-validation bindings; P5 is
allowed only with the exact operator context, matching assignment route and
activation-speed-hold policy. Optional training P5 requires TRAINING context.
Generic live-smoke remains capped at60 seconds; `--drive-duration 600` is rejected
rather than used as a bypass. No segmented runs are accumulated into a module.
Existing control/shutdown limits and diagnostic-mode caps remain intact.

During a drive, existing Runner key ownership is unchanged (W/S/A/D, N, brake,
V, indicators). F8 is a training-only facilitator key, not a driving input.
ESC/window close aborts, including an early Runner exit; Ctrl+C also finalizes an
open session and returns 130. Other aborts return 2. The UI discards stale keyboard
events after Runner return and reinitializes its display; held Space cannot
advance successive stages. The process has no synthetic hardware fallback.

### Output and acceptance boundary

`data/p8_operator/<run-id>/` contains shared research event/telemetry CSVs and
`session_manifest.json`. `_registration_claims/` preserves exclusive participant
claims. Session status remains `DEV_REHEARSAL_PARTIAL_COMPLETE`, with
`participant_ready=false`, `hardware_validated=false`, `analysis_eligible=false`.
Each module's `window_complete` records only whether its nominal requested drive
duration was actually met without abort. It can be true while the experiment
remains PARTIAL because required exit/gaze/hazard integration is blocked.
No data-use decision is collected by this workflow; no raw data is deleted.
Run results distinguish planned/configured duration, viewer-run active elapsed time,
persisted active stage boundaries and enclosing runtime setup/cleanup boundaries.
`OPERATOR_EARLY_FINISH`, `TIME_LIMIT_REACHED` and `USER_ABORT` remain distinct.

The continuous600/780 driving-duration contract and explicit P5 bindings are
connected. Approved full routes, physical P5 validation, P6 gaze/clock/AOI and
moving P7/rescue remain blocked or unverified. Tablet task/video is externally
operated independent physical equipment. P5 has a historical low-speed scripted
exit47 validation, not approval for a complete nominal module at this mode-specific
speed policy. P7 live code requires parked ego and does not own ego rescue.
Survey/interview collection is external by the latest operating policy, not a
software readiness claim or permission to omit research records. Participant mode uses this same frontend but the
unconditional readiness guard rejects registration before hardware activity.
Do not interpret this UI delivery as complete P8 participant operation.

Current activation/REST evidence is under `.omo/evidence/p8-activation-rest/`:
the requested REST screen and an event trace with immediate fresh-Space navigation
and actual elapsed-time metadata. Earlier `p8-workflow/` and `p8-operator/` captures
describe superseded UI/timing behavior. Virtual test clocks are not physical
participant-run validation. No actual CARLA, Tobii or physical tablet run was
performed. Current results are in `.omo/evidence/p8-activation-rest-verification.md`;
earlier counts and diagnostic-mode descriptions below are historical.

### Windows response investigation

The reported `P8-8aeed7afb91347bcb95ea65a512cb324` raw run was not found in the
current tree. Its exact 402-second incident was not independently reproduced.
An actual SDL lifecycle defect **was** reproduced offline: a Pygame frame Clock
created before `pygame.quit()` retains its old tick origin after SDL resets.
The first reused `tick(30)` waited 0.855 seconds versus 0.034 seconds on a fresh
Clock after a ~0.75-second stimulus. This scales with prior SDL uptime and blocks
event pumping during the wait; it does not establish that every native stall has
this cause. The UI now owns and recreates its frame Clock with display restoration,
including after modules. The research SessionClock is unchanged.

The actual Windows video backend, ResearchLiveDriverView/DriverView loop,
pygame.quit/reinit, Runner shutdown/cleanup and outer operator event loop were
exercised together with fake CARLA I/O. F8 reached calibration, Space reached M1
instructions, and QUIT remained responsive. Sensor/hero cleanup and shared
logger/clock identity were checked. Physical CARLA RPC cleanup latency, audio-device
initialization and the exact original incident remain user-side validation items.

User-side live validation order: first confirm map/spawn and safe empty road;
then bounded manual/NoA/brake/V/mirror OFF/ON smoke with fresh IDs; inspect CSVs
and abort cleanup; only then exercise the guarded M1/rest/M2 operator sequence.
P5, P6/Tablet/clock/AOI and P7/rescue validation remain separate prerequisites,
not actions silently enabled by this command.

## Authority and evidence limits

The requested authority order is professor-original V5, professor internal test
manual, latest repository documentation, then current source. The original V5
PDF/DOCX and a standalone internal manual were not found in this working tree.
`CARLA_Experiment_Design_v5_Source_of_Truth.md` is explicitly a reconstruction,
not a newly claimed original. Its fixed requirements and the user's quoted
internal-test/reset requirements govern this implementation. No new IRB wording,
survey items, pilot thresholds or final experimental settings were invented.

Latest P3/P4, P5 implementation/fork correction, P6 backend/AOI and P7 documents
were inspected. Nano discovery/capture and an archived five-window screen run are
operator-reported evidence, but current offline reanalysis does not validate its
clock or physical AOI accuracy. Clock retesting, tablet hardware/AOI, the remaining
P5 matrix and P7 live validation are still pending.

The pre-change baseline was 981 passing tests; the final offline suite is 1014
passing tests. No actual CARLA drive, Tobii acquisition, tablet/browser validation,
Unreal Editor action or 55-minute participant run was performed for this
implementation.
This is not P8 completion or participant-ready software.

## Status / requirement map

| V5 / internal-test requirement | Status | Implementation / remaining boundary |
|---|---|---|
| Fixed M1 → rest → M2 → hidden final hazard order | IMPLEMENTED / SOFTWARE TESTED | Explicit ordered stage machine; skips/re-entry rejected |
| Independent M1/M2 1:1 assignment | IMPLEMENTED binding | Existing P4 preassigned CSV is preserved; P8 does not generate/rebalance a randomization table |
| 600 s M1, 120 s rest, 780 s M2 | IMPLEMENTED / SOFTWARE TESTED | Immutable production timing profile; separate virtual/accelerated DEV profiles |
| Training skills and no hazard training | IMPLEMENTED boundary / DEV STUB execution | P3/P5/SuRT logic exercised; human skill assessment remains an operator gate |
| P1/P2 physical driving/rendering | LIVE VALIDATION REQUIRED | DEV geometry/actuation ports only; no new NoA controller or physical renderer |
| P3 NoA initial states and priority | IMPLEMENTED reuse / SOFTWARE TESTED | Existing AutomationInteractionController and AutomationRuntimeController |
| P4 shared keys/clock/logging | IMPLEMENTED / SOFTWARE TESTED | One StudyRunContext and SessionClock; existing ResearchCsvLogger |
| Short live driving through the P8 entrypoint | IMPLEMENTED / FAKE-BACKED TESTED | Explicitly acknowledged DEV-only partial Module 1 segment; real keyboard, CARLA snapshot and Research NoA paths; no participant/full-flow claim |
| P5 one exit opportunity per module | IMPLEMENTED reuse / DEV STUB observations | Real ExitAssistanceCoordinator, fake route/reference/presentation/observations; live matrix remains pending |
| P6 condition-specific tablet/gaze lifecycle | IMPLEMENTED reuse / DEV STUB transport | Real TabletSession, explicit SyntheticGazeSource and existing gaze writer; no hardware claim |
| P6 real clock/tablet/TABLET AOI readiness | HARDWARE BLOCKED | P8 orchestration does not promote revised P6 clock fits or add synthetic fallback into real mode; fitted versus verified remains explicit |
| M2 boundary → P7 once-only hazard | IMPLEMENTED reuse / DEV STUB actors | Existing HazardScenario and OccurrenceRegistry, shared P4 clock |
| P7 moving-ego/live/rescue behavior | LIVE VALIDATION REQUIRED / P8/PARTICIPANT BLOCKED | No automatic activation of unvalidated moving-ego path |
| Survey/debrief/data use | IMPLEMENTED lifecycle / NOT_IMPLEMENTED UI | External operator-step boundary, no questionnaires or consent decisions fabricated |
| Partial abort / new participant isolation | IMPLEMENTED / SOFTWARE TESTED | Stream close/flush, owned cleanup, final manifest; fresh per-run component graph |
| Complete 55-minute live participant experiment | P8/PARTICIPANT BLOCKED | No participant launcher; readiness flags alone cannot unlock an unimplemented runtime |

## State sequence and guards

```text
SESSION_CREATED
CONSENT_AND_ELIGIBILITY
TRAINING
EYE_TRACKER_CALIBRATION
MODULE_1_PREPARE
MODULE_1_RUNNING
MODULE_1_COMPLETE
REST
MODULE_2_PREPARE
MODULE_2_RUNNING
FINAL_HAZARD
POST_EXPERIMENT
SESSION_COMPLETE

Any unfinished state -> ABORTED
```

Transitions can only move to the next state. Consent/eligibility, training,
calibration and post-experiment boundaries require explicit receipts. A
`NOT_IMPLEMENTED` or `EXTERNAL_OPERATOR_STEP` receipt is not completion.
Development automation supplies `DEV_STUB` receipts; it does not impersonate a
real operator's consent, skill judgment, calibration or questionnaire completion.

Module/rest transitions enforce their profile minimum duration. Each running module
must have consumed exactly one exit opportunity. FINAL_HAZARD requires prior
Module 2 completion and cannot be entered directly through a component with a
different orchestration context. Its exit requires the P7 terminal state.
Failed component/module initialization logs `stage_start_attempt` and abort,
not a normal module `stage_start`.

`SESSION_COMPLETE` is an orchestration endpoint. A successful development run's
completion status is **DEV_FLOW_COMPLETE**, with `participant_run=false`,
`participant_ready=false`, `hardware_validated=false`, `analysis_eligible=false`.
It is never a claim that a participant or real survey completed the experiment.

## Shared services and component wiring

`SessionServices` owns the one immutable StudyRunContext, the one P4 SessionClock,
the virtual DEV timeline, stage-to-SegmentContext routing and common CSV writer.
The P7 constructor now accepts an optional shared SessionClock with an origin
consistency check; its existing default behavior is unchanged.

The accelerated DEV component graph instantiates the existing P3
runtime/interaction classes with a non-actuating backend and a labeled
lane/vehicle fixture. It does not copy PI/lateral control laws. The separate
`dev-live-drive` mode delegates actor/viewer/control ownership to the existing
`ResearchNoARunner`, while borrowing P8's exact StudyRunContext, SessionClock and
ResearchCsvLogger. It is a partial Module 1 development segment, not the participant
runtime or the full P8 stage machine.

P5 uses the existing coordinator with one separate training opportunity and one
slot per module. Its route and display/movement observations are explicit fixtures,
not Town04 execution. Training confirm/reject ability receipts are DEV assessments,
not a claim that two physical exits were rehearsed. No hazard is rehearsed.

P6 uses a fresh TabletSession/epoch and explicit synthetic source per run. No HTTP
server or browser is launched. `NO_SURT` binds to BLANK; `SURT` uses the existing
trial lifecycle. Both Module 2 conditions use identical fixture bytes and media
metadata. `DEV_STUB_NOT_A_VIDEO.bin` is deliberately **not video**: metadata/playing
acknowledgments exercise the software protocol only. The declared 900-second test
metadata is not a selected final video or measured file duration.

The video remains playing through FINAL_HAZARD and is stopped at POST_EXPERIMENT,
not ended prematurely at Module 2's attention-window boundary. Accelerated timing
does not weaken P6's real 780-second media-duration contract or generate a fake EOF.
Gaze records are checkpoint samples, not a validated 60 Hz acquisition stream.
P8 orchestration does not modify or promote P6 clock/AOI approval. Separate P6 work
revised affine-fit numerical conditioning and distinguishes fitted estimates from
verified timing; current mapping and AOI approval remain unverified.

P7 uses the actual hazard state machine with fake actor/observation ports. Its
events share the same participant/run/clock and FINAL_HAZARD segment. Occurrence
claims use the existing P7 registry in a **separate DEV-only shared store** under
the output root, never the production participant ledger. A new run-local graph
does not erase an existing same-participant claim in that store.

## Assignment and timing

Production assignments are still supplied through P4's preassigned CSV/lookup.
`--assignment-file` requires `--participant-id` and cannot be combined with forced
conditions/matrix/reset flags. No condition is recomputed from M1 behavior or the
other module. Generating and approving the independent blocked randomization table
remains the existing external research process, not an unannounced P8 algorithm.

DEV IDs are UUID-based research identifiers, not derived from name, age, birthday
or other personal data. Supplied CSV IDs are treated as existing research IDs;
the operator remains responsible for not placing personal identifiers in that file.

| Profile | M1 | Rest | M2 | Time source / purpose |
|---|---:|---:|---:|---|
| V5_NOMINAL | 600 s | 120 s | 780 s | Production contract only; participant execution blocked |
| DEV_VIRTUAL_NOMINAL (`dry`) | 600 s | 120 s | 780 s | Virtual time; no real waiting/driving |
| DEV_ACCELERATED (`accelerated`) | 4 s | 2 s | 4 s | Virtual short full-flow integration |
| DEV_LIVE_PARTIAL (`dev-live-drive`) | Operator time cap | Not run | Not run | Real CARLA/keyboard path; partial Module 1 only |

Other DEV boundaries use short explicit steps and P7 fake observation progression.
The V5 approximate 55-minute overall schedule and human-dependent training/calibration/
post durations are not replaced by these development steps.

M1 starts MANUAL. DEV input requests turn NoA ON at one quarter and OFF at three
quarters to test duration accounting. The 50% result is calculated from the actual
canonical software mode intervals, not assigned as an outcome. This is scripted
DEV behavior, not participant advice or a change to voluntary activation semantics.
M1 ending ON is stopped through the existing owner before REST/M2; M2 starts
MANUAL or NOA_ACTIVE according to its independently assigned condition. Final hazard
does not create a new controller. Raw DEV brake response is routed through P3 first.

## Readiness and historical success

`config/p8_readiness.json` separates operator-reported history from current approval
gates. Reported Nano discovery/gaze and first screen-AOI success are retained.
The current-run available/calibrated/approved flags are conservative false, not a
denial of that prior evidence. Calibration readiness resets for every new participant.

Required fields include eye tracker availability/calibration, screen AOI, tablet,
tablet AOI, clock synchronization, P7 live validation, remaining P1-P5 matrix,
rescue-owner binding, final parameters and final video. Tablet absence blocks all
participant combinations, including NO_SURT, which still requires the same inactive
physical tablet and later Module 2 video.

Setting flags true does **not** enable a participant run in this implementation.
`PARTICIPANT_RUNTIME_NOT_IMPLEMENTED` is a separate hard capability gate. A DEV_STUB
or synthetic adapter cannot become a real source fallback.

## Logging, manifest and completeness

Each run has an exclusive directory under `data/p8_full_flow/<run-id>/`:

```text
research_events_<run-id>.csv
research_telemetry_<run-id>.csv
p6_tablet_<run-id>.jsonl
p6_gaze_<run-id>.jsonl
DEV_STUB_NOT_A_VIDEO.bin
session_manifest.json
```

CSV schemas remain P4-compatible. P3/P5/P6/P7 events go through the common writer;
P6 raw events/gaze remain separate sidecars, enriched with participant/run context
and explicit DEV provenance. No original raw log is replaced by the manifest.

The manifest indexes conditions, versions, shared origin, current readiness,
stage attempts/start/end/duration/receipt/reason, component snapshots, partial
metrics, cleanup ordering and data files (relative paths, sizes, row counts, hashes,
keys and provenance). Initial reset snapshots are copied, not mutable aliases.
The manifest is atomically updated only inside the newly exclusively owned run
directory. Existing run directories and raw files are never overwritten.

On normal closure, completeness checks expected files, nonempty data rows, common
IDs, exit counts, hazard terminality, gaze presence and remaining owned tokens.
It is **software artifact completeness**, not physical experiment completeness.
Missing/corrupted files or cleanup failures cannot yield DEV_FLOW_COMPLETE.

## Abort and cross-participant reset

Abort closes the current stage with a reason, records ABORTED, attempts every
component's event/raw flush, cleans owned resources through the existing owner,
closes the common logger, then persists final status/index. Failure in one event
sink does not skip P6 stream closure or remaining cleanup. Errors remain in the
manifest, and partial files remain. Hardware/power/disk-loss durability beyond
successful filesystem flushes is not claimed.

Order recorded: `LOGGER_FLUSH -> OWNED_RESOURCE_CLEANUP -> SESSION_STATUS_PERSIST`.
A failed cleanup blocks another participant in the same manager. No automatic
recovery/reconciliation of an unknown real actor state is enabled.

Each new participant gets fresh P3 state/counters, P5 references/slots, P6 calibration,
tablet epoch/trials/events, P7 run-local state/metrics, post state, resource tokens
and timers. The persistent P7 occurrence store is intentionally retained and is
not confused with run-local reset. Reusing a participant after a claimed hazard
fails closed even with a different run ID.

## Post-experiment boundary

Lifecycle events include `post_experiment_start`, `survey_status`, `debrief_status`,
`data_use_status`, `post_experiment_end`. No survey/IRB-facing wording or questions
were created. Initial survey status is NOT_IMPLEMENTED; debrief/data use are
EXTERNAL_OPERATOR_STEP. Development runs explicitly use DEV_STUB and retain
`real_survey_completed=false`. No real consent/data-use decision is inferred.

The actual survey collection, external completion evidence, debrief delivery,
data-use refusal/deletion workflow and production approval remain external or
unimplemented participant prerequisites. DEV raw files are not participant data.

## Tomorrow's commands

From `C:\dev\carla`, using the unchanged Python 3.12 CARLA `.venv`:

```powershell
# Four software condition combinations; no CARLA/Tobii/tablet activity.
& ".\.venv\Scripts\python.exe" -B -m tools.p8_smoke --mode accelerated --matrix

# Explicit partial-data abort example (expected nonzero exit).
& ".\.venv\Scripts\python.exe" -B -m tools.p8_smoke --mode accelerated --abort-at MODULE_2_RUNNING

# Consecutive SURT/NOA then NO_SURT/MANUAL participants, fresh state each time.
& ".\.venv\Scripts\python.exe" -B -m tools.p8_smoke --mode accelerated --reset-smoke

# Nominal M1/rest/M2 durations on a virtual clock, not a 55-minute live run.
& ".\.venv\Scripts\python.exe" -B -m tools.p8_smoke --mode dry

# Tomorrow: bounded, read-only CARLA connectivity/assets and SDK package checks.
& ".\.venv\Scripts\python.exe" -B -m tools.p8_smoke --mode live-preflight `
  --tobii-python ".\.venv-tobii\Scripts\python.exe" --host 127.0.0.1 --port 2000

# Tonight/software-only alternative: do not launch either external probe.
& ".\.venv\Scripts\python.exe" -B -m tools.p8_smoke --mode live-preflight --offline
```

Preflight returns `P8_PARTICIPANT_BLOCKED` and exit 2 while these gates remain.
It never spawns/controls actors, changes world settings or starts a viewer/server.
The Tobii check queries Python/package metadata only, never native SDK discovery,
calibration or gaze. Child probes have ten-second deadlines. Connectivity/assets
and a working SDK package are not promoted to calibration or hardware validation.

Batch summaries link the individual manifests. After physical P6/P7 smoke, compare
the new evidence against the readiness backlog. Do not turn every flag true merely
because accelerated orchestration passed. P6 and P7 physical commands remain in
their own documents; this P8 entrypoint does not silently execute them.

## DEV-only live driving

This is the shortest implemented P8 path that opens the real Research NoA driver
view and writes into one P8 run directory. Before running it, start CARLA with
Town04 loaded and unpaused, confirm `synchronous_mode=false`, and select a known
safe Driving-lane spawn index for the currently loaded map. No repository-wide
spawn index is declared safe; the operator must inspect and choose one for that run.

```powershell
$safeSpawnIndex = Read-Host "Verified safe Driving-lane spawn index"
# Stage 1: low-policy manual/NoA regression (desired 10, guard 20, cap 0.4).
& ".\.venv\Scripts\python.exe" -B -m tools.p8_smoke `
  --mode dev-live-drive --ack-dev-live --spawn-index $safeSpawnIndex `
  --duration 10 --front-camera-only --noa-desired-speed-kmh 10

# Stage 2: manual-acceleration validation. Desired speed remains 10 km/h;
# the explicit 30 km/h guard only provides operator-chosen headroom.
& ".\.venv\Scripts\python.exe" -B -m tools.p8_smoke `
  --mode dev-live-drive --ack-dev-live --spawn-index $safeSpawnIndex `
  --duration 10 --front-camera-only `
  --noa-desired-speed-kmh 10 --dev-speed-limit-kmh 30 `
  --ack-high-speed-dev --noa-max-throttle 1 --pi-trace

# Stage 3: bounded NoA DEV validation (maximum 20 seconds; 25 is a target,
# not an achievement assertion and not the 110 km/h policy ceiling).
& ".\.venv\Scripts\python.exe" -B -m tools.p8_smoke `
  --mode dev-live-drive --ack-dev-live --spawn-index $safeSpawnIndex `
  --duration 20 --front-camera-only `
  --noa-desired-speed-kmh 25 --dev-speed-limit-kmh 35 `
  --ack-high-speed-dev --noa-max-throttle 1 --pi-trace
```

The desired speed is the existing longitudinal controller target and remains the
single target-speed source. The `110 km/h` maximum allowed policy value is never a
target. The effective desired speed is `min(desired, 110)`, while every executable
high-DEV profile requires `desired < operational guard < 110`. This is a software
guard, not a physical promise that CARLA cannot overshoot it. Values above the
legacy 20 km/h guard require all three explicit options shown above. A low desired
speed at or below 20 km/h may be supplied alone and retains the legacy 20 km/h
guard.

`--noa-max-throttle` is only an actuator maximum; the unchanged PI controller
computes each command. The default is `0.4`. Values in `(0.4, 1]` require the
complete acknowledged high-speed DEV profile: `--ack-high-speed-dev`, an explicit
`--noa-desired-speed-kmh`, and an explicit `--dev-speed-limit-kmh`, with
`desired < guard < 110`. Non-finite and out-of-range values fail before a run
directory, CARLA client, or actor can be created. The generic live run config applies
the same rule. High acknowledged profiles remain prohibited with P5, P6, and P5
`--development-validation`; the low P5 route-validation path remains capped at
`0.4`. No 110 km/h run is approved.

The longitudinal PI law and tuning are unchanged. A learned positive integral can
continue to request some throttle at or slightly above target until subsequent
error unwinds it; this continuity is intentional and is one possible contributor
to near-target overshoot. Existing anti-windup freezes same-direction integral
accumulation while an actuator is saturated. Therefore neither the desired speed,
the throttle cap, nor the operational guard is a physical clamp or a guarantee
against overshoot.

Without P5 options, the existing default duration is 10 seconds and the maximum is
20 seconds, including explicit high-DEV profiles. P5 is enabled only by supplying
both `--exit-route-manifest` and `--exit-route-id`. High-speed DEV is prohibited
with P5, P6, and P5 `--development-validation`. Only explicit low-speed P5
`--development-validation` permits up to 120 seconds; `--ack-dev-live` remains
mandatory. For a bounded manual-navigation candidate smoke, after independently
checking the current map and spawn:

```powershell
& ".\.venv\Scripts\python.exe" -m tools.p8_smoke `
  --mode dev-live-drive --ack-dev-live --spawn-index 107 `
  --duration 90 --development-validation `
  --exit-route-manifest config/town04_exit_routes_dev_v3.json `
  --exit-route-id town04-exit-47
```

This is not a validated assisted Module 1 exit command. Each invocation creates a
fresh run directory; the live manifest refuses to overwrite an existing manifest.

The acknowledgment and spawn index are mandatory. The default endpoint is
`127.0.0.1:2000`. `--duration` is a wall-clock safety cap; closing the window or
pressing `Esc` ends the segment early. The path spawns and later destroys its own
`research_noa` hero. Do not point it at an occupied or unsafe spawn.

| Key | DEV live behavior |
|---|---|
| `W` / `S` | Manual throttle up to 1.0 / bounded forward braking; brake has priority and `S` does not request reverse |
| `A` / `D` | Manual steering, capped by the DEV control limits |
| `Space` | Hand brake |
| `N` | Request Research NoA activation/deactivation |
| `C` / `R` | Confirm/reject the rendered P5 exit recommendation |
| Brake while NoA is active | Disengage through the existing P3 interaction path |
| `V` | Toggle DRIVER/COCKPIT view |
| `Z` / `X` | Toggle left/right indicator |
| `Esc` or window close | End this partial segment |

`P` is filtered out in the Research NoA view, so CARLA built-in autopilot is not
the automation path. Supplying the explicit exit manifest and route ID binds the
actual P5 Module 1 coordinator, presentation and persistence path to the shared P8
run context/logger. The pre-spawn gate requires Module 1 context, the assigned route,
`N`/`C`/`R` bindings and the P8 DEV-live persistence stage. Module 1 starts with NoA
OFF. If `N` changes the mode after a P5 presentation timestamp, that opportunity is
interrupted rather than retroactively reclassified. The manifest records the final
P5 event, frozen NoA state, decision, physical outcome and termination reason on
time-cap, `Esc`, and runtime-error cleanup paths. P6 tablet/gaze, P7 hazard and survey
remain disabled. The default development values are 10 km/h desired speed and a
20 km/h operational guard. Manual throttle is independently normalized to 1.0;
NoA defaults to 0.4 throttle and remains capped at 0.5 brake and 0.15 steering. The controller
gains and deadbands are the existing P3/P4 stationary-validation development
values from `tools/validate_p3_p4_stationary.py`, copied explicitly into this
adapter rather than newly tuned or promoted to final participant parameters.

The `0.4` NoA throttle statement above is the legacy/default and P5 limit. Only the
explicit acknowledged high-speed DEV profile may raise the NoA throttle maximum up
to `1.0`; brake remains `0.5`, steering remains `0.15`, and all gains, deadbands,
durations, transmission behavior, writers, physics, and gear control are unchanged.

The run writes `research_events_<run-id>.csv`,
`research_telemetry_<run-id>.csv`, and `session_manifest.json` under
`data/p8_full_flow/<run-id>/`. Telemetry is sampled from actual CARLA snapshots and
the applied vehicle control. Human activation/deactivation/brake semantics are
recorded as interaction events. The manifest labels real versus injected sources,
records shared-object identity and cleanup, and includes desired, maximum allowed,
effective desired, operational guard, manual/NoA caps, and the legacy versus
`HIGH_SPEED_DEV_UNVALIDATED` profile. The shared event log emits one
`speed_policy_effective` event and, after any bounded shutdown response, a
`speed_limit_violation` event at initial, MANUAL, NoA-active, or final observation
boundaries. When post-response capture succeeds, the violation event and matching
telemetry row share one timestamp and record cached control readback, not proof of
physical application. Shutdown call outcomes and physical braking verification are
separate; `physical_braking` remains `NOT_VERIFIED`. The
manifest always retains
`participant_run=false`, `participant_ready=false`, `module_1_completed=false`,
and `analysis_eligible=false`. A successful time-capped drive is
`DEV_LIVE_PARTIAL_COMPLETE`, not a completed module or participant run. Startup,
runtime, runtime-cleanup, event-write, inspection or logger-close failure is
`ABORTED`. The manifest records
runtime-created versus actual-stage-started state, uses `UNKNOWN` for cleanup that
cannot be verified, and uses `FAILED` rather than claiming closure when cleanup
raises. Partial CSV files and a best-effort manifest are retained when the manifest
path remains writable. A completed viewer cleanup call does not prove CARLA camera
sensor destruction: the manifest reports camera/sensor destruction as `UNVERIFIED`.
In particular, the existing `V` view-switch path destroys and replaces the front
sensor in place, so a failure during replacement can leave actor state that this P8
adapter cannot independently reconcile. This is a pre-existing residual risk, not a
participant-ready cleanup guarantee.

With `--pi-trace`, stdout now includes target/measured speed, PI `dt`, integral,
commanded throttle/brake/steering, saturation flags, and the throttle/brake/steering/
gear values from the one existing post-command `get_control()` read. These fields are
labeled cached readback: they do not prove physical application and are not an
atomic CARLA-frame observation. A supplied `control_trace` callback and `--pi-trace`
both run; trace activation does not add a second control-read RPC. The public lateral
trace CSV and research telemetry CSV schemas are unchanged.

The shared research event CSV emits `noa_control_saturation_transition` only for
the first observed saturation state and subsequent changes, including clear. Its
JSON payload carries the exact command, cached readback, PI state, frame and
simulation time; the row retains the existing study/run ID and shared session clock.
This is a diagnostic transition stream, not physical-actuation evidence. Gear is
read from that cached control. RPM remains an external read-only diagnostic through
the already known CARLA Python telemetry API (`get_telemetry_data`); no RPM RPC or
physics query was added to the control hot path.

For staged live use, perform Stage 1 first and verify manual acceleration, NoA
activation/deactivation, brake disengagement and reactivation. Stage 2 checks manual
headroom while the NoA target remains 10. Stage 3 should activate NoA, observe the
approach/cruise behavior, press `S` to disengage, press `N` to reactivate, and check
straight-lane lateral behavior. A single bounded run may be too short for all
actions; repeat the same bounded stage with a fresh automatically generated run ID
rather than extending the time cap. Do not claim that 25 km/h was reached.

Current interaction semantics are narrower than a production driver-priority
claim: brake above threshold disengages NoA before button handling; engaged steering
overrides and is clamped to the existing steering cap but does not itself disengage
NoA. Activation still fails for off-center, misaligned, straddling, lane-change, or
active-brake states. Lateral control remains speed-independent and uses the same
lane-geometry error path.

The NoA backend has no lead detection, ACC, headway planner, relative-speed input,
traffic adaptation, collision warning, or avoidance braking. Functional completion
here is offline/fake-backed controller, policy, trace and persistence coverage only.
Live validation must use an empty road until traffic adaptation and participant
readiness are separately implemented and validated. The final hazard continues to
provide no collision warning or automatic avoidance braking. Physical acceleration,
stability, overshoot, lane holding, cached-readback fidelity, participant operation,
and CARLA shutdown effects remain `NOT_VERIFIED`; shutdown command-call outcomes
remain `RETURNED`, `RAISED`, or `NOT_ATTEMPTED`, with physical braking
`NOT_VERIFIED`.

## Current readiness handoff

The 2026-10-08 software-only matrix produced four `DEV_FLOW_COMPLETE` runs, reset
smoke produced two, and the intentional `MODULE_2_RUNNING` abort returned exit 2
with `ABORTED`; every result retained `participant_ready=false`. P8 remains DEV-only.

The following are unresolved and must not be inferred from the software smokes:

- Historical M2 `MANUAL -> NOA_ACTIVE` concern: the resumed operator intentionally
  permits N and logs actual active transitions as protocol deviations. Fake-I/O
  regression covers this; physical operation and participant gates remain unverified.
- P7 moving-ego validation and primary-owner rescue binding remain blocked.
- Required telemetry fields `lane_pos`, `headway_dist`, `rel_speed`, and experiment
  TTC are not yet a complete participant analysis contract; P7's DEV TTC is narrower.
- Time-weighted AOI outcomes and valid continuous 150 ms mirror-check events still
  require final analysis implementation/validation.
- Final-exit continuation/rejoin, the remaining P5 live matrix, questionnaires,
  debrief, and data-use refusal/deletion workflow remain incomplete or external.
- Route 47 at spawn 107 is only a manual-navigation smoke candidate. Existing
  evidence shows projection near 1038.500 m and a confirmation envelope ending near
  1051.605 m. The prior physical success documented in
  `P5_Exit47_Fork_Fix_20261007.md` used Module 2 starting ON and scripted confirmation,
  not P8 Module 1 or a physical C-key test. This spawn is already within the guidance
  window, so Module 1 may present guidance while still MANUAL. Offline integration
  tests establish that an `N` input after MANUAL presentation interrupts that
  opportunity; they do not establish physical timing at spawn 107. Assisted Module 1
  behavior and current-map/spawn suitability remain physically unverified.
- Collision/road-departure automatic stop must be implemented separately from the
  intended no-warning/no-avoidance-braking hazard behavior. P7 currently records a
  termination request but does not actuate ego rescue.

The professor-original V5 and standalone internal manual were not found in this
working tree and are not claimed as reviewed.
