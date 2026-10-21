"""No lead vehicle, no ego control writes: reveal one owned static obstacle."""

from math import isfinite, radians, tan

import carla

from src.p7.moving_backend import MovingHazardBackend
from src.p7.carla_visibility import projected_bounds
from src.p7.moving_config import TARGET_TTC_S
from src.p7.moving_route import MovingRoute, angle_delta, box_record, pose_record
from src.p7.ownership import ActorRole
from src.p7.types import P7Error


class ClearRoadHazardBackend(MovingHazardBackend):
    def begin_readiness(self, sample, skipped_reason="EARLIER_CONDITION_NOT_PASSED"):
        """Fresh evidence for this attempt; never recycle a previous PASS."""
        self.preparation_attempt = {
            "ego_speed_kmh": sample.speed_kmh,
            "target_speed_kmh": None,
            "speed_basis": "MEASURED_AT_REVEAL",
            "minimum_exposure_speed_kmh": self.config.minimum_exposure_speed_kmh,
            "checks": {
                name: {"status": "NOT_CHECKED", "reason": skipped_reason}
                for name in (
                    "moving_speed_eligible", "route_available",
                    "junction_free", "approach_clear", "camera_available",
                )
            },
        }

    def _checked(self, name, **evidence):
        self.preparation_attempt["checks"][name] = {"status": "PASS", **evidence}

    def preflight(self, sample, preparation_s=None):
        self.begin_readiness(sample)
        self._readiness_check = "moving_speed_eligible"
        try:
            return self._check_preflight(sample)
        except Exception as error:
            self.preparation_attempt["checks"][self._readiness_check] = {
                "status": "FAIL" if isinstance(error, P7Error) else "ERROR",
                "reason": str(error),
                "error_type": type(error).__name__,
            }
            raise

    def _check_preflight(self, sample):
        speed = sample.speed_kmh / 3.6
        if not isfinite(speed):
            raise P7Error("CURRENT_SPEED_UNAVAILABLE")
        if sample.speed_kmh < self.config.minimum_exposure_speed_kmh:
            raise P7Error("STOPPED_OR_LOW_SPEED")
        self._checked("moving_speed_eligible", value_kmh=sample.speed_kmh,
                      minimum_kmh=self.config.minimum_exposure_speed_kmh)
        # Preview envelope, followed by exact vertex clearance at reveal.
        length = speed * TARGET_TTC_S + self.ego_box.extent.x + 12.0
        self._readiness_check = "route_available"
        route = MovingRoute.select(self.world_map, sample.transform, length,
                                   self.config.route_step_m,
                                   diagnostics=self.preparation_attempt, adjacent=False)
        self._checked("route_available", checked_ahead_m=length)
        self._readiness_check = "junction_free"
        if any(wp.is_junction for wp in route.source):
            raise P7Error("JUNCTION_IN_HAZARD_CORRIDOR")
        self._checked("junction_free")
        # One inventory per readiness attempt, not per rendering frame.
        self._readiness_check = "approach_clear"
        vehicles = self.world.get_actors().filter("vehicle.*")
        for actor in vehicles:
            if actor.id == self.ego.id:
                continue
            observed = sample.world_snapshot.find(actor.id)
            if observed is None:
                raise P7Error("TRAFFIC_SNAPSHOT_UNAVAILABLE")
            pose = observed.get_transform()
            station, lateral = route.project(pose.location)
            half_width = route.source[0].lane_width / 2 + actor.bounding_box.extent.y
            if (station + actor.bounding_box.extent.x >= 0
                    and station - actor.bounding_box.extent.x <= length
                    and abs(lateral) < half_width
                    # project() clamps stations at the route endpoints. A car
                    # far behind/beyond the corridor must not become a blocker.
                    and pose.location.distance(route.pose(station).location)
                    < half_width + actor.bounding_box.extent.x + 2.0
                    and abs(pose.location.z - route.pose(station).location.z) < 3):
                self.preparation_attempt["blocking_actor_id"] = actor.id
                raise P7Error("APPROACH_CORRIDOR_OCCUPIED")
        self._checked("approach_clear", lead_vehicle_count=0)
        self.route = route
        self._readiness_check = "camera_available"
        self._refresh_camera()
        self._checked("camera_available")
        self.preparation_attempt.update(
            road_id=route.source[0].road_id, section_id=route.source[0].section_id,
            lane_id=route.source[0].lane_id, checked_ahead_m=length,
            lead_vehicle_count=0, preparation_s=0.0)
        return dict(self.preparation_attempt)

    def prepare(self, sample):
        if self.cargo is not None:
            raise P7Error("DUPLICATE_CARGO_PREPARATION")
        # Stage out of view so asset bounds and pair filtering are established
        # before any visible obstacle. No lead car is ever created.
        hidden = carla.Transform(carla.Location(sample.transform.location), sample.transform.rotation)
        hidden.location.z -= 50.0
        self.cargo = self._spawn(self.config.cargo_blueprint, hidden, ActorRole.CARGO)
        self.cargo_box = self.cargo.bounding_box
        self.cargo.set_simulate_physics(False)
        tm = self.session.bundle.traffic_manager_backend
        if tm is not None:
            self.pair_changed = True
            tm.set_hazard_pair_detection(self.cargo, False)
            self.pair_revision = tm.hazard_pair_revision
            self.emit("tm_pair", {"ego_id": self.ego.id, "cargo_id": self.cargo.id,
                                  "before": True, "after": False,
                                  "reason": "NO_CARGO_AUTO_AVOIDANCE",
                                  "scope": "NEW_OWNED_PAIR_ONLY",
                                  "physical_collisions": "UNCHANGED"})
        self.emit("actors_prepared", {"lead_actor_id": None, "cargo_actor_id": self.cargo.id,
                                     "cargo_bounds": box_record(self.cargo_box),
                                     "visibility": "STAGED_BELOW_ROAD",
                                     "ego_control_writes": False})
        return 0.0

    def reveal(self, sample, *, before_placement=None):
        if self.motion_started is not None:
            raise P7Error("DUPLICATE_REVEAL")
        # Rebuild from the current lane/pose, rather than a stale preparation route.
        self.preflight(sample)
        ego_front = max(self.route.project(v)[0]
                        for v in self.ego_box.get_world_vertices(sample.transform))
        desired_clearance = sample.speed_kmh / 3.6 * TARGET_TTC_S
        station = ego_front + desired_clearance + self.cargo_box.extent.x
        for _ in range(3):
            pose = self.route.pose(station)
            cargo_rear = min(self.route.project(v)[0]
                             for v in self.cargo_box.get_world_vertices(pose))
            station += ego_front + desired_clearance - cargo_rear
        self.cargo_pose = self.route.pose(station)
        self.cargo_pose.location.z += self.cargo_box.extent.z - self.cargo_box.location.z + 0.02
        try:
            self._check_placement_geometry(sample)
        except P7Error as error:
            self.preparation_attempt["checks"]["placement_geometry"] = {
                "status": "FAIL", "reason": str(error),
            }
            raise
        self._checked("placement_geometry", visibility="PROJECTED_BOUNDS_PROXY")
        conditions = dict(self.preparation_attempt)
        # Reserve the one occurrence only after all retryable readiness checks.
        # Once a placement is requested it must never be repeated blindly.
        if before_placement is not None:
            before_placement()
        self.cargo_placement_request_frame = int(sample.world_snapshot.frame)
        self.motion_started = sample.timestamp.carla_snapshot.simulation_seconds
        self.cargo.set_transform(self.cargo_pose)
        self.reveal_record = {
            **conditions, "actor_id": self.cargo.id, "target_pose": pose_record(self.cargo_pose),
            "after_frame": self.cargo_placement_request_frame,
            "placement_speed_kmh": sample.speed_kmh,
            "requested_bumper_clearance_m": desired_clearance,
            "distance_basis": "CURRENT_SPEED_MPS_TIMES_4_ALONG_LANE_BETWEEN_BOUNDS",
            "target_ttc_s": TARGET_TTC_S, "ego_control_writes": False,
            "automatic_avoidance": "NONE", "physical_collisions": "UNCHANGED"}
        self.emit("obstacle_reveal_requested", self.reveal_record)

    def _check_placement_geometry(self, sample):
        # Screen/path suitability must defer BEFORE claiming/placing an actor,
        # rather than consume the only event on a bend or during a steering correction.
        station, lateral = self.route.project(sample.transform.location)
        _, cargo_lateral = self.route.project(self.cargo_pose.location)
        if (abs(lateral - cargo_lateral) >= self.ego_box.extent.y + self.cargo_box.extent.y
                or abs(angle_delta(sample.transform.rotation.yaw,
                                   self.route.pose(station).rotation.yaw)) >= radians(8)):
            raise P7Error("PLACEMENT_COLLISION_PATH_NOT_READY")
        bounds = projected_bounds(self.cargo_box, self.camera_at(sample.transform),
                                  transform=self.cargo_pose)
        horizontal = tan(radians(self.fov / 2))
        vertical = horizontal / self.aspect
        if not (bounds.left < horizontal and bounds.right > -horizontal
                and bounds.bottom < vertical and bounds.top > -vertical):
            raise P7Error("PLACEMENT_OUTSIDE_FRONT_CAMERA")

    def geometry(self, snapshot, camera=None):
        if self.motion_started is None:
            return None
        return super().geometry(snapshot, camera)

    def advance(self, sample, geometry):
        # No lead controller, target velocity write, brake, steering or NoA toggle.
        tm = self.session.bundle.traffic_manager_backend
        if self.pair_changed and tm.hazard_pair_revision != self.pair_revision:
            self.pair_revision = tm.hazard_pair_revision
            self.emit("tm_pair", {"reason": "PAIR_REASSERTED_AFTER_DRIVER_NOA_ACTIVATION",
                                  "revision": self.pair_revision, "after": False})
