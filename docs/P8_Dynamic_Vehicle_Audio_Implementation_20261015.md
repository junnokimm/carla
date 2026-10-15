# P8 Dynamic Vehicle Audio 구현 보고서 — 2026-10-15

## 요약

**현재 단일 `python -m src.p8.operator_cli`의 Training/M1/M2에 동작 가능한 동적
차량음을 연결했다.** 실제 적용 차량 상태를 재사용하는 순수 모델, 원본 절차적 PCM 자산,
유한 길이 교차 재생 계층으로 구성한다. 실제 엔진 녹음·현재 RPM 측정·Mercedes 음색 재현은 아니다.

- 동일 속도에서도 적용 throttle/brake에 따라 부하·배음 혼합·음량이 달라진다.
- 속도와 실제 gear를 함께 사용해 `estimated_rpm`을 계산한다. 기어 변화는 완만하게 보간한다.
- 엔진·타이어/노면·풍절음과 기존 방향지시등 클릭을 동시에 재생할 수 있다.
- 주행 루프에는 추가 CARLA 호출, PCM 생성, 파일 읽기, Sound/Channel 객체 생성이 없다.
- 기존 P8 명령에 별도 오디오 옵션을 추가할 필요 없이 운영 Run에서 켜진다.
- 실제 CARLA 접속/주행, 물리 스피커 출력, USB/FFB/드라이버/World/TM 조작은 수행하지 않았다.

**구현·Offline 검증 완료와 실차 수준 청취 품질 달성은 구분한다.** 후자는 미검증이다.

## A. Current Audio Architecture

### 기준과 실제 차량

현재 Working Tree, 브랜치 `research/phase-1c-automation`, HEAD
`e1af127bb096beaecec3e12cbd2b5bf18a378785`를 기준으로 작업했다.
Source of Truth, P8 통합/TM/Patched-TM 문서, FPS Phase1/2와 TS-XW 초기화 진단의
운영·관측·장치 수명주기 계약을 유지한다.

`ResearchNoARunConfig.vehicle_blueprint` 기본값은 **`vehicle.mercedes.coupe_2020`**다.
`src/p8/operator_run_config.py`는 이 값을 덮어쓰지 않고,
`ResearchNoARunner`는 실제로 `config.vehicle_blueprint`를 찾아 hero를 만든다.
따라서 현재 코드의 운영 선택은 이 Blueprint다. 이번에 서버 Actor를 생성/조회해서 확인한 것은 아니다.
실차 엔진 종류·실제 기어비·최종감속비·타이어 둘레와 CARLA asset의 물리 값이 같다는 근거는 없다.

### 기존 관측 재사용

`ResearchNoAObservationSource.get_observation()`:

1. 기존 World Snapshot에서 hero의 velocity/transform을 읽는다.
2. 기존 `hero.get_control()` 결과를 읽는다.
3. waypoint/indicator 등 연구 관측을 수행한다.
4. `last_snapshot_sample`에 speed_kmh, control, 원본 timestamp를 보관한다.

오디오는 4번의 **이미 보관된 값만** 사용한다. 새로운 `get_snapshot`, `get_control`,
`get_velocity`, `get_physics_control`, `get_light_state` 호출을 추가하지 않는다.
Snapshot 속도와 적용 control은 기존 순차 관측이며 완전한 원자적 동시 관측이라고 주장하지 않는다.
CARLA의 `get_control()`은 마지막 tick에 적용된 클라이언트 관측 값이다.
MANUAL의 방금 제출한 명령이 즉시 반영됐다는 보장도 없고, NoA의 throttle을 운전자 페달로 대체하지도 않는다.

속도 단위는 km/h, throttle/brake는0~1, 시각은 원본 Snapshot 획득의 host monotonic seconds다.
같은 World frame이 반복되면 새 조회 시각으로 freshness를 갱신하지 않는다.
actor ID, segment ID, 제어 모드를 함께 전달하며 모드는 음색 선택에 사용하지 않는다.

### 현재 RPM

