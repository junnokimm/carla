# P8 파일럿 안정화 분석 및 수정 결과

현재 구현은 개발 파일럿이며, 이번 변경으로 정식 파일럿 준비가 완료된 것은 아니다.
확인된 정리 오류 경로와 진단 정보 누락을 수정했다. 일반 주행 충돌의 물리적 원인,
네이티브 Fail Fast 원인, 실장비 성능 개선 및 실제 장애물 노출은 아직 검증되지 않았다.

## A. What was checked — 확인한 범위

### 기준과 보존

- 프로젝트: `C:\dev\carla`, 브랜치 `fix/p8-pilot-input-20261017`.
- HEAD: `4ab636d383aa5e884427e1db937abf9f829ee09e`.
- 시작 당시 추적 변경 9개, untracked 파일 577개, staged 변경 없음.
- 작업 시작 파일 해시는 `pilot_stability_verification/baseline.json`에 보존했다.
  다른 브랜치·stash·WIP를 가져오지 않았다. commit/push/branch/reset/restore/clean은 수행하지 않았다.
- `run_pilot.cmd`, 설정, 기존 `.omo`, 원본 CSV/manifest/ZIP, 가상환경과 native 바이너리를 변경하지 않았다.
- 실행기: `run_pilot.cmd → tools.run_pilot → src.p8.operator_cli`.
- 지정 Python: `C:\dev\carla-native-tm-cancel-20261012\.venv-p8\Scripts\python.exe`.
  실제 조회: Python 3.12.10, carla 배포 버전 0.9.16, pygame 2.6.1,
  pytest 8.4.2, Ruff 0.16.5. 이 경로는 실행 환경 확인에만 사용했고 다른 작업 폴더의 소스는 가져오지 않았다.
- libcarla: 해당 환경의 `Lib\site-packages\carla\libcarla.cp312-win_amd64.pyd`.
  SHA-256 `636d249a6898feb4a6a8d166917ddde04f26b70dab9e95c1d3d50e9acf3ca471`.
  배포 버전 문자열은 patched binary와 서버의 실제 호환성 증명이 아니다.
- CARLA 서버 연결, Unreal 실행, 실제 TS-XW 열기, 주행 및 시스템 설정 변경은 수행하지 않았다.

### 설계·코드·자료

`CARLA_Experiment_Design_v5_Source_of_Truth.md`, `P8_Full_Experiment_Integration.md`,
`P8_P7_V5_Hazard_Status.md`, M2 경로/배치 수정 문서, 기존 MANUAL 성능 Phase 2/3 문서와
해당 코드·테스트를 읽었다. 원본 `C:\Users\nexta\Downloads\실험설계 v5.docx`는
OOXML의 문단·표 텍스트를 읽어 설계 내용을 대조했다. 문서 레이아웃을 수정하지 않았다.

실행 자료는 다음 네 run의 원본 events/telemetry/manifest와 존재하는 export ZIP이다.
CSV는 `csv.DictReader`, JSON payload는 `json.loads`로 읽었으며, 아래 CSV 위치는
헤더를 1로 세는 **논리 레코드 번호**다. 재현용 읽기 스크립트는
`pilot_stability_verification/analyze_pilot_logs.py`에 있다.

| Run 접미사 | 확인 목적 |
|---|---|
| `bdb31eb6b3874f43b93a3313b4b9822f` | 일반 M2 충돌, 정상 오류 종료·부분 저장, 성능 |
| `42c0b587e1e64ca0b08deaf0742ca01c` | 네이티브 종료, 미완료 manifest·ZIP 누락 |
| `9813fc1585164f4494dc4f638e845d3a` | 이전 화물 높이 보정/정지성 판정 |
| `ad1d1304cc994408a5aba3ff697334b2` | 준비 조건 거부로 위험사건 미발생 |

### 임의로 해소하지 않은 설계 차이

