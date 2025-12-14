#!/usr/bin/env python3
"""
PDF Extraction Backends - Template Method Pattern Implementation

Each backend implements _extract_impl() for backend-specific logic while
sharing common extraction workflow (timing, output writing, error handling).

Backends (9 total):
- Modern (2024-2025): docling, marker, markitdown
- Traditional: pymupdf4llm, pdfbox, pdfminer, pypdf2, pdfplumber, pdftotext
"""

import os
import subprocess
import tempfile
import time

import pdfplumber
import PyPDF2

# Core dependencies (always available)
from pdfminer.high_level import extract_text

# Optional dependencies - graceful degradation
try:
    from pdfbox import PDFBox
except ImportError:
    PDFBox = None

try:
    import pymupdf4llm
except ImportError:
    pymupdf4llm = None


class BackendExtractor:
    """
    Base class for PDF extraction backends implementing Template Method pattern.

    All backends share same extraction workflow:
    - Lazy converter initialization with caching
    - Consistent metadata return format
    - Error handling without raising exceptions

    Subclasses override only _extract_impl() for backend-specific logic.
    """

    def __init__(self, name: str, converter_factory):
        """
        Initialize backend extractor.

        Args:
            name: Backend name (e.g., 'docling', 'marker', 'markitdown')
            converter_factory: Callable that creates converter instance
        """
        self.name = name
        self.converter_factory = converter_factory
        self.converter = None

    def extract(self, pdf_file: str, output_file: str) -> dict:
        """
        Extract PDF using this backend with metadata tracking.

        Args:
            pdf_file: Absolute path to PDF file
            output_file: Absolute path for output text file

        Returns:
            dict with keys:
            - success: bool (True if extraction succeeded)
            - content: str or None (extracted text)
            - time: float (extraction time in seconds)
            - error: str or None (error message if failed)
        """
        start_time = time.time()

        try:
            # Initialize converter (lazy, cached after first use)
            if self.converter is None:
                self.converter = self.converter_factory()

            # Extract (backend-specific implementation)
            content = self._extract_impl(pdf_file)

            # Ensure output directory exists
            os.makedirs(os.path.dirname(output_file) or '.', exist_ok=True)

            # Write output
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(content)

            extraction_time = time.time() - start_time

            return {
                'success': True,
                'content': content,
                'time': extraction_time,
                'error': None
            }

        except Exception as e:
            extraction_time = time.time() - start_time

            return {
                'success': False,
                'content': None,
                'time': extraction_time,
                'error': str(e)
            }

    def _extract_impl(self, pdf_file: str) -> str:
        """
        Backend-specific extraction implementation. Override in subclass.

        Args:
            pdf_file: Path to PDF file

        Returns:
            Extracted text content (markdown or plain text)

        Raises:
            Exception if extraction fails (caught by extract())
        """
        raise NotImplementedError(f"{self.name} backend must implement _extract_impl()")


# =============================================================================
# MODERN BACKENDS (2024-2025)
# =============================================================================

class DoclingExtractor(BackendExtractor):
    """Docling backend - IBM document processor with layout analysis (MIT license)."""

    def __init__(self):
        def create_docling():
            from docling.document_converter import DocumentConverter
            return DocumentConverter()

        super().__init__('docling', create_docling)

    def _extract_impl(self, pdf_file: str) -> str:
        result = self.converter.convert(pdf_file)
        return result.document.export_to_markdown()


class MarkerExtractor(BackendExtractor):
    """Marker backend - Vision-based PDF to Markdown (GPL-3.0, GPU-accelerated)."""

    def __init__(self):
        def create_marker():
            from marker.converters.pdf import PdfConverter
            from marker.models import create_model_dict
            return PdfConverter(artifact_dict=create_model_dict())

        super().__init__('marker', create_marker)

    def _extract_impl(self, pdf_file: str) -> str:
        result = self.converter(pdf_file)
        return result.markdown


