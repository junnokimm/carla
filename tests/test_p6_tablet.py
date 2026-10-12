from __future__ import annotations

import json
from http.client import HTTPConnection
from pathlib import Path

import pytest

from src.experiment.context import Module1Condition, Module2Condition
from src.p6.clock import SessionOrigin
from src.p6.server import TabletHttpServer
from src.p6.tablet import TabletProtocolError, TabletSession
from src.p6.tablet_models import Circle, MediaMetadata, StimulusConfig


def test_assignment_matrix_selects_blank_surt_or_same_video() -> None:
    tablet = TabletSession("run", SessionOrigin(0, 0), token="secret")

    assert tablet.configure_module_one(Module1Condition.NO_SURT).stage == "BLANK"
    assert tablet.configure_module_one(Module1Condition.SURT).stage == "SURT_READY"
    manual = tablet.configure_module_two(Module2Condition.MANUAL, "meal.mp4", "v1")
    noa = tablet.configure_module_two(Module2Condition.NOA_L2, "meal.mp4", "v1")
    assert manual.media == noa.media


def test_video_requires_browser_metadata_before_stimulus_shown(tmp_path: Path) -> None:
    media = tmp_path / "meal.mp4"
    media.write_bytes(b"fixture")
    tablet = TabletSession("run", SessionOrigin(0, 0), token="secret")
    tablet.configure_module_two(Module2Condition.MANUAL, str(media), "v1")
    trial = tablet.prepare()
    tablet.request_start(trial.trial_uuid)
    epoch = tablet.state().run_epoch

    with pytest.raises(TabletProtocolError, match="metadata"):
        tablet.acknowledge(epoch, "media_playing", trial.trial_uuid, "event-1", 10, 20)

    tablet.record_video_metadata(
        epoch,
        trial.trial_uuid,
        "meta-1",
        MediaMetadata(duration_seconds=781, muted=True, loop=False, playback_rate=1.0),
    )
    state = tablet.acknowledge(
        epoch, "media_playing", trial.trial_uuid, "event-2", 11, 20
    )
    assert state.stage == "MEDIA_PLAYING"
    assert state.media is not None
    assert state.media.sha256 is not None


def test_missing_video_cannot_prepare_or_start() -> None:
    tablet = TabletSession("run", SessionOrigin(0, 0), token="secret")
    state = tablet.configure_module_two(Module2Condition.MANUAL, "missing.mp4", "v1")

    with pytest.raises(TabletProtocolError, match="MEDIA_MISSING"):
        tablet.prepare()

    assert state.stage == "MEDIA_MISSING"


def test_duplicate_stale_pause_reset_and_unfinished_end_are_explicit() -> None:
    tablet = TabletSession("run", SessionOrigin(0, 0), token="secret")
    tablet.configure_module_one(Module1Condition.SURT)
    trial = tablet.prepare(
        StimulusConfig(
            (Circle("a", 0.2, 0.2, 20, False), Circle("b", 0.8, 0.8, 30, True))
        )
    )
    tablet.request_start(trial.trial_uuid)
    epoch = tablet.state().run_epoch
    tablet.acknowledge(epoch, "shown", trial.trial_uuid, "same", 1, 2)
    tablet.acknowledge(epoch, "shown", trial.trial_uuid, "same", 1, 2)
    assert tablet.events()[-1].disposition == "duplicate"
    tablet.pause(epoch, "pause", "participant")
    assert tablet.state().stage == "PAUSED"
    tablet.resume(epoch)
    tablet.reset_new_run(epoch)
    assert tablet.state().current_trial is None


def test_http_server_exposes_state_accepts_events_and_serves_ranges(
    tmp_path: Path,
) -> None:
    static = tmp_path / "static"
    static.mkdir()
    (static / "app.js").write_text("ok")
    media = tmp_path / "video.mp4"
    media.write_bytes(b"0123456789")
    tablet = TabletSession("run", SessionOrigin(0, 0), token="secret")
    server = TabletHttpServer(
        tablet, static_dir=static, media_file=media, bind="127.0.0.1", port=0
    )
    server.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.port, timeout=2)
        connection.request(
            "GET", "/api/state", headers={"Authorization": "Bearer secret"}
        )
        response = connection.getresponse()
        state = json.loads(response.read())
        assert response.status == 200
        assert state["run_epoch"] == tablet.state().run_epoch
        connection.request(
            "GET",
            "/media",
            headers={"Range": "bytes=2-5", "Authorization": "Bearer secret"},
        )
        partial = connection.getresponse()
        assert partial.status == 206
        assert partial.read() == b"2345"
        connection.close()
    finally:
        server.close()


def test_browser_time_mapping_stays_receipt_only_without_handshake() -> None:
    receipt_times = iter((1_000_000, 2_000_000, 3_000_000, 4_000_000, 5_000_000))
    tablet = TabletSession(
        "run", SessionOrigin(0, 0), token="secret", monotonic_ns=receipt_times.__next__
    )

    for index in range(5):
        tablet.sync_mark(
            tablet.state().run_epoch, f"sync-{index}", float(index), 1_000.0
        )

    events = tablet.events()
    assert all(event.browser_mapping_seconds is None for event in events)
    assert all(event.mapping_status == "RECEIPT_ONLY_UNVERIFIED" for event in events)


def test_nonresponse_rule_is_explicitly_development_only() -> None:
    tablet = TabletSession("run", SessionOrigin(0, 0), token="secret")
    tablet.configure_module_one(Module1Condition.SURT)
    trial = tablet.prepare(StimulusConfig((Circle("a", 0.5, 0.5, 20, True),)))

    with pytest.raises(TabletProtocolError, match="disabled"):
        tablet.record_development_nonresponse(
            tablet.state().run_epoch, trial.trial_uuid, "nonresponse"
        )
