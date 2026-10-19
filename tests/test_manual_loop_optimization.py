"""Regression checks with real view/control/observation code and fake CARLA."""

import json
from types import SimpleNamespace

import carla
import pygame
import pytest

from src.experiment.automation_activation_gate import AutomationActivationGate
from src.experiment.automation_interaction import (
    AutomationInteractionController,
    DriverInput,
)
from src.experiment.automation_runtime import AutomationRuntimeController
from src.scenario import driver_view
from src.scenario.iteration_light_state import IterationLightState
from src.scenario.research_camera_diagnostics import ResearchCameraDiagnostics
from src.scenario.research_noa_control_delivery import make_checked_control_sender
from src.scenario.research_noa_persistence import ResearchNoAObservationSource
from src.scenario.research_noa_view import (
    ResearchDriverViewConfig,
    ResearchLiveDriverView,
    ResearchManualControlLimits,
)
from tests.research_noa_fakes import FakeResearchVehicle, FakeResearchWorld
from tests.test_automation_interaction import RecordingBackend, geometry
from tests.test_p5_exit_assistance import ACTIVE, MANUAL
from tests.test_p8_phase2_performance import Clock, Vehicle, config


def test_static_bounds_reuse_keeps_live_geometry_and_refreshes_activation():
    clock = Clock()
    hero = Vehicle(clock)
    contexts = iter([geometry(), geometry(lateral=0.4), geometry()])
    gate = AutomationActivationGate(
        SimpleNamespace(observe_with_context=lambda _: next(contexts)),
        hero,
        config(),
        cache_static_bounds=True,
    )
    assert gate.failure(DriverInput(), reuse_static_bounds=True) is None
    assert gate.failure(DriverInput(), reuse_static_bounds=True) is not None
    assert hero.reads == 1
    # Activation uses a fresh read even after the manual cache was populated.
    hero.fail = True
    with pytest.raises(RuntimeError, match="bbox RPC failed"):
        gate.failure(DriverInput())
    assert gate._cached_bounds is None
    assert hero.reads == 2


def test_bounds_cache_is_not_shared_with_new_ego_and_failed_reads_retry():
    clock = Clock()
    for _ in range(2):
        hero = Vehicle(clock, fail=True)
        gate = AutomationActivationGate(
            SimpleNamespace(observe_with_context=lambda _: geometry()),
            hero,
            config(),
            cache_static_bounds=True,
        )
        with pytest.raises(RuntimeError):
            gate.failure(DriverInput(), reuse_static_bounds=True)
        hero.fail = False
        assert gate.failure(DriverInput(), reuse_static_bounds=True) is None
        assert gate.failure(DriverInput(), reuse_static_bounds=True) is None
        assert hero.reads == 2


def test_manual_cache_does_not_bypass_failed_noa_activation_and_can_recover():
    clock = Clock()
    hero = Vehicle(clock)
    backend = RecordingBackend()
    controller = AutomationInteractionController(
        runtime=AutomationRuntimeController(MANUAL, backend),
        geometry=SimpleNamespace(observe_with_context=lambda _: geometry()),
        vehicle=hero,
        steering_target=None,
        config=config(),
        event_sink=lambda _: None,
        cache_static_bounds=True,
    )
    for _ in range(3):
        controller.update(DriverInput())
        controller.after_control_applied()
    assert hero.reads == 1
    hero.fail = True
    with pytest.raises(RuntimeError, match="bbox RPC failed"):
        controller.update(DriverInput(activation_requested=True))
    assert controller.state.control_mode == MANUAL.control_mode
    assert "noa" not in backend.calls
    hero.fail = False
    controller.update(DriverInput(activation_requested=True))
    assert controller.state.control_mode == ACTIVE.control_mode
    assert hero.reads == 3


@pytest.mark.parametrize("ending", ["new_loop", "expired", "invalidated", "consumed"])
def test_audio_never_reuses_invalid_or_previous_loop_observation(ending):
    clock = Clock()
    shared = IterationLightState(clock)
    shared.begin()
    shared.publish(carla.VehicleLightState.LeftBlinker)
    if ending == "new_loop":
        shared.begin()
    elif ending == "expired":
        clock.advance(0.051)
    elif ending == "invalidated":
        shared.invalidate()
    else:
        assert shared.consume() == (carla.VehicleLightState.LeftBlinker, 0.0)
    assert shared.consume() is None


