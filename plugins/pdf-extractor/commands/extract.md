---
description: Extract text from PDF files or directories to markdown
allowed-tools: Bash(*)
---

# PDF Extraction Command

Extract text from PDF documents using the multi-backend extraction system.

## Usage

To extract a single PDF file:
```bash
python "${CLAUDE_PLUGIN_ROOT}/skills/pdf-extraction/scripts/extract_pdfs.py" "<input_path>" "<output_path>"
```

To extract all PDFs in a directory:
```bash
python "${CLAUDE_PLUGIN_ROOT}/skills/pdf-extraction/scripts/extract_pdfs.py" "<input_dir>" "<output_dir>"
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
python "${CLAUDE_PLUGIN_ROOT}/skills/pdf-extraction/scripts/extract_pdfs.py" document.pdf
```

Extract directory with specific backends:
```bash
python "${CLAUDE_PLUGIN_ROOT}/skills/pdf-extraction/scripts/extract_pdfs.py" ./pdfs/ ./output/ --backends markitdown pdfplumber
```

List available backends:
```bash
python "${CLAUDE_PLUGIN_ROOT}/skills/pdf-extraction/scripts/extract_pdfs.py" --list-backends
```