class MarkItDownExtractor(BackendExtractor):
    """MarkItDown backend - Microsoft multi-format converter (MIT license)."""

    def __init__(self):
        def create_markitdown():
            from markitdown import MarkItDown
            return MarkItDown()

        super().__init__('markitdown', create_markitdown)

    def _extract_impl(self, pdf_file: str) -> str:
        result = self.converter.convert(pdf_file)
        return result.text_content


# =============================================================================
# TRADITIONAL BACKENDS
# =============================================================================

class Pymupdf4llmExtractor(BackendExtractor):
    """PyMuPDF4LLM backend - LLM-optimized output (AGPL-3.0)."""

    def __init__(self):
        def create_pymupdf4llm():
            if pymupdf4llm is None:
                raise ImportError("pymupdf4llm not installed")
            return pymupdf4llm

        super().__init__('pymupdf4llm', create_pymupdf4llm)

    def _extract_impl(self, pdf_file: str) -> str:
        return pymupdf4llm.to_markdown(pdf_file)


class PdfboxExtractor(BackendExtractor):
    """PDFBox backend - Java-based, good for tables (Apache-2.0)."""

    def __init__(self):
        def create_pdfbox():
            if PDFBox is None:
                raise ImportError("pdfbox module not available")
            return PDFBox()

        super().__init__('pdfbox', create_pdfbox)

    def _extract_impl(self, pdf_file: str) -> str:
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as tmp:
            tmp_path = tmp.name

        try:
            self.converter.extract_text(pdf_file, tmp_path)
            with open(tmp_path, 'r') as f:
                return f.read()
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


class PdfminerExtractor(BackendExtractor):
    """PDFMiner backend - Pure Python (MIT license)."""

    def __init__(self):
        super().__init__('pdfminer', lambda: extract_text)

    def _extract_impl(self, pdf_file: str) -> str:
        return extract_text(pdf_file)


class Pypdf2Extractor(BackendExtractor):
    """PyPDF2 backend - Basic extraction, always available (BSD-3)."""

    def __init__(self):
        super().__init__('pypdf2', lambda: PyPDF2)

    def _extract_impl(self, pdf_file: str) -> str:
        with open(pdf_file, 'rb') as pdf:
            reader = PyPDF2.PdfReader(pdf)
            text_parts = []
            for page in reader.pages:
                text_parts.append(page.extract_text())
            return ''.join(text_parts)


class PdfplumberExtractor(BackendExtractor):
    """PDFPlumber backend - Excellent for tables (MIT license)."""

    def __init__(self):
        super().__init__('pdfplumber', lambda: pdfplumber)

    def _extract_impl(self, pdf_file: str) -> str:
        with pdfplumber.open(pdf_file) as pdf:
            text_parts = [page.extract_text() for page in pdf.pages]
            return "\n".join(text_parts)


class PdftotextExtractor(BackendExtractor):
    """Pdftotext backend - Command-line tool (requires system install)."""

    def __init__(self):
        super().__init__('pdftotext', lambda: subprocess)

    def _extract_impl(self, pdf_file: str) -> str:
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as tmp:
            tmp_path = tmp.name

        try:
            subprocess.run(['pdftotext', '-layout', pdf_file, tmp_path], check=True)
            with open(tmp_path, 'r') as f:
                return f.read()
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


# =============================================================================
# BACKEND REGISTRY
# =============================================================================

BACKEND_REGISTRY = {
    # Modern backends (2024-2025)
    'docling': lambda: DoclingExtractor(),
    'marker': lambda: MarkerExtractor(),
    'markitdown': lambda: MarkItDownExtractor(),

    # Traditional backends
    'pymupdf4llm': lambda: Pymupdf4llmExtractor(),
    'pdfbox': lambda: PdfboxExtractor(),
    'pdfminer': lambda: PdfminerExtractor(),
    'pypdf2': lambda: Pypdf2Extractor(),
    'pdfplumber': lambda: PdfplumberExtractor(),
    'pdftotext': lambda: PdftotextExtractor()
}
