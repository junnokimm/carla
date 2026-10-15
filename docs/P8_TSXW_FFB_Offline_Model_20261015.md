# TS-XW FFB Offline Model — 2026-10-15

## A. 구현 목적

**최소 Spring/Damper 계산 모델, 독립 Safety Layer, 출력 Protocol 및 Fake 검증을 구현했다.**
현재 P8 실행 경로에는 연결하지 않았으며 실제 FFB Backend도 없다.
모든 명령의 `physical_output_allowed`는 변경 가능한 설정이 아니라 **항상 False인 속성**이다.

이는 향후 구현을 위한 소프트웨어 준비다. P8 실행 시 기존 센터링 힘이 사라지는 문제를
해결하거나, 그 원인을 확정하거나, TS-XW 물리 출력 안전성을 검증한 작업은 아니다.

현재 물리 근거는 사용자 관찰이다:

- 모든 프로그램 종료/Unreal Editor 단독: 힘 유지.
- P8 관리자 화면/Training: 힘 소실.
- P8 완전 종료: 힘 복원.
- 기존 A~F 초기화 진단의 실장치 비교는 아직 미수행.

개발 기준은 현재 Working Tree와 `CARLA_Experiment_Design_v5_Source_of_Truth.md`다.
브랜치 `research/phase-1c-automation`, HEAD
`e1af127bb096beaecec3e12cbd2b5bf18a378785`에서 작업했다.
기존 P5/FPS/TS-XW 진단/동적 오디오 변경을 보존했다.

## B. 기존 코드 분석

### TS-XW 입력과 실제 각도의 구분

`src/p8/tsxw_input.py`는 기존 축0 조향, 축1 브레이크, 축2 가속 및 버튼 매핑을 사용한다.
`TsxwInput.poll()`은 연결·instance ID·유한 축 값을 확인하고, `tsxw_policy.py`로 정규화한다.

`normalize_steering()`은 설정된 left/center/right endpoint와 명시적인 Deadzone을 사용한다.
중심±Deadzone 안은0, 바깥은 좌우의 유효 범위를 각각 선형 환산하여 **[-1, 1]**로 제한한다.
기존 입력 기준상 -1은 보정된 왼쪽 endpoint, +1은 오른쪽 endpoint다.
이 부호가 실제 출력 API의 복원 방향과 일치한다는 뜻은 아니다.

이 값은 실제 핸들 각도나 전륜 조향각이 아니다. 실제 도/라디안 환산에는 회전 범위,
center offset, endpoint, 방향, 기구비, Deadzone 전후 값의 의미에 대한 별도 보정이 필요하다.
TS-XW 물리 회전 범위나 steering-rack 특성을 임의로 대입하지 않았다.

### MANUAL/NoA 및 Driver Override

- `steering_engaged = steering != 0.0`이다.
- 기존 키보드 W/S/A/D와 wheel 병합, pedal endpoint 정규화는 유지된다.
- `AutomationInteractionController.update()`는 NoA에서 brake threshold, steering_engaged,
  버튼 해제 정책을 평가한다. 활성화와 같은 프레임의 조향 개입도 해제 대상이 될 수 있다.
- `ResearchDriverView`의 실제 제어권 판정은 automation의 `MANUAL`/`NOA_ACTIVE`를 사용한다.
  `src/vehicle/driving_mode.py`의 일반 `MANUAL`/`AUTONOMOUS` 표시와 혼동하면 안 된다.
- FFB에 의한 물리 움직임도 기존 `steering_engaged`를 발생시킬 수 있다. 이를 숨기기 위해
  Deadzone/Override 임계값을 변경하지 않았다.

### 기존 관측 재사용 가능성

`ResearchNoAObservationSource.last_snapshot_sample`에 기존 Snapshot 속도(km/h),
관측 host monotonic timestamp, 적용 control이 이미 있다. 동적 오디오의
`CachedVehicleAudioSource`는 동일 frame을 새로 수신한 것처럼 freshness를 갱신하지 않는 예다.
향후 FFB adapter도 기존 관측을 재사용해야 한다.

