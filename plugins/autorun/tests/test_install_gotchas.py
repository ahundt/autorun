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
import shutil
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


def test_a_symlinked_shipped_file_publishes_once_and_then_says_so(tmp_path):
    """The shared-file route copies content, so it must compare content.

    `publish_files` writes through a symlinked source — `copy2` follows one —
    and records the regular file it landed. `decide_files` fingerprinted the
    *source* instead, and `_fingerprint` records a symlink by its target, so
    the two could never agree: every install reported PUBLISH and rewrote a
    directory nothing had changed. Whole-tree publication keeps links
    (`copytree(symlinks=True)`) and `scan_tree` compares them as links; the
    routes differ because what they write differs, and each has to be compared
    the way it writes.
    """
    source, destination = tmp_path / "src", tmp_path / "dest"
    source.mkdir()
    destination.mkdir()
    (tmp_path / "real.md").write_text("shipped\n", encoding="utf-8")
    try:
        (source / "ar-go.md").symlink_to(tmp_path / "real.md")
    except (OSError, NotImplementedError) as unsupported:
        pytest.skip(f"this platform cannot create symlinks: {unsupported}")

    assert publish_files(source, destination, plugin="ar").verdict is Verdict.PUBLISH
    assert (destination / "ar-go.md").read_text(encoding="utf-8") == "shipped\n"
    assert not (destination / "ar-go.md").is_symlink(), "a link escaped into the user's dir"

    # `decide_files` is the decider — `publish_files` is the writer and has no
    # SKIP of its own — so this is the answer `_perform` acts on, and PUBLISH
    # here is what made every install rewrite the directory.
    assert decide_files(source, destination, plugin="ar").verdict is Verdict.SKIP


def test_a_file_deleted_between_the_check_and_the_hash_does_not_fail_the_install(
    tmp_path, monkeypatch
):
    """The user's own directory keeps changing while we publish in it.

    `commands/` belongs to the user as much as to us, and the install lock only
    excludes other autorun processes. Both `decide_files` and `publish_files`
    asked `exists()` and then hashed, so a deletion landing in that window
    raised `FileNotFoundError` out of the installer. The transaction rolls back
    safely, but a file the user deleted is the least surprising thing that can
    happen to a file, and the answer for it already exists: `scan_tree` treats
    an entry that vanishes mid-walk as absent.
    """
    import autorun.installer.fs as fs

    source, destination = tmp_path / "src", tmp_path / "dest"
    source.mkdir()
    destination.mkdir()
    (source / "ar-go.md").write_text("shipped\n", encoding="utf-8")
    publish_files(source, destination, plugin="ar")  # now recorded as ours

    real_fingerprint = fs._fingerprint
    vanished = []

    def deleting(path):
        if path == destination / "ar-go.md" and not vanished:
            vanished.append(path)
            path.unlink()
        return real_fingerprint(path)

    monkeypatch.setattr(fs, "_fingerprint", deleting)
    (source / "ar-go.md").write_text("shipped v2\n", encoding="utf-8")

    decision = publish_files(source, destination, plugin="ar")

    assert vanished, "the race never happened; the test proves nothing"
    assert decision.verdict is Verdict.PUBLISH, decision
    assert (destination / "ar-go.md").read_text(encoding="utf-8") == "shipped v2\n"


