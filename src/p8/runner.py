from src.experiment.automation_interaction_types import DriverInput
from src.p7.types import HazardState, Json
from src.p8.model import RECEIPTS, SEQUENCE, P8Error, Stage
from src.p8.orchestrator import ExperimentSession


def run_development(session: ExperimentSession, *, abort_at: Stage | None = None) -> dict[str, Json]:
    components = session.components
    if components is None:
        raise P8Error("DEV_COMPONENTS_MISSING")
    try:
        for stage in SEQUENCE:
            if session.state is not stage:
                session.transition(stage)
            if session.closed:
                break
            duration = session.services.profile.minimum(stage)
            if abort_at is stage:
                session.services.timeline.advance(max(0.5, duration / 2))
                components.tick()
                session.abort("DEV_ABORT_INJECTION:" + stage.value)
                break
            if stage in (Stage.MODULE_1_RUNNING, Stage.MODULE_2_RUNNING):
                for step in range(4):
                    session.services.timeline.advance(duration / 4)
                    if stage is Stage.MODULE_1_RUNNING and step == 0:
                        components.automation.input(DriverInput(activation_requested=True))
                    if step == 1:
                        components.exit_opportunity()
                    if stage is Stage.MODULE_1_RUNNING and step == 2:
                        components.automation.input(DriverInput(deactivation_requested=True))
                    components.tick()
            elif stage is Stage.REST:
                session.services.timeline.advance(duration)
                components.tick()
            elif stage is Stage.FINAL_HAZARD:
                for _ in range(10):
                    session.services.timeline.advance(1)
                    components.tick()
                    if components.hazard is not None and components.hazard.state is HazardState.COMPLETED:
                        break
            else:
                session.services.timeline.advance(1)
                components.tick()
            for item in RECEIPTS.get(stage, ()):
                session.acknowledge(item, "DEV_STUB")
        return session.result
    except (RuntimeError, ValueError, OSError) as error:
        session.abort(str(error))
        raise
    except KeyboardInterrupt:
        session.abort("OPERATOR_INTERRUPT")
        raise
