#!/usr/bin/env python3
"""
PDF Data Extraction with Multi-Backend Support

Modern PDF extraction backends (2024-2025):
- markitdown: Microsoft multi-format converter (MIT, no models, lightweight)
- docling: IBM document processor with layout analysis (MIT, ~500MB models)
- marker: Vision-based PDF to Markdown (GPL-3.0, ~1GB models, GPU-accelerated)

Traditional extraction backends:
- pymupdf4llm: PyMuPDF with LLM optimization (AGPL-3.0)
- pdfbox: Java-based, good for tables (Apache-2.0)
- pdfminer: Pure Python (MIT, older)
- pypdf2: Basic extraction (BSD-3)
- pdfplumber: Table support (MIT)
- pdftotext: Command-line tool (requires system install)

Source: Adapted from auto-qualitative-coding/file_processing.py
"""

import os
import time
import subprocess
from tqdm import tqdm
from pdfminer.high_level import extract_text
import pdfplumber
import PyPDF2

# Optionally import pdfbox (has installation issues on some systems)
try:
    from pdfbox import PDFBox
except ImportError:
    PDFBox = None

# Optionally import pymupdf4llm
try:
    import pymupdf4llm
except ImportError:
    pymupdf4llm = None


# ==============================================================================
# MODERN PDF EXTRACTION BACKENDS (DRY-Compliant Classes)
# ==============================================================================

