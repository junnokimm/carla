from __future__ import annotations

import csv
import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from src.experiment.automation_interaction import (
    AutomationInteractionConfig,
    DriverInput,
)
from src.experiment.context import Module1Condition, Module2Condition
from src.experiment.exit_assistance import ExitContext
from src.p8.model import Mode, P8Error, Stage
from src.p8.operator_controller import OperatorController
from src.p8.operator_exit_result import ExitOpportunityClassification
from src.p8.operator_metrics import reduce_module_run
from src.p8.operator_model import (
    DevOutcome,
    OperatorRuntimeConfig,
    Receipt,
    RegistrationRequest,
    TrainingRun,
)
from src.p8.readiness import Readiness
from src.scenario.research_exit_config import (
    ExitEligibilityWindow,
    ResearchExitAssistanceConfig,
)
from src.scenario.research_noa_runtime import ResearchNoARunner
from tests.p8_p5_spawn_fakes import configure_p5_spawn
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverView,
    FakeMonotonicClock,
    FakeResearchVehicle,
    FakeResearchWorld,
    FakeTrafficManagerRuntimeAttestor,
    make_live_control_config,
)
from tests.test_p5_boundary_contracts import observation

ROUTE_MANIFEST = Path("config/town04_exit_routes_dev_v3.json")
ROUTE_ID = "town04-exit-39"


class StrictClock:
    def __init__(self) -> None:
        self.nanoseconds = 10_000_000_000

    def monotonic_ns(self) -> int:
        self.nanoseconds += 1_000_000
        return self.nanoseconds

    def utc_ns(self) -> int:
        return 1_800_000_000_000_000_000 + self.nanoseconds

    def advance(self, seconds: float) -> None:
        self.nanoseconds += round(seconds * 1e9)


@dataclass(slots=True)
class RunnerEvidence:
    borrowed_log_ids: list[int]
    viewers: list[FakeDriverView]
    vehicles: list[FakeResearchVehicle]
    driver_inputs: list[DriverInput]
    failure: BaseException | None = None
    cleanup_failure: RuntimeError | None = None
    p5_scenario: str | None = None
    observed_driver_inputs: list[DriverInput] = field(default_factory=list)
    user_exit: bool = False
    driver_input_action: Callable[[], None] | None = None


class EvidenceDriverView(FakeDriverView):
    def __init__(self, config, evidence: RunnerEvidence) -> None:
        super().__init__(config)
        self.evidence = evidence

    def run(
        self,
        duration,
        scheduler=None,
        diagnostics=None,
        input_observer=None,
        continuation_factory=None,
    ):
        super().run(
            duration,
            scheduler,
            diagnostics,
            input_observer,
            None if self.evidence.user_exit else continuation_factory,
        )
        return self.evidence.user_exit

    def close(self) -> None:
        super().close()
        if self.evidence.cleanup_failure is not None:
            raise self.evidence.cleanup_failure


def runtime_config(
    clock: StrictClock,
    evidence: RunnerEvidence,
    *,
    module_exit: ResearchExitAssistanceConfig | None = None,
    training_exit: ResearchExitAssistanceConfig | None = None,
) -> OperatorRuntimeConfig:
    runtime: OperatorRuntimeConfig

    def build(config, borrowed_log):
        assert config.camera_diagnostics is True
        vehicle = FakeResearchVehicle()
        run_clock = FakeMonotonicClock()
        world = FakeResearchWorld(vehicle, monotonic_clock=run_clock)
        if config.exit_assistance is not None:
            configure_p5_spawn(world, config)

        def viewer_factory(world_arg, hero, view_config):
            viewer = EvidenceDriverView(view_config, evidence)
            if evidence.p5_scenario is not None:
                _configure_p5_viewer(
                    viewer,
                    config.duration,
                    run_clock,
                    clock,
                    evidence,
                )
            elif evidence.failure is None:
                viewer.actions = [lambda: run_clock.advance(config.duration)]
            else:

                def fail_action() -> None:
                    assert evidence.failure is not None
                    raise evidence.failure

                viewer.actions = [fail_action]
            evidence.viewers.append(viewer)
            return viewer

        evidence.borrowed_log_ids.append(id(borrowed_log.logger))
        evidence.vehicles.append(vehicle)

        def next_driver_input() -> DriverInput:
            if evidence.driver_input_action is not None:
                evidence.driver_input_action()
            value = evidence.driver_inputs.pop(0)
            evidence.observed_driver_inputs.append(value)
            return value

        return ResearchNoARunner(
            config,
            client=FakeClient(world),
            viewer_factory=viewer_factory,
            monotonic_clock=run_clock,
            monotonic_ns=clock.monotonic_ns,
            utc_ns=clock.utc_ns,
            driver_input_source=(
                None
                if not evidence.driver_inputs and evidence.p5_scenario is None
                else next_driver_input
            ),
            exit_route_validator=lambda route, carla_map, hero: None,
            borrowed_log=borrowed_log,
            tm_runtime_attestor=FakeTrafficManagerRuntimeAttestor(),
        )

    runtime = OperatorRuntimeConfig(
        spawn_index=0,
        control_config=replace(
            make_live_control_config(),
            longitudinal=replace(
                make_live_control_config().longitudinal,
                target_speed_kmh=None,
            ),
        ),
        speed_policy=None,
        interaction_policy=AutomationInteractionConfig(0.2, 0.1, 0.05, True, True),
        runner_builder=build,
        module_exit=module_exit,
        training_exit=training_exit,
        module_1_exit_window=(
            None if module_exit is None else ExitEligibilityWindow(0.0, 600.0)
        ),
        module_2_exit_window=(
            None if module_exit is None else ExitEligibilityWindow(0.0, 780.0)
        ),
    )
    return runtime


