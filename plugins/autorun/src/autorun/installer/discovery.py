#!/usr/bin/env python3
"""Where autorun is installed from, and where each plugin lives inside it.

Two questions, each answered today by a long procedural search: 197 lines for
the marketplace root across six install shapes, and 125 for plugin resolution
across five strategies — of which 70 are an inline second copy inside
``_install_gemini_family_extensions`` that swallows the manifest errors the
shared one reports.

Both are the same shape: generate candidate paths in priority order, keep the
first that satisfies one predicate. Writing them that way makes the priority
order *readable* rather than reconstructable from control flow, and makes each
strategy independently testable — the inline copy existed because the original
could not be called with a different starting point.

Complexity: the ancestor and layout strategies are O(depth) stats. The UV-tool
fallback globs up to four levels under three bases and is only reached when a
UV tool install cannot see its own source, which is why it is last.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Mapping, Sequence
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

__all__ = [
    "MARKETPLACE_MANIFEST",
    "marketplace_root",
    "plugin_dir",
    "resolve_plugins",
    "is_marketplace_root",
    "config_dir", "extensions_dir", "expand_home", "build_timestamp",
    "skill_destinations", "shared_root", "codex_plugin_source",
    "personal_marketplace", "plugin_name", "PLUGIN_MANIFEST",
    "plugin_runtime_root",
    "redirected_home", "process_home",
    "python_too_old", "MINIMUM_PYTHON",
]

#: The file that makes a directory a marketplace root. One name, one authority.
MARKETPLACE_MANIFEST = Path(".claude-plugin") / "marketplace.json"

#: The manifest inside one plugin, which declares the name it registers under.
PLUGIN_MANIFEST = Path(".claude-plugin") / "plugin.json"

#: Copies kept for reference are not the thing to install from. A root is only
#: skipped when we are *not* running out of it: a developer working inside a
#: directory named `reference` still expects their own tree to win.
_SHADOWED = ("backup", "reference")

#: Bases the UV-tool fallback scans, and how deep. Only consulted when the
#: running file is inside a UV tool tree and cannot see a root above itself.
_UV_MARKERS = (".local/share/uv/tools", ".local/share/uv/python")
_UV_MAX_DEPTH = 4


def process_home() -> Path:
    """Return the configured process home on every supported OS.

    ``Path.home()`` consults the account database on Windows and therefore
    ignores a test or deployment that intentionally redirects ``HOME``.  The
    installer treats ``HOME`` as its isolation seam, so prefer it whenever it
    is present and fall back to the platform-native home lookup otherwise.
    """
    configured = os.environ.get("HOME")
    return Path(configured) if configured else Path.home()


def _uv_bases() -> tuple[Path, ...]:
    """Where to look for a checkout when a UV tool install cannot see its own.

    Harness config directories come from the registry rather than a literal
    ``~/.claude``: naming one harness here means a checkout kept under any other
    harness's directory is never found, and it duplicates a fact
    ``Platform.config_dir`` already owns. Derived lazily so importing this
    module does not import the registry.
    """
    from ..platforms import PLATFORMS

    homes = {
        expand_home(p.config_dir) for p in PLATFORMS.values() if getattr(p, "config_dir", "")
    }
    return (*sorted(homes), process_home(), Path("/opt"))


def is_marketplace_root(candidate: Path) -> bool:
    return (candidate / MARKETPLACE_MANIFEST).is_file()


def _shadowed(candidate: Path, origin: Path) -> bool:
    """True when ``candidate`` is a backup or reference we are not running from."""
    return any(word in str(candidate).lower() for word in _SHADOWED) and not str(
        origin
    ).startswith(str(candidate))


def _from_ancestors(origin: Path) -> Iterator[Path]:
    """Walk up from the running file, outermost match first.

    Tried first, deliberately: a materialized Gemini extension and a Claude
    plugin cache must install from their own copy rather than jumping to a
    development checkout elsewhere on the machine. Those copies have no root
    above them, so they win either way.

    Outermost rather than nearest, because this repository nests two roots —
    ``.claude-plugin/marketplace.json`` at the top *and* one inside
    ``plugins/autorun/`` — and the top one is the marketplace that lists both
    plugins. Taking the nearest resolves a source checkout to the plugin
    directory and finds no sibling plugin at all.

    This matches the behaviour of the search it replaces, which reached the same
    answer by accident: its loop had no ``break``, so it kept overwriting its
    result and returned the last match. Its comment claims the opposite rule.
    The behaviour is preserved here and the comment is not, because the
    behaviour is what six install shapes were tested against.
    """
    return reversed([origin, *origin.parents])


def _from_editable_install(origin: Path) -> Iterator[Path]:
    """Follow ``direct_url.json`` back to the source of an editable install.

    The recorded URL may be the workspace root or the plugin directory, so both
    are offered and the caller's predicate picks.

    It is a ``file://`` URL, not a path: a checkout under ``My Projects`` is
    recorded as ``My%20Projects``, and Windows records ``file:///C:/src`` with
    a leading slash the drive letter must not keep. Stripping the scheme alone
    left both, so the resolver looked for a directory that does not exist and
    silently fell through to the last-resort search. ``url2pathname`` is the
    decoder for exactly this, on each platform's own terms.
    """
    for dist_info in origin.parent.parent.glob("autorun*.dist-info"):
        try:
            data = json.loads((dist_info / "direct_url.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if "editable" not in data.get("dir_info", {}):
            continue
        url = str(data.get("url", ""))
        source = _url_to_path(url) if url.startswith("file:") else Path(url)
        if source is None:
            continue
        yield from (source, source / "plugins" / "autorun")


def _url_to_path(url: str) -> Path | None:
    """Decode one ``file://`` URL, authority included, or ``None``.

    ``url2pathname`` takes a path, not a URL, so the authority has to be put
    back in front of the decoded path or the host is simply gone: ``pip install
    -e \\\\build01\\share\\autorun`` records ``file://build01/share/autorun``,
    and passing ``urlparse(url).path`` alone decodes ``/share/autorun`` — a
    local directory on a different machine's disk, which does not exist,
    resolves to nothing, and reports nothing.

    An empty authority and ``localhost`` both mean "this machine" (RFC 8089
    §2), and pip has written each, so neither is reattached: ``//localhost/opt/
    src`` would be a network path on Windows.

    Any other authority names a host, and only Windows has a path syntax for
    one. ``None`` says so, and the caller skips that record rather than
    offering a local path that means somewhere else. Python 3.14 reached the
    same conclusion in the standard library — its ``url2pathname`` raises
    ``URLError: file:// scheme is supported only on localhost`` for a remote
    authority — so the authority is joined here rather than handed back to a
    decoder whose answer changes with the interpreter version.
    """
    parsed = urlparse(url)
    decoded = url2pathname(parsed.path)
    host = parsed.netloc
    if host and host.lower() != "localhost":
        if os.name != "nt":
            return None
        decoded = f"\\\\{unquote(host)}{decoded}"
    return Path(decoded)


def _from_uv_tool_search(origin: Path) -> Iterator[Path]:
    """Last resort: a UV tool install that cannot see its own source.

    ``AUTORUN_DEV_PATH`` is honoured first so a custom location does not have to
    win a glob race against every checkout in the home directory.
    """
    if not any(marker in str(origin) for marker in _UV_MARKERS):
        return
    if dev := os.environ.get("AUTORUN_DEV_PATH"):
        yield Path(dev)
    for base in (b for b in _uv_bases() if b.is_dir()):
        for depth in range(1, _UV_MAX_DEPTH + 1):
            found = [
                match.parent.parent
                for match in base.glob("/".join(["*"] * depth) + "/" + str(MARKETPLACE_MANIFEST))
            ]
            # An exact `autorun` beats a dashed variant beats anything else, so
            # a sibling checkout cannot outrank the real one by sorting earlier.
            yield from sorted(found, key=lambda p: (p.name != "autorun", "-" in p.name, str(p)))


_STRATEGIES: tuple[Callable[[Path], Iterable[Path]], ...] = (
    _from_ancestors,
    _from_editable_install,
    _from_uv_tool_search,
)


def marketplace_root(origin: Path | None = None) -> Path | None:
    """The directory autorun installs from, or None if there is none.

    ``origin`` defaults to this file, and exists so a caller can ask the
    question about a different location. The absence of that parameter is why a
    70-line second copy of this search was written inline.
    """
    origin = (origin or Path(__file__)).resolve()
    return next(
        (
            candidate
            for strategy in _STRATEGIES
            for candidate in strategy(origin)
            if is_marketplace_root(candidate) and not _shadowed(candidate, origin)
        ),
        None,
    )


def _source_path(source: object) -> str:
    """The path a manifest ``source`` names, in either spelling it uses.

    A local entry spells it as a relative string. A GitHub entry spells it as
    an object whose ``subdirectory`` is the path within the checked-out repo,
    which is the form this repository's own manifest uses::

        {"name": "ar",
         "source": {"source": "github", "repo": "...",
                    "subdirectory": "plugins/autorun"}}

    The code this replaces did ``root / source`` unconditionally, which raises
    ``TypeError`` on the object form — swallowed by a bare ``except Exception``,
    so the manifest's declared location was silently discarded and resolution
    only ever worked when the caller happened to pass a directory name. A plugin
    whose registered name differs from its directory, which is exactly the
    ``ar`` / ``autorun`` case here, resolved to nothing by this route.
    """
    if isinstance(source, str):
        return source
    return str(source.get("subdirectory", "")) if isinstance(source, dict) else ""


def _declared_source(root: Path, name: str) -> Iterator[Path]:
    """What the marketplace manifest says, which outranks any layout guess.

    A manifest that will not parse raises rather than being skipped: the inline
    copy swallowed the error, so a typo in the manifest silently fell through to
    a directory-name match and installed something the manifest did not declare.
    """
    manifest = root / MARKETPLACE_MANIFEST
    if not manifest.is_file():
        return
    entries = json.loads(manifest.read_text(encoding="utf-8")).get("plugins", [])
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("name") != name:
            continue
        if relative := _source_path(entry.get("source")):
            yield (root / relative).resolve()


def _layouts(root: Path, name: str) -> Iterator[Path]:
    """The layouts that exist in the wild, in priority order."""
    yield root / "plugins" / name      # workspace layout
    yield root / name                  # flat layout
    yield root.parent / name           # legacy sibling layout
    if root.name == name or (
        (root / PLUGIN_MANIFEST).is_file() and plugin_name(root) == name
    ):
        yield root                     # the root is the installed plugin package


def plugin_dir(root: Path, name: str) -> Path | None:
    """Resolve one plugin by its registered name, manifest first."""
    return next(
        (
            candidate
            for candidate in (*_declared_source(root, name), *_layouts(root, name))
            if candidate.is_dir()
        ),
        None,
    )


def plugin_name(directory: Path) -> str:
    """The name a plugin directory registers under, not the directory's own.

    The two differ — ``plugins/autorun`` registers as ``ar`` — and it is the
    registered name that ``--uninstall ar`` selects on and that every ownership
    marker records. Recording the directory name instead is what left a Codex
    tree unremovable under the name every other route used.
    """
    manifest = directory / PLUGIN_MANIFEST
    try:
        declared = json.loads(manifest.read_text(encoding="utf-8")).get("name")
    except (OSError, AttributeError, json.JSONDecodeError):
        declared = None
    return declared if isinstance(declared, str) and declared else directory.name


def plugin_runtime_root(directory: Path) -> Path:
    """Return the Python resource root in a checkout or installed wheel.

    Source trees keep templates under ``src/autorun`` so Claude does not scan
    Gemini hook manifests as its own.  Wheel resources are already isolated
    inside the installed ``autorun`` package and live directly beside the
    Python modules.
    """
    source = directory / "src" / "autorun"
    return source if source.is_dir() else directory


def resolve_plugins(root: Path, names: Sequence[str]) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    """Resolve several plugins, de-duplicated, preserving order.

    Two names can resolve to one directory (a registered name and its directory
    name), and installing that directory twice writes it twice. Returns the
    directories found and the names that resolved to nothing, so a caller
    reports which plugin is missing rather than a count.
    """
    found: dict[Path, None] = {}
    missing: list[str] = []
    for name in names:
        if resolved := plugin_dir(root, name):
            found.setdefault(resolved.resolve(), None)
        else:
            missing.append(name)
    return (tuple(found), tuple(missing))


#: The lowest interpreter this installer supports. Checked before any autorun
#: import so the message is about the Python version rather than a SyntaxError
#: from a match statement in a module that was never going to load.
MINIMUM_PYTHON = (3, 10)


def python_too_old(current: tuple[int, ...] | None = None) -> str:
    """An actionable message when the interpreter is too old, else empty.

    Returned rather than raised so the caller can print it and exit cleanly. A
    traceback from a parse error names a file the user did not write and gives
    them nothing to do.
    """
    version = current or sys.version_info[:2]
    if tuple(version) >= MINIMUM_PYTHON:
        return ""
    have = ".".join(str(p) for p in version)
    want = ".".join(str(p) for p in MINIMUM_PYTHON)
    return (
        f"autorun needs Python {want} or newer; this interpreter is {have}.\n"
        f"Install a newer Python and re-run, for example:\n"
        f"  uv python install {want}\n"
        f"  uv run --python {want} python -m autorun --install"
    )


def expand_home(value: str | Path, *, home: Path | None = None) -> Path:
    """Expand ``~`` through one seam.

    One function, so a test that patches ``Path.home`` moves every path at once.
    Scattered ``expanduser`` calls read the real home directly and are why an
    isolated test could still reach the developer's own configuration.

    ``~`` and ``~/…`` resolve through the seam. ``~otheruser/…`` cannot — it
    names a home this process is not running as — so it is handed to
    ``expanduser``, which the installer being replaced also used. A name with no
    such user is returned verbatim rather than raising, because the value came
    from configuration and refusing to build a path is not the installer's call
    to make; the caller sees a path that plainly does not exist.
    """
    text = str(value)
    root = home if home is not None else process_home()
    if text == "~":
        return root
    if text.startswith("~/"):
        return root / text[2:]
    if text.startswith("~"):
        try:
            return Path(text).expanduser()
        except RuntimeError:
            return Path(text)
    return Path(text)


def _configured_roots() -> Mapping[str, str]:
    """User-configured harness roots, read lazily so importing stays cheap."""
    from ..config import CONFIG

    roots = CONFIG.get("harness_config_dirs", {})
    return roots if isinstance(roots, Mapping) else {}


@contextmanager
def redirected_home(home: Path) -> Iterator[Path]:
    """Point ``$HOME`` at ``home`` for the duration, then put it back.

    The one correct way to isolate anything that installs. ``Path.home()`` reads
    ``$HOME``, so redirecting it moves *every* route together; setting a
    ``home`` argument while leaving ``$HOME`` alone moves some and not others,
    which is a walk that reads a sandbox and writes a real home. That is not
    hypothetical — it uninstalled 16 skills from a live machine during a
    self-check that looked isolated, which is why ``Context`` now refuses the
    mismatch and why this exists to make the right form the easy one.
    """
    # Both names, on every platform. Path.home() resolves through
    # os.path.expanduser, which reads USERPROFILE on Windows and HOME
    # elsewhere, and never consults the other one. Setting only HOME left
    # Path.home() pointing at the real profile on Windows -- the redirect this
    # function exists to make reliable would have read a sandbox and written a
    # live home there, which is the failure the docstring above describes.
    # Setting the unused name costs nothing and removes the branch.
    names = ("HOME", "USERPROFILE")
    previous = {name: os.environ.get(name) for name in names}
    for name in names:
        os.environ[name] = str(home)
    try:
        yield home
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def shared_root(*, home: Path | None = None) -> Path:
    """The vendor-neutral skills root several harnesses read (default
    ``~/.agents/skills``).

    Not per-harness, so it is configuration rather than platform data, and it
    belongs here beside :func:`config_dir` for the same reason: one authority
    per location question. Both halves are configurable, and reading them
    through CONFIG rather than hardcoding the path is what keeps install and
    uninstall looking in the same directory — a literal here would leave
    uninstall unable to find what install wrote under a user's override.
    """
    from ..config import CONFIG

    base = expand_home(str(CONFIG.get("shared_agents_dir", "~/.agents")), home=home)
    return base / str(CONFIG.get("shared_agents_skills_subdir", "skills"))


def codex_plugin_source(name: str = "ar", *, home: Path | None = None) -> Path:
    """One local Codex plugin tree named by the personal marketplace."""
    from ..config import CONFIG

    base = expand_home(str(CONFIG.get("codex_plugin_source_dir", "~/plugins")), home=home)
    return base / name


def personal_marketplace(*, home: Path | None = None) -> Path:
    """Codex's implicit personal marketplace under the shared agents root."""
    return shared_root(home=home).parent / "plugins" / "marketplace.json"


