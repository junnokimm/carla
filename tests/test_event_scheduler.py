import pytest

from src.experiment.scheduler import EventScheduler, ScheduledEvent


def test_event_scheduler_returns_requested_number_of_events():
    schedule = EventScheduler(3, 1.0, 2.0, seed=42).schedule()

    assert len(schedule) == 3
    assert [event.event_id for event in schedule] == [1, 2, 3]
    assert all(isinstance(event, ScheduledEvent) for event in schedule)


def test_event_scheduler_returns_monotonic_trigger_times():
    schedule = EventScheduler(5, 1.0, 2.0, seed=42).schedule()

    assert all(
        schedule[index].trigger_time > schedule[index - 1].trigger_time
        for index in range(1, len(schedule))
    )


def test_event_scheduler_is_deterministic_for_same_seed():
    first = EventScheduler(3, 1.0, 2.0, seed=42).schedule()
    second = EventScheduler(3, 1.0, 2.0, seed=42).schedule()

    assert first == second


def test_event_scheduler_changes_schedule_for_different_seed():
    first = EventScheduler(3, 1.0, 2.0, seed=42).schedule()
    second = EventScheduler(3, 1.0, 2.0, seed=43).schedule()

    assert first != second


def test_event_scheduler_keeps_intervals_within_bounds():
    schedule = EventScheduler(5, 1.0, 2.0, seed=42).schedule()
    trigger_times = [event.trigger_time for event in schedule]

    assert all(1.0 <= interval <= 2.0 for interval in trigger_times[:1])
    assert all(
        1.0 <= trigger_times[index] - trigger_times[index - 1] <= 2.0
        for index in range(1, len(trigger_times))
    )


@pytest.mark.parametrize(
    ("event_count", "min_interval", "max_interval"),
    [(-1, 1.0, 2.0), (1, -1.0, 2.0), (1, 2.0, 1.0)],
)
def test_event_scheduler_rejects_invalid_inputs(
    event_count, min_interval, max_interval
):
    with pytest.raises(ValueError):
        EventScheduler(event_count, min_interval, max_interval, seed=42)