def _configure_p5_viewer(
    viewer: FakeDriverView,
    duration: float,
    run_clock: FakeMonotonicClock,
    clock: StrictClock,
    evidence: RunnerEvidence,
) -> None:
    def binding():
        assert viewer.exit_view_binding is not None
        return viewer.exit_view_binding

    def present() -> None:
        current = binding()
        route = current.observer.coordinator.config.route
        current.observer.coordinator.observe(
            replace(
                observation(route.confirmation_start, 0.0, frame=10),
                timestamp=current.persistence.source.get_host_timestamp(),
            )
        )
        rendered = current.prepare_presentation()
        assert rendered is not None
        current.mark_presented(rendered)

    def activate_and_present() -> None:
        current = binding()
        current.observer.update(DriverInput(activation_requested=True))
        evidence.observed_driver_inputs.append(DriverInput(activation_requested=True))
        present()

    def complete(outcome: str) -> None:
        current = binding()
        route = current.observer.coordinator.config.route
        completion = route.points[-1].distance_m + 1.0
        if outcome == "EXIT":
            road, section, lane = route.exit_segments[-1]
            observed = observation(
                completion,
                0.0,
                frame=20,
                road=road,
                section=section,
                lane=lane,
                target_boundary_valid=False,
            )
        else:
            road, section, lane = route.through_segments[-1]
            observed = replace(
                observation(
                    completion,
                    0.0,
                    frame=20,
                    road=road,
                    section=section,
                    lane=lane,
                    target_boundary_valid=False,
                ),
                route_reference_distance_m=4.0,
                route_reference_half_width_m=1.0,
            )
        current.observer.coordinator.observe(
            replace(
                observed,
                timestamp=current.persistence.source.get_host_timestamp(),
            )
        )
        run_clock.advance(duration)

    scenario = evidence.p5_scenario
    if scenario in ("off_exit", "initial_exit"):
        evidence.driver_inputs[:] = [DriverInput(), DriverInput()]
        viewer.actions = [present, lambda: complete("EXIT")]
    elif scenario in ("on_confirm_exit", "on_reject_missed"):
        decision = DriverInput(
            exit_confirm_requested=scenario == "on_confirm_exit",
            exit_reject_requested=scenario == "on_reject_missed",
        )
        evidence.driver_inputs[:] = [
            decision,
            DriverInput(),
        ]
        outcome = "EXIT" if scenario == "on_confirm_exit" else "MISSED"
        viewer.actions = [activate_and_present, lambda: complete(outcome)]
    elif scenario == "on_noresponse_missed":
        evidence.driver_inputs[:] = [
            DriverInput(),
            DriverInput(),
            DriverInput(),
        ]
        viewer.actions = [
            activate_and_present,
            lambda: clock.advance(6.0),
            lambda: complete("MISSED"),
        ]
    elif scenario == "mode_change":
        evidence.driver_inputs[:] = [
            DriverInput(deactivation_requested=True),
            DriverInput(),
        ]
        viewer.actions = [activate_and_present, lambda: run_clock.advance(duration)]
    elif scenario == "off_activation_steer":
        evidence.driver_inputs[:] = [
            DriverInput(activation_requested=True, steering_engaged=True, steering=0.1),
            DriverInput(),
        ]
        viewer.actions = [present, lambda: run_clock.advance(duration)]
    elif scenario == "driver_override":
        evidence.driver_inputs[:] = [
            DriverInput(
                exit_confirm_requested=True,
                steering_engaged=True,
            ),
            DriverInput(),
        ]
        viewer.actions = [activate_and_present, lambda: run_clock.advance(duration)]
    else:
        raise AssertionError(f"unknown P5 scenario: {scenario}")


def register_dev(
    tmp_path: Path,
    clock: StrictClock,
    m1: Module1Condition = Module1Condition.NO_SURT,
    m2: Module2Condition = Module2Condition.MANUAL,
    *,
    module_exit: ResearchExitAssistanceConfig | None = None,
    training_exit: ResearchExitAssistanceConfig | None = None,
) -> tuple[OperatorController, RunnerEvidence]:
    evidence = RunnerEvidence([], [], [], [])
    controller = OperatorController(
        tmp_path,
        mode=Mode.DEV_REHEARSAL,
        runtime=runtime_config(
            clock,
            evidence,
            module_exit=module_exit,
            training_exit=training_exit,
        ),
        monotonic_ns=clock.monotonic_ns,
        utc_ns=clock.utc_ns,
    )
    controller.register_id(
        RegistrationRequest(
            participant_id="R-0042",
            run_id="operator-run",
            m1_condition=m1,
            m2_condition=m2,
            route_id=None if module_exit is None else module_exit.route_id,
        )
    )
    return controller, evidence


