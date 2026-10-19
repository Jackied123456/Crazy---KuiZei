"""Windows shell helpers for Tk; no camera, recording, network, or new dependencies.

Public API:
    NativeTray(root, on_open, on_pause, on_sensitive, on_stats, on_adjust, on_exit)
        .update(paused, sensitive); .close()
    configure_floating_window(toplevel, click_through=True, no_activate=True) -> HWND
    GlobalHotkey(root, callback, modifiers=MOD_CONTROL | MOD_ALT, key=0x48)
        .close()

Create and close these objects on the Tk/UI thread. The independent hidden native
windows use Tk's normal Windows message loop; no additional message pump runs.
"""

from __future__ import annotations

import argparse
from collections import deque
import ctypes
from ctypes import wintypes
import sys
import uuid
from small_mirror_brand import PRODUCT_TITLE,PRODUCT_CN


MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
VK_H = 0x48

WM_NULL = 0x0000
WM_MOUSEACTIVATE = 0x0021
WM_NCDESTROY = 0x0082
WM_CONTEXTMENU = 0x007B
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_HOTKEY = 0x0312
WM_TRAY = 0x8007
MA_NOACTIVATE = 3

GWL_EXSTYLE = -20
GA_ROOT = 2
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
LWA_ALPHA = 0x00000002

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
MF_CHECKED = 0x00000008
TPM_RIGHTBUTTON = 0x00000002
TPM_NONOTIFY = 0x00000080
TPM_RETURNCMD = 0x00000100
IDI_INFORMATION = 32516

LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
UINT_PTR = ctypes.c_size_t
DWORD_PTR = ctypes.c_size_t
LONG_PTR = ctypes.c_ssize_t
CALLBACK = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
WNDPROC = CALLBACK(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)
SUBCLASSPROC = CALLBACK(LRESULT, wintypes.HWND, wintypes.UINT,
                       WPARAM, LPARAM, UINT_PTR, DWORD_PTR)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256), ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID), ("hBalloonIcon", wintypes.HICON),
    ]


def _error(message):
    code = ctypes.get_last_error()
    detail = ctypes.FormatError(code).strip() if code else "系统未提供错误代码"
    return RuntimeError(f"{message}（Windows 错误 {code}：{detail}）")


class _WinAPI:
    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("托盘、全局快捷键和无焦点横条仅支持 Windows。")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.comctl32 = ctypes.WinDLL("comctl32", use_last_error=True)
        u, s, k, c = self.user32, self.shell32, self.kernel32, self.comctl32
        k.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        k.GetModuleHandleW.restype = wintypes.HMODULE
        u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        u.RegisterClassW.restype = wintypes.ATOM
        u.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        u.UnregisterClassW.restype = wintypes.BOOL
        u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR,
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
            wintypes.HINSTANCE, ctypes.c_void_p]
        u.CreateWindowExW.restype = wintypes.HWND
        u.DestroyWindow.argtypes = [wintypes.HWND]
        u.DestroyWindow.restype = wintypes.BOOL
        u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
        u.DefWindowProcW.restype = LRESULT
        u.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        u.RegisterWindowMessageW.restype = wintypes.UINT
        u.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
        u.LoadIconW.restype = wintypes.HICON
        s.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        s.Shell_NotifyIconW.restype = wintypes.BOOL
        u.CreatePopupMenu.argtypes = []
        u.CreatePopupMenu.restype = wintypes.HMENU
        u.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, UINT_PTR, wintypes.LPCWSTR]
        u.AppendMenuW.restype = wintypes.BOOL
        u.DestroyMenu.argtypes = [wintypes.HMENU]
        u.DestroyMenu.restype = wintypes.BOOL
        u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        u.GetCursorPos.restype = wintypes.BOOL
        u.SetForegroundWindow.argtypes = [wintypes.HWND]
        u.SetForegroundWindow.restype = wintypes.BOOL
        u.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
        u.TrackPopupMenu.restype = wintypes.UINT
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
        u.PostMessageW.restype = wintypes.BOOL
        u.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        u.RegisterHotKey.restype = wintypes.BOOL
        u.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        u.UnregisterHotKey.restype = wintypes.BOOL
        u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetAncestor.restype = wintypes.HWND
        long_get = u.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else u.GetWindowLongW
        long_set = u.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else u.SetWindowLongW
        long_get.argtypes = [wintypes.HWND, ctypes.c_int]
        long_get.restype = LONG_PTR
        long_set.argtypes = [wintypes.HWND, ctypes.c_int, LONG_PTR]
        long_set.restype = LONG_PTR
        self.GetWindowLongPtrW, self.SetWindowLongPtrW = long_get, long_set
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.SetWindowPos.restype = wintypes.BOOL
        u.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD,
                                                ctypes.c_ubyte, wintypes.DWORD]
        u.SetLayeredWindowAttributes.restype = wintypes.BOOL
        c.SetWindowSubclass.argtypes = [wintypes.HWND, SUBCLASSPROC, UINT_PTR, DWORD_PTR]
        c.SetWindowSubclass.restype = wintypes.BOOL
        c.RemoveWindowSubclass.argtypes = [wintypes.HWND, SUBCLASSPROC, UINT_PTR]
        c.RemoveWindowSubclass.restype = wintypes.BOOL
        c.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
        c.DefSubclassProc.restype = LRESULT


