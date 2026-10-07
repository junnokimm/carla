from __future__ import annotations

from pathlib import Path

import pytest

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.scenario.research_noa_runtime import ResearchNoARunner
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeResearchVehicle,
    FakeResearchWorld,
)
from tests.test_p4_research_noa_integration import (
    IncrementingNanoseconds,
    InjectedP4RunError,
    make_config,
    make_module_two_config,
    read_dicts,
)


class InjectedLifecycleWriteError(RuntimeError):
    pass


def test_lifecycle_and_shutdown_events_use_host_only_timestamps(
    tmp_path: Path,
) -> None:
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        make_module_two_config(tmp_path, "run-host-only"),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
        monotonic_ns=IncrementingNanoseconds(),
        utc_ns=lambda: 1,
    )

    with runner.session() as session:
        viewer_factory.created[0].actions = []
        session.run()

    assert runner.log_paths is not None
    lifecycle = [
        row
        for row in read_dicts(runner.log_paths.events)
        if row["event_type"] in {"stage_start", "stage_end"}
        or row["event_value"] == "SAFETY_SHUTDOWN"
    ]
    assert lifecycle
    assert all(row["carla_frame"] == "" for row in lifecycle)
    assert all(row["carla_simulation_seconds"] == "" for row in lifecycle)
    assert lifecycle[-1]["event_value"].endswith(":NORMAL_COMPLETION")


def test_shutdown_event_records_supplied_post_shutdown_state(tmp_path: Path) -> None:
    runner = ResearchNoARunner(
        make_config(tmp_path, "run-shutdown-state"),
        client=FakeClient(FakeResearchWorld(FakeResearchVehicle())),
        viewer_factory=FakeDriverViewFactory(),
        monotonic_ns=IncrementingNanoseconds(),
        utc_ns=lambda: 1,
    )

    with runner.session() as session:
        assert session.persistence is not None
        session.persistence.record_shutdown(
            "SAFETY_SHUTDOWN",
            AutomationState(
                AutomationAvailability.UNAVAILABLE,
                DrivingControlMode.MANUAL,
            ),
        )

    assert runner.log_paths is not None
    auto_state = next(
        row
        for row in read_dicts(runner.log_paths.events)
        if row["event_type"] == "auto_state"
    )
    assert auto_state["event_value"] == "UNAVAILABLE|MANUAL"


def test_user_exit_has_distinct_stage_end_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        make_config(tmp_path, "run-user-exit"),
        client=FakeClient(FakeResearchWorld(FakeResearchVehicle())),
        viewer_factory=viewer_factory,
        monotonic_ns=IncrementingNanoseconds(),
        utc_ns=lambda: 1,
    )

    with runner.session() as session:
        monkeypatch.setattr(
            viewer_factory.created[0], "run", lambda *args, **kwargs: True
        )
        session.run()

    assert runner.log_paths is not None
    stage_end = next(
        row
        for row in read_dicts(runner.log_paths.events)
        if row["event_type"] == "stage_end"
    )
    assert stage_end["event_value"].endswith(":USER_EXIT")


def test_activation_event_write_failure_immediately_applies_safe_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vehicle = FakeResearchVehicle()
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        make_module_two_config(tmp_path, "run-activation-write-error"),
        client=FakeClient(FakeResearchWorld(vehicle)),
        viewer_factory=viewer_factory,
        monotonic_ns=IncrementingNanoseconds(),
        utc_ns=lambda: 1,
    )

    with runner.session() as session:
        assert session.persistence is not None
        original = session.persistence.logger.write_event

        def fail_committed_state(segment, timestamp, event) -> None:
            if event.event_type == "auto_state":
                raise InjectedLifecycleWriteError("auto-state write failed")
            original(segment, timestamp, event)

        monkeypatch.setattr(
            session.persistence.logger, "write_event", fail_committed_state
        )

        with pytest.raises(InjectedLifecycleWriteError, match="auto-state"):
            session.run()

        assert session.bundle.automation_runtime.state.control_mode is (
            DrivingControlMode.MANUAL
        )
        assert vehicle.applied_controls[-1].brake == pytest.approx(0.5)


