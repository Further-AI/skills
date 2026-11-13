#!/usr/bin/env python3
"""FurtherAI PowerPoint layout library.

Tokens, primitives, chrome and one composite per slide pattern in the brand
template set.  Everything is data-driven: composites take plain Python
strings/lists/dicts and return the Slide they built.

Two hard rules encoded throughout (the shipped decks all carry the STOCK Office
theme, so nothing brand-coloured is inheritable):

  1. Never emit MSO_THEME_COLOR and never rely on an inherited theme font.
     Every run gets an explicit ``font.name`` + ``font.color.rgb``; every shape
     gets an explicit ``fill.fore_color.rgb`` or an explicit no-fill.
  2. ``shape.shadow.inherit = False`` on every shape we create -- python-pptx
     otherwise inherits the theme's drop shadow, which is not in the brand.

Font names come from ``fonts.py``.  Run ``python3 fonts.py check`` before
building; the template names ("Fraunces Light", "Fraunces 9pt", bare "Fraunces")
are deliberately NOT used here because none of them resolve from the vendored
TTFs -- see fonts.py for the full story.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.oxml.ns import qn
    from pptx.util import Emu, Inches, Pt
except ImportError:  # pragma: no cover
    raise SystemExit("python-pptx is required.\n  python3 -m pip install python-pptx")

import fonts as _fonts

FAMILY = _fonts.FAMILY
F = FAMILY

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
BRAND_DIR = PLUGIN_ROOT / "assets" / "brand"
LOGO_DIR = BRAND_DIR / "logos"

EMU_PER_IN = 914400
CANVAS_EMU = (12192000, 6858000)                     # PowerPoint-native 16:9
CANVAS = (CANVAS_EMU[0] / float(EMU_PER_IN),
          CANVAS_EMU[1] / float(EMU_PER_IN))          # (13.3333, 7.5) inches

# Slide sizes PowerPoint itself writes, in exact EMU.  A spec may say
# [13.333, 7.5] for readability -- 13.333in rounds to 12191695 EMU, 305 short
# of the templates' 12192000.  That 0.0003in gap is invisible but it makes a
# generated deck a DIFFERENT slide size from the brand decks, so pasting a
# slide between them triggers PowerPoint's rescale prompt and silently shifts
# every hand-placed coordinate.  Snap anything within 0.01in to the exact value.
STANDARD_CANVASES_EMU = [
    (12192000, 6858000),   # 13-1/3 x 7.5in  -- 16:9 widescreen, the default
    (9144000, 5143500),    # 10 x 5.625in    -- Google Slides 16:9
    (9144000, 6858000),    # 10 x 7.5in      -- 4:3
]
_CANVAS_SNAP_EMU = 9144    # 0.01in

# ---------------------------------------------------------------------------
# Palette 1 -- product / brand.  Harvested from Enterprise_Deck + Example_Slides
# 13-18.  This is the canonical FurtherAI palette.
# ---------------------------------------------------------------------------
BRAND = {
    "green_deep": "#074B40",       # primary green: headings, table header, gradient start
    "green": "#25654F",            # secondary green: rules, card outlines, logo mark
    "green_dark_text": "#1D4438",  # green text on light cards; dark table header
    "green_pale": "#D9EAD3",       # positive / total table cells
    "ink": "#14161C",
    "ink_alt": "#2B2D31",
    "ink_navy": "#1E1E2E",
    "warm_dark": "#444339",        # body on cream cards
    "paper": "#FBFBF9",            # primary off-white slide background
    "paper_warm": "#F4F3F0",
    "card": "#F8F7F5",
    "tan": "#D0CEC3",
    "hair_light": "#EEEEEE",
    "hair": "#CCCCCC",
    "hair_mid": "#9E9E9E",
    "muted": "#595959",
    "black_deep": "#0B0B12",
    "white": "#FFFFFF",
    "rule_dark": "#666666",
    "connector": "#44546A",
}
# Multi-stop gradients: [(position 0..1, hex), ...]
COVER_GRADIENT = [(0.0, "#074B40"), (0.47, "#1A421F"), (0.5, "#14452A"), (1.0, "#203E13")]
SCREENSHOT_GRADIENT = [(0.0, "#424242"), (1.0, "#010101")]

# ---------------------------------------------------------------------------
# Palette 2 -- consulting / analysis (Example_Slides 2-12), for dense
# analytical slides.
# ---------------------------------------------------------------------------
BRAND_CONSULTING = {
    "hdr_green": "#143524",        # card header bar fill
    "accent_green": "#2C7A4B",     # number bubbles, card outline, big arrow
    "eyebrow_green": "#B7D9C5",    # eyebrow text ON #143524
    "fill_green": "#E1EFD8",       # highlighted card / gantt bars
    "fill_pale": "#EDF3EF",        # nested sub-card
    "fill_neutral": "#F7F9F8",     # neutral card / zebra stripe
    "border": "#DDDDDD",
    "body": "#111111",
    "muted": "#5B6770",
    "label_green": "#385623",
    "matrix_ink": "#17250F",
    "bubble_green": "#274E13",
    "milestone": "#38761D",
    "sidebar_dark": "#051610",
    # The rejected side's header bar.  The source deck's #8A9893 gives white
    # only 3.00:1 -- under the 4.5:1 needed for the 12pt eyebrow on it.
    # Darkened along the same hue to 5.55:1.
    "neutral_header": "#5F6B66",
    "rule_grey": "#7F7F7F",
    "callout_red": "#C00000",
    "rate_up": "#1C2B11",
    "rate_side": "#A8D08C",
    "rate_down": "#7F7F7F",
    "sev_critical": "#C0392B",
    "sev_high": "#F08C3F",
    "sev_high_text": "#5C2F00",
    "sev_med": "#F4C842",
    "sev_med_text": "#664E00",
    "white": "#FFFFFF",
}
C = BRAND_CONSULTING

SEVERITY = {
    "critical": (C["sev_critical"], "#FFFFFF"),
    "high": (C["sev_high"], C["sev_high_text"]),
    "med": (C["sev_med"], C["sev_med_text"]),
    "medium": (C["sev_med"], C["sev_med_text"]),
    "low": (C["fill_neutral"], C["muted"]),
}
RATING = {  # arrow direction -> (rotation degrees, fill)
    "up": (0, C["rate_up"]),
    "favorable": (0, C["rate_up"]),
    "side": (90, C["rate_side"]),
    "neutral": (90, C["rate_side"]),
    "down": (180, C["rate_down"]),
    "unfavorable": (180, C["rate_down"]),
}

# Swimlane float / slack bars.  Warm and near-neutral so a buffer never reads
# as work: it sits between paper_warm and tan without belonging to either.
SWIMLANE_BUFFER = "#F0EEE4"

# status_table cell fills.  These are the Google-Sheets status greens/blues the
# reference trackers use, not brand tokens -- a status column has to read as a
# status column at a glance, and #D9EAD3 is the only overlap with the palette.
# Keys are normalized (see _status_key), so "In-Progress" and "In progress"
# both land on the same entry.
STATUS_FILLS = {
    "complete": "#D9EAD3",
    "done": "#D9EAD3",
    "in progress": "#C9DAF8",
    "not started": "#EEEEEE",
    "backlog": "#EEEEEE",
    "blocked": "#F4CCCC",
    "pending": "#FFF2CC",
}


def _status_key(text):
    """Normalize a status string for a case- and punctuation-insensitive lookup."""
    return " ".join(str(text).replace("-", " ").replace("_", " ").lower().split())

# ---------------------------------------------------------------------------
# Type ramp (section A.5), 13.333 x 7.5in canvas.  Multiply every size by 0.75
# for the 10 x 5.625in Google-Slides canvas.
# ---------------------------------------------------------------------------
RAMP = {
    "cover_headline":   dict(font=F["display_light"], size=49.3, color=BRAND["white"]),
    "hero_headline":    dict(font=F["display_light"], size=40.7, color=BRAND["white"]),
    "section_headline": dict(font=F["display_light"], size=34.7, color=BRAND["white"]),
    "rail_headline":    dict(font=F["display_light"], size=37.7, color="#000000"),
    "statement":        dict(font=F["display"],       size=33.3, color=BRAND["ink"]),
    "why_headline":     dict(font=F["display"],       size=31.0, color=BRAND["white"]),
    "trust_headline":   dict(font=F["display"],       size=29.3, color=BRAND["ink_alt"]),
    "slide_title":      dict(font=F["display"],       size=26.0, color=C["body"]),
    "slide_title_sm":   dict(font=F["display"],       size=24.0, color=C["body"]),
    "agenda_title":     dict(font=F["display"],       size=44.0, color=BRAND["white"]),
    "card_title":       dict(font=F["display"],       size=24.0, color=BRAND["warm_dark"]),
    "rail_item":        dict(font=F["display"],       size=20.7, color="#000000"),
    "subhead":          dict(font=F["body"],          size=14.0, color=C["muted"]),
    "lede":             dict(font=F["body"],          size=19.0, color=C["body"]),
    "card_heading":     dict(font=F["body"],          size=17.0, color=BRAND["white"]),
    "body":             dict(font=F["body"],          size=14.0, color=C["body"]),
    "body_sm":          dict(font=F["body"],          size=12.0, color=BRAND["ink_navy"]),
    "caption":          dict(font=F["body"],          size=12.0, color=C["muted"]),
    "eyebrow":          dict(font=F["body"],          size=12.0, color=C["eyebrow_green"]),
    "eyebrow_light":    dict(font=F["body"],          size=12.0, color=C["label_green"]),
    "footer":           dict(font=F["body"],          size=9.0,  color=C["muted"]),
    "footer_dark":      dict(font=F["body"],          size=8.7,  color=BRAND["hair_light"]),
    "badge_mono":       dict(font=F["mono"],          size=11.3, color=BRAND["tan"]),
}

THEMES = {"brand": BRAND, "consulting": BRAND_CONSULTING}

_BRAND_DEFAULTS = dict(BRAND)
_GRADIENT_DEFAULT = list(COVER_GRADIENT)


def set_theme(name="brand"):
    """Switch the accent greens used by the theme-neutral patterns.

    'brand'       product palette (#074B40 / #25654F) + the 4-stop cover gradient
    'consulting'  analysis palette (#143524 / #2C7A4B) + a flat dark-green gradient

    The analytical patterns (before_after, two_pillars, scorecard,
    process_audit, rag_matrix, timeline) always use the consulting palette --
    that is what Example_Slides 2-12 do, and mixing them would break fidelity.
    This switch only affects cover / divider / closing gradients and the generic
    card, stat and table chrome.
    """
    global COVER_GRADIENT
    BRAND.update(_BRAND_DEFAULTS)
    COVER_GRADIENT = list(_GRADIENT_DEFAULT)
    if name == "consulting":
        BRAND["green_deep"] = C["hdr_green"]
        BRAND["green"] = C["accent_green"]
        BRAND["green_dark_text"] = C["hdr_green"]
        BRAND["green_pale"] = C["fill_green"]
        COVER_GRADIENT = [(0.0, C["sidebar_dark"]), (1.0, C["hdr_green"])]
    elif name != "brand":
        raise ValueError("unknown theme %r (brand | consulting)" % name)
    return name


# ===========================================================================
# low-level helpers
# ===========================================================================
def _rgb(value):
    """'#074B40' | '074B40' | RGBColor -> RGBColor."""
    if isinstance(value, RGBColor):
        return value
    return RGBColor.from_string(str(value).lstrip("#").upper())


def _relative_luminance(color):
    """WCAG relative luminance of a hex colour."""
    h = str(color).lstrip("#")
    parts = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    lin = [(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4)
           for v in parts]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast_ratio(a, b):
    """WCAG contrast ratio between two hex colours (1.0 .. 21.0)."""
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def is_dark(color):
    """True when white text reads better on this ground than near-black does."""
    return contrast_ratio(color, BRAND["white"]) >= contrast_ratio(color, BRAND["ink"])


def _fill_hex(shape):
    """The hex a shape paints, or None.  Gradients report their LIGHTEST stop,
    which is the worst case for light text sitting on them."""
    el = getattr(shape, "_element", None)
    spPr = el.find(qn("p:spPr")) if el is not None else None
    if spPr is None:
        return None
    grad = spPr.find(qn("a:gradFill"))
    if grad is not None:
        stops = [c.get("val") for c in grad.iter(qn("a:srgbClr")) if c.get("val")]
        if stops:
            return "#" + max(stops, key=lambda v: _relative_luminance("#" + v))
    solid = spPr.find(qn("a:solidFill"))
    if solid is not None:
        clr = solid.find(qn("a:srgbClr"))
        if clr is not None:
            return "#" + clr.get("val")
    return None


def slide_bg_hex(slide):
    """The slide's own <p:bg> colour, or None."""
    cSld = slide._element.find(qn("p:cSld"))
    bg = cSld.find(qn("p:bg")) if cSld is not None else None
    if bg is None:
        return None
    clr = bg.find(".//" + qn("a:srgbClr"))
    return "#" + clr.get("val") if clr is not None else None


def ground_at(slide, x_in, y_in, default=None):
    """The colour behind a point, in inches: the topmost filled shape covering
    it, else the slide background, else ``default`` / white.

    This is what lets chrome pick its own treatment instead of making every
    caller remember whether its background happens to be dark.
    """
    x, y = x_in * EMU_PER_IN, y_in * EMU_PER_IN
    found = None
    for shp in slide.shapes:
        left, top = shp.left, shp.top
        width, height = shp.width, shp.height
        if None in (left, top, width, height):
            continue
        if not (left <= x <= left + width and top <= y <= top + height):
            continue
        hexval = _fill_hex(shp)
        if hexval:
            found = hexval
    return found or slide_bg_hex(slide) or default or BRAND["white"]


def _shadow_off(shape):
    """python-pptx inherits the theme drop shadow by default; kill it."""
    try:
        shape.shadow.inherit = False
    except (AttributeError, NotImplementedError):
        spPr = getattr(shape, "_element", None)
        spPr = spPr.find(qn("p:spPr")) if spPr is not None else None
        if spPr is not None and spPr.find(qn("a:effectLst")) is None:
            spPr.append(spPr.makeelement(qn("a:effectLst"), {}))
    return shape


def _strip_theme_style(shape):
    """Drop the <p:style> block python-pptx adds to every autoshape.

    That block carries lnRef / fillRef / effectRef / fontRef pointing at the
    THEME (a:schemeClr accent1, and a drop shadow).  We set spPr explicitly on
    every shape, so the block is dead weight -- but leaving it in means any
    property we ever forget silently falls back to the stock Office theme.
    """
    el = getattr(shape, "_element", None)
    if el is None:
        return shape
    for style in el.findall(qn("p:style")):
        el.remove(style)
    return shape




def _set_fill(shape, color):
    if color is None:
        shape.fill.background()
    else:
        shape.fill.solid()
        shape.fill.fore_color.rgb = _rgb(color)
    return shape


def _set_line(shape, color, width_in=0.01):
    if color is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = _rgb(color)
        shape.line.width = Inches(width_in)
    return shape


def _set_font(run, font=None, size=None, color=None, bold=None, italic=None,
              underline=None):
    """Explicit run formatting -- name on latin/ea/cs so nothing inherits."""
    f = run.font
    if size is not None:
        f.size = Pt(size)
    if bold is not None:
        f.bold = bool(bold)
    if italic is not None:
        f.italic = bool(italic)
    if underline is not None:
        f.underline = bool(underline)
    if color is not None:
        f.color.rgb = _rgb(color)
    if font is not None:
        f.name = font
        rPr = run._r.get_or_add_rPr()
        for tag in ("a:ea", "a:cs"):
            el = rPr.find(qn(tag))
            if el is None:
                el = rPr.makeelement(qn(tag), {})
                rPr.append(el)
            el.set("typeface", font)
    return run


_set = _set_font  # short alias used internally


def _set_bullet(paragraph, char="•", font=None, indent_in=0.22):
    """Add a buChar bullet (python-pptx has no bullet API)."""
    pPr = paragraph._p.get_or_add_pPr()
    pPr.set("marL", str(int(indent_in * EMU_PER_IN)))
    pPr.set("indent", str(-int(indent_in * EMU_PER_IN)))
    for tag in ("a:buNone", "a:buChar", "a:buAutoNum", "a:buFont"):
        for el in pPr.findall(qn(tag)):
            pPr.remove(el)
    bu_font = pPr.makeelement(qn("a:buFont"), {"typeface": font or F["body"]})
    bu_char = pPr.makeelement(qn("a:buChar"), {"char": char})
    # buFont/buChar must precede any run elements; pPr has none, so append.
    pPr.append(bu_font)
    pPr.append(bu_char)
    return paragraph


def _track(shape, pt=1.0):
    """Letterspace every run in a shape (python-pptx exposes no spacing API).

    ``spc`` on a:rPr is in 1/100 pt.  Used only for the all-caps eyebrow
    labels, which close up badly at small sizes without it.
    """
    tf = getattr(shape, "text_frame", None)
    if tf is None:
        return shape
    for para in tf.paragraphs:
        for run in para.runs:
            run._r.get_or_add_rPr().set("spc", str(int(round(pt * 100))))
    return shape


_ALIGN = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER,
          "right": PP_ALIGN.RIGHT, "justify": PP_ALIGN.JUSTIFY}
_ANCHOR = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE,
           "bottom": MSO_ANCHOR.BOTTOM}


