"""Adds (or with --uninstall, removes) the Claude Buddy hooks in ~/.claude/settings.json."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
EVENTS = ["SessionStart", "SessionEnd", "UserPromptSubmit", "PostToolUse", "PostToolUseFailure",
          "PermissionRequest", "Notification", "Stop"]
MATCHED = ("PostToolUse", "PostToolUseFailure", "PermissionRequest")
TIMEOUTS = {"PermissionRequest": 120}  # it may wait for an Allow / Deny click on the puppy


def posix(p):
    return p.replace("\\", "/")


def ensure_pillow():
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("Installing Pillow (used to draw the buddy)...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", "pillow"])


def main():
    uninstall = "--uninstall" in sys.argv
    try:
        settings = json.load(open(SETTINGS))
    except FileNotFoundError:
        settings = {}
    hooks = settings.setdefault("hooks", {})

    # Drop any previous buddy hooks so reinstalling never duplicates them.
    for ev in list(hooks):
        groups = []
        for g in hooks[ev]:
            g["hooks"] = [h for h in g.get("hooks", []) if "buddy_hook.py" not in h.get("command", "")]
            if g["hooks"]:
                groups.append(g)
        if groups:
            hooks[ev] = groups
        else:
            del hooks[ev]

    if not uninstall:
        ensure_pillow()
        cmd = f'"{posix(sys.executable)}" -S "{posix(os.path.join(HERE, "buddy_hook.py"))}"'
        for ev in EVENTS:
            group = {"hooks": [{"type": "command", "command": cmd, "timeout": TIMEOUTS.get(ev, 5)}]}
            if ev in MATCHED:
                group["matcher"] = "*"
            hooks.setdefault(ev, []).append(group)

    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    with open(SETTINGS, "w") as f:
        json.dump(settings, f, indent=2)

    sys.path.insert(0, HERE)
    import sysutil
    try:
        pid = int(open(os.path.join(HERE, "widget.pid")).read().strip())
        if sysutil.pid_alive(pid):
            sysutil.kill(pid)  # restart so the new version runs
    except (OSError, ValueError):
        pass
    if uninstall:
        print("Removed Claude Buddy hooks from", SETTINGS)
        return
    print("Added Claude Buddy hooks to", SETTINGS)
    sysutil.spawn_detached([sysutil.gui_python(), os.path.join(HERE, "buddy.pyw")])
    print("Buddy started. Right-click it or its tray icon -> Test wave. It also starts with each Claude session.")


if __name__ == "__main__":
    main()
