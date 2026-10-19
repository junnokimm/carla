# CARLA V5-P5 STEP 1+2 — 관측 수정 및 Town04 읽기 전용 경로 검증

## 기준과 범위

- Branch: `research/phase-1c-automation`
- HEAD: `e1af127bb096beaecec3e12cbd2b5bf18a378785` (이전 감사와 동일)
- 최우선 설계: `CARLA_Experiment_Design_v5_Source_of_Truth.md` §5, §7.
- 기존 `.omo/` 변경·미추적 자료와 `src/error_notes.py`를 보존했다.
  후자는 `operator_execution`, `tsxw_input`, `native_tm_diagnostics`,
  `research_noa_lifecycle`에서 import하므로 결과는 현재 Working Tree 기준이다.
- STEP 1만 코드 수정. STEP 2는 실행 중인 서버의 지도 읽기 전용 조회.
- Actor 생성/삭제/이동, 차량 제어, TM 설정, world tick/settings, map load,
  장비 제어, Wheel 변경은 수행하지 않았다. 기존 CSV/Manifest/지도도 변경하지 않았다.

## 1. Changes made

### t0 없는 연구용 완료

`observe_exit()`는 안내 draft와 독립적으로 이동/경계/결과를 처리하여,
WAITING/DRAFT에서 t0 없이 EXIT 완료가 가능했다. 새 테스트로 재현했다.

수정 후 미제시 exit identity 또는 분리된 through identity는 기존 이벤트 sink에
`lc_unpresented_path_observed`로 남긴다. `outcome`, t0, 연구 이동/tL 시각은 null이다.
동일 identity가 연속 관측되면 반복 발행하지 않는다. 시간창 전 물리 관측도 보존한다.
이 관측은 `_path_outcome`을 바꾸거나 연구 기회를 소비하지 않는다.
다음 접근의 draft/실제 flip/t0 생성은 기존 자격 조건에 따라 가능하다.
창 종료 및 오류·사용자 종료의 기존 의미는 유지한다.

### 완료 이후 결과 오염

기존에는 COMPLETED 검사 전에 `_path_outcome` 변경 및 entry 이벤트 발행이 가능했다.
수정 후 COMPLETED에서는 최신 observation/frame만 갱신하고 연구 관측 전이를 종료한다.
기존 EXIT/MISSED와 entry 이벤트는 불변이다. 차량 관측·제어 루프는 계속 실행되며
다른 모듈 Coordinator에는 영향을 주지 않는다.

### 목표 차로 기준 혼용

`ResearchExitObservationSource.observe()`의 현재 차로 중심 fallback을 제거했다.
source에서 실제 우측 target을 관측하거나 현재 lane이 target일 때만 해당 중심을 쓴다.
목표 기하 결측은 lateral null / boundary-valid false다.

`mark_presented()`는 유효한 경계 기하만 baseline으로 채택한다.
`observe_exit()`는 결측/invalid 또는 road·section·폭 변경 시 비교 기준을 무효화하고,
다음 유효 관측을 새 baseline으로 삼는다. 새 기준 획득 자체는 이동/tL이 아니다.
`lc_lateral_reference_invalidated` 및 `lc_lateral_reference_acquired`로 근거를 남긴다.
road/section 변경은 연속성을 증명할 자료가 없어 보수적으로 재기준화한다.
결측 동안의 실제 최초 onset/tL을 복원하거나 성공으로 채우지 않는다.
이미 관측된 시각은 유지하며 기존 onset 임계값은 변경하지 않았다.

경로 identity에 의한 EXIT/MISSED는 횡기하 결측과 독립적으로 판정한다.
기하 결측 때문에 확인된 출구 identity까지 버리는 이전 조기 return을 제거했다.
기존 Fork에서의 MISSED 분리 조건(through identity + exit corridor 밖)은 유지했다.

### 이벤트 계약

CSV header/Writer/Reader/SessionClock 변경 없음. 기존 JSON event_value에 다음만 추가:

- `target_lateral_offset_m`
- `target_boundary_valid`
- `target_lane_width_m` (유효 경계가 아니면 null)

