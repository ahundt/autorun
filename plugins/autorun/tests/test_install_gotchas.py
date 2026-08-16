"""Failure modes the new design makes possible, which no earlier test covers.

The old installer could not express most of these: it had no `Intent`, no
per-file shared ownership, and no manifest. Each test here is a way the new
shape can go wrong that the old shape could not, so none of them is inherited
coverage — they are the cost of the redesign, written down.

Where a case is genuinely undefined rather than wrong, the test pins the
behaviour that was chosen and says why, so a later change is a decision rather
than an accident.
"""
from __future__ import annotations

import os
import json
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.installer.fs import (  # noqa: E402
    OWNED_MARKER_NAME,
    Verdict,
    compare,
    decide,
    decide_files,
    publish_files,
    publish_tree,
    read_marker,
    withdraw_files,
    withdrawn,
)
from autorun.installer.traversal import (
    Kind,  # noqa: E402
    Context,
    Intent,
    Mode,
    Step,
    run,
    walk,
)


@dataclass(frozen=True, slots=True)
class Fake:
    name: str
    install_steps: tuple[Step, ...]


def _home_context(tmp_path, monkeypatch, marketplace_root):
    """A Context whose `home` agrees with `$HOME`, which is the only seam.

    Setting the field alone moves some routes and not others: anything anchored
    at `Path.home()` reads `$HOME` directly. `Context` refuses the mismatch, so
    a test that wants an isolated home redirects the variable.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    # Both names: Path.home() resolves through os.path.expanduser, which reads
    # USERPROFILE on Windows and HOME elsewhere and never consults the other,
    # so setting one isolates this test on one platform and lets it write the
    # real home on the other.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return Context(marketplace_root=marketplace_root, home=home)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "src" / "demo"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("v1\n", encoding="utf-8")
    return root


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch) -> Context:
    return _home_context(tmp_path, monkeypatch, tmp_path)


# ─── Steps that behave unusually ────────────────────────────────────────────


def test_a_step_yielding_nothing_is_not_an_error(ctx):
    """A harness whose capability does not apply — no skills shipped, no
    commands — must contribute nothing rather than failing the whole install."""
    def empty(harness, context):
        return ()

    assert list(walk([Fake("h", (empty,))], ctx)) == []
    assert run([Fake("h", (empty,))], ctx, Mode.INSTALL) == []


def test_a_harness_with_no_steps_contributes_nothing(ctx):
    """This is how an unsupported harness stays out of the walk without
    anything testing for its name."""
    assert list(walk([Fake("h", ())], ctx)) == []


def test_a_step_that_raises_is_not_swallowed(ctx):
    """A step failing means autorun could not work out what to do. Continuing
    would install a partial set and report success."""
    def broken(harness, context):
        raise RuntimeError("cannot resolve plugin dir")

    with pytest.raises(RuntimeError, match="cannot resolve"):
        list(walk([Fake("h", (broken,))], ctx))


@pytest.mark.parametrize("poison", ["hooks", "marketplace", "guidance"])
def test_shared_file_preflight_prevents_every_durable_install_write(
    tmp_path, monkeypatch, poison
):
    from autorun.installer.memory import Block
    from autorun.installer.orchestrate import install
    from autorun.platforms import PLATFORMS

    home = tmp_path / poison / "home"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    if poison == "hooks":
        target = home / ".codex" / "hooks.json"
        content = '{"hooks":'
    elif poison == "marketplace":
        target = home / ".agents" / "plugins" / "marketplace.json"
        content = json.dumps({"plugins": {}})
    else:
        target = home / ".codex" / "AGENTS.md"
        content = Block("codex-agents-md").start + "\nmissing end marker\n"
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    before = {
        path.relative_to(home): path.read_bytes()
        for path in home.rglob("*")
        if path.is_file()
    }
    calls = []

    result = install(
        marketplace_root=Path(__file__).resolve().parents[3],
        plugins=("ar",),
        settings={
            "_codex_hook_command": "autorun --cli codex",
            "_guidance": {"codex": "guidance"},
            "codex_hook_source": "user",
            "codex_plugin_marketplace": "personal",
            "skill_placement": {"codex": "auto"},
        },
        home=home,
        harnesses=(PLATFORMS["codex"],),
        available=(),
        run_command=lambda argv: calls.append(tuple(argv)),
        state_dir=tmp_path / poison / "state",
    )

    after = {
        path.relative_to(home): path.read_bytes()
        for path in home.rglob("*")
        if path.is_file()
    }
    assert result.ok is False
    assert any(finding.check == "installer preflight" for finding in result.findings)
    assert after == before
    assert calls == []


def test_two_steps_yielding_an_identical_intent_decide_it_once(ctx, source, tmp_path):
    """One (kind, target, source, plugin, settings) tuple is one question,
    however many steps or harnesses ask it. Six harnesses read the shared
    ``~/.agents/skills`` root and each yields the same intent per skill; before
    this the report listed every shared skill six times and the install hashed
    each published tree six times to conclude "already current". The outcome
    was already single, so the extra decisions carried no information — an
    identical second intent can only ever SKIP.

    An intent that differs in any field is still walked separately (see the
    shared-and-exclusive test below), so a genuinely different step is never
    hidden."""
    target = tmp_path / "dest" / "demo"
    def step(h, c):
        return (Intent(target=target, source=source, plugin="ar"),)

    decisions = run([Fake("h", (step, step))], ctx, Mode.INSTALL)

    assert [d.verdict for d in decisions] == [Verdict.PUBLISH]
    assert (target / "SKILL.md").read_text() == "v1\n"


def test_a_shared_and_an_exclusive_intent_for_one_directory(ctx, source, tmp_path):
    """Mixing the two ownership models on one path. The exclusive publish
    claims the whole directory; the shared publish then finds its own files
    already recorded and adds nothing new, rather than corrupting the marker."""
    target = tmp_path / "dest" / "mixed"
    def exclusive(h, c):
        return (Intent(target=target, source=source, plugin="ar"),)

    def shared(h, c):
        return (Intent(target=target, source=source, plugin="ar", kind=Kind.FILES),)

    run([Fake("h", (exclusive, shared))], ctx, Mode.INSTALL)

    marker = read_marker(target)
    assert marker is not None and marker.plugin == "ar"
    assert (target / "SKILL.md").is_file()


# ─── Paths that do not exist, or are the wrong kind of thing ────────────────


def test_deciding_about_a_path_whose_parent_is_missing(tmp_path, source):
    """`decide` must answer without creating anything: PREVIEW promises the
    walk is read-only, and a status pass over an uninstalled harness asks about
    directories several levels deep that do not exist."""
    deep = tmp_path / "nope" / "further" / "demo"

    decision = decide(deep, source, plugin="ar")

    assert decision.verdict is Verdict.PUBLISH
    assert not deep.parent.exists(), "deciding must not create directories"


def test_publishing_creates_missing_parents(tmp_path, source):
    target = tmp_path / "a" / "b" / "c" / "demo"

    publish_tree(source, target, plugin="ar")

    assert (target / "SKILL.md").is_file()


def test_a_target_that_is_a_file_rather_than_a_directory(tmp_path, source):
    """An unmarked file where a directory belongs is still the user's."""
    target = tmp_path / "dest" / "demo"
    target.parent.mkdir(parents=True)
    target.write_text("i am a file the user made\n", encoding="utf-8")

    decision = publish_tree(source, target, plugin="ar")

    assert decision.verdict is Verdict.KEEP
    assert target.read_text() == "i am a file the user made\n"
    assert withdrawn(target) is False, "a file is never removed as an owned tree"


