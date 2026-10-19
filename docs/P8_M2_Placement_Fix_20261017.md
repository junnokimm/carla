# M2 화물 배치 확인·배정 표시 수정본 — 2026-10-17

## 범위와 기준

버전: `P8_M2_V1_PLACEMENT_FIX_20261017`.
기준은 이 대화의 `carla(20261017-072209).zip` 작업 파일에 `carla_pilot_fix_20261017.zip` 및
`carla_m2_hazard_fix_20261017.zip`을 적용한 상태다. 다른 Git 브랜치·과거 구현은 사용하지 않았다.
적용기는 이 기준 파일과 일치하는지 먼저 확인하고, 예상 밖 로컬 수정이 있으면 중단한다.
`config/p8_pilot.json`, `.git`, `.omo`, 가상환경, 실험 데이터는 변경하지 않는다.

이번 범위는 (1) `CARGO_MOVED` 오판을 일으키는 배치 확인 순서, (2) M2 안내 화면의 배정 표시다.
앞선 감사에서 나온 모든 연구 지표·프로토콜 문제를 완성했다는 뜻이 아니다.

## 확인한 오류

대상 실행: `P8-PILOT-9813fc1585164f4494dc4f638e845d3a`.
manifest의 `abort_reason`은 `ACTUAL_MODULE_FAILURE:P7Error:M2_V1_CARGO_MOVED`다.
화물 배치 기록의 z=0과 다음 관측의 z=0.025553364307m 차이는
`extent.z - location.z + 0.02 = 0.025553364755m`라는 코드의 지면 보정량과 사실상 일치했다.
기존 `prepare()`는 `set_transform()` 직후 `get_transform()`을 읽어 기준 자세로 저장했다.
CARLA의 getter는 최신 수신 tick의 상태를 반환하므로 그 호출을 배치 요청의 적용 확인으로 사용할 수 없다.
공식 근거: https://carla.readthedocs.io/en/latest/python_api/#carla.Actor

원본 소스의 실제 `prepare()`/`geometry()`를 지연 setter 모형에서 실행하면,
기준 z=0과 적용 후 z=0.025553364755의 차이로 `cargo_stationary=False`가 재현된다.
다만 실제 Windows 엔진의 내부 처리 순서를 모두 관측한 것은 아니다.

## 변경 내용

### 화물 배치

- 단 한 번의 지면 보정 요청과 그 목표 자세를 분리해서 보존한다.
- 요청 직전의 world frame을 기록한다. 즉시 읽은 actor getter 값을 기준 자세로 채택하지 않는다.
- 이후 주행 관측의 더 새로운 WorldSnapshot에서 화물의 위치·속도·회전이 요청 목표와 맞는지 확인한다.
- 확인 전에는 정지성·노출 후보 판정을 하지 않는다. 주행 루프를 sleep 또는 tick 대기로 막지 않는다.
- 확인된 snapshot의 자세를 별도로 복사해 정지 판정 기준으로 보존한다.
- 과거 카메라 snapshot으로 배치 확인을 수행하거나 기준 자세를 다시 설정하지 않는다.
- 기존 5mm 위치/5mm·s⁻¹ 속도/0.01° yaw 판정은 느슨하게 바꾸지 않는다.
  배치 목표 확인에서는 pitch·roll도 같은 0.01° 오차로 확인한다.
- 확인 뒤 실제 화물 이동이 관측되면 기존 `M2_V1_CARGO_MOVED` 중단을 유지한다.
- 선행차 이동 명령 예정 시각까지 배치가 확인되지 않으면
  `M2_V1_CARGO_PLACEMENT_UNCONFIRMED`로 명확히 중단한다. 확인하지 않은 장면을 억지로 노출하지 않는다.
- 엔진 설정, Ego 조작, 차로/위험사건 시간창, 충돌 처리, 연구 반응 임계값은 바꾸지 않는다.

### 새 로그 의미

`m2_hazard_cargo_placement_requested`: 요청 목표, 요청 직전 frame, 자산 bounds.
`m2_hazard_cargo_placed`: 더 새로운 WorldSnapshot에서 목표 일치를 확인한 뒤에만 생성.
`placement_status=CONFIRMED_WORLD_SNAPSHOT`, 실제 frame/자세와 목표 오차가 함께 기록된다.
`m2_hazard_actors_prepared`의 `cargo_placement_status=AWAITING_WORLD_SNAPSHOT`은
장면 배치 요청을 보냈다는 뜻이지, 배치를 확인했다는 뜻은 아니다.
이벤트/최종 위험사건 요약의 program_version은 새 버전으로 바꿨다.
세션 전체의 기존 `P8_OPERATOR_NOT_PARTICIPANT_READY` 표식은 그대로 유지한다.

### M2 배정 안내

기존 M2 안내는 두 배정에서 같은 제목/공통 본문만 표시했다.
새 화면은 세션의 `module_2_condition`을 읽어서 다음 제목을 표시한다.

- `Module 2 안내 — 배정: 수동 (MANUAL)`
- `Module 2 안내 — 배정: NoA형 (NOA_L2)`

공통 본문과 SPACE 진행은 그대로다. 위험사건 존재·형태·시간·TTC를 추가 안내하지 않는다.
표시만 바꾸고 배정을 다시 뽑거나 바꾸지 않는다. 주행 중 실제 NoA 상태 표시와 배정은 별개다.

