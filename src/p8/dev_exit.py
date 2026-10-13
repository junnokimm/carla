from __future__ import annotations

import json

from src.experiment.automation import AutomationState
from src.experiment.automation_interaction_types import DriverInput
from src.experiment.context import ExperimentCondition, ExperimentModule
from src.experiment.exit_assistance import (
    ExitAssistanceConfig,
    ExitAssistanceCoordinator,
    ExitAssistanceEvent,
    ExitAssistanceStatus,
    ExitContext,
    ExitObservation,
    ExitTerminationReason,
)
from src.experiment.exit_route import ExitRoute, RoutePoint
from src.p8.model import P8Error
from src.p8.services import SessionServices


class StubReference:
    def __init__(self) -> None:
        self.armed = False

    def arm(self, route: ExitRoute, point_index: int) -> None:
        self.armed = True

    def cancel(self) -> None:
        self.armed = False


def development_route() -> ExitRoute:
    return ExitRoute(
        "DEV_STUB_P8_EXIT", "DEV_STUB_V1", "NO_CARLA_MAP", -3, -4, 150,
        (RoutePoint(0, 0, 0, 0, 1, 0, -3, 3.5),
         RoutePoint(100, 0, 0, 100, 1, 0, -3, 3.5),
         RoutePoint(150, 3.5, 0, 150, 1, 0, -4, 3.5),
         RoutePoint(200, 3.5, 0, 200, 2, 0, 2, 3.5)),
        through_segments=((3, 0, -3),), exit_segments=((2, 0, 2),), initiation_end_m=100,
    )


class DevExits:
    def __init__(self, services: SessionServices) -> None:
        self.services = services
        self.slots: dict[str, int] = {}
        self.coordinators: dict[str, ExitAssistanceCoordinator] = {}
        self.references: list[StubReference] = []

    def exercise(self, key: str, module: ExperimentModule, condition: ExperimentCondition,
                 state: AutomationState) -> None:
        if key in self.slots:
            raise P8Error("EXIT_SLOT_ALREADY_USED:" + key)
        self.slots[key] = 1
        reference = StubReference()
        self.references.append(reference)
        subject = ExitAssistanceCoordinator(
            ExitAssistanceConfig(module, condition, ExitContext.TRAINING if key == "TRAINING" else ExitContext.MODULE,
                                 f"{self.services.context.study_run_id}:{key}:exit", development_route(), 100, 5, 0.15),
            reference, self._event,
        )
        self.coordinators[key] = subject
        subject.observe(ExitObservation(self.services.stamp(), 50, -3.5, 1, 0, -3, None, None,
                                        target_boundary_valid=True))
        subject.mark_presented(subject.prepare_presentation(state), state, self.services.stamp())
        subject.update(DriverInput(exit_confirm_requested=True), state, self.services.stamp())
        subject.observe(ExitObservation(self.services.stamp(), 200, 0, 2, 0, 2, None, None,
                                        target_boundary_valid=True))
        if subject.status is not ExitAssistanceStatus.COMPLETED:
            raise P8Error("DEV_EXIT_FIXTURE_DID_NOT_TERMINATE")

    def finish(self) -> None:
        for subject in self.coordinators.values():
            subject.finish(ExitTerminationReason.ERROR, self.services.stamp())

    def _event(self, event: ExitAssistanceEvent) -> None:
        self.services.emit(event.event_type, {**json.loads(event.payload_json), "observation_source": "DEV_STUB_NOT_RENDERED_OR_DRIVEN"}, timestamp=event.timestamp)