현재 `TsxwInputSample`에는 별도의 wheel 관측 timestamp가 없다. 향후 adapter는 **기존 poll의
관측 경계에서 원본 host monotonic timestamp를 기록**해야 한다. 기존 조향값을 나중에 전달하면서
그때의 시각을 새 wheel 관측 시각으로 붙이면 안 된다. 추가 장치 polling/RPC는 이번에 만들지 않았다.

### 초기화·종료 및 FFB 출력 상태

- P8 운영 UI `pygame.init()` → 화면 생성 → `OperatorApp.start()`.
- `start()`의 Training 동기 실행이 첫 관리자 페이지 render보다 먼저다.
- DriverView는 display init → TS-XW input init/Open → 주행 화면 생성 순서다.
- 종료는 기존 click close → input close(현재 차량음 close 포함) → `pygame.quit()`.
- 운영 UI 복귀/새 Stage에서는 기존 Pygame·viewer 수명주기를 따른다.
- 실제 `src`에서 SDL Haptic/DirectInput/rumble/FFB Effect 출력 구현은 다시 확인되지 않았다.
  이번의 `ffb_*` 모듈 역시 장치 API를 구현하지 않는다.
- 기존 SDL2.28.4 분석의 exclusive acquisition/reset/autocenter 경로는 입력 Open의 간접 영향
  가능성이다. 실제 backend 선택·호출 성공 및 힘 소실 단계는 여전히 실장치 확인이 필요하다.

동적 사운드 시스템은 `src/scenario/vehicle_audio_*` 및 Mixer 수명주기를 소유한다.
신규 FFB 모듈은 `src/p8/ffb_*`의 순수 계산/계약이므로 Mixer·오디오 채널·초기화와 연결되지 않는다.

## C. FFB Model

`src/p8/ffb_model.py`는 표준 라이브러리만 사용한다.

### 데이터 계약

`FfbObservation`:

- `FfbContext`: session ID, stage ID, actor ID, device generation.
- 정규화 wheel position [-1, 1], 원본 wheel 관측 시각.
- 차량 속도(km/h), 원본 차량 상태 관측 시각.
- canonical mode, connected, session_active, stage_active.

Timestamp는 같은 호스트의 monotonic **초**다. CARLA simulation seconds나 UTC와 섞지 않는다.
재연결은 같은 장치 이름이어도 새로운 device generation을 제공해야 한다.

`FfbParameters`:

- spring: 중심으로 복원하려는 condition effect의 추상 계수.
- damper: 핸들 움직임에 저항하는 condition effect의 추상 계수.
- center: 이번 구현에서는 항상0.

모두 장치 독립적이다. **Nm, 실제 motor torque, DirectInput gain, SDL coefficient가 아니다.**
Spring 계수에 현재 변위를 곱해 토크를 계산하지 않는다. Damper도 핸들 위치 크기를
움직임 속도라고 취급하지 않는다. 향후 condition effect의 실제 힘 계산/평가는 검증된
장치 API가 수행해야 한다. 중앙에서도 양수 계수가 존재할 수 있으나 토크가 존재한다는 주장은 아니다.

### 최소 속도 의존 곡선

`blend = min(speed_kmh / reference_speed_kmh, 1)`를 이용한 두 endpoint 선형 보간이다.
기본 **Offline 예시 값**:

| 항목 | 정차 | 기준100km/h 이상 |
|---|---:|---:|
| Spring | 0.060 | 0.040 |
| Damper | 0.025 | 0.050 |

Spring이 속도와 함께 반드시 강해진다는 전제를 두지 않았다. 각 endpoint와 기준 속도는
`FfbCurve`에서 설정할 수 있으며, 반대 추세도 테스트했다. 기준 속도 이상의 포화는 파라미터
계산일 뿐 차량 속도 제한이 아니다.

위 숫자는 코드 검증용 예시이며 **TS-XW 실물 초기 설정이나 연구 조건의 승인값이 아니다.**
모델은 모드별 곡선을 만들지 않는다. NoA용 강도는 확정하지 않았다.
타이어 self-aligning torque, 노면/서스펜션/랙 모델, 충돌 진동, 물리 핸들의 NoA 각도 추종은 없다.

## D. Safety Layer

