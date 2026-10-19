# CARLA V5-P8 MANUAL 프레임 저하 분석 및 최소 최적화

## A. What was checked

### 결론

**MANUAL이 더 느린 현상은 동일 Stage의 모드별 완료 루프 시간으로 확인된다.
그러나 NoA도 빠른 상태는 아니며, Checked Batch 하나만으로 전체 저하를 설명할 수 없다.**
최신 Training의 MANUAL/NoA 평균 루프는 119.190/76.005ms이다.
MANUAL Checked Batch는 평균 23.908ms를 실제 소비한다. 공통 vehicle light 조회도
telemetry에서 약23~24ms, 방향지시등 audio 경로에서 약23~24ms를 소비한다.
추가 MANUAL post-control 비용은 활성화 가능성 재평가/bounding-box RPC가 유력하지만
개별 실측이 없으므로 확정하지 않는다.

유일한 production 수정은 **P5가 설정된 입력 경로에서 Z/X 이벤트가 없는 경우,
사용되지 않는 light-state RPC 1회를 제거**하는 것이다. 필수 제어 전송은 그대로다.
실주행 개선율은 NOT_MEASURED이다.

### 기준과 자료

- 작업 시작 branch: `research/phase-1c-automation`.
- HEAD: `e1af127bb096beaecec3e12cbd2b5bf18a378785`.
- 미커밋 STEP 1~3 production/test/report와 `.omo`, `src/error_notes.py`, 원본 자료를 보존했다.
- Source of Truth 및 `P8_P5_FPS_Diagnosis.md`, STEP 1+2/STEP 3 보고서를 확인했다.
- `data/p8_operator`의 Manifest가 있는 **34 Run**을 읽었다.
  **9 Run / 13 Stage summary**가 있고 25 Run에는 `performance_summary`가 없다.
- 모든 원본 CSV/JSON은 읽기 전용. CARLA 연결, Actor/World/TM/USB 조작은 수행하지 않았다.
- 분석은 Python `csv.DictReader`와 `json.loads(event_value)`로 수행했다.
  summary마다 `run_id`, `segment_id`, CSV record 번호(헤더=1), Stage를 유지했다.
  서로 다른 summary의 평균/최대값을 합쳐 새 구간으로 만들지 않았다.
- 원본 요약은 count/mean/max만 저장하므로 operation p50/p95/p99 및 개별 loop-RPC
  상관계수는 NOT_MEASURED이다. telemetry 행 수는 Display FPS로 사용하지 않았다.
- `performance_summary.status=COMPLETE`는 측정 수집 상태이며 모듈 완주 또는 실험 성공이 아니다.
  아래 실제 로그는 개발 리허설이며 hardware/participant readiness 검증과 구분한다.

### 주요 Run 식별

| 별칭 | 실제 Run ID | UTC 시작 | 사용 목적 |
|---|---|---|---|
| A | `P8-P5-c4cd368b8f384031abd9392016b6bce7` | 2026-10-15 06:02:18 | 최신 P5+TS-XW, 현재 HEAD/dirty 기록 |
| B | `04040027-8b78-4891-9099-3acd3670ec77` | 2026-10-15 02:52:54 | 같은 날 TS-XW, P5 미설정, 중단 구간 |
| C | `241aefc2-1317-4bab-a3ae-986933c56504` | 2026-10-14 13:52:51 | 이전 혼합 모드 Training |
| D | `4abe17cd-9d00-4754-949b-bbee0272a99b` | 2026-10-14 13:49:31 | 이전 혼합 모드 Training |
| E | `fa485504-dd8e-48d9-95e1-6310223548dc` | 2026-10-14 12:03:53 | Training/M1 별도 분석 |
| F | `fd7b0707-879e-4022-9edc-f2084a7b97c1` | 2026-10-14 11:40:40 | MANUAL-only 중단 |
| G | `P8-LC-b226ca9e3fad4b229e6fc33a1de6502d` | 2026-10-13 11:20:57 | 이전 혼합 모드 Training |
| H | `P8-7e5e430633494a1b86a87639c483b0d0` | 2026-10-13 03:02:41 | 600/600/780초의 기존 기록 |
| I | `P8-FPS-LC-2ed4a106055f4519bfc981086f62e451` | 2026-10-13 00:56:41 | 이전 FPS 진단 이후 기록 |

B~G camera metadata HEAD는 `eca8a61a7fa2b066040a79da1cdc7d8e15f45eeb`,
H/I는 `f7308674f2366f6e0b28da86dd874e7de396ba11`이다. 과거 코드를 가져오지 않았다.
A의 dirty flag만으로 과거 실행 Working Tree 전체가 현재와 동일하다고 단정하지 않는다.

### Stage 경계와 종료

