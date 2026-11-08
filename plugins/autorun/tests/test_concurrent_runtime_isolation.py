"""Process-level proof that two autorun runtimes stay independent under load.

This deliberately does not use the test-process IPC monkeypatches.  Each side
is a real daemon process with its own HOME, AUTORUN_HOME, state database, and
endpoint.  The TCP case removes ``socket.AF_UNIX`` in the child so the same
test also exercises the loopback token protocol on platforms that support
Unix-domain sockets.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import time

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HOOK_ENTRY = PLUGIN_ROOT / "hooks" / "hook_entry.py"
SESSION_ID = "same-session-in-two-runtimes"
RUNTIME_NAMES = ("live-standin", "dev-standin")


def _clean_runtime_env(runtime: dict[str, Path]) -> dict[str, str]:
    """Return an environment with inherited harness/runtime selectors removed."""
    env = dict(os.environ)
    prefixes = (
        "AUTORUN_",
        "CLAUDE_",
        "CODEX_",
        "GEMINI_",
        "QWEN_",
        "PI_",
        "PRIME_",
        "FORGE_",
        "OPENCODE_",
        "ANTIGRAVITY_",
        "AGY_",
    )
    for key in tuple(env):
        if key.startswith(prefixes) or key in {
            "UV_PYTHON",
            "PYTHONHOME",
            "PYTHONPATH",
            "TMUX",
            "XDG_CONFIG_HOME",
        }:
            del env[key]

    env.update(
        {
            "HOME": str(runtime["home"]),
            "USERPROFILE": str(runtime["home"]),
            "PI_CODING_AGENT_DIR": str(runtime["home"] / ".pi" / "agent"),
            "AUTORUN_HOME": str(runtime["autorun"]),
            "AUTORUN_TEST_STATE_DIR": str(runtime["state"]),
            # The hook must not discover a globally installed live autorun.
            "PATH": "/usr/bin:/bin",
        }
    )
    return env


def _new_runtimes(base: Path) -> dict[str, dict[str, Path]]:
    runtimes = {}
    for name in RUNTIME_NAMES:
        root = base / name
        runtime = {
            "root": root,
            "home": root / "home",
            "autorun": root / "ar-home",
            "state": root / "state",
        }
        for path in runtime.values():
            path.mkdir(parents=True, exist_ok=True)
        runtimes[name] = runtime
    return runtimes


def _seed_state(runtime: dict[str, Path], *, allow: bool) -> None:
    code = """
from autorun.scoped_allow import ScopedAllow
from autorun.session_manager import session_state
import time

with session_state("same-session-in-two-runtimes") as state:
    if @@ALLOW@@:
        state["session_allowed_patterns"] = [
            ScopedAllow(
                pattern=pattern,
                pattern_type="literal",
                granted_at=time.time(),
                remaining_uses=None,
            ).to_dict()
            for pattern in ("printf", "rm", "rm -rf")
        ]
    else:
        state["session_blocked_patterns"] = [
            {
                "pattern": "printf",
                "pattern_type": "literal",
                "suggestion": "stand-in live runtime block",
            }
        ]
