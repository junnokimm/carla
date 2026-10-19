def append_error_note(error: BaseException, note: str) -> None:
    notes = getattr(error, "__notes__", None)
    if notes is None:
        object.__setattr__(error, "__notes__", [])
        notes = error.__notes__
    notes.append(note)
