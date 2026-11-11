# Word (.docx)

Build with `docx` (python-docx API). Paste the helpers below, call `setup(doc)` first, and write content only through the
helpers and the styles they set (`Heading 1`, `Heading 2`, `Normal`, `List Bullet`,
`List Number`, `Caption`). Do not set fonts or colors run by run.

## Page anatomy

US Letter, white page, 1 in side margins. From the top of page 1:

1. `masthead()`: a Main Green band with a mono label
   (`PROPERTY SUBMISSION · ACCOUNT NAME`), the title in Fraunces 72pt Light (one or two
   lines) and a meta line; then a 2 to 4 sentence summary that gives the answer.
2. `key_numbers()`: 3 or 4 short figures on Cream cards; the first is marked by the
   one Mango dot beside its label.
3. Sections opened with `heading()`: a numbered mono eyebrow over a Fraunces heading.
   Pass `italic_words` for one to three words at most once or twice per document.
4. `table()` for figures, `callout()` once for the recommendation or decision (Mint),
   `status()` tags inside bullets or cells, `Caption` for notes and sources.

When the user asks for one page, keep it to about 350 words with at most one table
and three key numbers, because nothing here can render the page to confirm it fits.

| Element | Font | Size | Color |
| --- | --- | --- | --- |
| Masthead label / title / meta | IBM Plex Mono Medium / Fraunces 72pt Light / Wix Madefor Display | 8 / 28 / 9 pt | `88E9C0` / Cream / `A9B3AE` on Main Green |
| Summary | Wix Madefor Display | 11.5 pt | Ink |
| Key numbers / labels | Wix Madefor Display / IBM Plex Mono Medium caps | 20 / 7 pt | Ink / Slate, a Mango dot on the first |
| Eyebrow / Heading 1 | IBM Plex Mono Medium caps / Fraunces 72pt Light | 8 / 18 pt | Pine / Ink |
| Body, lists | Wix Madefor Display | 10.5 pt | Ink |
| Table header / cells | Wix Madefor Text SemiBold / Wix Madefor Display | 8.5 / 9.5 pt | Ink, Ink rule under the header |
| Caption, footer | Wix Madefor Display | 8.5 / 8 pt | Slate |

## Helpers

