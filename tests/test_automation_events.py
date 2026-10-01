from __future__ import annotations

import csv
from collections.abc import Callable
from pathlib import Path

import pytest

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.automation_events import ResearchAutomationRuntimeController
from src.experiment.automation_runtime import AutomationRuntimeController
from src.experiment.context import (
    ExperimentModule,
    ExperimentPhase,
    Module1Condition,
    Module2Condition,
    OutcomeFamily,
    ParticipantId,
    SegmentContext,
    SegmentId,
    StudyRunContext,
    StudyRunId,
)
from src.experiment.events import ResearchEventRecorder
from src.experiment.timestamp import (
    CarlaSnapshotTimestamp,
    HostClockTimestamp,
    TimestampEnvelope,
)
from src.logging.csv_logger import (
    RESEARCH_EVENT_HEADER,
    ResearchCsvLogger,
    ResearchEvent,
)
from src.vehicle import VehicleObservation, VehicleState


class BackendTransitionError(RuntimeError):
    pass


class RecordingAutomationBackend:
    def __init__(
        self,
        *,
        fail_manual: bool = False,
        fail_noa: bool = False,
    ) -> None:
        self.calls: list[str] = []
        self.fail_manual = fail_manual
        self.fail_noa = fail_noa

    def enter_manual_control(self) -> None:
        self.calls.append("manual")
        if self.fail_manual:
            raise BackendTransitionError

    def enter_noa_control(self) -> None:
        self.calls.append("noa")
        if self.fail_noa:
            raise BackendTransitionError


class FakeObservationSource:
    def __init__(self, observation: VehicleObservation) -> None:
        self.observation = observation
        self.call_count = 0

    def get_observation(self) -> VehicleObservation:
        self.call_count += 1
        return self.observation


class RecordingEventWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[SegmentContext, TimestampEnvelope, ResearchEvent]] = []
        self.mode_reader: Callable[[], DrivingControlMode] | None = None
        self.modes_at_write: list[DrivingControlMode] = []

    def write_event(
        self,
        segment: SegmentContext,
        timestamp: TimestampEnvelope,
        event: ResearchEvent,
    ) -> None:
        if self.mode_reader is not None:
            self.modes_at_write.append(self.mode_reader())
        self.calls.append((segment, timestamp, event))


def make_study_run() -> StudyRunContext:
    return StudyRunContext(
        study_run_id=StudyRunId("run-001"),
        participant_id=ParticipantId("participant-007"),
        module_1_condition=Module1Condition.SURT,
        module_2_condition=Module2Condition.MANUAL,
    )


def make_segment() -> SegmentContext:
    return SegmentContext(
        segment_id=SegmentId("segment-m1"),
        study_run_id=StudyRunId("run-001"),
        phase=ExperimentPhase.MODULE_1,
        module=ExperimentModule.MODULE_1,
        condition=Module1Condition.SURT,
        outcome_family=OutcomeFamily.AUTOMATION_CHOICE,
    )


def make_observation() -> VehicleObservation:
    return VehicleObservation(
        timestamp=TimestampEnvelope(
            host=HostClockTimestamp(monotonic_ns=1_250, utc_ns=1_700_000_000),
            carla_snapshot=CarlaSnapshotTimestamp(
                simulation_seconds=12.5,
                frame=321,
                host_capture_started_monotonic_ns=1_000,
                host_capture_completed_monotonic_ns=1_250,
            ),
        ),
        state=VehicleState(
            timestamp=12.5,
            speed_kmh=80.0,
            steering=0.0,
            throttle=0.0,
            brake=0.0,
            lane_id=2,
            indicator="off",
        ),
    )


def make_controller(
    initial_mode: DrivingControlMode,
    backend: RecordingAutomationBackend,
    source: FakeObservationSource,
    writer: RecordingEventWriter | ResearchCsvLogger,
) -> ResearchAutomationRuntimeController:
    runtime = AutomationRuntimeController(
        AutomationState(AutomationAvailability.AVAILABLE, initial_mode),
        backend,
    )
    if isinstance(writer, RecordingEventWriter):
        writer.mode_reader = lambda: runtime.state.control_mode
    return ResearchAutomationRuntimeController(
        runtime,
        ResearchEventRecorder(source, writer, make_segment()),
    )


def test_activation_commits_before_recording_one_research_event() -> None:
    backend = RecordingAutomationBackend()
    observation = make_observation()
    source = FakeObservationSource(observation)
    writer = RecordingEventWriter()
    controller = make_controller(DrivingControlMode.MANUAL, backend, source, writer)

    state = controller.request_control_mode(DrivingControlMode.NOA_ACTIVE)

    assert backend.calls == ["noa"]
    assert state.control_mode is DrivingControlMode.NOA_ACTIVE
    assert writer.modes_at_write == [DrivingControlMode.NOA_ACTIVE]
    assert writer.calls == [
        (
            make_segment(),
            observation.timestamp,
            ResearchEvent(event_type="automation_activated"),
        )
    ]
    assert source.call_count == 1


