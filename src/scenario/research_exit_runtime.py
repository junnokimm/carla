from __future__ import annotations

from dataclasses import dataclass, replace
from math import cos, pi
from typing import Protocol, runtime_checkable

from src.experiment.automation import AutomationState, DrivingControlMode
from src.experiment.automation_interaction_types import (
    ActivationFailureReason,
    DriverInput,
)
from src.experiment.context import ExperimentCondition, ExperimentModule
from src.experiment.exit_assistance import (
    ExitAssistanceCoordinator,
    ExitAssistanceStatus,
    ExitObservation,
    ExitPresentation,
)
from src.experiment.exit_route import (
    ExitRoute,
    ExitRouteError,
    RouteProjection,
    project_route,
)
from src.experiment.lane_change import LaneChangeDirection
from src.experiment.lane_geometry import PlanarPose, compute_lane_relative_geometry
from src.experiment.timestamp import TimestampEnvelope
from src.scenario.research_exit_config import ResearchExitAssistanceConfig
from src.scenario.research_lane_change import LaneChangeInteraction
from src.scenario.research_noa_persistence import (
    ResearchNoAObservationSource,
    ResearchNoAPersistence,
)


@runtime_checkable
class LaneChangeObservationFinisher(Protocol):
    def finish_lane_change_observation(
        self,
        reason: str,
        timestamp: TimestampEnvelope,
    ) -> None: ...


@runtime_checkable
class AssistedLaneChangeInteraction(LaneChangeInteraction, Protocol):
    @property
    def lane_change_in_progress(self) -> bool: ...

    def submit_confirmed_p5(self) -> bool: ...


@runtime_checkable
class GeneralSignalInteraction(Protocol):
    @property
    def signal_request_count(self) -> int: ...

    def request_general(self, direction: LaneChangeDirection) -> None: ...

    def take_failed_p5_submission(self) -> TimestampEnvelope | None: ...


@dataclass(frozen=True, slots=True)
class ResearchExitObservationSource:
    route: ExitRoute
    source: ResearchNoAObservationSource
    cursor: RouteProjection | None = None
    signal_guided_exit: bool = False

    def observe(self) -> ExitObservation:
        sample = self.source.last_snapshot_sample
        if sample is None:
            raise ResearchExitObservationError(
                "post-control snapshot sample is missing"
            )
        transform = sample.transform
        pose = PlanarPose(
            float(transform.location.x),
            float(transform.location.y),
            float(transform.rotation.yaw) * 0.017453292519943295,
        )
        try:
            geometric_projection = project_route(
                self.route,
                pose,
                cursor=self.cursor,
            )
        except ExitRouteError:
            geometric_projection = None
            if self.signal_guided_exit and self.cursor is not None:
                # Moving laterally on a curve (or a stationary snapshot) can
                # shift the projected station slightly backwards. Retain the
                # actual projection, with a bounded tolerance and a forward
                # search cursor; never jump to another lap or road segment.
                try:
                    candidate = project_route(
                        self.route, pose, start_index=self.cursor.point_index
                    )
                    if 0.0 <= self.cursor.distance_m - candidate.distance_m <= 1.0:
                        geometric_projection = candidate
                except ExitRouteError:
                    pass
        projection = geometric_projection
        if projection is not None:
            corridor_lanes = 3.5 if self.signal_guided_exit else 1.5
            if projection.distance_from_route_m > max(
                point.lane_width_m * corridor_lanes for point in self.route.points
            ) or (self.signal_guided_exit and cos(projection.heading_error_rad) <= cos(pi / 4)):
                projection = None
        matched = projection is not None
        if projection is not None and (
            self.cursor is None or projection.distance_m >= self.cursor.distance_m
        ):
            object.__setattr__(self, "cursor", projection)
        # A source-lane centre is not a substitute for an unobserved target.
        target_transform = None
        target_width = None
        if sample.lane_id == self.route.target_lane_id:
            target_transform = sample.lane_transform
            target_width = sample.lane_width_m
        if (
            sample.lane_id == self.route.source_lane_id
            and sample.right_lane_id == self.route.target_lane_id
        ):
            target_transform = sample.right_lane_transform
            target_width = sample.right_lane_width_m
        target_lateral = None
        if target_transform is not None:
            target_lateral = compute_lane_relative_geometry(
                pose,
                PlanarPose(
                    float(target_transform.location.x),
                    float(target_transform.location.y),
                    float(target_transform.rotation.yaw) * 0.017453292519943295,
                ),
            ).lateral_error_m
        carla_timestamp = sample.timestamp.carla_snapshot
        route_distance = -1.0 if projection is None else projection.distance_m
        boundary_valid = (
            projection is not None
            and target_transform is not None
            and target_width is not None
            and target_width > 0.0
            and sample.road_id == self.route.points[projection.point_index].road_id
            and sample.section_id
            == self.route.points[projection.point_index].section_id
            and route_distance <= self.route.fork_distance_m
            and sample.lane_id in (self.route.source_lane_id, self.route.target_lane_id)
        )
        return ExitObservation(
            timestamp=sample.timestamp,
            route_distance_m=route_distance,
            lateral_offset_m=target_lateral,
            road_id=sample.road_id,
            section_id=sample.section_id,
            lane_id=sample.lane_id,
            carla_frame=None if carla_timestamp is None else carla_timestamp.frame,
            carla_simulation_seconds=None
            if carla_timestamp is None
            else carla_timestamp.simulation_seconds,
            indicator=sample.indicator,
            lane_width_m=3.5 if target_width is None else target_width,
            route_matched=matched,
            route_point_index=0 if projection is None else projection.point_index,
            target_boundary_valid=boundary_valid,
            route_reference_distance_m=(
                None
                if geometric_projection is None
                else geometric_projection.distance_from_route_m
            ),
            route_reference_half_width_m=(
                None
                if geometric_projection is None
                else self.route.points[geometric_projection.point_index].lane_width_m
                / 2.0
            ),
            right_lane_id=(
                sample.right_lane_id
                if sample.right_road_id == sample.road_id
                and sample.right_section_id == sample.section_id
                else None
            ),
        )

    def reset_cursor(self) -> None:
        object.__setattr__(self, "cursor", None)