def exit_template(context: ExitContext) -> ResearchExitAssistanceConfig:
    return ResearchExitAssistanceConfig(
        manifest_path=ROUTE_MANIFEST,
        route_id=ROUTE_ID,
        lc_event_id=f"template:{context.value}:{ROUTE_ID}",
        context=context,
        navigation_trigger_distance_m=1000.0,
        response_timeout_s=5.0,
        lateral_onset_threshold_m=0.15,
        confirm_key="x",
        reject_key="r",
        assisted_execution_enabled=True,
        indicator_confirmation=True,
    )


def test_operator_connects_stage_scoped_p5_to_actual_nominal_runners(
    tmp_path: Path,
) -> None:
    clock = StrictClock()
    controller, _ = register_dev(
        tmp_path,
        clock,
        module_exit=exit_template(ExitContext.MODULE),
        training_exit=exit_template(ExitContext.TRAINING),
    )
    advance_to(controller, Stage.TRAINING)

    training = controller.execute_training(TrainingRun.MANUAL_NOA, 1.0)
    controller.record_execution_policy_outcome(DevOutcome.PARTIAL, "training observed")
    controller.advance()
    controller.record_actual(
        Receipt("calibration_boundary", "EXTERNAL_OPERATOR_STEP", None)
    )
    controller.advance()
    controller.advance()
    module_1 = controller.execute_current_module()
    controller.record_execution_policy_outcome(DevOutcome.PARTIAL, "M1 observed")
    controller.advance()
    controller.advance()
    clock.advance(120.0)
    controller.advance()
    controller.advance()
    module_2 = controller.execute_current_module()

    assert training.exit_opportunity is not None
    assert training.exit_opportunity.context == "TRAINING"
    assert training.exit_opportunity.lc_event_id == (
        f"operator-run:{Stage.TRAINING.value}:{ROUTE_ID}"
    )
    assert module_1.requested_duration_s == 600.0
    assert module_2.requested_duration_s == 780.0
    assert module_1.exit_opportunity is not None
    assert module_2.exit_opportunity is not None
    assert module_1.exit_opportunity.lc_event_id == (
        f"operator-run:{Stage.MODULE_1_RUNNING.value}:{ROUTE_ID}"
    )
    assert module_2.exit_opportunity.lc_event_id == (
        f"operator-run:{Stage.MODULE_2_RUNNING.value}:{ROUTE_ID}"
    )


@pytest.mark.parametrize(
    ("scenario", "classification"),
    [
        ("off_exit", ExitOpportunityClassification.EXIT_CONFIRMED),
        ("on_reject_missed", ExitOpportunityClassification.NORMAL_MISSED),
    ],
)
def test_training_p5_accepts_blank_module_identity(
    tmp_path: Path,
    scenario: str,
    classification: ExitOpportunityClassification,
) -> None:
    controller, evidence = register_dev(
        tmp_path,
        StrictClock(),
        training_exit=exit_template(ExitContext.TRAINING),
    )
    advance_to(controller, Stage.TRAINING)
    evidence.p5_scenario = scenario

    result = controller.execute_training(TrainingRun.MANUAL_NOA, 600.0)

    assert result.exit_opportunity is not None
    assert result.exit_opportunity.classification is classification


@pytest.mark.parametrize(
    ("scenario", "classification", "decision", "noa_state_t0"),
    [
        ("off_exit", ExitOpportunityClassification.EXIT_CONFIRMED, None, "MANUAL"),
        (
            "on_confirm_exit",
            ExitOpportunityClassification.INCOMPLETE,
            "CONFIRM",
            "NOA_ACTIVE",
        ),
        (
            "on_reject_missed",
            ExitOpportunityClassification.NORMAL_MISSED,
            "REJECT",
            "NOA_ACTIVE",
        ),
        (
            "on_noresponse_missed",
            ExitOpportunityClassification.NORMAL_MISSED,
            "NORESPONSE",
            "NOA_ACTIVE",
        ),
        (
            "mode_change",
            ExitOpportunityClassification.PARTICIPANT_INTERRUPTED,
            None,
            "NOA_ACTIVE",
        ),
        (
            "driver_override",
            ExitOpportunityClassification.PARTICIPANT_INTERRUPTED,
            "UNAVAILABLE_DRIVER_PRIORITY",
            "NOA_ACTIVE",
        ),
    ],
)
def test_actual_operator_m1_uses_real_runner_coordinator_and_view_binding(
    tmp_path: Path,
    scenario: str,
    classification: ExitOpportunityClassification,
    decision: str | None,
    noa_state_t0: str,
) -> None:
    clock = StrictClock()
    controller, evidence = register_dev(
        tmp_path,
        clock,
        module_exit=exit_template(ExitContext.MODULE),
    )
    advance_to(controller, Stage.MODULE_1_RUNNING)
    evidence.p5_scenario = scenario

    result = controller.execute_current_module()

    assert result.actual_active_drive_s == pytest.approx(600.0)
    assert result.exit_opportunity is not None
    assert result.exit_opportunity.classification is classification
    assert result.exit_opportunity.decision == decision
    assert result.exit_opportunity.noa_state_t0 == noa_state_t0
    assert result.exit_opportunity.observed_opportunity is (
        classification
        in (
            ExitOpportunityClassification.EXIT_CONFIRMED,
            ExitOpportunityClassification.NORMAL_MISSED,
            ExitOpportunityClassification.BLOCKED,
        )
    )
    assert evidence.observed_driver_inputs
    with controller.session.services.logger.event_path.open(
        newline="", encoding="utf-8"
    ) as source:
        event_ids = {
            json.loads(row["event_value"])["lc_event_id"]
            for row in csv.DictReader(source)
            if row["event_type"].startswith("lc_")
        }
    assert event_ids == {f"operator-run:{Stage.MODULE_1_RUNNING.value}:{ROUTE_ID}"}


