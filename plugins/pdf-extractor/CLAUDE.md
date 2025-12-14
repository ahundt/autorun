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

### Using uv (Recommended)

```bash
cd ~/.claude/clautorun/plugins/pdf-extractor

# Create virtual environment and install
uv venv
source .venv/bin/activate
uv pip install -e .

# Verify installation
extract-pdfs --list-backends
```

### Optional GPU Backends

For GPU-accelerated extraction (recommended for scanned documents):

```bash
uv pip install -e ".[gpu]"  # Installs docling + marker-pdf
```

### Development Setup

```bash
uv pip install -e ".[dev]"  # Adds pytest, pytest-cov, ruff
python -m pytest tests/ -v  # Run tests
ruff check src/ tests/      # Run linter
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
├── skills/pdf-extraction/       # Skill files for Claude
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

### Backend not found
```bash
# Check which backends are available
extract-pdfs --list-backends

# Install missing optional backends
uv pip install docling marker-pdf pymupdf4llm
```

### Encrypted PDF
The extractor will warn about encrypted PDFs. Some backends can handle password-free encryption, others will fail. Try different backends with `--backends`.

### Empty output
Some PDFs contain only images (scanned). Use GPU backends (docling, marker) for OCR:
```bash
extract-pdfs scanned.pdf --backends marker docling
```
