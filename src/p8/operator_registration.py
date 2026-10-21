from __future__ import annotations

import json
import re
from pathlib import Path
from typing import assert_never

from src.experiment.assignment import ParticipantAssignment, load_assignment_csv
from src.experiment.context import ParticipantId
from src.p8.model import Mode, P8Error
from src.p8.operator_model import RegistrationRequest

SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}")


def claim_registration(
    root: Path,
    request: RegistrationRequest,
    mode: Mode,
) -> ParticipantAssignment:
    if request.session_type is not None and (
        mode is not Mode.DEV_REHEARSAL or request.session_type not in ("test", "pilot")
    ):
        raise P8Error("SESSION_TYPE_REQUIRES_NEW_DEV_TEST_OR_PILOT_RUN")
    if SAFE_ID.fullmatch(request.participant_id) is None:
        raise P8Error("RESEARCH_ID_MUST_BE_PATH_SAFE_NOT_A_NAME")
    if SAFE_ID.fullmatch(request.run_id) is None:
        raise P8Error("RUN_ID_MUST_BE_PATH_SAFE")
    if (root / request.run_id).exists():
        raise P8Error("RUN_ID_ALREADY_CLAIMED_NO_AUTO_RESUME")
    match mode:
        case Mode.DEV_REHEARSAL:
            if request.assignment_file is None:
                if request.m1_condition is None or request.m2_condition is None:
                    raise P8Error("DEV_CONDITIONS_MUST_BE_EXPLICIT")
                assignment = ParticipantAssignment(
                    ParticipantId(request.participant_id),
                    request.m1_condition,
                    request.m2_condition,
                    request.route_id or "DEV_OPERATOR_ROUTE_BLOCKED",
                    "P8_DEV_REHEARSAL",
                    "REAR_DISABLED_UNVALIDATED",
                    "P8_OPERATOR_NOT_PARTICIPANT_READY",
                )
            else:
                if request.m1_condition is not None or request.m2_condition is not None:
                    raise P8Error("PREASSIGNED_CONDITIONS_CANNOT_BE_OVERRIDDEN")
                assignment = load_assignment_csv(request.assignment_file).lookup(
                    ParticipantId(request.participant_id)
                )
        case Mode.PARTICIPANT:
            if request.assignment_file is None:
                raise P8Error("PARTICIPANT_REQUIRES_PREASSIGNED_CSV")
            assignment = load_assignment_csv(request.assignment_file).lookup(
                ParticipantId(request.participant_id)
            )
        case Mode.DRY | Mode.ACCELERATED | Mode.DEV_LIVE_DRIVE:
            raise P8Error("OPERATOR_CONTROLLER_MODE_REQUIRED")
        case unreachable:
            assert_never(unreachable)
    if request.route_id is not None and assignment.route_id != request.route_id:
        raise P8Error("ASSIGNMENT_EXIT_ROUTE_MISMATCH")
    claims = root / "_registration_claims"
    if request.session_type is not None:
        # Explicitly labelled test/pilot launches may repeat a participant number,
        # but must still claim a new run. All other registration policies stay intact.
        claims = claims / "_pilot_runs"
    claims.mkdir(parents=True, exist_ok=True)
    claim_id = request.run_id if request.session_type is not None else request.participant_id
    target = claims / f"{claim_id}.json"
    try:
        with target.open("x", encoding="utf-8") as output:
            json.dump(
                {
                    "participant_id": request.participant_id,
                    "run_id": request.run_id,
                    "mode": mode.value,
                    "session_type": request.session_type,
                    "restart_policy": "NO_AUTOMATIC_RESUME",
                },
                output,
            )
    except FileExistsError as error:
        if request.session_type is not None:
            raise P8Error("RUN_ID_ALREADY_CLAIMED_NO_AUTO_RESUME") from error
        raise P8Error("PARTICIPANT_ID_ALREADY_CLAIMED_NO_AUTO_RESUME") from error
    return assignment
