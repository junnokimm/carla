"""Select the project-private CARLA client before its first import."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

RUNTIME_DIRECTORY = Path(__file__).resolve().parents[2] / "native/noa_tm"
LIBRARY_PATH = RUNTIME_DIRECTORY / "carla/libcarla.cp312-win_amd64.pyd"
PROJECT_LIBCARLA_SHA256 = (
    "2af89add3e333ffcca316a5d99ece6313677c14fee3af35f89bed41a353a59e3"
)


def enable_lane_change_runtime() -> dict[str, str]:
    manifest = json.loads(
        (RUNTIME_DIRECTORY / "manifest.json").read_text(encoding="utf8")
    )
    expected = manifest["libcarla_sha256"]
    digest = hashlib.sha256(LIBRARY_PATH.read_bytes()).hexdigest()
    if digest != expected or digest != PROJECT_LIBCARLA_SHA256:
        raise RuntimeError("Project NoA CARLA client hash mismatch")
    loaded = sys.modules.get("carla.libcarla")
    if loaded is not None and Path(loaded.__file__).resolve() != LIBRARY_PATH.resolve():
        raise RuntimeError("CARLA was imported before the project lane-change runtime")
    directory = str(RUNTIME_DIRECTORY)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    return {
        "status": "NOA_LANE_CHANGE_RUNTIME_ENABLED",
        "sha256": digest,
        "path": str(LIBRARY_PATH),
        "rpc_receive_guard": manifest.get("version", "UNKNOWN"),
    }
