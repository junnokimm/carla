from __future__ import annotations

from dataclasses import dataclass

from src.experiment.automation import AutomationState, DrivingControlMode
from src.experiment.automation_interaction_types import (
    AutomationInteractionConfig,
    DriverInput,
)
from src.experiment.lane_change import LaneChangeDirection, LaneChangeEligibility
from src.scenario.lane_change_contracts import (
    LaneChangeCorrelation,
    LaneChangeEventPersistence,
    LaneChangeVehicle,
)
from src.scenario.lane_change_events import (
    LaneChangeEventSink,
    record_lane_change_request,
    unavailable_lifecycle_payload,
)
from src.scenario.lane_change_lifecycle import LaneChangeTracker
from src.scenario.lane_change_request import build_lane_change_tracker
from src.scenario.research_noa_persistence import ResearchSnapshotSample
from src.vehicle.carla_adjacent_lanes import (
    CarlaAdjacentLaneAdapter,
    CarlaAdjacentLaneTransform,
    CarlaAdjacentLaneUnavailableError,
)


@dataclass(frozen=True, slots=True)
class _SnapshotVehicle:
    transform: CarlaAdjacentLaneTransform

    def get_transform(self) -> CarlaAdjacentLaneTransform:
        return self.transform


class LaneChangeRequestController:
    def __init__(
        self,
        adjacent_lanes: CarlaAdjacentLaneAdapter,
        vehicle: LaneChangeVehicle,
        persistence: LaneChangeEventPersistence,
        events: LaneChangeEventSink,
        correlation: LaneChangeCorrelation,
        config: AutomationInteractionConfig | None,
    ) -> None:
        self._adjacent_lanes = adjacent_lanes
        self._vehicle = vehicle
        self._persistence = persistence
        self._events = events
        self._correlation = correlation
        self._config = config
        self._sequence = 0

    def create(
        self,
        direction: LaneChangeDirection,
        state: AutomationState,
        driver_input: DriverInput,
        *,
        p5: bool,
        active_tracker: LaneChangeTracker | None,
        generation: int | None,
    ) -> LaneChangeTracker | None:
        timestamp = self._persistence.source.get_host_timestamp()
        self._sequence += 1
        request_id = f"lane-request:{timestamp.host.monotonic_ns}:{self._sequence}"
        context = "P5" if p5 else "GENERAL"
        lc_event_id = self._correlation.lc_event_id if p5 else None
        baseline = self._persistence.source.last_snapshot_sample
        if active_tracker is not None:
            record_lane_change_request(
                self._events,
                request_id,
                direction,
                "REJECTED_ONGOING",
                None,
                context,
                lc_event_id,
                timestamp,
                state.control_mode.value,
                baseline,
                active_tracker,
            )
            return None
        eligibility = self._evaluate(direction, baseline)
        topology = None
        if eligibility is not None and eligibility.eligible:
            try:
                vehicle = (
                    self._vehicle
                    if baseline is None
                    else _SnapshotVehicle(baseline.transform)
                )
                topology = self._adjacent_lanes.capture_topology(vehicle, direction)
            except (CarlaAdjacentLaneUnavailableError, RuntimeError):
                topology = None
        if state.control_mode is DrivingControlMode.MANUAL:
            disposition = "REJECTED_MANUAL_PRIORITY"
        elif driver_input.activation_requested:
            disposition = "REJECTED_ACTIVATION_CYCLE"
        elif generation is None:
            disposition = "UNSUPPORTED_NO_EXECUTOR"
        elif eligibility is None:
            disposition = "REJECTED_RESOURCE_UNAVAILABLE"
        elif not eligibility.eligible:
            disposition = "REJECTED_STATIC_INELIGIBLE"
        elif topology is None:
            disposition = "REJECTED_RESOURCE_UNAVAILABLE"
        else:
            disposition = "REQUESTED"
        record_lane_change_request(
            self._events,
            request_id,
            direction,
            disposition,
            eligibility,
            context,
            lc_event_id,
            timestamp,
            state.control_mode.value,
            baseline,
        )
        if disposition != "REQUESTED" or eligibility is None or generation is None:
            return None
        if self._config is None:
            return None
        built = build_lane_change_tracker(
            request_id,
            direction,
            eligibility,
            baseline,
            generation,
            context,
            lc_event_id,
            self._vehicle.bounding_box,
            self._config,
            topology,
        )
        if built.tracker is None:
            self._events.record(
                "lane_change_lifecycle",
                unavailable_lifecycle_payload(
                    request_id,
                    direction,
                    context,
                    lc_event_id,
                    generation,
                    built.reason,
                    timestamp,
                ),
                timestamp,
            )
        return built.tracker

    def _evaluate(
        self,
        direction: LaneChangeDirection,
        baseline: ResearchSnapshotSample | None,
    ) -> LaneChangeEligibility | None:
        try:
            vehicle = self._vehicle if baseline is None else _SnapshotVehicle(baseline.transform)
            return self._adjacent_lanes.evaluate(vehicle, direction)
        except (CarlaAdjacentLaneUnavailableError, RuntimeError):
            return None
