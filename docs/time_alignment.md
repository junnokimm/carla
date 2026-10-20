# 차량–시선 시간 정렬 변경 및 검증

2026-10-19. 제어용 시계와 기존 주행·입력·NoA 해제·위험사건 정책은 유지한다.
참가자·보정 정보, AOI 모양·분류, SuRT APK 연결은 이번 변경 대상이 아니다.

## 기록과 해석

- 기존 `session_elapsed_s`, `host_monotonic_ns`, CARLA 프레임·시뮬레이션 시각을 보존한다.
- 세션 원점에 `perf_counter_ns`를 추가한다. 새 `analysis_elapsed_s`는 그 원점으로부터의 QPC 초 단위 시간이다. **기존 `session_elapsed_s`와 직접 빼거나 같은 열로 합치지 않는다.**
- 차량 관측에는 스냅샷 조회 전후의 QPC를 기록한다. 후처리 차량 CSV의 `analysis_elapsed_s`는 조회 완료 시각이고 `record_observed_analysis_elapsed_s`는 전체 관측 완료 시각이다. 스냅샷 프레임·시뮬레이션 시각도 함께 남긴다. 서버 물리 프레임이 생성된 정확한 PC 시각이라는 뜻은 아니다.
- 운전자 입력은 기존 수집 함수의 전후 시각을 추가한다. M2 원본 입력 배치와 핸들 입력 이벤트에 `poll_started_perf_counter_ns`, `poll_completed_perf_counter_ns`가 남는다. 입력값·보정·제어 적용 순서는 유지한다.
- 위험사건 표시에는 화면 flip 완료 직후 QPC를 기록한다. 사건 이벤트에는 기존 로그를 flush하기 전 관측 QPC를 추가한다. 실제 화면 광자 출력·피험자의 인지 시각은 검증하지 않았다.
- 모듈 시작·종료 및 일시 정지 구간도 QPC로 기록하여, 시선 집계의 경계에 같은 시계를 사용한다.

Tobii의 `system_time_stamp`(측정 시각)를 SDK–QPC 대조 자료로 변환한다. 새 실행에서는 QPC를 해상도가 낮은 부모 프로세스 monotonic 시계로 다시 변환하지 않는다. 원본 시선 좌표·분류는 유지한다.

## 정렬 검증과 오류 처리

캡처 전체에 걸친 대조 자료 중 시작 근처·중간·종료 근처의 **다섯 점을 계산에서 제외**하고 독립 검증에 사용한다. 양 끝점은 계산 자료에 남겨 외삽 없이 전체 기록 범위를 확보한다. 최소 10개 대조 자료(계산용 최소 5개, 검증용 5개)가 필요하다.

기존 소프트웨어 기준(계산 잔차 및 검증 오차 10ms 이하, 대조 구간 반폭 20ms 이하, 기울기 변화 2,000ppm 이하)을 사용한다. 오프셋·기울기·잔차·다섯 검증 오차·처음과 마지막 검증 오차의 차이를 저장한다. 이 기준은 장비의 물리적 정확도 인증이 아니다.

정밀 시각을 읽지 못하면 null/빈 필드를 기록한다. 정렬 계산·로그 훅 오류를 제어 중단 조건으로 쓰지 않는다. SDK 시계 대조에 실패해도 원본 시선 수집을 계속 시도한다. 시간 경계가 누락되면 거친 시각을 정밀 시각으로 대체하지 않는다.

정렬 실패 또는 검증 범위 밖 시선은 `UNALIGNED`나 명시적인 QPC 콜백 근사값으로 표시한다. 해당 집계에 근사값이 포함되면 원본 비율·관측 시간은 남기되 시간 검증을 통과한 분석값은 null로 둔다. 추적 불가 구간을 태블릿 주시나 0초로 채우지 않는다.

## 실행 후 확인할 파일

기존 `run_pilot.cmd` 명령을 그대로 사용한다. 새 실행의 원본과 아래 파생 파일은 기존 결과 폴더 및 ZIP 저장 흐름에 포함된다.