def test_publish_files_into_a_symlinked_directory(tmp_path, source):
    """A `commands/` that is itself a symlink writes through to the target,
    which may be outside anything autorun owns. Pinning current behaviour: the
    write follows the link, and the marker lands beside the real files, so
    uninstall can still find them."""
    real = tmp_path / "real_commands"
    real.mkdir()
    linked = tmp_path / "dest" / "commands"
    linked.parent.mkdir(parents=True)
    linked.symlink_to(real)
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "go.md").write_text("go\n", encoding="utf-8")

    publish_files(staging, linked, plugin="ar")

    assert (real / "go.md").is_file(), "the write follows the link"
    assert (real / OWNED_MARKER_NAME).is_file(), "and so does the marker"
    assert withdraw_files(linked, plugin="ar") == ("go.md",)
    assert not (real / "go.md").exists()


# ─── The manifest describing something that changed shape ───────────────────


def test_a_recorded_file_that_became_a_directory(tmp_path, source):
    """Neither edited nor intact. Reporting it as missing is right: the file we
    wrote is gone, and refusing to touch the tree is safer than replacing a
    directory the user built there."""
    target = tmp_path / "dest" / "demo"
    publish_tree(source, target, plugin="ar")
    (target / "SKILL.md").unlink()
    (target / "SKILL.md").mkdir()

    edited, missing, extra = compare(target, read_marker(target))

    assert missing == ("SKILL.md",)
    assert edited == () and extra == ()