_api = None


def _get_api():
    global _api
    if _api is None:
        _api = _WinAPI()
    return _api


class _MessageWindow:
    """A hidden native window owned by the same thread as the Tk root."""

    def __init__(self, root, handler):
        self.root, self.handler = root, handler
        self.api = _get_api()
        self.hwnd = None
        self.closed = False
        self.last_error = ""
        self.pending = deque()
        self.after_id = None
        self.class_name = "SmallMirror_" + uuid.uuid4().hex
        self.instance = self.api.kernel32.GetModuleHandleW(None)
        self.wndproc = WNDPROC(self._dispatch)
        wc = WNDCLASSW()
        wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = self.wndproc, self.instance, self.class_name
        if not self.api.user32.RegisterClassW(ctypes.byref(wc)):
            raise _error("无法创建 Windows 消息窗口")
        self.hwnd = self.api.user32.CreateWindowExW(
            0, self.class_name, PRODUCT_TITLE, 0, 0, 0, 0, 0,
            None, None, self.instance, None)
        if not self.hwnd:
            self.api.user32.UnregisterClassW(self.class_name, self.instance)
            raise _error("无法创建 Windows 消息窗口")
        self.after_id = self.root.after(25, self._drain)

    def _dispatch(self, hwnd, message, wparam, lparam):
        try:
            if not self.closed:
                result = self.handler(hwnd, message, wparam, lparam)
                if result is not None:
                    return result
        except Exception as exc:
            # Exceptions must never escape a native callback boundary.
            self.last_error = str(exc)
        return self.api.user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def schedule(self, callback):
        if self.closed:
            return
        # Never enter Tcl from a ctypes WndProc: Tk may currently have released
        # its saved Python thread state. The Tk timer safely drains this queue.
        self.pending.append(callback)

    def _drain(self):
        self.after_id = None
        try:
            for _ in range(min(32, len(self.pending))):
                if self.closed:
                    break
                self.pending.popleft()()
        finally:
            if not self.closed:
                self.after_id = self.root.after(25, self._drain)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.pending.clear()
        if self.after_id is not None:
            try:
                self.root.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None
        if self.hwnd:
            self.api.user32.DestroyWindow(self.hwnd)
            self.hwnd = None
        self.api.user32.UnregisterClassW(self.class_name, self.instance)


