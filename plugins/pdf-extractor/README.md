# PDF Extractor

Turns PDFs into Markdown or plain text that you or an AI agent can read. It
tries up to nine extraction libraries in turn and keeps the first one that
succeeds.

## Install

`extract-pdfs` ships in autorun. Its Python extraction backends are optional,
so they are not installed unless you request them:

```bash
uv tool install 'autorun-ai[pdf]'
extract-pdfs --list-backends     # what is installed, and what each missing backend needs
```

| Extra | Adds |
|-------|------|
| `pdf` | markitdown, pdfplumber, pdfminer.six, pypdf |
| `pdf-gpu` | docling on Linux and Windows (needs PyTorch; downloads models on first use) |
| `pdf-llm` | pymupdf4llm |
| `pdf-progress` | tqdm progress bars |
| `pdf-all` | all four |

Combine extras as `'autorun-ai[pdf,pdf-gpu]'`, or install from GitHub with `uv
tool install 'autorun-ai[pdf] @
git+https://github.com/ahundt/autorun.git#subdirectory=plugins/autorun'`.
Without any extra `extract-pdfs` still runs. `pdftotext` works once poppler is
installed (`brew install poppler`, `apt install poppler-utils`), and an
extraction attempt names the extra it needs.

`pdf-gpu` installs nothing on macOS, because docling's macOS model stack still
selects a transformers 4.x release with a security advisory. `marker` is not in
any extra, because marker-pdf pins Pillow below its first fully patched
release; install marker-pdf yourself and `--backends marker` uses it.

### In your agent

The `pdf-extractor` plugin adds `/pdf-extractor:extract` and a skill that loads
when you ask an agent to extract, convert, parse or read a PDF.

```bash
uv tool install --force autorun-ai && autorun --install pdf-extractor   # each detected agent

claude plugin marketplace add https://github.com/ahundt/autorun.git    # Claude Code alone
claude plugin install pdf-extractor@autorun
```

`autorun --install` registers all three plugins, `ar`, `autorun-skills`, and `pdf-extractor`, on
every detected agent; `autorun --install pdf-extractor` installs only this
one. Both work from the PyPI package and from a git install. The `[pdf]` extra
adds only the extraction backends, not the plugin. Limit the install to one
agent with `--claude`, `--gemini`, `--qwen`,
`--antigravity`, `--codex`, `--pi`, or `--prime`. Claude, Codex, Gemini,
Qwen, Antigravity, Pi, and Prime load the skill from their installed plugin,
extension, or package. ForgeCode and OpenCode use their own global skill roots;
the default does not write `~/.agents/skills`. In Codex, use `$pdf-extractor` or
`/skills`; `/pdf-extractor:extract` is not a Codex command.

## Usage

```bash
extract-pdfs document.pdf                      # writes document.md
extract-pdfs document.pdf output.md            # choose the output file
extract-pdfs document.pdf --format txt         # plain text
extract-pdfs ./pdfs/ ./output/                 # a folder; files already extracted are skipped
extract-pdfs ./pdfs/ --no-resume               # re-extract everything
extract-pdfs document.pdf --backends pdfplumber markitdown pdfminer   # these, in this order
```

```python
from pdf_extraction import extract_single_pdf, pdf_to_txt

result = extract_single_pdf("document.pdf", "output.md")
files, metadata = pdf_to_txt("./pdfs/", "./output/", return_metadata=True)
```

## Backends

Without `--backends`, GPU backends go first when a CUDA GPU is present and
last otherwise.

| Backend | License | Best for |
|---------|---------|----------|
| markitdown | MIT | general text, forms |
| pdfplumber | MIT | tables, structured data |
| pdfminer | MIT | simple text |
| pypdf2 | BSD-3 | basic extraction through `pypdf` (the id predates the rename) |
| docling | MIT | layout analysis (GPU) |
| marker | GPL-3.0 | scanned documents (GPU) |
| pymupdf4llm | AGPL-3.0 | output tuned for LLMs |
| pdfbox | Apache-2.0 | tables (Java) |
| pdftotext | system | simple text (poppler's CLI) |

## License

Apache License 2.0. See the repository's `LICENSE` file.
