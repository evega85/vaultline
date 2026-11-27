"""Typing into another window (Windows): the window in front, its admin level, and SendInput.

Characters go as KEYEVENTF_UNICODE, so the keyboard layout doesn't matter; Tab and Enter go as real
keys (many apps ignore a Unicode tab). Before every keystroke the target window must still be in
front, or typing stops. Nothing typed is ever logged or kept after typing.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from typing import Callable

from .autotype import DEFAULT_GAP_MS, VK_RETURN, VK_TAB, Step, describe_progress

WIN = sys.platform == "win32"

if WIN:
    import ctypes
    from ctypes import wintypes

    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _adv = ctypes.WinDLL("advapi32", use_last_error=True)

    ULONG_PTR = ctypes.c_size_t

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    _u32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
    _u32.SendInput.restype = wintypes.UINT
    _u32.GetForegroundWindow.restype = wintypes.HWND
    _u32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    _u32.GetAncestor.restype = wintypes.HWND
    _u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _u32.IsWindow.argtypes = [wintypes.HWND]
    _u32.IsWindowVisible.argtypes = [wintypes.HWND]
    _u32.IsIconic.argtypes = [wintypes.HWND]
    _u32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _u32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _u32.GetAsyncKeyState.restype = ctypes.c_short
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.GetCurrentProcess.restype = wintypes.HANDLE
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    _adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.DWORD)]
    _adv.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    _adv.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    _adv.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    _adv.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x2, 0x4
GA_ROOT = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x8
TOKEN_INTEGRITY_LEVEL = 25
_MODIFIERS = (0x10, 0x11, 0x12, 0x5B, 0x5C)  # Shift, Ctrl, Alt, left/right Win

# The taskbar, desktop, Start, task view and the like: never a window to fill into.
SHELL_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW", "NotifyIconOverflowWindow",
                 "TopLevelWindowForOverflowXamlIsland", "Windows.UI.Core.CoreWindow", "ForegroundStaging",
                 "MultitaskingViewFrame", "TaskListThumbnailWnd", "XamlExplorerHostIslandWindow",
                 "Shell_InputSwitchTopLevelWindow", "#32768"}


# ---- windows ------------------------------------------------------------------------------
def foreground_window() -> int:
    """The top-level window in front (0 when there's none, e.g. the lock screen)."""
    if not WIN:
        return 0
    hwnd = _u32.GetForegroundWindow()
    if not hwnd:
        return 0
    return int(_u32.GetAncestor(hwnd, GA_ROOT) or hwnd)


def window_title(hwnd: int) -> str:
    if not WIN or not hwnd:
        return ""
    n = _u32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 2)
    _u32.GetWindowTextW(hwnd, buf, n + 2)
    return buf.value


def window_class(hwnd: int) -> str:
    if not WIN or not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    _u32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd: int) -> int:
    if not WIN or not hwnd:
        return 0
    pid = wintypes.DWORD()
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def is_window(hwnd: int) -> bool:
    return bool(WIN and hwnd and _u32.IsWindow(hwnd))


def is_own(hwnd: int) -> bool:
    """A window of this process (Vaultline's own windows are never typed into)."""
    return window_pid(hwnd) == os.getpid()


def is_fill_target(hwnd: int) -> bool:
    """A window someone could be filling a login into: visible, titled, not ours, not the shell."""
    return (is_window(hwnd) and bool(_u32.IsWindowVisible(hwnd)) and not is_own(hwnd)
            and window_class(hwnd) not in SHELL_CLASSES and bool(window_title(hwnd).strip()))


def bring_to_front(hwnd: int) -> bool:
    """Give `hwnd` the keyboard (we're in front, so Windows lets us). Restores it if minimised."""
    if not is_window(hwnd):
        return False
    if _u32.IsIconic(hwnd):
        _u32.ShowWindow(hwnd, 9)  # SW_RESTORE
    _u32.SetForegroundWindow(hwnd)
    return foreground_window() == hwnd


