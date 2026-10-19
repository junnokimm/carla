"""Nonfatal report I/O. These helpers never run in the driving frame loop."""

import json
from pathlib import Path


def records(path, errors):
    try:
        with Path(path).open(encoding="utf-8-sig") as source:
            for number, line in enumerate(source, 1):
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise TypeError("record is not an object")
                    yield row
                except (ValueError, TypeError) as error:
                    if len(errors) < 100:
                        errors.append(f"{Path(path).name}:{number}:{error}")
    except OSError as error:
        errors.append(f"{Path(path).name}:{error}")


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def notice(error):
    try:
        print(
            json.dumps(
                {
                    "status": "LOG_COLLECTION_WARNING",
                    "session_action": "CONTINUE",
                    "detail": str(error),
                },
                ensure_ascii=True,
            ),
            flush=True,
        )
    except Exception:  # noqa: BLE001, S110 -- broken console cannot stop a drive
        pass
