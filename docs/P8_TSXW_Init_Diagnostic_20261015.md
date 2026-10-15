# P8 TS-XW 초기화 소실 지점 분리 진단

## 1. 결과와 현재 근거

독립 진단 도구 `tools/diagnose_tsxw_init.py`를 추가했다. 한 프로세스에서 사용자가
선택·확인한 A~F 단계 하나만 실행한다. CARLA 및 기존 P8 모듈을 import하지 않는다.
FFB Effect/rumble/Haptic Open/Auto Center 설정/레지스트리 쓰기 API는 없다.

사용자가 제공한 물리 관찰:

| 상태 | 기존 힘 |
|---|---|
| 모든 프로그램 종료 | 있음 |
| Unreal Editor만 실행 | 있음 |
| P8 관리자 화면 | 없음 |
| Training | 없음 |
| P8 완전 종료 | 있음 |

이는 P8/Pygame 수명주기와 관련된 상태 변화의 증거다. Unreal 단독 실행이 충분한
원인이라는 가설은 이 재현에서 약해진다. 그러나 `pygame.init()`과 개별 Joystick Open 중
어느 단계가 원인인지는 아직 확인하지 못했다. 아래 관리 화면 순서 때문에 구분이 필요하다.

### P8의 실제 순서

`src/p8/operator_ui_runtime.py:12-26`:

```text
pygame.init()
display.set_mode() / set_caption()
OperatorApp 생성
app.start()                         ← 동기 실행
while loop:
    handle_events()
    app.render()
    display.flip()
```

`src/p8/operator_ui.py:33-61`의 `start()`:

```text
register_id / advance / 정책 기록 / advance
controller.execute_training(MANUAL_NOA, training_duration_s)
_restore_display()                  ← pygame.init(), 화면 재생성
결과 기록 / advance
```

**Training 호출은 운영 UI의 첫 `app.render()`보다 먼저다.** 최초 OS 창 생성과 실제
관리자 페이지 렌더는 다른 시점이다. 정상적인 `--tsxw` Training 경로라면 TS-XW Open과
Training 종료의 close/pygame.quit이 관리자 페이지 렌더보다 먼저 수행될 수 있다.
따라서 사용자가 관찰한 관리자 화면을 "TS-XW Open 이전 상태"라고 판단할 수 없다.
실패 경로나 설정 차이는 해당 Run의 `tsxw_device_opened/closed`, stage 및 시각으로 확인한다.

`DriverView.run()`은 display.init → `_initialize_input()` → 주행 display.set_mode 순서이며,
종료 시 `_close_input()` 후 pygame.quit을 수행한다. 현재 `pygame.init()`은 제거/교체하지 않았다.

## 2. 단계별 경계

매 명령은 새로운 Python 프로세스다. A부터 F까지 자동 순회하지 않는다.

| 단계 | import 이후 명시적 호출 | 관찰 중 동작 |
|---|---|---|
| A | 없음 | 대기만 |
| B | display.init → 최소480×160 화면 생성/flip | 창 이벤트 처리, 닫기로 조기 종료 가능 |
| C | joystick.init | 명시적 Joystick Open·event pump 없음 |
| D | pygame.init | 모듈 초기화 성공/실패 개수 기록, 명시적 Open·pump 없음 |
| E | display.init(창 생성 없음) → joystick.init → Joystick(index) | name/instance 확인 후 대기, 명시적 축·버튼 polling 없음 |
| F | E와 동일 | event.get으로 pump, 축/버튼/hat 읽기 |

E/F의 display.init은 F의 event 처리를 위한 공통 전제다. **E/F는 이 전제를 동일하게
두고 명시적 polling의 유무를 비교**한다. E는 C에 장치 Open만 더한 순수 단일 변경이
아니므로 display 초기화와의 조합 가능성도 남는다. B는 C/D/E/F의 선행 단계가 아니다.

Pygame의 Joystick 생성자는 장치를 즉시 연다. 별도 `.init()` 재호출이나 장치 순회는 없다.
기본 index는0이며 필요하면 사용자가 `--index`로 명시한다. 이름은 Open 이후에야 검증하므로
잘못된 index도 잠시 열릴 수 있음을 시작 전에 알린다. 이름이 `TS-XW Racer`가 아니면
polling하지 않고 실패·Cleanup한다. F는 기존4축/13버튼/1hat 형태를 확인한다.

**명시적 polling 없음은 SDL 내부 polling 없음이라는 뜻이 아니다.** 앞선 분석의
SDL2.28.4 DirectInput Open 경로에는 exclusive/background 획득, FFB reset,
Auto Center ON 시도와 초기 Poll이 있다. Joystick subsystem 초기화에도 backend 탐색이
포함될 수 있다. 따라서 이 도구는 Python API 경계를 분리하며, 드라이버 내부 호출 결과까지
자동 측정하지 않는다. C/D/E/F 자체가 기존 힘·장치 상태에 영향을 줄 수 있다.