| 항목 | 원본 v5 | 현재 개발 프로그램 | 필요한 판단 |
|---|---|---|---|
| 사건 시점 | M2 13분 + 마지막 사건, 주의배분 구간에서 사건 제외 | 개발 v1에서 400~600초 사건 시도, 같은 M2를 780초까지 유지 | 공식 파일럿에서 적용할 사양과 시선 분석 구간 확정 |
| 휴식 | 2분 | SPACE로 조기 진행 가능, 실제 시간 기록 | 정식 운영의 2분 준수 방식 |
| 배정 | M1/M2 독립 1:1 사전 블록 배정표 | 실행기 JSON 기본값 NO_SURT/MANUAL, CLI 덮어쓰기 | 사전 배정표와 운영자 임의 선택 방지 연결 |
| M2 실제 모드 | 배정된 모드로 주행 | DEV MANUAL에서도 NoA 사용 가능, deviation 기록 | 정식 조건 통제 방식 |
| 위험 반응·구조 | 임계값/경계는 테스트 후 확정 | raw/proxy 기록, 연구용 값 미완성 | 연구자 승인 및 물리 검증 |

SoT의 2026-10-17 덧붙임은 개발 v1 예외이며 교수님 최종 승인으로 표시되어 있지 않다.
이번에 어느 쪽도 임의 변경하지 않았다. `PARTICIPANT_RUNTIME_NOT_IMPLEMENTED` 차단도 유지한다.

## B. Issues found — 발견한 문제

### A — 일반 M2 충돌 종료

**확인된 직접 경로:** M2 활성 시작 때 `MovingHazardBackend.start_observation()`이 Ego에
collision sensor를 붙인다. callback은 대기열에 이벤트를 넣고, 매 주행 루프의
`OperatorM2Hazard.after_control()`이 `backend.failures()`를 확인한다. 이벤트가 한 건이라도
있으면 scene을 ABORTED로 만들고 `P7Error:M2_V1_COLLISION`을 발생시킨다.
이는 위험사건 생성 전 SCHEDULED 상태에도 적용된다. 기존 세션의 종료 제동·부분 저장으로 이어진다.

- bdb3… events 레코드 40332: 예정 노출 497.052920527초.
- 레코드 50680: 활성 184.969초, 실제 모드 MANUAL, 상대 actor ID 0,
  충돌 frame 618181, simulation time 7557.424334757초.
- 종료 정리 기록에 **다음 frame 618182의 두 번째 actor 0 충돌**도 존재한다.
  단발 이벤트였다고 해석할 수 없다.
- 준비 시작·lead/cargo spawn 기록 없음. 이 실행에서 위험사건 차량/화물과 충돌한 것은 아니다.
- 마지막 telemetry: frame 618180, 85.3632 km/h, steer 0, throttle 0.591796875, brake 0, lane -4.
  이 값은 충돌 frame 자체가 아니라 직전 관측이다.
- P5 레코드 50673: road 45 / section 0 / lane -4, `route_matched=false`,
  기준 경로 거리 5.9039m, `target_boundary_valid=false`, 실제 모드 MANUAL.
  그 전후 manual change proxy와 navigation unavailable 기록이 있다.
- 일반 telemetry에는 world XYZ가 없고 당시 raw SCHEDULED 자료에도 완전한 Ego pose가 없어
  충돌 지점을 원자료만으로 정확히 복원할 수 없다. 경로 이탈·출구 안내와의 시간적 인접성은
  확인되지만 시스템이 차량을 잘못 조향했다는 인과 증거는 없다.
- manifest는 ABORTED, export ZIP 존재. cleanup failures/remaining owned actor IDs는 비어 있다.

