from __future__ import annotations

import json
import random

import pytest

from src.experiment.context import Module1Condition, Module2Condition
from src.p6.clock import SessionOrigin
from src.p6.tablet import TabletProtocolError, TabletSession
from src.p6.tablet_models import Circle, MediaMetadata, StimulusConfig, TabletEvent
from src.p6.tablet_writer import TabletEventSidecarWriter


def _surt_tablet(*, sink: list[TabletEvent] | None = None) -> TabletSession:
    tablet = TabletSession(
        "run",
        SessionOrigin(0, 0),
        token="secret",
        event_sink=None if sink is None else sink.append,
        rng=random.Random(7),
    )
    tablet.configure_module_one(Module1Condition.SURT)
    return tablet


def _shown_trial(tablet: TabletSession) -> tuple[str, str]:
    trial = tablet.prepare(
        StimulusConfig(
            (Circle("standard", 0.2, 0.2, 64, False), Circle("odd", 0.8, 0.8, 72, True))
        )
    )
    tablet.request_start(trial.trial_uuid)
    epoch = tablet.state().run_epoch
    tablet.acknowledge(epoch, "shown", trial.trial_uuid, "shown", 10.0, 20.0)
    return epoch, trial.trial_uuid


def test_touch_dispositions_are_retained_and_only_one_outcome_advances() -> None:
    tablet = _surt_tablet()
    trial = tablet.prepare(StimulusConfig((Circle("odd", 0.5, 0.5, 72, True),)))
    epoch = tablet.state().run_epoch

    tablet.touch(epoch, trial.trial_uuid, "early", "odd", 1.0, 2.0)
    tablet.request_start(trial.trial_uuid)
    tablet.acknowledge(epoch, "shown", trial.trial_uuid, "shown", 2.0, 2.0)
    tablet.touch(epoch, trial.trial_uuid, "accepted", "odd", 3.0, 2.0)
    next_trial = tablet.state().current_trial
    tablet.touch(epoch, trial.trial_uuid, "late", "odd", 4.0, 2.0)
    tablet.touch(epoch, trial.trial_uuid, "accepted", "odd", 3.0, 2.0)

    assert next_trial is not None
    assert next_trial.trial_uuid != trial.trial_uuid
    assert [
        event.disposition for event in tablet.events() if event.event_type == "touch"
    ] == [
        "pre_show",
        "accepted",
        "stale_trial",
        "duplicate",
    ]
    assert [
        event.correct
        for event in tablet.events()
        if event.event_type == "touch" and event.disposition == "accepted"
    ] == [True]


def test_pause_blocks_touch_and_resume_restores_surt_stage() -> None:
    tablet = _surt_tablet()
    epoch, trial_uuid = _shown_trial(tablet)

    tablet.pause(epoch, "pause", "developer")
    tablet.touch(epoch, trial_uuid, "paused-touch", "odd", 12.0, 20.0)
    resumed = tablet.resume(epoch)

    assert resumed.stage == "STIMULUS_SHOWN"
    assert (
        next(
            event for event in tablet.events() if event.event_id == "paused-touch"
        ).disposition
        == "paused"
    )


def test_reset_starts_new_epoch_clears_configuration_and_keeps_history() -> None:
    tablet = _surt_tablet()
    old_epoch, trial_uuid = _shown_trial(tablet)

    reset_state = tablet.reset_new_run(old_epoch)
    tablet.touch(old_epoch, trial_uuid, "stale-run", "odd", 1.0, 2.0)

    assert reset_state.run_epoch != old_epoch
    assert reset_state.current_trial is None
    assert reset_state.module is None
    assert reset_state.condition is None
    assert reset_state.media is None
    assert any(event.event_type == "shown" for event in tablet.events())
    assert tablet.events()[-1].disposition == "stale_epoch"
    assert tablet.events()[-1].run_epoch == old_epoch


def test_event_sink_receives_each_event_during_the_run() -> None:
    persisted: list[TabletEvent] = []
    tablet = _surt_tablet(sink=persisted)

    tablet.prepare()

    assert persisted
    assert persisted[-1] == tablet.events()[-1]


def test_sidecar_sink_flushes_each_event_before_session_close(tmp_path) -> None:
    path = tmp_path / "tablet.jsonl"
    writer = TabletEventSidecarWriter(path, "run", SessionOrigin(0, 0))
    writer.start()
    tablet = TabletSession(
        "run", SessionOrigin(0, 0), token="secret", event_sink=writer.capture
    )
    tablet.configure_module_one(Module1Condition.SURT)

    tablet.prepare()
    rows_during_run = [json.loads(line) for line in path.read_text().splitlines()]
    writer.close()

    assert rows_during_run[-1]["record_type"] == "tablet_event"
    assert rows_during_run[-1]["event_type"] == "prepare"


def test_one_way_sync_marks_remain_receipt_only_and_duplicate_is_idempotent() -> None:
    tablet = _surt_tablet()
    epoch = tablet.state().run_epoch

    for index in range(6):
        tablet.sync_mark(epoch, f"sync-{index}", float(index), 1_000.0)
    tablet.sync_mark(epoch, "sync-5", 999.0, 9_999.0)

    assert all(event.browser_mapping_seconds is None for event in tablet.events())
    assert all(
        event.mapping_status == "RECEIPT_ONLY_UNVERIFIED" for event in tablet.events()
    )
    assert tablet.events()[-1].disposition == "duplicate"


def test_no_surt_cannot_prepare_or_emit_stimulation() -> None:
    tablet = TabletSession("run", SessionOrigin(0, 0), token="secret")
    tablet.configure_module_one(Module1Condition.NO_SURT)

    with pytest.raises(TabletProtocolError, match="NO_SURT"):
        tablet.prepare()


def test_video_events_require_epoch_metadata_and_actual_playing(tmp_path) -> None:
    media = tmp_path / "meal.mp4"
    media.write_bytes(b"fixture")
    tablet = TabletSession("run", SessionOrigin(0, 0), token="secret")
    tablet.configure_module_two(Module2Condition.MANUAL, str(media), "v1")
    trial = tablet.prepare()
    tablet.request_start(trial.trial_uuid)
    epoch = tablet.state().run_epoch

    tablet.record_video_metadata(
        epoch,
        trial.trial_uuid,
        "metadata",
        MediaMetadata(781.0, True, False, 1.0),
    )
    state = tablet.acknowledge(
        epoch, "media_playing", trial.trial_uuid, "playing", 1.0, 2.0
    )
    tablet.record_media_ended(epoch, trial.trial_uuid, "ended", 12.0)

    assert state.stage == "MEDIA_PLAYING"
    assert tablet.state().stage == "ERROR"
    assert tablet.state().error == "MEDIA_EARLY_END"
