from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from time import perf_counter

from src.experiment.noa_control import NoAControlCommand
from src.scenario.research_noa_types import ResearchNoAWorldSnapshot

type PerformanceClock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class ResearchWorldTiming:
    initial_frame: int
    final_frame: int
    initial_simulation_time_seconds: float
    final_simulation_time_seconds: float

    @classmethod
    def from_snapshots(
        cls,
        initial: ResearchNoAWorldSnapshot,
        final: ResearchNoAWorldSnapshot,
    ) -> ResearchWorldTiming:
        return cls(
            initial.frame,
            final.frame,
            initial.timestamp.elapsed_seconds,
            final.timestamp.elapsed_seconds,
        )


@dataclass(frozen=True, slots=True)
class ResearchNoADiagnosticsSummary:
    initial_world_frame: int
    final_world_frame: int
    world_frame_delta: int
    initial_simulation_time_seconds: float
    final_simulation_time_seconds: float
    simulation_elapsed_seconds: float
    driver_loop_iterations: int
    driver_loop_mean_ms: float
    driver_loop_max_ms: float
    scheduler_mean_ms: float
    scheduler_max_ms: float
    render_mean_ms: float
    render_max_ms: float
    scheduler_updates_per_wall_second: float | None
    scheduler_updates_per_sim_second: float | None
    world_frames_per_wall_second: float | None
    active_initial_gear: int | None
    active_final_gear: int | None
    active_min_gear: int | None
    active_max_gear: int | None
    active_gear_change_count: int
    mean_observed_speed_kmh: float | None
    maximum_active_speed_kmh: float | None
    min_commanded_throttle: float
    max_commanded_throttle: float
    mean_commanded_throttle: float
    min_commanded_brake: float
    max_commanded_brake: float
    mean_commanded_brake: float


class _RunningStats:
    __slots__ = ("count", "maximum", "minimum", "total")

    def __init__(self) -> None:
        self.count = 0
        self.total = 0.0
        self.minimum: float | None = None
        self.maximum: float | None = None

    def record(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)

    @property
    def mean(self) -> float | None:
        return None if self.count == 0 else self.total / self.count


class ResearchNoADiagnostics:
    __slots__ = (
        "_brake",
        "_clock",
        "_gear_change_count",
        "_gear_count",
        "_gear_final",
        "_gear_initial",
        "_gear_maximum",
        "_gear_minimum",
        "_loop",
        "_loop_iterations",
        "_loop_started_at",
        "_render",
        "_scheduler",
        "_speed",
        "_throttle",
    )

    def __init__(self, clock: PerformanceClock = perf_counter) -> None:
        self._clock = clock
        self._loop = _RunningStats()
        self._loop_iterations = 0
        self._scheduler = _RunningStats()
        self._render = _RunningStats()
        self._speed = _RunningStats()
        self._throttle = _RunningStats()
        self._brake = _RunningStats()
        self._loop_started_at: float | None = None
        self._gear_count = 0
        self._gear_initial: int | None = None
        self._gear_final: int | None = None
        self._gear_minimum: int | None = None
        self._gear_maximum: int | None = None
        self._gear_change_count = 0

    def timestamp(self) -> float:
        return self._clock()

    def begin_driver_loop(self) -> None:
        now = self.timestamp()
        if self._loop_started_at is not None:
            self._loop.record(now - self._loop_started_at)
        self._loop_iterations += 1
        self._loop_started_at = now

    def discard_incomplete_driver_loop(self) -> None:
        self._loop_started_at = None

    def record_driver_loop_duration(self, duration_seconds: float) -> None:
        self._loop_iterations += 1
        self._loop.record(duration_seconds)

    def record_scheduler_duration(self, duration_seconds: float) -> None:
        self._scheduler.record(duration_seconds)

    def record_render_duration(self, duration_seconds: float) -> None:
        self._render.record(duration_seconds)

    def record_speed(self, speed_kmh: float) -> None:
        self._speed.record(speed_kmh)

    def record_command(self, command: NoAControlCommand) -> None:
        self._throttle.record(command.throttle)
        self._brake.record(command.brake)

    def record_gear(self, gear: int) -> None:
        if self._gear_final is not None and gear != self._gear_final:
            self._gear_change_count += 1
        if self._gear_initial is None:
            self._gear_initial = gear
        self._gear_final = gear
        self._gear_minimum = (
            gear if self._gear_minimum is None else min(self._gear_minimum, gear)
        )
        self._gear_maximum = (
            gear if self._gear_maximum is None else max(self._gear_maximum, gear)
        )
        self._gear_count += 1

    def summarize(
        self,
        world: ResearchWorldTiming,
        wall_elapsed_seconds: float,
        scheduler_updates: int,
    ) -> ResearchNoADiagnosticsSummary:
        simulation_elapsed = (
            world.final_simulation_time_seconds - world.initial_simulation_time_seconds
        )
        world_frame_delta = world.final_frame - world.initial_frame
        return ResearchNoADiagnosticsSummary(
            initial_world_frame=world.initial_frame,
            final_world_frame=world.final_frame,
            world_frame_delta=world_frame_delta,
            initial_simulation_time_seconds=world.initial_simulation_time_seconds,
            final_simulation_time_seconds=world.final_simulation_time_seconds,
            simulation_elapsed_seconds=simulation_elapsed,
            driver_loop_iterations=self._loop_iterations,
            driver_loop_mean_ms=_milliseconds(self._loop.mean),
            driver_loop_max_ms=_milliseconds(self._loop.maximum),
            scheduler_mean_ms=_milliseconds(self._scheduler.mean),
            scheduler_max_ms=_milliseconds(self._scheduler.maximum),
            render_mean_ms=_milliseconds(self._render.mean),
            render_max_ms=_milliseconds(self._render.maximum),
            scheduler_updates_per_wall_second=_rate(
                scheduler_updates, wall_elapsed_seconds
            ),
            scheduler_updates_per_sim_second=_rate(
                scheduler_updates, simulation_elapsed
            ),
            world_frames_per_wall_second=_rate(world_frame_delta, wall_elapsed_seconds),
            active_initial_gear=self._gear_initial,
            active_final_gear=self._gear_final,
            active_min_gear=self._gear_minimum,
            active_max_gear=self._gear_maximum,
            active_gear_change_count=self._gear_change_count,
            mean_observed_speed_kmh=self._speed.mean,
            maximum_active_speed_kmh=self._speed.maximum,
            min_commanded_throttle=self._throttle.minimum or 0.0,
            max_commanded_throttle=self._throttle.maximum or 0.0,
            mean_commanded_throttle=self._throttle.mean or 0.0,
            min_commanded_brake=self._brake.minimum or 0.0,
            max_commanded_brake=self._brake.maximum or 0.0,
            mean_commanded_brake=self._brake.mean or 0.0,
        )


def _milliseconds(seconds: float | None) -> float:
    return 0.0 if seconds is None else seconds * 1000.0


def _rate(count: int, elapsed_seconds: float) -> float | None:
    if not isfinite(elapsed_seconds) or elapsed_seconds <= 0.0:
        return None
    return count / elapsed_seconds
