# CARLA V5-P5 STEP 3 — 내비게이션 및 P8 Offline 통합 검증

## 개발 기준

- Branch `research/phase-1c-automation`, HEAD `e1af127bb096beaecec3e12cbd2b5bf18a378785`.
- 시작 시 STEP 1+2의 production 4개 수정과 미추적 테스트/보고서가 존재했다.
  이를 포함한 현재 Working Tree 기준으로 개발했다.
- 최우선 문서: `CARLA_Experiment_Design_v5_Source_of_Truth.md` §7.
- 이전 근거: `P5_Step1_2_Observation_Route_Validation_20261015.md`.
- 기존 `.omo`, `src/error_notes.py`, 진단 로그, CSV, Session/Route Manifest를 보존했다.
- 이번 작업에서는 CARLA RPC/실주행/Actor/장비/TM/World 조작을 수행하지 않았다.

## 1. Changes made

### 일반 안내와 추천/실행 자격

기존 `observe_exit()`는 navigation과 confirmation 조건을 동시에 요구했고,
`mark_presented()`도 confirmation 구간을 요구했다. 따라서 v3 Manifest에 이미
명시된 navigation 범위 중 confirmation 종료 이후 부분은 일반 안내도 나오지 않았다.

수정 후:

- 일반 안내: 기존 eligibility window, route match, source lane, navigation envelope,
  trigger distance를 사용한다. 접근 차로를 -1/-2/-4로 확대하지 않았다.
- 추천: 실제 모듈/NoA 상태와 기존 confirmation envelope를 추가로 요구한다.
- 실행: t0에 실제 추천을 제시한 기회만 확인 가능하다. 기존 입력 edge, 모드,
  수동 우선권, lateral onset, route 및 TM 제출 guard를 유지한다.
- NoA 상태라도 confirmation 밖에서 최초 안내했다면 일반 안내만 표시한다.
  미제시 추천의 NORESPONSE나 자동 제출을 만들지 않는다.
  나중에 그 기회에 새로운 추천을 임의로 추가하지 않는다.
- 실제 이동 onset이 관측되어 기존 실행 guard가 더 이상 확인을 허용하지 않는
  경우 추천 조작 UI도 숨긴다. 일반 안내는 유효한 거리가 있으면 유지한다.

변경 함수: `is_navigation_observation`, `observe_exit`, `prepare_presentation`,
`mark_presented`, `update`.

기존 `test_actual_manifest_rejects_first_confirmation_after_blend_starts`는
일반 안내까지 WAITING이어야 한다는 기대를 일반 안내 허용/추천 및 실행 차단으로
갱신했다. 실제 v3를 사용하며 자동 실행 차단 assert는 유지·강화했다.

### 위치·시간 및 t0 기록

기존 시간창 수치·CLI 검증·활성 주행 원점·600/780초는 변경하지 않았다.
늦은 진입을 다음 바퀴로 강제 이월하거나 임의 오차 범위를 만들지도 않았다.

기존 event_value JSON에 다음 증거를 추가했다:

- `navigation_distance_to_exit_m`: 유효한 관측의 fork station까지 참조 경로 거리.
- `navigation_start_offset_m`: Manifest navigation 시작 station 대비 현재 station 차이.
- `recommendation_at_t0`: 실제 t0 화면의 추천 표시 여부; t0 이전은 null.
- `route_matched`: 해당 이벤트의 관측 match 여부.

`lc_presented`의 기존 observation과 함께 기록되어 늦은 안내와 추천 미표시를
정상적인 약1km 추천으로 오인하지 않도록 한다. offset 양수만으로 시간창이 원인이라고
단정할 수는 없다. 늦은 차로 진입/표시 지연 등도 가능하다.
허용오차·연구 적합성은 이 값으로 자동 승인하지 않는다.

t0는 기존 성공한 display flip 직후 host 시각이다. 광출력/인지 시각이 아니다.
receipt/decision commit 및 기존 SessionClock·Writer·Queue를 그대로 사용한다.

### 잘못된 거리 표시

기존에는 미관측 station=-1을 빼서 1201m 같은 거리를 만들고, 비유한 값도 표시할 수 있었다.
`navigation_distance_m()`에서 route match, finite, 0 이상 및 fork 이전/동일 station을
검사한다. 관측 불가 또는 이미 fork 뒤라면 미래 출구까지의 숫자 안내를 숨긴다.
sentinel을 0 또는 양수로 바꾸지 않는다.

