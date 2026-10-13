# P8 / P7 v5 Hazard 개발 및 검증 기록

## Changes made

**결론: P8의 기존 pre-shutdown handoff에서 승인/준비 provenance를 주입받아 기존 P7
상태기계를 실행하는 scenario loop를 구현하고 offline fake-world로 검증했다. 이동 Ego에서
선행차가 비켜나 적재물을 노출하는 실제 실험은 아직 실행할 수 없다.**
이는 물리 구현 완료 보고서가 아니다. 연구 파라미터나 안전 정책을 임의로 정하지 않았다.

## 2026-10-13 executable P7 loop update

`OperatorRuntimeConfig.hazard_execution`은 기본 `None`이다. 따라서 기존 CLI와 실제 operator는
계속 `BLOCKED_UNAPPROVED`이며 actor/occurrence를 생성하지 않는다. 실행에는 코드로 주입한
`HazardExecutionPlan`이 필요하고, 이 plan은 `HazardConfig`, policy source,
approval reference, backend-readiness source, observation/actor backend factory 및 safety owner를
모두 명시해야 한다. 현재 CLI에는 이를 켜는 옵션이 없다.

이 주입 경로는 **Offline 전용**이다. `DEV_REHEARSAL`과 injected test client/viewer/input을
모두 요구하며, 승인 근거 문자열만 채워 실제 CARLA 실행을 허용할 수 없다. 물리 실행 scope는
제공하지 않는다. 실제 adapter와 연구 정책 승인 이후 별도 통합·검증이 필요하다.

명목 M2의 duration boundary에서 기존 viewer loop 안에서 `FINAL_HAZARD`로 전환한 뒤 같은
`ResearchNoASession`, Ego, world, viewer/sensor owner, control bundle, persistence, logger 및
`SessionClock`을 `HazardExecutionResources`로 전달한다. 이 경계에서는 shutdown brake가 아직
`NOT_ATTEMPTED`이며 viewer attach와 automation initialization을 반복하지 않는다.

전환은 pygame/audio teardown 전에 이뤄지고, 같은 input collection, interaction update,
scheduler/manual control, post-control persistence, draw/flip 순서를 계속 사용한다. backend는
geometry만 공급하며 실제 viewer가 수집한 `DriverInput`과 persistence timestamp를 합쳐 첫 frame에서
기존 `HazardScenario.arm()`을 호출하고 이후 frame마다 기존 `update()`를 호출한다. 임의
timeout/window/tick cap은 추가하지 않았고 P7 자체의
`COMPLETED`/`ABORTED`에서 종료한다. observation stream이 먼저 끝나면 명시적으로
`OBSERVATION_STREAM_ENDED`/UNOBSERVABLE로 abort한다. 각 tick은 공통 logger의 별도
`FINAL_HAZARD_ASSESSMENT` telemetry row와 기존 P7 event를 남기며 raw `DriverInput`과
`input_source`는 candidate/validated response와 분리된다.

backend가 Lead/Cargo를 포함한 자기 소유 자원을 정리하며 offline 검증은 실제 `OwnedActors`의
역순 role-scoped destroy를 사용했다. Ego/viewer는 계속 `ResearchNoASession` owner가 한 번만
정리한다. collision/departure의 `termination_requested`는 별도 safety owner의
`request_safe_shutdown()`으로 전달하며 `REQUESTED`, `RETURNED_NOT_PHYSICAL_SUCCESS`, `FAILED`를
구분한다. 반환은 물리 정지 성공으로 기록하지 않고 `physical_stop_success`는 `null`이다.

### Offline evidence

- `tests/test_p8_hazard_execution.py`는 실제 fake-world `ResearchNoASession`을 거쳐 명목 780초
  요청 후 9개 hazard tick, 기존 `HazardScenario` 완료, 같은 resource identity, viewer attach/run
  1회, shutdown 전 실행, FINAL_HAZARD telemetry/P7 raw-input event를 확인한다.
- collision abort에서 safety owner 요청/반환과 Lead/Cargo cleanup을 확인한다. observation backend
  오류와 safety-owner 오류에서도 owned actor와 기존 viewer/Ego cleanup이 각각 한 번 수행되며
  safety failure가 `REQUESTED` 후 `FAILED`로 기록됨을 확인한다.
- 기존 `tests/test_p8_hazard_records.py`는 plan이 없는 기본 경로가 계속 BLOCKED이고 occurrence를
  claim하지 않음을 검증한다. 실제 CARLA/Unreal/Tobii/network 연결은 하지 않았다.
- 최종 전체 Offline suite는 loopback attestation을 포함해 **1546 passed**다.
  부모 실행에서도 `.venv\Scripts\python.exe -m pytest -q`로 **1546 passed, 28.87s**를 확인했다.
- 실제 `DriverView.run()`의 명목 종료 이후 3개 연속 frame에서 input/scheduler/draw/flip이
  계속 실행되고 최종 정리가 한 번 수행되는 회귀를 추가했다. 단일 continuation frame만
  검사하던 테스트가 놓친 duration 재검사 종료 문제도 수정했다.
- M2 persistence는 handoff에서 종료하며 reducer는 M2 phase만 사용한다. Hazard frame은
  별도 segment에 한 번씩 기록하고 M2 자동화 비율·진단·카메라 측정창에 합산하지 않는다.
- 획득/관측 오류와 flush/emit 오류가 동시에 발생해도 최초 오류를 보존하고, snapshot과
  소유 Actor cleanup 증거를 남긴다.