Stage 시작/종료는 Manifest의 orchestration session seconds이며 setup/cleanup을 포함한다.
Active는 summary의 `world.wall_elapsed_seconds`이다. 완료 Manifest가 있는 경우
`actual_active_drive_s`와 일치한다. B/E-M1/F는 중단되어 module_runs 결과가 없지만
PARTIAL summary에 active 측정이 남아 있다.

| Run/Stage | Stage 시작→종료(s) | Active(s) | 실제 모드 완료 loop n (M/N) | 종료 / summary | CSV record |
|---|---:|---:|---:|---|---:|
| A Training | 0.016→496.391 | 489.344 | 3069/1618 | F8 조기 종료 / COMPLETE | 21777 |
| A M1 | 543.266→720.047 | 174.109 | 1457/0 | USER_ABORT / COMPLETE | 29087 |
| B Training | 0.015→251.390 | 249.953 | 2462/327 | KeyboardInterrupt / PARTIAL | 10969 |
| C Training | 0.016→39.156 | 34.094 | 237/213 | USER_ABORT / COMPLETE | 1384 |
| D Training | 0.016→100.672 | 99.109 | 837/341 | USER_ABORT / COMPLETE | 3571 |
| E Training | 0.000→80.610 | 75.063 | 62/1377 | F8 조기 종료 / COMPLETE | 2900 |
| E M1 | 83.766→172.094 | 87.407 | 268/1176 | KeyboardInterrupt / PARTIAL | 5822 |
| F Training | 0.000→197.234 | 192.125 | 1783/0 | KeyboardInterrupt / PARTIAL | 3585 |
| G Training | 0.015→139.781 | 138.547 | 161/1578 | USER_ABORT / COMPLETE | 3679 |
| H Training | 0.016→601.813 | 600.078 | 370/11675 | TIME_LIMIT_REACHED / COMPLETE | 24123 |
| H M1 | 857.485→1459.219 | 600.063 | 6625/0 | TIME_LIMIT_REACHED / COMPLETE | 37393 |
| H M2 | 1883.250→2665.906 | 780.078 | 1832/11828 | TIME_LIMIT_REACHED / COMPLETE | 64834 |
| I Training | 0.016→30.188 | 24.938 | 63/319 | USER_ABORT / COMPLETE | 861 |

A Training의 Manifest NoA ON 시간은 123.062s(6회 활성화/해제), M1은 0s다.
A의 active stage 기록은 Training 5.266→494.688, M1 544.547→718.656이다.
이 경계 이벤트와 viewer.run 측정창은 완전히 같은 시점이 아니므로 서로 빼서 FPS를 만들지 않았다.
H는 session state가 SESSION_COMPLETE이나 주행 Stage들은 연구 준비상 PARTIAL이며,
나머지 위 Run의 session state는 ABORTED다.

## B. MANUAL vs NoA Performance Comparison

### Stage별 실제 cadence

각 행은 독립 summary이다. 모두 wall-second 기준이며 World/sim만 simulation-second 기준.

| Run/Stage | Display flips/s | Driver loops/s | World/wall s | World/sim s | Front callbacks/s |
|---|---:|---:|---:|---:|---:|
| A Training | 9.5783 | 9.5781 | 41.2675 | 41.2664 | 41.1026 |
| A M1 (MANUAL-only) | 8.3686 | 8.3683 | 41.9794 | 41.9809 | 41.8141 |
| B Training | 11.1581 | 11.1581 | 41.7198 | 41.7181 | 41.5036 |
| C Training | 13.2000 | 13.1988 | 41.2683 | 41.2613 | 40.7441 |
| D Training | 11.8857 | 11.8859 | 41.9034 | 41.8958 | 40.6616 |
| E Training | 19.1708 | 19.1706 | 40.9922 | 40.9917 | 40.1268 |
| E M1 | 16.5207 | 16.5204 | 38.6811 | 38.6775 | 38.6817 |
| F Training (MANUAL-only) | 9.2800 | 9.2804 | 37.6526 | 37.6479 | 37.2711 |
| G Training | 12.5517 | 12.5517 | 39.7482 | 39.7415 | 36.6518 |
| H Training | 20.0724 | 20.0724 | 41.9495 | 41.9477 | 41.4079 |
| H M1 (MANUAL-only) | 11.0406 | 11.0405 | 44.1737 | 44.1727 | 44.1606 |
| H M2 | 17.5110 | 17.5111 | 41.6907 | 41.6903 | 41.6816 |
| I Training | 15.3200 | 15.3180 | 39.5381 | 39.5486 | 39.3426 |

### 같은 A Training 안의 모드 비교

모드 분류는 `canonical control mode at operation completion`이다. 전환 loop가 섞이며
각 operation의 완료 모드가 다를 수 있다. 모드별 elapsed wall dwell과 World/callback
boundary가 저장되지 않아 엄밀한 모드별 cadence는 NOT_MEASURED이다.
아래 loop 역수는 `1000 / camera.timing_by_mode[mode].driver_loop.mean_ms`로 계산한
**완료 loop span 처리율**이며 Display FPS 또는 전체 모드 체류시간당 rate가 아니다.

