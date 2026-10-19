"""Recover an already stopped pilot into a separate copy; preserve the source."""

import argparse
import json
import shutil
from pathlib import Path

from src.p8.crash_recovery import atomic_json, recover_stopped_run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--worker-exited", action="store_true", required=True,
                        help="Only use after confirming this run's worker has exited")
    parser.add_argument("--exit-code", type=int, required=True,
                        help="Observed process exit code, signed or unsigned")
    parser.add_argument("--retry-export", action="store_true",
                        help="Retry only the ZIP of an existing recovered copy")
    args = parser.parse_args(argv)
    source = args.source.resolve()
    if not (source / "session_manifest.json").is_file():
        parser.error("Source must contain session_manifest.json")
    if args.retry_export:
        if not (source / "crash_recovery/report.json").is_file():
            parser.error("Retry requires an existing recovered directory")
        result = recover_stopped_run(source, returncode=args.exit_code, worker_exited=True)
        print(json.dumps({"archive": str(result.archive) if result.archive else None,
                          "error": result.error}), flush=True)
        return 0 if result.error is None else 2
    for path in source.rglob("*"):
        if path.is_symlink() or path.is_junction():
            parser.error("Linked files/directories are not supported")
    destination = source.parent / "_recovered" / source.name
    if destination.exists():
        parser.error(f"Recovery copy already exists; it was not replaced: {destination}")
    shutil.copytree(source, destination)
    atomic_json(destination / "recovery_source.json", {
        "original_directory": str(source), "original_modified": False,
        "worker_exit_confirmed_by_operator": True})
    result = recover_stopped_run(destination, returncode=args.exit_code, worker_exited=True)
    print(json.dumps({"status": "RECOVERED_PARTIAL" if result.error is None else "EXPORT_FAILED",
                      "directory": str(destination),
                      "archive": str(result.archive) if result.archive else None,
                      "error": result.error}), flush=True)
    return 0 if result.error is None else 2


if __name__ == "__main__":
    raise SystemExit(main())