@pytest.mark.parametrize("shape", ["file", "link_to_file", "broken_link"])
def test_a_shared_directory_route_occupied_by_a_file_is_never_replaced(
    tmp_path, shape
):
    """`commands/` is a *directory* contract, and the user has a file there.

    Publishing would have to replace it, and there is nothing to replace it
    with that keeps their bytes: `_swapped` moves the target into a staging
    directory that is deleted on the way out, so the file left no copy anywhere
    — it was simply gone, while the install reported PUBLISH.

    The symlink case is why moving it aside is not the answer either.
    `publish_files` resolves a linked target, so `~/.codex/commands ->
    ~/notes.txt` puts both the replacement and any backup in a directory
    autorun has no business writing to. A non-directory here is the user's, it
    is reported, and nothing is written.
    """
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "ar-go.md").write_text("shipped\n", encoding="utf-8")
    shared = tmp_path / "dest" / "commands"
    shared.parent.mkdir(parents=True)
    if shape == "file":
        shared.write_text("USER CONTENT\n", encoding="utf-8")
    else:
        elsewhere = tmp_path / "elsewhere.txt"
        if shape == "link_to_file":
            elsewhere.write_text("USER CONTENT\n", encoding="utf-8")
        shared.symlink_to(elsewhere)

    for decision in (
        decide_files(staging, shared, plugin="ar"),
        publish_files(staging, shared, plugin="ar"),
    ):
        assert decision.verdict is Verdict.KEEP, decision
        assert "directory" in decision.reason, decision

    assert not shared.is_dir(), "the route stayed the user's"
    assert not (shared / "ar-go.md").exists()
    survivors = [
        p for p in tmp_path.rglob("*")
        if p.is_file() and not p.is_symlink()
        and "USER CONTENT" in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert bool(survivors) is (shape != "broken_link"), survivors


def test_a_directory_that_arrives_while_the_link_lock_is_taken_survives(
    tmp_path, monkeypatch
):
    """`publish_link` decided before the lock and never looked again.

    `publish_tree` closes this window with `published(..., authorize=judge)`,
    which re-decides under the publication lock immediately before the swap.
    Its link twin checked once, up front, and then swapped whatever had arrived
    in the meantime — so a directory the user created between the two moments
    was replaced with our symlink and its contents were gone.

    The window is opened deterministically here rather than raced for: the
    stand-in creates the user's directory at the instant the real `_swapped`
    is entered, which is exactly the interleaving the check must survive.
    """
    from autorun.installer import fs

    source = tmp_path / "shared" / "commit"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("ours\n", encoding="utf-8")
    target = tmp_path / "dest" / "commit"
    target.parent.mkdir(parents=True)
    real_swapped = fs._swapped

    def racing(path):
        if not path.exists():
            path.mkdir(parents=True)
            (path / "SKILL.md").write_text("THEIRS\n", encoding="utf-8")
        return real_swapped(path)

    monkeypatch.setattr(fs, "_swapped", racing)

    decision = fs.publish_link(source, target, plugin="ar")

    assert decision.verdict is Verdict.KEEP, decision
    assert not target.is_symlink(), "the user's directory was replaced by our link"
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "THEIRS\n"


def test_a_swap_a_kill_interrupted_puts_the_users_tree_back(tmp_path):
    """SIGKILL between the two renames leaves the tree only in the stage.

    `_swapped` moves the target aside to `previous`, then renames the stage over
    it. A process killed between those two renames cannot run either the
    context manager's cleanup or its rollback, so the target is *absent* and the
    user's bytes exist only inside `.autorun-publish-<name>-XXXX/previous`.

    That is worse than clutter. The next install finds no target, `decide`
    answers "new", and the tree is republished over the top — the user's edits
    were never seen, never reported, and are then deleted with the stale stage.
    Resuming the interrupted rollback is what the dead process would have done.
    """
    source = tmp_path / "src" / "demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("shipped\n", encoding="utf-8")

    target = tmp_path / "dest" / "demo"
    target.parent.mkdir(parents=True)
    abandoned = target.parent / ".autorun-publish-demo-k1LL3d"
    (abandoned / "previous").mkdir(parents=True)
    (abandoned / "previous" / "SKILL.md").write_text("THEIRS\n", encoding="utf-8")
    (abandoned / "next").mkdir()
    (abandoned / "next" / "SKILL.md").write_text("shipped\n", encoding="utf-8")

    decision = publish_tree(source, target, plugin="ar")

    assert (target / "SKILL.md").read_text(encoding="utf-8") == "THEIRS\n", decision
    assert decision.verdict is Verdict.KEEP, decision
    assert not list(target.parent.glob(".autorun-publish-*")), "stage left behind"


def test_a_recreated_target_does_not_cost_the_user_the_tree_in_the_stage(
    tmp_path, monkeypatch
):
    """The kill above, then anything puts a directory back at the target.

    `_resume_interrupted` restores `previous` only when the target is absent,
    which is the state the kill leaves. But nothing holds that state: the user
    can recreate the directory, restore it from a backup, or let another tool
    write it, and the next install then finds `previous` unrestorable and
    deleted the whole stage with it. That tree is the *only* remaining copy of
    what the user had — the swap moved it out of the target and the replacement
    never landed — so sweeping it is the same data loss the restore exists to
    prevent, reached by a state the restore did not anticipate.

    Parking it under `~/.autorun/installer/backups/` is where `--force` already
    puts a tree it may not simply discard. It is outside every harness's config
    root, so nothing lists it as a skill or sweeps it as an owned tree, and the
    stage still leaves the scanned directory.
    """
    monkeypatch.setenv("AUTORUN_HOME", str(tmp_path / "ar-home"))

    source = tmp_path / "src" / "demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("shipped\n", encoding="utf-8")
    target = tmp_path / "dest" / "demo"
    assert publish_tree(source, target, plugin="ar").verdict is Verdict.PUBLISH

    # The kill: the swap moved their tree to `previous` and never landed ours.
    abandoned = target.parent / ".autorun-publish-demo-k1LL3d"
    (abandoned / "previous").mkdir(parents=True)
    (abandoned / "previous" / "SKILL.md").write_text("THEIRS\n", encoding="utf-8")

    # ...and the target is a directory again by the time the next install runs.
    # An upgrade publishes over it, which is when the abandoned stage is swept.
    (source / "SKILL.md").write_text("shipped v2\n", encoding="utf-8")

    assert publish_tree(source, target, plugin="ar").verdict is Verdict.PUBLISH

    parked = sorted((tmp_path / "ar-home" / "installer" / "backups").glob("demo-*"))
    assert parked, "the only copy of the user's old tree was deleted with the stage"
    assert (parked[0] / "SKILL.md").read_text(encoding="utf-8") == "THEIRS\n"
    assert not list(target.parent.glob(".autorun-publish-*")), "stage left behind"


@pytest.mark.parametrize("kind", ["file", "link"])
def test_a_recreated_target_does_not_cost_the_user_a_previous_of_any_kind(
    tmp_path, monkeypatch, kind
):
    """`_swapped` is not a tree-only transaction, and neither is the loss.

    `preserved_paths` snapshots and restores whatever the native harness CLIs
    touch, and two of those are single files: an extension's
    `.<cli>-extension-install.json` receipt, and Antigravity's
    `import_manifest.json`, which lists every plugin the user has imported —
    not only ours. Both reach `_swapped`, so both can be left in an abandoned
    stage as a regular-file `previous`. A skill bridge leaves a symlink there.

    The recovery branch parked `previous` only when it was a directory, because
    `_park_backup` copied with `copytree`. Everything else fell through to
    `shutil.rmtree(stage, ignore_errors=True)` and was deleted — the same loss
    the branch was added to prevent, for the two shapes it did not cover. The
    docstring already promised otherwise.
    """
    monkeypatch.setenv("AUTORUN_HOME", str(tmp_path / "ar-home"))
    from autorun.installer import fs

    target = tmp_path / "dest" / "manifest.json"
    target.parent.mkdir(parents=True)
    abandoned = target.parent / ".autorun-publish-manifest.json-k1LL3d"
    abandoned.mkdir()
    previous = abandoned / "previous"
    if kind == "file":
        previous.write_text('{"plugins": ["THEIRS"]}\n', encoding="utf-8")
    else:
        previous.symlink_to(tmp_path / "somewhere-else")

    # ...and the target exists again by the time the next publication runs, so
    # the restore cannot put `previous` back where it came from.
    target.write_text('{"plugins": ["recreated"]}\n', encoding="utf-8")

    with fs._swapped(target) as staged:
        staged.write_text('{"plugins": ["ours"]}\n', encoding="utf-8")

    parked = sorted((tmp_path / "ar-home" / "installer" / "backups").glob("manifest.json-*"))
    assert parked, "the only copy of the user's previous state was deleted with the stage"
    if kind == "file":
        assert parked[0].read_text(encoding="utf-8") == '{"plugins": ["THEIRS"]}\n'
    else:
        assert parked[0].is_symlink()
        # Windows resolves a link target through `\\?\`, which names the same
        # path; `fs` already owns stripping it.
        assert fs._strip_extended_prefix(Path(os.readlink(parked[0]))) == (
            fs._strip_extended_prefix(tmp_path / "somewhere-else")
        )
    assert not list(target.parent.glob(".autorun-publish-*")), "stage left behind"


def test_a_replacement_written_while_we_staged_is_kept_not_swapped_away(
    tmp_path, monkeypatch
):
    """The ownership check runs before the copy, and the copy is not instant.

    `published(authorize=...)` asks "may we replace what is at the target?"
    under the lock and then yields, and what the caller does next is
    `shutil.copytree` of the whole source tree — seconds for a real skills
    bundle. Only autorun processes honour that lock. A user who writes their
    own tree at the target during those seconds had it renamed into the stage
    on exit and deleted with the stage, having never been asked about.

    Checking again before the rename would only narrow the window. Renaming
    first closes it: once the target is `previous`, no later write can change
    the bytes being decided about, so the identity captured on entry is
    compared to what actually came out, and a mismatch is parked rather than
    dropped. The publication still completes — the shipped set has to be
    complete — and the user's copy is recoverable.
    """
    monkeypatch.setenv("AUTORUN_HOME", str(tmp_path / "ar-home"))
    from autorun.installer import fs

    target = tmp_path / "dest" / "demo"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("ours v1\n", encoding="utf-8")

    with fs._swapped(target) as staged:
        # The window: our copy is still running, and the user replaces the
        # directory out from under it.
        shutil.rmtree(target)
        target.mkdir()
        (target / "SKILL.md").write_text("THEIRS\n", encoding="utf-8")
        staged.mkdir()
        (staged / "SKILL.md").write_text("ours v2\n", encoding="utf-8")

    assert (target / "SKILL.md").read_text(encoding="utf-8") == "ours v2\n"
    parked = sorted((tmp_path / "ar-home" / "installer" / "backups").glob("demo-*"))
    assert parked, "the tree the user wrote during the copy was deleted with the stage"
    assert (parked[0] / "SKILL.md").read_text(encoding="utf-8") == "THEIRS\n"


def test_an_unchanged_target_is_not_parked_on_every_publication(tmp_path, monkeypatch):
    """The ordinary upgrade must leave no backup behind.

    An identity check that fired on our own reads would put a copy of every
    tree into `~/.autorun/installer/backups/` on every install, which is both
    noise and disk.
    """
    monkeypatch.setenv("AUTORUN_HOME", str(tmp_path / "ar-home"))
    from autorun.installer import fs

    target = tmp_path / "dest" / "demo"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("ours v1\n", encoding="utf-8")

    with fs._swapped(target) as staged:
        staged.mkdir()
        (staged / "SKILL.md").write_text("ours v2\n", encoding="utf-8")

    assert (target / "SKILL.md").read_text(encoding="utf-8") == "ours v2\n"
    assert not (tmp_path / "ar-home" / "installer" / "backups").exists(), (
        "an ordinary replacement parked a backup"
    )


def test_a_link_replaced_after_the_final_ownership_check_survives(tmp_path, monkeypatch):
    """`ours()` then `unlink()` are two operations, and the user is not locked.

    The recheck under the parent's publication lock proved the link was ours
    at the moment it was read. The removal is a separate syscall against the
    *path*, so a user link written between the two was removed on the strength
    of what the previous link pointed at.

    The lock cannot help: it coordinates autorun processes, and the writer here
    is the user's editor or another tool. Renaming the link aside first is what
    makes the decision exact — the object being judged is no longer reachable
    by the path anyone else is writing to.

    The stand-in writes the user's link at the instant the removal is issued,
    which is the one moment the recheck cannot cover. Before the fix that
    removal names the target, so the link written there is what disappears.
    After it, the removal names the copy already renamed out of the way, and
    the path the user wrote to is not the path being deleted.
    """
    from autorun.installer import fs

    shared = tmp_path / "shared" / "commit"
    shared.mkdir(parents=True)
    (shared / "SKILL.md").write_text("ours\n", encoding="utf-8")
    theirs = tmp_path / "their-own" / "commit"
    theirs.mkdir(parents=True)
    (theirs / "SKILL.md").write_text("THEIRS\n", encoding="utf-8")

    target = tmp_path / "dest" / "commit"
    target.parent.mkdir(parents=True)
    target.symlink_to(shared, target_is_directory=True)

    real_unlink = Path.unlink
    raced = []

    def unlink_after_the_user_writes(self, *args, **kwargs):
        if not raced:
            raced.append(self)
            if target.is_symlink() or target.exists():
                real_unlink(target)
            target.symlink_to(theirs, target_is_directory=True)
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink_after_the_user_writes)

    fs.withdraw_link(target, tmp_path / "shared")

    assert raced, "the stand-in never fired; the test proved nothing"
    assert target.is_symlink(), "the user's link was removed"
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "THEIRS\n"


