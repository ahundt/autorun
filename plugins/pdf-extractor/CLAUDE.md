# PDF Extractor plugin: development guide

Install, the extras, usage and the backend table are in [README.md](README.md);
this file is for changing the code.

## Layout

This directory has no `pyproject.toml` or `uv.lock`, because the code ships
inside `autorun-ai` behind the `pdf` extras.

```
plugins/pdf-extractor/
├── .claude-plugin/plugin.json      # Claude Code plugin manifest
├── gemini-extension.json           # Gemini-family manifest (root layout: the installer treats it as the template)
├── commands/extract.md             # /pdf-extractor:extract
├── skills/pdf-extractor/           # the skill
├── src/pdf_extraction/             # the package; plugins/autorun/src/pdf_extraction is a symlink here
│   ├── __init__.py                 # public API
│   ├── backends.py                 # the 9 backend extractors, BACKEND_REGISTRY order
│   ├── extractors.py               # extract_single_pdf, pdf_to_txt
│   ├── utils.py                    # GPU detection, quality metrics, encryption check
│   └── cli.py                      # extract-pdfs
└── tests/                          # pytest suite
```

`plugins/autorun/pyproject.toml` declares the extras and the `extract-pdfs`
entry point.

## Develop and test

From the repository root:

```bash
uv run --project plugins/autorun --locked --extra pdf \
  pytest plugins/pdf-extractor/tests/ -v
uvx ruff check plugins/pdf-extractor/src/pdf_extraction plugins/pdf-extractor/tests
uv run --project plugins/autorun --extra pdf extract-pdfs document.pdf   # try the CLI on your edits
```

`--extra pdf` makes the backends importable. Without it every backend reports
unavailable and the suite still passes, so it proves nothing about extraction.

Also `uv run --project plugins/autorun python -m pdf_extraction document.pdf`,
or `python plugins/pdf-extractor/src/pdf_extraction/cli.py document.pdf`
(cli.py sets up its own imports). Installing `extract-pdfs` as a tool
(`uv tool install --editable`) changes the machine's PATH, so do it in a
sandbox or on the user's instruction (root [AGENTS.md](../../AGENTS.md)).

`extract_single_pdf` returns a dict with `backend_used` (or `None`),
`extraction_time_seconds`, `output_size_bytes` and `encrypted`; `pdf_to_txt(...,
return_metadata=True)` returns that dict per input file. With no backend
installed, `extract_single_pdf` names the extra to install
(`extractors.py`); keep that message, since it is the only hint a base install
gives.

## Installing the plugin into agents

`autorun --install pdf-extractor --force` runs from a clone only, because the
`autorun-ai` wheel carries just the `ar` plugin's assets, and like every install
it runs in a sandbox unless the user asked for the live machine (root
[AGENTS.md](../../AGENTS.md)). Limit it with `--claude`, `--gemini`, `--qwen`,
`--antigravity`, `--codex`, `--pi`, or `--prime`, the selection flags
`autorun --help` lists. Under the default skill placement, plugin-capable
harnesses use the plugin, extension, or package copy; ForgeCode and OpenCode
use their own global skill roots. The default does not write
`~/.agents/skills/pdf-extractor/`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `extract-pdfs: command not found` | not installed as a tool: `uv tool install 'autorun-ai[pdf]'`, or run it through `uv run --project plugins/autorun` |
| "No extraction backend installed" | no extra installed: add `[pdf]`; `extract-pdfs --list-backends` then lists them under available, not "Supported but not installed" |
| `ModuleNotFoundError` for `pdf_extraction`, `markitdown` or `pdfplumber` | reinstall with `[pdf]`; failing that, `uv pip install "markitdown>=0.1.0" "pdfplumber>=0.10.0" "pdfminer.six>=20221105" "pypdf>=6.0.0" tqdm` |
| `pdfminer` import error | the package is `pdfminer.six`, imported as `pdfminer` |
| markitdown API errors | markitdown's API changed in 0.1.0: `uv pip install "markitdown>=0.1.0"` |
| docling missing | `[pdf,pdf-gpu]` on Linux or Windows; it needs PyTorch and downloads models on first use |
| empty output from a scanned PDF | the text is an image: `--backends docling`, or `pdftotext` after `brew install poppler` / `apt install poppler-utils` |
| encrypted PDF | the extractor warns; some backends read password-free encryption, so try others with `--backends` |
| other backends | `uv pip install "docling>=2.94.0" pymupdf4llm`, then `extract-pdfs --list-backends` |
