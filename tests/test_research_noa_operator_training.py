from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import pygame
import pytest

from src.experiment.context import (
    ExperimentModule,
    ExperimentPhase,
    Module1Condition,
)
from src.p6.config import P6Config
from src.scenario.research_exit_config import ResearchExitAssistanceConfig
from src.scenario.research_noa import ResearchNoARunner
from src.scenario.research_noa_config import (
    ResearchNoAConfigError,
    ResearchNoARunMode,
)
from src.scenario.research_noa_speed_policy import ResearchSpeedPolicy
from tests.research_noa_fakes import (
    FakeClient,
    FakeDriverViewFactory,
    FakeResearchVehicle,
    FakeResearchWorld,
)
from tests.test_driver_view import FakeHero, FakeWorld
from tests.test_driver_view_runtime import FakeFrameClock, FakeTurnSignalAudio
from tests.test_p4_research_noa_integration import make_config


def training_config(output_dir: Path, *, duration: float = 1.0):
    config = make_config(output_dir, "training-run")
    assert config.persistence is not None
    assert config.automation_interaction is not None
    return replace(
        config,
        duration=duration,
        operator_training=True,
        persistence=replace(
            config.persistence,
            stage_label="TRAINING",
            segment_phase=ExperimentPhase.TRAINING,
        ),
        automation_interaction=replace(
            config.automation_interaction,
            initial_stage="TRAINING",
        ),
    )


def test_operator_training_accepts_continuous_duration_through_600_seconds(
    tmp_path: Path,
) -> None:
    config = training_config(tmp_path, duration=600.0)

    assert config.operator_training is True
    assert config.duration == 600.0


def test_operator_training_rejects_duration_above_600_seconds(tmp_path: Path) -> None:
    with pytest.raises(ResearchNoAConfigError, match="duration.*600.0"):
        training_config(tmp_path, duration=600.001)


@pytest.mark.parametrize(
    "invalid_update",
    [
        {"persistence": None},
        {"mode": ResearchNoARunMode.DRY_RUN},
        {"exit_assistance": ResearchExitAssistanceConfig(Path("route.json"), "r", "e")},
        {"p6": P6Config(schema_version=1, source_mode="synthetic")},
        {"development_validation": True},
    ],
)
def test_operator_training_rejects_non_training_runtime_features(
    tmp_path: Path,
    invalid_update,
) -> None:
    with pytest.raises(ResearchNoAConfigError, match="operator_training"):
        replace(training_config(tmp_path), **invalid_update)


def test_operator_training_accepts_explicit_speed_policy_without_changing_caps(
    tmp_path: Path,
) -> None:
    policy = ResearchSpeedPolicy(35.0, True)
    config = replace(training_config(tmp_path, duration=600.0), speed_policy=policy)

    assert config.speed_policy is policy
    assert config.effective_speed_policy.profile == "HIGH_SPEED_DEV_UNVALIDATED"
    assert config.effective_speed_policy.operational_guard_speed_kmh == 35.0
    assert config.effective_speed_policy.max_allowed_speed_kmh == 110.0
    with pytest.raises(ResearchNoAConfigError, match="duration.*600.0"):
        replace(config, duration=600.001)
    with pytest.raises(ResearchNoAConfigError, match="operator_training"):
        replace(config, p6=P6Config(1, "synthetic"))


def test_operator_training_requires_training_phase_module_and_stage(
    tmp_path: Path,
) -> None:
    config = training_config(tmp_path)
    assert config.persistence is not None
    assert config.automation_interaction is not None

    invalid_configs = (
        {"persistence": replace(config.persistence, segment_phase=None)},
        {
            "persistence": replace(
                config.persistence,
                module=ExperimentModule.MODULE_2,
            )
        },
        {
            "automation_interaction": replace(
                config.automation_interaction,
                initial_stage="MODULE_1",
            )
        },
        {
            "automation_interaction": replace(
                config.automation_interaction,
                condition=Module1Condition.SURT,
            )
        },
    )

    for invalid_update in invalid_configs:
        with pytest.raises(ResearchNoAConfigError, match="operator_training"):
            replace(config, **invalid_update)


@pytest.mark.parametrize(
    ("training_enabled", "events", "expected_user_exit", "expected_early"),
    [
        (True, (pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F8),), False, True),
        (
            True,
            (
                pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F8),
                pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE),
            ),
            True,
            False,
        ),
        (
            True,
            (
                pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F8),
                pygame.event.Event(pygame.QUIT),
            ),
            True,
            False,
        ),
        (False, (pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F8),), False, False),
        (True, (), False, False),
    ],
)
def test_driver_view_f8_is_training_only_and_abort_has_priority(
    monkeypatch,
    training_enabled: bool,
    events: tuple[pygame.event.Event, ...],
    expected_user_exit: bool,
    expected_early: bool,
) -> None:
    from src.scenario import driver_view

    event_batches = iter((list(events), []))
    times = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(driver_view, "TurnSignalAudio", FakeTurnSignalAudio)
    monkeypatch.setattr(driver_view.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(driver_view.pygame.display, "init", lambda: None)
    monkeypatch.setattr(driver_view.pygame.display, "set_mode", lambda size: None)
    monkeypatch.setattr(driver_view.pygame.display, "set_caption", lambda title: None)
    monkeypatch.setattr(driver_view.pygame.display, "flip", lambda: None)
    monkeypatch.setattr(driver_view.pygame.event, "get", lambda: next(event_batches))
    monkeypatch.setattr(driver_view.pygame.time, "Clock", FakeFrameClock)
    monkeypatch.setattr(driver_view.pygame, "quit", lambda: None)
    monkeypatch.setattr(
        driver_view.pygame.key,
        "get_pressed",
        lambda: defaultdict(bool),
    )

    viewer = driver_view.DriverView(
        FakeWorld(),
        FakeHero(),
        training_early_finish=training_enabled,
    )
    monkeypatch.setattr(viewer, "_update_turn_signal_audio", lambda: None)
    monkeypatch.setattr(viewer, "_draw", lambda screen: None)

    user_exited = viewer.run(0.5)

    assert user_exited is expected_user_exit
    assert viewer.training_completed_early is expected_early


def test_runner_propagates_training_early_finish_after_shutdown_before_cleanup(
    tmp_path: Path,
) -> None:
    vehicle = FakeResearchVehicle()
    world = FakeResearchWorld(vehicle)
    viewer_factory = FakeDriverViewFactory()
    runner = ResearchNoARunner(
        training_config(tmp_path),
        client=FakeClient(world),
        viewer_factory=viewer_factory,
    )

    with runner.session() as session:
        viewer = viewer_factory.created[0]
        viewer.training_completed_early = True

        report = session.run()

        assert report is not None
        assert report.user_exited is False
        assert report.training_completed_early is True
        assert viewer.config.training_early_finish is True
        assert vehicle.applied_controls[-1].brake == pytest.approx(0.5)
        assert viewer.close_count == 0

    assert viewer.close_count == 1
    assert vehicle.destroy_count == 1
    assert runner.log_paths is not None
    with runner.log_paths.events.open(newline="", encoding="utf-8") as event_file:
        stage_end = [
            row
            for row in csv.DictReader(event_file)
            if row["event_type"] == "stage_end"
        ][-1]
    assert stage_end["event_value"].endswith(":TRAINING_COMPLETED_EARLY")
