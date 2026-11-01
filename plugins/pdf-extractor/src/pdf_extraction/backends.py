#!/usr/bin/env python3
"""
PDF Extraction Backends - Template Method Pattern Implementation

Each backend implements _extract_impl() for backend-specific logic while
sharing common extraction workflow (timing, output writing, error handling).

Backends (9 total):
- Modern (2024-2025): docling, marker, markitdown
- Traditional: pymupdf4llm, pdfbox, pdfminer, pypdf2, pdfplumber, pdftotext
"""

import importlib.util
import os
import shutil
import subprocess
import tempfile
import time


def _keep_existing_output(output_file: str, content: str):
    """Copy a non-empty output_file holding other text to output_file.~N~; return that path.

    The output path may hold a file extract-pdfs did not write - notes.md beside
    notes.pdf in an in-place run, or an output path the user named - and
    neither single-file mode nor --no-resume asks before replacing it. N is the
    first unused number (GNU `cp --backup=numbered` naming), claimed with an
    exclusive create so no earlier backup is replaced. Identical text needs no
    backup, so re-running an extraction does not pile up copies.
    """
    try:
        with open(output_file, 'rb') as f:
            existing = f.read()
    except FileNotFoundError:
        return None
    # The text-mode write below turns '\n' into os.linesep; compare those bytes.
    if not existing or existing == content.replace('\n', os.linesep).encode('utf-8'):
        return None
    n = 1
    while True:
        backup = f'{output_file}.~{n}~'
        try:
            with open(backup, 'xb') as f:
                f.write(existing)
            return backup
        except FileExistsError:
            n += 1


class BackendExtractor:
    """
    Base class for PDF extraction backends implementing Template Method pattern.

    All backends share same extraction workflow:
    - Lazy converter initialization with caching
    - Consistent metadata return format
    - Error handling without raising exceptions

    Subclasses override only _extract_impl() for backend-specific logic.
    """

    # What backend_availability() probes for: an importable module, or for a
    # command-line tool, an executable on PATH. Each backend sets one.
    module = None
    executable = None
    # GPU backends are tried first when a CUDA GPU is detected, last otherwise.
    gpu = False

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
            - backup: str or None (where a different, non-empty file that
              was at output_file was kept; success only)
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

            backup = _keep_existing_output(output_file, content)

            # Write output
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(content)

            extraction_time = time.time() - start_time

            return {
                'success': True,
                'content': content,
                'time': extraction_time,
                'error': None,
                'backup': backup,
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

    module = 'docling'
    gpu = True

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

    module = 'marker'
    gpu = True

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

    module = 'markitdown'

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

    module = 'pymupdf4llm'

    def __init__(self):
        def create_pymupdf4llm():
            import pymupdf4llm

            return pymupdf4llm

        super().__init__('pymupdf4llm', create_pymupdf4llm)

    def _extract_impl(self, pdf_file: str) -> str:
        return self.converter.to_markdown(pdf_file)


class PdfboxExtractor(BackendExtractor):
    """PDFBox backend - Java-based, good for tables (Apache-2.0)."""

    module = 'pdfbox'

    def __init__(self):
        def create_pdfbox():
            from pdfbox import PDFBox

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

    module = 'pdfminer'

    def __init__(self):
        def create_pdfminer():
            from pdfminer.high_level import extract_text

            return extract_text

        super().__init__('pdfminer', create_pdfminer)

    def _extract_impl(self, pdf_file: str) -> str:
        return self.converter(pdf_file)


class Pypdf2Extractor(BackendExtractor):
    """pypdf backend under the legacy ``pypdf2`` CLI id (BSD-3)."""

    module = 'pypdf'

    def __init__(self):
        def create_pypdf2():
            import pypdf

            return pypdf

        super().__init__('pypdf2', create_pypdf2)

    def _extract_impl(self, pdf_file: str) -> str:
        with open(pdf_file, 'rb') as pdf:
            reader = self.converter.PdfReader(pdf)
            text_parts = []
            for page in reader.pages:
                text_parts.append(page.extract_text())
            return ''.join(text_parts)


class PdfplumberExtractor(BackendExtractor):
    """PDFPlumber backend - Excellent for tables (MIT license)."""

    module = 'pdfplumber'

    def __init__(self):
        def create_pdfplumber():
            import pdfplumber

            return pdfplumber

        super().__init__('pdfplumber', create_pdfplumber)

    def _extract_impl(self, pdf_file: str) -> str:
        with self.converter.open(pdf_file) as pdf:
            text_parts = [page.extract_text() for page in pdf.pages]
            return "\n".join(text_parts)


class PdftotextExtractor(BackendExtractor):
    """Pdftotext backend - Command-line tool (requires system install)."""

    executable = 'pdftotext'

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

# One row per backend: the class is the factory and carries its module, its
# executable and its GPU flag. Insertion order is the default try order
# (utils.detect_gpu_availability keeps it, moving GPU backends by GPU presence).
# pdftotext goes last: it is the only backend with no Python dependency, so it
# is what remains when the package is installed without extras and poppler
# happens to be on the system.
BACKEND_REGISTRY = {
    # Modern backends (2024-2025)
    'docling': DoclingExtractor,
    'marker': MarkerExtractor,
    'markitdown': MarkItDownExtractor,

    # Traditional backends
    'pymupdf4llm': Pymupdf4llmExtractor,
    'pdfbox': PdfboxExtractor,
    'pdfplumber': PdfplumberExtractor,
    'pdfminer': PdfminerExtractor,
    'pypdf2': Pypdf2Extractor,
    'pdftotext': PdftotextExtractor,
}


def backend_availability() -> dict[str, bool]:
    """Report installed backends without importing their heavy modules."""
    available: dict[str, bool] = {}
    for name, extractor in BACKEND_REGISTRY.items():
        if extractor.executable:
            available[name] = shutil.which(extractor.executable) is not None
            continue
        try:
            available[name] = importlib.util.find_spec(extractor.module) is not None
        except (ImportError, ValueError):
            available[name] = False
    return available