`lc_presented`에도 기존 observation을 실어 t0 baseline의 근거를 보존한다.
신규 evidence 이벤트의 outcome은 null이므로 기존 ledger의 최종 결과를 덮지 않는다.
새 필드가 모든 프레임의 궤적이나 정확한 물리 onset 복원을 제공하는 것은 아니다.

## 2. Files changed

| 파일 | 목적 |
|---|---|
| `src/experiment/exit_assistance.py` | 미제시 identity 별도 보관, 유효 baseline·표시 관측 기록 |
| `src/experiment/exit_assistance_observation.py` | 미제시/완료 경계, 횡기하 재기준화, 경로 관측 독립 |
| `src/experiment/exit_assistance_event.py` | 최소 횡기하 근거 3개 JSON 필드 |
| `src/scenario/research_exit_runtime.py` | 목표 차로가 없을 때 현재 차로 fallback 제거 |
| `tests/test_p5_observation_lifecycle_regression.py` | 결함 재현 및 수명/기하/CSV 결과 회귀 |
| 이 문서 | 수정·검증·STEP 3 인계 기록 |

TS-XW, 입력 mapping/정규화, TM/GENERAL 실행, NoA 전환·속도, 추천 확인/거부,
Training/M1/M2 시간, P7/P6/배정 관련 production 코드는 변경하지 않았다.

## 3. Verification results

- 최초 regression 12개: 수정 전 12 실패, 수정 후 12 통과.
- 확장 regression: 20 통과 / 0 실패.
- `test_*p5*.py`에 해당하는 22개 파일: 204 통과 / 0 실패.
- 수정 전 전체 pytest: 1,715 통과 / 8 실패 / 1 skipped.
- 수정 후 최초 전체 pytest(12개 추가 시점): 1,727 통과 / 8 실패 / 1 skipped.
- 최종 전체 pytest(20개 추가): **1,735 통과 / 8 실패 / 1 skipped** (85.24초).
  수정 전과 실패한 8개 test ID가 동일하며 새 회귀 실패는 없었다.
- 전체 Ruff check: 통과.
- 변경 Python 5개 format check: 통과.
- 전체 format check: 기존 130개 파일 포맷 불일치, 344개 이미 포맷됨.
  관련 없는 파일을 일괄 포맷하지 않았다.
- skipped 1개는 `CARLA_TEST_WINDOWS_DISPLAY=1` opt-in이 필요한 실제 Windows
  borderless/F11 화면 테스트이며, 활성화하지 않았다.

전체 pytest의 기존 실패:

1. `test_driver_view.py` 4개: cockpit transform 및 mirror 위치 기대값 불일치.
2. `test_driver_view_cockpit.py` 1개: cockpit x=0.10 기대, 실제 0.25.
3. `test_p6_candidate_aoi.py` 1개: 후보 AOI y=561, 실제 mirror y=453.
4. `test_p6_validation_loop.py` 1개: 위 기하 불일치 및 예외 처리 TypeError.
5. `test_p8_training_pygame_lifecycle.py` 1개: F8 조기 종료/복구 assert는 통과하나
   전체 실행 약 7초로 2초 한계 초과. 수정 전후 동일하게 실패했다.

20개 신규 regression은 미제시 WAITING/DRAFT(HUD OFF/no flip), 시간창 내/이전,
다음 순환, 정상 EXIT/MISSED, 완료 후 교차 재접근, 독립 event ID, 종료 오류,
target 처음부터 존재/뒤늦게 출현/소실, road/section/결측/invalid, 실제 이동과 tL,
기존 ledger 및 P8 결과 reader round-trip을 검사한다.
테스트 데이터는 임시 디렉터리에 생성하며 원자료를 사용해 성공을 합성하지 않는다.
실제 장치·주행·TM 분기 검증은 수행하지 않았다.

## 4. Town04 Route Validation

### 현재 서버와 방법