| 구분 | MANUAL | NOA_ACTIVE |
|---|---:|---:|
| 모드별 Display FPS | NOT_MEASURED | NOT_MEASURED |
| 모드별 wall-time Driver Loop Rate | NOT_MEASURED | NOT_MEASURED |
| 완료 loop span 역수(회/s, 파생값) | 8.3900 | 13.1569 |
| loop 평균/최대(ms) | 119.190 / 1703.475 | 76.005 / 127.676 |
| 모드별 World Frame Rate | NOT_MEASURED | NOT_MEASURED |
| 모드별 Front Callback Rate | NOT_MEASURED | NOT_MEASURED |
| Manual Batch RPC 평균(ms) | 23.908 | NOT_MEASURED (호출 표본 없음) |
| Input collection 평균(ms) | 15.019 | 15.615 |
| Post-control 평균(ms) | 46.191 | 25.216 |
| Draw / Display flip 평균(ms) | 9.038 / 0.882 | 9.664 / 0.915 |
| Telemetry / event write 평균(ms/호출) | 0.072 / 0.039 | 0.074 / 0.040 |

B Training에서도 loop 평균 93.366/59.591ms, 완료 span 역수 10.7105/16.7811로
같은 방향이다. C/D/E/G/H/I의 각 혼합 Stage에서도 MANUAL 평균 loop가 더 길다.
**저하는 MANUAL 전용 현상이 아니다. NoA도 A에서 평균76ms loop다.**

### A Training operation 상세 (n / mean ms / max ms)

| Operation | MANUAL | NOA_ACTIVE |
|---|---|---|
| manual_batch_rpc_wait | 3069 / 23.908292 / 41.989600 | NOT_MEASURED |
| input_collection | 3069 / 15.018591 / 48.413100 | 1618 / 15.614803 / 37.160300 |
| input_observer | 3069 / 0.173794 / 91.544900 | 1618 / 0.092870 / 46.999600 |
| scheduler | 3069 / 0.035536 / 0.158400 | 1618 / 0.036948 / 0.163900 |
| post_control | 3069 / 46.191086 / 64.526400 | 1618 / 25.216086 / 56.753500 |
| snapshot_getter | 3069 / 0.004567 / 0.085300 | 1618 / 0.003976 / 0.054700 |
| snapshot_actor_getters | 3069 / 0.003044 / 0.103900 | 1618 / 0.002848 / 0.014000 |
| control_getter | 3069 / 0.004328 / 0.062100 | 1618 / 0.002439 / 0.120200 |
| waypoint_query | 3069 / 0.082633 / 0.251700 | 1618 / 0.061542 / 0.366700 |
| neighbor_lane_queries | 3069 / 0.004869 / 0.065000 | 1618 / 0.004642 / 0.055500 |
| light_getter | 3069 / 23.010441 / 42.086900 | 1618 / 24.422105 / 56.162900 |
| traffic_light_actor_lookup | 107 / 0.017224 / 0.078400 | 209 / 0.016197 / 0.060700 |
| traffic_light_observation | 3069 / 0.086464 / 0.714400 | 1618 / 0.129236 / 0.742800 |
| json_serialize | 3069 / 0.035800 / 0.142000 | 1618 / 0.036049 / 0.157000 |
| telemetry_write | 3069 / 0.072414 / 0.290600 | 1618 / 0.074002 / 0.306600 |
| event_write | 15269 / 0.038727 / 0.566300 | 6487 / 0.039618 / 0.434200 |
| draw (camera timing) | 3069 / 9.037971 / 12.043700 | 1618 / 9.663855 / 14.523200 |
| display_flip | 3069 / 0.881672 / 2.195500 | 1618 / 0.915002 / 1.499200 |
| fps_limiter | 3069 / 0.006917 / 0.263300 | 1618 / 0.007985 / 0.258100 |
| audio (light RPC 포함) | 3069 / 23.094703 / 46.125800 | 1618 / 24.324837 / 55.508900 |
| resize.front | 3069 / 1.308181 / 2.448000 | 1618 / 1.507022 / 3.820000 |
| resize.left | 3069 / 0.205525 / 0.438000 | 1618 / 0.209336 / 0.407800 |
| resize.right | 3069 / 0.186942 / 0.420200 | 1618 / 0.190733 / 0.388000 |

`map_getter`는 waypoint_query와 같은 범위를 재는 legacy alias이다.
Post-control은 snapshot/light/기록 등을 포함하고 draw는 resize/blit/HUD 등을 포함한다.
이 표 전체를 합산하면 이중 계산이다. json_serialize는 모든 JSON 호출의 총합도 아니다
(현재 명시적 계측은 traffic-light payload 직렬화). 입력/TS-XW JSON 등은 상위 span에 포함된다.
light_getter는 **차량 lamp/blinker 상태**, traffic_light_observation은 **도로 신호등**으로 서로 다르다.