def test_a_recorded_file_that_became_a_symlink(tmp_path, source):
    """A file replaced by a link to elsewhere is an edit, not a coincidence."""
    target = tmp_path / "dest" / "demo"
    publish_tree(source, target, plugin="ar")
    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text("theirs\n", encoding="utf-8")
    (target / "SKILL.md").unlink()
    (target / "SKILL.md").symlink_to(elsewhere)

    assert "SKILL.md" in compare(target, read_marker(target))[0]


def test_a_file_the_user_added_inside_an_owned_tree_survives(tmp_path, source):
    """An unrecorded file is user data, not disposable install residue."""
    target = tmp_path / "dest" / "demo"
    publish_tree(source, target, plugin="ar")
    (target / "their-note.md").write_text("mine\n", encoding="utf-8")

    edited, missing, extra = compare(target, read_marker(target))

    assert edited == () and missing == () and extra == ("their-note.md",)
    assert publish_tree(source, target, plugin="ar").verdict is Verdict.KEEP
    assert (target / "their-note.md").read_text(encoding="utf-8") == "mine\n"
    assert withdrawn(target, plugin="ar") is False


def test_a_deleted_file_we_recorded_is_reported_missing(tmp_path, source):
    target = tmp_path / "dest" / "demo"
    publish_tree(source, target, plugin="ar")
    (target / "SKILL.md").unlink()

    assert compare(target, read_marker(target))[1] == ("SKILL.md",)
    assert publish_tree(source, target, plugin="ar").verdict is Verdict.KEEP
    assert not (target / "SKILL.md").exists(), "reinstall preserves the user's deletion"
    assert withdrawn(target, plugin="ar") is False


def test_an_empty_source_tree_publishes_an_empty_owned_directory(tmp_path):
    """A plugin that ships no files for this route still gets a claimed
    directory, so a later prune knows it is ours to remove."""
    empty = tmp_path / "empty_source"
    empty.mkdir()
    target = tmp_path / "dest" / "empty"

    publish_tree(empty, target, plugin="ar")

    assert target.is_dir()
    assert read_marker(target) is not None
    assert withdrawn(target, plugin="ar") is True


# ─── Shared-directory decisions ─────────────────────────────────────────────


def test_deciding_a_shared_directory_that_does_not_exist_yet(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "go.md").write_text("go\n", encoding="utf-8")

    decision = decide_files(staging, tmp_path / "dest" / "commands", plugin="ar")

    assert decision.verdict is Verdict.PUBLISH
    assert not (tmp_path / "dest").exists(), "deciding must not create directories"


def test_a_shared_publish_with_nothing_to_ship_claims_nothing(tmp_path):
    """An empty source must not claim the user's directory wholesale."""
    staging = tmp_path / "staging"
    staging.mkdir()
    shared = tmp_path / "dest" / "commands"
    shared.mkdir(parents=True)
    (shared / "theirs.md").write_text("theirs\n", encoding="utf-8")

    publish_files(staging, shared, plugin="ar")

    assert (shared / "theirs.md").read_text() == "theirs\n"
    assert read_marker(shared).files == {}, "we claimed no files"


@pytest.mark.parametrize("collides", [(), ("ar-go.md",), ("ar-go.md", "ar-commit.md"),
                                      ("ar-go.md", "ar-commit.md", "ar-stop.md")])
def test_every_shipped_file_lands_however_many_collide(tmp_path, collides):
    """One collision used to cancel the whole directory: a user with their own
    `ar-go.md` received NONE of autorun's commands, reported as a KEEP that
    installed nothing. That is the "one blocked name flips the whole plugin"
    defect reintroduced one layer up.

    The shipped set has to be complete or the package is broken — `/ar:go`
    simply absent — so a colliding file is moved aside rather than refused.
    """
    staging = tmp_path / "staging"
    staging.mkdir()
    shipped = ("ar-go.md", "ar-commit.md", "ar-stop.md")
    for name in shipped:
        (staging / name).write_text("ours\n", encoding="utf-8")
    shared = tmp_path / "dest" / "commands"
    shared.mkdir(parents=True)
    for name in collides:
        (shared / name).write_text("MINE\n", encoding="utf-8")

    decision = publish_files(staging, shared, plugin="ar")

    assert decision.verdict is Verdict.PUBLISH
    for name in shipped:
        assert (shared / name).read_text() == "ours\n", f"{name} must be installed"
    for name in collides:
        assert (shared / f"{name}.autorun-backup").read_text() == "MINE\n", \
            "the user's content is preserved, not discarded"


