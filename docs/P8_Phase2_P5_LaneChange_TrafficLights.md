# P8 Phase 2: P5 시간창, 상시 방향지시등 요청, 신호 대응 조사

> Current guarded execution, observed completion and automatic signal cancellation:
> [P8_Patched_TM_LaneChange.md](P8_Patched_TM_LaneChange.md). Earlier BLOCKED findings below remain historical evidence.

## 현재 판정

단일 `python -m src.p8.operator_cli`에 요청·시간창·관측 연결을 구현했다.
**패치된 TM 단발 차로변경은 EMPTY_REHEARSAL_ONLY 조건에서 코드 연결됐으며,
물리 방향·완료·적색 신호 정지 및 재합류 동작은 UNVERIFIED다. Phase 2 전체 기능
완성 또는 참가자 운영 준비를 주장하지 않는다.**
이번에 실제 주행은 시작하지 않았다. 별도 운전 프로그램/Custom Controller를 만들지 않았다.
지도 확인은 기존 서버에 읽기 전용으로 수행했으며 Actor 생성, vehicle control,
TM 생성/설정, 신호 변경, World tick/설정 변경을 하지 않았다.

기준은 현재 HEAD `675f21edf2326581c059cf5b99cde8a894bfffa6`와 Working Tree다.
Source of Truth 원본, 연구 원본 로그, 기존 route manifest, Exit47 Fork Fix,
P6/P7 구현과 Participant Readiness 차단을 보존했다. 과거 브랜치 구현을 가져오지 않았다.

## 연구 범위 변경

V5 §6.1의 출구 상황 한정 요청에서 **NoA ON 일반 주행 중 좌/우 요청도 허용**하도록
소프트웨어 입력 범위를 확대했다. 이는 이번 사용자의 명시적 개발 요구이며 연구 책임자의
최종 승인을 의미하지 않는다. P5 연구 기회는 여전히 모듈별 한 번이다.
기존 TM 기본 속도, 110km/h 자체 Guard 제거, N/브레이크/조향 해제 정책은 유지한다.

## 1. 방향지시등 요청

- Z=LEFT, X=RIGHT. OFF→ON으로 바뀌는 fresh input만 요청한다.
- 요청 판정과 실제 light toggle에 동일한 accepted-edge 필터를 사용한다.
  repeat/held, OFF 전환, 같은 batch에서 켰다 끄기, 비키보드 이벤트를 구분한다.
  같은 batch의 양방향 입력은 최종 켜진 방향으로 합쳐진다.
- 실제 조향/브레이크/N 해제가 먼저 처리된다. MANUAL에서는 시스템 차로변경 요청을
  거부하되 방향지시등 자체는 정상 조작할 수 있다.
- P5 추천 중 우측 X 요청은 `context=P5`, 동일 `lc_event_id`로 연결한다.
  일반 요청은 `context=GENERAL`이다. 같은 X로 두 번 실행하는 경로는 없다.
- Z는 이제 항상 좌측 요청이며 P5 거부가 아니다. P5 명시적 거부는 기존 **R**을
  선택적으로 사용한다. 확인에는 C/R을 요구하지 않는다. 기존 HUD가 X 확인/R 거부를 표시한다.
- 인접 차로 존재, Driving 타입, lane ID 방향, waypoint lane-change permission을
  검사한다. 이는 정적 자격 검사이지 주변 차량 gap 안전성 검증이 아니다.
- 부적합 차로는 `REJECTED_STATIC_INELIGIBLE`, 수동 우선은
  `REJECTED_MANUAL_PRIORITY`로 기록한다. 적합한 요청은 `REQUESTED` 뒤 로드된
  PYD 해시, TM listener PID 단독 소유권, ego 외 vehicle/walker/static.prop 부재를
  매 제출 직전에 확인하고 `SUBMITTED_TO_TM`, `IN_PROGRESS`를 기록한다.

### 안전한 실행이 차단된 이유와 대안

CARLA0.9.16 `force_lane_change()`는 강제 변경이며 충돌 가능성을 무시할 수 있다.
`auto_lane_change(True)`는 운전자 단발 의도를 제한하지 않는다. `set_path/set_route`는
경로 선택 기능이지 안전한 좌/우 단발 요청 완료 계약이 아니다. 따라서 이 작업은
어느 API도 안전한 요청 실행인 것처럼 연결하지 않았다. TM의 자체 차로변경 OFF와
좌/우 random percentage0, 운전자 vehicle-light 소유권은 유지한다.

가능한 다음 선택지는 다음과 같으며 이번에 구현하지 않았다.

1. TM 내부에 요청 수명·취소·gap 평가·결과를 연결하는 upstream 기능 또는 승인된
   native TM 확장: 새 Python 조향 Controller 없이 가능하지만 일치하는 CARLA build와
   충돌/취소/driver-priority의 물리 검증이 필요하다.