표시가 사라져도 연구 기회를 완료/재시작하지 않는다. 유효 관측 복구 시 안내를 재생성하며
기존 t0를 유지한다. `lc_navigation_unavailable` / `lc_navigation_restored`로 표시 가능한
관측의 전환을 기존 sink에 남긴다. 이 이벤트의 outcome은 null이다.
비유한 station은 JSON의 `route_progress_m`에서도 null로 남기며 -1 sentinel은 보존한다.
station=inf를 완료 station으로 취급하지 않도록 완료 관측에도 finite 검사를 추가했다.

### HUD 및 오래된 입력

기존 view controller는 마지막 flip에서 활성화된 추천을 다음 입력까지 그대로 보관했다.
수정 후 `requests()`에서 현재 binding의 추천 유효성을 재검사한다.
종료/완료/모드 전환/위치 이탈/투영 실패/이동 onset으로 무효화된 추천의 오래된 입력을
확인·거부로 전달하지 않는다. 기존 KEYDOWN/KEYUP/repeat 및 X OFF→ON 처리는 유지한다.

`mark_after_flip()`도 이미 제시된 기회의 오래된 snapshot으로 추천을 재활성화하지 않는다.
최초 flip의 snapshot 검증과 t0 기록은 기존 Coordinator 경로를 유지한다.
`finish()`는 presentation을 정리하고 종료된 기회의 UI/입력 재생성을 막는다.
이는 차량 제어 루프의 종료 조건이 아니다.

확인/거부/무응답 및 운전 중 모드 중단 후에는 기존처럼 기본 안내를 유지할 수 있다.
다만 거리 관측이 유효해야 한다. 새 화면 layout, animation, 조작 버튼은 추가하지 않았다.

## 2. Files changed

이번 STEP 3 변경:

| 파일 | 목적 |
|---|---|
| `src/experiment/exit_assistance_policy.py` | 거리 유효성 및 일반 안내 자격 |
| `src/experiment/exit_assistance.py` | 기본 안내/추천 구분, 추천 표시 증거, 종료 UI 경계 |
| `src/experiment/exit_assistance_observation.py` | draft 자격 분리, 거리 숨김/복구, 비유한 완료 차단 |
| `src/experiment/exit_assistance_event.py` | 실제 안내 거리/위치/추천/관측 상태 근거 |
| `src/scenario/research_exit_view.py` | 오래된 추천 입력 및 flip snapshot 차단 |
| `tests/test_p5_navigation_contract.py` | v3의 일반 안내와 확인 차단 계약 검증 |
| `tests/test_p5_navigation_step3.py` | 신규 regression 28개 |
| 이 문서 | 결과와 실제 주행 인계 |

STEP 1+2의 목표 기하 수정은 `research_exit_runtime.py`에 그대로 남아 있으며 이번에
수정하지 않았다. `test_p5_observation_lifecycle_regression.py`도 변경 없이 실행했다.
미제시 물리 관측/결과 불변/기하 결측 이벤트와 필드를 유지한다.

P8 운영 구조, TS-XW/키보드 mapping, TM/GENERAL, NoA 속도·활성화·해제,
600/600/780초, F8, P6/P7/배정, Route Manifest는 변경하지 않았다.

## 3. Verification results

- 변경 전 전체 pytest 재확인: **1,735 passed / 8 failed / 1 skipped**.
- 최종 전체 pytest: **1,763 passed / 8 failed / 1 skipped** (86.30초).
  실패한 test ID 8개는 변경 전과 동일하며 신규 실패는 없다.
- 최초 신규 검사 16개는 수정 전 실패했다. 이 중 공통 모드/늦은 시점 검사는
  신규 증거 필드 부재를 검출한 것이며 기존 모드 분기 자체의 실패는 아니다.
- 최종 신규 regression 28개를 포함한 P5 관련 23개 파일: **232 passed / 0 failed**.
- 지정 우선 테스트 + 신규 테스트 + DriverView flip 테스트 + P8 operator backend:
  **122 passed / 0 failed**.
