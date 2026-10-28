"""Claude Code hook: records each session's state for the desktop buddy.

States: working, ask (needs permission / input), done (finished, waiting for you).
For permission requests it can also wait for an Allow / Deny click on the puppy.
"""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SESSIONS = os.path.join(HERE, "sessions")
PID_FILE = os.path.join(HERE, "widget.pid")
CONF = os.path.join(HERE, "buddy_conf.json")

LONG_TASK = 180  # seconds of work that earns a celebration
TEST_CMD = re.compile(r"\b(pytest|unittest|jest|vitest|mocha|go test|cargo test|npm (run )?test|"
                      r"yarn test|pnpm test|dotnet test|mvn test|gradle test|rspec|phpunit)\b")
TEST_FAIL = re.compile(r"\b[1-9]\d* (failed|failing|failures?|errors?)\b|\bFAIL(ED)?\b|Traceback")


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, rec):
    tmp = path + "." + os.urandom(3).hex() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f)
    os.replace(tmp, path)


def ensure_widget():
    import sysutil
    try:
        if sysutil.pid_alive(int(open(PID_FILE).read().strip())):
            return
    except (OSError, ValueError):
        pass
    sysutil.spawn_detached([sysutil.gui_python(), os.path.join(HERE, "buddy.pyw")])


def where():
    """Which processes and window this session lives in, so the puppy can jump back to it."""
    import sysutil
    pids = sysutil.ancestors()
    return {"pids": pids, "hwnd": sysutil.console_window(pids)}


def describe(tool, inp):
    detail = inp.get("command") or inp.get("file_path") or inp.get("url") or inp.get("pattern") or ""
    detail = " ".join(str(detail).split())
    return f"{tool}: {detail}" if detail else f"wants to use {tool}"


def wait_for_decision(path, rec, sid, pids, project, hwnd):
    """Hold the permission prompt while the puppy offers Allow / Deny. Returns a decision or None."""
    import sysutil
    conf = load(CONF, {})
    if not conf.get("approve", True) or sysutil.is_focused(pids, project, hwnd):
        return None  # user is right there: let the normal prompt show
    wait = conf.get("approve_wait", 60)
    req = rec["req"] = os.urandom(4).hex()
    rec["waiting"] = True
    save(path, rec)
    answer_path = os.path.join(SESSIONS, f"{sid}.answer.json")
    end = time.time() + wait
    while time.time() < end:
        ans = load(answer_path, None)
        if ans and ans.get("req") == req:
            try:
                os.remove(answer_path)
            except OSError:
                pass
            return ans
        if sysutil.is_focused(pids, project, hwnd):
            break  # they switched to the window; hand over to the normal prompt
        time.sleep(0.25)
    cur = load(path, {})
    if cur.get("req") == req:
        cur["waiting"] = False
        save(path, cur)
    return None


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    sys.path.insert(0, HERE)
    event = data.get("hook_event_name", "")
    sid = data.get("session_id") or "unknown"
    path = os.path.join(SESSIONS, sid + ".json")
    os.makedirs(SESSIONS, exist_ok=True)

    if event == "SessionStart":
        ensure_widget()
        return
    if event == "SessionEnd":
        for p in (path, os.path.join(SESSIONS, f"{sid}.answer.json")):
            try:
                os.remove(p)
            except OSError:
                pass
        return

    prev = load(path, {})
    now = time.time()
    project = os.path.basename((data.get("cwd") or "").rstrip("\\/")) or "claude"
    rec = dict(prev, project=project, updated=now)

    if event == "UserPromptSubmit":
        rec.update(state="working", message="", working_since=now, tests_passed=False,
                   waiting=False, celebrate=False)
    elif event == "PostToolUse":
        if prev.get("state") == "working" and not (data.get("tool_name") == "Bash"):
            return  # nothing changed; keep this hot path cheap
        rec.update(state="working", message="", waiting=False)
        rec.setdefault("working_since", now)
        if data.get("tool_name") == "Bash":
            cmd = (data.get("tool_input") or {}).get("command", "")
            resp = data.get("tool_response") or {}
            out = (resp.get("stdout", "") + resp.get("stderr", "")) if isinstance(resp, dict) else str(resp)
            if TEST_CMD.search(cmd):
                rec["tests_passed"] = not TEST_FAIL.search(out)
    elif event == "PostToolUseFailure":
        if data.get("tool_name") == "Bash" and TEST_CMD.search((data.get("tool_input") or {}).get("command", "")):
            rec["tests_passed"] = False
        else:
            return
    elif event == "PermissionRequest":
        import sysutil
        rec.update(where())
        pids, hwnd = rec["pids"], rec["hwnd"]
        rec.update(state="ask", message=describe(data.get("tool_name", "a tool"), data.get("tool_input") or {}),
                   alert_at=now, waiting=False, kind="permission",
                   suggest=bool(data.get("permission_suggestions")))
        save(path, rec)
        ans = wait_for_decision(path, rec, sid, pids, project, hwnd)
        if ans:
            decision = {"behavior": "allow" if ans["behavior"] in ("allow", "always") else "deny"}
            if ans["behavior"] == "always" and data.get("permission_suggestions"):
                decision["updatedPermissions"] = data["permission_suggestions"]
            if decision["behavior"] == "deny":
                decision["message"] = "The user denied this from Claude Buddy."
            cur = load(path, {})
            if cur.get("req") == rec.get("req"):
                cur.update(state="working", waiting=False, message="")
                save(path, cur)
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}))
        return
    elif event == "Notification":
        ntype = data.get("notification_type", "")
        if prev.get("state") in ("ask", "done") and ntype in ("idle_prompt", "permission_prompt", ""):
            return  # already pinged for this one
        if ntype in ("auth_success", "elicitation_complete", "elicitation_response") or ntype.startswith("quota"):
            return
        rec.update(where(), state="ask", message=data.get("message") or "Needs your input", alert_at=now,
                   waiting=False, kind="question")
    elif event == "Stop":
        since = prev.get("working_since") or now
        rec.update(where(), state="done", message="Finished - your turn", alert_at=now,
                   waiting=False, kind="done", celebrate=now - since >= LONG_TASK,
                   party=bool(prev.get("tests_passed")), took=round(now - since))
    else:
        return
    save(path, rec)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # never break Claude Code
