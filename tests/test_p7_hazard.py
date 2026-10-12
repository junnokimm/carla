from dataclasses import replace

import pytest

from src.experiment.automation_interaction_types import DriverInput
from src.experiment.context import ExperimentModule, ExperimentPhase
from src.experiment.timestamp import HostClockTimestamp, TimestampEnvelope
from src.p7.config import HazardConfig
from src.p7.hazard import HazardScenario
from src.p7.occurrence import OccurrenceRegistry
from src.p7.types import HazardObservation, HazardState, P7Error


class Actors:
    def __init__(self, fail=False):
        self.prepares = 0
        self.starts = 0
        self.fail = fail

    def prepare(self, timestamp):
        self.prepares += 1
        if self.fail:
            raise P7Error("SPAWN_FAILED")

    def begin_reveal(self, timestamp):
        self.starts += 1


def observation(seconds=0.0, station=0.0, **changes):
    base = HazardObservation(
        TimestampEnvelope(HostClockTimestamp(int(seconds * 1e9), int(seconds * 1e9))),
        ego_station_m=station, lead_station_m=12, lead_lateral_m=0,
        target_lane_id=-4, lead_complete=False, reveal_clear=False,
        distance_m=30, closing_speed_mps=10, ego_speed_mps=10,
        collision_course=True, driver_input=DriverInput(),
    )
    return replace(base, **changes)


def scenario(tmp_path, *, module=ExperimentModule.MODULE_2, actors=None, participant="DEV-1"):
    events = []
    actor_port = actors or Actors()
    hazard = HazardScenario(HazardConfig(), module=module,
                            phase=ExperimentPhase.FINAL_HAZARD_ASSESSMENT if module is ExperimentModule.MODULE_2 else ExperimentPhase.MODULE_1,
                            participant_id=participant, run_id="run-1", actors=actor_port,
                            occurrences=OccurrenceRegistry(tmp_path / "occurrences"),
                            event_sink=events.append, origin_ns=0)
    return hazard, actor_port, events


def test_module_one_never_prepares_or_triggers_hazard(tmp_path):
    hazard, actors, events = scenario(tmp_path, module=ExperimentModule.MODULE_1)

    assert not hazard.arm(observation())
    hazard.update(observation(1000, 1000))

    assert actors.prepares == actors.starts == 0
    assert events == []
    assert hazard.state is HazardState.IDLE
    assert not (tmp_path / "occurrences").exists()


def test_route_trigger_progression_once_and_no_gaze_dependency(tmp_path):
    hazard, actors, events = scenario(tmp_path)
    assert hazard.arm(observation())
    hazard.update(observation(1, 21))
    assert actors.starts == 0

    hazard.update(observation(2, 22))
    hazard.update(observation(2, 22))
    hazard.update(observation(3, 23, lead_lateral_m=1, reveal_clear=True))
    hazard.update(observation(4, 24, lead_lateral_m=3.5, lead_complete=True,
                              reveal_clear=True, driver_input=DriverInput(brake=0.3)))
    hazard.update(observation(9, 25, lead_lateral_m=3.5, lead_complete=True, reveal_clear=True))

    assert actors.starts == 1
    assert hazard.state is HazardState.COMPLETED
    names = [event.kind.value for event in events]
    assert names.count("hazard_trigger") == 1
    assert "lead_lateral_onset" in names and "hazard_revealed" in names
    assert "hazard_response" in names
    assert not {"tor", "collision_warning", "automatic_brake", "hazard_warning"}.intersection(names)
    assert hazard.metrics.first_brake_t == 4
    assert hazard.metrics.raw_response_latency_s == 1
    assert hazard.metrics.hazard_visible_t is None
    with pytest.raises(P7Error, match="ALREADY"):
        hazard.arm(observation(10))


def test_occurrence_is_persistent_across_instances_and_runs(tmp_path):
    hazard, _, _ = scenario(tmp_path)
    hazard.arm(observation())
    other, actors, events = scenario(tmp_path)
    other.run_id = "run-2"

    with pytest.raises(P7Error, match="ALREADY"):
        other.arm(observation())

    assert actors.prepares == 0
    assert other.state is HazardState.ABORTED
    assert events[-1].kind.value == "hazard_abort"


@pytest.mark.parametrize("change,reason", [
    ({"actors_alive": False}, "ACTOR_LOST"),
    ({"obstacle_stationary": False}, "OBSTACLE_MOVED"),
    ({"route_valid": False}, "ROUTE_INVALID"),
    ({"lead_path_clear": False}, "LEAD_PATH_BLOCKED"),
    ({"p5_active": True}, "P5_CONFLICT"),
])
def test_actor_route_and_p5_failures_abort_without_retrigger(tmp_path, change, reason):
    hazard, actors, events = scenario(tmp_path)
    hazard.arm(observation())

    hazard.update(observation(1, 22, **change))
    hazard.update(observation(2, 23))

    assert hazard.state is HazardState.ABORTED
    assert actors.starts == 0
    assert events[-1].payload["reason"] == reason


def test_spawn_failure_consumes_claim_and_fails_closed(tmp_path):
    hazard, actors, events = scenario(tmp_path, actors=Actors(fail=True))

    with pytest.raises(P7Error, match="SPAWN_FAILED"):
        hazard.arm(observation())

    assert hazard.state is HazardState.ABORTED
    assert events[-1].kind.value == "hazard_abort"
    assert actors.prepares == 1


def test_collision_emits_termination_request_not_ego_control(tmp_path):
    hazard, _, events = scenario(tmp_path)
    hazard.arm(observation())

    hazard.update(observation(1, 1, collision_actor_id=9))

    assert hazard.state is HazardState.ABORTED
    assert hazard.termination_requested
    assert {event.kind.value for event in events} >= {"collision", "experiment_termination_requested"}


@pytest.mark.parametrize("change", [{"reveal_clear": True}, {"lead_lateral_m": 0.5}])
def test_pretrigger_reveal_or_lateral_motion_is_rejected(tmp_path, change):
    hazard, actors, events = scenario(tmp_path)
    hazard.arm(observation())

    hazard.update(observation(1, 1, **change))

    assert hazard.state is HazardState.ABORTED
    assert actors.starts == 0
    assert events[-1].payload["reason"] == "PRETRIGGER_LEAD_OR_REVEAL_INVALID"


def test_module_two_attention_phase_cannot_arm_final_hazard(tmp_path):
    hazard, actors, _ = scenario(tmp_path)
    hazard.phase = ExperimentPhase.MODULE_2

    assert hazard.arm(observation()) is False
    assert actors.prepares == 0
    assert not (tmp_path / "occurrences").exists()