### Front 카메라 도착과 audio 분해

| 구간 | callback n | 도착 간격 n / 평균 / 최대(ms) | callback 자체 평균/최대(ms) |
|---|---:|---|---|
| A Training | 20113 | 20112 / 24.308953 / 1610.053500 | 0.016670 / 0.140200 |
| A M1 | 7280 | 7279 / 23.861789 / 77.985400 | 0.018243 / 0.138900 |
| B Training | 10374 | 10373 / 24.061887 / 1178.471700 | 0.016923 / 0.175700 |

A Training audio 후속4686회: `light_state_rpc` 평균23.499445/최대55.490700ms,
`audio_update` 평균0.004088/최대0.439900ms. 초기1회도 각각21.7531/0.0065ms다.
즉 audio 항목의 긴 시간은 이 기록에서 소리 생성보다 상태 조회 대기다.
Front callback 작업은 image reference/counter 교체이고 변환·resize는 UI thread에서 한다.
callback span에는 진단 observer 전체 비용이 포함되지 않으므로 전체 sensor thread 비용으로
해석하지 않는다. 장시간 도착 공백과 loop 최대값의 원인이 같다는 증거는 없다.

### 비교 조건의 한계

- A/B 모두 TS-XW Racer open 이벤트, front1280×720 + side480×270 두 개,
  3 RGB camera, Town04, async World/fixed_delta=null, 기존60FPS limiter를 기록했다.
- A는 Exit47/P5 설정, B는 `DEV_OPERATOR_ROUTE_BLOCKED`이고 P5 manifest 이벤트가 없다.
- A Training의 traffic-light 관측에 연결된 속도 표본 평균/범위는
  MANUAL59.346km/h(0~168.789), NoA54.266km/h(0~88.102)다.
  시간가중 평균이 아닌 관측 표본 평균이며 road 집합도 각각30/15개로 다르다.
- A M1은 MANUAL-only지만 road41에서 속도0.008~0.224km/h, 평균0.131km/h로 거의 정지다.
  이것을 같은 도로를 달린 NoA와의 주행 비교로 사용하지 않는다.
- A Training 입력 focus false는11/4687, M1은133/1457; B는230/2790이다.
  이는 pygame 입력 focus이며 Unreal Editor 포커스/Viewport/Play 상태가 아니다.
- 교통량, GPU/Unreal frame profile, Editor throttle 여부, 개별 RPC server 처리/queue/network
  시간은 NOT_MEASURED. 이전 Run들과 개선 전후 controlled comparison은 성립하지 않는다.

## C. Issues found

### 실제 프레임 실행 순서

`DriverView.run()`은 event polling → TS-XW/keyboard 입력 수집 → input observer → scheduler
→ 필요 시 `_apply_manual_control()` → indicator 처리 → post-control 관측/로깅
→ turn-signal audio 상태 조회 → HUD/카메라 draw → 성공 flip → t0 hook → `Clock.tick(60)`이다.
순차 UI thread 작업이므로 blocking RPC 동안 다음 input/render를 진행하지 못한다.

`ResearchLiveDriverView`는 현재 NoA가 제어권을 가지면 manual 전송을 하지 않는다.
P8 TM scheduler가 active일 때도 manual fallback을 막는다. MANUAL에서는 pending input으로
VehicleControl을 만들고 Checked Sender를 1회 호출한다. A Training의 manual control 이벤트
3069회/Batch3069회/완료 MANUAL loop3069회가 일치하고 M1은 각각1457회다.
프레임마다 같은 명령을 임의 생략하거나 두 번 보내는 근거는 없다.
여기서 frame은 driver iteration이며 41Hz CARLA World frame마다 전송한다는 뜻이 아니다.

### 가설별 판정

