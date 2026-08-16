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

### Cross-Harness Plugin Install

```bash
# From the repository root; installs the selected plugin's commands and skill.
autorun --install pdf-extractor --force
```

Use `--claude`, `--gemini`, `--qwen`, `--antigravity`, `--codex`, `--pi`, or
`--prime` to target one harness — the full set `autorun --help` accepts. Codex,
ForgeCode, Pi, and Prime Agent load `$pdf-extractor` through the shared
`~/.agents/skills/pdf-extractor/` route.

### Using uv tool install (Recommended — makes extract-pdfs globally available)

There is no separate package. `extract-pdfs` and the `pdf_extraction` module
ship inside the `autorun` distribution, and every extraction backend lives in an
optional extra, so name the ones you want. `pdf` gets the CPU backends and is
the ordinary choice:

```bash
uv tool install 'autorun[pdf]'
extract-pdfs --list-backends
```

For an editable source checkout, from the repository root:

```bash
uv tool install --force --editable "./plugins/autorun[pdf]"

# Verify:
extract-pdfs --list-backends
```

| Extra | Adds |
|-------|------|
| `pdf` | markitdown, pdfplumber, pdfminer.six, pypdf |
| `pdf-gpu` | docling on Linux/Windows |
| `pdf-llm` | pymupdf4llm |
| `pdf-progress` | tqdm progress bars |
| `pdf-all` | every extra above |

The `marker` backend id remains discoverable when users install marker-pdf
separately. It is excluded from published extras because its supported-platform
dependency graph pins Pillow below the first fully patched release. The
`pdf-gpu` extra is empty on macOS because docling's macOS model stack still
selects an advisory-affected transformers 4.x release.

Installing with no extra is supported and leaves `pdftotext` as the only usable
backend, if poppler is on the system. An extraction attempt with no backend
installed names the extra to install rather than failing silently
(`plugins/pdf-extractor/src/pdf_extraction/extractors.py:extract_single_pdf`).

### Optional GPU backend

For GPU-accelerated extraction on Linux or Windows:

```bash
uv tool install --force --editable "./plugins/autorun[pdf,pdf-gpu]"
# Requires PyTorch; docling downloads models on first use.
extract-pdfs --list-backends  # Verify docling appears
```

### Venv Install (alternative — installs into current venv only)

```bash
uv pip install -e "./plugins/autorun[pdf]"
```

### Development Setup

Run from the repository root. The tests live in autorun's suite because the code
ships in autorun's distribution; `--extra pdf` is what makes the backends
importable, and without it they are all reported unavailable and still pass.

```bash
uv run --project plugins/autorun --locked --extra pdf \
  pytest plugins/pdf-extractor/tests/ -v
uv run ruff check \
  plugins/pdf-extractor/src/pdf_extraction plugins/pdf-extractor/tests
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
python plugins/pdf-extractor/src/pdf_extraction/cli.py document.pdf
```

## Available Backends

| Backend | License | Best For | GPU |
|---------|---------|----------|-----|
| markitdown | MIT | General text, forms | No |
| pdfplumber | MIT | Tables, structured data | No |
| pdfminer | MIT | Simple text documents | No |
| pypdf2 | BSD-3 | Basic extraction through maintained `pypdf` | No |
| docling | MIT | Layout analysis | Yes |
| marker | GPL-3.0 | Scanned documents, OCR | Yes |
| pymupdf4llm | AGPL-3.0 | LLM-optimized output | No |
| pdfbox | Apache-2.0 | Tables (Java-based) | No |
| pdftotext | System | Simple text (CLI tool) | No |

Backends are tried in order until one succeeds. Default order is auto-detected based on GPU availability.

## Project Structure

The harness plugin and the Python code live apart on purpose. This is a plugin
in every harness, and it is not a Python distribution: there is no
`pyproject.toml` and no `uv.lock` here, because the code ships inside `autorun`.

```
plugins/pdf-extractor/            # The harness plugin
├── .claude-plugin/plugin.json    # Claude Code plugin manifest
├── gemini-extension.json         # Gemini-family manifest
├── commands/extract.md           # Slash command definition
└── skills/pdf-extractor/         # Skill files

plugins/autorun/                  # The distribution that ships the code
├── pyproject.toml                # `pdf` extra, extract-pdfs entry point
├── src/pdf_extraction/           # Main package
│   ├── __init__.py               # Public API exports
│   ├── backends.py               # 9 backend extractors
│   ├── extractors.py             # extract_single_pdf, pdf_to_txt
│   ├── utils.py                  # GPU detection, quality metrics
│   └── cli.py                    # CLI entry point
└── tests/pdf_extraction/         # pytest test suite
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
uv tool install --force --editable "./plugins/autorun[pdf]"
# Verify:
extract-pdfs --list-backends
```

### "No extraction backend installed"
The backends are extras and none is installed. Add one:
```bash
uv tool install --force --editable "./plugins/autorun[pdf]"
extract-pdfs --list-backends  # confirm they moved out of "Supported but not installed"
```

### `ModuleNotFoundError: No module named 'pdf_extraction'` (or 'markitdown', 'pdfplumber')
```bash
# Re-install with the cpu backends:
uv tool install --force --editable "./plugins/autorun[pdf]"
# If that fails, install explicitly:
uv pip install "markitdown>=0.1.0" "pdfplumber>=0.10.0" "pdfminer.six>=20221105" "pypdf>=6.0.0" tqdm
```

### GPU backend (docling) not available
```bash
# This requires PyTorch; install the optional GPU extra:
uv tool install --force --editable "./plugins/autorun[pdf,pdf-gpu]"
extract-pdfs --list-backends  # verify docling appears
# Note: docling downloads models on first use.
```

### Empty output from scanned PDF (image-only document)
```bash
# Scanned PDFs require OCR; docling is in the supported GPU extra:
extract-pdfs scanned.pdf --backends docling
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
# Install supported optional backends:
uv pip install "docling>=2.94.0" pymupdf4llm
```
