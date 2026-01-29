#!/usr/bin/env python3

# Copyright 2025 Andrew Hundt <ATHundt@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import subprocess as sp
import shelve
import time
import os
import sys
import signal
import logging
from pathlib import Path
from contextlib import contextmanager

# Import centralized tmux utilities for DRY compliance
from .tmux_utils import get_tmux_utilities

STATE_DIR = Path.home() / ".claude" / "sessions"
STATE_DIR.mkdir(parents=True, exist_ok=True)

def setup_clautorun_logging():
    """Setup clautorun logging with cross-user compatible location"""
    # Use cross-user compatible log directory - works regardless of installation location
    log_dir = Path.home() / ".claude" / "sessions"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Setup logging to file with clautorun prefix
    log_file = log_dir / "clautorun_ai_monitor.log"
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(process)d: %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stderr)  # Also log to stderr for visibility
        ]
    )

# Monitor state using shelve for persistence
@contextmanager
def monitor_state(session_id):
    s = shelve.open(str(STATE_DIR / f"monitor-{session_id}.db"), writeback=True)
    try:
        yield s
    finally:
        s.sync()
        s.close()

# Global tmux utilities instance
_tmux_utils = None

def get_tmux():
    """Get tmux utilities instance with proper session naming"""
    global _tmux_utils
    if _tmux_utils is None:
        _tmux_utils = get_tmux_utilities()  # Uses default "clautorun" session
    return _tmux_utils

# Window operations dispatch using centralized tmux utilities
def win_list(session):
    """List windows in session"""
    result = get_tmux().execute_tmux_command(['list-windows', '-t', session, '-F', '#{window_index}'])
    if result and result['returncode'] == 0:
        return [int(w) for w in result['stdout'].split() if w.strip().isdigit()]
    return []

def win_read(session, window):
    """Read window content"""
    result = get_tmux().execute_tmux_command(['capture-pane', '-t', f'{session}:{window}', '-p', '-S', '-100'])
    return result['stdout'] if result and result['returncode'] == 0 else ""

def win_send(session, window, text):
    """Send text to window"""
    # Send literal text first
    get_tmux().send_keys(text, session, window)
    time.sleep(0.1)
    # Send Enter
    return get_tmux().send_keys('C-m', session, window)

def win_own():
    """Get current session and window"""
    env_info = get_tmux().detect_tmux_environment()
    if env_info:
        return env_info["session"], int(env_info["window"])
    return None, None

# Window operations dispatch using centralized utilities
WIN_OPS = {
    'list': win_list,
    'read': win_read,
    'send': win_send,
    'own': win_own
}

# Monitor control functions (library interface)
def start_monitor(session_id, prompt="Continue working", stop_marker=None, max_cycles=5, prompt_on_start=False, start_window=None):
    """Start monitoring session for autonomous execution"""
    pf = Path(f"/tmp/ai-monitor-{session_id}.pid")
    if pf.exists():
        try:
            os.kill(int(pf.read_text()), 0)
            return int(pf.read_text())  # Already running
        except (OSError, ValueError):
            pf.unlink()

    # Spawn monitor as subprocess
    script = Path(__file__)
    pid = sp.Popen([sys.executable, str(script), session_id, '--prompt', prompt,
                    '--max-retry-cycles', str(max_cycles)] +
                   (['--prompt-on-start'] if prompt_on_start else []) +
                   (['--start', str(start_window)] if start_window else []) +
                   (['--stop', stop_marker] if stop_marker else []),
                   stdout=sp.DEVNULL, stderr=sp.DEVNULL).pid
    return pid

def stop_monitor(session_id):
    """Stop monitor for session"""
    pf = Path(f"/tmp/ai-monitor-{session_id}.pid")
    if pf.exists():
        try:
            os.kill(int(pf.read_text()), signal.SIGTERM)
            pf.unlink()
        except (OSError, ValueError):
            pass

def check_monitor(session_id):
    """Check if monitor is running, return PID or None"""
    pf = Path(f"/tmp/ai-monitor-{session_id}.pid")
    if pf.exists():
        try:
            pid = int(pf.read_text())
            os.kill(pid, 0)
            return pid
        except (OSError, ValueError):
            pf.unlink()
    return None

