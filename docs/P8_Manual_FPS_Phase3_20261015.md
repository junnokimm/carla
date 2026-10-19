# P8 MANUAL FPS Phase 3 — 2026-10-15

## 1. 결과 요약

**P8 Training/M1/M2 운영 설정의 합성 차량음을 OFF로 변경했다.** 엔진·노면·풍절음
구현과 명시적 opt-in 테스트는 보존했다. 방향지시등 클릭음은 계속 동작한다.

**추가 CARLA RPC 제거는 0건이다.** 지정 Run의 병목은 확인했지만, Bounding Box 및
조명 관측의 실패·복구·관측 시점까지 보존하는 호출 제거는 입증되지 않았다.
Checked Batch 전송도 그대로 유지했다. 따라서 이번 변경으로 심각한 MANUAL 프레임 드랍이
해결되었다고 주장하지 않는다.

기존 계측 밖의 장시간 정체를 좁히기 위해 네 구간의 진단과 **가장 느린 완료 루프 한 개의
동일 루프 구간 기록**을 구현했다. 매 프레임 파일 출력이나 추가 관측 RPC는 없다.

- 기준: 현재 로컬 `research/phase-1c-automation` Working Tree.
- HEAD: `e1af127bb096beaecec3e12cbd2b5bf18a378785` 유지.
- 최우선: `CARLA_Experiment_Design_v5_Source_of_Truth.md`, 특히 §6 운전자 우선권·가용성,
  §7 출구/차로변경 계약. Phase 2의 캐싱 기각 근거를 먼저 검토했다.
- 실제 CARLA 주행, World/TM 변경, TS-XW Open/FFB 출력, native 교체는 수행하지 않았다.
- 전체 검증은 dummy display/audio와 물리 Joystick/CARLA Client 차단 runner에서 수행했다.

## 2. 차량음 비활성화

`src/p8/operator_run_config.py`의 `vehicle_audio=True`를 **False**로 변경했다.
Training/M1/M2는 같은 builder를 사용한다. 별도 운영 진입점이나 새 CLI 옵션은 없다.

기존 연결 경로가 OFF를 처리한다:

```text
P8 builder: vehicle_audio=False
  → ResearchNoARunner: audio_source=None, status_sink=None
  → ResearchDriverView: _vehicle_audio=None
  → attach/run/close에서 차량음 prepare/update/fade 실행 없음
```

따라서 운영 경로에서는 차량음 Runtime/Playback 객체 생성, 절차적 PCM bank 생성,
306개 Sound 로드, 36개 전용 채널 예약, 차량음 종료 fade가 수행되지 않는다.
차량음 코드 import 자체는 남아 있으나 import는 PCM 생성이나 장치 초기화를 하지 않는다.

Fake 통합 테스트는 실제 P8 builder→Runner가 만든 세 Stage의 view config를 확인하고,
그 config로 실제 ResearchLiveDriverView를 Fake World/Vehicle/Mixer와 생성·attach·update·close했다.
차량음 생성자/prepare/PCM 생성 및 채널 예약 접근을 실패하도록 막은 상태에서 통과했다.
같은 테스트에서 방향지시등 ON→클릭 재생, OFF→클릭 정지, close→정지를 확인했다.
클릭용 Mixer lazy init과 기존 cleanup 경로는 유지된다.

이전 오디오 통합 테스트의 opt-in 검증도 별도 분기로 유지했다. 테스트 내부에서만
ResearchNoARunConfig의 `vehicle_audio=True`를 지정하며, 운영 builder가 False인지 먼저 검증한다.
기존 `src/scenario/vehicle_audio_*` 구현은 수정·삭제하지 않았다.

## 3. 지정 Run의 실제 근거

Run: `P8-P5-4f9c04c5330642389966242c8627c048`

원본 위치:
`data/p8_operator/P8-P5-4f9c04c5330642389966242c8627c048/`

- `research_events_*.csv`의 `performance_summary`를 직접 읽었다.
- Training 약477.203초, completed loops/display flips **4811**.
- `PARTIAL`, `KeyboardInterrupt` 종료. 일부 operation은 마지막 미완료 루프를 포함해4812회다.
- `NOA_ACTIVE` operation 집계는 비어 있다. MANUAL 대 NoA 비교 자료가 아니다.
- World는 asynchronous, fixed_delta_seconds=null, Front/Left/Right 3 RGB 구성이다.

