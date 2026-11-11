# PowerPoint (.pptx)

Build with `pptx` (python-pptx API) on the **Blank** layout using the helpers below.
Set `SKILL_DIR` to this skill's directory first. Do not use the stock title and
content placeholders.

## Canvas and grid

16:9, 13.333 × 7.5 in, margins 0.6 in left and right.

| Zone | Top | Height |
| --- | --- | --- |
| Eyebrow (`02 · LABEL`, mono, Pine) | 0.5 in | 0.3 in |
| Title (Fraunces 72pt Light 28 pt) | 0.82 in | 0.8 in |
| Content (White cards on Cream) | 1.95 in | 4.7 in |
| Footer (date left, slide number right) | 6.95 in | 0.3 in |

Titles fit on one line: at most about 60 characters. Shorten the wording rather than
wrapping, and put the supporting fact in the body.

| Text | Font | Size | Color |
| --- | --- | --- | --- |
| Cover title | Fraunces 72pt Light (+ one accent line in `88E9C0`) | 44 pt | Cream on the cover image |
| Slide title | Fraunces 72pt Light, optional `italic_words` (its italic) | 28 pt | Ink |
| Card title | Fraunces 72pt Light | 18 pt | Ink |
| Large figures | Wix Madefor Display | 26 to 32 pt | Ink |
| Eyebrow, status tags | IBM Plex Mono Medium caps | 12 / 10 pt | Pine / per status |
| Body | Wix Madefor Display | 14 pt | Ink |
| Captions, chart text, footer | Wix Madefor Display | 12 / 10 pt | Slate |
| Table header | Wix Madefor Text SemiBold | 12 pt | Ink |

## Deck shape

Light by default. Open on `cover()` and close on `decision()` (both dark, using the
brand background images); everything between is a Cream `light_slide()`. Vary the
frames and never use the same one twice in a row:

1. **Numbers**: `numbers()`, one key figure marked by a Mango dot plus 2 or 3 more, each on a White card.
2. **Chart + takeaways**: `bar_chart()` (Stone bars, the takeaway in Pine) with
   `takeaways()` beside it. Use `horizontal=True` for categories with long names.
3. **List**: `two_column_list()` for exposure and control, measure and detail, with
   `status_tag()` where it helps.
4. **Cards**: `card()` for two or three parallel ideas.
5. **Table**: `table()`, at most 7 rows.

One message per slide, at most 5 short lines of body text. A Mango dot marks one number
per slide; the italic or accent phrase marks one idea; never both on the same line.

## Helpers