- 별도 Offline driver: M2 active=780.0, NOA_ACTIVE handoff, hazard ticks/telemetry=9/9,
  same Ego/viewer, attach/run=1/1, Lead/Cargo destroy=1/1, Ego destroy=1을 확인했다.
  최초 가시 frame과 물리 정지 성공은 null이다. Ruff 전체 검사는 통과했다.
  basedpyright 미설치로 타입 검사 통과는 주장하지 않는다.

### 실제 CARLA 후속 검증 순서 (이번 작업에서는 실행하지 않음)

1. 먼저 P5만 검증한다. 기존 단일 operator 진입점과 승인된 runtime/route/window를 사용하고,
   attestation 및 `EMPTY_REHEARSAL_ONLY`를 통과한 경우에만 요청한다. 임의 재전송하지 않는다.
2. 같은 request ID의 `lane_change_request`, `lane_change_lifecycle`,
   `lane_change_geometric_sample`을 수집한다. source/target road-section-lane, advancing frame,
   차량 pose, target pose/width, lateral/heading error 및 alignment failure를 비교한다.
   SUBMITTED_TO_TM과 실제 이동/완료를 구분한다. 완료 후 signal autooff readback도 확인한다.
3. 제동·조향·NoA 해제에서 generation 무효화 및 deregistration을 확인한다. 출구 안내 표시,
   확인/거부/무응답, `lc_exit_entry_observed`, `lc_completed:EXIT`를 각각 확인한다.
   재합류는 현재 NOT_CONFIGURED이므로 승인된 재합류 경계 없이 성공을 주장하지 않는다.
4. P7은 지금 CLI로 활성화할 수 없다. 시간·위치 규칙과 허용 범위/미도달 정책, lead 궤적,
   cargo, 반응 임계값, 구조 경계, 관찰 종료를 먼저 확정하고 physical backend를 구현한다.
   TM 장애물 반응 정책도 검증해야 하며 P5의 제한을 해제하여 대신 해결하지 않는다.
5. 이후 같은 Ego의 780초 경계, lead 실제 pose/velocity, cargo 정지, 가림과 rendered frame,
   원시 입력, NoA 제어권, collision/departure 및 구조 명령/실제 정지를 별도 증거로 수집한다.
   Host/CARLA frame/session clock과 phase를 함께 대조하고 정상·ESC·창 종료·오류 후
   Actor/sensor 정리와 부분 CSV/manifest를 확인한다.

### Executable-loop update file list

| 파일 | 이번 update의 변경 |
|---|---|
| `src/p8/operator_hazard_contracts.py` | approval/scope/backend/resources/cleanup/safety-owner typed contracts. |
| `src/p8/operator_hazard.py` | 기존 P7 arm/update loop, FINAL_HAZARD telemetry/event, safety request 및 cleanup evidence. |
| `src/p8/operator_hazard_record.py` | 기본 blocked record를 실행 loop와 분리. |
| `src/scenario/driver_view.py` | pygame teardown 전 typed per-frame continuation. |
| `src/scenario/research_noa_session.py` | M2 boundary metrics freeze와 opt-in continuation factory. |
| `src/scenario/research_noa_persistence.py` | M2 flush 후 FINAL_HAZARD segment/recorder rebind. |
| `src/p8/operator_model.py` | 기본 `None`인 opt-in hazard execution plan. |
| `src/p8/operator_continuity.py` | identity 검증 뒤 retained `ResearchNoASession` 접근. |
| `src/p8/operator_components.py` | FINAL_HAZARD에서 기본 blocked 또는 주입 loop 실행/manifest snapshot. |
| `src/p8/operator_controller.py` | nominal M2 handoff 결과를 물리 미검증 BLOCKED stage outcome으로 보존. |
| `tests/p8_hazard_execution_support.py` | fake observations/backend/safety owner 및 실제 `OwnedActors` fixtures. |
| `tests/test_p8_hazard_execution.py` | multi-tick, common logging, completion/collision/error/safety-failure cleanup. |
| `docs/P8_P7_V5_Hazard_Status.md` | 본 구현/검증/미완료 경계. |
| `docs/P7_Traffic_Hazard_Development.md` | P7 상태기계의 새 P8 offline caller와 production blocker. |

## 2026-10-13 Module 2 -> guarded P7 continuity update

명목 780초 Module 2가 성공하면 `ResearchNoASession`이 shutdown을 시작하기 전에 P8의
동기 handoff callback을 호출한다. 이 callback은 같은 Runner, Ego, world, viewer,
control bundle, persistence, logger, clock에서 `MODULE_2_RUNNING`을 닫고 별도
`FINAL_HAZARD` stage를 연 뒤 승인되지 않은 hazard를 즉시 `BLOCKED`로 기록한다.

Handoff 순간에는 M2의 실제 MANUAL/NOA_ACTIVE state가 유지되고 shutdown brake는
`NOT_ATTEMPTED`다. 기록은 `SAME_LIVE_SESSION_PRE_SHUTDOWN`으로 이 순서를 보존한다.
그 직후 기존 `finally`가 MANUAL 전환, shutdown brake, P5/P6 종료와 actor/viewer cleanup을
수행한다. Hazard control/render/observation loop는 실행하지 않으며 이를 움직이는
위험사건 또는 센서 관측 성공으로 해석하지 않는다.

