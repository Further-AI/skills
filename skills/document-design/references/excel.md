# Excel (.xlsx)

Build with `openpyxl` (the installed paper-xlsx). Paste the helpers below and build
each sheet with `sheet()`, then `key_figures()` (summary sheets only), `table()` and
`bar_chart()`. Do not style cells by hand.

A workbook cannot carry fonts, so the brand fonts would turn into Calibri for most
readers. Workbooks use **Georgia** for sheet titles and **Arial** for every other
cell, key figures included. Never put Fraunces, Wix Madefor or IBM Plex Mono in a workbook.

## Sheet anatomy

1. Row 1: a small Pine label in caps (`LOSS RUN · ACCOUNT NAME`).
2. Row 2: the sheet title in Georgia 18 pt; row 3: a meta line (defaults to today's date).
3. Row 5 onward: `key_figures()` on a summary sheet (up to four figures on a Cream band,
   compact formats such as `money_m`/`money_m2`, the first marked by the Mango dot),
   then tables, then charts.
4. One sheet per topic (Summary, Locations, Losses); the first sheet is the summary.

Tables: a Cream header row with an Ink rule, Stone hairlines between rows, no
gridlines, numbers right-aligned in one format per column, units in the header
(`TIV ($)`), years as `year` (never `int`, which prints 2,008), a bold total row over
an Ink rule, and the header frozen on long tables. Mark
the one takeaway value with `highlight` (Pine, bold) and statuses with `status()`
(a word on Mint or Mango, never color alone). Prefer formulas for totals
(`"=SUM(D9:D11)"`) so the workbook stays live. Sheets print landscape, fit to width,
with the date and page number in the footer.

## Helpers

```python
from datetime import date
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.series import DataPoint
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

TITLE_FONT, CELL_FONT = "Georgia", "Arial"  # workbooks cannot carry fonts, so use faces every reader has
INK, SLATE, STONE, CREAM, WHITE = "161611", "6F6D64", "DAD8CE", "F7F6F2", "FFFFFF"
PINE, MINT, MANGO = "25644F", "D9EAD3", "FB9608"
STATUS = {"good": (MINT, PINE), "watch": (MANGO, INK), "problem": (MANGO, INK)}
TODAY = f"{date.today():%b} {date.today().day}, {date.today():%Y}"
FORMATS = {"money": '"$"#,##0', "money2": '"$"#,##0.00', "money_m": '"$"0.0,,"M"', "money_m2": '"$"0.00,,"M"', "money_k": '"$"0,"K"',
           "int": "#,##0", "year": "0", "dec1": "0.0", "pct": "0.0%", "date": "mmm d, yyyy", "text": "@"}
HAIR = Side(style="thin", color=STONE)
RULE = Side(style="medium", color=INK)


def workbook():
    wb = Workbook()
    wb.remove(wb.active)
    return wb


def sheet(wb, name, label, title, meta=None):
    """A sheet with a small label, a Georgia title and a meta line in rows 1 to 3; tables start at row 5."""
    ws = wb.create_sheet(name[:31])
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = PINE if len(wb.sheetnames) == 1 else STONE
    ws["A1"] = label.upper()
    ws["A1"].font = Font(name=CELL_FONT, size=8, bold=True, color=PINE)
    ws["A2"] = title
    ws["A2"].font = Font(name=TITLE_FONT, size=18, color=INK)
    ws["A3"] = meta or TODAY
    ws["A3"].font = Font(name=CELL_FONT, size=9, color=SLATE)
    ws.row_dimensions[2].height = 28
    ws.oddFooter.left.text, ws.oddFooter.left.size, ws.oddFooter.left.font = TODAY, 8, "Arial"
    ws.oddFooter.right.text, ws.oddFooter.right.size, ws.oddFooter.right.font = "&P of &N", 8, "Arial"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_options.horizontalCentered = False
    ws._next_row = 5
    return ws


def table(ws, header, rows, formats, total=None, highlight=None, widths=None, freeze=None, filters=False):
    """header: column names. formats: one FORMATS key per column ("text", "money", "year", "pct", ...).
    total: a row of values (or None) appended below a rule. highlight: (row_index, col_index) of the one
    takeaway cell. The header freezes when there are more than 8 rows; filters=True adds filter buttons
    for a long table the reader will sort. Returns (first_row, last_row) of the data for charts and formulas."""
    r0 = ws._next_row
    freeze = len(rows) > 8 if freeze is None else freeze

    def align(c, header_row=False):
        left = formats[c - 1] == "text"
        indent = 1 if left and c > 1 else (2 if header_row and filters and not left else 0)
        return Alignment(horizontal="left" if left else "right", vertical="center", indent=indent)

    for c, name in enumerate(header, start=1):
        cell = ws.cell(row=r0, column=c, value=name)
        cell.font = Font(name=CELL_FONT, size=9, bold=True, color=INK)
        cell.fill = PatternFill("solid", fgColor=CREAM)
        cell.border = Border(bottom=RULE)
        cell.alignment = align(c, header_row=True)
    ws.row_dimensions[r0].height = 22
    for i, values in enumerate(rows):
        r = r0 + 1 + i
        for c, value in enumerate(values, start=1):
            cell = ws.cell(row=r, column=c, value=value)
            cell.number_format = FORMATS[formats[c - 1]]
            bold = highlight == (i, c - 1)
            cell.font = Font(name=CELL_FONT, size=10, bold=bold, color=PINE if bold else INK)
            cell.border = Border(bottom=HAIR)
            cell.alignment = align(c)
        ws.row_dimensions[r].height = 18
    last = r0 + len(rows)
    if total:
        r = last + 1
        for c, value in enumerate(total, start=1):
            cell = ws.cell(row=r, column=c, value=value)
            cell.number_format = FORMATS[formats[c - 1]]
            cell.font = Font(name=CELL_FONT, size=10, bold=True, color=INK)
            cell.border = Border(top=RULE)
            label = isinstance(value, str) and not value.startswith("=")
            cell.alignment = Alignment(horizontal="left", vertical="center") if label else align(c)
        ws.row_dimensions[r].height = 20
    if freeze:
        ws.freeze_panes = ws.cell(row=r0 + 1, column=1)
    if filters:
        ws.auto_filter.ref = f"A{r0}:{get_column_letter(len(header))}{last}"
    for c in range(1, len(header) + 1):
        letter = get_column_letter(c)
        if widths:
            ws.column_dimensions[letter].width = widths[c - 1]
        else:
            longest = max(len(str(ws.cell(row=r, column=c).value or "")) for r in range(r0, last + 2))
            ws.column_dimensions[letter].width = min(max(12, longest + (6 if filters else 4)), 48)
    ws._next_row = last + (3 if total else 2)
    return r0 + 1, last


def status(cell, word, kind):
    """Status as a word plus its color, never color alone: kind is good, watch or problem."""
    surface, color = STATUS[kind]
    cell.value = word
    cell.fill = PatternFill("solid", fgColor=surface)
    cell.font = Font(name=CELL_FONT, size=9, bold=True, color=color)
    cell.alignment = Alignment(horizontal="center", vertical="center")


def key_figures(ws, items):
    """A row of up to four key figures under the title, each on two merged columns.
    items: [(value, label, number_format), ...]; use compact formats such as '"$"0.0,,"M"' (millions)
    or "pct". The first figure gets the one Mango marker. Call before the first table."""
    from openpyxl.cell.rich_text import CellRichText, TextBlock
    from openpyxl.cell.text import InlineFont
    r = ws._next_row
    fill = PatternFill("solid", fgColor=CREAM)
    for i, (value, label, fmt) in enumerate(items):
        c1, c2 = 1 + i * 2, 2 + i * 2
        for row in (r, r + 1):
            for col in (c1, c2):
                ws.cell(row=row, column=col).fill = fill
            ws.merge_cells(start_row=row, start_column=c1, end_row=row, end_column=c2)
        v = ws.cell(row=r, column=c1, value=value)
        v.number_format = FORMATS.get(fmt, fmt)
        v.font = Font(name=CELL_FONT, size=20, color=INK)
        v.alignment = Alignment(horizontal="left", vertical="bottom", indent=1)
        parts = [TextBlock(InlineFont(rFont=CELL_FONT, sz=8, color=SLATE), label.upper())]
        if i == 0:
            parts.insert(0, TextBlock(InlineFont(rFont=CELL_FONT, sz=8, color=MANGO), "\u25cf "))
        lab = ws.cell(row=r + 1, column=c1, value=CellRichText(*parts))
        lab.font = Font(name=CELL_FONT, size=8, color=SLATE)
        lab.alignment = Alignment(horizontal="left", vertical="top", indent=1)
    ws.row_dimensions[r].height = 34
    ws.row_dimensions[r + 1].height = 20
    ws._next_row = r + 3


def bar_chart(ws, title, cats_ref, vals_ref, anchor_row, highlight=None, number_format="#,##0"):
    """A native bar chart under a small label: Stone bars, the takeaway bar (index `highlight`) in Pine,
    value labels only, Arial text. anchor_row: the sheet row for the label; the chart sits below it."""
    from openpyxl.chart.label import DataLabelList
    from openpyxl.chart.shapes import GraphicalProperties
    from openpyxl.chart.text import RichText
    from openpyxl.drawing.text import CharacterProperties, Font as DrawingFont, Paragraph, ParagraphProperties

    def arial(size, color):
        props = CharacterProperties(latin=DrawingFont(typeface=CELL_FONT), sz=size * 100, solidFill=color)
        return RichText(p=[Paragraph(pPr=ParagraphProperties(defRPr=props), endParaRPr=props)])

    label = ws.cell(row=anchor_row, column=1, value=title.upper())
    label.font = Font(name=CELL_FONT, size=8, bold=True, color=PINE)
    chart = BarChart()
    chart.type, chart.style, chart.title, chart.legend = "col", 10, None, None
    chart.add_data(vals_ref, titles_from_data=False)
    chart.set_categories(cats_ref)
    series = chart.series[0]
    series.graphicalProperties.solidFill = STONE
    series.graphicalProperties.line.noFill = True
    if highlight is not None:
        point = DataPoint(idx=highlight)
        point.graphicalProperties.solidFill = PINE
        point.graphicalProperties.line.noFill = True
        series.dPt.append(point)
    series.dLbls = DataLabelList()
    series.dLbls.showVal = True
    series.dLbls.showSerName = series.dLbls.showCatName = series.dLbls.showLegendKey = False
    series.dLbls.txPr = arial(9, INK)
    chart.y_axis.majorGridlines = None
    chart.y_axis.numFmt = number_format
    chart.y_axis.delete = True
    chart.x_axis.delete = False
    chart.x_axis.txPr = arial(9, SLATE)
    chart.graphical_properties = GraphicalProperties()
    chart.graphical_properties.line.noFill = True
    chart.roundedCorners = False
    chart.height, chart.width = 7.5, 15
    ws.add_chart(chart, f"A{anchor_row + 1}")
    ws._next_row = anchor_row + 17
    return chart
```

Example:

```python
wb = workbook()
ws = sheet(wb, "Summary", "Property submission · Crescent Hospitality Group", "Three Florida hotels, $96.4M TIV")
key_figures(ws, [(96400000, "Total insured value", "money_m"), (1150000, "Target premium", "money_m2"), (535, "Rooms", "int"), (2547000, "Losses, 5 years", "money_m2")])
first, last = table(ws, ["Location", "Rooms", "Year built", "TIV ($)", "Construction", "Sprinklers", "Coast (mi)", "Status"],
      [["Tampa", 210, 2008, 41200000, "Fire-resistive", "Full", 2.1, None], ["Sarasota", 145, 1998, 24800000, "Joisted masonry", "60%", 0.4, None], ["Fort Myers", 180, 2015, 30400000, "Fire-resistive", "Full", 6.8, None]],
      ["text", "int", "year", "money", "text", "text", "dec1", "text"], total=["Total", 535, None, 96400000, None, None, None, None], highlight=(1, 6))
status(ws.cell(row=first, column=8), "Good", "good"); status(ws.cell(row=first+1, column=8), "Watch", "watch"); status(ws.cell(row=first+2, column=8), "Good", "good")
bar_chart(ws, "TIV by location", Reference(ws, min_col=1, min_row=first, max_row=last), Reference(ws, min_col=4, min_row=first, max_row=last), ws._next_row, highlight=1, number_format='"$"#,##0,,"M"')
ws2 = sheet(wb, "Losses", "Loss history · last five years", "Three losses totaling $2.55M")
table(ws2, ["Year", "Cause", "Location", "Incurred ($)"], [[2022, "Hurricane Ian", "Fort Myers", 2300000], [2023, "Water damage", "Sarasota", 185000], [2024, "Kitchen fire", "Tampa", 62000]],
      ["year", "text", "text", "money"], total=["Total", None, None, 2547000], highlight=(0, 3))
wb.save(out_path)
```

## Check before delivering

```python
from openpyxl import load_workbook

wb = load_workbook(out_path)
problems = []
for ws in wb.worksheets:
    if ws.sheet_view.showGridLines:
        problems.append(f"{ws.title}: gridlines are on")
    if ws.freeze_panes is None and ws.max_row > 20:
        problems.append(f"{ws.title}: long sheet, freeze the table header (table(..., freeze=True))")
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is None:
                continue
            if cell.font.name not in ("Georgia", "Arial"):
                problems.append(f"{ws.title}!{cell.coordinate}: font {cell.font.name}")
            if "furtherai" in str(cell.value).lower():
                problems.append(f"{ws.title}!{cell.coordinate}: remove the FurtherAI name")
            if isinstance(cell.value, str) and ("\u2014" in cell.value or "\u2013" in cell.value):
                problems.append(f"{ws.title}!{cell.coordinate}: em/en dash")
print("\n".join(sorted(set(problems))) or "OK")
```

Then render the workbook and look at every sheet (the sandbox has LibreOffice Calc):

```bash
mkdir -p render && timeout 180 soffice -env:UserInstallation=file:///tmp/lo_profile \
  --headless --convert-to pdf --outdir render book.xlsx >/dev/null 2>&1
pdftoppm -png -r 60 render/book.pdf render/sheet && ls render
```

Look for `####` (a column too narrow for its value), cut-off headers, charts covering
tables, and misaligned numbers. Fix the widths or formats and render again.
