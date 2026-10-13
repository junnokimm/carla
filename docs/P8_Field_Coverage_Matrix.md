# P8 field coverage matrix

Current lane-change delta: [P8_Patched_TM_LaneChange.md](P8_Patched_TM_LaneChange.md).
Submission, observed alignment and signal cancellation are connected offline;
physical driving, exit/rejoin and traffic safety remain unverified. Older BLOCKED
execution statements below describe the earlier implementation.

## Phase2 delta

See [Phase2 contract](P8_Phase2_P5_LaneChange_TrafficLights.md). General left/right
requests, static lane eligibility, geometric samples and observation termination
are CONNECTED. Patched-TM one-shot submission is CONNECTED only behind loaded-PYD,
exclusive-listener, and EMPTY_REHEARSAL_ONLY gates. General physical onset,
crossing, direction, and completion remain UNVERIFIED. P5 has explicit active-drive time windows plus
original location checks; no in-window arrival means NOT_PRESENTED, not completion.
Traffic-light vehicle/actor states and host acquisition intervals are CONNECTED;
actual red-light stopping/violation attribution remain UNVERIFIED. Current Town04
topology was read without driving; structural continuation is not a validated TM route.

## Scope and evidence

This inventory resumes the interrupted field-matrix task; no earlier matrix was
found. Authority is `CARLA_Experiment_Design_v5_Source_of_Truth.md`, section 11
(repository reconstruction, not a newly reviewed professor-original document).
It does not introduce questionnaire wording, exclusion rules, or research policy.

The current operator entrypoint is `python -m src.p8.operator_cli`.
Its actual Runner adapter must not be confused with the older
`tools.p8_smoke --mode accelerated` synthetic component graph.
The latest user-confirmed scope removes administrative, competency and post-answer
UI. External paper responsibilities remain, but stage scope records do not assert
their completion or manufacture field values. Training is planned 600 seconds with
normal F8 early finish. M1/M2 default to single continuous600/780-second runs;
explicit short overrides stay1–60 seconds. Time-window completion does not imply
completed P5/P6/P7 integration. Final P8 uses Traffic Manager default speed with
no numeric P8 speed cap in either mode. Nonfinite speed remains an error. No target
is captured on activation; Custom PI/lateral control is not used by P8.
See [P8_Traffic_Manager_Integration.md](P8_Traffic_Manager_Integration.md).
Operator REST recommends120 seconds but has no forced
minimum; actual start/end/elapsed values are retained without substituting120.
The vehicle-readiness correction settles moving Gear0 before stationary priming
and confirms automatic forward mode before participant control. It adds no
research threshold. The old NoA110 guard is removed by explicit researcher request;
TM physical speed behavior is still UNVERIFIED. Ordinary rejected in-drive
activation requests remain MANUAL and do not abort the run.

Status vocabulary:

- **CONNECTED**: implemented and wired into the operator path; software/fake-I/O
  evidence only, **not** a physical validation claim.
- **PARTIAL**: some required semantics are recorded, but not the full field contract.
- **SEPARATE**: repository implementation exists outside this operator path.
- **MISSING**: no complete collector/export for this requirement in the operator path.
- **PHYSICAL BLOCKED**: hardware, timing, rendering, or live validation is outstanding.
- **EXTERNAL**: researcher-managed paper/external collection, not collected or confirmed by this UI.

All connected driving fields below remain physically unverified in this work.
Missing values are not zero measurements. An operator receipt is not hardware
evidence. `DEV_REHEARSAL_PARTIAL_COMPLETE` is not a completed participant run.

## 11.1 Common links and clocks

| Required field | Status | Actual representation / limitation |
|---|---|---|
| `participant_id` | CONNECTED | Common research CSV columns and manifest; one registration per operator instance |
| `run_id` | CONNECTED | Same `run_id` / `study_run_id` across training, M1, rest and M2 |
| `session_elapsed_s` | CONNECTED | One borrowed `SessionClock`; host-monotonic origin persists across Runner lifecycles |
| Original device time | PARTIAL | CARLA simulation seconds/frame and capture interval in common CSV; external-clock JSON supported; no operator gaze/tablet acquisition |
| Converted common device time | SEPARATE / PHYSICAL BLOCKED | P6 clock-fit and mapping interfaces exist; real clock synchronization is not verified or enabled by P8 |
| Nonempty persisted values after exit | CONNECTED | CSV writes flush; manifest indexes rows, file sizes/hashes and audit issues; fake-I/O evidence does not prove power-loss durability |

Code: `src/logging/csv_logger.py`, `src/p8/services.py`,
`src/p8/manifest.py`, `src/experiment/session_clock.py`.

## 11.2 Session records