후속 검토에서 이 경계의 기록과 종료 계약을 보강했다. Handoff가 현재 stage를
`FINAL_HAZARD`로 바꾼 뒤에도 완료된 bounded-run event는 명시적으로
`MODULE_2_RUNNING` segment에 기록된다. 최종 world snapshot 획득이 실패한 run은
handoff callback을 호출하지 않는다. Live binding 없이 별도 `FINAL_HAZARD`에 진입한
경우 continuity/control/sensor 상태는 각각 `NOT_BOUND`/`INACTIVE_NOT_BOUND`이며 live
상태를 주장하지 않는다. 성공한 terminal shutdown은 close에서 반복하지 않고, 실패한
shutdown만 close에서 한 번 더 시도한다. 이 idempotency는 P7 arm/update/actor 실행을
추가하지 않으며 pre-shutdown P8 경계에만 적용된다.

위험 stage 진입 시 실제 M2 session binding을 검증하되 P7 actor를 생성하거나 occurrence를
claim하지 않는다. `hazard_visible_t`와 `first_displayed_frame`, raw observation,
candidate observation은 모두 `null`이고 observation readiness는
`BLOCKED_UNAPPROVED`다. trigger time+position, lead trajectory, 반응 임계값, rescue boundary,
관찰 종료 기준을 추가하지 않았다. UI와 stage record는 이제 이 결과를 `SKIPPED`로
덮어쓰지 않고 `BLOCKED`로 보존한다.

현재 tree 전체에서 DOCX를 다시 검색했지만 찾지 못했다. 교수 원본 DOCX를 읽었다고
주장하지 않으며 이 문서와 `CARLA_Experiment_Design_v5_Source_of_Truth.md`의 재구성본만
현재 작업의 문서 근거다.

### Offline verification for this update

- Oracle 후속 수정의 focused event/snapshot/unbound/shutdown matrix는 `41 passed`다.
  전체 offline collection `1523`개를 검토한 뒤 loopback listener fake를 포함한 전체 suite를
  실행해 `1523 passed`를 확인했다. 실제 CARLA/Tobii 연결은 없었다.
- Fake TM 성공 경로는 activation 후 deregistration 1회와 shutdown brake batch 1회만
  수행했다. 첫 shutdown delivery를 실패시킨 경로는 close에서 1회 재시도해 성공했다.
- 새 pre-shutdown continuity/blocking 계약은 변경 전 MANUAL/NOA 모두 red를 확인했고
  구현 후 `tests/test_p8_hazard_records.py`의 `13 passed`다.
- runtime-attestation socket test를 제외한 P7/P8 집합은 `365 passed`, P5/P6/Phase2
  집합은 `256 passed`다. CARLA/Unreal/Tobii/hardware 연결은 수행하지 않았다.
- 변경 Python 파일의 `ruff check`와 `ruff format --check`는 통과했다. 새 continuity와
  run-config 분리 파일을 포함한 no-excuse audit도 통과했다.
- `basedpyright` module과 LSP server는 현재 `.venv`에 설치되어 있지 않고 이전 설치
  거절 상태이므로 타입 검사 통과를 주장하지 않는다.
- fake-world MANUAL/NOA 통합 경로에서 hazard binding은 shutdown brake
  `NOT_ATTEMPTED`와 실제 M2 control mode를 기록했고, run 반환 시 viewer/Ego cleanup은
  각각 정확히 한 번 완료됐다.

## 이전 작업 기록 및 기존 P7 기능 분석

아래 642개 회귀 결과와 환경 확인은 이전 작업 기록이다. 이번 작업의 변경 및
검증 결과는 위 2026-10-13 update가 우선하며, 이번에는 다른 환경이나 worktree를
사용하지 않고 현재 프로젝트 `.venv`로 전체 Offline 테스트 1523개를 실행했다.

분석 기준 HEAD: `f7308674f2366f6e0b28da86dd874e7de396ba11`
(`chore: P8 Traffic Manager, P5 integration`). 시작 시 이미 다수의 P5/P8/카메라/TM
수정 및 미추적 파일이 있었다. 기존 수정은 보존했으며 commit, stash, branch/worktree
전환, native patch 변경, 환경 설치, 서버 접속, 실제 주행 및 장비 제어를 하지 않았다.

프로젝트 전체 검색에서 `실험설계 v5(1).docx`를 포함한 DOCX를 찾지 못했다.
원본을 읽었다고 주장하지 않는다. 아래의 원본 절 번호는 사용자 제공 요구사항에 따른
분류이고, 실제 비교 문서는 `docs/CARLA_Experiment_Design_v5_Source_of_Truth.md`다.
Source of Truth, P6, 참가자 문구, HUD, 배정 정책, P5 제어 및 연구 설정은 수정하지 않았다.

### 기존 구현 분석

