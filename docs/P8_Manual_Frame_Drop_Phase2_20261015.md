# CARLA V5-P8 MANUAL Frame Drop — Phase 2

## A. Initial status

**결론: 추가 RPC 제거의 동등성을 입증하지 못해 실행 경로 최적화는 보류하고,
Post-control 하위 구간을 분해할 최소 계측을 구현했다. 실제 성능 향상은 미측정이다.**
1차 최적화는 유지한다. Bounding Box를 정적 값으로 캐싱하거나 Telemetry 조명을
Audio에 재사용하지 않았다. 약46ms 전체를 Bounding Box 시간으로 해석하지 않는다.

- Branch: `research/phase-1c-automation`.
- HEAD: `e1af127bb096beaecec3e12cbd2b5bf18a378785` (시작/종료 동일).
- 현재 로컬 Working Tree가 기준이다. STEP 1~3, 1차 입력 light RPC 수정,
  기존 `.omo`, `src/error_notes.py`, 테스트·문서·실험 자료를 보존했다.
- 최우선: `docs/CARLA_Experiment_Design_v5_Source_of_Truth.md`, 특히 §6 활성화/해제,
  §7 출구 안내·추천·관측·진출 이후 계속 주행.
- 참고: `P8_Manual_Frame_Drop_Analysis_20261015.md`, `P8_P5_FPS_Diagnosis.md`,
  `P8_Patched_TM_LaneChange.md`, STEP 1+2 및 STEP 3 보고서.
- 운영 진입점: `python -m src.p8.operator_cli`.
- Offline 검증: 저장소 `.venv\Scripts\python.exe`.
- 운영 patched Python은 `C:\dev\carla-native-tm-cancel-20261012\.venv-p8\Scripts\python.exe`.
  이번에 운영 프로그램이나 CARLA RPC를 실행하지 않았다.
- Git checkout/pull/merge/rebase/reset/stash/commit/push를 수행하지 않았다.

실제 원본 Run:

| 별칭 | Run ID |
|---|---|
| A: 1차 수정 전 | `P8-P5-c4cd368b8f384031abd9392016b6bce7` |
| B: 1차 수정 후 | `P8-P5-ae299a59da4e46948d20ec57eca8bfff` |

각 `data/p8_operator/<Run ID>/`의 events CSV, telemetry CSV, Manifest를 읽기 전용으로
사용했다. CSV는 `csv.DictReader`, `performance_summary.event_value`는 `json.loads`로 읽었다.
아래 A/B 비교는 **각 Run의 Training summary만** 사용한다. A의 M1을 섞지 않았다.

## B. Performance baseline

### 구간과 모드

| 항목 | A Training | B Training |
|---|---:|---:|
| Summary CSV record (헤더=1) | 21777 | 10505 |
| Summary session_elapsed_s | 494.688 | 197.984 |
| Stage 시작→종료 (session s) | 0.016→496.391 | 0.016→198.594 |
| Active Drive Duration (s) | 489.344 | 192.688 |
| Manifest 실제 NoA ON 시간(s) | 123.062 | 11.219 |
| MANUAL 완료 loop n | 3069 | 1902 |
| NOA_ACTIVE 완료 loop n | 1618 | 240 |
| Training 종료 | OPERATOR_EARLY_FINISH | USER_ABORT |
| Summary status / error | COMPLETE / null | COMPLETE / null |

B는 Training에서 사용자 종료한 ABORTED 세션이며 M1/M2 주행 자료는 없다.
COMPLETE는 summary 수집 상태이며 600초 완주나 실험 성공이 아니다.
두 기록 모두 같은 HEAD와 dirty 상태를 기록하지만 과거 Working Tree의 모든 바이트가
현재와 같다는 뜻은 아니다. 모드별 operation 분류는 **호출 완료 시 canonical control mode**다.

### 전체 구간 cadence (모드별 FPS 아님)