def test_backups_are_numbered_so_they_never_overwrite_each_other(tmp_path):
    """A user who keeps their own version across several installs would
    otherwise have the first backup silently replaced by the second, losing the
    original they cared about."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "ar-go.md").write_text("ours-v1\n", encoding="utf-8")
    shared = tmp_path / "dest" / "commands"
    shared.mkdir(parents=True)
    (shared / "ar-go.md").write_text("FIRST\n", encoding="utf-8")

    publish_files(staging, shared, plugin="ar")
    (shared / "ar-go.md").write_text("SECOND\n", encoding="utf-8")
    (staging / "ar-go.md").write_text("ours-v2\n", encoding="utf-8")
    publish_files(staging, shared, plugin="ar")

    assert (shared / "ar-go.md.autorun-backup").read_text() == "FIRST\n"
    assert (shared / "ar-go.md.autorun-backup.1").read_text() == "SECOND\n"


def test_our_own_unchanged_file_is_replaced_without_a_backup(tmp_path):
    """An upgrade must not litter a backup beside every file it updates. Only
    content that differs from what we recorded is worth preserving."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "ar-go.md").write_text("ours-v1\n", encoding="utf-8")
    shared = tmp_path / "dest" / "commands"
    publish_files(staging, shared, plugin="ar")

    (staging / "ar-go.md").write_text("ours-v2\n", encoding="utf-8")
    publish_files(staging, shared, plugin="ar")

    assert (shared / "ar-go.md").read_text() == "ours-v2\n"
    assert not list(shared.glob("*.autorun-backup*")), "no backup for our own file"


def test_a_backup_is_named_in_the_decision(tmp_path):
    """Silently moving a user's file would be worse than refusing. The caller
    has to be able to say what moved and where."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "ar-go.md").write_text("ours\n", encoding="utf-8")
    shared = tmp_path / "dest" / "commands"
    shared.mkdir(parents=True)
    (shared / "ar-go.md").write_text("MINE\n", encoding="utf-8")

    decision = publish_files(staging, shared, plugin="ar")

    assert "backed up 1" in decision.reason
    assert "ar-go.md.autorun-backup" in decision.edited


def test_refusing_instead_of_backing_up_is_still_available(tmp_path):
    """Right where a fallback exists — a skill blocked on the shared root still
    has its harness's native route — and wrong for commands, where nothing else
    can deliver the file."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "ar-go.md").write_text("ours\n", encoding="utf-8")
    shared = tmp_path / "dest" / "commands"
    shared.mkdir(parents=True)
    (shared / "ar-go.md").write_text("MINE\n", encoding="utf-8")

    decision = publish_files(staging, shared, plugin="ar", backup=False)

    assert decision.verdict is Verdict.KEEP
    assert (shared / "ar-go.md").read_text() == "MINE\n"
    assert not list(shared.glob("*.autorun-backup*"))


