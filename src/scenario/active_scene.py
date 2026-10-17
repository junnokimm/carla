from typing import Protocol

from src.experiment.automation_interaction import DriverInput
from src.scenario.camera_feed import DisplayedCameraFrame


class ActiveDriveScene(Protocol):
    """An internal scene never owns duration, driver control or viewer lifetime."""

    started_at: float

    def after_control(
        self, driver_input: DriverInput, poll_started_ns: int, poll_completed_ns: int
    ) -> None: ...

    def after_display_completed(self, frame: DisplayedCameraFrame | None) -> None: ...

    def finalize(self, reason: str, error: BaseException | None = None) -> None: ...