| 지표 | A Training | B Training |
|---|---:|---:|
| completed_display_flips_per_wall_second | 9.578266 | 11.116545 |
| completed_driver_loops_per_wall_second | 9.578129 | 11.116416 |
| world_frames_per_wall_second | 41.267493 | 42.151042 |
| world_frames_per_sim_second | 41.266444 | 42.148415 |
| Front callbacks_per_wall_second | 41.102554 | 41.705207 |
| Front callback n | 20113 | 8036 |
| Front 도착 간격 평균/최대(ms) | 24.308953 / 1610.053500 | 23.926251 / 1133.908900 |
| Front 도착 간격 n | 20112 | 8035 |

### 모드별 평균 / 최대(ms)

| 지표 | A MANUAL | B MANUAL | A NOA_ACTIVE | B NOA_ACTIVE |
|---|---:|---:|---:|---:|
| driver_loop | 119.189733 / 1703.4754 | 95.130525 / 1194.7959 | 76.005 / 127.676 | 46.630840 / 69.3052 |
| input_collection | 15.018591 / 48.4131 | 0.128352 / 0.3202 | 15.614803 / 37.1603 | 0.125074 / 0.2098 |
| manual_batch_rpc_wait | 23.908292 / 41.9896 | 16.368873 / 45.1974 | NOT_MEASURED | NOT_MEASURED |
| post_control | 46.191086 / 64.5264 | 46.445603 / 92.6633 | 25.216086 / 56.7535 | 15.194887 / 23.1086 |
| light_getter | 23.010441 / 42.0869 | 22.872652 / 49.4644 | 24.422105 / 56.1629 | 14.131557 / 22.1304 |
| audio (light RPC 포함) | 23.094703 / 46.1258 | 22.585896 / 50.4916 | 24.324837 / 55.5089 | 21.823692 / 24.7953 |
| draw | 9.037971 / 12.0437 | 7.533595 / 10.9861 | 9.663855 / 14.5232 | 8.345191 / 9.6660 |
| display_flip duration | 0.881672 / 2.1955 | 0.839954 / 1.5270 | 0.915002 / 1.4992 | 0.899793 / 1.3626 |

위 표의 각 loop/input/post/light/audio/draw/flip 표본 수는 해당 모드 완료 loop n과 같다.
NoA batch는 표본이 없으며 0ms 측정으로 표시하지 않았다.
B MANUAL의 snapshot_getter/snapshot_actor_getters/control_getter 평균은
0.004752/0.003333/0.005359ms, waypoint/neighbor는0.063500/0.005288ms다.
Telemetry write는0.082159ms/1902회, event write는0.038660ms/9520회다.
이 계측 범위의 getter/쓰기 평균은 남은 수십ms를 설명하지 못한다.

**비교 제한:** 속도·위치·교통·시뮬레이터 부하·모드 체류시간이 통제되지 않았다.
특히 B NoA는 약11초뿐이다. Input 비용 감소는 1차 수정의 기대 방향과 일치하지만,
Batch·draw 등도 달라져 전체 변화의 인과나 향상률을 단정할 수 없다.
정확한 모드별 Display FPS, 모드별 World/callback rate, p95/p99는 NOT_MEASURED다.
loop 평균의 역수나 flip duration의 역수를 모드별 FPS로 쓰지 않았다.
이번 Phase 2 이후 실주행 자료는 아직 없다.

## C. Root cause investigation

### 1. Post-control 및 Bounding Box

실제 호출 경로:

```text
DriverView.run → input_observer.after_control_applied (post_control span)
  ResearchExitInteractionObserver
    ResearchLaneChangeInteractionObserver
      PersistedInteractionObserver
        AutomationInteractionController.after_control_applied
          MANUAL + pending input일 때 _refresh_availability
            AutomationActivationGate.failure
              brake/lane-change/external-availability guard
              geometry.observe_with_context(vehicle)
              vehicle.bounding_box
              lane_alignment_failure
            availability 상태/이벤트 갱신
        pending automation events flush
        persistence.capture → snapshot/control/map/neighbors/light/telemetry
        native TM diagnostics callback (설정 시)
      traffic-light observation
      tracker 관측/완료 및 signal clear (추적 중일 때)
    P5 관측 처리
```