def test_withdrawing_a_shared_file_the_user_edited_leaves_it(tmp_path):
    """An edit protects against removal exactly as it protects against
    replacement."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "go.md").write_text("v1\n", encoding="utf-8")
    shared = tmp_path / "dest" / "commands"
    publish_files(staging, shared, plugin="ar")
    (shared / "go.md").write_text("MY EDIT\n", encoding="utf-8")

    assert withdraw_files(shared, plugin="ar") == ()
    assert (shared / "go.md").read_text() == "MY EDIT\n"


# ─── Modes ──────────────────────────────────────────────────────────────────


def test_preview_writes_nothing_even_where_a_directory_is_missing(ctx, source, tmp_path):
    target = tmp_path / "never" / "created" / "demo"
    def step(h, c):
        return (Intent(target=target, source=source, plugin="ar"),)

    run([Fake("h", (step,))], ctx, Mode.PREVIEW)

    assert not (tmp_path / "never").exists()


def test_uninstall_of_something_never_installed_is_a_no_op(ctx, source, tmp_path):
    target = tmp_path / "dest" / "demo"
    def step(h, c):
        return (Intent(target=target, source=source, plugin="ar"),)

    decisions = run([Fake("h", (step,))], ctx, Mode.UNINSTALL)

    assert [d.verdict for d in decisions] == [Verdict.SKIP]
    assert not target.exists()


def test_uninstall_uses_receipts_when_the_marketplace_source_is_gone(tmp_path, monkeypatch):
    import json
    from autorun.installer import codex
    from autorun.installer.orchestrate import uninstall
    from autorun.platforms import PLATFORMS

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    installed = tmp_path / ".codex" / "skills" / "demo"
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("installed\n", encoding="utf-8")
    publish_tree(source, installed, plugin="ar")
    hooks = tmp_path / ".codex" / "hooks.json"
    hooks.parent.mkdir(parents=True, exist_ok=True)
    hooks.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"hooks": [{"type": "command", "command": "echo user"}]}
    ]}}), encoding="utf-8")
    codex.merge_hooks(hooks, {"PreToolUse": ("uv run hook_entry.py --cli codex",)})

    result = uninstall(
        marketplace_root=tmp_path / "missing-marketplace",
        plugins=("ar",),
        harnesses=(PLATFORMS["codex"],),
        home=tmp_path,
        available=(),
        state_dir=tmp_path / "state",
    )

    assert not installed.exists()
    assert result.missing == (), "a receipt makes source-independent uninstall possible"
    text = hooks.read_text(encoding="utf-8")
    assert "echo user" in text and "hook_entry.py" not in text


def test_retirement_of_shared_files_keeps_the_users_directory(tmp_path, monkeypatch):
    from autorun.installer.traversal import retirements

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    source = tmp_path / "source"
    source.mkdir()
    (source / "ar-go.md").write_text("ours\n", encoding="utf-8")
    shared = tmp_path / "commands"
    publish_files(source, shared, plugin="ar")
    (shared / "mine.md").write_text("user\n", encoding="utf-8")

    intents = tuple(retirements((tmp_path,), (), plugins=("ar",)))
    run((), Context(marketplace_root=tmp_path, home=tmp_path), Mode.UNINSTALL, extra=intents)

    assert shared.is_dir()
    assert (shared / "mine.md").read_text(encoding="utf-8") == "user\n"
    assert not (shared / "ar-go.md").exists()


def test_uninstall_never_removes_a_companion_product_as_a_side_effect(tmp_path, monkeypatch):
    import subprocess
    from types import SimpleNamespace
    from autorun.installer.orchestrate import _registrations

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    harness = SimpleNamespace(name="gemini")
    plugins = {"ar": tmp_path / "ar", "pdf-extractor": tmp_path / "pdf"}

    def exercise(conductor):
        calls = []

        def invoke(argv):
            calls.append(tuple(argv))
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        context = Context(
            marketplace_root=tmp_path,
            home=tmp_path,
            settings={"conductor": conductor},
        )
        _registrations(
            (harness,), plugins, tmp_path, "autorun", context,
            removing=True, run=invoke, available=("gemini",),
        )
        return calls

    assert all("conductor" not in call for call in exercise(False))
    assert all("conductor" not in call for call in exercise(True))


def test_failed_claude_registration_fills_its_versioned_cache(tmp_path, monkeypatch):
    import subprocess
    from autorun.installer.orchestrate import _registrations
    from autorun.platforms import PLATFORMS

    ctx = _home_context(tmp_path, monkeypatch, tmp_path)
    plugin = tmp_path / "plugins" / "ar"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": "1.2.3"}), encoding="utf-8"
    )
    (plugin / "hooks").mkdir()
    (plugin / "hooks" / "hook_entry.py").write_text("current hook\n", encoding="utf-8")

    def offline(argv):
        return subprocess.CompletedProcess(argv, 1, "", "network unavailable")

    outcomes = _registrations(
        (PLATFORMS["claude"],), {"ar": plugin}, tmp_path, "autorun", ctx,
        removing=False, run=offline, available=("claude",),
    )

    cached = ctx.home / ".claude" / "plugins" / "cache" / "autorun" / "ar" / "1.2.3"
    assert (cached / "hooks" / "hook_entry.py").read_text(encoding="utf-8") == "current hook\n"
    assert outcomes and all(outcome.ok for outcome in outcomes)
    assert outcomes[-1].step == "claude: cache fallback"


def test_successful_claude_registration_expands_the_cached_hook_root(tmp_path, monkeypatch):
    import shutil
    import subprocess
    from autorun.installer.orchestrate import _registrations
    from autorun.platforms import PLATFORMS

    ctx = _home_context(tmp_path, monkeypatch, tmp_path)
    plugin = tmp_path / "plugins" / "ar"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": "1.2.3"}), encoding="utf-8"
    )
    (plugin / "hooks").mkdir()
    (plugin / "hooks" / "hooks.json").write_text(
        '${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py\n', encoding="utf-8"
    )
    cached = ctx.home / ".claude" / "plugins" / "cache" / "autorun" / "ar" / "1.2.3"

    def native_update(argv):
        if tuple(argv[:3]) == ("claude", "plugin", "update"):
            cached.parent.mkdir(parents=True)
            shutil.copytree(plugin, cached)
        return subprocess.CompletedProcess(argv, 0, "", "")

    outcomes = _registrations(
        (PLATFORMS["claude"],), {"ar": plugin}, tmp_path, "autorun", ctx,
        removing=False, run=native_update, available=("claude",),
    )

    hooks = (cached / "hooks" / "hooks.json").read_text(encoding="utf-8")
    assert str(cached) in hooks and "${CLAUDE_PLUGIN_ROOT}" not in hooks
    assert outcomes and all(outcome.ok for outcome in outcomes)


def test_failed_registration_never_replaces_an_existing_claude_runtime(
    tmp_path, monkeypatch
):
    import subprocess
    from autorun.installer.orchestrate import _registrations
    from autorun.platforms import PLATFORMS

    ctx = _home_context(tmp_path, monkeypatch, tmp_path)
    plugin = tmp_path / "plugins" / "ar"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": "1.2.3"}), encoding="utf-8"
    )
    cached = ctx.home / ".claude" / "plugins" / "cache" / "autorun" / "ar" / "1.2.3"
    runtime = cached / ".venv" / "lib" / "site-packages" / "autorun"
    runtime.mkdir(parents=True)
    (runtime / "__init__.py").write_text("installed = True\n", encoding="utf-8")

    outcomes = _registrations(
        (PLATFORMS["claude"],), {"ar": plugin}, tmp_path, "autorun", ctx,
        removing=False,
        run=lambda argv: subprocess.CompletedProcess(argv, 1, "", "network unavailable"),
        available=("claude",),
    )

    assert (runtime / "__init__.py").read_text(encoding="utf-8") == "installed = True\n"
    assert outcomes and not all(outcome.ok for outcome in outcomes)


def test_conductor_registration_is_noninteractive_and_idempotent():
    import subprocess
    from autorun.installer.registration import companions

    calls = []

    def already(argv):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 1, "", "extension already installed")

    outcomes = companions(
        "gemini", ("conductor",), {}, run=already, available=("gemini",)
    )["conductor"]

    assert outcomes[0].ok
    assert calls == [(
        "gemini", "extensions", "install",
        "https://github.com/gemini-cli-extensions/conductor",
        "--auto-update", "--consent",
    )]


def test_an_intent_with_no_source_is_a_removal_in_every_mode(ctx, tmp_path, source):
    """`source is None` already means "no longer shipped", so a step can retire
    something without the walk being in UNINSTALL mode — that is how prune
    works."""
    target = tmp_path / "dest" / "demo"
    publish_tree(source, target, plugin="ar")
    def step(h, c):
        return (Intent(target=target, source=None, plugin="ar"),)

    decisions = run([Fake("h", (step,))], ctx, Mode.INSTALL)

    assert [d.verdict for d in decisions] == [Verdict.RETIRE]
    assert not target.exists()


# ─── Upgrading from an install a previous version made ─────────────────────


def test_a_tree_a_retired_route_left_behind_is_swept(tmp_path, source, ctx):
    """A step yields intents for where its capability writes today, so anything
    an older release wrote elsewhere is never visited and never removed — while
    still carrying our marker. Retiring Qwen's native skill route left 17 marked
    directories behind, in a location the current registry cannot even name."""
    from autorun.installer.traversal import retirements

    stale = tmp_path / "harness" / "extensions" / "old-route" / "commit"
    current = tmp_path / "harness" / "skills" / "commit"
    publish_tree(source, stale, plugin="ar")
    publish_tree(source, current, plugin="ar")

    found = list(retirements([tmp_path / "harness"], [current], plugins=["ar"]))

    assert [i.target for i in found] == [stale]
    assert all(i.source is None for i in found), "a retirement has no source"


def test_the_sweep_never_touches_a_tree_the_user_wrote(tmp_path, source, ctx):
    from autorun.installer.traversal import retirements

    theirs = tmp_path / "harness" / "skills" / "mine"
    theirs.mkdir(parents=True)
    (theirs / "SKILL.md").write_text("hand written\n", encoding="utf-8")

    assert list(retirements([tmp_path / "harness"], [], plugins=["ar"])) == []


def test_the_sweep_never_claims_another_plugins_tree(tmp_path, source):
    from autorun.installer.traversal import retirements

    theirs = tmp_path / "harness" / "skills" / "pdf"
    publish_tree(source, theirs, plugin="pdf-extractor")

    assert list(retirements([tmp_path / "harness"], [], plugins=["ar"])) == []


def test_the_sweep_ignores_markers_copied_into_a_harness_plugin_cache(
    tmp_path, source
):
    """Codex copies the owned source tree, including its marker, into cache."""
    from autorun.installer.traversal import retirements

    root = tmp_path / ".codex"
    cached = root / "plugins" / "cache" / "personal" / "ar" / "1.0.0"
    publish_tree(source, cached, plugin="ar")

    assert list(retirements([root], [], plugins=["ar"])) == []


def test_an_edited_tree_is_proposed_by_the_sweep_but_kept_by_the_decision(
    tmp_path, source
):
    """The sweep only *proposes*. `decide(target, None)` still returns KEEP for
    a tree the user edited, so the upgrade needs no new policy — and none may be
    added, or an upgrade would quietly delete someone's changes."""
    from autorun.installer.traversal import retirements

    stale = tmp_path / "harness" / "old-route" / "commit"
    publish_tree(source, stale, plugin="ar")
    (stale / "SKILL.md").write_text("MY OWN EDIT\n", encoding="utf-8")

    proposed = list(retirements([tmp_path / "harness"], [], plugins=["ar"]))
    assert [i.target for i in proposed] == [stale]

    assert decide(stale, None, plugin="ar").verdict is Verdict.KEEP
    assert withdrawn(stale, plugin="ar") is True or (stale / "SKILL.md").is_file()


