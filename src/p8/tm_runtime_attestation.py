from __future__ import annotations

import ctypes
import hashlib
import importlib
import os
import socket
import struct
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, assert_never

APPROVED_LIBCARLA_SHA256: Final = (
    "636D249A6898FEB4A6A8D166917DDDE04F26B70DAB9E95C1D3D50E9ACF3CA471"
)
DIAGNOSTIC_LIBCARLA_SHA256: Final = (
    "E4B2BAD4D70DF255C1123E7064602B1B010E34F740638B19BC2608478D479721"
)
DIAGNOSTIC_LIBCARLA_PATH: Final = Path(
    r"C:\dev\carla-native-tm-cancel-20261012\.venv-p8-diagnostic\Lib\site-packages\carla\libcarla.cp312-win_amd64.pyd"
)
_TCP_TABLE_OWNER_PID_LISTENER: Final = 3
_ERROR_INSUFFICIENT_BUFFER: Final = 122
_NO_ERROR: Final = 0


class TrafficManagerBinaryProfile(StrEnum):
    DEFAULT = "DEFAULT"
    NATIVE_DIAGNOSTIC_DEV = "NATIVE_DIAGNOSTIC_DEV"


@dataclass
class LoadedLibcarlaMismatchError(RuntimeError):
    path: Path
    expected_sha256: str
    actual_sha256: str

    def __str__(self) -> str:
        return (
            f"loaded libcarla hash mismatch at {self.path}: expected "
            f"{self.expected_sha256}, observed {self.actual_sha256}"
        )


@dataclass(frozen=True, slots=True)
class LoadedLibcarlaPathMismatchError(RuntimeError):
    expected_path: Path
    actual_path: Path

    def __str__(self) -> str:
        return (
            f"loaded libcarla path mismatch: expected {self.expected_path}, "
            f"observed {self.actual_path}"
        )


@dataclass
class ListenerOwnershipError(RuntimeError):
    port: int
    phase: str
    owner_pids: frozenset[int]

    def __str__(self) -> str:
        return (
            f"Traffic Manager port {self.port} {self.phase}; "
            f"listener owner PIDs={sorted(self.owner_pids)}"
        )


@dataclass(frozen=True, slots=True)
class PreparedRuntimeAttestation:
    port: int
    owner_pid: int
    libcarla_path: Path
    libcarla_sha256: str


@dataclass(frozen=True, slots=True)
class RuntimeAttestation:
    port: int
    owner_pid: int
    libcarla_path: Path
    libcarla_sha256: str


@dataclass(frozen=True, slots=True)
class NativeDiagnosticAttestationError(RuntimeError):
    attestation: RuntimeAttestation

    def __str__(self) -> str:
        return "native TM diagnostics require the exact candidate-J path and SHA-256"


