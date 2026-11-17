#!/usr/bin/env python3
"""build_pdf.py — print-quality branded PDFs via HTML -> headless Chrome.

The reference one-pager (``assets/brand/doc_templates/One_Pager.pdf``) is a US
Letter portrait sheet built from hairline-bordered card grids, letterspaced
all-caps green eyebrows, full-width dark-green panels and stat bands, a sparing
orange accent and a centered footer credit line. None of that is reachable with
python-docx, so this script renders it as a self-contained HTML page and prints
it with Chrome.

Beyond the one-pager it also carries the long-technical-document blocks — a
full-bleed dark cover, a dotted contents page, numbered section headers, code
blocks, HTTP method badges, field tables and the three-variant callout — and the
type contracts from ``references/doc_types.md``, so a given kind of document
comes out with the same structure every time.

    python3 build_pdf.py spec.json --out One_Pager.pdf
    python3 build_pdf.py spec.json --out One_Pager.pdf --html One_Pager.html
    python3 build_pdf.py --check spec.json
    python3 build_pdf.py --list-blocks
    python3 build_pdf.py --list-types
    python3 build_pdf.py --type api_integration_guide --scaffold > spec.json
    python3 build_pdf.py spec.json --check --type api_integration_guide
    python3 build_pdf.py --docx Report.docx     # .docx -> .pdf (needs LibreOffice)

Everything is inlined — fonts, logo, styles — so the HTML never makes a network
request. With no Chrome on the machine the HTML is still written and the path is
printed, so the user can open it and print to PDF themselves.
"""
from __future__ import annotations

import argparse
import base64
import html as html_mod
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "lib"))
sys.path.insert(0, str(SCRIPT_DIR))

from fai_render import find_chrome, font_face_css, print_to_pdf, tokens_css  # noqa: E402

BRAND = PLUGIN_ROOT / "assets" / "brand"
FONT_DIR = BRAND / "fonts"
LOGO_DIR = BRAND / "logos"

#: Palette 1 — the canonical FurtherAI palette, plus the one-pager's accents.
PALETTE = {
    "green_deep": "#074B40",
    "green": "#25654F",
    "green_dark": "#1D4438",
    "green_pale": "#D9EAD3",
    "ink": "#14161C",
    "ink_alt": "#2B2D31",
    "paper": "#FBFBF9",
    "card": "#F7F7F5",
    "tan": "#D0CEC3",
    "hair": "#CCCCCC",
    "muted": "#595959",
    "orange": "#E08A4F",
    # Long-technical-doc extensions. The greens/tans above are BRAND.md
    # canonical; these are the semantic accents (mango / sinopia / navy from
    # CONVENTIONS.md) plus the two neutral fills the technical blocks need.
    "near_black": "#162C28",
    "cream": "#F5F0EB",
    "warn": "#FB9608",
    "warn_bg": "#FEF9F0",
    "warn_ink": "#8A4B00",
    "mint": "#7DD9B7",
    "red": "#B53B18",
    "navy": "#425C86",
    "code_bg": "#F4F4F4",
    "code_edge": "#E4E4E4",
}

#: The cover gradient recipe from BRAND.md, verbatim.
COVER_GRADIENT = ("linear-gradient(158deg, #074B40 0%, #1A421F 47%, "
                  "#14452A 50%, #203E13 100%)")

#: Default @page margins. A spec may override them with a "margins" object.
MARGINS = {"top": 0.45, "right": 0.5, "bottom": 0.45, "left": 0.5}

#: Content bbox of the shipped wordmark JPEG — the file is ~57% padding.
WORDMARK_BBOX = (22, 116, 614, 242)


# --------------------------------------------------------------------------
# Assets
# --------------------------------------------------------------------------


def _data_uri(blob: bytes, mime: str) -> str:
    return "data:{};base64,{}".format(mime, base64.b64encode(blob).decode("ascii"))


_IMG_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".gif": "image/gif", ".webp": "image/webp"}

# At a 6.5in text column a crop wider than ~1900 source pixels renders UI text
# under 4pt. Warn rather than fail: the caller may be embedding a deliberately
# wide diagram, but a wide screenshot is almost always the wrong crop.
_LEGIBLE_MAX_PX = 1900


def _image_uri(path: Path) -> str:
    mime = _IMG_MIME.get(path.suffix.lower())
    if mime is None:
        raise ValueError("unsupported image type: {}".format(path.name))
    return _data_uri(path.read_bytes(), mime)


def _warn_if_illegible(path: Path) -> None:
    try:
        from PIL import Image
    except ImportError:
        return
    try:
        with Image.open(path) as img:
            width = img.size[0]
    except Exception:
        return
    if width > _LEGIBLE_MAX_PX:
        sys.stderr.write(
            "warning: {} is {}px wide (> {}px) - crop to the panel that "
            "matters or its UI text will print under 4pt\n".format(
                path.name, width, _LEGIBLE_MAX_PX))


def _font_uri(path: Path) -> str:
    return _data_uri(path.read_bytes(), "font/ttf")


def logo_uri() -> str:
    """The horizontal wordmark, cropped to its content bbox, as a data URI."""
    src = LOGO_DIR / "Logo_Full_Green_on_White.jpg"
    try:
        import io

        from PIL import Image

        with Image.open(src) as img:
            buf = io.BytesIO()
            img.convert("RGB").crop(WORDMARK_BBOX).save(buf, "PNG")
        return _data_uri(buf.getvalue(), "image/png")
    except ImportError:
        sys.stderr.write(
            "build_pdf: Pillow not installed — using the uncropped wordmark, which "
            "carries ~57% white padding. "
            "Run `python3 -m pip install Pillow` for a tight masthead.\n"
        )
        return _data_uri(src.read_bytes(), "image/jpeg")


def logo_white_uri() -> str:
    """The white wordmark for dark grounds, as a data URI.

    ``Logo_Full_White_on_Transparent.png`` is already cropped to its content
    bbox, so it needs no Pillow pass — place it by height and let width follow.
    """
    return _data_uri((LOGO_DIR / "Logo_Full_White_on_Transparent.png").read_bytes(),
                     "image/png")


def brand_font_css() -> str:
    """@font-face rules for the brand families, all embedded as data URIs.

    Fraunces is the variable font, so every rule that uses it must pin
    ``font-variation-settings: 'opsz' 72, 'wght' N`` — the font's default
    instance is opsz 9 / wght 900, i.e. Black, which is never what the brand
    means by "Fraunces".

    All five Wix Madefor Display statics are registered, at the weights their
    names claim, so ``font-weight: 500`` / ``800`` resolve to the real Medium
    and ExtraBold cuts instead of being synthesised off Regular or Bold.
    """
    fraunces = FONT_DIR / "Fraunces" / "Fraunces-VariableFont_SOFT,WONK,opsz,wght.ttf"
    fraunces_i = FONT_DIR / "Fraunces" / "Fraunces-Italic-VariableFont_SOFT,WONK,opsz,wght.ttf"
    wmd = FONT_DIR / "Wix_Madefor_Display" / "static"
    def face(name, path, weight, style="normal"):
        return ("@font-face { font-family: '" + name + "'; src: url("
                + _font_uri(path) + ") format('truetype'); font-weight: " + weight
                + "; font-style: " + style + "; }")

    return "\n".join([
        face("Fraunces", fraunces, "100 900"),
        face("Fraunces", fraunces_i, "100 900", "italic"),
        face("Wix Madefor Display", wmd / "WixMadeforDisplay-Regular.ttf", "400"),
        face("Wix Madefor Display", wmd / "WixMadeforDisplay-Medium.ttf", "500"),
        face("Wix Madefor Display", wmd / "WixMadeforDisplay-SemiBold.ttf", "600"),
        face("Wix Madefor Display", wmd / "WixMadeforDisplay-Bold.ttf", "700"),
        face("Wix Madefor Display", wmd / "WixMadeforDisplay-ExtraBold.ttf", "800"),
    ])


# --------------------------------------------------------------------------
# Stylesheet
# --------------------------------------------------------------------------