| 가설 | 코드·실측과 설명 | 한계/반대 근거 | 판정 |
|---|---|---|---|
| MANUAL Checked Batch 대기 | `research_noa_control_delivery.send`: ApplyVehicleControl 생성+apply_batch_sync(False)를 finally로 계측. A 평균23.908ms, 전체 MANUAL loop 누적시간의 약20.06% | server frame phase/네트워크/CPU 대기는 분리 불가. MANUAL-NoA 평균차43.184ms 전체를 설명하지 않음 | **CONFIRMED 기여 요인**, 단독 원인은 아님 |
| TS-XW 폴링 자체 병목 | `tsxw_input.poll`, normalization/merge. A input15ms, B input M0.142/N0.394ms | A input에는 P5 light RPC도 포함. 장치 폴링 독립 timing 없음 | **UNVERIFIED**; TS-XW 탓으로 확정 불가 |
| P5 무입력 light RPC | `research_noa_view._collect_driver_input`이 indicator_confirmation 설정만으로 매번 조회. Z/X 없는 이벤트에는 반환값 소비자가 없음. Offline10개 회귀에서 불필요한 호출 재현 | 해당 호출 단독 실주행 timing은 없음. A/B 차이만으로 절감ms 확정 불가 | **CONFIRMED 불필요 RPC**, 최적화 적용 |
| 공통 lamp 조회 비용 | persistence `light_getter`와 driver-view audio `light_state_rpc`가 각각 약23ms. native Vehicle→Simulator→Client 동기 RPC 경로 확인 | 각각 다른 시점의 관측. 캐시 공유 시 최신성/실패 전파 의미 변화 검증 필요 | **CONFIRMED 공통 비용**, 이번 유지 |
| MANUAL availability/bbox | `AutomationInteractionController.after_control_applied → _refresh_availability → gate.failure → vehicle.bounding_box`. native Actor.cpp→Simulator→Client RPC. A post-control M46.191/N25.216ms | gate 및 bbox 독립 timing 없음. control/관측 시간 위상이 다르고 gate는 brake 등에서 일찍 반환 | **LIKELY 잔여 모드차 기여**, 수정 안 함 |
| 다른 반복 getter/waypoint | snapshot/control/neighbor가 수 µs, waypoint <0.1ms 평균 | 모든 getter가 RPC인 것은 아님. activation gate getter는 이 timing에 미포함 | **RULED_OUT 주요 평균 병목으로서**, 계측된 범위 한정 |
| 과거 도로 신호등 lookup | 기존 문서32ms는 오래된 STICKY Run. A는 전체 traffic_light_observation M0.086/N0.129ms | 기존 no-light lookup 생략이 반영된 경로. 과거값을 현재 병목으로 재사용 불가 | **RULED_OUT 현재 주요 평균 원인으로서** |
| 로깅/직렬화 | event/write 약0.04ms/호출, telemetry 약0.07ms/호출. 같은 loop에 복수 event 존재 | 전체 JSON/flush/OS 지연 모두 별도 분해한 것은 아님 | **RULED_OUT 계측된 평균의 주원인**, tail 전체는 UNVERIFIED |
| 카메라/미러/HUD 렌더 | A draw9.038/9.664ms, flip0.882/0.915ms. 오히려 NoA draw가 조금 더 큼 | 렌더 비용은 존재. GPU/광출력 미측정, 이후 RPC 감소 시 비중 증가 가능 | **RULED_OUT MANUAL 차이의 주원인**, GPU 원인은 UNVERIFIED |
| World/Unreal | A world41Hz, camera41Hz인데 display9.58Hz. limiter 평균0.007ms | World가60Hz가 아닌 이유·장기stall·Editor background throttling은 로그로 불명 | **UNVERIFIED 원인**, 현재 Python 병목 증거와 별개 |
| 복합 원인 | MANUAL 추가 Batch, 공통 lamp 조회, post-control 및 P5 입력 비용이 순차 누적 | 상위/하위 span 및 전환 loop 때문에 단순 합산 금지 | **CONFIRMED 복수 비용**, 각 원인의 실주행 개선율 미검증 |

Native 읽기 전용 근거:
`C:\dev\carla-native-tm-cancel-20261012\source\LibCarla\source\carla\client\Vehicle.cpp:114`,
`Actor.cpp:35`, `detail/Simulator.h:274,455`, `detail/Client.cpp:289,434`.
이는 코드 경로 근거이며 실제 실행 binary 내부 profiler를 대신하지 않는다.

### 필수 제어 전송의 대안 검토

- `apply_control()` 복귀: 현재 checked 응답/Actor 검증 동등성을 증명하지 못하므로 적용 안 함.
- async/background queue: 입력 적용 순서·driver override·TM 권한 전환·종료 제동 시점이
  달라질 수 있다. 동일 fail-closed 계약과 bounded acknowledgment를 먼저 검증해야 한다.
- 동일 control 캐싱/skip: 전송 빈도·적용·실패 발견 의미가 달라지므로 적용 안 함.
- native RPC/server queue 개선: transport timeout/처리시간/World frame과의 정렬을 따로
  측정하고 승인된 별도 native 검증이 필요하다. 이번 Wheel/World/TM 설정 변경 없음.

## D. Changes made

`ResearchDriverView._collect_driver_input()`의 P5 `active_indicator_keys` 계산은
기존 Z/X event 처리가 이미 읽은 `lights`가 있을 때만 수행한다.

- 변경 전: Z/X가 없어도 P5 indicator_confirmation 설정이면 get_light_state()를 호출했다.
- 변경 후: Z/X 없는 빈 입력/W/N/R 등의 loop는 그 조회를 하지 않는다.
- Z/X KEYDOWN/KEYUP 및 TS-XW가 합성한 Z/X에는 기존 fresh read를 그대로 사용한다.
  실제 상태 조회의 예외도 그대로 전파한다. 이전 loop 상태를 캐싱하지 않는다.