CARLA0.9.16 공개 Python Vehicle API 및 로컬 native `Vehicle.h`에서 현재 엔진 RPM getter를
확인하지 못했다. `VehiclePhysicsControl.max_rpm`은 **최대 RPM**이며 현재 RPM이 아니다.
`show_debug_telemetry()`는 spectator 시각화 명령이지 현재 구현의 Python RPM 관측 인터페이스가 아니다.
현재 연결은 항상 `measured_rpm=None`, 출력 출처는 `estimated_rpm`이다.

### 기존 Mixer/방향지시등 수명주기

- 운영 UI의 `pygame.init()`이 먼저 실행된다.
- `OperatorApp.start()`에서 첫 관리자 페이지 render보다 먼저 Training이 동기 실행된다.
- `TurnSignalAudio`는 활성 방향지시등에서 Sound를 lazy 생성한다. Mixer가 없으면
  22050Hz/signed16/mono로 초기화한다. `Sound.play()`의 자동 채널 선택을 사용한다.
- DriverView 종료 시 click close → 입력 close → `pygame.quit()` 순서다.
- 운영 UI 복귀 시 Pygame을 다시 초기화한다. 다음 Runner는 새 hero/viewer를 소유한다.
- 새 차량음은 이 기존 Mixer를 재사용하고, 없을 때만 준비 단계에서 초기화를 한 번 시도한다.
  새로운 `pygame.init()`/Joystick/Haptic 호출은 추가하지 않았다.

프로젝트의 WAV/OGG/MP3/FLAC 탐색에서 재사용할 엔진 녹음 자산은 없었다.
기존 오디오는 코드로 생성하는 방향지시등 클릭이다.

## B. Dynamic Sound Model

`src/scenario/vehicle_audio_model.py`는 CARLA/Pygame import가 없는 순수 계산 계층이다.

### 입력·출력 계약

`VehicleAudioState`:

- 속도, 적용 throttle/brake, gear/reverse
- 원본 관측 시각, actor/stage ID, MANUAL/NoA 모드
- 선택적 measured_rpm, engine_running

현재 ignition 센서는 없으므로 `engine_running=True`는 명시적 모델 가정이다.
별도로 실제 engine-off를 측정했다고 표시하지 않는다.

`SoundParameters`:

- rpm와 rpm_source, engine_load/pitch/volume
- idle/acceleration/cruise/deceleration blend
- gear_shift_transition, road_gain, wind_gain, fresh

### 추정 모델

- Idle750RPM, 음향 모델 상한6500RPM.
- 차량 속도 → 가상의 타이어 둘레2.1m에 따른 wheel RPM → 관측 gear에 대응하는 일반 음향
  기어비와 최종감속비3.2를 적용한다. **실차 또는 Blueprint에서 측정한 수치가 아니다.**
- 저속 출발은 throttle 기반 slip/rev 성분을 더한다.
- 중립은 속도 대신 throttle에 따른 무부하 rev 모델을 사용한다.
- reverse는 별도 역방향 음향 기어비를 사용한다. 속도는 크기이며 음수 속도는 invalid다.
- gear 누락/범위 밖/비정수이면 속도 구간별 음향 추정치를 사용한다.
  이 fallback은 실제 변속 관측으로 기록하지 않는다.
- throttle은 부하를 올리고 brake는 부하를 낮춘다. brake만으로 RPM을0으로 만들지 않는다.
- 관측 gear 변화 후 약0.18초 시정수의 transition으로 부하를 일시 완화한다.
- RPM·load·gain·speed는 프레임 수가 아니라 실제 dt를 사용한 지수 보간으로 변화한다.
  RPM 변화율도 상향6500/하향5000RPM/s로 제한한다. 이는 **음향 출력 모델 제한**이다.
- MANUAL↔NoA 변경만으로 모델/위상/채널을 재생성하지 않는다.
- NaN/Inf, 음수 속도, 미래 timestamp, 0.5초보다 오래된 관측은 신규 재생을 허용하지 않는다.
- 속도300km/h acoustic clamp, RPM 상한, gain clamp는 차량/TM 속도 정책을 변경하지 않는다.