@pytest.mark.parametrize(
    ("condition", "expected_t0"),
    [
        (Module2Condition.MANUAL, "MANUAL"),
        (Module2Condition.NOA_L2, "NOA_ACTIVE"),
    ],
)
def test_actual_operator_m2_preserves_assigned_branch_through_observed_exit(
    tmp_path: Path,
    condition: Module2Condition,
    expected_t0: str,
) -> None:
    clock = StrictClock()
    controller, evidence = register_dev(
        tmp_path,
        clock,
        m2=condition,
        module_exit=exit_template(ExitContext.MODULE),
    )
    advance_to(controller, Stage.MODULE_1_RUNNING)
    evidence.p5_scenario = "off_exit"
    controller.execute_current_module()
    controller.record_execution_policy_outcome(DevOutcome.PARTIAL, "M1 observed")
    controller.advance()
    controller.advance()
    clock.advance(120.0)
    controller.advance()
    controller.advance()
    evidence.p5_scenario = "initial_exit"

    result = controller.execute_current_module()

    assert result.actual_active_drive_s == pytest.approx(780.0)
    assert result.exit_opportunity is not None
    assert (
        result.exit_opportunity.classification
        is ExitOpportunityClassification.EXIT_CONFIRMED
    )
    assert result.exit_opportunity.noa_state_t0 == expected_t0
    assert result.initial_mode == expected_t0


def test_user_exit_after_observed_exit_retains_physical_outcome_without_completion(
    tmp_path: Path,
) -> None:
    controller, evidence = register_dev(
        tmp_path,
        StrictClock(),
        module_exit=exit_template(ExitContext.MODULE),
    )
    advance_to(controller, Stage.MODULE_1_RUNNING)
    evidence.p5_scenario = "off_exit"
    evidence.user_exit = True

    with pytest.raises(P8Error, match="USER_EXIT_ABORTED"):
        controller.execute_current_module()

    run = next(
        item
        for item in controller.snapshot().module_runs
        if item.stage is Stage.MODULE_1_RUNNING
    )
    assert run.user_exited is True
    assert run.window_complete is False
    assert run.exit_opportunity is not None
    assert (
        run.exit_opportunity.classification
        is ExitOpportunityClassification.PARTICIPANT_INTERRUPTED
    )
    assert run.exit_opportunity.physical_outcome == "EXIT"
    assert run.exit_opportunity.observed_opportunity is False


def test_operator_rejects_partial_module_when_p5_is_selected_before_runner(
    tmp_path: Path,
) -> None:
    controller, evidence = register_dev(
        tmp_path,
        StrictClock(),
        module_exit=exit_template(ExitContext.MODULE),
    )
    advance_to(controller, Stage.MODULE_1_RUNNING)

    with pytest.raises(P8Error, match="P5_MODULE_EXIT_REQUIRES_NOMINAL_WINDOW"):
        controller.execute_current_module(1.0)

    assert len(evidence.viewers) == 1


def test_registration_route_mismatch_fails_before_claim_side_effects(
    tmp_path: Path,
) -> None:
    assignment = tmp_path / "assignment.csv"
    assignment.write_text(
        "participant_id,m1_condition,m2_condition,route_id,scenario_version,aoi_file_version,program_version\n"
        "R-0042,NO_SURT,MANUAL,other-route,S1,A1,V1\n",
        encoding="utf-8",
    )
    controller = OperatorController(
        tmp_path / "output",
        mode=Mode.DEV_REHEARSAL,
        runtime=runtime_config(
            StrictClock(),
            RunnerEvidence([], [], [], []),
            module_exit=exit_template(ExitContext.MODULE),
        ),
    )

    with pytest.raises(P8Error, match="ASSIGNMENT_EXIT_ROUTE_MISMATCH"):
        controller.register_id(
            RegistrationRequest(
                participant_id="R-0042",
                run_id="route-mismatch",
                assignment_file=assignment,
                route_id=ROUTE_ID,
            )
        )

    assert not (tmp_path / "output" / "_registration_claims").exists()
    assert not (tmp_path / "output" / "route-mismatch").exists()


def advance_to(controller: OperatorController, target: Stage) -> None:
    while controller.snapshot().stage is not target:
        stage = controller.snapshot().stage
        if stage is Stage.CONSENT_AND_ELIGIBILITY:
            controller.record_execution_policy_outcome(
                DevOutcome.SKIPPED,
                "software collection omitted; external management",
            )
        elif stage is Stage.TRAINING:
            if not any(
                item.stage is Stage.TRAINING
                for item in controller.snapshot().module_runs
            ):
                controller.execute_training(TrainingRun.MANUAL_NOA, 1.0)
            controller.record_execution_policy_outcome(
                DevOutcome.PARTIAL,
                "actual training captured; assessment external",
            )
        elif stage is Stage.EYE_TRACKER_CALIBRATION:
            controller.record_actual(
                Receipt("calibration_boundary", "EXTERNAL_OPERATOR_STEP", "UNCONNECTED")
            )
        controller.advance()


