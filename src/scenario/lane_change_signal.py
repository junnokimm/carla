from __future__ import annotations

import carla

from src.experiment.automation import AutomationState, DrivingControlMode
from src.experiment.timestamp import TimestampEnvelope
from src.scenario.lane_change_contracts import (
    LaneChangeEventPersistence,
    LaneChangeVehicle,
)
from src.scenario.lane_change_events import LaneChangeEventSink
from src.scenario.lane_change_lifecycle import LaneChangeTracker


def clear_completed_signal(
    vehicle: LaneChangeVehicle,
    persistence: LaneChangeEventPersistence,
    events: LaneChangeEventSink,
    state: AutomationState,
    tracker: LaneChangeTracker,
    completion_timestamp: TimestampEnvelope,
) -> None:
    if state.control_mode is not DrivingControlMode.NOA_ACTIVE:
        return
    selected = (
        carla.VehicleLightState.LeftBlinker
        if tracker.direction.value == "LEFT"
        else carla.VehicleLightState.RightBlinker
    )
    try:
        before = vehicle.get_light_state()
    except RuntimeError as error:
        failed_at = persistence.source.get_host_timestamp()
        events.record(
            "lane_change_signal_autooff",
            {
                "request_id": tracker.request_id,
                "context": tracker.context,
                "completion_host_monotonic_ns": completion_timestamp.host.monotonic_ns,
                "before_host_monotonic_ns": None,
                "before_result": f"UNVERIFIED:{type(error).__name__}:{error}",
                "command_host_monotonic_ns": None,
                "readback_host_monotonic_ns": None,
                "before": None,
                "command": None,
                "setter_result": "NOT_ATTEMPTED",
                "setter_return": None,
                "readback": None,
                "readback_result": "NOT_ATTEMPTED",
                "selected_cleared": None,
            },
            failed_at,
        )
        return
    before_timestamp = persistence.source.get_host_timestamp()
    command = carla.VehicleLightState(before & ~selected)
    command_timestamp = persistence.source.get_host_timestamp()
    setter_result = "RETURNED"
    setter_return: None = None
    try:
        setter_return = vehicle.set_light_state(command)
    except RuntimeError as error:
        setter_result = f"UNVERIFIED:{type(error).__name__}:{error}"
    try:
        readback = vehicle.get_light_state()
    except RuntimeError as error:
        readback = None
        readback_timestamp = persistence.source.get_host_timestamp()
        readback_host_monotonic_ns = None
        readback_result = f"UNVERIFIED:{type(error).__name__}:{error}"
    else:
        readback_timestamp = persistence.source.get_host_timestamp()
        readback_host_monotonic_ns = readback_timestamp.host.monotonic_ns
        readback_result = "RETURNED"
    events.record(
        "lane_change_signal_autooff",
        {
            "request_id": tracker.request_id,
            "context": tracker.context,
            "completion_host_monotonic_ns": completion_timestamp.host.monotonic_ns,
            "before_host_monotonic_ns": before_timestamp.host.monotonic_ns,
            "before_result": "RETURNED",
            "command_host_monotonic_ns": command_timestamp.host.monotonic_ns,
            "readback_host_monotonic_ns": readback_host_monotonic_ns,
            "before": int(before),
            "command": int(command),
            "setter_result": setter_result,
            "setter_return": setter_return,
            "readback": None if readback is None else int(readback),
            "readback_result": readback_result,
            "selected_cleared": (
                None if readback is None else not bool(readback & selected)
            ),
        },
        readback_timestamp,
    )
