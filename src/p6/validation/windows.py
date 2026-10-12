from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from src.p6.aoi import Rect
from src.p6.validation.mapping import ValidationError, WindowGeometry


class MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                ("szDevice", wintypes.WCHAR * 32)]


class WindowProbe:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise ValidationError("Windows monitor/client probing is required")
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        self.api.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        self.api.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        self.api.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.api.GetClientRect.restype = wintypes.BOOL
        self.api.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self.api.ClientToScreen.restype = wintypes.BOOL
        self.api.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        self.api.MonitorFromWindow.restype = wintypes.HANDLE
        self.api.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        self.api.GetMonitorInfoW.restype = wintypes.BOOL
        self.api.GetSystemMetrics.argtypes = [ctypes.c_int]
        self.api.GetSystemMetrics.restype = ctypes.c_int
        self.api.GetDpiForWindow.argtypes = [wintypes.HWND]
        self.api.GetDpiForWindow.restype = wintypes.UINT
        self.api.GetForegroundWindow.restype = wintypes.HWND
        self._previous = self.api.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        if not self._previous:
            raise ValidationError("Cannot establish per-monitor-v2 DPI coordinates")

    def close(self) -> None:
        self.api.SetThreadDpiAwarenessContext(self._previous)

    def focused(self, hwnd: int) -> bool:
        return self.api.GetForegroundWindow() == hwnd

    def snapshot(self, hwnd: int, surface_size: tuple[int, int]) -> WindowGeometry:
        client = wintypes.RECT()
        monitor = MonitorInfo()
        monitor.cbSize = ctypes.sizeof(MonitorInfo)
        if not self.api.GetClientRect(hwnd, ctypes.byref(client)):
            raise ValidationError("GetClientRect failed")
        first, last = wintypes.POINT(client.left, client.top), wintypes.POINT(client.right, client.bottom)
        if not self.api.ClientToScreen(hwnd, ctypes.byref(first)) or not self.api.ClientToScreen(hwnd, ctypes.byref(last)):
            raise ValidationError("ClientToScreen failed")
        handle = self.api.MonitorFromWindow(hwnd, 0)
        if not handle or not self.api.GetMonitorInfoW(handle, ctypes.byref(monitor)):
            raise ValidationError("MonitorFromWindow/GetMonitorInfoW failed")
        rect = monitor.rcMonitor
        return WindowGeometry(
            Rect(*(self.api.GetSystemMetrics(index) for index in (76, 77, 78, 79))),
            Rect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top),
            Rect(first.x, first.y, last.x - first.x, last.y - first.y),
            surface_size, hwnd, monitor.szDevice, self.api.GetDpiForWindow(hwnd),
        )
