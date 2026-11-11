---
name: document-design
metadata:
  furtherai-display-name: Document Design
description: House visual style for new Word (.docx), PowerPoint (.pptx), Excel (.xlsx) and PDF files, covering fonts, colors, page, slide and sheet layouts, tables, charts, a date and page-number footer, and a final check. Use whenever creating a new report, memo, summary, brief, letter, one-pager, slide deck, spreadsheet or workbook as a .docx, .pptx, .xlsx or .pdf. Do not use when editing or filling a file the user supplied (including PDF forms and spreadsheet templates), or when the user gives their own template, brand, fonts or colors; keep theirs.
---

# Document design

New Word, PowerPoint, Excel and PDF files share one house style: calm, concrete and built for
insurance. Cream grounds, Fraunces Light headings, Main Green as the lead color, one
accent per line. This skill sets the look only. Content, data gathering and file
delivery follow your usual instructions.

**The documents belong to the user's organization.** Never put the FurtherAI name or
logo, or a "Prepared by" line, anywhere in the file: not in footers, covers, mastheads
or document properties. Add a logo only when the user supplies their own image.

## When this applies

- **Apply** to every new Word, PowerPoint, Excel or PDF file you create from scratch.
- **Do not restyle** a file the user uploaded or asked you to edit, a form or
  template being filled, or anything the user gave its own template, brand, fonts
  or colors for. Match what is there.
- If the user asks for a different look, their request wins over this skill.

Then read the reference for the format you are making. It has tested helper code;
use it rather than writing your own styling. Set `SKILL_DIR` to this skill's
directory before pasting the helpers.

- Word: [references/word.md](references/word.md)
- PowerPoint: [references/slides.md](references/slides.md)
- Excel: [references/excel.md](references/excel.md)
- PDF: [references/pdf.md](references/pdf.md)

## Fonts

Four families, each with one job. Use these exact names:

| Font name | Job |
| --- | --- |
| `Fraunces 72pt Light` | Every title and heading. Never for key figures or other standalone numbers. Never bold. Its italic, for one to three words inside a heading, once per slide or section, same color |
| `Wix Madefor Display` | Key figures and other large numbers, body text, bullets, table cells, captions, footers |
| `Wix Madefor Text SemiBold` | Table headers and short UI labels |
| `IBM Plex Mono Medium` | ALL CAPS eyebrows above headings, status tags |

These are the unmodified Google Fonts files in `assets/fonts/`, under the SIL Open
Font License (license texts alongside). Most readers do not have them installed, so
each format carries them differently:

- **Word and PowerPoint**: run the embed script on every file before delivering it.
  Without it every heading turns into Calibri.

  ```bash
  python "$SKILL_DIR/scripts/embed_fonts.py" path/to/output.pptx   # or .docx
  ```

- **PDF**: the PDF helpers embed the fonts automatically.
- **Excel**: a workbook cannot carry fonts, so sheets use Georgia for sheet titles and
  Arial for every other cell, key figures included. Never put the brand fonts in a workbook.

Never set bold on the brand fonts, and italic only on Fraunces 72pt Light.

## Color

| Name | Hex | Use |
| --- | --- | --- |
| Ink | `161611` | Text on light grounds |
| Slate | `6F6D64` | Muted text, captions, metadata, footers |
| Stone | `DAD8CE` | Dividers, table rules, context bars in charts |
| Cream | `F7F6F2` | Slide ground; key-number cards in Word |
| White | `FFFFFF` | Cards on Cream; the Word page |
| Main Green | `1A4438` | The Word masthead band; the lead brand color |
| Pine | `25644F` | Eyebrows, the takeaway bar or cell, links |
| Mint | `D9EAD3` | One callout per document; "good" tags |
| Mango | `FB9608` | One small dot beside the key number's label, once per slide or page; "watch" tags with Ink text. Never a box behind a number |
| On dark | `F7F6F2` text, `A9B3AE` muted, `88E9C0` the one accent phrase | |

**One accent per line, never two.** A Mango dot marks one number, the italic or the
`88E9C0` phrase marks one idea, and they never share a line. Dark slides use the
brand background images in `assets/brand/`; light slides use Cream with White cards.
No other colors, no gradients you draw yourself, no shadows.

## Footer

Every page, light slide and printed sheet shows today's date (`Nov 1, 2026`, month
abbreviated) on the left and the page or slide number on the right, in Slate. Covers
and closers show the date only. Nothing else goes in the footer.

## Voice and composition

- **Sentence case everywhere.** Headings, labels, buttons. Never Title Case.
- **Outcome first**: a short summary, then key numbers, then the detail.
- **Speak insurance**: submissions, loss runs, SOVs, quotes, binders, not generic words.
- **Stay quiet**: no emoji, no exclamation marks, headings never bold, ALL CAPS only
  in mono eyebrows. No em dashes or en dashes.
- **Eyebrows** above headings read `02 · LOSS HISTORY`; the helpers number them.
- **Lines under about 75 characters; headings at most two lines;** left-aligned,
  centered only for a short cover title, never justified.
- **Vary the layout** from one slide or section to the next; never the same frame
  twice in a row.

## Never

- Bold or Title Case headings, a fully italic line, or an italic word in a color.
- Two accents in one line, or Cream text on Mango.
- The FurtherAI name or logo, or any "Prepared by" line.
- Colored edge stripes, accent lines under titles, or rounded cards with shadows.
- Emoji, custom icons, or text smaller than 7 pt in Word, PDF or Excel, or 10 pt on a slide.

## Before you deliver

1. Word and PowerPoint: run `scripts/embed_fonts.py` on the file.
2. Run the check in the reference for your format and fix what it finds.
3. Slides, PDFs and workbooks: render every page or sheet to an image and look at it
   (each reference shows how).
