"""Claude Code hook: records each session's state for the desktop buddy.

States: working, ask (needs permission / input), done (finished, waiting for you).
"""
import ctypes
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SESSIONS = os.path.join(HERE, "sessions")
PID_FILE = os.path.join(HERE, "widget.pid")


def widget_running():
    try:
        pid = int(open(PID_FILE).read().strip())
    except (OSError, ValueError):
        return False
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    k32.GetExitCodeProcess(h, ctypes.byref(code))
    k32.CloseHandle(h)
    return code.value == 259  # STILL_ACTIVE


def launch_widget():
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    subprocess.Popen(
        [pyw, os.path.join(HERE, "buddy.pyw")],
        creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | NEW_PROCESS_GROUP
        close_fds=True,
    )


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    event = data.get("hook_event_name", "")
    sid = data.get("session_id") or "unknown"
    path = os.path.join(SESSIONS, sid + ".json")
    os.makedirs(SESSIONS, exist_ok=True)

    if event == "SessionStart":
        if not widget_running():
            launch_widget()
        return
    if event == "SessionEnd":
        try:
            os.remove(path)
        except OSError:
            pass
        return

    try:
        prev = json.load(open(path))
    except (OSError, ValueError):
        prev = {}
    now = time.time()

    if event in ("UserPromptSubmit", "PostToolUse"):
        state, msg = "working", ""
    elif event == "PermissionRequest":
        state, msg = "ask", "Wants permission: " + data.get("tool_name", "a tool")
    elif event == "Notification":
        ntype = data.get("notification_type", "")
        if ntype == "idle_prompt" and prev.get("state") in ("done", "ask"):
            return  # already pinged for this
        state, msg = "ask", data.get("message", "Needs your input")
    elif event == "Stop":
        state, msg = "done", "Finished - your turn"
    else:
        return

    # Don't re-ping when PermissionRequest and Notification fire back to back.
    if state == prev.get("state") and state != "working" and now - prev.get("alert_at", 0) < 5:
        return

    rec = {
        "state": state,
        "message": msg,
        "project": os.path.basename(data.get("cwd", "") or "") or "claude",
        "updated": now,
        "alert_at": now if state != "working" else prev.get("alert_at", 0),
    }
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f)
    os.replace(tmp, path)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # never break Claude Code
