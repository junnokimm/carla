# P8 / P7 v5 Hazard 개발 및 검증 기록

## 2026-10-17 — 사용자 지정 M2 내부 랜덤 위험사건 v1

이 절이 현재 **opt-in 개발 실행**의 기준이다. 아래 10월 16일 이전의 780초 후 별도
FINAL_HAZARD, 물리 Backend 미연결, CLI 옵션 없음 설명은 결정 이력/legacy Offline 경로다.
이번 변경은 사용자가 지정한 v1 변경이며 교수님 최종 승인·본실험 승인이 아니다.

### Changes made / 완료 경계

| 구분 | 상태 |
|---|---|
| 1. 실제 Backend 및 단일 P8 연결 | 구현. `--m2-hazard-v1`로 선택하는 개발 경로 |
| 2. Offline 검증 | 실제 구현을 Fake CARLA·가상 Clock으로 실행. 결과는 아래 검증 절 |
| 3. 사용자 실주행 검증 | **미수행** |
| 4. Tobii 포함 데이터 수집 검증 | **미수행** |
| 5. 파일럿 투입 준비 | **미완료**. 기하 proxy·물리 튜닝·데이터/운영 검증 필요 |

- 개발 기준: 현재 로컬 `research/phase-1c-automation`, HEAD
  `58aa51689c2497285e2f4365f484fd539e5131b6`와 시작 당시 미커밋 변경.
  기존 P7 policy/display/hazard/metrics, 카메라/DriverView 및 문서 변경을 보존했다.
  `.omo`, `src/error_notes.py`, 원본 CSV/JSON/Manifest는 편집하지 않았다.
- **사용자 확정:** M2 활성 시작=0, 전용 RNG 균등 `[400,600]`에서 예정 노출 시각 1회 추첨,
  Training/M1 제외, 반응/장면 이후에도 M2 780초 유지, 종료 뒤 재실행 없음.
  `static.prop.box01` 기본 크기, 최초 부분 가시 목표 TTC 약4초(파일럿 출발점), 원시 입력 보존.
  제동/조향 연구 임계값·유효 반응·회피 성공 판정은 미정이다.
- 기존 `OccurrenceRegistry`, `OwnedActors`, `smooth_lane_change`, projected bounds,
  canonical 미검증 필드, 공통 logger/clock, 입력/표시 경계를 재사용한다.
  5초 개발 종료/반응 threshold가 결합된 legacy `HazardScenario` 대신 v1 내부 장면 상태를
  `OperatorM2Hazard`가 관리한다. legacy Fake plan과 v1 설정은 동시에 선택할 수 없다.
- 실행 연결:

  ```text
  operator_cli --m2-hazard-v1
    → OperatorRuntimeConfig.m2_hazard_v1
    → OperatorRunExecutor (M2 명목 실행만)
    → ResearchNoASession.active_scene_factory (활성 시작)
    → 동일 DriverView 입력/제어/표시 loop의 ActiveDriveScene hook
    → OperatorM2Hazard → P7 MovingHazardBackend
    → 780초/사용자 종료/오류에서 기존 session shutdown + 소유 Actor cleanup
  ```

M2 내부에서 stage/segment/clock/viewer/ego/logger를 교체하지 않는다. 장면이 끝나도
hook은 M2 종료까지 입력을 기록한다. 운전자 제동으로 정지해도 시간은 기존 정책대로 흐른다.
별도 pause 기능은 없다. v1 viewer의 deadline도 동일 active origin을 사용한다.
M2 반환 후 UI의 FINAL_HAZARD는 **저장 결과/검증 미완료를 처리하는 행정 경계**일 뿐,
Actor 생성·주행 continuation을 실행하지 않는다. `require_offline_hazard_runner`는 기존
Fake 전용 plan에 유지하고 실제 client/viewer/input provenance를 위장하지 않는다.
Native TM attestation/소유권 검사와 participant readiness 차단도 유지한다.

### 준비·물리 동작·시간창

1. 예정 시각 약6초 전부터 현재 Ego 위치의 Driving waypoint를 따라 전방 경로를 탐색한다.
   `Waypoint.next()` 연결, 같은 진행 방향의 인접 차로와 연결, 차로변경 허용, junction/fork,
   곡률, 기존 차량 점유를 검사한다. Town04 한 좌표나 static-smoke corridor를 복사하지 않는다.
2. P5/일반 차로변경 in-flight 또는 P5 비종료 상태에서는 준비를 대기한다.
   EXIT/MISSED는 기존 coordinator의 COMPLETED로 끝난 뒤 **현재 도로에서 새 경로를 탐색**한다.
   NOT_PRESENTED/INTERRUPTED도 실제 maneuver 종료 후 검토한다. 경로를 강제 재합류시키지 않는다.
   실패는 `P5_CONFLICT`, `LANE_UNSUITABLE`, `SPAWN_CORRIDOR_OCCUPIED` 등으로 기록하고
   같은 예정 시각/seed를 유지하여 1초 간격으로 재검토한다.
3. 준비 가능 시 기존 발생 저장소를 먼저 예약한다. 선행차를 물리 차량으로 생성하고
   **선행차만** 최초 속도를 Ego 관측 속도에 맞춘다. 이후 snapshot 기반 속도/간격 feedback과
   물리 `apply_control`로 추종한다. 차량의 매-frame `set_transform`이나 Ego 속도 변경은 없다.