`src/p8/ffb_safety.py`는 명시적으로 전달된 `now`만 사용한다. 내부 시계 읽기·sleep·thread가 없다.
입력/목표/설정 유효성, freshness, identity와 모드, 강도·변화율을 독립적으로 검사한다.

### 상태·요청

| 상태 | 의미 |
|---|---|
| `DISABLED` | 기본 상태/정상 비활성, 평가 명령0 |
| `OFFLINE_EVALUATING` | 명시적 요청 이후 Fake 수치 평가만 허용 |
| `FAULT` | 오류가 latch된 비활성 상태 |
| `CLOSED` | 종료된 owner, 재활성화 불가 |

요청은 HOLD, ENABLE_OFFLINE_EVALUATION, DISABLE, ACKNOWLEDGE_FAULT다.
기본 HOLD만으로 평가가 시작되지 않는다. ENABLE은 P8 옵션이나 물리 활성화 버튼이 아니다.

모든 `FfbCommand`는 `physical_output_allowed=False`다. `simulation_allowed`는
OFFLINE_EVALUATING에서만True이며, **물리 장치 사용 승인으로 해석해서는 안 된다.**

### 수치 제한과 시간

기본 Offline 예시 `FfbLimits`:

- Spring/Damper 상한 각각0.10.
- 상승0.10/s, 하강0.20/s.
- wheel 최대 age0.15s, 차량 최대 age0.50s.
- 평가 중 update 간격0.25s 초과는 UPDATE_GAP Fault.

설정은 유한·유효 범위를 확인한다. 이 값들 또한 실물 검증 전 확정된 안전 한계가 아니다.
현재 MANUAL loop cadence와 실제 지연을 보고 향후 검토해야 하며, 이를 맞추려고 기존 polling이나
FPS 경로를 변경하지 않았다.

최초 ENABLE은 계수0에서 시작한다. 이후 실제 dt에 따라 각 계수를 독립적으로 제한한다.
큰 update gap 후 한 번에 높은 힘으로 뛰어오르는 대신 Fault로 전환한다.

정상 DISABLE 시:

- 전달 가능한 `parameters`는 즉시0, `simulation_allowed=False`.
- `disable_target`은 항상0.
- 이전 계수가 하강률에 따라 줄어드는 값은 **`decay_preview`에만** 보관한다.
- 이 preview는 Fake apply나 실제 출력의 허가가 아니다. Dispatcher는 disable만 호출한다.

입력 상실/Fault/identity 변경은 preview까지0으로 무효화한다.
즉, 일반 평가 중 변화율 제한과 긴급 차단 명령의0 전환을 구분한다.
소프트웨어의0/disable 명령이 실제 모터를 즉시 정지시킨다는 보장은 없다.

### 입력·Fault 처리

- wheel 범위 밖, 음수 속도, NaN/Inf, bool을 숫자로 전달, 잘못된 타입은 차단.
- Timestamp 누락/NaN/음수/미래/오래됨, clock 역행, 같은 context의 관측 시각 역행은 Fault.
- 전체 observation/target 누락은 비활성화하고 재평가 요청을 요구한다.
- 원래 관측 timestamp를 update 시각으로 덮지 않는다. 같은 관측은 시간이 흐르면 stale이 된다.
- 연결 끊김은 Fault. 재연결/정상 입력/ENABLE만으로 Fault를 해제하지 않는다.
- 유효한 연결·관측·활성 Session/Stage에서 ACKNOWLEDGE_FAULT → DISABLED.
  이후 별도 ENABLE 요청이 있어야0부터 재평가한다.
- 모델/Backend 오류도 Fault와 disable로 처리한다. Fault reason은 최초 원인을 latch한다.
- session/stage/actor/device generation 변경은 기존 계수와 평가 권한을 무효화한다.
  변경을 발견한 호출에 ENABLE이 함께 있어도 허용하지 않는다.
- MANUAL↔NOA_ACTIVE 양방향 전환도 이전 평가 권한을 무효화한다.
- NoA에서는 `NOA_POLICY_UNAPPROVED`로 평가 apply를 차단한다. 실제 NoA UX 정책을 정한 것이 아니다.
- inactive Session 또는 inactive Stage는0 출력 상태이며 활성 복귀만으로 평가를 재개하지 않는다.
- center≠0인 목표는 INVALID_TARGET. 자동 물리 조향 경로를 열지 않는다.

