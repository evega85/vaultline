"""Our own title bar on a real Windows window.

The window keeps its native caption/thick-frame styles, so Windows still handles moving, Aero Snap,
the Windows 11 snap-layout flyout, minimise/maximise animations, the system menu (Alt+Space) and
resizing. We only tell Windows (WM_NCCALCSIZE) that the whole window is client area, and answer
WM_NCHITTEST so it knows which pixels of our bar are "caption", "maximise button" or "resize edge".

Everything here is a no-op off Windows (and on Qt's offscreen platform used by the tests).
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

WM_SIZING = 0x0214
WM_MOVING = 0x0216
WM_ENTERSIZEMOVE = 0x0231
WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_NCMOUSEMOVE = 0x00A0
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONUP = 0x00A2
WM_NCLBUTTONDBLCLK = 0x00A3
WM_MOUSEMOVE = 0x0200
WM_NCMOUSELEAVE = 0x02A2
WM_DPICHANGED = 0x02E0
WM_GETDPISCALEDSIZE = 0x02E4

HTCLIENT, HTCAPTION, HTMAXBUTTON = 1, 2, 9
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17

GWL_STYLE = -16
WS_CAPTION, WS_THICKFRAME, WS_SYSMENU = 0x00C00000, 0x00040000, 0x00080000
WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00020000, 0x00010000
SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020  # NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED
SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE = 3, 6, 9
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2

# Resize zones inside the window edge, in logical pixels (the content has 16px of padding anyway).
EDGE = 6
TOP_EDGE = 4
CORNER = 14


def available() -> bool:
    return sys.platform == "win32" and QGuiApplication.platformName() == "windows"


class _NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


class _TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("hwndTrack", wintypes.HWND), ("dwHoverTime", wintypes.DWORD)]


def _user32():
    u = ctypes.windll.user32
    u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT]
    u.MonitorFromWindow.restype = wintypes.HMONITOR
    u.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    u.IsZoomed.argtypes = [wintypes.HWND]
    return u


def _signed16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


class NativeFrame:
    """Attach to a top-level widget; call `handle()` from its nativeEvent.

    `hit(pos)` is the title bar's answer for a point in the window's own (logical) coordinates:
    "caption", "max" or None (ordinary client area). `max_button` mirrors Windows' hover/press
    state for the maximise button, because Windows sends those as non-client messages.
    """

    def __init__(self, window: QWidget, hit, max_button=None, sizing_height=None, vertical_resize=None):
        self.window = window
        self.hit = hit
        self.max_button = max_button
        # sizing_height(width, height) -> height (logical px): lets the window pick its own height
        # while the user drags its width (the compact view grows with its results).
        self.sizing_height = sizing_height
        self.vertical_resize = vertical_resize  # () -> bool; False: only the side edges resize
        self.size_move_started = None  # () -> None, when the user starts dragging or resizing
        self.maximizable = True        # False: no maximise (also not by dragging to the screen top)
        self.snap_to_top = False       # True: dragged near the top of the screen, it sticks to it
        self.bottom_resize = False     # with vertical_resize False: still allow the bottom edge
        self.enabled = available()
        self.wanted_width = None       # while a side edge is dragged: the width the mouse asks for
        self._max_pressed = False
        self._hwnd = 0

    @property
    def hwnd(self) -> int:
        # Never call winId() from inside a native message: while the window is being created that
        # sends WM_NCCALCSIZE again and recurses. handle() records the handle from the message.
        return self._hwnd or int(self.window.winId())

    # ---- setup ---------------------------------------------------------------
    def apply_styles(self) -> None:
        """Give the frameless Qt window back the styles that make it a real window to Windows."""
        if not self.enabled:
            return
        u = _user32()
        hwnd = self.hwnd
        style = u.GetWindowLongPtrW(hwnd, GWL_STYLE)
        want = style | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
        if not self.maximizable:
            want &= ~WS_MAXIMIZEBOX
        if want != style:
            u.SetWindowLongPtrW(hwnd, GWL_STYLE, want)
            u.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_FLAGS)
        try:  # Windows 11: rounded corners (8px, the search box's radius); squared when maximised
            pref = ctypes.c_int(2)  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), 33, ctypes.byref(pref), 4)
        except Exception:
            pass

    def set_maximizable(self, on: bool) -> None:
        self.maximizable = on
        if self.enabled and self.window.isVisible():
            self.apply_styles()

    # ---- window commands (native, so they match double-click / snap exactly) ----------
    def is_maximized(self) -> bool:
        if self.enabled:
            return bool(_user32().IsZoomed(self.hwnd))
        return self.window.isMaximized()

    def toggle_maximize(self) -> None:
        if self.enabled:
            _user32().ShowWindow(self.hwnd, SW_RESTORE if self.is_maximized() else SW_MAXIMIZE)
        elif self.window.isMaximized():
            self.window.showNormal()
        else:
            self.window.showMaximized()

    def minimize(self) -> None:
        if self.enabled:
            _user32().ShowWindow(self.hwnd, SW_MINIMIZE)
        else:
            self.window.showMinimized()

    def set_topmost(self, on: bool) -> bool:
        """Pin above other windows without Qt recreating the window's styles. False if not native."""
        if not self.enabled:
            return False
        _user32().SetWindowPos(self.hwnd, HWND_TOPMOST if on else HWND_NOTOPMOST, 0, 0, 0, 0,
                               0x0001 | 0x0002 | 0x0010)  # NOSIZE|NOMOVE|NOACTIVATE
        return True

    # ---- messages ------------------------------------------------------------
    def handle(self, event_type, message) -> tuple[bool, int] | None:
        """Returns (handled, result) for nativeEvent, or None to let Qt carry on."""
        if not self.enabled or event_type != b"windows_generic_MSG":
            return None
        msg = wintypes.MSG.from_address(int(message))
        if not msg.hWnd:
            return None
        self._hwnd = int(msg.hWnd)
        m = msg.message
        if m == WM_NCCALCSIZE:
            if msg.wParam:
                self._calc_size(msg.lParam)
            return True, 0
        if m == WM_ENTERSIZEMOVE:
            self._start_drag()
            if self.size_move_started is not None:
                self.size_move_started()
            return None
        if m == WM_MOVING and self.snap_to_top:
            self._snap_top(msg.lParam)
            return True, 1
        if m == WM_SIZING and self.sizing_height is not None:
            self._sizing(msg.wParam, msg.lParam)
            return True, 1
        if m == WM_DPICHANGED and not self.is_maximized():
            # Qt resizes for the new scaling as if the window had a native caption and border.
            # Keep the size it had (in Qt's units) once Qt is done.
            keep = self.window.size()
            QTimer.singleShot(0, lambda: self._keep_size(keep))
            return None
        if m == WM_GETDPISCALEDSIZE:
            return True, int(self._dpi_scaled_size(msg.wParam & 0xFFFF, msg.lParam))
        if m == WM_NCHITTEST:
            return True, self._hit_test(_signed16(msg.lParam), _signed16(msg.lParam >> 16))
        if m == WM_NCMOUSEMOVE:
            over = msg.wParam == HTMAXBUTTON
            self._set_hover(over)
            if over:
                self._track_leave()
                return True, 0
            return None
        if m in (WM_NCLBUTTONDOWN, WM_NCLBUTTONDBLCLK) and msg.wParam == HTMAXBUTTON:
            self._max_pressed = True
            if self.max_button is not None:
                self.max_button.set_pressed(True)
            return True, 0
        if m == WM_NCLBUTTONUP and msg.wParam == HTMAXBUTTON:
            pressed, self._max_pressed = self._max_pressed, False
            if self.max_button is not None:
                self.max_button.set_pressed(False)
            if pressed:
                self.toggle_maximize()
            return True, 0
        if m == WM_NCMOUSELEAVE or m == WM_MOUSEMOVE:
            self._set_hover(False)
        return None

    def _set_hover(self, on: bool) -> None:
        if not on:
            self._max_pressed = False
        if self.max_button is not None:
            self.max_button.set_hover(on)
            if not on:
                self.max_button.set_pressed(False)

    def _track_leave(self) -> None:
        tme = _TRACKMOUSEEVENT(ctypes.sizeof(_TRACKMOUSEEVENT), 0x00000002 | 0x00000010, self.hwnd, 0)
        ctypes.windll.user32.TrackMouseEvent(ctypes.byref(tme))  # TME_LEAVE | TME_NONCLIENT

    def _calc_size(self, lparam: int) -> None:
        """Client area = whole window. Maximised windows hang over the screen edge by the frame
        width, so clip them to the monitor's work area (taskbar stays visible, on every monitor)."""
        if not self.is_maximized():
            return
        params = _NCCALCSIZE_PARAMS.from_address(lparam)
        r = params.rgrc[0]
        u = _user32()
        info = _MONITORINFO(ctypes.sizeof(_MONITORINFO))
        if not u.GetMonitorInfoW(u.MonitorFromWindow(self.hwnd, 2), ctypes.byref(info)):
            return
        w = info.rcWork
        r.left, r.top, r.right, r.bottom = (max(r.left, w.left), max(r.top, w.top),
                                            min(r.right, w.right), min(r.bottom, w.bottom))
        if (w.left, w.top, w.right, w.bottom) == (info.rcMonitor.left, info.rcMonitor.top,
                                                  info.rcMonitor.right, info.rcMonitor.bottom):
            r.bottom -= 2  # auto-hide taskbar: leave a sliver so it can still slide in

    def _snap_top(self, lparam: int) -> None:
        """While being dragged: within ~24px of the work area's top, stick to it (like a launcher)."""
        r = wintypes.RECT.from_address(lparam)
        u = _user32()
        info = _MONITORINFO(ctypes.sizeof(_MONITORINFO))
        pt = wintypes.POINT()
        u.GetCursorPos(ctypes.byref(pt))
        u.MonitorFromPoint.restype = wintypes.HMONITOR
        mon = u.MonitorFromPoint(pt, 2)
        if not mon or not u.GetMonitorInfoW(mon, ctypes.byref(info)):
            return
        dpr = (u.GetDpiForWindow(wintypes.HWND(self.hwnd)) or 96) / 96
        top = info.rcWork.top
        if r.top < top + round(24 * dpr):
            h = r.bottom - r.top
            r.top, r.bottom = top, top + h

    def _start_drag(self) -> None:
        rect, pt = wintypes.RECT(), wintypes.POINT()
        u = _user32()
        u.GetWindowRect(self.hwnd, ctypes.byref(rect))
        u.GetCursorPos(ctypes.byref(pt))
        self._drag_from = (pt.x, rect.right - rect.left)
        self.wanted_width = None

    def end_drag(self) -> None:
        """Stop a resize the user is still dragging (Windows ends its sizing loop on capture loss)."""
        if self.enabled:
            ctypes.windll.user32.ReleaseCapture()

    def _wanted_width(self, edge: int, dpr: float) -> float | None:
        """Width the mouse asks for while a side edge is dragged, in logical px: unlike the
        rectangle Windows offers, it isn't held back by the minimum width."""
        start = getattr(self, "_drag_from", None)
        if start is None or edge not in (1, 2):  # WMSZ_LEFT, WMSZ_RIGHT
            return None
        pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        dx = pt.x - start[0]
        return (start[1] + (dx if edge == 2 else -dx)) / dpr

    def _sizing(self, edge: int, lparam: int) -> None:
        r = wintypes.RECT.from_address(lparam)
        dpr = (ctypes.windll.user32.GetDpiForWindow(wintypes.HWND(self.hwnd)) or 96) / 96
        self.wanted_width = self._wanted_width(edge, dpr)
        want = self.sizing_height((r.right - r.left) / dpr, (r.bottom - r.top) / dpr, edge)
        if want is None:
            return
        if isinstance(want, tuple):  # (width, height): switching views from the bottom edge
            w = round(want[0] * dpr)
            if edge in (1, 4, 7):  # dragging a left edge: the right side stays put
                r.left = r.right - w
            else:
                r.right = r.left + w
            want = want[1]
        h = round(want * dpr)
        if edge in (3, 4, 5):  # WMSZ_TOP, TOPLEFT, TOPRIGHT: the bottom stays put
            r.top = r.bottom - h
        else:
            r.bottom = r.top + h

    def _keep_size(self, size) -> None:
        """Compare with the real (physical) window size: Qt's own record can still be stale here."""
        if self.is_maximized() or self.window.isMinimized() or self.window.size() != size:
            return  # (a different size means it was resized on purpose meanwhile)
        u = _user32()
        rect = wintypes.RECT()
        u.GetWindowRect(self.hwnd, ctypes.byref(rect))
        dpr = (u.GetDpiForWindow(wintypes.HWND(self.hwnd)) or 96) / 96
        w, h = round(size.width() * dpr), round(size.height() * dpr)
        if (rect.right - rect.left, rect.bottom - rect.top) != (w, h):
            u.SetWindowPos(self.hwnd, None, 0, 0, w, h, 0x0002 | 0x0004 | 0x0010)  # NOMOVE|NOZORDER|NOACTIVATE

    def _dpi_scaled_size(self, new_dpi: int, lparam: int) -> bool:
        """Moving to a monitor with other scaling: Windows would rescale a caption and border we
        don't have, so the window would change size. The whole window is client area, so scale it
        exactly in proportion."""
        old_dpi = ctypes.windll.user32.GetDpiForWindow(wintypes.HWND(self.hwnd))
        if not old_dpi or not new_dpi:
            return False
        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
        size = wintypes.SIZE.from_address(lparam)
        size.cx = round((rect.right - rect.left) * new_dpi / old_dpi)
        size.cy = round((rect.bottom - rect.top) * new_dpi / old_dpi)
        return True

    def _hit_test(self, sx: int, sy: int) -> int:
        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
        dpr = self.window.devicePixelRatioF() or 1.0
        x, y = (sx - rect.left) / dpr, (sy - rect.top) / dpr
        w, h = (rect.right - rect.left) / dpr, (rect.bottom - rect.top) / dpr
        if not self.is_maximized() and not self.window.isFullScreen():
            vertical = self.vertical_resize() if self.vertical_resize else True
            near_l, near_r = x < CORNER, x >= w - CORNER
            left, right = x < EDGE, x >= w - EDGE
            top, bottom = vertical and y < TOP_EDGE, vertical and y >= h - EDGE
            if not vertical:  # corners act as the side edges; the bottom edge may still resize
                if self.bottom_resize and y >= h - EDGE:
                    return HTBOTTOMLEFT if near_l else HTBOTTOMRIGHT if near_r else HTBOTTOM
                return HTLEFT if left else HTRIGHT if right else self._hit_inside(x, y)
            near_l, near_r = x < CORNER, x >= w - CORNER
            near_t, near_b = y < CORNER, y >= h - CORNER
            if (top and near_l) or (left and near_t):
                return HTTOPLEFT
            if (top and near_r) or (right and near_t):
                return HTTOPRIGHT
            if (bottom and near_l) or (left and near_b):
                return HTBOTTOMLEFT
            if (bottom and near_r) or (right and near_b):
                return HTBOTTOMRIGHT
            if left:
                return HTLEFT
            if right:
                return HTRIGHT
            if top:
                return HTTOP
            if bottom:
                return HTBOTTOM
        return self._hit_inside(x, y)

    def _hit_inside(self, x: float, y: float) -> int:
        what = self.hit(QPoint(int(x), int(y)))
        if what == "caption":
            return HTCAPTION
        if what == "max":
            return HTMAXBUTTON
        return HTCLIENT
