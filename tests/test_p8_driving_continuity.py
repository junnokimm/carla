from pathlib import Path

from src.experiment.automation import DrivingControlMode
from src.experiment.automation_interaction import (
    ActivationFailureReason,
    AutomationInteractionEventType,
    DriverInput,
)
from tests.test_research_noa_nominal_runtime import make_nominal_runner


def test_rejected_noa_requests_during_fast_manual_driving_do_not_abort(
    tmp_path: Path,
) -> None:
    runner, vehicle, factory = make_nominal_runner(
        tmp_path,
        [120.0],
        (
            DriverInput(activation_requested=True, lane_change_in_progress=True),
            DriverInput(activation_requested=True, brake=0.2),
        ),
    )
    with runner.session() as session:
        factory.created[0].actions = [lambda: None, lambda: None]

        report = session.run()

        assert report is not None
        assert report.user_exited is False
        assert report.final_control_mode is DrivingControlMode.MANUAL
        assert report.scheduler_updates == 2
        assert report.control_frames == 0
        failures = [
            event.failure_reason
            for event in session.interaction_events
            if event.event_type is AutomationInteractionEventType.FAILURE
        ]
        assert failures == [
            ActivationFailureReason.LANE_CHANGE_IN_PROGRESS,
            ActivationFailureReason.DRIVER_BRAKE_ACTIVE,
        ]
    assert vehicle.destroy_count == 1
    assert factory.created[0].close_count == 1