PRINT_CSS = """
:root {
  --green-deep: %(green_deep)s;
  --green: %(green)s;
  --green-dark: %(green_dark)s;
  --green-pale: %(green_pale)s;
  --ink: %(ink)s;
  --ink-alt: %(ink_alt)s;
  --paper: %(paper)s;
  --card: %(card)s;
  --tan: %(tan)s;
  --hair: %(hair)s;
  --muted: %(muted)s;
  --orange: %(orange)s;
  --near-black: %(near_black)s;
  --cream: %(cream)s;
  --warn: %(warn)s;
  --warn-bg: %(warn_bg)s;
  --warn-ink: %(warn_ink)s;
  --mint: %(mint)s;
  --red: %(red)s;
  --navy: %(navy)s;
  --code-bg: %(code_bg)s;
  --code-edge: %(code_edge)s;
  --serif: 'Fraunces', Georgia, 'Times New Roman', serif;
  --sans: 'Wix Madefor Display', 'Wix Madefor Text', -apple-system, 'Segoe UI', sans-serif;
  /* No monospace face is vendored in assets/brand/fonts, and BRAND.md points
     monospace at the system ("Courier New"). This stack keeps it a real mono
     everywhere; Chrome subsets and embeds whichever one it picks. */
  --mono: 'SF Mono', SFMono-Regular, ui-monospace, Menlo, Consolas,
          'Liberation Mono', 'Courier New', monospace;
}

@page { size: 8.5in 11in; margin: 0.45in 0.5in; }

html, body { margin: 0; padding: 0; background: #ffffff; }
body {
  font-family: var(--sans);
  font-size: 9pt;
  line-height: 1.45;
  color: var(--ink-alt);
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}
* { box-sizing: border-box; }
p { margin: 0 0 6pt; }

/* Each spec page is a flex column the height of the printable area, so the
   footer sits on the page's bottom edge the way the reference sheet does. */
.page { display: flex; flex-direction: column; min-height: 10.1in; }
.page + .page { break-before: page; page-break-before: always; }
.page > .footer { margin-top: auto; }

/* Masthead ------------------------------------------------------------- */
.masthead { display: flex; align-items: center; justify-content: space-between;
            margin: 0 0 12pt; }
.masthead img { height: 0.24in; width: auto; display: block; }
.masthead .tag { font-size: 6.5pt; letter-spacing: 0.16em; text-transform: uppercase;
                 color: var(--muted); }

/* Title block ---------------------------------------------------------- */
h1.title {
  font-family: var(--serif);
  font-variation-settings: 'opsz' 72, 'wght' 400;
  font-weight: 400;
  font-size: 20pt; line-height: 1.12; letter-spacing: -0.005em;
  color: var(--ink); margin: 0 0 4pt;
}
.deck { font-size: 8.5pt; color: var(--muted); margin: 0 0 6pt; }
.rule { border-top: 0.75pt solid var(--green-deep); margin: 0 0 9pt; }
.intro { font-size: 9pt; color: var(--ink-alt); margin: 0 0 4pt; }

h2.section {
  font-family: var(--serif);
  font-variation-settings: 'opsz' 72, 'wght' 400;
  font-weight: 400; font-size: 13.5pt; color: var(--ink);
  margin: 13pt 0 2pt;
}
h2.section + .deck { margin-bottom: 7pt; }

/* Eyebrow -------------------------------------------------------------- */
.eyebrow {
  font-size: 7.5pt; font-weight: 700; letter-spacing: 0.15em;
  text-transform: uppercase; color: var(--green-deep);
  margin: 12pt 0 6pt;
}

/* Hairline card grid --------------------------------------------------- */
.grid { display: grid; border-top: 0.5pt solid var(--hair);
        border-left: 0.5pt solid var(--hair); }
.grid > .cell { border-right: 0.5pt solid var(--hair);
                border-bottom: 0.5pt solid var(--hair); padding: 8pt 10pt; }
.card .t { font-weight: 700; font-size: 9.5pt; color: var(--ink); margin: 0 0 3pt; }
.card .b { font-size: 8pt; color: var(--muted); line-height: 1.42; margin: 0; }
.card .note { font-size: 8pt; color: var(--ink-alt); margin: 5pt 0 0; }
.card .note b { color: var(--green-deep); font-weight: 700; }

/* Pipeline strip ------------------------------------------------------- */
.pipeline > .cell { padding: 7pt 6pt; text-align: center; }
.pipeline .l { font-weight: 700; font-size: 8pt; color: var(--ink); }
.pipeline .s { font-size: 6.5pt; color: var(--muted); margin-top: 2pt; line-height: 1.3; }
.pipeline .cell.accent .l, .pipeline .cell.accent .s { color: var(--green-deep); }

/* Data table ----------------------------------------------------------- */
table.data { width: 100%%; border-collapse: collapse; }
table.data th {
  background: var(--green-deep); color: #ffffff;
  font-size: 7.5pt; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase;
  text-align: left; padding: 6pt 8pt;
}
table.data td {
  border-bottom: 0.5pt solid var(--hair);
  padding: 6pt 8pt; font-size: 8pt; vertical-align: top; color: var(--muted);
}
table.data td:first-child { color: var(--ink); font-weight: 700; }

/* Stat band ------------------------------------------------------------ */
.stats { background: var(--green-deep); display: grid; padding: 11pt 0; text-align: center; }
.stats .n {
  font-family: var(--serif);
  font-variation-settings: 'opsz' 72, 'wght' 300;
  font-weight: 300; font-size: 26pt; line-height: 1; color: #ffffff;
}
.stats .l { font-size: 6.5pt; letter-spacing: 0.14em; text-transform: uppercase;
            color: var(--tan); margin-top: 5pt; }

/* Architecture panel --------------------------------------------------- */
.panel { background: var(--green-deep); padding: 12pt 14pt 14pt; }
.panel h3 {
  font-family: var(--serif); font-style: italic;
  font-variation-settings: 'opsz' 72, 'wght' 300;
  font-weight: 300; font-size: 13pt; color: #ffffff;
  text-align: center; margin: 0 0 9pt;
}
.panel .caps { font-size: 6pt; letter-spacing: 0.18em; text-transform: uppercase;
               color: var(--tan); text-align: center; margin: 9pt 0 5pt; }
.panel .row { display: grid; gap: 4pt; }
.panel .box { background: #ffffff; color: var(--green-deep);
              font-size: 7.5pt; font-weight: 700; text-align: center; padding: 6pt 4pt; }
.panel .box.dark { background: var(--green-dark); color: #ffffff; }
.panel .box .sub { display: block; font-weight: 400; font-size: 6pt;
                   color: var(--tan); margin-top: 2pt; }
.panel .box.dark .sub { color: var(--tan); }
.panel .bar { background: var(--orange); color: #3B1F06;
              font-size: 7.5pt; font-weight: 700; letter-spacing: 0.05em;
              text-align: center; padding: 5pt; margin: 4pt 0 0; }

/* Tinted band ---------------------------------------------------------- */
.band { background: var(--card); display: grid; }
.band .col { padding: 9pt 10pt; }
.band .col .t { font-weight: 700; font-size: 8.5pt; color: var(--ink); margin: 0 0 3pt; }
.band .col .b { font-size: 7pt; color: var(--muted); line-height: 1.38; margin: 0; }

/* Footer --------------------------------------------------------------- */
.footer { border-top: 0.5pt solid var(--hair); margin-top: 13pt; padding-top: 6pt;
          text-align: center; font-size: 7.5pt; color: var(--muted); }
.footer b { color: var(--ink); font-weight: 700; }
.footer .sep { padding: 0 7pt; color: var(--hair); }
.footer .last, .footer .last b { color: var(--green-deep); }

/* ====================================================================== */
/* Long-technical-document blocks                                          */
/* ====================================================================== */

/* Dark cover ----------------------------------------------------------- */
/* A named page is the only way to bleed past the @page margin in Chrome:
   the margin box is not paintable, so the cover gets its own zero-margin
   page and fills the whole 8.5 x 11in sheet. */
@page cover { size: 8.5in 11in; margin: 0; }
.page.cover { page: cover; display: block; min-height: 0; height: 11in;
              overflow: hidden; }
.cover-fill {
  width: 8.5in; height: 11in; overflow: hidden;
  background: %(cover_gradient)s;
  color: #ffffff; padding: 1.0in 0.95in 0.8in;
  display: flex; flex-direction: column;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
/* align-self is load-bearing: .cover-fill is a column flex container, so the
   default stretch would blow the wordmark out to the full 6.6in measure. */
.cover-fill img.mark { height: 0.3in; width: auto; display: block;
                       align-self: flex-start; }
.cover-fill .lower { margin-top: auto; }
.cover-fill h1 {
  font-family: var(--serif);
  font-variation-settings: 'opsz' 72, 'wght' 300;
  font-weight: 300; font-size: 38pt; line-height: 1.06; letter-spacing: -0.012em;
  color: #ffffff; margin: 0 0 13pt; max-width: 5.6in;
}
.cover-fill .sub { font-size: 11.5pt; line-height: 1.46; color: #E3EDE7;
                   margin: 0 0 20pt; max-width: 5.2in; }
.cover-fill .cpills { display: flex; flex-wrap: wrap; gap: 5pt; margin: 0 0 22pt; }
.cover-fill .cpills span {
  border: 0.75pt solid rgba(255,255,255,0.42); border-radius: 999px;
  padding: 3.5pt 9pt; font-size: 7pt; font-weight: 600; letter-spacing: 0.09em;
  text-transform: uppercase; color: #ffffff; white-space: nowrap;
}
.cover-fill .meta { font-size: 8.5pt; letter-spacing: 0.03em; color: var(--tan); }
.cover-fill .conf {
  margin-top: 9pt; padding-top: 8pt; font-size: 6.5pt; letter-spacing: 0.15em;
  text-transform: uppercase; color: rgba(255,255,255,0.62);
  border-top: 0.5pt solid rgba(255,255,255,0.2);
}

/* Dotted contents ------------------------------------------------------ */
.toc { margin: 4pt 0 0; }
.toc .row { break-inside: avoid; page-break-inside: avoid; display: flex; align-items: baseline; gap: 8pt; padding: 5.5pt 0; }
.toc .n {
  flex: 0 0 auto; min-width: 0.32in;
  font-family: var(--serif);
  font-variation-settings: 'opsz' 72, 'wght' 400;
  font-weight: 400; font-size: 11pt; color: var(--green);
}
.toc .t { flex: 0 1 auto; font-size: 10pt; font-weight: 600; color: var(--ink);
          line-height: 1.3; }
.toc .dots { flex: 1 1 12pt; min-width: 12pt; align-self: baseline;
             border-bottom: 0.5pt dotted var(--hair); transform: translateY(-2.5pt); }

/* Numbered section header ---------------------------------------------- */
.nsh { margin: 15pt 0 8pt; break-after: avoid; page-break-after: avoid; }
.nsh .hd { display: flex; align-items: baseline; gap: 10pt; }
.nsh .n {
  flex: 0 0 auto;
  font-family: var(--serif);
  font-variation-settings: 'opsz' 72, 'wght' 300;
  font-weight: 300; font-size: 23pt; line-height: 1; color: var(--green);
}
.nsh .t {
  font-family: var(--serif);
  font-variation-settings: 'opsz' 72, 'wght' 600;
  font-weight: 600; font-size: 15pt; line-height: 1.16; color: var(--ink);
}
.nsh .hr { border-top: 0.5pt solid var(--hair); margin-top: 7pt; }

/* Quad grid ------------------------------------------------------------ */
/* Deliberate gutter, so these read as separate cards rather than as one
   table. Green rule on the top edge, tan hairlines on the other three. */
.quad { display: grid; gap: 9pt; margin: 8pt 0; }
.quad .q {
  break-inside: avoid; page-break-inside: avoid;
  border-top: 1.5pt solid var(--green);
  border-left: 0.5pt solid var(--tan); border-right: 0.5pt solid var(--tan);
  border-bottom: 0.5pt solid var(--tan);
  padding: 9pt 11pt 10pt; min-width: 0;
}
.quad .q .n {
  font-family: var(--serif);
  font-variation-settings: 'opsz' 72, 'wght' 400;
  font-weight: 400; font-size: 13pt; line-height: 1; color: var(--green);
  margin-bottom: 5pt;
}
.quad .q .t { font-weight: 700; font-size: 10pt; color: var(--ink); margin: 0 0 4pt; }
.quad .q .s { font-size: 8pt; line-height: 1.44; color: var(--muted); margin: 0 0 8pt; }
.quad .q .sl { font-size: 6.5pt; font-weight: 700; letter-spacing: 0.14em;
               text-transform: uppercase; color: var(--green-deep); margin: 0 0 3pt; }
.quad .q .d { font-size: 8pt; line-height: 1.42; color: var(--ink-alt); margin: 0; }

/* Highlight panel ------------------------------------------------------ */
.hl { padding: 11pt 13pt; margin: 10pt 0;
      break-inside: avoid; page-break-inside: avoid; }
.hl.cream { background: var(--cream); border: 0.75pt solid var(--green); }
.hl.muted { background: var(--card); }
.hl .eb { font-size: 6.5pt; font-weight: 700; letter-spacing: 0.15em;
          text-transform: uppercase; color: var(--green-deep); margin: 0 0 6pt; }
.hl p { font-size: 8.5pt; line-height: 1.5; color: var(--ink-alt); margin: 0 0 6pt; }
.hl p:last-child { margin-bottom: 0; }
.hl p b { color: var(--green-deep); font-weight: 700; }

/* Dark band, N columns ------------------------------------------------- */
.dbc { padding: 12pt 14pt 13pt; margin: 11pt 0;
       break-inside: avoid; page-break-inside: avoid; }
.dbc.green { background: var(--green-deep); }
.dbc.near_black { background: var(--near-black); }
.dbc .eb { font-size: 6.5pt; font-weight: 700; letter-spacing: 0.16em;
           text-transform: uppercase; color: #ffffff; margin: 0 0 9pt; }
.dbc .cols { display: grid; gap: 14pt; }
.dbc .cols > div { min-width: 0; }
.dbc .t { font-weight: 700; font-size: 8.5pt; color: #ffffff; margin: 0 0 3pt; }
.dbc .b { font-size: 7.5pt; line-height: 1.46; color: var(--tan); margin: 0; }

/* Pill row ------------------------------------------------------------- */
.pills { display: flex; flex-wrap: wrap; gap: 5pt; margin: 7pt 0; }
.pills span { border-radius: 999px; font-size: 7pt; font-weight: 600;
              letter-spacing: 0.05em; padding: 3pt 9pt; white-space: nowrap; }
.pills.outline span { border: 0.75pt solid var(--green); color: var(--green-deep); }
.pills.filled span { background: var(--green-deep); color: #ffffff; }

/* Flow diagram --------------------------------------------------------- */
.flow { margin: 10pt 0; break-inside: avoid; page-break-inside: avoid; }
.flow .rail { position: relative; display: grid; }
.flow .rail .line { position: absolute; top: 0.135in; height: 0;
                    border-top: 0.75pt solid var(--tan); z-index: 0; }
.flow .node { position: relative; z-index: 1; text-align: center;
              padding: 0 4pt; min-width: 0; }
.flow .dot {
  width: 0.27in; height: 0.27in; border-radius: 50%%; margin: 0 auto 6pt;
  background: var(--green-deep); color: #ffffff;
  font-size: 8pt; font-weight: 700; line-height: 1;
  display: flex; align-items: center; justify-content: center;
}
.flow .l { font-weight: 700; font-size: 8pt; line-height: 1.26; color: var(--ink); }
.flow .s { font-size: 6.5pt; line-height: 1.34; color: var(--muted); margin-top: 3pt; }
.flow .cap { font-size: 7pt; color: var(--muted); text-align: center;
             margin-top: 9pt; font-style: italic;
             break-before: avoid; page-break-before: avoid; }
.flow .boxes { display: flex; align-items: stretch; }
.flow .boxes .bx {
  flex: 1 1 0; min-width: 0;
  border: 0.5pt solid var(--hair); border-top: 1.25pt solid var(--green);
  padding: 7pt 8pt 8pt;
}
.flow .boxes .arw { flex: 0 0 auto; align-self: center; padding: 0 5pt;
                    color: var(--green); font-size: 12pt; font-weight: 700;
                    line-height: 1; }
.flow .boxes .st { font-size: 6pt; font-weight: 700; letter-spacing: 0.14em;
                   text-transform: uppercase; color: var(--green); margin: 0 0 4pt; }

/* Callout — three variants, and they must stay three ------------------- */
.callout { display: flex; gap: 9pt; padding: 9pt 11pt; margin: 9pt 0;
           break-inside: avoid; page-break-inside: avoid; }
.callout .ico {
  flex: 0 0 auto; width: 0.17in; height: 0.17in; border-radius: 50%%;
  display: flex; align-items: center; justify-content: center;
  font-size: 7.5pt; font-weight: 700; line-height: 1; margin-top: 0.6pt;
}
.callout .bd { flex: 1 1 auto; min-width: 0; }
.callout .t { font-weight: 700; font-size: 8.5pt; margin: 0 0 3pt; }
.callout p { font-size: 8pt; line-height: 1.46; margin: 0; }
.callout.warning { background: var(--warn-bg); border-left: 2pt solid var(--warn); }
.callout.warning .ico { background: var(--warn); color: #3B1F06; }
.callout.warning .t { color: var(--warn-ink); }
.callout.warning p { color: var(--ink-alt); }
.callout.info { background: var(--card); border-left: 2pt solid var(--muted); }
.callout.info .ico { background: var(--muted); color: #ffffff; }
.callout.info .t { color: var(--ink); }
.callout.info p { color: var(--muted); }
.callout.success { background: var(--green-pale); border-left: 2pt solid var(--green); }
.callout.success .ico { background: var(--mint); color: var(--green-deep); }
.callout.success .t { color: var(--green-deep); }
.callout.success p { color: var(--green-dark); }

/* Figure -- a product screenshot and its caption ----------------------- */
.fig { margin: 9pt 0 11pt; break-inside: avoid; page-break-inside: avoid; }
.fig img { display: block; width: 100%%; height: auto; }
.fig.framed img { border: 0.5pt solid var(--hair); }
.fig figcaption { font-size: 7pt; line-height: 1.4; color: var(--muted);
                  margin: 4pt 0 0; }
.fig figcaption .n { font-weight: 700; color: var(--green-deep);
                     letter-spacing: 0.06em; text-transform: uppercase;
                     font-size: 6.5pt; margin-right: 4pt; }

/* Step row -- numbered caption left, screenshot right ------------------ */
.steprow { display: grid; grid-template-columns: 29%% minmax(0, 1fr); gap: 0 16pt;
           padding: 11pt 0 12pt; border-top: 0.5pt solid var(--hair);
           break-inside: avoid; page-break-inside: avoid; align-items: start; }
.steprow.first { border-top: none; padding-top: 4pt; }
.steprow .lead .num { font-family: var(--serif); font-size: 20pt; line-height: 1;
                      color: var(--green); font-variation-settings: 'wght' 300;
                      margin: 0 0 4pt; }
.steprow .lead .t { font-family: var(--serif); font-size: 11pt; line-height: 1.2;
                    color: var(--green-deep); margin: 0 0 4pt; }
.steprow .lead .b { font-size: 8pt; line-height: 1.45; color: var(--muted); margin: 0; }
.steprow .shot { min-width: 0; }
.steprow .shot img { display: block; width: 100%%; height: auto;
                     border: 0.5pt solid var(--hair); }

/* Code block ----------------------------------------------------------- */
.code { background: var(--code-bg); border: 0.5pt solid var(--code-edge);
        border-left: 2pt solid var(--green-deep); padding: 8pt 10pt; margin: 8pt 0; }
.code .lang { font-size: 6pt; font-weight: 700; letter-spacing: 0.14em;
              text-transform: uppercase; color: var(--muted); margin: 0 0 5pt; }
/* One div per source line, with a hanging indent: leading whitespace is
   preserved and a wrapped line is visibly a continuation rather than
   masquerading as a new line at a different indent level. */
.code .ln {
  font-family: var(--mono); font-size: 7.5pt; line-height: 1.5; color: var(--ink);
  white-space: pre-wrap; overflow-wrap: break-word; word-break: break-word;
  padding-left: 1.8em; text-indent: -1.8em;
}
.code-cap { font-size: 7pt; color: var(--muted); margin: 4pt 0 8pt;
            break-before: avoid; page-break-before: avoid; }
.tok-k { color: var(--green-deep); font-weight: 600; }
.tok-s { color: var(--ink-alt); }
.tok-n { color: var(--green); }
.tok-p { color: var(--muted); }

/* Method badge --------------------------------------------------------- */
.mb { display: flex; align-items: center; gap: 8pt; margin: 11pt 0 6pt;
      break-inside: avoid; page-break-inside: avoid;
      break-after: avoid; page-break-after: avoid; }
.mb .v { flex: 0 0 auto; font-family: var(--mono); font-size: 7.5pt;
         font-weight: 700; letter-spacing: 0.08em; color: #ffffff;
         padding: 3pt 8pt; border-radius: 2.5pt; }
.mb .p { font-family: var(--mono); font-size: 9pt; color: var(--ink);
         min-width: 0; overflow-wrap: break-word; word-break: break-all; }
.mb .v.post { background: var(--green); }
.mb .v.get { background: var(--navy); }
.mb .v.put, .mb .v.patch { background: var(--warn); color: #3B1F06; }
.mb .v.delete { background: var(--red); }
.mb .v.other { background: var(--muted); }

/* Field table ---------------------------------------------------------- */
table.fields { width: 100%%; border-collapse: collapse; table-layout: fixed;
               margin: 7pt 0 10pt; }
table.fields th { background: var(--green-deep); color: #ffffff; font-size: 6.5pt;
                  font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase;
                  text-align: left; padding: 5.5pt 8pt; }
table.fields td { border-bottom: 0.5pt solid var(--hair); padding: 6pt 8pt;
                  font-size: 8pt; line-height: 1.42; vertical-align: top;
                  color: var(--muted); overflow-wrap: break-word;
                  word-break: break-word; }
table.fields .chip { font-family: var(--mono); font-size: 7.5pt;
                     color: var(--green-deep); background: var(--card);
                     border: 0.5pt solid var(--hair); border-radius: 2pt;
                     padding: 1pt 4pt; }
table.fields .ty { font-family: var(--mono); font-size: 7.5pt; color: var(--ink-alt); }
table.fields .req { font-size: 6.5pt; font-weight: 700; letter-spacing: 0.09em;
                    text-transform: uppercase; color: var(--red); }
table.fields .opt { font-size: 6.5pt; font-weight: 700; letter-spacing: 0.09em;
                    text-transform: uppercase; color: var(--muted); }
""" % dict(PALETTE, cover_gradient=COVER_GRADIENT)