def test_operator_runs_actual_training_and_both_modules_with_one_borrowed_logger(
    tmp_path: Path,
) -> None:
    clock = StrictClock()
    controller, evidence = register_dev(tmp_path, clock)
    advance_to(controller, Stage.TRAINING)

    training = controller.execute_training(TrainingRun.MANUAL_NOA, 1.0)
    advance_to(controller, Stage.MODULE_1_RUNNING)
    module_1 = controller.execute_current_module(1.0)
    controller.approve_dev_outcome(DevOutcome.PARTIAL, "bounded M1 route")
    controller.advance()
    controller.advance()
    clock.advance(120.0)
    controller.advance()
    controller.advance()
    module_2 = controller.execute_current_module(1.0)

    assert training.actual_active_drive_s == pytest.approx(1.0)
    assert module_1.initial_mode == "MANUAL"
    assert module_2.initial_mode == "MANUAL"
    assert evidence.borrowed_log_ids == [id(controller.session.services.logger)] * 3
    assert [viewer.close_count for viewer in evidence.viewers] == [1, 1, 1]
    assert [vehicle.destroy_count for vehicle in evidence.vehicles] == [1, 1, 1]
    assert controller.session.services.closed is False


def test_active_timestamps_come_from_persisted_drive_window(tmp_path: Path) -> None:
    event_path = tmp_path / "events.csv"
    with event_path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(
            target,
            fieldnames=(
                "phase",
                "event_type",
                "event_value",
                "session_elapsed_s",
            ),
        )
        writer.writeheader()
        writer.writerows(
            (
                {
                    "phase": "MODULE_1",
                    "event_type": "initial_state",
                    "event_value": "AVAILABLE|MANUAL",
                    "session_elapsed_s": "2.0",
                },
                {
                    "phase": "MODULE_1",
                    "event_type": "stage_start",
                    "event_value": "MODULE_1_RUNNING",
                    "session_elapsed_s": "3.0",
                },
                {
                    "phase": "MODULE_1",
                    "event_type": "stage_end",
                    "event_value": "MODULE_1_RUNNING",
                    "session_elapsed_s": "7.0",
                },
            )
        )

    result = reduce_module_run(
        event_path,
        offset=0,
        stage=Stage.MODULE_1_RUNNING,
        requested_duration_s=4.0,
        actual_active_drive_s=4.0,
        user_exited=False,
        planned_duration_s=600.0,
        runtime_started_session_s=1.0,
        runtime_closed_session_s=9.0,
        completion_reason="TIME_LIMIT_REACHED",
        training_completed_early=False,
        nominal_request=False,
    )

    assert result.actual_active_start_session_s == 3.0
    assert result.actual_active_end_session_s == 7.0
    assert result.runtime_started_session_s == 1.0
    assert result.runtime_closed_session_s == 9.0
    assert result.actual_active_drive_s == 4.0
    assert result.manifest()["window_complete"] is False


def test_operator_rest_advances_immediately_with_truthful_recommended_timing(
    tmp_path: Path,
) -> None:
    clock = StrictClock()
    controller, _ = register_dev(tmp_path, clock)
    advance_to(controller, Stage.MODULE_1_RUNNING)
    controller.execute_current_module(1.0)
    controller.approve_dev_outcome(DevOutcome.PARTIAL, "bounded")
    controller.advance()
    controller.advance()

    rest_start = controller.session.records[-1]["start_session_s"]
    snapshot = controller.advance()
    rest_record = controller.session.records[-2]

    assert snapshot.stage is Stage.MODULE_2_PREPARE
    assert rest_record["minimum_s"] == 0
    assert rest_record["recommended_duration_s"] == 120
    assert rest_record["duration_s"] == pytest.approx(
        rest_record["end_session_s"] - rest_start
    )
    assert 0 <= rest_record["duration_s"] < 120
    assert controller.session.services.profile.module_1_s == 600
    assert controller.session.services.profile.rest_s == 120
    assert controller.session.services.profile.module_2_s == 780


def test_training_requires_positive_actual_run_but_not_synthetic_competency(
    tmp_path: Path,
) -> None:
    clock = StrictClock()
    controller, _ = register_dev(tmp_path, clock)
    advance_to(controller, Stage.TRAINING)

    controller.execute_training(TrainingRun.MANUAL_NOA, 1.0)
    controller.record_execution_policy_outcome(
        DevOutcome.PARTIAL,
        "actual training captured; assessment external and unverified",
    )

    snapshot = controller.advance()

    assert snapshot.stage is Stage.EYE_TRACKER_CALIBRATION
    training_record = controller.session.records[2]
    event_text = controller.session.services.logger.event_path.read_text(
        encoding="utf-8"
    )
    assert training_record["receipts"] == {}
    assert "dev_approval" not in training_record
    assert "manual_driving_status" not in event_text
    assert "operator_assessment_status" not in event_text
    assert "surt_status" not in event_text