| Required field | Status | Actual representation / limitation |
|---|---|---|
| `participant_id` | CONNECTED | Registration claim, common CSV, manifest |
| `enroll_status` | EXTERNAL | Paper management; no enrollment/consent/eligibility value collected. Stage records external scope only |
| Stage start time | CONNECTED | `stage_start`, `start_session_s`; failed entry separately recorded |
| Stage end time | CONNECTED | `stage_end` / `stage_abort`, `end_session_s`, duration |
| `interruption_reason` | PARTIAL | Abort reason and stage reason exist; no separate classified interruption field |
| `withdrawal_reason` | MISSING | No actual withdrawal-reason form; ESC is not inferred to mean withdrawal |
| `adverse_reaction_code` | MISSING | No adverse-reaction coding form |
| `post_debrief_data_use` (agree/refuse) | EXTERNAL | Not collected by current operator UI; no automatic data-use status event or decision |
| Data-use decision time | EXTERNAL | Must be retained by external procedure; software stage time is not decision time |
| Compensation paid | MISSING | No compensation receipt or payment workflow |

Data-use refusal/deletion obligations remain external, not silently removed.
Legacy explicit receipt API remains available to its existing callers, but this
workflow does not call it for consent, competencies, survey, debrief or data use.
Run-level `analysis_eligible=false` and participant readiness remain false.
Code: `src/p8/orchestrator.py`, `src/p8/operator_components.py`,
`src/p8/operator_ui.py`.

## 11.3 Assignment records

| Required field | Status | Actual representation / limitation |
|---|---|---|
| `m1_condition` | CONNECTED | CSV `module_1_condition`, manifest assigned M1 condition |
| `m2_condition` | CONNECTED | CSV `module_2_condition`, manifest assigned M2 condition; independent explicit/CSV assignment |
| `route_id` | PARTIAL | Assignment route preserved; explicit DEV assignment says `DEV_OPERATOR_ROUTE_BLOCKED`; no full operator exit route |
| `scenario_version`, including traffic density | PARTIAL | Version string preserved; no connected traffic-density realization in operator runs |
| AOI file version | PARTIAL | Assignment metadata only; not a loaded/validated operator AOI mapping |
| Program version | PARTIAL | Assignment version string; not an automatically verified build/source hash |

Code: `src/p8/operator_registration.py`, `src/experiment/assignment.py`,
`src/logging/csv_logger.py`. Restoring the rear camera does not itself change or
validate AOI assignment metadata. The rear-disabled candidate must not be used as
proof of a physically validated mirror AOI.

## 11.4 Automation events (both modules)

| Required field | Status | Actual representation / limitation |
|---|---|---|
| `auto_request` ON/OFF attempts | CONNECTED | Existing interaction event stream retained |
| `auto_state` actual mode | CONNECTED | Canonical controller transitions, not requested mode |
| Activation failure reason | CONNECTED | Existing activation/availability event reasons |
| Automatic disengagement reason (including brake) | CONNECTED | Existing interaction events; brake priority retained |
| Available/unavailable transitions | CONNECTED | Existing automation persistence path |
| Event time | CONNECTED | Common clock and actual event timestamp |

P8 emits initial `control_authority` and ongoing
`traffic_manager_speed_limit_observation` in the existing event CSV, preserving
canonical `auto_state`. Target speed is null, no `activation_speed_hold` is emitted.
Brake/steering/button reasons are distinct; M2 NOA_L2 initializes actual TM
registration after preparation. Physical departure from standstill is unverified.
Cached control readback is not physical application proof.

`operator_metrics.py` derives ON duration, activation/deactivation/reactivation
counts and first activation latency from persisted events. Observed-window ratio
is distinct from nominal-window ratio, which stays null for short/interrupted runs.
For a fulfilled nominal request, ON time is integrated only inside the nominal
window, not obtained by clamping an all-time total. Raw events remain unchanged.
M2 MANUAL permits N; an actual active transition records a protocol deviation,
without forcing OFF or inventing an exclusion decision.
Code: `src/scenario/research_noa_persistence.py`, `src/p8/operator_metrics.py`.

## 11.5 Exit/lane-change events (one per module)

The operator now binds the existing P5 coordinator/persistence into nominal M1/M2
when an explicit route and behavior configuration is supplied. Optional training
has its own explicit configuration/context/event ID. This is code connection,
not route approval or physical validation. Missing configuration stays explicit.
See `P8_P5_Operator_Integration.md` for selection and outcome contracts.

TM assisted lane-change execution is BLOCKED. Indicator confirmation/decision and
observations are connected; no forced lane change is sent. Request acceptance does
not imply maneuver execution or completion. Later EXIT/MISSED is retained separately.

