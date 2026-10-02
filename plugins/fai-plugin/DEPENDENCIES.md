# Dependencies

Most platform operations use Python 3.9+ and the standard library. Artifact
creation and a few import formats need additional software. Install only what
your work requires.

## Capability matrix

| Capability | Required | Optional or fallback |
|---|---|---|
| Setup, accounts, workflow operations, workflow authoring, triage, and accuracy reports | Python 3.9+ | Linear MCP for reading ticket details in `fix`; Logfire MCP for backend traces in `triage` |
| Eval Studio with CSV, JSON, or folders | Python 3.9+ | None |
| Eval Studio with Excel files | `pandas`, `openpyxl` | Convert the input to CSV or JSON instead |
| PowerPoint creation, inspection, and linting | `python-pptx`; bundled brand fonts installed through `fonts.py` | Pillow improves logo handling; `fonttools` is only needed when installing font aliases |
| Full PowerPoint rendering | LibreOffice plus one PDF-to-image tool: Poppler, ImageMagick, or macOS `sips` | macOS Quick Look renders slide 1 only and is not a complete review |
| Word document creation and inspection | `python-docx`; bundled brand fonts installed through `fonts.py` | Pillow improves wordmark cropping |
| Designed PDF creation | Chrome, Chromium, or Microsoft Edge | Without a supported browser, `build_pdf.py` keeps the self-contained HTML but cannot create the PDF |
| Automated DOCX to PDF conversion | LibreOffice | Microsoft Word can export the DOCX manually; the DOCX remains the deliverable when conversion is unavailable |
| FurtherAI app screenshots | Chrome, Chromium, or Microsoft Edge | Pillow is required for cropping and annotation |
| Copy review of DOCX and PPTX | Python 3.9+ | `python-docx` and `python-pptx` are not required because the extractor has OOXML fallbacks |
| Copy review of PDF files | `pypdf` or `PyPDF2` | Use extracted or pasted text when neither package is installed |

## Python packages by task

For deck and document work:

```bash
python3 -m pip install python-pptx python-docx Pillow
```

For PDF copy review:

```bash
python3 -m pip install pypdf
```

For Excel uploads to Eval Studio:

```bash
python3 -m pip install pandas openpyxl
```

For optional font alias generation:

```bash
python3 -m pip install fonttools
```

## Brand fonts

Font files ship with the plugin. Check or install them with:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deck/scripts/fonts.py" check
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deck/scripts/fonts.py" install
```

Font installation changes the current user's font directory. Run `install`
only when the user approves that machine-level change.

## External integrations

`fix` can read a Linear ticket and its comments when Linear MCP is connected.
Without it, the user can paste the ticket and comments.

`triage` always starts from the FurtherAI platform API. A connected Logfire MCP
adds backend spans, exceptions, and function-step logs when the execution record
does not explain the failure.

No skill should install packages, applications, fonts, or integrations without
the user's approval.