**물리 원인 미확정:** 당시 로그에는 객체 type/semantic tag/impulse가 없다.
[CARLA 센서 문서](https://carla.readthedocs.io/en/latest/ref_sensors/)는 건물·수풀 등에도
식별용 actor를 생성한다고 설명한다. ID 0만으로 허위 충돌이라고 판정하거나 무시할 수 없다.
센서 이벤트는 엔진이 보고한 접촉 증거이며, 화면에서 운전자가 인지한 충돌 여부와는 다르다.

**수명주기:** world callback은 256개 snapshot만 유지하고 충돌 callback은 파일 I/O/RPC를 하지 않는다.
M2 종료 시 tick 등록 해제 → sensor stop → owned actors destroy를 시도한다.
카메라는 각 runner의 viewer가 정리한다. bdb3… Training/M1/M2 카메라는 각 3개이며 front ID는
153/157/161이다. 이 정보만으로 world 전체에 이전 actor가 전혀 남지 않았다고 보장할 수는 없다.
추가적인 전체 world actor inventory 시계열은 기존 로그에 없다.

안전 종료 자체는 재현되었다. 실제 충돌/오신호 구분은 재현하지 못했다.
센서, ID 0 처리, impulse threshold, 종료 정책은 변경하지 않았다.

### B — Python 네이티브 종료

- 42c0… manifest는 `MODULE_2_RUNNING`, cleanup completed=false로 남아 있다. export ZIP은 없다.
- 마지막 events 레코드 64590은 `tsxw_manual_control`, UTC 2026-10-17 11:51:43.633,
  KST 20:51:43.633이다. 마지막 저장 raw 표본은 M2 212.703초, MANUAL이다.
- Windows Application 이벤트 1000/1001에서 KST 20:51:43~46의 Python 오류를 확인했다.
  `ucrtbase.dll`, `0xc0000409`, parameter 7, BEX64, report ID
  `4c222896-79df-46b1-9e40-315923bc897b`. 실행 로그와 시각이 부합한다.
- KST 19:07:38에도 같은 서명 오류가 있다. 두 오류의 스택이 같다는 뜻은 아니다.
- WER에 표시된 Python 경로는 venv의 `sys.base_prefix`와 일치한다.
  이 경로만으로 잘못된 interpreter를 실행했다고 판단하지 않는다.
- 사용자 CrashDumps 경로는 존재하지 않았다. WER ReportArchive의 Python 폴더는 발견했지만,
  권한 확대 조회 후에도 Windows ACL 접근 거부가 남아 **내용과 덤프 유무는 확인하지 못했다**.
  소유권·ACL·레지스트리는 변경하지 않았다.

[Microsoft fast-fail 문서](https://learn.microsoft.com/en-us/cpp/intrinsics/fastfail?view=msvc-170)에
따르면 0xC0000409는 일반 예외 처리로 계속할 수 없는 fast-fail 경로다.
이벤트의 DLL 이름과 마지막 CSV 이벤트는 호출 스택이 아니다. libcarla/SDL/Python C++ runtime,
callback 수명·동시성 중 어느 것이 원인인지는 아직 모른다. MANUAL 프레임 저하와 동일 원인이라는
증거도 없다. Python finally/ZIP export를 추가하는 것만으로 이 종료를 복구한다고 주장하지 않는다.

별도로 **오디오 close 예외가 카메라 정리를 건너뛰는 Python 오류 경로**를 재현·수정했다.
이것은 실패 시 리소스 정리의 결함이며, 42c0… 충돌 원인으로 입증된 것은 아니다.

### C — 위험사건 미발생과 화물 배치

- ad1d…: 준비 거부 172회. fork/disconnected 68, curvature 59, start 29, speed unavailable 16.
  준비·spawn·노출 없이 NOT_OCCURRED로 끝났다.
- 9813…: 가림 부적합으로 25회 대기 후 579.421초에 준비 시작. 579.687초 CARGO_MOVED.
  기존 배치 기준 z=0과 다음 snapshot의 약 0.025553m 차이가 코드의 지면 보정량과 일치한다.
- 현재 working tree에는 요청 frame 기록 → 더 새로운 WorldSnapshot 확인 → 실제 pose를
  정지 기준으로 고정하는 패치가 반영돼 있다. 렌더링의 과거 snapshot은 기준을 바꾸지 않는다.
  기존 5mm/5mm·s⁻¹/0.01° 기준도 보존되어 있다.
- delayed setter/readback 회귀는 MANUAL/NOA 두 경로에서 통과했다.
  이번에 배치 알고리즘을 새로 변경하지 않았다. bdb3…는 185초에 끝나므로 이 패치의
  실제 위험사건 배치 성공을 입증하지 못한다.

| 흐름 | 현재 연결과 한계 |
|---|---|
| 400~600초 계획 | 전용 seed/RNG 1회, 반복 시도는 같은 기회 |
| 도로·가림 검사 | preflight/connected route/occlusion, 부적합은 대기·미발생 가능 |
| 생성·배치 | owned lead/cargo, 단일 높이 보정, 후속 snapshot 확인 |
| lead 차로변경 | 명령/제어 반환과 실제 횡방향 이동·완료 관측을 별도 기록 |
| 최초 노출 | world geometry와 실제 선택 카메라 frame/flip 연결 후보 |
| 실제 가시 시각 | bbox projection proxy이며 mesh 픽셀·다른 가림·광자/지각 시각 미검증. `hazard_visible_t=null` |
| 대응/TTC | raw 입력·readback·clearance/closing-speed TTC 후보 보존. 유효 brake/steer onset·반응 TTC·연구 outcome 미완성 |
| 안전 종료 | 같은 M2 collision sensor가 일반 주행부터 담당; 별도 위험 전용 센서를 추가 생성하지 않음 |

약 4초 TTC는 개발 목표이지 확정 연구 임계값이 아니다. 반응 임계값·미발생 처리·구조 경계는
승인 없이 채우거나 완화하지 않았다. 시간창 안에 사건이 항상 발생함을 보장하는 상태가 아니다.

### D — MANUAL 성능

기존 `performance_summary`의 별도 cadence/operation 계측을 사용했다.
telemetry 표본 수를 렌더링 FPS로 바꾸지 않았고, loop 평균의 역수도 FPS로 보고하지 않았다.

| bdb3… 구간 | 완료 화면 flip / wall second | world frames / wall second | 완료 loop mean / max (ms) |
|---|---:|---:|---:|
| Training, F8 조기 종료 약 6.64초 | 8.883 | 42.614 | MANUAL 110.534 / 1086.795 (59개) |
| M1 약 600초 | 21.550 | 43.230 | MANUAL 94.362 / 101.769 (35개); NoA 46.254 / 102.044 (12,898개) |
| M2 약 184.99초, 모두 MANUAL | 10.925 | 43.572 | 91.432 / 145.614 (2,021개) |

M2 front callback은 약 43.559회/초, 도착 간격 평균 22.955ms, 최대 92.157ms이다.
이 구간에서 서버·카메라 수신보다 Python 화면/제어 루프가 느린 것은 확인된다.
CPU/GPU 사용량·메모리 추이·photon latency는 기록되지 않았다.

| M2의 실제 계측 범위 | 평균 ms | 해석 |
|---|---:|---|
| checked manual batch RPC wait | 19.467 | MANUAL의 동기 응답 대기, 오류 확인 계약 존재 |
| post_control 전체 | 45.457 | 아래 activation bbox와 telemetry light 조회 등을 포함 |
| activation bounding box 조회 | 22.319 | MANUAL availability 재평가 경로 |
| telemetry light 조회 | 22.250 | post_control 내부 |
| turn-signal audio 전체 | 21.940 | 거의 전부 별도의 light RPC 21.906ms |
| draw | 3.419 | 화면·미러·HUD 전체; 하위 conversion/resize를 중복 합산하지 않음 |
| display.flip 호출 | 0.561 | 호출 소요시간이며 FPS가 아님 |
| input collection | 0.145 | TS-XW/키보드 입력 수집 |
| telemetry write / event write | 0.070 / 0.036 | 해당 계측 범위 평균; JSON·hazard flush 전체와 같지 않음 |

동기 전송·조회가 이 구간의 느린 루프를 대부분 설명한다. 그러나 각 RPC의 서버 내부 대기 원인은
미확정이다. 같은 frame처럼 보여도 bbox/조명 관측 시점과 오류·복구 의미를 바꾸는 캐싱,
unchecked 제어 전송으로 바꾸지 않았다. 기존 클릭음도 끄지 않았다.

M2 최악 완료 loop 145.614ms의 **같은 loop**에서 post_control 67.196ms,
audio 51.905ms, manual delivery 22.715ms가 기록된다.
42c0… M1에서는 NoA loop 최대 1025.917ms도 존재하므로 긴 정체를 MANUAL만의 현상으로 단정하지 않는다.
모드 체류·도로·속도·교통을 통제한 전후 비교가 아니며 성능 개선율은 산출하지 않았다.

추가로 M2 `after_control`과 `after_display_completed`가 기존 세부 계측에서 빠져 있었다.
이 둘은 위험 준비/geometry/raw flush를 실행하므로 비용이 잔여 시간으로 남았다.
이를 분리 계측하고 완료 loop p95/p99의 구간 추정치를 추가했다. 과거 로그에는 p95/p99가 없으며
평균·최대값만으로 이를 복원할 수 없다. 장시간 메모리/actor 수 누적과 시간대별 FPS는 별도 측정이 필요하다.

## C. Changes made — 수정 내용

| 파일 | 이번 변경 |
|---|---|
| `src/p7/collision_evidence.py` (신규) | 이벤트의 객체 type/tag, Ego/sensor ID, impulse, sensor pose를 값으로 복사. 메타데이터 누락은 null |
| `src/p7/moving_backend.py` | 충돌 시 기존 256-frame cache에서 같은 frame Ego pose/velocity 연결. 최신 제어 관측의 위치/도로/차로/제어를 별도 basis로 기록 |
| `src/scenario/driver_view.py` | 오디오 오류 후에도 모든 카메라 stop/destroy 시도, 원래 오류 보존. M2 두 hook에 finally 기반 시간 계측 |
| `src/scenario/research_noa_view.py` | vehicle audio 오류로 base camera 정리가 생략되지 않도록 보완. 입력 정리의 선행 오류도 보존 |
| `src/scenario/loop_timing_distribution.py` (신규) | 고정 1001-bin, 1ms 간격 histogram. p95/p99를 bounds로 기록, 1000ms 이상은 overflow 구간 |
| `src/scenario/research_camera_diagnostics.py` | 전체/완료 시점 모드별 histogram과 같은 loop의 M2 처리시간을 기존 summary에 추가 |
| 신규 테스트 3개 | collision evidence, scene timing/quantiles, audio cleanup 실패 경로 |

연구 조건·시간·차량 제어·센서 구성·sampling·raw 기록을 바꾸지 않았다. 추가 collision 정보는
실제 충돌 시에만 직렬화한다. callback에 RPC/파일 쓰기를 추가하지 않았고 native event 객체도 보관하지 않는다.
일반 주행 중 추가 계측은 clock 읽기와 상수 크기 집계이며 매 frame 진단 출력을 추가하지 않았다.
histogram은 전체/모드당 1001개 counter로 크기가 제한된다. 1초 이상 percentile 구간은 넓을 수 있다.
완료되지 않은 실패 loop는 percentile에서 제외하고, 해당 hook 시간은 별도로 보존한다.

독립 histogram 100,000회 갱신의 단발 microbenchmark는 약 0.295µs/회였다.
이는 실제 전체 계측 오버헤드나 실주행 성능 개선 측정이 아니다.

## D. Verification results — 검증 결과

동일한 현재 P8 Python에서 dummy display/audio를 사용했다.
`run_offline.py`는 CARLA Client 생성과 물리 Joystick 생성을 차단한다. native math 객체와
가상 world/장치는 사용하므로 실제 서버/운전 장비 검증과 구분된다.

| 검증 | 결과 |
|---|---|
| 최초 샌드박스 전체 시도 | 임시 폴더 권한 오류 다수. 기준 회귀 결과로 사용하지 않음 |
| 수정 전 Windows 오프라인 전체 | 2,119 passed / 7 failed / 2 skipped |
| 진단 공백 재현 | 신규 7개가 누락 field/timing 때문에 실패 |
| base audio cleanup 결함 재현 | 신규 4개에서 sensor stop/destroy 호출 누락으로 실패 |
| M2 배치·경로·위험사건·화면·성능 집중 회귀 | 100 passed |
| 수정 후 Windows 오프라인 전체 | 2,135 passed / 동일 7 failed / 2 skipped (신규 16개 통과) |
| 최종 예외 처리·import 정리 후 cleanup/timing 재검증 | 별도 `final_cleanup_timing.txt` 참조 (최종 13개) |
| Ruff 전체 src/tests/tools | 기존 SIM102 및 RUF059 잔여; 이번 추가 코드의 신규 위반 없음 |

전체 테스트의 기존 실패 7개는 driver view 미러 배치 4개, cockpit transform 1개,
P6 candidate AOI 1개, P6 validation loop 1개다. 마지막 항목에는 기존 frozen exception의
traceback 설정 TypeError도 함께 나타난다. 이 작업에서 연구 화면/AOI를 임의 수정하지 않았다.
신규 실패가 생기지 않았음을 JUnit testcase ID로 비교한다. 전체 통과로 표현하지 않는다.

검증 기록: `pilot_stability_verification/`의 baseline/final JUnit·텍스트,
reproduction, cleanup_reproduction, focused_tests, ruff_final, log evidence JSON.
원본 파일 삭제 없이 이번 변경은 기존 파일 4개에 한정된다(신규 파일 별도).

## E. Remaining concerns — 남은 우려 사항

1. 일반 M2의 actor 0 접촉이 실제 도로 구조물/다른 환경 접촉인지 아직 모른다.
   다음 기록의 type/tag/impulse와 collision-frame pose, 실제 화면을 대조해야 한다.
   callback 순서 때문에 같은 frame snapshot이 아직 없으면 해당 필드는 null로 남는다.
   최신 관측 frame을 충돌 frame으로 위장하지 않는다.
2. Fail Fast의 native call stack이 없다. 아직 해결하지 않았고 다음 주행도 예기치 않게 끝날 수 있다.
3. MANUAL 약 11Hz 화면/제어 루프는 관측되지만 이번 변경은 최적화 완료가 아니다.
   조건 간 기술 차이가 연구 결과를 왜곡하지 않는다는 검증은 남아 있다.
4. 위험 준비·가림 조건을 만족하지 못하면 NOT_OCCURRED가 가능하다.
   배치 수정 테스트 통과는 실장비 노출/TTC 검증이 아니다.
5. 실제 hazard_visible_t, 유효 반응 onset, 연구 outcome, 최종 TTC 결과와 Tobii/태블릿/설문 연결은
   별도 완성·검증이 필요하다. 참가자 준비 차단을 해제하지 않았다.
6. raw 파일이 남아 있다는 사실이 모든 마지막 표본의 flush나 완전한 결과 ZIP을 보장하지 않는다.
   중단 자료와 최종 연구 자료를 구분해야 한다.

### 다음 native 충돌에서 필요한 자료

사용자가 재현 주행을 하기로 결정하면, 실행 중인 **해당 P8 Python의 정확한 PID**와 command line,
run ID, stdout/stderr, 서버 로그를 함께 보존한다. ProcDump 설치/디버거 연결/시스템 설정은 이번에
수행하지 않았다. 다음은 사용자 실행 계획이며 자동 실행 명령이 아니다.

- [Microsoft ProcDump](https://learn.microsoft.com/en-us/sysinternals/downloads/procdump)의
  `-ma -e <해당 PID> <새 dump 폴더>`로 unhandled exception full dump를 수집하는 방식을 검토한다.
  이름만 `python`으로 지정하면 다른 프로세스를 잡을 수 있으므로 PID를 확인한다.
- WinDbg에서 exception record/context, 실패 thread와 전체 thread stack, loaded modules,
  libcarla/SDL/ucrtbase 버전과 matching symbols를 확인한다. 종료 DLL 이름만으로 원인을 지정하지 않는다.
- full dump·프로파일러의 실행 영향이 있으므로 진단 run은 성능 비교의 기준 run과 구분한다.
- 관리자 권한이 필요한 WER/LocalDumps 설정·권한 변경은 별도 승인 후 진행한다.
  현재 접근 불가를 덤프 부재로 간주하지 않는다.

## F. Recommended next actions — 다음 개발 순서

1. **일반 주행 조기 종료:** 기존 실행기로 사용자 직접 재현. 충돌 frame/type/tag/impulse/위치와
   화면을 대조하고 road 45의 출구 경로·차로 경계를 확인한다. 충돌 검사를 제거하지 않는다.
2. **MANUAL 프레임 저하:** 동일 spawn/route/교통/속도/카메라/창 상태에서 MANUAL/NoA를 비교한다.
   새 `camera.completed_loop_distribution` 및 M2 hook 시간, 기존 동일-loop worst 기록,
   server/callback/flip cadence를 비교한다. CPU/GPU/메모리·native RPC trace를 별도 진단 run에서 수집한다.
   동기 조회 비용을 줄이려면 반환 값의 시점·오류·복구 계약 동등성을 먼저 입증한다.
3. **실제 위험 노출:** 요청 → 후속 frame 배치 확인 → lead 명령 → 실제 이동 → geometry 후보 →
   선택 카메라 frame/flip 후보 순서와 시각을 검증한다. 실패·미발생 이유를 별도로 집계한다.
4. **반응/TTC:** 연구자가 유효 제동/조향·구조 경계를 결정한 뒤 raw 입력/노출/clearance/closing speed로
   재계산하고 실제 화면과 동기화 오차를 검증한다. proxy를 최종 연구 지표로 승격하지 않는다.
5. **전체 흐름·저장:** v5와 개발 예외를 결정한 뒤 600초 훈련, 600초 M1, 휴식, 780초 M2와
   합의된 사건 절차를 사용자 직접 수행한다. 정상·충돌·ESC·장치 오류 종료의 cleanup과 ZIP을 확인한다.
   고정 실행기는 `C:\dev\carla\run_pilot.cmd` 그대로다.
