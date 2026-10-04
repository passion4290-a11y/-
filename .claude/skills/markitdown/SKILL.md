---
name: markitdown
description: Convert files and URLs (PDF, Word, PowerPoint, Excel, HTML, CSV, JSON, XML, images, audio, ZIP, YouTube) to Markdown with Microsoft's markitdown. Use when the user wants to read, summarize, search, or extract text/tables from a document file, or asks to "convert to markdown".
---

# markitdown

Converts many document formats into clean Markdown so their content can be read, summarized, or edited as plain text.

## Setup

```bash
pip install 'markitdown[all]'
markitdown --version   # verify
```

Install only the formats needed with extras, e.g. `pip install 'markitdown[pdf,docx,pptx,xlsx]'`.

## Usage

### CLI

```bash
markitdown path/to/file.pdf                 # print Markdown to stdout
markitdown path/to/file.docx -o out.md      # write to a file
cat file.pptx | markitdown > out.md         # read from stdin
```

### Python

```python
from markitdown import MarkItDown

md = MarkItDown()
result = md.convert("path/to/file.xlsx")    # local path or http(s) URL
print(result.text_content)
```

## Supported inputs

| Format | Notes |
| --- | --- |
| PDF | Text-based PDFs only; scanned PDFs need OCR first |
| Word (`.docx`) | Headings, lists, tables preserved |
| PowerPoint (`.pptx`) | One section per slide, speaker notes included |
| Excel (`.xlsx`, `.xls`) | Each sheet becomes a Markdown table |
| HTML, CSV, JSON, XML | Converted to structured Markdown |
| Images | EXIF metadata; OCR/description needs an LLM client |
| Audio | Metadata and speech transcription |
| ZIP | Each contained file is converted |
| YouTube URL | Transcript |

## Workflow

1. Confirm the input path or URL exists.
2. Run `markitdown <input>`; for long output write to a file with `-o` and read only the parts needed.
3. If the result is empty or garbled, check the format-specific extra is installed (`pip install 'markitdown[all]'`) and whether the PDF is scanned.
4. Show the user the relevant converted Markdown, not the whole file, unless asked.

## Notes

- Output is intended for LLM/text analysis, not high-fidelity visual reproduction.
- Run conversions only on files the user provided; do not fetch arbitrary URLs unprompted.