def test_a_stage_left_by_a_kill_before_the_swap_is_swept(tmp_path):
    """The common abandoned stage: killed during the copy, target untouched.

    Nothing was moved aside yet, so there is no user data to recover and the
    publication proceeds normally. The leftover directory still has to go: it
    accumulates one per interrupted run beside the user's own directories, and
    the tree inside it carries our marker, so a later sweep reports it as an
    owned tree at a path no route names.
    """
    source = tmp_path / "src" / "demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("shipped\n", encoding="utf-8")

    target = tmp_path / "dest" / "demo"
    target.parent.mkdir(parents=True)
    abandoned = target.parent / ".autorun-publish-demo-h4LfW4y"
    (abandoned / "next").mkdir(parents=True)
    (abandoned / "next" / "SKILL.md").write_text("partial", encoding="utf-8")

    assert publish_tree(source, target, plugin="ar").verdict is Verdict.PUBLISH
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "shipped\n"
    assert not list(target.parent.glob(".autorun-publish-*")), "stage left behind"


def test_another_targets_abandoned_stage_is_left_for_its_own_publication(tmp_path):
    """Sweeping is scoped to the target being published, not to the directory.

    A skills root holds every skill, so one `previous` there may belong to a
    different target entirely. Restoring it to *this* target would move one
    user's tree on top of another's name. The stage prefix carries the target
    name for exactly this reason, and only a matching one is resumed.
    """
    source = tmp_path / "src" / "demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("shipped\n", encoding="utf-8")

    target = tmp_path / "dest" / "demo"
    target.parent.mkdir(parents=True)
    foreign = target.parent / ".autorun-publish-commit-0th3rs"
    (foreign / "previous").mkdir(parents=True)
    (foreign / "previous" / "SKILL.md").write_text("SOMEONE ELSE\n", encoding="utf-8")

    assert publish_tree(source, target, plugin="ar").verdict is Verdict.PUBLISH
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "shipped\n"
    assert (foreign / "previous" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "SOMEONE ELSE\n", "another target's interrupted swap was consumed"


def test_a_rename_that_fails_puts_the_previous_tree_back(tmp_path, monkeypatch):
    """The last step of a swap can fail, and the target must not vanish.

    `_swapped` moves the target aside and then renames the stage over it. If
    that second rename fails — a full disk, a permission change, an antivirus
    hold on Windows — the target has already been moved and there is nothing at
    its path. The rollback that restores the backup is the only thing standing
    between that and the user's tree being gone, and every publication in the
    installer runs through it.

    The demo covers a caller raising *before* the swap. This covers the swap
    itself failing, which is the branch with the user's bytes already in
    motion.
    """
    from autorun.installer import fs

    source = tmp_path / "src" / "demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("shipped\n", encoding="utf-8")

    target = tmp_path / "dest" / "demo"
    publish_tree(source, target, plugin="ar")
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "shipped\n"

    real_replace = os.replace

    def failing_replace(src, dst, *args, **kwargs):
        # Fail exactly `os.replace(staged, target)` and nothing else. Counting
        # calls instead matched the marker's own atomic_write, which raised
        # inside the `with` body — the exception then travelled back through
        # the yield and the swap never ran at all, so the test passed while
        # proving nothing about the rollback.
        if Path(src).name == "next" and Path(dst) == target:
            raise OSError("no space left on device")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(fs.os, "replace", failing_replace)
    (source / "SKILL.md").write_text("version two\n", encoding="utf-8")

    with pytest.raises(OSError):
        publish_tree(source, target, plugin="ar")

    monkeypatch.undo()
    assert target.is_dir(), "the target was left missing after a failed swap"
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "shipped\n", (
        "the previous tree was not restored"
    )


def test_a_link_that_was_actually_linked_does_not_claim_it_was_copied(tmp_path):
    """The two outcomes of `publish_link` must be distinguishable.

    A copy forks silently — the harness keeps showing old text and nothing says
    why — so `publish_link` reports which one happened. If a real symlink is
    reported as "copied: this platform cannot create links", the message is
    exactly backwards: it tells a user their bridge is a dead copy when it is
    live, and hides the real fallback when it does occur.
    """
    from autorun.installer import fs

    source = tmp_path / "shared" / "commit"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("ours\n", encoding="utf-8")
    target = tmp_path / "dest" / "commit"
    target.parent.mkdir(parents=True)

    decision = fs.publish_link(source, target, plugin="ar")

    assert target.is_symlink(), "this platform should have made a real link"
    assert "cannot create links" not in decision.reason, decision
    assert str(source) in decision.reason, decision


def test_a_platform_without_symlinks_copies_and_records_that_it_did(
    tmp_path, monkeypatch
):
    """Windows without developer mode cannot create links; the copy must say so.

    A copy forks silently: the harness keeps showing old text and nothing
    indicates why. `publish_link` therefore records `bridge=copy` in the marker
    so status and uninstall can tell a copy from a link, and reports the
    fallback in its decision rather than claiming it linked.
    """
    from autorun.installer import fs

    source = tmp_path / "shared" / "commit"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("ours\n", encoding="utf-8")
    target = tmp_path / "dest" / "commit"
    target.parent.mkdir(parents=True)

    def no_symlinks(self, *args, **kwargs):
        raise OSError("symlink privilege not held")

    monkeypatch.setattr(Path, "symlink_to", no_symlinks)

    decision = fs.publish_link(source, target, plugin="ar")

    assert decision.verdict is Verdict.PUBLISH, decision
    assert "cannot create links" in decision.reason, decision
    assert not target.is_symlink(), "reported a copy but made a link"
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "ours\n"
    assert read_marker(target).settings["bridge"] == "copy", (
        "a copy that records bridge=link is indistinguishable from a real link"
    )


def test_an_unreadable_parent_does_not_stop_a_publication(tmp_path, monkeypatch):
    """Resuming an interrupted swap is best effort, never a new failure mode.

    `_resume_interrupted` lists the target's parent to find abandoned stages.
    That listing can fail on a directory the process cannot read, and a
    cleanup pass must not turn a publication that would have worked into an
    error.
    """
    from autorun.installer import fs

    source = tmp_path / "src" / "demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("shipped\n", encoding="utf-8")
    target = tmp_path / "dest" / "demo"
    target.parent.mkdir(parents=True)

    real_iterdir = Path.iterdir

    def refuse_once(self):
        if self == target.parent:
            raise PermissionError("cannot list")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", refuse_once)

    assert publish_tree(source, target, plugin="ar").verdict is Verdict.PUBLISH
    monkeypatch.undo()
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "shipped\n"


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


def test_every_tree_an_install_publishes_is_found_again_by_the_marker_sweep(
    tmp_path, monkeypatch
):
    """`owned_trees(max_depth=5)` is an assumption about the routes we ship.

    The retirement sweep is the only thing that finds what a *previous* version
    installed, and it stops descending at five levels. Today's routes fit; a
    future one that does not would leak a tree carrying our marker at a path no
    decision names, silently and permanently — the exact failure the sweep was
    added to fix, reintroduced by a bound nobody re-checked.

    Comparing an unbounded marker walk against the bounded sweep makes the
    assumption executable, so a route that outgrows it fails here rather than
    on a user's machine.
    """
    from autorun.installer.fs import owned_trees
    from autorun.installer.orchestrate import _config_roots, install
    from autorun.installer.traversal import Context
    from autorun.platforms import PLATFORMS

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / ".pi" / "agent"))
    selected = tuple(PLATFORMS[name] for name in ("claude", "codex", "pi"))
    install(
        marketplace_root=Path(__file__).resolve().parents[3],
        plugins=("ar",),
        harnesses=selected,
        home=tmp_path,
        available=(),
        state_dir=tmp_path / "state",
    )

    roots = _config_roots(
        selected, Context(marketplace_root=tmp_path, home=tmp_path)
    )
    assert roots, "the sweep would visit nothing, so this proves nothing"
    reachable = {tree for root in roots for tree in owned_trees(root, plugin="ar")}
    planted = {
        marker.parent
        for root in roots
        for marker in root.rglob(OWNED_MARKER_NAME)  # unbounded, on purpose
        if not any(part.startswith(".autorun-") for part in marker.parent.parts)
    }

    unreachable = sorted(
        str(tree.relative_to(tmp_path))
        for tree in planted - reachable
        # A nested marker inside an already-reported tree is correct to skip:
        # an owned tree is removed whole.
        if not any(tree != found and found in tree.parents for found in reachable)
    )
    assert not unreachable, (
        f"published deeper than owned_trees() descends, so the retirement sweep "
        f"can never find these again: {unreachable}"
    )


