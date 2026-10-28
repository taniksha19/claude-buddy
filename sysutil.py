"""OS helpers shared by the hook and the widget: process tree, window focus, sound, DND.

Windows is the main target. macOS and Linux get best-effort versions of the same calls.
"""
import os
import subprocess
import sys

WIN = sys.platform == "win32"
MAC = sys.platform == "darwin"

if WIN:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.MonitorFromPoint.restype = wintypes.HANDLE
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


# --- processes ---------------------------------------------------------------

def _parent_map():
    if WIN:
        snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        parents = {}
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
        kernel32.CloseHandle(snap)
        return parents
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True).stdout
    return {int(a): int(b) for a, b in (line.split() for line in out.splitlines() if line.strip())}


def ancestors(pid=None):
    """PIDs from our parent up to the root, e.g. bash -> claude -> pwsh -> WindowsTerminal."""
    try:
        parents = _parent_map()
    except Exception:
        return []
    chain, pid = [], parents.get(pid or os.getpid())
    while pid and pid not in chain and len(chain) < 30:
        chain.append(pid)
        pid = parents.get(pid)
    return chain


def pid_alive(pid):
    if WIN:
        h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        kernel32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kill(pid):
    if WIN:
        h = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        if h:
            kernel32.TerminateProcess(h, 0)
            kernel32.CloseHandle(h)
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass


def spawn_detached(args):
    if WIN:
        subprocess.Popen(args, creationflags=0x00000008 | 0x00000200, close_fds=True)  # DETACHED | NEW_GROUP
    else:
        subprocess.Popen(args, start_new_session=True, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def gui_python():
    """pythonw on Windows (no console window), the same interpreter elsewhere."""
    if WIN:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if os.path.exists(pyw):
            return pyw
    return sys.executable


# --- windows -----------------------------------------------------------------

SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}  # desktop, taskbar


def _class(hwnd):
    buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, buf, 64)
    return buf.value


def console_window(pids):
    """The visible window hosting the console Claude runs in (Windows Terminal, conhost...).

    Windows Terminal isn't an ancestor of the shell, but the console's pseudo-window is owned by it.
    """
    if not WIN:
        return None
    kernel32.GetConsoleWindow.restype = wintypes.HWND
    user32.GetWindow.restype = wintypes.HWND
    for pid in pids:
        kernel32.FreeConsole()
        if not kernel32.AttachConsole(pid):
            continue
        con = kernel32.GetConsoleWindow()
        kernel32.FreeConsole()
        if not con:
            continue
        for hwnd in (user32.GetWindow(con, 4), con):  # owner first (the terminal app), then the console itself
            if hwnd and user32.IsWindowVisible(hwnd):
                return hwnd
        return None  # attached, but the console is hidden (e.g. the VS Code extension)
    return None


def _windows_of(pids):
    """Visible top-level windows owned by any of `pids`, as (hwnd, title), in z-order."""
    found = []
    want = set(pids)

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4) and _class(hwnd) not in SHELL_CLASSES:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in want:
                n = user32.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, buf, n + 1)
                    found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def _pick(windows, hint):
    # VS Code runs every window in one process, so tell them apart by the folder in the title.
    if hint:
        for w in windows:
            if hint.lower() in w[1].lower():
                return w
    return windows[0] if windows else None


def session_window(pids, hint="", hwnd=None):
    if not WIN:
        return None
    if hwnd and user32.IsWindow(hwnd) and user32.IsWindowVisible(hwnd):
        return hwnd
    # Otherwise the nearest ancestor that owns a window (e.g. VS Code for its Claude extension).
    for pid in pids or []:
        w = _pick(_windows_of([pid]), hint)
        if w:
            return w[0]
    return None


def is_focused(pids, hint="", hwnd=None):
    """True when the user is already looking at the window this session runs in."""
    if WIN:
        fg = user32.GetForegroundWindow()
        if not fg:
            return False
        if hwnd and user32.IsWindow(hwnd):
            return fg == hwnd
        if not pids:
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
        if pid.value not in pids:
            return False
        own = _windows_of([pid.value])
        if len(own) <= 1 or not hint:
            return True
        match = _pick(own, hint)
        return match is None or match[0] == fg or hint.lower() not in match[1].lower()
    if not pids:
        return False
    if MAC:
        try:
            out = subprocess.run(["osascript", "-e", 'tell application "System Events" to get unix id of '
                                  "first process whose frontmost is true"],
                                 capture_output=True, text=True, timeout=2).stdout.strip()
            return int(out) in pids
        except Exception:
            return False
    return False


def focus(pids, hint="", hwnd=None):
    """Bring the session's terminal / editor window to the front."""
    if WIN:
        hwnd = session_window(pids, hint, hwnd)
        if not hwnd:
            return False
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        # Windows only lets the foreground process hand focus over; a tap of Alt unlocks it.
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        user32.SetForegroundWindow(hwnd)
        return True
    if MAC:
        for pid in pids:
            r = subprocess.run(["osascript", "-e", 'tell application "System Events" to set frontmost of '
                                f"(first process whose unix id is {pid}) to true"],
                               capture_output=True, timeout=3)
            if r.returncode == 0:
                return True
        return False
    try:  # Linux: needs wmctrl
        out = subprocess.run(["wmctrl", "-lp"], capture_output=True, text=True, timeout=2).stdout
        for line in out.splitlines():
            parts = line.split(None, 4)
            if len(parts) >= 3 and parts[2].isdigit() and int(parts[2]) in pids:
                subprocess.run(["wmctrl", "-ia", parts[0]], timeout=2)
                return True
    except Exception:
        pass
    return False


# --- screen ------------------------------------------------------------------

def dpi_scale():
    if WIN:
        try:
            return user32.GetDpiForSystem() / 96
        except Exception:
            return 1.0
    return 1.0


def work_area_at_cursor():
    """Work area (excluding the taskbar) of the monitor the mouse is on."""
    if WIN:
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        mon = user32.MonitorFromPoint(pt, 2)  # MONITOR_DEFAULTTONEAREST
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        user32.GetMonitorInfoW(mon, ctypes.byref(info))
        r = info.rcWork
        return r.left, r.top, r.right, r.bottom
    return None


def presenting():
    """True during full-screen apps / presentations, when popping up would be rude."""
    if not WIN:
        return False
    state = ctypes.c_int()
    try:
        ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
    except Exception:
        return False
    return state.value in (2, 3, 4)  # BUSY (full screen), D3D full screen, presentation mode


# --- sound -------------------------------------------------------------------

def play(path=None, beep_kind="ask"):
    if WIN:
        import winsound
        if path and os.path.exists(path):
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        else:
            winsound.MessageBeep(winsound.MB_ICONHAND if beep_kind == "ask" else winsound.MB_ICONASTERISK)
        return
    if not path:
        print("\a", end="", flush=True)
        return
    for player in (["afplay"], ["paplay"], ["aplay", "-q"]):
        try:
            subprocess.Popen(player + [path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            continue
