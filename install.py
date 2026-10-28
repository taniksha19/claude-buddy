"""Adds (or with --uninstall, removes) the Claude Buddy hooks in ~/.claude/settings.json."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
EVENTS = ["SessionStart", "SessionEnd", "UserPromptSubmit", "PostToolUse",
          "PermissionRequest", "Notification", "Stop"]
MATCHED = ("PostToolUse", "PermissionRequest")


def posix(p):
    return p.replace("\\", "/")


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
        cmd = f'"{posix(sys.executable)}" "{posix(os.path.join(HERE, "buddy_hook.py"))}"'
        for ev in EVENTS:
            group = {"hooks": [{"type": "command", "command": cmd, "timeout": 5}]}
            if ev in MATCHED:
                group["matcher"] = "*"
            hooks.setdefault(ev, []).append(group)

    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    with open(SETTINGS, "w") as f:
        json.dump(settings, f, indent=2)

    if uninstall:
        print("Removed Claude Buddy hooks from", SETTINGS)
        return
    print("Added Claude Buddy hooks to", SETTINGS)
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    subprocess.Popen([pyw, os.path.join(HERE, "buddy.pyw")], creationflags=0x00000008, close_fds=True)
    print("Puppy started. Right-click it -> Test wave. It also starts with each new Claude session.")


if __name__ == "__main__":
    main()
