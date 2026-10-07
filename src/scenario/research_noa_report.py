from __future__ import annotations

from dataclasses import dataclass

import carla

from src.experiment.automation import DrivingControlMode
from src.experiment.noa_control import NoAControlCommand
from src.scenario.research_noa_config import ResearchNoARunConfig, ResearchNoARunMode
from src.scenario.research_noa_diagnostics import ResearchNoADiagnosticsSummary
from src.scenario.research_noa_smoke import ResearchAutomationScheduler
from src.scenario.research_noa_transmission import ResearchTransmissionPrimeTelemetry
from src.scenario.research_noa_types import ResearchNoAMap, ResearchNoAVehicle


@dataclass(frozen=True, slots=True)
class ResearchSmokeReport:
    map_name: str
    spawn_index: int
    spawn_x: float
    spawn_y: float
    spawn_z: float
    spawn_yaw: float
    vehicle_id: int
    vehicle_type: str
    initial_lane_id: int
    final_lane_id: int | None
    target_speed_kmh: float
    initial_speed_kmh: float
    final_speed_kmh: float
    maximum_observed_speed_kmh: float
    initial_gear: int
    transmission_prime_required: bool
    transmission_prime_applied: bool
    post_prime_gear: int
    duration: float
    elapsed_seconds: float
    diagnostics: ResearchNoADiagnosticsSummary
    shutdown_brake: float
    scheduler_updates: int
    commands: tuple[NoAControlCommand, ...]
    user_exited: bool
    final_control_mode: DrivingControlMode
    camera_diagnostics_json: str | None

    @property
    def control_frames(self) -> int:
        return len(self.commands)

    @property
    def lane_changed(self) -> bool | None:
        if self.final_lane_id is None:
            return None
        return self.initial_lane_id != self.final_lane_id

    def format(self) -> str:
        steering = max(
            (abs(command.steering) for command in self.commands),
            default=0.0,
        )
        fields = (
            ("mode", ResearchNoARunMode.LIVE_SMOKE.value),
            ("preflight", "passed"),
            ("map_name", self.map_name),
            ("spawn_index", self.spawn_index),
            ("spawn_x", self.spawn_x),
            ("spawn_y", self.spawn_y),
            ("spawn_z", self.spawn_z),
            ("spawn_yaw", self.spawn_yaw),
            ("vehicle_id", self.vehicle_id),
            ("vehicle_type", self.vehicle_type),
            ("initial_lane_id", self.initial_lane_id),
            ("final_lane_id", self.final_lane_id),
            ("target_speed_kmh", self.target_speed_kmh),
            ("initial_speed_kmh", self.initial_speed_kmh),
            ("final_speed_kmh", self.final_speed_kmh),
            ("maximum_observed_speed_kmh", self.maximum_observed_speed_kmh),
            ("initial_gear", self.initial_gear),
            ("transmission_prime_required", self.transmission_prime_required),
            ("transmission_prime_applied", self.transmission_prime_applied),
            ("post_prime_gear", self.post_prime_gear),
            ("requested_duration_seconds", self.duration),
            ("elapsed_seconds", round(self.elapsed_seconds, 3)),
            (
                "simulation_elapsed_seconds",
                round(self.diagnostics.simulation_elapsed_seconds, 3),
            ),
            ("initial_world_frame", self.diagnostics.initial_world_frame),
            ("final_world_frame", self.diagnostics.final_world_frame),
            ("world_frame_delta", self.diagnostics.world_frame_delta),
            (
                "world_frames_per_wall_second",
                self.diagnostics.world_frames_per_wall_second,
            ),
            ("driver_loop_iterations", self.diagnostics.driver_loop_iterations),
            ("scheduler_updates", self.scheduler_updates),
            ("control_frames", self.control_frames),
            (
                "scheduler_updates_per_wall_second",
                self.diagnostics.scheduler_updates_per_wall_second,
            ),
            (
                "scheduler_updates_per_sim_second",
                self.diagnostics.scheduler_updates_per_sim_second,
            ),
            ("driver_loop_mean_ms", self.diagnostics.driver_loop_mean_ms),
            ("driver_loop_max_ms", self.diagnostics.driver_loop_max_ms),
            ("scheduler_mean_ms", self.diagnostics.scheduler_mean_ms),
            ("scheduler_max_ms", self.diagnostics.scheduler_max_ms),
            ("render_mean_ms", self.diagnostics.render_mean_ms),
            ("render_max_ms", self.diagnostics.render_max_ms),
            ("shutdown_brake", self.shutdown_brake),
            ("active_initial_gear", self.diagnostics.active_initial_gear),
            ("active_final_gear", self.diagnostics.active_final_gear),
            ("active_min_gear", self.diagnostics.active_min_gear),
            ("active_max_gear", self.diagnostics.active_max_gear),
            (
                "active_gear_change_count",
                self.diagnostics.active_gear_change_count,
            ),
            ("mean_observed_speed_kmh", self.diagnostics.mean_observed_speed_kmh),
            (
                "max_observed_throttle",
                self.diagnostics.max_commanded_throttle or 0.0,
            ),
            ("mean_commanded_throttle", self.diagnostics.mean_commanded_throttle),
            ("min_commanded_throttle", self.diagnostics.min_commanded_throttle),
            ("max_observed_brake", self.diagnostics.max_commanded_brake or 0.0),
            ("mean_commanded_brake", self.diagnostics.mean_commanded_brake),
            ("min_commanded_brake", self.diagnostics.min_commanded_brake),
            ("mean_applied_throttle", self.diagnostics.mean_applied_throttle),
            ("max_applied_brake", self.diagnostics.max_applied_brake),
            ("active_hand_brake_seen", self.diagnostics.active_hand_brake_seen),
            ("active_reverse_seen", self.diagnostics.active_reverse_seen),
            (
                "active_manual_gear_shift_seen",
                self.diagnostics.active_manual_gear_shift_seen,
            ),
            ("max_observed_abs_steering", steering),
            ("lane_changed", self.lane_changed),
            ("user_exited", self.user_exited),
            ("final_control_mode", self.final_control_mode.value.lower()),
        )
        formatted = "\n".join(f"{name}={value}" for name, value in fields)
        if self.camera_diagnostics_json is None:
            return formatted
        return f"{formatted}\ncamera_diagnostics_json={self.camera_diagnostics_json}"


