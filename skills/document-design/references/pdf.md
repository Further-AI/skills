# PDF

Build PDFs with WeasyPrint through the `PdfDoc` helpers below. Set `SKILL_DIR` to this
skill's directory first. A PDF always carries its fonts, so the brand fonts in
`assets/fonts/` are embedded automatically; there is no separate embed step.

## Page anatomy

Same structure as the Word reference, so a PDF and a Word file of the same content
match: `masthead()` (Main Green band, mono label, Fraunces title, meta line, then the
summary), `key_numbers()` (Cream cards, the first marked by the Mango dot),
`heading()` (numbered mono eyebrow over a Fraunces heading, optional `italic_words`),
`table()`, `bullets()` with optional status tags, `bar_chart()` (Stone bars, the
takeaway in Pine), `callout()` once for the recommendation, and `caption()` for notes
and sources. US Letter, the date and page number in the footer.

Pass plain text; the helpers escape it. Do not hand-write HTML or CSS around them,
and do not add other fonts or colors. A symbol the brand fonts lack (such as ★ or →)
falls back to another font; write the word instead.

## Helpers

```python
from datetime import date
from html import escape
from pathlib import Path
import weasyprint

FONTS = Path(SKILL_DIR) / "assets" / "fonts"  # noqa: F821  (set SKILL_DIR to this skill's directory)
INK, SLATE, STONE, CREAM, WHITE = "#161611", "#6F6D64", "#DAD8CE", "#F7F6F2", "#FFFFFF"
MAIN_GREEN, PINE, MINT, MANGO = "#1A4438", "#25644F", "#D9EAD3", "#FB9608"
ON_DARK, ON_DARK_MUTED, ON_DARK_ACCENT = "#F7F6F2", "#A9B3AE", "#88E9C0"
STATUS = {"good": (MINT, PINE), "watch": (MANGO, INK), "problem": (MANGO, INK)}
TODAY = f"{date.today():%b} {date.today().day}, {date.today():%Y}"  # footers carry the date and number only

CSS = f"""
@font-face {{ font-family: "Fraunces"; font-weight: 300; font-style: normal; src: url("{(FONTS / 'Fraunces72pt-Light.ttf').as_uri()}"); }}
@font-face {{ font-family: "Fraunces"; font-weight: 300; font-style: italic; src: url("{(FONTS / 'Fraunces72pt-LightItalic.ttf').as_uri()}"); }}
@font-face {{ font-family: "Wix Madefor Display"; font-weight: 400; src: url("{(FONTS / 'WixMadeforDisplay-Regular.ttf').as_uri()}"); }}
@font-face {{ font-family: "Wix Madefor Text"; font-weight: 600; src: url("{(FONTS / 'WixMadeforText-SemiBold.ttf').as_uri()}"); }}
@font-face {{ font-family: "IBM Plex Mono"; font-weight: 500; src: url("{(FONTS / 'IBMPlexMono-Medium.ttf').as_uri()}"); }}
@page {{
  size: Letter; margin: 0.9in 1in 0.85in 1in;
  @bottom-left {{ content: "{TODAY}"; font: 8pt "Wix Madefor Display"; color: {SLATE}; }}
  @bottom-right {{ content: counter(page); font: 8pt "Wix Madefor Display"; color: {SLATE}; }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font: 400 10.5pt/1.4 "Wix Madefor Display"; color: {INK}; }}
.mono {{ font-family: "IBM Plex Mono"; font-weight: 500; text-transform: uppercase; letter-spacing: 0.08em; }}
.serif {{ font-family: "Fraunces"; font-weight: 300; }}
em {{ font-family: "Fraunces"; font-weight: 300; font-style: italic; }}
.masthead {{ background: {MAIN_GREEN}; padding: 22pt 22pt 20pt; }}
.masthead .label {{ font-size: 8pt; color: {ON_DARK_ACCENT}; margin-bottom: 8pt; }}
.masthead h1 {{ font: 300 28pt/1.08 "Fraunces"; color: {ON_DARK}; margin: 0 0 8pt; }}
.masthead .meta {{ font-size: 9pt; color: {ON_DARK_MUTED}; }}
.summary {{ font-size: 11.5pt; line-height: 1.45; margin: 16pt 0 14pt; }}
.figures {{ display: flex; gap: 5pt; margin-bottom: 6pt; break-inside: avoid; }}
.figure {{ flex: 1; background: {CREAM}; padding: 10pt 12pt 9pt; }}
.figure .v {{ font: 400 20pt/1.1 "Wix Madefor Display"; }}
.figure .l {{ font-size: 6.5pt; color: {SLATE}; margin-top: 4pt; }}
.figure .dot {{ display: inline-block; width: 5pt; height: 5pt; border-radius: 50%; background: {MANGO}; margin-right: 4pt; vertical-align: 0.5pt; }}
.head {{ break-inside: avoid; break-after: avoid; page-break-after: avoid; margin-top: 20pt; }}
.eyebrow {{ font-size: 8pt; color: {PINE}; margin: 0 0 2pt; }}
h2 {{ font: 300 18pt/1.15 "Fraunces"; margin: 0 0 8pt; break-after: avoid; }}
p {{ margin: 0 0 6pt; }}
ul, ol {{ margin: 0 0 8pt; padding-left: 16pt; }} li {{ margin-bottom: 3pt; }}
.caption {{ font-size: 8.5pt; color: {SLATE}; margin: 4pt 0 10pt; }}
table {{ width: 100%; border-collapse: collapse; margin: 2pt 0 2pt; }}
table.keep {{ break-inside: avoid; }}
th {{ font: 600 8.5pt "Wix Madefor Text"; text-align: left; padding: 4pt 6pt 4pt 0; border-bottom: 1pt solid {INK}; }}
td {{ font-size: 9.5pt; padding: 4pt 6pt 4pt 0; border-bottom: 0.5pt solid {STONE}; }}
th.num, td.num {{ text-align: right; padding-right: 0; padding-left: 6pt; }}
td.hl {{ font-family: "Wix Madefor Text"; font-weight: 600; color: {PINE}; }}
.callout {{ background: {MINT}; padding: 12pt 14pt; margin: 4pt 0 8pt; break-inside: avoid; }}
.callout .label {{ font-size: 7.5pt; color: {PINE}; margin-bottom: 4pt; }}
.callout .text {{ font-size: 11pt; line-height: 1.4; }}
.tag {{ font-size: 6.5pt; padding: 1pt 4pt; margin-left: 4pt; }}
.chart {{ break-inside: avoid; margin: 4pt 0 6pt; }}
"""


class PdfDoc:
    """Build a branded PDF with WeasyPrint. Pass plain text; the helpers escape it."""

    def __init__(self):
        self.parts, self.sections, self.dot_used = [], 0, False

    def masthead(self, label, title, meta, summary):
        self.parts.append(
            f'<div class="masthead"><div class="label mono">{escape(label)}</div>'
            f'<h1>{escape(title)}</h1><div class="meta">{escape(meta)}</div></div>'
            f'<p class="summary">{escape(summary)}</p>')

    def key_numbers(self, items):
        """items: [(value, label), ...], 3 or 4 short values such as "$1.31M". The first figure of the
        first call gets the one Mango dot; a second row of figures gets none."""
        dot, self.dot_used = not self.dot_used, True
        cards = "".join(
            f'<div class="figure"><div class="v">{escape(v)}</div><div class="l mono">'
            f'{"<span class=dot></span>" if i == 0 and dot else ""}{escape(l)}</div></div>'
            for i, (v, l) in enumerate(items))
        self.parts.append(f'<div class="figures">{cards}</div>')

    def heading(self, text, label=None, italic_words=None):
        """Numbered mono eyebrow over a Fraunces heading; italic_words: one to three words to set in italic."""
        self.sections += 1
        html = escape(text)
        if italic_words and italic_words in text:
            html = html.replace(escape(italic_words), f"<em>{escape(italic_words)}</em>", 1)
        self.parts.append(f'<div class="head"><div class="eyebrow mono">{self.sections:02d} · {escape(label or text)}</div><h2>{html}</h2></div>')

    def paragraph(self, text):
        self.parts.append(f"<p>{escape(text)}</p>")

    def bullets(self, items, numbered=False):
        """items: strings, or (text, (word, kind)) to add a status tag."""
        lis = ""
        for item in items:
            text, state = (item, None) if isinstance(item, str) else item
            lis += f"<li>{escape(text)}{self.status(*state) if state else ''}</li>"
        tag = "ol" if numbered else "ul"
        self.parts.append(f"<{tag}>{lis}</{tag}>")

    def caption(self, text):
        self.parts.append(f'<div class="caption">{escape(text)}</div>')

    def table(self, header, rows, numeric_cols=(), highlight=None):
        """highlight: (row_index, col_index) of the one takeaway cell. Tables of 12 rows or fewer stay on one page."""
        head = "".join(f'<th class="{"num" if c in numeric_cols else ""}">{escape(str(h))}</th>' for c, h in enumerate(header))
        body = ""
        for r, row in enumerate(rows):
            cells = ""
            for c, v in enumerate(row):
                cls = " ".join(x for x in ("num" if c in numeric_cols else "", "hl" if highlight == (r, c) else "") if x)
                cells += f'<td class="{cls}">{escape(str(v))}</td>'
            body += f"<tr>{cells}</tr>"
        keep = ' class="keep"' if len(rows) <= 12 else ""
        self.parts.append(f"<table{keep}><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")

    def callout(self, label, text):
        """One Mint panel per document for the recommendation or decision."""
        self.parts.append(f'<div class="callout"><div class="label mono">{escape(label)}</div><div class="text">{escape(text)}</div></div>')

    @staticmethod
    def status(word, kind):
        surface, color = STATUS[kind]
        return f'<span class="tag mono" style="background:{surface};color:{color}">{escape(word)}</span>'

    def bar_chart(self, categories, values, highlight, labels=None, height=170):
        """Simple vertical bar chart as inline SVG: Stone bars, the takeaway bar in Pine.
        labels: the text above each bar (defaults to the values)."""
        labels = labels or [str(v) for v in values]
        width, top, bottom = 468, 18, 22
        peak = max(values) or 1
        slot = width / len(values)
        bar = slot * 0.5
        out = [f'<svg class="chart" width="{width}pt" height="{height}pt" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">']
        for i, (cat, val, lab) in enumerate(zip(categories, values, labels)):
            h = (height - top - bottom) * val / peak
            x = i * slot + (slot - bar) / 2
            y = height - bottom - h
            fill = PINE if i == highlight else STONE
            out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar:.1f}" height="{h:.1f}" fill="{fill}"/>')
            out.append(f'<text x="{x + bar / 2:.1f}" y="{y - 5:.1f}" text-anchor="middle" font-family="Wix Madefor Display" font-size="8.5" fill="{INK}">{escape(lab)}</text>')
            out.append(f'<text x="{x + bar / 2:.1f}" y="{height - 7}" text-anchor="middle" font-family="Wix Madefor Display" font-size="8" fill="{SLATE}">{escape(str(cat))}</text>')
        out.append(f'<line x1="0" y1="{height - bottom}" x2="{width}" y2="{height - bottom}" stroke="{STONE}" stroke-width="0.75"/></svg>')
        self.parts.append("".join(out))

    def save(self, out_path):
        html = f'<html><head><meta charset="utf-8"><style>{CSS}</style></head><body>{"".join(self.parts)}</body></html>'
        weasyprint.HTML(string=html).write_pdf(out_path)
```