4. 선행차 뒤의 고정 전방 위치에 기본 크기의 box01을 배치한다. 생성 전 보수적 bbox 가림을
   검사하고 실제 bbox를 획득한 뒤 초기 지면 정렬 1회만 수행한다. 이후 prop 위치/회전/속도를
   관측하고 이동하면 실패로 종료한다. prop만 정지 physics 설정을 사용하며 물리 collision을
   끄지 않는다. bbox 가림은 mesh/픽셀 가림 보장이 아니다.
5. 준비 속도로 예상 노출 위치를 계산하고, 기존 quintic 측면 이동 profile의 **부분 노출 시작**을
   60개 분할로 예측하여 그 지연만큼 이동 명령을 앞당긴다. `not contains`와 depth/FOV 조건을
   사용하며 `fully disjoint`를 최초 가시로 쓰지 않는다. 명령 요청, 첫 apply_control 반환,
   실제 차로 도달 관측, 부분/완전 노출, 통과를 별도 기록한다.
6. 미준비 상태에서 600초가 지나면 NOT_OCCURRED. 기본 준비 여유6초를 확보하지 못하면
   INSUFFICIENT_PREPARATION_TIME_IN_WINDOW다. 준비 후 callback 지연으로 늦어진 명령은
   억제하고 실패 종료한다. 400초 전/600초 후/이동 명령 전의 기하·표시 노출 후보도 정상 발생으로
   승격하지 않고 INVALID_EXPOSURE와 원자료를 보존한다. spawn 실패는 재예약하지 않는다.
7. 적재물 통과는 성공 판정이 아니다. 통과 후 현재 진행 방향 뒤80m 이상에서 적재물을 정리하여
   이후 같은 적재물을 다시 만나는 것을 방지한다. 선행차는 이때 또는 준비한 local corridor 끝에
   접근했을 때 자기 Actor의 TM 주행으로 이관한다(끝점을 향해 되돌아가는 제어 방지).
   M2 종료에 정리하며 Ego/카메라/외부 차량은 이 owner가 삭제하지 않는다.
   뒤80m는 가시성의 거리 proxy이므로 미러에서 실제 삭제가 보이는지, 통과 직후 역주행/회차로
   재노출될 여지가 있는지는 실주행 검증 대상이다. 삭제로 운전자 성공을 만들지 않는다.
8. Ego collision sensor/기존 lane snapshot 기반 이탈 proxy에서 예상 밖 실패를 기록하고
   기존 session 오류 종료·종료 제동으로 연결한다. 새 정교한 구조 모델은 없다.
   ESC/창 닫기/장치 오류와 종료 직전 대기 collision도 부분 자료로 보존한다.

### TTC·기술 튜닝·NoA 제한

- 목표 전방 여유거리=`양의 접근속도 × 4초`. 배치 계획은 Ego 전면 offset과 보수적 cargo
  전면 여유를 포함한다. 실제 후보는 연결 경로에 투영한 Ego/cargo bbox vertex의 전면 여유거리와
  경로 접선 접근속도로 계산한다. 정지/후진/다른 차로·heading 불일치/비양의 여유거리는
  TTC=null과 사유를 남긴다. 곡선 경로를 단순 Euclidean 거리/속도로 치환하지 않는다.
- 고정 배치 뒤 Ego 가감속으로 4초에서 벗어나도 prop이나 Ego를 이동시켜 맞추지 않는다.
  target=4, 예정/조정 목표/명령/관측 시간, 실제 clearance/closing speed/TTC/pose를 분리한다.
- `src/p7/moving_config.py`의 **기술 튜닝 후보**:
  준비6s(생성/가림 확보 여유), 이동3s(기존 개발 profile 재사용), 선행 간격 max(12m, Ego속도×1.5s),
  경로 sample4m, 재검토1s, 삭제80m, speed/gap/steering gain 0.35/0.15/1.5,
  steering slew 0.5/s. speed/gap feedback은 안정 추종 검증이 아니다.
  보조 기하 guard: heading spread8°, 초기 heading15°, 최소 접근0.5m/s,
  선행/cargo 간격8m, 실제 측면 도달 오차0.35m, 초기 prop 외접 envelope 1m 반크기.
  모두 구현/기하 튜닝이며 연구 반응 임계값이나 새 실험 조건 승인이 아니다.
- 선행차 blueprint는 기존 `vehicle.mercedes.coupe_2020`을 재사용한다. box01 크기를 변경하지 않는다.
- Ego–새 소유 cargo **한 pair**에만 `TM.collision_detection(..., False)`를 설정하고 기존
  기본 True를 복원한다. 운전자의 명시적 NoA 재활성화에도 해당 pair 설정만 재적용한다.
  평상시 차로/속도 지원, 선행차 추종, 운전자 override는 기존 Runtime 소유다.
  모든 차량 ignore 설정, 물리 collision 비활성화, 강제 NoA 해제/재활성화는 없다.
- **한계:** static prop은 TM vehicle/walker collision pipeline에 포함되지 않을 수 있다.
  pair API 반환은 자동 회피 제동 금지의 실증이 아니다. Lead 추종으로 사전 감속하거나
  Native TM이 다른 이유로 제동할 가능성도 남는다. raw 입력과 control readback/speed를 비교해야 한다.
  이 조정으로도 cargo 자동 제동이 남는다면 native의 해당 Actor 범위 정책 검토가 추가로 필요하다.
  현 단계에서 NOA_L2 실험 동작이 검증되었다고 주장하지 않는다.

### 데이터 위치·시각·성능

`data/p8_operator/<Run ID>/research_events_<Run ID>.csv`의 `m2_hazard_*` 이벤트:
scheduled / camera_basis / preparation_deferred / preparation_started / actor_spawn /
cargo_placed / actors_prepared / exposure_plan / lead_move_command / lead_control_delivery /
geometric_exposure / display_candidate / lead_move_observed / cargo_passed / cargo_retired /
tm_pair / lead_route_handoff / unexpected_failure / not_occurred / raw_samples / end.

