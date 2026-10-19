import json
from collections import defaultdict
from types import SimpleNamespace

import carla
import pygame
import pytest

from src.experiment.automation import DrivingControlMode
from src.experiment.automation_activation_gate import AutomationActivationGate
from src.experiment.automation_interaction import (
    AutomationInteractionConfig,
    AutomationInteractionController,
    DriverInput,
)
from src.experiment.automation_runtime import AutomationRuntimeController
from src.scenario.research_noa import (
    ResearchNoARunConfig,
    ResearchNoARunMode,
    ResearchNoARunner,
)
from src.scenario.research_noa_diagnostics import ResearchNoADiagnostics
from src.scenario.research_noa_view import (
    ResearchDriverViewConfig,
    ResearchLiveDriverView,
    ResearchManualControlLimits,
)
from src.scenario.research_performance_summary import build_performance_summary
from src.vehicle.carla_lane_geometry import CarlaLaneGeometryUnavailableError
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeResearchVehicle,
    FakeResearchWorld,
    make_live_control_config,
)
from tests.test_automation_interaction import (
    FakeBoundingBox,
    RecordingBackend,
    geometry,
)
from tests.test_automation_interaction_runtime import interaction_config
from tests.test_driver_view import FakeWorld
from tests.test_p5_exit_assistance import ACTIVE, MANUAL


def config():
    return AutomationInteractionConfig(0.2, 0.1, 0.05, True)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Vehicle:
    def __init__(self, clock, fail=False):
        self.clock = clock
        self.fail = fail
        self.reads = 0

    @property
    def bounding_box(self):
        self.reads += 1
        self.clock.advance(0.025)
        if self.fail:
            raise RuntimeError("bbox RPC failed")
        return FakeBoundingBox()


def controller(clock, diagnostics, vehicle, state=MANUAL):
    def observe(vehicle):
        clock.advance(0.002)
        return geometry()

    return AutomationInteractionController(
        runtime=AutomationRuntimeController(state, RecordingBackend()),
        geometry=SimpleNamespace(observe_with_context=observe),
        vehicle=vehicle,
        steering_target=None,
        config=config(),
        event_sink=lambda event: None,
        diagnostics=diagnostics,
    )


@pytest.mark.parametrize("fail", [False, True])
def test_manual_post_control_separates_bbox_and_preserves_failure(fail):
    clock = Clock()
    diagnostics = ResearchNoADiagnostics(clock)
    diagnostics.set_control_mode(DrivingControlMode.MANUAL)
    vehicle = Vehicle(clock, fail)
    subject = controller(clock, diagnostics, vehicle)
    subject.update(DriverInput())
    if fail:
        with pytest.raises(RuntimeError, match="bbox RPC failed"):
            subject.after_control_applied()
    else:
        subject.after_control_applied()
    ops = diagnostics.performance_payload()["operations"]
    assert ops["manual_availability_refresh"]["mean_ms"] == pytest.approx(27)
    assert ops["activation_geometry_query"]["mean_ms"] == pytest.approx(2)
    assert ops["activation_bounding_box_getter"]["mean_ms"] == pytest.approx(25)
    assert vehicle.reads == 1


def test_noa_post_control_has_no_manual_refresh_or_bbox_query():
    clock = Clock()
    diagnostics = ResearchNoADiagnostics(clock)
    subject = controller(clock, diagnostics, Vehicle(clock), ACTIVE)
    subject.update(DriverInput())
    subject.after_control_applied()
    ops = diagnostics.performance_payload()["operations"]
    assert ops["manual_availability_refresh"]["count"] == 0
    assert ops["activation_bounding_box_getter"]["mean_ms"] is None


def test_bbox_is_fresh_each_evaluation_and_each_controller():
    clock = Clock()
    diagnostics = ResearchNoADiagnostics(clock)
    first = Vehicle(clock)
    subject = controller(clock, diagnostics, first)
    for _ in range(2):
        subject.update(DriverInput())
        subject.after_control_applied()
    first.fail = True
    subject.update(DriverInput())
    with pytest.raises(RuntimeError, match="bbox RPC failed"):
        subject.after_control_applied()
    second = Vehicle(clock)
    replacement = controller(clock, diagnostics, second)
    replacement.update(DriverInput())
    replacement.after_control_applied()
    assert (first.reads, second.reads) == (3, 1)