```python
from datetime import date
from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

SERIF = "Fraunces 72pt Light"  # its italic style is embedded too
SANS, UI, MONO = "Wix Madefor Display", "Wix Madefor Text SemiBold", "IBM Plex Mono Medium"
INK, SLATE, STONE, CREAM = "161611", "6F6D64", "DAD8CE", "F7F6F2"
MAIN_GREEN, PINE, MINT, MANGO = "1A4438", "25644F", "D9EAD3", "FB9608"
ON_DARK, ON_DARK_MUTED, ON_DARK_ACCENT = "F7F6F2", "A9B3AE", "88E9C0"
STATUS = {"good": (MINT, PINE), "watch": (MANGO, INK), "problem": (MANGO, INK)}
TODAY = f"{date.today():%b} {date.today().day}, {date.today():%Y}"  # footers carry the date and number only, no company name or logo


def _font(target, size, color, font=SANS, caps=False, tracking=None, italic=False):
    target.font.name, target.font.size = font, Pt(size)
    target.font.bold, target.font.italic, target.font.all_caps = False, italic, caps
    target.font.color.rgb = RGBColor.from_string(color)
    if tracking:
        target.font.spacing = Pt(tracking)
    fonts = target.element.get_or_add_rPr().find(qn("w:rFonts"))
    for attr in list(fonts.attrib):
        del fonts.attrib[attr]
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attr), font)


def _space(p, before=0, after=6, line=1.3):
    pf = p.paragraph_format
    pf.space_before, pf.space_after, pf.line_spacing = Pt(before), Pt(after), line


def _rule(owner, color=STONE, edge="bottom", space=6):
    ppr = owner.element.get_or_add_pPr() if hasattr(owner, "element") else owner._p.get_or_add_pPr()
    for old in ppr.findall(qn("w:pBdr")):
        ppr.remove(old)
    borders = OxmlElement("w:pBdr")
    line = OxmlElement(f"w:{edge}")
    line.set(qn("w:val"), "single"), line.set(qn("w:sz"), "4")
    line.set(qn("w:space"), str(space)), line.set(qn("w:color"), color)
    borders.append(line)
    ppr.append(borders)


_TBLPR_ORDER = ["tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize", "tblStyleColBandSize",
                "tblW", "jc", "tblCellSpacing", "tblInd", "tblBorders", "shd", "tblLayout", "tblCellMar", "tblLook"]


def _tblpr_set(t, el):
    """Put a table property in the position Word's schema requires, replacing any old one."""
    tbl_pr = t._tbl.tblPr
    tag = el.tag.split("}")[1]
    for old in tbl_pr.findall(qn(f"w:{tag}")):
        tbl_pr.remove(old)
    later = _TBLPR_ORDER[_TBLPR_ORDER.index(tag) + 1:]
    for child in tbl_pr:
        if child.tag.split("}")[1] in later:
            child.addprevious(el)
            return
    tbl_pr.append(el)


def _borders(t, size=4, **edges):
    el = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        line = OxmlElement(f"w:{edge}")
        color = edges.get(edge)
        line.set(qn("w:val"), "single" if color else "nil")
        if color:
            line.set(qn("w:sz"), str(size)), line.set(qn("w:color"), color)
        el.append(line)
    _tblpr_set(t, el)
    t.autofit = False


def _pad(cell, inches):
    """Inner side padding as paragraph indents, so the panel's outer edge stays on the margin in every app."""
    for p in cell.paragraphs:
        p.paragraph_format.left_indent = p.paragraph_format.right_indent = Inches(inches)


def _align(t, inset_in=0.0):
    """Make the table span the text column exactly: outer edges on the page margins (keep inset 0)."""
    width = OxmlElement("w:tblW")
    width.set(qn("w:w"), str(int(6.5 * 1440))), width.set(qn("w:type"), "dxa")
    _tblpr_set(t, width)
    indent = OxmlElement("w:tblInd")
    indent.set(qn("w:w"), str(int(inset_in * 1440))), indent.set(qn("w:type"), "dxa")
    _tblpr_set(t, indent)
    margins = OxmlElement("w:tblCellMar")  # no default side padding: every app then puts the edge on the margin
    for side in ("left", "right"):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:w"), "0"), el.set(qn("w:type"), "dxa")
        margins.append(el)
    _tblpr_set(t, margins)


def _shade(target, fill):
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear"), shd.set(qn("w:color"), "auto"), shd.set(qn("w:fill"), fill)
    if hasattr(target, "_tc"):
        target._tc.get_or_add_tcPr().append(shd)
    else:
        target._r.get_or_add_rPr().append(shd)


def _cell_margins(cell, inches, left=None, right=None):
    mar = OxmlElement("w:tcMar")
    for side in ("top", "left", "bottom", "right"):
        value = {"left": left, "right": right}.get(side)
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:w"), str(int((inches if value is None else value) * 1440))), el.set(qn("w:type"), "dxa")
        mar.append(el)
    tc_pr = cell._tc.get_or_add_tcPr()
    for old in tc_pr.findall(qn("w:tcMar")):
        tc_pr.remove(old)
    tc_pr.append(mar)


def setup(doc):
    for section in doc.sections:
        section.page_width, section.page_height = Inches(8.5), Inches(11)
        section.left_margin = section.right_margin = Inches(1)
        section.top_margin, section.bottom_margin = Inches(0.7), Inches(0.8)
    s = doc.styles
    spec = {  # name: (font, size, color, before, after, line)
        "Normal": (SANS, 10.5, INK, 0, 6, 1.35),
        "Title": (SERIF, 28, INK, 0, 6, 1.05),
        "Heading 1": (SERIF, 18, INK, 2, 8, 1.1),
        "Heading 2": (SERIF, 13, INK, 12, 4, 1.15),
        "List Bullet": (SANS, 10.5, INK, 0, 3, 1.35),
        "List Number": (SANS, 10.5, INK, 0, 3, 1.35),
        "Caption": (SANS, 8.5, SLATE, 3, 10, 1.25),
    }
    for name, (font, size, color, before, after, line) in spec.items():
        _font(s[name], size, color, font)
        _space(s[name], before, after, line)
        s[name].paragraph_format.keep_with_next = name.startswith("Heading") or name == "Title"
    for name in ("Title", "Heading 1"):
        ppr = s[name].element.get_or_add_pPr()
        for old in ppr.findall(qn("w:pBdr")):
            ppr.remove(old)
    fonts = s.element.find(qn("w:docDefaults")).find(qn("w:rPrDefault")).find(qn("w:rPr")).find(qn("w:rFonts"))
    for attr in list(fonts.attrib):
        del fonts.attrib[attr]
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attr), SANS)
    doc._section_count = 0
    _footer(doc)


def masthead(doc, label, title, meta, summary):
    """Main Green band with a mono label, Fraunces title and meta line; summary below."""
    t = doc.add_table(rows=1, cols=1)
    _borders(t)
    cell = t.cell(0, 0)
    cell.width = Inches(6.5)
    _shade(cell, MAIN_GREEN)
    _cell_margins(cell, 0.3, left=0, right=0)
    _align(t)
    p = cell.paragraphs[0]
    _space(p, 6, 8)
    _font(p.add_run(label), 8, ON_DARK_ACCENT, MONO, caps=True, tracking=1)
    p = cell.add_paragraph(style="Title")
    _font(p.add_run(title), 28, ON_DARK, SERIF)
    p = cell.add_paragraph()
    _space(p, 0, 6)
    _font(p.add_run(meta), 9, ON_DARK_MUTED)
    _pad(cell, 0.3)
    p = doc.add_paragraph()
    _space(p, 16, 14, 1.4)
    _font(p.add_run(summary), 11.5, INK)


def key_numbers(doc, items):
    """Cream cards. items: [(value, label), ...], 3 or 4 short values. The first figure of the first call
    gets the one Mango dot; a second row of figures gets none."""
    dot, doc._dot_used = not getattr(doc, "_dot_used", False), True
    t = doc.add_table(rows=1, cols=len(items))
    _borders(t, size=36, insideV="FFFFFF")
    _align(t)
    for c, (value, label) in enumerate(items):
        cell = t.cell(0, c)
        cell.width = Inches(6.5 / len(items))
        _shade(cell, CREAM)
        _cell_margins(cell, 0.14, left=0, right=0)
        p = cell.paragraphs[0]
        _space(p, 4, 2, 1.0)
        _font(p.add_run(value), 20, INK, SANS)
        p = cell.add_paragraph()
        _space(p, 2, 4, 1.15)
        if c == 0 and dot:
            _font(p.add_run("\u25cf "), 8, MANGO, SANS)
        _font(p.add_run(label), 7, SLATE, MONO, caps=True, tracking=0.6)
        _pad(cell, 0.14)
    doc.add_paragraph().paragraph_format.space_after = Pt(0)


def heading(doc, text, label=None, italic_words=None):
    """Mono eyebrow ("02 · LABEL", numbered automatically) over a Fraunces heading."""
    doc._section_count += 1
    p = doc.add_paragraph()
    _space(p, 20, 2, 1.0)
    p.paragraph_format.keep_with_next = True
    _font(p.add_run(f"{doc._section_count:02d} · {label or text}"), 8, PINE, MONO, caps=True, tracking=1)
    h = doc.add_paragraph(style="Heading 1")
    parts = [(text, False)]
    if italic_words and italic_words in text:
        a, b = text.split(italic_words, 1)
        parts = [(a, False), (italic_words, True), (b, False)]
    for chunk, italic in parts:
        if chunk:
            _font(h.add_run(chunk), 18, INK, SERIF, italic=italic)
    return h


def table(doc, header, rows, widths_in, numeric_cols=(), highlight=None):
    """highlight: (row_index, col_index) of the one takeaway cell, counted in rows."""
    t = doc.add_table(rows=1, cols=len(header))
    _borders(t, bottom=STONE, insideH=STONE)
    _align(t)
    last = len(header) - 1
    for r, values in enumerate([header] + list(rows)):
        cells = t.rows[0].cells if r == 0 else t.add_row().cells
        for c, value in enumerate(values):
            cell = cells[c]
            cell.width = Inches(widths_in[c])
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            _cell_margins(cell, 0, left=0 if c == 0 else 0.08, right=0 if c == last else 0.08)
            p = cell.paragraphs[0]
            _space(p, 4, 4, 1.15)
            if c in numeric_cols:
                p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            run = p.add_run(str(value))
            if r == 0:
                _font(run, 8.5, INK, UI)
            elif highlight == (r - 1, c):
                _font(run, 9.5, PINE, UI)
            else:
                _font(run, 9.5, INK)
    for cell in t.rows[0].cells:
        borders = OxmlElement("w:tcBorders")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single"), bottom.set(qn("w:sz"), "6"), bottom.set(qn("w:color"), INK)
        borders.append(bottom)
        cell._tc.get_or_add_tcPr().append(borders)
    head = t.rows[0]._tr.get_or_add_trPr()
    repeat = OxmlElement("w:tblHeader")
    repeat.set(qn("w:val"), "true")
    head.append(repeat)
    if len(t.rows) <= 12:  # short tables never break across pages
        for row in t.rows:
            row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
            if row is not t.rows[-1]:
                for cell in row.cells:
                    for para in cell.paragraphs:
                        para.paragraph_format.keep_with_next = True
    return t


def callout(doc, label, text):
    """One Mint panel per document for the recommendation or decision."""
    t = doc.add_table(rows=1, cols=1)
    _borders(t)
    t.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
    cell = t.cell(0, 0)
    cell.width = Inches(6.5)
    _shade(cell, MINT)
    _cell_margins(cell, 0.2, left=0, right=0)
    _align(t)
    p = cell.paragraphs[0]
    _space(p, 2, 4)
    p.paragraph_format.keep_with_next = True
    _font(p.add_run(label), 7.5, PINE, MONO, caps=True, tracking=0.8)
    p = cell.add_paragraph()
    _space(p, 0, 2, 1.35)
    _font(p.add_run(text), 11, INK)
    _pad(cell, 0.2)
    doc.add_paragraph().paragraph_format.space_after = Pt(0)


def status(paragraph, word, kind):
    """A small status tag inside a paragraph or table cell: kind is good, watch or problem."""
    surface, color = STATUS[kind]
    run = paragraph.add_run(f" {word.upper()} ")
    _font(run, 7, color, MONO, tracking=0.6)
    _shade(run, surface)
    return run


def _footer(doc):
    for section in doc.sections:
        p = section.footer.paragraphs[0]
        p.text = ""
        tabs = p.paragraph_format.tab_stops
        tabs.add_tab_stop(Inches(3.25), WD_TAB_ALIGNMENT.CLEAR)
        tabs.add_tab_stop(Inches(6.5), WD_TAB_ALIGNMENT.CLEAR)
        tabs.add_tab_stop(section.page_width - section.left_margin - section.right_margin, WD_TAB_ALIGNMENT.RIGHT)
        _font(p.add_run(TODAY + "\t"), 8, SLATE)
        run = p.add_run()
        for kind, text in (("begin", None), (None, "PAGE"), ("end", None)):
            el = OxmlElement("w:fldChar" if kind else "w:instrText")
            if kind:
                el.set(qn("w:fldCharType"), kind)
            else:
                el.set(qn("xml:space"), "preserve")
                el.text = text
            run._r.append(el)
        _font(run, 8, SLATE)
```

