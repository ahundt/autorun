"""Tests for pdf_extraction.cli module."""

import sys
from unittest.mock import patch

import pytest

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

    def test_list_backends_shows_every_registered_backend(self, capsys, echo_backend):
        """A registry entry is listed from its own row, not looked up in a second table."""
        with patch.object(sys, 'argv', ['extract-pdfs', '--list-backends']):
            main()

        installed = capsys.readouterr().out.split('Supported but not installed')[0]
        assert f'  - {echo_backend}\n' in installed

    @pytest.mark.parametrize('backend', ['pymupdf4llm', 'docling', 'marker'])
    def test_default_backends_use_an_installed_extra_only_backend(self, tmp_path, environment, backend):
        """Only pdf-llm, or pdf-gpu without CUDA, installed: the default still uses that backend."""
        environment(backend)
        pdf = tmp_path / 'doc.pdf'
        pdf.write_bytes(b'%PDF-1.4 fake pdf')

        with (
            patch.object(sys, 'argv', ['extract-pdfs', str(pdf)]),
            patch('pdf_extraction.cli.extract_single_pdf') as mock_extract,
        ):
            mock_extract.return_value = {
                'success': True,
                'backend_used': backend,
                'extraction_time_seconds': 1.0,
                'output_size_bytes': 100,
            }
            main()

        assert mock_extract.call_args.kwargs['backends'] == [backend]

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
                mock_pdf_to_txt.return_value = ([], {})

                with patch('os.path.isfile', return_value=False):
                    with patch('os.path.isdir', return_value=True):
                        main()

                call_args = mock_pdf_to_txt.call_args
                assert call_args[1]['resume'] is False

    def test_directory_extraction_uses_pdf_to_txt(self, capsys):
        """Directory input should use pdf_to_txt function."""
        with patch.object(sys, 'argv', ['extract-pdfs', '/fake/directory/']):
            with patch('pdf_extraction.cli.pdf_to_txt') as mock_pdf_to_txt:
                mock_pdf_to_txt.return_value = (
                    ['/output/file1.md', '/output/file2.md'],
                    {'file1.pdf': {'success': True}, 'file2.pdf': {'success': True}},
                )

                with patch('os.path.isfile', return_value=False):
                    with patch('os.path.isdir', return_value=True):
                        main()

                mock_pdf_to_txt.assert_called_once()

        captured = capsys.readouterr()
        assert 'Extracted 2 of 2 PDFs' in captured.out

    def test_directory_extraction_honors_format_and_input_tree(self, tmp_path, echo_backend):
        """Batch --format txt writes .txt, mirroring each PDF's folder under the output dir."""
        input_dir = tmp_path / 'input'
        output_dir = tmp_path / 'output'
        (input_dir / 'a').mkdir(parents=True)
        (input_dir / 'a' / 'report.pdf').write_bytes(b'%PDF-1.4 fake pdf')

        with patch.object(sys, 'argv', [
            'extract-pdfs', str(input_dir), str(output_dir),
            '--format', 'txt', '--backends', echo_backend
        ]):
            main()

        assert sorted(p.relative_to(output_dir).as_posix() for p in output_dir.rglob('*.*')) == [
            'a/report.txt'
        ]

    @pytest.mark.parametrize('names', [('bad1.pdf', 'bad2.pdf'), ('bad1.pdf', 'good.pdf')])
    def test_batch_exits_1_when_any_pdf_fails(self, tmp_path, picky_backend, capsys, names):
        """A batch with a failed PDF exits 1 like a failed single file, and names each failure."""
        input_dir = tmp_path / 'input'
        input_dir.mkdir()
        for name in names:
            (input_dir / name).write_bytes(b'%PDF-1.4 fake pdf')

        argv = ['extract-pdfs', str(input_dir), str(tmp_path / 'output'), '--backends', picky_backend]
        with patch.object(sys, 'argv', argv), pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        failed = [name for name in names if name.startswith('bad')]
        assert f'Extracted {len(names) - len(failed)} of {len(names)} PDFs' in out, out
        for name in failed:
            assert f'Failed: {name}: cannot parse' in out, out

    def test_batch_of_extracted_and_already_extracted_pdfs_exits_0(self, tmp_path, picky_backend, capsys):
        """Skipped-as-already-extracted counts as success: the batch returns normally."""
        input_dir = tmp_path / 'input'
        output_dir = tmp_path / 'output'
        input_dir.mkdir()
        output_dir.mkdir()
        for name in ('good.pdf', 'bad-but-done.pdf'):
            (input_dir / name).write_bytes(b'%PDF-1.4 fake pdf')
        (output_dir / 'bad-but-done.md').write_text('extracted earlier')

        with patch.object(sys, 'argv', [
            'extract-pdfs', str(input_dir), str(output_dir), '--backends', picky_backend
        ]):
            main()

        assert 'Extracted 2 of 2 PDFs' in capsys.readouterr().out

    @pytest.mark.parametrize('batch', [False, True], ids=['single-file', 'batch-no-resume'])
    def test_existing_output_with_other_text_is_kept_as_a_numbered_backup(
        self, tmp_path, echo_backend, batch
    ):
        """notes.md beside notes.pdf is the user's own file: extraction must not destroy it."""
        pdf = tmp_path / 'notes.pdf'
        pdf.write_bytes(b'%PDF-1.4 fake pdf')
        output = tmp_path / 'notes.md'
        output.write_text('my own notes')
        (tmp_path / 'notes.md.~1~').write_text('an older backup')

        target = [str(tmp_path), '--no-resume'] if batch else [str(pdf)]
        with patch.object(sys, 'argv', ['extract-pdfs', *target, '--backends', echo_backend]):
            main()

        assert output.read_text() == str(pdf)
        assert (tmp_path / 'notes.md.~1~').read_text() == 'an older backup'
        assert (tmp_path / 'notes.md.~2~').read_text() == 'my own notes'

    def test_reextracting_identical_text_makes_no_backup(self, tmp_path, echo_backend):
        pdf = tmp_path / 'doc.pdf'
        pdf.write_bytes(b'%PDF-1.4 fake pdf')
        for _ in range(2):
            with patch.object(sys, 'argv', ['extract-pdfs', str(pdf), '--backends', echo_backend]):
                main()

        assert sorted(p.name for p in tmp_path.iterdir()) == ['doc.md', 'doc.pdf']


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