| 지표 | count | 평균 ms | 최대 ms |
|---|---:|---:|---:|
| camera MANUAL driver_loop | 4811 | 99.056320 | 1223.651600 |
| MANUAL post_control | 4812 | 48.452687 | 115.045400 |
| manual_availability_refresh | 4812 | 23.809263 | 65.431300 |
| activation_bounding_box_getter | 4748 | 24.006494 | 65.301400 |
| telemetry light_getter | 4812 | 23.855621 | 52.082800 |
| turn_signal_audio_light_rpc (camera) | 4811 | 23.847700 | 63.718000 |
| manual_control_delivery | 4812 | 15.502847 | 35.750200 |
| manual_batch_rpc_wait | 4812 | 15.475487 | 35.726800 |
| vehicle_audio_update | 4811 | 0.073876 | 0.368300 |
| camera draw | 4811 | 9.515627 | 18.300300 |
| camera display_flip | 4811 | 0.933414 | 3.203000 |

실측 completed Display cadence는 **10.0816656 FPS**, World cadence는40.3455133 frames/s다.
loop 평균의 역수나 display_flip 호출 시간의 역수로 모드별 FPS를 계산하지 않았다.
camera의 vehicle_audio_update 평균0.082705ms는 바깥 계측이고 위0.073876ms는 내부 계측이다.
동일 업데이트를 두 번 더하지 않는다.

BBox는 refresh의 하위 구간이고 refresh/light는 post_control에 포함된다.
Batch wait는 control_delivery에 포함되며, Audio light는 camera audio에 포함된다.
BBox/refresh count도 다르므로 평균을 무조건 더하거나 빼서 정밀한 구성비를 만들지 않는다.
차량음 OFF로 제거되는 업데이트의 관측 평균은 약0.074ms에 불과하다.
Mixer 비동기 비용/메모리·준비 제거의 실주행 효과는 별도 측정 전이다.

## 4. 병목별 검토와 결정

### A. Bounding Box

실제 경로는 MANUAL post-control→availability refresh→geometry→`vehicle.bounding_box`다.
이번 Run에서 bbox 단독 약24ms가 확인되어 Phase 2의 추정 병목이 직접 계측되었다.

로컬 native 근거:
`C:\dev\carla-native-tm-cancel-20261012\source\LibCarla\source\carla\client\Actor.cpp:35-42`
및 `detail/Simulator.h`, `detail/Client.cpp`.

매 호출마다 `GetActorBoundingBox`→`CallAndWait("get_actor_bounding_box", actor)`를 수행한다.
세 extent가 모두0인 응답은 그대로 반환하고, 그 외에는 `Super::GetBoundingBox()`의 메타데이터
값을 반환한다. 따라서 메타데이터가 정적이라는 사실만으로 fresh RPC의 의미를 제거할 수 없다.

- Actor 생존·destroy/respawn, 서버 오류, zero-extent 응답, 정상 복구가 기존 조회 경계에 노출된다.
- 같은 Actor ID/Stage마다 캐시를 새로 만든다고 중간 실패·zero·복구가 보존되지는 않는다.
- 일반 geometry/brake/lane-change guard를 통과한 경우의 조회를 유지했다.
- 기존 gate는 zero extent를 일괄 INVALID로 바꾸지 않는다. 신규 Fake 반례는 큰 bbox→zero→오류→복구를
  매번 관측하고 기존 가용성 결과/예외가 달라짐을 검증한다. zero의 의미를 임의로 재해석하지 않았다.
- availability UI/이벤트를 매 프레임 평가하는 기존 주기를 낮추지 않았다.

**결정:** 캐싱/조건 없는 제거는 불가. 적용된 RPC 변화0.

**필요 증거/구조적 대안:** 실제 실행 서버와 일치하는 bbox 처리·Actor lifecycle·zero 반환 조건을
확인하고, 같은 freshness와 오류를 전달하는 native/서버 계약이 있어야 한다. 예를 들어 검증된
동일 관측의 lifecycle+geometry 데이터 제공은 후보이나 기존 RPC와 오류 발생 시점이 달라질 수 있다.
별도 native 변경·attestation·실장치/시뮬레이터 검증이 필요하며 이번에 적용하지 않았다.
버튼을 누를 때만 조회하거나 일정 주기로 갱신하는 대안은 UI·실패 사유·이벤트 시점을 바꾼다.

### B. 차량 조명 관측

다음 관측은 서로 다른 호출이다:

1. Z/X 입력 이벤트에서 기존 상태 확인(Phase 1: 이벤트 없으면 조회 없음).
2. accepted mode-event handler에서 fresh read→toggle→setter.
3. post-control persistence의 `get_light_state`→Telemetry/Snapshot indicator 기록.
4. 해당 NoA 차로변경 완료 시 fresh read→선택 bit OFF→readback 및 이벤트 기록.
5. Audio 직전 fresh `get_light_state`→방향지시등 클릭 활성 여부.

