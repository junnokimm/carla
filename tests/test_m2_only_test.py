import json
from dataclasses import replace
from types import SimpleNamespace

import pygame
import pytest

from src.experiment.context import Module1Condition, Module2Condition
from src.p8.model import Mode, P8Error, Stage
from src.p8.operator_cli import build_parser, main as operator_main
from src.p8.operator_controller import OperatorController
from src.p8.operator_model import RegistrationRequest
from src.p8.operator_pages import stage_page
from src.p8.operator_ui import OperatorApp
from tests.test_p8_operator_backend import RunnerEvidence, StrictClock, runtime_config
from tests.test_p8_operator_ui import FakeController, config
from tests.test_p8_pilot_launcher import settings
from tests.vehicle_audio_fakes import FakeMixer
from tools import run_pilot


@pytest.mark.parametrize("condition", ["1", "2"])
def test_launcher_only_prompts_participant_and_m2_and_preserves_runtime(tmp_path, monkeypatch, condition):
    source = tmp_path / "pilot.json"
    original = json.dumps(settings() | {"m1": "SURT"})
    source.write_text(original)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run_pilot, "PROJECT_ROOT", tmp_path)
    replies = iter(["7", condition])
    prompts, received = [], []
    def reply(prompt):
        prompts.append(prompt)
        return next(replies)
    monkeypatch.setattr("builtins.input", reply)
    monkeypatch.setattr(run_pilot.operator_cli, "main", lambda args: received.append(build_parser().parse_args(args)) or 0)
    assert run_pilot.main(["--m2-only", "--config", str(source)]) == 0
    assert len(prompts) == 2 and "참가자 번호" in prompts[0] and "M2" in prompts[1]
    args = received[0]
    assert args.m2_only and args.session_type == "test" and args.run_id == "007_test"
    assert args.participant_id == "007" and args.m2 == run_pilot.M2_CHOICES[condition]
    assert args.drive_duration is None and args.m2_hazard_v1 and args.tsxw
    assert args.gaze_config is not None and args.exit_route_id == "town04-exit-47"
    assert source.read_text() == original
    assert run_pilot.main(["--m2-only", "--config", str(source), "--participant-id", "7", "--m2", condition, "--no-gaze"]) == 0
    assert received[-1].run_id == "007_test_02" and received[-1].gaze_config is None


@pytest.mark.parametrize("args", [["--session-type", "pilot"], ["--session-type", "2"], ["--m1", "SURT"]])
def test_launcher_rejects_misleading_m2_only_labels_before_any_prompt(monkeypatch, args):
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("unexpected prompt"))
    with pytest.raises(SystemExit) as error:
        run_pilot.main(["--m2-only", *args])
    assert error.value.code == 2


@pytest.mark.parametrize("args", [[], ["--session-type", "pilot"], ["--session-type", "test", "--drive-duration", "10"], ["--mode", "participant", "--session-type", "test"]])
def test_raw_cli_rejects_invalid_m2_only_scope(args):
    with pytest.raises(SystemExit) as error:
        operator_main(["--participant-id", "001", "--run-id", "001_test", "--m2-only", *args])
    assert error.value.code == 2


def request(**kwargs):
    return RegistrationRequest("001", "001_test", m1_condition=Module1Condition.SURT,
        m2_condition=Module2Condition.MANUAL, session_type="test", m2_only_test=True, **kwargs)


def test_direct_start_is_fresh_only_without_false_training_or_m1_evidence(tmp_path):
    clock, evidence = StrictClock(), RunnerEvidence([], [], [], [])
    controller = OperatorController(tmp_path, mode=Mode.DEV_REHEARSAL,
        runtime=runtime_config(clock, evidence), monotonic_ns=clock.monotonic_ns, utc_ns=clock.utc_ns)
    snapshot = controller.register_id(request())
    assert snapshot.stage is Stage.MODULE_2_PREPARE and snapshot.m2_only_test
    assert not evidence.viewers and not snapshot.module_runs
    manifest = controller.session.result
    assert manifest["execution_scope"] == "M2_ONLY_TEST" and not manifest["full_protocol_performed"]
    assert [s["stage"] for s in manifest["stages"]] == ["SESSION_CREATED", "MODULE_2_PREPARE"]
    assert "M2 단독 테스트" in stage_page(snapshot, "").eyebrow
    with pytest.raises(P8Error, match="FRESH_OPERATOR_TEST"):
        controller.session.start_m2_test()
    with pytest.raises(P8Error, match="TRAINING_RUN_OUTSIDE"):
        from src.p8.operator_model import TrainingRun
        controller.execute_training(TrainingRun.MANUAL_NOA, 10)
    controller.abort("TEST_COMPLETE")


@pytest.mark.parametrize("mode, session_type", [(Mode.PARTICIPANT, "test"), (Mode.DEV_REHEARSAL, "pilot"), (Mode.DEV_REHEARSAL, None)])
def test_controller_rejects_invalid_scope_before_claims(tmp_path, mode, session_type):
    clock = StrictClock()
    controller = OperatorController(tmp_path, mode=mode, runtime=runtime_config(clock, RunnerEvidence([], [], [], [])))
    with pytest.raises(P8Error, match="M2_ONLY_REQUIRES_DEV_TEST_SESSION"):
        controller.register_id(replace(request(), session_type=session_type))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("audio_fault", [None, "play", "sound", "stop"])
def test_m2_ui_keeps_warning_and_gaze_check_before_nominal_drive(monkeypatch, audio_fault):
    class M2Controller(FakeController):
        def register_id(self, registration):
            super().register_id(registration)
            self.stage = Stage.MODULE_2_PREPARE
            return self.snapshot()
    controller = M2Controller()
    events = []
    controller.runtime = SimpleNamespace(m2_hazard_v1=object(), gaze_config="gaze.json")
    controller.session = SimpleNamespace(services=SimpleNamespace(emit=lambda n, p: events.append((n, p))))
    mixer = FakeMixer(fail=audio_fault)
    monkeypatch.setattr(pygame, "mixer", mixer)
    app = OperatorApp(controller, replace(config(), registration=request()))
    app._restore_display = lambda **kwargs: None
    app.start()
    app.warning_practice.clock = lambda: mixer.now
    app.render(pygame.Surface((1280, 720)))
    app.after_display_flip()
    mixer.now += 1
    app._handle_key(pygame.K_SPACE)
    assert app.warning_practice is None and controller.stage is Stage.MODULE_2_PREPARE
    assert not controller.training_durations and not controller.run_stages
    assert all(n.startswith("m2_test_warning_audio_") for n, _ in events)
    checks, accepted = [], iter([False, True])
    def gaze_check(current):
        checks.append(current.snapshot().stage)
        return next(accepted)
    monkeypatch.setattr("src.p8.m2_gaze_check.prepare_m2_gaze", gaze_check)
    app._handle_key(pygame.K_SPACE)
    assert not controller.run_stages
    app._handle_key(pygame.K_SPACE)
    assert checks == [Stage.MODULE_2_PREPARE] * 2
    assert controller.run_stages == [Stage.MODULE_2_RUNNING]
    assert controller.module_durations == [None]
    assert controller.stage is Stage.SESSION_COMPLETE