Example:

```python
doc = PdfDoc()
doc.masthead("Claims review · Q3 2026", "Fewer claims, but each one costs more", "For claims leadership · Workers' compensation · Q3 2026",
             "New claims fell to 412 in Q3, the second straight quarterly decline, but the average incurred per claim rose to $18,900 and litigation reached 14% of claims. Three actions target litigation and return to work.")
doc.key_numbers([("412", "New claims in Q3"), ("$18,900", "Average incurred"), ("14%", "Litigation rate"), ("58%", "Return to work, 30 days")])
doc.heading("New claims fell for the second straight quarter", label="Claim volume")
doc.bar_chart(["Q1 2026", "Q2 2026", "Q3 2026"], [471, 455, 412], highlight=2)
doc.caption("New claims by quarter.")
doc.heading("Strains and falls drive 60% of claims", label="Causes", italic_words="60%")
doc.table(["Cause", "Share of Q3 claims"], [["Strains", "38%"], ["Falls", "22%"], ["Struck-by", "14%"], ["Vehicle", "9%"], ["Other", "17%"]], numeric_cols=(1,), highlight=(0, 1))
doc.heading("Litigation and severity")
doc.bullets([("Litigation rate rose to 14% from 11% in Q2", ("Watch", "watch")), ("Litigated claims average $61,000, about 3.2 times the all-claims average", ("Problem", "problem")), ("Return to work within 30 days is 58%, against a 65% target", ("Watch", "watch"))])
doc.heading("Actions")
doc.callout("Recommended", "Nurse triage on all lost-time claims within 24 hours, an attorney-representation review at day 30, and a refreshed fall-prevention program with the three largest employers.")
doc.save(out_path)
```

## Check before delivering

```python
import subprocess

fonts = subprocess.run(["pdffonts", out_path], capture_output=True, text=True).stdout.splitlines()[2:]
names = {line.split()[0].split("+")[-1] for line in fonts if line.strip()}
allowed = {"Fraunces-Light", "Fraunces-Light-Italic", "Wix-Madefor-Display", "Wix-Madefor-Text-Semi-Bold", "IBM-Plex-Mono-Medium"}
text = subprocess.run(["pdftotext", out_path, "-"], capture_output=True, text=True).stdout
problems = [f"unexpected font {n}: a character the brand fonts lack, or a font outside the helpers" for n in names - allowed]
if "furtherai" in text.lower():
    problems.append("remove the FurtherAI name")
if "\u2014" in text or "\u2013" in text:
    problems.append("em/en dash in the text")
print("\n".join(problems) or "OK")
```

Then render every page and look at each image:

```bash
mkdir -p render && pdftoppm -png -r 60 output.pdf render/page && ls render
```

Look for a heading alone at the bottom of a page, a table split awkwardly, text
crowding the footer, and chart labels colliding. Fix and render again.
