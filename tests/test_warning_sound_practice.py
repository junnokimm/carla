from types import SimpleNamespace

import pygame
import pytest

from src.p8.model import Stage
from src.p8.operator_ui import OperatorApp
from src.p8.warning_sound_practice import WarningSoundPractice
from src.scenario.hazard_warning_audio import CUE_ID, DURATION_SECONDS
from tests.test_p8_operator_ui import FakeController, config
from tests.vehicle_audio_fakes import FakeMixer


def event(key, kind=pygame.KEYDOWN, **kwargs):
    return pygame.event.Event(kind, key=key, **kwargs)


def start_practice(monkeypatch, fault=None):
    mixer = FakeMixer(fail=fault)
    monkeypatch.setattr(pygame, "mixer", mixer)
    controller = FakeController()
    controller.runtime = SimpleNamespace(m2_hazard_v1=object())
    events = []
    def emit(name, payload):
        if fault == "logging":
            raise OSError("simulated log write error")
        events.append((name, payload))
    controller.session = SimpleNamespace(services=SimpleNamespace(emit=emit))
    app = OperatorApp(controller, config())
    app.start()
    app.warning_practice.clock = lambda: mixer.now
    return app, controller, mixer, events


def present(app):
    app.render(pygame.Surface((1280, 720)))
    app.after_display_flip()


def test_practice_requires_visible_instruction_and_finishing_cue_before_drive(monkeypatch):
    app, controller, mixer, events = start_practice(monkeypatch)
    assert controller.stage is Stage.TRAINING and controller.training_durations == []
    app.handle_events([event(pygame.K_SPACE), event(pygame.K_SPACE, pygame.KEYUP)])
    app.after_display_flip()
    assert app.warning_practice.audio.attempts == 0
    present(app)
    present(app)
    assert app.warning_practice.audio.attempts == 1
    app.handle_events([event(pygame.K_SPACE), event(pygame.K_SPACE, pygame.KEYUP)])
    assert controller.training_durations == []
    mixer.now += DURATION_SECONDS + .01
    app.handle_events([event(pygame.K_r), event(pygame.K_r, repeat=1), event(pygame.K_r)])
    assert app.warning_practice.audio.attempts == 2
    mixer.now += DURATION_SECONDS + .01
    app.handle_events([event(pygame.K_r)])  # held key does not repeat
    assert app.warning_practice.audio.attempts == 2
    app.handle_events([event(pygame.K_SPACE), event(pygame.K_SPACE, pygame.KEYUP),
                       event(pygame.K_SPACE)])
    assert controller.training_durations == [600.0]
    assert controller.stage is Stage.EYE_TRACKER_CALIBRATION
    assert app.warning_practice is None and app.awaiting_space_release
    app.handle_events([event(pygame.K_SPACE, pygame.KEYUP), event(pygame.K_SPACE)])
    assert controller.stage is Stage.MODULE_1_PREPARE
    assert [p["audio"]["cue_id"] for n, p in events if n.endswith("play_requested")] == [CUE_ID, CUE_ID]
    acknowledged = [p for n, p in events if n.endswith("acknowledged")]
    assert len(acknowledged) == 1
    assert acknowledged[0]["physical_audibility_verified"] is False
    assert all(n.startswith("training_warning_audio_") for n, _ in events)


@pytest.mark.parametrize("fault", ["sound", "play", "stop", "logging"])
def test_practice_audio_or_log_fault_cannot_block_training(monkeypatch, fault):
    app, controller, mixer, events = start_practice(monkeypatch, fault)
    present(app)
    practice = app.warning_practice
    if fault in ("sound", "play"):
        assert "음향 사용 불가" in practice.page().status
    mixer.now += 1
    app.handle_events([event(pygame.K_SPACE)])
    assert controller.training_durations == [600.0]
    assert controller.stage is Stage.EYE_TRACKER_CALIBRATION
    assert practice.closed and app.runtime_error is None
    if fault == "logging":
        assert practice.recording_errors
    elif fault == "stop":
        assert any("close:" in e for e in practice.audio.errors)


@pytest.mark.parametrize("exit_event", [pygame.QUIT, pygame.K_ESCAPE])
def test_abort_during_practice_stops_only_cue_and_never_launches_training(monkeypatch, exit_event):
    app, controller, mixer, events = start_practice(monkeypatch)
    present(app)
    app.handle_events([pygame.event.Event(pygame.QUIT) if exit_event == pygame.QUIT
                       else event(exit_event)])
    assert not app.running and controller.stage is Stage.ABORTED
    assert controller.training_durations == []
    assert not any(c.get_busy() for c in mixer.channels)
    assert app.warning_practice.closed
    app.close_warning_practice()
    assert len([n for n, _ in events if n.endswith("closed")]) == 1


def test_operator_loop_runs_first_flip_hook_and_closes_practice(monkeypatch):
    from src.p8.operator_ui_runtime import run_operator_ui
    controller = FakeController()
    controller.runtime = SimpleNamespace(m2_hazard_v1=object())
    events = []
    controller.session = SimpleNamespace(services=SimpleNamespace(
        emit=lambda name, payload: events.append((name, payload))))
    batches = iter([[], [pygame.event.Event(pygame.QUIT)]])
    monkeypatch.setattr(pygame.event, "get", lambda: next(batches))
    assert run_operator_ui(controller, config()) == 2
    assert any(n.endswith("play_requested") for n, _ in events)
    assert any(n.endswith("closed") for n, _ in events)
    assert controller.training_durations == []


@pytest.mark.parametrize("fault", [None, "play"])
def test_real_session_exports_practice_evidence_even_if_sound_failed(tmp_path, fault):
    import csv
    import io
    import json
    from zipfile import ZipFile

    from src.scenario.hazard_warning_audio import HazardWarningAudio
    from tests.test_p8_operator_backend import StrictClock, advance_to, register_dev

    controller, _ = register_dev(tmp_path, StrictClock())
    advance_to(controller, Stage.TRAINING)
    mixer = FakeMixer(fail=fault)
    practice = WarningSoundPractice(
        controller, audio=HazardWarningAudio(mixer=mixer), clock=lambda: mixer.now)
    practice.drawn = True
    practice.after_display_flip()
    mixer.now += 1
    assert practice.acknowledge()
    controller.abort("OFFLINE_PRACTICE_VERIFICATION")
    exported = controller.snapshot().result_export
    assert exported.error is None
    with ZipFile(exported.archive) as archive:
        assert archive.testzip() is None
        name = next(n for n in archive.namelist() if "/research_events_" in n)
        rows = list(csv.DictReader(io.StringIO(archive.read(name).decode("utf-8"))))
        rows = [r for r in rows if r["event_type"].startswith("training_warning_audio_")]
        assert len(rows) == 3
        assert all(r["phase"] == "TRAINING" for r in rows)
        payloads = [json.loads(r["event_value"]) for r in rows]
        assert payloads[0]["audio"]["cue_id"] == CUE_ID
        assert payloads[0]["audio"]["play_request_accepted"] is (fault is None)
        assert payloads[1]["confirmation"] == "SPACE"
        assert payloads[2]["audio"]["closed"]