""".replace("@@ALLOW@@", repr(allow))
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=runtime["home"],
        env=_clean_runtime_env(runtime),
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _lifecycle_paths(runtime: dict[str, Path], *, tcp: bool) -> tuple[Path, ...]:
    paths = (runtime["autorun"] / "daemon.lock", runtime["autorun"] / "daemon.flock")
    if tcp:
        return paths + (runtime["autorun"] / "daemon.port", runtime["autorun"] / "daemon.token")
    return paths + (runtime["autorun"] / "daemon.sock",)


def _fingerprint(paths: tuple[Path, ...]) -> dict[str, dict[str, object] | None]:
    result: dict[str, dict[str, object] | None] = {}
    for path in paths:
        try:
            metadata = path.stat()
            # The flock file's identity is its inode, and Windows refuses to
            # read a byte range another process holds locked, so it is
            # compared by stat alone, like the socket.
            digest = (
                hashlib.sha256(path.read_bytes()).hexdigest()
                if stat.S_ISREG(metadata.st_mode) and path.name != "daemon.flock"
                else None
            )
            result[str(path)] = {
                "mtime_ns": metadata.st_mtime_ns,
                "size": metadata.st_size,
                "inode": metadata.st_ino,
                "sha256": digest,
            }
        except FileNotFoundError:
            result[str(path)] = None
    return result


def _endpoint(runtime: dict[str, Path], *, tcp: bool) -> tuple[int, object, bytes]:
    if tcp:
        port = int((runtime["autorun"] / "daemon.port").read_text().strip())
        token = (runtime["autorun"] / "daemon.token").read_text().strip().encode("ascii")
        return socket.AF_INET, ("127.0.0.1", port), token + b"\n"
    return socket.AF_UNIX, str(runtime["autorun"] / "daemon.sock"), b""


def _round_trip(
    runtime: dict[str, Path], payload: dict, *, tcp: bool, timeout: float = 10.0
) -> dict:
    family, address, preamble = _endpoint(runtime, tcp=tcp)
    with socket.socket(family, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(address)
        client.sendall(preamble + json.dumps(payload).encode("utf-8") + b"\n")
        with client.makefile("rb") as response_stream:
            line = response_stream.readline()
    result = json.loads(line or b"null")
    assert isinstance(result, dict), result
    return result


async def _open_async_connection(
    runtime: dict[str, Path], *, tcp: bool
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, bytes]:
    if tcp:
        _family, address, preamble = _endpoint(runtime, tcp=True)
        reader, writer = await asyncio.open_connection(*address)
    else:
        _family, address, preamble = _endpoint(runtime, tcp=False)
        reader, writer = await asyncio.open_unix_connection(address)
    return reader, writer, preamble


async def _concurrent_denials(runtime: dict[str, Path], *, tcp: bool) -> list[tuple[str, str]]:
    """Launch 200 distinct clients at one async barrier under the hook budget.

    Connections are established in batches below the kernel listen backlog,
    then all 200 already-connected clients send their requests at one barrier.
    This tests 200 concurrent handler requests without making a platform queue
    overflow itself the verdict.
    """
    from autorun.platforms import PLATFORMS

    count = 200
    ready = 0
    ready_lock = asyncio.Lock()
    release = asyncio.Event()
    # macOS commonly clamps listen backlog to kern.ipc.somaxconn=128. Derive a
    # safe connection-establishment batch from the host's advertised limit;
    # request concurrency remains the full 200 after all sockets are live.
    connect_limit = max(1, socket.SOMAXCONN // 2)
    connect_sem = asyncio.Semaphore(connect_limit)
    tool_name = PLATFORMS["claude"].tool_names["bash"]
    timeout = _wrapper_timeout("claude")

    async def deny_one(index: int) -> tuple[str, str]:
        nonlocal ready
        payload = _request_payload(
            runtime,
            cli_type="claude",
            session_id=f"concurrent-session-{index}",
            command="rm /tmp/standin-concurrent-target",
            tool_name=tool_name,
        )
        reader = writer = None
        started = time.monotonic()
        try:
            async with connect_sem:
                reader, writer, preamble = await asyncio.wait_for(
                    _open_async_connection(runtime, tcp=tcp), timeout=timeout
                )
            async with ready_lock:
                ready += 1
                if ready == count:
                    release.set()
            await release.wait()
            writer.write(preamble + json.dumps(payload).encode("utf-8") + b"\n")
            await writer.drain()
            remaining = max(0.0, timeout - (time.monotonic() - started))
            line = await asyncio.wait_for(reader.readline(), timeout=remaining)
            response = json.loads(line or b"null")
            assert isinstance(response, dict), response
            return _decision_reason(response)
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass

    return await asyncio.gather(*(deny_one(index) for index in range(count)))


def _wait_ready(runtime: dict[str, Path], *, tcp: bool, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 15
    probe = {"_autorun_round_trip_probe": True}
    while time.monotonic() < deadline:
        if process.poll() is not None:
            log = runtime["root"] / "daemon.log"
            detail = log.read_text(errors="replace") if log.exists() else ""
            raise AssertionError(f"daemon exited with {process.returncode}: {detail}")
        try:
            if _round_trip(runtime, probe, tcp=tcp, timeout=0.5) == probe:
                return
        except (FileNotFoundError, ConnectionError, OSError, TimeoutError, ValueError):
            time.sleep(0.02)
    output = runtime["root"] / "daemon-output.log"
    raise AssertionError(output.read_text(errors="replace") if output.exists() else "daemon did not start")


def _start_daemon(runtime: dict[str, Path], *, tcp: bool) -> subprocess.Popen[str]:
    code = (
        "from autorun import ipc; "
        + ("ipc.HAS_UNIX_SOCKETS = False; " if tcp else "")
        + "from autorun.daemon import main; main()"
    )
    output_path = runtime["root"] / "daemon-output.log"
    output = output_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=runtime["home"],
        env=_clean_runtime_env(runtime),
        stdout=output,
        stderr=subprocess.STDOUT,
        text=True,
    )
    # The descriptor is inherited by the child; closing our copy is safe.
    output.close()
    try:
        _wait_ready(runtime, tcp=tcp, process=process)
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        raise
    return process


def _failure_diagnostics(
    runtimes: dict[str, dict[str, Path]], processes: dict[str, subprocess.Popen[str]]
) -> str:
    """Capture bounded daemon identity/output before the fixture removes roots."""
    details = []
    for name in RUNTIME_NAMES:
        process = processes.get(name)
        output_path = runtimes[name]["root"] / "daemon-output.log"
        try:
            tail = "\n".join(output_path.read_text(errors="replace").splitlines()[-40:])[-4000:]
        except OSError as error:
            tail = f"<log unavailable: {error!r}>"
        details.append(
            f"{name}: pid={process.pid if process else None} "
            f"poll={process.poll() if process else None}\n{tail}"
        )
    return "\n\n".join(details)


def _wrapper_timeout(cli_type: str) -> float:
    from autorun.config import CONFIG

    return float(CONFIG["hook_wrapper_timeouts_seconds"][cli_type])


def _request_payload(
    runtime: dict[str, Path], *, cli_type: str, session_id: str, command: str, tool_name: str
) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_input": {"command": command},
        "tool_result": {},
        "prompt": "",
        "session_transcript": [],
        "cwd": str(runtime["home"]),
        "_cwd": str(runtime["home"]),
        "cli_type": cli_type,
        "_pid": os.getpid(),
        "_autorun_hook_deadline_epoch": time.time() + _wrapper_timeout(cli_type),
    }


def _payload(runtime: dict[str, Path], command: str) -> dict:
    from autorun.platforms import PLATFORMS

    return _request_payload(
        runtime,
        cli_type="claude",
        session_id=SESSION_ID,
        command=command,
        tool_name=PLATFORMS["claude"].tool_names["bash"],
    )


def _decision_reason(response: dict) -> tuple[str, str]:
    nested = response.get("hookSpecificOutput")
    if isinstance(nested, dict):
        decision = nested.get("permissionDecision") or nested.get("decision")
        reason = nested.get("permissionDecisionReason") or nested.get("reason") or ""
    else:
        decision = response.get("permissionDecision") or response.get("decision")
        reason = response.get("reason") or ""
    return str(decision or "allow"), str(reason)


def _decision(response: dict) -> str:
    return _decision_reason(response)[0]


def _run_hook(
    runtime: dict[str, Path], *, platform_name: str, tool_name: str, command_text: str, tcp: bool
) -> subprocess.CompletedProcess[str]:
    payload = json.dumps(
        _request_payload(
            runtime,
            cli_type=platform_name,
            session_id=f"hook-{platform_name}-{command_text.split()[0]}",
            command=command_text,
            tool_name=tool_name,
        )
    )
    # Load hook_entry without triggering its __main__ block, then forbid both
    # recovery routes. A successful result therefore proves this subprocess
    # reached the live-like daemon over the selected transport, rather than
    # silently succeeding through a CLI/direct-import fallback.
    # Windows Python may have no AF_UNIX at all; TCP is then already forced.
    transport_patch = "hasattr(socket, 'AF_UNIX') and delattr(socket, 'AF_UNIX'); " if tcp else ""
    runner = (
        "import runpy, socket, sys; "
        + transport_patch
        + f"sys.argv = ['hook_entry.py', '--cli', {platform_name!r}]; "
        + f"ns = runpy.run_path({str(HOOK_ENTRY)!r}, run_name='autorun_hook_probe'); "
        + "forbid = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('hook fallback used')); "
        + "ns['try_cli'] = forbid; ns['run_fallback'] = forbid; ns['main']()"
    )
    command = [sys.executable, "-c", runner]
    return subprocess.run(
        command,
        cwd=runtime["home"],
        env=_clean_runtime_env(runtime),
        input=payload,
        capture_output=True,
        text=True,
        timeout=_wrapper_timeout(platform_name),
    )


def _hook_decision(result: subprocess.CompletedProcess[str]) -> tuple[str, str]:
    """Extract one platform-neutral decision and reason from a hook process."""
    if result.returncode == 2:
        return "deny", result.stderr.strip()
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert result.stderr == "", result.stderr
    response = json.loads(result.stdout) if result.stdout.strip() else {}
    assert isinstance(response, dict), response
    nested = response.get("hookSpecificOutput")
    if isinstance(nested, dict):
        decision = nested.get("permissionDecision") or nested.get("decision")
        reason = nested.get("permissionDecisionReason") or nested.get("reason") or ""
    else:
        decision = response.get("permissionDecision") or response.get("decision")
        reason = response.get("reason") or ""
    return str(decision or "allow"), str(reason)


@pytest.mark.parametrize("tcp", [False, True], ids=["unix", "tcp"])
def test_two_daemons_and_two_homes_are_independent_under_200_clients(tcp: bool):
    """A live stand-in survives a concurrent dev daemon and all hook routes."""
    if not tcp and not hasattr(socket, "AF_UNIX"):
        # The forced TCP code path is still covered by the platform's normal
        # Windows CI; this host cannot create the Unix side of the pair.
        pytest.skip("the host has no Unix sockets for the Unix transport run")

    with tempfile.TemporaryDirectory(prefix="ari.", dir="/tmp" if os.name != "nt" else None) as directory:
        base = Path(directory)
        runtimes = _new_runtimes(base)
        _seed_state(runtimes["live-standin"], allow=False)
        _seed_state(runtimes["dev-standin"], allow=True)
        processes: dict[str, subprocess.Popen[str]] = {}
        try:
            for name, runtime in runtimes.items():
                processes[name] = _start_daemon(runtime, tcp=tcp)

            paths = {
                name: _lifecycle_paths(runtime, tcp=tcp)
                for name, runtime in runtimes.items()
            }
            before = {name: _fingerprint(paths[name]) for name in RUNTIME_NAMES}
            assert all(all(entry is not None for entry in snapshot.values()) for snapshot in before.values())
            assert all(process.poll() is None for process in processes.values())

            if tcp:
                # A token from one sandbox must not authenticate against the
                # other sandbox's loopback endpoint, even while both daemons
                # are serving requests.
                for source_name, destination_name in (
                    ("live-standin", "dev-standin"),
                    ("dev-standin", "live-standin"),
                ):
                    _, destination, _ = _endpoint(runtimes[destination_name], tcp=True)
                    _, _source_address, source_token = _endpoint(runtimes[source_name], tcp=True)
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                        client.settimeout(_wrapper_timeout("claude"))
                        client.connect(destination)
                        client.sendall(source_token + json.dumps({"_autorun_round_trip_probe": True}).encode() + b"\n")
                        assert client.makefile("rb").readline() == b""
                assert all(process.poll() is None for process in processes.values())

            from autorun.platforms import PLATFORMS

            hooked_platforms = tuple(platform for platform in PLATFORMS.values() if platform.has_hooks)
            unhooked_platforms = tuple(platform for platform in PLATFORMS.values() if not platform.has_hooks)
            assert hooked_platforms
            assert unhooked_platforms
            for platform in unhooked_platforms:
                assert platform.schema_type == "none"
                assert platform.hook_protocol.name == "none"
                assert "advisory" in platform.command_invocation_hint.lower()

            # Launch exactly 200 distinct-session clients at the same barrier
            # on each runtime. Every ungranted session must receive a concrete
            # deny and a non-empty reason for the native shell command.
            for runtime in runtimes.values():
                decisions = asyncio.run(_concurrent_denials(runtime, tcp=tcp))
                assert len(decisions) == 200
                assert all(decision in {"deny", "block"} for decision, _ in decisions)
                assert all(reason.strip() for _, reason in decisions)
                assert all("trash" in reason.lower() for _, reason in decisions)

            live = runtimes["live-standin"]
            dev = runtimes["dev-standin"]
            assert _decision(_round_trip(live, _payload(live, "printf hello"), tcp=tcp)) == "deny"
            assert _decision(_round_trip(dev, _payload(dev, "printf hello"), tcp=tcp)) != "deny"
            assert _decision(_round_trip(live, _payload(live, "rm -rf /tmp/standin"), tcp=tcp)) == "deny"
            assert _decision(_round_trip(dev, _payload(dev, "rm -rf /tmp/standin"), tcp=tcp)) != "deny"

            # Exercise each registry hook route through the real hook_entry
            # subprocess in both roots. Every native shell tool must deny an
            # ungranted rm with a reason and allow a harmless echo.
            for platform in hooked_platforms:
                tool_name = platform.tool_names["bash"]
                for runtime in runtimes.values():
                    denied = _run_hook(
                        runtime,
                        platform_name=platform.name,
                        tool_name=tool_name,
                        command_text="rm /tmp/hook-isolation-target",
                        tcp=tcp,
                    )
                    deny_decision, deny_reason = _hook_decision(denied)
                    assert deny_decision in {"deny", "block"}, (
                        platform.name,
                        denied.stdout,
                        denied.stderr,
                    )
                    assert deny_reason.strip(), (platform.name, denied.stdout, denied.stderr)
                    assert "trash" in deny_reason.lower(), (
                        platform.name,
                        denied.stdout,
                        denied.stderr,
                    )

                    allowed = _run_hook(
                        runtime,
                        platform_name=platform.name,
                        tool_name=tool_name,
                        command_text="echo hook-isolation-check",
                        tcp=tcp,
                    )
                    allow_decision, _allow_reason = _hook_decision(allowed)
                    assert allow_decision not in {"deny", "block"}, (
                        platform.name,
                        allowed.stdout,
                        allowed.stderr,
                    )

            after = {name: _fingerprint(paths[name]) for name in RUNTIME_NAMES}
            assert after == before
            assert all(processes[name].poll() is None for name in RUNTIME_NAMES)
            for name in RUNTIME_NAMES:
                assert processes[name].pid == int(runtimes[name]["autorun"].joinpath("daemon.lock").read_text().split()[0])
        except BaseException as error:
            diagnostics = _failure_diagnostics(runtimes, processes)
            raise AssertionError(
                "stand-in daemon failure diagnostics (cause unestablished):\n" + diagnostics
            ) from error
        finally:
            for name, process in processes.items():
                if process.poll() is None:
                    runtime = runtimes[name]
                    try:
                        _round_trip(
                            runtime,
                            {"_autorun_shutdown": process.pid},
                            tcp=tcp,
                            timeout=5,
                        )
                    except (ConnectionError, FileNotFoundError, OSError, TimeoutError, ValueError):
                        pass
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)