동일 속도·기어에서는 가속/순항 RPM이 같을 수 있지만 부하별 음색과 음량은 다르다.
타이어/풍절음은 별도의 속도 기반 gain을 유지한다.

## C. Sound Implementation

### 선택한 방식

Pygame2.6.1의 `Sound`/`Channel`에는 실시간 playback rate/pitch 변경 API가 없다.
이를 가정하는 호출을 만들지 않았다.

`vehicle_audio_assets.py`는 주행 시작 전 다음 bank를 만든다.

- 750~6500RPM의19개 음역
- 음역별 light/high load 배음 조합2종
- 위상8종: 실제 launch 시각에 가까운 위상을 선택해 반복 grain의 위상 상쇄를 줄임
- 노면·풍절음2종
- 총 **306개** signed16 PCM. mono/stereo, 22050/44100/48000Hz 지원

엔진 기본음은 일반적인4기통4행정 firing rhythm 가정으로 RPM/30Hz를 사용한다.
8개 배음, 부하별 배음 감쇠 차이, 실내 저역 중심 spectral coloration을 사용한다.
노면·풍절음은 고정 seed로 만든 서로 다른 대역의 filtered noise다.
단일 sine/beep 또는 속도에 따른 볼륨만의 구현이 아니다.

### Pitch·Gain·Crossfade

현재 RPM의 이웃 음역2개 × light/high2개를 혼합하므로 pitch와 부하 음색이 함께 바뀐다.
단일 oscillator의 완전 연속 pitch bend 대신 **음역 bank 간 연속 가중치 변화**다.
인접 음역 혼합의 beating/chorus감과 합성적 음색은 청취 평가 대상이다.

각 PCM grain은0.64초이며 시작/끝이0에 가까워지는 raised-cosine envelope를 가진다.
0.16초 간격을 목표로 새 grain을 중첩하고, 규칙적인4중 overlap의 envelope 합은1이다.
6개 cohort를 둬 프레임 도착 오차의 여유를 확보한다. 현재 재생 중인 cohort를 강제로 덮지 않는다.

**무한 `loops=-1` 대신, 유한 grain의 반복 중첩으로 지속음**을 만든다.
이는 Python loop 정지 후에도 무한 엔진음이 남지 않게 하기 위한 선택이다.
새 업데이트가 완전히 끊기면 마지막 재생이0.64초 이내에 자체적으로 끝난다.
stale 관측 이후 신규 launch는 없다. stale 판정 전 마지막으로 허용한 grain의 tail은 남을 수 있다.

### Mixer 채널·실패

- 차량음 전용36채널:6 cohort × (엔진4 + road1 + wind1).
- 자동 선택용8채널 이상을 남기므로 기본 Mixer 총 채널은44개가 된다.
- 차량음0~35번을 reserve하고, 기존 `TurnSignalAudio.Sound.play()`는36번 이후를 사용한다.
- 준비 시 기존 해당 채널에 소리가 있으면 차량음만 비활성화한다. 기존 소리를 빼앗지 않는다.
- 현재 P8에는 다른 `set_reserved` 소유자가 없다. 여러 차량음 owner의 동시 준비도 거부한다.
- Mixer 재초기화, 전역 stop/quit, music 채널 조작은 하지 않는다.
- signed16 이외 형식이나 지원하지 않는 sample rate/channel 수는 변환을 추측하지 않고 차량음만 끈다.
- 장치 없음/초기화·Sound 생성·재생 실패는 상태에 기록하고 재시도 루프 없이 차량음만 닫는다.
- 정상 종료는 **입력 close 후** 해당 채널50ms fade → 최대한 완료를 기다림 → stop → reservation 해제다.
  이50ms 대기는 주행 반복문 밖이고 기존 `pygame.quit()` 전에만 수행한다.
  전체 종료 경로 시간에는 추가될 수 있으므로 shutdown timing 실측에 포함해야 한다.