class TrafficManagerRuntimeAttestor:
    def __init__(
        self,
        *,
        expected_sha256: str = APPROVED_LIBCARLA_SHA256,
        expected_path: Path | None = None,
        hash_file: Callable[[Path], str] | None = None,
        library_path: Callable[[], Path] | None = None,
        listener_pids: Callable[[int], frozenset[int]] | None = None,
        process_id: Callable[[], int] = os.getpid,
    ) -> None:
        self._expected_sha256 = expected_sha256.upper()
        self._expected_path = None if expected_path is None else expected_path.resolve()
        self._hash_file = hash_file or _sha256
        self._library_path = library_path or _loaded_libcarla_path
        self._listener_pids = listener_pids or windows_listener_pids
        self._process_id = process_id

    @classmethod
    def for_profile(
        cls,
        profile: TrafficManagerBinaryProfile,
        **dependencies,
    ) -> TrafficManagerRuntimeAttestor:
        match profile:
            case TrafficManagerBinaryProfile.DEFAULT:
                return cls(**dependencies)
            case TrafficManagerBinaryProfile.NATIVE_DIAGNOSTIC_DEV:
                return cls(
                    expected_sha256=DIAGNOSTIC_LIBCARLA_SHA256,
                    expected_path=DIAGNOSTIC_LIBCARLA_PATH,
                    **dependencies,
                )
            case unreachable:
                assert_never(unreachable)

    def verify_before_creation(self, port: int) -> PreparedRuntimeAttestation:
        library = self._library_path().resolve()
        if self._expected_path is not None and library != self._expected_path:
            raise LoadedLibcarlaPathMismatchError(self._expected_path, library)
        actual_hash = self._hash_file(library).upper()
        expected_hash = self._expected_sha256
        # The pilot wrapper selects this exact private artifact before import.
        # Retain the old/diagnostic profiles for their existing entrypoints.
        from src.p8.noa_tm import LIBRARY_PATH, PROJECT_LIBCARLA_SHA256

        if expected_hash == APPROVED_LIBCARLA_SHA256 and library == LIBRARY_PATH.resolve():
            expected_hash = PROJECT_LIBCARLA_SHA256.upper()
        if actual_hash != expected_hash:
            raise LoadedLibcarlaMismatchError(
                library,
                expected_hash,
                actual_hash,
            )
        owners = self._listener_pids(port)
        # Training, M1 and M2 run in one process and reuse its native TM.
        # A foreign listener would instead select an unverified Remote TM.
        if owners and owners != frozenset({self._process_id()}):
            raise ListenerOwnershipError(port, "has a listener before creation", owners)
        return PreparedRuntimeAttestation(
            port,
            self._process_id(),
            library,
            actual_hash,
        )

    def attest_after_creation(
        self,
        prepared: PreparedRuntimeAttestation,
    ) -> RuntimeAttestation:
        owners = self._listener_pids(prepared.port)
        expected = frozenset({prepared.owner_pid})
        if owners != expected:
            raise ListenerOwnershipError(
                prepared.port,
                "is not exclusively owned after creation",
                owners,
            )
        return RuntimeAttestation(
            prepared.port,
            prepared.owner_pid,
            prepared.libcarla_path,
            prepared.libcarla_sha256,
        )

    def verify_owned(self, attestation: RuntimeAttestation) -> None:
        owners = self._listener_pids(attestation.port)
        if owners != frozenset({attestation.owner_pid}):
            raise ListenerOwnershipError(
                attestation.port,
                "ownership lost",
                owners,
            )


def require_native_diagnostic_attestation(attestation: RuntimeAttestation) -> None:
    if (
        attestation.libcarla_path.resolve() != DIAGNOSTIC_LIBCARLA_PATH.resolve()
        or attestation.libcarla_sha256.upper() != DIAGNOSTIC_LIBCARLA_SHA256
    ):
        raise NativeDiagnosticAttestationError(attestation)


def windows_listener_pids(port: int) -> frozenset[int]:
    owners: set[int] = set()
    for family, row_size, port_offset, pid_offset in (
        (socket.AF_INET, 24, 8, 20),
        (socket.AF_INET6, 56, 20, 52),
    ):
        table = _tcp_table(family)
        count = struct.unpack_from("<I", table)[0]
        for index in range(count):
            offset = 4 + index * row_size
            encoded_port = struct.unpack_from("<I", table, offset + port_offset)[0]
            if socket.ntohs(encoded_port & 0xFFFF) == port:
                owners.add(struct.unpack_from("<I", table, offset + pid_offset)[0])
    return frozenset(owners)


def _tcp_table(family: int) -> bytes:
    iphlpapi = ctypes.WinDLL("iphlpapi")
    size = ctypes.c_ulong(0)
    result = iphlpapi.GetExtendedTcpTable(
        None,
        ctypes.byref(size),
        False,
        family,
        _TCP_TABLE_OWNER_PID_LISTENER,
        0,
    )
    if result != _ERROR_INSUFFICIENT_BUFFER:
        raise OSError(result, "GetExtendedTcpTable size lookup failed")
    buffer = ctypes.create_string_buffer(size.value)
    result = iphlpapi.GetExtendedTcpTable(
        buffer,
        ctypes.byref(size),
        False,
        family,
        _TCP_TABLE_OWNER_PID_LISTENER,
        0,
    )
    if result != _NO_ERROR:
        raise OSError(result, "GetExtendedTcpTable listener lookup failed")
    return buffer.raw[: size.value]


def _loaded_libcarla_path() -> Path:
    module = importlib.import_module("carla.libcarla")
    path = getattr(module, "__file__", None)
    if not isinstance(path, str):
        raise TypeError("loaded carla.libcarla has no filesystem path")
    return Path(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()
