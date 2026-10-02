from __future__ import annotations

from collections.abc import Sequence

from src.scenario.research_noa_config import (
    ResearchNoARunConfig,
    ResearchNoARunMode,
    parse_arguments,
)
from src.scenario.research_noa_runtime import (
    ResearchNoACleanupError,
    ResearchNoADryRunActivationError,
    ResearchNoAPreflightError,
    ResearchNoARunner,
    ResearchNoASession,
)
from src.scenario.research_noa_smoke import (
    ResearchNoAActualSpeedSafetyError,
    ResearchSmokeReport,
)
from src.scenario.research_noa_view import (
    ResearchDriverView,
    ResearchLiveDriverView,
)

__all__ = [
    "ResearchDriverView",
    "ResearchLiveDriverView",
    "ResearchNoAActualSpeedSafetyError",
    "ResearchNoACleanupError",
    "ResearchNoADryRunActivationError",
    "ResearchNoAPreflightError",
    "ResearchNoARunConfig",
    "ResearchNoARunMode",
    "ResearchNoARunner",
    "ResearchNoASession",
    "ResearchSmokeReport",
    "main",
    "parse_arguments",
]


def main(argv: Sequence[str] | None = None) -> int:
    config = parse_arguments(argv)
    report = ResearchNoARunner(config).run()
    if report is None:
        print("mode=dry-run\ncustom_control_activation=0\ncustom_control_apply=0")
    else:
        print(report.format())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
