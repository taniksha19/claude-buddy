# Claude Buddy 🐶

A little puppy that peeks out from the edge of your screen and waves when a
[Claude Code](https://claude.com/claude-code) session needs you.

- **"woof! i need you ♡"**: Claude is waiting on a permission prompt or a question.
- **"all done! ♡"**: Claude finished its turn and it's your move.

The puppy stays hidden past the screen edge while Claude works. When something
needs you, it pops its head out from a random edge (or the one you choose),
waves its paw, beeps, and shows which project is waiting. Click it to send it
back. It also ducks away by itself once you reply in that session.

Works with every local Claude Code session: terminals, the VS Code extension,
and several sessions at once.

## Requirements

- Windows (it uses `winsound` and the Win32 API for the screen work area)
- Python 3.8+ with tkinter (the standard python.org installer includes it)

## Install

```sh
git clone https://github.com/taniksha19/claude-buddy.git
cd claude-buddy
python install.py
```

This adds hooks to `~/.claude/settings.json`. It leaves any existing hooks
alone. The puppy starts right away, and the hooks relaunch it with each new
Claude session if it isn't running. Start a new Claude session (or reload VS
Code) so the hooks take effect.

To remove it: `python install.py --uninstall`

## Using it

Right-click the puppy for:

- **Peek from**: random, bottom, top, left or right
- **Mute / Unmute**
- **Test wave**
- **Quit**

## How it works

`buddy_hook.py` runs on Claude Code hook events (`UserPromptSubmit`,
`PostToolUse`, `PermissionRequest`, `Notification`, `Stop`, `SessionStart`,
`SessionEnd`). It writes each session's state to `sessions/<id>.json`.
`buddy.pyw` is a borderless, always-on-top tkinter window that watches those
files and animates the puppy.
