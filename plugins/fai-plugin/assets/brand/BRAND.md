# FurtherAI brand reference

Everything a generated deck, document, or report needs to look on-brand. Read
this when composing a new artifact from scratch; skip it when following an
existing template, which already carries its own styling.

Measured from the real templates in this directory, not from a style deck.

## What's in here

```
assets/brand/
  fonts/Fraunces/                variable + 36 static TTFs
  fonts/Wix_Madefor_Display/     variable + 5 static TTFs
  logos/                         8 logo files, see below
  pptx_templates/                Example_Slides, Enterprise_Deck, plus a client deck
  doc_templates/                 Meeting_Brief.docx, Pricing_Document.docx, One_Pager.pdf
  style_guide/                   Writing_Style_Guide.pdf, Brand_Colors.png
```

The writing style guide's rules are already inlined in the `copy-review` skill —
never read that PDF.

## Fonts — read this before setting a single font name

The templates were authored with font names that **do not resolve** from the
TTFs shipped here. This is the highest-risk detail in the whole brand system.

| The templates say | Resolves? | Use instead |
|---|---|---|
| `Fraunces Light` | **No** — no TTF registers that family | `Fraunces 72pt Light` |
| `Fraunces 9pt` | **No** — that's a font's full name, not a family | `Fraunces 72pt` |
| `Fraunces` | Yes, but **to Black (weight 900)** — the variable font's default instance | `Fraunces 72pt` |
| `Wix Madefor Display` | Yes, correctly | keep it |
| `Wix Madefor Text` | **Not vendored here** | substitute `Wix Madefor Display` |

The family name PowerPoint and Word show is TTF name ID 1. What the static
files here actually register:

| Intent | Family name to set | File |
|---|---|---|
| Display serif, light — covers, section titles | `Fraunces 72pt Light` | `Fraunces/static/Fraunces_72pt-Light.ttf` (+ `-LightItalic`) |
| Display serif, regular — slide and card titles | `Fraunces 72pt` | `Fraunces/static/Fraunces_72pt-{Regular,Bold,Italic,BoldItalic}.ttf` |
| Display serif, semibold | `Fraunces 72pt SemiBold` | `Fraunces/static/Fraunces_72pt-SemiBold.ttf` |
| Body sans, regular + bold | `Wix Madefor Display` (`bold=True` works) | `Wix_Madefor_Display/static/WixMadeforDisplay-{Regular,Bold}.ttf` |
| Body sans, medium / semibold / extrabold | `Wix Madefor Display Medium` / `… SemiBold` / `… ExtraBold` | matching statics |
| Monospace badge label | `Courier New` | system |

`Medium`, `SemiBold`, and `ExtraBold` are **separate families** — select them by
full name, never with `bold=True`.

In CSS the variable font is safe, because axes can be set explicitly:

```css
font-family: 'Fraunces', Georgia, serif;
font-variation-settings: 'opsz' 72, 'wght' 300;  /* without this you get Black */
```

Run `python3 skills/deck/scripts/fonts.py check` to see what resolves on a given
machine, and `fonts.py install` to install the bundled statics.

## Palette

The brand palette. Use this for decks, documents, and reports.

| Token | Hex | Role |
|---|---|---|
| `green_deep` | `#074B40` | primary brand green — headings, table header fill, panels |
| `green` | `#25654F` | secondary green — rules, outlines, emphasis numbers |
| `green_dark_text` | `#1D4438` | green text on light cards; dark table header fill |
| `green_pale` | `#D9EAD3` | positive cells, total rows |
| `ink` | `#14161C` | near-black body |
| `ink_alt` | `#2B2D31` | charcoal body — the default document text color |
| `warm_dark` | `#444339` | warm dark gray on cream cards |
| `paper` | `#FBFBF9` | primary off-white background |
| `paper_warm` | `#F4F3F0` | warm cream background |
| `card` | `#F8F7F5` | card fill on cream |
| `tan` | `#D0CEC3` | footer text on dark, card hairlines, badge text |
| `hair` | `#CCCCCC` | rules and dividers |
| `muted` | `#595959` | captions and footers on light |
| `black_deep` | `#0B0B12` | full-bleed dark slide background |

Cover gradient: `#074B40` → `#1A421F` → `#14452A` → `#203E13` at stops
0 / .47 / .5 / 1. Screenshot backdrop: `#424242` → `#010101`.

A second, denser palette exists for analytical consulting slides — card headers
`#143524`, accents `#2C7A4B`, eyebrows on dark `#B7D9C5`, fills `#E1EFD8` /
`#EDF3EF` / `#F7F9F8`, body `#111111`, muted `#5B6770`, labels `#385623`,
timeline milestones `#38761D`, agenda panel `#051610`. Rating arrows: favorable
`#1C2B11`, neutral `#A8D08C`, unfavorable `#7F7F7F`. Severity: critical
`#C0392B`, high `#F08C3F`, medium `#F4C842`.

The webapp UI palette in `CONVENTIONS.md` (mango, navy, pinehurst, sinopia) is a
**different system** for product screens and data reports. Don't mix the two in
one artifact.

## Type ramp

On the 13.333 × 7.5in slide canvas. Scale by 0.75 for a 10 × 5.625in deck.

