"""Operator-chosen participant numbers and exclusive names for new pilot runs."""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

SESSION_TYPES = {"1": "test", "2": "pilot"}


def participant_number(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"[0-9]{1,6}", value) is None:
        raise ValueError("참가자 번호는 숫자 1~6자리로 입력하세요. 예: 001, 002")
    return value.zfill(3)


def select_participant(value: str | None) -> str:
    if value is not None:
        return participant_number(value)
    while True:
        try:
            return participant_number(
                input("참가자 번호를 입력하세요 (예: 001, 002): ")
            )
        except ValueError as error:
            print(error)


def select_session_type(value: str | None) -> str:
    if value is not None:
        return SESSION_TYPES.get(value, value)
    while True:
        choice = input("저장 유형을 선택하세요 (1=test, 2=pilot): ").strip().lower()
        selected = SESSION_TYPES.get(choice, choice)
        if selected in ("test", "pilot"):
            return selected
        print("1 또는 2를 입력하세요.")


def reserve_pilot_run(root: Path, participant_id: str, session_type: str) -> str:
    """Never reuse a directory, archive, or durable launch/registration claim.

    Reserving the name separately leaves creation of the actual run directory
    to the existing session lifecycle. A cancelled/crashed run is not resumed.
    """
    if participant_id != participant_number(participant_id) or session_type not in (
        "test",
        "pilot",
    ):
        raise ValueError("INVALID_PILOT_IDENTITY")
    claims = root / "_pilot_launches"
    claims.mkdir(parents=True, exist_ok=True)
    base = f"{participant_id}_{session_type}"
    sequence = 1
    while True:
        run_id = base if sequence == 1 else f"{base}_{sequence:02d}"
        sequence += 1
        if (root / run_id).exists() or (root / "_exports" / f"{run_id}.zip").exists():
            continue
        if (root / "_registration_claims" / "_pilot_runs" / f"{run_id}.json").exists():
            continue
        try:
            stream = (claims / f"{run_id}.json").open("x", encoding="utf-8")
        except FileExistsError:
            continue
        with stream:
            json.dump(
                {
                    "participant_id": participant_id,
                    "session_type": session_type,
                    "run_id": run_id,
                    "archive_name": f"{run_id}.zip",
                    "launch_id": uuid4().hex,
                    "reserved_at_utc": datetime.now(timezone.utc).isoformat(),
                    "restart_policy": "NEW_RUN_NO_AUTOMATIC_RESUME",
                },
                stream,
                indent=2,
            )
            stream.flush()
            os.fsync(stream.fileno())
        return run_id