## 3. 실행 명령 — 사용자 수동 실행 전용

다른 P8 세션·입력 진단·실험을 진행 중이지 않은 상태에서 실행한다. 현재 연결을 유지하고
재연결·재보정·드라이버 변경 없이 비교한다. 기존 힘과 핸들 움직임에 대비하고
작은 정상 수동 조작으로 상태를 관찰한다. 이 문서의 명령은 이번 작업에서 실행하지 않았다.

PowerShell, 작업 디렉터리 `C:\dev\carla`:

```powershell
$py = 'C:\dev\carla-native-tm-cancel-20261012\.venv-p8\Scripts\python.exe'
$out = 'C:\Users\nexta\AppData\Local\Temp\opencode'
```

**아래에서 한 번에 하나만 선택한다.** 이전 프로세스가 종료하고 힘이 복원됐는지
확인한 다음 다음 단계를 선택한다. 자동 for-loop로 실행하지 않는다.

```powershell
& $py -B tools/diagnose_tsxw_init.py --stage A --seconds 15 --output "$out\tsxw-A-01.jsonl"
```

```powershell
& $py -B tools/diagnose_tsxw_init.py --stage B --seconds 15 --output "$out\tsxw-B-01.jsonl"
```

```powershell
& $py -B tools/diagnose_tsxw_init.py --stage C --seconds 15 --output "$out\tsxw-C-01.jsonl"
```

```powershell
& $py -B tools/diagnose_tsxw_init.py --stage D --seconds 15 --output "$out\tsxw-D-01.jsonl"
```

```powershell
& $py -B tools/diagnose_tsxw_init.py --stage E --index 0 --seconds 15 --output "$out\tsxw-E-01.jsonl"
```

```powershell
& $py -B tools/diagnose_tsxw_init.py --stage F --index 0 --seconds 15 --output "$out\tsxw-F-01.jsonl"
```

- 시작 경고를 읽고 정확히 `START A`, `START B` 등 해당 문구를 입력해야 import/실행한다.
- 다른 입력·EOF·Ctrl+C는 시작 전 취소한다. 자동 승인 옵션은 없다.
- 관찰 시간은 기본15초, 허용 범위0초 초과~120초다. 이는 독립 진단 시간이며 P8 시간 변경이 아니다.
- A/C/D/E는 별도 관찰 창이 없다. B만 화면을 만들고 F는 화면 없이 입력을 읽는다.
- 종료 후에는 힘 유지/소실 및 Cleanup 후 복원 여부를 콘솔에 적는다. 예:
  `during: lost; after cleanup: restored; resistance lower than baseline`.
- 최종 프로세스 종료 뒤 복원 여부도 별도로 기록한다. 로그 안의 사용자 답변은
  Cleanup 이후이지만 Python 프로세스 완전 종료 이전의 관찰이다.
- 재실행 시 `-02.jsonl`처럼 **새 파일명**을 사용한다. 기존 파일은 덮어쓰지 않는다.
- `--help`는 Pygame을 import/초기화하지 않는다.

## 4. 기록과 Cleanup 판독

JSONL 및 콘솔에 stage/PID/host monotonic·UTC 시각, 각 호출 시작/완료,
Pygame·display·joystick·device 초기화 상태, 실제 Open 시 이름/Instance ID,
실패 operation/예외, 관찰 완료, Cleanup 시도·오류·최종 상태를 기록한다.
F는 polling 횟수와 마지막 raw 입력을 남기며 매 sample 파일 쓰기는 하지 않는다.
이 입력은 힘 센서나 토크 측정이 아니다.

Cleanup은 다음 순서로 한 번씩 시도한다.

```text
열린 device.quit → joystick.quit → pygame.quit → 상태 확인
```

앞 단계 실패에도 뒤 Cleanup을 시도한다. Open/획득 재시도·재연결·장치 Reset 반복은 없다.
FFB 정지/Auto Center 복원 명령도 추가하지 않는다. import만 한 A도 종료 시 quit을
호출하지만 관찰 중 초기화는 하지 않는다.

- `exit_code=0`: 선택한 단계와 소프트웨어 Cleanup 성공.
- `exit_code=1`: 단계·Cleanup·로그 I/O 실패.
- `exit_code=2`: 시작 전 취소/입력 중단.
- `cleanup_completed.success=true`와 초기화 상태false는 **SDL 객체/서브시스템 상태**다.
  Windows exclusive ownership이 해제됐다는 직접 증거는 아니다.
- `os_ownership_released=NOT_VERIFIED`, `force_measurement=NOT_MEASURED`를 명시한다.
- `user_observation`은 `USER_REPORT_NOT_SENSOR`로 저장한다.
- `process_exit_requested`도 실제 OS 프로세스 소멸을 스스로 증명하는 이벤트는 아니다.
  PowerShell prompt 복귀 후 필요하면 로그 PID에 대해
  `Get-Process -Id <PID> -ErrorAction SilentlyContinue`로 남아 있는지 확인한다.
  프로세스 종료와 힘 복원을 함께 기록하되, 다른 프로세스의 점유까지 배제했다고 해석하지 않는다.