def test_a_link_replaced_after_the_preflight_is_not_removed(tmp_path, monkeypatch):
    """Resolving a link and unlinking a path are two operations.

    `withdraw_link` read the link, proved its target sat inside the shared
    skills root, and then unlinked — by path, with nothing holding the path
    still in between. A replacement landing in that window was removed on the
    strength of what the *previous* link pointed at, and a user's own link into
    the same shared root is exactly the shape that cannot be told apart after
    we stop looking. `publish_link` already closed the same window on the write
    side by re-judging under the parent's lock; this is the removal side.
    """
    import autorun.installer.fs as fs

    shared = tmp_path / "agents" / "skills" / "demo"
    shared.mkdir(parents=True)
    link = tmp_path / "dest" / "demo"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(shared)
    except (OSError, NotImplementedError) as unsupported:
        pytest.skip(f"this platform cannot create symlinks: {unsupported}")

    theirs = tmp_path / "agents" / "skills" / "theirs"
    theirs.mkdir()
    real_lock = fs.FileLock

    class ReplacingLock(real_lock):
        """Stands in for the concurrent writer, between preflight and removal."""

        def __enter__(self):
            entered = super().__enter__()
            if link.is_symlink():
                link.unlink()
                link.symlink_to(theirs)
            return entered

    monkeypatch.setattr(fs, "FileLock", ReplacingLock)

    removed = fs.withdraw_link(link, tmp_path / "agents" / "skills", exact_target=shared)

    assert removed is False, "the replacement was removed as if it were ours"
    assert link.is_symlink() and link.resolve() == theirs.resolve()


