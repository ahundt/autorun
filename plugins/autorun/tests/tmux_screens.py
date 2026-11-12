"""Pane captures of Claude Code in each prompt state autorun reads.

One definition, shared by the unit tests of the send path and the tests
that draw these screens in a pane on the private tmux server, so both test
the same screens. NOT_READY maps the reason check_safe_to_send gives for a
screen to the screen; READY is the one screen text may be typed into.
"""

_BOX = "─" * 40 + "\n"

READY = "⏺ Done.\n\n" + _BOX + "❯ \n" + _BOX + "  ? for shortcuts\n"

NOT_READY = {
    # The tool permission dialog: Enter accepts the highlighted "1. Yes".
    "question": (
        " Bash command\n\n   rm -rf build\n\n Do you want to proceed?\n"
        " ❯ 1. Yes\n   2. Yes, and don't ask again for rm commands\n"
        "   3. No, and tell Claude what to do differently (esc)\n"),
    "plan_approval": (
        " Ready to code?\n\n Would you like to proceed?\n\n"
        " ❯ 1. Yes, and auto-accept edits\n   2. Yes, and manually approve edits\n"
        "   3. No, keep planning\n"),
    "tool_permission_yn": " Run npm install in ~/src/app? [Y/n]\n",
    "tool_permission_numbered": " Allow Bash(git push)?  [1] Allow once  [2] Allow always  [3] Deny\n",
    "happy_mode_switch": " 📱 Press space to switch to remote mode\n",
    "happy_remote": " 📱 Press space twice to switch to local mode\n",
    "error_prompt": " API Error: overloaded. Press Enter to continue\n",
    "clarification": "⏺ Should I also update the README files?\n",
    # Mid-turn: the input box is drawn below the spinner while Claude works.
    "active": (
        "⏺ Reading files\n✳ Working… (esc to interrupt · 7s · ↓ 44 tokens)\n\n"
        + _BOX + "❯ \n" + _BOX + "  ? for shortcuts\n"),
    "user_typing": READY.replace("❯ \n", "❯ fix the lint\n"),
}

#: The NOT_READY screens that are a dialog needing the person, not mid-turn
#: or half-typed input.
DIALOGS = [name for name in NOT_READY if name not in ("active", "user_typing")]