def test_training_execution_policy_requires_positive_actual_run(
    tmp_path: Path,
) -> None:
    clock = StrictClock()
    controller, _ = register_dev(tmp_path, clock)
    advance_to(controller, Stage.TRAINING)

    with pytest.raises(P8Error, match="POSITIVE_ACTUAL_TRAINING_RUN_REQUIRED"):
        controller.session.record_execution_policy_outcome(
            DevOutcome.PARTIAL,
            "actual duration unavailable; skill assessment external",
        )


@pytest.mark.parametrize(
    ("stage", "outcome"),
    [
        (Stage.CONSENT_AND_ELIGIBILITY, DevOutcome.PARTIAL),
        (Stage.TRAINING, DevOutcome.SKIPPED),
        (Stage.MODULE_1_RUNNING, DevOutcome.SKIPPED),
        (Stage.MODULE_2_RUNNING, DevOutcome.SKIPPED),
        (Stage.FINAL_HAZARD, DevOutcome.PARTIAL),
        (Stage.POST_EXPERIMENT, DevOutcome.PARTIAL),
    ],
)
def test_execution_policy_rejects_outcome_not_allowed_for_stage(
    tmp_path: Path,
    stage: Stage,
    outcome: DevOutcome,
) -> None:
    controller, _ = register_dev(tmp_path, StrictClock())
    controller.session.state = stage
    controller.session.services.stage = stage

    with pytest.raises(P8Error, match="OUTCOME_NOT_ALLOWED_FOR_STAGE"):
        controller.session.record_execution_policy_outcome(outcome, "policy")


def test_module_partial_requires_actual_bounded_run(tmp_path: Path) -> None:
    clock = StrictClock()
    controller, _ = register_dev(tmp_path, clock)
    advance_to(controller, Stage.MODULE_1_RUNNING)

    with pytest.raises(P8Error, match="ACTUAL_BOUNDED_RUN"):
        controller.approve_dev_outcome(DevOutcome.PARTIAL, "run omitted")


def test_unconnected_surt_and_calibration_cannot_be_confirmed_as_real(
    tmp_path: Path,
) -> None:
    clock = StrictClock()
    controller, _ = register_dev(tmp_path, clock)
    advance_to(controller, Stage.TRAINING)

    with pytest.raises(P8Error, match="SURT_UNCONNECTED"):
        controller.record_actual(Receipt("surt", "OPERATOR_CONFIRMED", True))

    advance_to(controller, Stage.EYE_TRACKER_CALIBRATION)
    with pytest.raises(P8Error, match="CALIBRATION_REQUIRES_EXTERNAL"):
        controller.record_actual(
            Receipt("calibration_boundary", "OPERATOR_CONFIRMED", True)
        )


@pytest.mark.parametrize("duration", [1.0, 10.0, 20.0])
def test_operator_accepts_bounded_actual_duration(
    tmp_path: Path, duration: float
) -> None:
    clock = StrictClock()
    controller, _ = register_dev(tmp_path, clock)
    advance_to(controller, Stage.MODULE_1_RUNNING)

    assert controller.execute_current_module(duration).requested_duration_s == duration


def test_operator_accepts_module_duration_through_sixty(tmp_path: Path) -> None:
    clock = StrictClock()
    controller, _ = register_dev(tmp_path, clock)
    advance_to(controller, Stage.MODULE_1_RUNNING)

    assert controller.execute_current_module(60.0).requested_duration_s == 60.0


def test_operator_defaults_launch_one_nominal_runner_per_module(
    tmp_path: Path,
) -> None:
    clock = StrictClock()
    controller, evidence = register_dev(tmp_path, clock)
    advance_to(controller, Stage.MODULE_1_RUNNING)

    module_1 = controller.execute_current_module()
    controller.record_execution_policy_outcome(DevOutcome.PARTIAL, "nominal M1")
    controller.advance()
    controller.advance()
    clock.advance(120.0)
    controller.advance()
    controller.advance()
    module_2 = controller.execute_current_module()

    assert [module_1.requested_duration_s, module_2.requested_duration_s] == [
        600.0,
        780.0,
    ]
    assert [
        module_1.actual_active_drive_s,
        module_2.actual_active_drive_s,
    ] == pytest.approx([600.0, 780.0])
    assert module_1.manifest()["window_complete"] is True
    assert module_2.manifest()["window_complete"] is True
    assert module_1.nominal_on_proportion is not None
    assert module_2.nominal_on_proportion is not None
    assert len(evidence.viewers) == 3


def test_operator_rejects_module_duration_over_sixty(tmp_path: Path) -> None:
    clock = StrictClock()
    controller, _ = register_dev(tmp_path, clock)
    advance_to(controller, Stage.MODULE_1_RUNNING)

    with pytest.raises(P8Error, match="1_TO_60"):
        controller.execute_current_module(60.001)


def test_operator_accepts_training_duration_through_six_hundred(tmp_path: Path) -> None:
    clock = StrictClock()
    controller, _ = register_dev(tmp_path, clock)
    advance_to(controller, Stage.TRAINING)

    result = controller.execute_training(TrainingRun.MANUAL_NOA, 600.0)

    assert result.requested_duration_s == 600.0
    assert result.planned_duration_s == 600.0
    assert result.completion_reason == "TIME_LIMIT_REACHED"


