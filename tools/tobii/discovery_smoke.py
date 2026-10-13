# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# Run: .venv-tobii/Scripts/python.exe -m tools.tobii.discovery_smoke
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only, bounded Tobii Pro Nano discovery; no calibration or gaze")
    parser.add_argument("--python", default=sys.executable, help="Isolated Windows x64 CPython 3.10 executable")
    args = parser.parse_args(argv)
    try:
        result = subprocess.run(
            [args.python, "-B", "-m", "tools.tobii.worker", "discover"],
            cwd=Path(__file__).resolve().parents[2], capture_output=True,
            text=True, encoding="utf-8", timeout=10, check=False,
        )
        report = json.loads(result.stdout)
        status = report["status"]
        print(f"TOBII_DISCOVERY={status}")
        print(json.dumps(report, ensure_ascii=True))
        return 0 if result.returncode == 0 and status == "OK" else 2
    except subprocess.TimeoutExpired:
        print("TOBII_DISCOVERY=TIMEOUT")
    except OSError as error:
        print(f"TOBII_DISCOVERY=RUNTIME_UNAVAILABLE\n{error}")
    except (ValueError, KeyError, TypeError) as error:
        print(f"TOBII_DISCOVERY=WORKER_ERROR\n{error}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