```python
from datetime import date
from pathlib import Path
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

BRAND = Path(SKILL_DIR) / "assets" / "brand"  # noqa: F821  (set SKILL_DIR to this skill's directory)
SERIF = "Fraunces 72pt Light"  # its italic style is embedded too
SANS, UI, MONO = "Wix Madefor Display", "Wix Madefor Text SemiBold", "IBM Plex Mono Medium"
INK, SLATE, STONE, CREAM, WHITE = "161611", "6F6D64", "DAD8CE", "F7F6F2", "FFFFFF"
MAIN_GREEN, PINE, MINT, MANGO = "1A4438", "25644F", "D9EAD3", "FB9608"
ON_DARK, ON_DARK_MUTED, ON_DARK_ACCENT = "F7F6F2", "A9B3AE", "88E9C0"
STATUS = {"good": (MINT, PINE), "watch": (MANGO, INK), "problem": (MANGO, INK)}
LEFT, WIDTH = Inches(0.6), Inches(12.133)
TODAY = f"{date.today():%b} {date.today().day}, {date.today():%Y}"  # footers carry the date and number only, no company name or logo


def deck():
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    prs._section_count = 0
    return prs


def text(slide, x, y, w, h, lines, size=14, color=INK, font=SANS, bold=False,
         align=PP_ALIGN.LEFT, caps=False, tracking=None, line=1.15, italic_words=None):
    """lines: a string or list of strings. italic_words: one phrase (1 to 3 words) to set in italic."""
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap, tf.vertical_anchor = True, MSO_ANCHOR.TOP
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    for i, value in enumerate([lines] if isinstance(lines, str) else lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment, p.space_after, p.line_spacing = align, Pt(size * 0.55), line
        value = value.upper() if caps else value
        parts = [(value, False)]
        if italic_words and italic_words in value:
            a, b = value.split(italic_words, 1)
            parts = [(a, False), (italic_words, True), (b, False)]
        for chunk, italic in parts:
            if not chunk:
                continue
            run = p.add_run()
            run.text = chunk
            run.font.name = font
            run.font.size, run.font.bold, run.font.italic = Pt(size), bold, italic
            run.font.color.rgb = RGBColor.from_string(color)
            if tracking:
                run.font._element.set("spc", str(tracking))
    return box


def rect(slide, x, y, w, h, fill):
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor.from_string(fill)
    shape.line.fill.background()
    shape._element.remove(shape._element.find(qn("p:style")))
    return shape


def rule(slide, x1, y1, x2, y2, color=STONE):
    line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, x1, y1, x2, y2)
    line.line.color.rgb, line.line.width = RGBColor.from_string(color), Pt(0.75)
    line._element.remove(line._element.find(qn("p:style")))


def eyebrow(slide, x, y, value, color=PINE, w=WIDTH):
    text(slide, x, y, w, Inches(0.3), value, 12, color, MONO, caps=True, tracking=100)


def _background(slide, fill=None, image=None, prs=None):
    if image:
        pic = slide.shapes.add_picture(str(image), 0, 0, prs.slide_width, prs.slide_height)
        slide.shapes._spTree.remove(pic._element)
        slide.shapes._spTree.insert(2, pic._element)
    else:
        bg = slide.background.fill
        bg.solid()
        bg.fore_color.rgb = RGBColor.from_string(fill)


def light_slide(prs, title, label, italic_words=None):
    """Cream slide: numbered mono eyebrow, Fraunces title, footer with the date and slide number."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _background(slide, CREAM)
    prs._section_count += 1
    eyebrow(slide, LEFT, Inches(0.5), f"{prs._section_count:02d} · {label}")
    text(slide, LEFT, Inches(0.82), WIDTH, Inches(0.8), title, 28, INK, SERIF, italic_words=italic_words)
    text(slide, LEFT, Inches(6.98), Inches(8), Inches(0.3), TODAY, 10, SLATE)
    text(slide, Inches(10.733), Inches(6.98), Inches(2), Inches(0.3), str(len(prs.slides)), 10, SLATE, align=PP_ALIGN.RIGHT)
    return slide


def dark_slide(prs, image="bg-dark.jpg"):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _background(slide, image=BRAND / image, prs=prs)
    return slide


def cover(prs, label, title, accent_words, subtitle):
    """Dark cover: mono pill eyebrow, Fraunces title with an accent line, subtitle, date."""
    slide = dark_slide(prs, "bg-cover.jpg")
    pill = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, LEFT, Inches(2.1), Inches(0.3 + 0.118 * len(label)), Inches(0.36))
    pill.fill.background()
    pill.line.color.rgb, pill.line.width = RGBColor.from_string(ON_DARK_MUTED), Pt(0.75)
    pill._element.remove(pill._element.find(qn("p:style")))
    pill.adjustments[0] = 0.5
    tf = pill.text_frame
    tf.word_wrap = False
    tf.margin_left = tf.margin_right = Inches(0.12)
    tf.margin_top = tf.margin_bottom = 0
    run = tf.paragraphs[0].add_run()
    run.text = label.upper()
    run.font.name, run.font.size, run.font.color.rgb = MONO, Pt(11), RGBColor.from_string(ON_DARK)
    run.font._element.set("spc", "100")
    box = text(slide, LEFT, Inches(2.75), Inches(10.5), Inches(1.6), title, 44, ON_DARK, SERIF, line=1.0)
    box.text_frame.paragraphs[0].space_after = Pt(0)
    if accent_words:
        p = box.text_frame.add_paragraph()
        p.line_spacing, p.space_after = 1.0, Pt(0)
        r = p.add_run()
        r.text = accent_words
        r.font.name, r.font.size, r.font.color.rgb = SERIF, Pt(44), RGBColor.from_string(ON_DARK_ACCENT)
    text(slide, LEFT, Inches(4.75), Inches(9), Inches(0.8), subtitle, 16, ON_DARK)
    text(slide, LEFT, Inches(6.5), Inches(6), Inches(0.3), TODAY, 11, ON_DARK_MUTED)
    return slide


def decision(prs, statement, steps, accent_words=None):
    """Dark closer: the ask in Georgia with an optional accent phrase, then numbered steps."""
    slide = dark_slide(prs)
    eyebrow(slide, LEFT, Inches(0.9), "Decision", ON_DARK_ACCENT)
    box = text(slide, LEFT, Inches(1.3), Inches(11.6), Inches(1.6), statement, 30, ON_DARK, SERIF)
    if accent_words:
        for run in list(box.text_frame.paragraphs[0].runs):
            if accent_words in run.text:
                before, after = run.text.split(accent_words, 1)
                run.text = before
                for chunk, color in ((accent_words, ON_DARK_ACCENT), (after, ON_DARK)):
                    r = box.text_frame.paragraphs[0].add_run()
                    r.text = chunk
                    r.font.name, r.font.size, r.font.color.rgb = SERIF, Pt(30), RGBColor.from_string(color)
    for i, step in enumerate(steps):
        y = Inches(3.55) + i * Inches(0.62)
        text(slide, LEFT, y + Inches(0.04), Inches(0.6), Inches(0.4), f"{i + 1:02d}", 12, ON_DARK_ACCENT, MONO)
        text(slide, Inches(1.25), y, Inches(10.5), Inches(0.5), step, 16, ON_DARK)
    text(slide, LEFT, Inches(6.6), Inches(6), Inches(0.3), TODAY, 10, ON_DARK_MUTED)
    return slide


def numbers(slide, hero, others, y=Inches(2.0)):
    """White cards on Cream. hero: (value, caption), marked by the one Mango dot; others: 2 or 3 more."""
    count = 1 + len(others)
    gap = Inches(0.25)
    card_w = int((WIDTH - gap * (count - 1)) / count)
    for i, (value, caption) in enumerate([hero] + list(others)):
        x = LEFT + i * (card_w + gap)
        rect(slide, x, y, card_w, Inches(1.85), WHITE)
        text(slide, x + Inches(0.3), y + Inches(0.38), card_w - Inches(0.5), Inches(0.75), value, 32, INK, SANS, line=1.0)
        indent = Inches(0.22) if i == 0 else 0
        if i == 0:
            dot = slide.shapes.add_shape(MSO_SHAPE.OVAL, x + Inches(0.3), y + Inches(1.33), Inches(0.12), Inches(0.12))
            dot.fill.solid()
            dot.fill.fore_color.rgb = RGBColor.from_string(MANGO)
            dot.line.fill.background()
            dot._element.remove(dot._element.find(qn("p:style")))
        text(slide, x + Inches(0.3) + indent, y + Inches(1.25), card_w - Inches(0.5) - indent, Inches(0.5), caption, 12, SLATE)


def card(slide, x, y, w, h, title, body, label=None):
    """A White card on Cream with an optional mono label, a Georgia title and Arial body text."""
    rect(slide, x, y, w, h, WHITE)
    top = y + Inches(0.3)
    if label:
        eyebrow(slide, x + Inches(0.3), top, label, PINE, w - Inches(0.6))
        top += Inches(0.4)
    text(slide, x + Inches(0.3), top, w - Inches(0.6), Inches(0.6), title, 18, INK, SERIF)
    text(slide, x + Inches(0.3), top + Inches(0.55), w - Inches(0.6), h - (top - y) - Inches(0.7), body, 13, SLATE)


def bar_chart(slide, categories, values, highlight, number_format="0%",
              x=LEFT, y=Inches(1.95), w=Inches(7.4), h=Inches(4.7), horizontal=False):
    """Inside a White card: context bars in Stone, the takeaway bar in Pine."""
    rect(slide, x, y, w, h, WHITE)
    data = CategoryChartData()
    data.categories = categories
    data.add_series("Series 1", values)
    kind = XL_CHART_TYPE.BAR_CLUSTERED if horizontal else XL_CHART_TYPE.COLUMN_CLUSTERED
    chart = slide.shapes.add_chart(kind, x + Inches(0.3), y + Inches(0.3), w - Inches(0.6), h - Inches(0.5), data).chart
    chart.has_title = chart.has_legend = False
    chart.font.name, chart.font.size = SANS, Pt(12)
    chart.font.color.rgb = RGBColor.from_string(SLATE)
    plot = chart.plots[0]
    plot.gap_width, plot.has_data_labels = 55, True
    labels = plot.data_labels
    labels.number_format, labels.number_format_is_linked = number_format, False
    labels.position = XL_LABEL_POSITION.OUTSIDE_END
    labels.font.size, labels.font.color.rgb = Pt(13), RGBColor.from_string(INK)
    series = plot.series[0]
    series.format.fill.solid()
    series.format.fill.fore_color.rgb = RGBColor.from_string(STONE)
    point = series.points[highlight]
    point.format.fill.solid()
    point.format.fill.fore_color.rgb = RGBColor.from_string(PINE)
    chart.value_axis.visible = chart.value_axis.has_major_gridlines = False
    chart.category_axis.format.line.color.rgb = RGBColor.from_string(STONE)
    if horizontal:
        chart.category_axis.reverse_order = True
    return chart


def takeaways(slide, items, x=Inches(8.3), y=Inches(1.95), w=Inches(4.43), h=Inches(4.7)):
    """A White card beside a chart with 2 or 3 (figure, caption) pairs."""
    rect(slide, x, y, w, h, WHITE)
    for i, (figure, caption) in enumerate(items):
        top = y + Inches(0.35) + i * Inches(1.45)
        text(slide, x + Inches(0.35), top, w - Inches(0.7), Inches(0.6), figure, 26, INK, SANS, line=1.0)
        text(slide, x + Inches(0.35), top + Inches(0.6), w - Inches(0.7), Inches(0.6), caption, 12, SLATE)


def status_tag(slide, x, y, word, kind):
    surface, color = STATUS[kind]
    rect(slide, x, y, Inches(0.11 * len(word) + 0.3), Inches(0.3), surface)
    text(slide, x, y + Inches(0.05), Inches(0.11 * len(word) + 0.3), Inches(0.25), word.upper(), 10, color, MONO,
         align=PP_ALIGN.CENTER, tracking=80)


def two_column_list(slide, left_label, right_label, rows):
    """rows: (item, detail, status) with status None or (word, kind). Sits on a White card."""
    rect(slide, LEFT, Inches(1.95), WIDTH, Inches(4.7), WHITE)
    eyebrow(slide, LEFT + Inches(0.35), Inches(2.2), left_label, SLATE, Inches(5))
    eyebrow(slide, Inches(7.0), Inches(2.2), right_label, SLATE, Inches(5))
    row_h = min(Inches(1.3), Inches(3.9 / max(len(rows), 1)))
    for i, (item, detail, state) in enumerate(rows):
        y = Inches(2.65) + i * row_h
        rule(slide, LEFT + Inches(0.35), y, LEFT + WIDTH - Inches(0.35), y)
        text(slide, LEFT + Inches(0.35), y + Inches(0.2), Inches(5.6), Inches(0.6), item, 18, INK, SERIF)
        if state:
            status_tag(slide, LEFT + Inches(0.35), y + Inches(0.68), *state)
        text(slide, Inches(7.0), y + Inches(0.22), Inches(5.4), row_h - Inches(0.3), detail, 14, INK)


def table(slide, header, rows, col_widths_in, numeric_cols=(), highlight=None, x=LEFT, y=Inches(1.95)):
    """A hairline table on a White card. highlight: (row_index, col_index) of the takeaway cell."""
    width = Inches(sum(col_widths_in))
    rect(slide, x, y, width + Inches(0.6), Inches(0.45 * (len(rows) + 1) + 0.6), WHITE)
    shape = slide.shapes.add_table(len(rows) + 1, len(header), x + Inches(0.3), y + Inches(0.3), width, Inches(0.45 * (len(rows) + 1)))
    tbl = shape._element.graphic.graphicData.tbl
    tbl.tblPr.set("bandRow", "0")
    tbl.tblPr.find(qn("a:tableStyleId")).text = "{2D5ABB26-0587-4C30-8999-92F81FD0307C}"
    t = shape.table
    for c, cw in enumerate(col_widths_in):
        t.columns[c].width = Inches(cw)
    for r, values in enumerate([header] + list(rows)):
        t.rows[r].height = Inches(0.45)
        for c, value in enumerate(values):
            cell = t.cell(r, c)
            cell.margin_left = cell.margin_right = Inches(0.08)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            tc_pr = cell._tc.get_or_add_tcPr()
            line = tc_pr.makeelement(qn("a:lnB"), {"w": "9525"})
            fill = line.makeelement(qn("a:solidFill"), {})
            fill.append(fill.makeelement(qn("a:srgbClr"), {"val": INK if r == 0 else STONE}))
            line.append(fill)
            tc_pr.insert(0, line)
            cell.fill.background()
            p = cell.text_frame.paragraphs[0]
            p.alignment = PP_ALIGN.RIGHT if c in numeric_cols else PP_ALIGN.LEFT
            run = p.add_run()
            run.text = str(value)
            if r == 0:
                run.font.name, run.font.size, color = UI, Pt(12), INK
            elif highlight == (r - 1, c):
                run.font.name, run.font.size, color = UI, Pt(14), PINE
            else:
                run.font.name, run.font.size, color = SANS, Pt(14), INK
            run.font.color.rgb = RGBColor.from_string(color)
    return t
```

