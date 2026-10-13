from __future__ import annotations

from dataclasses import replace

import carla
import pytest

from src.experiment.automation_interaction_types import (
    AutomationInteractionConfig,
    DriverInput,
)
from src.experiment.context import ExperimentModule, Module1Condition
from src.scenario.research_noa_config import (
    ResearchAutomationInteractionConfig,
    ResearchNoARunConfig,
    ResearchNoARunMode,
)
from src.scenario.research_noa_control_delivery import CheckedControlDeliveryError
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.scenario.research_noa_shutdown import ShutdownBrakeCommandOutcome
from src.scenario.research_noa_view import ResearchLiveDriverView
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeMonotonicClock,
    FakeResearchVehicle,
    FakeResearchWorld,
    FakeTrafficManagerRuntimeAttestor,
    make_live_control_config,
)
from tests.test_driver_view import FakeWorld
from tests.test_p8_control_delivery import (
    BatchResponse,
    CacheSeparatedClient,
    CacheSeparatedVehicle,
    ControlledBatchClient,
)


def tm_config() -> ResearchNoARunConfig:
    return ResearchNoARunConfig(
        duration=1.0,
        control_config=replace(
            make_live_control_config(),
            longitudinal=replace(
                make_live_control_config().longitudinal,
                target_speed_kmh=None,
            ),
        ),
        mode=ResearchNoARunMode.LIVE_SMOKE,
        spawn_index=0,
        traffic_manager_port=8000,
    )


def tm_runner(
    client: ControlledBatchClient,
    viewer_factory: FakeDriverViewFactory,
) -> ResearchNoARunner:
    return ResearchNoARunner(
        tm_config(),
        client=client,
        viewer_factory=viewer_factory,
        tm_runtime_attestor=FakeTrafficManagerRuntimeAttestor(),
    )


def test_tm_shutdown_deregisters_before_checked_batch_brake() -> None:
    vehicle = FakeResearchVehicle()

    class EventClient(ControlledBatchClient):
        def apply_batch_sync(
            self,
            commands: list[carla.command.ApplyVehicleControl],
            do_tick: bool = False,
        ) -> list[BatchResponse]:
            vehicle.events.append("batch")
            return super().apply_batch_sync(commands, do_tick)

    client = EventClient(vehicle)

    with tm_runner(client, FakeDriverViewFactory()).session():
        pass

    assert vehicle.events[-2:] == ["autopilot:False", "batch"]


def test_tm_shutdown_brake_bypasses_matching_pre_tm_client_cache() -> None:
    vehicle = CacheSeparatedVehicle()
    client = CacheSeparatedClient(vehicle)
    cached_brake = carla.VehicleControl(brake=0.5)
    vehicle.apply_control(cached_brake)
    vehicle.traffic_manager_control(carla.VehicleControl(throttle=0.85))

    with tm_runner(client, FakeDriverViewFactory()).session():
        pass

    assert vehicle.client_cache.brake == pytest.approx(0.5)
    assert vehicle.get_control().brake == pytest.approx(0.5)
    assert vehicle.get_control().throttle == 0.0


def test_shutdown_batch_response_error_is_raised_and_cleanup_completes() -> None:
    vehicle = FakeResearchVehicle()
    client = ControlledBatchClient(vehicle, [BatchResponse(vehicle.id, "rejected")])
    viewer_factory = FakeDriverViewFactory()

    with pytest.raises(CheckedControlDeliveryError), tm_runner(
        client, viewer_factory
    ).session() as session:
        pass

    assert session.shutdown_brake_outcome.command_call_outcome is (
        ShutdownBrakeCommandOutcome.RAISED
    )
    assert vehicle.destroy_count == 1
    assert viewer_factory.created[0].close_count == 1


class PrimaryRunError(RuntimeError):
    pass


def test_primary_error_survives_shutdown_batch_failure_with_cleanup_note() -> None:
    vehicle = FakeResearchVehicle()
    client = ControlledBatchClient(vehicle, [BatchResponse(vehicle.id, "rejected")])
    viewer_factory = FakeDriverViewFactory()
    expected = PrimaryRunError("primary")

    with pytest.raises(PrimaryRunError) as caught, tm_runner(
        client, viewer_factory
    ).session():
        raise expected

    assert caught.value is expected
    assert any(
        "checked vehicle control delivery failed" in note
        for note in expected.__notes__
    )
    assert vehicle.destroy_count == 1
    assert viewer_factory.created[0].close_count == 1


def test_preparation_tm_manual_and_shutdown_share_the_runners_client() -> None:
    vehicle = FakeResearchVehicle(initial_gear=0)
    clock = FakeMonotonicClock()
    client = FakeClient(FakeResearchWorld(vehicle, monotonic_clock=clock))
    views = FakeDriverViewFactory()
    config = replace(tm_config(), automation_interaction=ResearchAutomationInteractionConfig(
        ExperimentModule.MODULE_1, Module1Condition.NO_SURT,
        AutomationInteractionConfig(0.2, 0.1, 0.05, True, True),
    ))
    runner = ResearchNoARunner(
        config, client=client, viewer_factory=views, monotonic_clock=clock,
        tm_runtime_attestor=FakeTrafficManagerRuntimeAttestor(),
    )
    with runner.session() as session:
        viewer = ResearchLiveDriverView(FakeWorld(), vehicle, views.created[0].config)
        assert viewer._research_config.control_sender is session.control_sender

        def drive() -> None:
            assert session.interaction is not None
            assert session.live_scheduler is not None
            for driver_input in (
                DriverInput(throttle=0.4), DriverInput(activation_requested=True),
                DriverInput(brake=0.1), DriverInput(),
            ):
                viewer._pending_driver_input = driver_input
                session.interaction.update(driver_input)
                if not session.live_scheduler.update():
                    viewer._apply_manual_control()
            clock.advance(config.duration)

        views.created[0].actions = [drive]
        session.run()

    controls = [commands[0].control for commands, _ in client.batch_calls]
    assert controls[0].manual_gear_shift and controls[0].gear == 1
    assert not controls[1].manual_gear_shift
    assert controls[0].brake == controls[1].brake == controls[-1].brake == pytest.approx(0.5)
    assert [control.throttle for control in controls[2:5]] == pytest.approx([0.4, 0.0, 0.0])
    assert controls[3].brake == pytest.approx(0.1)
    assert all(not do_tick for _, do_tick in client.batch_calls)
    assert vehicle.destroy_count == views.created[0].close_count == 1
