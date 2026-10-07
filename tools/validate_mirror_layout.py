# /// script
# requires-python = ">=3.12"
# dependencies = ["carla==0.9.16", "pygame==2.6.1", "numpy"]
# ///
# Laboratory interpreter: .venv/Scripts/python.exe -m tools.validate_mirror_layout
from __future__ import annotations

import json
import time
from contextlib import redirect_stdout
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import carla
import pygame

from src.experiment.assignment import ParticipantAssignment
from src.experiment.automation_interaction_types import (
    AutomationInteractionConfig,
    DriverInput,
)
from src.experiment.context import (
    ExperimentModule,
    Module1Condition,
    Module2Condition,
    ParticipantId,
    StudyRunId,
)
from src.experiment.lateral_control import LateralControlConfig
from src.experiment.longitudinal_control import LongitudinalControlConfig
from src.experiment.noa_runtime import NoAControlConfig
from src.scenario.driver_view import calculate_layout
from src.scenario.research_noa_config import (
    ResearchAutomationInteractionConfig,
    ResearchNoARunConfig,
    ResearchNoARunMode,
)
from src.scenario.research_noa_persistence_config import ResearchPersistenceConfig
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.scenario.research_noa_smoke import observe_live_smoke_speed_kmh
from src.scenario.research_noa_transmission import (
    TRANSMISSION_PRIME_STATIONARY_THRESHOLD_KMH,
)
from src.scenario.research_noa_view import (
    ResearchDriverViewConfig,
    ResearchLiveDriverView,
)
from tools.validate_p3_p4_stationary import source_hashes
from tools.validate_p5_readonly import editor_states


class CapturedMirrorView(ResearchLiveDriverView):
    def __init__(self, world: carla.World, hero: carla.Actor, config: ResearchDriverViewConfig, directory: Path) -> None:
        super().__init__(world, hero, config)
        self._capture_directory = directory
        self._capture_started: float | None = None
        self._captured = False

    def _begin_iteration(self) -> None:
        super()._begin_iteration()
        if self._capture_started is None:
            self._capture_started = time.perf_counter()
            pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_v))

    def _after_display_flip(self) -> None:
        super()._after_display_flip()
        if self._captured or self._capture_started is None:
            return
        if time.perf_counter() - self._capture_started < 3.0:
            return
        if not all(feed.snapshot().image is not None for feed in self.feeds):
            return
        screen = pygame.display.get_surface()
        pygame.image.save(screen, str(self._capture_directory / "cockpit.png"))
        for feed in self.feeds:
            image = feed.snapshot().image
            if image is not None:
                surface = pygame.image.frombuffer(image.raw_data, (image.width, image.height), "BGRA")
                if feed.role != "front":
                    surface = pygame.transform.flip(surface, True, False)
                pygame.image.save(surface, str(self._capture_directory / f"{feed.role}_native.png"))
        self._captured = True
        print("screenshot_captured", [feed.role for feed in self.feeds])


def main() -> None:
    root = Path.cwd()
    batch = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
    directory = root / "data" / "mirror_validation" / batch
    directory.mkdir(parents=True, exist_ok=False)
    print("evidence", directory, flush=True)
    hashes = source_hashes(root)
    with (directory / "source_manifest.json").open("x", encoding="utf-8") as output:
        json.dump(hashes, output, indent=2)
    client = carla.Client("127.0.0.1", 2000)
    client.set_timeout(5.0)
    world = client.get_world()
    with (directory / "environment.txt").open("x", encoding="utf-8") as output, redirect_stdout(output):
        states = editor_states()
        print("editor_minimized", states)
        print("map", world.get_map().name, "settings", world.get_settings())
        vehicles = list(world.get_actors().filter("vehicle.*"))
        print("vehicles", [vehicle.id for vehicle in vehicles])
        print("quality=UNKNOWN; no window/engine/world setting changes")
        if states != [False] or vehicles:
            print("BLOCKED: editor readiness or existing vehicle")
            return
    controls = NoAControlConfig(
        LongitudinalControlConfig(10, 0.2, 0.1, 0.1, 0.4, 0.5, 0.02),
        LateralControlConfig(0.2, 0.5, 0.1, 0.05, 0.15),
    )
    assignment = ParticipantAssignment(ParticipantId("DEV-MIRROR"), Module1Condition.NO_SURT,
                                       Module2Condition.MANUAL, "Town04-spawn107-stationary",
                                       "mirror-development", "candidate-only", "HEAD-dirty-source-manifest")
    for label, front_only in (("front_a", True), ("full", False), ("front_b", True)):
        run_directory = directory / label
        run_directory.mkdir()
        run_id = f"mirror-{batch}-{label}"
        config = ResearchNoARunConfig(
            duration=8.0, control_config=controls, mode=ResearchNoARunMode.LIVE_SMOKE,
            spawn_index=107, front_camera_only=front_only, camera_diagnostics=True,
            run_id=run_id,
            automation_interaction=ResearchAutomationInteractionConfig(
                ExperimentModule.MODULE_2, Module2Condition.MANUAL,
                AutomationInteractionConfig(0.2, 0.1, 0.05, True),
            ),
            persistence=ResearchPersistenceConfig(assignment, StudyRunId(run_id), run_directory,
                                                  ExperimentModule.MODULE_2, "development/mirror-stationary"),
        )

        def viewer_factory(world: carla.World, hero: carla.Actor, view_config: ResearchDriverViewConfig, capture_directory: Path = run_directory) -> CapturedMirrorView:
            print("layout", asdict(calculate_layout(view_config)))
            print("view_config", view_config)
            return CapturedMirrorView(world, hero, view_config, capture_directory)

        with (run_directory / "stdout.txt").open("x", encoding="utf-8") as output, redirect_stdout(output):
            print("SCRIPTED constant brake 0.5, normal V cockpit toggle; no P5 or automatic driving")
            runner = ResearchNoARunner(config, client=client, viewer_factory=viewer_factory,
                                      driver_input_source=lambda: DriverInput(brake=0.5))
            with runner.session() as session:
                deadline = time.monotonic() + config.timeout
                while observe_live_smoke_speed_kmh(session.hero) > TRANSMISSION_PRIME_STATIONARY_THRESHOLD_KMH:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0.0:
                        raise SystemExit("Passive spawn settling did not reach the existing prime threshold")
                    session.world.wait_for_tick(min(remaining, 1.0))
                report = session.run()
            if report is not None:
                print(report.format())
        print("completed", label, flush=True)
    with (directory / "source_unchanged.txt").open("x", encoding="utf-8") as output:
        print(hashes == source_hashes(root), file=output)


if __name__ == "__main__":
    main()