2. 검증된 전체 경로를 TM `set_path/set_route`로 지정: 분기 선택에는 도움이 되지만
   상시 차로변경의 동적 안전 문제는 별도로 남는다.
3. 외부 gap 검사 후 `force_lane_change`: 관측과 실행 사이의 race 및 강제 API의
   충돌 무시를 없애지 못한다. 안전한 구현으로 채택하지 않았다.

### 관측의 한계

`lane_change_request`, `lane_change_geometric_sample`, `lane_change_observation_end`를
기존 CSV queue/writer에 기록한다. 방향·모드·정적 판정·P5 연계와 request ID를 보존한다.
후속 XY, 요청 위치 대비 변위, road/section/lane identity 변화를 기록한다.
MANUAL 전환·새 요청·정상 종료·중단·오류는 관측 종료 이유로 구분한다.
일반 요청의 onset 임계값은 새로 정하지 않았으며 `lateral_onset_status`는 UNVERIFIED다.
lane identity 전환을 물리 경계 통과/차로변경 완료로 승격하지 않는다.
기존 P5의 별도 lateral onset/tL 및 실제 EXIT/MISSED 관측 정의는 유지한다.

## 2. P5 시간과 위치의 결합

모듈 route를 선택하면 다음 네 설정이 모두 필요하며 기본값은 없다.

- `--m1-exit-open-s`, `--m1-exit-close-s`
- `--m2-exit-open-s`, `--m2-exit-close-s`

유한·비음수·open≤close, M1 close≤600/M2 close≤780을 검증한다.
정확히 몇 초부터가 후반부인지 임의로 확정하지 않는다. 연구자가 승인할 값은 미확정이다.
route 없이 시간창만 지정하거나 route에 시간창을 빠뜨리면 시작 전에 거부한다.

시간 원점은 Spawn/Gear 준비와 초기 automation 설정 이후 실제 viewer 주행 직전이다.
공통 SessionClock origin에 대한 비변경 projection으로 경과시간을 계산한다.
UI 진입/차량 준비 시간을 eligibility에 포함하지 않는다. 훈련은 기존 별도 설정을
유지하며 M1 시간창을 상속하지 않는다.

`open <= active_elapsed <= close`와 기존 navigation/confirmation 위치 조건을 모두
만족할 때만 안내 후보를 만든다. 아직 제시하지 않은 WAITING 상태에서는 projection
cursor를 재탐색해 첫 접근이나 시간창 내 늦은 접근이 다음 순환 기회를 잠그지 않게 했다.
한 번 표시된 기회에는 기존 단일 lifecycle과 Fork Fix 관측을 유지한다.

- t0는 여전히 실제 display flip 뒤 기록하며 당시 canonical NoA 상태를 고정한다.
- 표시 준비 전에 시간창이 닫혔으면 안내하지 않고 `NOT_PRESENTED` 및
  `ELIGIBILITY_WINDOW_CLOSED`를 남긴다.
- draw/flip 도중 close를 넘은 경우 이미 보인 화면을 NOT_PRESENTED로 숨기지 않는다.
  실제 t0와 `lc_presentation_timing_deviation`을 기록한다. strict 실시간 deadline 보장은 아니다.
- 안내/확인/거부/무응답/EXIT/MISSED는 모듈 종료 명령이 아니다. 주행은 기존 시간까지 계속된다.
- 모듈당 **최대 한 번**은 보장하지만 창 안에서 지정 위치에 도달하지 않으면0회다.
  정확히 한 번 도달할 물리 경로·시간창이 검증됐다는 뜻은 아니다.

기존 manifest의 source -3→target -4 조건과 corridor를 임의 확대하지 않았다.
일반 주행 중 다른 차로로 이동하면 해당 조건을 충족하지 못해 안내가 없을 수 있다.
모든 접근 차로에서 동일 안내가 필요한 최종 연구 경로 설계는 미완료다.

## 3. Town04 읽기 전용 지도 결과

현재 서버에서 직접 조회한 값:

- Client `0.9.16`, Server `294096e`, version mismatch 경고.
- Map `Carla/Maps/Town04`, `synchronous_mode=False`, 조회 당시 vehicle Actor0개.
- `get_topology()` 559구간. lane identity 그래프에서 Exit39와47을 포함하는 폐회로 확인.
- 해당 -3 차로 폐회로의 OpenDRIVE 도로 section 기준 길이 합 **3049.924m**.
  실제 lane-center 주행거리·TM 한 바퀴 시간 측정값이 아니다.