# --------------------------------------------------------------------------
# Block rendering
# --------------------------------------------------------------------------


def esc(text) -> str:
    """HTML-escape, then honour **bold** spans."""
    out = html_mod.escape(str(text))
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)


def _cols(n):
    return "grid-template-columns: repeat({}, 1fr);".format(n)


#: Light JSON tokenizer for ``code_block``. Four steps, all of which stay
#: legible in greyscale print: keys, string values, literals, punctuation.
_JSON_TOKEN = re.compile(
    r'(?P<str>"(?:[^"\\]|\\.)*")'
    r'|(?P<num>-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)'
    r'|(?P<lit>\b(?:true|false|null)\b)'
    r'|(?P<punct>[{}\[\],:])')


def _json_line(line: str) -> str:
    """Escape one JSON line, wrapping its tokens in colour spans."""
    out, pos = [], 0
    for match in _JSON_TOKEN.finditer(line):
        if match.start() > pos:
            out.append(html_mod.escape(line[pos:match.start()]))
        kind = match.lastgroup
        if kind == "str":
            klass = "tok-k" if line[match.end():].lstrip().startswith(":") else "tok-s"
        elif kind in ("num", "lit"):
            klass = "tok-n"
        else:
            klass = "tok-p"
        out.append('<span class="{}">{}</span>'.format(klass, html_mod.escape(match.group(0))))
        pos = match.end()
    out.append(html_mod.escape(line[pos:]))
    return "".join(out)


def _code_lines(text, language):
    """One div per source line, so wrapping cannot corrupt indentation."""
    colour = (language or "").strip().lower() in ("json", "jsonc")
    rows = []
    for line in str(text).expandtabs(2).split("\n"):
        body = _json_line(line) if colour else html_mod.escape(line)
        rows.append('<div class="ln">{}</div>'.format(body or "&nbsp;"))
    return "".join(rows)


def _labelled(items, label_key="label", sub_key="sub"):
    """Normalise a list of ``str`` or ``{label, sub}`` into (label, sub) pairs."""
    out = []
    for item in items:
        if isinstance(item, dict):
            out.append((item.get(label_key, ""), item.get(sub_key, "")))
        else:
            out.append((item, ""))
    return out


