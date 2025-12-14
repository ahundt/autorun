"""Tests for pdf_extraction.backends module."""

import pytest

from pdf_extraction.backends import (
    BACKEND_REGISTRY,
    BackendExtractor,
    DoclingExtractor,
    MarkerExtractor,
    MarkItDownExtractor,
    PdfboxExtractor,
    PdfminerExtractor,
    PdfplumberExtractor,
    PdftotextExtractor,
    Pymupdf4llmExtractor,
    Pypdf2Extractor,
)


class TestBackendRegistry:
    """Tests for BACKEND_REGISTRY."""

    def test_registry_is_dict(self):
        """Registry should be a dictionary."""
        assert isinstance(BACKEND_REGISTRY, dict)

    def test_registry_has_all_backends(self):
        """Registry should have all 9 backends."""
        expected_backends = {
            'docling', 'marker', 'markitdown',
            'pymupdf4llm', 'pdfbox', 'pdfminer',
            'pypdf2', 'pdfplumber', 'pdftotext'
        }
        assert set(BACKEND_REGISTRY.keys()) == expected_backends

    def test_registry_values_are_callable(self):
        """Registry values should be callable factories."""
        for name, factory in BACKEND_REGISTRY.items():
            assert callable(factory), f"{name} factory is not callable"

    def test_core_backends_instantiate(self):
        """Core backends (pdfminer, pypdf2, pdfplumber) should instantiate without error."""
        core_backends = ['pdfminer', 'pypdf2', 'pdfplumber']

        for name in core_backends:
            factory = BACKEND_REGISTRY[name]
            extractor = factory()
            assert isinstance(extractor, BackendExtractor), \
                f"{name} did not produce BackendExtractor instance"
            assert extractor.name == name, \
                f"{name} extractor has wrong name: {extractor.name}"


class TestBackendExtractor:
    """Tests for BackendExtractor base class."""

    def test_init_sets_name(self):
        """Should set name attribute."""
        extractor = BackendExtractor('test', lambda: None)
        assert extractor.name == 'test'

    def test_init_sets_converter_factory(self):
        """Should set converter_factory attribute."""
        def factory():
            return "converter"
        extractor = BackendExtractor('test', factory)
        assert extractor.converter_factory is factory

    def test_init_converter_is_none(self):
        """Converter should be None initially (lazy loading)."""
        extractor = BackendExtractor('test', lambda: "converter")
        assert extractor.converter is None

    def test_extract_impl_raises_not_implemented(self):
        """_extract_impl should raise NotImplementedError in base class."""
        extractor = BackendExtractor('test', lambda: None)

        with pytest.raises(NotImplementedError) as exc_info:
            extractor._extract_impl("/fake/path.pdf")

        assert "test backend must implement _extract_impl()" in str(exc_info.value)

    def test_extract_returns_dict_on_failure(self):
        """extract() should return error dict when extraction fails."""
        extractor = BackendExtractor('test', lambda: None)
        result = extractor.extract("/nonexistent.pdf", "/output.md")

        assert isinstance(result, dict)
        assert result['success'] is False
        assert result['content'] is None
        assert result['error'] is not None
        assert 'time' in result


class TestPdfminerExtractor:
    """Tests for PdfminerExtractor."""

    def test_instantiation(self):
        """Should instantiate without error."""
        extractor = PdfminerExtractor()
        assert extractor.name == 'pdfminer'

    def test_extract_nonexistent_file(self):
        """Should return error for non-existent file."""
        extractor = PdfminerExtractor()
        result = extractor.extract("/nonexistent/file.pdf", "/output.md")

        assert result['success'] is False
        assert result['error'] is not None


class TestPypdf2Extractor:
    """Tests for Pypdf2Extractor."""

    def test_instantiation(self):
        """Should instantiate without error."""
        extractor = Pypdf2Extractor()
        assert extractor.name == 'pypdf2'

    def test_extract_nonexistent_file(self):
        """Should return error for non-existent file."""
        extractor = Pypdf2Extractor()
        result = extractor.extract("/nonexistent/file.pdf", "/output.md")

        assert result['success'] is False
        assert result['error'] is not None


class TestPdfplumberExtractor:
    """Tests for PdfplumberExtractor."""

    def test_instantiation(self):
        """Should instantiate without error."""
        extractor = PdfplumberExtractor()
        assert extractor.name == 'pdfplumber'

    def test_extract_nonexistent_file(self):
        """Should return error for non-existent file."""
        extractor = PdfplumberExtractor()
        result = extractor.extract("/nonexistent/file.pdf", "/output.md")

        assert result['success'] is False
        assert result['error'] is not None


class TestMarkItDownExtractor:
    """Tests for MarkItDownExtractor."""

    def test_instantiation(self):
        """Should instantiate without error (markitdown is a core dependency)."""
        extractor = MarkItDownExtractor()
        assert extractor.name == 'markitdown'

    def test_extract_nonexistent_file(self):
        """Should return error for non-existent file."""
        extractor = MarkItDownExtractor()
        result = extractor.extract("/nonexistent/file.pdf", "/output.md")

        assert result['success'] is False
        assert result['error'] is not None


class TestOptionalBackends:
    """Tests for optional backends (may not be installed)."""

    def test_docling_extractor_instantiation(self):
        """DoclingExtractor should handle missing dependency gracefully."""
        try:
            extractor = DoclingExtractor()
            assert extractor.name == 'docling'
        except ImportError:
            pytest.skip("docling not installed")

    def test_marker_extractor_instantiation(self):
        """MarkerExtractor should handle missing dependency gracefully."""
        try:
            extractor = MarkerExtractor()
            assert extractor.name == 'marker'
        except ImportError:
            pytest.skip("marker not installed")

    def test_pymupdf4llm_extractor_instantiation(self):
        """Pymupdf4llmExtractor should handle missing dependency gracefully."""
        try:
            extractor = Pymupdf4llmExtractor()
            assert extractor.name == 'pymupdf4llm'
        except ImportError:
            pytest.skip("pymupdf4llm not installed")

    def test_pdfbox_extractor_instantiation(self):
        """PdfboxExtractor should handle missing dependency gracefully."""
        try:
            extractor = PdfboxExtractor()
            assert extractor.name == 'pdfbox'
        except ImportError:
            pytest.skip("pdfbox not installed")

    def test_pdftotext_extractor_instantiation(self):
        """PdftotextExtractor should instantiate (uses system command)."""
        extractor = PdftotextExtractor()
        assert extractor.name == 'pdftotext'
