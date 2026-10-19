"""Copy collision evidence without RPCs, actor retention or safety filtering."""

from src.p7.moving_route import pose_record


def _optional(read):
    try:
        return read()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        # Missing diagnostic metadata must never suppress the collision itself.
        return None


def vector_record(vector):
    return {axis: float(getattr(vector, axis)) for axis in ("x", "y", "z")}


def collision_record(event, sensor_id, receipt_ns):
    return {
        "actor_id": event.other_actor.id,
        "frame": event.frame,
        "simulation_seconds": event.timestamp,
        "receipt_monotonic_ns": receipt_ns,
        "sensor_actor_id": sensor_id,
        "ego_actor_id": _optional(lambda: event.actor.id),
        "other_actor_type_id": _optional(lambda: str(event.other_actor.type_id)),
        "other_actor_semantic_tags": _optional(
            lambda: list(event.other_actor.semantic_tags)
        ),
        "normal_impulse": _optional(lambda: vector_record(event.normal_impulse)),
        "sensor_pose": _optional(lambda: pose_record(event.transform)),
    }


def collision_frame_ego(snapshot, ego_id):
    if snapshot is None:
        return None

    def read():
        actor = snapshot.find(ego_id)
        return {
            "frame": int(snapshot.frame),
            "simulation_seconds": snapshot.timestamp.elapsed_seconds,
            "pose": pose_record(actor.get_transform()),
            "velocity_mps": vector_record(actor.get_velocity()),
            "basis": "CACHED_COLLISION_FRAME_WORLD_SNAPSHOT",
        }

    return _optional(read)


def observation_context(sample):
    return _optional(
        lambda: {
            "basis": "LATEST_CONTROL_OBSERVATION_NOT_COLLISION_FRAME",
            "frame": sample.timestamp.carla_snapshot.frame,
            "simulation_seconds": sample.timestamp.carla_snapshot.simulation_seconds,
            "pose": pose_record(sample.transform),
            "speed_kmh": sample.speed_kmh,
            "road_id": sample.road_id,
            "section_id": sample.section_id,
            "lane_id": sample.lane_id,
            "lane_width_m": sample.lane_width_m,
            "control_readback": {
                "throttle": sample.control.throttle,
                "brake": sample.control.brake,
                "steer": sample.control.steer,
            },
        }
    )
