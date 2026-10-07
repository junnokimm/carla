from dataclasses import replace
from pathlib import Path

import carla
import pygame
import pytest

from src.experiment.automation_interaction_types import DriverInput
from src.experiment.context import Module1Condition
from src.experiment.exit_assistance import ExitPresentation
from src.experiment.exit_route import ExitRouteError
from src.scenario.research_exit_config import (
    ResearchExitAssistanceConfig,
    ResearchExitConfigError,
)
from src.scenario.research_exit_hud import ExitNavigationRenderer
from src.scenario.research_exit_view import ResearchExitViewController
from src.vehicle.carla_exit_route import validate_exit_route_map
from tests.test_p5_boundary_contracts import (
    ACTIVE,
    ReferenceRecorder,
    coordinator,
    event_payloads,
    observation,
    route,
    stamp,
)
from tests.test_p5_route_reference import (
    ValidationMap,
    ValidationVehicle,
    ValidationWaypoint,
)


@pytest.mark.parametrize("reject", [True, False])
def test_missed_exit_preserves_assisted_decision(reject: bool) -> None:
    subject = coordinator(Module1Condition.NO_SURT, ReferenceRecorder())
    subject.observe(observation(5.0, -3.5, 1))
    subject.mark_presented(subject.prepare_presentation(ACTIVE), ACTIVE, stamp(20))
    subject.update(
        DriverInput(exit_reject_requested=reject),
        ACTIVE,
        stamp(30 if reject else 6_000_000_030),
    )
    subject.observe(replace(
        observation(-1.0, -3.5, 3, road=1184, lane=-4, matched=False),
        route_reference_distance_m=4.0,
        route_reference_half_width_m=1.75,
    ))
    assert subject.events[-1].event_type == "lc_completed"
    assert event_payloads(subject)[-1]["decision"] == (
        "REJECT" if reject else "NORESPONSE"
    )


def test_left_only_permission_cannot_validate_right_maneuver() -> None:
    selected = replace(route(), exit_segments=())
    target = ValidationWaypoint(-4)
    source = ValidationWaypoint(-3, right=target)
    source.lane_change = carla.LaneChange.Left
    points = tuple(
        replace(point, road_id=39, section_id=0, x=0.0 if point.lane_id == -3 else 10.0, y=0.0)
        for point in selected.points
    )
    selected = replace(selected, points=points)
    with pytest.raises(ExitRouteError, match="permit a right"):
        validate_exit_route_map(selected, ValidationMap(source, target), ValidationVehicle())


def test_exit_input_ignores_pygame_window_and_mouse_events() -> None:
    class Binding:
        config = ResearchExitAssistanceConfig(Path("unused"), "route", "event")

        def prepare_presentation(self):
            return None

        def mark_presented(self, rendered):
            raise AssertionError("no render in input test")

    controller = ResearchExitViewController((1280, 720))
    controller.bind(Binding())
    assert controller.requests([
        pygame.event.Event(pygame.WINDOWSHOWN),
        pygame.event.Event(pygame.MOUSEMOTION, pos=(10, 20)),
    ]) == (False, False)


def test_manual_navigation_does_not_show_assisted_arrow(monkeypatch) -> None:
    renderer = ExitNavigationRenderer((1280, 720))
    arrows: list[bool] = []
    monkeypatch.setattr(renderer, "_draw_right_arrow", lambda screen, rect: arrows.append(True))
    renderer.draw(
        pygame.Surface((1280, 720)),
        ExitPresentation("e", "1.0 km 앞 출구, 우측 차로로", False, 1000.0, 1, 0),
        "c", "r",
    )
    assert arrows == []


def test_config_rejects_case_insensitive_button_collision() -> None:
    with pytest.raises(ResearchExitConfigError, match="distinct"):
        ResearchExitAssistanceConfig(Path("unused"), "route", "event", confirm_key="C", reject_key="c")
