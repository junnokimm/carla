from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from time import perf_counter

from src.experiment.automation import DrivingControlMode
from src.experiment.noa_control import NoAControlCommand
from src.scenario.research_noa_types import ResearchNoAWorldSnapshot

type PerformanceClock = Callable[[], float]
type DiagnosticsValue = str | int | float | None | dict[str, "DiagnosticsValue"]

PERFORMANCE_OPERATIONS = (
    "input_collection",
    "input_observer",
    "scheduler",
    "manual_batch_rpc_wait",
    "manual_control_preparation",
    "manual_control_delivery",
    "manual_availability_refresh",
    "activation_geometry_query",
    "activation_bounding_box_getter",
    "indicator_input_light_rpc",
    "post_control",
    "vehicle_audio_update",
    "snapshot_getter",
    "snapshot_actor_getters",
    "control_getter",
    "waypoint_query",
    "neighbor_lane_queries",
    "light_getter",
    "traffic_light_observation",
    "traffic_light_actor_lookup",
    "json_serialize",
    "telemetry_write",
    "event_write",
)


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
class OperationTimingSummary:
    count: int
    mean_ms: float
    maximum_ms: float


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
    mean_applied_throttle: float | None
    max_applied_brake: float | None
    active_hand_brake_seen: bool
    active_reverse_seen: bool
    active_manual_gear_shift_seen: bool


class _RunningStats:
    __slots__ = ("at_least_100_ms_count", "count", "maximum", "minimum", "total")

    def __init__(self) -> None:
        self.count = 0
        self.total = 0.0
        self.minimum: float | None = None
        self.maximum: float | None = None
        self.at_least_100_ms_count = 0

    def record(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)
        self.at_least_100_ms_count += int(value >= 0.100)

    @property
    def mean(self) -> float | None:
        return None if self.count == 0 else self.total / self.count


