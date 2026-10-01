

# CARLA Mobility UX Experiment

CARLA 기반 SAE Level 2 / NoA-like 주행 실험 환경 개발 프로젝트 - Junho Kim

본 프로젝트는 자율주행 및 지능형 모빌리티 환경에서
운전자의 자동화 사용 행동, 주의 분배, 위험 상황 대응 등을 연구하기 위한
CARLA 기반 driving simulator를 구축하는 것을 목적으로 한다.

---

# 1. Project Overview

실험은 동일한 참가자가 순서대로 두 개의 Module을 수행하는 구조이다.

```text
Participant
    │
    ├── Training / Calibration
    │
    ├── Module 1
    │     └── Voluntary NoA Use + SuRT condition
    │
    ├── Rest
    │
    ├── Module 2
    │     └── Manual vs NoA + Passive Video + Eye Tracking
    │
    ├── Final Hazard Event
    │
    └── Post Survey
```

주행 환경은 CARLA `Town04`를 기반으로 하며,
고속도로 환경에서 약 80–100 km/h 수준의 주행을 기본으로 한다.

---

# 2. Experiment Scenario

## Module 1 — Voluntary Automation Use

모든 참가자는 연구용 **NoA-like Level 2 automation을 사용할 수 있는 상태**에서 시작한다.

단, NoA는 처음부터 활성화되어 있지 않으며:

```text
Initial state

Automation available = YES
Driving mode         = MANUAL
```

참가자가 필요하다고 판단할 때 NoA를 활성화하거나 비활성화할 수 있다.

Module 1에서는 참가자를 두 조건으로 나눈다.

```text
Module 1

├── NO_SURT
│
└── SURT
    └── External tablet secondary task
```

주요 측정 항목:

- NoA activation 여부
- 최초 activation까지 걸린 시간
- automation 사용 비율
- activation / disengagement 횟수
- 재활성화 행동
- SuRT 수행 성공 횟수

SuRT는 외부 태블릿에서 수행하며,
현재 설계에서는 CARLA timestamp와 stimulus 단위로 동기화하지 않고
session 단위의 수행 결과를 기록하는 방향이다.

---

## Module 2 — Attention Allocation

Module 2에서는 참가자를 다음 조건으로 배정한다.

```text
Module 2

├── MANUAL
│
└── NOA_L2
```

두 조건 모두 외부 태블릿에서 동일한 비반응형 영상을 제공한다.

Tobii eye tracker를 이용하여 다음 영역에 대한 gaze behavior를 기록한다.

- Forward road
- Rear-view mirror
- Left side mirror
- Right side mirror
- Tablet
- Other

주요 측정 항목:

- Forward-road gaze ratio
- Time to re-look
- Continuous off-road gaze
- Total off-road gaze
- Mirror checking
- Tablet dwell

Tobii Python SDK를 이용하여 calibration 후 gaze/fixation 좌표 및 timestamp를 기록할 예정이다.

---

## Final Hazard Event

Module 2 마지막에는 참가자당 1회의 hazard event를 발생시킨다.

Hazard는 gaze 상태에 의해 trigger하지 않으며,
사전에 정의된 차량 및 상대 차량 조건에서 발생한다.

주요 측정 항목:

- Hazard response latency
- Brake latency
- Steering latency
- Minimum TTC
- Speed
- Lane keeping
- Lane boundary violation
- Vehicle control response

---

# 3. Research NoA vs CARLA Built-in Autopilot

본 프로젝트에는 서로 다른 두 automation 경로가 존재한다.

## Existing CARLA Autopilot

기존 개발/테스트용 기능.

```text
P key

MANUAL
  ↕
CARLA built-in AUTOPILOT
```

CARLA의 기존 autopilot을 이용한다.

이 기능은 현재도 유지되고 있으며
연구용 NoA와는 별개의 기능이다.

## Research NoA-like L2

본 실험에서 사용할 연구용 automation.

```text
Research NoA

├── Automation state management
├── Longitudinal speed control
├── Lateral lane keeping
├── Lane geometry observation
├── Lane-change recommendation
└── Research event logging
```

CARLA built-in autopilot이나 TrafficManager를
연구용 NoA 구현으로 사용하지 않는다.