NoA steady state는 MANUAL availability 재평가를 건너뛰지만 공통 persistence/관측은 수행한다.
Gate는 차량 정렬, 차로 걸침, brake, lane-change, 외부 가용성 및 실패 사유를 판단한다.
MANUAL에서 매 프레임 재평가는 HUD 가용성과 availability 이벤트의 시점을 결정하므로
주기 감소나 활성화 버튼 때만 평가하는 변경은 기존 기록/상태 의미를 바꾼다.

B MANUAL post_control46.445603ms 중 light_getter22.872652ms는 직접 계측된 포함 비용이다.
단순 차23.572951ms는 **기하·bbox·기록·기타 후처리 전체의 미분해 잔여**다.
Bounding Box 단독 시간이 아니다. 각 operation은 중첩되어 모든 평균을 더하면 안 된다.

읽기 전용 native 코드 근거:
`C:\dev\carla-native-tm-cancel-20261012\source\LibCarla\source\carla\client\Actor.cpp:35-42`
는 매번 `GetActorBoundingBox()`를 호출하고, 모든 extent가0인 응답은 그대로 반환하며,
그 외는 `Super::GetBoundingBox()`를 반환한다. `detail/Simulator.h:455` 및
`detail/Client.cpp:434`는 `CallAndWait("get_actor_bounding_box", actor)` 경로다.
따라서 **동기 RPC 경로는 CONFIRMED, 잔여23.57ms의 지배 원인인지는 LIKELY/미측정**이다.
로컬 소스 확인은 실행 binary의 profiler나 runtime attestation을 대신하지 않는다.

메타데이터 bbox의 정적인 모양만 보고 RPC를 건너뛰면 zero 응답, Actor 소실/오류를
발견하던 경계가 바뀔 수 있다. Run/Actor별 캐시를 쓰더라도 이 관측 동등성은 해결되지 않는다.
서버의 zero 반환 조건과 승인된 lifecycle 동등성이 입증되지 않아 캐싱하지 않았다.
차로변경 tracker의 별도 bbox 조회(`research_lane_change.py`)는 다른 관측 시점/목적이며,
이번 `activation_bounding_box_getter` 계측이 이 조회까지 포함한다고 주장하지 않는다.

### 2. Vehicle Light State

순서상 주요 소비자는 다음과 같다.

1. Z/X input edge의 현재 indicator 확인 (1차 최적화: 이벤트가 없으면 조회 없음).
2. 방향지시등 toggle의 현재 값 읽기와 쓰기.
3. persistence의 실제 light observation → telemetry `indicator` 및 snapshot sample.
4. 차로변경 완료 시 `clear_completed_signal`: fresh read → selected bit clear → readback/이벤트.
5. 그 후 `_update_turn_signal_audio`: 다시 fresh read → 클릭음의 active 여부.

**동일 Python loop는 동일 World frame/동일 관측 순간을 의미하지 않는다.** Async World에서
RPC 사이에도 상태가 바뀔 수 있고, 3→5 사이에 실제 자동 해제가 있다. Telemetry가 ON을
기록한 뒤 Audio가 OFF를 관측하는 것은 정상이다. 앞 값을 공유하면 해제가 늦게 반영된다.
자체 setter가 없는 loop만 공유해도 TM/external 변경과 두 번째 RPC 실패 관측은 달라진다.
OFF 상태도 다음 ON/자동 OFF 전이를 알기 위한 fresh 관측이 필요하다.

공통 light 비용은 CONFIRMED다. 그러나 두 소비자의 관측 시점/타임스탬프·실패 처리를
동일하게 보존하는 재사용은 **UNVERIFIED**이므로 유지했다. Audio 조회 실패는 정상 OFF로
대체하지 않고 원래 예외를 전파하며, 이제 그 실패 시간도 계측한다.

