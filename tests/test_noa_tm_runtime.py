from __future__ import annotations

import hashlib
import json

import pytest

from src.p8 import noa_tm
from src.p8.tm_runtime_attestation import (
    APPROVED_LIBCARLA_SHA256,
    LoadedLibcarlaMismatchError,
    TrafficManagerRuntimeAttestor,
)


def test_project_artifact_matches_pinned_build() -> None:
    manifest = json.loads((noa_tm.RUNTIME_DIRECTORY / "manifest.json").read_text())
    assert (
        hashlib.sha256(noa_tm.LIBRARY_PATH.read_bytes()).hexdigest()
        == noa_tm.PROJECT_LIBCARLA_SHA256
    )
    assert manifest["libcarla_sha256"] == noa_tm.PROJECT_LIBCARLA_SHA256
    assert (
        hashlib.sha256(
            (noa_tm.RUNTIME_DIRECTORY / "lane_change.patch").read_bytes()
        ).hexdigest()
        == manifest["patch_sha256"]
    )


def test_project_artifact_is_attested_without_loosening_original_profile() -> None:
    attestor = TrafficManagerRuntimeAttestor(
        library_path=lambda: noa_tm.LIBRARY_PATH,
        listener_pids=lambda _port: frozenset(),
        process_id=lambda: 41,
    )
    attested = attestor.verify_before_creation(8123)
    assert attested.libcarla_sha256 == noa_tm.PROJECT_LIBCARLA_SHA256.upper()
    assert attested.libcarla_path == noa_tm.LIBRARY_PATH
    wrong = TrafficManagerRuntimeAttestor(
        library_path=lambda: noa_tm.LIBRARY_PATH,
        hash_file=lambda _path: APPROVED_LIBCARLA_SHA256,
        listener_pids=lambda _port: frozenset(),
        process_id=lambda: 41,
    )
    with pytest.raises(LoadedLibcarlaMismatchError):
        wrong.verify_before_creation(8123)


def test_bootstrap_rejects_corrupt_binary(tmp_path, monkeypatch) -> None:
    library = tmp_path / "corrupt.pyd"
    library.write_bytes(b"corrupt")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "libcarla_sha256": noa_tm.PROJECT_LIBCARLA_SHA256,
            }
        )
    )
    monkeypatch.setattr(noa_tm, "RUNTIME_DIRECTORY", tmp_path)
    monkeypatch.setattr(noa_tm, "LIBRARY_PATH", library)
    with pytest.raises(RuntimeError, match="hash mismatch"):
        noa_tm.enable_lane_change_runtime()
