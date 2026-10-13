from src.p6.validation.segments import SegmentSequence


def test_sequence_has_prepare_start_end_edges_and_half_open_windows() -> None:
    sequence = SegmentSequence("test-only", 1_000_000_000)
    sequence.prepare(1_000_000_000)

    for index in range(5):
        start = (2 + index * 4) * 1_000_000_000
        sequence.start(start)
        sequence.start(start + 10)
        assert sequence.segment_at(start - 1) is None
        assert sequence.segment_at(start).index == index
        assert sequence.segment_at(start + 2_000_000_000) is None
        sequence.advance(start + 2_000_000_000)

    assert sequence.phase == "DONE"
    assert [window.target for window in sequence.windows] == [
        "FORWARD", "REAR_MIRROR", "LEFT_MIRROR", "RIGHT_MIRROR", "MISSING"
    ]
    assert [event.event for event in sequence.events] == ["PREPARE", "START", "END"] * 5


def test_abort_closes_partial_segment_without_relabeling_late_samples() -> None:
    sequence = SegmentSequence("test-only", 0)
    sequence.prepare(0)
    sequence.start(100)

    sequence.abort(1000, "ESC")

    assert sequence.phase == "ABORTED"
    assert sequence.segment_at(999).target == "FORWARD"
    assert sequence.segment_at(1000) is None
    assert not sequence.windows[0].complete
    assert sequence.events[-1].event == "ABORT"
    assert sequence.events[-1].reason == "ESC"


def test_abort_before_any_segment_retains_prepare_and_abort() -> None:
    sequence = SegmentSequence("test-only", 0)
    sequence.prepare(0)

    sequence.abort(10, "NO_DEVICE")

    assert [event.event for event in sequence.events] == ["PREPARE", "ABORT"]
    assert sequence.windows == []
