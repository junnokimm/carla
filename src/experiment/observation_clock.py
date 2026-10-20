"""Optional analysis clock. Never use this clock to drive the experiment."""

import time


def observation_ns():
    """Windows QPC shared by the Python processes; missing evidence stays null."""
    try:
        value = time.perf_counter_ns()
        return value if isinstance(value, int) and value >= 0 else None
    except Exception:  # noqa: BLE001 -- instrumentation cannot interrupt controls
        return None


def record_input_clock(scene, mono_start, mono_end, qpc_start, qpc_end):
    """Optional metadata hook; the existing three-argument control API is intact."""
    try:
        hook = getattr(scene, "record_input_clock", None)
        if hook is not None:
            hook(mono_start, mono_end, qpc_start, qpc_end)
    except Exception:  # noqa: BLE001 -- a missing timestamp is not a control error
        return