---

# 4. Current Development Status

현재 개발 상태:

```text
Phase 0   Existing CARLA / Driver View             
Phase 1A  Basic Runtime Infrastructure             
Phase 1B  Research Data & Timing Infrastructure    
Phase 1C  NoA-like L2 Automation
    1C-1 Automation State Model                        
    1C-2 Automation Runtime Controller                 
    1C-3 Longitudinal / Speed Control                  
    1C-4 Lateral / Lane Keeping Control                
    1C-5 CARLA Lane Geometry Adapter                   
    1C-6 Combined NoA Vehicle Control Backend          
    1C-7 Activation / Deactivation + Event Logging
    1C-8 Lane-change Recommendation
    1C-9 Full NoA Integration / Live Validation
Phase 1D Experiment Flow / Training
Phase 2  Module 1 Integration
Phase 3  Module 2 + Tobii Integration
Phase 4  Hazard Event
Phase 5  Full Integration / Pilot / Data Validation
```

---

# 5. Development Environment

현재 개발 환경:

```text
OS        Windows 11
GPU       NVIDIA RTX 3060 Ti 8GB
Python    3.12.x
CARLA     Client API 0.9.16
pygame    2.6.1
Map       Town04
```

Python project:

```text
C:\dev\carla
```

> CARLA Unreal source/project 위치는 로컬 설치 위치에 따라 다를 수 있다.

---

# 6. Starting the CARLA Environment

## 6.1 Start Unreal Editor

CARLA source root에서 PowerShell을 실행한다.

```powershell
make launch
```

정상적으로 실행되면 Unreal Editor에서:

```text
CarlaUE4.uproject
```

가 열린다.

처음 실행하거나 새로운 map을 여는 경우
shader compilation이 발생할 수 있다.

완료될 때까지 기다린다.

---

## 6.2 Open Town04

Unreal Editor의 Content Browser 또는 map 목록에서:

```text
Town04
```

를 연다.

본 연구의 기본 driving environment는 Town04이다.

Town04가 정상적으로 로드되었는지 확인한 후
CARLA simulator가 client connection을 받을 수 있는 상태로 실행한다.

기본 CARLA RPC port:

```text
127.0.0.1:2000
```

---

# 7. Python Environment

새 PowerShell을 실행한다.

```powershell
cd C:\dev\carla
```

PowerShell execution policy가 venv activation을 막는 경우:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
```

virtual environment 활성화:

```powershell
.\.venv\Scripts\Activate.ps1
```

정상 활성화되면:

```text
(.venv) PS C:\dev\carla>
```

형태로 표시된다.

---

# 8. Run the Driving Scenario

기본 실행:

```powershell
python -m src.scenario.highway --duration 120 --driver-view
```

예를 들어 짧게 테스트하려면:

```powershell
python -m src.scenario.highway --duration 20 --driver-view
```

`--driver-view`를 사용하면 1인칭 driving view가 실행된다.

---

# 9. Driving Controls

현재 기본 조작:

| Key | Function |
|---|---|
| `W` | Accelerate |
| `S` | Brake / Reverse control |
| `A` | Steer left |
| `D` | Steer right |
| `Space` | Hand brake |
| `P` | Existing CARLA Autopilot ↔ Manual |
| `V` | DRIVER ↔ COCKPIT view |
| `Z` | Left turn signal |
| `X` | Right turn signal |

주의:

```text
P key automation
≠
Research NoA
```

`P` 키는 기존 CARLA built-in autopilot 테스트 기능이다.

향후 실험에서는 별도로 구현한 Research NoA runtime을 사용한다.

---

# 10. Driver View

현재 front-camera view는 두 가지 모드를 지원한다.

```text
DRIVER
COCKPIT
```

`V` 키를 눌러 전환한다.

현재 주요 설정:

```text
DRIVER FOV  = 100
COCKPIT FOV = 105
```

현재 COCKPIT camera transform:

```text
x     = 0.20
y     = -0.40
z     = 1.30
pitch = -1.5
```

---

# 11. Turn Signals

MANUAL 모드에서:

```text
Z = LEFT indicator ON / OFF
X = RIGHT indicator ON / OFF
```

반대쪽 indicator가 켜진 상태에서 다른 방향을 선택하면
기존 indicator를 끄고 새 방향을 활성화한다.

실제 CARLA `VehicleLightState`를 사용하며,
turn-signal click sound도 실제 light state를 기반으로 재생한다.

기존 LowBeam 등의 다른 light flag는 유지한다.

---

# 12. Research Data Infrastructure

Phase 1B에서는 실험 데이터 기록을 위한 공통 infrastructure를 구현했다.

주요 구성:

```text
StudyRunContext
SegmentContext