# Core monitor loop
def run_monitor(session_id, config):
    """Main monitoring loop"""
    pf = Path(f"/tmp/ai-monitor-{session_id}.pid")
    pf.write_text(str(os.getpid()))

    try:
        with monitor_state(session_id) as state:
            # Init
            if "start_time" not in state:
                state.update({"start_time": time.time(), "last_change": 0, "checks": 0,
                             "cycles": 0, "last_output": None})

            # Discover windows (exclude monitor's own) with graceful tmux handling
            own_sess, own_win = WIN_OPS['own']()

            # Setup logging for clautorun AI monitor
            setup_clautorun_logging()

            # Check if tmux is available and has windows
            if not own_sess or not WIN_OPS['list'](session_id):
                # No tmux session or no windows - run in degraded mode
                logging.info(f"clautorun AI monitor: tmux not available (session={own_sess}) - running in degraded mode")
                windows = {}
                state["windows"] = windows
            else:
                # tmux available - discover windows
                windows = {w: WIN_OPS['read'](session_id, w) for w in WIN_OPS['list'](session_id)
                          if not (session_id == own_sess and w == own_win)}
                windows = {w: out for w, out in windows.items() if out and '🤖[AI-MONITOR]🤖' not in out}

                if not windows:
                    logging.info("clautorun AI monitor: No tmux windows found - running in degraded mode")
                    windows = {}
                    state["windows"] = windows

            # Initial prompt
            if config.get("prompt_on_start"):
                targets = [int(x) for x in (config.get("start_window") or "").split(',') if x.strip().isdigit()]
                targets = [t for t in targets if t in windows] or [min(windows.keys())]
                for t in targets:
                    WIN_OPS['send'](session_id, t, config["prompt"])
                state["last_output"] = '\n'.join(windows.values())
                state["last_change"] = time.time() + config["interval"]

            # Monitor loop
            while True:
                time.sleep(config["interval"])

                # Check limits
                if (config["max_runtime"] > 0 and
                        time.time() - state["start_time"] > config["max_runtime"] * 60):
                    break

                # Scan windows
                changed = []
                all_content = ""
                for w in list(state["windows"].keys()):
                    curr = WIN_OPS['read'](session_id, w)
                    if not curr or '🤖[AI-MONITOR]🤖' in curr:
                        del state["windows"][w]
                        continue
                    all_content += curr
                    if curr != state["windows"][w]:
                        changed.append(w)
                        state["windows"][w] = curr

                # Check stop marker
                if config.get("stop_marker") and config.get("stop_marker") in all_content:
                    if time.time() - state["start_time"] > config.get("stop_delay", 300):
                        break

                # Detect meaningful changes (>100 chars to filter echo)
                if changed:
                    if not state["last_output"] or len(all_content) > len(state["last_output"]) + 100:
                        state["last_change"], state["checks"], state["cycles"] = time.time(), 0, 0
                        state["last_output"] = None
                    else:
                        state["checks"] += 1
                else:
                    state["checks"] += 1

                # Reprompt after 3 idle checks
                if state["checks"] >= 3:
                    if config["max_cycles"] > 0 and state["cycles"] >= config["max_cycles"]:
                        break
                    if state["windows"]:
                        WIN_OPS['send'](session_id, min(state["windows"].keys()), config["prompt"])
                        state["cycles"], state["checks"] = state["cycles"] + 1, 0
                        state["last_output"], state["last_change"] = '\n'.join(state["windows"].values()), time.time() + config["interval"]
    finally:
        pf.unlink(missing_ok=True)

# CLI argument dispatch
ARG_DISPATCH = {
    '--prompt': ('prompt', str), '-p': ('prompt', str),
    '--stop': ('stop_marker', str), '-s': ('stop_marker', str),
    '--stop-delay-seconds': ('stop_delay', int),
    '--max-retry-cycles': ('max_cycles', int), '-c': ('max_cycles', int),
    '--max-runtime-minutes': ('max_runtime', int),
    '--check-interval': ('interval', int),
}

def parse_cli():
    """Parse CLI args using dispatch dict"""
    config = {"prompt": "Continue working", "interval": 40, "max_cycles": 5, "max_runtime": 0,
              "prompt_on_start": False, "start_window": None, "stop_marker": None, "stop_delay": 300}
    session_id, args, i = None, sys.argv[1:], 0

    if '--help' in args or '-h' in args:
        print('ai-monitor.py [session] [options]\nOptions: --prompt,-p <txt> --prompt-on-start --start <win> --stop,-s <str>\n  --max-retry-cycles,-c <n> --max-runtime-minutes <n> --check-interval <n>')
        sys.exit(0)

    while i < len(args):
        arg = args[i]
        if arg == '--prompt-on-start':
            config["prompt_on_start"] = True
            if i + 1 < len(args) and not args[i + 1].startswith('-'):
                config["start_window"], i = args[i + 1], i + 2
            else:
                i += 1
        elif arg in ARG_DISPATCH:
            key, cast = ARG_DISPATCH[arg]
            config[key], i = cast(args[i + 1]) if i + 1 < len(args) else config[key], i + 2
        elif not session_id and not arg.startswith('-'):
            session_id, i = arg, i + 1
        else:
            i += 1

    if not session_id:
        own_sess, _ = WIN_OPS['own']()
        # Default to "clautorun" session as required by standards
        session_id = own_sess or get_tmux().DEFAULT_SESSION_NAME
        # Ensure session exists
        get_tmux().ensure_session_exists(session_id)

    return session_id, config

if __name__ == "__main__":
    sid, cfg = parse_cli()
    if sid:
        run_monitor(sid, cfg)