- 실행 중인 TCP 2000 listener 확인 후 `127.0.0.1:2000`에 timeout 5초로 조회.
- Client 0.9.16, Server `294096e`: API version mismatch 경고 존재.
- Map `Carla/Maps/Town04`; `get_topology()` 559 edges.
- `to_opendrive()` UTF-8 SHA-256:
  `4dddd1560ab7812b0e89cd4a523d8b6f2b93947eafb496d9eeb83cc772116c85`.
- `generate_waypoints(2)`로 road/section/lane별 위치·방향 비교.
- `next(2)` BFS로 ramp 연결과 -3 mainline 순환을 별도 확인.
  검색 station은 2m 단위로 방문 중복을 제한했다. 거리 값은 탐색 step 합이며 주행 거리 측정이 아니다.
- OpenDRIVE road/lane links도 별도 XML 파싱. 지도 객체에서 수행하는 계산이며 차량을 움직이지 않는다.

### Manifest 정합성

| 버전 | 현재 loader 결과 | 저장 hash | 현재 semantic hash |
|---|---|---|---|
| v1 | sample spacing 기준 실패 | `0ef154c7db4ca2a11f07a66ae622be6e0747fbe6e28a99e84da70d17b579469f` | `97df21c734e957b51aa87c90df3bda1c567ad822f5d9437a2ae5c556fc5d8b4b` |
| v2 | hash 불일치 | `07204d2b0b8d1d0e9069017fc85849d205f54542927c49a8b228e9cf31afc6fc` | `74a76802caec44812faca67118b7c914714a7b3825566b7204bd1f677d241332` |
| v3 | PASS | `31cce59e7a37b423af4f338674b6a7c1187145554ca1b9314428944fc05c93cc` | 저장 hash와 동일 |

이전 버전의 불일치는 현재 parser/hash 계약과의 호환성 판정이다. 과거 파일 손상으로 단정하지 않는다.
어떤 Manifest도 수정하지 않았다. 아래 값은 개발값이며 연구 승인값이 아니다.

### 출구 비교 (identity = Road/Section/Lane, station 단위 m)

| 항목 | Exit39 | Exit47 |
|---|---|---|
| Route ID | `town04-exit-39` | `town04-exit-47` |
| Version | `town04-exits-dev-v3` | 동일 |
| 단일 route semantic SHA-256 | `276f5c28579dde0d1b4336fee982a8b243e5ff260bc08e435c112cfe23a5b7a4` | `baafba3d9264eb16e6e010bde128821ef2e2c779f230f1b8cdc4318d0a8d97e8` |
| 샘플 수 | 288 | 284 |
| 시작 identity / XY | `50/0/-3`, `(64.919632,-334.347717)` | `41/0/-3`, `(-510.318817,111.686974)` |
| Source / Target | -3 / -4 | -3 / -4 |
| Navigation | 86.107673–1086.107673 | 151.671258–1151.671258 |
| Confirmation | 86.107673–985.983227 | 151.671258–1051.605042 |
| 기동 구간 | 985.983227–1061.064847 | 1051.605042–1126.671254 |
| Fork station | 1086.107673 | 1151.671258 |
| Fork 기준 XY | `(-66.684799,5.857579)` | `(15.254236,-46.968483)` |
| Exit segments | `1191/1/-2`, `33/0/+2` | `782/1/-2`, `34/0/+2` |
| Through segments | `1184/0/-3`, `1184/0/-4`, `40/0/-4` | `774/0/-3`, `774/0/-4`, `48/0/-4` |
| Manifest 끝 | `47/0/+6`, 1361.318298 | `1097/1/+2`, 1426.816221 |
| 끝 XY | `(-16.136597,-19.090786)` | `(70.799179,6.370247)` |
| 폭 | 모든 샘플 3.5m | 모든 샘플 3.5m |
| 지도 identity 누락 | 0 | 0 |
| 동일 identity 최근접 2m 표본 거리 최대 | 1.975715m | 1.915144m |
| yaw 차이 최대 | 0.018373rad | 0.015369rad |
| 안내/기동 위치 동방향 Driving lane | 4개 | 4개 |
| 연결성 | TOPOLOGY_VERIFIED | TOPOLOGY_VERIFIED |
| 실제 수동/TM/재합류 | PHYSICAL_UNVERIFIED | PHYSICAL_UNVERIFIED |
| v5 적합성 | 3차로 조건 연구자 판단 필요 | 동일 |