- 공통 participant/run/module/phase/SessionClock 연결을 유지한다. 원시 DriverInput과 기존 polling
  시작/완료 monotonic_ns, 관측 host 시각·CARLA frame/sim time, 실제 모드, 속도/control readback,
  장면 상태 및 관측 geometry를 M2 전 구간에서 보존한다. 입력시각을 persistence 시각으로 덮지 않는다.
  polling bounds는 USB 장치 자체 acquisition timestamp의 확정값이 아니다.
- raw samples는 메모리에 모아 1초 또는120개마다 저장하고 종료 때 잔여분을 flush한다.
  각 row의 원래 시각을 유지한다. 기존 차량 telemetry 파일도 M2 종료까지 계속된다.
- world callback의 256-frame bounded history와 **선택된 실제 Front image.transform/frame**을
  결합하고 성공 display flip 뒤에만 표시 후보를 기록한다. frame 불일치는 최신 geometry로
  대체하지 않는다. camera receipt perf_counter, snapshot callback 시각, flip 완료 host 시각을
  별도 보존한다. bitmap/광학 표시/참가자 인지 판정이 아닌 bbox proxy이며 불확실성을 명시한다.
- 최종 record는 `session_manifest.json`의 component `hazard_observation`에도 들어간다.
  canonical `hazard_visible_t`, 유효 반응시간/회피 결과는 미검증 null을 유지하지만 실제 후보/원자료는
  저장한다. 주의배분 분석 구간은 정하지 않는다. Tobii/AOI/태블릿 자동 연결은 추가하지 않았다.
- 추가 비용: M2 시작 collision sensor 1개와 world callback 1개; 준비 시 카메라/Actor metadata 조회,
  경로 sample 및 inventory 조회는 준비 attempt당 최대1회(1초 cadence), 생성은1회.
  활성 장면에는 기존 `ResearchSnapshotSample.world_snapshot`을 재사용하고 소유 Actor 3개의
  snapshot lookup/기하 계산, 새 snapshot당 lead control RPC 최대1회가 있다.
  매-frame world 전체 Actor 검색·새 world snapshot RPC·파일 저장은 없다. 원시 자료 batch와
  상태 전환 이벤트 저장 비용이 있다. 기존 Phase3 계측/Checked Batch/차량음 OFF/클릭음/FFB 미연결을
  유지했다. **Offline 통과를 FPS 영향 없음으로 해석하지 않는다.**

### Verification results

- 실제 서버 연결·Actor 생성/주행·World/TM 변경·TS-XW/FFB 출력을 실행하지 않았다.
- 기존 승인 임시 경로의 `p8_audio_offline_pytest.py`를 읽고 사용했다. CARLA Client/물리 joystick
  차단, display/audio dummy guard 유지. 설치된 두 Python의 API 존재만 import/introspection으로 확인했다.
  운영 native Python에도 collision_detection/on_tick/remove_on_tick/apply_control/set_target_velocity/
  Image.transform이 있다. API 존재와 실제 서버의 행동 검증은 구분한다.
- 변경 전 전체 baseline: **1986 passed / 8 failed / 1 skipped** (35.54s).
- 최종 집중 검증: **44 passed** (4.95s; 신규40 + 관련 기존4).
- 최종 guarded 전체: **2026 passed / 기존 8 failed / 1 skipped** (32.30s).
  baseline과 실패 node ID가 동일하고 신규 실패는 없다. 기존 실패는 DriverView layout/draw 4,
  cockpit 1, P6 candidate AOI 1, P6 validation-loop 1, dummy display/Training lifecycle 1이다.
  아래 이전 검증 절의 8개 node ID 목록과 같다. 이 실패들을 수정하거나 pass로 표시하지 않았다.
- 전체 `ruff check src tests tools`, 변경 Python22개 `ruff format --check`,
  `git diff --check` 통과. 운영 native Python의 `operator_cli --help`에서도 새 옵션을 확인했다.
  실제 장치/주행/타입 검사/FPS 검증 통과를 주장하지 않는다.
- 원본 검증 로그는 승인 임시 디렉터리
  `C:\Users\nexta\AppData\Local\Temp\opencode\m2v1_baseline.txt`, `m2v1_final.txt`에 저장한다.
- 새 테스트는 실제 물리 Backend를 Fake CARLA math/snapshot/물리 모델과 실행하고 실제
  config→controller→runner→session→Backend→operator UI 종료까지 통합한다. Training/M1 제외,
  seed/RNG, 시간창 경계·중복/skip, P5 종료 이후 경로/실패, 정지 cargo/Lead 실제 pose,
  TTC/invalid, MANUAL/NoA 유지·driver override, 780초까지 입력, 종료/오류/collision cleanup,
  실제 DriverView polling/blit/flip/ESC/QUIT를 검사한다.
- 기존 P7 AST 테스트의 모든 control API 금지를 **moving_backend의 self.lead만 허용**하도록 바꿨다.
  이는 물리 선행차 구현 때문이며 Ego writer 금지는 유지한다. legacy post-780 Fake plan 테스트는
  별도 opt-in Offline 계약이고 v1에서는 post-780 호출이 없음을 새 통합 테스트가 검사한다.

### PowerShell 운영 명령 (문서화만, 여기서는 실주행하지 않음)

