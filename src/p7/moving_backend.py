"""Physical moving-ego CARLA adapter. No ego motion/control writer lives here."""

import time
from collections import OrderedDict, deque
from math import atan2, cos, isfinite, radians, sin, tan
from threading import Lock

import carla

from src.p7.carla_visibility import projected_bounds
from src.p7.collision_evidence import (
    collision_frame_ego,
    collision_record,
    observation_context,
)
from src.p7.geometry import smooth_lane_change
from src.p7.moving_config import TARGET_TTC_S
from src.p7.moving_route import MovingRoute, angle_delta, box_record, pose_record
from src.p7.ownership import ActorRole, OwnedActors
from src.p7.types import P7Error


class MovingHazardBackend:
    def __init__(self, session, config, emit):
        self.session, self.config, self.emit = session, config, emit
        self.world, self.ego = session.world, session.hero
        self.world_map = self.world.get_map()
        self.owned = OwnedActors(self.ego.id)
        self.lead = self.cargo = self.route = None
        self.cargo_pose = None
        self.cargo_placement_request_frame = None
        self.cargo_placement_frame = None
        self.cargo_placement_observation = None
        self.lead_box = self.cargo_box = None
        self.ego_box = self.ego.bounding_box
        self.motion_started = None
        self.reveal_control_delivered = False
        self.lead_handed_to_tm = False
        self.previous_sim = None
        self.steer = 0.0
        self.pair_changed = False
        self.pair_revision = None
        self.retired = False
        self._tick_id = None
        self._sensor = None
        self._frames = OrderedDict()
        self._collisions = deque()
        self._lock = Lock()
        self._camera_local = None
        self.fov = None
        self.preparation_s = config.preparation_s
        self.preparation_attempt = None

    def start_observation(self):
        self._refresh_camera()
        self._tick_id = self.world.on_tick(self._capture)
        blueprint = self.world.get_blueprint_library().find("sensor.other.collision")
        self._sensor = self.world.spawn_actor(
            blueprint, carla.Transform(), attach_to=self.ego
        )
        self.owned.add(self._sensor, ActorRole.SENSOR)
        self._sensor.listen(self._collision)

    def _refresh_camera(self):
        front = next(
            (feed for feed in self.session.viewer.feeds if feed.role == "front"), None
        )
        if front is None:
            raise P7Error("M2_V1_FRONT_CAMERA_REQUIRED")
        camera = front.sensor.get_transform()
        ego = self.ego.get_transform()
        matrix = ego.get_inverse_matrix()
        vector = (camera.location.x, camera.location.y, camera.location.z, 1)
        local = [sum(row[i] * vector[i] for i in range(4)) for row in matrix[:3]]
        self._camera_local = carla.Transform(
            carla.Location(*local),
            carla.Rotation(
                pitch=camera.rotation.pitch - ego.rotation.pitch,
                yaw=camera.rotation.yaw - ego.rotation.yaw,
                roll=camera.rotation.roll - ego.rotation.roll,
            ),
        )
        self.fov = float(front.sensor.attributes["fov"])
        self.aspect = float(front.sensor.attributes["image_size_x"]) / float(
            front.sensor.attributes["image_size_y"]
        )
        self.emit(
            "camera_basis",
            {
                "sensor_actor_id": front.sensor.id,
                "fov": self.fov,
                "relative_pose": pose_record(self._camera_local),
                "method": "CURRENT_FRONT_SENSOR_RELATIVE_POSE_GEOMETRY_PROXY",
            },
        )

    def _capture(self, snapshot):
        # No RPC, world tick or full actor enumeration in the callback.
        with self._lock:
            self._frames[int(snapshot.frame)] = (
                snapshot,
                time.monotonic_ns(),
                self.session.monotonic_clock(),
            )
            while len(self._frames) > 256:
                self._frames.popitem(last=False)

    def _collision(self, event):
        record = collision_record(
            event,
            None if self._sensor is None else self._sensor.id,
            time.monotonic_ns(),
        )
        with self._lock:
            self._collisions.append(record)

    def failures(self, sample):
        with self._lock:
            collisions = list(self._collisions)
            self._collisions.clear()
            frames = [self._frames.get(item["frame"]) for item in collisions]
        if collisions:
            for item, entry in zip(collisions, frames, strict=True):
                item["collision_frame_ego"] = collision_frame_ego(
                    None if entry is None else entry[0], self.ego.id
                )
            return {
                "reason": "COLLISION",
                "collisions": collisions,
                "observation_context": observation_context(sample),
                "hazard_actors_spawned": self.lead is not None or self.cargo is not None,
            }
        if sample.lane_transform is None or sample.lane_width_m is None:
            return {
                "reason": "ROAD_DEPARTURE_PROXY",
                "method": "NO_DRIVING_WAYPOINT",
                "observation_context": observation_context(sample),
            }
        pose = sample.lane_transform
        yaw = radians(pose.rotation.yaw)
        lateral = -(sample.transform.location.x - pose.location.x) * sin(yaw) + (
            sample.transform.location.y - pose.location.y
        ) * cos(yaw)
        if abs(lateral) > sample.lane_width_m / 2 + self.ego_box.extent.y:
            return {
                "reason": "ROAD_DEPARTURE_PROXY",
                "lateral_m": lateral,
                "method": "BODY_FULLY_OUTSIDE_PROJECTED_DRIVING_LANE",
                "observation_context": observation_context(sample),
                "lane_pose": pose_record(pose),
                "vehicle_half_width_m": self.ego_box.extent.y,
                "threshold_m": sample.lane_width_m / 2 + self.ego_box.extent.y,
            }
        return None

    def camera_at(self, ego_pose):
        local = self._camera_local
        return carla.Transform(
            ego_pose.transform(carla.Location(local.location)),
            carla.Rotation(
                pitch=ego_pose.rotation.pitch + local.rotation.pitch,
                yaw=ego_pose.rotation.yaw + local.rotation.yaw,
                roll=ego_pose.rotation.roll + local.rotation.roll,
            ),
        )

    def preflight(self, sample, preparation_s=None):
        self.preparation_s = (
            self.config.preparation_s if preparation_s is None else preparation_s
        )
        speed = sample.speed_kmh / 3.6
        self.preparation_attempt = {
            "ego_pose": pose_record(sample.transform),
            "ego_speed_mps": speed,
            "preparation_s": self.preparation_s,
        }
        if not isfinite(speed) or speed <= 0.5:
            raise P7Error("APPROACH_SPEED_UNAVAILABLE")
        lead_station = max(
            self.config.minimum_lead_gap_m, speed * self.config.lead_headway_s
        )
        cargo_station = (
            speed * (self.preparation_s + TARGET_TTC_S)
            + self.ego_box.extent.x
            + self.ego_box.location.x
            + 1.0
        )
        if cargo_station - lead_station < 8.0:
            raise P7Error("INSUFFICIENT_LEAD_CARGO_CLEARANCE")
        # Cover the whole lead maneuver plus its existing handoff/lookahead
        # margins. A later road feature outside this scene is not a blocker.
        length = max(
            cargo_station + self.ego_box.extent.x + 1.0,
            lead_station
            + speed * (self.preparation_s + self.config.lane_change_s)
            + max(10.0, speed * 2)
            + max(4.0, speed * 0.4),
        )
        self.preparation_attempt["required_route_length_m"] = length
        route = MovingRoute.select(
            self.world_map,
            sample.transform,
            length,
            self.config.route_step_m,
            diagnostics=self.preparation_attempt,
        )
        self.preparation_attempt.update(
            road_id=route.source[0].road_id,
            section_id=route.source[0].section_id,
            lane_id=route.source[0].lane_id,
            starts_in_junction=route.source[0].is_junction,
        )
        # One inventory per preparation attempt, never per frame.
        vehicles = self.world.get_actors().filter("vehicle.*")
        for actor in vehicles:
            if actor.id == self.ego.id:
                continue
            point = actor.get_transform().location
            station, lateral = route.project(point)
            if (
                0 <= station <= length
                and abs(lateral) < 2 * route.source[0].lane_width
                and point.distance(route.pose(station).location)
                < 2 * route.source[0].lane_width
            ):
                raise P7Error("SPAWN_CORRIDOR_OCCUPIED")
        self._refresh_camera()
        self._preview_occlusion(sample, route, lead_station, cargo_station)
        self.route = route
        self.lead_station, self.cargo_station = lead_station, cargo_station
        return {
            "side": route.side,
            "road_id": route.source[0].road_id,
            "section_id": route.source[0].section_id,
            "lane_id": route.source[0].lane_id,
            "cargo_station_m": cargo_station,
            "lead_station_m": lead_station,
            "speed_reference_mps": speed,
            "preparation_s": self.preparation_s,
            "required_route_length_m": length,
            "occlusion_preview": "INITIAL_BOUNDS_AND_PREPARATION_LATERAL_ENVELOPE",
        }

    def _preview_occlusion(self, sample, route, lead_station, cargo_station):
        # The default lead uses the same vehicle asset as the live ego. Use its
        # measured bounds; do not spawn a visible trial car or invent dimensions.
        if self.ego.type_id != self.config.lead_blueprint:
            raise P7Error("LEAD_PREVIEW_BOUNDS_UNAVAILABLE")
        envelope = carla.BoundingBox(carla.Location(z=1), carla.Vector3D(1, 1, 1))
        cargo = route.pose(cargo_station)
        speed = sample.speed_kmh / 3.6
        ground_offset = sample.transform.location.z - route.pose(0).location.z
        for i in range(61):
            elapsed = self.preparation_s * i / 60
            ego = sample.transform if i == 0 else route.pose(speed * elapsed)
            if i:
                ego.location.z += ground_offset
            camera = self.camera_at(ego)
            lead = route.pose(lead_station + speed * elapsed)
            cb = projected_bounds(envelope, camera, transform=cargo)
            # Check the route surface and the existing initial spawn lift. The
            # actual actor bounds are checked again after spawning/placement.
            for lift in (0.0, 0.3):
                pose = carla.Transform(carla.Location(lead.location), lead.rotation)
                pose.location.z += lift
                lb = projected_bounds(self.ego_box, camera, transform=pose)
                # The conservative 2 m high pre-spawn envelope is only an
                # initial visibility guard, not the asset's actual height.
                # Check lateral exposure along the curve now; actual vertical
                # bounds along the approach are verified after hidden placement.
                if (i == 0 and not lb.contains(cb)) or not (
                    lb.left <= cb.left and cb.right <= lb.right
                ):
                    self.preparation_attempt["occlusion_failure_preview_s"] = elapsed
                    raise P7Error("PREPARATION_OCCLUSION_UNSUITABLE")

    def _validate_prepared_occlusion(self, sample):
        speed = sample.speed_kmh / 3.6
        ground_offset = sample.transform.location.z - self.route.pose(0).location.z
        for i in range(61):
            elapsed = self.preparation_s * i / 60
            ego = sample.transform if i == 0 else self.route.pose(speed * elapsed)
            if i:
                ego.location.z += ground_offset
            camera = self.camera_at(ego)
            cargo = projected_bounds(self.cargo_box, camera, transform=self.cargo_pose)
            lead = self.route.pose(self.lead_station + speed * elapsed)
            for lift in (0.0, 0.3):
                pose = carla.Transform(carla.Location(lead.location), lead.rotation)
                pose.location.z += lift
                if not projected_bounds(self.lead_box, camera, transform=pose).contains(
                    cargo
                ):
                    raise P7Error("PREPARATION_ACTUAL_OCCLUSION_UNSUITABLE")

    def _spawn(self, blueprint_id, pose, role):
        blueprint = self.world.get_blueprint_library().find(blueprint_id)
        if blueprint.has_attribute("role_name"):
            blueprint.set_attribute(
                "role_name", f"p7-v1:{self.session.config.run_id}:{role.value}"
            )
        actor = self.world.try_spawn_actor(blueprint, pose)
        if actor is None:
            raise P7Error(f"SPAWN_FAILED:{role.value}:{blueprint_id}")
        self.owned.add(actor, role)
        if not actor.is_alive or actor.type_id != blueprint_id:
            raise P7Error(f"SPAWN_IDENTITY_UNVERIFIED:{role.value}")
        self.emit(
            "actor_spawn",
            {
                "actor_id": actor.id,
                "blueprint": actor.type_id,
                "pose": pose_record(actor.get_transform()),
                "bounds": box_record(actor.bounding_box),
            },
        )
        return actor

    def prepare(self, sample):
        if self.lead is not None or self.cargo is not None:
            raise P7Error("M2_V1_ACTORS_ALREADY_PREPARED")
        self._refresh_camera()
        speed = sample.speed_kmh / 3.6
        lead_pose = self.route.pose(self.lead_station)
        lead_pose.location.z += 0.3
        self.lead = self._spawn(self.config.lead_blueprint, lead_pose, ActorRole.LEAD)
        self.lead_box = self.lead.bounding_box
        forward = lead_pose.get_forward_vector()
        self.lead.set_target_velocity(
            carla.Vector3D(
                x=speed * forward.x, y=speed * forward.y, z=speed * forward.z
            )
        )
        cargo_pose = self.route.pose(self.cargo_station)
        # Conservative box01 envelope for PRE-spawn occlusion check, not an asset resize.
        envelope = carla.BoundingBox(carla.Location(z=1), carla.Vector3D(1, 1, 1))
        camera = self.camera_at(sample.transform)
        if not projected_bounds(self.lead_box, camera, transform=lead_pose).contains(
            projected_bounds(envelope, camera, transform=cargo_pose)
        ):
            raise P7Error("PRESPAWN_OCCLUSION_UNSUITABLE")
        self.cargo = self._spawn(
            self.config.cargo_blueprint, cargo_pose, ActorRole.CARGO
        )
        self.cargo_box = self.cargo.bounding_box
        # Only the prop is static; vehicle physics and every collision stay enabled.
        self.cargo.set_simulate_physics(False)
        self.cargo_pose = carla.Transform(
            carla.Location(cargo_pose.location), cargo_pose.rotation
        )
        self.cargo_pose.location.z += (
            self.cargo_box.extent.z - self.cargo_box.location.z + 0.02
        )
        self.cargo_placement_request_frame = int(self.world.get_snapshot().frame)
        # get_transform() is a last-tick read, not acknowledgement of this write.
        # Use the requested target for preview; confirm it on a later snapshot
        # before establishing the stationary baseline or exposing the cargo.
        self.cargo.set_transform(self.cargo_pose)
        self.emit(
            "cargo_placement_requested",
            {
                "actor_id": self.cargo.id,
                "target_pose": pose_record(self.cargo_pose),
                "after_frame": self.cargo_placement_request_frame,
                "bounds": box_record(self.cargo_box),
                "placement_adjustments": 1,
            },
        )
        if not projected_bounds(self.lead_box, camera, transform=lead_pose).contains(
            projected_bounds(self.cargo_box, camera, transform=self.cargo_pose)
        ):
            raise P7Error("INITIAL_OCCLUSION_FAILED")
        self._validate_prepared_occlusion(sample)
        tm = self.session.bundle.traffic_manager_backend
        if tm is not None:
            # New owned ID: prior pair state is the TM default True.
            self.pair_changed = True
            tm.set_hazard_pair_detection(self.cargo, False)
            self.pair_revision = tm.hazard_pair_revision
            self.emit(
                "tm_pair",
                {
                    "ego_id": self.ego.id,
                    "cargo_id": self.cargo.id,
                    "before": True,
                    "after": False,
                    "reason": "NO_CARGO_AUTO_AVOIDANCE",
                    "scope": "NEW_OWNED_PAIR_ONLY",
                    "effect": "UNVERIFIED_STATIC_PROP_PIPELINE",
                },
            )
        delay = self.predict_first_exposure(sample)
        self.emit(
            "actors_prepared",
            {
                "ego_bounds": box_record(self.ego_box),
                "cargo_static_physics": True,
                "cargo_placement_status": "AWAITING_WORLD_SNAPSHOT",
                "physical_collisions": "UNCHANGED",
                "initial_occlusion_proxy": True,
                "predicted_exposure_delay_s": delay,
                "lead_physics": "ENABLED_APPLY_CONTROL",
                "lead_initial_velocity_mps": speed,
            },
        )
        return delay

    def predict_first_exposure(self, sample):
        """Sample the planned quintic path for *partial*, not disjoint, exposure."""
        speed = sample.speed_kmh / 3.6
        lead_pose = self.lead.get_transform()
        lead_station = self.route.project(lead_pose.location)[0]
        ego_station = self.route.project(sample.transform.location)[0]
        lead_ground_offset = (
            lead_pose.location.z - self.route.pose(lead_station).location.z
        )
        ego_ground_offset = (
            sample.transform.location.z - self.route.pose(ego_station).location.z
        )
        for i in range(1, 61):
            delay = self.config.lane_change_s * i / 60
            fraction, _ = smooth_lane_change(delay, self.config.lane_change_s)
            lead = self.route.pose(lead_station + speed * self.preparation_s, fraction)
            lead.location.z += lead_ground_offset
            ego = self.route.pose(ego_station + speed * self.preparation_s)
            ego.location.z += ego_ground_offset
            camera = self.camera_at(ego)
            if not projected_bounds(self.lead_box, camera, transform=lead).contains(
                projected_bounds(self.cargo_box, camera, transform=self.cargo_pose)
            ):
                return delay
        return self.config.lane_change_s

    def begin_reveal(self, simulation_s):
        if self.motion_started is not None:
            raise P7Error("DUPLICATE_REVEAL")
        self.motion_started = simulation_s

    def _confirm_cargo_placement(self, snapshot, cargo):
        if int(snapshot.frame) <= self.cargo_placement_request_frame:
            return False
        observed = cargo.get_transform()
        target = self.cargo_pose
        distance = observed.location.distance(target.location)
        speed = cargo.get_velocity().length()
        rotation_error = max(
            abs(
                angle_delta(
                    getattr(observed.rotation, axis), getattr(target.rotation, axis)
                )
            )
            for axis in ("yaw", "pitch", "roll")
        )
        self.cargo_placement_observation = {
            "frame": int(snapshot.frame),
            "simulation_seconds": snapshot.timestamp.elapsed_seconds,
            "observed_pose": pose_record(observed),
            "target_pose": pose_record(target),
            "position_error_m": distance,
            "speed_mps": speed,
            "rotation_error_rad": rotation_error,
        }
        # Keep the existing 5 mm / 5 mm/s / 0.01 degree stationary tolerances.
        if not (
            distance <= 0.005 and speed <= 0.005 and rotation_error <= radians(0.01)
        ):
            return False
        self.cargo_pose = carla.Transform(
            carla.Location(observed.location), observed.rotation
        )
        self.cargo_placement_frame = int(snapshot.frame)
        self.emit(
            "cargo_placed",
            {
                "actor_id": self.cargo.id,
                "pose": pose_record(self.cargo_pose),
                "bounds": box_record(self.cargo_box),
                "placement_adjustments": 1,
                "placement_status": "CONFIRMED_WORLD_SNAPSHOT",
                "request_after_frame": self.cargo_placement_request_frame,
                **self.cargo_placement_observation,
            },
        )
        return True

    def geometry(self, snapshot, camera=None):
        camera_source = (
            "ACTUAL_SELECTED_IMAGE_TRANSFORM"
            if camera is not None
            else "CACHED_FRONT_RELATIVE_POSE"
        )
        if self.cargo is None or self.retired:
            return None
        if not self.lead.is_alive or not self.cargo.is_alive:
            raise P7Error("HAZARD_ACTOR_LOST")
        ego, lead, cargo = (
            snapshot.find(actor.id) for actor in (self.ego, self.lead, self.cargo)
        )
        if any(actor is None for actor in (ego, lead, cargo)):
            return None  # Spawn acknowledgements can precede their first snapshot.
        if self.cargo_placement_frame is None:
            # Rendering can use an older snapshot. Only the control/observation
            # path acknowledges placement; rendering never changes its baseline.
            if camera is not None or not self._confirm_cargo_placement(snapshot, cargo):
                return None
        if int(snapshot.frame) < self.cargo_placement_frame:
            return None
        ep, lp, cp = (actor.get_transform() for actor in (ego, lead, cargo))
        es, el = self.route.project(ep.location)
        ls, ll = self.route.project(lp.location)
        cs, cl = self.route.project(cp.location)
        tangent = self.route.pose(es).get_forward_vector()
        velocity = ego.get_velocity()
        closing = (
            velocity.x * tangent.x + velocity.y * tangent.y + velocity.z * tangent.z
        )

        def longitudinal_extent(box, pose):
            vertices = box.get_world_vertices(pose)
            return [self.route.project(vertex)[0] for vertex in vertices]

        clearance = min(longitudinal_extent(self.cargo_box, cp)) - max(
            longitudinal_extent(self.ego_box, ep)
        )
        on_path = abs(
            el - cl
        ) < self.ego_box.extent.y + self.cargo_box.extent.y and abs(
            angle_delta(ep.rotation.yaw, self.route.pose(es).rotation.yaw)
        ) < radians(8)
        ttc = clearance / closing if closing > 0 and clearance > 0 and on_path else None
        stationary = (
            cp.location.distance(self.cargo_pose.location) <= 0.005
            and cargo.get_velocity().length() <= 0.005
            and abs(angle_delta(cp.rotation.yaw, self.cargo_pose.rotation.yaw))
            <= radians(0.01)
        )
        visible = fully_clear = None
        if cs > es + self.ego_box.extent.x:
            try:
                camera = camera or self.camera_at(ep)
                lb = projected_bounds(self.lead_box, camera, transform=lp)
                cb = projected_bounds(self.cargo_box, camera, transform=cp)
                horizontal = tan(radians(self.fov / 2))
                vertical = horizontal / self.aspect
                in_view = (
                    cb.left < horizontal
                    and cb.right > -horizontal
                    and cb.bottom < vertical
                    and cb.top > -vertical
                )
                lead_in_front = es < ls < cs
                visible = in_view and not (lead_in_front and lb.contains(cb))
                fully_clear = in_view and (not lead_in_front or lb.disjoint(cb))
            except P7Error:
                pass
        with self._lock:
            frame_entry = self._frames.get(int(snapshot.frame))
        result = {
            "camera_source": camera_source,
            "camera_pose": None if camera is None else pose_record(camera),
            "ttc_method": "CONNECTED_ROUTE_VERTEX_FRONT_CLEARANCE_OVER_TANGENT_CLOSING_SPEED",
            "frame": int(snapshot.frame),
            "simulation_seconds": snapshot.timestamp.elapsed_seconds,
            "ego_pose": pose_record(ep),
            "lead_pose": pose_record(lp),
            "cargo_pose": pose_record(cp),
            "ego_speed_mps": velocity.length(),
            "lead_speed_mps": lead.get_velocity().length(),
            "closing_speed_mps": closing,
            "clearance_m": clearance,
            "ttc_candidate_s": ttc,
            "ttc_invalid_reason": None
            if ttc is not None
            else "NOT_POSITIVE_APPROACH_ON_COLLISION_PATH",
            "collision_path_proxy": on_path,
            "partial_visible_proxy": visible,
            "fully_clear_proxy": fully_clear,
            "cargo_stationary": stationary,
            "lead_station_m": ls,
            "ego_station_m": es,
            "lead_lateral_m": ll,
            "lead_move_observed_complete": self.motion_started is not None
            and abs(ll - self.route.project(self.route.pose(ls, 1).location)[1]) < 0.35,
            "passed": es > cs + self.ego_box.extent.x + self.cargo_box.extent.x,
        }
        if frame_entry is not None:
            result["snapshot_callback_runtime_monotonic_s"] = frame_entry[2]
            result["snapshot_callback_receipt_monotonic_ns"] = frame_entry[1]
        return result

    def advance(self, sample, geometry):
        if self.lead is None or self.retired or geometry is None:
            return
        tm = self.session.bundle.traffic_manager_backend
        if self.pair_changed and tm.hazard_pair_revision != self.pair_revision:
            self.pair_revision = tm.hazard_pair_revision
            self.emit(
                "tm_pair",
                {
                    "ego_id": self.ego.id,
                    "cargo_id": self.cargo.id,
                    "before": "REGISTRATION_DEPENDENT",
                    "after": False,
                    "reason": "PAIR_REASSERTED_AFTER_DRIVER_NOA_ACTIVATION",
                    "revision": self.pair_revision,
                },
            )
        sim = geometry["simulation_seconds"]
        if self.previous_sim is not None and sim <= self.previous_sim:
            return
        dt = 0.05 if self.previous_sim is None else min(0.2, sim - self.previous_sim)
        self.previous_sim = sim
        snapshot = sample.world_snapshot.find(self.lead.id)
        pose = snapshot.get_transform()
        speed = snapshot.get_velocity().length()
        if self.lead_handed_to_tm:
            return
        if geometry["lead_station_m"] >= self.route.stations[-1] - max(10, speed * 2):
            self._handoff_lead("LOCAL_CORRIDOR_END")
            return
        gap = geometry["lead_station_m"] - geometry["ego_station_m"]
        desired_gap = max(
            self.config.minimum_lead_gap_m,
            sample.speed_kmh / 3.6 * self.config.lead_headway_s,
        )
        target_speed = max(
            0, sample.speed_kmh / 3.6 + self.config.gap_gain * (desired_gap - gap)
        )
        fraction = (
            0
            if self.motion_started is None
            else smooth_lane_change(
                sim - self.motion_started, self.config.lane_change_s
            )[0]
        )
        target = self.route.pose(
            geometry["lead_station_m"] + max(4, speed * 0.4), fraction
        )
        heading = atan2(
            target.location.y - pose.location.y, target.location.x - pose.location.x
        )
        error = atan2(
            sin(heading - radians(pose.rotation.yaw)),
            cos(heading - radians(pose.rotation.yaw)),
        )
        requested = max(-0.7, min(0.7, self.config.steering_gain * error))
        slew = self.config.steering_slew_per_s * dt
        self.steer += max(-slew, min(slew, requested - self.steer))
        longitudinal = self.config.speed_gain * (target_speed - speed)
        self.owned.require(self.lead.id, ActorRole.LEAD)
        control = carla.VehicleControl(
            throttle=max(0, min(1, longitudinal)),
            brake=max(0, min(1, -longitudinal)),
            steer=self.steer,
        )
        started_ns = time.monotonic_ns()
        self.lead.apply_control(control)
        geometry["lead_control_command"] = {
            "started_monotonic_ns": started_ns,
            "completed_monotonic_ns": time.monotonic_ns(),
            "reference_ego_speed_mps": sample.speed_kmh / 3.6,
            "target_lead_speed_mps": target_speed,
            "desired_headway_m": desired_gap,
            "throttle": control.throttle,
            "brake": control.brake,
            "steer": control.steer,
            "status": "RETURNED_NOT_MOVEMENT_COMPLETION",
        }
        if self.motion_started is not None and not self.reveal_control_delivered:
            self.reveal_control_delivered = True
            self.emit("lead_control_delivery", geometry["lead_control_command"])

    def display_geometry(self, frame):
        if (
            frame.camera_transform is None
            or frame.simulation_seconds is None
            or not isfinite(frame.simulation_seconds)
            or type(frame.frame) is not int
            or frame.frame < 0
        ):
            return None
        with self._lock:
            entry = self._frames.get(frame.frame)
        if (
            entry is None
            or abs(entry[0].timestamp.elapsed_seconds - frame.simulation_seconds) > 1e-5
        ):
            return None
        result = self.geometry(entry[0], frame.camera_transform)
        if result is not None:
            result["snapshot_callback_receipt_monotonic_ns"] = entry[1]
            result["snapshot_callback_runtime_monotonic_s"] = entry[2]
        return result

    def retire_if_behind(self, geometry):
        if self.retired or geometry is None or not geometry["passed"]:
            return False
        # Beyond both forward and mirror useful range; do not delete at response onset.
        ego_pose, cargo_pose = geometry["ego_pose"], geometry["cargo_pose"]
        longitudinal = (ego_pose["x"] - cargo_pose["x"]) * cos(
            radians(ego_pose["yaw"])
        ) + (ego_pose["y"] - cargo_pose["y"]) * sin(radians(ego_pose["yaw"]))
        if longitudinal < self.config.cleanup_behind_m:
            return False
        self._restore_pair()
        failures = self.owned.close(ActorRole.CARGO)
        if failures:
            raise P7Error(f"CARGO_CLEANUP_FAILED:{failures}")
        self.retired = True
        if not self.lead_handed_to_tm:
            self._handoff_lead("CARGO_RETIRED")
        self.emit(
            "cargo_retired",
            {
                "reason": "PASSED_AND_80M_BEHIND",
                "repeat_exposure_prevented": True,
                "visibility_limit": "DISTANCE_PROXY_NOT_PIXEL_VALIDATED",
            },
        )
        return True

    def _handoff_lead(self, reason):
        # Stop targeting a clamped path endpoint behind a moving vehicle.
        # Only this scene-owned lead is handed to TM; ego authority is untouched.
        self.owned.require(self.lead.id, ActorRole.LEAD)
        self.lead.set_autopilot(True, self.session.config.traffic_manager_port)
        self.lead_handed_to_tm = True
        self.emit(
            "lead_route_handoff",
            {"actor_id": self.lead.id, "reason": reason, "driver_outcome": None},
        )

    def _restore_pair(self):
        if self.pair_changed:
            self.session.bundle.traffic_manager_backend.set_hazard_pair_detection(
                self.cargo, True
            )
            self.pair_changed = False
            self.emit(
                "tm_pair",
                {
                    "ego_id": self.ego.id,
                    "cargo_id": self.cargo.id,
                    "before": False,
                    "after": True,
                    "reason": "RESTORE_NEW_PAIR_DEFAULT",
                },
            )

    def cleanup(self):
        failures = []
        for action in (
            self._restore_pair,
            lambda: (
                self.world.remove_on_tick(self._tick_id)
                if self._tick_id is not None
                else None
            ),
            lambda: self._sensor.stop() if self._sensor is not None else None,
        ):
            try:
                action()
            except (OSError, RuntimeError, ValueError) as error:
                failures.append(str(error))
        failures.extend(self.owned.close())
        result = {"failures": failures, "remaining_actor_ids": list(self.owned.actors)}
        with self._lock:
            if self._collisions:
                result["pending_collisions"] = list(self._collisions)
                self._collisions.clear()
        return result
