from __future__ import annotations

from src.experiment.timestamp import TimestampEnvelope
from src.p7.metrics import canonical_hazard_record
from src.p7.types import HazardOutcome, Json
from src.p8.operator_hazard_contracts import HazardExecutionResources
from src.p8.services import SessionServices


def blocked_hazard_record(
    resources: HazardExecutionResources | None,
    services: SessionServices,
    timestamp: TimestampEnvelope,
) -> dict[str, Json]:
    identities = None if resources is None else resources.identities
    context = services.context
    return {
        **canonical_hazard_record(HazardOutcome.UNOBSERVABLE),
        "participant_id": context.participant_id,
        "run_id": context.study_run_id,
        "session_elapsed_s": services.clock.project_elapsed_seconds(timestamp.host),
        "m2_condition": context.module_2_condition.value,
        "auto_state": None if identities is None else identities.control_mode,
        "execution_status": "BLOCKED",
        "observation_status": "NOT_OBSERVED",
        "blocker": "MOVING_HAZARD_INTEGRATION_BLOCKED",
        "trigger_definition_status": "BLOCKED_UNAPPROVED",
        "actor_preparation_status": "NOT_ATTEMPTED",
        "visibility_status": "FIRST_VISIBLE_FRAME_UNVERIFIED",
        "first_displayed_frame": None,
        "raw_observation": None,
        "candidate_observation": None,
        "rescue_status": "OWNER_NOT_BOUND",
        "tm_obstacle_response_status": "UNVERIFIED",
        "module_2_runtime_status": (
            "CLOSED_BEFORE_FINAL_HAZARD"
            if identities is None
            else "BOUND_SHARED_RUNTIME"
        ),
        "binding_status": "NOT_BOUND" if identities is None else "BOUND",
        "observation_readiness": "BLOCKED_UNAPPROVED",
        "runtime_continuity_scope": (
            "NOT_BOUND" if identities is None else "SAME_LIVE_SESSION_PRE_SHUTDOWN"
        ),
        "control_activity_status": (
            "INACTIVE_NOT_BOUND"
            if identities is None
            else "LIVE_M2_STATE_AT_HAZARD_ENTRY"
        ),
        "sensor_activity_status": (
            "INACTIVE_NOT_BOUND"
            if identities is None
            else "LIVE_VIEWER_BOUND_AT_HAZARD_ENTRY"
        ),
        "shutdown_brake_status_at_binding": (
            None if identities is None else identities.shutdown_brake_status
        ),
        "shared_resource_identities": (
            None
            if identities is None
            else {
                "session": identities.session,
                "world": identities.world,
                "hero": identities.hero,
                "viewer_sensor_owner": identities.viewer_sensor_owner,
                "control": identities.control,
                "persistence": identities.persistence,
                "logger": identities.logger,
                "session_clock": identities.session_clock,
            }
        ),
    }