진입 접근 관측 corridor는 기존 최대 폭×1.5=5.25m이고, spawn 검증은 반폭=1.75m 및
45도 이내 방향/identity/source lane/confirmation 종료 이전을 요구한다.
지도상 허용 spawn 예: Exit39 33·116, Exit47 1·107 (조회한 전체 후보는 각각 17개·11개).
이는 실제 spawn 성공/차량 충돌 여부/연구 배정이 아니다. 새 spawn을 선택하지 않았다.

각 경로의 기동 source 샘플 8개에서 immediate right -4 / Driving / 동방향 /
Right lane-change permission을 확인했다. 기존 footprint guard는
`2 * bounding_box.extent.y < min(source_width,target_width)`만 검사한다.
실제 운영 차량 bbox·길이·회전 swept footprint·동적 추종은 이번 검증에 포함되지 않는다.

### Case A: EXIT

OpenDRIVE road/lane links와 forward Waypoint 탐색에서 확인한 경로:

```text
39/0/-4 → 1191/0/-4 → 1191/1/-2 → 33/0/+2
        → 779/1/+2 → 779/0/+6 → 47/0/+6

47/0/-4 → 782/0/-4 → 782/1/-2 → 34/0/+2
        → 1097/1/+2 → 1097/0/+4 → 39/0/-4
```

Ramp section1 표본부터 반환 road까지 2m 탐색 합은 각각 268m/302m.
최대 인접 표본 chord는 2.000935m/2.001305m, heading 변화는 2.138962도/1.940918도였다.
이 표본 경로에서 큰 위치 단절이나 역방향 전환은 관측하지 않았다.
이는 차량의 곡률 추종·충돌·승차감 검증이 아니다.

Manifest 끝에서 `next(100)`도 존재한다. Exit39는 1073/0/+6 또는 1076/0/+2로 분기하며,
Exit47은 39/0/-4로 이어진다. 이후 경로는 Manifest가 완전히 정하지 않는다.
복귀 road 접근은 확인했지만 원래 -3 차로의 승인된 재합류 완료를 뜻하지 않는다.

Fork 직전 표본의 `next(5)`는 각각 through 1184와 exit 1191, through 774와 exit 782를
모두 반환했다. 따라서 단순 lane -4 도달은 실제 출구 선택과 다르다.

### Case B: MISSED

원래 source -3을 유지하는 방향성 순환을 `next(2)`로 확인했다:

```text
39 → 1184 → 40 → 1401 → 41 → 6 → 45 → 145 → 46 → 1072 → 47
→ 774 → 48 → 901 → 49 → 1173 → 50 → 42 → 267 → 43 → 35
→ 861 → 36 → 760 → 37 → 1602 → 38 → 1091 → 39
```

모두 section0/lane-3. 1184에서 39까지 약2918m, 774에서 47까지 약2938m의 step 탐색이다.
한 바퀴 실제 거리/시간 또는 TM 선택 보장이 아니다.
lane -4에서 제한 없는 최단 검색을 하면 다른 ramp·도심 road를 통과하는 경로도 나오므로,
MISSED 자체가 원래 고속도로 차로 유지까지 보장하지는 않는다.
STEP 1 수정으로 완료한 연구 기회의 재접근이 entry/최종 결과를 다시 만들지는 않는다.

### Case C: M2와 TM

P5 완료는 Runner 종료 조건이 아니며 600/780초를 유지한다.
하지만 남은 M2를 어느 road/차로에서 주행하고 마지막 위험사건의 접근 위치·시간·모드를
충족하는지는 승인된 후속 경로와 P7 계약이 필요하다. 현재 topology만으로 판정할 수 없다.

P8 NoA 경로 선택은 TM이 맡고, P5 확인은 기존 one-shot `force_lane_change`를 요청한다.
`src`에는 `set_path`/`set_route` 호출이 없다. Manifest는 관측·검증 기준이며
P8이 그 polyline을 TM의 전체 출구/재합류 경로로 설정하지 않는다.
수동에서는 운전자 조향이 경로를 결정한다. TM 코드와 기존 입력 동작은 수정하지 않았다.