Example:

```python
doc = Document()
setup(doc)
masthead(doc, "Property submission · Crescent Hospitality Group", "Quote Tampa and Fort Myers; condition Sarasota",
         "Prepared for Underwriting · Nov 2, 2026 · Three Florida hotels",
         "Crescent Hospitality Group requests a $96.4M blanket limit on three Florida hotels at a target premium of $1.15M. We recommend quoting Tampa and Fort Myers as requested. Sarasota carries the weakest construction and sits 0.4 miles from the coast, so it should be written only with a roof survey and a 10% named-storm deductible.")
key_numbers(doc, [("$96.4M", "Total insured value"), ("$1.15M", "Target premium"), ("535", "Rooms"), ("$2.55M", "Losses, 5 years")])
heading(doc, "Location schedule")
table(doc, ["Location", "Rooms", "Year built", "TIV ($M)", "Coast (mi)"],
      [["Tampa", "210", "2008", "41.2", "2.1"], ["Sarasota", "145", "1998", "24.8", "0.4"], ["Fort Myers", "180", "2015", "30.4", "6.8"], ["Total", "535", "", "96.4", ""]],
      [2.0, 1.0, 1.1, 1.2, 1.2], numeric_cols=(1, 2, 3, 4), highlight=(1, 4))
doc.add_paragraph("Distance to coast from the submission.", style="Caption")
heading(doc, "Sarasota is the exposure that needs conditions", label="Construction and exposure", italic_words="needs conditions")
p = doc.add_paragraph("Sarasota: joisted masonry, 60% sprinklered, built 1998  ", style="List Bullet")
status(p, "Watch", "watch")
doc.add_paragraph("Tampa and Fort Myers: fire-resistive with full sprinklers", style="List Bullet")
heading(doc, "Recommendation")
callout(doc, "Recommended", "Quote Tampa ($41.2M) and Fort Myers ($30.4M) as requested. For Sarasota ($24.8M), require a roof survey and a 10% named-storm deductible, or exclude it.")
doc.save(out_path)
```