class NativeTray:
    def __init__(self, root, on_open, on_pause, on_sensitive,
                 on_stats, on_adjust, on_exit):
        self.root = root
        self.api = _get_api()
        self.paused = False
        self.sensitive = False
        self.closed = False
        self.last_error = ""
        self.callbacks = {1: on_open, 2: on_pause, 3: on_sensitive,
                          4: on_stats, 5: on_adjust, 6: on_exit}
        self.taskbar_created = self.api.user32.RegisterWindowMessageW("TaskbarCreated")
        self.window = _MessageWindow(root, self._on_message)
        self.hwnd = self.window.hwnd
        self.icon_data = NOTIFYICONDATAW()
        self.icon_data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        self.icon_data.hWnd = self.hwnd
        self.icon_data.uID = 1
        self.icon_data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self.icon_data.uCallbackMessage = WM_TRAY
        self.icon_data.hIcon = self.api.user32.LoadIconW(None, ctypes.c_void_p(IDI_INFORMATION))
        self.icon_data.szTip = PRODUCT_TITLE+" · 监测中"
        if not self.icon_data.hIcon:
            self.window.close()
            raise _error("无法加载系统托盘图标")
        if not self.api.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.icon_data)):
            self.window.close()
            raise _error("无法添加系统托盘图标")

    def _on_message(self, hwnd, message, wparam, lparam):
        if message == WM_TRAY:
            # Version-0 notification semantics: lParam is the mouse message.
            if lparam in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self.window.schedule(self._show_menu)
            elif lparam == WM_LBUTTONDBLCLK:
                self.window.schedule(self.callbacks[1])
            return 0
        if self.taskbar_created and message == self.taskbar_created:
            if not self.api.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.icon_data)):
                self.last_error = "资源管理器重启后无法恢复托盘图标。"
            return 0
        return None

    def _show_menu(self):
        if self.closed:
            return
        u = self.api.user32
        menu = u.CreatePopupMenu()
        if not menu:
            raise _error("无法打开托盘菜单")
        try:
            entries = [(1, "打开"+PRODUCT_CN, False),
                       (2, "开始" if self.paused else "暂停", False),
                       (4, "统计", False), (6, "退出", False)]
            for command, label, checked in entries:
                if not u.AppendMenuW(menu, MF_STRING | (MF_CHECKED if checked else 0), command, label):
                    raise _error("无法创建托盘菜单项")
            point = wintypes.POINT()
            if not u.GetCursorPos(ctypes.byref(point)):
                raise _error("无法取得鼠标位置")
            # Foreground ownership lets Windows dismiss the menu when clicked away.
            u.SetForegroundWindow(self.hwnd)
            command = u.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                                       point.x, point.y, 0, self.hwnd, None)
            u.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        finally:
            u.DestroyMenu(menu)
        if command in self.callbacks:
            self.window.schedule(self.callbacks[command])

    def update(self, paused, sensitive):
        self.paused, self.sensitive = bool(paused), bool(sensitive)
        if self.closed:
            return
        state = "已暂停" if self.paused else "监测中"
        self.icon_data.szTip = f"{PRODUCT_TITLE} · {state}"
        if not self.api.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self.icon_data)):
            self.last_error = "无法更新托盘图标状态。"

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.api.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.icon_data))
        self.window.close()
        self.hwnd = None


def floating_exstyle(previous, click_through=True, no_activate=True):
    """Pure style computation, suitable for tests without native calls."""
    style = int(previous) | WS_EX_TOOLWINDOW
    style = (style | WS_EX_NOACTIVATE) if no_activate else (style & ~WS_EX_NOACTIVATE)
    if click_through:
        style |= WS_EX_TRANSPARENT | WS_EX_LAYERED
    else:
        style &= ~WS_EX_TRANSPARENT
    return style


_FLOATING_SUBCLASS_ID = 0xA11CE
_floating_windows = set()


def _floating_subclass_impl(hwnd, message, wparam, lparam, subclass_id, reference):
    api = _get_api()
    if message == WM_MOUSEACTIVATE and hwnd in _floating_windows:
        # Do not eat the mouse message: dragging still works in adjustment mode.
        return MA_NOACTIVATE
    if message == WM_NCDESTROY:
        api.comctl32.RemoveWindowSubclass(hwnd, _FLOATING_SUBCLASS, _FLOATING_SUBCLASS_ID)
        _floating_windows.discard(hwnd)
    return api.comctl32.DefSubclassProc(hwnd, message, wparam, lparam)


# Keep one callback alive for the whole module lifetime, including native teardown.
_FLOATING_SUBCLASS = SUBCLASSPROC(_floating_subclass_impl)


def configure_floating_window(toplevel, click_through=True, no_activate=True):
    """Apply styles after Tk overrideredirect setup, preferably before deiconify.

    Calling again with click_through=False allows drag events without activating
    the window. WM_NCDESTROY removes the safe comctl32 subclass automatically;
    Tk's original wndproc is never replaced. Call on the owning Tk/UI thread.
    """
    api = _get_api()
    toplevel.update_idletasks()
    client = int(toplevel.winfo_id())
    hwnd = api.user32.GetAncestor(client, GA_ROOT) or client
    previous = api.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    style = floating_exstyle(previous, click_through, no_activate)
    ctypes.set_last_error(0)
    result = api.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)
    if result == 0 and ctypes.get_last_error():
        raise _error("无法设置横条窗口样式")
    if click_through:
        if not api.user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA):
            raise _error("无法设置横条鼠标穿透")
    if no_activate:
        if not api.comctl32.SetWindowSubclass(hwnd, _FLOATING_SUBCLASS, _FLOATING_SUBCLASS_ID, 0):
            raise _error("无法设置横条不抢焦点")
        _floating_windows.add(hwnd)
    elif hwnd in _floating_windows:
        api.comctl32.RemoveWindowSubclass(hwnd, _FLOATING_SUBCLASS, _FLOATING_SUBCLASS_ID)
        _floating_windows.discard(hwnd)
    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED
    if not api.user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0, flags):
        raise _error("无法设置横条置顶")
    return hwnd


