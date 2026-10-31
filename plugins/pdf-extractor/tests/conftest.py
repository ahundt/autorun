"""Shared fixtures for the pdf_extraction tests."""

import pytest
from pdf_extraction.backends import BACKEND_REGISTRY, BackendExtractor


class EchoExtractor(BackendExtractor):
    """Fake backend: the extracted text is the source PDF's path, so each output names its input."""

    def __init__(self):
        super().__init__('echo', lambda: None)

    def _extract_impl(self, pdf_file: str) -> str:
        return pdf_file


@pytest.fixture
def echo_backend(monkeypatch):
    """Register EchoExtractor as backend 'echo' for one test and return that name."""
    monkeypatch.setitem(BACKEND_REGISTRY, 'echo', EchoExtractor)
    return 'echo'