| 영역 | 코드 근거와 현재 상태 |
|---|---|
| 단일 운영 진입점 | `src/p8/operator_cli.py` → `OperatorController` → `ExperimentSession` → `OperatorComponents` → `OperatorRunExecutor` → `ResearchNoARunner`. 별도 참가자 실행 경로를 만들지 않았다. |
| 전체 단계 | `src/p8/model.py`: SESSION_CREATED → CONSENT_AND_ELIGIBILITY → TRAINING → EYE_TRACKER_CALIBRATION → MODULE_1_PREPARE → MODULE_1_RUNNING → MODULE_1_COMPLETE → REST → MODULE_2_PREPARE → MODULE_2_RUNNING → FINAL_HAZARD → POST_EXPERIMENT → SESSION_COMPLETE. 중단은 ABORTED. |
| 시간 | `TimingProfile.production()`은 600/120/780초. `FINAL_HAZARD`는 별도 단계이며 300초 관찰 종료 기준은 없다. `src/p8/orchestrator.py`는 단계 시작/끝을 기록한다. 현재 rehearsal 휴식은 기존 운영 정책상 강제 120초 대기가 아니다. |
| M2 이후 연결 한계 | 명목 M2의 동일 live session에서 shutdown 전에 `FINAL_HAZARD`를 열고 MANUAL/NOA_ACTIVE state와 object identity를 bind한다. 승인되지 않은 hazard는 즉시 BLOCKED이며 이후 기존 shutdown/cleanup이 실행된다. 새로운 차량은 만들지 않는다. |
| P7 상태 | `src/p7/hazard.py`: IDLE, ARMED, TRIGGERED, LEAD_MOVING_ASIDE, HAZARD_REVEALED, RESPONDED, COMPLETED, ABORTED. 마지막 세 상태의 의미도 DEV 관측과 연구 결과를 구분해야 한다. |
| P7 실행 경로 | `src/p7/live.py`는 기존 외부 정지 Ego에 연결하는 bounded DEV smoke 전용. `src/p8/components.py`는 synthetic P8 경로에서 stub actors를 사용한다. 둘 다 실제 operator moving-hazard 구현이 아니다. 이번에 이 프로그램들을 실주행으로 실행하지 않았다. |
| Actor 소유권 | `src/p7/carla_actors.py`가 cargo/lead를 생성하고 `OwnedActors`에 등록한다. `src/p7/ownership.py`는 소유 Actor만 정리하고 destroy 실패를 보존한다. Ego 소유권 획득은 금지된다. `src/p7/live.py` finally가 센서/뷰/소유 Actor 정리를 수행한다. |
| 선행차 동작 | 기존 `CarlaHazardActors.advance()`는 physics를 끈 `set_transform` 기반 DEV kinematic 이동이다. P5/TM 차로변경 완료에 의존하지 않는다. `src/p7/carla_observation.py`가 실제 lead transform을 읽고 목표 횡위치와 비교하므로 명령만으로 완료를 선언하지는 않는다. 자연스러운 물리 주행은 미검증이다. |
| 노출 | `src/p7/carla_visibility.py`의 projected bounding boxes는 기하학적 proxy다. 실제 mesh 가림, 사용 중인 카메라와의 일치, 렌더된 최초 가시 프레임은 입증하지 않는다. |
| 발동 | 기존 P7은 route station 기준이며 DEV 정지 smoke에는 lead station 옵션이 있다. 최종 시간·위치 결합 규칙은 없다. 시선 입력은 발동에 사용하지 않는다. 시간 조건을 추가했다는 주장이나 임의 AND/OR/time window 정책은 추가하지 않았다. |
| 배정과 상태 | M2 MANUAL/NOA_L2 초기화 및 브레이크/조향/버튼 해제는 기존 경로 유지. MANUAL에서 성공한 활성화는 기존 코드가 protocol deviation으로 기록하며 배정을 변경하지 않는다. 새로운 모드 잠금은 없다. |
| TM 제어 | `src/scenario/research_noa_traffic_manager.py`와 기존 checked-control delivery 경로 유지. TM 활성 중 수동 writer 억제, 해제 후 수동 전달. 일반 collision response는 유지된다. |
| 안전 | P7 collision sensor는 actor ID/frame/simulation timestamp를 보존한다. road departure는 기존 driving waypoint 관측에 기반하며 최종 연구용 경계값이 아니다. `termination_requested`는 요청이지 Ego 자동 정지 성공이 아니다. P8 위험사건 구조 owner는 아직 연결되지 않았다. |
| P5 | 현재 patched TM 요청에는 `EMPTY_REHEARSAL_ONLY` inventory gate가 있다. lead/cargo가 있으면 기존 일반/P5 요청 계약과 충돌한다. 상시 Z/X 요청은 v5의 모듈별 진출 1회 한정과 다르며 연구 승인 필요. 이번 작업에서 변경하지 않았다. |
| P6 | 시선, AOI, Tobii, calibration, tablet 코드는 변경하지 않았다. 위험사건 발동에 시선을 연결하지 않았다. 기존 operator의 P6 차단/부분 상태도 유지한다. |

### 이번에 구현한 기록

1. `HazardOutcome`은 AVOIDED, STOPPED, COLLISION, ROAD_DEPARTURE,
   AUTOMATIC_RESCUE, INCOMPLETE, UNOBSERVABLE을 구분한다.
   열거형에 값이 있다는 사실은 해당 결과 자동 판정이 구현됐다는 뜻이 아니다.
2. 공통 canonical record에 v5의 8개 필수 변수 이름을 모두 제공한다.
   `hazard_outcome` 외 7개 측정 필드는 현재 `null`이다. `parameter_status=DEV_ONLY`,
   `canonical_status=UNVALIDATED_RESEARCH_DEFINITIONS`가 함께 기록된다.
3. 기존 `first_brake_t`, `first_steer_t`, candidate response, `ttc_at_response_s`,
   `min_ttc_s`, `hazard_start_speed_mps`, `hazard_start_distance_m`는 보존한다.
   이 값은 DEV 진단이며, 검증된 v5 유효 반응/첫 표시/연구 시작 상태로 승격하지 않는다.
4. P7 상태 이벤트에 원시 `driver_input`, `input_source`, 공유 SessionClock의
   `session_elapsed_s`, collision/departure evidence를 함께 보존한다.
   이전에는 조기 종료 분기에서 누락되던 마지막 원시 입력도 terminal event에 남는다.