def extensions_dir(platform: object, *, env: Mapping[str, str] | None = None,
                   home: Path | None = None) -> Path | None:
    """Where this harness keeps extensions, or None if it has none.

    ``None`` is the answer for Claude, Codex, ForgeCode and OpenCode, and it is
    how they declare "extensions are not a thing here" — ``extensions_subdir``
    is empty for them. Defaulting to the literal ``"extensions"`` instead
    invents ``~/.claude/extensions/ar``, a path with no meaning that a caller
    will happily create.
    """
    subdir = str(getattr(platform, "extensions_subdir", "") or "")
    base = config_dir(platform, env=env, home=home)
    return base / subdir if subdir and base is not None else None


def config_dir(platform: object, *, env: Mapping[str, str] | None = None,
               home: Path | None = None) -> Path | None:
    """Where this harness keeps its configuration.

    Three tiers, in order.

    ``CONFIG["harness_config_dirs"][name]`` first: it is the only way to
    relocate a harness root *without* an environment variable, which matters
    because two harnesses document no variable at all. Omitting this tier does
    not error — it silently writes to the default location, and the user's
    configured root simply never receives anything.

    Then the harness's own documented variable. Those name the *parent*:
    ``XDG_CONFIG_HOME`` is ``~/.config``, not ``~/.config/<harness>``, so the
    harness's subdirectory is appended. Treating the variable as the final
    answer writes every file one level too high, where the harness never looks
    and nothing reports an error.

    Then the declared default.
    """
    environ = os.environ if env is None else env
    if override := _configured_roots().get(getattr(platform, "name", "")):
        return expand_home(override, home=home)
    for var in getattr(platform, "config_dir_env_vars", ()) or ():
        if not (value := environ.get(var)):
            continue
        resolved = expand_home(value, home=home)
        subdir = getattr(platform, "config_dir_env_var_subdir", "")
        return resolved / subdir if subdir else resolved
    declared = getattr(platform, "config_dir", "")
    return expand_home(declared, home=home) if declared else None


