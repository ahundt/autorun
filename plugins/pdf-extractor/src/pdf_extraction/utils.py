#!/usr/bin/env python3
"""
PDF Extraction Utilities - GPU detection, quality metrics, encryption check.
"""

def detect_gpu_availability(*, probe_runtime: bool = True) -> dict:
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
        # pdftotext goes last everywhere: it is the only backend with no Python
        # dependency, so it is what remains when the package is installed
        # without extras and poppler happens to be on the system.
        'recommended_backends': ['markitdown', 'pdfbox', 'pdfplumber', 'pdfminer', 'pypdf2', 'pdftotext']
    }

    try:
        if not probe_runtime:
            return gpu_info
        import torch
        if torch.cuda.is_available():
            gpu_info['available'] = True
            gpu_info['device_count'] = torch.cuda.device_count()
            gpu_info['device_name'] = torch.cuda.get_device_name(0)
            gpu_info['recommended_backends'] = [
                'docling', 'marker', 'markitdown',
                'pdfbox', 'pdfplumber', 'pdfminer', 'pypdf2', 'pdftotext'
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
    return {
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


def is_pdf_encrypted(pdf_path: str) -> bool:
    """
    Check if PDF is encrypted/password-protected.

    Args:
        pdf_path: Path to PDF file

    Returns:
        True if encrypted, False otherwise (also False if pypdf is unavailable)
    """
    try:
        import pypdf

        with open(pdf_path, 'rb') as f:
            reader = pypdf.PdfReader(f)
            return reader.is_encrypted
    except Exception:
        # Availability probes deliberately collapse parser/import failures into
        # "not readable here"; extraction reports detailed backend errors.
        return False
