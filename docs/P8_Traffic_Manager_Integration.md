# P8 Traffic Manager 전환: 1단계 운영 계약

> Current implementation and patched-environment command: [P8_Patched_TM_LaneChange.md](P8_Patched_TM_LaneChange.md).
> The stage-one/phase-two BLOCKED descriptions below are historical, not the current execution contract.

> 현재 Phase2 계약은 [P8_Phase2_P5_LaneChange_TrafficLights.md](P8_Phase2_P5_LaneChange_TrafficLights.md)를 따른다.
> 일반 Z/X 요청과 M1/M2 명시적 안내 시간창, 차량별 신호 정책·관측이 추가됐다.
> Z는 좌측 요청, P5 선택적 거부는 R이다. 패치된 TM 단발 실행은
> EMPTY_REHEARSAL_ONLY 조건에서 코드 연결됐고 물리 동작은 UNVERIFIED다.
> 아래 1단계 검증 기록은 당시 결과이며 Phase2의 읽기 전용 지도 조회와 구분한다.

## 완료 범위

최종 운영 진입점은 `python -m src.p8.operator_cli` 하나다. 이 경로의 NoA는
Custom PI/Lateral Controller가 아닌 CARLA Traffic Manager(TM)를 등록한다.
독립 Research NoA 진단 프로그램의 Custom Controller는 보존한다.

코드 연결 및 Fake CARLA Offline 검증은 실제 물리 주행 검증과 다르다.
이번 작업에서는 CARLA 서버에 접속하거나 실제 주행을 시작하지 않았다.
**차로변경 실행 경로는 EMPTY_REHEARSAL_ONLY로 CONNECTED, 실제 TM 주행은
UNVERIFIED, Participant 운영은 BLOCKED**다. 실행 전 로드된 PYD 해시와 Windows
listener PID 단독 소유권을 확인하며, 어느 하나라도 알 수 없으면 실패한다.

## 연구 문서와 명시적 변경

`CARLA_Experiment_Design_v5_Source_of_Truth.md` 원본은 수정하지 않는다.
이번 연구개발 담당자의 명시적 변경은 다음과 같다.

1. 활성화 시 속도 캡처/고정 대신 TM 기본 속도 제어를 사용한다.
2. P8 NoA 자체 110 km/h 상한과 그 초과에 따른 종료를 제거한다.
3. `DriverInput.steering_engaged`도 NoA 해제 조건이다.
4. 고속도로 진출 확인은 방향지시등 조작이며 별도 C/R 버튼을 요구하지 않는다.

추가 연구 충돌/미확정 사항:

- V5 §5.1은 조건 간 동일 목표속도를 요구한다. TM의 제한속도 기반 기본 동작과
  MANUAL 실제 속도가 동일한 연구 조건이라는 뜻은 아니다. 최종 속도 조건의
  동등성과 해석은 연구 책임자의 확인이 필요하며 임의 목표속도를 설정하지 않았다.
- V5 §6.1/§7.6의 확인 후 시스템 차로변경은 현재 BLOCKED다. 요청 기록만으로
  V5 기능 완료를 주장하지 않는다.
- V5의 3차로 조건과 기존 개발 P5 보조 4차로 경로 차이, M1 후반/M2 중후반 진출
  위치, 재합류 및 전체 주행 경로는 기존 미확정 사항이다. Route/Fork Fix는 유지한다.
- V5의 2분 휴식은 진행자 권고/관리로 유지하며 기존 P8처럼 강제 대기는 없다.
- TM 기본 분기 선택은 임의 분기를 포함한다. 자동 차로변경을 꺼도 모든 분기에서
  연구 경로 유지가 보장되는 것은 아니다. 최종 전체 경로 승인은 아직 없다.

## Single Control Authority