5. 단일 collision/departure 관측은 대응 결과를 기록하고, 기술적 setup/관측 실패는
   UNOBSERVABLE로 기록한다. 동시에 collision과 departure가 보고되면 둘 다 남기고
   임의 우선순위 없이 결과는 `null`로 유지한다. 이는 인과관계나 연구 경계 승인 증명이 아니다.
6. 일반 중단 및 DEV 관찰 시간 만료는 연구 결과를 추정하지 않는다.
   `outcome=DEV_OBSERVATION_ENDED_NOT_VALIDATED`는 기존 DEV 진단으로 유지하며
   `hazard_outcome=INCOMPLETE`로 매핑하지 않는다. 완료/중단 상태는 뒤늦은 abort로 덮어쓰지 않는다.
7. 실제 operator `FINAL_HAZARD` 진입 시 `hazard_observation`을 한 번 기록한다.
   `BLOCKED`, `NOT_OBSERVED`, `UNOBSERVABLE` 및 미연결 사유를 저장한다. Live M2
   binding이 있으면 실제 control state를 기록하고, binding이 없으면 `auto_state=null`과
   명시적인 inactive/unbound 상태를 기록한다. M2 배정에서 상태를 추정하지 않는다.
   Actor/runner 생성과 occurrence claim은 수행하지 않는다.
8. 기존 완료 blocker를 유지한다. 이번 update에서는 위험사건의 rehearsal 결과를
   `SKIPPED`가 아닌 `BLOCKED`로 보존한다.
   record는 manifest의 component snapshot에도 남고 중단 시 부분 로그가 보존된다.
   반복 entry 방지는 현재 component 생명주기 내 idempotency다. crash/부분 I/O에 대한
   exactly-once 저장을 새로 보장하지 않는다. 실제 발생 예약은 기존 persistent registry를 사용한다.

### 타임스탬프와 차량 데이터

`src/logging/csv_logger.py`의 공통 열은 participant_id, run_id/study_run_id,
session_elapsed_s, 배정, segment/phase/module, host monotonic/UTC, CARLA simulation/frame,
snapshot capture host bounds, external_timestamps_json이다. 기존 구조를 변경하지 않았다.
외부 원본 timestamp 보존이 장치별 동기화/드리프트 보정의 검증을 뜻하지는 않는다.

| v5 차량 변수 | 현재 경로/한계 |
|---|---|
| speed | telemetry `speed_kmh`; P7 진단 `ego_speed_mps`. 단위를 혼동하지 않는다. |
| lane_pos | 공통 telemetry 열에는 없다. 일부 lane geometry/P5 진단과는 별개다. |
| steer/brake | telemetry `steering`/`brake`는 `get_control()` readback. 실제 물리 actuator의 독립 증거는 아니다. P7 event `driver_input`은 원시 입력이며 source를 함께 보존한다. |
| accel | `throttle` 열은 있으나 물리 가속도와 동일시하지 않는다. 공통 accel 측정은 미완료다. |
| headway_dist/rel_speed/ttc | 공통 telemetry에는 없다. P7 DEV event의 distance_m/closing_speed_mps/ttc_s는 cargo 대상이며 일반 선행차 headway와 구분한다. |
| 차로 번호 | telemetry lane_id 존재. |
| 샘플링 주기 | 원시 시각 차이로 간격을 재구성할 수 있지만 공통 telemetry에 명시적 주기 열은 없다. |

TTC 계산은 `src/p7/metrics.py`에서 유한한 양의 clearance, 양의 closing speed,
명시된 collision_course가 있을 때만 유효하다. 경로 미확인/횡방향 상대속도 등은
미정으로 남는다. 반응시간은 현재 raw/geometric DEV 진단만 존재한다.
`hazard_visible_t`, 유효 반응 임계값, 시간 기준이 확정되지 않았으므로 연구용 반응시간은
생성하지 않는다. camera callback/frame/flip 진단이 곧 적재물 첫 가시 프레임 검출은 아니다.

## Files changed

| 파일 | 변경 이유 |
|---|---|
| `src/p7/types.py` | v5 결과 taxonomy 추가. |
| `src/p7/metrics.py` | P7/P8에서 공유하는 명시적 미검증 canonical record. |
| `src/p7/hazard.py` | terminal evidence, 원시 입력, 공통 시각, 기술적 결과 및 terminal 불변성. |
| `src/p8/operator_components.py` | 실제 operator의 마지막 별도 단계에 차단/미관측 기록 및 manifest 연결. |
| `tests/test_p7_v5_records.py` | canonical null, raw/candidate 분리, 단일/동시 safety evidence, 중단/완료 기록 보호. |
| `tests/test_p8_hazard_records.py` | 4개 배정 조합, 780초 요청 이후 별도 단계, 재진입, skip/abort, 실제 Actor 없음 검증. |
| `docs/P8_P7_V5_Hazard_Status.md` | 본 분석, 검증 기록, 미결정 사항 및 compliance matrix. |

## Verification results

모든 실행 테스트는 프로젝트 `.venv\Scripts\python.exe`의 Python 3.12.10에서 수행했다.
실제 CARLA 연결이 아닌 fake runtime 및 임시 디렉터리만 사용했다.

