from __future__ import annotations

import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import carla
import pygame

from src.experiment.automation_interaction_types import DriverInput
from src.experiment.context import ExperimentModule
from src.experiment.timestamp import (
    CarlaSnapshotTimestamp,
    HostClockTimestamp,
    TimestampEnvelope,
)
from src.p7.carla_actors import CarlaHazardActors
from src.p7.carla_observation import CollisionObserver, observe
from src.p7.carla_route import CorridorError, build_corridor
from src.p7.carla_traffic import CarlaTraffic, in_hazard_zone
from src.p7.hazard import HazardScenario
from src.p7.occurrence import OccurrenceRegistry
from src.p7.ownership import OwnedActors
from src.p7.session import DevelopmentSession
from src.p7.types import (
    EventKind,
    HazardObservation,
    HazardState,
    Json,
    P7Error,
    TriggerSource,
)
from src.scenario.driver_view import DriverView, DriverViewConfig
from src.vehicle import VehicleState


@dataclass(frozen=True, slots=True)
class LiveOptions:
    host: str
    port: int
    ego_id: int
    duration_s: float
    occurrences: Path


def snapshot_timestamp(world: carla.World) -> TimestampEnvelope:
    start = time.monotonic_ns()
    snapshot = world.get_snapshot()
    end = time.monotonic_ns()
    return TimestampEnvelope(HostClockTimestamp(end, time.time_ns()),
                             CarlaSnapshotTimestamp(snapshot.timestamp.elapsed_seconds, snapshot.frame, start, end))


def execute(session: DevelopmentSession, options: LiveOptions) -> dict[str, Json]:
    enabled = session.module is ExperimentModule.MODULE_2 and session.config.hazard.enabled
    if enabled and session.config.hazard.trigger_source is not TriggerSource.DEV_LEAD_ROUTE_STATION:
        raise P7Error("LIVE_STATIC_SMOKE_REQUIRES_EXPLICIT_DEV_LEAD_TRIGGER; moving study integration is not enabled")
    client = carla.Client(options.host, options.port)
    client.set_timeout(2.0)
    world = client.get_world()
    if world.get_settings().synchronous_mode:
        raise P7Error("ASYNC_WORLD_REQUIRED; P7 never changes world settings or ticks")
    ego = world.get_actor(options.ego_id)
    if ego is None or not ego.type_id.startswith("vehicle."):
        raise P7Error("EXISTING_PARKED_EGO_REQUIRED")
    if ego.get_velocity().length() > 0.05:
        raise P7Error("PARK_EGO_EXTERNALLY; P7 never brakes ego")
    waypoint = world.get_map().get_waypoint(ego.get_location(), project_to_road=False, lane_type=carla.LaneType.Driving)
    if waypoint is None:
        raise P7Error("EGO_NOT_ON_DRIVING_LANE")
    corridor = build_corridor(waypoint, session.config.corridor_length_m, require_target=enabled)
    owned = OwnedActors(ego.id)
    actors = CarlaHazardActors(world, ego, corridor, config=session.config.hazard,
                               owned=owned, emit=session.emit, run_id=session.context.study_run_id)
    traffic = CarlaTraffic(actors, session.config)
    hazard = HazardScenario(session.config.hazard, module=session.module, phase=session.segment.phase,
                            participant_id=session.context.participant_id, run_id=session.context.study_run_id,
                            actors=actors, occurrences=OccurrenceRegistry(options.occurrences),
                            event_sink=session.capture, origin_ns=session.origin.monotonic_ns)
    viewer, collision = None, None
    last = HazardObservation(snapshot_timestamp(world), 0, session.config.hazard.lead_initial_gap_m,
                              0, corridor.target_lane_id or 0, False, False,
                              session.config.hazard.obstacle_station_m, 0, 0, True, None,
                              input_source="UNKNOWN_AT_PREPARATION")
    failures: list[str] = []
    status = "ABORTED"
    try:
        foreign = tuple(actor for actor in world.get_actors().filter("vehicle.*") if actor.id != ego.id)
        if enabled and any(in_hazard_zone(corridor, actor.get_transform()) for actor in foreign):
            raise P7Error("HAZARD_CORRIDOR_OCCUPIED")
        traffic.prepare(last.timestamp)
        if enabled:
            hazard.arm(last)
            collision = CollisionObserver(actors)
        base = DriverViewConfig()
        view_config = replace(base, front_transform=base.cockpit_transform, fov=base.cockpit_fov)
        viewer = DriverView(world, ego, view_config, read_only=True)
        viewer.attach()
        pygame.display.init()
        screen = pygame.display.set_mode(view_config.window_size)
        pygame.display.set_caption("CARLA driver view")
        clock = pygame.time.Clock()
        started = last_tick = time.monotonic()
        last_frame = None
        last_sim = None
        while time.monotonic() - started < options.duration_s:
            events = pygame.event.get()
            if any(event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE) for event in events):
                status = "USER_ABORT"
                break
            if ego.get_velocity().length() > 0.05:
                raise P7Error("EGO_MOVED_IN_STATIC_DEV_SMOKE")
            snapshots = [feed.snapshot() for feed in viewer.feeds]
            ready = all(item.image is not None for item in snapshots)
            if not ready and time.monotonic() - started > 5:
                raise P7Error("CAMERA_STARTUP_TIMEOUT")
            if ready and any(item.last_host_receipt_seconds is None or time.perf_counter() - item.last_host_receipt_seconds > 1 for item in snapshots):
                raise P7Error("CAMERA_STALE")
            timestamp = snapshot_timestamp(world)
            session.last_timestamp = timestamp
            simulation = timestamp.carla_snapshot
            if simulation is None:
                raise P7Error("SNAPSHOT_UNAVAILABLE")
            if ready and simulation.frame != last_frame:
                if last_sim is not None and not 0 < simulation.simulation_seconds - last_sim <= 0.5:
                    raise P7Error("SIMULATION_TIME_GAP_OR_REGRESSION")
                last_frame, last_sim, last_tick = simulation.frame, simulation.simulation_seconds, time.monotonic()
                foreign = tuple(actor for actor in world.get_actors().filter("vehicle.*")
                                if actor.id != ego.id and actor.id not in owned.actors)
                driver: DriverInput = viewer._collect_driver_input(events)
                if enabled:
                    last = observe(actors, timestamp, driver=driver,
                                   collision=None if collision is None else collision.take(), foreign=foreign)
                    hazard.update(last)
                    if hazard.state in (HazardState.ABORTED, HazardState.COMPLETED):
                        status = hazard.state.value
                        break
                    actors.advance(simulation.simulation_seconds)
                traffic.advance(simulation.simulation_seconds, (ego, *foreign))
                control = ego.get_control()
                session.logger.write_telemetry(session.segment, timestamp, VehicleState(
                    simulation.simulation_seconds, ego.get_velocity().length() * 3.6,
                    control.steer, control.throttle, control.brake, waypoint.lane_id, "NOT_OBSERVED"))
            elif ready and time.monotonic() - last_tick > 2:
                raise P7Error("CARLA_SNAPSHOT_STALLED")
            screen.fill((0, 0, 0))
            viewer.draw_preview(screen)
            pygame.display.flip()
            clock.tick(30)
        else:
            status = "TIME_CAP" if enabled else "TRAFFIC_ONLY_COMPLETE"
    except (RuntimeError, ValueError, OSError, pygame.error) as error:
        status = "ABORTED"
        failures.append(str(error))
    except KeyboardInterrupt:
        status = "USER_ABORT"
    finally:
        timestamp = TimestampEnvelope(HostClockTimestamp(time.monotonic_ns(), time.time_ns()))
        if enabled and hazard.state not in (HazardState.ABORTED, HazardState.COMPLETED):
            hazard.abort(replace(last, timestamp=timestamp), failures[-1] if failures else status)
        pygame.quit()
        for action in (() if collision is None else (collision.close,)) + (() if viewer is None else (viewer.close,)):
            try:
                action()
            except RuntimeError as error:
                failures.append(f"SENSOR_CLEANUP:{error}")
        failures.extend(owned.close())
        session.emit(EventKind.CLEANUP, TimestampEnvelope(HostClockTimestamp(time.monotonic_ns(), time.time_ns())),
                     {"failures": failures, "remaining_owned_ids": list(owned.actors), "ego_destroyed": False})
    return {"status": status if not failures else "ABORTED", "errors": failures,
            "hazard_state": hazard.state.value, "metrics": hazard.metrics.snapshot(),
            "traffic_count": len(traffic.vehicles), "map": actors.world_map.name,
            "source_lane_id": corridor.points[0].lane_id, "target_lane_id": corridor.target_lane_id,
            "route_origin": {"x": corridor.origin.x, "y": corridor.origin.y, "yaw_rad": corridor.origin.yaw_rad},
            "trigger_source": session.config.hazard.trigger_source.value,
            "termination_requested": hazard.termination_requested, "ego_control_changed": False}