## Embed the fonts, then check

After `doc.save(...)`, always run:

```bash
python "$SKILL_DIR/scripts/embed_fonts.py" output.docx
```

The sandbox cannot render Word files to images, so check the structure:

```python
import zipfile
from datetime import date
from docx import Document

TODAY = f"{date.today():%b} {date.today().day}, {date.today():%Y}"
BRAND_FONTS = {"Fraunces 72pt Light", "Wix Madefor Display", "Wix Madefor Text SemiBold", "IBM Plex Mono Medium"}
doc = Document(out_path)
paragraphs = list(doc.paragraphs)
for t in doc.tables:
    paragraphs += [p for row in t.rows for cell in row.cells for p in cell.paragraphs]
problems = []
for p in paragraphs:
    if "\u2014" in p.text or "\u2013" in p.text:
        problems.append(f"em/en dash in: {p.text[:60]}")
    for run in p.runs:
        if run.text.strip() and run.font.name not in BRAND_FONTS | {None}:
            problems.append(f"font {run.font.name} in: {p.text[:40]}")
        if run.font.bold or (run.font.italic and run.font.name != "Fraunces 72pt Light"):
            problems.append(f"bold, or italic outside Fraunces, in: {p.text[:40]}")
        if run.font.size is not None and run.font.size.pt < 7:
            problems.append(f"text below 7 pt in: {p.text[:40]}")
if TODAY not in doc.sections[0].footer.paragraphs[0].text:
    problems.append("date footer missing")
everything = " ".join(p.text for p in paragraphs) + " ".join(p.text for p in doc.sections[0].footer.paragraphs)
if "furtherai" in everything.lower():
    problems.append("remove the FurtherAI name; documents belong to the user's organization")
with zipfile.ZipFile(out_path) as z:
    if b"embedRegular" not in z.read("word/fontTable.xml"):
        problems.append("brand fonts not embedded: run scripts/embed_fonts.py")
print("\n".join(sorted(set(problems))) or "OK")
```

Fix every line it prints and run it again. Then reread the outline (one masthead,
summary first, numbered sections in order) as the reader would.