def test_brake_gate_skips_geometry_and_bbox_without_inventing_measurements():
    clock = Clock()
    diagnostics = ResearchNoADiagnostics(clock)
    vehicle = Vehicle(clock)
    gate = AutomationActivationGate(None, vehicle, config(), diagnostics)
    assert gate.failure(DriverInput(brake=1.0)) is not None
    assert vehicle.reads == 0
    assert (
        diagnostics.performance_payload()["operations"]["activation_geometry_query"][
            "count"
        ]
        == 0
    )


@pytest.mark.parametrize("fail", [False, True])
def test_manual_delivery_timing_keeps_every_send_and_propagates_error(fail):
    clock = Clock()
    diagnostics = ResearchNoADiagnostics(clock)
    sent = []

    def send(control):
        sent.append(control)
        clock.advance(0.020)
        if fail:
            raise RuntimeError("checked send failed")

    view = ResearchLiveDriverView(
        FakeWorld(),
        FakeResearchVehicle(),
        ResearchDriverViewConfig(
            control_sender=send,
            automation_state_source=lambda: MANUAL,
            manual_control_limits=ResearchManualControlLimits(1.0, 1.0, 1.0),
        ),
    )
    view._diagnostics = diagnostics
    for _ in range(2):
        view._pending_driver_input = DriverInput(throttle=0.4)
        if fail:
            with pytest.raises(RuntimeError, match="checked send failed"):
                view._apply_manual_control()
        else:
            view._apply_manual_control()
    assert len(sent) == 2
    assert all(c.throttle == pytest.approx(0.4) for c in sent)
    ops = diagnostics.performance_payload()["operations_by_mode"]["MANUAL"]
    assert ops["manual_control_delivery"]["count"] == 2
    assert ops["manual_control_delivery"]["mean_ms"] == pytest.approx(20)
    assert ops["manual_control_preparation"]["count"] == 2


def test_summary_accumulation_respects_window_mode_and_freeze():
    diagnostics = ResearchNoADiagnostics()
    diagnostics.set_control_mode(DrivingControlMode.MANUAL)
    diagnostics.record_operation_duration("activation_bounding_box_getter", 9)
    diagnostics.begin_operation_measurement_window()
    diagnostics.record_operation_duration("activation_bounding_box_getter", 0.025)
    diagnostics.record_operation_duration("activation_bounding_box_getter", 0.125)
    diagnostics.set_control_mode(DrivingControlMode.NOA_ACTIVE)
    diagnostics.record_operation_duration("activation_bounding_box_getter", 0.050)
    diagnostics.freeze_measurement_window()
    diagnostics.record_operation_duration("activation_bounding_box_getter", 8)
    payload = json.loads(
        build_performance_summary(
            diagnostics=diagnostics,
            world=None,
            wall_elapsed_seconds=1,
            scheduler_updates=0,
            camera_diagnostics_json=None,
            error=None,
        )
    )
    total = payload["operation_accumulation"]["activation_bounding_box_getter"]
    assert total["total_ms"] == pytest.approx(200)
    assert total["at_least_100_ms_count"] == 1
    manual = payload["operation_accumulation_by_mode"]["MANUAL"]
    assert manual["activation_bounding_box_getter"]["total_ms"] == pytest.approx(150)
    assert payload["operations"]["activation_bounding_box_getter"]["count"] == 3


def test_audio_reads_current_off_state_and_records_failed_rpc(monkeypatch):
    view = ResearchLiveDriverView(
        FakeWorld(),
        FakeResearchVehicle(),
        ResearchDriverViewConfig(
            camera_diagnostics=True, automation_state_source=lambda: ACTIVE
        ),
    )
    audio = []
    view._turn_signal_audio = SimpleNamespace(
        update=lambda **values: audio.append(values["active"])
    )
    states = iter([carla.VehicleLightState.RightBlinker, carla.VehicleLightState.NONE])
    monkeypatch.setattr(view._hero, "get_light_state", lambda: next(states))
    view._update_turn_signal_audio()
    view._update_turn_signal_audio()
    assert audio == [True, False]

    def failed():
        raise RuntimeError("audio light RPC failed")

    monkeypatch.setattr(view._hero, "get_light_state", failed)
    with pytest.raises(RuntimeError, match="audio light RPC failed"):
        view._update_turn_signal_audio()
    payload = json.loads(view._camera_diagnostics.to_json([]))
    assert (
        payload["timing_by_mode"]["NOA_ACTIVE"]["turn_signal_audio_light_rpc"]["count"]
        == 3
    )
    assert audio == [True, False]