### 3. Checked Manual Control

`ResearchLiveDriverView._apply_manual_control → _send_control → make_checked_control_sender`
는 `client.apply_batch_sync([ApplyVehicleControl(actor_id, control)], False)`를 사용한다.
기존 `manual_batch_rpc_wait`는 명령 구성+동기 호출을 감싸고, 응답 개수/has_error/Actor ID
검증은 그 뒤다. 새 `manual_control_delivery`는 이 검증을 포함한 sender 전체다.

B Training은 MANUAL1902 loop/1902 batch 표본으로 중복 전송 증거가 없다.
NoA에는 해당 batch 표본이 없다. 제어 준비와 응답 검증 안에서 추가적인 RPC는 발견하지 못했다.
Checked sender의 내부 검증 자체는 별도 실측되지 않았다. 평균16.368873ms를 검증 비용으로
해석하지 않는다. async queue, command skip 또는 apply_control 복귀의 적용·순서·오류 발견
동등성이 입증되지 않아 전송 방식은 그대로다.

## D. Changes made

### 적용한 변경: 계측 보완

| 새 항목 | 실제 경계 / 위치 | 포함·제외 |
|---|---|---|
| `manual_availability_refresh` | MANUAL post-control의 `_refresh_availability` | gate 및 availability 이벤트 포함, capture 제외 |
| `activation_geometry_query` | gate의 `observe_with_context` | 정렬 판단 수식·bbox 제외 |
| `activation_bounding_box_getter` | gate의 `vehicle.bounding_box` property | Python/native getter 전체, tracker bbox 제외 |
| `indicator_input_light_rpc` | 실제 Z/X 이벤트가 있을 때 fresh read | toggle용 별도 read 및 telemetry 제외 |
| `manual_control_preparation` | pending input 획득, 기존 clamp/VehicleControl 구성 | 필요 시 fallback input collection 포함 |
| `manual_control_delivery` | `_send_control` 전체 | 기존 batch와 응답 검증 포함, 후속 TS-XW event write 제외 |
| `turn_signal_audio_light_rpc` | Audio 직전 fresh read | `camera.timing_by_mode`에 추가, 기존 audio light_state_rpc와 같은 호출 |

Gate는 optional Protocol로 기존 Diagnostics에 연결해 실험 계층이 scenario 구현을
import하지 않도록 했다. 기존 고해상도 clock을 사용하고 각 신규 호출 span은 `finally`로
성공·실패 시간을 남긴다. MANUAL 전환 직후의 query가 이전 NoA 모드에 분류되지 않도록
실제 상태를 동기화한다. 원래 예외/가용성 실패 반환과 데이터 관측 순서는 보존한다.

기존 `operations` 및 `operations_by_mode`의 count/mean_ms/max_ms 형식은 유지한다.
기존 summary JSON에 추가한 `operation_accumulation` 및 `operation_accumulation_by_mode`는
각 operation의 `total_ms`와 `at_least_100_ms_count`를 저장한다.
미측정 total은null, count는0이며 빠진 모드 항목을0ms로 해석하지 않는다.
Threshold는 **100ms 이상 호출 횟수**이며 새로운 제어/실험 정책이 아니다.
Audio의 새 모드 timing은 기존 camera RunningStats의 count/mean/max/total을 사용한다.

기존 active-window reset/freeze를 그대로 따르며 종료 제동/P7 continuation을 active
gate/operation 통계에 섞지 않는다. Camera timing은 기존 camera 측정창 의미를 따른다.
초기화/활성화 요청에도 gate query가 생길 수 있으므로 query 전체 누적값을 그대로
post-control에서 빼지 않는다. Summary의 `overlap`에도 중첩/alias 설명을 추가했다.

### 효과와 보존 계약

