from dataclasses import replace
from pathlib import Path

import pytest

from src.experiment.automation_interaction_types import DriverInput
from src.experiment.context import ExperimentModule, ExperimentPhase
from src.experiment.timestamp import HostClockTimestamp, TimestampEnvelope
from src.p7.config import HazardConfig
from src.p7.hazard import HazardScenario
from src.p7.metrics import canonical_hazard_record
from src.p7.occurrence import OccurrenceRegistry
from src.p7.types import (
    EventKind,
    HazardObservation,
    HazardOutcome,
    HazardState,
    P7Event,
)


class Actors:
    def prepare(self, timestamp: TimestampEnvelope) -> None:
        del timestamp

    def begin_reveal(self, timestamp: TimestampEnvelope) -> None:
        del timestamp


def observation(seconds: float = 0.0, station: float = 0.0) -> HazardObservation:
    return HazardObservation(
        TimestampEnvelope(HostClockTimestamp(int(seconds * 1e9), int(seconds * 1e9))),
        ego_station_m=station,
        lead_station_m=12.0,
        lead_lateral_m=0.0,
        target_lane_id=-4,
        lead_complete=False,
        reveal_clear=False,
        distance_m=30.0,
        closing_speed_mps=10.0,
        ego_speed_mps=10.0,
        collision_course=True,
        driver_input=DriverInput(),
    )


def scenario(directory: Path) -> tuple[HazardScenario, list[P7Event]]:
    events: list[P7Event] = []
    hazard = HazardScenario(
        HazardConfig(),
        module=ExperimentModule.MODULE_2,
        phase=ExperimentPhase.FINAL_HAZARD_ASSESSMENT,
        participant_id="DEV-1",
        run_id="run-1",
        actors=Actors(),
        occurrences=OccurrenceRegistry(directory / "occurrences"),
        event_sink=events.append,
        origin_ns=0,
    )
    return hazard, events


def test_canonical_record_keeps_unvalidated_research_fields_null() -> None:
    # Given: an explicit technical outcome with no validated research definitions.
    # When: the shared v5 record is built.
    record = canonical_hazard_record(HazardOutcome.COLLISION)

    # Then: status and outcome are explicit while research metrics remain absent.
    assert record == {
        "parameter_status": "DEV_ONLY",
        "canonical_status": "UNVALIDATED_RESEARCH_DEFINITIONS",
        "hazard_visible_t": None,
        "brake_onset_t": None,
        "steer_onset_t": None,
        "ttc_at_response": None,
        "min_ttc": None,
        "hazard_start_speed": None,
        "hazard_start_distance": None,
        "hazard_outcome": "COLLISION",
    }


def test_raw_and_candidate_response_do_not_populate_canonical_response(tmp_path: Path) -> None:
    # Given: a revealed DEV hazard with an established zero input sample.
    hazard, events = scenario(tmp_path)
    hazard.arm(observation())
    hazard.update(observation(2.0, 22.0))
    hazard.update(replace(observation(3.0, 23.0), reveal_clear=True))

    # When: raw brake input exceeds the existing DEV candidate threshold.
    hazard.update(replace(observation(4.0, 24.0), reveal_clear=True, driver_input=DriverInput(brake=0.3)))

    # Then: DEV diagnostics remain, but canonical research response fields stay null.
    response = next(event for event in events if event.kind is EventKind.RESPONSE)
    assert response.payload["first_brake_t"] == 4.0
    assert response.payload["candidate_response_t"] == 4.0
    assert response.payload["brake_onset_t"] is None
    assert response.payload["ttc_at_response"] is None
    assert response.payload["hazard_outcome"] is None


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"collision_actor_id": 9}, HazardOutcome.COLLISION),
        ({"road_departure": True}, HazardOutcome.ROAD_DEPARTURE),
        ({"actors_alive": False}, HazardOutcome.UNOBSERVABLE),
    ],
)
def test_explicit_terminal_evidence_sets_only_supported_outcome(
    tmp_path: Path,
    changes: dict[str, int | bool],
    expected: HazardOutcome,
) -> None:
    # Given: an armed hazard observation path.
    hazard, events = scenario(tmp_path)
    hazard.arm(observation())

    # When: explicit collision, departure, or technical observation evidence arrives.
    hazard.update(replace(observation(1.0, 1.0), **changes))

    # Then: the terminal record carries the corresponding supported taxonomy value.
    assert events[-1].payload["hazard_outcome"] == expected.value


def test_simultaneous_collision_and_departure_preserve_both_without_precedence(tmp_path: Path) -> None:
    # Given: an armed hazard.
    hazard, events = scenario(tmp_path)
    hazard.arm(observation())

    # When: one observation explicitly reports collision and road departure.
    hazard.update(replace(observation(1.0, 1.0), collision_actor_id=9, road_departure=True))

    # Then: both evidence flags survive and no canonical outcome is inferred.
    terminal = events[-1]
    assert terminal.payload["collision_observed"] is True
    assert terminal.payload["road_departure_observed"] is True
    assert terminal.payload["hazard_outcome"] is None
    assert {event.kind for event in events} >= {EventKind.COLLISION, EventKind.ROAD_DEPARTURE}


def test_generic_abort_has_no_inferred_outcome_and_cannot_rewrite_completion(tmp_path: Path) -> None:
    # Given: one generically aborted scenario and one technically completed scenario.
    aborted, aborted_events = scenario(tmp_path / "aborted")
    aborted.arm(observation())
    aborted.abort(observation(1.0), "SESSION_ABORT")
    completed, completed_events = scenario(tmp_path / "completed")
    completed.run_id = "run-2"
    completed.arm(observation())
    completed.update(observation(2.0, 22.0))
    completed.update(replace(observation(3.0, 23.0), reveal_clear=True, lead_complete=True))
    completed.update(replace(observation(9.0, 24.0), reveal_clear=True, lead_complete=True))

    # When: abort is requested after completion.
    completed.abort(observation(10.0), "SESSION_ABORT")

    # Then: generic abort stays unclassified and completed terminal state is immutable.
    assert aborted_events[-1].payload["hazard_outcome"] is None
    assert completed.state is HazardState.COMPLETED
    assert completed_events[-1].kind is EventKind.COMPLETE
    # A DEV duration is not an approved research observation-end criterion.
    assert completed_events[-1].payload["hazard_outcome"] is None


def test_terminal_event_retains_partial_raw_input_and_shared_elapsed_time(tmp_path: Path) -> None:
    # Given: an armed hazard using the shared session-clock origin.
    hazard, events = scenario(tmp_path)
    hazard.arm(observation())
    terminal_observation = replace(
        observation(1.25, 1.0),
        collision_actor_id=9,
        driver_input=DriverInput(brake=0.4),
        input_source="PARTIAL_DEVICE_SAMPLE",
    )

    # When: explicit collision evidence terminates the scenario.
    hazard.update(terminal_observation)

    # Then: the terminal record retains raw input provenance and shared-clock time.
    terminal = events[-1].payload
    assert terminal["driver_input"]["brake"] == 0.4
    assert terminal["driver_input"]["steering"] == 0.0
    assert terminal["input_source"] == "PARTIAL_DEVICE_SAMPLE"
    assert terminal["session_elapsed_s"] == 1.25