def render_block(block, assets):
    btype = block["type"]

    if btype == "masthead":
        tag = block.get("tag", "")
        right = '<div class="tag">{}</div>'.format(esc(tag)) if tag else "<div></div>"
        return '<div class="masthead"><img src="{}" alt="FurtherAI">{}</div>'.format(
            assets["logo"], right)

    if btype == "title":
        parts = ['<h1 class="title">{}</h1>'.format(esc(block["text"]))]
        if block.get("deck"):
            parts.append('<p class="deck">{}</p>'.format(esc(block["deck"])))
        if block.get("rule", True):
            parts.append('<div class="rule"></div>')
        return "".join(parts)

    if btype == "section":
        parts = ['<h2 class="section">{}</h2>'.format(esc(block["text"]))]
        if block.get("deck"):
            parts.append('<p class="deck">{}</p>'.format(esc(block["deck"])))
        return "".join(parts)

    if btype == "eyebrow":
        return '<div class="eyebrow">{}</div>'.format(esc(block["text"]))

    if btype == "paragraph":
        return '<p class="intro">{}</p>'.format(esc(block["text"]))

    if btype == "cards":
        cols = block.get("columns", 2)
        cells = []
        for idx, card in enumerate(block["items"], 1):
            title = card.get("title", "")
            if block.get("numbered", True):
                title = "{}.&nbsp; {}".format(idx, esc(title))
            else:
                title = esc(title)
            inner = ['<div class="t">{}</div>'.format(title)]
            if card.get("body"):
                inner.append('<p class="b">{}</p>'.format(esc(card["body"])))
            if card.get("note"):
                label = card.get("note_label", "")
                note = ("<b>{}</b> ".format(esc(label)) if label else "") + esc(card["note"])
                inner.append('<p class="note">{}</p>'.format(note))
            cells.append('<div class="cell card">{}</div>'.format("".join(inner)))
        return '<div class="grid" style="{}">{}</div>'.format(_cols(cols), "".join(cells))

    if btype == "pipeline":
        cells = []
        for step in block["steps"]:
            klass = "cell" + (" accent" if step.get("accent") else "")
            inner = '<div class="l">{}</div>'.format(esc(step.get("label", "")))
            if step.get("sub"):
                inner += '<div class="s">{}</div>'.format(esc(step["sub"]))
            cells.append('<div class="{}">{}</div>'.format(klass, inner))
        return '<div class="grid pipeline" style="{}">{}</div>'.format(
            _cols(len(block["steps"])), "".join(cells))

    if btype == "table":
        head = "".join("<th>{}</th>".format(esc(h)) for h in block["headers"])
        rows = "".join(
            "<tr>{}</tr>".format("".join("<td>{}</td>".format(esc(c)) for c in row))
            for row in block["rows"])
        return ('<table class="data"><thead><tr>{}</tr></thead>'
                "<tbody>{}</tbody></table>").format(head, rows)

    if btype == "stats":
        cells = "".join(
            '<div><div class="n">{}</div><div class="l">{}</div></div>'.format(
                esc(s.get("value", "")), esc(s.get("label", "")))
            for s in block["items"])
        return '<div class="stats" style="{}">{}</div>'.format(
            _cols(len(block["items"])), cells)

    if btype == "panel":
        parts = []
        if block.get("title"):
            parts.append("<h3>{}</h3>".format(esc(block["title"])))
        for band in block.get("bands", []):
            kind = band.get("kind", "cells")
            if kind == "caps":
                parts.append('<div class="caps">{}</div>'.format(esc(band["text"])))
            elif kind == "bar":
                parts.append('<div class="bar">{}</div>'.format(esc(band["text"])))
            else:
                dark = " dark" if band.get("dark") else ""
                boxes = []
                for item in band["items"]:
                    if isinstance(item, dict):
                        text = esc(item.get("label", ""))
                        if item.get("sub"):
                            text += '<span class="sub">{}</span>'.format(esc(item["sub"]))
                    else:
                        text = esc(item)
                    boxes.append('<div class="box{}">{}</div>'.format(dark, text))
                parts.append('<div class="row" style="{}">{}</div>'.format(
                    _cols(len(band["items"])), "".join(boxes)))
        return '<div class="panel">{}</div>'.format("".join(parts))

    if btype == "band":
        cols = "".join(
            '<div class="col"><div class="t">{}</div><p class="b">{}</p></div>'.format(
                esc(c.get("title", "")), esc(c.get("body", "")))
            for c in block["items"])
        return '<div class="band" style="{}">{}</div>'.format(
            _cols(len(block["items"])), cols)

    if btype == "footer":
        items = block.get("items", ["furtherai.com"])
        chunks = []
        for idx, item in enumerate(items):
            klass = ' class="last"' if idx == len(items) - 1 else ""
            chunks.append("<span{}>{}</span>".format(klass, esc(item)))
        joined = '<span class="sep">|</span>'.join(chunks)
        return '<div class="footer">{}</div>'.format(joined)

    # ---- long-technical-document blocks ---------------------------------

    if btype == "dark_cover":
        parts = ['<img class="mark" src="{}" alt="FurtherAI">'.format(assets["logo_white"])]
        lower = ['<h1>{}</h1>'.format(esc(block["title"]))]
        if block.get("subtitle"):
            lower.append('<p class="sub">{}</p>'.format(esc(block["subtitle"])))
        if block.get("pills"):
            lower.append('<div class="cpills">{}</div>'.format(
                "".join("<span>{}</span>".format(esc(p)) for p in block["pills"])))
        if block.get("meta"):
            lower.append('<div class="meta">{}</div>'.format(esc(block["meta"])))
        if block.get("confidential"):
            lower.append('<div class="conf">{}</div>'.format(esc(block["confidential"])))
        parts.append('<div class="lower">{}</div>'.format("".join(lower)))
        return '<div class="cover-fill">{}</div>'.format("".join(parts))

    if btype == "toc_dotted":
        rows = []
        for idx, item in enumerate(block["items"], 1):
            if isinstance(item, dict):
                number, title = item.get("number", str(idx)), item.get("title", "")
            else:
                number, title = str(idx), item
            rows.append('<div class="row"><div class="n">{}</div>'
                        '<div class="t">{}</div><div class="dots"></div></div>'.format(
                            esc(number), esc(title)))
        return '<div class="toc">{}</div>'.format("".join(rows))

    if btype == "numbered_section_header":
        rule = '<div class="hr"></div>' if block.get("rule", True) else ""
        return ('<div class="nsh"><div class="hd"><div class="n">{}</div>'
                '<div class="t">{}</div></div>{}</div>').format(
                    esc(block["number"]), esc(block["title"]), rule)

    if btype == "quad_grid":
        cards = []
        for item in block["items"]:
            inner = []
            if item.get("number"):
                inner.append('<div class="n">{}</div>'.format(esc(item["number"])))
            if item.get("title"):
                inner.append('<div class="t">{}</div>'.format(esc(item["title"])))
            if item.get("summary"):
                inner.append('<p class="s">{}</p>'.format(esc(item["summary"])))
            if item.get("sub_label"):
                inner.append('<div class="sl">{}</div>'.format(esc(item["sub_label"])))
            if item.get("detail"):
                inner.append('<p class="d">{}</p>'.format(esc(item["detail"])))
            cards.append('<div class="q">{}</div>'.format("".join(inner)))
        return '<div class="quad" style="{}">{}</div>'.format(
            _cols(block.get("columns", 2)), "".join(cards))

    if btype == "highlight_panel":
        variant = block.get("variant", "cream")
        parts = []
        if block.get("eyebrow"):
            parts.append('<div class="eb">{}</div>'.format(esc(block["eyebrow"])))
        parts += ["<p>{}</p>".format(esc(p)) for p in block["paragraphs"]]
        return '<div class="hl {}">{}</div>'.format(variant, "".join(parts))

    if btype == "dark_band_columns":
        parts = []
        if block.get("eyebrow"):
            parts.append('<div class="eb">{}</div>'.format(esc(block["eyebrow"])))
        cols = "".join(
            '<div><div class="t">{}</div><p class="b">{}</p></div>'.format(
                esc(c.get("title", "")), esc(c.get("body", "")))
            for c in block["columns"])
        parts.append('<div class="cols" style="{}">{}</div>'.format(
            _cols(len(block["columns"])), cols))
        return '<div class="dbc {}">{}</div>'.format(
            block.get("tone", "green"), "".join(parts))

    if btype == "pill_row":
        chips = "".join("<span>{}</span>".format(esc(i)) for i in block["items"])
        return '<div class="pills {}">{}</div>'.format(
            block.get("variant", "outline"), chips)

    if btype == "flow_diagram":
        nodes = _labelled(block["nodes"])
        style = block.get("style", "nodes")
        connector = block.get("connector", True)
        if style == "boxes":
            cells = []
            for idx, (label, sub) in enumerate(nodes, 1):
                inner = ['<div class="st">Step {}</div>'.format(idx),
                         '<div class="l">{}</div>'.format(esc(label))]
                if sub:
                    inner.append('<div class="s">{}</div>'.format(esc(sub)))
                cells.append('<div class="bx">{}</div>'.format("".join(inner)))
            sep = '<div class="arw">&#8250;</div>' if connector else ""
            body = '<div class="boxes">{}</div>'.format(sep.join(cells))
        else:
            # The rail is drawn from the first node's centre to the last one's,
            # i.e. inset by half a column on each side, and sits behind the
            # opaque dots.
            inset = 100.0 / (2 * max(len(nodes), 1))
            line = ('<div class="line" style="left: {0:.4f}%; right: {0:.4f}%;">'
                    "</div>").format(inset) if connector and len(nodes) > 1 else ""
            cells = []
            for idx, (label, sub) in enumerate(nodes, 1):
                inner = ['<div class="dot">{}</div>'.format(idx),
                         '<div class="l">{}</div>'.format(esc(label))]
                if sub:
                    inner.append('<div class="s">{}</div>'.format(esc(sub)))
                cells.append('<div class="node">{}</div>'.format("".join(inner)))
            body = '<div class="rail" style="{}">{}{}</div>'.format(
                _cols(len(nodes)), line, "".join(cells))
        caption = ('<div class="cap">{}</div>'.format(esc(block["caption"]))
                   if block.get("caption") else "")
        return '<div class="flow">{}{}</div>'.format(body, caption)

    if btype == "figure":
        img_path = Path(block["src"]).expanduser()
        if not img_path.is_file():
            raise ValueError("figure src not found: {}".format(img_path))
        _warn_if_illegible(img_path)
        classes = "fig" if block.get("framed", True) else "fig plain"
        style = ""
        width = block.get("width")
        if width:
            style = ' style="width:{};margin-left:auto;margin-right:auto"'.format(
                esc(str(width)))
        cap = ""
        if block.get("caption") or block.get("label"):
            label = ('<span class="n">{}</span>'.format(esc(block["label"]))
                     if block.get("label") else "")
            cap = "<figcaption>{}{}</figcaption>".format(
                label, esc(block.get("caption", "")))
        return '<figure class="{}"{}><img src="{}" alt="{}">{}</figure>'.format(
            "framed " + classes if block.get("framed", True) else classes,
            style, _image_uri(img_path),
            html_mod.escape(strip_markers(block.get("caption", "")) or "figure"),
            cap)

    if btype == "step_figure":
        img_path = Path(block["src"]).expanduser()
        if not img_path.is_file():
            raise ValueError("step_figure src not found: {}".format(img_path))
        _warn_if_illegible(img_path)
        lead = []
        if block.get("number"):
            lead.append('<div class="num">{}</div>'.format(esc(block["number"])))
        if block.get("title"):
            lead.append('<div class="t">{}</div>'.format(esc(block["title"])))
        if block.get("body"):
            lead.append('<div class="b">{}</div>'.format(esc(block["body"])))
        style = ""
        if block.get("width"):
            style = ' style="width:{}"'.format(esc(str(block["width"])))
        return ('<div class="steprow{}"><div class="lead">{}</div>'
                '<div class="shot"><img src="{}" alt="{}"{}></div></div>').format(
                    " first" if block.get("first") else "", "".join(lead),
                    _image_uri(img_path),
                    html_mod.escape(strip_markers(block.get("title", "")) or "step"),
                    style)

    if btype == "callout":
        variant = block.get("variant", "info")
        glyph = {"warning": "!", "success": "&#10003;", "info": "i"}.get(variant, "i")
        inner = []
        if block.get("title"):
            inner.append('<div class="t">{}</div>'.format(esc(block["title"])))
        inner.append("<p>{}</p>".format(esc(block["text"])))
        return ('<div class="callout {}"><div class="ico">{}</div>'
                '<div class="bd">{}</div></div>').format(variant, glyph, "".join(inner))

    if btype == "code_block":
        parts = []
        if block.get("language"):
            parts.append('<div class="lang">{}</div>'.format(esc(block["language"])))
        parts.append(_code_lines(block["text"], block.get("language")))
        out = '<div class="code">{}</div>'.format("".join(parts))
        if block.get("caption"):
            out += '<p class="code-cap">{}</p>'.format(esc(block["caption"]))
        return out

    if btype == "method_badge":
        verb = str(block["method"]).strip().upper()
        klass = verb.lower() if verb.lower() in (
            "post", "get", "put", "patch", "delete") else "other"
        return ('<div class="mb"><span class="v {}">{}</span>'
                '<span class="p">{}</span></div>').format(
                    klass, esc(verb), esc(block["path"]))

    if btype == "field_table":
        headers = block.get("headers", ["Field", "Type", "Required", "Description"])
        head = "".join("<th>{}</th>".format(esc(h)) for h in headers)
        body = []
        for row in block["rows"]:
            required = bool(row.get("required"))
            tag = ('<span class="req">Required</span>' if required
                   else '<span class="opt">Optional</span>')
            body.append(
                '<tr><td><span class="chip">{}</span></td>'
                '<td><span class="ty">{}</span></td><td>{}</td><td>{}</td></tr>'.format(
                    esc(row.get("field", "")), esc(row.get("type", "")), tag,
                    esc(row.get("description", ""))))
        return ('<table class="fields"><colgroup><col style="width:27%">'
                '<col style="width:16%"><col style="width:13%">'
                '<col style="width:44%"></colgroup>'
                "<thead><tr>{}</tr></thead><tbody>{}</tbody></table>").format(
                    head, "".join(body))

    raise ValueError("unhandled block type: {}".format(btype))