- 이번 효과는 **비용 위치와 실패 시간을 구분할 수 있게 된 것**이다. RPC 수 감소나
  FPS 향상을 구현/검증했다고 주장하지 않는다. 추가 clock/counter의 물리 실행 부하도 미측정이다.
- 매 프레임 별도 파일/이벤트를 쓰지 않고 메모리 집계 후 기존 performance_summary에 저장한다.
  연구 CSV schema, Writer, SessionClock 및 원본 timestamp의 의미/빈도는 유지한다.
- Phase 1 무관 입력 RPC 제거, TS-XW 축/버튼·endpoint, W/S/A/D, priming, NoA 토글/override,
  sticky_control/Checked Response/fail-closed/TM attestation/shutdown brake를 보존한다.
- P5 거리/내비·추천·Z/X/R·GENERAL 구분·t0·관측·자동 OFF·EXIT/MISSED 이후 주행,
  카메라/미러/HUD/60FPS, Training600/F8·M1 600·M2 780·배정·P6/P7 조건을 유지한다.
- Force Feedback, 엔진음, native Wheel 또는 실험 설계 변경을 추가하지 않았다.

## E. Files changed

이번 Phase 2 변경 파일(기존 미커밋 STEP 1~3 목록과 구분):

| 파일 | 변경 범위 |
|---|---|
| `src/experiment/automation_activation_gate.py` | optional diagnostics, geometry/bbox 타이머 |
| `src/experiment/automation_interaction.py` | post-control refresh 계측·현재 mode 연결 |
| `src/scenario/research_noa_runtime.py` | 기존 세션 diagnostics를 interaction에 주입 |
| `src/scenario/research_noa_view.py` | 1차 수정 유지, indicator/preparation/delivery 계측 |
| `src/scenario/driver_view.py` | Audio light RPC의 실패 시간도 finally로 기록 |
| `src/scenario/research_camera_diagnostics.py` | Audio light RPC의 모드별 timing 추가 |
| `src/scenario/research_noa_diagnostics.py` | 새 operation 이름·누적/100ms 이상 집계 |
| `src/scenario/research_performance_summary.py` | additive JSON 필드·중첩 설명 |
| `tests/test_p8_phase2_performance.py` | 신규14개 Offline 회귀 |
| 이 보고서 | 근거·측정 정의·후속 검증 |

## F. Verification results

| 검사 | 결과 |
|---|---|
| 작업 시작 전체 pytest | **1778 passed / 7 failed / 1 skipped**, 35.04초 |
| 최초 신규 계측 테스트, production 수정 전 | fixture 인자 수정 후9 failed: 미지원 diagnostics/미기록 timing 재현 |
| 최종 신규 회귀 | **14 passed**, 0.39초 |
| 관련129개 파일 | **1199 passed / 5 failed / 1 skipped**, 28.87초 (Runner 연결 테스트1개 추가 전) |
| 최종 전체 pytest | **1792 passed / 7 failed / 1 skipped**, 35.20초 |
| 전체 Ruff check | PASS |
| 변경 Python format | 8개 파일 PASS; `driver_view.py` 전체는 기존 포맷 불일치 |
| `driver_view.py` 수정 구간 format | `--range 610-620 --check` PASS |
| `git diff --check` | PASS |
| A/B 원본 해시 대조 | CSV/Manifest 총6개 모두 작업 전후 동일 |

`driver_view.py`는 HEAD 내용 자체도 전체 format check 실패임을 stdin 검사로 확인했다.
기존 카메라 위치/주석/기타 광범위한 포맷을 이번 작업으로 재작성하지 않았다.
다른 변경 파일의 format은 전체 파일 기준 통과했다.