| 검증 | 결과 |
|---|---|
| 새 계약 red → green | 새 두 테스트 파일: 변경 전 6 failed/7 passed, 구현 후 **13 passed**. 자식 작업의 초기 schema 부재 실패도 확인됨. |
| P7/P8 regression | `tests/test_p7_*.py`, `tests/test_p8_*.py` 중 runtime attestation 제외: **362 passed, 19.49s**. |
| P5/P6/Phase2 regression | `tests/test_p5_*.py`, `tests/test_p6_*.py`, `tests/test_phase2_*.py`: **256 passed, 9.81s**. |
| 공통 로그/시간 regression | `test_research_csv_logger.py`, `test_research_telemetry.py`, `test_p4_core.py`: **24 passed, 0.25s**. |
| 합계 | 최종 세 regression 집합 **642 passed**. 새 13개는 362개 안에 포함되므로 더하지 않는다. 전체 저장소 full suite 실행 결과가 아니다. |
| 정적 검사 | 수정한 Python 6개 파일 `ruff check` 통과. LSP basedpyright 미설치 및 기존 설치 거절로 진단 불가. 타입 검사 통과라고 주장하지 않는다. |
| 수동 offline 사용 | P8 controller를 fake runner로 import/실행: M2 요청 780.0, 초기 NOA_ACTIVE, hazard BLOCKED/UNOBSERVABLE, fake 차량 3대 각각 cleanup 1회, skip 후 POST_EXPERIMENT, abort 후 logger closed 확인. |
| CLI 경계 | 기존 `src.p8.operator_cli --help` 정상, ack 없는 입력은 parser 단계에서 거절. UI/실주행은 시작하지 않았다. |
| 검토 | Oracle read-only 최종 검토에서 변경 6개 파일에 must-fix semantic defect 없음. |

`test_p8_runtime_attestation.py`는 로컬 listener/socket 테스트를 포함하므로 이번 보수적인
offline 회귀 집합에서 제외했다. CARLA/Tobii/wheel 실물, UI 화면, 물리 충돌, TM 프레임별
제어권, 자동 구조 및 첫 가시 프레임은 검증하지 않았다.

### 재현 가능한 offline 명령

PowerShell의 wildcard 인자 확장을 가정하지 않는다.

```powershell
$tests = @(Get-ChildItem tests -File | Where-Object {
    $_.Name -match '^test_p[78]_.*\.py$' -and
    $_.Name -ne 'test_p8_runtime_attestation.py'
} | ForEach-Object { $_.FullName })
& '.venv\Scripts\python.exe' -m pytest -q @tests

$tests = @(Get-ChildItem tests -File | Where-Object {
    $_.Name -match '^test_(p[56]_|phase2_).*\.py$'
} | ForEach-Object { $_.FullName })
& '.venv\Scripts\python.exe' -m pytest -q @tests

& '.venv\Scripts\python.exe' -m pytest -q tests/test_research_csv_logger.py tests/test_research_telemetry.py tests/test_p4_core.py
```

### 환경 확인과 후속 운영 명령

기본 PATH의 `python`은 3.10.11이며 이 프로젝트 검증용 interpreter가 아니다.
프로젝트 `.venv`는 3.12.10/pygame 2.6.1이다. 서버/포트 확인 없이 로드된 확장 파일만
기존 hash 함수로 읽었다.

| 환경 | loaded libcarla SHA256 | 승인값 일치 |
|---|---|---|
| `C:\dev\carla\.venv` | `81E9AC361D989198E208C525D4F7C94895277E6532224FBD8EA4DBB5C36B1AD2` | 아니오 |
| `C:\dev\carla-native-tm-cancel-20261012\.venv-p8` | `636D249A6898FEB4A6A8D166917DDDE04F26B70DAB9E95C1D3D50E9ACF3CA471` | 예 |

두 번째 환경은 기존 문서 `docs/P8_Patched_TM_LaneChange.md`에 지정된 interpreter다.
다른 worktree의 코드로 대체한 것이 아니며, 작업 디렉터리와 import 대상은 현재
`C:\dev\carla`다. 환경/확장/승인 해시는 변경하지 않았다. 일치 결과는 TM listener의
소유권, 서버 호환성, 물리 안전성 검증과 별개다.

현재 안전하게 확인 가능한 기존 진입점 명령:

```powershell
# C:\dev\carla에서 실행. 도움말만 표시하며 서버 연결/주행을 시작하지 않음.
& 'C:\dev\carla-native-tm-cancel-20261012\.venv-p8\Scripts\python.exe' -m src.p8.operator_cli --help
```

추후 일반 P8 운영을 재개할 때는 위 interpreter와 기존 승인 assignment/route/spawn/TM
운영 옵션을 사용한다. 최종 위험사건을 켜는 CLI 옵션은 현재 없다. 여기서 임의의 live
명령이나 미정 파라미터 예시를 제공하지 않는다. 해시 gate를 우회하거나 wheel을 교체하지 말 것.

## Remaining concerns

### 연구자 결정이 필요한 사항

1. **시간·위치 결합:** 어떤 simulation epoch/모듈 기준 시각과 어느 road/section/lane/
   station 또는 절대 위치를 사용하는가? 허용 범위, 불일치/미도달 시 처리까지 승인해야 한다.
   한 조건을 무시하거나 gaze/운전자 반응에 따라 지연시키지 않는다.
2. **사건 시작:** trigger, 선행차 이동 시작, 가림 해제 중 시작 속도/거리의 기준은 무엇인가?
   거리의 bounding-box clearance와 center-to-center 중 승인된 정의가 필요하다.
3. **설정표:** 목표속도, 가림 해제 거리, 최초 가시 TTC, cargo blueprint/종류/크기/위치,
   lead 경로/속도/측면 회피, 유효 브레이크/조향 임계값을 확정해야 한다.
4. **구조/종료:** 도로이탈 경계, collision 처리, 구조 owner의 제어권 인수/정지/종료,
   관찰 종료 및 회피/정지/미완료 판정 기준을 승인해야 한다. 약 5분 운영 배정은 종료 기준이 아니다.
