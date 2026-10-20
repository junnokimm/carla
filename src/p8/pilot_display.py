"""Read-only display mapping checks; window placement remains operator-controlled."""


def mapping_signature(geometry):
    """Exclude HWND: SDL legitimately creates a new window between stages."""
    from dataclasses import asdict
    return {"monitor_name": geometry.monitor_name, "monitor": asdict(geometry.monitor),
            "client": asdict(geometry.client), "surface_size": list(geometry.surface_size),
            "dpi": geometry.dpi}


def mapping_issue(geometry, expected=None):
    if not geometry.display_mapping.geometry_valid:
        return "INVALID_DISPLAY_GEOMETRY"
    # GetClientRect/monitor bounds are physical Windows pixels. SDL's drawing
    # surface can be FHD on a 4K fullscreen display; it is not a window bound.
    if geometry.client != geometry.monitor:
        return "FULLSCREEN_MAPPING_REQUIRED"
    if expected:
        actual = mapping_signature(geometry)
        if any(actual[key] != expected.get(key) for key in ("monitor_name", "monitor", "client", "dpi")):
            return "DISPLAY_MAPPING_CHANGED_SINCE_PRECHECK"
        previous = expected.get("surface_size", ())
        if (len(previous) != 2 or any(not isinstance(v, (int, float)) or v <= 0 for v in previous)
                or actual["surface_size"][0] * previous[1] != actual["surface_size"][1] * previous[0]):
            return "DISPLAY_MAPPING_CHANGED_SINCE_PRECHECK"
    return None
