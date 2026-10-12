from __future__ import annotations

import random
import uuid
from collections.abc import Callable
from time import monotonic_ns

from src.experiment.context import Module1Condition, Module2Condition
from src.p6.clock import SessionOrigin
from src.p6.tablet_error import TabletProtocolError
from src.p6.tablet_events import EventSink, TabletEventLedger
from src.p6.tablet_media import development_surt_stimulus, select_media
from src.p6.tablet_models import (
    EventRequest,
    JsonValue,
    MediaMetadata,
    MediaSelection,
    StimulusConfig,
    TabletEvent,
    TabletState,
    Trial,
    tablet_state_json,
)


class TabletSessionState:
    def __init__(
        self,
        run_id: str,
        origin: SessionOrigin,
        *,
        token: str,
        monotonic_ns: Callable[[], int] = monotonic_ns,
        event_sink: EventSink | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.token = token
        self._run_id = run_id
        self._ledger = TabletEventLedger(origin, monotonic_ns, event_sink)
        self._rng = rng or random.Random()
        self._run_epoch = str(uuid.uuid4())
        self._trial: Trial | None = None
        self._condition: str | None = None
        self._module: str | None = None
        self._stage = "IDLE"
        self._stage_before_pause: str | None = None
        self._media: MediaSelection | None = None
        self._metadata: MediaMetadata | None = None
        self._paused = False
        self._error: str | None = None

    def configure_module_one(self, condition: Module1Condition) -> TabletState:
        self._clear_active_state()
        self._module = "MODULE_1"
        self._condition = condition.value
        self._stage = "BLANK" if condition is Module1Condition.NO_SURT else "SURT_READY"
        return self.state()

    def configure_module_two(
        self, condition: Module2Condition, filename: str, version: str
    ) -> TabletState:
        self._clear_active_state()
        self._module = "MODULE_2"
        self._condition = condition.value
        self._media = select_media(filename, version)
        self._stage = "VIDEO_READY" if self._media.exists else "MEDIA_MISSING"
        return self.state()

    def prepare(self, stimulus: StimulusConfig | None = None) -> Trial:
        if (
            self._module == "MODULE_1"
            and self._condition == Module1Condition.NO_SURT.value
        ):
            raise TabletProtocolError("NO_SURT cannot prepare stimulation")
        if self._module == "MODULE_2" and (
            self._media is None or not self._media.exists
        ):
            raise TabletProtocolError("MEDIA_MISSING")
        if self._module is None:
            raise TabletProtocolError("tablet module is not configured")
        active_stimulus = (
            development_surt_stimulus(self._rng)
            if self._module == "MODULE_1" and stimulus is None
            else stimulus
        )
        self._trial = Trial(str(uuid.uuid4()), self._run_epoch, active_stimulus)
        self._stage = "PREPARED"
        self._append("prepare", str(uuid.uuid4()), self._trial.trial_uuid)
        return self._trial

    def request_start(self, trial_uuid: str) -> TabletState:
        self._require_current(trial_uuid)
        self._stage = "START_REQUESTED"
        self._append("start_requested", str(uuid.uuid4()), trial_uuid)
        return self.state()

    def end(self) -> TabletState:
        self._stage = "EXPERIMENT_STOPPED"
        self._append("experiment_stop", str(uuid.uuid4()))
        return self.state()

    def reset_new_run(self, run_epoch: str, event_id: str = "reset") -> TabletState:
        disposition = self._epoch_disposition(run_epoch)
        self._append("reset", event_id, disposition=disposition, event_epoch=run_epoch)
        if disposition == "accepted":
            self._run_epoch = str(uuid.uuid4())
            self._clear_active_state()
            self._module = None
            self._condition = None
            self._stage = "IDLE"
        return self.state()

    def state(self) -> TabletState:
        circles = (
            ()
            if self._trial is None or self._trial.stimulus is None
            else self._trial.stimulus.circles
        )
        return TabletState(
            self._run_id,
            self._run_epoch,
            self._trial,
            circles,
            self._condition,
            self._module,
            self._stage,
            self._media,
            self._paused,
            self._error,
        )

    def state_json(self) -> dict[str, JsonValue]:
        return tablet_state_json(self.state())

    def events(self) -> tuple[TabletEvent, ...]:
        return self._ledger.events()

    def _append(
        self,
        event_type: str,
        event_id: str,
        trial_uuid: str | None = None,
        performance_ms: float | None = None,
        time_origin_ms: float | None = None,
        detail: str | None = None,
        correct: bool | None = None,
        disposition: str = "accepted",
        event_epoch: str | None = None,
    ) -> TabletEvent:
        return self._ledger.append(
            EventRequest(
                event_type,
                event_id,
                self._run_epoch if event_epoch is None else event_epoch,
                trial_uuid,
                performance_ms,
                time_origin_ms,
                detail,
                correct,
                disposition,
            )
        )

    def _event_disposition(self, run_epoch: str, trial_uuid: str, event_id: str) -> str:
        if self._ledger.contains(event_id):
            return "duplicate"
        if run_epoch != self._run_epoch:
            return "stale_epoch"
        if self._trial is None or trial_uuid != self._trial.trial_uuid:
            return "stale_trial"
        return "accepted"

    def _epoch_disposition(self, run_epoch: str) -> str:
        return "accepted" if run_epoch == self._run_epoch else "stale_epoch"

    def _require_current(self, trial_uuid: str) -> None:
        if self._trial is None or self._trial.trial_uuid != trial_uuid:
            raise TabletProtocolError("trial UUID is stale or unknown")

    def _require_surt(self) -> None:
        if self._module != "MODULE_1" or self._condition != Module1Condition.SURT.value:
            raise TabletProtocolError("task pause controls are SURT-only")

    def _clear_active_state(self) -> None:
        self._trial = None
        self._media = None
        self._metadata = None
        self._paused = False
        self._stage_before_pause = None
        self._error = None