def catalog(host: str, port: int, *, ego_id: int | None = None, corridor_length_m: float = 90) -> dict[str, Json]:
    client = carla.Client(host, port)
    client.set_timeout(2)
    world = client.get_world()
    report = {"status": "CATALOG_ONLY_NO_SPAWN", "map": world.get_map().name,
            "cargo_candidates": sorted(bp.id for bp in world.get_blueprint_library().filter("static.prop.box*")),
            "vehicles": [{"id": actor.id, "type_id": actor.type_id} for actor in world.get_actors().filter("vehicle.*")]}
    if ego_id is not None:
        diagnostic = {"ego_id": ego_id, "requested_length_m": corridor_length_m,
                      "route_approved": False, "actor_mutations": False, "status": "INCOMPATIBLE"}
        report["corridor"] = diagnostic
        ego = world.get_actor(ego_id)
        if ego is None or not ego.type_id.startswith("vehicle."):
            diagnostic["reason"] = "EXPLICIT_EGO_NOT_FOUND_OR_NOT_VEHICLE"
            return report
        transform = ego.get_transform()
        diagnostic["ego_pose"] = {"x": transform.location.x, "y": transform.location.y,
                                  "z": transform.location.z, "yaw": transform.rotation.yaw}
        diagnostic["ego_speed_mps"] = ego.get_velocity().length()
        start = world.get_map().get_waypoint(transform.location, project_to_road=False, lane_type=carla.LaneType.Driving)
        if start is None:
            diagnostic["reason"] = "EGO_NOT_ON_DRIVING_LANE"
            return report
        try:
            corridor = build_corridor(start, corridor_length_m, require_target=True)
            diagnostic.update({"status": "COMPATIBLE", "sample_count": len(corridor.points),
                               "source_lane_id": corridor.points[0].lane_id,
                               "target_lane_id": corridor.target_lane_id,
                               "remaining_live_checks": "occupancy/assets/occlusion/parking; not a route approval"})
        except CorridorError as error:
            diagnostic["first_failure"] = asdict(error)
        return report
    return report