def test_shutdown_log_failure_does_not_mask_primary_run_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vehicle = FakeResearchVehicle()
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        make_module_two_config(tmp_path, "run-shutdown-log-error"),
        client=FakeClient(FakeResearchWorld(vehicle)),
        viewer_factory=viewer_factory,
        monotonic_ns=IncrementingNanoseconds(),
        utc_ns=lambda: 1,
    )

    with runner.session() as session:
        assert session.persistence is not None
        original = session.persistence.logger.write_event

        def fail_shutdown_event(segment, timestamp, event) -> None:
            if event.event_type == "disengagement":
                raise InjectedLifecycleWriteError("shutdown write failed")
            original(segment, timestamp, event)

        monkeypatch.setattr(
            session.persistence.logger, "write_event", fail_shutdown_event
        )
        viewer_factory.created[0].actions = [
            lambda: (_ for _ in ()).throw(InjectedP4RunError("primary run failure"))
        ]

        with pytest.raises(InjectedP4RunError, match="primary run failure"):
            session.run()

        assert session.bundle.automation_runtime.state.control_mode is (
            DrivingControlMode.MANUAL
        )
        assert vehicle.applied_controls[-1].brake == pytest.approx(0.5)


def test_end_stage_write_failure_does_not_mask_primary_run_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vehicle = FakeResearchVehicle()
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        make_config(tmp_path, "run-stage-end-error"),
        client=FakeClient(FakeResearchWorld(vehicle)),
        viewer_factory=viewer_factory,
        monotonic_ns=IncrementingNanoseconds(),
        utc_ns=lambda: 1,
    )

    with runner.session() as session:
        assert session.persistence is not None
        original = session.persistence.logger.write_event

        def fail_stage_end(segment, timestamp, event) -> None:
            if event.event_type == "stage_end":
                raise InjectedLifecycleWriteError("stage end write failed")
            original(segment, timestamp, event)

        monkeypatch.setattr(session.persistence.logger, "write_event", fail_stage_end)
        viewer_factory.created[0].actions = [
            lambda: (_ for _ in ()).throw(InjectedP4RunError("primary run failure"))
        ]

        with pytest.raises(InjectedP4RunError, match="primary run failure"):
            session.run()


def test_telemetry_failure_is_preserved_after_safe_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vehicle = FakeResearchVehicle()
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        make_module_two_config(tmp_path, "run-telemetry-error"),
        client=FakeClient(FakeResearchWorld(vehicle)),
        viewer_factory=viewer_factory,
        monotonic_ns=IncrementingNanoseconds(),
        utc_ns=lambda: 1,
    )
    primary = OSError("primary telemetry failure")
    calls = 0

    with runner.session() as session:
        assert session.persistence is not None

        def fail_telemetry(segment, timestamp, state) -> None:
            nonlocal calls
            calls += 1
            raise primary if calls == 1 else OSError("secondary telemetry failure")

        def install_failure() -> None:
            monkeypatch.setattr(
                session.persistence.logger,
                "write_telemetry",
                fail_telemetry,
            )

        viewer_factory.created[0].actions = [install_failure]

        with pytest.raises(OSError) as caught:
            session.run()

        assert caught.value is primary
        assert session.bundle.automation_runtime.state.control_mode is (
            DrivingControlMode.MANUAL
        )
        assert vehicle.applied_controls[-1].brake == pytest.approx(0.5)


def test_normal_stage_end_write_failure_propagates_after_safe_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vehicle = FakeResearchVehicle()
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        make_module_two_config(tmp_path, "run-normal-stage-error"),
        client=FakeClient(FakeResearchWorld(vehicle)),
        viewer_factory=viewer_factory,
        monotonic_ns=IncrementingNanoseconds(),
        utc_ns=lambda: 1,
    )

    with runner.session() as session:
        assert session.persistence is not None
        original = session.persistence.logger.write_event

        def fail_stage_end(segment, timestamp, event) -> None:
            if event.event_type == "stage_end":
                raise InjectedLifecycleWriteError("stage end write failed")
            original(segment, timestamp, event)

        monkeypatch.setattr(session.persistence.logger, "write_event", fail_stage_end)
        viewer_factory.created[0].actions = []

        with pytest.raises(InjectedLifecycleWriteError, match="stage end"):
            session.run()

        assert session.bundle.automation_runtime.state.control_mode is (
            DrivingControlMode.MANUAL
        )
        assert vehicle.applied_controls[-1].brake == pytest.approx(0.5)
