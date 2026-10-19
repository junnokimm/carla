from types import SimpleNamespace

import pytest

from tools import diagnose_tsxw_init as diagnostic


class FakePygame:
    """Deliberately provides no haptic, effect, rumble or device-write API."""

    QUIT = 1
    JOYDEVICEREMOVED = 2

    def __init__(self, fail=None):
        self.calls = []
        self.flags = {
            "global": False,
            "display": False,
            "joystick": False,
            "device": False,
        }
        self.fail = fail
        self.name = "TS-XW Racer"
        self.events = []
        self.bad_axis = False
        self.partial_init = False
        self.version = SimpleNamespace(ver="FAKE")
        self.__file__ = "FAKE/no-device"
        self.display = SimpleNamespace(
            init=lambda: self.change("display.init", "display", True),
            get_init=lambda: self.flags["display"],
            set_mode=lambda size: self.call("display.set_mode"),
            set_caption=lambda title: self.call("display.set_caption"),
            flip=lambda: self.call("display.flip"),
        )
        self.joystick = SimpleNamespace(
            init=lambda: self.change("joystick.init", "joystick", True),
            quit=lambda: self.change("joystick.quit", "joystick", False),
            get_init=lambda: self.flags["joystick"],
            Joystick=self.open,
        )
        self.event = SimpleNamespace(get=self.get_events)
        self.device = SimpleNamespace(
            get_init=lambda: self.flags["device"],
            get_name=lambda: self.name,
            get_instance_id=lambda: 99,
            get_numaxes=lambda: 4,
            get_numbuttons=lambda: 13,
            get_numhats=lambda: 1,
            get_axis=self.axis,
            get_button=lambda i: self.call("button") or 0,
            get_hat=lambda i: self.call("hat") or (0, 0),
            quit=lambda: self.change("device.quit", "device", False),
        )

    def call(self, name):
        self.calls.append(name)
        if name == self.fail:
            raise RuntimeError(f"fake {name} failure")

    def change(self, name, key, value):
        self.call(name)
        self.flags[key] = value

    def get_init(self):
        return self.flags["global"]

    def get_sdl_version(self):
        return (2, 28, 4)

    def init(self):
        self.call("pygame.init")
        self.flags.update({"global": True, "display": True, "joystick": True})
        return (4, 1 if self.partial_init else 0)

    def quit(self):
        self.call("pygame.quit")
        self.flags = dict.fromkeys(self.flags, False)

    def open(self, index):
        assert index == 0
        self.change("Joystick", "device", True)
        return self.device

    def axis(self, index):
        self.call("axis")
        return float("nan") if self.bad_axis else 0.0

    def get_events(self):
        self.call("event.get")
        return self.events


def execute(stage, pg=None, interrupt=False):
    pg = pg or FakePygame()
    rows = []
    now = [0.0]

    def sleep(seconds):
        if interrupt:
            raise KeyboardInterrupt()
        now[0] += seconds

    code = diagnostic.run_stage(
        stage,
        0.1,
        0,
        lambda event, **values: rows.append({"event": event, **values}),
        loader=lambda: pg,
        clock=lambda: now[0],
        sleep=sleep,
    )
    return code, pg, rows


@pytest.mark.parametrize(
    "stage,expected",
    [
        ("A", []),
        (
            "B",
            ["display.init", "display.set_mode", "display.set_caption", "display.flip"],
        ),
        ("C", ["joystick.init"]),
        ("D", ["pygame.init"]),
        ("E", ["display.init", "joystick.init", "Joystick"]),
        ("F", ["display.init", "joystick.init", "Joystick"]),
    ],
)
def test_stages_have_distinct_boundaries_and_release_without_reopen(stage, expected):
    code, pg, rows = execute(stage)
    assert code == 0
    setup = [
        c
        for c in pg.calls
        if c not in ("event.get", "axis", "button", "hat") and not c.endswith("quit")
    ]
    assert setup == expected
    assert pg.calls.count("Joystick") == int(stage in "EF")
    assert ("axis" in pg.calls) is (stage == "F")
    assert ("event.get" in pg.calls) is (stage in "BF")
    assert pg.calls[-2:] == ["joystick.quit", "pygame.quit"]
    if stage in "EF":
        assert pg.calls[-3] == "device.quit"
        opened = next(r for r in rows if r["event"] == "device_opened")
        assert opened["name"] == "TS-XW Racer" and opened["instance_id"] == 99
    cleanup = next(r for r in rows if r["event"] == "cleanup_completed")
    assert not any(cleanup["state"].values())
    assert cleanup["os_ownership_released"] == "NOT_VERIFIED"
    assert rows[-1]["force_measurement"] == "NOT_MEASURED"


