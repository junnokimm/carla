from dataclasses import dataclass

import pytest

from src.p7.config import ProtectedZone, TrafficConfig
from src.p7.ownership import ActorRole, OwnedActors
from src.p7.traffic import SpawnCandidate, TrafficScenario, select_candidates
from src.p7.types import P7Error


def test_seeded_traffic_is_order_independent_and_respects_exclusions():
    candidates = tuple(SpawnCandidate(str(i), float(i * 10), 0) for i in range(20))
    config = TrafficConfig(seed=19, vehicle_count=3, exclusion_m=15)

    first = select_candidates(candidates, config, ego=(0, 0), protected=(ProtectedZone(60, 0, 15),))
    second = select_candidates(tuple(reversed(candidates)), config, ego=(0, 0), protected=(ProtectedZone(60, 0, 15),))

    assert first == second
    assert len(first) == 3
    assert all(item.x >= 15 and abs(item.x - 60) > 15 for item in first)
    assert all(abs(left.x - right.x) >= 15 for i, left in enumerate(first) for right in first[i + 1:])


def test_disabled_traffic_never_spawns():
    calls = []
    traffic = TrafficScenario(TrafficConfig(enabled=False), spawn=lambda candidate: calls.append(candidate))

    traffic.start((), ego=(0, 0), protected=())

    assert calls == []


def test_insufficient_traffic_candidates_fail_explicitly():
    with pytest.raises(P7Error, match="INSUFFICIENT"):
        select_candidates((), TrafficConfig(), ego=(0, 0), protected=())


@dataclass
class Actor:
    id: int
    destroyed: int = 0

    def destroy(self):
        self.destroyed += 1
        return True


def test_cleanup_is_owned_and_role_scoped_never_ego_or_hazard_by_traffic_cleanup():
    owned = OwnedActors(ego_id=1)
    ego, lead, cargo, traffic = Actor(1), Actor(2), Actor(3), Actor(4)
    with pytest.raises(P7Error, match="EGO"):
        owned.add(ego, ActorRole.TRAFFIC)
    owned.add(lead, ActorRole.LEAD)
    owned.add(cargo, ActorRole.CARGO)
    owned.add(traffic, ActorRole.TRAFFIC)

    assert owned.close(ActorRole.TRAFFIC) == ()

    assert traffic.destroyed == 1
    assert ego.destroyed == lead.destroyed == cargo.destroyed == 0
    assert owned.close() == ()
    assert lead.destroyed == cargo.destroyed == 1
    assert ego.destroyed == 0
