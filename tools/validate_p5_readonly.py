# /// script
# requires-python = ">=3.12"
# dependencies = ["carla==0.9.16", "pygame==2.6.1", "numpy"]
# ///
# Laboratory interpreter: .venv/Scripts/python.exe -m tools.validate_p5_readonly
from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import carla

from src.experiment.automation import (
    AutomationAvailability,
    AutomationState,
    DrivingControlMode,
)
from src.experiment.automation_interaction_types import DriverInput
from src.experiment.exit_assistance import (
    ExitAssistanceConfig,
    ExitAssistanceCoordinator,
    ExitAssistanceEvent,
    ExitContext,
    ExitObservation,
)
from src.experiment.timestamp import HostClockTimestamp, TimestampEnvelope
from src.logging.research_event import ResearchEvent
from src.scenario.research_exit_ledger import reconstruct_exit_ledger
from src.scenario.research_noa_log_setup import open_research_log
from src.scenario.research_noa_persistence import build_segment
from src.vehicle.carla_exit_route import (
    load_exit_route_manifest,
    route_data_sha256,
    validate_exit_route_map,
)
from tests.test_p4_research_noa_integration import make_config
from tests.test_p5_boundary_contracts import ReferenceRecorder
from tools.validate_p3_p4_stationary import source_hashes


def editor_states() -> list[bool]:
    result: list[bool] = []
    user32 = ctypes.windll.user32
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.IsIconic.argtypes = [ctypes.c_void_p]

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(window, parameter):
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(window, title, len(title))
        if "CarlaUE4" in title.value:
            result.append(bool(user32.IsIconic(window)))
        return True

    user32.EnumWindows(visit, 0)
    return result


def synthetic_ledger(directory: Path) -> None:
    manifest = load_exit_route_manifest(Path("config/town04_exit_routes_dev_v3.json"))
    selected = manifest.routes[0]
    config = make_config(directory, "SYNTHETIC-P5-CONFIRM")
    persistence = config.persistence
    assert persistence is not None
    opened = open_research_log(persistence, lambda: 0, lambda: 0)
    segment = build_segment(persistence)

    def stamp(ns: int) -> TimestampEnvelope:
        return TimestampEnvelope(HostClockTimestamp(ns, ns))

    def record(event: ExitAssistanceEvent) -> None:
        opened.logger.write_event(segment, event.timestamp, ResearchEvent(event.event_type, event.payload_json))

    subject = ExitAssistanceCoordinator(
        ExitAssistanceConfig(config.automation_interaction.module, config.automation_interaction.condition,
                             ExitContext.MODULE, "synthetic-exit-1", selected, 1000.0, 5.0, 0.15),
        ReferenceRecorder(), record,
    )
    active = AutomationState(AutomationAvailability.AVAILABLE, DrivingControlMode.NOA_ACTIVE)
    observation = ExitObservation(stamp(100_000_000), selected.fork_distance_m - 1000.0, -3.5,
                                  39, 0, -3, None, None, target_boundary_valid=True)
    try:
        opened.logger.write_event(segment, stamp(0), ResearchEvent("validation_origin", "SYNTHETIC timeline and poses; NOT CARLA motion or physical input"))
        subject.observe(observation)
        prepared = subject.prepare_presentation(active)
        subject.mark_presented(prepared, active, stamp(200_000_000))
        subject.update(DriverInput(exit_confirm_requested=True), active, stamp(300_000_000), stamp(310_000_000))
        subject.observe(ExitObservation(stamp(400_000_000), selected.initiation_start_m + 10, -3.0,
                                        39, 0, -3, None, None, target_boundary_valid=True))
        subject.observe(ExitObservation(stamp(500_000_000), selected.initiation_start_m + 40, -1.7,
                                        39, 0, -4, None, None, target_boundary_valid=True))
        road, section, lane = selected.exit_segments[-1]
        subject.observe(ExitObservation(stamp(600_000_000), selected.points[-1].distance_m, 0.0,
                                        road, section, lane, None, None))
    finally:
        opened.logger.close()
    summary = reconstruct_exit_ledger(opened.paths.events)[0]
    assert summary.decision == "CONFIRM" and summary.outcome == "EXIT"
    assert summary.t0_host_monotonic_ns == 200_000_000
    assert summary.tL_host_monotonic_ns == 500_000_000
    print("SYNTHETIC_ledger", opened.paths.events, summary.decision, summary.outcome,
          "t0", summary.t0_host_monotonic_ns, "tL", summary.tL_host_monotonic_ns)


def main() -> None:
    root = Path.cwd()
    identity = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    directory = root / "data" / "p5_validation" / identity
    directory.mkdir(parents=True, exist_ok=False)
    print("evidence", directory, flush=True)
    hashes = source_hashes(root)
    with (directory / "source_manifest.json").open("x", encoding="utf-8") as destination:
        json.dump(hashes, destination, indent=2)
    with (directory / "stdout.txt").open("x", encoding="utf-8") as output, redirect_stdout(output):
        print("runtime", sys.version)
        print("HEAD", subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip())
        print("dirty", subprocess.check_output(["git", "status", "--porcelain", "--", "src", "tests", "tools", "config", "docs"], text=True))
        print("editor_minimized", editor_states())
        client = carla.Client("127.0.0.1", 2000)
        client.set_timeout(5.0)
        world = client.get_world()
        carla_map = world.get_map()
        print("map", carla_map.name, "settings", world.get_settings())
        print("vehicles", [(a.id, a.type_id) for a in world.get_actors().filter("vehicle.*")])
        start = world.get_snapshot()
        wall = time.perf_counter()
        time.sleep(2)
        end = world.get_snapshot()
        print("world_hz", (end.frame - start.frame) / (time.perf_counter() - wall))
        manifest = load_exit_route_manifest(root / "config/town04_exit_routes_dev_v3.json")

        class FootprintFixture:
            bounding_box = carla.BoundingBox(carla.Location(), carla.Vector3D(2.5, 0.95, 0.8))

        print("footprint=1.90m fixture, not an actor spawn; no control writes")
        print("route_hash", route_data_sha256(manifest.routes))
        for selected in manifest.routes:
            validate_exit_route_map(selected, carla_map, FootprintFixture())
            print("validated", selected.route_id, "fork", selected.fork_distance_m,
                  "maneuver", selected.initiation_start_m, selected.initiation_end_m,
                  "continuation_last", selected.points[-1])
        print("ACTIVE_LIVE=NOT_RUN; no actor/window/world settings modified")
        synthetic_ledger(directory)
        print("source_unchanged", hashes == source_hashes(root))


if __name__ == "__main__":
    main()