def build_research_smoke_report(
    *,
    config: ResearchNoARunConfig,
    world_map: ResearchNoAMap,
    hero: ResearchNoAVehicle,
    spawn_transform: carla.Transform,
    spawn_index: int,
    initial_lane_id: int,
    final_lane_id: int | None,
    initial_speed_kmh: float,
    final_speed_kmh: float,
    transmission_prime: ResearchTransmissionPrimeTelemetry,
    elapsed_seconds: float,
    diagnostics: ResearchNoADiagnosticsSummary,
    user_exited: bool,
    scheduler: ResearchAutomationScheduler,
    camera_diagnostics_json: str | None,
) -> ResearchSmokeReport:
    location = spawn_transform.location
    rotation = spawn_transform.rotation
    active_maximum_speed = diagnostics.maximum_active_speed_kmh
    return ResearchSmokeReport(
        map_name=str(world_map.name),
        spawn_index=spawn_index,
        spawn_x=float(location.x),
        spawn_y=float(location.y),
        spawn_z=float(location.z),
        spawn_yaw=float(rotation.yaw),
        vehicle_id=int(hero.id),
        vehicle_type=str(hero.type_id),
        initial_lane_id=initial_lane_id,
        final_lane_id=final_lane_id,
        target_speed_kmh=config.control_config.longitudinal.target_speed_kmh,
        initial_speed_kmh=initial_speed_kmh,
        final_speed_kmh=final_speed_kmh,
        maximum_observed_speed_kmh=max(
            initial_speed_kmh,
            initial_speed_kmh if active_maximum_speed is None else active_maximum_speed,
            final_speed_kmh,
        ),
        initial_gear=transmission_prime.initial_gear,
        transmission_prime_required=transmission_prime.required,
        transmission_prime_applied=transmission_prime.applied,
        post_prime_gear=transmission_prime.post_prime_gear,
        duration=config.duration,
        elapsed_seconds=elapsed_seconds,
        diagnostics=diagnostics,
        shutdown_brake=config.control_config.longitudinal.max_brake,
        scheduler_updates=scheduler.update_count,
        commands=scheduler.commands,
        user_exited=user_exited,
        final_control_mode=scheduler.runtime.state.control_mode,
        camera_diagnostics_json=camera_diagnostics_json,
    )
