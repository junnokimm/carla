from __future__ import annotations

import csv
import json
from pathlib import Path

from src.scenario.research_exit_ledger import reconstruct_exit_ledger


def test_reconstruct_exit_ledger_orders_equal_host_stamps_by_csv_fifo(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.csv"
    fields = ("host_monotonic_ns", "event_type", "event_value")
    with path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        writer.writerow(
            {
                "host_monotonic_ns": "30",
                "event_type": "lc_presented",
                "event_value": json.dumps(
                    {
                        "lc_event_id": "m1-exit-1",
                        "t0_host_monotonic_ns": 30,
                        "decision": None,
                    }
                ),
            }
        )
        writer.writerow(
            {
                "host_monotonic_ns": "30",
                "event_type": "lc_decision",
                "event_value": json.dumps(
                    {
                        "lc_event_id": "m1-exit-1",
                        "t0_host_monotonic_ns": 30,
                        "decision": "CONFIRM",
                    }
                ),
            }
        )
        writer.writerow(
            {
                "host_monotonic_ns": "70",
                "event_type": "lc_boundary_crossing",
                "event_value": json.dumps(
                    {
                        "lc_event_id": "m1-exit-1",
                        "tL_host_monotonic_ns": 70,
                    }
                ),
            }
        )

    result = reconstruct_exit_ledger(path)

    assert len(result) == 1
    assert [event.event_type for event in result[0].events] == [
        "lc_presented",
        "lc_decision",
        "lc_boundary_crossing",
    ]
    assert result[0].decision == "CONFIRM"
    assert result[0].t0_host_monotonic_ns == 30
    assert result[0].tL_host_monotonic_ns == 70