def _gradient(shape, stops, angle=90.0):
    """Multi-stop linear gradient fill.  ``stops`` = [(pos 0..1, hex), ...].

    python-pptx only models two-stop gradients, so write the gradFill directly.
    """
    spPr = shape._element.spPr
    for tag in ("a:noFill", "a:solidFill", "a:gradFill", "a:blipFill",
                "a:pattFill", "a:grpFill"):
        for el in spPr.findall(qn(tag)):
            spPr.remove(el)
    grad = spPr.makeelement(qn("a:gradFill"), {"flip": "none", "rotWithShape": "1"})
    gs_lst = grad.makeelement(qn("a:gsLst"), {})
    for pos, hexval in stops:
        gs = gs_lst.makeelement(qn("a:gs"), {"pos": str(int(round(pos * 100000)))})
        clr = gs.makeelement(qn("a:srgbClr"), {"val": str(hexval).lstrip("#").upper()})
        gs.append(clr)
        gs_lst.append(gs)
    grad.append(gs_lst)
    lin = grad.makeelement(qn("a:lin"), {"ang": str(int(round(angle * 60000))),
                                         "scaled": "0"})
    grad.append(lin)
    # spPr children are ordered: xfrm, geometry, fill, line, effects
    ln = spPr.find(qn("a:ln"))
    if ln is not None:
        ln.addprevious(grad)
    else:
        spPr.append(grad)
    return shape


# ===========================================================================
# deck / slide setup
# ===========================================================================
def new_deck(width_in=CANVAS[0], height_in=CANVAS[1]):
    """Blank presentation on the FurtherAI canvas (default 13.333 x 7.5in)."""
    w = int(round(width_in * EMU_PER_IN))
    h = int(round(height_in * EMU_PER_IN))
    w, h = snap_canvas(w, h)
    prs = Presentation()
    prs.slide_width = Emu(w)
    prs.slide_height = Emu(h)
    return prs


def snap_canvas(width_emu, height_emu):
    """Snap a near-standard slide size to the exact EMU PowerPoint writes."""
    for std_w, std_h in STANDARD_CANVASES_EMU:
        if (abs(width_emu - std_w) <= _CANVAS_SNAP_EMU
                and abs(height_emu - std_h) <= _CANVAS_SNAP_EMU):
            return std_w, std_h
    return width_emu, height_emu


def _blank_layout(prs):
    for layout in prs.slide_layouts:
        if layout.name and layout.name.lower() == "blank":
            return layout
    return prs.slide_layouts[6]


def slide_size(prs):
    return (prs.slide_width / EMU_PER_IN, prs.slide_height / EMU_PER_IN)


def blank_slide(prs, bg=None, gradient=None, gradient_angle=90.0, image=None):
    """Append a slide on the BLANK layout.

    bg        hex fill for the slide background
    gradient  [(pos, hex), ...] -- drawn as a full-bleed rectangle
    image     full-bleed picture path (drawn first, so it sits behind)
    """
    slide = prs.slides.add_slide(_blank_layout(prs))
    for ph in list(slide.placeholders):
        ph._element.getparent().remove(ph._element)
    w, h = slide_size(prs)
    if bg:
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = _rgb(bg)
    if image and Path(image).exists():
        slide.shapes.add_picture(str(image), 0, 0, Inches(w), Inches(h))
    elif gradient:
        shp = rect(slide, 0, 0, w, h, fill=None, line=None)
        _gradient(shp, gradient, gradient_angle)
    return slide


# ===========================================================================
# primitives  (spec section H.2)
# ===========================================================================
def rect(slide, l, t, w, h, *, fill=None, line=None, line_w_in=0.01,
         rounded=False, shape=None, rotation=None, adjust=None):
    """Filled / outlined autoshape with no text.  Basis for cards and bars."""
    if shape is None:
        shape = MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE
    shp = slide.shapes.add_shape(shape, Inches(l), Inches(t), Inches(w), Inches(h))
    _strip_theme_style(shp)
    _shadow_off(shp)
    _set_fill(shp, fill)
    _set_line(shp, line, line_w_in)
    if rotation:
        shp.rotation = rotation
    if adjust is not None:
        try:
            shp.adjustments[0] = adjust
        except (IndexError, ValueError):
            pass
    shp.text_frame.word_wrap = True
    return shp


def rule(slide, l, t, w, *, color=None, w_in=0.01, vertical=False):
    """Horizontal (default) or vertical hairline, drawn as a straight connector."""
    color = color or BRAND["hair"]
    x2, y2 = (l, t + w) if vertical else (l + w, t)
    con = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(l), Inches(t),
                                     Inches(x2), Inches(y2))
    con.line.color.rgb = _rgb(color)
    con.line.width = Inches(w_in)
    # Connectors have no fill API in python-pptx; write an explicit noFill so the
    # shape inherits nothing at all from the theme.
    spPr = con._element.spPr
    if not any(spPr.find(qn(t)) is not None for t in
               ("a:noFill", "a:solidFill", "a:gradFill", "a:blipFill",
                "a:pattFill", "a:grpFill")):
        no_fill = spPr.makeelement(qn("a:noFill"), {})
        ln = spPr.find(qn("a:ln"))
        if ln is not None:
            ln.addprevious(no_fill)
        else:
            spPr.append(no_fill)
    _strip_theme_style(con)
    _shadow_off(con)
    return con


def _iter_paragraphs(runs):
    """Normalise the content model into [[(text, style), ...], ...].

    "abc"                              -> one paragraph, one run
    ["a", "b"]                         -> two paragraphs
    [[("Bold ", {"bold": True}), ("rest", {})], "b"]  -> mixed runs in para 1
    ("a", {"bold": True})              -> one styled paragraph
    """
    if runs is None:
        return []
    if isinstance(runs, str):
        return [[(runs, {})]]
    if isinstance(runs, tuple) and len(runs) == 2 and isinstance(runs[0], str):
        return [[(runs[0], runs[1] or {})]]
    out = []
    for para in runs:
        if para is None:
            out.append([("", {})])
        elif isinstance(para, str):
            out.append([(para, {})])
        elif isinstance(para, tuple) and len(para) == 2 and isinstance(para[0], str):
            out.append([(para[0], para[1] or {})])
        else:
            bits = []
            for piece in para:
                if isinstance(piece, str):
                    bits.append((piece, {}))
                else:
                    bits.append((piece[0], piece[1] or {}))
            out.append(bits)
    return out


def text_box(slide, l, t, w, h, runs, *, size=12, font=None, color=None,
             bold=False, italic=False, align="left", anchor="top",
             line_spacing=None, space_before=None, space_after=None,
             bullets=False, bullet_char="•", margins=(0, 0, 0, 0),
             fill=None, line=None, line_w_in=0.01, word_wrap=True,
             shrink=False):
    """Text box.  ``runs`` follows the content model in ``_iter_paragraphs``.

    Every run is given an explicit font name, size and colour; per-run overrides
    ride in the style dict ({'bold','italic','size','color','font'}).
    """
    font = font or F["body"]
    color = color or C["body"]
    box = slide.shapes.add_textbox(Inches(l), Inches(t), Inches(w), Inches(h))
    _shadow_off(box)
    tf = box.text_frame
    tf.word_wrap = word_wrap
    tf.margin_left, tf.margin_top, tf.margin_right, tf.margin_bottom = (
        Inches(margins[0]), Inches(margins[1]), Inches(margins[2]), Inches(margins[3]))
    tf.vertical_anchor = _ANCHOR.get(anchor, MSO_ANCHOR.TOP)
    if fill is not None or line is not None:
        _set_fill(box, fill)
        _set_line(box, line, line_w_in)
    if shrink:
        from pptx.enum.text import MSO_AUTO_SIZE
        tf.auto_size = MSO_AUTO_SIZE.NONE

    paras = _iter_paragraphs(runs)
    for i, pieces in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = _ALIGN.get(align, PP_ALIGN.LEFT)
        if line_spacing is not None:
            p.line_spacing = line_spacing
        if space_before is not None:
            p.space_before = Pt(space_before)
        if space_after is not None:
            p.space_after = Pt(space_after)
        if bullets:
            _set_bullet(p, bullet_char, font)
        for text, style in pieces:
            r = p.add_run()
            r.text = text
            _set_font(r,
                      font=style.get("font", font),
                      size=style.get("size", size),
                      color=style.get("color", color),
                      bold=style.get("bold", bold),
                      italic=style.get("italic", italic),
                      underline=style.get("underline"))
    return box


def _label_in(shape, runs, *, size=12, font=None, color=None, bold=False,
              italic=False, align="left", anchor="middle", pad_in=0.1,
              line_spacing=None):
    """Write text INTO an existing autoshape (header bars, chips, bubbles)."""
    font = font or F["body"]
    color = color or BRAND["white"]
    tf = shape.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = _ANCHOR.get(anchor, MSO_ANCHOR.MIDDLE)
    tf.margin_left = tf.margin_right = Inches(pad_in)
    tf.margin_top = tf.margin_bottom = Inches(0.02)
    paras = _iter_paragraphs(runs)
    for i, pieces in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = _ALIGN.get(align, PP_ALIGN.LEFT)
        if line_spacing is not None:
            p.line_spacing = line_spacing
        for text, style in pieces:
            r = p.add_run()
            r.text = text
            _set_font(r,
                      font=style.get("font", font),
                      size=style.get("size", size),
                      color=style.get("color", color),
                      bold=style.get("bold", bold),
                      italic=style.get("italic", italic))
    return shape


def circle_label(slide, l, t, d, text, *, fill=None, color=None, size=14,
                 font=None, line=None):
    """Numbered-step bubble: an OVAL plus a centred label written into it."""
    shp = rect(slide, l, t, d, d, fill=fill or C["accent_green"],
               line=line or fill or C["accent_green"], shape=MSO_SHAPE.OVAL)
    _label_in(shp, str(text), size=size, font=font or F["body"],
              color=color or BRAND["white"], align="center", anchor="middle",
              pad_in=0.0)
    return shp


def picture(slide, path, l, t, w=None, h=None):
    """Insert an image, preserving aspect when only one dimension is given."""
    path = str(path)
    if not Path(path).exists():
        raise FileNotFoundError(path)
    kw = {}
    if w is not None:
        kw["width"] = Inches(w)
    if h is not None:
        kw["height"] = Inches(h)
    pic = slide.shapes.add_picture(path, Inches(l), Inches(t), **kw)
    _shadow_off(pic)
    return pic


def chip(slide, l, t, w, h, text, *, fill=None, color=None, size=14,
         line=None, line_w_in=0.01, align="center", font=None, bold=False):
    """Rounded rating / severity chip."""
    shp = rect(slide, l, t, w, h, fill=fill or C["fill_neutral"],
               line=line, line_w_in=line_w_in, rounded=True, adjust=0.25)
    _label_in(shp, text, size=size, color=color or C["muted"], align=align,
              font=font, bold=bold, pad_in=0.05)
    return shp


def placeholder_panel(slide, l, t, w, h, label="Screenshot", *,
                      gradient=None, color=None):
    """Stand-in for artwork we do not have (product shots, background art)."""
    shp = rect(slide, l, t, w, h, fill=None, line=None)
    _gradient(shp, gradient or SCREENSHOT_GRADIENT, 45.0)
    if label:
        text_box(slide, l, t + h / 2 - 0.2, w, 0.4, label, size=12,
                 color=BRAND["hair_light"] if color is None else color,
                 align="center")
    return shp


# ===========================================================================
# logos  (spec section F)
# ===========================================================================
# Logo_Full_Green_on_White.jpg is 640x360 with the artwork confined to
# (22,116)-(614,242) -- ~57% of the canvas is white padding.  Placed uncropped
# it renders tiny and vertically off-centre, so always crop to the content bbox.
LOGO_BBOX = (21, 116, 615, 244)          # measured, matches the spec's (22,116)-(614,242)
LOGO_WORDMARK_ASPECT = 4.64              # cropped content w/h
LOGO_MARK_COLOR = "#056250"              # the wordmark's own green
_LOGO_CACHE = {}
_TMPDIR = None
_NOTES = []


def notes():
    """Degradation notes accumulated during a build (asset fallbacks, etc.)."""
    return list(_NOTES)


def _note(msg):
    if msg not in _NOTES:
        _NOTES.append(msg)


def _tmpdir():
    global _TMPDIR
    if _TMPDIR is None:
        _TMPDIR = tempfile.mkdtemp(prefix="fai_deck_")
    return Path(_TMPDIR)


def _pillow():
    try:
        from PIL import Image, ImageChops
        return Image, ImageChops
    except ImportError:
        return None, None


def logo_png(dark_bg=False, color=None):
    """Path to a transparent wordmark PNG for the given background.

    Derivation order:
      1. cairosvg on Logo_Full_White_on_Transparent.svg (best; rarely installed)
      2. Pillow: crop Logo_Full_Green_on_White.jpg to its content bbox and key
         the white field to alpha, recolouring the mark (white / brand green)
      3. Logo_Social_White_on_Green.png -- square, opaque green field.  Noted as
         a compromise because it is a different lockup.
    Returns (path, aspect_ratio).
    """
    key = ("dark" if dark_bg else "light", color or "")
    if key in _LOGO_CACHE:
        return _LOGO_CACHE[key]
    target = color or (BRAND["white"] if dark_bg else LOGO_MARK_COLOR)
    out = _tmpdir() / ("logo_%s_%s.png" % (key[0], target.lstrip("#")))

    if dark_bg and not color:
        svg = LOGO_DIR / "Logo_Full_White_on_Transparent.svg"
        try:
            import cairosvg
            cairosvg.svg2png(url=str(svg), write_to=str(out), output_width=2048)
            _LOGO_CACHE[key] = (out, 1600.0 / 900.0)
            return _LOGO_CACHE[key]
        except Exception:                                          # noqa: BLE001
            pass

    Image, ImageChops = _pillow()
    src = LOGO_DIR / "Logo_Full_Green_on_White.jpg"
    if Image is not None and src.exists():
        im = Image.open(src).convert("RGB")
        grey = im.convert("L")
        bbox = ImageChops.invert(grey).point(lambda v: 255 if v > 25 else 0).getbbox() \
            or LOGO_BBOX
        im, grey = im.crop(bbox), grey.crop(bbox)
        lo = grey.getextrema()[0]
        span = max(1, 255 - lo)
        alpha = grey.point(lambda v: max(0, min(255, int(round((255 - v) * 255.0 / span)))))
        rgb = _rgb(target)
        flat = Image.new("RGBA", im.size, (rgb[0], rgb[1], rgb[2], 0))
        flat.putalpha(alpha)
        flat = flat.resize((im.size[0] * 2, im.size[1] * 2), Image.LANCZOS)
        flat.save(out)
        _LOGO_CACHE[key] = (out, im.size[0] / float(im.size[1]))
        return _LOGO_CACHE[key]

    if Image is None:
        _note("Pillow is not installed, so the wordmark could not be cropped to "
              "its content bbox; using the square social mark instead "
              "(python3 -m pip install Pillow for the proper lockup).")
    fallback = LOGO_DIR / ("Logo_Social_White_on_Green.png" if dark_bg
                           else "Logo_Social_Green_on_White.png")
    _note("Logo fallback: %s (square mark on an opaque field, not the "
          "horizontal wordmark)." % fallback.name)
    _LOGO_CACHE[key] = (fallback, 1.0)
    return _LOGO_CACHE[key]


def logo_mark_png(dark_bg=True):
    """Square F mark (for hub nodes / avatars)."""
    return LOGO_DIR / ("Logo_Social_White_on_Green.png" if dark_bg
                       else "Logo_Social_Green_on_White.png")


def logo(slide, *, dark_bg=False, corner="tl", width_in=1.4, l=None, t=None,
         color=None):
    """Drop the wordmark at a canonical corner for that variant.

    Light slides: L0.4 T0.3 W1.4 (or top-right L11.19 T0.247 W1.477).
    Dark slides:  L11.95 T0.344 W0.969, or L0.647 T0.624 W1.94 on covers.
    """
    path, aspect = logo_png(dark_bg=dark_bg, color=color)
    h = width_in / aspect
    if l is None or t is None:
        margin = 0.4
        positions = {
            "tl": (0.4, 0.3),
            "tr": (CANVAS[0] - margin - width_in, 0.28),
            "bl": (0.4, CANVAS[1] - 0.3 - h),
            "br": (CANVAS[0] - margin - width_in, CANVAS[1] - 0.3 - h),
            "center": ((CANVAS[0] - width_in) / 2.0, (CANVAS[1] - h) / 2.0),
        }
        l, t = positions.get(corner, positions["tl"])
    return picture(slide, path, l, t, w=width_in)