| Required field | Operator status | Existing boundary |
|---|---|---|
| `lc_event_id` | CONNECTED when configured | One run/stage/route identity, distinct training/M1/M2 |
| `module` | CONNECTED when configured | Assigned module; training retains blank CSV module and TRAINING context |
| `lc_recommend_t` / displayed `t0` | CONNECTED / PHYSICAL UNVERIFIED | Existing display-submission timestamp, not photon onset |
| `noa_state_t0` | CONNECTED | Existing frozen canonical mode at presentation |
| `lc_decision`: confirm/refuse/no-response or manual change/no-change | CONNECTED | Existing decisions; never promoted to physical success by themselves |
| `lc_decision_t` / `tD` | CONNECTED | Existing input-receipt/decision-commit timestamps |
| Indicator and steering onset | CONNECTED / PHYSICAL UNVERIFIED | Existing observation events; unknown values remain null |
| Lateral movement onset | CONNECTED / PHYSICAL UNVERIFIED | Existing observation contract and development threshold |
| Lane crossing time / `tL` | CONNECTED / PHYSICAL UNVERIFIED | Existing boundary-crossing observation, not inferred from confirmation |
| Exit passed | CONNECTED / PHYSICAL UNVERIFIED | Existing EXIT/MISSED observation and fork fix; continuation/rejoin unresolved |

See `P5_Implementation_Validation_20261007.md` and
`src/scenario/research_noa_persistence.py`. Operator module completion is explicitly
based on actual configured, presented, decision, normal missed, observed exit,
interrupted and incomplete/technical evidence even when `window_complete=true`.
The overall session stays PARTIAL/readiness-blocked. No new route,
recommendation location/timing or lane-change criteria were introduced.

## 11.6 Vehicle samples

| Required field | Status | Actual representation / limitation |
|---|---|---|
| `speed` | CONNECTED | `speed_kmh` |
| `lane_pos` | MISSING | Lane-geometry controller input is not a complete persisted continuous lane-position analysis contract |
| `steer` | CONNECTED | `steering` |
| `brake` | CONNECTED | `brake` |
| `accel` | PARTIAL | `throttle` is normalized control input, not measured longitudinal acceleration |
| `headway_dist` | MISSING | No connected lead/headway observer |
| `rel_speed` | MISSING | No connected lead-relative-speed observer |
| `ttc` with collision path and positive closing speed | SEPARATE | P7 qualified constant-velocity TTC exists, not continuous operator traffic TTC |
| Lane number | CONNECTED | `lane_id`, nullable when unavailable |
| Sampling period | PARTIAL | Per-row host/CARLA timestamps allow interval reconstruction; no validated fixed sample-period field/rate |

Code: `src/logging/csv_logger.py`, `src/scenario/research_noa_runtime.py`,
`src/p7/metrics.py`. Control readback is not proof of physical actuation.

## 11.7 Gaze samples

| Required field | Status | Actual representation / limitation |
|---|---|---|
| `gaze_x` | SEPARATE / PHYSICAL BLOCKED | P6 left/right `EyeDatum.x`; not connected to operator |
| `gaze_y` | SEPARATE / PHYSICAL BLOCKED | P6 left/right `EyeDatum.y`; not connected to operator |
| `gaze_valid`: tracked/lost/blink/out-of-range | PARTIAL / PHYSICAL BLOCKED | P6 validity/reasons exist; full categorical blink/loss interpretation is not asserted |
| `aoi`: forward/rear/left/right/video/other/missing | SEPARATE / PHYSICAL BLOCKED | P6 AOI model/candidates; no operator acquisition. Disabled rear is unavailable, never a valid rear hit; physical tablet AOI unresolved |
| `calibration_result` | PARTIAL | P6 result model; operator stores EXTERNAL/UNCONNECTED receipt only, never successful calibration |

Code: `src/p6/models.py`, `src/p6/aoi.py`, `src/p6/aoi_config.py`,
`config/p6_aoi_rear_disabled_candidate_v1.json`. Time-weighted AOI outcomes and
continuous 150 ms mirror events remain unvalidated; no hardware completion claim.

## 11.8 M1 SuRT

| Required field | Status | Actual representation / limitation |
|---|---|---|
| `stim_id` | SEPARATE | P6 trial UUID/event identity |
| `stim_t` | SEPARATE / PHYSICAL BLOCKED | Browser presentation/receipt clocks; physical onset not verified |
| `resp_t` | SEPARATE / PHYSICAL BLOCKED | Browser event timestamp plus host receipt; clock mapping unverified |
| `correct` | SEPARATE | `TabletEvent.correct` |
| `nonresponse` | SEPARATE | Development timeout lifecycle only; no invented final timeout policy |
| Pause time | SEPARATE | Tablet pause lifecycle event |
| Resume time | SEPARATE | Tablet resume lifecycle event |