def test_shared_audio_uses_actual_telemetry_and_falls_back_to_fresh_rpc():
    hero = FakeResearchVehicle()
    source = ResearchNoAObservationSource(FakeResearchWorld(hero), hero)
    view = ResearchLiveDriverView(
        source._world,
        hero,
        ResearchDriverViewConfig(iteration_light_state=source.iteration_lights),
    )
    audio = []
    view._turn_signal_audio = SimpleNamespace(
        update=lambda **kw: audio.append(kw["active"])
    )
    view._begin_iteration()
    hero.current_light_state = carla.VehicleLightState.LeftBlinker
    observation = source.get_observation()
    assert observation.state.indicator == "left"
    # Sharing is with the observed value, never an invented commanded state.
    hero.current_light_state = carla.VehicleLightState.NONE
    view._update_turn_signal_audio()
    view._begin_iteration()  # No telemetry in this loop: original fresh RPC.
    view._update_turn_signal_audio()
    assert audio == [True, False]


def test_failed_new_observation_invalidates_shared_audio(monkeypatch):
    hero = FakeResearchVehicle()
    source = ResearchNoAObservationSource(FakeResearchWorld(hero), hero)
    source.iteration_lights.begin()
    source.get_observation()

    def fail():
        raise RuntimeError("light read failed")

    monkeypatch.setattr(hero, "get_light_state", fail)
    with pytest.raises(RuntimeError, match="light read failed"):
        source.get_observation()
    assert source.iteration_lights.consume() is None


def run_probe(monkeypatch, *, optimized, state=MANUAL, frames=60, stall_site=None):
    """Deterministic blocking-cost model; the returned times are NOT live FPS."""
    clock = Clock()
    counts = {"bounds": 0, "lights": 0, "checked_send": 0, "draw": 0}

    class Hero(FakeResearchVehicle):
        @property
        def bounding_box(self):
            counts["bounds"] += 1
            clock.advance(0.0217)
            return self.box

        @bounding_box.setter
        def bounding_box(self, value):
            self.box = value

        def get_light_state(self):
            counts["lights"] += 1
            clock.advance(0.0216)
            return self.current_light_state

    hero = Hero()
    world = FakeResearchWorld(hero)
    source = ResearchNoAObservationSource(
        world,
        hero,
        monotonic_ns=lambda: int(clock.now * 1e9),
        utc_ns=lambda: int(clock.now * 1e9),
    )
    source.iteration_lights = IterationLightState(clock)
    events, controls, observations, audio = [], [], [], []
    interaction = AutomationInteractionController(
        runtime=AutomationRuntimeController(state, RecordingBackend()),
        geometry=SimpleNamespace(observe_with_context=lambda _: geometry()),
        vehicle=hero,
        steering_target=None,
        config=config(),
        event_sink=events.append,
        cache_static_bounds=optimized,
    )

    class Client:
        def apply_batch_sync(self, commands, do_tick):
            assert do_tick is False
            assert len(commands) == 1
            command = commands[0]
            assert command.actor_id == hero.id
            counts["checked_send"] += 1
            clock.advance(0.0182)
            control = command.control
            controls.append((control.throttle, control.brake, control.steer))
            hero.apply_control(control)
            return [SimpleNamespace(actor_id=hero.id, has_error=lambda: False)]

    view = ResearchLiveDriverView(
        world,
        hero,
        ResearchDriverViewConfig(
            automation_state_source=lambda: interaction.state,
            manual_control_limits=ResearchManualControlLimits(1, 1, 1),
            control_sender=make_checked_control_sender(Client(), hero.id, clock=clock),
            iteration_light_state=source.iteration_lights if optimized else None,
        ),
    )
    diagnostics = ResearchCameraDiagnostics(
        clock, control_mode_source=lambda: interaction.state.control_mode
    )
    view._camera_diagnostics = view._performance_observer = diagnostics
    view._turn_signal_audio = SimpleNamespace(
        update=lambda **kw: audio.append(kw["active"]), close=lambda: None
    )
    index = 0

    def poll():
        nonlocal index
        index += 1
        if index > frames:
            return [pygame.event.Event(pygame.QUIT)]
        if index == 1 and stall_site == "event_poll":
            clock.advance(1.0)
        hero.current_light_state = (
            carla.VehicleLightState.LeftBlinker
            if index % 2
            else carla.VehicleLightState.NONE
        )
        return (
            [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F11)]
            if index == 1 and stall_site == "fullscreen_transition"
            else []
        )

    def input_sample(events):
        if state == ACTIVE:
            return DriverInput()
        return DriverInput(
            throttle=0.6,
            brake=0.3 if index % 5 == 0 else 0,
            steering=(-0.2 if index % 2 else 0.2),
        )

    def post_control():
        interaction.after_control_applied()
        observations.append(source.get_observation().state)

    def draw(_):
        counts["draw"] += 1
        clock.advance(0.004)

    monkeypatch.setattr(view, "_collect_driver_input", input_sample)
    monkeypatch.setattr(view, "_draw", draw)
    monkeypatch.setattr(view, "_close_input", lambda: None)
    monkeypatch.setattr(view, "_open_display", lambda: None)
    monkeypatch.setattr(view, "_prepare_frame", lambda: None)
    monkeypatch.setattr(view, "_handle_mode_events", lambda _: None)
    monkeypatch.setattr(driver_view.time, "monotonic", clock)
    monkeypatch.setattr(pygame.event, "get", poll)
    monkeypatch.setattr(pygame.display, "flip", lambda: clock.advance(0.0006))
    monkeypatch.setattr(pygame, "quit", lambda: None)
    monkeypatch.setattr(
        pygame.time, "Clock", lambda: SimpleNamespace(tick=lambda _: None)
    )
    from src.scenario.driver_view_presentation import DriverViewPresentation

    presentation = DriverViewPresentation((1280, 720), None)
    handle_events = presentation.handle_events

    def handle(events):
        if index == 1 and stall_site == "presentation_events":
            clock.advance(1.0)
        handle_events(events)

    monkeypatch.setattr(presentation, "handle_events", handle)
    monkeypatch.setattr(presentation, "_toggle_fullscreen", lambda: clock.advance(1.0))
    monkeypatch.setattr(
        driver_view, "DriverViewPresentation", lambda *args: presentation
    )
    assert (
        view.run(
            1000,
            scheduler=SimpleNamespace(
                update=lambda: interaction.state.control_mode == ACTIVE.control_mode
            ),
            input_observer=SimpleNamespace(
                update=interaction.update, after_control_applied=post_control
            ),
        )
        is True
    )
    payload = json.loads(diagnostics.to_json([]))
    return {
        "counts": counts,
        "loop_mean_ms": payload["timing"]["driver_loop"]["mean_ms"],
        "controls": controls,
        "observations": [
            (s.throttle, s.brake, s.steering, s.indicator) for s in observations
        ],
        "audio": audio,
        "events": [(e.event_type.value, e.state, e.failure_reason) for e in events],
        "diagnostics": payload,
    }