# --------------------------------------------------------------------------
# Spec validation
# --------------------------------------------------------------------------

BLOCKS = {
    "masthead": {"required": [], "optional": {"tag": ""},
                 "doc": "Logo top-left, optional letterspaced caps tag top-right."},
    "title": {"required": ["text"], "optional": {"deck": "", "rule": True},
              "doc": "Fraunces 20pt title, optional gray deck line, dark-green "
                     "full-width hairline rule under it."},
    "section": {"required": ["text"], "optional": {"deck": ""},
                "doc": "Fraunces 13.5pt section title with an optional deck line."},
    "eyebrow": {"required": ["text"], "optional": {},
                "doc": "Letterspaced all-caps dark-green 7.5pt label above a block."},
    "paragraph": {"required": ["text"], "optional": {},
                  "doc": "Body paragraph at 9pt. Supports **bold** spans."},
    "cards": {"required": ["items"], "optional": {"columns": 2, "numbered": True},
              "doc": "Hairline-bordered card grid, no fill. Each item takes "
                     "title / body / note / note_label; note_label renders bold "
                     "dark green ahead of the note (the 'Case Study' run)."},
    "pipeline": {"required": ["steps"], "optional": {},
                 "doc": "Single-row hairline grid. Each step takes label / sub / "
                        "accent:true (accent tints that cell's text green)."},
    "table": {"required": ["headers", "rows"], "optional": {},
              "doc": "Dark-green header band with white all-caps labels; body "
                     "rows separated by hairline rules, no vertical rules."},
    "stats": {"required": ["items"], "optional": {},
              "doc": "Full-width dark-green band of stats; each item takes "
                     "value / label."},
    "panel": {"required": ["bands"], "optional": {"title": ""},
              "doc": "Full-width dark-green architecture panel. title renders "
                     "centered in Fraunces italic white. bands is a list of "
                     "{kind: 'caps'|'cells'|'bar'}: caps = tiny letterspaced "
                     "label, bar = full-width orange bar, cells = a row of "
                     "boxes (dark:true for darker green; each item is a string "
                     "or {label, sub})."},
    "band": {"required": ["items"], "optional": {},
             "doc": "Tinted #F7F7F5 column band; each item takes title / body."},
    "footer": {"required": [], "optional": {"items": ["furtherai.com"]},
               "doc": "Centered credit line above a hairline rule. items are "
                      "separated by a pipe; the last one renders green. "
                      "**bold** spans are honoured."},
    "page_break": {"required": [], "optional": {},
                   "doc": "Divider between pages. Each page is laid out as its "
                          "own printable sheet, so a footer at the end of a page "
                          "sits on the sheet's bottom edge."},

    # -- long-technical-document blocks ---------------------------------
    "dark_cover": {"required": ["title"],
                   "optional": {"subtitle": "", "pills": [], "meta": "",
                                "confidential": ""},
                   "doc": "Full-bleed dark-green gradient cover for a long "
                          "technical document — BRAND.md's cover recipe, white "
                          "wordmark top-left, Fraunces Light 38pt title bottom-"
                          "left. pills are outlined caps chips (the doc's "
                          "topics); meta is the version + date line; "
                          "confidential is a small caps line under a hairline. "
                          "Put it alone on its own page: it prints on a "
                          "zero-margin named page, so anything after it on the "
                          "same page is pushed off the sheet."},
    "toc_dotted": {"required": ["items"], "optional": {},
                   "doc": "Contents page — green numeral, bold title, thin "
                          "dotted rule trailing each row. items are strings or "
                          "{number, title}. Deliberately carries NO page "
                          "numbers; adding them is a change, not a fix."},
    "numbered_section_header": {"required": ["number", "title"],
                                "optional": {"rule": True},
                                "doc": "Large green Fraunces numeral + bold "
                                       "Fraunces heading over a full-width "
                                       "hairline rule. rule:false drops the "
                                       "rule."},
    "quad_grid": {"required": ["items"], "optional": {"columns": 2},
                  "doc": "Hairline cards in a gapped grid — green rule on the "
                         "top edge, tan hairlines on the sides and bottom, and "
                         "a real gutter between cards so they don't read as a "
                         "table. Each item takes number / title / summary / "
                         "sub_label / detail; sub_label is the letterspaced "
                         "caps green run (e.g. 'WHAT YOU GET') above detail."},
    "highlight_panel": {"required": ["paragraphs"],
                        "optional": {"eyebrow": "", "variant": "cream"},
                        "doc": "Boxed emphasis panel. variant cream = #F5F0EB "
                               "fill with a green border; muted = plain grey, "
                               "no border. paragraphs is a list of strings and "
                               "**bold** spans render dark green."},
    "dark_band_columns": {"required": ["columns"],
                          "optional": {"eyebrow": "", "tone": "green"},
                          "doc": "Full-width dark band with a white all-caps "
                                 "eyebrow over N equal columns; each column "
                                 "takes title / body. tone green = #074B40, "
                                 "near_black = #162C28."},
    "pill_row": {"required": ["items"], "optional": {"variant": "outline"},
                 "doc": "Inline row of rounded chips — compliance marks, "
                        "integrations, topics. variant outline = green rule on "
                        "paper, filled = solid dark green with white text."},
    "flow_diagram": {"required": ["nodes"],
                     "optional": {"style": "nodes", "connector": True,
                                  "caption": ""},
                     "doc": "Left-to-right process diagram. nodes are strings "
                            "or {label, sub}; the sub-caption renders in both "
                            "styles. style nodes = numbered green circles on a "
                            "tan rail with captions below; boxes = STEP-N cards "
                            "joined by chevrons. connector:false drops the rail "
                            "or the chevrons. caption is an italic line under "
                            "the diagram."},
    "step_figure": {"required": ["src"],
                    "optional": {"number": "", "title": "", "body": "",
                                 "width": "", "first": False},
                    "doc": "One row of a screenshot walkthrough: a green Fraunces "
                           "numeral, a short title and a one-line description in a "
                           "30% left column, the screenshot in the 70% right "
                           "column. Rows are separated by a hairline and never "
                           "split across a page, so several fit on one sheet. "
                           "width ('60%') shrinks a tall screenshot inside its "
                           "column; first:true drops the leading rule."},
    "figure": {"required": ["src"],
               "optional": {"caption": "", "label": "", "framed": True,
                            "width": ""},
               "doc": "A product screenshot, embedded as a data URI. src is a "
                      "path to a .png/.jpg; caption prints 7pt grey beneath "
                      "and supports **bold**; label prefixes it with a small "
                      "green caps run ('Figure 3'). framed:false drops the "
                      "hairline border; width ('70%') narrows a tall crop. "
                      "Warns when the source is wider than 1900px, which "
                      "prints UI text under 4pt."},
    "callout": {"required": ["text"], "optional": {"variant": "info", "title": ""},
                "doc": "Iconned note. Three visually distinct variants, and "
                       "they carry meaning: warning = mango icon on #FEF9F0 "
                       "cream, info = neutral grey, success = mint icon on pale "
                       "green. Never collapse them to one box."},
    "code_block": {"required": ["text"], "optional": {"language": "", "caption": ""},
                   "doc": "Monospace on a #F4F4F4 box with a green left rule. "
                          "Whitespace is preserved and each source line wraps "
                          "with a hanging indent, so wrapping cannot corrupt "
                          "indentation. language 'json' adds light token "
                          "colouring; anything else renders plain."},
    "method_badge": {"required": ["method", "path"], "optional": {},
                     "doc": "Inline coloured HTTP verb pill + monospace path. "
                            "POST green, GET navy, PUT/PATCH mango, DELETE "
                            "sinopia; any other verb renders grey."},
    "field_table": {"required": ["rows"], "optional": {"headers": []},
                    "doc": "Request/response field reference — dark-green "
                           "header row, field names as monospace chips, a "
                           "sinopia 'Required' or muted 'Optional' tag. rows "
                           "take field / type / required (bool) / description. "
                           "headers defaults to Field / Type / Required / "
                           "Description."},
}