대표 폐회로의 도로 순서는
`39→1184→40→1401→41→6→45→145→46→1072→47→774→48→901→49→1173→50→42→267→43→35→861→36→760→37→1602→38→1091→39`다.
모두 section0/lane-3인 topology 연결이며, 사용자 설명의 순환 도로 재접근 가능성을
뒷받침한다. 기하학적 8자 모양이나 실제 TM 순환 완주를 별도 검증한 것은 아니다.

출구 뒤 연결도 현재 graph에서 확인했다:

- Exit39: `(33,0,+2)→(779,1,+2)→(779,0,+6)→(47,0,+6)`
- Exit47: `(34,0,+2)→(1097,1,+2)→(1097,0,+4)→(39,0,-4)`

원래 -3 차로로 돌아오는 lane-change-free 경로는 이 그래프 탐색에서 찾지 못했다.
lane-change edge를 추가하지 않은 탐색이므로 재합류 불가능의 증명은 아니다.
TM에 이 경로를 적용하지 않았으며 무작위 분기·복귀 차로·연속 주행은 물리 UNVERIFIED다.

기존 Manifest를 기존 loader로 재검사한 결과:

| 경로 | 점 수 | 참조 끝 station(m) | 안내 station(m) | 확인 종료(m) |
|---|---:|---:|---|---:|
| town04-exit-39 | 288 | 1361.318298 | 86.107673~1086.107673 | 985.983227 |
| town04-exit-47 | 284 | 1426.816221 | 151.671258~1151.671258 | 1051.605042 |

두 경로의 안내 시작은 fork 약1000m 전이다. 이 짧은 exit corridor manifest는
전체 순환/재합류 경로가 아니다. 접근 주기는 `주행거리/실제 변화하는 속도 + 정지 지연`
의 영향을 받으며 MANUAL/TM/신호 대기 차이로 달라진다. 따라서 위 거리로 특정 초나
바퀴 수를 정책으로 정하지 않았다.

`P5_Exit47_Fork_Fix_20261007.md`의 과거 성공은 Custom 제어·scripted 확인으로
road34 진입 후 제동한 기록이다. 그것을 TM 재합류 성공으로 재해석하지 않는다.

## 4. 적색 신호 조사

### 가설과 현재 증거

| 가설 | 구분할 증거 | 현재 결론 |
|---|---|---|
| 화면의 빨간 신호가 ego에 적용되지 않음/캐시 연계 차이 | is_at, vehicle cached state, actor state/id, affected/stop lane 관계 | 원래 주행 증거 없음; 미확정 |
| ignore 설정 또는 공유 TM의 외부 변경 | 매 활성화 전 actor별 설정 성공, TM port, 외부 클라이언트 이력 | 기존 코드에 명시 setter 없었음; 원인이라는 증거는 아님 |
| 비동기 처리 지연/낮은 update rate/버전 차이 | frame·sim/host 간격, 속도·brake readback, client/server version | 비동기와 버전 경고는 직접 확인, 인과관계 미확정 |

TM 등록 직전에 **해당 ego만** `ignore_lights_percentage(ego, 0.0)`을 설정한다.
이는 TM의 기본 신호 대응 의도를 명시적으로 재확인하는 변경이다. 기본값도0이므로
이 한 줄이 사용자가 본 현상을 고쳤다고 말할 수 없다. 다른 충돌·보행자·신호 정책이나
World/TM sync 설정은 변경하지 않았다. 별도 Python 제동 writer는 없다.

`traffic_light_observation`에는 다음을 기록한다.

- vehicle.is_at_traffic_light, vehicle cached state, associated actor id/state 및 차이
- affected/stop waypoint lane 관계, ego road/section/lane
- ego snapshot frame/time·속도, cached brake/throttle
- 별도 traffic-light RPC 시작/완료 host interval, 완료 시각의 event envelope
- 설정된 TM port, 소프트웨어상 등록 여부, World sync, Client/Server 버전

다중 RPC는 원자적 동일-frame 관측이 아니다. actor association은 UNVERIFIED,
포트 공유는 UNKNOWN, 위반 여부는 NOT_CLASSIFIED다. 캐시 제동값을 실제 제동 적용
증명으로 쓰지 않는다. 현재 지도 조회에 ego가 없어 적용 신호를 직접 판정하지 못했다.
정지선 통과/정지 성공 및 원인 해결은 다음 실제 전체 시간 주행에서 확인해야 한다.

## 5. 검증과 남은 경계

- 변경 전 pytest:1353 passed. 최종 전체 pytest:**1382 passed, 240.73s**.
- 전체 Ruff:**All checks passed**. `git diff --check`:성공, 기존 CRLF 경고 있음.
- Python LSP는 basedpyright 미설치/기존 설치 거절로 사용 불가. 타입 검사 통과 주장 없음.
- actual ResearchDriverView의 synthetic 입력 직접 실행: RIGHT→repeat None→LEFT,
  대응 light state 유지, local vehicle-control write0회.
