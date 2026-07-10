"""Build configuration that embeds plugin assets in the autorun wheel."""

import importlib.util
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

PLUGIN_ROOT = Path(__file__).resolve().parent
_SUPPORT_SPEC = importlib.util.spec_from_file_location(
    "autorun_build_support", PLUGIN_ROOT / "build_support.py"
)
if _SUPPORT_SPEC is None or _SUPPORT_SPEC.loader is None:
    raise RuntimeError("Could not load autorun wheel build support")
_BUILD_SUPPORT = importlib.util.module_from_spec(_SUPPORT_SPEC)
_SUPPORT_SPEC.loader.exec_module(_BUILD_SUPPORT)


class BuildPyWithPluginAssets(_build_py):
    """Stage canonical plugin resources beside the autorun Python modules."""

    def run(self) -> None:
        super().run()
        _BUILD_SUPPORT.copy_plugin_assets(PLUGIN_ROOT, Path(self.build_lib) / "autorun")


setup(cmdclass={"build_py": BuildPyWithPluginAssets})