# ===========================================================================
# chrome  (spec section H.3)
# ===========================================================================
def title_block(slide, title, *, subtitle=None, eyebrow=None, variant="analysis",
                color=None, eyebrow_color=None, subtitle_color=None,
                width=12.5, left=0.4):
    """Slide title recipes.

    analysis   L0.4 T1.25 W12.5 H0.6, Fraunces 26pt; subtitle T1.9 WMD 14pt muted
    compact    title T0.85 H0.55 at 24pt; subtitle T1.36
    systematic eyebrow T0.45 WMD 11.5pt bold green + headline T0.78 Fraunces 28pt
    statement  Fraunces 33.3pt at L0.667 T0.858
    """
    if variant == "analysis":
        text_box(slide, left, 1.25, width, 0.6, title, anchor="middle",
                 **_ramp("slide_title", color))
        if subtitle:
            text_box(slide, left, 1.9, width, 0.5, subtitle, **_ramp("subhead"))
    elif variant == "compact":
        text_box(slide, left, 0.85, width, 0.55, title, anchor="middle",
                 **_ramp("slide_title_sm", color))
        if subtitle:
            text_box(slide, left, 1.493, min(width, 10.002), 0.431, subtitle,
                     **_ramp("lede"))
    elif variant == "systematic":
        if eyebrow:
            text_box(slide, 0.55, 0.45, 11.5, 0.3, eyebrow.upper(), size=11.5,
                     bold=True, font=F["body"],
                     color=eyebrow_color or BRAND["green_deep"])
        text_box(slide, 0.55, 0.78, 11.5, 0.95, title, size=28,
                 font=F["display_light"], color=color or BRAND["ink"],
                 anchor="middle")
        if subtitle:
            text_box(slide, 0.55, 1.78, 12.2, 0.5, subtitle, size=14.5,
                     font=F["body"], color=subtitle_color or BRAND["ink_alt"])
    elif variant == "statement":
        text_box(slide, 0.667, 0.858, 12.0, 0.695, title, size=33.3,
                 font=F["display"], color=color or BRAND["ink"], anchor="middle")
        if subtitle:
            text_box(slide, 0.667, 1.6, 12.0, 0.5, subtitle, **_ramp("subhead"))
    else:
        raise ValueError("unknown title variant %r" % variant)


def _ramp(name, color=None, **over):
    """Spread a RAMP entry into text_box kwargs."""
    spec = dict(RAMP[name])
    if color:
        spec["color"] = color
    spec.update(over)
    return spec


def footer(slide, *, left="AI Workspace for Insurance", center="furtherai.com",
           page=None, dark=None, rule_line=None):
    """Standard footer trio.  Light: T7.15 WMD 9pt #5B6770.  Dark: T6.996 8.7pt.

    ``dark`` defaults to None, meaning INFER: the band's position and size come
    from the slide's own background, and each of the three items is coloured
    from the ground directly under it.  That is what keeps #5B6770 off a dark
    slide (1.73:1, effectively invisible) without every composite having to
    remember its own background -- and it gets partial grounds right too, e.g.
    the agenda's dark left panel, where the left credit needs #EEEEEE while the
    centre and page number are still over paper.

    Pass dark=True/False to force the treatment (needed when the background is
    an image, whose colour cannot be read).
    """
    if dark is None:
        layout_dark = is_dark(slide_bg_hex(slide) or BRAND["white"])
    else:
        layout_dark = bool(dark)
    if rule_line is None:
        rule_line = layout_dark

    if layout_dark:
        top, height, size = 6.996, 0.281, RAMP["footer_dark"]["size"]
        slots = [(left, 0.276, 3.256, "left"), (center, 6.051, 3.3, "center"),
                 (None if page is None else str(page), 11.827, 1.2, "right")]
        if rule_line:
            rule(slide, 0.284, 6.839, 12.765, color=BRAND["rule_dark"], w_in=0.005)
    else:
        top, height, size = 7.15, 0.25, RAMP["footer"]["size"]
        slots = [(left, 0.4, 4.0, "left"), (center, 5.0, 3.3, "center"),
                 (None if page is None else str(page), 11.6, 1.3, "right")]
        if rule_line:
            rule(slide, 0.4, 7.05, 12.5, color=BRAND["hair"], w_in=0.005)

    for text, l, w, align in slots:
        if not text:
            continue
        if dark is None:
            ground = ground_at(slide, l + w / 2.0, top + height / 2.0)
            color = BRAND["hair_light"] if is_dark(ground) else C["muted"]
        else:
            color = BRAND["hair_light"] if layout_dark else C["muted"]
        text_box(slide, l, top, w, height, text, size=size, font=F["body"],
                 color=color, align=align)


# ===========================================================================
# reusable elements
# ===========================================================================
def card(slide, l, t, w, h, *, header=None, eyebrow=None, summary=None,
         body=None, bullets=None, blocks=None, output=None,
         fill=None, border=None, border_w=0.01, header_fill=None,
         header_h=0.8, header_color=None, eyebrow_color=None,
         header_size=18, eyebrow_size=12, body_size=14.5, body_color=None,
         summary_color=None, output_h=0.55, pad=0.25, rounded=False):
    """The universal card used by slides 3-7.

    Optional dark header bar (eyebrow + title), a summary line, N nested
    ``#EDF3EF`` sub-blocks, free body text or bullets, and a full-width OUTPUT
    footer bar.  Returns the card body shape.
    """
    fill = fill if fill is not None else C["fill_neutral"]
    border = border if border is not None else C["border"]
    header_fill = header_fill or C["hdr_green"]
    header_color = header_color or BRAND["white"]
    eyebrow_color = eyebrow_color or C["eyebrow_green"]
    body_color = body_color or C["body"]
    summary_color = summary_color or C["hdr_green"]

    body_shape = rect(slide, l, t, w, h, fill=fill, line=border,
                      line_w_in=border_w, rounded=rounded)
    cursor = t + 0.15
    if header or eyebrow:
        rect(slide, l, t, w, header_h, fill=header_fill, line=header_fill,
             line_w_in=0.014, rounded=rounded)
        y = t + 0.07
        if eyebrow:
            text_box(slide, l + pad, y, w - 2 * pad, 0.25, eyebrow.upper(),
                     size=eyebrow_size, font=F["body"], color=eyebrow_color)
            y += 0.23
        if header:
            text_box(slide, l + pad, y, w - 2 * pad, header_h - (y - t) - 0.05,
                     header, size=header_size, font=F["body"], color=header_color)
        cursor = t + header_h + 0.1

    if summary:
        text_box(slide, l + pad, cursor, w - 2 * pad, 0.55, summary, size=12,
                 font=F["body"], color=summary_color, line_spacing=1.1)
        cursor += 0.65

    if blocks:
        block_h = 0.95
        for label, text in blocks:
            rect(slide, l + pad, cursor, w - 2 * pad, block_h,
                 fill=C["fill_pale"], line=None)
            text_box(slide, l + pad + 0.15, cursor + 0.1, w - 2 * pad - 0.3, 0.3,
                     label, size=14, font=F["body"], color=C["hdr_green"])
            text_box(slide, l + pad + 0.15, cursor + 0.42, w - 2 * pad - 0.3,
                     block_h - 0.5, text, size=12, font=F["body"],
                     color=C["body"], line_spacing=1.05)
            cursor += block_h + 0.09

    bottom = t + h - (output_h + 0.1 if output else 0.1)
    if bullets:
        text_box(slide, l + pad, cursor, w - 2 * pad, max(0.3, bottom - cursor),
                 list(bullets), size=body_size, font=F["body"], color=body_color,
                 bullets=True, space_before=12, line_spacing=1.05)
    elif body:
        text_box(slide, l + pad, cursor, w - 2 * pad, max(0.3, bottom - cursor),
                 body, size=body_size, font=F["body"], color=body_color,
                 line_spacing=1.1, space_after=6)

    if output:
        bar_t = t + h - output_h
        rect(slide, l, bar_t, w, output_h, fill=C["hdr_green"], line=None)
        text_box(slide, l + pad, bar_t, w - 2 * pad, output_h,
                 [[("OUTPUT   ", {"size": 10, "color": C["eyebrow_green"]}),
                   (output, {"size": 12, "color": BRAND["white"]})]],
                 font=F["body"], anchor="middle")
    return body_shape


def steps_list(slide, l, t, w, steps, *, bubble_d=0.32, pitch=None,
               zebra=False, dividers=True, caption_color=None,
               title_size=14, caption_size=14, bubble_fill=None,
               bubble_size=14, start=1):
    """Numbered rows: bubble + title (+ caption), used by slides 5 and 7.

    ``steps`` items are ``"title"``, ``("title", "caption")`` or
    ``{"title":..., "caption":...}``.
    """
    caption_color = caption_color or C["muted"]
    bubble_fill = bubble_fill or C["accent_green"]
    norm = []
    for s in steps:
        if isinstance(s, dict):
            norm.append((s.get("title", ""), s.get("caption")))
        elif isinstance(s, (list, tuple)):
            norm.append((s[0], s[1] if len(s) > 1 else None))
        else:
            norm.append((s, None))
    n = max(1, len(norm))
    pitch = pitch or 0.65
    for i, (title, caption) in enumerate(norm):
        y = t + i * pitch
        if zebra and i % 2 == 0:
            rect(slide, l - 0.05, y - 0.08, w + 0.1, pitch - 0.07,
                 fill=C["fill_neutral"], line=None)
        circle_label(slide, l, y, bubble_d, start + i, fill=bubble_fill,
                     size=bubble_size)
        tx = l + bubble_d + 0.1
        tw = w - (bubble_d + 0.1)
        if caption:
            text_box(slide, tx, y - 0.055, tw, 0.28, title, size=title_size,
                     font=F["body"], color=C["body"])
            text_box(slide, tx, y + 0.195, tw, 0.25, caption, size=caption_size,
                     font=F["body"], color=caption_color)
        else:
            text_box(slide, tx, y - 0.06, tw, bubble_d + 0.12, title,
                     size=title_size, font=F["body"], color=C["body"],
                     anchor="middle")
        if dividers and i < n - 1:
            rule(slide, l + 0.02, y + pitch - 0.12, w - 0.04,
                 color=C["rule_grey"], w_in=0.01)


def line_table(slide, l, t, headers, rows, col_widths, *, header_rule_w=0.016,
               row_rule_w=0.008, row_h=0.6, header_size=16, body_size=14,
               color=None, rule_color=None, header_color=None, gap=0.28):
    """A "table" made of text boxes + rules (slide 12).

    Full control of column widths and per-cell mixed-format runs; preferred over
    a native table for layout-critical work.  Returns the y of the last rule.
    """
    color = color or BRAND["ink_navy"]
    rule_color = rule_color or C["rule_grey"]
    header_color = header_color or BRAND["ink_navy"]
    total_w = sum(col_widths) + gap * (len(col_widths) - 1)
    x = l
    for i, head in enumerate(headers):
        text_box(slide, x, t, col_widths[i], 0.3, head, size=header_size,
                 bold=True, font=F["body"], color=header_color)
        x += col_widths[i] + gap
    y = t + 0.32
    rule(slide, l, y, total_w, color="#000000", w_in=header_rule_w)
    for r in rows:
        y += 0.12
        x = l
        for i, cell in enumerate(r):
            if i >= len(col_widths):
                break
            text_box(slide, x, y, col_widths[i], row_h - 0.12, cell,
                     size=body_size, font=F["body"], color=color,
                     line_spacing=1.05)
            x += col_widths[i] + gap
        y += row_h - 0.12
        rule(slide, l, y, total_w, color=rule_color, w_in=row_rule_w)
    return y


def brand_table(slide, l, t, w, h, headers, rows, col_widths=None, *,
                header_fill=None, header_color=None, label_fill=None,
                total_row=False, font_size=11, header_size=11,
                row_h=None, align_right_cols=()):
    """Native PPTX table styled like Enterprise 26/37 and Pricing_Document.

    Green header row, optional tinted label column, optional #D9EAD3 total row,
    first-row emphasis and banding disabled (we colour every cell ourselves).
    """
    header_fill = header_fill or BRAND["green_deep"]
    header_color = header_color or BRAND["white"]
    n_rows, n_cols = len(rows) + 1, len(headers)
    gf = slide.shapes.add_table(n_rows, n_cols, Inches(l), Inches(t),
                                Inches(w), Inches(h))
    table = gf.table
    table.first_row = False
    table.horz_banding = False
    if col_widths:
        for i, cw in enumerate(col_widths[:n_cols]):
            table.columns[i].width = Inches(cw)
    if row_h:
        for r in table.rows:
            r.height = Inches(row_h)

    def fill_cell(cell, text, *, fill, color, bold=False, size=font_size,
                  font=None, align="left"):
        cell.fill.solid()
        cell.fill.fore_color.rgb = _rgb(fill)
        cell.margin_left = cell.margin_right = Inches(0.11)
        cell.margin_top = cell.margin_bottom = Inches(0.06)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        tf = cell.text_frame
        tf.word_wrap = True
        paras = _iter_paragraphs(text)
        for i, pieces in enumerate(paras):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = _ALIGN.get(align, PP_ALIGN.LEFT)
            for txt, style in pieces:
                r = p.add_run()
                r.text = txt
                _set_font(r, font=style.get("font", font or F["body"]),
                          size=style.get("size", size),
                          color=style.get("color", color),
                          bold=style.get("bold", bold),
                          italic=style.get("italic", False))

    for j, head in enumerate(headers):
        fill_cell(table.cell(0, j), head, fill=header_fill, color=header_color,
                  bold=True, size=header_size,
                  align="right" if j in align_right_cols else "left")
    for i, row in enumerate(rows):
        is_total = total_row and i == len(rows) - 1
        for j in range(n_cols):
            value = row[j] if j < len(row) else ""
            if is_total:
                bg, fg, bold = BRAND["green_pale"], BRAND["green_deep"], True
            elif j == 0 and label_fill:
                bg, fg, bold = label_fill, BRAND["ink_alt"], True
            else:
                bg, fg, bold = BRAND["white"], BRAND["ink_alt"], False
            fill_cell(table.cell(i + 1, j), value, fill=bg, color=fg, bold=bold,
                      align="right" if j in align_right_cols else "left")
    return table


def stat_tiles(slide, l, t, w, stats, *, cols=3, dark=True, number_size=None,
               card_h=1.95, gap=0.28, number_color=None, label_color=None):
    """Systematic-deck stat band: N stat tiles on a fixed pitch.

    number_size defaults to a size the tile height can actually hold.
    """
    number_size = number_size or max(20.0, min(44.0, round(card_h * 22.0, 1)))
    number_color = number_color or (BRAND["green_pale"] if dark else BRAND["green_deep"])
    label_color = label_color or (BRAND["tan"] if dark else BRAND["muted"])
    tile_fill = BRAND["green_dark_text"] if dark else BRAND["card"]
    tile_line = BRAND["green"] if dark else BRAND["tan"]
    cols = max(1, cols)
    tile_w = (w - gap * (cols - 1)) / cols
    for i, s in enumerate(stats):
        r, c = divmod(i, cols)
        x = l + c * (tile_w + gap)
        y = t + r * (card_h + gap)
        rect(slide, x, y, tile_w, card_h, fill=tile_fill, line=tile_line,
             rounded=True, adjust=0.06)
        text_box(slide, x + 0.25, y + 0.22, tile_w - 0.5, card_h * 0.5,
                 s.get("value", ""), size=number_size, font=F["display"],
                 color=number_color, anchor="middle")
        text_box(slide, x + 0.25, y + card_h * 0.58, tile_w - 0.5,
                 card_h * 0.36, s.get("label", ""), size=13, font=F["body"],
                 color=label_color, line_spacing=1.1)


def icon_cards(slide, l, t, w, h, items, *, cols=2, dark=False, gap=0.3,
               title_size=16, body_size=12.5):
    """Systematic-deck card grid: rounded card + badge + title + body."""
    cols = max(1, cols)
    rows = (len(items) + cols - 1) // cols
    cw = (w - gap * (cols - 1)) / cols
    ch = (h - gap * (rows - 1)) / max(1, rows)
    fill = BRAND["green_dark_text"] if dark else BRAND["card"]
    border = BRAND["green"] if dark else BRAND["tan"]
    title_color = BRAND["white"] if dark else BRAND["ink"]
    body_color = BRAND["tan"] if dark else BRAND["muted"]
    for i, it in enumerate(items):
        r, c = divmod(i, cols)
        x, y = l + c * (cw + gap), t + r * (ch + gap)
        rect(slide, x, y, cw, ch, fill=fill, line=border, line_w_in=0.014,
             rounded=True, adjust=0.06)
        if it.get("cap"):
            rect(slide, x, y, cw, 0.1, fill=BRAND["green"], line=None)
        badge = it.get("badge") or it.get("icon_text")
        text_x = x + 0.3
        text_w = cw - 0.6
        # Stack the badge above the title only when the card is tall enough;
        # otherwise sit it inline so the body keeps its room.
        stacked = ch >= 2.4
        head_h = 0.4
        if badge:
            d = min(0.62, ch * 0.3)
            circ = rect(slide, x + 0.3, y + 0.28, d, d,
                        fill=BRAND["green_pale"] if not dark else BRAND["green"],
                        line=None, shape=MSO_SHAPE.OVAL)
            _label_in(circ, str(badge), size=min(14, d * 22), font=F["body"],
                      color=BRAND["green_deep"] if not dark else BRAND["white"],
                      align="center", pad_in=0.0)
            if stacked:
                head_y = y + 0.28 + d + 0.18
            else:
                head_y = y + 0.28
                head_h = d
                text_x = x + 0.3 + d + 0.18
                text_w = cw - 0.6 - d - 0.18
        else:
            head_y = y + 0.28
        text_box(slide, text_x, head_y, text_w, head_h, it.get("title", ""),
                 size=title_size, font=F["body"], bold=True, color=title_color,
                 anchor="middle" if (badge and not stacked) else "top")
        body_y = head_y + head_h + 0.1
        text_box(slide, x + 0.3, body_y, cw - 0.6,
                 max(0.3, (y + ch - 0.25) - body_y), it.get("body", ""),
                 size=body_size, font=F["body"], color=body_color,
                 line_spacing=1.15)


