import pytest

from src.experiment.automation_interaction_types import DriverInput
from src.p7.metrics import ResponseMetrics, calculate_ttc


@pytest.mark.parametrize("distance,closing,course,value,reason", [
    (20, 5, True, 4, None), (20, 0, True, None, "NOT_CLOSING"),
    (20, -1, True, None, "NOT_CLOSING"), (20, 5, False, None, "NOT_COLLISION_COURSE"),
    (-1, 5, True, None, "NONPOSITIVE_CLEARANCE"),
    (float("nan"), 5, True, None, "NONFINITE"),
    (20, 5, None, None, "COLLISION_COURSE_UNVERIFIED"),
])
def test_ttc_only_when_mathematically_defined(distance, closing, course, value, reason):
    result = calculate_ttc(distance, closing, course)

    assert result.seconds == value
    assert result.reason == reason


def test_preexisting_input_not_a_response_until_release_and_new_onset():
    metrics = ResponseMetrics(brake_candidate=0.1, steer_candidate=0.05)
    metrics.observe_input(0, DriverInput(brake=0.3, steering=0.2))
    metrics.reveal_t = 1
    metrics.observe_input(1, DriverInput(brake=0.4, steering=0.3))
    assert metrics.first_brake_t is None and metrics.first_steer_t is None

    metrics.observe_input(2, DriverInput())
    metrics.observe_input(3, DriverInput(steering=0.01))
    metrics.observe_input(4, DriverInput(brake=0.2, steering=0.08))

    assert metrics.first_steer_t == 3
    assert metrics.first_brake_t == 4
    assert metrics.raw_response_latency_s == 2
    assert metrics.candidate_response_t == 4
    assert metrics.first_meaningful_driver_response_t is None


def test_unknown_input_is_not_treated_as_driver_response():
    metrics = ResponseMetrics(brake_candidate=0.1, steer_candidate=0.05)
    metrics.reveal_t = 1
    metrics.observe_input(2, None)
    metrics.observe_input(3, DriverInput(brake=1))

    assert metrics.first_brake_t is None
    assert metrics.raw_response_latency_s is None