5. **MANUAL의 재활성화 및 일반 Z/X 정책:** 기존 protocol deviation 정책과 확장 기능을
   연구자가 확인해야 한다. 이번에 잠금 또는 기능 삭제로 임의 해결하지 않았다.

### 물리 구현 및 안전 blocker

- M2 종료와 마지막 단계 진입 사이의 동일 live session handoff는 구현했다.
  주입 observation/actor/safety backend로 P7 arm/update loop를 지속하는 실행 seam도 구현했다.
  기본 operator에는 승인 plan이 없어 계속 차단되며, 실제 CARLA moving-hazard backend는 없다.
  기존 정지 smoke를 production 경로로 호출하지 않는다.
- P7 motion은 kinematic DEV다. 본실험용 lead 주행 및 검증된 측면 회피 adapter가 필요하다.
- TM은 선행차 충돌 위험에 대응해 감속/제동할 수 있다. cargo static prop이 TM collision
  pipeline에 들어오는지도 실제 모델/서버에서 확인해야 한다.
- 특정 actor pair의 `collision_detection(ego, obstacle, False)`는 검토 후보일 뿐 적용하지 않았다.
  물리 collision 비활성화가 아니며, 선행차와 cargo의 TM 동작 전체를 해결한다고 보장할 수 없다.
  기존 API로 충족되지 않으면 Native 변경 승인/별도 기술 결정을 받아야 한다.
- 일반 collision avoidance를 끄지 않았다. P7 안전 요청을 기존 종료 제동으로 연결한 것으로
  가장하지 않았으며, 실제 rescue owner 미연결 상태를 기록한다.
- 카메라 프레임의 cargo 최초 노출 검출과 실제 표시 프레임 연결이 필요하다.
  spawn/명령/geometry proxy/flip duration을 `hazard_visible_t`로 대체하지 않는다.

