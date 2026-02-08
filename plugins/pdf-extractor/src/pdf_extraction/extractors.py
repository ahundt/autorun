#!/usr/bin/env python3
"""
PDF Extraction Functions - Single file and batch extraction.
"""

import os

try:
    from tqdm import tqdm
except ImportError:
    # Fallback: no-op progress bar
    def tqdm(iterable, **kwargs):
        return iterable

from .backends import BACKEND_REGISTRY
from .utils import calculate_extraction_quality_metrics, detect_gpu_availability, is_pdf_encrypted


def extract_single_pdf(input_file: str, output_file: str, backends: list = None) -> dict:
    """
    Extract a single PDF file to output file.

    Args:
        input_file: Path to PDF file
        output_file: Path to output file (.txt or .md)
        backends: List of backends to try in order. If None, auto-detects based on GPU.

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
            chars = len(result['content'])
            print(f"OK {backend}: {input_file} -> {output_file} ({chars} chars, {result['time']:.2f}s)")
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
        backends: The tools to use for PDF to text conversion.
            If None, auto-detects based on GPU availability.
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
        # Use .md extension by default (markdown output)
        txt_file = os.path.splitext(os.path.basename(pdf_file))[0] + '.md'
        txt_path = os.path.join(output_dir, txt_file)
        txt_files.append(txt_path)

        # Initialize metadata for this PDF
        pdf_basename = os.path.basename(pdf_file)
        extraction_metadata[pdf_basename] = {
            'backend_used': None,
            'extraction_time_seconds': None,
            'output_size_bytes': None,
            'quality_metrics': None,
            'success': False,
            'error': None,
            'encrypted': False
        }

        try:
            if os.path.exists(txt_path) and resume:
                if os.path.getsize(txt_path) > 0:
                    print(f"Skipping {pdf_file} because {txt_path} already exists.")
                    extraction_metadata[pdf_basename]['success'] = True
                    extraction_metadata[pdf_basename]['backend_used'] = 'cached'
                    continue
                else:
                    print(f"Converting {pdf_file} because {txt_path} exists but is empty.")
                    os.remove(txt_path)

            # Use extract_single_pdf() to avoid duplicating extraction logic (DRY)
            result = extract_single_pdf(pdf_file, txt_path, backends=backends)

            # Copy result to extraction_metadata
            meta = extraction_metadata[pdf_basename]
            meta['backend_used'] = result['backend_used']
            meta['extraction_time_seconds'] = result['extraction_time_seconds']
            meta['output_size_bytes'] = result['output_size_bytes']
            meta['quality_metrics'] = result['quality_metrics']
            meta['success'] = result['success']
            meta['error'] = result['error']
            meta['encrypted'] = result['encrypted']

        except Exception as e:
            print(f"Error converting {pdf_file}: {e}")
            extraction_metadata[pdf_basename]['error'] = str(e)

    if return_metadata:
        return txt_files, extraction_metadata
    else:
        return txt_files
