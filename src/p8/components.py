from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict

from src.experiment.automation_interaction_types import DriverInput
from src.experiment.context import ExperimentModule, ExperimentPhase, Module1Condition
from src.experiment.timestamp import TimestampEnvelope
from src.p7.config import HazardConfig, TrafficConfig
from src.p7.hazard import HazardScenario
from src.p7.occurrence import OccurrenceRegistry
from src.p7.traffic import SpawnCandidate, TrafficScenario
from src.p7.types import HazardObservation, HazardState, Json, P7Event
from src.p8.dev_automation import DevAutomation
from src.p8.dev_exit import DevExits
from src.p8.dev_p6 import DevP6
from src.p8.model import P8Error, Stage
from src.p8.services import SessionServices
from src.vehicle import VehicleState


class StubHazardActors:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.tokens: list[str] = []
        self.prepared = False

    def prepare(self, timestamp: TimestampEnvelope) -> None:
        self.prepared = True
        self.tokens.extend((self.run_id + ":DEV_STUB_LEAD", self.run_id + ":DEV_STUB_CARGO"))

    def begin_reveal(self, timestamp: TimestampEnvelope) -> None:
        return


class DevelopmentComponents:
    kind = "DEV_STUB"

    def __init__(self, services: SessionServices, occurrences: OccurrenceRegistry) -> None:
        self.services, self.occurrences = services, occurrences
        self.automation = DevAutomation(services)
        self.exits = DevExits(services)
        self.p6 = DevP6(services)
        self.hazard: HazardScenario | None = None
        self.hazard_port = StubHazardActors(services.context.study_run_id)
        self._hazard_start = 0.0
        self.traffic_tokens: list[str] = []
        self.traffic_plans: dict[str, list[str]] = {}
        self.post: dict[str, Json] = {"survey": "NOT_IMPLEMENTED", "debrief": "EXTERNAL_OPERATOR_STEP",
                                      "data_use": "EXTERNAL_OPERATOR_STEP", "real_survey_completed": False}
        self.training: dict[str, Json] = {}
        self.clean = False

    def enter(self, stage: Stage) -> None:
        if stage is not self.services.stage:
            raise P8Error("COMPONENT_STAGE_CONTEXT_MISMATCH")
        ctx = self.services.context
        match stage:
            case Stage.TRAINING:
                self.automation.begin(ExperimentModule.MODULE_1, Module1Condition.SURT, "TRAINING")
                for driver in (DriverInput(activation_requested=True), DriverInput(deactivation_requested=True),
                               DriverInput(activation_requested=True), DriverInput(brake=0.2),
                               DriverInput(), DriverInput(activation_requested=True)):
                    self.automation.input(driver)
                self.exits.exercise("TRAINING", ExperimentModule.MODULE_1, Module1Condition.SURT, self.automation.controller.state)
                self.p6.module_one(Module1Condition.SURT)
                self.p6.tick()
                self.training = {"hazard_rehearsed": False, "exit_opportunities": 1, "ability_assessment": "DEV_STUB"}
            case Stage.EYE_TRACKER_CALIBRATION:
                self.p6.calibrate_stub()
            case Stage.MODULE_1_PREPARE | Stage.MODULE_2_PREPARE:
                key = "MODULE_1" if stage is Stage.MODULE_1_PREPARE else "MODULE_2"
                self._traffic(key)
                self.services.emit("p8_module_binding", {"module_key": key,
                    "condition": ctx.module_1_condition.value if key == "MODULE_1" else ctx.module_2_condition.value,
                    "dependency_mode": "DEV_STUB", "hardware_validated": False})
            case Stage.MODULE_1_RUNNING:
                self.automation.begin(ExperimentModule.MODULE_1, ctx.module_1_condition, "MODULE_1")
                self.p6.module_one(ctx.module_1_condition)
            case Stage.MODULE_1_COMPLETE:
                self.automation.stop()
                self.p6.stop_tablet()
                self.traffic_tokens.clear()
            case Stage.MODULE_2_RUNNING:
                self.automation.begin(ExperimentModule.MODULE_2, ctx.module_2_condition, "MODULE_2")
                self.p6.module_two(ctx.module_2_condition)
            case Stage.FINAL_HAZARD:
                if self.hazard is not None:
                    raise P8Error("HAZARD_ALREADY_CREATED")
                self._hazard_start = self.services.elapsed
                self.hazard = HazardScenario(HazardConfig(), module=ExperimentModule.MODULE_2,
                    phase=ExperimentPhase.FINAL_HAZARD_ASSESSMENT, participant_id=ctx.participant_id,
                    run_id=ctx.study_run_id, actors=self.hazard_port, occurrences=self.occurrences,
                    event_sink=self._hazard_event, origin_ns=self.services.clock.baseline_monotonic_ns,
                    session_clock=self.services.clock)
                self.hazard.arm(self._observation(0))
            case Stage.POST_EXPERIMENT:
                self.p6.stop_tablet()
                self.automation.stop()
                self.services.emit("post_experiment_start", {"survey_ui": "NOT_IMPLEMENTED"})
            case _:
                return

    def leave(self, stage: Stage) -> None:
        if stage in (Stage.TRAINING, Stage.MODULE_1_RUNNING, Stage.MODULE_2_RUNNING):
            self.automation.finish(complete=True)
        if stage is Stage.TRAINING:
            self.automation.stop()
            self.p6.stop_tablet()
        if stage is Stage.POST_EXPERIMENT:
            self.services.emit("post_experiment_end", self.post)

    def completion_blocker(self, stage: Stage) -> str | None:
        if stage in (Stage.MODULE_1_RUNNING, Stage.MODULE_2_RUNNING):
            key = "MODULE_1" if stage is Stage.MODULE_1_RUNNING else "MODULE_2"
            if self.exits.slots.get(key) != 1:
                return "MODULE_REQUIRES_EXACTLY_ONE_EXIT_SLOT"
        if stage is Stage.FINAL_HAZARD and (
            self.hazard is None or self.hazard.state is not HazardState.COMPLETED
        ):
            return "FINAL_HAZARD_NOT_TERMINAL"
        return None

    def record_receipt(self, name: str, status: str, value: Json) -> None:
        if self.services.stage is Stage.POST_EXPERIMENT:
            self.post[name] = status

    def tick(self) -> None:
        self.p6.tick()
        if self.services.stage is Stage.FINAL_HAZARD and self.hazard is not None:
            observation = self._observation(self.services.elapsed - self._hazard_start)
            self.automation.input(observation.driver_input or DriverInput())
            self.hazard.update(observation)
            if self.hazard.termination_requested or self.hazard.state is HazardState.ABORTED:
                raise P8Error("P7_TERMINATION_OR_ABORT")
        driver = self.automation.last_input
        self.services.logger.write_telemetry(self.services.segment(), self.services.stamp(),
            VehicleState(self.services.elapsed, 0, driver.steering, driver.throttle, driver.brake,
                         None, "DEV_STUB_NOT_VEHICLE_DYNAMICS"))

    def exit_opportunity(self) -> None:
        stage, ctx = self.services.stage, self.services.context
        if stage not in (Stage.MODULE_1_RUNNING, Stage.MODULE_2_RUNNING):
            raise P8Error("EXIT_OUTSIDE_MODULE")
        module = ExperimentModule.MODULE_1 if stage is Stage.MODULE_1_RUNNING else ExperimentModule.MODULE_2
        condition = ctx.module_1_condition if module is ExperimentModule.MODULE_1 else ctx.module_2_condition
        self.exits.exercise(module.value, module, condition, self.automation.controller.state)

    def flush(self) -> None:
        errors = []
        for action in (self.exits.finish, lambda: self.automation.finish(complete=False)):
            try:
                action()
            except (RuntimeError, ValueError, OSError) as error:
                errors.append(str(error))
        try:
            if self.hazard is not None and self.hazard.state not in (HazardState.COMPLETED, HazardState.ABORTED):
                self.hazard.abort(self._observation(self.services.elapsed - self._hazard_start), "P8_SESSION_ABORT")
        except (RuntimeError, ValueError, OSError) as error:
            errors.append(str(error))
        try:
            self.p6.flush()
        except (RuntimeError, ValueError, OSError) as error:
            errors.append(str(error))
        if errors:
            raise P8Error("COMPONENT_FLUSH_FAILED:" + ";".join(errors))

    def cleanup(self) -> None:
        try:
            self.automation.stop()
        finally:
            self.traffic_tokens.clear()
            self.hazard_port.tokens.clear()
            self.clean = True

    def snapshot(self) -> dict[str, Json]:
        state = self.p6.tablet.state()
        return {"provenance": "DEV_STUB", "hardware_validation": "NOT_HARDWARE_VALIDATED",
                "modules": deepcopy(self.automation.modules), "current_noa_mode": self.automation.controller.state.control_mode.value,
                "exit_slots": dict(self.exits.slots), "training": dict(self.training),
                "calibration_status": self.p6.calibration_status, "gaze_samples": self.p6.sequence,
                "p6_source_mode": "SYNTHETIC_EXPLICIT_DEV_ONLY", "clock_sync_ready": False,
                "tablet_state": state.stage, "tablet_epoch": state.run_epoch,
                "tablet_trial": None if state.current_trial is None else state.current_trial.trial_uuid,
                "tablet_event_count": len(self.p6.tablet.events()), "tablet_aoi_status": "NOT_TESTED",
                "video": {"path": str(self.p6.media_path.name), "real_video_validated": False},
                "traffic_plans": deepcopy(self.traffic_plans),
                "hazard_count": int(self.hazard_port.prepared),
                "hazard_state": "IDLE" if self.hazard is None else self.hazard.state.value,
                "hazard_metrics": None if self.hazard is None else self.hazard.metrics.snapshot(),
                "p7_live_validated": False, "post": dict(self.post),
                "real_owned_actor_ids": [], "owned_resource_tokens": [*self.traffic_tokens, *self.hazard_port.tokens],
                "cleanup_completed": self.clean}

    def _traffic(self, key: str) -> None:
        chosen: list[str] = []
        scenario = TrafficScenario(TrafficConfig(), spawn=lambda candidate: chosen.append(candidate.key))
        scenario.start(tuple(SpawnCandidate(str(i), -200 + i * 20, 40) for i in range(8)), ego=(0, 0), protected=())
        self.traffic_plans[key] = chosen
        self.traffic_tokens = [f"{self.services.context.study_run_id}:{key}:DEV_STUB_TRAFFIC:{item}" for item in chosen]
        self.services.emit("traffic_plan", {"module_key": key, "config": asdict(scenario.config), "selected": chosen})

    def _observation(self, elapsed: float) -> HazardObservation:
        return HazardObservation(self.services.stamp(), 0 if elapsed < 1 else 25, 12,
                                  0 if elapsed < 2 else 3.5, -4, elapsed >= 2, elapsed >= 2,
                                  30, 0, 0, True, DriverInput(brake=0.2 if elapsed >= 3 else 0),
                                  input_source="DEV_STUB_P8_SCRIPTED_INPUT")

    def _hazard_event(self, event: P7Event) -> None:
        self.services.emit(event.kind.value, event.payload, timestamp=event.timestamp)