4의 기존 구현은 `lane_change_signal.clear_completed_signal`에 있으며 MANUAL 상태에서는 반환한다.
이번에 MANUAL 자동 해제 정책을 새로 만들지 않았다. GENERAL/P5의 기존 완료·관측 흐름을 보존했다.

Telemetry가 ON을 기록한 직후 완료 OFF가 실행되어 Audio는 OFF를 읽는 것이 가능하다.
동일 Python loop가 동일 World frame 또는 같은 순간의 상태라는 보장은 없다.
자체 setter가 없는 루프에서도 외부/TM 조명 변경, 두 번째 RPC의 실패·복구가 가능하다.
OFF 때만 조회를 생략해도 다음 ON과 오류를 놓칠 수 있다.

**결정:** 두 관측을 공유·캐싱하지 않음. Audio light RPC의 실패는 기존처럼 전파하고,
실패 시간을 남긴다. 방향지시등 클릭 및 자동 OFF 회귀는 guarded 전체 테스트에 포함했다.

**필요 증거/구조적 대안:** authoritative light writer의 독점 소유권만으로는 실패 시점까지 같아지지
않는다. 서버가 제공하는 관측 순서/세대/오류 계약 또는 연구 Telemetry와 Audio가 하나의 관측
시점을 사용하도록 승인된 재설계가 필요하다. Telemetry를 자동 OFF 뒤로 옮기는 방식은 연구 기록
시각·indicator 값을 바꾸며, native bundled RPC도 현재 두 시점의 의미를 바꿀 위험이 있다.

### C. Manual Control Delivery

`ResearchLiveDriverView._send_control`→`make_checked_control_sender`를 확인했다.
`apply_batch_sync([ApplyVehicleControl(actor_id, control)], False)` 후 응답 개수1,
has_error, actor_id를 검사한다. 이 검사는 추가 RPC가 아니다.

4812회의 control_delivery/batch 계측으로 중복 전송을 제거할 근거가 없다.
두 평균의 차 약0.02736ms는 wrapper/응답 검증/계측 경계 전체 차이이며 순수 검증 시간은 아니다.
정상적인 동일 명령 반복도 기존 per-loop 전달·오류 감지 계약이므로 생략하지 않았다.

**결정:** 전송 빈도, checked response, sticky_control=true, MANUAL/NoA 소유권,
실패 전파·shutdown brake 유지. 비동기/unchecked/skip으로 바꾸지 않음.

**구조적 대안:** 먼저 실제 client/server queue·게임 스레드 응답 지연을 추적해야 한다.
native 서버 응답 경로 최적화는 후보이나 원자성·응답 검증·적용 순서·False tick 의미가 보존되어야 한다.
client 비동기화는 오류 발견 및 제어 적용 시점이 달라져 현재 계약의 대체가 아니다.

## 5. 약1.22초 Stall 분석 및 새 진단

기존 camera 통계에서 input/input-observer/scheduler/manual-preparation/manual-delivery/
post-control/audio/draw/display-flip/limiter **각 최대값**의 합은261.1534ms다.
이들을 모두 같은 최악 루프에 배정하는 보수적인 상한을 사용해도1223.6516ms 중
약**962.4982ms**가 이 구간들 밖에 남는다. 이는 특정 함수의 실측 비용이 아니라
미계측 훅·구간 사이 대기·계측/스케줄링 비용을 포함하는 잔여의 하한이다.
24ms RPC 하나가1.22초 stall의 직접 원인이라고 기존 통계에서 결론낼 수 없다.

원본 이벤트·Telemetry 추가 확인:

- Training `session_elapsed_s=6.703`의 traffic-light/P5 observation-gap 기록 뒤,
  다음 `tsxw_input_sample`은7.875초: **1172ms** 공백.
- 인접 Telemetry6.703→7.937초: **1234ms**, CARLA frame15220→15269.
- Front callback 간격 최대1172.255ms, Left1167.7623ms, Right1164.1776ms.
- callback 최대값의 개별 발생 시각은 summary에 없어 같은 공백이라고 단정할 수 없다.
- 이벤트의 host timestamp와 perf_counter의 차분도 섞지 않았다.

이는 주행 초기 호스트 측 관측 공백과 일관되지만, 원인 후보인 SDL event pump/창 처리,
P5 prepare/presentation hook, 프로세스 scheduling/GIL/시뮬레이터·시스템 정체 중 하나를 확정하지 못한다.
공백 이전/이후 이벤트는 profiler의 함수 경계가 아니다.