- STEP 1 회귀 20개는 위 실행에 포함되어 통과했다.
- 전체 Ruff lint PASS, 변경 Python 7개 format check PASS.
- 전체 format은 요구대로 실행/수정하지 않았다.
- 최종 `git diff --check` 통과. 보호 경로 `config`, `data`, `maps`, `tools`,
  `src/p8`, `src/p6`, `src/p7`의 tracked diff는 없다.
- 변경은 미커밋·미푸시 상태다.

기존 전체 pytest 실패 목록:

- `test_driver_view.py`: cockpit transform/미러 배치 관련 4개.
- `test_driver_view_cockpit.py`: cockpit x 기대값 1개.
- `test_p6_candidate_aoi.py`: 실제 미러와 후보 AOI 차이 1개.
- `test_p6_validation_loop.py`: 기하 불일치와 예외 처리 1개.
- `test_p8_training_pygame_lifecycle.py`: F8 기능 assert 이후 약7초 실행이 2초 한계 초과.
- skipped: opt-in Windows borderless/F11 화면 테스트.

P8 연결은 `operator_run_config → Runner → Coordinator/ViewBinding`이며,
`test_p8_operator_backend.py`가 실제 Python 운영 클래스와 Fake CARLA로
훈련/M1/M2별 ID, M1 자유 선택, M2 배정 상태, EXIT/MISSED, 오류/종료, 시간 보존을 검사한다.
`test_p5_real_driver_view_binding.py` 및 `test_p5_driver_view_binding.py`는
실제 view 연결, HUD OFF, 성공/실패 flip, 입력 edge, t0 단일성을 검사한다.
`test_p5_tm_execution.py`는 P5 확인의 단일 TM 요청 및 GENERAL 중복 방지를 검사한다.
이것들은 실제 장비·차량·물리 TM 검증이 아니다.

## 4. Navigation Behavior Matrix

모듈 행은 유효한 시간·source lane·위치·HUD/flip을 전제로 한다.
‘확인’은 P5 확인이며 GENERAL Z/X 요청을 의미하지 않는다.

| 조건 | 기본 안내 | 추천 | P5 확인 | 결과 |
|---|---|---|---|---|
| M1 MANUAL | 표시 | 없음 | 불가 | 수동 진출 관측 |
| M1 NOA_ACTIVE | 표시 | 기존 confirmation 자격일 때 | 표시된 추천 + 기존 guard | 확인 시만 기존 제출 |
| M2 MANUAL | 표시 | 없음 | 불가 | 수동 진출 관측 |
| M2 NOA_L2 | 표시 | 실제 NoA 활성 + 기존 자격일 때 | 표시된 추천 + 기존 guard | 확인 시만 기존 제출 |
| Time Window 이전 | 최초 안내 없음 | 없음 | 불가 | WAITING, 다음 접근 가능 |
| Window 종료까지 미표시 | 없음 | 없음 | 불가 | NOT_PRESENTED |
| Window 이후, 이미 표시한 기회 | 거리 유효 시 기존 안내 | 기존 응답/모드/위치 상태에 따름 | 기존 guard | 새로운 t0 없음 |
| 최초 안내가 confirmation 밖 | 표시 가능 | 없음 | 불가 | 추천/무응답 성공을 만들지 않음 |
| Route Projection 실패 | 숫자 안내 숨김 | 없음 | 불가 | outcome 변경 없이 복구 대기 |
| EXIT/MISSED 이후 | 없음 | 없음 | 불가 | 결과 불변, 차량 주행 계속 |
| explicit finish/모듈 종료 | 없음 | 없음 | 불가 | 종료 기회 재활성화 안 함 |

NoA 조건이어도 실제 수동이면 추천하지 않는다. t0 이후 모드 변경은 기존 중단 의미를 따른다.
추천이 유효하지 않은 X는 기존 GENERAL 방향지시등 처리의 대상일 수 있다.
이번 수정은 GENERAL 기능을 차단하거나 새 자격을 부여하지 않는다.

## 5. Remaining concerns

- ‘최대 1회’는 보존하지만 ‘반드시 1회’는 보장하지 않는다.
  시간창 동안 유효한 접근/차로/HUD/flip이 없으면 미제시다.
- 시간창이 늦게 열리면 Manifest 범위 안에서 가까운 거리의 안내가 가능하다.
  다음 바퀴까지 기다릴지/늦은 안내를 연구상 허용할지/허용 오차는 연구자 결정이다.