- 예외 종료는 추가 대기 없이 stop을 시도한다. Cleanup 실패를 수집하고 기존 차량/Shutdown 예외를 덮지 않는다.
- 강제 프로세스 종료나 오디오 장치 자체 오류의 무클릭 종료까지 보장하지 않는다.

### 성능 및 수명주기 선택의 이유

| 후보 | 판단 |
|---|---|
| 허가된 multi-RPM 실제 녹음 | 품질 면에서 우선이지만 현재 자산 없음 |
| 런타임 PCM 합성/resampling | 현재 MANUAL loop에 비용·지연을 추가하므로 선택하지 않음 |
| 무한 loop bank | 가벼우나 Python 정지 시 무한 잔류 문제 |
| 별도 native streaming/worker | thread-safety·소유권·종료 검증이 추가로 필요해 이번에는 도입하지 않음 |
| 사전 생성 유한 overlap bank | 이번 선택. 고정 메모리·객체 수, native Mixer의 background mixing 사용 |

Python update는 기존 주행 loop의 호출을 받는다. 모델 dt와 PCM 재생 시간은 wall time 기준이지만,
**새 상태 반영과 grain launch 시각은 loop cadence 영향을 받는다.** 장시간 frame drop에서
grain 사이 음량 요동/감쇠가 생길 수 있다. 0.64초 이상 정지 시 silence는 설계된 동작이다.
새 worker 없이 독립적인 고주기 상태 갱신을 달성했다고 주장하지 않는다.

## D. Audio Assets

### 실제 사용 자산

| 자산 | 출처·권리 상태 | 저장/생성 |
|---|---|---|
| engine light/high ×19RPM ×8phase | 이번 프로젝트에서 새로 작성한 절차적 생성 코드 | `vehicle_audio_assets.py`, 준비 시 PCM 생성 |
| road/wind | 동일, 고정 seed noise와 filter | 동일 |
| turn signal click | 기존 프로젝트 코드 | `turn_signal_audio.py`, 변경 없음 |

외부 녹음 파일 다운로드, 상용 게임 추출, 출처 불명 파일 포함은 없다.
별도 외부 샘플 라이선스를 주장하지 않는다. 신규 코드·생성물의 배포 조건은 프로젝트의
코드 라이선스/권리 정책을 따른다. 특정 Mercedes 원음 또는 측정된 실내 음압 자료가 아니다.

`AudioBank` 및 `bank_provider(rate, channels)`를 분리했다. 향후 허가된 자산은
음역/부하/위상 slot, sample rate, PCM 형식, zero-edge envelope 계약에 맞춰 준비 단계에서
변환한 bank로 교체할 수 있다. 현재는 임의 WAV를 그대로 불러오는 사용자 자산 loader를 만들지 않았다.

### 메모리

cache는 **format1개**로 제한한다. Pygame Sound가 buffer를 복사하므로 raw cache와 native copy를
둘 다 고려해야 한다. Sound306개, playback channel36개, 자동용8개, update 중 증가는 없다.

| 형식 | raw bank bytes | 이번 Offline 생성 시간 |
|---|---:|---:|
| 22050Hz mono | 8,636,544 | 1.821s |
| 22050Hz stereo | 17,273,088 | 1.995s |
| 44100Hz stereo | 34,546,176 | 3.964s |
| 48000Hz mono | 18,800,640 | 3.697s |
| 48000Hz stereo | 37,601,280 | 4.069s |

최대 raw+native PCM은 약75.2MB이며 Python/backend/임시 데이터 오버헤드는 별도다.
시작 준비 비용을 실제 Active driving loop 비용으로 섞지 않는다.

## E. P8 Integration

```text
operator_cli → operator_run_config(vehicle_audio=True)
  → ResearchNoARunner._create_viewer
    → CachedVehicleAudioSource(existing persistence, hero ID, mode)
    → ResearchDriverView
      attach: Mixer/bank/Sound/Channel 준비 (Active drive 이전, 재생 없음)
      기존 post-control 관측
      기존 방향지시등 audio
      차량음 cached source → model → bounded playback
      기존 HUD/camera/display/FPS limiter
      종료: 기존 입력 close → 차량음 fade/stop → 기존 pygame.quit
```

