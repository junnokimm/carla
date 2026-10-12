from __future__ import annotations

import pytest

from src.experiment.automation import DrivingControlMode
from src.scenario.research_noa import ResearchNoATransmissionPrimeError
from src.scenario.research_noa_transmission import (
    ResearchTransmissionPrimeFailure,
)
from src.scenario.research_noa_transmission_preparation import (
    TransmissionPreparationError,
    TransmissionPreparationFailure,
)
from tests.research_noa_fakes import FakeResearchVehicle, FakeWorldSnapshot
from tests.research_noa_transmission_support import (
    make_transmission_fixture,
    run_live_frames,
)


def test_deadline_tick_engagement_succeeds_before_timeout_check() -> None:
    fixture = make_transmission_fixture(
        FakeResearchVehicle(initial_gear=0, prime_engages_after_ticks=5)
    )

    report = run_live_frames(fixture, 1)

    assert report.post_prime_gear == 1
    assert fixture.clock() == pytest.approx(0.6)
    assert len(fixture.world.wait_for_tick_calls) == 6


def test_failed_prime_confirmation_blocks_noa_activation() -> None:
    fixture = make_transmission_fixture(
        FakeResearchVehicle(initial_gear=0, prime_engages_after_ticks=None)
    )

    with (
        pytest.raises(ResearchNoATransmissionPrimeError) as caught,
        fixture.runner.session() as session,
    ):
        session.run()

    assert fixture.vehicle.autopilot_calls == [False]
    assert caught.value.failure is (
        ResearchTransmissionPrimeFailure.CONFIRMATION_TIMEOUT
    )
    assert session.bundle.automation_runtime.state.control_mode is (
        DrivingControlMode.MANUAL
    )
    assert session.live_scheduler is not None
    assert session.live_scheduler.commands == ()
    assert len(fixture.vehicle.applied_controls) == 1
    assert len(fixture.world.wait_for_tick_calls) == 5
    assert fixture.clock() == pytest.approx(0.5)
    assert fixture.vehicle.destroy_count == 1


def test_wait_for_tick_failure_blocks_activation_and_preserves_cause() -> None:
    fixture = make_transmission_fixture(FakeResearchVehicle(initial_gear=0))
    wait_error = RuntimeError("fake tick failure")
    fixture.world.wait_error = wait_error

    with (
        pytest.raises(ResearchNoATransmissionPrimeError) as caught,
        fixture.runner.session() as session,
    ):
        session.run()

    assert caught.value.__cause__ is wait_error
    assert session.bundle.automation_runtime.state.control_mode is (
        DrivingControlMode.MANUAL
    )
    assert session.live_scheduler is not None
    assert session.live_scheduler.commands == ()
    assert len(fixture.vehicle.applied_controls) == 1
    assert len(fixture.world.wait_for_tick_calls) == 1
    assert fixture.vehicle.destroy_count == 1


def test_manual_confirmation_rejects_first_tick_at_baseline_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_transmission_fixture(
        FakeResearchVehicle(initial_gear=0, prime_engages_after_ticks=1)
    )
    original_wait_for_tick = fixture.world.wait_for_tick

    def stale_wait_for_tick(seconds: float) -> FakeWorldSnapshot:
        original_wait_for_tick(seconds)
        return FakeWorldSnapshot(0)

    monkeypatch.setattr(fixture.world, "wait_for_tick", stale_wait_for_tick)

    with (
        pytest.raises(ResearchNoATransmissionPrimeError) as caught,
        fixture.runner.session() as session,
    ):
        session.run()

    assert caught.value.failure is ResearchTransmissionPrimeFailure.CONFIRMATION_TIMEOUT
    assert isinstance(caught.value.__cause__, TransmissionPreparationError)
    assert caught.value.__cause__.failure is TransmissionPreparationFailure.FRAME
    assert len(fixture.vehicle.applied_controls) == 1
    assert session.live_scheduler is not None
    assert session.live_scheduler.commands == ()


def test_automatic_confirmation_rejects_manual_phase_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_transmission_fixture(
        FakeResearchVehicle(initial_gear=0, prime_engages_after_ticks=1)
    )
    original_wait_for_tick = fixture.world.wait_for_tick
    tick_count = 0

    def repeated_phase_frame(seconds: float) -> FakeWorldSnapshot:
        nonlocal tick_count
        snapshot = original_wait_for_tick(seconds)
        tick_count += 1
        return FakeWorldSnapshot(1) if tick_count == 2 else snapshot

    monkeypatch.setattr(fixture.world, "wait_for_tick", repeated_phase_frame)

    with (
        pytest.raises(ResearchNoATransmissionPrimeError) as caught,
        fixture.runner.session() as session,
    ):
        session.run()

    assert caught.value.failure is ResearchTransmissionPrimeFailure.AUTOMATIC_ENGAGEMENT
    assert isinstance(caught.value.__cause__, TransmissionPreparationError)
    assert caught.value.__cause__.failure is TransmissionPreparationFailure.FRAME
    assert len(fixture.vehicle.applied_controls) == 2
    assert session.live_scheduler is not None
    assert session.live_scheduler.commands == ()


@pytest.mark.parametrize("gear_switch_time", [-0.1, float("nan"), float("inf")])
def test_invalid_gear_switch_time_fails_before_control_apply(
    gear_switch_time: float,
) -> None:
    fixture = make_transmission_fixture(
        FakeResearchVehicle(initial_gear=0, gear_switch_time=gear_switch_time)
    )

    with (
        pytest.raises(ResearchNoATransmissionPrimeError) as caught,
        fixture.runner.session() as session,
    ):
        session.run()

    assert caught.value.failure is (
        ResearchTransmissionPrimeFailure.INVALID_GEAR_SWITCH_TIME
    )
    assert fixture.vehicle.applied_controls == []
    assert fixture.world.wait_for_tick_calls == []
    assert session.live_scheduler is not None
    assert session.live_scheduler.commands == ()
    assert fixture.vehicle.destroy_count == 1


@pytest.mark.parametrize(
    ("gear_switch_time", "expected_window"),
    [(0.0, 0.5), (0.4, 0.8), (0.8, 1.0)],
)
def test_confirmation_window_is_physics_derived_and_bounded(
    gear_switch_time: float,
    expected_window: float,
) -> None:
    fixture = make_transmission_fixture(
        FakeResearchVehicle(initial_gear=0, gear_switch_time=gear_switch_time)
    )

    run_live_frames(fixture, 1)

    assert fixture.world.wait_for_tick_calls[0] == pytest.approx(expected_window)