참고 공식 문서: [CARLA 0.9.16 TM collision stage](https://github.com/carla-simulator/carla/blob/0.9.16/Docs/adv_traffic_manager.md),
[CARLA 0.9.16 Python API](https://github.com/carla-simulator/carla/blob/0.9.16/Docs/python_api.md).
API 존재와 문서 설명은 현재 patched binary의 실제 동작 검증을 대체하지 않는다.

### 사용자 후속 live 체크리스트

위 결정/구현 이후 기존 P8 프로그램에서만 검증한다.

- 선행차 자연스러운 주행, 실제 actor pose/velocity와 목표 차로 도달, 명령 실패/지연.
- 정지 무생물 적재물의 실제 가림 및 노출, renderer/camera frame와 최초 표시 시각 대응.
- MANUAL/NOA_L2 동일 road/vehicle/traffic/cargo/목표속도, 실제 사건 시작 속도/거리 비교.
- TOR/충돌 경고/장애물 자동 회피 제동 없음, 평상시 차로/속도 지원 및 운전자 우선권 유지.
- 원시 입력과 readback/전달 제어 분리, 유효 반응 onset, 접근 경로/속도 기반 TTC 재구성.
- collision/departure/구조/관찰 종료 각각의 owner 동작, 비정상 종료 후 센서/Actor 정리.
- 공통 ID/시간, 원본 장치 timestamp, 단계 경계, 부분 자료 및 최종 CSV/manifest 실제 값 확인.

## V5 Compliance Matrix

원본 절 번호는 사용자 요청 기준이다. 괄호의 SoT 절은 실제 읽은 프로젝트 문서의 대응 절이다.
동일 요구 안에서도 기록 계약과 물리 실행 상태를 분리한다.

| 요구 | 상태 | 코드 및 검증 근거 |
|---|---|---|
| v5 3.3: M1 600초 / M2 780초, 배정과 실제 자동화 상태 분리 (SoT 1, 6) | Implemented and offline verified | `src/p8/model.py`, `operator_execution.py`, 기존 automation interaction. `test_p8_operator_backend.py`, `test_p8_hazard_records.py`; 362개 회귀에 포함. 물리 13분 주행 증명은 아님. |
| v5 3.3: 별도 마지막 단계와 시작/종료 로그 (SoT 2) | Implemented and offline verified | `orchestrator.py`, `services.py`, `operator_components.py`; 신규 5개 P8 사례에서 별도 segment/skip/abort 확인. |
| v5 3.3: M2에서 위험사건까지 동일 차량 연속 주행 | Offline executable seam verified; physical driving blocked | 명목 M2의 동일 Ego/world/viewer/control/logger/clock과 MANUAL/NOA state로 shutdown 전에 `FINAL_HAZARD`를 bind하고 주입 backend로 기존 P7 loop를 실행한다. 기본 operator는 plan 부재로 BLOCKED이며 실제 moving-hazard adapter는 없다. |
| v5 3.3/3.5: 두 조건 동일 위험 설정/시작 상태 | Blocked by unresolved research parameter | `src/p7/config.py`는 DEV_ONLY. 최종 설정 부재. 신규 4조합 테스트는 동일한 차단 계약만 증명. |
| v5 3.5: Module 1/훈련 제외, 참가자당 한 번 (SoT 3.3, 8) | Implemented and offline verified | `hazard.py`, `occurrence.py`; `test_p7_hazard.py`, `test_p8_lifecycle.py`. P8 blocked 기록은 발생 예약을 소비하지 않음. |
| v5 3.5: 선행차가 비켜나 정지 무생물 적재물 노출 | Partially implemented | `carla_actors.py`, `carla_observation.py`; `test_p7_live_fake.py`, `test_p7_carla_boundary.py`. DEV kinematic만 존재, operator live 경로 없음. |
| v5 3.5: 명령과 실제 선행차 이동/노출 구분 | Implemented but live unverified | 기존 pose/횡위치 관측과 projected bounds; `test_p7_geometry.py`, `test_p7_bounding_box_cache.py`. render 노출 입증은 아님. |
| v5 3.5: 사전 시간·위치 결합 발동 | Blocked by unresolved research parameter | `hazard.py`는 기존 station-only DEV 규칙. 승인된 시간 기준/결합/실패 정책 없음. |
| v5 3.5: gaze/반응에 따른 선택적 발동 금지 | Implemented and offline verified | hazard 관측에는 gaze 조건이 없고 raw 반응은 trigger를 선택하지 않음. `test_p7_hazard.py`의 one-shot/trigger 사례. |
| v5 3.5: TOR/장애물 경고 미제공, 사전 비공개 | Implemented and offline verified | P7 warning actuation 없음, 참가자 문구/UI 변경 없음. `test_p7_logging_cli.py`; 실제 화면 검증은 별도. |
| v5 3.5: 자동 회피 제동 없음 + 평상시 NoA 지원 | Partially implemented | P7 자체 Ego 제어 없음. 기존 TM 일반 collision response 유지. `test_p8_traffic_manager.py`; cargo/lead 자동제동 부재는 미검증. |
| v5 3.5: 브레이크/조향 우선 및 재활성화 | Implemented but live unverified | 기존 interaction/TM checked-delivery 경로 유지. `test_p8_control_transitions.py`, `test_p8_control_integration.py`; 실제 프레임/장비 미검증. |
| v5 3.5: 충돌/도로이탈 evidence 및 종료 요청 | Implemented and offline verified | `hazard.py`, `carla_observation.py`; 신규 단일/동시 evidence 테스트 및 기존 sensor fake 테스트. 최종 연구 경계 승인은 아님. |
| v5 3.5: 경계 도달 시 자동 정지/종료 | Partially implemented | P7 종료 요청까지만 존재. P8 rescue owner 미연결, `rescue_status=OWNER_NOT_BOUND`. 구체 경계도 미확정. |
| v5 6: 공통 ID, 원본 timestamp, 공통 시간 보존 (SoT 11) | Implemented and offline verified | `csv_logger.py`, `timestamp.py`, `SessionClock`, 새 terminal raw 기록. 로그/telemetry/P4 24개 및 P7/P8 회귀. 장치 동기화 정확도 미검증. |
| v5 6: 위험사건 필수 schema와 미관측 상태 | Implemented and offline verified | `canonical_hazard_record`, `HazardOutcome`, operator blocked 기록. 새 13개 테스트. 숫자 결과 완료를 뜻하지 않음. |
| v5 6: hazard_visible_t 실제 최초 표시 프레임 | Not implemented | 카메라/frame/flip 진단은 있으나 cargo 최초 화면 가시 검출/연결 없음. canonical 값 null 유지. |
| v5 6: 유효 brake/steer onset 및 ttc_at_response | Blocked by unresolved research parameter | 임계값/반응 기준 미확정. 기존 raw/candidate 진단만 보존. `test_p7_v5_records.py`, `test_p7_metrics.py`. |
| v5 6: min_ttc, 시작 속도/거리 | Partially implemented | DEV TTC 유효성 및 시작 상태 진단 존재. 연구용 값은 null, 최종 사건 시작/거리/충돌 경로 정의 필요. `test_p7_metrics.py`. |
| v5 6: outcome 분류 | Partially implemented | 단일 collision/departure 및 기술적 unobservable만 명시적 매핑. 회피/정지/구조/연구 관찰 미완료는 판정하지 않음. 신규 safety/terminal tests. |
| v5 6: 차량 필수 샘플 전체 | Partially implemented | 공통 telemetry에 speed/steering/brake/throttle/lane_id 존재. lane_pos/accel/headway/relative speed/TTC/명시적 주기는 위 표의 누락/제약 참조. |
| v5 6: 원시 입력과 적용 제어 구분 | Partially implemented | P7 driver_input/input_source vs telemetry get_control readback. 최종 operator hazard는 실행되지 않으며 readback은 물리 적용 증거가 아님. |
| v5 7: 미확정 설정을 임의 확정하지 않음 (SoT 12) | Implemented and offline verified | 새 수치/Blueprint/시간창/경계 없음. 기존 DEV config 불변, blocked entry 유지. 새 schema 및 blocker tests. |
| v5 7: 최종 위험사건 설정표 | Blocked by unresolved research parameter | SoT 12의 위험사건 항목 및 기존 `P7_Traffic_Hazard_Development.md`의 TBD BY PILOT. |
| v5 9: 내부 테스트/파일럿/실주행 검증 (SoT 14 V5-8) | Partially implemented | Offline 642개 통과 및 수동 fake driver 확인. CARLA/장비/첫 표시/구조/조건 동등성/파일럿은 미수행. |

개발 완료 범위는 **기존 DEV hazard의 정직한 기록, 실제 P8 마지막 단계의 기본 차단, 그리고
명시적으로 주입된 policy/backend/safety owner로 실행되는 offline P7 loop**다. 물리 위험사건
실행을 하려면 승인된 parameter table과 CARLA actor motion/observation/render visibility adapter,
TM obstacle policy 및 실제 rescue owner 검증을 먼저 완료해야 한다.
