from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from src.p8.tm_runtime_attestation import (
    APPROVED_LIBCARLA_SHA256,
    ListenerOwnershipError,
    LoadedLibcarlaMismatchError,
    RuntimeAttestation,
    TrafficManagerRuntimeAttestor,
    windows_listener_pids,
)


def test_attestor_rejects_loaded_libcarla_hash_mismatch(tmp_path: Path) -> None:
    library = tmp_path / "libcarla.cp312-win_amd64.pyd"
    library.write_bytes(b"not-the-approved-extension")
    attestor = TrafficManagerRuntimeAttestor(
        library_path=lambda: library,
        listener_pids=lambda port: frozenset(),
        process_id=lambda: 41,
    )

    with pytest.raises(LoadedLibcarlaMismatchError) as raised:
        attestor.verify_before_creation(8123)

    assert raised.value.expected_sha256 == APPROVED_LIBCARLA_SHA256


def test_attestor_rejects_foreign_listener_before_tm_creation(tmp_path: Path) -> None:
    library = tmp_path / "libcarla.cp312-win_amd64.pyd"
    library.write_bytes(b"approved")
    attestor = TrafficManagerRuntimeAttestor(
        expected_sha256="D5C4",
        hash_file=lambda path: "D5C4",
        library_path=lambda: library,
        listener_pids=lambda port: frozenset({99}),
        process_id=lambda: 41,
    )

    with pytest.raises(ListenerOwnershipError, match="before creation"):
        attestor.verify_before_creation(8123)


def test_attestor_requires_exclusive_current_process_listener_after_creation(
    tmp_path: Path,
) -> None:
    library = tmp_path / "libcarla.cp312-win_amd64.pyd"
    library.write_bytes(b"approved")
    listeners = iter((frozenset(), frozenset({41}), frozenset({41})))
    attestor = TrafficManagerRuntimeAttestor(
        expected_sha256="D5C4",
        hash_file=lambda path: "D5C4",
        library_path=lambda: library,
        listener_pids=lambda port: next(listeners),
        process_id=lambda: 41,
    )

    prepared = attestor.verify_before_creation(8123)
    attestation = attestor.attest_after_creation(prepared)
    attestor.verify_owned(attestation)

    assert attestation == RuntimeAttestation(
        port=8123,
        owner_pid=41,
        libcarla_path=library.resolve(),
        libcarla_sha256="D5C4",
    )


def test_attestor_fails_closed_when_listener_ownership_is_lost(
    tmp_path: Path,
) -> None:
    library = tmp_path / "libcarla.cp312-win_amd64.pyd"
    library.write_bytes(b"approved")
    listeners = iter((frozenset(), frozenset({41}), frozenset({42})))
    attestor = TrafficManagerRuntimeAttestor(
        expected_sha256="D5C4",
        hash_file=lambda path: "D5C4",
        library_path=lambda: library,
        listener_pids=lambda port: next(listeners),
        process_id=lambda: 41,
    )
    attestation = attestor.attest_after_creation(
        attestor.verify_before_creation(8123)
    )

    with pytest.raises(ListenerOwnershipError, match="ownership lost"):
        attestor.verify_owned(attestation)


def test_attestor_reuses_local_listener_across_p8_stages(tmp_path: Path) -> None:
    attestor = TrafficManagerRuntimeAttestor(
        expected_sha256="D5C4",
        hash_file=lambda path: "D5C4",
        library_path=lambda: tmp_path / "libcarla.pyd",
        listener_pids=lambda port: frozenset({41}),
        process_id=lambda: 41,
    )
    prepared = attestor.verify_before_creation(8123)
    assert attestor.attest_after_creation(prepared).owner_pid == 41


@pytest.mark.parametrize(
    "family,address", [(socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")]
)
def test_real_windows_listener_pid_without_carla(family: int, address: str) -> None:
    with socket.socket(family, socket.SOCK_STREAM) as listener:
        listener.bind((address, 0))
        listener.listen(1)
        assert windows_listener_pids(listener.getsockname()[1]) == frozenset({os.getpid()})
