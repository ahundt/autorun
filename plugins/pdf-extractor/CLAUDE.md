# PDF Extractor Plugin

## Purpose

Extract text and structured data from PDF documents with automatic backend selection and graceful fallback. Supports 9 extraction backends optimized for different document types (scanned, tables, forms, etc.).

## Quick Start

```bash
# Extract single PDF
extract-pdfs document.pdf

# Extract directory of PDFs
extract-pdfs ./pdfs/ ./output/

# List available backends
extract-pdfs --list-backends
```

## Installation

### Using uv tool install (Recommended — makes extract-pdfs globally available)

```bash
# From repository root:
cd plugins/pdf-extractor && uv tool install --force --editable . && cd ../..

# Verify:
extract-pdfs --list-backends
```

### Optional GPU Backends

For GPU-accelerated extraction (recommended for scanned/image-only PDFs):

```bash
cd plugins/pdf-extractor && uv tool install --force --editable ".[gpu]" && cd ../..
# Requires PyTorch + CUDA or MPS (Apple Silicon)
# Note: docling downloads ~500MB models on first use; marker downloads ~1GB
extract-pdfs --list-backends  # Verify gpu backends appear
```

### Venv Install (alternative — installs into current venv only)

```bash
cd plugins/pdf-extractor && uv pip install -e . && cd ../..
```

### Development Setup

```bash
cd plugins/pdf-extractor
uv pip install -e ".[dev]"  # Adds pytest, pytest-cov, ruff
uv run pytest tests/ -v     # Run tests
uv run ruff check src/ tests/  # Run linter
```

## Usage

### CLI Commands

```bash
# Single file extraction
extract-pdfs document.pdf                    # Output: document.md
extract-pdfs document.pdf output.md          # Custom output path
extract-pdfs document.pdf --format txt       # Plain text output

# Batch extraction
extract-pdfs ./input-pdfs/                   # In-place extraction
extract-pdfs ./input-pdfs/ ./output-dir/     # Different output directory
extract-pdfs ./pdfs/ --no-resume             # Re-extract all files

# Backend selection
extract-pdfs doc.pdf --backends markitdown pdfplumber  # Specific order
```

### Python API

```python
from pdf_extraction import extract_single_pdf, pdf_to_txt

# Single file
result = extract_single_pdf("document.pdf", "output.md")
print(f"Backend: {result['backend_used']}, Size: {result['output_size_bytes']}")

# Batch with metadata
files, metadata = pdf_to_txt("./pdfs/", "./output/", return_metadata=True)
for pdf, info in metadata.items():
    print(f"{pdf}: {info['backend_used']} ({info['extraction_time_seconds']:.2f}s)")
```

### Alternative Execution Methods

```bash
# Module execution
python -m pdf_extraction document.pdf

# Standalone script (no install required)
python src/pdf_extraction/cli.py document.pdf
```

## Available Backends

| Backend | License | Best For | GPU |
|---------|---------|----------|-----|
| markitdown | MIT | General text, forms | No |
| pdfplumber | MIT | Tables, structured data | No |
| pdfminer | MIT | Simple text documents | No |
| pypdf2 | BSD-3 | Basic extraction | No |
| docling | MIT | Layout analysis | Yes |
| marker | GPL-3.0 | Scanned documents, OCR | Yes |
| pymupdf4llm | AGPL-3.0 | LLM-optimized output | No |
| pdfbox | Apache-2.0 | Tables (Java-based) | No |
| pdftotext | System | Simple text (CLI tool) | No |

Backends are tried in order until one succeeds. Default order is auto-detected based on GPU availability.

## Project Structure

```
pdf-extractor/
├── .claude-plugin/plugin.json   # Claude Code plugin manifest
├── pyproject.toml               # Package config with uv/pip support
├── uv.lock                      # Locked dependencies
├── commands/extract.md          # Slash command definition
├── skills/pdf-extractor/        # Skill files for Claude
├── src/pdf_extraction/          # Main package
│   ├── __init__.py              # Public API exports
│   ├── backends.py              # 9 backend extractors
│   ├── extractors.py            # extract_single_pdf, pdf_to_txt
│   ├── utils.py                 # GPU detection, quality metrics
│   └── cli.py                   # CLI entry point
└── tests/                       # pytest test suite
```

## Skill Triggers

The plugin skill activates when you ask to:
- "extract text from PDF"
- "convert PDF to markdown"
- "parse PDF contents"
- "read this PDF file"
- "batch extract PDFs"

## Troubleshooting

### `extract-pdfs: command not found`
```bash
# Install as global UV tool from repo root:
cd plugins/pdf-extractor && uv tool install --force --editable . && cd ../..
# Verify:
extract-pdfs --list-backends
```

### `ModuleNotFoundError: No module named 'pdf_extraction'` (or 'markitdown', 'pdfplumber')
```bash
# Re-install with all base dependencies:
cd plugins/pdf-extractor && uv tool install --force --editable . && cd ../..
# If that fails, install explicitly:
uv pip install "markitdown>=0.1.0" "pdfplumber>=0.10.0" "pdfminer.six>=20221105" "PyPDF2>=3.0.0" tqdm
```

### GPU backends (docling, marker) not available
```bash
# These require PyTorch; install optional GPU extras:
cd plugins/pdf-extractor && uv tool install --force --editable ".[gpu]" && cd ../..
# Verify GPU backends appear:
extract-pdfs --list-backends
# Note: docling downloads ~500MB models on first use; marker downloads ~1GB
```

### Empty output from scanned PDF (image-only document)
```bash
# Scanned PDFs require OCR (GPU backends):
extract-pdfs scanned.pdf --backends marker docling
# If GPU unavailable, try pdftotext (system tool):
brew install poppler        # macOS
# apt install poppler-utils  # Ubuntu/Debian
extract-pdfs scanned.pdf --backends pdftotext
```

### pdfminer import error (package name confusion)
```bash
# Install correct package (name has .six suffix):
uv pip install "pdfminer.six>=20221105"
# Imports correctly as: from pdfminer.high_level import extract_text  (no .six)
```

### markitdown version conflict
```bash
# markitdown API changed significantly in 0.1.0; ensure correct version:
uv pip install "markitdown>=0.1.0"
```

### Encrypted PDF
The extractor will warn about encrypted PDFs. Some backends can handle password-free encryption, others will fail. Try different backends with `--backends`.

### Backend not found
```bash
# Check which backends are available:
extract-pdfs --list-backends
# Install missing optional backends:
uv pip install docling marker-pdf pymupdf4llm
```
