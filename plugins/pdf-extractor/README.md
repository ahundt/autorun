# PDF Extractor Plugin

Extract text and structured data from PDF documents using a multi-backend approach with automatic fallback.

## Installation

### AI Harnesses

From an autorun source checkout, install the plugin and skill for the detected
supported harnesses:

```bash
autorun --install pdf-extractor --force
```

For Claude Code, install it directly from the repository marketplace:

```bash
claude plugin marketplace add https://github.com/ahundt/autorun.git
claude plugin install pdf-extractor@autorun
```

The standalone autorun wheel embeds only the `ar` plugin's harness assets. Its
`autorun --install pdf-extractor` selection is therefore available only when
running from a source marketplace checkout that contains both plugin trees. The
extraction code itself is always present — see below.

Target one harness with `--claude`, `--gemini`, `--qwen`, `--antigravity`, or
`--codex`. Claude, Gemini, Qwen, and Antigravity use native per-plugin skills.
Codex and ForgeCode load `$pdf-extractor` from the shared
`~/.agents/skills/pdf-extractor/` route using the same ownership and upgrade
rules as autorun's other global skills.

### Python CLI

There is no separate package to install. `extract-pdfs` and the `pdf_extraction`
module ship inside the `autorun` distribution, and every extraction library is an
optional extra. `pdf` installs markitdown, pdfplumber, pdfminer, and pypdf:

```bash
uv tool install 'autorun[pdf]'
```

For the Linux/Windows GPU backend (docling):
```bash
uv tool install --force 'autorun[pdf,pdf-gpu]'
```

Source checkout installation:

```bash
uv tool install 'autorun[pdf] @ git+https://github.com/ahundt/autorun.git#subdirectory=plugins/autorun'
```

| Extra | Adds |
|-------|------|
| `pdf` | markitdown, pdfplumber, pdfminer.six, pypdf |
| `pdf-gpu` | docling on Linux/Windows (needs PyTorch; downloads models on first use) |
| `pdf-llm` | pymupdf4llm |
| `pdf-progress` | tqdm progress bars (falls back to no output when absent) |
| `pdf-all` | all four above |

The `marker` backend id remains available for users who manage that dependency
separately. It is not in a published extra because marker-pdf's supported
platform graph pins Pillow below the first fully patched release. The `pdf-gpu`
extra is empty on macOS because docling's macOS model stack still selects an
advisory-affected transformers 4.x release.

Plain `uv tool install autorun` still gives you the CLI: `--list-backends`
reports what is missing, and an extraction attempt names the extra to install.
The only backend available without an extra is `pdftotext`, if poppler is on the
system. Nobody who never touches a PDF downloads an extraction library.

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
| pypdf2 | BSD-3 | Basic extraction through `pypdf`; CLI id retained for compatibility |
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

Apache License 2.0. See the repository's `LICENSE` file.
