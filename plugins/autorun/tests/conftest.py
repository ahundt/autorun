# -*- coding: utf-8 -*-
# conftest.py — pytest automatically discovers and loads this file before running
# any tests in this directory. This is a standard pytest convention:
# https://docs.pytest.org/en/stable/reference/fixtures.html#conftest-py-sharing-fixtures-across-files
#
# This file (plugins/autorun/tests/conftest.py) provides:
#   - Custom pytest markers (slow, stress, race, daemon, e2e, serial)
#   - Serial/parallel test assignment based on file name
#   - DaemonManager: protects production daemon PIDs; manages test-spawned daemons
#   - Shared fixtures: unique_session_id, temp_session_dir, ensure_single_daemon, etc.
#   - pytest_sessionstart / pytest_sessionfinish hooks for cleanup
#
# The parent conftest.py (plugins/autorun/conftest.py) runs first and provides
# a Python 3.10+ version guard via src/autorun/python_check.py.
"""
pytest configuration and fixtures for autorun testing

Environment Variables:
    AUTORUN_KEEP_TEST_ARTIFACTS: Set to 'true', '1', or 'yes' to keep test artifacts
                                   for debugging instead of cleaning them up.
"""
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import psutil

import pytest


def skip_if_windows_service_provider_error(result: subprocess.CompletedProcess) -> None:
    """Skip only hosted-Windows socket-provider failures, not product errors."""
    if sys.platform == "win32" and "WinError 10106" in (result.stderr or ""):
        pytest.skip("Windows runner has no usable _overlapped service provider")


def private_tmux_binary(resolved: str | None) -> str | None:
    """The real tmux client to wrap, or ``None`` when there is none to wrap.

    `pytest_configure` writes a shell script named `tmux` that adds
    `-S <private socket>` and puts its directory first on `PATH`, so every tmux
    call in the run is isolated. That only works if the script execs a *different*
    binary. `tmux_utils._candidate_tmux_binaries` ends with `or ["tmux"]` — a
    bare name — when the machine has no tmux at all, and a wrapper that execs
    `tmux` resolves through `PATH` straight back to itself and loops until
    something times out.

    `ci (macos-latest, 3.13)` is such a machine: only the `tmux-integration`
    job installs tmux. The first non-tmux-marked test to call tmux there hung
    for its full 60-second subprocess budget.

    So: wrap an absolute, executable path, and nothing else. Without one there
    is no private server, `AUTORUN_TEST_TMUX_SOCKET` stays unset, `PATH` is left
    alone, and the tests that need tmux skip — which is the honest outcome on a
    machine that has none.
    """
    if not resolved:
        return None
    if not os.path.isabs(str(resolved)):
        return None
    if not os.access(str(resolved), os.X_OK):
        return None
    return str(resolved)