def test_deactivation_commits_before_recording_one_research_event() -> None:
    backend = RecordingAutomationBackend()
    source = FakeObservationSource(make_observation())
    writer = RecordingEventWriter()
    controller = make_controller(DrivingControlMode.NOA_ACTIVE, backend, source, writer)

    state = controller.request_control_mode(DrivingControlMode.MANUAL)

    assert backend.calls == ["manual"]
    assert state.control_mode is DrivingControlMode.MANUAL
    assert writer.modes_at_write == [DrivingControlMode.MANUAL]
    assert writer.calls[0][2] == ResearchEvent(event_type="automation_deactivated")
    assert source.call_count == 1


@pytest.mark.parametrize(
    ("initial_mode", "requested_mode", "fail_manual", "fail_noa", "backend_call"),
    [
        (DrivingControlMode.MANUAL, DrivingControlMode.NOA_ACTIVE, False, True, "noa"),
        (
            DrivingControlMode.NOA_ACTIVE,
            DrivingControlMode.MANUAL,
            True,
            False,
            "manual",
        ),
    ],
)
def test_backend_failure_preserves_state_without_recording_event(
    initial_mode: DrivingControlMode,
    requested_mode: DrivingControlMode,
    fail_manual: bool,
    fail_noa: bool,
    backend_call: str,
) -> None:
    backend = RecordingAutomationBackend(
        fail_manual=fail_manual,
        fail_noa=fail_noa,
    )
    source = FakeObservationSource(make_observation())
    writer = RecordingEventWriter()
    controller = make_controller(initial_mode, backend, source, writer)

    with pytest.raises(BackendTransitionError):
        controller.request_control_mode(requested_mode)

    assert controller.state.control_mode is initial_mode
    assert backend.calls == [backend_call]
    assert writer.calls == []
    assert source.call_count == 0


@pytest.mark.parametrize(
    "mode",
    [DrivingControlMode.MANUAL, DrivingControlMode.NOA_ACTIVE],
)
def test_duplicate_mode_request_does_not_call_backend_or_record_event(
    mode: DrivingControlMode,
) -> None:
    backend = RecordingAutomationBackend()
    source = FakeObservationSource(make_observation())
    writer = RecordingEventWriter()
    controller = make_controller(mode, backend, source, writer)

    state = controller.request_control_mode(mode)

    assert state.control_mode is mode
    assert backend.calls == []
    assert writer.calls == []
    assert source.call_count == 0


def test_availability_loss_records_the_committed_deactivation() -> None:
    backend = RecordingAutomationBackend()
    source = FakeObservationSource(make_observation())
    writer = RecordingEventWriter()
    controller = make_controller(DrivingControlMode.NOA_ACTIVE, backend, source, writer)

    state = controller.set_availability(AutomationAvailability.UNAVAILABLE)

    assert state == AutomationState(
        AutomationAvailability.UNAVAILABLE,
        DrivingControlMode.MANUAL,
    )
    assert backend.calls == ["manual"]
    assert writer.calls[0][2] == ResearchEvent(event_type="automation_deactivated")


def test_activation_preserves_research_csv_context_and_timestamp(
    tmp_path: Path,
) -> None:
    backend = RecordingAutomationBackend()
    observation = make_observation()
    source = FakeObservationSource(observation)
    logger = ResearchCsvLogger(make_study_run(), tmp_path)
    controller = make_controller(DrivingControlMode.MANUAL, backend, source, logger)

    controller.request_control_mode(DrivingControlMode.NOA_ACTIVE)
    logger.close()

    with logger.event_path.open(newline="") as event_file:
        row = next(iter(csv.DictReader(event_file)))
    assert {key: row[key] for key in ("study_run_id", "participant_id")} == {
        "study_run_id": "run-001",
        "participant_id": "participant-007",
    }
    assert {key: row[key] for key in ("segment_id", "phase", "condition")} == {
        "segment_id": "segment-m1",
        "phase": "MODULE_1",
        "condition": "SURT",
    }
    assert {key: row[key] for key in ("host_monotonic_ns", "carla_frame")} == {
        "host_monotonic_ns": "1250",
        "carla_frame": "321",
    }
    assert row[RESEARCH_EVENT_HEADER[-2]] == "automation_activated"
    assert row[RESEARCH_EVENT_HEADER[-1]] == ""