class GlobalHotkey:
    def __init__(self, root, callback, modifiers=MOD_CONTROL | MOD_ALT, key=VK_H):
        self.root, self.callback = root, callback
        self.api = _get_api()
        self.closed = False
        self.registered = False
        self.hotkey_id = 1
        self.window = _MessageWindow(root, self._on_message)
        self.hwnd = self.window.hwnd
        self.modifiers, self.key = modifiers, key
        if not self.api.user32.RegisterHotKey(self.hwnd, self.hotkey_id,
                                              modifiers | MOD_NOREPEAT, key):
            error = _error("无法注册全局快捷键，可能已被其他程序占用。请更换快捷键")
            self.window.close()
            self.hwnd = None
            raise error
        self.registered = True

    def _on_message(self, hwnd, message, wparam, lparam):
        if message == WM_HOTKEY and wparam == self.hotkey_id:
            if not self.closed and self.registered:
                self.window.schedule(self.callback)
            return 0
        return None

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.registered:
            self.api.user32.UnregisterHotKey(self.hwnd, self.hotkey_id)
            self.registered = False
        self.window.close()
        self.hwnd = None


def self_test():
    style = floating_exstyle(0)
    assert style & WS_EX_TOOLWINDOW
    assert style & WS_EX_NOACTIVATE
    assert style & WS_EX_LAYERED
    assert style & WS_EX_TRANSPARENT
    dragging = floating_exstyle(style, click_through=False)
    assert dragging & WS_EX_NOACTIVATE and not dragging & WS_EX_TRANSPARENT
    normal = floating_exstyle(dragging, False, False)
    assert not normal & WS_EX_NOACTIVATE
    assert normal & WS_EX_TOOLWINDOW
    assert ctypes.sizeof(WPARAM) == ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(LPARAM) == ctypes.sizeof(ctypes.c_void_p)
    if sys.platform == "win32" and ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(WNDCLASSW) == 72
        assert ctypes.sizeof(NOTIFYICONDATAW) == 976
    print("SHELL SELF-TEST PASS: style transitions and pointer-sized Windows types; no native side effects.")


def smoke_test():
    """Exercise real native lifecycles briefly; never imports or accesses a camera."""
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    calls = []
    tray = hotkey = bar = None
    try:
        tray = NativeTray(root, lambda: calls.append("open"), lambda: None,
                          lambda: None, lambda: None, lambda: None, lambda: None)
        tray.update(paused=True, sensitive=True)
        # F24 avoids reserving the application's everyday Ctrl+Alt+H in a test.
        hotkey = GlobalHotkey(root, lambda: calls.append("hotkey"), key=0x87)
        bar = tk.Toplevel(root)
        bar.withdraw()
        bar.overrideredirect(True)
        bar.geometry("200x80+20+20")
        hwnd = configure_floating_window(bar)
        assert hwnd in _floating_windows
        configure_floating_window(bar, click_through=False)
        assert _get_api().GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_NOACTIVATE
        _get_api().user32.PostMessageW(hotkey.hwnd, WM_HOTKEY, hotkey.hotkey_id, 0)
        _get_api().user32.PostMessageW(tray.hwnd, WM_TRAY, 1, WM_LBUTTONDBLCLK)
        # Timer-driven dispatch ensures no Tcl call occurs inside a native callback.
        root.after(90, root.quit)
        root.mainloop()
        assert "hotkey" in calls and "open" in calls, calls
        bar.destroy()
        bar = None
        assert hwnd not in _floating_windows
    finally:
        if bar is not None:
            bar.destroy()
        if hotkey is not None:
            hotkey.close()
        if tray is not None:
            tray.close()
        root.destroy()
    print("SHELL SMOKE-TEST PASS: native tray, deferred callbacks, hotkey, floating styles and cleanup; no camera.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Windows shell helpers; never accesses a camera.")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args()
    if args.smoke_test:
        smoke_test()
    else:
        self_test()