현재 `run_pilot.cmd` → `tools.run_pilot`은 `config/p8_pilot.json`의 m1/m2를 읽으며,
`--m1`, `--m2`가 있으면 이번 실행에만 덮어쓴다. 매 실행의 UUID가 달라져도
조건을 새로 무작위 추첨하는 것은 아니다. 본실험 독립 블록 배정표 운영과 구분해야 한다.
직전 9813 실행의 배정은 MANUAL이었다. 운전 중 켰다고 NOA_L2 배정으로 바뀌지는 않는다.
현재 DEV에서는 MANUAL 배정의 NoA 활성화를 차단하지 않고 protocol_deviation으로 남긴다.
이 패치에서 이 정책을 바꾸지 않았다.

## 적용 방법 (Windows)

P8와 파일을 수정 중인 도구를 종료한다. ZIP을 아래에 해제한다.
`C:\dev\carla_m2_placement_fix_20261017`
폴더 바로 아래에 `Apply-M2PlacementFix.py`와 `m2_placement_fix_manifest.json`이 있어야 한다.

```powershell
Set-Location -LiteralPath 'C:\dev\carla_m2_placement_fix_20261017'
& 'C:\dev\carla-native-tm-cancel-20261012\.venv-p8\Scripts\python.exe' .\Apply-M2PlacementFix.py --project-root 'C:\dev\carla' --check
```

`Preflight passed` / `CHECK ONLY`이면 적용한다.

```powershell
& 'C:\dev\carla-native-tm-cancel-20261012\.venv-p8\Scripts\python.exe' .\Apply-M2PlacementFix.py --project-root 'C:\dev\carla' --apply
```

`APPLY_COMPLETE`를 확인한다. 기존 파일은 먼저
`C:\dev\carla-m2-placement-backup-<UTC시각>-<UUID>`에 보존된다.
기존 백업 도구와 같은 파일별 해시 검사, CRLF 허용, 예상 밖 파일 충돌 차단,
반복 적용 시 건너뛰기, 적용 실패 시 이번 변경분 롤백을 사용한다.
Git commit/push/switch는 실행하지 않는다. 현재 체크아웃된 작업 폴더에 적용된다.

## 로컬 회귀 확인

이번 작성 환경에는 CARLA Python API와 pygame이 설치되어 있지 않고 패키지 다운로드도 실패했다.
따라서 아래 네이티브 의존 회귀 파일은 작성했지만 이 환경에서 실행 완료하지 못했다.
설치된 Windows P8 환경에서 먼저 확인한다. 이 테스트는 실제 CARLA 서버에 연결하지 않는다.

```powershell
Set-Location -LiteralPath 'C:\dev\carla'
& 'C:\dev\carla-native-tm-cancel-20261012\.venv-p8\Scripts\python.exe' -B -m pytest -q tests/test_m2_cargo_placement.py tests/test_p8_m2_assignment_display.py tests/test_m2_hazard_v1.py tests/test_m2_hazard_preparation.py
```

실패하면 실제 주행 전에 출력을 확인한다. 테스트를 통과한 뒤 기존 명령으로 실행한다.

```powershell
& 'C:\dev\carla\run_pilot.cmd'
```

NoA 배정의 내부 기능 확인용 일회성 명령(참가자 배정표를 대체하지 않음):

```powershell
& 'C:\dev\carla\run_pilot.cmd' --m2 NOA_L2
```

명령 옵션은 JSON 설정을 덮어 저장하지 않는다. 다음 기본 명령 실행은 다시 JSON의 m2를 사용한다.

## 검증 범위

- 실제 소스 클래스/함수 본문을 AST로 분리해 외부 의존성만 시험용 객체로 바꾼 검사: 18개 통과.
  원본의 지연 readback 재현, 수정 후 확인, 이전 frame 배제, renderer의 기준 변경 방지,
  확인 후 XYZ 이동 중단, 미확인 deadline, 잘못된 위치/회전/actor 소실,
  세션 context → snapshot → 조건 제목 전달을 검사했다.
  이 검사는 CARLA 물리·투영·센서·픽셀·pygame 렌더링 통합 검증이 아니다.
- 설치 도구 회귀: 52개 통과.
- 실제 배포 파일로 검사 모드의 무변경, 원본 바이트 백업, 적용 후 해시,
  반복 적용 무변경, 설정/원자료/목록 밖 파일 보존을 추가 검사한다.
- 변경 Python 파일 문법은 Python 3.12 문법 모드로 확인한다.
- 네이티브 의존 신규 테스트는 carla 미설치로 collection 단계에서 차단.
  전체 프로젝트 suite, Ruff, Windows/CARLA/TS-XW 실주행, 실제 화면 렌더링은 미검증이다.
- 자세한 실행 결과는 `docs/m2_placement_verification/checks.json` 및 ZIP의 verification 폴더에 있다.

## 남아 있는 범위

이 수정본이 모든 위험사건 발생을 보장하는 것은 아니다. 기존 도로·가림·속도 조건과
400–600초 시간창은 유지하며, 조건 불충족으로 `NOT_OCCURRED`가 남을 수 있다.
화물 배치 오류 수정과 별개로 연구용 hazard_visible_t/유효 반응 임계값/최종 결과 산출,
중단 M2 요약, 종료 해제 횟수 분리, 출구 안내, 추가 차량 지표, 시선/외부 태블릿/설문 연결은
이 패치에서 새로 구현하지 않았다. 정의가 미정인 연구 값을 임의로 채우지 않았다.

수정 범위: 소스 5개, 기존 회귀 fixture 2개 조정, 새 회귀 파일 2개, 이 안내서와 검증 기록.
새 실행은 배치 요청 → 배치 확인 → 선행차 이동 명령 → 노출 후보 → 결과 저장 순서를
실제로 확인해야 한다. 소스 수정을 실제 장비 검증 완료로 해석하지 않는다.
