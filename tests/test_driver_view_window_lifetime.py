from __future__ import annotations

import os
import subprocess
import sys
import weakref

import pygame
import pytest

from src.scenario import driver_view_presentation


def test_window_wrapper_survives_both_f11_transitions(monkeypatch) -> None:
    # Given: SDL keeps a non-owning pointer to each borrowed wrapper.
    wrappers = []

    class BorrowedWindow:
        display_index = 0
        position = (40, 30)

        @classmethod
        def from_display_module(cls):
            window = cls()
            wrappers.append(weakref.ref(window))
            return window

        def set_windowed(self) -> None:
            return None

    monkeypatch.setattr(driver_view_presentation, "Window", BorrowedWindow)
    monkeypatch.setattr(
        driver_view_presentation,
        "current_monitor_bounds",
        lambda: (0, 0, 1280, 720),
        raising=False,
    )
    monkeypatch.setattr(pygame.display, "get_window_size", lambda: (640, 360))
    monkeypatch.setattr(pygame.display, "get_desktop_sizes", lambda: [(1280, 720)])
    monkeypatch.setattr(
        pygame.display, "set_mode", lambda size, *args, **kwargs: pygame.Surface(size)
    )
    presentation = driver_view_presentation.DriverViewPresentation(
        (640, 360), pygame.Surface((640, 360))
    )
    press = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F11)
    release = pygame.event.Event(pygame.KEYUP, key=pygame.K_F11)

    # When: the handler returns before SDL processes resize/expose events.
    for _ in range(4):
        presentation.handle_events([press, release])
        # Then: SDL's registered Python object must still be alive.
        assert wrappers[-1]() is not None
    assert len(wrappers) == 1