Example:

```python
prs = deck()
cover(prs, "Claims leadership · Q3 2026", "Workers' compensation", "claims review", "Volume is down, but litigation and severity are rising.")
s = light_slide(prs, "Fewer claims, but each one costs more", "Quarter at a glance", italic_words="costs more")
numbers(s, ("412", "New claims in Q3, down from 455 in Q2"), [("$18,900", "Average incurred per claim, up from $17,400"), ("1,284", "Open claims at Sep 30"), ("$48.6M", "Open reserves")])
text(s, LEFT, Inches(4.25), Inches(11.5), Inches(1.5), ["Return to work within 30 days is 58%, against a 65% target.", "Litigation rose to 14% of claims, from 11% in Q2."], 14, INK)
s = light_slide(prs, "New claims fell for the second straight quarter", "Claim volume")
bar_chart(s, ["Q1 2026", "Q2 2026", "Q3 2026"], [471, 455, 412], highlight=2, number_format="#,##0")
takeaways(s, [("Down 12.5%", "from 471 claims in Q1"), ("$18,900", "average incurred, up from $17,400"), ("58%", "return to work within 30 days")])
s = light_slide(prs, "Strains and falls drive 60% of new claims", "Causes of injury", italic_words="60%")
bar_chart(s, ["Strains", "Falls", "Struck-by", "Vehicle", "Other"], [0.38, 0.22, 0.14, 0.09, 0.17], highlight=0, horizontal=True)
takeaways(s, [("38%", "strains, the largest single cause"), ("22%", "falls, the target of the refreshed program")])
s = light_slide(prs, "Litigation is up, and litigated claims cost more", "Litigation and severity")
two_column_list(s, "Measure", "Detail", [("Litigation rate 14%", "Up from 11% in Q2.", ("Watch", "watch")), ("Litigated claims $61,000", "About 3.2 times the $18,900 all-claims average.", ("Problem", "problem")), ("Return to work 58%", "Within 30 days, against a 65% target.", ("Watch", "watch"))])
decision(prs, "Approve three actions to curb litigation and lift return to work", ["Nurse triage on all lost-time claims within 24 hours", "Attorney-representation review at day 30", "Refresh fall-prevention with the three largest employers"], accent_words="curb litigation")
prs.save(out_path)
```