- `requests()`는 매번 호출되어 키 edge/반복/거부/무효 UI 처리를 유지한다.
- 바뀌는 결과는 사용하지 않던 조회가 사라지는 것뿐이다. telemetry light 관측,
  audio light 관측, CSV/event 수집, control 전송은 유지한다.
- Checked Batch Actor/Response 검증, sticky_control=true, due_tick_cue=False,
  NoA/Manual 배타성, brake/steer 우선권, shutdown brake는 변경하지 않았다.
- STEP 1~3 P5 자격/기하/거리/t0/결과 계약, TS-XW mapping·정규화,
  화면 해상도/미러/HUD/센서/60FPS 제한, 훈련·배정·시간·P6/P7 정책을 보존한다.

**보장하는 효과:** P5 설정 + Z/X event 없는 input collection마다 필요 없는 RPC 1회 감소.
**보장하지 않는 수치:** FPS 증가율·몇 ms 절감·MANUAL/NoA 격차 해소.

## E. Files changed

이번 작업 파일은 다음 3개다.

1. `src/scenario/research_noa_view.py` — 위의 조건문 최소 수정.
2. `tests/test_p8_input_light_query.py` — 14개 회귀: 무관 입력8, 조회 오류4,
   fresh state/반복/해제1, TS-XW 합성 edge1.
3. 이 보고서.

일회성 읽기 전용 분석 script/파생 JSON은 승인된 임시 디렉터리
`C:\Users\nexta\AppData\Local\Temp\opencode\p8_manual_perf_audit_20261015.*`에 두었다.
새 주행 프로그램이 아니며 원본 경로에 결과를 쓰지 않는다.

## F. Verification results

- 변경 전 전체 pytest 재측정: **1764 passed / 7 failed / 1 skipped** (36.68초).
  이전 기준의 F8 실행시간 실패는 이번 baseline에서 재현되지 않았다.
- 변경 후 전체 pytest: **1778 passed / 7 failed / 1 skipped** (35.56초).
  실패 ID는 변경 전7개와 동일하고 신규 회귀 실패는 없다. F8은 변경 후에도 통과했다.
- 신규 회귀는 fixture 준비 오류를 수정한 뒤 production 변경 전
  **10 failed / 4 passed**로 불필요한 호출을 재현했고 변경 후 모두 통과했다.
- 신규 + P5 view/STEP 1/STEP 3 + TS-XW 회귀 묶음: **85 passed**.
- 관련53개 파일: **520 passed / 5 failed** (9.57초).
  실패5개는 baseline의 DriverView/cockpit 기하 기대값 불일치와 동일하다.
- Ruff 전체 lint PASS, 이번 변경 Python2개 format check PASS.
- `git diff --check` PASS. 분석 시작/종료 SHA-256 대조에서 **34 Run의 원본102개 파일 모두 동일**.
  `config/data/maps/tools/src/p8/src/p6/src/p7`의 tracked diff는 없다.
  HEAD는 동일하며 이번 수정은 미커밋·미푸시 상태다.

기존 실패 ID:

```text
test_driver_view.py::test_protected_front_cockpit_and_hud_configuration_is_unchanged
test_driver_view.py::test_layout_places_rear_above_road_and_sides_in_lower_dashboard_gaps
test_driver_view.py::test_layout_recomputes_from_custom_window_and_legacy_side_size
test_driver_view.py::test_draw_keeps_front_raw_and_flips_all_three_mirrors
test_driver_view_cockpit.py::test_v_respawns_only_front_sensor_with_cockpit_relative_transform
test_p6_candidate_aoi.py::test_candidate_matches_current_mirrors_without_claiming_measured_accuracy
test_p6_validation_loop.py::test_space_at_previous_segment_end_must_not_skip_next_prepare
```

검증에는 MANUAL keyboard/TS-XW·페달 endpoint·NoA/override·checked response/error·shutdown,
P5 관측 및 navigation·Writer·F8·nominal window·M2→P7 continuity를 포함했다.
Offline Fake 기반이며 실제 조작감/주행 제어 지연/FPS 개선 검증은 아니다.
기존 기하 테스트 기대값이나 기능 조건은 완화하지 않았다.

## G. Remaining concerns

1. MANUAL Checked Batch 비용은 남는다. 수동 입력은 다음 loop에만 수집되므로 느린 loop는
   입력 처리 기회도 줄인다. 측정된 input_collection duration은 장치→제어 적용 지연이 아니다.
2. MANUAL availability bbox RPC 및 P5/GENERAL tracker bbox 접근의 개별 비용은 미측정이다.
   geometry/actor lifetime을 확인하지 않고 bbox를 캐싱하지 않았다.
3. light telemetry/audio 조회를 합치는 것은 서로 다른 시점의 값과 예외 의미를 바꾸므로
   추가 설계·검증 없이 적용하지 않았다.