| 파일 | 확인 내용 |
|---|---|
| `session_manifest.json` | `origin.perf_counter_ns`, `observation_clock` |
| `research_telemetry_*.csv`, `research_events_*.csv` | 추가된 QPC 원본 필드, 기존 시간·입력값 |
| `collection/clock_sync.json` | `analysis_clock=SHARED_QPC`, SDK–QPC 검증, 독립 대조점 5개, 차량 시각 누락 수, 모듈 경계 오류 |
| `collection/MODULE_1_vehicle.csv`, `MODULE_2_vehicle.csv` | 차량 `analysis_elapsed_s` 및 스냅샷 조회 구간 |
| `collection/events_time_aligned.csv` | 원본 이벤트 종류와 새 분석 시각의 간단한 색인 |
| `collection/*_aligned_gaze.jsonl` | 시선 `analysis_elapsed_s`, `clock_domain`, `time_basis` |
| `collection/m2_gaze_metrics.json` | 같은 시계의 M2 경계로 집계한 비율·시간과 시간 검증 자격 |
| `collection/hazard_response_metrics.json` | 기존 반응 지표 및 별도 `qpc_alignment` 지표. 반응 기준·5초 창은 동일 |

물리적인 화면 지연, 카메라 프레임의 나이, Tobii 장비 자체 지연을 제거했다는 의미는 아니다. 시선·사건의 관측 시각을 일관된 소프트웨어 시간축에 연결하는 변경이다.

## 직전 실행 재검산

대상: `P8-PILOT-236e614c6aec4ed685000e08aeb8bd80`.
원본을 덮어쓰지 않고 `data/time_alignment_verification/replay_236e_final/`에 저장했다.

- 원본 31개 파일의 SHA-256을 재분석 전후 비교했다.
- SDK–QPC 독립 대조 5점의 최대 오차: 약 **0.0181ms**.
- 기존 QPC–부모 monotonic 변환의 최대 잔차: **10.342ms**. 기존 차량 이벤트에 QPC가 없으므로 완전한 정밀 정렬 성공으로 판정할 수 없다.
- 콜백 전달 시각과 측정 시각의 차이: 중앙값 **18.554ms**.
- 거친 부모 시계 변환을 진단 목적으로만 사용한 사후 추정에서 태블릿 비율은 **34.803946% → 34.803092%**, 관측 태블릿 시간은 **221.531811초 → 221.705172초**였다.
- M2 활성 구간과 위험사건 이전 구간에서 각각 표본 1개의 포함 여부가 달라졌다. AOI 분류값을 바꾼 것은 아니다.

사후 추정은 오차 기준을 통과한 새 정밀 기록을 대신할 수 없다. 자세한 전후 수치와 불확실성은 `comparison.json`, 원본 해시는 `source_sha256.json`에 있다.

재검산 명령(출력은 존재하지 않는 새 폴더여야 한다):

```powershell
cd C:\dev\carla
& .\.venv\Scripts\python.exe -B -m tools.recheck_clock_alignment data/p8_operator/P8-PILOT-236e614c6aec4ed685000e08aeb8bd80 --output data/time_alignment_recheck_new
```

## 검증 근거와 남은 실기기 확인

`data/time_alignment_verification/`에 다음 결과를 남겼다.

- `regression_tests.xml`, `regression_tests.txt`: 전체 오프라인 회귀 **2,404건 통과, 2건 건너뜀, 기존 화면 배치 실패 5건**. 프로젝트 전용 NoA/SDL을 사용했고 실행기는 CARLA 접속과 실제 조이스틱 열기를 금지한다.
- `baseline_existing_failures.xml`: 화면 배치 관련 기존 실패 5건을 수정 전 HEAD 소스로 재현했다. 작업 파일을 되돌리지 않고 메모리에 읽어 비교했다.
- `final_focus.xml`: 시각 누락·콜백 지연·독립 검증 실패·SDK 시계 오류·입력 유지·종료 흐름에 대한 집중 검증 **84건 통과**.
- `shared_qpc.json`: 실제 Python 3.12/3.10 프로세스의 QPC를 12회 대조하여 같은 기준임을 확인했다. CARLA/Tobii SDK에는 연결하지 않았다.

실제 장치로 새 코드를 실행한 주행 검증은 아직 수행하지 않았다. 다음 짧은 테스트에서는 핸들·페달·NoA 전환과 함께 `clock_sync.json`의 다섯 독립 대조점, 차량 QPC 누락 수, M2 경계 및 시선의 `time_basis`를 확인해야 한다. 완료된 M2 집계를 확인하려면 M2를 정상 종료하거나 진행자 종료로 결과를 저장한다. 위험사건 발생을 강제로 앞당기는 설정은 추가하지 않았다.