_LIST_FIELDS = {
    "cards": "items", "pipeline": "steps", "stats": "items",
    "band": "items", "panel": "bands",
    "toc_dotted": "items", "quad_grid": "items", "pill_row": "items",
    "highlight_panel": "paragraphs", "dark_band_columns": "columns",
    "flow_diagram": "nodes", "field_table": "rows",
}

#: Blocks whose list field must hold objects, not bare strings.
_OBJECT_ITEMS = {
    "quad_grid": ("number", "title", "summary", "sub_label", "detail"),
    "dark_band_columns": ("title", "body"),
    "field_table": ("field", "type", "required", "description"),
}

#: Blocks with a closed set of values for one field.
_ENUMS = {
    "highlight_panel": ("variant", ("cream", "muted")),
    "dark_band_columns": ("tone", ("green", "near_black")),
    "pill_row": ("variant", ("outline", "filled")),
    "flow_diagram": ("style", ("nodes", "boxes")),
    "callout": ("variant", ("info", "warning", "success")),
}


def strip_markers(value):
    """Drop the ``_``-prefixed scaffold marker keys, recursively.

    ``--scaffold`` stamps ``_slot`` on every block and ``_include_when`` on the
    conditional and repeated ones, so a caller can see which slots are safe to
    delete. They are annotations, never content — nothing renders them.
    """
    if isinstance(value, dict):
        return {k: strip_markers(v) for k, v in value.items()
                if not (isinstance(k, str) and k.startswith("_"))}
    if isinstance(value, list):
        return [strip_markers(v) for v in value]
    return value


def validate(spec):
    errors = []
    if not isinstance(spec, dict):
        return ["spec must be a JSON object with a 'blocks' array"]
    if "margins" in spec:
        errors.extend(_validate_margins(spec["margins"]))
    blocks = spec.get("blocks")
    if blocks is None:
        return ["spec has no 'blocks' array"]
    if not isinstance(blocks, list):
        return ["'blocks' must be an array"]
    if not blocks:
        errors.append("'blocks' is empty — nothing to render")

    for idx, block in enumerate(blocks):
        where = "block[{}]".format(idx)
        if not isinstance(block, dict):
            errors.append("{}: must be an object, got {}".format(where, type(block).__name__))
            continue
        btype = block.get("type")
        if btype is None:
            errors.append("{}: missing 'type'".format(where))
            continue
        if btype not in BLOCKS:
            errors.append("{}: unknown type {!r} — run --list-blocks for the "
                          "supported set".format(where, btype))
            continue
        where = "{} ({})".format(where, btype)
        schema = BLOCKS[btype]
        for field in schema["required"]:
            if field not in block:
                errors.append("{}: missing required field {!r}".format(where, field))
        known = set(schema["required"]) | set(schema["optional"]) | {"type"}
        for field in block:
            if field not in known:
                errors.append("{}: unknown field {!r} (known: {})".format(
                    where, field, ", ".join(sorted(known - {"type"})) or "none"))

        list_field = _LIST_FIELDS.get(btype)
        if list_field and list_field in block:
            value = block[list_field]
            if not isinstance(value, list) or not value:
                errors.append("{}: {!r} must be a non-empty array".format(where, list_field))
            elif btype in _OBJECT_ITEMS:
                for i_idx, item in enumerate(value):
                    if not isinstance(item, dict):
                        errors.append("{}: {}[{}] must be an object with {}".format(
                            where, list_field, i_idx,
                            ", ".join(_OBJECT_ITEMS[btype])))

        if btype in _ENUMS:
            field, allowed = _ENUMS[btype]
            if field in block and block[field] not in allowed:
                errors.append("{}: {!r} must be one of {}".format(
                    where, field, "/".join(allowed)))
        if btype == "pill_row" and isinstance(block.get("items"), list):
            for i_idx, item in enumerate(block["items"]):
                if isinstance(item, (dict, list)):
                    errors.append("{}: items[{}] must be a string".format(where, i_idx))
        if btype == "highlight_panel" and isinstance(block.get("paragraphs"), list):
            for i_idx, item in enumerate(block["paragraphs"]):
                if isinstance(item, (dict, list)):
                    errors.append("{}: paragraphs[{}] must be a string".format(
                        where, i_idx))
        if btype == "field_table" and block.get("headers"):
            if len(block["headers"]) != 4:
                errors.append("{}: 'headers' must have exactly 4 entries — the "
                              "table has fixed field/type/required/description "
                              "columns, got {}".format(where, len(block["headers"])))
        if btype == "field_table" and isinstance(block.get("rows"), list):
            for r_idx, row in enumerate(block["rows"]):
                if isinstance(row, dict) and "required" in row \
                        and not isinstance(row["required"], bool):
                    errors.append("{}: rows[{}].required must be true or "
                                  "false".format(where, r_idx))
        if btype == "table" and isinstance(block.get("headers"), list) \
                and isinstance(block.get("rows"), list):
            for r_idx, row in enumerate(block["rows"]):
                if not isinstance(row, list):
                    errors.append("{}: rows[{}] must be an array".format(where, r_idx))
                elif len(row) != len(block["headers"]):
                    errors.append("{}: rows[{}] has {} cells, headers has {}".format(
                        where, r_idx, len(row), len(block["headers"])))
        if btype == "panel" and isinstance(block.get("bands"), list):
            for b_idx, band in enumerate(block["bands"]):
                if not isinstance(band, dict):
                    errors.append("{}: bands[{}] must be an object".format(where, b_idx))
                    continue
                kind = band.get("kind", "cells")
                if kind not in ("caps", "cells", "bar"):
                    errors.append("{}: bands[{}].kind must be caps/cells/bar".format(
                        where, b_idx))
                elif kind in ("caps", "bar") and "text" not in band:
                    errors.append("{}: bands[{}] ({}) needs 'text'".format(where, b_idx, kind))
                elif kind == "cells" and not band.get("items"):
                    errors.append("{}: bands[{}] (cells) needs a non-empty "
                                  "'items'".format(where, b_idx))

    # The cover prints on a zero-margin named page and fills the whole sheet,
    # so anything sharing that page is pushed off it. Catch that here rather
    # than letting it render a blank page.
    sheet = []
    for idx, block in enumerate(list(blocks) + [{"type": "page_break"}]):
        btype = block.get("type") if isinstance(block, dict) else None
        if btype == "page_break":
            covers = [i for i, t in sheet if t == "dark_cover"]
            if covers and len(sheet) > 1:
                errors.append(
                    "block[{}] (dark_cover): must be alone on its page — it "
                    "fills the whole sheet, so the {} other block(s) on this "
                    "page print off it. Add a page_break.".format(
                        covers[0], len(sheet) - 1))
            sheet = []
        else:
            sheet.append((idx, btype))
    return errors


# --------------------------------------------------------------------------
# Type contracts
# --------------------------------------------------------------------------
#
# The slot plans below are transcribed from
# ``skills/doc/references/doc_types.md`` — slot name, block(s), req, and the
# "Include when" text, in the order that file gives them. That file is the
# spec; this is the machine-checkable copy of it. If they disagree, the .md
# wins and this needs fixing.
#
# req: yes  = always present
#      cond = only when the include-rule fires; dropping it is not an error
#      rep  = once per item in the source data; zero or more


def _slot(name, blocks, req, when):
    return {"name": name, "blocks": blocks, "req": req, "when": when}