강제 프로세스 종료·OS 오류는 finally 실행을 보장하지 않는다. 로그 실패 시 모든 결과가
파일에 남는다는 보장도 없다. 도구는 Cleanup을 시도하고 실패를 성공으로 대체하지 않는다.

## 5. 결과 해석과 FFB-2 권고

| 관찰 | 다음 판단 |
|---|---|
| A부터 소실 | import 또는 다른 실행 조건 조사; 개별 Joystick Open 원인으로 단정 불가 |
| A 유지, B 소실 | display/window/포커스와 드라이버 상호작용 조사 |
| B 유지, C 소실 | joystick subsystem 초기화/backend 탐색 우선 조사 |
| C 유지, D 소실 | pygame.init의 추가 subsystem·조합 조사; D=Joystick Open이라고 해석하지 않음 |
| C/D 유지, E 소실 | 명시적 Joystick Open/acquire/reset 및 display 조합을 우선 조사 |
| E 유지, F 소실 | event pumping/polling/reacquire 경로 조사 |
| 모든 단계 유지, P8만 소실 | 실제 P8 시작·Training·복원 순서, 다중 장치 열기, 실행 환경 차이 조사 |

이 결과가 나오기 전에는 P8의 pygame.init을 제거하거나 display.init으로 교체하지 않는다.
기본 센터링 복구와 동적 FFB 구현도 별개다. FFB-2는 다음 순서를 권고한다.

1. 이 도구와 사용자의 관찰로 소실 경계·복원 경계를 확정한다.
2. 실제 SDL backend 및 드라이버의 reset/autocenter/획득 결과를 확인한다.
3. P8의 조기 Training 호출과 재초기화 수명주기에 맞춰 단일 장치 소유권을 설계한다.
4. 기본 센터링 유지/복구를 먼저 검증한 뒤 별도 승인된 Spring/Damper 출력 검증으로 진행한다.
5. 입력 매핑·NoA Driver Override·600/600/780초·기존 로깅을 보존하고 FPS 영향도 따로 측정한다.

현재 구현에 새 Force Effect나 별도 DirectInput 독점 소유자를 추가하지 않았다.

## 6. 검증·보존

- 신규 테스트: `tests/test_tsxw_init_diagnostic.py`.
- 모든 신규 실행 테스트는 전체 Pygame/장치를 Fake로 교체한다.
  경계별 호출 차이, 1회 Open, 입력·분리 오류, partial pygame.init 실패, Ctrl+C,
  Cleanup 실패, 로그 저장 실패 시 해제, 사용자 관찰 표시, 기존 파일 보존을 검사한다.
- 관련 테스트 실행 시점: 신규23개를 포함한 **86 passed**, 7.37초.
- **검증 범위 주의:** 이 묶음의 기존 P8 UI/lifecycle 테스트는 CARLA가 Fake여도
  실제 Pygame 초기화/화면 생성·종료를 사용한다. 따라서 전체86개를 순수 장치 비접촉
  테스트라고 표현하지 않는다. 이 경로에는 TS-XW 설정이 없고 명시적 TS-XW Open이나
  FFB Effect 호출은 없지만 SDL 초기화의 간접 영향은 이번 테스트로 측정하지 않았다.
  이후 추가 테스트는 전체 Fake 기반 신규 파일로 제한했다.
- 최종 신규 Fake 테스트 재검증: **24 passed**, 0.11초. 시작 확인 중단 기록 테스트 포함.
- 운영 Python에서 실제 실행한 도구 명령은 **`--help`만**이다. A~F 실장치 단계는 실행하지 않았다.
- 전체 Ruff check 및 새 Python2개 format check 통과.
- 최종 `git diff --check` 통과. `git diff --name-only -- src/p8` 출력 없음.
- 기존 `src/p8`, TS-XW 입력·정규화, DriverView, P5/FPS 구현 파일은 이번에 수정하지 않았다.
- 시작 branch는 `research/phase-1c-automation`, HEAD는
  `e1af127bb096beaecec3e12cbd2b5bf18a378785`. 기존 미커밋 작업·실험 로그를 보존했다.
- 이번 추가 파일은 도구, 신규 테스트, 이 보고서의3개다. Commit/Push 및 브랜치 변경 없음.

참고 근거: 기존 FFB 진단 대화, Pygame2.6.1 Joystick 생성자의 즉시 Open 동작,
[SDL2.28.4 DirectInput Joystick 소스](https://github.com/libsdl-org/SDL/blob/release-2.28.4/src/joystick/windows/SDL_dinputjoystick.c),
`docs/CARLA_Experiment_Design_v5_Source_of_Truth.md` 및 현재 P8 소스.
