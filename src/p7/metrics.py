from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from src.experiment.automation_interaction_types import DriverInput
from src.p7.types import EventKind, Json


@dataclass(frozen=True, slots=True)
class TTC:
    seconds: float | None
    reason: str | None


def calculate_ttc(distance_m: float, closing_speed_mps: float, collision_course: bool | None) -> TTC:
    if not isfinite(distance_m) or not isfinite(closing_speed_mps):
        return TTC(None, "NONFINITE")
    if collision_course is None:
        return TTC(None, "COLLISION_COURSE_UNVERIFIED")
    if not collision_course:
        return TTC(None, "NOT_COLLISION_COURSE")
    if distance_m <= 0:
        return TTC(None, "NONPOSITIVE_CLEARANCE")
    if closing_speed_mps <= 0:
        return TTC(None, "NOT_CLOSING")
    return TTC(distance_m / closing_speed_mps, None)


class ResponseMetrics:
    def __init__(self, *, brake_candidate: float, steer_candidate: float) -> None:
        self.brake_candidate, self.steer_candidate = brake_candidate, steer_candidate
        self.trigger_t: float | None = None
        self.reveal_t: float | None = None
        self.hazard_visible_t: float | None = None
        self.first_brake_t: float | None = None
        self.first_steer_t: float | None = None
        self.candidate_response_t: float | None = None
        self.first_meaningful_driver_response_t: float | None = None
        self.min_ttc: float | None = None
        self.min_distance: float | None = None
        self.current_ttc = TTC(None, "NOT_OBSERVED")
        self.ttc_at_response = TTC(None, "NO_DRIVER_RESPONSE")
        self.start_speed_mps: float | None = None
        self.start_distance_m: float | None = None
        self._previous: DriverInput | None = None

    @property
    def first_raw_response_t(self) -> float | None:
        values = [value for value in (self.first_brake_t, self.first_steer_t) if value is not None]
        return min(values) if values else None

    @property
    def raw_response_latency_s(self) -> float | None:
        response = self.first_raw_response_t
        return None if response is None or self.reveal_t is None else response - self.reveal_t

    def observe_input(self, now: float, current: DriverInput | None) -> tuple[EventKind, ...]:
        previous = self._previous
        self._previous = current
        if current is None or previous is None or self.reveal_t is None:
            return ()
        events = []
        first_before = self.first_raw_response_t
        if self.first_brake_t is None and previous.brake == 0 and current.brake > 0:
            self.first_brake_t = now
            events.append(EventKind.BRAKE)
        if self.first_steer_t is None and previous.steering == 0 and current.steering != 0:
            self.first_steer_t = now
            events.append(EventKind.STEER)
        if first_before is None and self.first_raw_response_t is not None:
            self.ttc_at_response = self.current_ttc
            events.append(EventKind.RESPONSE)
        candidate = ((self.first_brake_t is not None and current.brake >= self.brake_candidate)
                     or (self.first_steer_t is not None and abs(current.steering) >= self.steer_candidate))
        if self.candidate_response_t is None and candidate:
            self.candidate_response_t = now
            events.append(EventKind.CANDIDATE_RESPONSE)
        return tuple(events)

    def observe_geometry(self, distance: float, closing: float, course: bool | None) -> None:
        self.current_ttc = calculate_ttc(distance, closing, course)
        if isfinite(distance):
            self.min_distance = distance if self.min_distance is None else min(self.min_distance, distance)
        if self.current_ttc.seconds is not None:
            self.min_ttc = (self.current_ttc.seconds if self.min_ttc is None
                            else min(self.min_ttc, self.current_ttc.seconds))

    def snapshot(self) -> dict[str, Json]:
        return {"hazard_trigger_t": self.trigger_t, "reveal_t": self.reveal_t,
                "hazard_visible_t": self.hazard_visible_t, "first_brake_t": self.first_brake_t,
                "first_steer_t": self.first_steer_t, "first_raw_response_t": self.first_raw_response_t,
                "first_meaningful_driver_response_t": self.first_meaningful_driver_response_t,
                "candidate_response_t": self.candidate_response_t,
                "candidate_response_latency_s": None if self.candidate_response_t is None or self.reveal_t is None else self.candidate_response_t - self.reveal_t,
                "raw_response_latency_s": self.raw_response_latency_s,
                "ttc_at_response_s": self.ttc_at_response.seconds,
                "ttc_at_response_undefined_reason": self.ttc_at_response.reason,
                "hazard_start_speed_mps": self.start_speed_mps,
                "hazard_start_distance_m": self.start_distance_m,
                "latency_reference": "GEOMETRIC_REVEAL_CANDIDATE_NOT_PHOTON_ONSET",
                "ttc_model": "CONSTANT_VELOCITY_LONGITUDINAL_CLEARANCE",
                "distance_model": "WORLD_BOUNDING_BOX_LONGITUDINAL_CLEARANCE",
                "ttc_s": self.current_ttc.seconds, "ttc_undefined_reason": self.current_ttc.reason,
                "min_ttc_s": self.min_ttc, "min_distance_m": self.min_distance}
