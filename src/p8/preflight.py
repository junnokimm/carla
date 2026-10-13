from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from src.p7.types import Json
from src.p8.readiness import load_readiness


def carla_probe(host: str, port: int) -> dict[str, Json]:
    import carla

    client = carla.Client(host, port)
    client.set_timeout(2)
    world = client.get_world()
    library = world.get_blueprint_library()
    required = ("vehicle.mercedes.coupe_2020", "static.prop.box01", "sensor.camera.rgb", "sensor.other.collision")
    return {"status": "CONNECTED_READ_ONLY", "map": world.get_map().name,
            "assets": {name: bool(library.filter(name)) for name in required},
            "synchronous_mode": world.get_settings().synchronous_mode,
            "actors_spawned": False, "controls_changed": False}


def inspect(directory: Path, *, readiness_file: Path, tobii_python: Path,
            host: str, port: int, offline: bool) -> dict[str, Json]:
    readiness, history = load_readiness(readiness_file)
    directory.mkdir(parents=True, exist_ok=False)
    checks = {"carla": {"status": "NOT_CHECKED_OFFLINE"},
              "tobii_worker": {"status": "NOT_CHECKED_OFFLINE"},
              "tobii_device": "NOT_PROBED_NO_HARDWARE_EXECUTION",
              "tablet": "NOT_HARDWARE_VALIDATED", "output_directory": "EXCLUSIVELY_CREATED"}
    if not offline:
        commands = {
            "carla": [sys.executable, "-B", "-m", "src.p8.preflight", "--probe-carla", "--host", host, "--port", str(port)],
            "tobii_worker": [str(tobii_python.resolve()), "-B", "-c",
                ("import json,sys,struct; from importlib.metadata import version; "
                 "print(json.dumps({'python':list(sys.version_info[:2]),'bits':struct.calcsize('P')*8,'sdk':version('tobii-research')}))")],
        }
        for key, command in commands.items():
            try:
                result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                        timeout=10, check=False, cwd=Path(__file__).resolve().parents[2])
                if result.returncode:
                    checks[key] = {"status": "UNAVAILABLE", "returncode": result.returncode,
                                   "detail": result.stderr.strip() or result.stdout.strip()}
                else:
                    lines = result.stdout.splitlines()
                    records = [line for line in lines if line.lstrip().startswith("{")]
                    if not records:
                        raise ValueError("probe returned no JSON record")
                    checks[key] = json.loads(records[-1])
                    if not isinstance(checks[key], dict):
                        raise ValueError("probe must return an object")
                    checks[key]["diagnostics"] = [line for line in lines if line != records[-1]]
                    if key == "tobii_worker":
                        supported = (checks[key]["python"] == [3, 10] and checks[key]["bits"] == 64
                                     and checks[key]["sdk"] == "2.1.0")
                        checks[key]["status"] = "WORKER_PACKAGE_AVAILABLE_NOT_DEVICE_VALIDATION" if supported else "UNSUPPORTED_WORKER"
                    elif checks[key].get("status") != "CONNECTED_READ_ONLY" or "assets" not in checks[key]:
                        raise ValueError("invalid CARLA probe response")
            except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError) as error:
                checks[key] = {"status": "UNAVAILABLE", "detail": str(error)}
    report = {"status": "P8_PARTICIPANT_BLOCKED", "participant_ready": False,
              "participant_run": False, "hardware_validated": False, "readiness": asdict(readiness),
              "reported_history": history, "checks": checks,
              "blockers": [*readiness.blockers(), "PARTICIPANT_RUNTIME_NOT_IMPLEMENTED"],
              "no_automatic_readiness_promotion": True}
    with (directory / "preflight.json").open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe-carla", action="store_true", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2000)
    args = parser.parse_args()
    try:
        print(json.dumps(carla_probe(args.host, args.port)))
    except (ImportError, RuntimeError, OSError) as error:
        print(json.dumps({"status": "UNAVAILABLE", "detail": str(error)}))
        raise SystemExit(2) from error
