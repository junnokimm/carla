"""Frame-bound visibility evidence. Geometry or callback arrival alone is insufficient.

No pixel/mesh visibility detector is supplied. A future validated detector must
identify the exact rendered sensor/frame. P8 currently supports Offline injection
only; even a matched verdict is a display candidate, not a research-valid onset.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from src.p7.types import P7Error
from src.scenario.camera_feed import DisplayedCameraFrame


@dataclass(frozen=True, slots=True)
class HazardFrameVisibility:
    sensor_actor_id: int
    frame: int
    visible: bool
    evidence_source: str

    def __post_init__(self) -> None:
        if (
            type(self.sensor_actor_id) is not int
            or self.sensor_actor_id <= 0
            or type(self.frame) is not int
            or self.frame < 0
            or type(self.visible) is not bool
            or not isinstance(self.evidence_source, str)
            or not self.evidence_source.strip()
        ):
            raise P7Error("INVALID_HAZARD_FRAME_VISIBILITY")


@runtime_checkable
class HazardDisplayBackend(Protocol):
    def visibility_for_frame(
        self, frame: DisplayedCameraFrame
    ) -> HazardFrameVisibility | None:
        """Consult already computed frame evidence; no new CARLA query here."""
        ...