def test_the_sweep_does_not_descend_into_a_tree_it_already_owns(tmp_path, source):
    """An owned tree is removed whole, so reporting nested markers separately
    would name the same artifact more than once."""
    from autorun.installer.traversal import retirements

    outer = tmp_path / "harness" / "extensions" / "ar"
    publish_tree(source, outer, plugin="ar")
    inner = outer / "skills" / "commit"
    publish_tree(source, inner, plugin="ar")

    found = [i.target for i in retirements([tmp_path / "harness"], [], plugins=["ar"])]

    assert found == [outer], found


def test_stale_content_inside_a_still_published_tree_is_cleared_by_the_republish(
    tmp_path, source
):
    """The other half of the upgrade story. The sweep skips a claimed tree
    because the republish already owns it: publish_tree swaps the whole
    directory, so a route that used to place files inside loses them."""
    old_staging = tmp_path / "staging-old"
    (old_staging / "commands").mkdir(parents=True)
    (old_staging / "commands" / "go.md").write_text("go\n", encoding="utf-8")
    (old_staging / "skills" / "commit").mkdir(parents=True)
    (old_staging / "skills" / "commit" / "SKILL.md").write_text("# commit\n", encoding="utf-8")
    target = tmp_path / "dest" / "ar"
    publish_tree(old_staging, target, plugin="ar")
    assert (target / "skills" / "commit").is_dir()

    new_staging = tmp_path / "staging-new"
    (new_staging / "commands").mkdir(parents=True)
    (new_staging / "commands" / "go.md").write_text("go\n", encoding="utf-8")

    publish_tree(new_staging, target, plugin="ar")

    assert not (target / "skills").exists(), "the retired inner route is gone"
    assert (target / "commands" / "go.md").is_file(), "the live content survives"