### 3차로 요구조건

Exit39 source 샘플 road 50/42/43/35/36/37/38/39 등은 동방향 4개이며,
일부 연결 road 267/861/760의 해당 section은 3개다.
Exit47 source 샘플 road 41/6/45/145/46/1072/47은 모두 4개다.
안내 시점부터 이미 -1/-2/-3/-4가 존재한다. 마지막 짧은 ramp만 4차로라고 볼 수 없다.
참가자 화면의 실제 가시성은 렌더링으로 검증하지 않았다.
현재 지도 유지와 보조차로의 연구상 허용 범위는 연구자가 결정해야 한다.

## 5. Remaining concerns

- 실제 TS-XW 진출/연결로 주행/재합류, TM 분기 모두 UNVERIFIED.
- 고속도로 road 복귀와 원래 차로 재합류는 별개. 자동 재합류 구현 필요 여부는
  최종 후속 경로·수동/NoA 역할 확정 후 결정해야 한다.
- 3차로 적합성, 최종 route/spawn, M1 후반/M2 중후반 window 및 위험사건 전 여유 미확정.
- geometry 단절 구간의 실제 최초 onset/tL은 재기준화만으로 복원할 수 없다.
- 현재 서버 버전 경고가 있는 지도 조회 결과이며 실제 운영 native 환경 attestation을 대체하지 않는다.
- 기존 전체 pytest 8 실패와 포맷 130개는 이번 범위 외 미해결 항목이다.
- 내비게이션과 M2 handoff 시 P5 종료 경계의 기존 제한은 이번에 변경하지 않았다.

## 6. STEP 3 Handoff

- 현재 안내: P8 설정→Runner→view binding→HUD draw→성공한 display flip→t0.
- 수동은 일반 안내, NoA는 기존 추천/X 확인/R 거부. t0에 실제 모드를 고정한다.
- 안내에는 eligibility + route match + source lane -3 + navigation + confirmation 구간이 필요하다.
  다른 차로/늦은 접근/창 종료/HUD OFF면 표시가 누락될 수 있다.
- 시간창이 지점 통과 뒤 열리면 약1km 안내 위치의 일관성을 보장하지 않는다.
- 미표시 물리 관측은 이제 다음 기회를 소비하지 않지만 창/위치 정책은 그대로다.
- 투영 실패의 station=-1을 이미 표시된 거리 계산에 사용하는 문제는 그대로다.
- 일반 수동 안내 자격과 기존 NoA 확인 자격을 분리할지 연구자 확정 필요.
- 위 표의 navigation 전체 범위가 실제 최초 표시 가능 범위는 아니다.
  현재 confirmation 종료(Exit39 985.983227 / Exit47 1051.605042)에서 더 일찍 제한된다.
- 다음 후보: `exit_assistance_policy.py`, `exit_assistance_observation.py`,
  `research_exit_view.py`, `research_exit_hud.py`, 필요 시 runtime/config validation.
- 테스트: 차로별 동일 위치, 창 전후 재접근, HUD OFF/ON, draw/flip 실패,
  route loss/recovery, 수동/NoA 추천 자격 분리, t0 단일성.
- STEP 3 코드는 작성하지 않았다.

## 7. 최종 작업 판정

| 항목 | 판정 |
|---|---|
| STEP 1 세 오류 수정 | IMPLEMENTED |
| STEP 1 P5 Offline 회귀 | PASS (204) |
| STEP 2 v3 Manifest | VERIFIED |
| v1/v2 현재 loader 호환성 | FAIL, 원본 보존 |
| 현재 Town04 지도 연결성 | TOPOLOGY_VERIFIED, 서버 버전 경고 명시 |
| 실제 차량 진출 | UNVERIFIED |
| 실제 재합류 | UNVERIFIED |
| P5 전체 실험 운영 준비 | BLOCKED — 경로/3차로/안내 정책 및 물리 검증 필요 |