| 시점 | 소프트웨어 제어권 |
|---|---|
| Spawn/Gear 준비 | 기존 Runner의 준비 제어, TM 미등록 |
| MANUAL | 기존 ResearchLiveDriverView 수동 입력 |
| NOA_ACTIVE | `vehicle.set_autopilot(True, tm.get_port())`로 등록된 TM |
| 브레이크/조향/N 해제 | TM 등록 해제 반환 후 MANUAL 상태와 운전자 입력 적용 |
| 종료/예외 | 등록 해제 시도, 기존 종료 제동/오류 기록, 센서 정리, 소유 차량 파괴 |

`TrafficManagerScheduler.update()`는 속도/제한속도만 관측한다. 활성 상태에서
수동 writer를 억제하며 `apply_control()`, Custom PI, Custom Lateral을 실행하지 않는다.
TM backend에는 Custom `step()`이나 조향 override 구현이 없다.
등록 RPC가 예외를 반환해도 서버에서 등록됐을 가능성을 고려해 해제 rollback을
시도한다. 최종 정리도 canonical 상태만 믿지 않고 TM 해제를 시도한다.
정리 실패 시 원래 예외와 보조 오류를 보존한다. 실패한 RPC 뒤 물리 제어권은
UNKNOWN이며 정상 전환으로 간주하지 않는다.

같은 포트를 backend와 DriverView 초기화에 전달한다. 단계마다 새 차량과 뷰를
소유하고 정리한다. 공유 TM 서버를 전역 종료하거나 다른 차량을 제거하지 않는다.
동일 포트를 다른 프로그램이 사용하면서 전역 TM 속도/동기 설정을 변경하면
기본값 보장은 깨진다. 운용 전 해당 포트의 독점적·기본 설정 사용을 확인해야 한다.

### 시간 관리와 물리 검증 경계

기존 P8은 Pygame 루프에서 World를 관측하는 비동기 방식이며 World tick 주인이 아니다.
World 설정이나 TM 전역 synchronous 설정을 변경하지 않는다. synchronous World는
차량 생성 전에 거부한다. TM 동기 실행을 지원한 것처럼 표시하지 않는다.

CARLA 0.9.16 공식 문서는 TM의 동기 실행을 권장하며, 비동기 실행의 예측 불가능한
동작을 경고한다. 비동기가 필요한 경우 최소 20~30 simulation FPS를 권고한다.
이는 본 프로젝트의 새 제어 임계값이 아니라 upstream 운용 제약이다.
**Offline 테스트는 해제 RPC → 수동 명령 순서만 증명한다.** 이미 전송된 TM batch가
물리 프레임에서 운전자 명령을 덮어쓰지 않는다는 보장은 실제 서버에서 검증하지 않았다.
정지 상태 출발, M2 초기 출발, 정리 후 잔여 Actor도 물리 검증 대상이다.

## 속도/입력

- TM 기본 목표는 공식 문서상 현재 제한속도의 70%다. 교통·신호·곡률 등으로
  실제 속도는 달라질 수 있다. `set_desired_speed`와 speed difference setter를 호출하지 않는다.
- Town04의 제한속도를 한 숫자로 가정하지 않는다. 운영 중
  `traffic_manager_speed_limit_observation`에 현재 `get_speed_limit()` 관측값,
  actual speed, canonical mode/authority를 기록한다. 실제 Town04 서버 값은 미측정이다.
- `--noa-max-throttle`, 고정 순항속도 옵션은 최종 operator에 없다.
  공유 control config의 기존 brake/steering 제한은 수동 입력·준비·종료에만 사용한다.
  남아 있는 PI gain 필드는 TM에 전달되지 않는다.
- NaN/Inf 속도와 비유한 제한속도는 기술 오류로 종료한다. 충돌·장비·로그 오류 처리와
  기존 P7 종료/구조 계약은 삭제하지 않았다. P7이 P8에 연결됐다는 뜻은 아니다.
- N은 매번 기존 geometry/centering/straddling/lane-change/availability Gate를 통과해야 한다.
  횟수 제한은 없다. 브레이크 기준은 기존 `>=0.05`, 조향은 기존 `steering_engaged`다.
  새 조향 노이즈 임계값은 없다. 물리 핸들의 engaged 판정/노이즈는 아직 미검증이다.
