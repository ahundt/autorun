"""Shared fixtures for the pdf_extraction tests."""

import importlib.util
import os
import shutil
import sys
from types import SimpleNamespace

import pytest
from pdf_extraction.backends import BACKEND_REGISTRY, BackendExtractor


class EchoExtractor(BackendExtractor):
    """Fake backend: the extracted text is the source PDF's path, so each output names its input."""

    module = 'pdf_extraction'  # always importable, so the fake counts as installed

    def __init__(self):
        super().__init__('echo', lambda: None)

    def _extract_impl(self, pdf_file: str) -> str:
        return pdf_file


class PickyExtractor(EchoExtractor):
    """Fake backend: fails on any PDF whose file name starts with 'bad', echoes the rest."""

    def _extract_impl(self, pdf_file: str) -> str:
        if os.path.basename(pdf_file).startswith('bad'):
            raise RuntimeError('cannot parse')
        return pdf_file


@pytest.fixture
def echo_backend(monkeypatch):
    """Register EchoExtractor as backend 'echo' for one test and return that name."""
    monkeypatch.setitem(BACKEND_REGISTRY, 'echo', EchoExtractor)
    return 'echo'


@pytest.fixture
def picky_backend(monkeypatch):
    """Register PickyExtractor as backend 'picky' for one test and return that name."""
    monkeypatch.setitem(BACKEND_REGISTRY, 'picky', PickyExtractor)
    return 'picky'


@pytest.fixture
def environment(monkeypatch):
    """Return setup(*installed, gpu=False) that fakes what this machine has.

    installed names import modules and executables; nothing else is found.
    gpu fakes torch reporting one CUDA device; otherwise torch is not importable.
    """
    def setup(*installed, gpu=False):
        monkeypatch.setattr(
            importlib.util, 'find_spec',
            lambda module, *args: SimpleNamespace(name=module) if module in installed else None,
        )
        monkeypatch.setattr(
            shutil, 'which',
            lambda cmd, *args, **kwargs: f'/usr/bin/{cmd}' if cmd in installed else None,
        )
        cuda = SimpleNamespace(
            is_available=lambda: True, device_count=lambda: 1, get_device_name=lambda index: 'Fake GPU'
        )
        monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=cuda) if gpu else None)

    return setup