- synthetic 시간창: WAITING→DRAFT→NOT_PRESENTED. 테스트 숫자는 연구 기본값이 아니다.
- 일반 요청을 남긴 정상 종료에서 SessionClock 역행을 failing test로 재현해 수정했다.
  실제 shutdown 시점에 observation end를 연결하며 원본 시각을 clamp하지 않는다.
- 훈련에 M1 window가 잘못 상속되던 문제도 failing regression으로 수정했다.
- 기존 P5 Fork Fix, P6/P7, Driver Override, Cleanup, F8/REST/600·780초 회귀 포함.
- 실제 CARLA 주행/신호 통과 재현/차로변경/재합류는 수행하지 않았다.

연구자가 확정할 항목: M1/M2 open/close, source lane 및 일반 차로변경 후 안내 범위,
전체 순환·출구·재합류 경로와 방향, 응답 시간, 기존 P5 onset 기준,
일반 lane onset/physical crossing 정의, 상시 차로변경 연구설계 승인.
P6/P7의 다음 개발 단계 및 Participant readiness 차단은 유지한다.

## 6. 전체 시간 운영 명령 (실행 전 미확정 값 확인)

개발 검증 assignment의 M2 NOA_L2 행을 포함해 검증한다. 기존 배정을 덮어쓰지 않는다.
route/시간창이 미확정이면 임의 숫자를 넣지 말고 실행을 보류한다.

```powershell
Set-Location C:\dev\carla
$assignment = Read-Host "기존 개발 검증 assignment CSV 경로"
$participant = Read-Host "해당 participant_id (M2 NOA_L2 행 확인)"
$spawn = [int](Read-Host "검토된 공통 spawn index")
$tmPort = [int](Read-Host "전용 기본 설정 TM 포트")
$manifest = Read-Host "기존 검토된 route manifest 경로"
$route = Read-Host "assignment와 일치하는 route ID"
$navigation = [double](Read-Host "확정된 안내 거리(m)")
$response = [double](Read-Host "확정된 응답 시간(s)")
$lateral = [double](Read-Host "확정된 기존 P5 lateral onset(m)")
$m1Open = [double](Read-Host "연구자가 정한 M1 안내 가능 시작(s)")
$m1Close = [double](Read-Host "연구자가 정한 M1 안내 가능 종료(s), 600 이하")
$m2Open = [double](Read-Host "연구자가 정한 M2 안내 가능 시작(s)")
$m2Close = [double](Read-Host "연구자가 정한 M2 안내 가능 종료(s), 780 이하")
$trainingOptions = @()
$trainingManifest = Read-Host "기존 훈련 manifest (미확정이면 Enter: NOT_CONFIGURED)"
if ($trainingManifest) {
    $trainingRoute = Read-Host "동일 spawn에 맞는 훈련 route ID"
    $trainingOptions = @("--training-exit-route-manifest", $trainingManifest,
                         "--training-exit-route-id", $trainingRoute)
}
$run = "P8-P2-$([guid]::NewGuid().ToString('N'))"
& ".\.venv\Scripts\python.exe" -m src.p8.operator_cli `
  --mode dev-rehearsal --participant-id $participant --run-id $run `
  --assignment-file $assignment --ack-dev-live --spawn-index $spawn `
  --traffic-manager-port $tmPort --training-duration 600 `
  --exit-route-manifest $manifest --exit-route-id $route `
  --exit-navigation-trigger-distance-m $navigation `
  --exit-response-timeout-s $response --exit-lateral-onset-threshold-m $lateral `
  --m1-exit-open-s $m1Open --m1-exit-close-s $m1Close `
  --m2-exit-open-s $m2Open --m2-exit-close-s $m2Close `
  --output-root data/p8_operator @trainingOptions
```

`--drive-duration`은 지정하지 않는다. 훈련600 → 외부 보정 안내 → M1600 →
진행자 관리 REST → M2780 → 결과 저장이다. 이 명령은 미완성 차로변경 실행을
활성화하지 않으며, 최종 참가자 실행 승인이 아니다.

## API 근거

- [0.9.16 TM 문서](https://carla.readthedocs.io/en/0.9.16/adv_traffic_manager/): lane-change/속도/비동기 제약.
- [0.9.16 LocalizationStage.cpp](https://github.com/carla-simulator/carla/blob/0.9.16/LibCarla/source/carla/trafficmanager/LocalizationStage.cpp): 강제/자동/route 동작 차이.
- [0.9.16 TrafficLightStage.cpp](https://github.com/carla-simulator/carla/blob/0.9.16/LibCarla/source/carla/trafficmanager/TrafficLightStage.cpp): affecting-light와 ignore 정책.
- [0.9.16 Python API](https://carla.readthedocs.io/en/0.9.16/python_api/): vehicle/light/waypoint 관측 API.