- 해제 사유는 `DRIVER_BRAKE`, `DRIVER_STEERING`, `DRIVER_BUTTON`으로 구분한다.
  동시 입력 우선순위는 brake → steering → button이다.
- 훈련/M1/M2 MANUAL은 초기 OFF, M2 NOA_L2는 준비 후 Gate를 통해 TM 초기 ON이다.
  Gate 실패 시 M2 조건을 MANUAL로 대체하지 않고 시작 실패한다.
  M2 MANUAL에서 N 성공 시 기존 protocol deviation을 기록하며 배정을 바꾸지 않는다.

## 방향지시등과 P5

`auto_lane_change(False)`, 좌/우 random lane-change percentage 0,
`update_vehicle_lights(False)`로 무확인 차로변경과 TM 방향지시등 덮어쓰기를 막는다.
Research DriverView의 legacy driving mode는 MANUAL에 머물고 canonical NoA 상태를
별도로 표시하므로 Z/X 토글과 오디오는 NoA 활성 상태에서도 작동한다. P 키는 차단된다.

현재 보존된 P5 경로는 우측 진출이다. 실제 표시된 추천과 응답 window 안에서
새 X 입력이 우측 방향지시등을 OFF→ON으로 바꾸는 요청이면 확인으로 기록한다.
켜진 X를 끄는 입력, repeat/held 입력, 추천 전 입력은 확인으로 쓰지 않는다.
Z는 좌측 신호 토글이며 기존 우측 진출 추천의 거부 입력으로 연결된다.
좌측 일반 차로변경 지원으로 확대하지 않았다. 이 입력 의미는 운영 안내에 포함해야 한다.
키 요청 시각과 차량 조명/차로 관측 시각은 별개이며 물리 조명 적용 증거로 혼동하지 않는다.

확인 후에는 `lc_decision=CONFIRM`, `lc_finished`의
`termination_reason=ASSISTED_EXECUTION_BLOCKED`, `outcome=UNVERIFIED`를 기록한다.
P5 Custom route reference를 arm하지 않고 TM 강제 차로변경을 호출하지 않는다.
실제 차로변경 onset/crossing/EXIT/MISSED 관측은 계속 보존한다.
BLOCKED 지원 상태와 실제 관측된 physical outcome은 별도 필드다.
요청·차로변경 실행·물리 완료를 동일한 사건으로 기록하지 않는다.

API 판단:

- `auto_lane_change(True)`: 운전자 확인으로 제한된 단발 요청 API가 아니다.
- `force_lane_change()`: 공식 문서가 충돌 무시를 명시한다. 사용하지 않는다.
- `set_path()`/`set_route()`: 경로 선택이지 운전자 요청에 대한 안전한 차로변경 계약이 아니다.
- 따라서 현재는 요청/거부/차단 및 관측 연결만 완료했다. 차로별 가용 gap 평가나
  별도 lateral controller를 새로 만들어 안전하다고 주장하지 않는다.

## P7 특정 Actor 예외: 3단계에서 검증

`tm.collision_detection(ego, obstacle, False)`는 특정 reference→other actor 쌍의
TM 충돌 판단 예외 API다. 물리 collision 자체를 끄는 API가 아니며, 반대 방향까지
자동으로 예외 처리한다는 뜻도 아니다. 이번에는 호출하지 않는다.

현재 P7은 설정된 obstacle blueprint로 cargo를 생성하고 physics를 끈 개발용
kinematic 경로를 사용한다(`src/p7/carla_actors.py`). Actor 타입을 최종 실험용으로
확정하지 않았다. TM ALSM은 주로 vehicle/walker를 추적하므로 static prop 등이
해당 collision pipeline에 들어오는지 실제 유형과 서버에서 확인해야 한다.
예외 API 호출이 가능하다는 사실만으로 최종 장애물에 대한 자동 제동 부재를
증명할 수 없다. 일반 collision avoidance/신호 대응은 전역 비활성화하지 않았다.
P7 생성·발동·관측·종료/구조 구현은 이번에 변경하지 않았다.