- v3 navigation 시작은 Exit39 86.107673m, Exit47 151.671258m이며 각 fork의 1000m 전이다.
  navigation 종료는 각각 1086.107673/1151.671258m,
  confirmation 종료는 985.983227/1051.605042m다.
- source lane 범위는 기존 -3이다. 다른 차로의 최초 안내 확대는 미확정이다.
- 거리 값은 기존 Manifest에 투영한 참조 거리다. 실제 운전 경로·패널 광출력·인지 시각의
  정밀 ground truth가 아니다. 기본 문구는 기존 소수점 1자리 반올림을 유지한다.
- Route/Spawn/window와 위험사건 전 여유시간은 승인되지 않았다.
- STEP 2에서 안내/기동 위치의 동방향 Driving lane 4개를 확인했다.
  v5 3차로 적합성은 연구자 판단이 필요하며 이번에는 지도를 재조회하지 않았다.
- 실제 수동 EXIT/MISSED·연결로·재합류·TM 분기·자동 변경 모두 UNVERIFIED.
- P7 실행 및 후속 접근 조건, M2 handoff의 기존 미완료 항목은 이번에 수정하지 않았다.
- 전체 실험 운영 준비를 선언하지 않는다.

## 6. 다음 실제 P8 주행 검증 계획 (이번에는 실행하지 않음)

### 환경·설정 준비

기존 문서 `P8_Patched_TM_LaneChange.md`와 runtime attestation 코드를 확인했다.

- 기존 운영용 Python: `C:\dev\carla-native-tm-cancel-20261012\.venv-p8\Scripts\python.exe`.
- 해당 환경의 현재 libcarla 파일 SHA-256을 읽기 전용 확인:
  `636D249A6898FEB4A6A8D166917DDDE04F26B70DAB9E95C1D3D50E9ACF3CA471` — 승인값 일치.
- 테스트용 로컬 `.venv`의 파일은
  `81E9AC361D989198E208C525D4F7C94895277E6532224FBD8EA4DBB5C36B1AD2`로 다르다.
- 이는 파일 확인이지 실행 중 loaded module/TM listener의 attestation은 아니다.
- 실제 실행은 기존 loaded extension hash, TM port 단독 PID 소유권,
  activation generation 및 EMPTY_REHEARSAL_ONLY gate를 통과해야 한다.
- 환경 교체·검사 우회는 수행하지 않았다.

연구자가 route/spawn/시간창/미확정 입력 설정을 확정하기 전에는 수치를 채운 실행 명령을
제시하지 않는다. 실제 실행 시에도 동일 `python -m src.p8.operator_cli`를 사용하며
Training 최대600초(F8 기존 동작), M1 600초, M2 780초를 유지한다.

### 사용자가 수행할 관측

1. 별도 run ID의 정상 길이 실행들에서 M1 안내 시 MANUAL/NOA_ACTIVE를 각각 확인한다.
   t0 상태, 기본 문구·거리·위치, 추천 유무를 대조한다.
2. M2 MANUAL/NOA_L2 배정별로 실제 제어 상태와 안내·추천을 확인한다.
   각 모듈 event ID가 다르고 `lc_presented`가 최대1개인지 확인한다.
3. NoA 추천의 X OFF→ON 확인, R 거부, 무응답을 각각 확인한다.
   제출 기록은 물리 차로변경/출구 성공과 별도로 판단한다.
4. 수동 실제 EXIT와 MISSED를 각각 운전하여 결과 identity와 tL/onset의 근거를 확인한다.
5. 진출 또는 통과 뒤에도 남은 모듈 주행이 지속되는지 확인한다.
   반복 접근이 기존 완료 결과나 entry를 덮어쓰지 않는지 검사한다.
6. HUD OFF/표시 실패 시 t0가 없는지, 투영 상실/복구 시 확인되지 않은 거리가 표시되지
   않는지 확인한다. 모드 변경·거부·무응답·종료 후 오래된 추천 입력이 동작하지 않아야 한다.
7. 종료 후 원본 CSV/Session Manifest를 보존하고 route version/hash, t0, t0 모드,
   recommendation_at_t0, 위치/거리, input receipt/decision commit, onset/tL,
   EXIT/MISSED, 종료/오류를 함께 검사한다. null을 성공 또는 0으로 바꾸지 않는다.

P7 실제 위험사건이나 재합류 신규 제어를 이 검증에 임의로 추가하지 않는다.