## Embed the fonts, then check

After `prs.save(...)`, always run:

```bash
python "$SKILL_DIR/scripts/embed_fonts.py" deck.pptx
```

Render every slide and look at each image. Install the brand fonts first so the
render matches what readers see:

```bash
mkdir -p ~/.fonts && cp "$SKILL_DIR"/assets/fonts/*.ttf ~/.fonts/ && fc-cache -f >/dev/null
mkdir -p render && timeout 180 soffice -env:UserInstallation=file:///tmp/lo_profile \
  --headless --convert-to pdf --outdir render deck.pptx >/dev/null 2>&1
pdftoppm -png -r 60 render/deck.pdf render/slide && ls render
```

Then run this structural check:

```python
import zipfile
from datetime import date
from pptx import Presentation

TODAY = f"{date.today():%b} {date.today().day}, {date.today():%Y}"
BRAND_FONTS = {"Fraunces 72pt Light", "Wix Madefor Display", "Wix Madefor Text SemiBold", "IBM Plex Mono Medium"}
prs = Presentation(out_path)
W, H = prs.slide_width, prs.slide_height
problems = []
for n, slide in enumerate(prs.slides, start=1):
    texts = []
    for shape in slide.shapes:
        if shape.left < 0 or shape.top < 0 or shape.left + shape.width > W or shape.top + shape.height > H:
            problems.append(f"slide {n}: {shape.name} runs off the slide")
        frames = [shape.text_frame] if shape.has_text_frame else []
        if shape.has_table:
            frames += [c.text_frame for row in shape.table.rows for c in row.cells]
        for tf in frames:
            for p in tf.paragraphs:
                texts.append(p.text)
                for run in p.runs:
                    if run.font.name not in BRAND_FONTS:
                        problems.append(f"slide {n}: font {run.font.name} in '{run.text[:30]}'")
                    if run.font.bold or (run.font.italic and run.font.name != "Fraunces 72pt Light"):
                        problems.append(f"slide {n}: bold, or italic outside Fraunces, in '{run.text[:30]}'")
                    if run.font.size is not None and run.font.size.pt < 10:
                        problems.append(f"slide {n}: text below 10 pt")
                    if run.font.size is not None and run.font.size.pt == 28 and len(p.text) > 60:
                        problems.append(f"slide {n}: title over 60 characters, it will wrap")
    joined = " ".join(texts)
    if "\u2014" in joined or "\u2013" in joined:
        problems.append(f"slide {n}: em/en dash")
    if TODAY not in joined:
        problems.append(f"slide {n}: date footer missing")
    if "furtherai" in joined.lower():
        problems.append(f"slide {n}: remove the FurtherAI name; documents belong to the user's organization")
with zipfile.ZipFile(out_path) as z:
    if b"embeddedFontLst" not in z.read("ppt/presentation.xml"):
        problems.append("brand fonts not embedded: run scripts/embed_fonts.py")
print("\n".join(problems) or "OK")
```

In the images, look for text cut off or overflowing its box, overlaps, titles at
different heights, chart labels colliding, and uneven gaps. Fix the code, rebuild,
embed again and render again until clean.
