#!/usr/bin/env python3
"""Extract reviewable text with stable locations from a document.

Supports .docx, .pptx, .pdf, .md, .txt, .html. Every extracted unit carries a
LOCATION token that addresses it precisely, so review findings can be written
back against the source without guessing.

Location tokens:
  docx   p:12            paragraph index (0-based, body order)
         p:12/r:3        run index inside that paragraph
         tbl:1/r:2/c:0   table / row / cell
         hdr:0/p:1       header part
         ftr:0/p:1       footer part
  pptx   s:3/sh:2/p:0    slide / shape / paragraph (1-based slide)
         s:3/tbl:1/r:0/c:2
         s:3/notes/p:0
  pdf    pg:4/l:17       page / line (text-layer only; no OCR)
  md/txt/html
         l:42            line number (1-based)

Usage:
  extract_text.py <file> [--json] [--min-chars N] [--include-notes]
  extract_text.py <file> --stats

Exit codes: 0 ok, 1 usage/read error, 2 missing optional dependency.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _fail(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _unit(loc: str, text: str, kind: str = "body", **extra):
    u = {"loc": loc, "kind": kind, "text": text}
    u.update(extra)
    return u


# ---------------------------------------------------------------- docx -----
def extract_docx(path: Path, include_notes: bool = False):
    """Parse the OOXML directly — no python-docx required, and it keeps table
    and header/footer text in document order."""
    units = []
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "word/document.xml" not in names:
            _fail(f"{path.name} is not a Word document (no word/document.xml)")
        root = ET.fromstring(z.read("word/document.xml"))
        body = root.find(f"{W_NS}body")
        if body is None:
            _fail("word/document.xml has no <w:body>")

        p_idx = 0
        tbl_idx = 0
        for child in body:
            if child.tag == f"{W_NS}p":
                text = _docx_para_text(child)
                style = _docx_para_style(child)
                if text.strip():
                    units.append(_unit(f"p:{p_idx}", text, kind="paragraph", style=style))
                p_idx += 1
            elif child.tag == f"{W_NS}tbl":
                for r_i, row in enumerate(child.findall(f"{W_NS}tr")):
                    for c_i, cell in enumerate(row.findall(f"{W_NS}tc")):
                        cell_text = " ".join(
                            _docx_para_text(p) for p in cell.findall(f".//{W_NS}p")
                        ).strip()
                        if cell_text:
                            units.append(
                                _unit(f"tbl:{tbl_idx}/r:{r_i}/c:{c_i}", cell_text, kind="table-cell")
                            )
                tbl_idx += 1

        if include_notes:
            for part_kind, prefix in (("header", "hdr"), ("footer", "ftr")):
                parts = sorted(n for n in names if re.match(rf"word/{part_kind}\d+\.xml$", n))
                for i, name in enumerate(parts):
                    proot = ET.fromstring(z.read(name))
                    for j, p in enumerate(proot.findall(f".//{W_NS}p")):
                        text = _docx_para_text(p)
                        if text.strip():
                            units.append(_unit(f"{prefix}:{i}/p:{j}", text, kind=part_kind))
    return units


def _docx_para_text(p) -> str:
    out = []
    for node in p.iter():
        if node.tag == f"{W_NS}t":
            out.append(node.text or "")
        elif node.tag == f"{W_NS}tab":
            out.append("\t")
        elif node.tag in (f"{W_NS}br", f"{W_NS}cr"):
            out.append("\n")
    return "".join(out)


def _docx_para_style(p) -> str:
    ppr = p.find(f"{W_NS}pPr")
    if ppr is None:
        return ""
    style = ppr.find(f"{W_NS}pStyle")
    return style.get(f"{W_NS}val", "") if style is not None else ""


# ---------------------------------------------------------------- pptx -----
def extract_pptx(path: Path, include_notes: bool = False):
    try:
        from pptx import Presentation  # type: ignore
    except ImportError:
        return _extract_pptx_xml(path, include_notes)

    units = []
    prs = Presentation(str(path))
    for s_i, slide in enumerate(prs.slides, start=1):
        tbl_i = 0
        for sh_i, shape in enumerate(slide.shapes):
            if shape.has_table:
                for r_i, row in enumerate(shape.table.rows):
                    for c_i, cell in enumerate(row.cells):
                        text = cell.text.strip()
                        if text:
                            units.append(
                                _unit(f"s:{s_i}/tbl:{tbl_i}/r:{r_i}/c:{c_i}", text, kind="table-cell")
                            )
                tbl_i += 1
                continue
            if not shape.has_text_frame:
                continue
            for p_i, para in enumerate(shape.text_frame.paragraphs):
                text = "".join(r.text for r in para.runs).strip()
                if text:
                    units.append(
                        _unit(
                            f"s:{s_i}/sh:{sh_i}/p:{p_i}",
                            text,
                            kind="slide-text",
                            shape_name=shape.name,
                            size_pt=_pptx_first_size(para),
                        )
                    )
        if include_notes and slide.has_notes_slide:
            nf = slide.notes_slide.notes_text_frame
            if nf is not None:
                for p_i, para in enumerate(nf.paragraphs):
                    text = para.text.strip()
                    if text:
                        units.append(_unit(f"s:{s_i}/notes/p:{p_i}", text, kind="notes"))
    return units


def _pptx_first_size(para):
    for r in para.runs:
        if r.font.size is not None:
            return round(r.font.size.pt, 1)
    return None


def _extract_pptx_xml(path: Path, include_notes: bool):
    """Fallback when python-pptx is absent. Slide order comes from the numeric
    suffix, which matches presentation order in every deck these skills build."""
    units = []
    with zipfile.ZipFile(path) as z:
        slides = sorted(
            (n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
            key=lambda n: int(re.search(r"(\d+)", n).group(1)),
        )
        for s_i, name in enumerate(slides, start=1):
            root = ET.fromstring(z.read(name))
            for sh_i, sp in enumerate(root.iter(f"{A_NS}txBody")):
                for p_i, p in enumerate(sp.findall(f"{A_NS}p")):
                    text = "".join(t.text or "" for t in p.iter(f"{A_NS}t")).strip()
                    if text:
                        units.append(_unit(f"s:{s_i}/sh:{sh_i}/p:{p_i}", text, kind="slide-text"))
        if include_notes:
            notes = sorted(
                (n for n in z.namelist() if re.match(r"ppt/notesSlides/notesSlide\d+\.xml$", n)),
                key=lambda n: int(re.search(r"(\d+)", n).group(1)),
            )
            for s_i, name in enumerate(notes, start=1):
                root = ET.fromstring(z.read(name))
                for p_i, p in enumerate(root.iter(f"{A_NS}p")):
                    text = "".join(t.text or "" for t in p.iter(f"{A_NS}t")).strip()
                    if text:
                        units.append(_unit(f"s:{s_i}/notes/p:{p_i}", text, kind="notes"))
    return units


# ----------------------------------------------------------------- pdf -----
def extract_pdf(path: Path):
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except ImportError:
            _fail(
                "reading .pdf needs pypdf: python3 -m pip install pypdf. "
                "Alternative: open the PDF with the Read tool and paste the text.",
                code=2,
            )
    units = []
    reader = PdfReader(str(path))
    for pg_i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for l_i, line in enumerate(text.splitlines(), start=1):
            if line.strip():
                units.append(_unit(f"pg:{pg_i}/l:{l_i}", line.strip(), kind="pdf-line"))
    if not units:
        print(
            "WARNING: no text layer found — this PDF is likely scanned images. "
            "Read it with the Read tool instead.",
            file=sys.stderr,
        )
    return units


# ------------------------------------------------------------ plain text ---
def extract_lines(path: Path):
    units = []
    for l_i, line in enumerate(path.read_text(errors="replace").splitlines(), start=1):
        if line.strip():
            units.append(_unit(f"l:{l_i}", line.rstrip(), kind="line"))
    return units


EXTRACTORS = {
    ".docx": extract_docx,
    ".pptx": extract_pptx,
    ".pdf": lambda p, include_notes=False: extract_pdf(p),
    ".md": lambda p, include_notes=False: extract_lines(p),
    ".markdown": lambda p, include_notes=False: extract_lines(p),
    ".txt": lambda p, include_notes=False: extract_lines(p),
    ".html": lambda p, include_notes=False: extract_lines(p),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--json", action="store_true", help="emit the unit list as JSON")
    ap.add_argument("--min-chars", type=int, default=0, help="skip units shorter than this")
    ap.add_argument("--include-notes", action="store_true",
                    help="also extract speaker notes (pptx) / headers and footers (docx)")
    ap.add_argument("--stats", action="store_true", help="print counts only, no text")
    args = ap.parse_args()

    path = Path(args.file).expanduser()
    if not path.exists():
        _fail(f"no such file: {path}")
    ext = path.suffix.lower()
    if ext not in EXTRACTORS:
        _fail(f"unsupported file type {ext or '(none)'} — supported: {', '.join(sorted(EXTRACTORS))}")

    units = EXTRACTORS[ext](path, args.include_notes) if ext in (".docx", ".pptx") else EXTRACTORS[ext](path)
    if args.min_chars:
        units = [u for u in units if len(u["text"]) >= args.min_chars]

    if args.stats or not units:
        words = sum(len(u["text"].split()) for u in units)
        kinds = {}
        for u in units:
            kinds[u["kind"]] = kinds.get(u["kind"], 0) + 1
        print(f"file:  {path}")
        print(f"units: {len(units)}")
        print(f"words: {words}")
        for k, v in sorted(kinds.items()):
            print(f"  {k}: {v}")
        if args.stats:
            return 0
        if not units:
            return 0

    if args.json:
        print(json.dumps(units, indent=2, ensure_ascii=False))
    else:
        for u in units:
            print(f"[{u['loc']}] {u['text']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
