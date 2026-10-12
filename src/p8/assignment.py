from pathlib import Path
from uuid import uuid4

from src.experiment.assignment import ParticipantAssignment, load_assignment_csv
from src.experiment.context import Module1Condition, Module2Condition, ParticipantId


def development_assignment(m1: Module1Condition = Module1Condition.NO_SURT,
                           m2: Module2Condition = Module2Condition.MANUAL) -> ParticipantAssignment:
    return ParticipantAssignment(ParticipantId("DEV-P8-" + uuid4().hex), m1, m2,
                                 "DEV_STUB_P8_ROUTE", "P8_DEV_ONLY", "p6-candidate-unvalidated-2",
                                 "P8_FOUNDATION_NOT_PARTICIPANT_READY")


def assigned_participant(path: Path, participant_id: str) -> ParticipantAssignment:
    return load_assignment_csv(path).lookup(ParticipantId(participant_id))
