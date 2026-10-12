from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType

import pytest

from tools.tobii.sdk import RuntimeInfo, TobiiError, discover, load_sdk


@dataclass(frozen=True, slots=True)
class FakeTracker:
    model: str = "Tobii Pro Nano"
    serial_number: str = "FAKE-NANO-001"
    address: str = "fake://nano"
    device_name: str = "CI fixture"
    firmware_version: str = "fake-firmware"


class FakeSdk:
    def __init__(self, trackers: tuple[FakeTracker, ...]) -> None:
        self.trackers = trackers

    def find_all_eyetrackers(self) -> tuple[FakeTracker, ...]:
        return self.trackers


def test_missing_sdk_is_explicit_on_supported_runtime(monkeypatch) -> None:
    runtime = RuntimeInfo("Windows", (3, 10), 64, "CPython")

    def absent(name: str) -> ModuleType:
        raise ModuleNotFoundError(name, name=name)

    monkeypatch.setattr("tools.tobii.sdk.importlib.import_module", absent)

    with pytest.raises(TobiiError) as caught:
        load_sdk(runtime)

    assert caught.value.status == "SDK_NOT_INSTALLED"


def test_unsupported_runtime_does_not_attempt_sdk_import(monkeypatch) -> None:
    def forbidden(name: str) -> ModuleType:
        pytest.fail("unsupported interpreter must not load native SDK")

    monkeypatch.setattr("tools.tobii.sdk.importlib.import_module", forbidden)

    with pytest.raises(TobiiError) as caught:
        load_sdk(RuntimeInfo("Windows", (3, 12), 64, "CPython"))

    assert caught.value.status == "UNSUPPORTED_RUNTIME"


@pytest.mark.parametrize(
    ("devices", "status"),
    [((), "NO_DEVICE"), ((FakeTracker(model="Tobii Pro Fusion"),), "NO_NANO")],
)
def test_discovery_never_reports_nano_success_without_nano(devices, status) -> None:
    report = discover(FakeSdk(devices))

    assert report.status == status
    assert len(report.devices) == len(devices)


def test_discovery_retains_sdk_identity_fields() -> None:
    report = discover(FakeSdk((FakeTracker(),)))

    assert report.status == "OK"
    assert report.devices[0].serial_number == "FAKE-NANO-001"
    assert report.devices[0].firmware_version == "fake-firmware"
    assert report.devices[0].address == "fake://nano"
    assert report.devices[0].is_nano