4. loop 최고1703ms와 camera 최고1610ms 공백의 원인은 독립적인 tail 분석이 필요하다.
5. mode별 wall cadence/World/camera 통계와 분위수는 현재 summary로 복원할 수 없다.
6. 기존7개 실패, 연구 Route/Spawn/window/3차로 적합성·물리 P5/P7 readiness는 남아 있다.

## H. Next live validation

이번에는 실행하지 않았다. 사용자가 별도 승인한 실주행에서 기존 운영 Python/attestation과
동일 `python -m src.p8.operator_cli`를 사용한다. 훈련 최대600초, M1 600초/NoA OFF 시작 및
자유 선택, M2 780초/배정 조건, 기존 rest/P7 연결을 유지한다.
승인되지 않은 Route/Spawn/window/속도 수치로 명령을 완성하지 않는다.

### 현재 계측으로 가능한 비교

1. 원본을 덮어쓰지 않는 새 Run ID를 사용하고 patch Working Tree, runtime attestation,
   카메라/미러/HUD, Town04, 교통·속도·도로 구간, 입력 focus, Unreal Editor의
   Play/Viewport/foreground 상태를 기록한다. Editor 설정을 임의 변경하지 않는다.
2. M1의 자연스러운 MANUAL/NoA 선택과 Training의 기존 연습을 이용한다.
   비교를 위해 자유선택 연구 조건에 강제 고정 모드 일정을 넣지 않는다.
3. 각 Stage가 끝나면 같은 `segment_id`의 `performance_summary` 하나씩 확인한다.
   `world.wall_elapsed_seconds`, `cadence` 4지표, front active callback rate/간격,
   `operations_by_mode`, `camera.timing_by_mode`를 위 표와 같은 정의로 비교한다.
4. 수정 효과의 직접 후보는 `input_collection.mean_ms/max_ms`이다.
   `tsxw_input_sample.edges`, GENERAL/P5 요청 이벤트로 Z/X가 드문 구간인지 확인한다.
   keyboard event 전부가 raw로 저장되는 것은 아니므로 정확한 skip 횟수는 현재 로그만으로
   재구성하지 않는다. `manual_batch_rpc_wait` count와 `tsxw_manual_control`/완료 loop도 대조한다.
5. batch는 평균·최대 대기가 그대로 남을 수 있다. `post_control`, `light_getter`,
   audio `light_state_rpc`를 함께 비교해 총 loop 변화와 지연의 이동을 확인한다.
   display flip 호출 duration의 역수를 Display FPS로 사용하지 않는다.
6. `stage_start/end`, `initial_state`, `auto_state`, `disengagement`, Manifest completion,
   실제 active duration을 확인한다. 중단 summary도 정상 완주와 구분해 보존한다.
7. W/S/A/D·TS-XW 조향/가속/브레이크, NoA 전환/override, X OFF→ON 확인·Z/R·반복 입력,
   shutdown, P5 t0/EXIT/MISSED 이후 계속 주행을 기존 절차로 확인한다.
   필수 CSV 관측이 누락되지 않았는지와 종료 후 원본 Manifest/CSV 보존을 검사한다.

### 잔여 원인을 분리하는 최소 추가 계측 제안 (이번에는 미구현)

- 기존 in-memory diagnostics에 `tsxw_poll`, `indicator_input_light_rpc`,
  `manual_availability_refresh`, 그 내부 `bounding_box_rpc` count/total/max를 분리한다.
  새 주행 프로그램/Writer 없이 기존 summary에 넣고 실패 span도 finally로 보존한다.
- 엄밀한 모드별 FPS가 필요하면 mode 전환 시 host 단일 clock의 dwell, completed flip/loop,
  world frame/sim delta 및 callback counts를 동일 boundary로 누적해야 한다.
  전환 loop는 별도 표시하고 M2 performance freeze 경계를 보존해야 한다.
- tail 원인은 짧은 bounded 진단용 loop/RPC timestamp trace와 server profiler가 필요하다.
  평균/최대 summary만으로 percentile/상관관계를 만들어내지 않는다.
- 추가 계측의 오버헤드와 실패/중단 경계를 Offline 확인한 뒤 승인된 별도 주행에서 측정한다.

## 부록 1. Summary 없는 25 Run

아래 Active는 Manifest module_runs에 기록된 값만 적었다. `NM`은 NOT_MEASURED이며
Stage 전체 시간을 Active로 대체하지 않았다. T/M1/M2는 Training/Module1/Module2다.
NoA 시간>0은 실제 ON 기록이 있음을 뜻하며 assignment를 실제 상태로 해석하지 않았다.

