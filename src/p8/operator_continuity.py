from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace

from src.p8.model import P8Error
from src.p8.services import SessionServices
from src.scenario.research_noa_runtime import ResearchNoARunner
from src.scenario.research_noa_session import ResearchNoASession


@dataclass(frozen=True, slots=True)
class SharedRuntimeIdentities:
    session: int
    world: int
    hero: int
    viewer_sensor_owner: int
    control: int
    persistence: int
    logger: int
    session_clock: int
    control_mode: str
    shutdown_brake_status: str


class OperatorRuntimeContinuity:
    def __init__(self) -> None:
        self._session: ResearchNoASession | None = None
        self._identities: SharedRuntimeIdentities | None = None

    @contextmanager
    def session(
        self,
        runner: ResearchNoARunner,
        services: SessionServices,
        *,
        handoff_enabled: bool,
    ) -> Iterator[ResearchNoASession]:
        if self._session is not None:
            raise P8Error("MODULE_2_RUNTIME_ALREADY_BOUND")
        borrowed = runner.borrowed_log
        if (
            borrowed is None
            or borrowed.logger is not services.logger
            or borrowed.session_clock is not services.clock
            or borrowed.study_run is not services.context
        ):
            raise P8Error("OPERATOR_SHARED_RESOURCE_IDENTITY_MISMATCH")
        with runner.session() as actual:
            if not handoff_enabled:
                yield actual
                return
            persistence = actual.persistence
            if persistence is None:
                raise P8Error("OPERATOR_PERSISTENCE_MISSING")
            self._session = actual
            self._identities = SharedRuntimeIdentities(
                session=id(actual),
                world=id(actual.world),
                hero=id(actual.hero),
                viewer_sensor_owner=id(actual.viewer),
                control=id(actual.bundle),
                persistence=id(persistence),
                logger=id(services.logger),
                session_clock=id(services.clock),
                control_mode=actual.bundle.automation_runtime.state.control_mode.value,
                shutdown_brake_status=(
                    actual.shutdown_brake_outcome.command_call_outcome.value
                ),
            )
            try:
                yield actual
            finally:
                self._session = None
                self._identities = None

    @property
    def active(self) -> bool:
        return self._session is not None

    @property
    def identities(self) -> SharedRuntimeIdentities | None:
        return self._identities

    @property
    def bound_session(self) -> ResearchNoASession:
        if self._session is None:
            raise P8Error("MODULE_2_RUNTIME_NOT_AVAILABLE_FOR_FINAL_HAZARD")
        return self._session

    def bind_final_hazard(self, services: SessionServices) -> SharedRuntimeIdentities:
        session = self._session
        identities = self._identities
        if session is None or identities is None:
            raise P8Error("MODULE_2_RUNTIME_NOT_AVAILABLE_FOR_FINAL_HAZARD")
        persistence = session.persistence
        if (
            persistence is None
            or persistence.logger is not services.logger
            or persistence.study_run is not services.context
            or id(session.world) != identities.world
            or id(session.hero) != identities.hero
            or id(session.viewer) != identities.viewer_sensor_owner
            or id(session.bundle) != identities.control
        ):
            raise P8Error("FINAL_HAZARD_SHARED_RESOURCE_IDENTITY_MISMATCH")
        return replace(
            identities,
            control_mode=session.bundle.automation_runtime.state.control_mode.value,
            shutdown_brake_status=(
                session.shutdown_brake_outcome.command_call_outcome.value
            ),
        )

    def tokens(self) -> tuple[str, ...]:
        return ()