def pytest_configure(config):
    """Configure pytest with custom markers and DB isolation."""
    config.addinivalue_line("markers", "slow: marks tests as slow")
    config.addinivalue_line("markers", "stress: marks tests as stress tests")
    config.addinivalue_line("markers", "race: marks tests as race condition tests")
    config.addinivalue_line("markers", "daemon: marks tests that require a running daemon")
    config.addinivalue_line("markers", "e2e: marks end-to-end tests")
    config.addinivalue_line("markers", "serial: marks tests that must run serially")

    # DB ISOLATION: Redirect session_manager to a temp directory BEFORE any
    # test module imports trigger _get_store(). This prevents tests from
    # reading/writing to the real user data at ~/.claude/sessions/.
    # Must happen in pytest_configure (earliest hook) because module-level
    # imports in test files can trigger _get_store() during collection.
    test_runtime_dir = Path(os.environ["AUTORUN_TEST_RUNTIME_DIR"])
    test_state_dir = Path(os.environ["AUTORUN_TEST_STATE_DIR"])
    test_autorun_home = Path(os.environ["AUTORUN_HOME"])
    config._autorun_test_runtime_dir = str(test_runtime_dir)
    config._autorun_test_state_dir = str(test_state_dir)
    os.environ["AUTORUN_TEST_STATE_DIR"] = str(test_state_dir)
    os.environ["AUTORUN_HOME"] = str(test_autorun_home)

    src_path = str(Path(__file__).parent.parent / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    # PLATFORM ISOLATION: derive every harness selector from the registry so
    # running pytest inside Claude, Codex, Qwen, Agy, Gemini, or Forge cannot
    # silently change response schemas or session selection. Individual tests
    # opt back into a platform after collection.
    from autorun.platforms import PLATFORMS

    platform_env_vars = {
        key
        for platform in PLATFORMS.values()
        for key in (
            *platform.detect_env_vars,
            *platform.standalone_session_env_vars,
        )
    }
    platform_env_vars.update(
        {
            "AUTORUN_CLI_TYPE",
            "AUTORUN_SESSION_ID",
        }
    )
    for env_var in platform_env_vars:
        os.environ.pop(env_var, None)

    # TMUX ISOLATION: tmux is one server per user, so a test that creates,
    # renames, or kills sessions in it is editing whatever the developer has
    # open. Redirect every tmux call this suite makes to a private socket.
    #
    # Unconditional, where it used to require pytest to be running inside a
    # live tmux. That condition made the isolation exactly backwards: a run
    # started from an ordinary shell got no private server and drove the real
    # one, and only a run started from inside tmux was protected.
    #
    # The wrapper goes first on PATH, not just into AUTORUN_TMUX_BIN, because
    # 43 call sites across four test modules build ["tmux", ...] by hand and
    # resolve it from PATH. Publishing the wrapper under both names is one
    # mechanism reaching both kinds of caller, and it covers the call site
    # somebody writes next without their having to know any of this.
    #
    # POSIX only: the wrapper is a /bin/sh script, and Windows has no tmux.
    # AUTORUN_TEST_USE_LIVE_TMUX=1 opts out for manual diagnostics.
    if os.name == "posix" and os.environ.get("AUTORUN_TEST_USE_LIVE_TMUX") != "1":
        from autorun.tmux_utils import resolve_tmux_binary

        # Resolve the real client BEFORE the wrapper shadows `tmux` on PATH,
        # or the wrapper would exec itself. On Apple Silicon this also prefers
        # /opt/homebrew over an older /usr/local client.
        real_tmux = private_tmux_binary(resolve_tmux_binary())
    else:
        real_tmux = None

    if real_tmux is not None:
        # Whoever invents the socket owns the server. An inherited one belongs
        # to an outer pytest — the controller, for an xdist worker, or the run
        # that spawned this one, for the nested pytest invocations in
        # `test_release_artifacts` and `test_suite_harness`. Reusing it is
        # deliberate and keeps one private server per run; tearing it down is
        # not, and `pytest_sessionfinish` used to do exactly that. The outer
        # run's next tmux call then reported `server exited unexpectedly` or
        # `no server running on <socket>`, its windows were gone, and its
        # `send_keys` returned False — a flake that only appears when something
        # else is spawning pytest at the same time.
        inherited_socket = os.environ.get("AUTORUN_TEST_TMUX_SOCKET")
        socket_path = inherited_socket or str(
            Path(tempfile.gettempdir()) / f"autorun-pytest-tmux-{uuid.uuid4().hex}"
        )
        config._autorun_owns_test_tmux_server = inherited_socket is None
        wrapper_dir = test_runtime_dir / "tmux-bin"
        wrapper_dir.mkdir(parents=True, exist_ok=True)
        wrapper = wrapper_dir / "tmux"
        wrapper.write_text(
            "#!/bin/sh\n"
            "unset TMUX\n"
            "export SHELL=/bin/sh\n"
            f"exec {shlex.quote(real_tmux)} -f /dev/null -S "
            f"{shlex.quote(socket_path)} \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)

        original_tmux = os.environ.get("TMUX", "")
        config._autorun_original_tmux = original_tmux
        config._autorun_real_tmux_bin = real_tmux
        config._autorun_test_tmux_socket = socket_path
        if original_tmux:
            os.environ["AUTORUN_ORIGINAL_TMUX"] = original_tmux
        os.environ["AUTORUN_TEST_TMUX_SOCKET"] = socket_path
        os.environ["AUTORUN_TMUX_BIN"] = str(wrapper)
        os.environ.pop("TMUX", None)

        path_parts = os.environ.get("PATH", "").split(os.pathsep)
        os.environ["PATH"] = os.pathsep.join(
            [str(wrapper_dir)] + [p for p in path_parts if p and p != str(wrapper_dir)]
        )
        resolve_tmux_binary.cache_clear()


# Test file groups for automatic serial/parallel assignment
_SERIAL_SHELVE_TESTS = {
    "test_database_functionality", "test_stale_lock_recovery",
    "test_same_session_multi_process", "test_race_condition_fix",
    "test_command_blocking_comprehensive", "test_command_blocking",
    "test_policy_enforcement_matrix", "test_three_stage_completion",
    "test_task_lifecycle_integration", "test_task_lifecycle_failure_modes",
    "test_task_lifecycle_edge_cases", "test_task_lifecycle_ghost_task_bug",
    "test_thread_safety_simple", "test_e2e_policy_lifecycle",
    "test_session_lifecycle_edge_cases",
}

_SERIAL_DAEMON_TESTS = {
    "test_hook_entry",
    "test_session_persistence_hooks", "test_gemini_e2e_improved",
    "test_gemini_e2e_real_money", "test_codex_e2e_real_money",
    "test_gemini_before_tool_hooks",
    "test_task_cli_commands", "test_demo",
}

_SERIAL_TMUX_TESTS = {
    "test_tmux_injector", "test_tmux_workflows_integration",
    "test_tmux_automation_agents", "test_tmux_compliance",
    "test_tmux_utils_enhanced", "test_session_targeting_diagnostic",
    "test_session_targeting_regression", "test_bang_syntax",
    "test_session_start_handler",
    # These create and kill sessions with fixed names in the one private tmux
    # server, so two of them at once kill each other's sessions. Found by the
    # guard in test_suite_harness.py, not by hand: `test_demo` is also in the
    # daemon table, which is what the priority order below exists to settle.
    "test_command_fixes_tmux_ttest", "test_tabs", "test_demo",
}


def tmux_argv(*args):
    """A raw tmux command that reaches the same server TmuxUtilities does.

    ``pytest_configure`` above already redirects tmux when the suite runs from
    inside the user's session: it writes a wrapper that unsets ``TMUX`` and
    adds ``-S <private socket>``, and publishes it as ``AUTORUN_TMUX_BIN``.
    ``resolve_tmux_binary`` returns that wrapper, so everything reaching tmux
    through ``TmuxUtilities`` is already isolated.

    A test that builds ``["tmux", ...]`` by hand bypasses it and talks to the
    default server instead — so it sends on one server and reads on another,
    which looks like a delivery bug and is really a split brain. Route raw
    commands through here and both halves land on the same server.

    Deliberately reuses ``AUTORUN_TMUX_BIN`` rather than adding a second
    socket switch: two mechanisms for one question is how the ``-S`` flag ends
    up applied twice.
    """
    return [os.environ.get("AUTORUN_TMUX_BIN", "tmux"), *args]


@pytest.hookimpl(tryfirst=True)
def pytest_exception_interact(node, call, report):
    """Attach a failed subprocess's own output to the failure report.

    ``subprocess.run(..., check=True)`` raises ``CalledProcessError``, whose
    message is only "Command '[...]' returned non-zero exit status N". The
    captured stdout and stderr are attributes on the exception, but nothing
    prints them, so a test that spawns a hook process and dies reports an exit
    code and nothing about why. That is exactly how the antigravity hook
    failure on ubuntu-3.11 stayed undiagnosed: the assertion had to be
    rewritten by hand to report the child's output before the cause was
    visible.

    Forty-two call sites in this suite use ``check=True`` and a further set
    passes ``timeout=``. Attaching the output once, here, covers all of them,
    and it cannot change any test's outcome -- it only adds sections to a
    report that has already failed.
    """
    exception = call.excinfo.value if call.excinfo is not None else None
    if not isinstance(
        exception, (subprocess.CalledProcessError, subprocess.TimeoutExpired)
    ):
        return
    returncode = getattr(exception, "returncode", "timed out")
    for stream in ("stdout", "stderr"):
        captured = getattr(exception, stream, None)
        if not captured:
            continue
        if isinstance(captured, bytes):
            captured = captured.decode("utf-8", "replace")
        report.sections.append(
            (f"subprocess {stream} (exit {returncode})", captured)
        )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Auto-assign serial/parallel markers based on test file dependencies.

    ``serial`` alone means nothing to pytest-xdist, which distributes by test,
    so each of these files was free to land on a different worker and race the
    others for the same singleton. tmux is one server per user: two workers
    creating windows and running ``select-layout`` in it interleave, and
    `test_multi_window_automation_workflow` failed intermittently under ``-n 8``
    because of it.

    Giving each resource class its own xdist group makes ``--dist loadgroup``
    put all of its tests on one worker, which serializes access to the
    singleton while the rest of the suite still spreads out. The three sets
    above stay the single declaration of what shares what; this only teaches
    them to xdist rather than restating them.

    ``tryfirst`` is load-bearing. The worker-side hook that acts on these marks
    (``xdist/remote.py``) appends ``@<group>`` to each nodeid, and the
    controller's scheduler groups by that suffix — so it has to see the mark
    already applied. Without ``tryfirst`` xdist's hook ran first, found no
    ``xdist_group``, appended nothing, and the marks were decoration: the tmux
    tests still spread over eight workers and raced the one tmux server.
    """
    # Most restrictive resource first. A module may appear in more than one
    # table -- `test_demo` drives both tmux and the daemon -- and exactly one
    # group may win: xdist joins several `xdist_group` marks into a compound
    # name (`daemon_tmux` in `remote.py`), which is a *third* bucket that runs
    # concurrently with both of the ones it was supposed to be inside. Every
    # matching table still contributes its markers; only the group is singular.
    groups = (
        (_SERIAL_TMUX_TESTS, "tmux", ()),
        (_SERIAL_DAEMON_TESTS, "daemon", (pytest.mark.daemon,)),
        (_SERIAL_SHELVE_TESTS, "shelve", ()),
    )
    for item in items:
        # Extract test file stem from nodeid
        parts = item.nodeid.split("::")
        if not parts:
            continue
        file_stem = parts[0].rsplit("/", 1)[-1].replace(".py", "")

        group = None
        for names, candidate, extra in groups:
            if file_stem not in names:
                continue
            group = group or candidate
            for marker in extra:
                item.add_marker(marker)
        if group is not None:
            item.add_marker(pytest.mark.serial)
            item.add_marker(pytest.mark.xdist_group(f"autorun-{group}"))


# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from autorun import CONFIG  # noqa: E402


# =============================================================================
# DAEMON LIFECYCLE MANAGEMENT (DRY — all daemon ops consolidated here)
# =============================================================================

class DaemonManager:
    """Centralized daemon lifecycle management for tests.

    Uses psutil for cross-platform process discovery and termination
    (works on Linux, macOS, and Windows — replaces pgrep/kill).

    Tracks production daemon PIDs (recorded before tests) so tests never kill
    daemons belonging to real coding sessions.

    Usage:
        # In pytest_sessionstart: DaemonManager.snapshot_production_pids()
        # In fixture:             DaemonManager.kill_test_daemons()
        # In pytest_sessionfinish: DaemonManager.cleanup()
    """

    # PIDs that existed before the test suite started — never killed
    _production_pids: set = set()

    # PIDs spawned by tests — killed on cleanup
    _test_spawned_pids: set = set()

    @classmethod
    def _get_all_daemon_pids(cls) -> list:
        """Get all autorun daemon PIDs currently running.

        Uses psutil.process_iter() for cross-platform process discovery
        (replaces Unix-only pgrep -f autorun.daemon).
        Skipped on Windows: daemon uses Unix sockets (AF_UNIX), unavailable on Windows.
        """
        if sys.platform == "win32":
            return []
        pids = []
        for proc in psutil.process_iter(['pid', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline') or []
                cmdline_str = ' '.join(cmdline)
                if 'autorun.daemon' in cmdline_str:
                    pids.append(str(proc.info['pid']))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        return pids

    @classmethod
    def snapshot_production_pids(cls):
        """Record daemon PIDs that exist before tests start.

        Called once in pytest_sessionstart. These PIDs are protected from
        all test cleanup operations.
        """
        cls._production_pids = set(cls._get_all_daemon_pids())
        if cls._production_pids and os.getenv("DEBUG", "").lower() in {"true", "1", "yes"}:
            print(f"\n[DEBUG] Production daemon PIDs (protected): {cls._production_pids}")

    @classmethod
    def _daemon_home(cls, pid_str: str):
        """The ``AUTORUN_HOME`` a running daemon was started with, or ``None``."""
        try:
            return psutil.Process(int(pid_str)).environ().get("AUTORUN_HOME")
        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.ZombieProcess,
            ValueError,
            OSError,
        ):
            return None

    @classmethod
    def _is_ours(cls, pid_str: str) -> bool:
        """Whether this worker started the daemon or hosts it in its own root.

        The environment is the durable signal: ``plugins/autorun/conftest.py``
        gives every worker process its own ``mkdtemp`` runtime root and exports
        ``AUTORUN_HOME`` under it, so a daemon's own ``AUTORUN_HOME`` says which
        worker it belongs to no matter who spawned it. ``_test_spawned_pids`` is
        the fallback for when ``environ()`` is denied.

        Unknown means not ours. Sparing a daemon we cannot identify costs a
        leaked process in a temporary directory; killing one costs another
        worker its test.
        """
        if pid_str in cls._test_spawned_pids:
            return True
        home = cls._daemon_home(pid_str)
        runtime = os.environ.get("AUTORUN_TEST_RUNTIME_DIR")
        if not home or not runtime:
            return False
        try:
            runtime = os.path.realpath(runtime)
            return os.path.commonpath([os.path.realpath(home), runtime]) == runtime
        except ValueError:  # different drives on Windows, or a relative path
            return False

    @classmethod
    def get_test_daemon_pids(cls) -> list:
        """Daemons this worker is responsible for.

        Excludes production daemons recorded at session start AND daemons
        belonging to the other xdist workers. `pytest_sessionstart` runs once
        per worker process, so each snapshot is blind to every daemon another
        worker starts later; without the ownership test the first worker to
        sweep terminated the others' daemons mid-test.
        """
        all_pids = set(cls._get_all_daemon_pids()) - cls._production_pids
        return sorted(pid for pid in all_pids if cls._is_ours(pid))

    @classmethod
    def _kill_pid(cls, pid_str: str):
        """Kill a process by PID string. Cross-platform via psutil."""
        try:
            proc = psutil.Process(int(pid_str))
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
            pass

    @classmethod
    def kill_test_daemons(cls):
        """Kill only test-spawned daemon processes. Never touches production PIDs."""
        test_pids = cls.get_test_daemon_pids()
        for pid in test_pids:
            cls._kill_pid(pid)
        if test_pids:
            time.sleep(0.3)
            cls._test_spawned_pids -= set(test_pids)

    @classmethod
    def spawn_test_daemon(cls):
        """Start a test daemon and track its PID.

        Returns the PID of the spawned daemon, or None on failure.
        """
        before = set(cls._get_all_daemon_pids())
        try:
            subprocess.run(
                [sys.executable, "-m", "autorun", "--restart-daemon"],
                capture_output=True, timeout=30
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        time.sleep(0.5)
        after = set(cls._get_all_daemon_pids())
        new_pids = after - before
        cls._test_spawned_pids.update(new_pids)
        return next(iter(new_pids), None)

    @classmethod
    def verify_daemon_count(cls) -> tuple:
        """Check test-spawned daemon count.

        Returns:
            (test_pids, production_pids) — both as lists
        """
        return cls.get_test_daemon_pids(), sorted(cls._production_pids)

    @classmethod
    def cleanup(cls):
        """Kill all test-spawned daemons. Called at session end.

        Idempotent — safe to call multiple times.
        """
        cls.kill_test_daemons()
        cls._test_spawned_pids.clear()

    @classmethod
    def assert_daemon_count(cls, max_test_daemons: int = 1):
        """Assert test daemon count is within limits. Cleans extras first.

        Returns (test_pids, production_pids) for diagnostics.
        Use this in tests instead of raw pgrep/kill calls.
        """
        # Kill extras first, keep oldest
        test_pids = cls.get_test_daemon_pids()
        if len(test_pids) > max_test_daemons:
            for pid in test_pids[max_test_daemons:]:
                cls._kill_pid(pid)
            time.sleep(0.3)
            test_pids = cls.get_test_daemon_pids()

        prod_pids = sorted(cls._production_pids & set(cls._get_all_daemon_pids()))

        if len(test_pids) > max_test_daemons:
            pytest.fail(
                f"Too many test daemons ({len(test_pids)}): {test_pids}. "
                f"Production daemons ({len(prod_pids)}): {prod_pids}."
            )

        return test_pids, prod_pids


@pytest.fixture(scope="session")
def test_timeout():
    """Default timeout for test operations."""
    return 10.0


@pytest.fixture(scope="session")
def stress_test_timeout():
    """Extended timeout for stress tests."""
    return 60.0



def should_keep_test_artifacts():
    """Check if test artifacts should be kept for debugging.

    Set AUTORUN_KEEP_TEST_ARTIFACTS=true to keep all test artifacts.
    """
    value = os.getenv("AUTORUN_KEEP_TEST_ARTIFACTS", "false").lower().strip()
    return value in {"true", "1", "yes", "on", "enabled"}


# Global registry to track session IDs created during tests
_test_session_ids = set()


def register_test_session(session_id: str):
    """Register a session ID for cleanup after tests."""
    _test_session_ids.add(session_id)


def cleanup_test_sessions():
    """Clean up all registered test sessions.

    This removes database files created during tests.
    Skipped if AUTORUN_KEEP_TEST_ARTIFACTS is set.
    """
    if should_keep_test_artifacts():
        print(f"\n[DEBUG] Keeping {len(_test_session_ids)} test session artifacts for debugging")
        return

    # The suite redirects every persistence backend here before imports.  The
    # cleanup path must use that same authority; consulting Path.home() can
    # delete an unrelated live session that happens to share a generated ID.
    state_dir = Path(os.environ["AUTORUN_TEST_STATE_DIR"])
    if not state_dir.exists():
        return

    cleaned = 0
    for session_id in _test_session_ids:
        # Direct file removal with known shelve suffixes (no glob — slow with 10K+ files)
        for prefix in [session_id, f"test_backend_{session_id}", f"test_dumbdbm_{session_id}",
                       f"plugin_{session_id}.db", f"plugin_{session_id}_dumb.db"]:
            base = str(state_dir / prefix)
            for suffix in ["", ".db", ".dir", ".bak", ".dat"]:
                try:
                    os.remove(base + suffix)
                    cleaned += 1
                except OSError:
                    pass

    _test_session_ids.clear()
    if cleaned > 0 and os.getenv("DEBUG", "").lower() in {"true", "1", "yes"}:
        print(f"\n[DEBUG] Cleaned up {cleaned} test session files")


#: Session ids that more than one test writes, so no test owns what it finds
#: there. `__global__` is the reserved id `ScopeAccessor(ctx, "global")` writes
#: to, which a unique per-test session id cannot isolate; the rest are literals
#: several modules happen to share -- `"test"` alone appears in 137 contexts
#: across six files. All are reset between tests below, and
#: `test_suite_harness.py` fails if a new one appears unlisted.
SHARED_TEST_SESSIONS = ("__global__", "test", "test-session", "session-1", "session-a")


@pytest.fixture(autouse=True)
def reset_shared_sessions_between_tests():
    """State written to a shared session id outlives the test that wrote it.

    `/ar:globalok 'git push'` is global by design: it is stored against
    `__global__`, not the caller's session, so every later test in the same
    worker saw git push allowed. `test_globalok_adds_to_global_allowed_patterns`
    left exactly that behind, and under `-n 8` it landed before
    `test_scoped_allow_uses_shared_wrapper_detection`, whose first assertion is
    that an ungranted `git push` is denied. The literal `"test"` did the same to
    `test_redirect_shown_in_message`, which asserts `rm` is blocked *with* its
    trash redirect and instead got a plain block from a leftover session block.

    Serially both orders happened to be benign, so the suite was green and the
    leaks invisible; only redistributing the tests exposed them.

    Reset here rather than in each test that writes shared state, because the
    tests that leak are the ones that forgot — a rule every test must remember
    is the rule that produced this. The clear is skipped unless the session
    actually holds something, which is the case for all but a handful.
    """
    yield

    from autorun.session_manager import (
        clear_test_session_states_batch,
        get_session_manager,
    )

    # One index read, then one batched delete only when something is there.
    # Opening each shared session in turn instead cost 7ms a test -- 43s over
    # the suite, doubling the parallel run -- because every open takes the
    # store's lock. Almost every test leaves all five untouched, so the common
    # path has to be a single query that finds nothing.
    try:
        occupied = [
            session
            for session in get_session_manager().list_sessions()
            if session in SHARED_TEST_SESSIONS
        ]
    except Exception:
        return
    if occupied:
        clear_test_session_states_batch(occupied)


@pytest.fixture
def temp_session_dir():
    """Create a temporary directory for session storage"""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    if not should_keep_test_artifacts():
        shutil.rmtree(temp_dir, ignore_errors=True)
    else:
        print(f"\n[DEBUG] Keeping temp session dir: {temp_dir}")


@pytest.fixture
def mock_session_state(temp_session_dir):
    """Create a mock session state for testing"""
    # Simple fixture that doesn't try to patch STATE_DIR
    # This avoids the AttributeError with function objects
    yield temp_session_dir


@pytest.fixture
def unique_session_id():
    """Generate a unique session ID for testing and register it for cleanup.

    Usage:
        def test_something(unique_session_id):
            session_id = unique_session_id()
            # Use session_id in test...
    """
    created_ids = []

    def _generate():
        session_id = f"test_session_{uuid.uuid4().hex[:8]}"
        created_ids.append(session_id)
        register_test_session(session_id)
        return session_id

    yield _generate

    # Cleanup specific to this test (no glob — slow with 10K+ files)
    if not should_keep_test_artifacts():
        state_dir = Path(os.environ["AUTORUN_TEST_STATE_DIR"])
        if state_dir.exists():
            for session_id in created_ids:
                for prefix in [session_id, f"test_backend_{session_id}", f"test_dumbdbm_{session_id}",
                               f"plugin_{session_id}.db", f"plugin_{session_id}_dumb.db"]:
                    base = str(state_dir / prefix)
                    for suffix in ["", ".db", ".dir", ".bak", ".dat"]:
                        try:
                            os.remove(base + suffix)
                        except OSError:
                            pass


# =============================================================================
# PYTEST SESSION HOOKS (daemon + session lifecycle)
# =============================================================================

# --- live-install canary -----------------------------------------------------
#
# AUTORUN_HOME and AUTORUN_TEST_STATE_DIR redirect state, but nothing redirects
# the *installed copy* of autorun: the plugin cache Claude Code loads, the
# harness settings that point at it, and the shared marketplace registry. A
# test that shells out to an installer, or resolves a path from the real home
# instead of its fixture, edits the user's working install -- and the suite
# still passes, because no assertion looks there.
#
# On 2026-08-11 a plugin cache venv lost its packages during install- and
# daemon-path testing. A later audit found a concrete deletion path: cache
# fallback replaced an existing version while intentionally omitting ``.venv``.
# No retained registration log proves that branch fired during the incident, so
# this canary preserves the missing runtime evidence for any recurrence. These
# are installed artifacts; a test may write them only with the explicit opt-out.
#
# Only code and configuration are fingerprinted. Sockets, PID files, logs, and
# databases under ~/.autorun are excluded: the user's own daemon writes those
# while the suite runs, and a canary with false positives gets deleted.
_LIVE_INSTALL_GLOBS = (
    "~/.claude/plugins/cache/autorun/*/*/hooks/hook_entry.py",
    "~/.claude/plugins/cache/autorun/*/*/pyproject.toml",
    "~/.claude/plugins/cache/autorun/*/*/.venv/pyvenv.cfg",
    "~/.claude/plugins/cache/autorun/*/*/.venv/**/site-packages/autorun/**/*.py",
    "~/.claude/plugins/cache/autorun/*/*/.venv/**/site-packages/filelock/**/*.py",
    "~/.claude/settings.json",
    "~/.codex/hooks.json",
    "~/.agents/plugins/marketplace.json",
)


def _live_install_digest(path: str) -> str:
    """Hash a file's bytes, chunked so a large artifact cannot exhaust memory."""
    import hashlib

    # digest_size=16 keeps the "before -> after" report readable. Collisions are
    # not an adversarial concern here; the writer is our own test suite.
    digest = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _live_install_fingerprint() -> dict:
    """Map each installed artifact to (size, digest); missing files map to None.

    Content, not mtime. Windows refreshes a file's last-write time on the system
    timer tick (~15.6 ms by default), so an edit landing in the same tick as the
    previous stat carries an identical ``st_mtime_ns``. A same-size in-place
    rewrite was therefore invisible to this canary on Windows -- the precise
    blind spot it exists to close, since an installer overwriting a file in
    place is the incident shape in the comment above. Hashing costs 6 ms across
    the 34 files these globs match, against 162 ms for the glob itself.
    """
    import glob as _glob

    fingerprint = {}
    for pattern in _LIVE_INSTALL_GLOBS:
        expanded = os.path.expanduser(pattern)
        matches = _glob.glob(expanded, recursive=True)
        # normpath, because these keys are compared against paths built
        # elsewhere. expanduser substitutes a backslash home on Windows but
        # leaves the rest of the pattern's forward slashes, so a match comes
        # back as C:\Users\...\.claude/settings.json -- equal to no str(Path)
        # any caller can produce, while POSIX matches by coincidence.
        #
        # Record the pattern itself when nothing matches, so an artifact that
        # is *deleted* during the run is caught, not just one that is edited.
        if not matches:
            fingerprint[os.path.normpath(expanded)] = None
            continue
        for path in matches:
            key = os.path.normpath(path)
            try:
                fingerprint[key] = (os.stat(path).st_size, _live_install_digest(path))
            except OSError:
                fingerprint[key] = None
    return fingerprint


def _live_install_canary_enabled(config) -> bool:
    """Protect the live install unless a deliberate run explicitly opts out."""
    return os.environ.get("AUTORUN_ALLOW_LIVE_INSTALL_WRITES") != "1"


def pytest_sessionstart(session):
    """Record production daemon PIDs and the live install before any tests run."""
    DaemonManager.snapshot_production_pids()
    if _live_install_canary_enabled(session.config):
        session.config._autorun_live_install = _live_install_fingerprint()


def _check_live_install_unchanged(session) -> None:
    """Report installed artifacts the suite modified, created, or deleted."""
    before = getattr(session.config, "_autorun_live_install", None)
    if before is None:
        return
    after = _live_install_fingerprint()
    changed = sorted(
        path for path in set(before) | set(after) if before.get(path) != after.get(path)
    )
    if not changed:
        return
    detail = "\n".join(
        f"  {path}: {before.get(path)!r} -> {after.get(path)!r}" for path in changed
    )
    # Written to the terminal rather than raised: pytest_sessionfinish runs
    # after reporting, so an exception here would be swallowed. The non-zero
    # exit status is what makes CI notice.
    print(
        "\n"
        "=========================== LIVE INSTALL MODIFIED ===========================\n"
        "The test suite changed autorun's installed copy. State isolation covers\n"
        "AUTORUN_HOME and AUTORUN_TEST_STATE_DIR; it does not cover the installed\n"
        "plugin, so a test that shells out to an installer or resolves a path from\n"
        "the real home edits the user's working install and still passes.\n\n"
        f"{detail}\n\n"
        "Find the test that writes here and give it a sandbox. If a test must\n"
        "install for real, mark it e2e so the default selection skips it, or set\n"
        "AUTORUN_ALLOW_LIVE_INSTALL_WRITES=1 for a deliberate run.\n"
        "============================================================================="
    )
    session.exitstatus = 1


def pytest_sessionfinish(session, exitstatus):
    """Clean up test sessions and test-spawned daemons after pytest finishes."""
    _check_live_install_unchanged(session)
    cleanup_test_sessions()
    DaemonManager.cleanup()

    test_tmux_socket = getattr(session.config, "_autorun_test_tmux_socket", None)
    owns_tmux_server = getattr(session.config, "_autorun_owns_test_tmux_server", False)
    if test_tmux_socket and owns_tmux_server and not should_keep_test_artifacts():
        tmux_bin = getattr(session.config, "_autorun_real_tmux_bin", "tmux")
        try:
            subprocess.run(
                [tmux_bin, "-S", test_tmux_socket, "kill-server"],
                capture_output=True,
                text=True,
                timeout=5,
                env={key: value for key, value in os.environ.items() if key != "TMUX"},
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        # `kill-server` removes the socket only when a server was listening on
        # it, and most runs never start one -- the file is created by the first
        # client that probes it. Left behind, one accumulates per run in the
        # user's temporary directory forever.
        try:
            Path(test_tmux_socket).unlink(missing_ok=True)
        except OSError:
            pass

    # Clean up isolated state, socket, PID, and logs together.
    test_runtime_dir = getattr(session.config, '_autorun_test_runtime_dir', None)
    if test_runtime_dir and os.path.isdir(test_runtime_dir):
        if not should_keep_test_artifacts():
            shutil.rmtree(test_runtime_dir, ignore_errors=True)
        else:
            print(f"\n[DEBUG] Keeping test runtime dir: {test_runtime_dir}")

    # Remove env var and reset singletons so production code isn't affected
    os.environ.pop("AUTORUN_TEST_STATE_DIR", None)
    os.environ.pop("AUTORUN_HOME", None)
    os.environ.pop("AUTORUN_TEST_RUNTIME_DIR", None)
    os.environ.pop("AUTORUN_TEST_TMUX_SOCKET", None)
    os.environ.pop("AUTORUN_ORIGINAL_TMUX", None)
    os.environ.pop("AUTORUN_TMUX_BIN", None)
    import autorun.session_manager as sm
    sm._store = None
    sm._manager = None


@pytest.fixture(scope="session")
def ensure_single_daemon():
    """Session-scoped fixture that ensures a test daemon is running.

    Uses DaemonManager to:
    - Kill only test-spawned daemons (never production ones)
    - Start a fresh test daemon
    - Clean up on teardown

    Tests that need a daemon should depend on this fixture.
    """
    # Kill any test-spawned daemons from previous runs
    DaemonManager.kill_test_daemons()

    # Spawn a fresh test daemon
    DaemonManager.spawn_test_daemon()

    yield

    # Cleanup test-spawned daemons
    DaemonManager.kill_test_daemons()


@pytest.fixture
def daemon_manager():
    """Per-test access to DaemonManager for daemon lifecycle operations.

    Usage:
        def test_daemon_count(daemon_manager):
            test_pids, prod_pids = daemon_manager.verify_daemon_count()
            assert len(test_pids) <= 1
    """
    return DaemonManager


@pytest.fixture
def mock_session_state_factory():
    """Factory for creating mock session states with configurable defaults.

    Usage:
        def test_something(mock_session_state_factory):
            state = mock_session_state_factory(policy="SEARCH", status="active")
            # Use state in test...

    Reduces mock duplication across tests by providing a single factory.
    """
    def _create(policy="ALLOW", status="inactive", stage="INITIAL", **extra):
        state = {
            "file_policy": policy,
            "session_status": status,
            "autorun_stage": stage,
            "activation_prompt": "",
            "recheck_count": 0,
        }
        state.update(extra)
        return state

    return _create


@pytest.fixture
def policy_responses():
    """Expected policy response strings - generated from CONFIG."""
    return {
        policy: f"AutoFile policy: {CONFIG['policies'][policy][0]} - {CONFIG['policies'][policy][1]}"
        for policy in ["SEARCH", "ALLOW", "JUSTIFY"]
    }


@pytest.fixture
def sample_commands():
    """Sample commands for testing"""
    return {
        "policy_commands": ["/afs", "/afa", "/afj", "/afst"],
        "control_commands": ["/autostop", "/estop"],
        "normal_commands": ["help me", "what is this", "test file"],
        "autorun_command": "/autorun test task description"
    }


@pytest.fixture
def expected_responses():
    """Expected responses for commands - generated from CONFIG."""
    return {
        "/afs": f"AutoFile policy: {CONFIG['policies']['SEARCH'][0]} - {CONFIG['policies']['SEARCH'][1]}",
        "/afa": f"AutoFile policy: {CONFIG['policies']['ALLOW'][0]} - {CONFIG['policies']['ALLOW'][1]}",
        "/afj": f"AutoFile policy: {CONFIG['policies']['JUSTIFY'][0]} - {CONFIG['policies']['JUSTIFY'][1]}",
        "/afst": f"Current policy: {CONFIG['policies']['ALLOW'][0]}",
        "/autostop": "Autorun stopped",
        "/estop": "Emergency stop activated"
    }


@pytest.fixture
def plugin_input_data():
    """Sample input data for plugin testing"""
    return {
        "prompt": "/afs",
        "session_id": "test_session",
        "session_transcript": []
    }


@pytest.fixture
def hook_input_data():
    """Sample input data for hook testing"""
    return {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "test_session",
        "prompt": "/afa",
        "session_transcript": []
    }
