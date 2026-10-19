"""Bridge from existing persisted snapshots. No CARLA methods are called here."""

from __future__ import annotations

from collections.abc import Callable
from time import monotonic
from typing import Any

from src.scenario.vehicle_audio_model import EngineSoundModel, VehicleAudioState
from src.scenario.vehicle_audio_playback import VehicleAudioPlayback


class CachedVehicleAudioSource:
    def __init__(
        self, persistence: Any, actor_id: int, mode: Callable[[], str]
    ) -> None:
        self.persistence = persistence
        self.actor_id = str(actor_id)
        self.mode = mode
        self._frame: tuple[str, int] | None = None
        self._observed_at = 0.0

    def __call__(self) -> VehicleAudioState | None:
        sample = self.persistence.source.last_snapshot_sample
        if sample is None or sample.timestamp.carla_snapshot is None:
            return None
        timestamp = sample.timestamp.carla_snapshot
        stage_id = str(self.persistence.segment.segment_id)
        frame = (stage_id, timestamp.frame)
        if self._frame != frame:
            self._frame = frame
            self._observed_at = timestamp.host_capture_completed_monotonic_ns / 1e9
        control = sample.control  # Already applied CARLA control, not DriverInput.
        return VehicleAudioState(
            speed_kmh=sample.speed_kmh,
            throttle=control.throttle,
            brake=control.brake,
            gear=getattr(control, "gear", None),
            reverse=getattr(control, "reverse", False),
            observed_at=self._observed_at,
            actor_id=self.actor_id,
            stage_id=stage_id,
            mode=self.mode(),
        )


class VehicleAudioRuntime:
    def __init__(
        self,
        source: Callable[[], VehicleAudioState | None],
        playback: VehicleAudioPlayback,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.source, self.playback, self.clock = source, playback, clock
        self.model = EngineSoundModel()
        self.updates = 0
        self.fresh_updates = 0
        self.error: str | None = None
        self._identity: tuple[str, str] | None = None

    def prepare(self) -> None:
        self.playback.prepare()

    def update(self) -> None:
        if self.error is not None or not self.playback.available:
            return
        try:
            state = self.source()
            identity = None if state is None else (state.actor_id, state.stage_id)
            if (
                self._identity is not None
                and identity is not None
                and identity != self._identity
            ):
                # Stage ownership must be recreated, not re-use a previous segment's voices.
                self.close(fade=False)
                self.error = (
                    "audio identity changed; next viewer must prepare a new owner"
                )
                return
            if identity is not None:
                self._identity = identity
            parameters = self.model.update(state, self.clock())
            self.updates += 1
            self.fresh_updates += int(parameters.fresh)
            self.playback.update(parameters, self.clock())
        except Exception as error:  # noqa: BLE001 -- optional observer cannot replace primary errors
            self.error = f"{type(error).__name__}: {error}"
            self.close(fade=False)

    def close(self, *, fade: bool = True) -> None:
        self.playback.close(fade=fade)

    def status(self) -> dict[str, Any]:
        return {
            "asset": "original-procedural-cabin-v1",
            "rpm_source": "estimated_rpm",
            "state_source": "existing_snapshot_and_applied_control",
            "updates": self.updates,
            "fresh_updates": self.fresh_updates,
            "grain_launches": self.playback.launches,
            "skipped_busy": self.playback.skipped_busy,
            "error": self.error or self.playback.error,
            "cleanup_errors": self.playback.cleanup_errors,
            "mixer_format": self.playback.format,
            "physical_audio_validation": "NOT_MEASURED",
        }
