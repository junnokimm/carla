from __future__ import annotations

from src.p6.tablet_error import TabletProtocolError
from src.p6.tablet_media import find_circle, media_metadata_issue
from src.p6.tablet_models import (
    Circle,
    EventRequest,
    MediaMetadata,
    StimulusConfig,
    TabletState,
)
from src.p6.tablet_session_base import TabletSessionState

__all__ = [
    "Circle",
    "MediaMetadata",
    "StimulusConfig",
    "TabletProtocolError",
    "TabletSession",
]


class TabletSession(TabletSessionState):
    def acknowledge(
        self,
        run_epoch: str,
        event_type: str,
        trial_uuid: str,
        event_id: str,
        performance_ms: float,
        time_origin_ms: float,
    ) -> TabletState:
        disposition = self._event_disposition(run_epoch, trial_uuid, event_id)
        if disposition != "accepted":
            self._append(
                event_type,
                event_id,
                trial_uuid,
                performance_ms,
                time_origin_ms,
                disposition=disposition,
                event_epoch=run_epoch,
            )
            return self.state()
        allowed = {"shown", "media_playing", "visibility_hidden", "visibility_visible"}
        if event_type not in allowed:
            raise TabletProtocolError("unsupported acknowledgment event type")
        if event_type == "shown":
            disposition = (
                "accepted"
                if self._module == "MODULE_1" and self._stage == "START_REQUESTED"
                else "invalid_stage"
            )
            if disposition == "accepted":
                self._stage = "STIMULUS_SHOWN"
        elif event_type == "media_playing":
            if self._module != "MODULE_2" or self._stage != "START_REQUESTED":
                disposition = "invalid_stage"
            elif self._metadata is None:
                raise TabletProtocolError(
                    "browser media metadata is required before media_playing acknowledgment"
                )
            else:
                self._stage = "MEDIA_PLAYING"
        self._append(
            event_type,
            event_id,
            trial_uuid,
            performance_ms,
            time_origin_ms,
            disposition=disposition,
            event_epoch=run_epoch,
        )
        return self.state()

    def record_video_metadata(
        self,
        run_epoch: str,
        trial_uuid: str,
        event_id: str,
        metadata: MediaMetadata,
    ) -> TabletState:
        disposition = self._event_disposition(run_epoch, trial_uuid, event_id)
        if disposition == "accepted":
            if (issue := media_metadata_issue(metadata)) is not None:
                raise TabletProtocolError(issue)
            self._metadata = metadata
        self._append(
            "video_metadata",
            event_id,
            trial_uuid,
            disposition=disposition,
            event_epoch=run_epoch,
        )
        return self.state()

    def touch(
        self,
        run_epoch: str,
        trial_uuid: str,
        event_id: str,
        circle_id: str,
        performance_ms: float,
        time_origin_ms: float,
    ) -> TabletState:
        disposition = self._event_disposition(run_epoch, trial_uuid, event_id)
        if disposition == "accepted" and self._paused:
            disposition = "paused"
        if disposition == "accepted" and self._stage != "STIMULUS_SHOWN":
            disposition = "pre_show"
        circle = None
        if disposition == "accepted":
            stimulus = self._trial.stimulus if self._trial is not None else None
            circle = find_circle(stimulus, circle_id)
            if circle is None:
                raise TabletProtocolError("circle ID is not in the current trial")
        self._append(
            "touch",
            event_id,
            trial_uuid,
            performance_ms,
            time_origin_ms,
            detail=circle_id,
            correct=None if circle is None else circle.target,
            disposition=disposition,
            event_epoch=run_epoch,
        )
        if disposition == "accepted":
            next_trial = self.prepare()
            self.request_start(next_trial.trial_uuid)
        return self.state()

    def pause(self, run_epoch: str, event_id: str, reason: str) -> TabletState:
        self._require_surt()
        disposition = self._epoch_disposition(run_epoch)
        if disposition == "accepted":
            self._stage_before_pause = self._stage
            self._paused = True
            self._stage = "PAUSED"
        self._append(
            "task_pause",
            event_id,
            detail=reason,
            disposition=disposition,
            event_epoch=run_epoch,
        )
        return self.state()

    def resume(self, run_epoch: str, event_id: str = "resume") -> TabletState:
        self._require_surt()
        disposition = self._epoch_disposition(run_epoch)
        if disposition == "accepted" and self._paused:
            self._paused = False
            self._stage = self._stage_before_pause or "SURT_READY"
            self._stage_before_pause = None
        self._append(
            "task_resume", event_id, disposition=disposition, event_epoch=run_epoch
        )
        return self.state()

    def record_media_ended(
        self,
        run_epoch: str,
        trial_uuid: str,
        event_id: str,
        current_time_seconds: float,
    ) -> TabletState:
        disposition = self._event_disposition(run_epoch, trial_uuid, event_id)
        if (
            disposition == "accepted"
            and self._stage == "MEDIA_PLAYING"
            and current_time_seconds < 780.0
        ):
            self._error = "MEDIA_EARLY_END"
            self._stage = "ERROR"
            disposition = "early_end"
        self._append(
            "media_ended",
            event_id,
            trial_uuid,
            detail=str(current_time_seconds),
            disposition=disposition,
            event_epoch=run_epoch,
        )
        return self.state()

    def record_development_nonresponse(
        self, run_epoch: str, trial_uuid: str, event_id: str
    ) -> TabletState:
        disposition = self._event_disposition(run_epoch, trial_uuid, event_id)
        stimulus = self._trial.stimulus if self._trial is not None else None
        if stimulus is None or not stimulus.development_nonresponse_enabled:
            raise TabletProtocolError("development nonresponse rule is disabled")
        self._append(
            "development_nonresponse",
            event_id,
            trial_uuid,
            disposition=disposition,
            event_epoch=run_epoch,
        )
        return self.state()

    def record_error(
        self, run_epoch: str, trial_uuid: str | None, event_id: str, message: str
    ) -> TabletState:
        disposition = self._epoch_disposition(run_epoch)
        if disposition == "accepted":
            self._error = message
            self._stage = "ERROR"
        self._append(
            "error",
            event_id,
            trial_uuid,
            detail=message,
            disposition=disposition,
            event_epoch=run_epoch,
        )
        return self.state()

    def sync_mark(
        self,
        run_epoch: str,
        event_id: str,
        performance_ms: float,
        time_origin_ms: float,
    ) -> TabletState:
        self._ledger.sync(
            EventRequest(
                "sync_mark",
                event_id,
                run_epoch,
                performance_ms=performance_ms,
                time_origin_ms=time_origin_ms,
                disposition=self._epoch_disposition(run_epoch),
            )
        )
        return self.state()
