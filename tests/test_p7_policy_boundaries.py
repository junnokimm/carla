from dataclasses import replace

import pytest

from src.experiment.automation_interaction import DriverInput
from src.p7.metrics import ResponseMetrics, calculate_ttc
from src.p7.policy import OutcomeDecision, TriggerAction, TriggerDecision
from src.p7.types import HazardOutcome, HazardState, P7Error
from tests.test_p7_hazard import observation, scenario


class FixturePolicy:
    """All numbers here are virtual test conditions, not research defaults."""

    def __init__(self, outcome=None):
        self.result = outcome
        self.trigger_calls = 0

    def trigger(self, observation, *, elapsed_since_arm_s):
        self.trigger_calls += 1
        if elapsed_since_arm_s > 10 or observation.ego_station_m > 35:
            return TriggerDecision(TriggerAction.BLOCK, "TEST_WINDOW_MISSED")
        ready = elapsed_since_arm_s >= 3 and observation.ego_station_m >= 25
        return TriggerDecision(
            TriggerAction.FIRE if ready else TriggerAction.WAIT,
            "TEST_TIME_AND_POSITION",
        )

    def outcome(
        self, observation, *, elapsed_since_trigger_s, elapsed_since_geometric_reveal_s
    ):
        if (
            elapsed_since_geometric_reveal_s is not None
            and elapsed_since_geometric_reveal_s >= 2
        ):
            return self.result
        return None


def test_injected_time_position_rule_does_not_use_legacy_station_or_repeat_command(
    tmp_path,
):
    hazard, actors, events = scenario(tmp_path)
    hazard.policy = FixturePolicy()
    hazard.arm(observation())
    hazard.update(observation(1, 26))  # position only
    hazard.update(observation(3, 24))  # time only; legacy station 22 already passed
    assert actors.starts == 0
    hazard.update(observation(4, 26))
    hazard.update(observation(5, 26))
    assert actors.starts == 1
    assert hazard.policy.trigger_calls == 3
    trigger = next(e for e in events if e.kind.value == "hazard_trigger")
    assert trigger.payload["reason"] == "TEST_TIME_AND_POSITION"
    assert hazard.metrics.reveal_t is None  # command success != movement/exposure


def test_missed_rule_aborts_without_automatic_relocation_or_retrial(tmp_path):
    hazard, actors, events = scenario(tmp_path)
    hazard.policy = FixturePolicy()
    hazard.arm(observation())
    hazard.update(observation(11, 26))
    hazard.update(observation(12, 26))
    assert hazard.state is HazardState.ABORTED
    assert hazard.outcome is HazardOutcome.UNOBSERVABLE
    assert actors.starts == 0 and hazard.reservation_claimed
    assert events[-1].payload["reason"] == "TEST_WINDOW_MISSED"


@pytest.mark.parametrize(
    "outcome",
    [
        HazardOutcome.AVOIDED,
        HazardOutcome.STOPPED,
        HazardOutcome.INCOMPLETE,
        HazardOutcome.UNOBSERVABLE,
    ],
)
def test_only_injected_decisions_classify_non_collision_outcomes(tmp_path, outcome):
    hazard, _, events = scenario(tmp_path)
    hazard.policy = FixturePolicy(OutcomeDecision(outcome, "TEST_OBSERVATION_RULE"))
    hazard.arm(observation())
    hazard.update(observation(4, 26))
    hazard.update(observation(5, 27, reveal_clear=True, lead_lateral_m=1))
    hazard.update(observation(7, 28, reveal_clear=True, lead_lateral_m=1))
    assert hazard.outcome is outcome
    assert hazard.state is (
        HazardState.ABORTED
        if outcome is HazardOutcome.UNOBSERVABLE
        else HazardState.COMPLETED
    )
    assert events[-1].payload["hazard_visible_t"] is None


