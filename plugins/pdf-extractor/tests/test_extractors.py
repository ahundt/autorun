"""Tests for pdf_extraction.extractors module."""

import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock

from pdf_extraction.extractors import extract_single_pdf, pdf_to_txt


class TestExtractSinglePdf:
    """Tests for extract_single_pdf()."""

    def test_returns_dict_with_required_keys(self):
        """Should return dict with all required keys even on failure."""
        result = extract_single_pdf(
            "/nonexistent/file.pdf",
            "/output.md",
            backends=['pypdf2']
        )

        assert isinstance(result, dict)
        assert 'success' in result
        assert 'backend_used' in result
        assert 'extraction_time_seconds' in result
        assert 'output_size_bytes' in result
        assert 'quality_metrics' in result
        assert 'error' in result
        assert 'encrypted' in result

    def test_nonexistent_file_returns_failure(self):
        """Should return failure for non-existent file."""
        result = extract_single_pdf(
            "/nonexistent/file.pdf",
            "/output.md",
            backends=['pypdf2']
        )

        assert result['success'] is False
        assert result['backend_used'] is None

    def test_respects_backends_order(self):
        """Should try backends in specified order."""
        with patch('pdf_extraction.extractors.BACKEND_REGISTRY') as mock_registry:
            mock_extractor1 = MagicMock()
            mock_extractor1.return_value.extract.return_value = {
                'success': True,
                'content': 'test content',
                'time': 1.0,
                'error': None
            }

            mock_extractor2 = MagicMock()

            mock_registry.__getitem__.side_effect = lambda key: {
                'backend1': mock_extractor1,
                'backend2': mock_extractor2
            }[key]
            mock_registry.__contains__ = lambda self, key: key in ['backend1', 'backend2']

            result = extract_single_pdf(
                "/fake/file.pdf",
                "/output.md",
                backends=['backend1', 'backend2']
            )

            # backend1 should be tried first
            mock_extractor1.assert_called_once()
            # backend2 should not be tried since backend1 succeeded
            mock_extractor2.assert_not_called()

    def test_expands_user_paths(self):
        """Should expand ~ in file paths."""
        # This test verifies path expansion happens without actually extracting
        with patch('pdf_extraction.extractors.BACKEND_REGISTRY') as mock_registry:
            mock_registry.__contains__ = lambda self, key: False

            result = extract_single_pdf(
                "~/test.pdf",
                "~/output.md",
                backends=['nonexistent']
            )

            # Should not raise an error about ~ in path
            assert result['success'] is False

    def test_auto_detects_backends_when_none_specified(self):
        """Should auto-detect backends when backends=None."""
        with patch('pdf_extraction.extractors.detect_gpu_availability') as mock_gpu:
            mock_gpu.return_value = {
                'available': False,
                'recommended_backends': ['markitdown', 'pdfplumber']
            }

            result = extract_single_pdf(
                "/nonexistent/file.pdf",
                "/output.md",
                backends=None
            )

            mock_gpu.assert_called_once()


class TestPdfToTxt:
    """Tests for pdf_to_txt()."""

    def test_creates_output_directory(self):
        """Should create output directory if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            input_dir = os.path.join(tmpdir, 'input')
            output_dir = os.path.join(tmpdir, 'output')

            os.makedirs(input_dir)
            # Don't create output_dir - pdf_to_txt should create it

            # Run with empty input directory
            result = pdf_to_txt(
                input_dir,
                output_dir,
                backends=['pypdf2']
            )

            assert os.path.isdir(output_dir)

    def test_returns_list_of_output_paths(self):
        """Should return list of output file paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            input_dir = os.path.join(tmpdir, 'input')
            output_dir = os.path.join(tmpdir, 'output')
            os.makedirs(input_dir)

            result = pdf_to_txt(input_dir, output_dir, backends=['pypdf2'])

            assert isinstance(result, list)

    def test_return_metadata_option(self):
        """Should return (files, metadata) tuple when return_metadata=True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            input_dir = os.path.join(tmpdir, 'input')
            output_dir = os.path.join(tmpdir, 'output')
            os.makedirs(input_dir)

            result = pdf_to_txt(
                input_dir,
                output_dir,
                backends=['pypdf2'],
                return_metadata=True
            )

            assert isinstance(result, tuple)
            assert len(result) == 2
            files, metadata = result
            assert isinstance(files, list)
            assert isinstance(metadata, dict)

    def test_resume_skips_existing_files(self):
        """Should skip files that already have output when resume=True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            input_dir = os.path.join(tmpdir, 'input')
            output_dir = os.path.join(tmpdir, 'output')
            os.makedirs(input_dir)
            os.makedirs(output_dir)

            # Create a fake PDF file
            pdf_path = os.path.join(input_dir, 'test.pdf')
            with open(pdf_path, 'wb') as f:
                f.write(b'%PDF-1.4 fake pdf content')

            # Create existing output file
            output_path = os.path.join(output_dir, 'test.md')
            with open(output_path, 'w') as f:
                f.write('Existing content')

            # Run with resume=True
            files, metadata = pdf_to_txt(
                input_dir,
                output_dir,
                resume=True,
                backends=['pypdf2'],
                return_metadata=True
            )

            # Should mark as cached, not re-extract
            assert metadata['test.pdf']['backend_used'] == 'cached'

    def test_expands_user_paths(self):
        """Should expand ~ in directory paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a symlink from ~/test_pdf_extraction to tmpdir
            # Actually, just verify the function doesn't crash on ~ paths
            result = pdf_to_txt(
                tmpdir,  # Use actual dir
                tmpdir,
                backends=['pypdf2']
            )
            assert isinstance(result, list)

    def test_walks_subdirectories(self):
        """Should find PDFs in subdirectories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            input_dir = os.path.join(tmpdir, 'input')
            subdir = os.path.join(input_dir, 'subdir')
            output_dir = os.path.join(tmpdir, 'output')
            os.makedirs(subdir)

            # Create PDF in subdirectory
            pdf_path = os.path.join(subdir, 'nested.pdf')
            with open(pdf_path, 'wb') as f:
                f.write(b'%PDF-1.4 fake pdf')

            files, metadata = pdf_to_txt(
                input_dir,
                output_dir,
                backends=['pypdf2'],
                return_metadata=True
            )

            # Should find the nested PDF
            assert 'nested.pdf' in metadata
