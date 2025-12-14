#!/usr/bin/env python3
"""
CLI wrapper for PDF data extraction - supports files and directories.

Usage:
    # Single file to auto-named output (.md)
    extract-pdfs /path/to/document.pdf

    # Single file to specific output file
    extract-pdfs /path/to/document.pdf /path/to/output.md

    # Directory to same directory (in-place)
    extract-pdfs /path/to/pdfs/

    # Directory to different output directory
    extract-pdfs /path/to/pdfs/ /path/to/output/

    # With specific backends
    extract-pdfs /path/to/document.pdf --backends markitdown pdfplumber

    # List available backends
    extract-pdfs --list-backends
"""

import argparse
import os
import sys


def _setup_standalone_imports():
    """Set up imports for standalone script execution."""
    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)


# Handle standalone execution vs package import
if __name__ == '__main__':
    _setup_standalone_imports()

# Now import the modules (works both installed and standalone)
from pdf_extraction.backends import BACKEND_REGISTRY
from pdf_extraction.extractors import extract_single_pdf, pdf_to_txt
from pdf_extraction.utils import detect_gpu_availability


def main():
    parser = argparse.ArgumentParser(
        description='Extract text from PDFs to markdown',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s document.pdf                    # Extract to document.md
  %(prog)s document.pdf output.md          # Extract to specific file
  %(prog)s ./pdfs/                         # Extract all PDFs in directory
  %(prog)s ./pdfs/ ./output/               # Extract to different directory
  %(prog)s doc.pdf --backends markitdown   # Use specific backend
  %(prog)s --list-backends                 # Show available backends
        """
    )
    parser.add_argument('input', nargs='?', help='PDF file or directory containing PDFs')
    parser.add_argument('output', nargs='?',
                        help='Output file (.md) or directory (default: same location as input)')
    parser.add_argument(
        '--backends', nargs='+',
        help='Backends to use (default: auto-detect). '
             'Options: markitdown, pdfplumber, pdfminer, pypdf2, docling, marker'
    )
    parser.add_argument('--no-resume', action='store_true',
                        help='Re-extract all files (ignore existing)')
    parser.add_argument('--format', choices=['md', 'txt'], default='md',
                        help='Output format (default: md)')
    parser.add_argument('--list-backends', action='store_true',
                        help='List available backends and exit')

    args = parser.parse_args()

    # List backends option (no input required)
    if args.list_backends:
        gpu_info = detect_gpu_availability()
        print("Available backends:")
        for name in BACKEND_REGISTRY.keys():
            recommended = " (recommended)" if name in gpu_info['recommended_backends'] else ""
            print(f"  - {name}{recommended}")
        print(f"\nGPU available: {gpu_info['available']}")
        if gpu_info['available']:
            print(f"GPU: {gpu_info['device_name']}")
        return

    # Require input if not listing backends
    if not args.input:
        parser.error("input is required (use --list-backends to see available backends)")

    # Determine backends
    backends = args.backends
    if not backends:
        gpu_info = detect_gpu_availability()
        backends = gpu_info['recommended_backends']

    input_path = os.path.expanduser(args.input)

    # Handle single file vs directory
    if os.path.isfile(input_path):
        # Single file extraction
        if args.output:
            output_path = os.path.expanduser(args.output)
        else:
            # Default: same directory, .md extension
            base = os.path.splitext(input_path)[0]
            output_path = f"{base}.{args.format}"

        print(f"Extracting: {input_path}")
        result = extract_single_pdf(input_path, output_path, backends=backends)

        if result['success']:
            print(f"Extracted: {output_path}")
            print(f"  Backend: {result['backend_used']}")
            print(f"  Size: {result['output_size_bytes']} bytes")
            print(f"  Time: {result['extraction_time_seconds']:.2f}s")
        else:
            print(f"Failed: {result['error']}")
            sys.exit(1)
    else:
        # Directory batch extraction
        if not os.path.isdir(input_path):
            print(f"Error: '{input_path}' is not a file or directory")
            sys.exit(1)

        output_dir = args.output or input_path  # Default: same directory
        output_dir = os.path.expanduser(output_dir)

        print(f"Extracting PDFs from: {input_path}")
        print(f"Output directory: {output_dir}")
        print(f"Backends: {', '.join(backends)}")

        results = pdf_to_txt(input_path, output_dir,
                             resume=not args.no_resume, backends=backends)
        print(f"\nExtracted {len(results)} PDFs to {output_dir}")


if __name__ == '__main__':
    main()