class BackendExtractor:
    """
    Base class for PDF extraction backends implementing DRY principle.

    All backends share same extraction pattern:
    - Lazy converter initialization with caching
    - Consistent metadata return format
    - Error handling without exceptions

    Subclasses override only _extract_impl() for backend-specific logic.
    """

    def __init__(self, name: str, converter_factory):
        """
        Initialize backend extractor.

        Args:
            name: Backend name ('docling', 'marker', 'markitdown')
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

            # Return success metadata
            return {
                'success': True,
                'content': content,
                'time': extraction_time,
                'error': None
            }

        except Exception as e:
            extraction_time = time.time() - start_time

            # Return failure metadata
            return {
                'success': False,
                'content': None,
                'time': extraction_time,
                'error': str(e)
            }

    def _extract_impl(self, pdf_file: str) -> str:
        """
        Backend-specific extraction implementation.
        Override in subclass.

        Args:
            pdf_file: Path to PDF file

        Returns:
            Extracted text content (markdown or plain text)

        Raises:
            Exception if extraction fails (caught by extract())
        """
        raise NotImplementedError(f"{self.name} backend must implement _extract_impl()")


class DoclingExtractor(BackendExtractor):
    """Docling backend (IBM, MIT license)."""

    def __init__(self):
        def create_docling():
            from docling.document_converter import DocumentConverter
            return DocumentConverter()

        super().__init__('docling', create_docling)

    def _extract_impl(self, pdf_file: str) -> str:
        result = self.converter.convert(pdf_file)
        return result.document.export_to_markdown()


class MarkerExtractor(BackendExtractor):
    """Marker backend (datalab-to, GPL-3.0)."""

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
    """MarkItDown backend (Microsoft, MIT license)."""

    def __init__(self):
        def create_markitdown():
            from markitdown import MarkItDown
            return MarkItDown()

        super().__init__('markitdown', create_markitdown)

    def _extract_impl(self, pdf_file: str) -> str:
        result = self.converter.convert(pdf_file)
        return result.text_content


class Pymupdf4llmExtractor(BackendExtractor):
    """PyMuPDF4LLM backend (AGPL-3.0)."""

    def __init__(self):
        def create_pymupdf4llm():
            if pymupdf4llm is None:
                raise ImportError("pymupdf4llm not installed")
            return pymupdf4llm

        super().__init__('pymupdf4llm', create_pymupdf4llm)

    def _extract_impl(self, pdf_file: str) -> str:
        # pymupdf4llm is module, not class - use directly
        return pymupdf4llm.to_markdown(pdf_file)


class PdfboxExtractor(BackendExtractor):
    """PDFBox backend (Apache-2.0)."""

    def __init__(self):
        def create_pdfbox():
            if PDFBox is None:
                raise ImportError("pdfbox module not available")
            return PDFBox()

        super().__init__('pdfbox', create_pdfbox)

    def _extract_impl(self, pdf_file: str) -> str:
        # PDFBox writes directly to file, need to read it back
        import tempfile
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
    """PDFMiner backend (MIT)."""

    def __init__(self):
        # pdfminer uses module-level function, not class
        super().__init__('pdfminer', lambda: extract_text)

    def _extract_impl(self, pdf_file: str) -> str:
        return extract_text(pdf_file)


class Pypdf2Extractor(BackendExtractor):
    """PyPDF2 backend (BSD-3)."""

    def __init__(self):
        # PyPDF2 uses per-file reader, return dummy
        super().__init__('pypdf2', lambda: PyPDF2)

    def _extract_impl(self, pdf_file: str) -> str:
        with open(pdf_file, 'rb') as pdf:
            reader = PyPDF2.PdfReader(pdf)
            text_parts = []
            for page in reader.pages:
                text_parts.append(page.extract_text())
            return ''.join(text_parts)


class PdfplumberExtractor(BackendExtractor):
    """PDFPlumber backend (MIT)."""

    def __init__(self):
        # pdfplumber uses per-file context manager
        super().__init__('pdfplumber', lambda: pdfplumber)

    def _extract_impl(self, pdf_file: str) -> str:
        with pdfplumber.open(pdf_file) as pdf:
            text_parts = [page.extract_text() for page in pdf.pages]
            return "\n".join(text_parts)


class PdftotextExtractor(BackendExtractor):
    """Pdftotext command-line backend."""

    def __init__(self):
        # pdftotext is CLI tool, no Python object
        super().__init__('pdftotext', lambda: subprocess)

    def _extract_impl(self, pdf_file: str) -> str:
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as tmp:
            tmp_path = tmp.name

        try:
            subprocess.run(['pdftotext', '-layout', pdf_file, tmp_path], check=True)
            with open(tmp_path, 'r') as f:
                return f.read()
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


# ==============================================================================
# UTILITY FUNCTIONS
# ==============================================================================

def detect_gpu_availability():
    """
    Detect if GPU is available for GPU-accelerated backends.

    Returns:
        dict with keys:
        - available: bool (True if GPU detected)
        - device_count: int (Number of GPUs)
        - device_name: str (GPU model name or 'CPU')
        - recommended_backends: list (Backends optimized for this hardware)
    """
    gpu_info = {
        'available': False,
        'device_count': 0,
        'device_name': 'CPU',
        'recommended_backends': ['markitdown', 'pdfbox', 'pdfplumber', 'pdfminer', 'pypdf2']
    }

    try:
        import torch
        if torch.cuda.is_available():
            gpu_info['available'] = True
            gpu_info['device_count'] = torch.cuda.device_count()
            gpu_info['device_name'] = torch.cuda.get_device_name(0)
            gpu_info['recommended_backends'] = [
                'docling', 'marker', 'markitdown',
                'pdfbox', 'pdfplumber', 'pdfminer'
            ]
    except ImportError:
        pass  # PyTorch not installed, use CPU backends

    return gpu_info


def calculate_extraction_quality_metrics(text_content: str) -> dict:
    """
    Calculate quality metrics for extracted text.

    Args:
        text_content: Extracted text (markdown or plain text)

    Returns:
        dict with metrics:
        - char_count: int (Total characters)
        - line_count: int (Total lines)
        - word_count: int (Approximate word count)
        - table_markers: int (Count of | markdown table pipes)
        - equation_markers: int (Count of $, \\(, \\[ equation indicators)
        - code_block_markers: int (Count of ``` code block markers)
        - has_structure: bool (Has markdown structure indicators)
    """
    metrics = {
        'char_count': len(text_content),
        'line_count': len(text_content.split('\n')),
        'word_count': len(text_content.split()),
        'table_markers': text_content.count('|'),
        'equation_markers': (
            text_content.count('$') +
            text_content.count('\\(') +
            text_content.count('\\[')
        ),
        'code_block_markers': text_content.count('```'),
        'has_structure': (
            '##' in text_content or
            '**' in text_content or
            '|' in text_content
        )
    }

    return metrics


def is_pdf_encrypted(pdf_path: str) -> bool:
    """
    Check if PDF is encrypted/password-protected.

    Args:
        pdf_path: Path to PDF file

    Returns:
        True if encrypted, False otherwise
    """
    try:
        with open(pdf_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            return reader.is_encrypted
    except Exception:
        return False


# ==============================================================================
# BACKEND REGISTRY
# ==============================================================================

BACKEND_REGISTRY = {
    # Modern backends (2024-2025)
    'docling': lambda: DoclingExtractor(),
    'marker': lambda: MarkerExtractor(),
    'markitdown': lambda: MarkItDownExtractor(),

    # Traditional backends (now DRY-compliant)
    'pymupdf4llm': lambda: Pymupdf4llmExtractor(),
    'pdfbox': lambda: PdfboxExtractor(),
    'pdfminer': lambda: PdfminerExtractor(),
    'pypdf2': lambda: Pypdf2Extractor(),
    'pdfplumber': lambda: PdfplumberExtractor(),
    'pdftotext': lambda: PdftotextExtractor()
}


# ==============================================================================
# MAIN EXTRACTION FUNCTIONS
# ==============================================================================

def extract_single_pdf(input_file: str, output_file: str, backends: list = None) -> dict:
    """
    Extract a single PDF file to output file.

    Args:
        input_file: Path to PDF file (file or directory)
        output_file: Path to output file (.txt or .md)
        backends: List of backends to try in order. If None, auto-detects.

    Returns:
        dict with keys:
        - success: bool
        - backend_used: str or None
        - extraction_time_seconds: float
        - output_size_bytes: int
        - quality_metrics: dict
        - error: str or None
        - encrypted: bool
    """
    input_file = os.path.expanduser(input_file)
    output_file = os.path.expanduser(output_file)

    # Auto-detect optimal backends if not specified
    if backends is None:
        gpu_info = detect_gpu_availability()
        backends = gpu_info['recommended_backends']

    # Initialize metadata
    metadata = {
        'success': False,
        'backend_used': None,
        'extraction_time_seconds': None,
        'output_size_bytes': None,
        'quality_metrics': None,
        'error': None,
        'encrypted': is_pdf_encrypted(input_file)
    }

    if metadata['encrypted']:
        print(f"Warning: PDF is encrypted: {input_file}")

    # Try each backend in order
    for backend in backends:
        if backend not in BACKEND_REGISTRY:
            print(f"Warning: Unknown backend '{backend}', skipping.")
            continue

        try:
            extractor = BACKEND_REGISTRY[backend]()
        except Exception as e:
            print(f"{backend} not available: {e}")
            continue

        result = extractor.extract(input_file, output_file)

        if result['success']:
            metadata['success'] = True
            metadata['backend_used'] = backend
            metadata['extraction_time_seconds'] = result['time']
            metadata['output_size_bytes'] = len(result['content'])
            metadata['quality_metrics'] = calculate_extraction_quality_metrics(result['content'])
            print(f"OK {backend}: {input_file} -> {output_file} ({len(result['content'])} chars, {result['time']:.2f}s)")
            break
        else:
            print(f"X {backend} failed: {result['error']}")
            metadata['error'] = result['error']

    return metadata


def pdf_to_txt(input_dir: str, output_dir: str, resume: bool = True, remove_empty: bool = False,
               backends: list = None, return_metadata: bool = False):
    """
    Convert all PDF files in input_dir to text files and save them in output_dir.

    Args:
        input_dir: Path to the directory containing PDF files.
        output_dir: Path to the directory where text files will be saved.
        resume: Whether to skip conversion for PDFs that already have corresponding text files.
        remove_empty: Whether to remove empty text files at the end of conversion.
        backends: The tools to use for PDF to text conversion. If None, auto-detects based on GPU availability.
        return_metadata: If True, returns (txt_files, metadata_dict).

    Returns:
        If return_metadata=False: List of paths to the output text files
        If return_metadata=True: Tuple of (txt_files list, metadata dict)
    """
    input_dir = os.path.expanduser(input_dir)
    output_dir = os.path.expanduser(output_dir)

    # Auto-detect optimal backends if not specified
    if backends is None:
        gpu_info = detect_gpu_availability()
        backends = gpu_info['recommended_backends']

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # Collect all PDF files in the input directory tree
    pdf_files = []
    for root, dirs, files in os.walk(input_dir):
        for file in files:
            if file.lower().endswith('.pdf'):
                pdf_files.append(os.path.join(root, file))

    # Convert each PDF file to text and save it in the output directory
    txt_files = []
    extraction_metadata = {}

    for pdf_file in tqdm(pdf_files, desc="Extracting PDFs"):
        try:
            # Use .md extension by default (markdown output)
            txt_file = os.path.splitext(os.path.basename(pdf_file))[0] + '.md'
            txt_path = os.path.join(output_dir, txt_file)
            txt_files.append(txt_path)

            # Initialize metadata for this PDF (extract_single_pdf() handles encryption check)
            pdf_basename = os.path.basename(pdf_file)
            extraction_metadata[pdf_basename] = {
                'backend_used': None,
                'extraction_time_seconds': None,
                'output_size_bytes': None,
                'quality_metrics': None,
                'success': False,
                'error': None,
                'encrypted': False  # Will be set by extract_single_pdf()
            }

            if os.path.exists(txt_path) and resume:
                # check if the file is empty
                if os.path.getsize(txt_path) > 0:
                    print(f"Skipping {pdf_file} because {txt_path} already exists.")
                    extraction_metadata[pdf_basename]['success'] = True
                    extraction_metadata[pdf_basename]['backend_used'] = 'cached'
                    continue
                # if the file is empty, delete it and convert it again
                else:
                    print(f"Converting {pdf_file} because {txt_path} exists but is empty.")
                    os.remove(txt_path)

            # Use extract_single_pdf() to avoid duplicating extraction logic (DRY)
            result = extract_single_pdf(pdf_file, txt_path, backends=backends)

            # Copy result to extraction_metadata
            extraction_metadata[pdf_basename]['backend_used'] = result['backend_used']
            extraction_metadata[pdf_basename]['extraction_time_seconds'] = result['extraction_time_seconds']
            extraction_metadata[pdf_basename]['output_size_bytes'] = result['output_size_bytes']
            extraction_metadata[pdf_basename]['quality_metrics'] = result['quality_metrics']
            extraction_metadata[pdf_basename]['success'] = result['success']
            extraction_metadata[pdf_basename]['error'] = result['error']
            extraction_metadata[pdf_basename]['encrypted'] = result['encrypted']

        except Exception as e:
            print(f"Error converting {pdf_file}: {e}")
            extraction_metadata[pdf_basename]['error'] = str(e)

    # Return with or without metadata (backward compatible)
    if return_metadata:
        return txt_files, extraction_metadata
    else:
        return txt_files


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python pdf_extraction.py <input_dir> [output_dir] [--backends backend1 backend2 ...]")
        print("\nAvailable backends:", list(BACKEND_REGISTRY.keys()))
        sys.exit(1)

    input_dir = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith('--') else input_dir

    backends = None
    if '--backends' in sys.argv:
        idx = sys.argv.index('--backends')
        backends = sys.argv[idx + 1:]

    results = pdf_to_txt(input_dir, output_dir, backends=backends)
    print(f"\nExtracted {len(results)} PDFs")