def pipeline_row(slide, l, t, w, h, steps, *, arrows=True, numbered=True,
                 dark=False):
    """Systematic-deck pipeline: N equal cards with arrow glyphs between."""
    n = max(1, len(steps))
    arrow_w = 0.32 if arrows and n > 1 else 0.0
    card_w = (w - arrow_w * (n - 1)) / n
    fill = BRAND["green_dark_text"] if dark else BRAND["card"]
    border = BRAND["green"] if dark else BRAND["tan"]
    title_color = BRAND["white"] if dark else BRAND["green_deep"]
    body_color = BRAND["tan"] if dark else BRAND["muted"]
    for i, s in enumerate(steps):
        x = l + i * (card_w + arrow_w)
        rect(slide, x, t, card_w, h, fill=fill, line=border, rounded=True,
             adjust=0.06)
        y = t + 0.2
        if numbered:
            text_box(slide, x + 0.18, y, card_w - 0.36, 0.3, str(i + 1),
                     size=12, font=F["body"], color=BRAND["green"], bold=True)
            y += 0.32
        text_box(slide, x + 0.18, y, card_w - 0.36, 0.5,
                 s.get("title", "") if isinstance(s, dict) else str(s),
                 size=14, font=F["body"], bold=True, color=title_color,
                 line_spacing=1.05)
        if isinstance(s, dict) and s.get("body"):
            text_box(slide, x + 0.18, y + 0.52, card_w - 0.36,
                     h - (y - t) - 0.7, s["body"], size=11, font=F["body"],
                     color=body_color, line_spacing=1.1)
        if arrows and i < n - 1:
            ax = x + card_w
            arrow = rect(slide, ax + 0.04, t + h / 2 - 0.11, arrow_w - 0.08, 0.22,
                         fill=BRAND["green"], line=None,
                         shape=MSO_SHAPE.RIGHT_ARROW)
            _shadow_off(arrow)


def rating_arrow(slide, l, t, w, h, rating):
    """UP_ARROW rotated 0 / 90 / 180 for favorable / neutral / unfavorable."""
    rot, fill = RATING.get(str(rating).lower(), RATING["neutral"])
    shp = rect(slide, l, t, w, h, fill=fill, line=fill,
               shape=MSO_SHAPE.UP_ARROW, rotation=rot)
    return shp


def _io_card(slide, l, t, w, h, eyebrow, items, *, accent=None, size=12.0,
             lead=7.0, head_h=0.62):
    """White card under a letterspaced all-caps eyebrow, with a coloured cap bar.

    The unit of ``step_detail_io``: cap bar, eyebrow, hairline, bulleted list.
    ``size`` / ``lead`` / ``head_h`` come from that composite's fit search.
    """
    accent = accent or C["accent_green"]
    rect(slide, l, t, w, h, fill=BRAND["white"], line=C["border"],
         line_w_in=0.011)
    rect(slide, l, t, w, 0.075, fill=accent, line=None)
    _track(text_box(slide, l + 0.22, t + 0.18, w - 0.44, 0.26,
                    str(eyebrow).upper(), size=10, bold=True, font=F["body"],
                    color=C["label_green"]), 1.2)
    rule(slide, l + 0.22, t + head_h - 0.1, w - 0.44, color=BRAND["hair"],
         w_in=0.008)
    text_box(slide, l + 0.22, t + head_h, w - 0.44, max(0.3, h - head_h - 0.14),
             list(items or []), size=size, font=F["body"], color=C["body"],
             bullets=True, space_before=lead, line_spacing=1.12)


# ===========================================================================
# composites -- one per Example_Slides pattern (spec section B)
# Each takes plain data and returns the Slide.
# ===========================================================================
def _fit(text, base, chars_per_line, lines=1):
    """Shrink a display size when the copy is longer than the box can hold."""
    n = len("".join(text) if isinstance(text, (list, tuple)) else (text or ""))
    cap = max(1, chars_per_line * lines)
    if n <= cap:
        return base
    return round(max(base * 0.55, base * (cap / float(n)) ** 0.6), 1)


# Mean advance of Wix Madefor Display as a fraction of point size, measured off
# a rendered slide (40 chars of 12pt body across 3.31in -> 0.496).  Rounded up
# to 0.52 so the estimate over-reserves: a card with a little slack looks
# considered, one that clips its last bullet looks broken.
CHAR_ADVANCE = 0.52