신규 테스트는 fake clock으로 bbox25ms/geometry2ms/refresh27ms 분리를 확인한다.
이는 테스트 입력값이며 CARLA 실측값이 아니다. 실패 호출·NoA skip·brake guard·fresh bbox·
Actor별 객체 분리·전환 모드·active window/freeze·반복 control 전송·Audio ON→OFF·
indicator event-only 측정·실제 Runner 주입을 검증한다. 기존 관련 테스트로 Checked Batch의
Actor/Response/error, shutdown, TS-XW endpoint, P5, 자동 signal-clear, CSV/Manifest,
nominal 시간 및 P7 handoff 경계도 실행했다. 물리 장비 검증은 아니다.

기존 실패7개의 ID는 baseline과 최종 결과에서 동일하다. 신규 실패는 없다.

```text
test_driver_view.py::test_protected_front_cockpit_and_hud_configuration_is_unchanged
test_driver_view.py::test_layout_places_rear_above_road_and_sides_in_lower_dashboard_gaps
test_driver_view.py::test_layout_recomputes_from_custom_window_and_legacy_side_size
test_driver_view.py::test_draw_keeps_front_raw_and_flips_all_three_mirrors
test_driver_view_cockpit.py::test_v_respawns_only_front_sensor_with_cockpit_relative_transform
test_p6_candidate_aoi.py::test_candidate_matches_current_mirrors_without_claiming_measured_accuracy
test_p6_validation_loop.py::test_space_at_previous_segment_end_must_not_skip_next_prepare
```

첫6개는 기존 cockpit/mirror/AOI 기하 불일치, 마지막은 기존 P6 layout 검사 실패 및
예외 처리 문제다. F8 테스트는 이번 baseline/최종 모두 통과했다.
`config/data/maps/tools/src/p8/src/p6/src/p7`의 tracked diff는 없다.

### B 원본 SHA-256

| 파일 (`data/p8_operator/<B>/`) | SHA-256 |
|---|---|
| research_events CSV | `5ec4411ce52cc2c35715d2ea191e6e965fd2dbcdbf76da6cb401e5c7e641fd9a` |
| research_telemetry CSV | `a16d757375fffaec0d03a6d2297cad02512cd9c5d8f976d5cd39bcbfa4a25661` |
| session_manifest.json | `89ac926565b682a8b6283631caa3884f3cbe40a2215e8f401dac7dc7aa062927` |

A 해시도 1차 보고서 부록2와 동일하다. 이번 시작/종료 비교용6개 해시 목록은
`C:\Users\nexta\AppData\Local\Temp\opencode\p8_phase2_original_hashes.json`에 보관했다.

## G. Remaining concerns

1. **Post-control 실제 하위 실행시간은 아직 미측정**이다. 다음 Run부터 새 필드가 채워진다.
   과거 CSV에 없던 값을 사후 생성하지 않았다.
2. Bounding Box 최적화에는 zero/오류/Actor lifecycle 의미를 포함한 동등성 근거가 필요하다.
   lifetime 캐시나 프레임 공유 모두 현재 상태에서 승인 가능한 최적화로 확정하지 않았다.
3. 조명 두 관측을 공유하면 TM 자동 OFF, 관측 시각, 실패 발견 시점이 달라질 수 있다.
4. 필수 Checked Batch의 server 처리/queue/network/World frame 위상 비용은 분리되지 않았다.
   비동기 대안은 적용 순서·오류 응답·driver override·shutdown 동등성 검증이 먼저다.
5. 약1.19초 loop 최대/1.13초 camera 공백은 평균값만으로 원인을 알 수 없다.
   100ms 이상 횟수는 tail 빈도의 단서이지 percentile이나 사건별 인과 분석이 아니다.
6. M1/M2 성능, 실제 TS-XW 응답성 및 P5 물리 EXIT/MISSED 이후 주행 개선은 UNVERIFIED다.
   기존 연구 Route/Spawn/window/3차로 적합성 및 P7 readiness 문제도 이번 범위에서 해결하지 않았다.

## H. Next physical validation

