from __future__ import annotations

from dataclasses import dataclass, replace

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
from src.experiment.lane_geometry import PlanarPose, compute_lane_relative_geometry
from src.scenario.research_exit_config import ResearchExitAssistanceConfig
from src.scenario.research_noa_persistence import (
    PersistedInteractionObserver,
    ResearchNoAObservationSource,
    ResearchNoAPersistence,
)


@dataclass(frozen=True, slots=True)
class ResearchExitObservationSource:
    route: ExitRoute
    source: ResearchNoAObservationSource
    cursor: RouteProjection | None = None

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
        projection = geometric_projection
        if (
            projection is not None
            and projection.distance_from_route_m
            > max(point.lane_width_m * 1.5 for point in self.route.points)
        ):
            projection = None
        matched = projection is not None
        if projection is not None:
            object.__setattr__(self, "cursor", projection)
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
                None if geometric_projection is None
                else geometric_projection.distance_from_route_m
            ),
            route_reference_half_width_m=(
                None if geometric_projection is None
                else self.route.points[geometric_projection.point_index].lane_width_m / 2.0
            ),
        )


class ResearchExitInteractionObserver:
    def __init__(
        self,
        interaction: PersistedInteractionObserver,
        coordinator: ExitAssistanceCoordinator,
        observations: ResearchExitObservationSource,
        persistence: ResearchNoAPersistence,
    ) -> None:
        self._interaction = interaction
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
        effective_input = replace(
            driver_input,
            lane_change_in_progress=(
                driver_input.lane_change_in_progress
                or self.coordinator.lane_change_in_progress
            ),
        )
        state = self._interaction.update(effective_input)
        commit_timestamp = self._persistence.source.get_host_timestamp()
        self.coordinator.update(
            driver_input,
            state,
            receipt_timestamp,
            commit_timestamp,
        )
        return state

    def after_control_applied(self) -> None:
        self._interaction.after_control_applied()
        self.coordinator.observe(self._observations.observe())


@dataclass(frozen=True, slots=True)
class ResearchExitViewBinding:
    observer: ResearchExitInteractionObserver
    persistence: ResearchNoAPersistence
    config: ResearchExitAssistanceConfig

    def prepare_presentation(self) -> ExitPresentation | None:
        if self.observer.presentation is None:
            return None
        presentation = self.observer.coordinator.prepare_presentation(
            self.observer.state
        )
        recommendation_active = (
            presentation.recommendation
            and self.observer.coordinator.status
            in (ExitAssistanceStatus.DRAFT, ExitAssistanceStatus.PRESENTED)
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


class ResearchExitObservationError(RuntimeError):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)