def test_operator_rejects_second_bounded_run_for_the_same_stage(
    tmp_path: Path,
) -> None:
    clock = StrictClock()
    controller, evidence = register_dev(tmp_path, clock)
    advance_to(controller, Stage.TRAINING)

    controller.execute_training(TrainingRun.MANUAL_NOA, 1.0)
    with pytest.raises(P8Error, match="ONE_ACTUAL_BOUNDED_RUN_PER_STAGE"):
        controller.execute_training(TrainingRun.MANUAL_NOA, 1.0)
    assert controller.snapshot().stage is Stage.TRAINING

    advance_to(controller, Stage.MODULE_1_RUNNING)
    controller.execute_current_module(1.0)
    with pytest.raises(P8Error, match="ONE_ACTUAL_BOUNDED_RUN_PER_STAGE"):
        controller.execute_current_module(1.0)
    assert controller.snapshot().stage is Stage.MODULE_1_RUNNING

    assert len(evidence.viewers) == 2


@pytest.mark.parametrize("m1", list(Module1Condition))
@pytest.mark.parametrize("m2", list(Module2Condition))
def test_all_assignments_run_actual_m2_with_assigned_initial_state(
    tmp_path: Path,
    m1: Module1Condition,
    m2: Module2Condition,
) -> None:
    clock = StrictClock()
    controller, _ = register_dev(tmp_path, clock, m1, m2)
    advance_to(controller, Stage.MODULE_1_RUNNING)
    controller.execute_current_module(1.0)
    controller.approve_dev_outcome(DevOutcome.PARTIAL, "bounded M1")
    controller.advance()
    controller.advance()
    clock.advance(120.0)
    controller.advance()
    controller.advance()

    result = controller.execute_current_module(1.0)

    expected = "MANUAL" if m2 is Module2Condition.MANUAL else "NOA_ACTIVE"
    assert result.initial_mode == expected
    assert controller.session.services.context.module_1_condition is m1
    assert controller.session.services.context.module_2_condition is m2


def test_registration_claim_and_run_outputs_are_exclusive(tmp_path: Path) -> None:
    first_clock = StrictClock()
    first, _ = register_dev(tmp_path, first_clock)
    first.abort("partial restart test")
    second = OperatorController(
        tmp_path,
        mode=Mode.DEV_REHEARSAL,
        runtime=runtime_config(StrictClock(), RunnerEvidence([], [], [], [])),
    )

    with pytest.raises(P8Error, match="ALREADY_CLAIMED"):
        second.register_id(
            RegistrationRequest(
                participant_id="R-0042",
                run_id="new-run",
                m1_condition=Module1Condition.SURT,
                m2_condition=Module2Condition.NOA_L2,
            )
        )

    assert (tmp_path / "operator-run").exists()
    assert not (tmp_path / "new-run").exists()


def test_duplicate_run_id_does_not_claim_a_second_participant(tmp_path: Path) -> None:
    first, _ = register_dev(tmp_path, StrictClock())
    first.abort("duplicate run test")
    second = OperatorController(
        tmp_path,
        mode=Mode.DEV_REHEARSAL,
        runtime=runtime_config(StrictClock(), RunnerEvidence([], [], [], [])),
    )

    with pytest.raises(P8Error, match="RUN_ID_ALREADY_CLAIMED"):
        second.register_id(
            RegistrationRequest(
                participant_id="R-0043",
                run_id="operator-run",
                m1_condition=Module1Condition.SURT,
                m2_condition=Module2Condition.NOA_L2,
            )
        )

    assert not (tmp_path / "_registration_claims" / "R-0043.json").exists()


def test_actual_runner_error_aborts_and_cleans_owned_resources(tmp_path: Path) -> None:
    clock = StrictClock()
    controller, evidence = register_dev(tmp_path, clock)
    advance_to(controller, Stage.MODULE_1_RUNNING)
    evidence.failure = RuntimeError("operator module failed")

    with pytest.raises(RuntimeError, match="operator module failed"):
        controller.execute_current_module(1.0)

    assert controller.session.closed
    assert controller.session.result["completion_status"] == "ABORTED"
    assert [viewer.close_count for viewer in evidence.viewers] == [1, 1]
    assert [vehicle.destroy_count for vehicle in evidence.vehicles] == [1, 1]
    assert controller.session.services.closed


def test_p5_runner_error_preserves_technical_stage_evidence(tmp_path: Path) -> None:
    clock = StrictClock()
    controller, evidence = register_dev(
        tmp_path,
        clock,
        module_exit=exit_template(ExitContext.MODULE),
    )
    advance_to(controller, Stage.MODULE_1_RUNNING)
    evidence.failure = RuntimeError("operator P5 module failed")

    with pytest.raises(RuntimeError, match="operator P5 module failed"):
        controller.execute_current_module()

    module_runs = tuple(
        run
        for run in controller.snapshot().module_runs
        if run.stage is Stage.MODULE_1_RUNNING
    )
    exit_result = controller._operator_components().exit_slots[Stage.MODULE_1_RUNNING]
    assert module_runs == ()
    assert exit_result.classification.value == "TECHNICAL_ERROR"
    assert exit_result.t0_host_monotonic_ns is None
    assert exit_result.technical_error == ("RuntimeError:operator P5 module failed")


