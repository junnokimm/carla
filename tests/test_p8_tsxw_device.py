from __future__ import annotations

from math import inf, nan

import pygame
import pytest

from src.p8.tsxw_input import TSXW_NAME, TsxwDeviceError, TsxwInput
from src.p8.tsxw_policy import TsxwConfig


class FakeJoystick:
    def __init__(
        self,
        index: int,
        *,
        name: str = TSXW_NAME,
        axes: list[float] | None = None,
    ) -> None:
        self.index = index
        self.name = name
        self.axes = axes or [0.0, 1.0, 1.0, 0.0]
        self.buttons = [0] * 13
        self.initialized = False
        self.quit_count = 0
        self.read_error: pygame.error | None = None
        self.quit_error: pygame.error | None = None

    def init(self) -> None:
        self.initialized = True

    def quit(self) -> None:
        self.initialized = False
        self.quit_count += 1
        if self.quit_error is not None:
            raise self.quit_error

    def get_init(self) -> bool:
        return self.initialized

    def get_name(self) -> str:
        return self.name

    def get_guid(self) -> str:
        return f"guid-{self.index}"

    def get_instance_id(self) -> int:
        return 100 + self.index

    def get_numaxes(self) -> int:
        return len(self.axes)

    def get_numbuttons(self) -> int:
        return len(self.buttons)

    def get_numhats(self) -> int:
        return 1

    def get_axis(self, index: int) -> float:
        if self.read_error is not None:
            raise self.read_error
        return self.axes[index]

    def get_button(self, index: int) -> int:
        return self.buttons[index]


def install_devices(
    monkeypatch: pytest.MonkeyPatch,
    devices: list[FakeJoystick],
) -> list[str]:
    operations: list[str] = []
    initialized = False

    def init() -> None:
        nonlocal initialized
        initialized = True
        operations.append("module.init")

    def quit_module() -> None:
        nonlocal initialized
        initialized = False
        operations.append("module.quit")

    monkeypatch.setattr(pygame.joystick, "get_init", lambda: initialized)
    monkeypatch.setattr(pygame.joystick, "init", init)
    monkeypatch.setattr(pygame.joystick, "quit", quit_module)
    monkeypatch.setattr(pygame.joystick, "get_count", lambda: len(devices))
    monkeypatch.setattr(pygame.joystick, "Joystick", lambda index: devices[index])
    return operations


def test_selection_rejects_ambiguous_name_and_shape_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations = install_devices(monkeypatch, [FakeJoystick(0), FakeJoystick(1)])

    with pytest.raises(TsxwDeviceError, match="ambiguous"):
        TsxwInput.open(TsxwConfig(name=TSXW_NAME, steering_deadzone=0.0))
    assert operations == ["module.init", "module.quit"]

    malformed = FakeJoystick(0, axes=[0.0, 1.0, 1.0])
    operations = install_devices(monkeypatch, [malformed])
    with pytest.raises(TsxwDeviceError, match="axes=3"):
        TsxwInput.open(TsxwConfig(index=0, steering_deadzone=0.0))
    assert operations == ["module.init", "module.quit"]


def test_poll_edges_read_failure_and_cleanup_are_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeJoystick(0, axes=[0.2, -0.639, -1.0, 0.0])
    operations = install_devices(monkeypatch, [device])
    records: list[tuple[str, str]] = []
    adapter = TsxwInput.open(
        TsxwConfig(index=0, steering_deadzone=0.0),
        event_sink=lambda event: records.append((event.event_type, event.payload_json)),
    )
    device.buttons[5] = 1
    device.buttons[7] = 1

    first = adapter.poll()
    held = adapter.poll()
    device.read_error = pygame.error("disconnected")
    with pytest.raises(TsxwDeviceError, match="disconnected"):
        adapter.poll()
    adapter.close()

    assert first.throttle == 1.0
    assert first.brake == pytest.approx(0.8195)
    assert first.noa_pressed and first.reject_pressed
    assert not held.noa_pressed and not held.reject_pressed
    assert [record[0] for record in records] == [
        "tsxw_device_opened",
        "tsxw_input_failure",
        "tsxw_device_closed",
    ]
    assert device.quit_count == 1
    assert operations == ["module.init", "module.quit"]


def test_index_and_name_must_identify_the_same_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_devices(
        monkeypatch,
        [FakeJoystick(0, name=TSXW_NAME), FakeJoystick(1, name="Other")],
    )

    with pytest.raises(TsxwDeviceError, match="no matching"):
        TsxwInput.open(TsxwConfig(index=1, name=TSXW_NAME, steering_deadzone=0.0))


def test_open_logging_failure_releases_device_and_subsystem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeJoystick(0)
    operations = install_devices(monkeypatch, [device])

    def fail_log(event) -> None:
        raise OSError("log unavailable")

    with pytest.raises(OSError, match="log unavailable"):
        TsxwInput.open(
            TsxwConfig(index=0, steering_deadzone=0.0),
            event_sink=fail_log,
        )

    assert device.quit_count == 1
    assert operations == ["module.init", "module.quit"]


@pytest.mark.parametrize("axis_value", [nan, inf, -inf])
@pytest.mark.parametrize("axis_index", [0, 1, 2])
def test_poll_rejects_non_finite_axis_samples(
    monkeypatch: pytest.MonkeyPatch,
    axis_value: float,
    axis_index: int,
) -> None:
    axes = [0.0, 1.0, 1.0, 0.0]
    axes[axis_index] = axis_value
    device = FakeJoystick(0, axes=axes)
    install_devices(monkeypatch, [device])
    records: list[str] = []
    adapter = TsxwInput.open(
        TsxwConfig(index=0, steering_deadzone=0.0),
        event_sink=lambda event: records.append(event.event_type),
    )

    with pytest.raises(TsxwDeviceError, match="finite"):
        adapter.poll()

    assert records == ["tsxw_device_opened", "tsxw_input_failure"]


def test_poll_detects_matching_sdl_removal_while_device_reports_initialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeJoystick(0)
    install_devices(monkeypatch, [device])
    adapter = TsxwInput.open(TsxwConfig(index=0, steering_deadzone=0.0))
    removal = pygame.event.Event(
        pygame.JOYDEVICEREMOVED,
        instance_id=device.get_instance_id(),
    )

    with pytest.raises(TsxwDeviceError, match="removed"):
        adapter.poll([removal])

    assert device.get_init() is True


def test_close_releases_owned_subsystem_when_device_quit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeJoystick(0)
    operations = install_devices(monkeypatch, [device])
    adapter = TsxwInput.open(TsxwConfig(index=0, steering_deadzone=0.0))
    device.quit_error = pygame.error("device quit failed")

    with pytest.raises(pygame.error, match="device quit failed"):
        adapter.close()

    assert operations == ["module.init", "module.quit"]