def test_a_kept_target_is_reported_once_per_uninstall_not_once_per_phase(
    tmp_path, monkeypatch
):
    """One target, one decision, however many walks the operation takes.

    Uninstall runs the selected-harness walk and then a separate retirement
    sweep, each with its own dedup set. A target the walk *removed* is gone
    before the sweep, so the two never collided — but a target the walk KEPT
    because the user edited it is still there, still marked, and the sweep finds
    it again. The user reading the report to find out what was preserved sees
    the same path listed twice and cannot tell that from two different things
    having been kept.
    """
    from collections import Counter

    from autorun.installer.orchestrate import install, uninstall
    from autorun.platforms import PLATFORMS

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    marketplace = Path(__file__).resolve().parents[3]
    where = dict(
        marketplace_root=marketplace,
        plugins=("ar",),
        harnesses=(PLATFORMS["codex"],),
        home=tmp_path,
        available=(),
        state_dir=tmp_path / "state",
    )

    install(**where)
    edited = tmp_path / ".agents" / "skills" / "commit" / "SKILL.md"
    assert edited.is_file(), "the shared skill route did not publish"
    edited.write_text("the user edited this\n", encoding="utf-8")

    result = uninstall(**where)

    repeated = {
        target: count
        for target, count in Counter(str(d.target) for d in result.decisions).items()
        if count > 1
    }
    assert not repeated, repeated
    assert edited.read_text(encoding="utf-8") == "the user edited this\n"


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


