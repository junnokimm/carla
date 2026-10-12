from __future__ import annotations

from dataclasses import replace
from math import inf, nan
from pathlib import Path

import pytest

from src.p6.config import P6Config
from src.p8.live_drive import LiveDriveOptions, build_live_drive_config
from src.p8.model import P8Error
from src.scenario.research_noa_config import (
    ResearchNoAConfigError,
    ResearchSpeedPolicy,
)
from src.scenario.research_noa_speed_policy import RESEARCH_MAX_ALLOWED_SPEED_KMH
from tests.test_p8_live_drive import _services


def test_legacy_p8_policy_keeps_ten_as_desired_and_twenty_as_guard(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "legacy-speed-policy")

    config = build_live_drive_config(
        services,
        LiveDriveOptions(duration=1.0, spawn_index=0),
    )

    assert config.control_config.longitudinal.target_speed_kmh == pytest.approx(10.0)
    assert config.effective_speed_policy.desired_speed_kmh == pytest.approx(10.0)
    assert config.effective_speed_policy.effective_desired_speed_kmh == pytest.approx(
        10.0
    )
    assert config.effective_speed_policy.max_allowed_speed_kmh == pytest.approx(
        RESEARCH_MAX_ALLOWED_SPEED_KMH
    )
    assert config.effective_speed_policy.operational_guard_speed_kmh == pytest.approx(
        20.0
    )
    assert config.control_config.longitudinal.max_throttle == pytest.approx(0.4)
    services.close()


def test_explicit_high_dev_policy_allows_low_desired_with_operator_headroom(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "manual-acceleration-policy")

    config = build_live_drive_config(
        services,
        LiveDriveOptions(
            duration=1.0,
            spawn_index=0,
            noa_desired_speed_kmh=10.0,
            speed_policy=ResearchSpeedPolicy(
                operational_guard_speed_kmh=30.0,
                high_speed_dev_acknowledged=True,
            ),
            noa_max_throttle=1.0,
        ),
    )

    assert config.effective_speed_policy.desired_speed_kmh == pytest.approx(10.0)
    assert config.effective_speed_policy.operational_guard_speed_kmh == pytest.approx(
        30.0
    )
    assert config.effective_speed_policy.profile == "HIGH_SPEED_DEV_UNVALIDATED"
    assert config.control_config.longitudinal.max_throttle == pytest.approx(1.0)
    services.close()


@pytest.mark.parametrize("max_throttle", [0.0, 1.01, inf, nan])
def test_live_options_reject_invalid_noa_throttle_cap(max_throttle: float) -> None:
    with pytest.raises(P8Error, match="NOA_MAX_THROTTLE"):
        LiveDriveOptions(
            duration=1.0,
            spawn_index=0,
            noa_max_throttle=max_throttle,
        )


def test_noa_throttle_above_legacy_cap_requires_acknowledged_high_policy() -> None:
    with pytest.raises(P8Error, match="HIGH_SPEED_POLICY"):
        LiveDriveOptions(
            duration=1.0,
            spawn_index=0,
            noa_max_throttle=0.5,
        )


@pytest.mark.parametrize("desired_speed_kmh", [110.01, inf, nan])
def test_live_config_rejects_desired_speed_above_policy_max_or_nonfinite(
    tmp_path: Path,
    desired_speed_kmh: float,
) -> None:
    services = _services(tmp_path, "invalid-desired")
    base = build_live_drive_config(
        services,
        LiveDriveOptions(duration=1.0, spawn_index=0),
    )
    with pytest.raises((ResearchNoAConfigError, ValueError), match="target_speed_kmh"):
        longitudinal = replace(
            base.control_config.longitudinal,
            target_speed_kmh=desired_speed_kmh,
        )
        replace(
            base,
            control_config=replace(base.control_config, longitudinal=longitudinal),
        )
    services.close()


def test_high_dev_policy_requires_desired_below_guard_below_max(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, "invalid-headroom")
    base = build_live_drive_config(
        services,
        LiveDriveOptions(duration=1.0, spawn_index=0),
    )

    with pytest.raises(ResearchNoAConfigError, match="operational_guard_speed_kmh"):
        replace(
            base,
            speed_policy=ResearchSpeedPolicy(
                operational_guard_speed_kmh=25.0,
                high_speed_dev_acknowledged=True,
            ),
            control_config=replace(
                base.control_config,
                longitudinal=replace(
                    base.control_config.longitudinal,
                    target_speed_kmh=25.0,
                ),
            ),
        )
    services.close()


def test_high_dev_policy_rejects_p6_runtime_binding(tmp_path: Path) -> None:
    services = _services(tmp_path, "high-p6-rejected")
    high = build_live_drive_config(
        services,
        LiveDriveOptions(
            duration=1.0,
            spawn_index=0,
            noa_desired_speed_kmh=25.0,
            speed_policy=ResearchSpeedPolicy(35.0, True),
            noa_max_throttle=1.0,
        ),
    )

    with pytest.raises(ResearchNoAConfigError, match="P5, P6"):
        replace(high, p6=P6Config(1, "synthetic"))
    services.close()
