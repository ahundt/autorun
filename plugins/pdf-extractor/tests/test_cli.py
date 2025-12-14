"""Tests for pdf_extraction.cli module."""

import sys
import pytest
from unittest.mock import patch, MagicMock
from io import StringIO

from pdf_extraction.cli import main


class TestCliMain:
    """Tests for CLI main() function."""

    def test_list_backends_option(self, capsys):
        """--list-backends should print available backends and exit."""
        with patch.object(sys, 'argv', ['extract-pdfs', '--list-backends']):
            main()

        captured = capsys.readouterr()
        assert 'Available backends:' in captured.out
        assert 'markitdown' in captured.out
        assert 'pdfplumber' in captured.out
        assert 'GPU available:' in captured.out

    def test_no_input_shows_error(self, capsys):
        """Should show error when no input provided."""
        with patch.object(sys, 'argv', ['extract-pdfs']):
            with pytest.raises(SystemExit) as exc_info:
                main()

            assert exc_info.value.code == 2  # argparse error exit code

    def test_nonexistent_file_exits_with_error(self, capsys):
        """Should exit with error for non-existent file."""
        with patch.object(sys, 'argv', ['extract-pdfs', '/nonexistent/file.pdf']):
            with pytest.raises(SystemExit) as exc_info:
                main()

            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert 'Failed:' in captured.out or 'Error:' in captured.out

    def test_nonexistent_directory_exits_with_error(self, capsys):
        """Should exit with error for non-existent directory."""
        with patch.object(sys, 'argv', ['extract-pdfs', '/nonexistent/directory/']):
            with pytest.raises(SystemExit) as exc_info:
                main()

            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert 'not a file or directory' in captured.out

    def test_backends_option_parsed(self):
        """--backends option should be parsed correctly."""
        with patch.object(sys, 'argv', [
            'extract-pdfs', '/fake/file.pdf',
            '--backends', 'markitdown', 'pdfplumber'
        ]):
            with patch('pdf_extraction.cli.extract_single_pdf') as mock_extract:
                mock_extract.return_value = {
                    'success': True,
                    'backend_used': 'markitdown',
                    'extraction_time_seconds': 1.0,
                    'output_size_bytes': 100,
                }

                with patch('os.path.isfile', return_value=True):
                    main()

                # Check that backends were passed correctly
                call_args = mock_extract.call_args
                assert call_args[1]['backends'] == ['markitdown', 'pdfplumber']

    def test_format_option_md(self):
        """--format md should use .md extension."""
        with patch.object(sys, 'argv', [
            'extract-pdfs', '/fake/file.pdf', '--format', 'md'
        ]):
            with patch('pdf_extraction.cli.extract_single_pdf') as mock_extract:
                mock_extract.return_value = {
                    'success': True,
                    'backend_used': 'markitdown',
                    'extraction_time_seconds': 1.0,
                    'output_size_bytes': 100,
                }

                with patch('os.path.isfile', return_value=True):
                    main()

                call_args = mock_extract.call_args
                output_path = call_args[0][1]
                assert output_path.endswith('.md')

    def test_format_option_txt(self):
        """--format txt should use .txt extension."""
        with patch.object(sys, 'argv', [
            'extract-pdfs', '/fake/file.pdf', '--format', 'txt'
        ]):
            with patch('pdf_extraction.cli.extract_single_pdf') as mock_extract:
                mock_extract.return_value = {
                    'success': True,
                    'backend_used': 'markitdown',
                    'extraction_time_seconds': 1.0,
                    'output_size_bytes': 100,
                }

                with patch('os.path.isfile', return_value=True):
                    main()

                call_args = mock_extract.call_args
                output_path = call_args[0][1]
                assert output_path.endswith('.txt')

    def test_no_resume_option(self):
        """--no-resume should pass resume=False to pdf_to_txt."""
        with patch.object(sys, 'argv', [
            'extract-pdfs', '/fake/directory/', '--no-resume'
        ]):
            with patch('pdf_extraction.cli.pdf_to_txt') as mock_pdf_to_txt:
                mock_pdf_to_txt.return_value = []

                with patch('os.path.isfile', return_value=False):
                    with patch('os.path.isdir', return_value=True):
                        main()

                call_args = mock_pdf_to_txt.call_args
                assert call_args[1]['resume'] is False

    def test_directory_extraction_uses_pdf_to_txt(self, capsys):
        """Directory input should use pdf_to_txt function."""
        with patch.object(sys, 'argv', ['extract-pdfs', '/fake/directory/']):
            with patch('pdf_extraction.cli.pdf_to_txt') as mock_pdf_to_txt:
                mock_pdf_to_txt.return_value = ['/output/file1.md', '/output/file2.md']

                with patch('os.path.isfile', return_value=False):
                    with patch('os.path.isdir', return_value=True):
                        main()

                mock_pdf_to_txt.assert_called_once()

        captured = capsys.readouterr()
        assert 'Extracted 2 PDFs' in captured.out


class TestCliArguments:
    """Tests for CLI argument parsing."""

    def test_help_option(self, capsys):
        """--help should show usage information."""
        with patch.object(sys, 'argv', ['extract-pdfs', '--help']):
            with pytest.raises(SystemExit) as exc_info:
                main()

            assert exc_info.value.code == 0

        captured = capsys.readouterr()
        assert 'Extract text from PDFs' in captured.out
        assert '--backends' in captured.out
        assert '--list-backends' in captured.out

    def test_custom_output_path(self):
        """Custom output path should be used."""
        with patch.object(sys, 'argv', [
            'extract-pdfs', '/fake/input.pdf', '/custom/output.md'
        ]):
            with patch('pdf_extraction.cli.extract_single_pdf') as mock_extract:
                mock_extract.return_value = {
                    'success': True,
                    'backend_used': 'markitdown',
                    'extraction_time_seconds': 1.0,
                    'output_size_bytes': 100,
                }

                with patch('os.path.isfile', return_value=True):
                    main()

                call_args = mock_extract.call_args
                output_path = call_args[0][1]
                assert output_path == '/custom/output.md'