# ─── One plugin identity ────────────────────────────────────────────────────


def test_a_tree_marked_with_a_legacy_plugin_name_is_still_ours(tmp_path, source):
    """The Codex path recorded the directory name `autorun` while every other
    path recorded the registered name `ar`. Ownership is scoped by that name,
    so the Codex tree was unremovable under `ar` — the whole of the 362-file
    leak the sandbox baseline measured."""
    from autorun.installer.fs import owns

    target = tmp_path / "dest" / "codex-package"
    publish_tree(source, target, plugin="autorun")

    marker = read_marker(target)
    assert marker.plugin == "autorun"
    assert owns(marker, "ar") is True
    assert decide(target, None, plugin="ar").verdict is Verdict.RETIRE
    assert withdrawn(target, plugin="ar") is True


def test_an_alias_never_lets_one_plugin_claim_another(tmp_path, source):
    """The alias table must widen ownership for one product, not collapse the
    scoping that stops autorun deleting pdf-extractor's trees."""
    from autorun.installer.fs import owns

    target = tmp_path / "dest" / "theirs"
    publish_tree(source, target, plugin="pdf-extractor")

    assert owns(read_marker(target), "ar") is False
    assert decide(target, None, plugin="ar").verdict is Verdict.KEEP
    assert withdrawn(target, plugin="ar") is False


def test_a_marker_with_no_recorded_plugin_belongs_to_whoever_asks(tmp_path, source):
    """Markers predating per-plugin scoping must be adoptable, or an upgrade
    strands every tree an older autorun claimed."""
    from autorun.installer.fs import owns

    target = tmp_path / "dest" / "legacy"
    publish_tree(source, target, plugin="")

    assert owns(read_marker(target), "ar") is True


def test_an_unmarked_tree_is_never_ours_whatever_the_name(tmp_path, monkeypatch):
    from autorun.installer.fs import owns

    assert owns(None, "ar") is False


# ─── One answer for a harness's directories ─────────────────────────────────


