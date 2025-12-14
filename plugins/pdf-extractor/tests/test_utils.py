"""Tests for pdf_extraction.utils module."""

import pytest
from pdf_extraction.utils import (
    detect_gpu_availability,
    calculate_extraction_quality_metrics,
    is_pdf_encrypted,
)


class TestDetectGpuAvailability:
    """Tests for detect_gpu_availability()."""

    def test_returns_dict_with_required_keys(self):
        """Should return dict with all required keys."""
        result = detect_gpu_availability()

        assert isinstance(result, dict)
        assert 'available' in result
        assert 'device_count' in result
        assert 'device_name' in result
        assert 'recommended_backends' in result

    def test_available_is_boolean(self):
        """available should be a boolean."""
        result = detect_gpu_availability()
        assert isinstance(result['available'], bool)

    def test_device_count_is_integer(self):
        """device_count should be an integer."""
        result = detect_gpu_availability()
        assert isinstance(result['device_count'], int)
        assert result['device_count'] >= 0

    def test_recommended_backends_is_list(self):
        """recommended_backends should be a non-empty list."""
        result = detect_gpu_availability()
        assert isinstance(result['recommended_backends'], list)
        assert len(result['recommended_backends']) > 0

    def test_recommended_backends_contains_cpu_fallbacks(self):
        """recommended_backends should include CPU fallback backends."""
        result = detect_gpu_availability()
        # These should always be available regardless of GPU
        cpu_backends = {'pdfminer', 'pypdf2', 'pdfplumber'}
        recommended = set(result['recommended_backends'])
        assert cpu_backends.issubset(recommended), \
            f"Missing CPU backends: {cpu_backends - recommended}"


class TestCalculateExtractionQualityMetrics:
    """Tests for calculate_extraction_quality_metrics()."""

    def test_empty_string(self):
        """Should handle empty string."""
        result = calculate_extraction_quality_metrics("")

        assert result['char_count'] == 0
        assert result['word_count'] == 0
        assert result['line_count'] == 1  # Empty string splits to ['']
        assert result['table_markers'] == 0
        assert result['equation_markers'] == 0
        assert result['code_block_markers'] == 0
        assert result['has_structure'] is False

    def test_plain_text(self):
        """Should count chars, words, lines correctly."""
        text = "Hello world\nThis is a test"
        result = calculate_extraction_quality_metrics(text)

        assert result['char_count'] == len(text)
        assert result['word_count'] == 6  # Hello, world, This, is, a, test
        assert result['line_count'] == 2
        assert result['has_structure'] is False

    def test_markdown_with_tables(self):
        """Should detect table markers."""
        text = "| Col1 | Col2 |\n|------|------|\n| A    | B    |"
        result = calculate_extraction_quality_metrics(text)

        assert result['table_markers'] == 9  # Count of | characters (3 per row * 3 rows)
        assert result['has_structure'] is True

    def test_markdown_with_headers(self):
        """Should detect markdown headers."""
        text = "## Section Header\nSome content"
        result = calculate_extraction_quality_metrics(text)

        assert result['has_structure'] is True

    def test_markdown_with_bold(self):
        """Should detect bold markers."""
        text = "This is **bold** text"
        result = calculate_extraction_quality_metrics(text)

        assert result['has_structure'] is True

    def test_equation_markers_dollar(self):
        """Should count $ equation markers."""
        text = "Inline $x^2$ and block $$y = mx + b$$"
        result = calculate_extraction_quality_metrics(text)

        assert result['equation_markers'] == 6  # 2 inline + 4 block

    def test_equation_markers_latex(self):
        """Should count LaTeX equation markers."""
        text = r"Inline \(x^2\) and block \[y = mx + b\]"
        result = calculate_extraction_quality_metrics(text)

        # The function counts \( and \[ (opening delimiters only)
        assert result['equation_markers'] == 2  # 1 inline \( + 1 block \[

    def test_code_block_markers(self):
        """Should count code block markers."""
        text = "```python\nprint('hello')\n```"
        result = calculate_extraction_quality_metrics(text)

        assert result['code_block_markers'] == 2


class TestIsPdfEncrypted:
    """Tests for is_pdf_encrypted()."""

    def test_nonexistent_file_returns_false(self):
        """Should return False for non-existent file."""
        result = is_pdf_encrypted("/nonexistent/path/to/file.pdf")
        assert result is False

    def test_invalid_pdf_returns_false(self):
        """Should return False for invalid PDF (not raise exception)."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
            f.write(b"Not a valid PDF content")
            temp_path = f.name

        try:
            result = is_pdf_encrypted(temp_path)
            assert result is False
        finally:
            import os
            os.unlink(temp_path)
