"""Compatibility import for the manifest-driven installer.

New code imports :mod:`autorun.installer.entrypoint`. This module remains only
because released scripts and the ``autorun-install`` console entry point name it.
"""

from .installer.entrypoint import (
    install_main,
    install_plugins,
    perform_self_update,
    show_status,
    uninstall_plugins,
)

__all__ = [
    "install_main",
    "install_plugins",
    "perform_self_update",
    "show_status",
    "uninstall_plugins",
]


if __name__ == "__main__":
    raise SystemExit(install_main())
