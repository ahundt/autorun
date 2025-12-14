"""PDF Extraction - Multi-backend PDF to text/markdown conversion.

Provides robust PDF text extraction with 9 backends supporting graceful degradation:
- Modern (2024-2025): docling, marker, markitdown
- Traditional: pymupdf4llm, pdfbox, pdfminer, pypdf2, pdfplumber, pdftotext

Usage:
    from pdf_extraction import extract_single_pdf, pdf_to_txt

    # Single file extraction
    result = extract_single_pdf("document.pdf", "output.md")

    # Batch extraction
    files = pdf_to_txt("/path/to/pdfs/", "/path/to/output/")
"""

from .backends import BackendExtractor, BACKEND_REGISTRY
from .utils import detect_gpu_availability, calculate_extraction_quality_metrics, is_pdf_encrypted
from .extractors import extract_single_pdf, pdf_to_txt

__all__ = [
    # Backends
    "BackendExtractor",
    "BACKEND_REGISTRY",
    # Utilities
    "detect_gpu_availability",
    "calculate_extraction_quality_metrics",
    "is_pdf_encrypted",
    # Extractors
    "extract_single_pdf",
    "pdf_to_txt",
]

__version__ = "0.1.0"
