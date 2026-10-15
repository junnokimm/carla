from __future__ import annotations

import ctypes
from ctypes import wintypes


class _MonitorInfo(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    )


def current_monitor_bounds() -> tuple[int, int, int, int]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    monitor_from_window = user32.MonitorFromWindow
    monitor_from_window.argtypes = (wintypes.HWND, wintypes.DWORD)
    monitor_from_window.restype = wintypes.HANDLE
    get_monitor_info = user32.GetMonitorInfoW
    get_monitor_info.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_MonitorInfo),
    )
    get_monitor_info.restype = wintypes.BOOL

    import pygame

    window_handle: int = pygame.display.get_wm_info()["window"]
    monitor_handle = monitor_from_window(wintypes.HWND(window_handle), 2)
    monitor_info = _MonitorInfo()
    monitor_info.cbSize = ctypes.sizeof(_MonitorInfo)
    if not monitor_handle or not get_monitor_info(
        monitor_handle, ctypes.byref(monitor_info)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    rect = monitor_info.rcMonitor
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