def skill_destinations(
    platform: object,
    *,
    reading: bool = False,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> tuple[Path, ...]:
    """Every directory one harness's skill routes resolve to.

    One entry point for a question asked from several places, so the read/write
    distinction and the de-duplication are decided once rather than at each call
    site.

    ``reading`` selects the routes the harness *reads* rather than the one
    autorun *writes*. They genuinely differ, and confusing them misplaces files:
    Antigravity reads ``~/.gemini/config/skills`` but its native write route is
    its plugins directory, and ForgeCode reads ``~/forge/skills`` with no write
    route at all.

    ``home`` is the resolved install target. Callers that have a
    :class:`~autorun.installer.traversal.Context` pass it explicitly so every
    route, including home-anchored and plugin-package routes, follows the same
    sandbox. Calls without it retain process-home behavior for status and
    discovery probes.
    """
    routes = (
        tuple(getattr(platform, "skill_search_routes", ()) or ())
        if reading
        else (getattr(platform, "native_skills", None),)
    )
    base = config_dir(platform, env=env, home=home)
    seen: dict[Path, None] = {}
    for route in routes:
        if route is None:
            continue
        try:
            destinations = route.destinations(base, home=home)
        except TypeError as error:
            if "unexpected keyword argument 'home'" not in str(error):
                raise
            # Keep compatibility with third-party route objects written
            # against the pre-Context.home protocol.
            destinations = route.destinations(base)
        for destination in destinations:
            seen.setdefault(destination, None)
    return tuple(seen)


def build_timestamp(env: Mapping[str, str] | None = None) -> str:
    """A reproducible ISO timestamp for anything recorded during an install.

    ``SOURCE_DATE_EPOCH`` is the cross-ecosystem reproducible-build input. With
    it, two installs of the same source produce byte-identical metadata; without
    it, wall-clock time makes every install differ and defeats any comparison a
    user might make between two machines.

    An unusable value falls back rather than aborting: a broken timestamp is not
    a reason to refuse to install.
    """
    raw = (os.environ if env is None else env).get("SOURCE_DATE_EPOCH", "")
    try:
        seconds = int(raw)
        if seconds < 0:
            raise ValueError("negative epoch")
    except ValueError:
        return ""
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def demo() -> None:
    """Self-check: every strategy, the shadow rule, and the manifest override."""
    import tempfile

    def make_root(path: Path, plugins: list[dict] | None = None) -> Path:
        (path / MARKETPLACE_MANIFEST).parent.mkdir(parents=True, exist_ok=True)
        (path / MARKETPLACE_MANIFEST).write_text(
            json.dumps({"plugins": plugins or []}), encoding="utf-8"
        )
        return path

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()

        # Strategy 1: walk up from the running file.
        repo = make_root(root / "repo")
        deep = repo / "plugins" / "autorun" / "src" / "autorun" / "install.py"
        deep.parent.mkdir(parents=True)
        assert marketplace_root(deep) == repo

        # Nested roots: the outermost wins, because that is the marketplace
        # listing every plugin. Taking the nearest resolves a source checkout to
        # one plugin directory and finds no siblings.
        inner = make_root(repo / "plugins" / "autorun")
        assert marketplace_root(deep) == repo, "outermost root wins when nested"
        assert inner.is_dir()

        # A copy with no root above it installs from itself, which is the
        # materialized-extension and plugin-cache case.
        lone = make_root(root / "ext" / "ar")
        assert marketplace_root(lone / "src" / "x.py") == lone

        # A backup copy is skipped unless we are running out of it.
        backup = make_root(root / "backup-repo")
        assert marketplace_root(deep) == repo, "a sibling backup never wins"
        assert marketplace_root(backup / "src" / "x.py") == backup, "unless we are in it"

        # Plugin resolution: the manifest outranks a same-named directory.
        market = make_root(root / "m", [{"name": "ar", "source": "./plugins/autorun"}])
        (market / "plugins" / "autorun").mkdir(parents=True)
        (market / "ar").mkdir()
        assert plugin_dir(market, "ar") == (market / "plugins" / "autorun").resolve()

        # A GitHub-sourced entry names its path as `subdirectory`, and its
        # registered name need not match its directory. This is the shape of
        # this repository's own manifest, and the form the old resolver dropped.
        gh = make_root(root / "gh", [{
            "name": "ar",
            "source": {"source": "github", "repo": "x/y", "subdirectory": "plugins/autorun"},
        }])
        (gh / "plugins" / "autorun").mkdir(parents=True)
        assert plugin_dir(gh, "ar") == (gh / "plugins" / "autorun").resolve(), \
            "the manifest resolves a name that matches no directory"

        # Layouts, in order, when the manifest says nothing.
        plain = make_root(root / "p")
        (plain / "plugins" / "pdf").mkdir(parents=True)
        assert plugin_dir(plain, "pdf") == plain / "plugins" / "pdf"
        (plain / "flat").mkdir()
        assert plugin_dir(plain, "flat") == plain / "flat"
        assert plugin_dir(plain, "nope") is None

        # A broken manifest raises rather than silently falling through to a
        # directory that the manifest never declared.
        broken = root / "b"
        (broken / MARKETPLACE_MANIFEST).parent.mkdir(parents=True)
        (broken / MARKETPLACE_MANIFEST).write_text("{not json", encoding="utf-8")
        (broken / "ar").mkdir()
        try:
            plugin_dir(broken, "ar")
        except json.JSONDecodeError:
            pass
        else:  # pragma: no cover - the assertion is the point
            raise AssertionError("a broken manifest must not be swallowed")

        # Several names, de-duplicated, with the missing ones named.
        dirs, missing = resolve_plugins(market, ["ar", "ar", "ghost"])
        assert len(dirs) == 1, dirs
        assert missing == ("ghost",), missing

        # --- config dirs, the ~ seam, reproducibility, the version guard -----
        class FakePlatform:
            config_dir = "~/.fakeharness/"
            config_dir_env_vars = ("XDG_CONFIG_HOME",)
            config_dir_env_var_subdir = "fakeharness"

        fake, fake_home = FakePlatform(), root / "home"

        # No override: the declared path, expanded through the one seam.
        assert config_dir(fake, env={}, home=fake_home) == fake_home / ".fakeharness"

        # XDG names the PARENT, so the harness subdirectory is appended.
        # Treating it as the final answer writes one level too high, where the
        # harness never looks and nothing reports an error.
        assert config_dir(fake, env={"XDG_CONFIG_HOME": str(root / "cfg")},
                          home=fake_home) == root / "cfg" / "fakeharness"
        assert config_dir(fake, env={"XDG_CONFIG_HOME": "~/cfg"},
                          home=fake_home) == fake_home / "cfg" / "fakeharness"

        # A harness with no declared directory has no answer, rather than home.
        class Bare:
            config_dir = ""
        assert config_dir(Bare(), env={}, home=fake_home) is None

        # One ~ seam, so a patched home moves every path together.
        assert expand_home("~", home=fake_home) == fake_home
        assert expand_home("~/x/y", home=fake_home) == fake_home / "x" / "y"
        assert expand_home("/abs/path", home=fake_home) == Path("/abs/path")
        # A name with no such user is returned verbatim rather than raising.
        assert expand_home("~not-a-user/x", home=fake_home) == Path("~not-a-user/x")
        # A real other user resolves through expanduser: the seam moves *this*
        # process's home, and ~someone-else names a home it is not running as.
        # Returning a relative path called `~root` under the CWD, which the
        # first draft did, is the one answer that is certainly wrong.
        #
        # POSIX only: `pwd` is the account database module and does not exist on
        # Windows, where `~other` has no meaning to expanduser either. The seam
        # itself is asserted above on every platform; this covers the one case
        # that needs a real second account to exist.
        if os.name == "posix":
            import pwd

            other = next((u.pw_name for u in pwd.getpwall() if u.pw_dir.startswith("/")), "")
            if other:
                resolved = expand_home(f"~{other}/x", home=fake_home)
                assert resolved.is_absolute(), resolved

        # Reproducible metadata: same epoch in, same string out.
        assert build_timestamp({"SOURCE_DATE_EPOCH": "1700000000"}) == \
               build_timestamp({"SOURCE_DATE_EPOCH": "1700000000"})
        assert build_timestamp({"SOURCE_DATE_EPOCH": "1700000000"}).startswith("2023-")
        # An unusable value falls back rather than aborting the install.
        for bad in ("", "not-a-number", "-1"):
            assert build_timestamp({"SOURCE_DATE_EPOCH": bad}) == ""

        # The version guard reports rather than raising a parse error.
        assert python_too_old((3, 12)) == ""
        assert python_too_old(MINIMUM_PYTHON) == ""
        message = python_too_old((3, 9))
        assert "3.10" in message and "3.9" in message and "uv python install" in message

    print("installer.discovery: all self-checks passed")


if __name__ == "__main__":
    demo()
