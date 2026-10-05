from __future__ import annotations

import pytest

from src.scenario.research_noa import (
    ResearchNoARunConfig,
    ResearchNoARunner,
)
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeResearchVehicle,
    FakeResearchWorld,
    make_live_control_config,
)
from tests.research_noa_transmission_support import (
    make_transmission_fixture,
    run_live_frames,
)
from tests.test_noa_runtime import FakeVelocity


def test_stopped_gear_zero_primes_once_before_active_frames() -> None:
    fixture = make_transmission_fixture(
        FakeResearchVehicle(initial_gear=0, prime_engages_after_ticks=3)
    )

    report = run_live_frames(fixture, 100)

    prime = fixture.vehicle.applied_controls[0]
    active_controls = fixture.vehicle.applied_controls[1:-1]
    shutdown = fixture.vehicle.applied_controls[-1]
    assert report.initial_gear == 0
    assert report.transmission_prime_required is True
    assert report.transmission_prime_applied is True
    assert report.post_prime_gear == 1
    assert "initial_gear=0" in report.format()
    assert "transmission_prime_required=True" in report.format()
    assert "transmission_prime_applied=True" in report.format()
    assert "post_prime_gear=1" in report.format()
    assert fixture.vehicle.get_control_count == 5
    assert len(fixture.world.wait_for_tick_calls) == 3
    assert prime.throttle == 0.0
    assert prime.brake == 0.5
    assert prime.steer == 0.0
    assert prime.hand_brake is False
    assert prime.reverse is False
    assert prime.manual_gear_shift is True
    assert prime.gear == 1
    assert len(active_controls) == 100
    assert all(control.manual_gear_shift is False for control in active_controls)
    assert (
        sum(control.manual_gear_shift for control in fixture.vehicle.applied_controls)
        == 1
    )
    assert report.scheduler_updates == 100
    assert report.control_frames == 100
    assert len(fixture.vehicle.applied_controls) == report.control_frames + 2
    assert (
        sum(
            control.brake == 0.5
            and control.throttle == 0.0
            and control.steer == 0.0
            and not control.manual_gear_shift
            for control in fixture.vehicle.applied_controls
        )
        == 1
    )
    assert shutdown.brake == 0.5
    assert fixture.vehicle.destroy_count == 1


def test_immediate_prime_engagement_stops_after_first_confirmation_tick() -> None:
    fixture = make_transmission_fixture(
        FakeResearchVehicle(initial_gear=0, prime_engages_after_ticks=1)
    )

    report = run_live_frames(fixture, 1)

    assert report.post_prime_gear == 1
    assert fixture.vehicle.confirmation_ticks == 1
    assert len(fixture.world.wait_for_tick_calls) == 1
    assert fixture.world.wait_for_tick_calls[0] == pytest.approx(0.5)
    assert (
        sum(control.manual_gear_shift for control in fixture.vehicle.applied_controls)
        == 1
    )


def test_engaged_forward_gear_skips_transmission_prime() -> None:
    fixture = make_transmission_fixture(FakeResearchVehicle(initial_gear=1))

    report = run_live_frames(fixture, 2)

    assert report.initial_gear == 1
    assert report.transmission_prime_required is False
    assert report.transmission_prime_applied is False
    assert report.post_prime_gear == 1
    assert fixture.vehicle.get_control_count == 1
    assert fixture.world.wait_for_tick_calls == []
    assert report.control_frames == 2
    assert len(fixture.vehicle.applied_controls) == report.control_frames + 1
    assert all(
        control.manual_gear_shift is False
        for control in fixture.vehicle.applied_controls
    )


def test_moving_gear_zero_fails_closed_without_priming() -> None:
    from src.scenario.research_noa import ResearchNoATransmissionPrimeError

    vehicle = FakeResearchVehicle(initial_gear=0)
    vehicle.velocity = FakeVelocity(1.0 / 3.6, 0.0, 0.0)
    fixture = make_transmission_fixture(vehicle)

    with (
        pytest.raises(ResearchNoATransmissionPrimeError),
        fixture.runner.session() as session,
    ):
        session.run()

    assert fixture.vehicle.autopilot_calls == [False]
    assert session.live_scheduler is not None
    assert session.live_scheduler.commands == ()
    assert fixture.vehicle.applied_controls == []
    assert fixture.world.wait_for_tick_calls == []
    assert fixture.vehicle.destroy_count == 1


def test_dry_run_never_reads_or_primes_transmission() -> None:
    vehicle = FakeResearchVehicle(initial_gear=0)
    world = FakeResearchWorld(vehicle)
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        ResearchNoARunConfig(
            duration=1.0,
            control_config=make_live_control_config(),
        ),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
    )

    with runner.session() as session:
        session.run()

    assert vehicle.get_control_count == 0
    assert vehicle.applied_controls == []
    assert world.wait_for_tick_calls == []
    assert vehicle.destroy_count == 1


@pytest.mark.parametrize(
    ("initial_gear", "reverse"),
    [(-1, False), (0, True)],
)
def test_invalid_forward_prime_state_fails_before_control_apply(
    initial_gear: int,
    *,
    reverse: bool,
) -> None:
    from src.scenario.research_noa import ResearchNoATransmissionPrimeError

    fixture = make_transmission_fixture(
        FakeResearchVehicle(initial_gear=initial_gear, reverse=reverse)
    )

    with (
        pytest.raises(ResearchNoATransmissionPrimeError),
        fixture.runner.session() as session,
    ):
        session.run()

    assert fixture.vehicle.get_control_count == 1
    assert fixture.world.wait_for_tick_calls == []
    assert fixture.vehicle.applied_controls == []
    assert fixture.vehicle.autopilot_calls == [False]
    assert session.live_scheduler is not None
    assert session.live_scheduler.commands == ()
    assert fixture.vehicle.destroy_count == 1