### 새 구간(기존에 빠진 경계만)

| 이름 | 경계 |
|---|---|
| event_poll_and_presentation | pygame.event.get + presentation.handle_events (F11 창 처리 포함) |
| mode_events | 기존 mode/view/indicator handler, toggle RPC 포함 |
| frame_prepare | 기존 pre-draw hook, P5 prepare 포함 |
| after_display_flip | 기존 post-flip hook, P5 mark_presented 등 포함 |

모두 기존 performance observer가 있을 때만 clock을 읽으며 `finally`에서 기록한다.
호출 순서·인수·예외 전파·cleanup은 유지했다. Z/X/R, display 뒤 presentation marking 위치도 동일하다.

`camera.slowest_completed_loop`에 loop index, duration_ms, 완료 시 canonical mode 및
**해당 루프에서 측정한 구간**을 보관한다. 기존 timing의 서로 다른 루프 최대값을 붙이지 않는다.
현재 루프와 최악 한 루프만 보관하고14개의 고정 구간명만 선택하므로 trace가 무한히 증가하지 않는다.
하위 camera conversion/audio light 값은 중복 비용으로 더하지 않는다.

- active window 안의 완료 루프만 비교한다. partial/error 루프는 전체 timing에는 남을 수 있지만
  최악 완료 루프에 포함하지 않는다. freeze/cleanup/continuation 이후 값으로 대체하지 않는다.
- mode는 완료 시 분류다. 전환 루프가 한 모드만 포함했다는 의미가 아니다.
- 누락 segment는0ms 측정이 아니다. 계측이 모든 실행을 덮는 것도 아니다.
- fallback 경로의 input_collection은 manual_control_preparation 안에 중첩될 수 있다.
- 기존 performance_summary JSON에 additive 항목만 추가했다. CSV 열/빈도/SessionClock 변경 없음.

Fake에서 진단 begin +14개 span 기록+loop complete를10,000회 실행:
평균0.01118ms, p95 0.0134ms, 최대0.1585ms, 보관 완료 루프1개.
기존 집계 비용도 포함한 Fake 전체 비용이며 실제 추가 오버헤드 delta나 실주행 FPS 측정이 아니다.

## 6. 수정 파일 및 RPC 수 변화

| 파일 | 이번 변경 |
|---|---|
| src/p8/operator_run_config.py | 운영 차량음 OFF |
| src/scenario/driver_view.py | 빠진 네 구간 계측, 기존 handler 호출 유지 |
| src/scenario/research_noa_view.py | 기존 loop begin에 진단 correlation 시작 |
| src/scenario/research_camera_diagnostics.py | bounded same-loop summary 및 timing 설명 |
| tests/test_vehicle_audio_integration.py | OFF 세 Stage 통합·클릭 유지 + 기존 opt-in 분기 |
| tests/test_driver_view_runtime.py | 새 진단 구간 순서 기대값 |
| tests/test_p8_phase3_performance.py (신규) |13개 Fake 회귀 |
| 본 문서 (신규) | 분석·검증·후속 절차 |

**추가 CARLA RPC 0, 제거 CARLA RPC 0.** 기존 오디오 상태 어댑터도 원래 cached observation만
사용하므로 차량음 OFF가 CARLA 조회를 줄이는 것은 아니다. 새로운 polling/thread/process는 없다.
운영 차량음 Runtime/Playback/PCM/전용 채널 준비는 실행0으로 바뀐다.

## 7. 동등성 및 보호 검증

- 신규13개 테스트: 동일 루프 correlation, mode 분류, bounded storage, window reset,
  freeze/partial 제외, 네 새 구간 정상/실패 때 원래 호출 순서·예외·cleanup, bbox zero/error/recovery.
- 기존 오디오 통합 테스트를 OFF/opt-in으로 매개변수화해1 case 추가.
- OFF integration + Phase2/3 집중 검증: **34 passed**.
- guarded 전체 baseline: **1936 passed / 8 failed / 1 skipped**.
- guarded 전체 final: **1950 passed / 8 failed / 1 skipped**. 실패 ID는 baseline과 동일.
- 전체 Ruff, 변경/신규 Python7개 format check, `git diff --check` 통과.

검증 runner는 `SDL_VIDEODRIVER=dummy`, `SDL_AUDIODRIVER=dummy`를 강제하고,
pygame.init을 dummy display/font/mixer 초기화로 대체한다. joystick init/count는 no-op/0,
실제 Joystick constructor 및 `carla.Client`는 예외로 차단한다. 물리 디스플레이/스피커/핸들
검증이 아니며 실제 FFB API를 호출하지 않았다. opt-in Windows display 테스트는 skip이다.

