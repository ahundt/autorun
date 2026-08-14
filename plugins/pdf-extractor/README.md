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
Codex and ForgeCode load `$pdf-extractor` from the shared
`~/.agents/skills/pdf-extractor/` route using the same ownership and upgrade
rules as autorun's other global skills.

### Python CLI

Every extraction backend is an extra, so pick the ones you want. `cpu` covers
the CPU backends (markitdown, pdfplumber, pdfminer, pypdf) and is the ordinary
choice:

```bash
uv tool install 'pdf-extractor[cpu]'
```

For the Linux/Windows GPU backend (docling):
```bash
uv tool install --force 'pdf-extractor[cpu,gpu]'
```

To install the current source instead of a published release:

```bash
uv tool install 'pdf-extractor[cpu] @ git+https://github.com/ahundt/autorun.git#subdirectory=plugins/pdf-extractor'
```

| Extra | Adds |
|-------|------|
| `cpu` | markitdown, pdfplumber, pdfminer.six, pypdf |
| `gpu` | docling on Linux/Windows (needs PyTorch; downloads models on first use) |
| `llm` | pymupdf4llm |
| `progress` | tqdm progress bars (falls back to no output when absent) |
| `all` | every extra above |

The `marker` backend id remains available for users who manage that dependency
separately. It is not in a published extra because marker-pdf's supported
platform graph pins Pillow below the first fully patched release. The `gpu`
extra is empty on macOS because docling's macOS model stack still selects an
advisory-affected transformers 4.x release.

Installing with no extra still works: the CLI runs, `--list-backends` reports
what is missing, and an extraction attempt names the extra to install. The only
backend available without any extra is `pdftotext`, if poppler is on the system.

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
| pypdf2 | BSD-3 | Basic extraction through maintained `pypdf`; CLI id retained for compatibility |
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