### 유효기간·오래된 명령

명령에는 sequence, context, issued_at, valid_until이 있다. 유효기간은 wheel/vehicle freshness와
update gap 한계 중 가장 이른 시점으로 제한한다.
Fake 수신 검증은 identity, sequence 역행/중복, expiry, 유한 계수·상한·center를 확인한다.
disable도 sequence가 있는 명령을 받으며 이전에 계산됐지만 늦게 도착한 명령을 무효화한다.
향후 Backend도 disable 실패 여부와 무관하게 이전 sequence를 재허용해서는 안 된다.

이 모델은 호출이 없으면 스스로 실행되는 watchdog이 아니다.
실제 장치의 Effect duration, 명령 TTL 집행, 프로세스 강제 종료 대응은 별도 구현·검증 사항이다.

## E. Architecture

```text
Future adapter (이번에는 미구현/미주입)
    기존 wheel poll + 기존 차량 snapshot + 원본 timestamps/identity
        ↓
ffb_model.calculate_parameters
        ↓
ffb_safety.FfbSafety
        ↓
ffb_protocol.OfflineFfbDispatcher / FfbOutput Protocol
        ↓
tests/test_ffb_protocol.py의 FakeOutput (기록만 수행)
```

`FfbOutput` 계약은 apply, disable, record_fault, close다.
이번 `apply`는 Fake 기록 용도다. 향후 물리 Backend는 현재 명령의
`physical_output_allowed=False`를 반드시 거부해야 한다.
향후 물리 허용 계약은 별도 승인·설계·하드웨어 검증이 필요하다.

Dispatcher는 오류 발생 시 fault 기록이 실패하더라도 disable을 시도한다.
종료 시 disable 실패와 무관하게 close를 시도하며 원래 예외를 덮지 않는다.
보관 진단은 마지막 Backend 오류 문자열(최대256자)과 실패 count뿐이다.
테스트 Fake의 event trace는 deque(maxlen=16)으로 제한한다. 연구 CSV/Manifest 연결은 없다.

### 향후 통합 후보 — 이번에는 실행 경로 변경 없음

1. `ResearchDriverView`의 기존 TS-XW poll 결과를 새 관측 루프 없이 adapter에 전달.
2. 기존 `ResearchNoAObservationSource.last_snapshot_sample`에서 속도와 원본 시각 재사용.
3. automation control_mode를 canonical MANUAL/NOA_ACTIVE로 전달.
4. Session/Stage/actor/device generation 경계에서 명령 무효화 및 owner 교체.
5. 출력 장치 handle 소유권·thread affinity를 검증한 뒤 기존 입력 close 및 SDL quit 경계와 통합.
   물리 Effect stop/release는 실제 Backend 계약에 맞게 별도 순서를 확정해야 한다.

P8 CLI, operator config, Research View/Runner, 차량음/방향지시등은 신규 모듈을 import하거나
객체를 생성하지 않는다. FFB 전용 주행 프로그램, 고주기 polling, thread/process, CARLA RPC는 없다.

## F. Changes made

추가한 저장소 파일은7개다. 기존 파일 수정은 없다.

| 신규 파일 | 역할 |
|---|---|
| `src/p8/ffb_model.py` | 입력/context/추상 계수, 실험 곡선과 순수 모델 |
| `src/p8/ffb_safety.py` | 유효성·freshness·강도/slew·상태 머신·비물리 명령 |
| `src/p8/ffb_protocol.py` | 출력 Protocol, Fake 수신 검증, Offline dispatch/오류 종료 |
| `tests/test_ffb_model.py` | 모델 수치·범위·곡선 검증 |
| `tests/test_ffb_safety.py` | 상태 전환·오류·상한·시간·identity 검증 |
| `tests/test_ffb_protocol.py` | bounded Fake·Backend 실패·replay·import 격리 검증 |
| 본 문서 | 구현·검증·후속 물리 계획 |