Training/M1/M2 운영 config builder에서 켜진다. 독립 Research 진단 config의 기본은False다.
기존 CLI 명령과 승인된 patched Python/runtime attestation을 그대로 사용한다.
새 주행 프로그램이나 CARLA 접속 진입점은 만들지 않았다.

각 Runner가 새 viewer/owner를 준비한다. 동일 actor/stage에서 모드 전환은 연속성을 유지한다.
동일 viewer 안에서 actor/segment ID가 바뀌는 비정상적인 재사용은 이전 소리를 정지하고 새 owner를
요구한다. P7로 임의 오디오 연결/위험사건 구현을 확장하지 않았다.

최소 diagnostics:

- `vehicle_audio_update` count/mean/max/total/≥100ms를 기존 diagnostics에 추가.
- camera의 기존 `audio` span 안에 포함되는 중첩 span이므로 합산하지 않는다.
- pre-drive 합성과 exit fade는 이 span에서 제외한다.
- Viewer run 종료 시 기존 event writer 경로로 `vehicle_audio_summary`를1회 enqueue한다.
  asset/rpm 출처, update/fresh/launch 수, busy skip, Mixer format, 출력·Cleanup 오류를 남긴다.
- 연구 Telemetry CSV schema/빈도, SessionClock, 기존 필수 이벤트는 변경하지 않는다.
- summary 저장 실패는 오디오 상태에 기록하며 기존 주행/Shutdown 예외를 대체하지 않는다.

## F. Changes made

| 파일 | 변경 |
|---|---|
| `src/scenario/vehicle_audio_model.py` | 상태·순수 추정 모델·보간·stale 검증 |
| `src/scenario/vehicle_audio_assets.py` | 원본 배음/noise PCM·위상 bank·혼합 가중치 |
| `src/scenario/vehicle_audio_playback.py` | 고정 채널·사전 로드·유한 overlap·Cleanup |
| `src/scenario/vehicle_audio_runtime.py` | 기존 Snapshot adapter·model/playback 연결·요약 |
| `src/scenario/research_noa_config.py` | 기본False의 vehicle_audio 설정, 형식 정리 |
| `src/p8/operator_run_config.py` | P8 운영 Run에 vehicle_audio=True |
| `src/scenario/research_noa_runtime.py` | 기존 persistence를 audio source와 summary sink에 주입 |
| `src/scenario/research_noa_view.py` | 준비/update/종료/요약 수명주기 |
| `src/scenario/research_noa_diagnostics.py` | operation 이름 추가 |
| `src/scenario/research_performance_summary.py` | 중첩 span 설명 추가 |
| `tests/test_vehicle_audio_model.py` | 순수 모델 회귀 |
| `tests/test_vehicle_audio_playback.py` | Fake Mixer/실제 PCM 계산 회귀 |
| `tests/test_vehicle_audio_integration.py` | 실제 View loop의 Fake 경계·P8 세 Stage wiring |
| `tests/vehicle_audio_fakes.py` | 무장치 Mixer/Channel/source Fake |
| `tools/preview_vehicle_audio.py` | CARLA/Pygame 없는 Offline WAV 생성 도구 |

기존 `driver_view.py`, `turn_signal_audio.py`, 관측·persistence·session 구현,
TS-XW 매핑/페달 정규화/FFB 진단 도구를 이번에 수정하지 않았다.
기존 미커밋 P5/FPS 변경을 보존했다. MANUAL checked batch 및 응답 검증, TM 소유권,
Driver Override, GENERAL/P5/Z/X/R, indicator 자동 OFF, mirror/HUD/camera,
600/600/780초와 F8, Tobii/Tablet/P6/P7 운영 범위, Native TM attestation을 변경하지 않았다.