TYPES = {
    "pricing_overview": {
        "purpose": "explain the four-part pricing model in the abstract — no "
                   "customer name, no figures",
        "length": "1 page",
        "notes": ["Margins run tighter than the docx types: L/R 0.5in, T/B 0.2in "
                  '(set "margins": {"top": 0.2, "bottom": 0.2}).',
                  "No customer name and no dollar figures anywhere. The moment a "
                  "real number appears, this became a pricing_summary (.docx).",
                  "The four components are Platform, Usage, Per-Seat, "
                  "Implementation, in that order."],
        "slots": [
            _slot("masthead", ["masthead"], "yes",
                  "always — logo left, PRICING OVERVIEW tag right"),
            _slot("title", ["title"], "yes", "always — plus an italic dek line"),
            _slot("intro", ["paragraph"], "yes", "always — one paragraph"),
            _slot("components", ["quad_grid"], "yes",
                  "always — the four fees, numbered 01–04, each with a "
                  "WHAT YOU GET sub-label"),
            _slot("example", ["highlight_panel"], "yes",
                  "always — what a typical engagement looks like, headline "
                  "figure bolded inline"),
            _slot("rationale", ["dark_band_columns"], "yes",
                  "always — why the model works, 3 columns"),
            _slot("cta", ["paragraph"], "yes",
                  "always — one centred italic closing line"),
        ],
    },
    "capability_onepager": {
        "purpose": "what FurtherAI does, for a segment or in general — "
                   "production workflows, how it works, proof",
        "length": "1–2 pages",
        "notes": ["Every workflow card needs a proof line. A card that only "
                  "describes a capability is marketing; a card with a measured "
                  "result is evidence.",
                  "Segment the whole document or none of it.",
                  "Named clients only where you know they're cleared — otherwise "
                  "the profile (Top-15 US broker).",
                  "The One_Pager architecture-stack diagram is a one-off. Don't "
                  "try to reconstruct it as a reusable block."],
        "slots": [
            _slot("masthead", ["masthead"], "yes", "always"),
            _slot("title", ["title"], "yes",
                  "always — segment eyebrow, H1, dek, hairline rule"),
            _slot("proof_stats", ["stats"], "yes", "always — 3 figures"),
            _slot("workflows", ["eyebrow", "cards"], "yes",
                  "always — production workflows, 2xN hairline grid"),
            _slot("example_flow", ["flow_diagram"], "cond",
                  "one workflow is worth walking end to end"),
            _slot("how", ["eyebrow", "pipeline"], "yes",
                  "always — the stage strip"),
            _slot("features", ["cards"], "cond",
                  "the platform capabilities need their own row (Evaluations, "
                  "Assistant, integrations)"),
            _slot("trust", ["dark_band_columns", "pill_row"], "yes",
                  "always — security and governance, compliance pills"),
            _slot("engagements", ["table"], "cond",
                  "you have named engagements cleared for external use"),
            _slot("cta", ["paragraph"], "yes", "always"),
            _slot("footer", ["footer"], "yes", "always — every page"),
        ],
    },
    "workflow_onepager": {
        "purpose": "one page on one workflow — what it does, how it's "
                   "configured, how it's performing",
        "length": "1 page",
        "notes": ["Gather with workflow_facts.py; don't reconstruct facts from "
                  "memory.",
                  "Skip empty sections entirely.",
                  "Use the UI's words: executions are Submissions, success is "
                  "completed, versions are 'Draft vN' and 'Live'."],
        "slots": [
            _slot("masthead", ["masthead"], "yes", "always"),
            _slot("title", ["title"], "yes",
                  "always — workflow name, truncated description at a word "
                  "boundary"),
            _slot("activity", ["stats"], "cond",
                  "there is 30-day submission activity to report"),
            _slot("steps", ["pipeline"], "yes",
                  "always — steps bucketed by category; cap the list and say "
                  "'+N more steps'"),
            _slot("config", ["cards"], "cond",
                  "there are configuration highlights worth surfacing"),
            _slot("integrations", ["pill_row"], "cond",
                  "the workflow has integrations"),
            _slot("ids", ["paragraph"], "yes",
                  "always — one small monospace line, never in body copy"),
            _slot("footer", ["footer"], "yes", "always"),
        ],
    },
    "screenshot_walkthrough": {
        "purpose": "teach a user to drive one workflow in the app, screen by "
                   "screen — a numbered caption beside each screenshot",
        "length": "2-4 pages, one row per screen",
        "notes": ["Screenshots come from fai:screenshots and its "
                  "shots/manifest.json; never drive a browser from this skill, "
                  "and never read a fact off a screenshot - facts come from "
                  "fai:wb and fai:workflow.",
                  "One row per screen, and fewer screens is better. Every row "
                  "is a paragraph someone has to read.",
                  "Keep body to one or two lines. The row's height is set by "
                  "the screenshot, and long copy just pushes the next step onto "
                  "another page.",
                  "Set width per row so three rows fit a sheet: a wide short "
                  "crop takes 100%, a tall panel 50-65%. Rows never split "
                  "across a page.",
                  "Crop the left sidebar out - it lists every workflow in the "
                  "org, including other clients' names.",
                  "first:true on the opening row drops its leading hairline.",
                  "Prose sections belong in a different type. If the document "
                  "needs tables of statuses, field references or limits, it is "
                  "a composed document, not a walkthrough."],
        "slots": [
            _slot("masthead", ["masthead"], "yes", "always — page 1 only"),
            _slot("title", ["title"], "yes",
                  "always — the workflow's name, with the workspace named in "
                  "the deck"),
            _slot("intro", ["paragraph"], "cond",
                  "the reader needs framing the steps cannot carry themselves"),
            _slot("step", ["step_figure"], "rep",
                  "once per screen, in the order a user meets them"),
            _slot("note", ["callout"], "rep",
                  "a caveat belongs with the step it qualifies"),
            _slot("footer", ["footer"], "yes", "always — every page"),
        ],
    },
    "api_integration_guide": {
        "purpose": "teach a customer's engineer to integrate against the "
                   "workflow-execution API",
        "length": "15–25 pages",
        "notes": ["Callouts carry meaning. Warning, info and success are three "
                  "distinct variants; collapsing them to one grey box loses "
                  "which failure mode each note addresses.",
                  "Never rebuild a code sample from extracted PDF text — write "
                  "code samples from a real, run source.",
                  "Section numbers are cross-referenced in the body (See §4). "
                  "Renumbering a section means fixing every reference to it.",
                  "The TOC has no page numbers. Adding them is a change, not a "
                  "fix — ask before you do it.",
                  "Brand type is Fraunces and Wix Madefor Display, not the "
                  "reference guide's Instrument Serif and Inter."],
        "slots": [
            _slot("cover", ["dark_cover"], "yes",
                  "always — title, topic pills, version and date, "
                  "confidentiality line"),
            _slot("contents", ["toc_dotted"], "yes", "always"),
            _slot("overview",
                  ["numbered_section_header", "paragraph", "flow_diagram", "table"],
                  "yes",
                  "always — prereqs, integration flow, base URL, quick reference"),
            _slot("section", ["numbered_section_header"], "rep",
                  "once per major section"),
            _slot("endpoint", ["method_badge", "code_block", "field_table"], "rep",
                  "once per endpoint documented"),
            _slot("note", ["callout"], "rep",
                  "wherever a warning, info, or success note belongs"),
            _slot("reference", ["table"], "yes",
                  "always — status codes, errors, troubleshooting"),
            _slot("example", ["code_block"], "yes",
                  "always — one complete copy-paste script"),
            _slot("footer", ["footer"], "yes",
                  "always — confidentiality stamp, no page number"),
        ],
    },
}

#: Chrome blocks recur once per printed page, so the order walk checks them for
#: presence only rather than for position.
_CHROME = {"masthead", "footer"}

#: Scaffold exemplar for a block's list field.
_SCAFFOLD_ITEM = {
    "cards": {"title": "", "body": "", "note_label": "", "note": ""},
    "pipeline": {"label": "", "sub": ""},
    "stats": {"value": "", "label": ""},
    "band": {"title": "", "body": ""},
    "panel": {"kind": "cells", "items": [""]},
    "toc_dotted": {"number": "", "title": ""},
    "quad_grid": {"number": "", "title": "", "summary": "",
                  "sub_label": "", "detail": ""},
    "dark_band_columns": {"title": "", "body": ""},
    "flow_diagram": {"label": "", "sub": ""},
    "field_table": {"field": "", "type": "", "required": False, "description": ""},
    "pill_row": "",
    "highlight_panel": "",
}

#: Optional fields whose declared default is an empty list — the scaffold wants
#: something fillable there instead.
_SCAFFOLD_OPTIONAL = {
    "field_table": {"headers": ["Field", "Type", "Required", "Description"]},
    "dark_cover": {"pills": [""]},
}


def _empty_block(btype):
    """One block of the given type with every field present but empty."""
    schema = BLOCKS[btype]
    block = {"type": btype}
    if btype == "table":
        block["headers"] = ["", "", ""]
        block["rows"] = [["", "", ""]]
        return block
    list_field = _LIST_FIELDS.get(btype)
    for field in schema["required"]:
        block[field] = ([_SCAFFOLD_ITEM[btype]] if field == list_field
                        else "")
    overrides = _SCAFFOLD_OPTIONAL.get(btype, {})
    for field, default in schema["optional"].items():
        block[field] = overrides.get(field, default)
    return block


def scaffold(type_name):
    """The type's blocks in contract order, empty, with slot markers.

    ``_slot`` names the slot every block belongs to; ``_include_when`` carries
    the contract's include-rule and appears only on ``cond`` and ``rep`` slots,
    so a caller can see at a glance which blocks are safe to delete. Both are
    stripped before rendering.
    """
    contract = TYPES[type_name]
    blocks = []
    for slot in contract["slots"]:
        for btype in slot["blocks"]:
            block = _empty_block(btype)
            block["_slot"] = slot["name"]
            if slot["req"] != "yes":
                block["_include_when"] = "{} — {}".format(slot["req"], slot["when"])
            blocks.append(block)
        if slot["blocks"] == ["dark_cover"]:
            # The cover owns its whole sheet; nothing else may share the page.
            blocks.append({"type": "page_break", "_slot": slot["name"]})
    spec = {"title": type_name.replace("_", " ").title(), "blocks": blocks}
    if type_name == "pricing_overview":
        spec["margins"] = {"top": 0.2, "right": 0.5, "bottom": 0.2, "left": 0.5}
    return spec


def _order_segments(slots):
    """Collapse each run of adjacent ``rep`` slots into one repeatable zone.

    A 20-page API guide cycles section -> endpoint -> note -> section -> …, so a
    run of ``rep`` slots is checked as a set of permitted block types rather
    than as a fixed sequence. What it still catches is a block type the type
    does not sanction, and a required slot that is missing or out of order.
    """
    segments, idx = [], 0
    while idx < len(slots):
        if slots[idx]["req"] == "rep":
            group = []
            while idx < len(slots) and slots[idx]["req"] == "rep":
                group.append(slots[idx])
                idx += 1
            segments.append(("rep", group))
        else:
            segments.append(("strict", [slots[idx]]))
            idx += 1
    return segments


def check_type(spec, type_name):
    """Violations of the named type's slot contract, each with its block index.

    Enforced: every required slot present, contract order respected, and no
    block type the type doesn't sanction. Dropped ``cond`` and ``rep`` slots are
    fine. Within a multi-block slot the listed blocks must appear in the listed
    order, but any of them may be omitted.
    """
    if type_name not in TYPES:
        return ["unknown type {!r} — run --list-types".format(type_name)]

    slots = TYPES[type_name]["slots"]
    sanctioned = {b for slot in slots for b in slot["blocks"]}
    chrome = _CHROME & sanctioned
    errors, entries, seen = [], [], set()

    for idx, block in enumerate(spec.get("blocks") or []):
        if not isinstance(block, dict) or block.get("type") is None:
            continue        # validate() already reported this one
        btype = block["type"]
        seen.add(btype)
        if btype == "page_break":
            continue        # structural, allowed anywhere
        if btype not in sanctioned:
            errors.append(
                "block[{}] ({}): not part of the {} contract — allowed blocks "
                "are {}".format(idx, btype, type_name, ", ".join(sorted(sanctioned))))
            continue
        if btype in chrome:
            continue        # page chrome, repeats per sheet
        entries.append((idx, btype))

    ordered = []
    for slot in slots:
        if set(slot["blocks"]) <= chrome:
            missing = sorted(set(slot["blocks"]) - seen)
            if slot["req"] == "yes" and missing:
                errors.append("slot {!r}: required but no {} block appears "
                              "anywhere in the spec".format(
                                  slot["name"], " or ".join(missing)))
        else:
            ordered.append(slot)

    pos = 0
    for kind, group in _order_segments(ordered):
        if kind == "rep":
            allowed = {b for slot in group for b in slot["blocks"]}
            while pos < len(entries) and entries[pos][1] in allowed:
                pos += 1
            continue
        slot = group[0]
        wanted, want_idx, matched = list(slot["blocks"]), 0, 0
        while pos < len(entries) and want_idx < len(wanted):
            if entries[pos][1] == wanted[want_idx]:
                matched += 1
                pos += 1
            want_idx += 1
        if not matched and slot["req"] == "yes":
            at = entries[pos][0] if pos < len(entries) else "end of spec"
            errors.append(
                "slot {!r}: required ({}) but missing or out of order — expected "
                "it at block[{}]".format(slot["name"], " + ".join(slot["blocks"]), at))

    for idx, btype in entries[pos:]:
        errors.append("block[{}] ({}): out of contract order — the {} contract "
                      "has nothing left to match it against".format(
                          idx, btype, type_name))
    return errors


# --------------------------------------------------------------------------
# Page assembly
# --------------------------------------------------------------------------


def _validate_margins(margins):
    """``margins`` is an optional top-level override, in inches."""
    if not isinstance(margins, dict):
        return ["'margins' must be an object of {top, right, bottom, left} "
                "inch numbers"]
    errors = []
    for key, value in margins.items():
        if key not in MARGINS:
            errors.append("margins: unknown side {!r} (top/right/bottom/left)".format(key))
        elif not isinstance(value, (int, float)) or isinstance(value, bool):
            errors.append("margins.{}: must be a number of inches".format(key))
        elif not 0 <= value <= 2:
            errors.append("margins.{}: {} in is outside 0–2 in".format(key, value))
    return errors


