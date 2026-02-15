---
description: Extract text from PDF files or directories to markdown
allowed-tools: Bash(*)
---

# PDF Extraction Command

Extract text from PDF documents using the multi-backend extraction system.

## Usage

**Installed CLI (recommended):**
```bash
extract-pdfs "<input_path>" "<output_path>"
```

**Module execution:**
```bash
python -m pdf_extraction "<input_path>" "<output_path>"
```

**Standalone script:**
```bash
python "${CLAUDE_PLUGIN_ROOT}/src/pdf_extraction/cli.py" "<input_path>" "<output_path>"
```

## Parameters

- `input_path`: Path to a PDF file or directory containing PDFs
- `output_path`: (Optional) Path to output file (.md) or directory. Defaults to same location as input.

## Options

- `--backends <backend1> <backend2>`: Specify backends to use (default: auto-detect)
- `--no-resume`: Re-extract all files, ignoring existing outputs
- `--format <md|txt>`: Output format (default: md)
- `--list-backends`: Show available backends

## Examples

Extract single file:
```bash
extract-pdfs document.pdf
```

Extract directory with specific backends:
```bash
extract-pdfs ./pdfs/ ./output/ --backends markitdown pdfplumber
```

List available backends:
```bash
extract-pdfs --list-backends
```