Git checkout/switch/pull/merge/reset/rebase/stash/commit/push는 실행하지 않았다.
`.omo`, `src/error_notes.py`, 기존 untracked 작업/로그를 정리하거나 덮어쓰지 않았다.
Phase2에서 기록한 비교 Run2개의 원본 CSV/Manifest **6개 SHA-256이 모두 동일**함을 재확인했다.
이번 작업에서 원본 실험 데이터에 쓰기/삭제는 하지 않았다.

## G. Verification results

### 장치 비접촉 실행 조건

신규 모델·PCM은 순수 계산, 신규 Playback은 전체 Fake Mixer, View integration의
display/event/clock/quit도 Fake로 대체했다.

전체 pytest에는 기존 Windows UI 초기화 테스트가 있으므로, 외부 임시 runner
`C:\Users\nexta\AppData\Local\Temp\opencode\p8_audio_offline_pytest.py`를 사용했다.
이 runner는 pytest 실행 전에 display/audio를 dummy backend로 고정하고,
Pygame 전역 init에서 Joystick init을 제외하며, 실제 Joystick constructor와 CARLA Client를
차단한다. 개별 테스트의 자체 Fake 주입은 허용한다. 실제 스피커/USB/서버에 연결하지 않는다.
기존 테스트의 기대값을 바꿔 통과시키지 않았다.

### 결과

| 검증 | 결과 |
|---|---|
| 재확인한 전체 guarded baseline | **1815 passed / 8 failed / 1 skipped**, 22.44s |
| 신규 Audio regression | **49 passed** |
| 신규 + 기존 Turn Signal Audio | **55 passed**, 2.67s |
| 전체 guarded final | **1864 passed / 8 failed / 1 skipped**, 29.75s |
| 전체 Ruff check | 통과 |
| 변경/신규 Python15개 format check | 통과 |
| git diff --check | 통과 |

두 전체 실행의 실패 ID는 동일하며 신규 회귀 실패는 없다.
full suite에 기존 P8/DriverView/TS-XW/P5 Navigation 테스트가 포함된다.
과거1792/7/1에 비해 TS-XW 초기화 도구 테스트24개가 추가된 Working Tree였으며,
이번 guard로 Windows 실제 화면을 기대하는 테스트1개가 추가로 실패했다.

기존7개 실패:

1. `tests/test_driver_view.py::test_protected_front_cockpit_and_hud_configuration_is_unchanged`
2. `tests/test_driver_view.py::test_layout_places_rear_above_road_and_sides_in_lower_dashboard_gaps`
3. `tests/test_driver_view.py::test_layout_recomputes_from_custom_window_and_legacy_side_size`
4. `tests/test_driver_view.py::test_draw_keeps_front_raw_and_flips_all_three_mirrors`
5. `tests/test_driver_view_cockpit.py::test_v_respawns_only_front_sensor_with_cockpit_relative_transform`
6. `tests/test_p6_candidate_aoi.py::test_candidate_matches_current_mirrors_without_claiming_measured_accuracy`
7. `tests/test_p6_validation_loop.py::test_space_at_previous_segment_end_must_not_skip_next_prepare`

guard 환경 차이1개:

8. `tests/test_p8_training_pygame_lifecycle.py::test_f8_training_restores_operator_display_and_accepts_space_then_quit`
   — 기존 테스트의 `restored_video_driver != "dummy"` 기대와 의도적인 dummy 실행이 충돌한다.
   실제 화면 테스트를 무장치 통과로 주장하지 않는다. F8 오디오 Cleanup은 신규 Fake View 테스트에서 검증했다.

단일 skip은 기존 opt-in Windows display 검증이다.

### 검증 내용

- Idle, 속도 상승, 동일 속도 부하 차이, 순항, braking, 실제 gear 변경 보간,
  reverse/neutral, missing/invalid gear, NaN/Inf, 오래된·미래·없는 상태, RPM 미제공.
- MANUAL/NoA 전환 결과 동일성, stage ID 변경 정지, 실제 dt 보간, pitch/gain 범위.
- preloading1회, 고정 Sound/Channel 개수, engine/click 공존과 click 미정지,
  PCM loop-edge, multi-harmonic 검증, crossfade 가중치 및 실제 동시 채널.