HostClockTimestamp
CarlaSnapshotTimestamp
ExternalClockTimestamp

ResearchCsvLogger

ResearchTelemetry
ResearchEventRecorder
```

Telemetry와 Event는 별도의 CSV로 기록되며
공통 timestamp/context를 이용하여 이후 동일 timeline으로 재구성할 수 있다.

예:

```text
telemetry
telemetry
automation_event
telemetry
hazard_event
telemetry
```

---

# 13. Research NoA Architecture

현재 연구용 NoA 구조:

```text
                 Automation State
                        │
                        ▼
            AutomationRuntimeController
                        │
                        ▼
                Research NoA Backend
                        │
          ┌─────────────┴─────────────┐
          │                           │
          ▼                           ▼
 Longitudinal Control          Lane Geometry
          │                           │
          │                           ▼
          │                    Lateral Control
          │                           │
          ▼                           ▼
 throttle / brake                steering
          │                           │
          └─────────────┬─────────────┘
                        ▼
                carla.VehicleControl
                        │
                        ▼
                vehicle.apply_control()
```

---

# 14. Longitudinal Control

Implemented in:

```text
src/experiment/longitudinal_control.py
```

입력:

```text
current_speed_kmh
target_speed_kmh
```

출력:

```text
throttle
brake
```

현재 방식은 deterministic proportional controller이다.

```text
현재 속도 < 목표 속도
→ throttle

현재 속도 > 목표 속도
→ brake

deadband 내부
→ throttle = 0
→ brake = 0
```

실험용 최종 gain과 deadband는 아직 확정하지 않았다.

Live CARLA pilot을 통해 tuning할 예정이다.

---

# 15. Lateral Control

Implemented in:

```text
src/experiment/lateral_control.py
```

입력:

```text
lateral_error_m
heading_error_rad
```

출력:

```text
steering [-1, 1]
```

sign convention:

```text
positive lateral error
= vehicle is RIGHT of lane center

positive heading error
= vehicle heading is RIGHT of lane direction

positive steering
= RIGHT steering
```

따라서 controller는 반대 방향으로 correction을 생성한다.

예:

```text
vehicle right of lane center
        ↓
lateral_error > 0
        ↓
steering < 0
        ↓
left correction
```

---

# 16. CARLA Lane Geometry

Implemented in:

```text
src/experiment/lane_geometry.py
src/vehicle/carla_lane_geometry.py
```

현재 ego vehicle의:

```text
vehicle.get_transform()
```

과 현재 Driving lane의:

```text
map.get_waypoint(...)
```

을 사용하여:

```text
LaneGeometryObservation

├── lateral_error_m
└── heading_error_rad
```

을 계산한다.

현재 lane의 center waypoint만 사용하며:

- future waypoint
- adjacent lane
- route planning
- lane-change target

은 사용하지 않는다.

## Live Validation

Town04에서 실제 validation 완료.

확인 결과:

```text
Lane center         → lateral ≈ 0
Move right          → lateral > 0
Move left           → lateral < 0

Heading right       → heading > 0
Heading left        → heading < 0
```

따라서 현재 Lane Geometry Adapter와
Phase 1C-4 lateral controller의 sign convention은 일치한다.

---

# 17. Read-only Lane Geometry Smoke Test

CARLA가 실행 중일 때 ego vehicle 확인:

```powershell
@'
import carla

client = carla.Client("127.0.0.1", 2000)
client.set_timeout(3.0)

world = client.get_world()

vehicles = world.get_actors().filter("vehicle.*")

print("Vehicles:")