기존 실패 ID:

```text
tests/test_driver_view.py::test_protected_front_cockpit_and_hud_configuration_is_unchanged
tests/test_driver_view.py::test_layout_places_rear_above_road_and_sides_in_lower_dashboard_gaps
tests/test_driver_view.py::test_layout_recomputes_from_custom_window_and_legacy_side_size
tests/test_driver_view.py::test_draw_keeps_front_raw_and_flips_all_three_mirrors
tests/test_driver_view_cockpit.py::test_v_respawns_only_front_sensor_with_cockpit_relative_transform
tests/test_p6_candidate_aoi.py::test_candidate_matches_current_mirrors_without_claiming_measured_accuracy
tests/test_p6_validation_loop.py::test_space_at_previous_segment_end_must_not_skip_next_prepare
tests/test_p8_training_pygame_lifecycle.py::test_f8_training_restores_operator_display_and_accepts_space_then_quit
```

마지막 실패는 dummy가 아니어야 한다는 기존 assertion과 의도적 격리 조건이 충돌한다.
중간 검증에서 새 계측 구간에 따른 기존 observer 목록 assertion을 갱신했고, 최종 신규 실패는0이다.

작업 전646개 파일 해시 중 의도한 기존6개 파일만 변경되었다. 나머지640개는 동일하다.
여기에는 **data/p8_operator 원본152개 파일**(지정 Run의 CSV2개/Manifest 포함)이 포함된다.
TS-XW 입력/정규화, vehicle_audio_* 구현, FFB Offline 모델, 입력 정책, 연구 persistence,
checked sender, shutdown, P5 모델, P6/P7 구현은 byte 단위로 보존했다.
기존 미커밋 변경을 유지했으며 `.omo`, `src/error_notes.py`를 편집하지 않았다.
Git switch/pull/reset/stash/commit/push 등은 수행하지 않았다.

증거 위치: `C:\Users\nexta\AppData\Local\Temp\opencode\`

```text
p8_phase3_before.json
p8_phase3_run_summaries.json
p8_phase3_baseline.txt
p8_phase3_final.txt
p8_phase3_diagnostics_benchmark.py
p8_phase3_diagnostics_benchmark.json
p8_audio_offline_pytest.py  (기존 guarded runner)
```

## 8. 실주행 전후 비교 및 후속 조치

후속 실주행은 연구실에서 별도 승인 후 기존 단일 `python -m src.p8.operator_cli`로 진행한다.
운영 patched Python/libcarla/TM listener attestation 요구는 그대로다.

1. 기존 Run은 MANUAL-only·KeyboardInterrupt partial이라는 한계를 표시한다. 같은 route/spawn,
   TS-XW, camera/mirror/HUD, 교통·weather·창 상태, nominal Training/M1/M2 조건으로 새 Run을 만든다.
   원본을 덮어쓰지 않는다. 비교를 위해 차량음을 다시 켤 필요는 없다.
2. 엔진/노면/풍절음이 들리지 않고 방향지시등 클릭 및 기존 자동 OFF가 유지되는지 확인한다.
   vehicle_audio_update count0/미측정, vehicle_audio_summary 없음도 확인한다.
3. Display completed flips/wall time, World rate, camera callback cadence를 각각 비교한다.
   MANUAL/NoA operation별 count/mean/max와 모드 전환도 별도로 본다. 이 Run의 NoA 성능은 미측정이다.
4. 최악 루프의 새 네 구간을 먼저 확인한다. event 구간이 크면 SDL/창·focus/입력 경계를,
   P5 hook이 크면 presentation 경계를 조사한다. 잔여가 크면 OS scheduling/GIL/프로파일러 증거가 필요하다.
   callback gap과 비교하되 서로 다른 clock/집계의 최대값을 같은 사건으로 단정하지 않는다.
5. bbox/light/batch의 호출 수와 실패·복구, MANUAL↔NoA/Driver Override, Z/X/R,
   EXIT/MISSED 이후 주행, Training600초/F8·M1 600초·M2 780초·shutdown brake를 점검한다.
6. RPC 감소가 필요하면 §4의 native/서버 관측 계약 검증을 별도 범위로 진행한다.
   의미 동등성을 입증하기 전에는 캐싱·가용성 주기 축소·unchecked 제어로 바꾸지 않는다.

**미해결:** 반복 약24ms 동기 조회와 장시간 호스트 stall의 실제 원인은 남아 있다.
이번 단계는 차량음 OFF를 실제 적용하고 stall 진단을 구체화했으며, 실주행 부드러움 향상은 아직 미검증이다.
