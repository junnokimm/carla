from __future__ import annotations

import importlib
import platform
import struct
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeAlias, runtime_checkable

SdkValue: TypeAlias = str | int | float | bool | None | tuple[float, ...]
SdkData: TypeAlias = dict[str, SdkValue]
SdkCallback: TypeAlias = Callable[[SdkData], None]


@dataclass(frozen=True, slots=True)
class TobiiError(RuntimeError):
    status: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.status}: {self.detail}"


@dataclass(frozen=True, slots=True)
class RuntimeInfo:
    system: str
    version: tuple[int, int]
    bits: int
    implementation: str

    @classmethod
    def current(cls) -> RuntimeInfo:
        return cls(platform.system(), sys.version_info[:2], struct.calcsize("P") * 8,
                   platform.python_implementation())

    @property
    def supported(self) -> bool:
        return (self.system == "Windows" and self.version == (3, 10)
                and self.bits == 64 and self.implementation == "CPython")


class TrackerIdentity(Protocol):
    @property
    def model(self) -> str: ...
    @property
    def serial_number(self) -> str: ...
    @property
    def address(self) -> str: ...
    @property
    def device_name(self) -> str: ...
    @property
    def firmware_version(self) -> str: ...


class Tracker(TrackerIdentity, Protocol):
    def subscribe_to(self, subscription_type: str, callback: SdkCallback,
                     as_dictionary: bool = False) -> None: ...
    def unsubscribe_from(self, subscription_type: str, callback: SdkCallback) -> None: ...


class DiscoverySdk(Protocol):
    def find_all_eyetrackers(self) -> tuple[TrackerIdentity, ...]: ...


@runtime_checkable
class Sdk(Protocol):
    EYETRACKER_GAZE_DATA: str
    EYETRACKER_NOTIFICATION_CONNECTION_LOST: str
    EYETRACKER_STREAM_ERRORS: str
    def find_all_eyetrackers(self) -> tuple[Tracker, ...]: ...
    def get_system_time_stamp(self) -> int: ...


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    model: str
    serial_number: str
    address: str
    device_name: str
    firmware_version: str

    @property
    def is_nano(self) -> bool:
        return self.model.strip().casefold() == "tobii pro nano"

    @classmethod
    def from_tracker(cls, tracker: TrackerIdentity) -> DeviceIdentity:
        return cls(tracker.model, tracker.serial_number, tracker.address,
                   tracker.device_name, tracker.firmware_version)


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    status: str
    devices: tuple[DeviceIdentity, ...]


def load_sdk(runtime: RuntimeInfo | None = None) -> Sdk:
    active = runtime or RuntimeInfo.current()
    if not active.supported:
        raise TobiiError("UNSUPPORTED_RUNTIME", "Requires Windows x64 CPython 3.10; CARLA environment is unchanged")
    try:
        module = importlib.import_module("tobii_research")
    except ModuleNotFoundError as error:
        status = "SDK_NOT_INSTALLED" if error.name == "tobii_research" else "SDK_IMPORT_FAILED"
        raise TobiiError(status, str(error)) from error
    except (ImportError, OSError) as error:
        raise TobiiError("SDK_IMPORT_FAILED", str(error)) from error
    if not isinstance(module, Sdk):
        raise TobiiError("SDK_API_MISMATCH", "Required Pro SDK API is missing")
    return module


def discover(sdk: DiscoverySdk) -> DiscoveryReport:
    devices = tuple(DeviceIdentity.from_tracker(item) for item in sdk.find_all_eyetrackers())
    status = "OK" if any(item.is_nano for item in devices) else "NO_NANO"
    return DiscoveryReport(status if devices else "NO_DEVICE", devices)


def select_nano(sdk: Sdk, serial: str) -> Tracker:
    for tracker in sdk.find_all_eyetrackers():
        if tracker.serial_number == serial:
            if not DeviceIdentity.from_tracker(tracker).is_nano:
                raise TobiiError("MODEL_MISMATCH", tracker.model)
            return tracker
    raise TobiiError("NO_DEVICE", serial)
