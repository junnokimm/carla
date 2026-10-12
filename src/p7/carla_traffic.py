from __future__ import annotations

from math import hypot

import carla

from src.experiment.timestamp import TimestampEnvelope
from src.p7.carla_actors import CarlaHazardActors
from src.p7.carla_route import CarlaCorridor, build_corridor
from src.p7.config import P7Config
from src.p7.ownership import ActorRole
from src.p7.traffic import SpawnCandidate, TrafficScenario
from src.p7.types import EventKind, P7Error


def in_hazard_zone(corridor: CarlaCorridor, transform: carla.Transform) -> bool:
    s, lateral = corridor.coordinates(transform)
    return -8 <= s <= corridor.points[-1].distance_m + 8 and -3 <= lateral <= 2 * corridor.width + 3


class CarlaTraffic:
    def __init__(self, actors: CarlaHazardActors, config: P7Config) -> None:
        self.actors, self.config = actors, config
        self.paths: dict[str, CarlaCorridor] = {}
        self.vehicles: list[tuple[carla.Actor, CarlaCorridor, float]] = []
        self._origin_s: float | None = None

    def prepare(self, timestamp: TimestampEnvelope) -> None:
        config, actors = self.config.traffic, self.actors
        if not config.enabled or config.vehicle_count == 0:
            actors.emit(EventKind.TRAFFIC_PLAN, timestamp, {"enabled": config.enabled, "count": 0, "seed": config.seed})
            return
        world_map = actors.world_map
        candidates = []
        ego = actors.ego.get_location()
        spawns = sorted(world_map.get_spawn_points(), key=lambda pose: (pose.location.x, pose.location.y, pose.location.z, pose.rotation.yaw))
        for index, transform in enumerate(spawns):
            if hypot(transform.location.x - ego.x, transform.location.y - ego.y) < config.exclusion_m:
                continue
            try:
                start = world_map.get_waypoint(transform.location, project_to_road=False, lane_type=carla.LaneType.Driving)
                if start is None:
                    raise P7Error("NO_DRIVING_WAYPOINT")
                path = build_corridor(start, config.speed_mps * 20 + 4, require_target=False)
                if any(in_hazard_zone(actors.corridor, pose) for pose in path.source):
                    raise P7Error("HAZARD_CORRIDOR_RESERVED")
                if any(hypot(pose.location.x - zone.x, pose.location.y - zone.y) <= zone.radius_m + 3
                       for pose in path.source for zone in self.config.protected_zones):
                    raise P7Error("P5_OR_OTHER_PROTECTED_ZONE")
                key = f"spawn-{index}"
                self.paths[key] = path
                candidates.append(SpawnCandidate(key, transform.location.x, transform.location.y))
            except P7Error as error:
                actors.emit(EventKind.TRAFFIC_REJECT, timestamp, {"spawn_index": index, "reason": str(error)})
        actors.emit(
            EventKind.TRAFFIC_SCAN,
            timestamp,
            {
                "total_spawn_count": len(spawns),
                "inspected_count": len(spawns),
                "valid_candidate_count": len(candidates),
                "requested_count": config.vehicle_count,
            },
        )

        def spawn(candidate: SpawnCandidate) -> None:
            path = self.paths[candidate.key]
            pose = path.transform_at(0)
            pose.location.z += 0.5
            actor = actors.spawn(config.vehicle_blueprint, pose, ActorRole.TRAFFIC, timestamp)
            bounding_box = actor.bounding_box
            lift = bounding_box.extent.z - bounding_box.location.z + 0.03
            self.vehicles.append((actor, path, lift))

        scenario = TrafficScenario(config, spawn=spawn)
        scenario.start(tuple(candidates), ego=(ego.x, ego.y), protected=self.config.protected_zones)
        actors.emit(EventKind.TRAFFIC_PLAN, timestamp, {"seed": config.seed, "count": len(scenario.selected),
                    "selected": [candidate.key for candidate in scenario.selected],
                    "protected_zones_configured": bool(self.config.protected_zones),
                    "parameter_status": "DEV_ONLY"})

    def advance(self, simulation_s: float, foreign: tuple[carla.Actor, ...]) -> None:
        if self._origin_s is None:
            self._origin_s = simulation_s
        distance = self.config.traffic.speed_mps * (simulation_s - self._origin_s)
        for actor, path, lift in self.vehicles:
            if not actor.is_alive:
                raise P7Error("TRAFFIC_ACTOR_LOST")
            pose = path.transform_at(distance)
            pose.location.z += lift
            if any(pose.location.distance(other.get_location()) < 6 for other in foreign):
                raise P7Error("TRAFFIC_PATH_BLOCKED_BY_FOREIGN_ACTOR")
            self.actors.owned.require(actor.id, ActorRole.TRAFFIC)
            actor.set_transform(pose)