@pytest.mark.parametrize(
    "failure",
    [
        "display.init",
        "joystick.init",
        "Joystick",
        "axis",
        "device.quit",
        "joystick.quit",
        "pygame.quit",
    ],
)
def test_failure_cleans_up_once_and_is_not_reported_as_success(failure):
    code, pg, rows = execute("F", FakePygame(failure))
    assert code == 1
    assert pg.calls.count("Joystick") <= 1
    assert pg.calls.count("pygame.quit") == 1
    assert any(r["event"] == "cleanup_completed" for r in rows)
    cleanup = next(r for r in rows if r["event"] == "cleanup_completed")
    if failure.endswith("quit"):
        assert not cleanup["success"]
        assert cleanup["errors"][0]["operation"] == failure
    else:
        failed = next(r for r in rows if r["event"] == "stage_failed")
        assert "fake" in failed["error"]["message"]


def test_interrupt_releases_device_and_subsystems():
    code, pg, rows = execute("E", interrupt=True)
    assert code == 1
    assert pg.calls[-3:] == ["device.quit", "joystick.quit", "pygame.quit"]
    assert (
        next(r for r in rows if r["event"] == "stage_failed")["error"]["type"]
        == "KeyboardInterrupt"
    )


@pytest.mark.parametrize(
    "problem", ["wrong_name", "removed", "nonfinite", "partial_init"]
)
def test_device_and_initialization_failures_are_explicit(problem):
    pg = FakePygame()
    if problem == "wrong_name":
        pg.name = "Different controller"
    elif problem == "removed":
        pg.events = [
            SimpleNamespace(type=pg.JOYDEVICEREMOVED, dict={"instance_id": 99})
        ]
    elif problem == "nonfinite":
        pg.bad_axis = True
    else:
        pg.partial_init = True
    code, pg, rows = execute("D" if problem == "partial_init" else "F", pg)
    assert code == 1
    assert any(r["event"] == "stage_failed" for r in rows)
    assert not any(pg.flags.values())
    if problem in ("wrong_name", "removed"):
        assert "axis" not in pg.calls


def test_cancel_does_not_load_pygame_or_run_stage(tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "cancel")
    monkeypatch.setattr(
        diagnostic, "run_stage", lambda *args: pytest.fail("must not run")
    )
    output = tmp_path / "cancel.jsonl"
    assert diagnostic.main(["--stage", "E", "--output", str(output)]) == 2
    assert "cancelled_before_import" in output.read_text()


def test_existing_evidence_is_never_overwritten(tmp_path, monkeypatch):
    output = tmp_path / "existing.jsonl"
    output.write_text("preserve me")
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("must not prompt"))
    assert diagnostic.main(["--stage", "A", "--output", str(output)]) == 1
    assert output.read_text() == "preserve me"


def test_cli_observation_is_labeled_as_user_report(tmp_path, monkeypatch):
    answers = iter(["START A", "during: lost; after cleanup: restored"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    def fake_stage(stage, seconds, index, emit):
        assert stage == "A"
        emit("cleanup_completed", success=True)
        return 0

    monkeypatch.setattr(diagnostic, "run_stage", fake_stage)
    output = tmp_path / "observed.jsonl"
    assert diagnostic.main(["--stage", "A", "--output", str(output)]) == 0
    text = output.read_text()
    assert "USER_REPORT_NOT_SENSOR" in text
    assert text.index("cleanup_completed") < text.index("user_observation")


def test_log_failure_after_open_still_releases_device():
    pg = FakePygame()

    def broken_log(event, **values):
        if pg.flags["device"] or event == "cleanup_completed":
            raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        diagnostic.run_stage("E", 0.1, 0, broken_log, loader=lambda: pg)
    assert not any(pg.flags.values())
    assert pg.calls.count("Joystick") == 1
    assert pg.calls[-3:] == ["device.quit", "joystick.quit", "pygame.quit"]


def test_import_failure_is_logged_without_initializing_anything():
    rows = []

    def absent():
        raise ImportError("pygame unavailable")

    assert (
        diagnostic.run_stage(
            "A",
            0.1,
            0,
            lambda event, **values: rows.append({"event": event, **values}),
            loader=absent,
        )
        == 1
    )
    assert (
        next(r for r in rows if r["event"] == "stage_failed")["operation"]
        == "import_pygame"
    )
    assert next(r for r in rows if r["event"] == "cleanup_completed")["attempted"] == []


def test_interrupted_confirmation_is_logged_without_starting(tmp_path, monkeypatch):
    def interrupted(prompt):
        raise KeyboardInterrupt()

    monkeypatch.setattr("builtins.input", interrupted)
    monkeypatch.setattr(
        diagnostic, "run_stage", lambda *args: pytest.fail("must not run")
    )
    output = tmp_path / "interrupted.jsonl"
    assert diagnostic.main(["--stage", "D", "--output", str(output)]) == 2
    assert "cancelled_before_import" in output.read_text()
    assert "KeyboardInterrupt" in output.read_text()
