# PDF Extractor Plugin

Extract text and structured data from PDF documents using a multi-backend approach with automatic fallback.

## Installation

```bash
cd ~/.claude/clautorun/plugins/pdf-extractor
uv venv
source .venv/bin/activate
uv pip install -e .
```

For GPU-accelerated backends (docling, marker):
```bash
uv pip install docling marker-pdf
```

## Usage

### Single File
```bash
python skills/pdf-extraction/scripts/extract_pdfs.py document.pdf
# Output: document.md
```

### Batch Directory
```bash
python skills/pdf-extraction/scripts/extract_pdfs.py /path/to/pdfs/ /path/to/output/
```

### Custom Backend Order
```bash
python skills/pdf-extraction/scripts/extract_pdfs.py document.pdf --backends pdfplumber markitdown pdfminer
```

### List Available Backends
```bash
python skills/pdf-extraction/scripts/extract_pdfs.py --list-backends
```

## Available Backends

| Backend | License | Best For |
|---------|---------|----------|
| markitdown | MIT | General text, forms |
| pdfplumber | MIT | Tables, structured data |
| pdfminer | MIT | Simple text documents |
| pypdf2 | BSD-3 | Basic extraction |
| docling | MIT | Layout analysis (GPU) |
| marker | GPL-3.0 | Scanned documents (GPU) |
| pymupdf4llm | AGPL-3.0 | LLM-optimized output |
| pdfbox | Apache-2.0 | Tables (Java-based) |
| pdftotext | System | Simple text (CLI) |

## Skill Triggers

This skill activates when you ask to:
- "extract text from PDF"
- "convert PDF to markdown"
- "parse PDF contents"
- "read this PDF file"
- "batch extract PDFs"

## License

MIT