## 연구 로그와 지표

공통 ID, SessionClock, Logger, stage/module, 기존 CSV 열을 유지한다.
`auto_request`, `auto_state`, failure/disengagement, P5 사건과 raw telemetry를 보존한다.
`control_authority`는 초기 상태 관측이며 이후 권한은 canonical `auto_state`와
TM speed-limit observation의 mode/authority로 재구성한다.
TM target은 null, Custom commands는 빈 집합이다. get_control은 cached readback이지
실제 actuator application의 독립 증거가 아니다. TM 보고서에 가짜 PI/0 제어값을 넣지 않는다.

기존 집계 의미는 유지했다: M2 초기 자동 engagement도 `activation_count`에 포함된다.
이 수치를 참가자의 자발적 버튼 ON 횟수로 해석하면 안 된다. `initial_mode` 및
원본 `auto_request`/`auto_state`와 함께 분석해야 한다. 참가자 ON만의 지표 정의 변경은
이번 TM 교체와 별개로 합의가 필요하다. 원본 로그는 수정하지 않는다.

## 실제 전체 시간 검증 명령 (이번 작업에서 실행하지 않음)

미확정 값을 임의로 채우지 않는다. 기존 assignment에서 **M2=NOA_L2인 개발 검증 행**을
사용하고 MANUAL 행도 별도 정상 배정으로 검증한다. 조건을 CLI로 덮어쓰지 않는다.
실제 참가자는 readiness 차단을 해제하지 않는다. 아래 dev-rehearsal은 같은 P8 프로그램의
실행 권한 모드이며 별도 주행 프로그램/저속 프로파일이 아니다.

사전 확인: Town04, 승인/검토된 common spawn, 기존 route/manifest와 assignment 일치,
비동기 World, 선택한 TM 포트의 전용 기본 설정, 클라이언트0.9.16/서버294096e 호환성.
route/행/행동 파라미터가 미확정이면 실행을 보류한다. 기존 개발 manifest를 자동 선택하지 않는다.