class ResearchNoADiagnostics:
    __slots__ = (
        "_active_operations",
        "_active_operations_by_mode",
        "_applied_brake",
        "_applied_hand_brake_seen",
        "_applied_manual_gear_shift_seen",
        "_applied_reverse_seen",
        "_applied_throttle",
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
        "_measurement_frozen",
        "_mode",
        "_operation_measurement_active",
        "_operations",
        "_operations_by_mode",
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
        self._operations: dict[str, _RunningStats] = {}
        self._operations_by_mode: dict[DrivingControlMode, dict[str, _RunningStats]] = {
            mode: {} for mode in DrivingControlMode
        }
        self._active_operations: dict[str, _RunningStats] = {}
        self._active_operations_by_mode: dict[
            DrivingControlMode, dict[str, _RunningStats]
        ] = {mode: {} for mode in DrivingControlMode}
        self._operation_measurement_active: bool | None = None
        self._mode: DrivingControlMode | None = None
        self._speed = _RunningStats()
        self._throttle = _RunningStats()
        self._brake = _RunningStats()
        self._applied_throttle = _RunningStats()
        self._applied_brake = _RunningStats()
        self._applied_hand_brake_seen = False
        self._applied_reverse_seen = False
        self._applied_manual_gear_shift_seen = False
        self._loop_started_at: float | None = None
        self._gear_count = 0
        self._gear_initial: int | None = None
        self._gear_final: int | None = None
        self._gear_minimum: int | None = None
        self._gear_maximum: int | None = None
        self._gear_change_count = 0
        self._measurement_frozen = False

    def timestamp(self) -> float:
        return self._clock()

    @property
    def completed_driver_loop_count(self) -> int:
        return self._loop_iterations

    def begin_driver_loop(self) -> None:
        if self._measurement_frozen:
            return
        self._loop_started_at = self.timestamp()

    def complete_driver_loop(self) -> None:
        if self._measurement_frozen:
            return
        if self._loop_started_at is None:
            return
        self._loop.record(self.timestamp() - self._loop_started_at)
        self._loop_iterations += 1
        self._loop_started_at = None

    def discard_incomplete_driver_loop(self) -> None:
        self._loop_started_at = None

    def record_driver_loop_duration(self, duration_seconds: float) -> None:
        if self._measurement_frozen:
            return
        self._loop_iterations += 1
        self._loop.record(duration_seconds)

    def record_scheduler_duration(self, duration_seconds: float) -> None:
        if self._measurement_frozen:
            return
        self._scheduler.record(duration_seconds)

    def record_render_duration(self, duration_seconds: float) -> None:
        if self._measurement_frozen:
            return
        self._render.record(duration_seconds)

    def record_operation_duration(
        self, operation: str, duration_seconds: float
    ) -> None:
        if self._measurement_frozen:
            return
        self._operations.setdefault(operation, _RunningStats()).record(duration_seconds)
        if self._mode is not None:
            self._operations_by_mode[self._mode].setdefault(
                operation, _RunningStats()
            ).record(duration_seconds)
        if self._operation_measurement_active is not True:
            return
        self._active_operations.setdefault(operation, _RunningStats()).record(
            duration_seconds
        )
        if self._mode is not None:
            self._active_operations_by_mode[self._mode].setdefault(
                operation, _RunningStats()
            ).record(duration_seconds)

    def begin_operation_measurement_window(self) -> None:
        self._active_operations = {}
        self._active_operations_by_mode = {mode: {} for mode in DrivingControlMode}
        self._operation_measurement_active = True

    def end_operation_measurement_window(self) -> None:
        self._operation_measurement_active = False

    def freeze_measurement_window(self) -> None:
        self.end_operation_measurement_window()
        self._measurement_frozen = True

    def set_control_mode(self, mode: DrivingControlMode) -> None:
        self._mode = mode

    def performance_payload(self) -> dict[str, DiagnosticsValue]:
        operations_source = (
            self._operations
            if self._operation_measurement_active is None
            else self._active_operations
        )
        modes_source = (
            self._operations_by_mode
            if self._operation_measurement_active is None
            else self._active_operations_by_mode
        )
        operations = {
            operation: _optional_timing_payload(operations_source.get(operation))
            for operation in PERFORMANCE_OPERATIONS
        }
        operations.update(
            {
                operation: _optional_timing_payload(stats)
                for operation, stats in operations_source.items()
                if operation not in operations
            }
        )
        return {
            "mode_definition": "canonical automation control mode at operation completion",
            "operations": operations,
            "operations_by_mode": {
                mode.value: {
                    operation: _optional_timing_payload(stats)
                    for operation, stats in sorted(mode_operations.items())
                }
                for mode, mode_operations in modes_source.items()
            },
            "operation_accumulation": {
                operation: _accumulation_payload(operations_source.get(operation))
                for operation in operations
            },
            "operation_accumulation_by_mode": {
                mode.value: {
                    operation: _accumulation_payload(stats)
                    for operation, stats in sorted(mode_operations.items())
                }
                for mode, mode_operations in modes_source.items()
            },
        }

    def operation_timing(self, operation: str) -> OperationTimingSummary:
        stats = self._operations.setdefault(operation, _RunningStats())
        return OperationTimingSummary(
            stats.count,
            _milliseconds(stats.mean),
            _milliseconds(stats.maximum),
        )

    def record_speed(self, speed_kmh: float) -> None:
        if self._measurement_frozen:
            return
        self._speed.record(speed_kmh)

    def record_command(self, command: NoAControlCommand) -> None:
        if self._measurement_frozen:
            return
        self._throttle.record(command.throttle)
        self._brake.record(command.brake)

    def record_applied_control(
        self,
        *,
        throttle: float,
        brake: float,
        hand_brake: bool,
        reverse: bool,
        manual_gear_shift: bool,
    ) -> None:
        if self._measurement_frozen:
            return
        self._applied_throttle.record(throttle)
        self._applied_brake.record(brake)
        self._applied_hand_brake_seen |= hand_brake
        self._applied_reverse_seen |= reverse
        self._applied_manual_gear_shift_seen |= manual_gear_shift

    def record_gear(self, gear: int) -> None:
        if self._measurement_frozen:
            return
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
            mean_applied_throttle=self._applied_throttle.mean,
            max_applied_brake=self._applied_brake.maximum,
            active_hand_brake_seen=self._applied_hand_brake_seen,
            active_reverse_seen=self._applied_reverse_seen,
            active_manual_gear_shift_seen=self._applied_manual_gear_shift_seen,
        )


def _milliseconds(seconds: float | None) -> float:
    return 0.0 if seconds is None else seconds * 1000.0


def _rate(count: int, elapsed_seconds: float) -> float | None:
    if count < 0 or not isfinite(elapsed_seconds) or elapsed_seconds <= 0.0:
        return None
    return count / elapsed_seconds


def _optional_timing_payload(
    stats: _RunningStats | None,
) -> dict[str, DiagnosticsValue]:
    measured = stats is not None and stats.count > 0
    return {
        "count": 0 if stats is None else stats.count,
        "mean_ms": _milliseconds(stats.mean)
        if measured and stats is not None
        else None,
        "max_ms": (
            _milliseconds(stats.maximum) if measured and stats is not None else None
        ),
    }


def _accumulation_payload(stats: _RunningStats | None) -> dict[str, DiagnosticsValue]:
    return {
        "total_ms": None if stats is None or stats.count == 0 else stats.total * 1000.0,
        "at_least_100_ms_count": 0 if stats is None else stats.at_least_100_ms_count,
    }