기존 P8 명령에 **`--m2-hazard-v1` 하나를 추가**하면 된다. seed는 자동 생성/기록한다.
동일 예정 시각 재현이 필요할 때만 `--m2-hazard-seed 42`처럼 추가한다.
기존 P5 시간창은 이번 요청에 수치가 없으므로 아래에서 **기존 실행에 사용한 값**을 입력한다.
이를 임의로 앞당기지 않았다. navigation1000m/response5s/lateral0.15m는 보존된
`P8-P5-4f9c04c5330642389966242c8627c048` Training 로그의 기존 DEV 값이며 연구 승인값이 아니다.

```powershell
Set-Location C:\dev\carla
$python = 'C:\dev\carla-native-tm-cancel-20261012\.venv-p8\Scripts\python.exe'
$tag = [guid]::NewGuid().ToString('N')
$m1Open = [double](Read-Host '기존 M1 P5 open 초')
$m1Close = [double](Read-Host '기존 M1 P5 close 초')
$m2Open = [double](Read-Host '기존 M2 P5 open 초')
$m2Close = [double](Read-Host '기존 M2 P5 close 초')
& $python -m src.p8.operator_cli `
  --mode dev-rehearsal --ack-dev-live `
  --participant-id "DEV-M2-$tag" --run-id "P8-M2-V1-$tag" `
  --m1 NO_SURT --m2 MANUAL --spawn-index 344 --traffic-manager-port 8000 `
  --tsxw --tsxw-index 0 --tsxw-steering-deadzone 0.02 `
  --exit-route-manifest config/town04_exit_routes_dev_v3.json --exit-route-id town04-exit-47 `
  --training-exit-route-manifest config/town04_exit_routes_dev_v3.json --training-exit-route-id town04-exit-47 `
  --exit-navigation-trigger-distance-m 1000 --exit-response-timeout-s 5 `
  --exit-lateral-onset-threshold-m 0.15 `
  --m1-exit-open-s $m1Open --m1-exit-close-s $m1Close `
  --m2-exit-open-s $m2Open --m2-exit-close-s $m2Close `
  --training-duration 600 --m2-hazard-v1 --output-root data/p8_operator
```

Town04는 기존 서버 map을 사용한다(새 `--town` 옵션 없음). M1 600/M2 780을 유지하므로
`--drive-duration`을 추가하지 않는다. 차량음 OFF는 기존 builder 기본값이다.
NOA_L2 검증 시에는 `--m2 MANUAL`만 `--m2 NOA_L2`로 바꾸고 새 ID로 실행한다.
위 명령은 확인한 기존 Training P5도 유지한다. 원래 Training P5를 사용하지 않던 명령에는
해당 두 옵션을 새로 추가할 필요 없이 `--m2-hazard-v1`만 추가하면 된다.

**설정 양립/남은 결정:** Town04/Spawn344/exit47/TS-XW는 builder에서 변경하지 않았다.
실제 EXIT와 MISSED 이후 연결 도로에 준비 가능한 인접 차로가 있고 594초 이전에 P5/차로변경이
종료되어야 기본 준비6초를 확보할 수 있다. 현재 요청에는 기존 M2 P5 시간창 수치가 없고,
전체 Town04 runtime topology도 Offline에서 조회하지 않았으므로 해당 실경로의 성립은 미확정이다.
P5가 594초 이후까지 계속되는 설정이라면 400~600 위험사건과 구조적으로 충돌한다.
그 경우 필요한 최소 사용자 결정은 **P5 시간창 변경 여부 또는 해당 실행의 미발생 수용**이며,
이번 코드가 P5 시간·위치를 몰래 바꾸지는 않는다. 연구 반응 임계값 결정은 개발 장면 실행 조건이 아니다.

### 전체 시스템 후속 검증 체크리스트

- [ ] 실제 MANUAL/NoA 주행과 FPS, 선행차 추종/측면 이동, 적재물 사전 가림·정지·삭제 시점
- [ ] 최초 부분 노출/화면 후보와 TTC 편차, TM 사전 감속/자동 회피 제동, 운전자 override/재활성화
- [ ] P5 EXIT/MISSED 각각의 실제 연결 경로와 400~600 시간창 양립
- [ ] Tobii 샘플 수신·보정
- [ ] 전방/미러/태블릿 AOI 확인
- [ ] 시선·차량·입력·사건의 시간 연결
- [ ] CSV/Manifest 실제 값·누락·중복·부분 저장, ESC/창 종료/장치 오류/cleanup
- [ ] 원자료에서 지표를 다시 계산할 수 있는지
- [ ] 관리자 문서만으로 정상 운영 가능한지

### Files changed (이번 v1 범위)

- 신규: `src/p7/moving_config.py`, `moving_route.py`, `moving_backend.py`,
  `src/p8/operator_m2_hazard.py`, `src/scenario/active_scene.py`.
- 연결: `src/p8/operator_cli.py`, `operator_model.py`, `operator_execution.py`,
  `operator_components.py`, `operator_ui.py`.
- 공통 loop/관측: `src/scenario/camera_feed.py`, `driver_view.py`,
  `research_noa_session.py`, `research_noa_view.py`, `research_noa_types.py`,
  `research_noa_persistence.py`, `research_noa_traffic_manager.py`.
- 검증: `tests/m2_hazard_fakes.py`, `test_m2_hazard_v1.py`,
  `test_m2_hazard_operator_integration.py`, `test_m2_hazard_driver_view.py`, `test_p7_logging_cli.py`.
- 문서: 본 문서, Source of Truth의 v1 덧붙임, P7 개발 문서, P8 전체 운영 흐름 안내.

---

## 아래는 이전 결정/검증 이력 (v1 운영 안내가 아님)

## 2026-10-16 — 현재 로컬 P7 확인 및 확정 범위 보완

**기존 구현 확인·아래 범위의 코드 보완·Offline 통합 검증을 완료했다. 실제 위험 장면,
반응시간 측정 유효성, 참가자 본실험 준비는 완료하지 않았다.** 이 절이 아래 10월 13일
기록보다 최신이다. 기존 `BLOCKED_NOT_RUN`은 미실행 증거이며 P7 코드 부재의 증거가 아니다.

### A. 확인 범위와 기준

- 작업 시작 branch: `research/phase-1c-automation`.
- 현재 기준 HEAD: `58aa51689c2497285e2f4365f484fd539e5131b6`.
  과거 FPS 보고서 HEAD나 원격 브랜치를 기준으로 삼지 않았다.
- 시작 Working Tree의 추적 변경은 `.omo/run-continuation/ses_f6076abc7ffeGEhktGoCKxl5vF.json`.
  `.omo/drafts`, `evidence`, `plans`, continuation 등 미추적 작업도 이미 존재했다.
  기존 미추적 `src/error_notes.py`도 보존했다. 이 작업들은 정리·수정하지 않았다.
  추적 코드 파일은 당시 HEAD 기준이었다.
- 실제 존재를 확인한 문서: Source of Truth, `P8_Full_Experiment_Integration.md`,
  `P8_Patched_TM_LaneChange.md`, `P8_Manual_FPS_Phase3_20261015.md`, 본 문서,
  `P7_Traffic_Hazard_Development.md`. 별도 교수님 원본 DOCX는 저장소에서 발견하지 못했다.
  사용자 요구와 Source of Truth §6.7/§8/§11.10/미확정 파라미터 표를 대조했다.
- v5와 이번 사용자 요구 사이에서 새 충돌은 발견하지 않았다. 과거 static-smoke의 정지 Ego,
  개발 궤적/수치와 v5 이동 Ego 실험의 차이는 승인값으로 합치지 않고 개발 제한으로 유지했다.

현재 연결은 다음과 같다. 상태 머신·handoff·발생 저장소를 중복 만들지 않았다.

```text
operator_cli → OperatorController / OperatorComponents
  → prepare_operator_run → ResearchNoARunner → ResearchNoASession
  → DriverView.run 명목 780초 경계 → 기존 nominal_m2_handoff
  → 동일 session/world/Ego/viewer/control/logger/SessionClock
  → FINAL_HAZARD → OperatorHazardLoop → HazardScenario
  → 소유 hazard actor cleanup → 기존 session 안전 종료/정리