| Role | Family | Size | Color |
|---|---|---|---|
| Cover headline | Fraunces 72pt Light | 49pt, line spacing 0.8 | `#FFFFFF` |
| Full-bleed hero | Fraunces 72pt Light | 41pt | `#FFFFFF` |
| Section headline | Fraunces 72pt Light | 35–38pt | `#FFFFFF` or `#14161C` |
| Big statement | Fraunces 72pt | 29–33pt | `#2B2D31` |
| Slide title | Fraunces 72pt | 24–26pt | `#111111` |
| Card title | Fraunces 72pt | 21–24pt | `#444339` |
| Sub-headline | Wix Madefor Display | 16–19pt | `#5B6770` |
| Card heading | Wix Madefor Display | 17–18pt | `#FFFFFF` |
| Body | Wix Madefor Display | 12–14.5pt | `#111111` |
| Eyebrow / kicker | Wix Madefor Display | 10–12pt | `#B7D9C5` on dark, `#385623` on light |
| Footer | Wix Madefor Display | 9pt | `#5B6770` light, `#EEEEEE` dark |

Documents run smaller: title Fraunces 72pt Light 32pt `#074B40`, section heading
the same at 22pt, body Wix Madefor Display 11pt `#2B2D31`, table body 10.5pt,
captions 10pt `#595959`.

## Logos

| File | Size | Transparent | Use when |
|---|---|---|---|
| `Logo_Full_Dark_on_Transparent.png` | 519 × 112 | yes | **Default on light backgrounds** — the wordmark every content slide uses, placed at `W1.4 H0.3`. Ink `#161611` |
| `Logo_Full_White_on_Transparent.png` | 2034 × 439 | yes | **Default on dark backgrounds.** Raster twin of the SVG, for python-pptx, which cannot place SVG |
| `Logo_Full_Green_on_Transparent.png` | 1589 × 344 | yes | Light backgrounds where the dark wordmark reads too heavy. Ink `#154037` |
| `Logo_Social_White_on_Transparent.png` | 297 × 299 | yes | Square `F` mark on any dark or colored ground — knocked out in `#FBFBF9` with a thin outline |
| `Logo_Full_White_on_Transparent.svg` | vector, 16:9 | yes | Vector source for dark backgrounds — HTML and PDF output, or re-rasterizing at a new size. Recolor via its single `fill`. python-pptx cannot place it; use the PNG above |
| `Logo_Full_Green_on_White.jpg` | 640 × 360 | no | Only when an opaque white plate is actually wanted — otherwise prefer `Logo_Full_Dark_on_Transparent.png`. **Crop to the content bbox `(22,116)–(614,242)` first** — 57% of the canvas is padding, and placing it uncropped renders it tiny and off-center |
| `Logo_Social_Green_on_White.png` | 800 × 800 | no | Square mark on light — the document logo slot, ~0.65in |
| `Logo_Social_White_on_Green.png` | 800 × 800 | no | Square mark on dark or colored surfaces; app icon |

Every `_on_Transparent.png` is already cropped to its content bbox, so the file
dimensions *are* the artwork — place by width and let height follow. The three
wordmarks are 4.62:1; the square mark is 1:1.

To re-rasterize the SVG at another size, use headless Chrome — it is the only
renderer on hand that keeps the alpha channel:

```
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless \
  --default-background-color=00000000 --force-device-scale-factor=3 \
  --window-size=1600,900 --screenshot=out.png file:///abs/path/to/logo.svg
```

Then crop to the alpha bbox. `qlmanage` is **not** a substitute: it flattens onto
a white page, which erases a white-on-transparent mark, and it clips to a square.

Logo greens are `#056250` (wordmark) and `#26634F` (square mark) — close to but
not the same as the deck greens. **Use `#074B40` / `#25654F` for generated
artwork**; that's what the templates use.

Slide placement: light slides put the wordmark at `L0.4 T0.3 W1.4 H0.3`; dark
slides put it top-right at roughly `L12.0 T0.34 W0.97`.

## Slide chrome

Repeats on every content slide of a 13.333 × 7.5in deck:

- Title: `L0.4 T1.25 W12.5 H0.6`, middle-anchored, Fraunces 72pt 26pt `#111111`
- Sub-headline: `L0.4 T1.9 W12.5 H0.5`, Wix Madefor Display 14pt `#5B6770`
- Footer left: `L0.4 T7.15`, 9pt `#5B6770` — "AI Workspace for Insurance"
- Footer center: `L5.0 T7.15`, centered, 9pt — "furtherai.com"
- On dark slides the footer sits at `T7.0` in `#EEEEEE` with a `#666666` hairline
  rule above at `T6.84`

## The one thing that breaks generated decks

Every template here ships the **stock Office theme** — Arial, blue/orange accents,
nothing branded. So a generated artifact must set `font.name`, `font.color.rgb`,
and `fill.fore_color.rgb` **explicitly on every run and every shape**. Never emit
a theme color reference, and never let a run inherit its font. Where a template
dump shows `scheme:LIGHT_1` / `DARK_1` / `DARK_2` / `LIGHT_2`, the author left a
theme reference behind — substitute the brand equivalent (`#FFFFFF`, `#14161C`,
`#595959`, `#F4F3F0`), not the literal Office hex.