별도 사용자 주행에서 승인된 patched 환경과 runtime attestation을 사용하고,
기존 `python -m src.p8.operator_cli`를 그대로 실행한다. 새 Run ID로 원본을 분리한다.
Training 최대600초/F8, M1 600초·OFF 시작·자유 NoA 선택, M2 780초·배정 모드,
기존 rest와 P7 handoff를 유지한다. 측정 편의를 위한 단축/속도/World/TM/카메라 변경은 하지 않는다.

1. Run ID, 실제 Working Tree 버전, stage_start/end, completion/error, 실제 active duration과
   모드 전환 이벤트를 기록한다. 단순 M2 조건명을 실제 NoA 상태 대신 쓰지 않는다.
2. 각 Stage의 `performance_summary`를 별도로 비교한다. 도로/속도/교통/카메라/HUD,
   pygame focus 및 Unreal Play/Viewport/foreground 조건 차이를 함께 기록한다.
   자유선택 연구 조건을 강제 MANUAL/NoA 시간표로 바꾸지 않는다.
3. 아래 위치에서 **같은 정의의 지표**를 비교한다.

| 비교 대상 | JSON 경로 (`performance_summary` 기준) |
|---|---|
| Display flips/s | `cadence.completed_display_flips_per_wall_second` |
| Driver loops/s | `cadence.completed_driver_loops_per_wall_second` |
| MANUAL/NoA loop mean/max/n | `camera.timing_by_mode.<MODE>.driver_loop` |
| Input/Post-control | `operations_by_mode.<MODE>.input_collection/post_control` |
| Availability/geometry/bbox | `operations_by_mode.<MODE>.manual_availability_refresh/activation_geometry_query/activation_bounding_box_getter` |
| Telemetry light | `operations_by_mode.<MODE>.light_getter` |
| Input light | `operations_by_mode.<MODE>.indicator_input_light_rpc` |
| Audio light | `camera.timing_by_mode.<MODE>.turn_signal_audio_light_rpc` |
| Checked Batch | `operations_by_mode.MANUAL.manual_batch_rpc_wait` |
| Control preparation/delivery | `operations_by_mode.MANUAL.manual_control_preparation/manual_control_delivery` |
| 누적/100ms 이상 호출 수 | `operation_accumulation_by_mode.<MODE>.<OPERATION>` |
| Front camera | `camera.active_drive_window.roles.front` (rate 및 host_arrival_interval) |
| World | `cadence.world_frames_per_wall_second/world_frames_per_sim_second` |

4. `manual_availability_refresh`와 `activation_bounding_box_getter`가 잔여 비용을 설명하는지
   먼저 확인한다. count 차이(초기화/활성화/guard/오류)를 확인한 뒤 포함 관계를 해석한다.
   새 진단은 과거 Run에서 NOT_MEASURED이므로 과거0ms와 비교하지 않는다.
5. Audio light는 기존 `turn_signal_audio_timing.light_state_rpc`와 alias이며 더하지 않는다.
   실제 자동 해제 직후 Telemetry ON→다음 관측 OFF와 Audio 중단을 확인한다.
6. TS-XW/WASD의 조향·가속·브레이크 반응, NoA 전환 후 수동 회복, brake/steering override,
   X 확인·Z/R·자동 OFF, P5 t0 단일성·거리·EXIT/MISSED 후 지속 주행을 기존 절차로 확인한다.
   조작감 보고는 정성 결과로 적고 장치→적용 latency 수치로 바꾸지 않는다.
7. Checked Batch 호출 수/오류·shutdown 기록, 필수 CSV/Manifest와 모드 전환 timestamp를
   함께 보존한다. 실제 오류 주입을 이번 계획에 추가하지 않는다.

성공 판단은 먼저 잔여 비용 분해와 계약 보존이다. 동일 정의·비교 가능한 조건의 실측이
확보되기 전에는 Phase 2 FPS 향상률이나 M1/M2 개선을 주장하지 않는다.
이번 작업은 문서·계측·Offline 검증에서 종료하며 변경은 미커밋·미푸시 상태다.