# ---- admin level --------------------------------------------------------------------------
def _integrity(process_handle) -> int | None:
    token = wintypes.HANDLE()
    if not _adv.OpenProcessToken(process_handle, TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        size = wintypes.DWORD()
        _adv.GetTokenInformation(token, TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(size))
        if not size.value:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if not _adv.GetTokenInformation(token, TOKEN_INTEGRITY_LEVEL, buf, size, ctypes.byref(size)):
            return None
        sid = ctypes.c_void_p.from_buffer(buf).value  # TOKEN_MANDATORY_LABEL.Label.Sid
        count = _adv.GetSidSubAuthorityCount(sid).contents.value
        return int(_adv.GetSidSubAuthority(sid, count - 1).contents.value)
    finally:
        _k32.CloseHandle(token)


def runs_above_us(hwnd: int) -> bool:
    """True when the window's program runs at a higher integrity level than Vaultline (an admin
    window), or its level can't be read. Windows silently drops keys sent to such windows."""
    if not WIN:
        return False
    pid = window_pid(hwnd)
    if not pid:
        return True
    proc = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not proc:
        return True
    try:
        theirs = _integrity(proc)
    finally:
        _k32.CloseHandle(proc)
    ours = _integrity(_k32.GetCurrentProcess())
    return theirs is None or ours is None or theirs > ours


# ---- keys -------------------------------------------------------------------------------
def modifiers_down() -> bool:
    return WIN and any(_u32.GetAsyncKeyState(vk) & 0x8000 for vk in _MODIFIERS)


def _key(vk: int = 0, scan: int = 0, flags: int = 0):
    inp = _INPUT(type=INPUT_KEYBOARD)
    inp.u.ki = _KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return inp


def _send(inputs: list) -> bool:
    arr = (_INPUT * len(inputs))(*inputs)
    return _u32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT)) == len(inputs)


def send_vk(vk: int) -> bool:
    return _send([_key(vk=vk), _key(vk=vk, flags=KEYEVENTF_KEYUP)])


def send_char(ch: str) -> bool:
    """One character, layout-free. Characters outside the BMP (emoji) go as their two halves."""
    if ch == "\t":
        return send_vk(VK_TAB)
    if ch == "\n":
        return send_vk(VK_RETURN)
    if ch == "\r":
        return True
    data = ch.encode("utf-16-le")
    units = [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]
    downs = [_key(scan=u, flags=KEYEVENTF_UNICODE) for u in units]
    ups = [_key(scan=u, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP) for u in units]
    return _send(downs + ups)


# ---- typing -----------------------------------------------------------------------------
@dataclass
class Result:
    ok: bool
    message: str = ""  # why it stopped; never includes anything typed


def type_steps(steps: list[Step], target: int, *,
               foreground: Callable[[], int] = foreground_window,
               char: Callable[[str], bool] = None, key: Callable[[int], bool] = None,
               sleep: Callable[[float], None] = time.sleep,
               cancelled: Callable[[], bool] = lambda: False,
               modifiers: Callable[[], bool] = modifiers_down) -> Result:
    """Type `steps` into `target`, checking before every keystroke that it's still in front.
    Waits (up to 2 s) for the shortcut's Ctrl/Alt/Win to be let go first, so they don't mix in."""
    char = char or send_char
    key = key or send_vk
    waited = 0.0
    while modifiers():
        if waited >= 2.0:
            return Result(False, "Let go of Ctrl, Alt, Shift and Win so it can type.")
        sleep(0.02)
        waited += 0.02
    gap = DEFAULT_GAP_MS / 1000
    done: list[Step] = []

    def ready() -> str:
        if cancelled():
            return "Stopped: Vaultline locked"
        if foreground() != target:
            return "Stopped: the window lost focus"
        return ""

    for step in steps:
        if step.kind == "gap":
            gap = step.ms / 1000
        elif step.kind == "delay":
            sleep(step.ms / 1000)
        elif step.kind == "key":
            if why := ready():
                return Result(False, f"{why} ({describe_progress(done)}).")
            if not key(step.vk):
                return Result(False, f"Windows refused the keystroke ({describe_progress(done)}).")
            sleep(gap)
        elif step.kind == "text":
            for n, ch in enumerate(step.value):
                if why := ready():
                    where = (f"partway through the {step.part}" if n and step.part != "text"
                             else describe_progress(done))
                    return Result(False, f"{why} ({where}).")
                if not char(ch):
                    return Result(False, f"Windows refused the keystroke ({describe_progress(done)}).")
                sleep(gap)
        done.append(step)
    return Result(True)


