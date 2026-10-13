from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import carla

from src.experiment.automation import AutomationState
from src.experiment.automation_interaction_types import (
    ActivationFailureReason,
    ActivationVehicleBoundingBox,
    DriverInput,
)
from src.experiment.context import ExperimentCondition, ExperimentModule
from src.experiment.timestamp import TimestampEnvelope
from src.scenario.lane_change_execution import LaneChangeSubmission
from src.scenario.research_noa_persistence import ResearchSnapshotSample
from src.vehicle.carla_adjacent_lanes import CarlaAdjacentLaneTransform


class LaneChangeInteraction(Protocol):
    @property
    def state(self) -> AutomationState: ...

    @property
    def initialization_failure(self) -> ActivationFailureReason | None: ...

    def initialize(
        self,
        module: ExperimentModule,
        condition: ExperimentCondition,
        *,
        lane_change_in_progress: bool = False,
        stage: str | None = None,
    ) -> AutomationState: ...

    def update(self, driver_input: DriverInput) -> AutomationState: ...

    def after_control_applied(self) -> None: ...


class LaneChangeVehicle(Protocol):
    bounding_box: ActivationVehicleBoundingBox

    def get_transform(self) -> CarlaAdjacentLaneTransform: ...

    def get_light_state(self) -> carla.VehicleLightState: ...

    def set_light_state(self, state: carla.VehicleLightState) -> None: ...


class LaneChangeEventSource(Protocol):
    last_snapshot_sample: ResearchSnapshotSample | None

    def get_host_timestamp(self) -> TimestampEnvelope: ...


class LaneChangeEventPersistence(Protocol):
    @property
    def source(self) -> LaneChangeEventSource: ...

    def record_phase2_event(
        self,
        event_type: str,
        payload_json: str,
        timestamp: TimestampEnvelope,
    ) -> None: ...


class TrafficLightRecorder(Protocol):
    def record(
        self,
        sample: ResearchSnapshotSample,
        state: AutomationState,
    ) -> None: ...


class LaneChangeExecutor(Protocol):
    @property
    def lane_change_in_flight(self) -> LaneChangeSubmission | None: ...

    @property
    def activation_generation(self) -> int: ...

    def submit_lane_change(
        self,
        request_id: str,
        activation_generation: int,
        *,
        right_lane_change: bool,
    ) -> LaneChangeSubmission: ...

    def complete_lane_change(self, generation: int) -> LaneChangeSubmission: ...

    def invalidate_lane_change(self) -> LaneChangeSubmission | None: ...

    def interrupt_lane_change(self) -> LaneChangeSubmission | None: ...


@dataclass(frozen=True, slots=True)
class LaneChangeCorrelation:
    context: str
    lc_event_id: str | None
    lateral_onset_threshold_m: float | None