- Mixer 없음/실패/상실, 기존 busy channel 보호, 정상 fade/close 및 재생성,
  Cleanup 실패 전 채널 시도, Python loop가 아예 멈춰도 finite playback 종료.
- 실제 DriverView loop를 Fake 경계로 실행하여 duration/F8/ESC/window/예외 종료 검증.
  오디오 stop 실패가 원래 draw 예외를 덮지 않는 것도 검증했다.
- Fake P8 operator를 통해 Training/M1/M2의 실제 config-builder→Runner→viewer
  연결에 각각 cached audio source가 주입되는지 검증했다.

### Offline 성능·자산 점검

별도 임시 benchmark에서10000회 source→model→Fake playback update를 수행했다.
준비 후 파일 open과 bank synthesis를 금지하고 Sound/Channel 객체 개수가 불변임을 확인했다.

- 평균 update **0.00758ms**, p95 **0.0109ms**, 최대 **0.4596ms**.
- Sound306개, Channel wrapper44개(초기 busy 확인8개 + 보유36개), 총 playback 채널44개.
- grain launch1042회, busy skip0, backend 오류0.
- 반복 파일 접근/PCM 합성0. model/source에는 CARLA 객체 호출 경로가 없다.
- 22050/44100/48000Hz mono/stereo bank의 실제 byte 길이도 별도로 확인했다.

**Fake backend 수치이므로 SDL/native mixing CPU·장치 지연·실제 FPS 영향은 NOT_MEASURED다.**
기존 performance_summary의 추가 span과 실제 Run의 whole-stage Display/World/camera rate를
함께 비교해야 한다. 준비와 종료 시간은 별도로 측정해야 한다.

### Offline WAV Preview

최종 생성물:

```text
C:\Users\nexta\AppData\Local\Temp\opencode\p8_audio_preview_20261015_final\
  synthetic_drive.wav
  preview_manifest.json
```

22050Hz mono, **39.64초**, PCM peak5519.22/32767, clipped samples0.
음량의 적절성·음색 자연스러움·장치 출력 SPL을 측정한 수치가 아니다.
Manifest에는 모든 source가 synthetic임을 표시하고 추정 파라미터 trace를 보관했다.

| 구간 | 내용 |
|---|---|
| 0~3s | Idle |
| 3~13s | 0→60km/h 가속, 기어 증가 |
| 13~18s | 60km/h 순항 |
| 18~26s | 60→100km/h 가속, 기어 증가 |
| 26~36s | 100→0km/h 감속/다운시프트 |
| 36~39s | 정차 |
| 15~29s | synthetic NoA 구간, 전후 MANUAL 전환 |

이는 **CARLA 녹음이 아닌 결정론적 가상 궤적의 모델 출력**이다. 실제 Mixer와 화면 jitter를
녹음한 것도 아니다. Preview 생성만 수행했고 자동 재생은 하지 않았다.

새 Preview 생성 명령(주행·오디오 장치 접속 없음, 새 디렉터리 사용):

```powershell
& 'C:\dev\carla-native-tm-cancel-20261012\.venv-p8\Scripts\python.exe' -B -m tools.preview_vehicle_audio --output-dir 'C:\Users\nexta\AppData\Local\Temp\opencode\p8_audio_preview_review'
```

검증 증거는 동일 임시 디렉터리의 `p8_audio_baseline.txt`, `p8_audio_final.txt`,
`p8_audio_benchmark.json`에 있다. 원본 연구 데이터와 별도다.

## H. Remaining concerns

1. 실내 엔진음의 자연스러움과 연구실 speaker/headphone 음량 균형은 청취 미검증이다.
2. 특정 Mercedes 소리, 실차 RPM/기어비, 실제 노면 재질/바람/타이어 힘을 측정한 구현이 아니다.
3. 실제 current RPM 없이 속도·gear·load로 추정하므로 torque converter, clutch slip,
   shift timing, stop-start 등 실제 drivetrain 반응과 차이가 난다.