for vehicle in vehicles:
    print(
        "id=", vehicle.id,
        "type=", vehicle.type_id,
        "role_name=", vehicle.attributes.get("role_name", "")
    )
'@ | python -
```

정상 예:

```text
id=124
type=vehicle.mercedes.coupe_2020
role_name=hero
```

Lane geometry monitor:

```powershell
@'
import time
import carla

from src.vehicle.carla_lane_geometry import CarlaLaneGeometryAdapter

client = carla.Client("127.0.0.1", 2000)
client.set_timeout(3.0)

world = client.get_world()
carla_map = world.get_map()

vehicles = list(world.get_actors().filter("vehicle.*"))

hero = next(
    (
        vehicle
        for vehicle in vehicles
        if vehicle.attributes.get("role_name", "") == "hero"
    ),
    None,
)

if hero is None:
    raise RuntimeError("Hero vehicle not found")

adapter = CarlaLaneGeometryAdapter(carla_map)

try:
    while True:
        observation = adapter.observe(hero)

        print(
            f"lateral={observation.lateral_error_m:+.3f} m   "
            f"heading={observation.heading_error_rad:+.4f} rad"
        )

        time.sleep(0.2)

except KeyboardInterrupt:
    print("Stopped.")
'@ | python -
```

이 script는 차량을 제어하지 않는다.

```text
vehicle transform read
        ↓
waypoint lookup
        ↓
geometry calculation
        ↓
console output
```

만 수행한다.

---

# 18. Testing

전체 test suite:

```powershell
python -m pytest -q
```

또는 virtual environment Python을 명시하려면:

```powershell
& ".venv\Scripts\python.exe" -m pytest -q
```

Lint:

```powershell
python -m ruff check .
```

Format check:

```powershell
python -m ruff format --check .
```

현재 Phase 1C-5 완료 시점 기준:

```text
292 tests passed
```

---

# 19. Git Workflow

현재 연구 개발 branch:

```text
research/phase-1c-automation
```

현재 branch 확인:

```powershell
git branch --show-current
```

현재 commit 확인:

```powershell
git rev-parse HEAD
```

변경 상태:

```powershell
git status --short
```

중요:

```text
.omo/run-continuation/
```

파일들은 OpenCode session state이므로
연구 source commit에 포함하지 않는다.

따라서 가능하면:

```powershell
git add <specific files>
```

방식으로 필요한 파일만 명시적으로 stage한다.

`git add .` 사용은 피한다.

---

# 20. Known Environment Warning

현재 CARLA 연결 시 다음 warning이 나타날 수 있다.

```text
WARNING: Version mismatch detected

Client API version     = 0.9.16
Simulator API version  = 294096e
```

현재까지:

- CARLA connection
- vehicle spawn/access
- telemetry
- waypoint access
- lane geometry

는 정상 동작했다.

따라서 현재 development smoke test의 blocker는 아니지만,
실제 participant experiment 전에
Client / Simulator compatibility를 별도로 확인해야 한다.

---

# 21. Current Next Step

현재 다음 개발 단계:

```text
Phase 1C-6
Combined NoA Vehicle Control Backend
```

목표:

```text
Current vehicle speed
        ↓
Longitudinal Controller
        ↓
throttle / brake


Current lane geometry
        ↓
Lateral Controller
        ↓
steering


throttle + brake + steering
        ↓
carla.VehicleControl
        ↓
vehicle.apply_control()
```

이 단계부터 연구용 NoA가 실제 CARLA vehicle에 control command를 적용하기 시작한다.

단:

- CARLA built-in autopilot은 사용하지 않음
- TrafficManager를 연구용 NoA로 사용하지 않음
- P-key 동작은 기존대로 유지
- 최종 controller tuning은 아직 수행하지 않음

---

# 22. Important Development Principle

본 프로젝트에서는 실제 연구 실험에 사용될 simulator를 개발하므로
단순히 차량이 움직이는 것보다 다음을 우선한다.

```text
Reproducibility
Traceability
Explicit state
Accurate timestamps
Controlled experiment conditions
Minimal hidden behavior
```

불확실한 CARLA behavior나 API semantics는 추측해서 구현하지 않고
현재 source code, tests, live CARLA validation을 통해 확인한 뒤 반영한다.

