from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile


@dataclass(frozen=True, slots=True)
class OperatorResultExport:
    directory: Path
    archive: Path | None
    error: str | None = None


def export_operator_results(directory: Path, *, closed: bool) -> OperatorResultExport:
    directory = directory.resolve()
    archive = directory.parent / "_exports" / f"{directory.name}.zip"
    published = False
    try:
        if not closed:
            raise ValueError("RESULT_EXPORT_REQUIRES_CLOSED_LOGS")
        manifest = json.loads(
            (directory / "session_manifest.json").read_text(encoding="utf-8")
        )
        if not isinstance(manifest, dict):
            raise TypeError("RESULT_EXPORT_REQUIRES_MANIFEST_OBJECT")
        if manifest.get("state") not in ("SESSION_COMPLETE", "ABORTED"):
            raise ValueError("RESULT_EXPORT_REQUIRES_TERMINAL_MANIFEST")
        if manifest.get("run_id") != directory.name:
            raise ValueError("RESULT_EXPORT_RUN_ID_MISMATCH")
        files = []
        for path in sorted(directory.rglob("*")):
            relative = path.relative_to(directory)
            if any(part.startswith(".") for part in relative.parts):
                continue
            if path.is_symlink():
                raise ValueError("RESULT_EXPORT_CANNOT_INCLUDE_SYMLINKS")
            if path.is_file():
                files.append(path)
        if archive.exists():
            if not _archive_matches(archive, directory, files):
                raise ValueError("RESULT_ZIP_EXISTS_WITH_DIFFERENT_CONTENT")
            return OperatorResultExport(directory, archive)
        archive.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(
            prefix=f".{directory.name}-", dir=archive.parent
        ) as temp:
            temporary = Path(temp) / "results.zip"
            with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as output:
                for path in files:
                    output.write(path, path.relative_to(directory.parent).as_posix())
            # Publish only a closed ZIP and never replace an earlier export.
            os.link(temporary, archive)
            published = True
        return OperatorResultExport(directory, archive)
    except (
        KeyboardInterrupt,
        OSError,
        TypeError,
        ValueError,
        RuntimeError,
        BadZipFile,
    ) as error:
        return OperatorResultExport(
            directory,
            archive if published else None,
            f"{type(error).__name__}:{error}",
        )


def _archive_matches(archive: Path, directory: Path, files: list[Path]) -> bool:
    names = [path.relative_to(directory.parent).as_posix() for path in files]
    with ZipFile(archive) as source:
        if sorted(source.namelist()) != sorted(names):
            return False
        for path, name in zip(files, names, strict=True):
            if source.getinfo(name).file_size != path.stat().st_size:
                return False
            with path.open("rb") as original, source.open(name) as archived:
                if (
                    hashlib.file_digest(original, "sha256").digest()
                    != hashlib.file_digest(archived, "sha256").digest()
                ):
                    return False
    return True