def test_a_moved_harness_config_dir_moves_the_native_skill_route(monkeypatch, tmp_path):
    """Expanding `Platform.config_dir` by hand ignores CLAUDE_CONFIG_DIR,
    CODEX_HOME, QWEN_HOME and XDG_CONFIG_HOME, so a user who moved their
    harness config would get skills written to the default location while
    every other part of the install honoured the override."""
    from autorun.installer import skills
    from autorun.platforms import PLATFORMS

    # $HOME is the seam; Context.home must agree with it rather than replace it.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    ctx = _home_context(tmp_path, monkeypatch, tmp_path)

    monkeypatch.delenv("QWEN_HOME", raising=False)
    assert skills._native_roots(PLATFORMS["qwen"], ctx) == (
        tmp_path / "home" / ".qwen" / "extensions",
    )

    monkeypatch.setenv("QWEN_HOME", str(tmp_path / "moved"))
    assert skills._native_roots(PLATFORMS["qwen"], ctx) == (
        tmp_path / "moved" / "extensions",
    )


def test_an_env_var_naming_the_parent_gets_the_harness_subdirectory(tmp_path):
    """XDG_CONFIG_HOME is ~/.config, not ~/.config/opencode. Treating it as the
    final answer writes one level too high, where the harness never looks."""
    from autorun.installer.discovery import config_dir
    from autorun.platforms import PLATFORMS

    opencode = PLATFORMS["opencode"]
    home = tmp_path / "home"

    assert config_dir(opencode, env={}, home=home) == home / ".config" / "opencode"
    assert config_dir(
        opencode, env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")}, home=home
    ) == tmp_path / "xdg" / "opencode"


def test_every_skill_destination_honours_a_redirected_home(monkeypatch, tmp_path):
    """$HOME is the isolation seam: install tests redirect it and so does the
    sandboxed-install recipe. Any route resolving outside it would let an
    isolated run read or write the developer's real configuration.

    Asserted over the whole registry rather than one route, because the failure
    is per-route and a new harness could reintroduce it. This is also why there
    is no separate `home` argument threaded through `SkillRoute.destinations`:
    a second mechanism for one question is silently wrong wherever it is
    omitted, and $HOME already covers every route including the Codex plugin
    package, whose path comes from a resolver callable rather than a config dir.
    """
    from autorun.installer.discovery import skill_destinations
    from autorun.platforms import PLATFORMS

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    # Explicit harness relocation variables are supported overrides, not part
    # of this HOME-only isolation assertion. CI commonly exports them globally.
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("PI_CODING_AGENT_DIR", raising=False)

    escaped = [
        (platform.name, str(destination))
        for platform in PLATFORMS.values()
        for reading in (False, True)
        for destination in skill_destinations(platform, reading=reading)
        if not str(destination).startswith(str(tmp_path))
    ]

    assert escaped == [], escaped


def test_a_harness_with_several_skill_search_routes_reports_all_of_them():
    """OpenCode reads two roots. A capability that returns one path silently
    drops the second, and a skill installed there is invisible."""
    from autorun.installer.discovery import config_dir
    from autorun.platforms import PLATFORMS

    opencode = PLATFORMS["opencode"]
    base = config_dir(opencode, env={})
    found = [d for route in opencode.skill_search_routes for d in route.destinations(base)]

    assert len(found) >= 2, found


def test_an_extensions_subdir_is_relative_to_the_resolved_config_dir(tmp_path, monkeypatch):
    """Gemini, Qwen and Antigravity keep extensions under their config dir, so
    moving that dir must move the extensions with it."""
    from autorun.installer.extension import extension_dir
    from autorun.platforms import PLATFORMS

    ctx = _home_context(tmp_path, monkeypatch, tmp_path)

    assert extension_dir(ctx, PLATFORMS["qwen"], "ar") == (
        tmp_path / "home" / ".qwen" / "extensions" / "ar"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="Windows does not expose POSIX executable bits")
def test_the_executable_bit_survives_a_republish(tmp_path):
    """hook_entry.py stops being runnable if this is lost, and nothing reports
    it until a hook fails."""
    source = tmp_path / "src"
    source.mkdir()
    script = source / "hook_entry.py"
    script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    target = tmp_path / "dest" / "hooks"

    publish_tree(source, target, plugin="ar")
    (source / "hook_entry.py").write_text("#!/usr/bin/env python3\n# v2\n", encoding="utf-8")
    (source / "hook_entry.py").chmod(script.stat().st_mode | stat.S_IXUSR)
    publish_tree(source, target, plugin="ar")

    assert os.stat(target / "hook_entry.py").st_mode & stat.S_IXUSR