def test_primary_runner_error_survives_cleanup_failure(tmp_path: Path) -> None:
    controller, evidence = register_dev(
        tmp_path,
        StrictClock(),
        module_exit=exit_template(ExitContext.MODULE),
    )
    advance_to(controller, Stage.MODULE_1_RUNNING)
    primary = RuntimeError("primary run failure")
    evidence.failure = primary
    evidence.cleanup_failure = RuntimeError("viewer cleanup failure")

    with pytest.raises(RuntimeError) as caught:
        controller.execute_current_module()

    exit_result = controller._operator_components().exit_slots[Stage.MODULE_1_RUNNING]
    assert caught.value is primary
    assert exit_result.technical_error == "RuntimeError:primary run failure"
    assert exit_result.secondary_errors == ("RuntimeError:viewer cleanup failure",)
    assert primary.__notes__ == [
        "Secondary evidence/cleanup failure: RuntimeError:viewer cleanup failure"
    ]


def test_keyboard_interrupt_survives_cleanup_failure(tmp_path: Path) -> None:
    controller, evidence = register_dev(
        tmp_path,
        StrictClock(),
        module_exit=exit_template(ExitContext.MODULE),
    )
    advance_to(controller, Stage.MODULE_1_RUNNING)
    primary = KeyboardInterrupt("operator interrupt")
    evidence.failure = primary
    evidence.cleanup_failure = RuntimeError("viewer cleanup failure")

    with pytest.raises(KeyboardInterrupt) as caught:
        controller.execute_current_module()

    exit_result = controller._operator_components().exit_slots[Stage.MODULE_1_RUNNING]
    assert caught.value is primary
    assert exit_result.technical_error == "KeyboardInterrupt:operator interrupt"
    assert exit_result.secondary_errors == ("RuntimeError:viewer cleanup failure",)


def test_participant_is_blocked_before_runner_construction(tmp_path: Path) -> None:
    builds: list[str] = []

    def forbidden_builder(config, borrowed_log):
        builds.append(config.run_id or "")
        raise AssertionError("hardware path must not be reached")

    controller = OperatorController(
        tmp_path,
        mode=Mode.PARTICIPANT,
        runtime=OperatorRuntimeConfig(
            spawn_index=0,
            control_config=replace(
                make_live_control_config(),
                longitudinal=replace(
                    make_live_control_config().longitudinal,
                    target_speed_kmh=None,
                ),
            ),
            speed_policy=None,
            interaction_policy=AutomationInteractionConfig(0.2, 0.1, 0.05, True, True),
            runner_builder=forbidden_builder,
        ),
        readiness=Readiness(),
    )

    with pytest.raises(P8Error, match="PARTICIPANT_BLOCKED"):
        controller.register_id(
            RegistrationRequest(
                participant_id="R-PARTICIPANT",
                run_id="participant-run",
                assignment_file=tmp_path / "assignment.csv",
            )
        )

    assert builds == []


def test_m2_manual_successful_activation_is_protocol_deviation_at_transition_time(
    tmp_path: Path,
) -> None:
    clock = StrictClock()
    controller, evidence = register_dev(tmp_path, clock)
    advance_to(controller, Stage.MODULE_1_RUNNING)
    controller.execute_current_module(1.0)
    controller.approve_dev_outcome(DevOutcome.PARTIAL, "bounded")
    controller.advance()
    controller.advance()
    clock.advance(120.0)
    controller.advance()
    controller.advance()
    evidence.driver_inputs.append(DriverInput(activation_requested=True))

    result = controller.execute_current_module(1.0)

    with controller.session.services.logger.event_path.open(newline="") as source:
        rows = list(csv.DictReader(source))
    active = next(
        row
        for row in rows
        if row["event_type"] == "auto_state"
        and row["event_value"].endswith("NOA_ACTIVE")
    )
    deviation = next(row for row in rows if row["event_type"] == "protocol_deviation")
    assert deviation["host_monotonic_ns"] == active["host_monotonic_ns"]
    assert result.activation_count == 1
    assert result.observed_on_proportion is not None
    assert result.nominal_on_proportion is None


def test_explicit_partial_and_blocked_outcomes_return_done_claim(
    tmp_path: Path,
) -> None:
    clock = StrictClock()
    controller, _ = register_dev(tmp_path, clock)
    advance_to(controller, Stage.MODULE_1_RUNNING)
    controller.execute_current_module(1.0)
    controller.approve_dev_outcome(DevOutcome.PARTIAL, "M1 bounded")
    controller.advance()
    controller.advance()
    clock.advance(120.0)
    controller.advance()
    controller.advance()
    controller.execute_current_module(1.0)
    controller.approve_dev_outcome(DevOutcome.PARTIAL, "M2 bounded")
    controller.advance()
    controller.approve_dev_outcome(DevOutcome.BLOCKED, "hazard moving route blocked")
    controller.advance()
    controller.record_actual(Receipt("survey", "NOT_IMPLEMENTED", None))
    controller.record_actual(Receipt("debrief", "EXTERNAL_OPERATOR_STEP", True))
    controller.record_actual(Receipt("data_use", "REFUSED", False))

    claim = controller.advance()

    assert claim.completion_status == "DEV_REHEARSAL_PARTIAL_COMPLETE"
    assert claim.participant_ready is False
    assert claim.cleanup_completed is True
    assert controller.snapshot().analysis_eligible is False
    assert controller.snapshot().deletion_status == "PENDING_APPROVED_PROCEDURE"