@pytest.mark.parametrize("state", [MANUAL, ACTIVE])
def test_actual_pipeline_reduces_waits_without_dropping_control_or_observations(
    monkeypatch, state
):
    before = run_probe(monkeypatch, optimized=False, state=state)
    after = run_probe(monkeypatch, optimized=True, state=state)
    for key in ("controls", "observations", "audio", "events"):
        assert after[key] == before[key]
    assert after["counts"]["draw"] == before["counts"]["draw"] == 60
    assert (
        after["counts"]["checked_send"]
        == before["counts"]["checked_send"]
        == (60 if state == MANUAL else 0)
    )
    assert before["counts"]["lights"] == 120
    assert after["counts"]["lights"] == 60
    assert after["counts"]["bounds"] == (1 if state == MANUAL else 0)
    assert after["loop_mean_ms"] < before["loop_mean_ms"] * 0.65
    if state == MANUAL:
        for throttle, brake, steer in after["controls"]:
            assert abs(steer) == pytest.approx(0.2)
            assert throttle == pytest.approx(0 if brake > 0 else 0.6)


@pytest.mark.parametrize(
    "site", ["event_poll", "presentation_events", "fullscreen_transition"]
)
def test_stall_spans_identify_poll_presentation_and_fullscreen(monkeypatch, site):
    probe = run_probe(monkeypatch, optimized=True, frames=2, stall_site=site)
    result = probe["diagnostics"]
    assert result["slowest_completed_loop"]["segments"][site]["max_ms"] == 1000
    assert result["timing"]["event_poll_thread_cpu"]["count"] == 3
    # A one-second wait does not drop either control or observation.
    assert probe["counts"]["checked_send"] == len(probe["observations"]) == 2
