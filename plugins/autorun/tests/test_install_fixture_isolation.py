"""Installer fixture helpers must reuse pytest's isolated runtime roots."""

from __future__ import annotations

def _assert_helper_keeps_runtime(before_home: str, before_state: str) -> None:
    """Assert a helper did not replace the worker-owned runtime paths."""
    import os

    after_home = os.environ["AUTORUN_HOME"]
    after_state = os.environ["AUTORUN_TEST_STATE_DIR"]
    assert after_home == before_home
    assert after_state == before_state


def test_pi_fixture_install_reuses_worker_runtime(monkeypatch, tmp_path):
    import os

    from test_pi_bridge import _install_pi

    before_home = os.environ["AUTORUN_HOME"]
    before_state = os.environ["AUTORUN_TEST_STATE_DIR"]
    _install_pi(tmp_path, monkeypatch)
    _assert_helper_keeps_runtime(before_home, before_state)


def test_prime_fixture_install_reuses_worker_runtime(monkeypatch, tmp_path):
    import os

    from test_pi_bridge import _install_prime

    before_home = os.environ["AUTORUN_HOME"]
    before_state = os.environ["AUTORUN_TEST_STATE_DIR"]
    _install_prime(tmp_path, monkeypatch)
    _assert_helper_keeps_runtime(before_home, before_state)


def test_opencode_fixture_install_reuses_worker_runtime(monkeypatch, tmp_path):
    import os

    from test_opencode_bridge import TestInstallerPlacesTheShim

    before_home = os.environ["AUTORUN_HOME"]
    before_state = os.environ["AUTORUN_TEST_STATE_DIR"]
    TestInstallerPlacesTheShim()._install(tmp_path, monkeypatch)
    _assert_helper_keeps_runtime(before_home, before_state)