```

| 항목 | 현재 분류와 근거 |
|---|---|
| 명목 M2→별도 FINAL_HAZARD, 공통 자원/시계 | 구현 및 Offline 검증 있음. `operator_execution.py`, `operator_continuity.py`, `research_noa_session.py` 재사용 |
| Training/M1/부분 M2/오류 제외 | 기존 controller/runner 검사 재사용. 이번에 780초 경계에서 대기 중인 ESC/창 닫기 우선 처리도 추가 |
| 실제 MANUAL/NOA_ACTIVE 유지 | 기존 pre-shutdown handoff 재사용. NOA_L2 배정 후 운전자 해제로 MANUAL인 사례도 검증 |
| P7 상태/발생 예약/소유권/원시 입력/TTC | 구현 및 Fake 검증 있음. `hazard.py`, `occurrence.py`, `ownership.py`, `metrics.py` |
| 선행차·적재물 spawn/이동 API adapter | `carla_actors.py`에 기존 코드와 Fake 검증 있음. **P8 이동 Ego용 physical backend는 미연결** |
| 화면 표시 프레임 연결 | 이번 구현 및 Fake 검증. 실제 영상 가시성 detector·광학 표시 시각은 미구현/실주행 검증 필요 |
| 연구용 유효 반응/회피·정지/구조 경계 | 승인 근거 부족. 주입 계약과 후보 기록은 구현, 운영 연구 수치는 미확정 |
| P5 EXIT/MISSED 이후 위험 구간 접근 | 경로 적합성 미검증. `p5_active=False`만으로 접근 가능성을 증명하지 않음 |
| TM 평상시 지원과 자동 회피 제동 금지의 양립 | 실제 동작/정책 근거 부족. 물리 실행 차단 사유 |

### B. 확정 요구와 미확정 결정

확정: M2 780초와 위험사건 시간 분리, 참가자당 1회, 선행차가 비켜나며 이미 존재한 정지
무생물 적재물 노출, 두 조건 동일 장애물, TOR/충돌 경고/자동 회피 제동 없음, 정상 NoA
지원 및 운전자 우선권 유지, 충돌/도로이탈 경계의 자동 정지·종료, 사전 비공개.
운영 예상 5분을 300초 관찰 timeout으로 바꾸지 않았다.

| 미확정 사항 | 현재 근거/값 | 막는 구체적 동작 및 이번 구현 |
|---|---|---|
| 위치·시각 규칙/허용 창/미도달 처리 | `HazardConfig` station 22m 등은 DEV_ONLY | 운영 발동 불가. `HazardPolicy.trigger`의 WAIT/FIRE/BLOCK와 arm 이후 host elapsed 주입 구현 |
| EXIT/MISSED 경로·spawn 연결 | 승인 route/rejoin 증거 없음 | 순간이동·임의 재경로 없이 준비/발동 차단해야 함. 기존 route/P5 guard 재사용 |
| 초기 간격/속도/가림/이동 방향·경로 | gap 12m, cargo station 45m, lead 3m/s, 이동 3s는 개발값 | 이동 Ego에 적합한 가림·궤적 backend 미연결. 기존 static adapter만 Fake 검증 |
| 적재물 종류/크기/배치/충돌 설정 | `static.prop.box01`, 개발 선행차 blueprint | 두 연구 조건의 최종 동일 asset·배치·충돌 승인 아님. spawn identity 검증 보완 |
| 실제 최초 가시 판정 | projected bounds proxy만 존재 | frame-matched detector 계약·성공 flip 연결 구현. canonical `hazard_visible_t`는 null |
| 유효 제동·조향 onset | brake 0.1/steer 0.05는 후보값 | raw candidate만 계산. 노출과 같은 tick 입력을 사후 반응으로 소급하지 않게 수정 |
| TTC/시작 속도·거리 | 개발 route/closing speed/collision-course 후보 | 유한값/접근/경로 유효 조건 유지, overflow 추가 차단. 연구 canonical 값은 null |
| 회피·정지·관찰 종료 | 기존 5s는 DEV_ONLY | injected `outcome` 계약 구현. policy가 있으면 개발 5s 종료를 상속하지 않음 |
| 충돌·이탈/구조 경계 | collision sensor와 개발 이탈 proxy | safety owner 요청 유지·오류 내성 보완. 요청 반환을 실제 구조 성공으로 승격하지 않음 |
| TM 위험사건 대응 | 일반 TM 주행 코드만 확인 | 사전 감속·회피 제동 금지 만족 여부 미검증. 전역/차량 충돌 회피 설정 변경 없음 |

신규 테스트 수치는 `FixturePolicy`에만 존재하며 운영 기본값에 복사하지 않았다.
`HazardPolicyApproval.policy=None`은 기존 **Offline 개발 station 규칙** 호환용이다.
승인 문자열이나 policy를 채워도 실제 실행 허가가 되지 않는다. `require_offline_hazard_runner`
및 `OFFLINE_TEST` scope, participant readiness 차단이 그대로 적용된다.

TM 확인: `research_noa_traffic_manager.py`는 Ego의 일반 자율 차로변경을 False로 설정하고
기존 지원/운전자 개입 lifecycle을 유지한다. 이것이 장애물 사전 감속이나 충돌 회피 제동이
없다는 증거는 아니다. Lead는 자기 Actor adapter, Ego는 기존 MANUAL checked batch/TM,
충돌/이탈 종료는 safety owner라는 소유권을 유지했다. P7 Lead 이동을 Ego Z/X 기능으로
대체하지 않았다. 이 부분의 정책/physical backend 없이는 전체 실장면 실행을 더 진행할 수 없다.

### C. 변경 전후 및 실제 연결

1. **발동/종료 주입**: 기존 `HazardScenario`에 typed policy만 추가했다. arm 이후 시각과
   관측 위치를 함께 평가할 수 있고, miss를 BLOCK 처리할 수 있다. 완료는 검증된 연구 결과가
   아니라 `INJECTED_POLICY_OFFLINE_NOT_PHYSICAL_VALIDATION`으로 출처를 기록한다.
   collision/departure와 rescue를 policy 반환 문자열로 만들어내는 것은 허용하지 않는다.
2. **준비/한 번 실행**: 기존 발생 예약은 prepare 직전 소비하며 실패해도 되돌리지 않는다.
   P5 conflict/nonfinite 준비값은 예약 전에 차단한다. 부분 spawn은 기존 소유권에 남겨 정리하고
   반환 actor의 `is_alive/type_id`를 확인한다. 재진입·중복 start·종료 뒤 callback은 재실행하지 않는다.
   P8 개발 `_dev_occurrences`와 participant 차단을 유지한다. 참가자 실행은 아직 구현되지 않았다.
3. **시각 provenance**: backend는 기존 persistence의 `ResearchSnapshotSample`을 인자로 받는다.
   그 sample에 대한 geometry임을 계약하고 timestamp 불일치는 차단한다. 과거 geometry를
   최신 persistence timestamp로 덮어쓰던 코드를 제거했다. 단, timestamp 일치만으로 backend
   내용의 진실성까지 검증되는 것은 아니다. 중단 이벤트는 현재 host 시각, 마지막 관측은 별도
   `observation_host_monotonic_ns/observation_carla_frame`으로 보존한다.
4. **표시 경계**: 실제 `DriverView._blit_image`가 선택한 Front sensor/frame/simulation time/
   receipt 시각을 보관하고, 성공한 `display.flip()` 직후 host monotonic_ns를 취득한다.
   callback이 중간에 다음 이미지를 교체해도 선택 frame은 바뀌지 않는다. 기존 after-flip hook
   뒤 optional continuation으로 전달한다. flip 실패에서는 표시 증거를 만들지 않는다.
   backend detector가 **같은 sensor/frame**에 대한 verdict를 제공한 경우에만 최초 display
   candidate를 한 번 기록한다. 발동 전 노출 판정은 abort한다. detector 부재/누락은 관측 불가다.
5. **해석 제한**: flip 반환은 광학적 화면 표시/참가자 인지 증거가 아니다. 실제 detector는 없고
   Fake verdict만 검증했다. 생성 시각, Lead 명령 시각, 기하 reveal, 카메라 receipt, flip 완료를
   섞지 않는다. `hazard_visible_t`, 유효 반응 canonical 필드는 계속 null이다. receipt의
   perf_counter, host monotonic, CARLA simulation time은 서로 직접 빼지 않는다.
6. **반응/TTC**: reveal 이전 입력과 reveal tick의 입력을 사후 onset으로 계산하지 않는다.
   release와 이후 edge가 필요하며 잘못된/역행 입력시각은 baseline을 끊는다. 실제 `DriverInput`을
   사용하고 TM control을 읽어 반응으로 쓰지 않는다. 후보 입력 timestamp는 post-control
   persistence 시각이지 input polling 시각이 아님을 기록한다. TTC가 정의되지 않으면 null과
   reason을 유지하고 유한 입력의 나눗셈 overflow도 정상 TTC로 쓰지 않는다.
7. **종료/오류**: observation 소실·abort를 `EXECUTED_OFFLINE/NORMAL_COMPLETION`으로 쓰던
   부분을 `ABORTED_OFFLINE/HAZARD_ABORTED:<reason>`으로 수정했다. collision/departure이면
   로그 쓰기가 실패해도 safety owner를 한 번 요청한다. 원래 오류는 cleanup 오류로 덮지 않고
   기존 `append_error_note`를 사용한다(`src/error_notes.py` 자체는 변경 없음).
   종료 record에는 occurrence/prepare/terminal reason/부분 후보/마지막 관측/끝의 실제 모드를 남긴다.
8. **M2 경계 사용자 종료**: duration 경계에서 대기 중인 ESC/QUIT를 확인한 뒤 handoff한다.
   사건 자원을 먼저 취득하지 않는다. 그 event batch는 같은 frame에서 재사용한다.
   활성 continuation의 나중 사용자 종료 테스트는 종료 입력을 다음 frame으로 이동했으며
   `finalize:USER_EXIT` 1회라는 기존 기대값은 완화하지 않았다.

성능: 이번 변경은 **새 CARLA RPC/센서/Actor 전체 검색을 추가하지 않는다**.
spawn identity의 `type_id/is_alive`는 기존 Actor 메타데이터/캐시 상태 경계이며 live 비용은 미측정이다.
추가 display metadata는 활성 P7 continuation에서 한 Front frame만 보관하고 이미지 buffer는
보관하지 않는다. 최초 표시 후보 이벤트/flush는 1회, 종료 기록은 기존 종료 경계다.
기존 P7 SAMPLE cadence와 일반 telemetry cadence는 유지했다. boundary event poll은 handoff
시점 한 번이며 같은 loop poll을 대체한다. FPS Phase3 계측/60FPS cap/checked batch는 유지했다.
추가 P7 callback 비용은 실주행으로 검증하지 않았다.

### D. 변경 파일과 보존

| 파일 | 변경 이유 |
|---|---|
| `src/p7/policy.py` (신규) | 발동/종료 typed 주입 계약, 운영 수치 없음 |
| `src/p7/display.py` (신규) | 동일 sensor/frame 가시성 verdict 계약 |
| `src/p7/hazard.py` | 기존 상태기에 policy, 준비/예약/종료 provenance, 안전 요청 경계 |
| `src/p7/metrics.py` | reveal tick 소급 반응 방지, 입력시각/비유한 TTC 처리 |
| `src/p7/carla_actors.py` | spawn 반환 identity/liveness 검사 |
| `src/p8/operator_hazard_contracts.py` | policy 및 cached sample backend 계약 |
| `src/p8/operator_hazard.py` | 실제 P8 연결, display 후보, abort/오류/안전 요청/부분 기록 |
| `src/p8/operator_components.py` | 재진입에서 기존 loop 중복 생성 방지 |
| `src/scenario/camera_feed.py` | buffer 없는 표시 frame metadata |
| `src/scenario/driver_view.py` | 성공 flip 연결, nominal 경계 queued exit |
| `tests/p8_hazard_execution_support.py` | 새 sample 계약을 사용하는 Fake 관측, 충분한 입력 fixture |
| `tests/test_driver_view_continuation_exits.py` | 활성화 이후 종료와 경계 이전 종료를 구분한 fixture |
| `tests/test_p7_policy_boundaries.py` (신규) | 주입/준비/명령≠관측/반응/TTC 검증 |
| `tests/test_p7_display_bridge.py` (신규) | 실제 DriverView blit/flip/race/queued exit 검증 |
| `tests/test_p8_hazard_observation_boundaries.py` (신규) | 실제 controller/config/runner/session의 Fake 통합 |
| 본 문서 | 최신 한국어 구현/검증/남은 결정 기록 |

기존 P7 파일 일부는 변경 파일 Ruff Format 적용으로 diff 줄 수가 늘었다.
Training 최대 600초/F8, M1 600초, M2 780초, P5 EXIT/MISSED·t0·방향지시등,
TS-XW/NoA/Driver Override, 차량음 OFF/클릭음, FFB Offline 상태, attestation을 유지했다.
Git checkout/switch/pull/merge/rebase/reset/stash/commit/push 및 다른 worktree 가져오기는 하지 않았다.
원본 로그·Manifest와 기존 작업 보존은 아래 hash 확인으로 별도 검증했다.

### E. Offline 검증

- 사용 Python: 로컬 `.venv\Scripts\python.exe` (Offline 테스트만). 운영 승인 native Python은
  교체하지 않았다. `p8_audio_offline_pytest.py`를 직접 확인 후 사용했다.
  dummy display/audio 강제, 실제 Joystick 생성 및 `carla.Client` 차단, joystick init/count 대체.
  실제 CARLA 연결/World/TM 변경/차량 생성·제어, Joystick/Haptic/FFB·스피커 출력은 하지 않았다.
- 이번 작업 전 baseline: **1950 passed / 8 failed / 1 skipped**, 26.80s.
- 신규 36건을 포함한 집중 P7/P8/DriverView 11개 파일: **110 passed**, 3.30s.
  기존 실제 controller→prepare config→ResearchNoARunner→ResearchNoASession→P7 호출 경로를
  Fake World/viewer/input/clock과 실행한다. 별도로 실제 `DriverView.run`의 렌더·flip 순서를
  Fake pygame으로 검증한다. Fake controller만으로 통합을 주장하지 않는다.
- 최종 guarded 전체: **1986 passed / 기존 8 failed / 1 skipped**, 26.61s.
  baseline과 실패 node ID 집합이 같으며 신규 실패는 없다. 신규 36건이 추가됐다.
- 전체 Ruff 통과, 변경 Python **15개 파일 Format --check** 통과, `git diff --check` 통과.
  타입 검사 및 물리 성능/안전 검증 통과는 주장하지 않는다.
- 작업 전 `src/tests/tools/docs/config/data` **1094개** SHA256 중 **1083개 동일**,
  위 표에 적힌 기존 파일 **11개만 변경**. 신규 파일은 Python 5개이며 별도 추가됐다.
  원본 `data/p8_operator` **164개 전부 동일**, `src/error_notes.py` 동일.
  `.omo`는 hash 범위 밖이며 이번 작업에서 편집하지 않았다. 기준 해시는 승인 임시 디렉터리의
  `p7_20261016_before.json`에 보존했다.
- 전체 suite는 P5, checked batch/TS-XW, Training/M1/M2, Phase3, 클릭음/차량음 OFF,
  FFB 미연결 등의 기존 회귀를 포함한다. 실행 로그는 임시 승인 디렉터리의
  `p7_20261016_baseline.txt`, `p7_20261016_final.txt`에 보존(UTF-16).
- 기존 실패 8건: `test_driver_view.py`의 protected-front/layout 2건/draw 총 4건,
  `test_driver_view_cockpit.py::test_v_respawns_only_front_sensor_with_cockpit_relative_transform`,
  `test_p6_candidate_aoi.py::test_candidate_matches_current_mirrors_without_claiming_measured_accuracy`,
  `test_p6_validation_loop.py::test_space_at_previous_segment_end_must_not_skip_next_prepare`,
  `test_p8_training_pygame_lifecycle.py::test_f8_training_restores_operator_display_and_accepts_space_then_quit`.
  마지막 것은 guarded dummy display와 non-dummy 기대값 충돌이다. 기존 실패 기대값을 수정하지 않았다.
- 필수 16항목 중 물리 구조 성공/실제 가시성/승인 반응 기준은 Offline 완료 대상이 아니다.
  해당 결과를 생성하지 않는 계약, 오류·누락·부분 cleanup, 같은 모드·식별 연속성, 1회 예약,
  개발/참가자 차단 및 안전 owner 호출까지만 검증했다.

### F. 남은 문제와 참가자 차단 이유

실제 이동 Ego용 actor/observation backend, 승인된 위치·시각/경로/가림·Lead 궤적/적재물,
종료/구조 경계, TM과의 양립 정책, 실제 pixel/frame detector, 유효 반응시간 정의가 없다.
이 때문에 `operator_hazard_contracts.py`의 plan과 backend를 운영에 연결할 근거가 부족하다.
기존 `carla_actors.py`의 static-smoke kinematic 이동을 이동 Ego 실험에 그대로 연결하면
가림/충돌/물리 궤적을 보장할 수 없다. 따라서 이번 구현은 단순 BLOCKED 보고에 그치지 않고
정책 주입·관측 provenance·표시 전달·반응 후보·종료/안전 오류 처리를 완성했지만,
**운영 CLI에서 hazard plan을 활성화하는 옵션은 추가하지 않았다**.

MANUAL FPS 약 10FPS 및 장시간 stall은 미해결이다. flip 반환 시각도 모니터 photon onset이나
참가자 인지 시각이 아니며 input polling 지연과 display 지연이 유효 반응 측정에 영향을 준다.
물리 stopping 성공은 `None`; safety 요청 반환을 `AUTOMATIC_RESCUE`로 만들지 않는다.
`AVOIDED/STOPPED/INCOMPLETE`는 주입된 Offline fixture 정책 분류이며 연구 승인값이 아니다.
전부 `DEV_ONLY/UNVALIDATED_RESEARCH_DEFINITIONS`, 참가자 분석 가능 상태로 승격되지 않는다.

### G. 후속 live 검증 (별도 승인 이후)

1. 연구 정책·physical backend·가시성/반응 정의 승인과 참가자 readiness가 먼저 필요하다.
   현재 CLI로 위험사건을 켜는 명령은 없으므로 존재하지 않는 옵션을 제시하지 않는다.
2. 기존 단일 `python -m src.p8.operator_cli` 경로와 승인 native runtime/attestation에서
   M2 780초→별도 FINAL_HAZARD, 동일 Ego/센서/clock, 실제 MANUAL/NOA_ACTIVE 연속성을 확인한다.
3. EXIT/MISSED 각각의 구간 도달, 사전 배치 적재물 가림, Lead 명령/실제 이동/노출을 구분한다.
4. TM의 의도치 않은 사전 감속·자동 회피 여부, 운전자 brake/steer 해제 및 종료 제어권을 확인한다.
5. 실제 선택/표시 frame·가시성 detector·광학 시각과 raw 입력, TTC·outcome을 대조한다.
6. 충돌/이탈/구조 요청/실제 정지, 장시간 stall/FPS, 오류·ESC 후 부분 로그/Manifest/자원 정리를 확인한다.

완료 판정: **기존 구현 확인 완료 / 확정 범위 코드 보완 완료 / Offline 통합 검증 완료**.
**실제 CARLA 장면 미검증 / 운전자 반응 측정 유효성 미검증 / 참가자 본실험 준비 미완료**.

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
