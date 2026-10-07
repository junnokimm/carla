from __future__ import annotations

from src.scenario.research_noa_types import ResearchNoAVehicle


class ResearchNoACleanupError(RuntimeError):
    pass


def destroy_owned_hero(hero: ResearchNoAVehicle) -> None:
    if hero.destroy() is False:
        raise ResearchNoACleanupError("owned research vehicle destroy failed")
