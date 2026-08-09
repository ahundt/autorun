# PDF Extractor Plugin

Extract text and structured data from PDF documents using a multi-backend approach with automatic fallback.

## Installation

### AI Harnesses

From an autorun source checkout, install the plugin and skill for every detected
supported harness:

```bash
autorun --install pdf-extractor --force
```

For Claude Code, install it directly from the repository marketplace:

```bash
claude plugin marketplace add https://github.com/ahundt/autorun.git
claude plugin install pdf-extractor@autorun
```

The standalone autorun wheel embeds only the `ar` plugin. Its
`autorun --install pdf-extractor` selection is therefore available only when
running from a source marketplace checkout that contains both plugin trees.

Target one harness with `--claude`, `--gemini`, `--qwen`, `--antigravity`, or
`--codex`. Claude, Gemini, Qwen, and Antigravity use native per-plugin skills.
Codex installs `$pdf-extractor` into `~/.agents/skills/pdf-extractor/` using the
same ownership and upgrade rules as autorun's other global skills. ForgeCode
does not currently expose a skill API.

### Python CLI

```bash
uv tool install 'git+https://github.com/ahundt/autorun.git#subdirectory=plugins/pdf-extractor'
```

For GPU-accelerated backends (docling, marker):
```bash
uv tool install --force 'pdf-extractor[gpu] @ git+https://github.com/ahundt/autorun.git#subdirectory=plugins/pdf-extractor'
```

## Usage

### Single File
```bash
extract-pdfs document.pdf
# Output: document.md
```

### Batch Directory
```bash
extract-pdfs /path/to/pdfs/ /path/to/output/
```

### Custom Backend Order
```bash
extract-pdfs document.pdf --backends pdfplumber markitdown pdfminer
```

### List Available Backends
```bash
extract-pdfs --list-backends
```

### Python API
```python
from pdf_extraction import extract_single_pdf, pdf_to_txt

result = extract_single_pdf("document.pdf", "output.md")
files, metadata = pdf_to_txt("./pdfs/", "./output/", return_metadata=True)
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

Use the harness's native skill picker. In Codex, invoke `$pdf-extractor` or
select it from `/skills`; `/pdf-extractor:extract` is the plugin command surface,
not the Codex skill invocation.

## License

Apache License 2.0 — see the repository's `LICENSE` file.