def _installer_modules_with_a_self_check() -> list[str]:
    """Installer modules shipping a ``demo()``, discovered rather than listed.

    A hardcoded list would cover only the modules that existed when it was
    written, which is exactly the failure this guards: nothing ran the demos,
    so `registration.demo()` asserted a stale invariant from 2026-08-05 through
    every green CI run until it was executed by hand.
    """
    package = Path(__file__).resolve().parents[1] / "src" / "autorun" / "installer"
    return sorted(
        path.stem
        for path in package.glob("*.py")
        if "\ndef demo(" in path.read_text(encoding="utf-8")
    )


@pytest.mark.timeout(150)
@pytest.mark.parametrize("module", _installer_modules_with_a_self_check())
def test_every_installer_module_self_check_passes(module, request):
    """`installer/AGENTS.md` ends with "Every module self-checks" and prints the
    command. Nothing ran it, so the claim was unverified and one of the sixteen
    was failing.

    Run as the documented subprocess rather than by importing `demo` here: that
    is the command a developer is told to run, several demos resolve paths at
    import time, and `orchestrate.demo` reaches the daemon unless `AUTORUN_HOME`
    is set before the first import — which only a fresh process can guarantee.

    `HOME` is redirected because that is the installer's isolation seam, and the
    runtime root comes from `AUTORUN_TEST_RUNTIME_DIR` (conftest.py) because the
    daemon socket underneath it must stay inside `sun_path`'s 104 bytes; a
    `tmp_path` under the platform temp directory does not.

    Run from the repository root, as the documented command is: `plugins/autorun`
    holds an `autorun.py` that shadows the package whenever it is the working
    directory, and `import autorun` there fails with "not a package".
    """
    import subprocess

    runtime = Path(os.environ["AUTORUN_TEST_RUNTIME_DIR"])
    home = runtime / f"demo-{module}"
    autorun_home = runtime / f"dh-{module}"
    for directory in (home, autorun_home):
        directory.mkdir(parents=True, exist_ok=True)

    # Below this test's own per-test budget, and derived from it so the two
    # cannot drift. When this was 120s against the suite's 30s default,
    # pytest-timeout fired first and aborted the whole session at 40% — thread
    # stacks, no failing test named, ~5,000 tests unrun because one demo was
    # still working.
    #
    # The 150s marker above is sized from measurement, not from a guess.
    # `orchestrate.demo()` runs a real install *and* uninstall across every
    # harness and takes 2.07s here, ten times the next slowest of the sixteen
    # and half their combined 4.2s. Windows does that many-small-file work an
    # order of magnitude slower, which is what put it over the 30s default;
    # its timeout dump showed `subprocess._readerthread` frames, which are this
    # very `subprocess.run` reading the child, not a deadlock inside it —
    # `orchestrate.demo()` spawns no subprocess at all.
    marker = request.node.get_closest_marker("timeout")
    budget = float(
        marker.args[0] if marker and marker.args else (request.config.getini("timeout") or 30)
    )

    try:
        result = subprocess.run(
            [sys.executable, "-c", f"from autorun.installer.{module} import demo; demo()"],
            capture_output=True,
            text=True,
            timeout=max(5.0, budget - 10.0),
            cwd=str(Path(__file__).resolve().parents[3]),
            env={
                **os.environ,
                "HOME": str(home),
                "USERPROFILE": str(home),
                "PI_CODING_AGENT_DIR": str(home / ".pi" / "agent"),
                "AUTORUN_HOME": str(autorun_home),
                "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            },
        )
    except subprocess.TimeoutExpired as expired:
        pytest.fail(
            f"autorun.installer.{module}.demo() did not finish within "
            f"{expired.timeout}s\nstdout:\n{expired.stdout}\nstderr:\n{expired.stderr}"
        )

    assert result.returncode == 0, (
        f"autorun.installer.{module}.demo() exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