@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("CARLA_TEST_WINDOWS_DISPLAY") != "1",
    reason="opt-in native Windows display test (opens real borderless windows)",
)
def test_windows_borderless_f11_survives_cross_monitor_focus_and_real_close() -> None:
    # Given: an isolated real Windows SDL process, without a CARLA client.
    environment = dict(os.environ, SDL_VIDEODRIVER="windows", SDL_AUDIODRIVER="dummy")
    # When: pump native events and render after every transition, then close via WM_CLOSE.
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-u",
            "-X",
            "faulthandler",
            "-c",
            """
import ctypes
import time
from ctypes import wintypes
import pygame
from pygame._sdl2.video import Window
from src.scenario.driver_view_presentation import DriverViewPresentation

user32 = ctypes.WinDLL('user32', use_last_error=True)
callback_type = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HMONITOR,
    wintypes.HDC,
    ctypes.POINTER(wintypes.RECT),
    wintypes.LPARAM,
)
enum_monitors = user32.EnumDisplayMonitors
enum_monitors.argtypes = (
    wintypes.HDC,
    ctypes.POINTER(wintypes.RECT),
    callback_type,
    wintypes.LPARAM,
)
enum_monitors.restype = wintypes.BOOL
get_window_rect = user32.GetWindowRect
get_window_rect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
get_window_rect.restype = wintypes.BOOL
get_foreground = user32.GetForegroundWindow
get_foreground.restype = wintypes.HWND
get_cursor_position = user32.GetCursorPos
get_cursor_position.argtypes = (ctypes.POINTER(wintypes.POINT),)
get_cursor_position.restype = wintypes.BOOL
set_cursor_position = user32.SetCursorPos
set_cursor_position.argtypes = (ctypes.c_int, ctypes.c_int)
set_cursor_position.restype = wintypes.BOOL
mouse_event = user32.mouse_event
mouse_event.argtypes = (
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_size_t,
)

class MouseInput(ctypes.Structure):
    _fields_ = (
        ('dx', wintypes.LONG),
        ('dy', wintypes.LONG),
        ('mouseData', wintypes.DWORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t),
    )

class KeyboardInput(ctypes.Structure):
    _fields_ = (
        ('wVk', wintypes.WORD),
        ('wScan', wintypes.WORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t),
    )

class InputValue(ctypes.Union):
    _fields_ = (('mouse', MouseInput), ('keyboard', KeyboardInput))

class Input(ctypes.Structure):
    _anonymous_ = ('value',)
    _fields_ = (('type', wintypes.DWORD), ('value', InputValue))

send_input = user32.SendInput
send_input.argtypes = (wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int)
send_input.restype = wintypes.UINT
map_virtual_key = user32.MapVirtualKeyW
map_virtual_key.argtypes = (wintypes.UINT, wintypes.UINT)
map_virtual_key.restype = wintypes.UINT
is_visible = user32.IsWindowVisible
is_visible.argtypes = (wintypes.HWND,)
is_visible.restype = wintypes.BOOL
is_iconic = user32.IsIconic
is_iconic.argtypes = (wintypes.HWND,)
is_iconic.restype = wintypes.BOOL
post = user32.PostMessageW
post.argtypes = (wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t)
post.restype = wintypes.BOOL

monitors = []
@callback_type
def collect_monitor(_monitor, _dc, rect, _data):
    value = rect.contents
    monitors.append((value.left, value.top, value.right, value.bottom))
    return True

assert enum_monitors(None, None, collect_monitor, 0)
assert len(monitors) >= 2, 'native test requires two monitors'
original_cursor = wintypes.POINT()
assert get_cursor_position(ctypes.byref(original_cursor))
pygame.display.init()
try:
    size = (800, 450)
    view = DriverViewPresentation(size, pygame.display.set_mode(size))
    main = Window.from_display_module()
    focus_target = Window('focus target', size=(320, 240))
    focus_target.show()
    hwnd = pygame.display.get_wm_info()['window']
    press = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F11)
    release = pygame.event.Event(pygame.KEYUP, key=pygame.K_F11)
    pygame.event.get()
    for repeat in range(2):
        for monitor_index, bounds in enumerate(monitors[:2]):
            original_position = (bounds[0] + 80, bounds[1] + 80)
            other = monitors[1 - monitor_index]
            main.size = size
            main.position = original_position
            main.focus()
            pygame.event.get()
            view.handle_events([press, release])
            view.canvas.fill((30 + repeat, 60 + monitor_index, 90))
            view.present()
            pygame.display.flip()
            rect = wintypes.RECT()
            assert get_window_rect(hwnd, ctypes.byref(rect))
            observed = (rect.left, rect.top, rect.right, rect.bottom)
            assert observed == bounds
            assert is_visible(hwnd) and not is_iconic(hwnd)

            focus_target.position = (other[0] + 120, other[1] + 120)
            pygame.event.get()
            assert set_cursor_position(other[0] + 280, other[1] + 240)
            mouse_event(0x0002, 0, 0, 0, 0)
            mouse_event(0x0004, 0, 0, 0, 0)
            deadline = time.monotonic() + 2
            while get_foreground() == hwnd and time.monotonic() < deadline:
                pygame.event.wait(20)
            pygame.event.pump()
            assert get_foreground() != hwnd
            assert is_visible(hwnd) and not is_iconic(hwnd)
            assert get_window_rect(hwnd, ctypes.byref(rect))
            assert (rect.left, rect.top, rect.right, rect.bottom) == bounds

            pygame.event.get()
            assert set_cursor_position(bounds[0] + 400, bounds[1] + 225)
            mouse_event(0x0002, 0, 0, 0, 0)
            mouse_event(0x0004, 0, 0, 0, 0)
            deadline = time.monotonic() + 2
            while get_foreground() != hwnd and time.monotonic() < deadline:
                pygame.event.wait(20)
            assert get_foreground() == hwnd
            pygame.event.get()
            scan_code = map_virtual_key(0x41, 0)
            assert scan_code
            keyboard_inputs = (Input * 2)()
            keyboard_inputs[0].type = 1
            keyboard_inputs[0].keyboard.wScan = scan_code
            keyboard_inputs[0].keyboard.dwFlags = 0x0008
            keyboard_inputs[1].type = 1
            keyboard_inputs[1].keyboard.wScan = scan_code
            keyboard_inputs[1].keyboard.dwFlags = 0x0008 | 0x0002
            assert send_input(2, keyboard_inputs, ctypes.sizeof(Input)) == 2
            keyboard_returned = False
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                event = pygame.event.wait(20)
                if event.type == pygame.KEYDOWN and event.key == pygame.K_a:
                    keyboard_returned = True
                    break
            assert keyboard_returned, 'Win32 keyboard input must return after focus'
            surface = pygame.display.get_surface()
            assert surface.get_at((400, 225))[:3] == (
                30 + repeat,
                60 + monitor_index,
                90,
            )

            view.handle_events([press, release])
            assert pygame.display.get_window_size() == size
            assert main.position == original_position
            events = pygame.event.get()
            assert not any(e.type in (pygame.QUIT, pygame.WINDOWCLOSE) for e in events)
            for event in events:
                if 'window' in event.dict and event.window is not None:
                    assert isinstance(event.window, Window)
    focus_target.destroy()
    assert post(hwnd, 0x0010, 0, 0)
    closed = False
    for _ in range(20):
        event = pygame.event.wait(100)
        view.handle_events([event])
        if event.type == pygame.QUIT:
            closed = True
            break
    assert closed, 'WM_CLOSE must still reach the application as QUIT'
finally:
    set_cursor_position(original_cursor.x, original_cursor.y)
    pygame.quit()
""",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    # Then: no native crash, valid rendered surfaces, and genuine close delivery.
    assert result.returncode == 0, result.stdout + result.stderr