| Run ID | Stage session 시작→종료(s) | Active / NoA ON(s) | 종료/상태 |
|---|---|---|---|
| `263b2265-3f25-4572-9899-9aa43536b506` | T0→5.031 | NM | simulator timeout |
| `33f909ac-fed5-4df1-9e5c-b2e8fc086662` | T0→null | NM | Manifest TRAINING/RUNNING, 종료 미기록 |
| `P8-TMDIAG-20261014-145223` | T0→0.453 | NM | EMPTY_REHEARSAL_INVENTORY_REJECTED |
| `P8-TMDIAG-20261014-145029` | T0→5.031 | NM | simulator timeout |
| `P8-TMDIAG-20261014-144355` | T0.016→5.047 | NM | simulator timeout |
| `P8-LC-6e53dcb4511541c8a23fecce10981085` | T0.016→5.016 | NM | simulator timeout |
| `P8-6c9f328ab68b47a98759d6fc288d7ae0` | T0.016→0.125 | NM | libcarla hash mismatch |
| `P8-STICKY-2c168872845147619faf2e1076a7337a` | T0→347.656; M1 354→556.625 | T333.937/219.704; M1 NM | M1 light RPC actor missing |
| `P8-STICKY-de68daa871e44734a3f88d9e660778d9` | T0.015→29.297 | 23.250/20.703 | USER_EXIT |
| `P8-STICKY-a6a9c5a0b71f4b199383840d7ebd2fc5` | T0→33.046 | 26.907/11.593 | USER_EXIT |
| `P8-LANECHANGE-4f8b85359ee04f3eaa8b3ca881383d57` | T0.015→166.937 | NM | light RPC actor missing |
| `P8-LANECHANGE-265c9987fcb4499ca089ea7518716834` | T0.016→121.484 | 115.906/35.124 | USER_EXIT |
| `P8-LANECHANGE-d8cedf307a6e442c8598249e5b8bef83` | T0→33.141 | 27.406/10.078 | USER_EXIT |
| `P8-TM-c8d960aba49d476283e2b28fddc3b02d` | T0.016→22.391; M1 78.641→106.156 | T17.110/2.875; M1 26.562/0 | USER_EXIT |
| `P8-TM-96facbf46374467f95b55e12ccd56675` | T0→159.625 | 154.609/104.626 | USER_EXIT |
| `P8-TM-07c797c1a0e34b3a851cec4c20c4d2cf` | T0.015→80.781; M1 98.031→117.890 | T75.593/35.985; M1 18.922/0 | USER_EXIT |
| `P8-ccef56e7fdc744b386cf4949be7f3a16` | T0→91.891; M1 110.578→225.219 | T82.969/31.656; M1 113.781/37.797 | USER_EXIT |
| `P8-09a13c73282f427081b7c41b4ee64035` | T0→58.828; M1 66.203→77.296; M2 322.484→333.593 | T53.828/31.922; M1 10.078/0; M2 10.047/0 | SESSION_COMPLETE, 단축 개발 기록 |
| `P8-655916c440cb4fb2ae035a3d0e0d246e` | T0→65; M1 80.641→81.047 | T60.062/19.938; M1 NM | transmission prime 오류 |
| `P8-2d3fc72cdaa4456da3ac5ba60ad9a729` | T0.016→60.500 | 55.656/0 | USER_EXIT |
| `P8-a49717ff65f3498fa640b6e847e0636c` | T0.016→34.079 | NM | 당시 speed guard 오류 |
| `P8-0c52a36915654b709fbf38c38627b5c2` | T0.016→9.157 | NM | 당시 speed guard 오류 |
| `P8-f0e63cd1cc304e9ba8fe69f5c7f6e428` | T0→9.125 | NM | 당시 speed guard 오류 |
| `P8-8aeed7afb91347bcb95ea65a512cb324` | T17.797→419.797 | 10.063/7.641 | OPERATOR_ESCAPE_ABORT |
| `P8-695cdb9e12544e819894e7316206706c` | T46.781→86.531 | NM | 당시 speed guard 오류 |

이들에는 요청된 cadence/operation summary가 없어 이번 성능 수치 비교에 넣지 않았다.
기존 STICKY 문서의 신호등 관측32ms는 역사적 근거로만 확인했다.

## 부록 2. 주요 원본 SHA-256

각 경로는 `data/p8_operator/<Run ID>/` 아래다.

| 원본 | SHA-256 |
|---|---|
| A research_events | `6f9edb193e521f3918ede405b13d00fcd5dee0a58ce366016d74e795032794f0` |
| A research_telemetry | `3f5e9f246c85df23e6604331a44afc36d97d652ee165c45dc09654c338cd2ef4` |
| A session_manifest.json | `eb3c2d5b1ebb52a15c2e35abfba0dce5292d3e2e20dc3346a37eb913f365149d` |
| B research_events | `cfce19815522385a801d40da062475f7e14ea8c0c437b813fb18590ac29ace74` |
| B research_telemetry | `33b7925c21f08996921711e6fba2be7174dfa82749dd1b2e366aa70b270920f0` |
| B session_manifest.json | `5524a5b28b823349af1a3a9bf9826228b84ececdc00c1b62f5dc906645bc6c96` |
