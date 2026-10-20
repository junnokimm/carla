"""Read-only M2 ordering gate. No RPC, control writes or synthetic exit outcomes."""

from src.experiment.context import ExperimentModule
from src.experiment.exit_assistance_types import ExitContext

POLICY = "M2_PRESENTED_AND_PHYSICALLY_RESOLVED_EXIT_BEFORE_HAZARD"


def sequence_blocker(coordinator):
    if coordinator is None:
        return "P5_M2_EXIT_COORDINATOR_UNAVAILABLE"
    if (coordinator.config.module is not ExperimentModule.MODULE_2
            or coordinator.config.context is not ExitContext.MODULE):
        return "P5_M2_EXIT_CONTEXT_MISMATCH"
    presented = coordinator._t0
    if presented is None:
        return "P5_M2_EXIT_NOT_PRESENTED"
    ended = coordinator._opportunity_end_timestamp
    if ended is None:
        return "P5_M2_EXIT_NOT_PHYSICALLY_RESOLVED"
    if ended.host.monotonic_ns < presented.host.monotonic_ns:
        return "P5_M2_EXIT_SEQUENCE_TIMESTAMP_INVALID"
    return None


def sequence_evidence(coordinator):
    reason = sequence_blocker(coordinator)
    presented = None if coordinator is None else coordinator._t0
    ended = None if coordinator is None else coordinator._opportunity_end_timestamp
    return {
        "policy": POLICY,
        "status": "PASS" if reason is None else "FAIL",
        "reason": reason,
        "exit_event_id": None if coordinator is None else coordinator.config.lc_event_id,
        "exit_module": None if coordinator is None else coordinator.config.module.value,
        "exit_status": None if coordinator is None else coordinator.status.value,
        "presented_host_monotonic_ns": None if presented is None else presented.host.monotonic_ns,
        "resolved_host_monotonic_ns": None if ended is None else ended.host.monotonic_ns,
        "physical_outcome": None if coordinator is None else coordinator._opportunity_end_outcome,
        "resolution_reason": None if coordinator is None else coordinator._opportunity_end_reason,
    }