def _est_lines(text, width_in, size, indent_in=0.22):
    """Rendered line count for one bullet at this size and column width."""
    cpl = max(6, int((width_in - indent_in) * 72.0 / (size * CHAR_ADVANCE)))
    return max(1, -(-len(str(text)) // cpl))


def _list_height(items, width_in, size, *, line_spacing=1.12, space_before=7.0,
                 indent_in=0.22):
    """Estimated rendered height of a bulleted list, in inches.

    ``space_before`` lands on every paragraph including the first, which is what
    ``text_box`` does, so it is counted len(items) times, not len(items) - 1.
    """
    total = 0.0
    for item in items or []:
        lines = _est_lines(item, width_in, size, indent_in)
        total += (lines * size * line_spacing + space_before) / 72.0
    return total


def _chrome(slide, *, dark=False, page=None, logo_on=True, logo_corner=None,
            footer_left="AI Workspace for Insurance",
            footer_center="furtherai.com", footer_rule=None):
    if logo_on:
        if dark:
            logo(slide, dark_bg=True, l=11.95, t=0.344, width_in=0.969)
        else:
            l, t = (0.4, 0.3) if (logo_corner or "tl") == "tl" else (11.19, 0.247)
            logo(slide, dark_bg=False, l=l, t=t, width_in=1.4 if l < 1 else 1.477)
    footer(slide, dark=dark, page=page, left=footer_left, center=footer_center,
           rule_line=dark if footer_rule is None else footer_rule)
    return slide


# --- slide 1 -------------------------------------------------------------
def cover(prs, headline, *, subhead=None, eyebrow=None, background=None,
          gradient=None, client_logo=None, client_logo_label=None, page=1,
          footer_left="AI Workspace for Insurance", footer_center="furtherai.com"):
    """Full-bleed cover.  Image background if given, else the brand gradient."""
    slide = blank_slide(prs, image=background,
                        gradient=gradient or COVER_GRADIENT, gradient_angle=45.0)
    logo(slide, dark_bg=True, l=0.647, t=0.624, width_in=1.94)
    y = 2.904
    if eyebrow:
        text_box(slide, 0.647, 2.45, 7.148, 0.35, eyebrow.upper(), size=12,
                 font=F["body"], color=BRAND["tan"])
    size = _fit(headline, 49.3, 22, 2)
    text_box(slide, 0.647, y, 7.148, 1.692, headline, anchor="middle",
             font=F["display_light"], size=size, color=BRAND["white"],
             line_spacing=0.8)
    if subhead:
        text_box(slide, 0.647, 4.72, 7.148, 0.7, subhead, size=16,
                 font=F["body"], color=BRAND["hair_light"], line_spacing=1.2)
    if client_logo and Path(str(client_logo)).exists():
        picture(slide, client_logo, 0.647, 5.508, w=1.454)
    elif client_logo_label:
        text_box(slide, 6.376, 2.38, 2.751, 0.6, client_logo_label, size=27,
                 font=F["body"], color=BRAND["white"], align="center",
                 anchor="middle")
    footer(slide, dark=True, page=page, rule_line=True, left=footer_left,
           center=footer_center)
    return slide


# --- slide 2 -------------------------------------------------------------
def agenda(prs, title, items, *, page=None, panel_title=None):
    """Split panel: dark left third, big serif title, numbered bullets right."""
    slide = blank_slide(prs, bg=BRAND["paper"])
    rect(slide, 0, 0, 5.4, 7.5, fill=C["sidebar_dark"], line=None)
    text_box(slide, 0.55, 3.067, 4.7, 1.2, title, font=F["display"],
             size=_fit(title, 44, 14, 2), color=BRAND["white"], line_spacing=1.15,
             anchor="middle")
    if panel_title:
        text_box(slide, 0.55, 2.5, 4.7, 0.4, panel_title.upper(), size=12,
                 font=F["body"], color=C["eyebrow_green"])
    logo(slide, dark_bg=False, l=11.464, t=0.38, width_in=1.319)
    n = max(1, len(items))
    top, bottom = 2.113, 6.0
    pitch = min(1.0, (bottom - top) / n) if n > 1 else 1.0
    for i, item in enumerate(items):
        y = top + i * pitch
        circle_label(slide, 5.861, y, 0.49, i + 1, fill=C["bubble_green"], size=19)
        text = item.get("text") if isinstance(item, dict) else item
        note = item.get("note") if isinstance(item, dict) else None
        text_box(slide, 6.49, y - 0.02, 6.4, 0.6, text, size=17, font=F["body"],
                 color=BRAND["ink"], line_spacing=1.15)
        if note:
            text_box(slide, 6.49, y + 0.42, 6.4, 0.35, note, size=12,
                     font=F["body"], color=C["muted"])
    footer(slide, page=page)
    return slide


# --- slide 3 -------------------------------------------------------------
def before_after(prs, title, left, right, *, subtitle=None, arrow="→",
                 recommend="right", page=None):
    """Old-way / new-way contrast: two cards with a big arrow between them.

    The recommended side gets the #143524 header and the 0.021in green outline;
    the other gets a grey header and a #DDDDDD hairline.
    """
    slide = blank_slide(prs, bg=BRAND["paper"])
    title_block(slide, title, subtitle=subtitle, variant="analysis")
    top, h = 2.6, 3.764
    for side, spec, x in (("left", left, 0.4), ("right", right, 7.2)):
        win = (recommend == side)
        card(slide, x, top, 5.4, h,
             header=spec.get("title"), eyebrow=spec.get("eyebrow"),
             bullets=spec.get("bullets"), body=spec.get("body"),
             fill=BRAND["white"] if win else C["fill_neutral"],
             border=C["accent_green"] if win else C["border"],
             border_w=0.021 if win else 0.01,
             header_fill=C["hdr_green"] if win else C["neutral_header"],
             eyebrow_color=C["eyebrow_green"] if win else BRAND["white"])
    text_box(slide, 6.0, 4.2, 1.0, 1.0, arrow, size=56, font=F["body"],
             color=C["accent_green"], align="center", anchor="middle")
    logo(slide, dark_bg=False, l=0.4, t=0.3, width_in=1.4)
    footer(slide, page=page)
    return slide


# --- slide 4 -------------------------------------------------------------
def two_pillars(prs, title, pillars, *, subtitle=None, legend=None, page=None):
    """Two co-equal deep cards, each with nested sub-blocks and an OUTPUT bar."""
    slide = blank_slide(prs, bg=BRAND["paper"])
    title_block(slide, title, subtitle=subtitle, variant="analysis")
    xs = [0.4, 6.8]
    for i, p in enumerate(pillars[:2]):
        focus = bool(p.get("focus"))
        card(slide, xs[i], 2.6, 6.15, 4.4,
             eyebrow=p.get("eyebrow"), header=p.get("title"),
             summary=p.get("summary"), blocks=p.get("blocks"),
             output=p.get("output"), header_h=0.95, header_size=17,
             fill=C["fill_green"] if focus else BRAND["white"],
             border=C["accent_green"], border_w=0.017)
    if legend:
        swatch = rect(slide, 11.345, 1.966, 0.247, 0.225, fill=C["fill_green"],
                      line=C["accent_green"])
        text_box(slide, 11.691, 1.911, 1.4, 0.333, legend, size=12,
                 font=F["body"], color="#000000", anchor="middle")
    logo(slide, dark_bg=False, l=0.4, t=0.3, width_in=1.4)
    footer(slide, page=page)
    return slide


# --- slide 5 -------------------------------------------------------------
def numbered_steps(prs, title, steps, *, eyebrow=None, header=None,
                   options=None, status=None, note=None, page=None):
    """Numbered method on the left, options / commentary panel on the right."""
    slide = blank_slide(prs, bg=BRAND["paper"])
    title_block(slide, title, variant="analysis")
    rect(slide, 0.4, 2.55, 7.2, 4.65, fill=BRAND["white"], line=C["border"])
    rect(slide, 0.4, 2.038, 7.2, 0.55, fill=C["hdr_green"], line=None)
    if eyebrow:
        text_box(slide, 0.6, 2.074, 5.8, 0.22, eyebrow.upper(), size=11,
                 font=F["body"], color=C["eyebrow_green"])
    if header:
        text_box(slide, 0.6, 2.278, 6.8, 0.3, header, size=15, font=F["body"],
                 color=BRAND["white"])
    n = max(1, len(steps))
    pitch = min(0.66, (6.95 - 2.75) / n)
    steps_list(slide, 0.58, 2.75, 6.9, steps, bubble_d=0.32, pitch=pitch,
               dividers=True)
    y = 1.94
    for opt in (options or []):
        text_box(slide, 7.907, y, 5.1, 0.37, opt.get("title", ""), size=16,
                 font=F["body"], color=C["label_green"])
        text_box(slide, 7.907, y + 0.37, 5.027, 1.5, list(opt.get("bullets", [])),
                 size=14, font=F["body"], color="#000000", bullets=True,
                 space_before=6, line_spacing=1.05)
        y += 1.95
    if status:
        text_box(slide, 7.913, 6.165, 4.9, 0.337, status, size=14, font=F["body"],
                 color=C["label_green"])
    if note:
        text_box(slide, 7.78, 6.5, 5.287, 0.8, note, size=14, font=F["body"],
                 color=C["callout_red"], line_spacing=1.05)
    logo(slide, dark_bg=False, l=0.4, t=0.3, width_in=1.4)
    footer(slide, page=page)
    return slide


# --- slide 6 -------------------------------------------------------------
def scorecard(prs, title, dimensions, *, narrative=None, narrative_title=None,
              eyebrow=None, header=None, page=None):
    """Left narrative column + right card of dimensions over rating chips.

    dimensions = [{'label': str, 'levels': [str, str, str], 'selected': int}]
    """
    slide = blank_slide(prs, bg=BRAND["paper"])
    title_block(slide, title, variant="analysis")
    if narrative_title:
        text_box(slide, 0.4, 2.728, 4.033, 0.37, narrative_title, size=16,
                 bold=True, font=F["body"], color=C["label_green"])
        rule(slide, 0.505, 3.098, 4.547, color="#000000", w_in=0.021)
    if narrative:
        text_box(slide, 0.4, 3.193, 4.653, 3.669, list(narrative), size=16,
                 font=F["body"], color="#000000", bullets=True, space_before=12,
                 line_spacing=1.05)
    rect(slide, 5.531, 2.55, 7.522, 4.5, fill=BRAND["white"], line=C["border"])
    rect(slide, 5.531, 2.11, 7.522, 0.683, fill=C["hdr_green"], line=None)
    if eyebrow:
        text_box(slide, 5.731, 2.227, 7.0, 0.22, eyebrow.upper(), size=14,
                 font=F["body"], color=C["eyebrow_green"])
    if header:
        text_box(slide, 5.731, 2.483, 7.0, 0.3, header, size=14, font=F["body"],
                 color=BRAND["white"])
    n = max(1, len(dimensions))
    top = 2.887
    pitch = min(0.86, (6.9 - top) / n)
    cols = [5.731, 8.215, 10.67]
    for i, dim in enumerate(dimensions):
        y = top + i * pitch
        text_box(slide, 5.731, y, 5.8, 0.24, dim.get("label", ""), size=14,
                 font=F["body"], color=C["label_green"])
        selected = dim.get("selected", 1)
        for j, level in enumerate(dim.get("levels", [])[:3]):
            chip(slide, cols[j], y + 0.3, 2.28, 0.36, level,
                 fill=C["fill_neutral"], line=C["border"],
                 color=C["hdr_green"] if j == selected else C["muted"], size=14,
                 bold=(j == selected))
    logo(slide, dark_bg=False, l=0.4, t=0.3, width_in=1.4)
    footer(slide, page=page)
    return slide


# --- slide 7 -------------------------------------------------------------
def process_audit(prs, title, steps, issues, *, meta=None,
                  left_header="CURRENT PROCESS — DETAILED",
                  right_header="MANUAL ACTIVITIES IDENTIFIED", page=None):
    """As-is process on the left, pain points with severity chips on the right.

    issues = [{'code','title','detail','severity': critical|high|med}]
    """
    slide = blank_slide(prs, bg=BRAND["paper"])
    title_block(slide, title, variant="compact")
    if meta:
        text_box(slide, 0.4, 1.355, 12.5, 0.35, meta, size=11, italic=True,
                 font=F["body"], color=C["muted"])
    rect(slide, 0.4, 1.776, 7.5, 5.271, fill=BRAND["white"], line=C["border"])
    rect(slide, 0.4, 1.787, 7.5, 0.4, fill=C["hdr_green"], line=None)
    text_box(slide, 0.55, 1.787, 7.2, 0.4, left_header, size=12, font=F["body"],
             color=BRAND["white"], anchor="middle")
    n = max(1, len(steps))
    pitch = min(0.68, (6.95 - 2.33) / n)
    norm = []
    for s in steps:
        if isinstance(s, dict):
            norm.append((s.get("title", ""), s.get("caption")))
        elif isinstance(s, (list, tuple)):
            norm.append((s[0], s[1] if len(s) > 1 else None))
        else:
            norm.append((s, None))
    for i, (t_, c_) in enumerate(norm):
        y = 2.33 + i * pitch
        if i % 2 == 0:
            rect(slide, 0.45, y - 0.1, 7.4, pitch - 0.06,
                 fill=C["fill_neutral"], line=None)
        circle_label(slide, 0.55, y, 0.3, i + 1, fill=C["accent_green"], size=10)
        runs = [[(t_, {"size": 11, "color": C["body"]})]]
        if c_:
            runs[0].append((" — " + c_, {"size": 10, "color": C["muted"]}))
        text_box(slide, 0.95, y - 0.1, 6.85, max(0.3, pitch - 0.05), runs,
                 font=F["body"], anchor="middle", line_spacing=1.05)

    rect(slide, 8.05, 1.776, 4.9, 5.271, fill=BRAND["white"], line=C["border"])
    rect(slide, 8.05, 1.793, 4.9, 0.4, fill=C["hdr_green"], line=None)
    text_box(slide, 8.2, 1.793, 4.6, 0.4, right_header, size=12, font=F["body"],
             color=BRAND["white"], anchor="middle")
    m = max(1, len(issues))
    ipitch = min(0.94, (6.9 - 2.35) / m)
    for i, iss in enumerate(issues):
        y = 2.35 + i * ipitch
        if i % 2 == 0:
            rect(slide, 8.1, y - 0.12, 4.8, ipitch - 0.06,
                 fill=C["fill_neutral"], line=None)
        circle_label(slide, 8.17, y, 0.4, iss.get("code", str(i + 1)),
                     fill=C["sev_critical"], size=10)
        text_box(slide, 8.65, y - 0.04, 3.4, 0.25, iss.get("title", ""), size=11,
                 font=F["body"], color=C["body"])
        if iss.get("detail"):
            text_box(slide, 8.65, y + 0.22, 3.4, ipitch - 0.3, iss["detail"],
                     size=10, font=F["body"], color=C["muted"], line_spacing=1.05)
        sev = str(iss.get("severity", "med")).lower()
        fill_, fg = SEVERITY.get(sev, SEVERITY["med"])
        chip(slide, 12.2, y - 0.02, 0.65, 0.22, sev.title(), fill=fill_, color=fg,
             size=10)
    logo(slide, dark_bg=False, l=0.4, t=0.3, width_in=1.4)
    footer(slide, page=page)
    return slide


# --- slides 8 / 9 --------------------------------------------------------
def rag_matrix(prs, title, rows, *, cols=None, cells=None, subtitle=None,
               mode="arrows", legend=True, row_header="Metrics",
               summary_header="Preliminary assessment", page=None):
    """Criteria x options grid.

    mode='arrows'  rows=criteria, cols=options,
                   cells=[[{'rating': up|side|down, 'text': str}, ...], ...]
    mode='summary' rows=option labels, cells=['verdict sentence', ...]
    """
    slide = blank_slide(prs, bg=BRAND["paper"])
    title_block(slide, title, subtitle=subtitle, variant="compact")
    n = max(1, len(rows))
    top = 2.834
    pitch = min(0.95, (6.9 - top) / n)

    if mode == "summary":
        text_box(slide, 0.665, 2.264, 1.6, 0.3, row_header, size=16, bold=True,
                 font=F["body"], color=C["matrix_ink"])
        text_box(slide, 2.539, 2.264, 4.0, 0.3, summary_header, size=16,
                 bold=True, font=F["body"], color=C["matrix_ink"])
        rule(slide, 0.704, 2.651, 11.744, color="#000000", w_in=0.021)
        for i, label in enumerate(rows):
            y = top + i * pitch
            text_box(slide, 0.665, y, 1.95, 0.6, label, size=16, font=F["body"],
                     color=C["matrix_ink"], line_spacing=1.05)
            verdict = (cells or [])[i] if i < len(cells or []) else ""
            text_box(slide, 2.82, y, 10.237, 0.7, verdict, size=16,
                     font=F["body"], color="#000000", line_spacing=1.05)
            if i < n - 1:
                rule(slide, 0.62, y + pitch - 0.13, 11.827, color=C["rule_grey"])
        logo(slide, dark_bg=False, l=0.4, t=0.3, width_in=1.4)
        footer(slide, page=page)
        return slide

    cols = cols or []
    ncol = max(1, len(cols))
    grid_l, grid_r = 2.039, 13.0
    col_w = (grid_r - grid_l) / ncol
    if legend:
        for k, (label, rating) in enumerate((("Favorable", "up"),
                                             ("Neutral", "side"),
                                             ("Unfavorable", "down"))):
            x = 8.921 + k * 1.45
            rating_arrow(slide, x, 0.3, 0.289, 0.284, rating)
            text_box(slide, x + 0.34, 0.29, 1.1, 0.3, label, size=12,
                     font=F["body"], color=C["matrix_ink"], anchor="middle")
    text_box(slide, 0.665, 2.264, 1.3, 0.3, row_header, size=14, font=F["body"],
             color=C["matrix_ink"])
    for j, col in enumerate(cols):
        text_box(slide, grid_l + j * col_w, 2.2, col_w - 0.15, 0.4, col, size=14,
                 font=F["body"], color=C["matrix_ink"], line_spacing=1.0)
    rule(slide, 0.704, 2.651, 11.744, color="#000000", w_in=0.021)
    for i, label in enumerate(rows):
        y = top + i * pitch
        text_box(slide, 0.665, y, 1.36, 0.6, label, size=14, bold=True,
                 font=F["body"], color=C["label_green"], line_spacing=1.05)
        row_cells = (cells or [])[i] if i < len(cells or []) else []
        for j in range(ncol):
            cell = row_cells[j] if j < len(row_cells) else {}
            if isinstance(cell, str):
                cell = {"text": cell}
            x = grid_l + j * col_w
            rating_arrow(slide, x, y, 0.289, 0.284, cell.get("rating", "side"))
            if cell.get("text"):
                text_box(slide, x + 0.46, y - 0.05, col_w - 0.6,
                         max(0.4, pitch - 0.15), cell["text"], size=12,
                         font=F["body"], color="#000000", line_spacing=1.02)
        if i < n - 1:
            rule(slide, 0.62, y + pitch - 0.13, 11.827, color=C["rule_grey"])
    logo(slide, dark_bg=False, l=0.4, t=0.3, width_in=1.4)
    footer(slide, page=page)
    return slide


# --- slide 10 ------------------------------------------------------------
def timeline(prs, title, periods, lanes, *, today=None,
             output_header="Key outputs", legend=True, page=None):
    """Month-by-month Gantt with milestone diamonds and a key-outputs column.

    Deliberately has NO next-steps box: next steps always get their own slide,
    immediately after the timeline.  Use the ``next_steps`` pattern for them.

    lanes = [{'label', 'start': int, 'span': int, 'output': str,
              'milestones': [float period offsets]}]
    today = {'label': 'Today (5/14)', 'at': float period offset}
    """
    slide = blank_slide(prs, bg=BRAND["paper"])
    text_box(slide, 0.708, 0.792, 11.414, 0.791, title, font=F["display"],
             size=_fit(title, 26, 55), color=BRAND["ink_navy"], anchor="middle")
    logo(slide, dark_bg=False, l=0.69, t=0.227, width_in=1.4)

    label_l, label_w = 0.758, 1.95
    chart_l, chart_r = 2.85, 10.45
    out_l, out_w = 10.63, 2.455
    ncol = max(1, len(periods))
    col_w = (chart_r - chart_l) / ncol
    head_y = 1.548

    text_box(slide, label_l, head_y, label_w, 0.3, "Timeline", size=16, bold=True,
             font=F["body"], color="#000000")
    for j, p in enumerate(periods):
        text_box(slide, chart_l + j * col_w, head_y, col_w, 0.3, str(p), size=13,
                 bold=True, font=F["body"], color="#000000", align="center")
    text_box(slide, out_l, head_y, out_w, 0.3, output_header, size=16, bold=True,
             font=F["body"], color="#000000")
    rule(slide, 0.8, 1.934, 12.282, color="#000000", w_in=0.021)
    rect(slide, chart_l, 1.985, chart_r - chart_l, 0.02, fill=BRAND["hair"],
         line=None)

    n = max(1, len(lanes))
    # leave room under the chart for the Today marker, and for the footer
    top, bottom = 2.05, (6.45 if today else 6.9)
    pitch = min(0.99, (bottom - top) / n)
    diamonds = []
    for i, lane in enumerate(lanes):
        y = top + i * pitch
        text_box(slide, label_l, y, label_w, pitch - 0.12,
                 lane.get("label", ""), size=13, font=F["body"], color="#000000",
                 line_spacing=1.05)
        start = float(lane.get("start", 0))
        span = float(lane.get("span", 1))
        bx = chart_l + start * col_w
        bw = max(0.2, span * col_w)
        rect(slide, bx, y + 0.12, bw, 0.317, fill=C["fill_green"],
             line=BRAND["white"], line_w_in=0.014)
        for m in lane.get("milestones", []):
            mx = chart_l + float(m) * col_w - 0.188
            diamonds.append((mx, y + 0.09))
            rect(slide, mx, y + 0.09, 0.376, 0.376, fill=C["milestone"],
                 line=BRAND["white"], line_w_in=0.014, shape=MSO_SHAPE.DIAMOND)
        if lane.get("output"):
            text_box(slide, out_l, y, out_w, pitch - 0.12, lane["output"],
                     size=12, font=F["body"], color="#000000", line_spacing=1.05)
        rule(slide, 0.8, y + pitch - 0.11, 12.282, color="#000000", w_in=0.01)

    if today:
        tx = chart_l + float(today.get("at", 0)) * col_w
        rule(slide, tx, 1.943, min(bottom, top + n * pitch) - 1.943,
             color=BRAND["connector"], w_in=0.01, vertical=True)
        rect(slide, tx - 0.28, bottom + 0.02, 0.567, 0.269,
             fill=BRAND["hair_light"], line=BRAND["connector"],
             shape=MSO_SHAPE.ISOSCELES_TRIANGLE)
        text_box(slide, tx - 0.76, bottom + 0.3, 1.512, 0.3,
                 today.get("label", "Today"), size=12, font=F["body"],
                 color="#000000", align="center")
    if legend:
        rect(slide, 8.212, 0.426, 0.283, 0.286, fill=C["fill_green"],
             line=C["accent_green"])
        text_box(slide, 8.533, 0.4, 1.5, 0.33, "Current focus", size=12,
                 font=F["body"], color="#000000", anchor="middle")
        rect(slide, 10.172, 0.404, 0.283, 0.283, fill=C["milestone"], line=None,
             shape=MSO_SHAPE.DIAMOND)
        text_box(slide, 10.497, 0.4, 1.6, 0.33, "Key milestones", size=12,
                 font=F["body"], color="#000000", anchor="middle")
    return slide


# --- next steps (always its own slide, straight after a timeline) ---------
def next_steps(prs, items, *, title="Next steps", subtitle=None, page=None):
    """One action per row: optional owner chip + action text in a light card.

    ``items`` entries are a bare string, or an ``[owner, action]`` pair -- the
    owner becomes a green chip ("FurtherAI" / "Client" / a person's name).
    """
    slide = blank_slide(prs, bg=BRAND["paper"])
    title_block(slide, title, subtitle=subtitle, variant="analysis")

    rows = []
    for item in items:
        if isinstance(item, dict):
            rows.append((item.get("owner"), item.get("text", "")))
        elif isinstance(item, (list, tuple)):
            rows.append((item[0], item[1] if len(item) > 1 else ""))
        else:
            rows.append((None, str(item)))

    owners = [o for o, _ in rows if o]
    chip_w = 0.0
    if owners:
        chip_w = max(1.1, min(2.3, 0.105 * max(len(o) for o in owners) + 0.42))

    n = max(1, len(rows))
    top, bottom = 2.6 if subtitle else 2.3, 6.95
    pitch = min(0.95, (bottom - top) / n)
    card_h = pitch - 0.14
    for i, (owner, text) in enumerate(rows):
        y = top + i * pitch
        rect(slide, 0.4, y, 12.5, card_h, fill=C["fill_neutral"],
             line=C["border"], rounded=True, adjust=0.12)
        text_l = 0.75
        if owners:
            text_l = 0.65 + chip_w + 0.3
            if owner:
                chip(slide, 0.65, y + (card_h - 0.34) / 2.0, chip_w, 0.34,
                     owner, fill=C["accent_green"], color=BRAND["white"],
                     size=11, bold=True)
        text_box(slide, text_l, y, 12.85 - text_l, card_h, text, size=14,
                 font=F["body"], color=C["body"], anchor="middle",
                 line_spacing=1.05)
    logo(slide, dark_bg=False, l=0.4, t=0.3, width_in=1.4)
    footer(slide, page=page)
    return slide


# --- slide 11 ------------------------------------------------------------
def phase_chevrons(prs, title, phases, rows, cells, *, recommendation=None,
                   page=None):
    """Phase chevrons over a maturity matrix, plus a recommendation box.

    cells[row][col] = {'title': str, 'body': str} or a plain string.
    """
    slide = blank_slide(prs, bg=BRAND["paper"])
    title_block(slide, title, variant="statement")
    logo(slide, dark_bg=False, l=11.19, t=0.247, width_in=1.477)
    grid_l, grid_r = 2.187, 13.16
    n = max(1, len(phases))
    gap = 0.05
    ph_w = (grid_r - grid_l - gap * (n - 1)) / n
    for j, ph in enumerate(phases):
        x = grid_l + j * (ph_w + gap)
        shape = MSO_SHAPE.PENTAGON if j == 0 else MSO_SHAPE.CHEVRON
        shp = rect(slide, x, 1.899, ph_w, 0.56, fill=C["bubble_green"],
                   line=None, shape=shape)
        _label_in(shp, ph, size=18.7, font=F["body"], color=BRAND["white"],
                  align="center", pad_in=0.05)
    nrow = max(1, len(rows))
    top, bottom = 2.595, (5.7 if recommendation else 7.0)
    pitch = (bottom - top) / nrow
    for i, label in enumerate(rows):
        y = top + i * pitch
        text_box(slide, 0.596, y, 1.55, 0.7, label, size=14.7, bold=True,
                 font=F["body"], color=BRAND["ink"], line_spacing=1.05)
        row = cells[i] if i < len(cells) else []
        for j in range(n):
            cell = row[j] if j < len(row) else ""
            if isinstance(cell, str):
                cell = {"body": cell}
            x = grid_l + j * (ph_w + gap) + 0.08
            runs = []
            if cell.get("title"):
                runs.append((cell["title"], {"bold": True}))
            if cell.get("body"):
                runs.append(cell["body"])
            text_box(slide, x, y, ph_w - 0.2, pitch - 0.2, runs, size=14.7,
                     font=F["body"], color=BRAND["ink"], line_spacing=1.05,
                     space_after=3)
        if i < nrow - 1:
            rule(slide, 0.74, y + pitch - 0.18, 12.294, color="#888888")
    if recommendation:
        box = rect(slide, 0.62, 5.804, 12.424, 1.2, fill=BRAND["paper_warm"],
                   line="#888888")
        _label_in(box, recommendation, size=13.3, font=F["body"], color="#000000",
                  align="left", anchor="middle", pad_in=0.22, line_spacing=1.15)
    return slide


# --- slide 12 ------------------------------------------------------------
def pricing_table(prs, title, headers, rows, *, col_widths=None, note=None,
                  section_title=None, options=None, page=None):
    """Line-drawn commercial table + optional brace note + option cards."""
    slide = blank_slide(prs, bg=BRAND["paper"])
    text_box(slide, 0.6, 0.552, 11.0, 0.561, title, font=F["display"],
             size=_fit(title, 33.3, 42), color=BRAND["ink"], anchor="middle")
    logo(slide, dark_bg=False, l=11.19, t=0.247, width_in=1.477)
    col_widths = col_widths or [2.3, 6.0, 2.5]
    n = max(1, len(rows))
    row_h = min(0.62, (5.2 - 1.585) / n)
    last_y = line_table(slide, 0.651, 1.585, headers, rows, col_widths,
                        row_h=row_h)
    if note:
        rect(slide, 11.482, 3.93, 0.119, 1.193, fill=None, line="#000000",
             line_w_in=0.008, shape=MSO_SHAPE.RIGHT_BRACE)
        text_box(slide, 11.75, 4.091, 1.4, 0.9, note, size=12, font=F["body"],
                 color=BRAND["ink_navy"], line_spacing=1.05)
    if section_title:
        text_box(slide, 0.624, 5.379, 5.0, 0.3, section_title, size=16, bold=True,
                 font=F["body"], color="#0A3318")
        rule(slide, 0.6, 5.715, 12.363, color="#000000", w_in=0.016)
    for i, opt in enumerate(options or []):
        x = 0.57 + i * 4.17
        rect(slide, x, 5.803, 4.05, 1.113, fill=BRAND["white"], line="#14452A",
             line_w_in=0.008)
        text_box(slide, x + 0.18, 5.88, 3.7, 0.32,
                 opt.get("title", "") if isinstance(opt, dict) else str(opt),
                 size=14.7, bold=True, font=F["body"], color="#1E6B3A")
        if isinstance(opt, dict) and opt.get("body"):
            text_box(slide, x + 0.18, 6.22, 3.7, 0.64, opt["body"], size=12,
                     font=F["body"], color="#000000", line_spacing=1.05)
    return slide


# --- slide 13 ------------------------------------------------------------
def architecture_hero(prs, headline, *, left_label=None, right_label=None,
                      props=None, certs=None, background=None, page=None):
    """Full-bleed dark positioning slide with a centred node diagram."""
    slide = blank_slide(prs, image=background,
                        gradient=[(0.0, BRAND["black_deep"]),
                                  (0.55, "#0F2A24"), (1.0, BRAND["green_deep"])],
                        gradient_angle=60.0)
    text_box(slide, 1.238, 0.802, 10.858, 0.9, headline, align="center",
             font=F["display_light"], size=_fit(headline, 40.7, 32),
             color=BRAND["white"], anchor="middle")
    picture(slide, logo_mark_png(dark_bg=True), 5.974, 2.395, w=1.386)
    if left_label:
        text_box(slide, 1.412, 2.607, 2.6, 0.97, left_label, align="center",
                 font=F["display_light"], size=21.3, color=BRAND["white"],
                 anchor="middle", line_spacing=1.0)
        rule(slide, 4.1, 3.09, 1.8, color=BRAND["connector"], w_in=0.01)
    if right_label:
        text_box(slide, 9.3, 2.553, 2.7, 0.97, right_label, align="center",
                 font=F["display_light"], size=21.3, color=BRAND["white"],
                 anchor="middle", line_spacing=1.0)
        rule(slide, 7.45, 3.09, 1.8, color=BRAND["connector"], w_in=0.01)
    props = props or []
    if props:
        n = len(props)
        w = 12.0 / n
        for i, p in enumerate(props):
            x = 0.667 + i * w
            rule(slide, x + 0.15, 4.35, w - 0.3, color=BRAND["connector"],
                 w_in=0.005)
            text_box(slide, x + 0.15, 4.5, w - 0.3, 1.1, p, size=13.3,
                     font=F["body"], color=BRAND["white"], align="center",
                     line_spacing=1.15)
    certs = certs or []
    if certs:
        n = len(certs)
        cw, gap = 1.45, 0.3
        total = n * cw + (n - 1) * gap
        x0 = (CANVAS[0] - total) / 2.0
        for i, cert in enumerate(certs):
            x = x0 + i * (cw + gap)
            pill = rect(slide, x, 6.05, cw, 0.44, fill=None, line=BRAND["tan"],
                        line_w_in=0.01, rounded=True, adjust=0.5)
            _label_in(pill, cert, size=11.3, font=F["mono"], bold=True,
                      color=BRAND["tan"], align="center", pad_in=0.04)
    _chrome(slide, dark=True, page=page)
    return slide


# --- slide 14 ------------------------------------------------------------
def use_case_rail(prs, category, items, *, screenshot=None, dark=False,
                  page=None):
    """Left rail of named capabilities, right side a product screenshot."""
    slide = blank_slide(prs, bg=BRAND["black_deep"] if dark else BRAND["paper"])
    if screenshot and Path(str(screenshot)).exists():
        picture(slide, screenshot, 6.43, 0.6, w=6.9)
    else:
        placeholder_panel(slide, 6.43, 0.55, 6.6, 6.1, "Product screenshot")
    panel_fill = BRAND["black_deep"] if dark else BRAND["paper"]
    rect(slide, -0.09, 0, 6.31, 7.66, fill=panel_fill, line=None)
    title_color = BRAND["white"] if dark else "#000000"
    body_color = BRAND["tan"] if dark else BRAND["connector"]
    text_box(slide, 0.556, 0.62, 5.667, 0.9, category, font=F["display_light"],
             size=_fit(category, 37.7, 18), color=title_color, line_spacing=0.98)
    n = max(1, len(items))
    top, bottom = 1.87, 6.7
    pitch = min(1.25, (bottom - top) / n)
    rule(slide, 0.526, 2.038, min(3.672, (n - 1) * pitch + 0.4),
         color=BRAND["hair_mid"], w_in=0.01, vertical=True)
    for i, it in enumerate(items):
        y = top + i * pitch
        rect(slide, 0.43, y + 0.17, 0.197, 0.196, fill=BRAND["green"], line=None,
             shape=MSO_SHAPE.OVAL)
        title = it.get("title") if isinstance(it, dict) else str(it)
        body = it.get("body") if isinstance(it, dict) else None
        text_box(slide, 0.82, y, 4.94, 0.45, title, font=F["display"],
                 size=20.7, color=title_color)
        if body:
            text_box(slide, 0.82, y + 0.42, 4.94, pitch - 0.5, body, size=12.7,
                     font=F["body"], color=body_color, line_spacing=1.15)
    logo(slide, dark_bg=bool(dark), l=(11.95 if dark else 0.4), t=(0.344 if dark else 0.3), width_in=(0.969 if dark else 1.4))
    footer(slide, dark=dark, page=page, center=None)
    return slide


# --- slide 15 ------------------------------------------------------------
def agent_diagram(prs, title, hub, nodes, *, subtitle=None, page=None):
    """Orchestrator hub with elbow connectors down to N workflow nodes."""
    slide = blank_slide(prs, bg=BRAND["paper"])
    banner = rect(slide, 0, 0, CANVAS[0], 2.284, fill=None, line=None)
    _gradient(banner, COVER_GRADIENT, 45.0)
    text_box(slide, 0.572, 0.938, 7.5, 0.853, title, font=F["display_light"],
             size=_fit(title, 34.7, 30), color=BRAND["white"], anchor="middle")
    if subtitle:
        text_box(slide, 0.572, 1.72, 7.5, 0.4, subtitle, size=14, font=F["body"],
                 color=BRAND["tan"])
    hub_l, hub_t, hub_w, hub_h = 5.103, 3.228, 3.032, 0.89
    rect(slide, hub_l, hub_t, hub_w, hub_h, fill=BRAND["white"],
         line=BRAND["green"], line_w_in=0.009, rounded=True, adjust=0.1)
    if hub and str(hub).strip():
        text_box(slide, hub_l + 0.15, hub_t, hub_w - 0.3, hub_h, hub, size=15.8,
                 font=F["display_light"], color=BRAND["green_deep"],
                 align="center", anchor="middle")
    else:
        picture(slide, logo_png(dark_bg=False)[0], hub_l + 0.42, hub_t + 0.2, w=2.19)
    n = max(1, len(nodes))
    node_w, node_h, node_t = 2.189, 0.797, 5.17
    span = CANVAS[0] - 2.0
    pitch = span / n
    xs = [1.0 + i * pitch + (pitch - node_w) / 2.0 for i in range(n)]
    bus_y = 4.6
    rule(slide, hub_l + hub_w / 2.0, hub_t + hub_h, bus_y - (hub_t + hub_h),
         color=BRAND["connector"], w_in=0.006, vertical=True)
    if n > 1:
        rule(slide, xs[0] + node_w / 2.0, bus_y,
             (xs[-1] + node_w / 2.0) - (xs[0] + node_w / 2.0),
             color=BRAND["connector"], w_in=0.006)
    for i, node in enumerate(nodes):
        x = xs[i]
        rule(slide, x + node_w / 2.0, bus_y, node_t - bus_y,
             color=BRAND["connector"], w_in=0.006, vertical=True)
        rect(slide, x, node_t, node_w, node_h, fill=BRAND["white"],
             line=BRAND["green"], line_w_in=0.009, rounded=True, adjust=0.12)
        if isinstance(node, dict):
            paras = [(node.get("title", ""), {"color": BRAND["green_dark_text"]}),
                     (node.get("body", ""), {"color": BRAND["green_deep"]})]
        else:
            paras = [(str(node), {"color": BRAND["green_dark_text"]})]
        text_box(slide, x + 0.1, node_t, node_w - 0.2, node_h, paras, size=15.8,
                 font=F["display_light"], align="center", anchor="middle",
                 line_spacing=1.0)
    logo(slide, dark_bg=True, l=0.4, t=0.3, width_in=1.4)
    footer(slide, page=page, center=None)
    return slide


# --- slide 16 ------------------------------------------------------------
def security_trust(prs, headline, certs, cards, *, page=None):
    """Left cert rail on dark, right stack of cream cards."""
    slide = blank_slide(prs, bg=BRAND["white"])
    panel = rect(slide, 0, -0.02, 4.1, 7.54, fill=None, line=None)
    _gradient(panel, [(0.0, BRAND["green_deep"]), (1.0, BRAND["black_deep"])], 90.0)
    n = max(1, len(certs))
    top, bottom = 0.66, 7.1
    pitch = (bottom - top) / n
    for i, cert in enumerate(certs):
        y = top + i * pitch
        d = min(1.0, pitch * 0.45)
        cx = 2.05 - d / 2.0
        ring = rect(slide, cx, y + 0.1, d, d, fill=None, line=BRAND["tan"],
                    line_w_in=0.012, shape=MSO_SHAPE.OVAL)
        _label_in(ring, "", size=1)
        text_box(slide, 0.6, y + 0.18 + d, 2.9, 0.35, cert, size=11.3,
                 font=F["mono"], bold=True, color=BRAND["tan"], align="center")
    text_box(slide, 4.664, 0.491, 7.789, 1.256, headline, font=F["display"],
             size=_fit(headline, 29.3, 34, 2), color=BRAND["ink_alt"],
             anchor="middle", line_spacing=1.05)
    m = max(1, len(cards))
    ctop, cbottom = 2.093, 7.0
    cpitch = min(1.208, (cbottom - ctop) / m)
    for i, c in enumerate(cards):
        y = ctop + i * cpitch
        h = cpitch - 0.13
        rect(slide, 4.803, y, 7.957, h, fill=BRAND["card"], line=BRAND["tan"],
             rounded=True, adjust=0.08)
        title = c.get("title") if isinstance(c, dict) else str(c)
        body = c.get("body") if isinstance(c, dict) else None
        text_box(slide, 4.955, y + 0.06, 7.5, 0.45, title, font=F["display"],
                 size=_fit(title, 22, 34), color=BRAND["warm_dark"])
        if body:
            text_box(slide, 4.955, y + 0.53, 7.65, h - 0.6, body, size=12.7,
                     font=F["body"], color=BRAND["warm_dark"], line_spacing=1.1)
    logo(slide, dark_bg=True, l=0.4, t=0.3, width_in=1.4)
    footer(slide, page=page, center=None)
    return slide


# --- slide 17 ------------------------------------------------------------
def why_us(prs, headline, body, callouts, *, screenshot=None, page=None):
    """Dark differentiation slide: big paragraph left, satellite callouts right."""
    slide = blank_slide(prs, bg=BRAND["black_deep"])
    if screenshot and Path(str(screenshot)).exists():
        picture(slide, screenshot, 5.833, 0.583, w=7.236)
    else:
        placeholder_panel(slide, 5.833, 0.583, 7.236, 6.3, "Product screenshot")
    panel = rect(slide, -0.2, 0, 5.9, 7.5, fill=None, line=None)
    _gradient(panel, [(0.0, BRAND["black_deep"]), (1.0, "#0F2A24")], 30.0)
    text_box(slide, 0.708, 0.694, 4.9, 0.9, headline, font=F["display"],
             size=_fit(headline, 31, 22), color=BRAND["white"], line_spacing=0.98,
             anchor="middle")
    rule(slide, 0.778, 2.478, 3.245, color=BRAND["connector"], w_in=0.01,
         vertical=True)
    text_box(slide, 1.109, 2.414, 4.0, 3.333, body, size=15, font=F["body"],
             color=BRAND["hair_light"], line_spacing=1.275)
    spots = [(6.528, 1.438, 2.9), (10.3, 1.438, 2.6), (7.833, 5.216, 3.222)]
    for i, c in enumerate(callouts[:3]):
        x, y, w = spots[i]
        title = c.get("title") if isinstance(c, dict) else str(c)
        cbody = c.get("body") if isinstance(c, dict) else None
        lines = 1 + (len(cbody) // max(1, int(w * 15))) if cbody else 0
        plate_h = 0.5 + lines * 0.24 if cbody else 0.5
        rect(slide, x - 0.14, y - 0.12, w + 0.28, plate_h,
             fill=BRAND["black_deep"], line=BRAND["connector"], line_w_in=0.005)
        text_box(slide, x, y, w, 0.3, title, size=12, bold=True, font=F["body"],
                 color=BRAND["white"], line_spacing=1.14)
        if cbody:
            text_box(slide, x, y + 0.3, w, plate_h - 0.42, cbody, size=12,
                     font=F["body"], color=BRAND["hair_light"], line_spacing=1.14)
    logo(slide, dark_bg=True, l=11.95, t=0.344, width_in=0.969)
    footer(slide, dark=True, page=page)
    return slide


# --- slide 18 ------------------------------------------------------------
def closing(prs, *, message=None, background=None, page=None,
            footer_left="AI Workspace for Insurance",
            footer_center="furtherai.com"):
    """Centred white logo on a full-bleed dark background."""
    slide = blank_slide(prs, image=background, gradient=COVER_GRADIENT,
                        gradient_angle=225.0)
    if message:
        picture(slide, logo_png(dark_bg=True)[0], 2.173, 3.149, w=3.194)
        rule(slide, 6.315, 2.989, 1.231, color=BRAND["connector"], w_in=0.01,
             vertical=True)
        text_box(slide, 6.7, 2.989, 5.6, 1.4, message, size=20,
                 font=F["display_light"], color=BRAND["white"], anchor="middle",
                 line_spacing=1.1)
    else:
        path, aspect = logo_png(dark_bg=True)
        w = 3.194
        picture(slide, path, (CANVAS[0] - w) / 2.0, 3.149, w=w)
    rule(slide, 0.647, 6.834, 12.101, color=BRAND["rule_dark"], w_in=0.005)
    text_box(slide, 0.585, 6.897, 4.0, 0.3, footer_left, size=10, font=F["body"],
             color=BRAND["tan"])
    text_box(slide, 10.5, 6.897, 2.3, 0.3, footer_center, size=10, font=F["body"],
             color=BRAND["tan"], align="right")
    return slide


# --- stat hero (systematic deck, spec C.2) -------------------------------
def stat_hero(prs, title, stats, *, eyebrow=None, subtitle=None, disclaimer=None,
              cols=3, dark=True, page=None):
    """Grid of headline numbers.  stats = [{'value','label'}]."""
    slide = blank_slide(prs, bg=BRAND["green_deep"] if dark else BRAND["paper"])
    title_block(slide, title, eyebrow=eyebrow, subtitle=subtitle,
                variant="systematic",
                color=BRAND["white"] if dark else BRAND["ink"],
                eyebrow_color=BRAND["green_pale"] if dark else None,
                subtitle_color=BRAND["tan"] if dark else None)
    n = max(1, len(stats))
    rows = (n + cols - 1) // cols
    card_h = min(1.95, (6.6 - 2.4) / rows - 0.28)
    stat_tiles(slide, 0.55, 2.45, 12.2, stats, cols=cols, dark=dark,
               card_h=card_h, gap=0.28)
    if disclaimer:
        text_box(slide, 0.55, 6.6, 12.2, 0.35, disclaimer, size=10,
                 font=F["body"], italic=True,
                 color=BRAND["tan"] if dark else BRAND["muted"])
    logo(slide, dark_bg=bool(dark), l=(11.95 if dark else 0.4), t=(0.344 if dark else 0.3), width_in=(0.969 if dark else 1.4))
    footer(slide, dark=dark, page=page)
    return slide


# --- card grid (systematic deck, spec C.2) -------------------------------
def card_grid(prs, title, cards, *, eyebrow=None, subtitle=None, cols=2,
              dark=False, page=None):
    """Icon + title + body cards on a computed grid.

    cards = [{'title','body','badge'}]
    """
    slide = blank_slide(prs, bg=BRAND["green_deep"] if dark else BRAND["paper"])
    title_block(slide, title, eyebrow=eyebrow, subtitle=subtitle,
                variant="systematic",
                color=BRAND["white"] if dark else BRAND["ink"],
                eyebrow_color=BRAND["green_pale"] if dark else None,
                subtitle_color=BRAND["tan"] if dark else None)
    top = 2.45 if subtitle else 2.1
    icon_cards(slide, 0.55, top, 12.2, 6.75 - top, cards, cols=cols, dark=dark)
    logo(slide, dark_bg=bool(dark), l=(11.95 if dark else 0.4), t=(0.344 if dark else 0.3), width_in=(0.969 if dark else 1.4))
    footer(slide, dark=dark, page=page)
    return slide


# --- Enterprise 26 / 37 --------------------------------------------------
def data_table(prs, title, headers, rows, *, subtitle=None, col_widths=None,
               total_row=False, label_column=True, page=None):
    """Real PPTX table with the brand header fill (Enterprise 26 / 37)."""
    slide = blank_slide(prs, bg=BRAND["paper"])
    text_box(slide, 0.603, 0.72, 12.0, 0.8, title, font=F["display_light"],
             size=_fit(title, 29, 46), color=BRAND["green_deep"], anchor="middle")
    if subtitle:
        text_box(slide, 0.603, 1.5, 12.0, 0.4, subtitle, size=14, font=F["body"],
                 color=BRAND["muted"])
    logo(slide, dark_bg=False, l=11.19, t=0.247, width_in=1.477)
    top = 2.0 if subtitle else 1.7
    n = len(rows) + 1
    h = min(4.9, n * 0.42)
    brand_table(slide, 0.603, top, 12.13, h, headers, rows,
                col_widths=col_widths, total_row=total_row,
                label_fill=BRAND["paper"] if label_column else None,
                row_h=h / n)
    footer(slide, page=page)
    return slide


# --- section divider (Enterprise 15) -------------------------------------
def section_divider(prs, title, *, nav=None, eyebrow=None, page=None):
    """Gradient divider with a light serif title and an optional nav row."""
    slide = blank_slide(prs, gradient=COVER_GRADIENT, gradient_angle=45.0)
    if eyebrow:
        text_box(slide, 0.9, 2.85, 11.5, 0.35, eyebrow.upper(), size=12,
                 font=F["body"], color=BRAND["tan"])
    text_box(slide, 0.9, 3.2, 11.5, 1.2, title, font=F["display_light"],
             size=_fit(title, 34.7, 30), color=BRAND["white"], anchor="middle")
    if nav:
        n = len(nav)
        w = 11.5 / n
        for i, item in enumerate(nav):
            text_box(slide, 0.9 + i * w, 4.6, w - 0.2, 0.35, item, size=12,
                     font=F["body"], color=BRAND["tan"])
        rule(slide, 0.9, 4.5, 11.5, color=BRAND["rule_dark"], w_in=0.005)
    _chrome(slide, dark=True, page=page)
    return slide


# ===========================================================================
# cross-deck composites -- the patterns that recur across the 18 reference
# decks rather than sitting on one numbered Example_Slides page.  Same chrome
# as the analysis-title composites above: paper ground, title_block
# "analysis", standard footer, no logo (the logo rides only on the composites
# that set their own title, e.g. timeline / data_table / pricing_table).
#
# All five size their content off _list_height / _est_lines rather than a fixed
# card height.  A fixed height is what puts a 1in void under a four-bullet
# card and silently clips a nine-bullet one -- and the lint's overflow check
# under-reports, so neither shows up without a picture.
# ===========================================================================
def workstream_swimlane(prs, title, rows, columns, *, bars=None, gates=None,
                        today=None, legend=False, subtitle=None, page=None):
    """Workstreams as rows, weeks or months as columns, bars spanning columns.

    rows     ["label"] or [{"label","owner"}] -- the owner renders italic and
             muted under the label, in a left rail OUTSIDE the grid
    columns  ["Wk 1", ...] or ["Jun", ...] -- the column headers
    bars     [{"row":int,"start":int,"span":int,"label":str,"kind":str}];
             row/start are 0-indexed, span is a column count, and kind is
             normal (brand green) | critical (#C0392B, white label) |
             buffer (#F0EEE4, italic muted label)
    gates    [{"label","at":float,"sublines":[str]}] -- diamond marker plus a
             caption under the grid; ``at`` is a column offset, may be fractional
    today    {"label":"Today (5/14)","at":float} -- rule through the grid plus a
             triangle marker under it
    legend   show the milestone / critical-path / buffer key, top right
    """
    slide = blank_slide(prs, bg=BRAND["paper"])
    title_block(slide, title, subtitle=subtitle, variant="analysis")

    # bar kinds are resolved per call so set_theme() moves the green with them
    kinds = {
        "normal": (BRAND["green"], BRAND["white"], False),
        "critical": (C["sev_critical"], BRAND["white"], False),
        "buffer": (SWIMLANE_BUFFER, C["muted"], True),
    }

    rail_l, rail_w = 0.4, 2.35
    grid_l, grid_r = rail_l + rail_w, 12.9
    ncol = max(1, len(columns))
    col_w = (grid_r - grid_l) / ncol

    head_y = 2.5 if subtitle else 2.1
    rule_y = head_y + 0.34
    grid_top = rule_y + 0.1
    gates_h = 0.95 if gates else 0.0
    today_h = 0.5 if today else 0.0
    n = max(1, len(rows))
    pitch = min(0.95, (6.92 - gates_h - today_h - grid_top) / n)
    lane_bottom = grid_top + n * pitch

    text_box(slide, rail_l, head_y, rail_w - 0.15, 0.3, "Workstream", size=12.5,
             bold=True, font=F["body"], color=BRAND["ink"])
    for j, col in enumerate(columns):
        text_box(slide, grid_l + j * col_w, head_y, col_w, 0.3, str(col),
                 size=12.5, bold=True, font=F["body"], color=BRAND["ink"],
                 align="center")
    rule(slide, rail_l, rule_y, grid_r - rail_l, color="#000000", w_in=0.016)

    for j in range(ncol + 1):
        rule(slide, grid_l + j * col_w, grid_top, lane_bottom - grid_top,
             color=BRAND["hair_light"], w_in=0.008, vertical=True)

    for i, row in enumerate(rows):
        y = grid_top + i * pitch
        label = row.get("label", "") if isinstance(row, dict) else str(row)
        owner = row.get("owner") if isinstance(row, dict) else None
        if owner and pitch >= 0.46:
            text_box(slide, rail_l, y + 0.02, rail_w - 0.15, pitch * 0.55, label,
                     size=12, font=F["body"], color=BRAND["ink"],
                     line_spacing=1.03)
            text_box(slide, rail_l, y + pitch * 0.53, rail_w - 0.15,
                     pitch * 0.42, owner, size=10, italic=True, font=F["body"],
                     color=C["muted"])
        elif owner:
            # Too many lanes to stack the owner under the label; run it inline
            # rather than letting it cross the lane rule.
            text_box(slide, rail_l, y, rail_w - 0.15, pitch,
                     [[(label, {}), ("  " + owner,
                                    {"size": 10, "italic": True,
                                     "color": C["muted"]})]],
                     size=12, font=F["body"], color=BRAND["ink"],
                     anchor="middle", line_spacing=1.03)
        else:
            text_box(slide, rail_l, y, rail_w - 0.15, pitch, label, size=12,
                     font=F["body"], color=BRAND["ink"], anchor="middle",
                     line_spacing=1.03)
        if i < n - 1:
            rule(slide, rail_l, y + pitch, grid_r - rail_l, color=BRAND["hair"],
                 w_in=0.008)

    bar_h = 0.26
    for b in (bars or []):
        r = int(b.get("row", 0))
        if not 0 <= r < n:
            continue
        kind = str(b.get("kind", "normal")).lower()
        fill, fg, ital = kinds.get(kind, kinds["normal"])
        x = grid_l + float(b.get("start", 0)) * col_w + 0.02
        w = max(0.14, float(b.get("span", 1)) * col_w - 0.04)
        y = grid_top + r * pitch + (pitch - bar_h) / 2.0
        shp = rect(slide, x, y, w, bar_h, fill=fill,
                   line=BRAND["tan"] if kind == "buffer" else BRAND["white"],
                   line_w_in=0.012, rounded=True, adjust=0.3)
        lbl = b.get("label")
        if not lbl:
            continue
        # A label wider than its bar wraps to a second line INSIDE a 0.26in
        # shape, which spills into the lane below -- so measure first and put
        # anything that does not fit out to the right of the bar instead.
        if len(str(lbl)) * 10.5 * CHAR_ADVANCE / 72.0 <= w - 0.12:
            _label_in(shp, lbl, size=10.5, font=F["body"], color=fg,
                      italic=ital, align="center", pad_in=0.06)
        else:
            text_box(slide, x + w + 0.07, y - 0.02,
                     max(0.6, grid_r - (x + w) - 0.09), bar_h + 0.04, lbl,
                     size=10, font=F["body"], color=C["body"], italic=ital,
                     anchor="middle")

    if today:
        tx = grid_l + float(today.get("at", 0)) * col_w
        rule(slide, tx, grid_top - 0.08, (lane_bottom + 0.02) - (grid_top - 0.08),
             color=BRAND["connector"], w_in=0.01, vertical=True)
        rect(slide, tx - 0.24, lane_bottom + 0.04, 0.48, 0.23,
             fill=BRAND["hair_light"], line=BRAND["connector"], line_w_in=0.008,
             shape=MSO_SHAPE.ISOSCELES_TRIANGLE)
        lw = 1.5
        text_box(slide, min(max(rail_l, tx - lw / 2.0), grid_r - lw),
                 lane_bottom + 0.27, lw, 0.24, today.get("label", "Today"),
                 size=10.5, font=F["body"], color=BRAND["ink"], align="center")

    if gates:
        gy = lane_bottom + today_h + 0.06
        rule(slide, rail_l, gy, grid_r - rail_l, color=BRAND["hair"], w_in=0.008)
        share = max(1.2, min(2.0, (grid_r - grid_l) / max(1, len(gates)) - 0.1))
        for g in gates:
            gx = grid_l + float(g.get("at", 0)) * col_w
            rect(slide, gx - 0.11, gy - 0.11, 0.22, 0.22, fill=C["milestone"],
                 line=BRAND["white"], line_w_in=0.01, shape=MSO_SHAPE.DIAMOND)
            # A gate near either end cannot centre a full-width caption, so
            # narrow the caption instead of sliding it out from under its
            # diamond -- an offset caption reads as belonging to the next gate.
            cap_w = max(1.35, min(share, 2.0 * min(gx - rail_l, 12.93 - gx)))
            want = gx - cap_w / 2.0
            cx = min(max(rail_l, want), 12.93 - cap_w)
            # A gate on the last column boundary cannot centre its caption at
            # all; hug the caption to whichever edge the diamond is on so the
            # two still read as one label.
            align = ("left" if cx > want + 0.02 else
                     "right" if cx < want - 0.02 else "center")
            text_box(slide, cx, gy + 0.16, cap_w, 0.24, g.get("label", ""),
                     size=10.5, bold=True, font=F["body"], color=BRAND["ink"],
                     align=align)
            subs = list(g.get("sublines") or [])
            if subs:
                text_box(slide, cx, gy + 0.4, cap_w, 0.42, subs, size=9.5,
                         font=F["body"], color=C["muted"], align=align,
                         line_spacing=1.05)

    if legend:
        keys = [(MSO_SHAPE.DIAMOND, C["milestone"], None, "Milestone gate", 1.28),
                (None, C["sev_critical"], None, "Critical path", 1.12),
                (None, SWIMLANE_BUFFER, BRAND["tan"], "Buffer", 0.68)]
        x = grid_r - sum(k[4] for k in keys) - 0.3 * len(keys)
        for shape_, fill_, line_, label_, lw_ in keys:
            if shape_ is None:
                rect(slide, x, 0.38, 0.22, 0.18, fill=fill_, line=line_,
                     line_w_in=0.008)
            else:
                rect(slide, x, 0.36, 0.22, 0.22, fill=fill_, line=None,
                     shape=shape_)
            text_box(slide, x + 0.3, 0.33, lw_, 0.28, label_, size=10.5,
                     font=F["body"], color=BRAND["ink"], anchor="middle")
            x += 0.3 + lw_

    logo(slide, dark_bg=False, l=0.4, t=0.3, width_in=1.4)
    footer(slide, page=page)
    return slide


def two_column_list(prs, title, *, columns=None, rows=None, headers=None,
                    mode="columns", subtitle=None, page=None):
    """Two parallel lists: benefits, scope in/out, asks and actions, risks.

    mode     "columns" (default) | "ledger"
    columns  mode=columns: [{"header","items":[str, ...]}] -- exactly 2, each
             under a filled brand-green header bar
    headers  mode=ledger: [str, str] -- the two column headers
    rows     mode=ledger: [{"left","right"}] -- bold label left, explanation
             right, on a subtle zebra
    No arrows or connecting glyphs between the columns; that is ``before_after``.
    """
    mode = str(mode or "columns").lower()
    if mode == "columns":
        if not columns:
            raise ValueError(
                'two_column_list: mode="columns" requires "columns" -- '
                '[{"header": str, "items": [str, ...]}, ...], exactly 2')
    elif mode == "ledger":
        if not rows:
            raise ValueError(
                'two_column_list: mode="ledger" requires "rows" -- '
                '[{"left": str, "right": str}, ...]')
    else:
        raise ValueError('two_column_list: unknown mode %r (columns | ledger)'
                         % mode)

    slide = blank_slide(prs, bg=BRAND["paper"])
    title_block(slide, title, subtitle=subtitle, variant="analysis")
    top = 2.6 if subtitle else 2.25
    bottom, head_h = 6.9, 0.55

    def header_bar(l, t, w, labels):
        """One bar, one or two labels placed at their columns' text inset."""
        rect(slide, l, t, w, head_h, fill=BRAND["green_deep"],
             line=BRAND["green_deep"], line_w_in=0.012)
        for lx, lw, text in labels:
            text_box(slide, lx, t, lw, head_h, text, size=15, bold=True,
                     font=F["body"], color=BRAND["white"], anchor="middle")

    if mode == "columns":
        cols = list(columns)[:2]
        card_w, text_w = 6.15, 6.15 - 0.64
        body_h = bottom - top - head_h - 0.42
        lists = [list(c.get("items") or []) if isinstance(c, dict) else []
                 for c in cols]
        n_items = max([len(x) for x in lists] or [1]) or 1
        # Pick the largest size whose text still leaves room for 10pt of lead
        # between bullets, then spend the slack on that lead: a five-bullet
        # list generously led fills the card, where a tight one leaves a void.
        size, lead = 13.5, 10.0
        for size in (13.5, 13.0, 12.5, 12.0, 11.5):
            lines = max([sum(_est_lines(i, text_w, size) for i in x)
                         for x in lists] or [1])
            text_h = lines * size * 1.12 / 72.0
            if text_h + n_items * 10.0 / 72.0 <= body_h:
                break
        lead = max(10.0, min(26.0, (body_h * 0.9 - text_h) * 72.0 / n_items))
        # Both cards take the taller list's height so the pair reads as one
        # unit, and the card stops where its content stops -- a bordered box
        # running on to the footer rule under a four-bullet list reads unfinished.
        list_h = text_h + n_items * lead / 72.0
        body_h = min(body_h, list_h + 0.06)
        card_h = min(bottom - top - head_h, body_h + 0.5)
        for i, col in enumerate(cols):
            x = 0.4 if i == 0 else 6.8
            rect(slide, x, top + head_h, card_w, card_h,
                 fill=C["fill_neutral"], line=C["border"])
            header_bar(x, top, card_w,
                       [(x + 0.28, card_w - 0.5,
                         col.get("header", "") if isinstance(col, dict)
                         else str(col))])
            text_box(slide, x + 0.32, top + head_h + 0.22, text_w, body_h,
                     lists[i], size=size, font=F["body"], color=C["body"],
                     bullets=True, space_before=lead, line_spacing=1.12)
        logo(slide, dark_bg=False, l=0.4, t=0.3, width_in=1.4)
        footer(slide, page=page)
        return slide

    left_l, left_w = 0.4, 4.1
    right_l, right_w = 4.6, 8.3
    span = right_l + right_w - left_l
    heads = list(headers or [])
    rtop = top
    if heads:
        header_bar(left_l, top, span,
                   [(left_l + 0.18, left_w - 0.3, heads[0] if heads else ""),
                    (right_l + 0.14, right_w - 0.3,
                     heads[1] if len(heads) > 1 else "")])
        rtop = top + head_h + 0.1
    norm = []
    for r in rows:
        if isinstance(r, dict):
            norm.append((r.get("left", ""), r.get("right", "")))
        elif isinstance(r, (list, tuple)):
            norm.append((r[0], r[1] if len(r) > 1 else ""))
        else:
            norm.append((str(r), ""))
    n = max(1, len(norm))
    pitch = min(0.92, (bottom - rtop) / n)
    for i, (left, right) in enumerate(norm):
        y = rtop + i * pitch
        if i % 2 == 0:
            rect(slide, left_l, y, span, pitch - 0.06, fill=C["fill_neutral"],
                 line=None)
        text_box(slide, left_l + 0.18, y, left_w - 0.3, pitch - 0.06, left,
                 size=13.5, bold=True, font=F["body"], color=C["label_green"],
                 anchor="middle", line_spacing=1.05)
        text_box(slide, right_l + 0.14, y, right_w - 0.3, pitch - 0.06, right,
                 size=13, font=F["body"], color=C["body"], anchor="middle",
                 line_spacing=1.08)
        if i < n - 1:
            rule(slide, left_l, y + pitch - 0.03, span,
                 color=BRAND["hair_light"], w_in=0.008)
    logo(slide, dark_bg=False, l=0.4, t=0.3, width_in=1.4)
    footer(slide, page=page)
    return slide


def stepper_row(prs, title, steps, *, subtitle=None, connector="triangle",
                terminal_inverted=False, callout=None, badge=None, page=None):
    """Horizontal numbered process: 4-6 step cards read left to right.

    steps             [{"title","body","number"}] -- auto-numbered when
                      "number" is absent; 4-6 cards read best
    connector         "triangle" (default) | "arrow" | "none", muted gray-green,
                      never the brand accent
    terminal_inverted render the LAST card dark fill / light text -- use when
                      the final step exits back to the customer
    callout           {"text","at":int} -- flag pinned above step index ``at``
    badge             pill flag, top right of the slide, for an unresolved item
    """
    slide = blank_slide(prs, bg=BRAND["paper"])
    title_block(slide, title, subtitle=subtitle, variant="analysis")
    if badge:
        bw = max(1.4, min(3.8, 0.082 * len(str(badge)) + 0.5))
        chip(slide, 12.9 - bw, 0.34, bw, 0.36, str(badge), fill=BRAND["white"],
             line=C["callout_red"], line_w_in=0.011, color=C["callout_red"],
             size=11, bold=True)

    norm = [s if isinstance(s, dict) else {"title": str(s)} for s in steps]
    n = max(1, len(norm))
    conn = str(connector or "triangle").lower()
    conn_w = {"triangle": 0.3, "arrow": 0.34, "none": 0.0}.get(conn, 0.3)
    if n == 1:
        conn_w = 0.0
    card_w = (12.5 - conn_w * (n - 1)) / n
    text_w = card_w - 0.52

    top = 2.6 if subtitle else 2.3
    if callout:
        top += 0.58
    avail = 6.85 - top
    d, t_size, b_size = 0.46, 14.5, 11.5
    title_h = max(0.42, max(_est_lines(s.get("title", ""), text_w, t_size)
                            for s in norm) * t_size * 1.06 / 72.0 + 0.1)
    body_h = max([_est_lines(s["body"], text_w, b_size) * b_size * 1.14 / 72.0
                  + 0.1 for s in norm if s.get("body")] or [0.0])
    card_h = max(1.55, min(0.26 + d + 0.2 + title_h + body_h + 0.28, avail))
    top += max(0.0, (avail - card_h) * 0.38)

    for i, s in enumerate(norm):
        x = 0.4 + i * (card_w + conn_w)
        inv = bool(terminal_inverted) and i == n - 1
        rect(slide, x, top, card_w, card_h,
             fill=C["hdr_green"] if inv else BRAND["white"],
             line=C["hdr_green"] if inv else C["border"], line_w_in=0.014,
             rounded=True, adjust=0.05)
        circle_label(slide, x + 0.26, top + 0.26, d,
                     s.get("number") or str(i + 1),
                     fill=BRAND["green_pale"] if inv else C["accent_green"],
                     color=C["hdr_green"] if inv else BRAND["white"], size=15)
        ty = top + 0.26 + d + 0.2
        text_box(slide, x + 0.26, ty, text_w, title_h, s.get("title", ""),
                 size=t_size, bold=True, font=F["body"],
                 color=BRAND["white"] if inv else C["hdr_green"],
                 line_spacing=1.06)
        if s.get("body"):
            text_box(slide, x + 0.26, ty + title_h + 0.06, text_w,
                     max(0.3, top + card_h - 0.2 - (ty + title_h + 0.06)),
                     s["body"], size=b_size, font=F["body"],
                     color=C["eyebrow_green"] if inv else C["muted"],
                     line_spacing=1.14)
        if i < n - 1 and conn_w:
            cy = top + card_h / 2.0
            if conn == "arrow":
                rect(slide, x + card_w + 0.05, cy - 0.1, conn_w - 0.1, 0.2,
                     fill=C["muted"], line=None, shape=MSO_SHAPE.RIGHT_ARROW)
            else:
                rect(slide, x + card_w + 0.06, cy - 0.11, conn_w - 0.12, 0.22,
                     fill=C["muted"], line=None, rotation=90,
                     shape=MSO_SHAPE.ISOSCELES_TRIANGLE)

    if callout:
        at = min(max(int(callout.get("at", 0)), 0), n - 1)
        cx = 0.4 + at * (card_w + conn_w)
        w = min(card_w + 0.7, 3.4)
        box = rect(slide, min(max(0.4, cx + (card_w - w) / 2.0), 12.9 - w),
                   top - 0.58, w, 0.4, fill=C["fill_green"],
                   line=C["accent_green"], line_w_in=0.011, rounded=True,
                   adjust=0.4)
        _label_in(box, str(callout.get("text", "")), size=11, bold=True,
                  font=F["body"], color=C["hdr_green"], align="center",
                  pad_in=0.12)
        rect(slide, cx + card_w / 2.0 - 0.09, top - 0.2, 0.18, 0.14,
             fill=C["accent_green"], line=None, rotation=180,
             shape=MSO_SHAPE.ISOSCELES_TRIANGLE)
    logo(slide, dark_bg=False, l=0.4, t=0.3, width_in=1.4)
    footer(slide, page=page)
    return slide


def status_table(prs, title, headers, rows, *, subtitle=None, status_col=None,
                 statuses=None, col_widths=None, note=None, page=None):
    """Dependency checklist / issue tracker: status lives in the cell fill.

    rows        [[cell, ...], ...] -- cell strings, "\\n" splits a cell onto
                more than one line
    status_col  0-indexed column whose value colours that cell's fill; omit to
                disable
    statuses    {"Complete":"#D9EAD3", ...} -- override the default map
                (Complete/Done, In-Progress, Not started/Backlog, Blocked,
                Pending), matched case-insensitively
    col_widths  inches per column
    note        small line under the table
    """
    slide = blank_slide(prs, bg=BRAND["paper"])
    title_block(slide, title, subtitle=subtitle, variant="analysis")

    ncol = max(1, len(headers))
    lines = 1
    norm = []
    for row in rows:
        cells = []
        for j in range(ncol):
            value = row[j] if j < len(row) else ""
            if isinstance(value, str) and "\n" in value:
                parts = value.split("\n")
                lines = max(lines, len(parts))
                cells.append(parts)
            else:
                cells.append(value)
        norm.append(cells)

    top = 2.5 if subtitle else 2.15
    bottom = 6.9 - (0.42 if note else 0.0)
    n_rows = len(norm) + 1
    avail = bottom - top
    # PowerPoint grows a row to fit its text no matter what height we set, so
    # a row_h the copy cannot live in does not clip -- it pushes the table's
    # real bottom past whatever we put underneath.  Size the rows for the copy
    # instead, and buy the space back from the cell padding and the type.
    size, pad = 11.5, 0.12
    row_h = max(0.34, lines * size * 1.22 / 72.0 + pad)
    tight = n_rows * row_h > avail
    if tight:
        pad = 0.06
        row_h = avail / n_rows
        size = max(9.0, min(size, (row_h - pad) * 72.0 / (lines * 1.22)))
    h = min(avail, n_rows * row_h)
    table = brand_table(slide, 0.4, top, 12.5, h, headers, norm,
                        col_widths=col_widths, label_fill=None, font_size=size,
                        header_size=size, row_h=h / n_rows)
    if tight:
        for r in table.rows:
            for cell in r.cells:
                cell.margin_top = cell.margin_bottom = Inches(pad / 2.0)

    if status_col is not None:
        fills = dict(STATUS_FILLS)
        for key, value in (statuses or {}).items():
            fills[_status_key(key)] = value
        j = int(status_col)
        if 0 <= j < ncol:
            for i, cells in enumerate(norm):
                value = cells[j]
                hexval = fills.get(_status_key(
                    " ".join(value) if isinstance(value, list) else value))
                if not hexval:
                    continue
                cell = table.cell(i + 1, j)
                cell.fill.solid()
                cell.fill.fore_color.rgb = _rgb(hexval)

    if note:
        text_box(slide, 0.4, min(top + h + 0.14, 6.6), 12.5, 0.3, note,
                 size=10.5, italic=True, font=F["body"], color=C["muted"])
    logo(slide, dark_bg=False, l=0.4, t=0.3, width_in=1.4)
    footer(slide, page=page)
    return slide


def step_detail_io(prs, title, inputs, does, outputs, *, phase=None, hitl=None,
                   needs=None, confirm=None, subtitle=None, page=None):
    """One process step in full: inputs, what FurtherAI does, outputs, review.

    inputs   [str] top row left, under the INPUTS eyebrow
    does     [str] top row middle, under FURTHERAI DOES
    outputs  [str] top row right, under OUTPUTS
    hitl     [str] wider card below left, under HUMAN REVIEW
    needs    [str] wider card below right, under WHAT WE NEED
    phase    small badge, e.g. "PHASE 2"
    confirm  "TO CONFIRM" callout for anything unresolved on this step
    """
    slide = blank_slide(prs, bg=BRAND["paper"])
    title_block(slide, title, subtitle=subtitle, variant="analysis")
    if phase:
        pw = max(1.15, min(2.8, 0.082 * len(str(phase)) + 0.52))
        _track(chip(slide, 12.9 - pw, 0.34, pw, 0.34, str(phase).upper(),
                    fill=C["hdr_green"], line=C["hdr_green"],
                    color=BRAND["white"], size=10.5, bold=True), 1.2)

    top = 2.5 if subtitle else 2.2
    bottom = 6.9 - (0.78 if confirm else 0.0)
    lower = [(e, items) for e, items in (("Human review", hitl),
                                         ("What we need", needs)) if items]
    gap = 0.26
    avail = bottom - top - (gap if lower else 0.0)
    cw = (12.5 - 2 * 0.25) / 3.0
    lw = (12.5 - 0.25 * (len(lower) - 1)) / len(lower) if lower else 12.5

    # Five cards on one slide is the densest thing in the library.  Step the
    # body size down until the estimate fits rather than letting the lower row
    # ride up over the top row's last bullet.
    for size in (12.0, 11.5, 11.0, 10.5, 10.0):
        lead = max(4.0, size * 0.58)
        head_h, pad_b = 0.3 + 0.024 * size, 0.18
        need_up = head_h + pad_b + max(
            _list_height(x, cw - 0.44, size, space_before=lead)
            for x in (inputs, does, outputs))
        need_lo = (head_h + pad_b + max(
            _list_height(x, lw - 0.44, size, space_before=lead)
            for _, x in lower)) if lower else 0.0
        if need_up + need_lo <= avail:
            break
    total = need_up + need_lo
    if total <= avail:
        slack = avail - total
        upper_h = need_up + min(0.22, slack * need_up / total)
        lower_h = need_lo + (min(0.22, slack * need_lo / total) if lower else 0.0)
        top += max(0.0, (avail - upper_h - lower_h) * 0.4)
    else:
        upper_h = avail * need_up / total
        lower_h = avail - upper_h if lower else 0.0

    for k, (eyebrow, items) in enumerate((("Inputs", inputs),
                                          ("FurtherAI does", does),
                                          ("Outputs", outputs))):
        _io_card(slide, 0.4 + k * (cw + 0.25), top, cw, upper_h, eyebrow, items,
                 accent=C["hdr_green"] if k == 1 else C["accent_green"],
                 size=size, lead=lead, head_h=head_h)
    for k, (eyebrow, items) in enumerate(lower):
        _io_card(slide, 0.4 + k * (lw + 0.25), top + upper_h + gap, lw, lower_h,
                 eyebrow, items, accent=BRAND["green"], size=size, lead=lead,
                 head_h=head_h)
    if confirm:
        cy = 6.9 - 0.66
        rect(slide, 0.4, cy, 12.5, 0.66, fill=BRAND["white"],
             line=C["callout_red"], line_w_in=0.012)
        _track(text_box(slide, 0.62, cy + 0.08, 12.1, 0.24, "TO CONFIRM",
                        size=10, bold=True, font=F["body"],
                        color=C["callout_red"]), 1.2)
        text_box(slide, 0.62, cy + 0.32, 12.1, 0.28, confirm, size=11.5,
                 font=F["body"], color=C["body"])
    logo(slide, dark_bg=False, l=0.4, t=0.3, width_in=1.4)
    footer(slide, page=page)
    return slide


# ===========================================================================
# pattern registry -- the contract build_deck.py dispatches on
# ===========================================================================
PATTERNS = {
    "cover": (cover, ["headline"],
              ["subhead", "eyebrow", "background", "gradient", "client_logo",
               "client_logo_label", "page", "footer_left", "footer_center"]),
    "section_divider": (section_divider, ["title"], ["nav", "eyebrow", "page"]),
    "agenda": (agenda, ["title", "items"], ["panel_title", "page"]),
    "before_after": (before_after, ["title", "left", "right"],
                     ["subtitle", "arrow", "recommend", "page"]),
    "two_pillars": (two_pillars, ["title", "pillars"],
                    ["subtitle", "legend", "page"]),
    "numbered_steps": (numbered_steps, ["title", "steps"],
                       ["eyebrow", "header", "options", "status", "note", "page"]),
    "scorecard": (scorecard, ["title", "dimensions"],
                  ["narrative", "narrative_title", "eyebrow", "header", "page"]),
    "process_audit": (process_audit, ["title", "steps", "issues"],
                      ["meta", "left_header", "right_header", "page"]),
    "rag_matrix": (rag_matrix, ["title", "rows"],
                   ["cols", "cells", "subtitle", "mode", "legend", "row_header",
                    "summary_header", "page"]),
    "timeline": (timeline, ["title", "periods", "lanes"],
                 ["today", "output_header", "legend", "page"]),
    "next_steps": (next_steps, ["items"], ["title", "subtitle", "page"]),
    "phase_chevrons": (phase_chevrons, ["title", "phases", "rows", "cells"],
                       ["recommendation", "page"]),
    "pricing_table": (pricing_table, ["title", "headers", "rows"],
                      ["col_widths", "note", "section_title", "options", "page"]),
    "architecture_hero": (architecture_hero, ["headline"],
                          ["left_label", "right_label", "props", "certs",
                           "background", "page"]),
    "use_case_rail": (use_case_rail, ["category", "items"],
                      ["screenshot", "dark", "page"]),
    "agent_diagram": (agent_diagram, ["title", "hub", "nodes"],
                      ["subtitle", "page"]),
    "security_trust": (security_trust, ["headline", "certs", "cards"], ["page"]),
    "why_us": (why_us, ["headline", "body", "callouts"], ["screenshot", "page"]),
    "closing": (closing, [], ["message", "background", "page", "footer_left",
                              "footer_center"]),
    "stat_hero": (stat_hero, ["title", "stats"],
                  ["eyebrow", "subtitle", "disclaimer", "cols", "dark", "page"]),
    "card_grid": (card_grid, ["title", "cards"],
                  ["eyebrow", "subtitle", "cols", "dark", "page"]),
    "data_table": (data_table, ["title", "headers", "rows"],
                   ["subtitle", "col_widths", "total_row", "label_column", "page"]),
    "workstream_swimlane": (workstream_swimlane, ["title", "rows", "columns"],
                            ["bars", "gates", "today", "legend", "subtitle",
                             "page"]),
    "two_column_list": (two_column_list, ["title"],
                        ["columns", "rows", "headers", "mode", "subtitle",
                         "page"]),
    "stepper_row": (stepper_row, ["title", "steps"],
                    ["subtitle", "connector", "terminal_inverted", "callout",
                     "badge", "page"]),
    "status_table": (status_table, ["title", "headers", "rows"],
                     ["subtitle", "status_col", "statuses", "col_widths",
                      "note", "page"]),
    "step_detail_io": (step_detail_io, ["title", "inputs", "does", "outputs"],
                       ["phase", "hitl", "needs", "confirm", "subtitle",
                        "page"]),
}

__all__ = [
    "BRAND", "BRAND_CONSULTING", "CANVAS", "CANVAS_EMU", "COVER_GRADIENT",
    "FAMILY", "RAMP",
    "SEVERITY", "RATING", "STATUS_FILLS", "SWIMLANE_BUFFER",
    "PATTERNS", "THEMES",
    "new_deck", "blank_slide", "slide_size", "notes", "set_theme",
    "text_box", "rect", "rule", "circle_label", "picture", "chip",
    "placeholder_panel", "logo", "logo_png", "logo_mark_png",
    "title_block", "footer",
    "card", "steps_list", "line_table", "brand_table", "stat_tiles",
    "icon_cards", "pipeline_row", "rating_arrow",
] + list(PATTERNS)