```powershell
Set-Location C:\dev\carla
$assignment = Read-Host "기존 개발 검증 assignment CSV 경로 (M2 NOA_L2 행 확인)"
$participant = Read-Host "해당 행의 participant_id"
$spawn = [int](Read-Host "검토된 공통 spawn index")
$tmPort = [int](Read-Host "전용 기본 설정 Traffic Manager 포트")
$manifest = Read-Host "검토된 기존 module route manifest 경로"
$route = Read-Host "assignment와 일치하는 module route ID"
$navigation = [double](Read-Host "확정된 navigation trigger 거리(m)")
$response = [double](Read-Host "확정된 응답 window(s)")
$lateral = [double](Read-Host "확정된 기존 lateral onset threshold(m)")
$m1Open = [double](Read-Host "연구자가 정한 M1 안내 시작(s)")
$m1Close = [double](Read-Host "연구자가 정한 M1 안내 종료(s), 600 이하")
$m2Open = [double](Read-Host "연구자가 정한 M2 안내 시작(s)")
$m2Close = [double](Read-Host "연구자가 정한 M2 안내 종료(s), 780 이하")
$trainingOptions = @()
$trainingManifest = Read-Host "기존 훈련 route manifest (미확정이면 Enter: NOT_CONFIGURED)"
if ($trainingManifest) {
    $trainingRoute = Read-Host "동일 spawn에 맞는 훈련 route ID"
    $trainingOptions = @("--training-exit-route-manifest", $trainingManifest,
                         "--training-exit-route-id", $trainingRoute)
}
$run = "P8-TM-$([guid]::NewGuid().ToString('N'))"
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

`--drive-duration` 미지정으로 M1 600초/M2 780초다. 훈련 600초/F8 조기 종료,
외부 Tobii 안내, M1, 진행자 관리 REST, M2, 부분 결과 저장 흐름을 유지한다.
각 단계에서 N/브레이크/A·D/재활성화, Z/X, V, cleanup을 관찰한다.
M2 정지 출발이 실패하거나 제어 덮어쓰기가 나타나면 BLOCKED로 기록하고 중단한다.
지원 차로변경은 현재 의도적으로 차단되므로 물리 완료를 기대하지 않는다.
1단계 실제 통합 검증 이후에만 2단계 P5 경로 작업으로 진행한다.

## API 근거

- [CARLA 0.9.16 Traffic Manager](https://github.com/carla-simulator/carla/blob/0.9.16/Docs/adv_traffic_manager.md): 기본 속도, 분기, 조명, 차로변경, 비동기 제약.
- [0.9.16 Parameters.cpp](https://github.com/carla-simulator/carla/blob/0.9.16/LibCarla/source/carla/trafficmanager/Parameters.cpp): 속도 및 actor-pair collision 정책.
- [0.9.16 TrafficManagerLocal.cpp](https://github.com/carla-simulator/carla/blob/0.9.16/LibCarla/source/carla/trafficmanager/TrafficManagerLocal.cpp): 등록/해제와 batch 실행.
- 로컬 Python package metadata: `carla==0.9.16`; 사용 TM 메서드의 존재와
  `set_autopilot(enabled, tm_port)` 바인딩은 서버 연결 없이 확인했다.

## 이번 작업의 Offline 검증 결과

- 시작 기준: HEAD `675f21edf2326581c059cf5b99cde8a894bfffa6`와 당시 Working Tree.
  기존 수정/미추적 P8 파일은 보존했으며 다른 브랜치/이력 구현을 가져오지 않았다.
- 변경 전 전체 pytest: **1332 passed**.
- 최종 전체 `python -m pytest -q`: **1353 passed, 240.37 s**.
- 전체 `python -m ruff check .`: **All checks passed**.
- `git diff --check`: 성공. 기존 LF/CRLF 변환 경고는 있으나 whitespace 오류는 없다.
  이 명령은 Git 미추적 파일을 검사하지 않는다는 한계가 있다.
- Python LSP: basedpyright 미설치 및 기존 설치 거절 상태로 실행 불가.
  정적 타입 검사 통과를 주장하지 않는다.
- `operator_cli --help` 실행: TM 포트 옵션과 제거된 throttle/고정 속도 입력 확인.
  포트0 입력은 UI/서버 접속 전에 exit2로 거부됨.
- 별도 파일/주행 프로그램을 만들지 않은 in-process Fake CARLA 실행에서
  MANUAL → Gate 거부 → TM ON → 브레이크 MANUAL → 재활성화 → 조향 MANUAL을 관측했다.
  활성 중 local control write는0, 해제 후 순서는 `autopilot:False` → driver brake였다.
  backend와 실제 Research DriverView 초기화가 모두 지정 포트8123을 사용했다.
- TM 전용 회귀: 브레이크/조향/버튼의 서로 다른 단일 disengagement,
  Gate 재검사 및30회 반복, M1/M2 초기 상태, active Z/X와 자동 조향 readback 분리,
  110초과 허용·비유한 값 거부, 등록 실패 rollback, 종료/차량 파괴, 제한속도 관측 변경,
  실제 완료와 BLOCKED P5 결정 분리, 중복 증거 거부를 확인했다.
- 기존 P5/P6/P7, SessionClock/Logger, F8/REST/600·780초 및 독립 진단 회귀도 전체 suite에 포함된다.
- 실제 Town04 속도/신호/주변차 대응, 정지 출발, 프레임 단위 takeover, 실제 P5/P7 및
  물리 핸들 노이즈는 **UNVERIFIED**다. 참가자 readiness는 **BLOCKED**로 유지된다.