class ResearchExitInteractionObserver:
    def __init__(
        self,
        interaction: LaneChangeInteraction,
        coordinator: ExitAssistanceCoordinator,
        observations: ResearchExitObservationSource,
        persistence: ResearchNoAPersistence,
    ) -> None:
        self._interaction = interaction
        self._assisted_interaction = (
            interaction
            if isinstance(interaction, AssistedLaneChangeInteraction)
            else None
        )
        self.coordinator = coordinator
        self._observations = observations
        self._persistence = persistence

    @property
    def state(self) -> AutomationState:
        return self._interaction.state

    @property
    def initialization_failure(self) -> ActivationFailureReason | None:
        return self._interaction.initialization_failure

    @property
    def presentation(self) -> ExitPresentation | None:
        return self.coordinator.presentation

    def initialize(
        self,
        module: ExperimentModule,
        condition: ExperimentCondition,
        *,
        lane_change_in_progress: bool = False,
        stage: str | None = None,
    ) -> AutomationState:
        return self._interaction.initialize(
            module,
            condition,
            lane_change_in_progress=lane_change_in_progress,
            stage=stage,
        )

    def update(self, driver_input: DriverInput) -> AutomationState:
        receipt_timestamp = self._persistence.source.get_host_timestamp()
        if (self.coordinator.config.signal_guided_exit
                and self.coordinator._automatic_route_armed
                and self.coordinator._route_choice == "EXIT"
                and driver_input.lane_change_direction is LaneChangeDirection.LEFT):
            self.coordinator._interrupt(receipt_timestamp, "DRIVER_SIGNAL_PRIORITY")
        assisted = self._assisted_interaction
        general = assisted if isinstance(assisted, GeneralSignalInteraction) else None
        request_count = None if general is None else general.signal_request_count
        p5_candidate = (
            assisted is not None
            and not self.coordinator.config.signal_guided_exit
            and driver_input.exit_confirm_requested
            and driver_input.lane_change_direction is LaneChangeDirection.RIGHT
        )
        effective_input = replace(
            driver_input,
            exit_confirm_requested=(
                False if self.coordinator.config.signal_guided_exit
                else driver_input.exit_confirm_requested
            ),
            exit_reject_requested=(
                False if self.coordinator.config.signal_guided_exit
                else driver_input.exit_reject_requested
            ),
            lane_change_in_progress=(
                driver_input.lane_change_in_progress
                or (
                    assisted.lane_change_in_progress
                    if assisted is not None
                    else self.coordinator.lane_change_in_progress
                )
            ),
            lane_change_direction=(
                None if p5_candidate else driver_input.lane_change_direction
            ),
        )
        state = self._interaction.update(effective_input)
        commit_timestamp = self._persistence.source.get_host_timestamp()
        self.coordinator.update(
            driver_input,
            state,
            receipt_timestamp,
            commit_timestamp,
            automatic_lane_change_busy=(
                assisted is not None and assisted.lane_change_in_progress
            ),
        )
        # An exit recommendation may expire/reject its confirmation on this
        # iteration. That must not swallow an otherwise ordinary NoA signal.
        # An attempted P5 submission (even a blocked one) consumes the edge once.
        if (p5_candidate and general is not None and assisted is not None
                and state.control_mode is DrivingControlMode.NOA_ACTIVE
                and not assisted.lane_change_in_progress
                and not driver_input.activation_requested
                and not driver_input.exit_reject_requested
                and general.signal_request_count == request_count):
            general.request_general(LaneChangeDirection.RIGHT)
        return state

    def after_control_applied(self) -> None:
        self._interaction.after_control_applied()
        assisted = self._assisted_interaction
        if isinstance(assisted, GeneralSignalInteraction):
            failure = assisted.take_failed_p5_submission()
            if failure is not None:
                self.coordinator.fail_assisted_submission(failure)
        observation = self._observations.observe()
        self.coordinator.observe(observation)
        if self.coordinator.status is ExitAssistanceStatus.WAITING:
            self._observations.reset_cursor()

    def finish_lane_change_observation(
        self,
        reason: str,
        timestamp: TimestampEnvelope,
    ) -> None:
        if isinstance(self._interaction, LaneChangeObservationFinisher):
            self._interaction.finish_lane_change_observation(reason, timestamp)