Code: `src/p6/tablet.py`, `src/p6/tablet_models.py`.
Operator SuRT is `NOT_IMPLEMENTED` / `EXTERNAL_UNCONNECTED`; neither NO_SURT nor
SURT is treated as proof of physical tablet readiness.

## 11.9 M2 video and interview

| Required field | Status | Actual representation / limitation |
|---|---|---|
| Playback start | SEPARATE / PHYSICAL BLOCKED | P6 playback acknowledgment/receipt; not connected to operator |
| Playback end | SEPARATE / PHYSICAL BLOCKED | P6 end/stop lifecycle; not an observed operator video |
| File name | SEPARATE | `MediaSelection.filename`; no final operator media selected |
| File version | SEPARATE | `MediaSelection.version`; no final operator media selected |
| Free-recall dish count | MISSING | No interview-answer form/export |
| Correct list-selection count | MISSING | No interview-answer form/export |
| False-selection count | MISSING | No interview-answer form/export |

The old synthetic diagnostic graph's fixture is explicitly not a video and is
not used to claim operator playback completion.

## 11.10 Hazard

| Required field | Status | Actual representation / limitation |
|---|---|---|
| `hazard_visible_t`: first displayed frame distinct from creation/reveal | SEPARATE / PHYSICAL BLOCKED | P7 distinguishes trigger/reveal/visible fields; geometric reveal is not photon onset |
| `brake_onset_t` | SEPARATE / PHYSICAL BLOCKED | P7 `first_brake_t`; raw onset distinct from meaningful-response approval |
| `steer_onset_t` | SEPARATE / PHYSICAL BLOCKED | P7 `first_steer_t`; event-preceding input distinguished by previous input |
| `ttc_at_response` | SEPARATE | P7 `ttc_at_response_s` and undefined reason |
| `min_ttc` | SEPARATE | P7 `min_ttc_s`, qualified collision-course/closing-speed model |
| `hazard_outcome`: avoid/stop/collision/departure/rescue/incomplete/unobservable | SEPARATE / PHYSICAL BLOCKED | P7 terminal/observation logic is not connected moving-ego rescue; no operator outcome synthesized |
| `hazard_start_speed` | SEPARATE | P7 `hazard_start_speed_mps` |
| `hazard_start_distance` | SEPARATE | P7 `hazard_start_distance_m` |

Code: `src/p7/metrics.py`, `src/p7/hazard.py` and
`P7_Traffic_Hazard_Development.md`. Operator hazard stays `BLOCKED_NOT_RUN` and
records an execution-policy SKIPPED outcome without an approval screen or fabricated
operator approval. P7 is not marked complete.

## 11.11 Surveys

Every item below is **EXTERNAL by the latest user-confirmed operating scope** and
has no answer form/export in this operator. The researcher still owns collection
and retention. Stage-level scope metadata is not a substitute for any value.

| Required fields | Current boundary |
|---|---|
| `age_years`, `gender` | Not collected |
| `license_valid`, `driving_exp_years`, `driving_frequency` | External collection; no automatic eligibility answer |
| `adas_experience`, `adas_types` | Not collected |
| `eyewear`, `sim_sickness_history`, `fit_to_participate` | External collection; no automatic eligibility answer |
| `toast_1`, `toast_2`, `toast_3`, `toast_4`, `toast_5`, `toast_6`, `toast_7`, `toast_8`, `toast_9` | No approved questionnaire text/scoring invented |
| `workload_mental` × M1 / M2 | Both missing |
| `workload_physical` × M1 / M2 | Both missing |
| `workload_effort` × M1 / M2 | Both missing |
| Post-interview memory responses / coded free text | Missing; no coding policy invented |

## 11.12 Integrity and interruption criteria

- Shared IDs/clock are connected for operator research CSVs and manifest. This does
  not assert connectivity for absent gaze/tablet streams.
- Existing run directories and participant registration claims reject reuse;
  no overwrite, automatic resume or removal of historical claims.
- Stage order, early rest advance, repeated bounded runs, ESC/QUIT and failure
  cleanup have software tests. Existing raw records are preserved on abort.
- App/process/power loss and physical device disconnect recovery are not validated
  by those tests; no universal loss-window or actor-cleanup guarantee is claimed.

## Acceptance boundary

Implemented: simplified unified operator, existing Stage authority, shared logging,
continuous planned-600-second training with F8 normal early finish, and single
continuous600/780-second module runs. Actual window completion remains distinct
from experimental completion. Blocked: approved complete module routes, full P5
exits/training, P6/Tablet/clock/AOI, moving P7/rescue and participant launch.
Actual administrative/survey/interview records remain externally managed, not
silently collected or completed by the software.
