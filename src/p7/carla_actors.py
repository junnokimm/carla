from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from math import atan2, cos, degrees, radians, sin

import carla

from src.experiment.timestamp import TimestampEnvelope
from src.p7.carla_route import CarlaCorridor
from src.p7.carla_visibility import camera_transform, projected_bounds
from src.p7.config import HazardConfig
from src.p7.geometry import smooth_lane_change
from src.p7.ownership import ActorRole, OwnedActors
from src.p7.types import EventKind, Json, P7Error

type Emit = Callable[[EventKind, TimestampEnvelope, dict[str, Json]], None]


@dataclass(frozen=True, slots=True)
class LocalActorBoxes:
    ego: carla.BoundingBox
    lead: carla.BoundingBox
    cargo: carla.BoundingBox


class CarlaHazardActors:
    def __init__(
        self,
        world: carla.World,
        ego: carla.Actor,
        corridor: CarlaCorridor,
        *,
        config: HazardConfig,
        owned: OwnedActors,
        emit: Emit,
        run_id: str,
    ) -> None:
        self.world, self.ego, self.corridor = world, ego, corridor
        self.world_map = world.get_map()
        self.config, self.owned, self.emit, self.run_id = config, owned, emit, run_id
        self.lead: carla.Actor | None = None
        self.cargo: carla.Actor | None = None
        self.local_boxes: LocalActorBoxes | None = None
        self.cargo_transform: carla.Transform | None = None
        self._motion_origin: float | None = None
        self._reveal_origin: float | None = None
        self._lead_lift = 0.0
        self.complete = False

    def spawn(
        self,
        blueprint_id: str,
        transform: carla.Transform,
        role: ActorRole,
        timestamp: TimestampEnvelope,
    ) -> carla.Actor:
        try:
            blueprint = self.world.get_blueprint_library().find(blueprint_id)
            if blueprint.has_attribute("role_name"):
                blueprint.set_attribute("role_name", f"p7:{self.run_id}:{role.value}")
            actor = self.world.try_spawn_actor(blueprint, transform)
            if actor is None:
                raise P7Error(f"SPAWN_FAILED:{role.value}:{blueprint_id}")
            self.owned.add(actor, role)
            if not actor.is_alive or actor.type_id != blueprint_id:
                raise P7Error(f"SPAWN_IDENTITY_UNVERIFIED:{role.value}")
            actor.set_simulate_physics(False)
            self.emit(
                EventKind.SPAWN,
                timestamp,
                {
                    "actor_id": actor.id,
                    "role": role.value,
                    "blueprint": blueprint_id,
                    "motion_policy": "DEV_KINEMATIC_NOT_PHYSICS_VALIDATED",
                    "requested_pose": {
                        "x": transform.location.x,
                        "y": transform.location.y,
                        "z": transform.location.z,
                        "yaw": transform.rotation.yaw,
                    },
                },
            )
            return actor
        except RuntimeError as error:
            self.emit(
                EventKind.SPAWN_FAILURE,
                timestamp,
                {"role": role.value, "blueprint": blueprint_id, "reason": str(error)},
            )
            raise P7Error(f"SPAWN_FAILED:{role.value}:{error}") from error

    def prepare(self, timestamp: TimestampEnvelope) -> None:
        if self.lead is not None or self.cargo is not None:
            raise P7Error("HAZARD_ACTORS_ALREADY_PREPARED")
        if not self.corridor.target:
            raise P7Error("LEAD_PATH_IMPOSSIBLE")
        for blueprint in (self.config.lead_blueprint, self.config.obstacle_blueprint):
            try:
                self.world.get_blueprint_library().find(blueprint)
            except RuntimeError as error:
                raise P7Error(
                    f"REQUIRED_BLUEPRINT_UNAVAILABLE:{blueprint}; no automatic fallback"
                ) from error
        lead_pose = self.corridor.transform_at(self.config.lead_initial_gap_m)
        cargo_pose = self.corridor.transform_at(self.config.obstacle_station_m)
        lead_pose.location.z += 0.5
        cargo_pose.location.z += 0.5
        self.cargo = self.spawn(
            self.config.obstacle_blueprint, cargo_pose, ActorRole.CARGO, timestamp
        )
        self.lead = self.spawn(
            self.config.lead_blueprint, lead_pose, ActorRole.LEAD, timestamp
        )
        ego_box = self.ego.bounding_box
        lead_box = self.lead.bounding_box
        cargo_box = self.cargo.bounding_box
        self._lead_lift = lead_box.extent.z - lead_box.location.z + 0.03
        lead_pose.location.z = (
            self.corridor.transform_at(self.config.lead_initial_gap_m).location.z
            + self._lead_lift
        )
        cargo_pose.location.z = (
            self.corridor.transform_at(self.config.obstacle_station_m).location.z
            + cargo_box.extent.z
            - cargo_box.location.z
            + 0.03
        )
        self.owned.require(self.lead.id, ActorRole.LEAD)
        self.lead.set_transform(lead_pose)
        self.owned.require(self.cargo.id, ActorRole.CARGO)
        self.cargo.set_transform(cargo_pose)
        self.cargo_transform = cargo_pose
        camera = camera_transform(self.ego)
        lead_projection = projected_bounds(lead_box, camera, transform=lead_pose)
        cargo_projection = projected_bounds(cargo_box, camera, transform=cargo_pose)
        occluded = lead_projection.contains(cargo_projection)
        self.emit(
            EventKind.ACTORS_SETUP,
            timestamp,
            {
                "initial_occlusion_proxy": occluded,
                "physical_occlusion_validated": False,
                "lead_projected_bounds": asdict(lead_projection),
                "cargo_projected_bounds": asdict(cargo_projection),
                "target_lane_id": self.corridor.target_lane_id,
                "pose_source": "COMMANDED_SETUP_POSE",
                "lead_pose": {
                    "x": lead_pose.location.x,
                    "y": lead_pose.location.y,
                    "z": lead_pose.location.z,
                    "yaw": lead_pose.rotation.yaw,
                },
                "cargo_pose": {
                    "x": cargo_pose.location.x,
                    "y": cargo_pose.location.y,
                    "z": cargo_pose.location.z,
                    "yaw": cargo_pose.rotation.yaw,
                },
                "cargo_extent": {
                    "x": cargo_box.extent.x,
                    "y": cargo_box.extent.y,
                    "z": cargo_box.extent.z,
                },
            },
        )
        if not occluded:
            raise P7Error(
                "INITIAL_OCCLUSION_PROXY_FAILED; mesh occlusion requires separate live review"
            )
        self.local_boxes = LocalActorBoxes(ego_box, lead_box, cargo_box)

    def begin_reveal(self, timestamp: TimestampEnvelope) -> None:
        if self._reveal_origin is not None:
            raise P7Error("LEAD_REVEAL_ALREADY_STARTED")
        if timestamp.carla_snapshot is None:
            raise P7Error("SIMULATION_TIME_REQUIRED_FOR_ACTOR_MOTION")
        self._reveal_origin = timestamp.carla_snapshot.simulation_seconds

    def advance(self, simulation_s: float) -> None:
        if self.lead is None:
            return
        if not self.lead.is_alive or self.cargo is None or not self.cargo.is_alive:
            raise P7Error("HAZARD_ACTOR_LOST")
        if self._motion_origin is None:
            self._motion_origin = simulation_s
        elapsed = simulation_s - self._motion_origin
        if elapsed < 0:
            raise P7Error("SIMULATION_TIME_REGRESSED")
        fraction, lateral_rate = (
            (0.0, 0.0)
            if self._reveal_origin is None
            else smooth_lane_change(
                simulation_s - self._reveal_origin, self.config.lane_change_duration_s
            )
        )
        distance = self.config.lead_initial_gap_m + self.config.lead_speed_mps * elapsed
        yaw = degrees(
            atan2(self.corridor.width * lateral_rate, self.config.lead_speed_mps)
        )
        pose = self.corridor.transform_at(
            distance, lateral_fraction=fraction, yaw_offset=yaw
        )
        pose.location.z += self._lead_lift
        self.owned.require(self.lead.id, ActorRole.LEAD)
        self.lead.set_transform(pose)
        self.complete = fraction == 1

    @property
    def obstacle_stationary(self) -> bool:
        if (
            self.cargo is None
            or self.cargo_transform is None
            or not self.cargo.is_alive
        ):
            return False
        current = self.cargo.get_transform()
        yaw_delta = radians(current.rotation.yaw - self.cargo_transform.rotation.yaw)
        return (
            current.location.distance(self.cargo_transform.location) <= 0.005
            and abs(degrees(atan2(sin(yaw_delta), cos(yaw_delta)))) <= 0.01
            and self.cargo.get_velocity().length() <= 0.005
        )
