from __future__ import annotations

from collections.abc import Sequence

from src.scenario.research_noa_cleanup import ResearchNoACleanupError
from src.scenario.research_noa_config import (
    ResearchAutomationInteractionConfig,
    ResearchNoARunConfig,
    ResearchNoARunMode,
    parse_arguments,
)
from src.scenario.research_noa_report import ResearchSmokeReport
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.scenario.research_noa_session import (
    ResearchNoADryRunActivationError,
    ResearchNoAPreflightError,
    ResearchNoASession,
)
from src.scenario.research_noa_smoke import ResearchNoAActualSpeedSafetyError
from src.scenario.research_noa_transmission import ResearchNoATransmissionPrimeError
from src.scenario.research_noa_view import (
    ResearchDriverView,
    ResearchDriverViewConfig,
    ResearchLiveDriverView,
)

__all__ = [
    "ResearchAutomationInteractionConfig",
    "ResearchDriverView",
    "ResearchDriverViewConfig",
    "ResearchLiveDriverView",
    "ResearchNoAActualSpeedSafetyError",
    "ResearchNoACleanupError",
    "ResearchNoADryRunActivationError",
    "ResearchNoAPreflightError",
    "ResearchNoARunConfig",
    "ResearchNoARunMode",
    "ResearchNoARunner",
    "ResearchNoASession",
    "ResearchNoATransmissionPrimeError",
    "ResearchSmokeReport",
    "main",
    "parse_arguments",
]


def main(argv: Sequence[str] | None = None) -> int:
    config = parse_arguments(argv)
    runner = ResearchNoARunner(config)
    report = runner.run()
    if report is None:
        print("mode=dry-run\ncustom_control_activation=0\ncustom_control_apply=0")
    else:
        print(report.format())
    if runner.log_paths is not None:
        print(f"research_telemetry_path={runner.log_paths.telemetry}")
        print(f"research_events_path={runner.log_paths.events}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