def test_injected_policy_does_not_inherit_dev_five_second_end(tmp_path):
    hazard, _, _ = scenario(tmp_path)
    hazard.policy = FixturePolicy()
    hazard.arm(observation())
    hazard.update(observation(4, 26))
    hazard.update(
        observation(5, 27, reveal_clear=True, lead_complete=True, lead_lateral_m=3.5)
    )
    hazard.update(
        observation(400, 28, reveal_clear=True, lead_complete=True, lead_lateral_m=3.5)
    )
    assert hazard.state is HazardState.HAZARD_REVEALED
    assert hazard.outcome is None


@pytest.mark.parametrize(
    "value",
    [
        HazardOutcome.AUTOMATIC_RESCUE,
        HazardOutcome.COLLISION,
        HazardOutcome.ROAD_DEPARTURE,
    ],
)
def test_policy_cannot_invent_physical_safety_outcomes(value):
    with pytest.raises(P7Error):
        OutcomeDecision(value, "not physical evidence")


def test_input_on_reveal_frame_is_not_retroactively_a_post_reveal_response():
    metrics = ResponseMetrics(brake_candidate=0.1, steer_candidate=0.05)
    metrics.observe_input(1, DriverInput())
    metrics.reveal_t = 2
    assert metrics.observe_input(2, DriverInput(brake=0.5, steering=0.2)) == ()
    metrics.observe_input(3, DriverInput(brake=0.5, steering=0.2))
    assert metrics.first_raw_response_t is None
    metrics.observe_input(4, DriverInput())
    metrics.observe_geometry(10, 5, True)
    metrics.observe_input(5, DriverInput(brake=0.2))
    assert metrics.first_brake_t == 5 and metrics.ttc_at_response.seconds == 2


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0, -1])
def test_invalid_input_time_breaks_baseline_instead_of_creating_response(bad):
    metrics = ResponseMetrics(brake_candidate=0.1, steer_candidate=0.05)
    metrics.reveal_t = 0
    metrics.observe_input(0, DriverInput())
    metrics.observe_input(bad, DriverInput(brake=0.2))
    metrics.observe_input(2, DriverInput(brake=0.2))
    assert metrics.first_raw_response_t is None


def test_ttc_nonfinite_result_is_unknown_not_infinite_normal_measurement():
    assert calculate_ttc(1e308, 1e-308, True).reason == "NONFINITE_RESULT"


def test_p5_conflict_blocks_before_occurrence_or_actor_setup(tmp_path):
    hazard, actors, _ = scenario(tmp_path)
    with pytest.raises(P7Error, match="P5_CONFLICT"):
        hazard.arm(replace(observation(), p5_active=True))
    assert actors.prepares == 0 and not hazard.reservation_claimed


@pytest.mark.parametrize("failure", ["type", "alive"])
def test_real_actor_adapter_checks_spawn_identity_and_retains_partial_cleanup(failure):
    from tests.p7_carla_fakes import World
    from tests.test_p7_carla_boundary import backend, stamp

    world = World()
    spawn = world.try_spawn_actor

    def invalid_actor(blueprint, transform):
        actor = spawn(blueprint, transform)
        if failure == "type":
            actor.type_id = "static.prop.wrong"
        else:
            actor.is_alive = False
        return actor

    world.try_spawn_actor = invalid_actor
    actors, ego, owned, _ = backend(world)
    with pytest.raises(P7Error, match="SPAWN_IDENTITY_UNVERIFIED"):
        actors.prepare(stamp())
    assert len(owned.actors) == 1
    assert owned.close() == () and ego.mutations == []


def test_successful_motion_command_is_not_observed_lateral_completion(monkeypatch):
    from src.p7.carla_observation import observe
    from tests.p7_carla_fakes import World
    from tests.test_p7_carla_boundary import backend, stamp

    actors, ego, owned, _ = backend(World())
    actors.prepare(stamp())
    actors.advance(0)
    actors.begin_reveal(stamp(1))
    monkeypatch.setattr(actors.lead, "set_transform", lambda pose: None)
    actors.advance(4)
    assert actors.complete  # commanded trajectory only
    actual = observe(actors, stamp(4), driver=DriverInput(), collision=None, foreign=())
    assert not actual.lead_complete and not actual.reveal_clear
    assert ego.mutations == [] and owned.close() == ()