# ---- which kind of box has the focus (UI Automation, read only) -------------------------
_CLSID_CUIAutomation = "{ff48dba4-60ef-4201-aa87-54103eef594e}"
_IID_IUIAutomation = "{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}"
_UIA_PROCESS_ID, _UIA_CONTROL_TYPE, _UIA_IS_PASSWORD = 30002, 30003, 30019
_UIA_VALUE = 30045  # ValuePattern's value: read ONLY from a browser's address bar (browser_address)
_UIA_NAME = 30005  # the box's label (from <label>, aria-label or placeholder in a browser), never its value
_UIA_EDIT = 50004
_UIA_COMBOBOX = 50003  # a drop-down (<select>): typing picks the option starting with what's typed
_VT_I4, _VT_BOOL, _VT_BSTR = 3, 11, 8

if WIN:
    class _GUID(ctypes.Structure):
        _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

    class _VARIANT(ctypes.Structure):  # just enough of VARIANT for VT_I4 / VT_BOOL
        _fields_ = [("vt", wintypes.WORD), ("r1", wintypes.WORD), ("r2", wintypes.WORD), ("r3", wintypes.WORD),
                    ("val", ctypes.c_longlong), ("pad", ctypes.c_void_p)]

    _ole = ctypes.WinDLL("ole32")
    _oleaut = ctypes.WinDLL("oleaut32")
    _ole.CLSIDFromString.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(_GUID)]
    _ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    _ole.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p, wintypes.DWORD,
                                      ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
    _oleaut.VariantClear.argtypes = [ctypes.POINTER(_VARIANT)]


def _com_call(obj: int, index: int, restype, argtypes: tuple = (), *args):
    """Call method `index` of a COM interface pointer (vtable order from the Windows SDK headers)."""
    vtbl = ctypes.cast(ctypes.c_void_p.from_address(obj).value, ctypes.POINTER(ctypes.c_void_p))
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])(obj, *args)


def _release(obj: int) -> None:
    if obj:
        _com_call(obj, 2, wintypes.ULONG)


def _int_property(element: int, prop: int) -> int | None:
    v = _VARIANT()
    if _com_call(element, 10, ctypes.HRESULT, (ctypes.c_int, ctypes.POINTER(_VARIANT)), prop, ctypes.byref(v)) != 0:  # GetCurrentPropertyValue
        return None
    try:
        if v.vt == _VT_BOOL:
            return 1 if ctypes.c_short(v.val & 0xFFFF).value else 0
        if v.vt == _VT_I4:
            return ctypes.c_int(v.val & 0xFFFFFFFF).value
        return None
    finally:
        _oleaut.VariantClear(ctypes.byref(v))


def _str_property(element: int, prop: int) -> str:
    v = _VARIANT()
    if _com_call(element, 10, ctypes.HRESULT, (ctypes.c_int, ctypes.POINTER(_VARIANT)), prop, ctypes.byref(v)) != 0:
        return ""
    try:
        if v.vt != _VT_BSTR or not v.val:
            return ""
        return ctypes.wstring_at(ctypes.c_void_p(v.val & 0xFFFFFFFFFFFFFFFF).value)[:200]
    finally:
        _oleaut.VariantClear(ctypes.byref(v))


def focused_box(target: int) -> str:
    """What has the keyboard focus inside `target`: "password" (a box that hides what's typed),
    "text" (any other text box), "choice" (a drop-down) or "" (can't tell: something else, another
    program, or no answer).
    Only the box's kind is read, never its contents. Call it on a thread of its own (it sets up COM)."""
    return focused_info(target)[0]


