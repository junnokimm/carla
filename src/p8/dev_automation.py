from __future__ import annotations

from dataclasses import asdict, dataclass

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.automation_interaction import AutomationInteractionController
from src.experiment.automation_interaction_types import (
    ActivationVehicle,
    AutomationInteractionConfig,
    AutomationInteractionEvent,
    DriverInput,
)
from src.experiment.automation_runtime import AutomationRuntimeController
from src.experiment.context import ExperimentCondition, ExperimentModule
from src.experiment.lane_geometry import LaneGeometryObservation, PlanarPose
from src.p7.types import Json
from src.p8.model import P8Error
from src.p8.services import SessionServices
from src.vehicle.carla_lane_geometry import CarlaLaneGeometryContext


@dataclass(frozen=True, slots=True)
class Extent:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Rotation:
    yaw: float = 0


@dataclass(frozen=True, slots=True)
class Box:
    extent: Extent = Extent(2, 0.9)
    location: Extent = Extent(0, 0)
    rotation: Rotation = Rotation()


@dataclass(frozen=True, slots=True)
class StubVehicle:
    bounding_box: Box = Box()


class StubGeometry:
    def observe_with_context(self, vehicle: ActivationVehicle) -> CarlaLaneGeometryContext:
        return CarlaLaneGeometryContext(LaneGeometryObservation(0, 0), 1, 0, -3, 3.5,
                                         PlanarPose(0, 0, 0), PlanarPose(0, 0, 0))


class StubControlBackend:
    def __init__(self) -> None:
        self.transitions: list[str] = []
        self.steering: float | None = None

    def enter_manual_control(self) -> None:
        self.transitions.append("MANUAL")

    def enter_noa_control(self) -> None:
        self.transitions.append("NOA_ACTIVE")

    def set_driver_steering(self, steering: float | None) -> None:
        self.steering = steering


class DevAutomation:
    def __init__(self, services: SessionServices) -> None:
        self.services = services
        self.backend = StubControlBackend()
        self.runtime = AutomationRuntimeController(
            AutomationState(AutomationAvailability.AVAILABLE, DrivingControlMode.MANUAL), self.backend)
        self.controller = AutomationInteractionController(
            runtime=self.runtime, geometry=StubGeometry(), vehicle=StubVehicle(),
            steering_target=self.backend, config=AutomationInteractionConfig(0.2, 0.1, 0.05, True),
            event_sink=self._event,
        )
        self.modules: dict[str, dict[str, Json]] = {}
        self._key: str | None = None
        self._last_s = self._started_s = 0.0
        self._last_mode = DrivingControlMode.MANUAL
        self._on_s = 0.0
        self._activations = self._deactivations = 0
        self.last_input = DriverInput()

    def begin(self, module: ExperimentModule, condition: ExperimentCondition, key: str) -> None:
        self.stop()
        self._key = key
        self._started_s = self._last_s = self.services.elapsed
        self._last_mode = DrivingControlMode.MANUAL
        self._on_s = 0
        self._activations = self._deactivations = 0
        state = self.controller.initialize(module, condition, stage=key)
        if self.controller.initialization_failure is not None:
            raise P8Error("P3_INITIALIZATION_FAILED")
        self.modules[key] = {"condition": condition.value, "initial_mode": state.control_mode.value}

    def input(self, driver: DriverInput) -> None:
        self.last_input = driver
        self.controller.update(driver)
        self.controller.after_control_applied()
        self._integrate(self.controller.state.control_mode)

    def finish(self, *, complete: bool) -> None:
        if self._key is None:
            return
        self._integrate(self.controller.state.control_mode)
        duration = self.services.elapsed - self._started_s
        self.modules[self._key].update({
            "observed_seconds": duration, "automation_on_seconds": self._on_s,
            "automation_on_proportion": self._on_s / duration if complete and duration > 0 else None,
            "observed_on_proportion": self._on_s / duration if duration > 0 else None,
            "activation_count": self._activations, "deactivation_count": self._deactivations,
            "window_complete": complete, "provenance": "DEV_STUB_CANONICAL_P3_STATE",
        })
        self.services.emit("p8_automation_window", {"module_key": self._key, **self.modules[self._key]})
        self._key = None

    def stop(self) -> None:
        self.input(DriverInput(deactivation_requested=True))

    def _integrate(self, mode: DrivingControlMode) -> None:
        now = self.services.elapsed
        if self._key is not None:
            if self._last_mode is DrivingControlMode.NOA_ACTIVE:
                self._on_s += now - self._last_s
            if mode is not self._last_mode:
                self._activations += mode is DrivingControlMode.NOA_ACTIVE
                self._deactivations += mode is DrivingControlMode.MANUAL
        self._last_s, self._last_mode = now, mode

    def _event(self, event: AutomationInteractionEvent) -> None:
        self._integrate(event.state.control_mode)
        self.services.emit("p3_" + event.event_type.value.lower(), asdict(event))