작업 전 해시와 비교해 **기존 소스/테스트/도구/문서483개 모두 변경/누락 없음**을 확인했다.
따라서 TS-XW 입력/페달/버튼, Driver Override, MANUAL checked batch, TM, P5,
Dynamic Vehicle Audio/Turn Signal Audio, 600/600/780초/F8, SessionClock,
Tobii/P6/P7 및 Runtime Attestation의 기존 코드가 그대로 유지된다.

Phase2의 비교 Run2개 원본 CSV/Manifest6개도 기존 SHA-256과 일치한다.
실험 데이터/로그를 수정·삭제하지 않았다. `.omo`와 `src/error_notes.py`는 편집하지 않았다.
branch/HEAD 변경, checkout/switch/pull/merge/reset/rebase/stash/commit/push는 수행하지 않았다.

## G. Verification results

### 테스트

| 범위 | 결과 |
|---|---|
| 신규 FFB 모델/Safety/Protocol | **72개 통과** |
| 신규 FFB + 기존 순수 Audio Model + Fake TS-XW 초기화 진단 | **116 passed**, 0.37초 |
| 전체 Ruff check | 통과 |
| 신규 Python6개 format check | 통과 |
| git diff --check | 통과 |

선택한 테스트는 순수 함수 또는 Fake만 사용한다. 실행 후 `sys.modules`에
`pygame`, `carla`, `sdl2`가 없음을 assertion으로 확인했다.
신규 모듈의 import AST를 표준 라이브러리/서로의 모듈로 제한해 검사하고,
장치 관련 import를 차단한 상태에서 각 파일을 다시 실행하는 테스트도 통과했다.
기존 src에서 새 FFB 모듈을 import하는 위치가 없는지도 검사했다.

핵심 검증:

- 정차/속도 변화/반대 곡선, 좌·우 endpoint·중심, 독립 Spring/Damper 계수.
- 속도·조향 범위와 NaN/Inf/타입 오류, Timestamp 누락·미래·stale·역행.
- 기본 비활성, 첫 평가0 시작, 시간 기반 상승·하강, 독립 강도 상한.
- Fault latch, ACK 후 별도 ENABLE 필요, 재연결 자동 복구 금지.
- Session/Stage/actor/device generation 및 모드 경계 무효화.
- inactive Stage가 active Session의 권한을 이어받지 않음.
- normal disable의0 목표와 비허용 decay preview 구분.
- Fake apply/disable/fault/close, Backend 오류 시 stop 시도 및 Cleanup 예외 보존.
- 만료/재생된/늦게 도착한/다른 context/위조 계수 명령 거부.
- seed 고정1000-step 조합에서 유한·범위·비물리 출력 불변조건.

실제 Pygame 초기화를 포함하는 전체 pytest는 이번에 다시 실행하지 않았다.
신규 코드가 런타임에 연결되지 않고 기존483개 파일이 byte 단위로 동일하므로,
이번 검증은 순수/Fake 선택 범위로 한정했다. **선택 범위의 신규 회귀 실패는0**이다.
앞선 Audio 작업의 전체 guarded 결과1864 passed/8 failed/1 skipped는 과거 결과이며,
이번 전체 결과로 재표시하지 않는다. 기존7개 실패 및 dummy 화면 차이에 따른1개는 이번에 재검증하지 않았다.