def focused_info(target: int, label: bool = False) -> tuple[str, str]:
    """(kind as in focused_box, the box's label if `label`). The label is the box's accessible
    name (what a screen reader would say: "First name", "Password"), never what's typed in it.
    Pages choose their labels, so a label only picks which profile value to offer; a password is
    still typed only where the kind is "password"."""
    if not WIN or not target:
        return "", ""
    try:
        _ole.CoInitializeEx(None, 0x2)  # apartment-threaded; harmless if already set up
        clsid, iid = _GUID(), _GUID()
        _ole.CLSIDFromString(_CLSID_CUIAutomation, ctypes.byref(clsid))
        _ole.CLSIDFromString(_IID_IUIAutomation, ctypes.byref(iid))
        uia = ctypes.c_void_p()
        if _ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(uia)) != 0:
            return "", ""
        el = ctypes.c_void_p()
        try:
            if _com_call(uia.value, 8, ctypes.HRESULT, (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(el)) != 0 or not el.value:  # GetFocusedElement
                return "", ""
            try:
                if _int_property(el.value, _UIA_PROCESS_ID) != window_pid(target):
                    return "", ""
                name = _str_property(el.value, _UIA_NAME) if label else ""
                if _int_property(el.value, _UIA_IS_PASSWORD) == 1:
                    return "password", name
                control = _int_property(el.value, _UIA_CONTROL_TYPE)
                return ({_UIA_EDIT: "text", _UIA_COMBOBOX: "choice"}.get(control, "")), name
            finally:
                _release(el.value)
        finally:
            _release(uia.value)
    except Exception:  # an odd program must never stop the fill: fall back to the full login
        return "", ""


# How browsers name their address bar (Chrome/Edge/Helium "Address and search bar", Firefox "Search with
# Google or enter address", French "Barre d'adresse..."). A page box like "Email address" must never match.
_ADDRESS_BAR = ("address and search", "enter address", "search or type", "barre d'adresse", "adresse et de recherche")


def browser_address(target: int) -> str:
    """What the browser window `target` shows in its address bar (its first text box, which must be
    named like an address bar), or "". This is the browser's own toolbar, never a box in the page.
    Call it on a thread of its own (it sets up COM)."""
    if not WIN or not target:
        return ""
    try:
        _ole.CoInitializeEx(None, 0x2)
        clsid, iid = _GUID(), _GUID()
        _ole.CLSIDFromString(_CLSID_CUIAutomation, ctypes.byref(clsid))
        _ole.CLSIDFromString(_IID_IUIAutomation, ctypes.byref(iid))
        uia = ctypes.c_void_p()
        if _ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(uia)) != 0:
            return ""
        root, cond, el = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        try:
            if _com_call(uia.value, 6, ctypes.HRESULT, (wintypes.HWND, ctypes.POINTER(ctypes.c_void_p)),
                         target, ctypes.byref(root)) != 0 or not root.value:  # ElementFromHandle
                return ""
            v = _VARIANT()
            v.vt, v.val = _VT_I4, _UIA_EDIT
            if _com_call(uia.value, 23, ctypes.HRESULT, (ctypes.c_int, _VARIANT, ctypes.POINTER(ctypes.c_void_p)),
                         _UIA_CONTROL_TYPE, v, ctypes.byref(cond)) != 0 or not cond.value:  # CreatePropertyCondition
                return ""
            if _com_call(root.value, 5, ctypes.HRESULT, (ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)),
                         4, cond.value, ctypes.byref(el)) != 0 or not el.value:  # FindFirst(TreeScope_Descendants)
                return ""
            name = _str_property(el.value, _UIA_NAME).casefold()
            if "mail" in name or not any(words in name for words in _ADDRESS_BAR):
                return ""  # not the address bar: never read a page's box
            return _str_property(el.value, _UIA_VALUE)
        finally:
            for obj in (el, cond, root, uia):
                _release(obj.value)
    except Exception:
        return ""


# ---- the window you came from (for Fill) --------------------------------------------------
class ForegroundWatcher:
    """Remembers the last window in front that could be filled (not ours, not the taskbar or
    desktop), using a WinEvent hook that runs on the Qt thread's message loop."""

    EVENT_SYSTEM_FOREGROUND = 3
    WINEVENT_OUTOFCONTEXT, WINEVENT_SKIPOWNPROCESS = 0x0, 0x2

    def __init__(self):
        self.last = 0
        self._hook = None
        self._proc = None
        if not WIN:
            return
        current = foreground_window()
        if is_fill_target(current):
            self.last = current
        proto = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG,
                                   wintypes.LONG, wintypes.DWORD, wintypes.DWORD)

        def on_event(_hook, _event, hwnd, id_object, _child, _thread, _time):
            if id_object != 0 or not hwnd:  # OBJID_WINDOW only
                return
            try:
                root = int(_u32.GetAncestor(hwnd, GA_ROOT) or hwnd)
                if is_fill_target(root):
                    self.last = root
            except Exception:
                pass

        self._proc = proto(on_event)  # kept alive: Windows calls it for as long as the hook exists
        _u32.SetWinEventHook.restype = wintypes.HANDLE
        self._hook = _u32.SetWinEventHook(self.EVENT_SYSTEM_FOREGROUND, self.EVENT_SYSTEM_FOREGROUND, None,
                                          self._proc, 0, 0,
                                          self.WINEVENT_OUTOFCONTEXT | self.WINEVENT_SKIPOWNPROCESS)

    def target(self) -> int:
        """The window to fill into, or 0 when it's gone."""
        return self.last if self.last and is_fill_target(self.last) else 0

    def stop(self) -> None:
        if self._hook:
            _u32.UnhookWinEvent(wintypes.HANDLE(self._hook))
            self._hook = None