def test_geometry_failure_is_timed_without_bbox_or_false_success():
    clock = Clock()
    diagnostics = ResearchNoADiagnostics(clock)
    vehicle = Vehicle(clock)

    def unavailable(vehicle):
        clock.advance(0.007)
        raise CarlaLaneGeometryUnavailableError("missing lane")

    gate = AutomationActivationGate(
        SimpleNamespace(observe_with_context=unavailable),
        vehicle,
        config(),
        diagnostics,
    )
    assert gate.failure(DriverInput()).value == "GEOMETRY_UNAVAILABLE"
    ops = diagnostics.performance_payload()["operations"]
    assert ops["activation_geometry_query"]["mean_ms"] == pytest.approx(7)
    assert ops["activation_bounding_box_getter"]["count"] == 0
    assert vehicle.reads == 0


@pytest.mark.parametrize("fail", [False, True])
def test_indicator_timing_is_event_driven_and_includes_failed_reads(monkeypatch, fail):
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: defaultdict(bool))
    clock = Clock()
    diagnostics = ResearchNoADiagnostics(clock)
    hero = FakeResearchVehicle()
    view = ResearchLiveDriverView(
        FakeWorld(),
        hero,
        ResearchDriverViewConfig(automation_state_source=lambda: MANUAL),
    )
    view._diagnostics = diagnostics

    def query():
        clock.advance(0.021)
        if fail:
            raise RuntimeError("indicator read failed")
        return carla.VehicleLightState.NONE

    monkeypatch.setattr(hero, "get_light_state", query)
    view._collect_driver_input([])
    assert (
        diagnostics.performance_payload()["operations"]["indicator_input_light_rpc"][
            "count"
        ]
        == 0
    )
    events = [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_x)]
    if fail:
        with pytest.raises(RuntimeError, match="indicator read failed"):
            view._collect_driver_input(events)
    else:
        view._collect_driver_input(events)
    timing = diagnostics.performance_payload()["operations_by_mode"]["MANUAL"][
        "indicator_input_light_rpc"
    ]
    assert timing["count"] == 1
    assert timing["mean_ms"] == pytest.approx(21)


def test_override_relabels_post_control_queries_as_manual():
    clock = Clock()
    diagnostics = ResearchNoADiagnostics(clock)
    diagnostics.set_control_mode(DrivingControlMode.NOA_ACTIVE)
    subject = controller(clock, diagnostics, Vehicle(clock), ACTIVE)
    subject.update(DriverInput(deactivation_requested=True))
    assert subject.state.control_mode is DrivingControlMode.MANUAL
    subject.after_control_applied()
    modes = diagnostics.performance_payload()["operations_by_mode"]
    assert modes["MANUAL"]["activation_bounding_box_getter"]["count"] == 1
    assert "activation_bounding_box_getter" not in modes["NOA_ACTIVE"]


def test_runner_wires_gate_timings_into_active_session_diagnostics():
    vehicle = FakeResearchVehicle()
    factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        ResearchNoARunConfig(
            duration=1.0,
            control_config=make_live_control_config(),
            mode=ResearchNoARunMode.LIVE_SMOKE,
            spawn_index=0,
            automation_interaction=interaction_config(),
        ),
        client=FakeClient(FakeResearchWorld(vehicle)),
        viewer_factory=factory,
        driver_input_source=lambda: DriverInput(),
    )
    with runner.session() as session:
        factory.created[0].actions = [lambda: None] * 2
        session.run()
        payload = session.live_scheduler.diagnostics.performance_payload()
        ops = payload["operations_by_mode"]["MANUAL"]
        assert ops["manual_availability_refresh"]["count"] == 2
        assert ops["activation_bounding_box_getter"]["count"] == 2
        assert ops["activation_geometry_query"]["count"] == 2
    assert vehicle.destroy_count == 1