@dataclass(frozen=True, slots=True)
class ResearchExitViewBinding:
    observer: ResearchExitInteractionObserver
    persistence: ResearchNoAPersistence
    config: ResearchExitAssistanceConfig

    def prepare_presentation(self) -> ExitPresentation | None:
        if getattr(self.config, "signal_guided_exit", False):
            from src.experiment.exit_signal import refresh_guidance

            refresh_guidance(self.observer.coordinator, self.persistence.source.get_host_timestamp())
        if self.observer.presentation is None:
            return None
        if (
            self.observer.coordinator.status is ExitAssistanceStatus.DRAFT
            and self.observer.coordinator.config.eligibility_close_s is not None
        ):
            timestamp = self.persistence.source.get_host_timestamp()
            if self.observer.coordinator.is_time_closed(timestamp):
                self.observer.coordinator.mark_not_presented(timestamp)
                return None
        presentation = self.observer.coordinator.prepare_presentation(
            self.observer.state
        )
        recommendation_active = (
            presentation.recommendation
            and (
                self.observer.coordinator.status in (ExitAssistanceStatus.DRAFT, ExitAssistanceStatus.PRESENTED)
                or (getattr(self.config, "signal_guided_exit", False)
                    and self.observer.coordinator.status is ExitAssistanceStatus.CONFIRMED)
            )
            and self.observer.state.control_mode is DrivingControlMode.NOA_ACTIVE
        )
        return (
            presentation
            if recommendation_active == presentation.recommendation
            else replace(presentation, recommendation=recommendation_active)
        )

    def mark_presented(self, rendered: ExitPresentation) -> None:
        self.observer.coordinator.mark_presented(
            rendered,
            self.observer.state,
            self.persistence.source.get_host_timestamp(),
        )

    def mark_hidden(self) -> None:
        subject = self.observer.coordinator
        if not getattr(self.config, "signal_guided_exit", False) or subject._t0 is None or subject._guidance_hidden_timestamp is not None:
            return
        from src.experiment.exit_signal import close_guidance

        timestamp = self.persistence.source.get_host_timestamp()
        close_guidance(subject, timestamp, "HUD_HIDDEN")
        subject._guidance_hidden_timestamp = timestamp
        subject._emit("lc_guidance_hidden", timestamp)


class ResearchExitObservationError(RuntimeError):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)