4. gear fallback과 generic reverse ratio는 연구용 실제 물리 관측으로 사용할 수 없다.
5. 위상8종의 근사와 이웃 음역 crossfade는 완전 연속 pitch oscillator가 아니다.
   음악적인 beating/기계적 반복감은 실청취 후 개선 대상이다.
6. 낮은/불규칙 loop cadence에서 grain 밀도 변화가 생긴다. 긴 stall에서 정지하도록 설계됐다.
   이를 감추기 위해 무한 playback이나 새 background 관측/제어 thread를 추가하지 않았다.
7. 실제 Mixer의 CPU·latency·sample-format·device recovery는 Fake로 검증할 수 없다.
8. 같은 형식으로 외부 코드가 Mixer를 quit/init하는 비소유 동작은 Pygame API만으로 완전히
   감지할 수 없다. 현재 P8의 명시적 Stage owner/quit 경계를 전제로 한다.
9. 오디오 초기화 실패 시 주행은 계속되지만 summary에 오류가 남는다. 연구자는 조건별 오디오
   제공 여부를 확인해야 한다. 청취 조건의 일관성은 연구 운영 검증 항목이다.
10. 기존7개 회귀 실패, P5 실주행/Route·Spawn·window 승인, P7 미완성 사항은 해결하지 않았다.

## I. Next Live Validation

사용자가 연구실에서 승인된 patched Python/runtime attestation과 기존 P8 명령으로 수행한다.
이번 작업에서 아래 실주행은 실행하지 않았다.

1. **정차:** quiet idle, road/wind의 소실, 반복 click/pop 유무, 적절한 실내 음량.
2. **출발·가속:** pedal→적용 throttle→부하 변화 지연, RPM 상승과 배음 변화.
3. **60km/h 순항/급가속:** 같은 속도에서도 부하 음색·강도가 구분되는지.
4. **고속:** road/wind 증가가 engine/click과 균형을 이루고 피로감을 주지 않는지.
5. **감속·브레이크:** 즉시 RPM0/음소거가 발생하지 않는지, 속도에 따라 소음이 줄어드는지.
6. **변속:** gear 관측 변화와 소리의 완만한 변화, 장시간 흔들림/beat/단절 유무.
7. **MANUAL↔NoA와 Override:** 모드만 바뀌어 음색이 재시작하지 않는지, 실제 NoA throttle/brake 반영.
8. **Z/X/R/자동 signal OFF:** 클릭과 엔진 동시 재생 및 기존 클릭 종료 유지.
9. **FPS:** 각 모드 `vehicle_audio_update`, 전체 audio, checked batch/Post-control,
   Display/World/camera rates를 함께 수집. startup bank 준비와 exit50ms도 별도로 기록.
10. **Training 최대600초/F8, M1 600초, M2 780초:** 각 Stage 소유권/재초기화/음 누출 확인.
11. **ESC/window/정상 종료:** 잔류 재생 없음, 정상 fade, 기존 Shutdown brake 및 attestation 계약 유지.
12. **오류·상태 상실:** 연구 진행과 분리된 승인된 환경에서만 audio unavailable/stale/cleanup 확인.
    이번에 별도 차량 제어·장치 분리 자동화는 추가하지 않았다.

이 결과를 받은 후에만 실제 샘플 도입·음향 파라미터 튜닝·독립 streaming backend 필요성을 결정한다.

### 공식 API 근거

- [Pygame Mixer: Sound/Channel, reservation, fadeout, buffer format](https://www.pygame.org/docs/ref/mixer.html)
- [CARLA0.9.16 Python API: Vehicle.get_control, VehiclePhysicsControl.max_rpm](https://carla.readthedocs.io/en/0.9.16/python_api/)
- 로컬 native `C:\dev\carla-native-tm-cancel-20261012\source\LibCarla\source\carla\client\Vehicle.h`
  읽기 전용 확인. source 확인은 loaded-binary attestation을 대체하지 않는다.

**구현·Offline 검증·보고서 작성을 완료했다. 실제 주행/청취 검증은 별도 후속 단계다.**