재현 명령(저장소 루트, 장치 초기화 없음):

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q tests/test_ffb_model.py tests/test_ffb_safety.py tests/test_ffb_protocol.py tests/test_vehicle_audio_model.py tests/test_tsxw_init_diagnostic.py
```

### Fake 성능

모델→Safety→Dispatcher→Fake 기록을20,000회 계산했다. 입력 sample 준비 시간은 제외했다.
평가 시각은 명시적100Hz 가상 데이터이며, **실제100Hz 장치 polling이나 thread가 아니다.**

- 평균 **0.00705ms**, p95 **0.0075ms**, 최대 **0.0479ms**.
- Fake trace capacity/최종 길이16/16, Backend 오류0.
- 물리 출력 지연: **NOT_MEASURED**.
- `pygame/carla/sdl2` import 없음.

이는 계산 비용 측정이다. 실제 장치 응답·모터 지연·물리 안전성·주행 FPS를 입증하지 않는다.
런타임 파일 I/O, 합성 PCM, 연구용 고빈도 logging은 추가하지 않았다.

증거 파일(원본 연구 자료와 별도):

```text
C:\Users\nexta\AppData\Local\Temp\opencode\p8_ffb_offline_before.json
C:\Users\nexta\AppData\Local\Temp\opencode\p8_ffb_offline_verify.py
C:\Users\nexta\AppData\Local\Temp\opencode\p8_ffb_offline_metrics.json
```

## H. Remaining concerns

1. **FFB 소실 원인:** A~F 실장치 비교 전이며 SDL init/Open/backend/reset 중 경계를 확정하지 못했다.
2. **장치 소유권:** SDL Joystick과 SDL Haptic/DirectInput의 handle 공유·독점 획득·창/스레드
   수명주기 및 실패 반환값을 실제 환경에서 확인해야 한다.
3. **단위/방향:** 추상0~1 계수를 드라이버 coefficient/gain에 곧바로 대입하거나 Nm로 표시할 수 없다.
   spring center, 좌우 부호, deadband/saturation, effect axis도 검증해야 한다.
4. **NoA 간섭:** 복원력에 의한 움직임이 Driver Override로 인식될 수 있다.
   NoA용 FFB 강도·허용 상태·UX는 미확정이며 기존 Deadzone/해제 임계값을 임의로 변경해서는 안 된다.
5. **종료 안전성:** software disable은 실제 motor stop 증거가 아니다. Effect stop/destroy,
   드라이버 Auto Center 복원, USB 단절, 프로세스 강제 종료, 장치 watchdog은 별도 검증이 필요하다.
6. **시간 계약:** 실제 wheel 원본 관측 timestamp와 frame 식별자를 adapter에서 보존해야 한다.
   낮은 FPS에서 신선도·watchdog 조건을 만족하는지도 미확인이다.
7. **연결 세대:** backend 재사용 시 sequence/context watermark를 초기화하는 조건과 새
   device generation 발급을 명확히 해야 한다. 이번에는 실제 adapter가 없다.
8. **Fake의 한계:** 수치 제한이 실제 TS-XW 힘의 상한이나 물리 안전을 보장하지 않는다.
   Python Protocol은 인증·샌드박스가 아니며 임의의 외부 구현체의 행동을 막는 장치도 아니다.

## I. Next Physical Validation

후속 작업은 연구실에서 사용자가 관찰할 수 있는 상태와 명시적 별도 승인 후 진행한다.
이번에는 아래 단계를 자동 실행하지 않았다.

1. 기존 `tools/diagnose_tsxw_init.py`의 A~F를 독립 프로세스별로 수동 실행.
   초기화/Open만으로 기존 힘이 변할 수 있다는 경고를 확인하고 force 유지/소실/복원을 기록.
2. P8 관리자 화면과 Training의 실제 초기화 순서를 포함해 **힘 소실 시점 확정**.
3. SDL/DirectInput backend, 장치 소유권, 지원 Effect/axis/방향/정지 API 검증.
   Haptic Open 자체도 순수 읽기 전용이라고 취급하지 않는다.
4. **별도 승인 후에만** 낮은 강도의 Spring/Damper 실물 검증.
   이 문서의0.06/0.10 같은 수치를 검증된 실제 초기 출력값으로 사용하지 않는다.
5. 입력 지연/NoA 해제 간섭, 비활성·재연결·Stage 변경·종료·출력 실패 처리 확인.
   움직이는 중심/NoA 물리 각도 추종은 별도 범위이며 이번 모델에 포함하지 않는다.
6. 검증된 backend와 연구용 모드 정책이 확정된 뒤 기존 단일 P8에 adapter를 통합.
   기존 observation/polling, 차량음, checked batch, SessionClock 및 attestation을 보존.
7. 최종 주행 조작감·모드 전환·600/600/780초/F8·종료 복원·FPS와 실제 출력 지연을 측정.

**완료 범위: 비물리 모델·소프트웨어 Safety·Fake 계약 검증. 실물 FFB 기능은 아직 구현하지 않았다.**
