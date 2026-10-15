import ast
import builtins
import runpy
from collections import deque
from dataclasses import replace
from pathlib import Path

import pytest

from src.p8.ffb_model import FfbParameters
from src.p8.ffb_protocol import OfflineFfbDispatcher, validate_simulated_command
from src.p8.ffb_safety import FfbRequest, FfbState
from tests.test_ffb_model import observation


class FakeOutput:
    """No device access. Bounded trace; separate total counters for long sequences."""

    def __init__(self, fail=()):
        self.events = deque(maxlen=16)
        self.fail = set(fail)
        self.previous_sequence = 0
        self.context = observation().context
        self.applies = 0

    def record(self, event, value=None):
        self.events.append((event, value))
        if event in self.fail:
            raise RuntimeError(f"fake {event} failure")

    def apply(self, command, *, now):
        validate_simulated_command(
            command,
            now=now,
            context=self.context,
            previous_sequence=self.previous_sequence,
        )
        self.previous_sequence = command.sequence
        self.record("apply", command)
        self.applies += 1

    def disable(self, command):
        self.previous_sequence = max(self.previous_sequence, command.sequence)
        self.record("disable", command.reason)

    def record_fault(self, reason):
        self.record("fault", reason)

    def close(self):
        self.record("close")


def test_fake_records_apply_disable_fault_and_close_without_hardware_permission():
    output = FakeOutput()
    subject = OfflineFfbDispatcher(output)
    assert not subject.step(observation(), now=1).simulation_allowed
    subject.step(observation(1.1), now=1.1, request=FfbRequest.ENABLE_EVALUATION)
    active = subject.step(observation(1.2), now=1.2)
    assert output.applies == 2 and active.parameters.spring > 0
    assert active.physical_output_allowed is False
    subject.step(observation(1.3), now=1.3, request=FfbRequest.DISABLE)
    subject.step(observation(1.4, connected=False), now=1.4)
    subject.close(now=1.5)
    before = list(output.events)
    subject.close(now=1.6)
    subject.step(observation(1.7), now=1.7, request=FfbRequest.ENABLE_EVALUATION)
    assert list(output.events) == before
    assert {e for e, _ in output.events} == {"apply", "disable", "fault", "close"}


@pytest.mark.parametrize("fail", [("apply",), ("apply", "fault"), ("disable", "fault")])
def test_backend_failures_latch_fault_and_still_attempt_disable(fail):
    output = FakeOutput(fail)
    subject = OfflineFfbDispatcher(output)
    request = FfbRequest.HOLD if "disable" in fail else FfbRequest.ENABLE_EVALUATION
    command = subject.step(observation(), now=1, request=request)
    assert command.state is FfbState.FAULT
    assert not command.simulation_allowed and command.parameters == FfbParameters()
    assert output.events[-1][0] == "disable"
    assert subject.last_backend_error and subject.backend_failure_count >= 1
    output.fail.clear()
    assert (
        subject.step(
            observation(1.1), now=1.1, request=FfbRequest.ENABLE_EVALUATION
        ).state
        is FfbState.FAULT
    )


def test_cleanup_failure_cannot_hide_an_existing_exception_and_close_is_attempted():
    output = FakeOutput(("disable", "close"))
    subject = OfflineFfbDispatcher(output)
    with pytest.raises(ValueError, match="primary"):
        try:
            raise ValueError("primary")
        finally:
            subject.close(now=1)
    assert [e for e, _ in output.events] == ["disable", "close"]
    assert subject.backend_failure_count == 2
    assert subject.safety.state is FfbState.CLOSED


def test_fake_receiver_rejects_replay_expiry_context_and_forged_values():
    output = FakeOutput()
    subject = OfflineFfbDispatcher(output)
    command = subject.step(observation(), now=1, request=FfbRequest.ENABLE_EVALUATION)
    with pytest.raises(ValueError):
        output.apply(command, now=1)
    for altered, now in (
        (command, 1.9),
        (replace(command, context=replace(command.context, stage_id="M2")), 1),
        (replace(command, parameters=FfbParameters(float("nan"), 0)), 1),
        (replace(command, parameters=FfbParameters(0.9, 0)), 1),
        (replace(command, valid_until=float("inf")), 1),
    ):
        with pytest.raises(ValueError):
            validate_simulated_command(
                altered, now=now, context=output.context, previous_sequence=0
            )


def test_fake_trace_is_bounded_and_no_automatic_fault_recovery():
    output = FakeOutput()
    subject = OfflineFfbDispatcher(output)
    for i in range(100):
        subject.step(observation(i / 10, connected=False), now=i / 10)
    assert len(output.events) == 16 and output.applies == 0


def test_disable_invalidates_in_flight_command_even_if_context_is_unchanged():
    output = FakeOutput()
    subject = OfflineFfbDispatcher(output)
    subject.step(observation(), now=1, request=FfbRequest.ENABLE_EVALUATION)
    # Simulate a calculated but delayed command without delivering it to the Fake.
    pending = subject.safety.step(
        observation(1.01), FfbParameters(0.05, 0.05), now=1.01
    )
    subject.step(observation(1.02), now=1.02, request=FfbRequest.DISABLE)
    with pytest.raises(ValueError):
        output.apply(pending, now=1.03)


def test_ffb_imports_are_device_free_and_no_existing_runtime_imports_them(monkeypatch):
    names = ("ffb_model", "ffb_safety", "ffb_protocol")
    root = Path(__file__).resolve().parents[1]
    allowed = {"__future__", "dataclasses", "math", "enum", "typing"}
    allowed.update(f"src.p8.{name}" for name in names)
    for name in names:
        tree = ast.parse((root / "src" / "p8" / f"{name}.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module in allowed
            elif isinstance(node, ast.Import):
                assert all(alias.name in allowed for alias in node.names)
    # Re-execute imports with device modules explicitly forbidden; no SDL init.
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert not name.startswith(("pygame", "carla", "ctypes", "win32", "sdl2"))
        return real_import(name, *args, **kwargs)

    # Execute each file afresh without replacing the already imported enum classes.
    with monkeypatch.context() as patch:
        patch.setattr(builtins, "__import__", guarded)
        for name in names:
            namespace = runpy.run_path(str(root / "src" / "p8" / f"{name}.py"))
            assert namespace["__name__"] == "<run_path>"
    for path in (root / "src").rglob("*.py"):
        if path.stem in names:
            continue
        assert not any(
            f"src.p8.{name}" in path.read_text(encoding="utf-8") for name in names
        )