def page_css(spec) -> str:
    """The @page override for a spec that asks for margins of its own.

    ``.page`` carries a ``min-height`` so a trailing footer sits on the sheet's
    bottom edge; that height is the sheet minus the vertical margins, so it has
    to move with them.
    """
    margins = dict(MARGINS, **(spec.get("margins") or {}))
    if margins == MARGINS:
        return ""
    return (
        "@page {{ size: 8.5in 11in; margin: {top}in {right}in {bottom}in {left}in; }}\n"
        ".page {{ min-height: {height:.3f}in; }}\n"
    ).format(height=11.0 - margins["top"] - margins["bottom"], **margins)


def _pages(blocks):
    """Split the flat block list on page_break into per-page groups."""
    pages, current = [], []
    for block in blocks:
        if block.get("type") == "page_break":
            pages.append(current)
            current = []
        else:
            current.append(block)
    pages.append(current)
    return [p for p in pages if p]


def render_html(spec) -> str:
    blocks = spec["blocks"]
    assets = {"logo": logo_uri()}
    if any(b.get("type") == "dark_cover" for b in blocks):
        assets["logo_white"] = logo_white_uri()

    pages = []
    for page in _pages(blocks):
        # A full-bleed cover needs the zero-margin named page, which only makes
        # sense if the cover is the whole sheet.
        klass = "page cover" if page[0].get("type") == "dark_cover" else "page"
        pages.append('<section class="{}">\n{}\n</section>'.format(
            klass, "\n".join(render_block(block, assets) for block in page)))

    title = html_mod.escape(str(spec.get("title", "FurtherAI")))
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<title>{title}</title>\n<style>\n{fonts}\n{tokens}\n{css}\n{page}</style>\n"
        "</head>\n<body>\n{body}\n</body>\n</html>\n"
    ).format(title=title, fonts=brand_font_css() + "\n" + font_face_css(),
             tokens=tokens_css(), css=PRINT_CSS, page=page_css(spec),
             body="\n".join(pages))


#: A URL only makes the page phone home if it sits somewhere the renderer
#: actually fetches from. A ``code_block`` documenting an HTTP API is *full* of
#: URLs as prose — an integration guide can hardly avoid printing its own base
#: URL — so matching every ``https://`` in the document would ban the block.
_FETCHING_URL = re.compile(
    r"""(?: \b(?:src|srcset|href|poster|action|formaction|background|data)\s*=
        |   @import\s+
        |   \burl\(
        )
        \s*['"(]?\s*(https?://[^\s"')]+)""",
    re.IGNORECASE | re.VERBOSE)


def assert_self_contained(html: str):
    """Fail loudly rather than shipping a page that phones home."""
    # The XHTML/OOXML namespace URIs in tokens.css comments are identifiers,
    # never fetched, and nothing should reference a remote host at render time.
    bad = [u for u in _FETCHING_URL.findall(html)
           if not u.startswith("http://www.w3.org/")]
    if bad:
        raise ValueError("HTML is not self-contained, found external URLs: "
                         + ", ".join(sorted(set(bad))[:5]))


def build(spec, pdf_path=None, html_path=None):
    html = render_html(spec)
    assert_self_contained(html)
    if html_path is None and pdf_path is not None:
        html_path = Path(pdf_path).with_suffix(".html")
    html_path = Path(html_path)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html, encoding="utf-8")

    pdf = None
    if pdf_path is not None:
        pdf = print_to_pdf(html_path, pdf_path)
    return html_path, pdf


# --------------------------------------------------------------------------
# .docx -> .pdf
# --------------------------------------------------------------------------


def docx_to_pdf(path, out_dir=None):
    """Convert a .docx to PDF with LibreOffice, if it is installed.

    Returns the PDF path, or None with an explanation on stderr. There is no
    honest pure-python fallback: the .docx *is* the deliverable, and a faithful
    PDF of it needs Word or LibreOffice to lay it out.
    """
    path = Path(path)
    out_dir = Path(out_dir) if out_dir else path.parent
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    for candidate in ("/Applications/LibreOffice.app/Contents/MacOS/soffice",):
        if soffice is None and Path(candidate).exists():
            soffice = candidate
    if soffice is None:
        sys.stderr.write(
            "No LibreOffice on this machine, so the .docx cannot be converted here.\n"
            "  The .docx is the deliverable — it carries the brand fonts embedded.\n"
            "  To get a PDF: open it in Word or LibreOffice and export, or install\n"
            "  LibreOffice (brew install --cask libreoffice) and re-run.\n")
        return None
    subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(path)],
        check=True, capture_output=True, timeout=180)
    pdf = out_dir / (path.stem + ".pdf")
    return pdf if pdf.exists() else None


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _wrap(text, width):
    words, line, out = text.split(), "", []
    for word in words:
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    if line:
        out.append(line)
    return out


EXAMPLE = {
    "title": "Workflow One-Pager",
    "blocks": [
        {"type": "masthead", "tag": "Confidential"},
        {"type": "title", "text": "AI Transformation Partner for Insurance",
         "deck": "An insurance-native AI workspace that deploys on top of existing "
                 "systems - no rip and replace."},
        {"type": "paragraph", "text": "Two to four lines of framing at full measure."},
        {"type": "eyebrow", "text": "Select workflows in production"},
        {"type": "cards", "items": [
            {"title": "Submission Intake & Triage",
             "body": "What the workflow does, in three lines.",
             "note_label": "Case Study", "note": "The measured result."}]},
        {"type": "footer", "items": ["**$0M+** raised", "furtherai.com"]},
    ],
}


def list_blocks():
    lines = ["PDF spec:", '  {"title": "...", "blocks": [...]}', "",
             "  US Letter portrait, 0.45 x 0.5in margins, print colors forced on.",
             "  Fonts, logo and styles are inlined — the page makes no network request.",
             "", "Blocks:"]
    for name in sorted(BLOCKS):
        schema = BLOCKS[name]
        lines.append("")
        lines.append("  " + name)
        lines.append("    required: {}".format(", ".join(schema["required"]) or "(none)"))
        if schema["optional"]:
            lines.append("    optional: {}".format(", ".join(
                "{}={}".format(k, json.dumps(v)) for k, v in schema["optional"].items())))
        for chunk in _wrap(schema["doc"], 72):
            lines.append("    " + chunk)
    lines.append("")
    lines.append("Example:")
    lines.append(json.dumps(EXAMPLE, indent=2))
    return "\n".join(lines)


def list_types():
    lines = [
        "PDF document types. Each one is an ordered slot plan transcribed from",
        "skills/doc/references/doc_types.md — follow it as written rather than",
        "re-deriving a structure per document.",
        "",
        "  python3 build_pdf.py --list-types",
        "  python3 build_pdf.py --type NAME --scaffold > spec.json",
        "  python3 build_pdf.py spec.json --check --type NAME",
        "",
        "  req  yes  = always present",
        "       cond = only when the include-rule fires; dropping it is not an error",
        "       rep  = once per item in the source data; zero or more",
        "",
        "  Adjacent rep slots are checked as one repeatable zone, so a guide may",
        "  cycle section -> endpoint -> note as many times as it needs. masthead",
        "  and footer are page chrome: checked for presence, not for position.",
        "  page_break is structural and allowed anywhere.",
    ]
    for name in TYPES:
        contract = TYPES[name]
        lines.append("")
        lines.append("  " + name)
        for chunk in _wrap(contract["purpose"], 68):
            lines.append("    " + chunk)
        lines.append("    length: {}".format(contract["length"]))
        lines.append("")
        for idx, slot in enumerate(contract["slots"], 1):
            lines.append("    {:>2}  {:<14} {:<4} {}".format(
                idx, slot["name"], slot["req"], " + ".join(slot["blocks"])))
            if slot["req"] != "yes":
                for chunk in _wrap("include when: " + slot["when"], 56):
                    lines.append("        " + chunk)
        lines.append("")
        lines.append("    Content rules")
        for note in contract["notes"]:
            wrapped = _wrap(note, 66)
            lines.append("      - " + wrapped[0])
            for chunk in wrapped[1:]:
                lines.append("        " + chunk)
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("spec", nargs="?", help="path to the JSON page spec ('-' for stdin)")
    parser.add_argument("--out", help="output .pdf path")
    parser.add_argument("--html", help="output .html path (default: --out with .html)")
    parser.add_argument("--check", action="store_true", help="validate the spec, render nothing")
    parser.add_argument("--list-blocks", action="store_true",
                        help="print the block API reference and exit")
    parser.add_argument("--list-types", action="store_true",
                        help="print the document type contracts and exit")
    parser.add_argument("--type", help="document type to scaffold or check against")
    parser.add_argument("--scaffold", action="store_true",
                        help="with --type, print an empty spec for that type and exit")
    parser.add_argument("--docx", help="convert an existing .docx to PDF instead")
    args = parser.parse_args(argv)

    if args.list_blocks:
        print(list_blocks())
        return 0
    if args.list_types:
        print(list_types())
        return 0
    if args.type and args.type not in TYPES:
        sys.stderr.write("unknown type {!r}. Known types: {}\n".format(
            args.type, ", ".join(TYPES)))
        return 1
    if args.scaffold:
        if not args.type:
            parser.error("--scaffold needs --type NAME (see --list-types)")
        print(json.dumps(scaffold(args.type), indent=2))
        return 0
    if args.docx:
        pdf = docx_to_pdf(args.docx)
        if pdf is None:
            return 1
        print("PDF       {}".format(pdf.resolve()))
        return 0
    if not args.spec:
        parser.error("a spec path is required (or --list-blocks / --docx)")

    if args.spec == "-":
        spec = json.load(sys.stdin)
    else:
        path = Path(args.spec)
        if not path.exists():
            sys.stderr.write("no such spec: {}\n".format(path))
            return 1
        try:
            spec = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            sys.stderr.write("spec is not valid JSON: {}\n".format(exc))
            return 1

    spec = strip_markers(spec)
    errors = validate(spec)
    if not errors and args.type:
        errors = check_type(spec, args.type)
    if errors:
        sys.stderr.write("spec has {} problem(s):\n".format(len(errors)))
        for error in errors:
            sys.stderr.write("  - {}\n".format(error))
        return 2
    if args.check:
        print("spec OK — {} blocks{}".format(
            len(spec["blocks"]),
            ", matches the {} contract".format(args.type) if args.type else ""))
        return 0
    if not args.out and not args.html:
        sys.stderr.write("--out (pdf) or --html is required when rendering\n")
        return 1

    html_path, pdf = build(spec, args.out, args.html)
    print("HTML      {}".format(html_path.resolve()))
    if pdf is not None:
        print("PDF       {}".format(Path(pdf).resolve()))
    else:
        print("PDF       not produced — no Chrome/Chromium/Edge on this machine.")
        print("          Open the HTML above and print to PDF (US Letter, no headers).")
        if find_chrome() is None:
            print("          Or install Chrome and re-run this command.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
