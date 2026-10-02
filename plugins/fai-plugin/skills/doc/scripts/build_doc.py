#!/usr/bin/env python3
"""build_doc.py — build a branded .docx from a JSON document spec.

    python3 build_doc.py spec.json --out Pricing.docx
    python3 build_doc.py spec.json --check         # validate only, build nothing
    python3 build_doc.py --list-blocks             # the full block API reference
    python3 build_doc.py --list-types              # the document type contracts
    python3 build_doc.py --type pricing_summary --scaffold > spec.json
    python3 build_doc.py spec.json --check --type pricing_summary

Spec shape::

    {
      "style": "client",            // "client" (branded) or "internal" (Arial)
      "embed_fonts": true,          // default true; embeds the brand TTFs
      "blocks": [
        {"type": "logo"},
        {"type": "title", "text": "Pricing Summary"},
        ...
      ]
    }

Validation runs before anything is built and reports *every* unknown block type
and missing field, each with its block index, so one pass fixes the whole spec.

``--type NAME`` layers the type contract from ``references/doc_types.md`` on top:
the type's ordered slot plan, which slots are required, and which grammar the
type is pinned to. ``--scaffold`` emits that plan as an empty spec; keys starting
with ``_`` are scaffold markers and are stripped before anything is validated or
built.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import doc_kit as dk  # noqa: E402

# --------------------------------------------------------------------------
# Block registry: type -> (required fields, optional fields with defaults, doc)
# --------------------------------------------------------------------------

BLOCKS = {
    "logo": {
        "required": [],
        "optional": {"variant": "square", "width_in": 0.646, "space_after": 12},
        "doc": "Inline brand logo in its own paragraph. variant 'square' (0.646in "
               "mark, the client template's slot) or 'wordmark' (horizontal "
               "mark+wordmark, cropped with Pillow; width_in then means the "
               "wordmark width, default 1.6in).",
    },
    "title": {
        "required": ["text"],
        "optional": {"space_after": 4},
        "doc": "Document title — Fraunces 72pt Light 32pt #074B40.",
    },
    "subtitle": {
        "required": ["text"],
        "optional": {"space_after": 3},
        "doc": "Gray line under the title — Wix Madefor Display 11pt #595959. "
               "Use for 'Prepared for X  ·  Month Year'.",
    },
    "body": {
        "required": ["text"],
        "optional": {"space_after": 8, "size": 11, "color": dk.PALETTE["ink_alt"],
                     "bold": False, "italic": False},
        "doc": "Body paragraph — Wix Madefor Display 11pt #2B2D31 (client) or "
               "Arial 10pt (internal).",
    },
    "heading": {
        "required": ["text"],
        "optional": {"space_before": 6, "space_after": 7},
        "doc": "Section heading above a table — Fraunces 72pt Light 22pt #074B40.",
    },
    "rule": {
        "required": [],
        "optional": {"color": dk.PALETTE["tan"], "space_after": 14, "sz": 6},
        "doc": "Horizontal divider (an empty paragraph with a bottom border).",
    },
    "table": {
        "required": ["headers", "rows"],
        "optional": {"col_widths_in": None, "total_row": False},
        "doc": "The brand table: no outer frame, hairline interior rules, "
               "#074B40 header band in Wix Madefor Display SemiBold white, "
               "#FBFBF9 first column, column 3 at 10pt gray. total_row:true "
               "fills the last row #D9EAD3 with #074B40 text. col_widths_in "
               "defaults to the template's 1.701/1.701/3.097 for 3 columns, "
               "otherwise equal columns across the content width. Cell strings "
               "may contain \\n for multi-line cells.",
    },
    "status_table": {
        "required": ["headers", "rows"],
        "optional": {"status_col": None, "statuses": None, "col_widths_in": None,
                     "size": None},
        "doc": "A grid whose status column carries its state in the cell fill: "
               "Complete/Done #D9EAD3, In-Progress #C9DAF8, Not started/Backlog "
               "#EEEEEE, Blocked #F4CCCC, Pending #FFF2CC — matched on the cell "
               "text case-insensitively with whitespace collapsed. status_col is "
               "0-indexed (negatives count from the right) and defaults to the "
               "last column; a cell matching nothing is left unfilled. statuses "
               "overrides or extends the map, e.g. {\"At risk\": \"#F4CCCC\"}. "
               "Chrome follows the grammar — the brand table in 'client', the "
               "plainer Meeting_Brief grid in 'internal'. Unlike 'table' there is "
               "no 10pt-gray third column: every body column reads the same. "
               "col_widths_in defaults to equal columns across the content width.",
    },
    "milestone_table": {
        "required": ["rows"],
        "optional": {"col_widths_in": None},
        "doc": "Milestone / Date / Owner / Status grid; rows is "
               "[{\"milestone\", \"date\", \"owner\", \"status\"}]. The status "
               "column is filled from the status_table map. col_widths_in "
               "defaults to 37/19/23/21% of the content width.",
    },
    "signoff_table": {
        "required": ["rows"],
        "optional": {"col_widths_in": None},
        "doc": "Role / Name / Date sign-off grid; rows is "
               "[{\"role\", \"name\", \"date\"}]. Blank name and date cells are "
               "expected — they get signed by hand, so the rows carry a 0.34in "
               "minimum height. col_widths_in defaults to 36/39/25% of the "
               "content width.",
    },
    "bullets": {
        "required": ["items"],
        "optional": {"size": 10, "space_after": 3},
        "doc": "Bullet list in the document's body font.",
    },
    "checklist": {
        "required": ["items"],
        "optional": {"size": 10, "space_after": 3},
        "doc": "Checkbox list; items is [str] or "
               "[{\"text\", \"done\": bool}]. Renders '[ ]' / '[x]' rather than "
               "the ☐/☑ ballot boxes: neither Wix Madefor Display nor Arial has "
               "U+2610/U+2611 in its cmap, so the glyphs would come out as tofu.",
    },
    "grouped_bullets": {
        "required": ["groups"],
        "optional": {"size": 10, "space_after": 3},
        "doc": "A bold label line then its bullets, once per group; groups is "
               "[{\"label\", \"items\": [str]}]. For week-grouped or "
               "category-grouped lists that would otherwise need a heading each.",
    },
    "internal_title": {
        "required": ["text"],
        "optional": {"space_after": 2},
        "doc": "Internal-brief title — Arial 15pt bold #1F4E79.",
    },
    "internal_heading": {
        "required": ["text"],
        "optional": {"space_before": 10, "space_after": 5},
        "doc": "Internal-brief section heading — Arial 12pt bold #1F4E79 with a "
               "#1F4E79 bottom rule.",
    },
    "meta": {
        "required": ["text"],
        "optional": {"space_after": 8},
        "doc": "Internal-brief meta line — Arial 9pt italic #555555 "
               "(date · time · channel · purpose).",
    },
    "sources": {
        "required": ["text"],
        "optional": {"space_before": 10},
        "doc": "Closing sources line — Arial 8pt #555555; the text before the "
               "first ':' is bolded.",
    },
    "facts": {
        "required": ["pairs"],
        "optional": {"label_w": 1.667, "value_w": 4.833, "size": 9.5},
        "doc": "Internal-brief 2-column fact table: #EEF3F8 bold label cells, "
               "hairline #CCCCCC grid. pairs is [[label, value], ...].",
    },
    "page_break": {
        "required": [],
        "optional": {},
        "doc": "Hard page break.",
    },
}

#: Blocks that belong to the internal (Meeting_Brief) grammar.
INTERNAL_BLOCKS = {"internal_title", "internal_heading", "meta", "sources", "facts"}
#: Blocks that belong to the client (Pricing_Document) grammar.
CLIENT_BLOCKS = {"logo", "title", "subtitle", "heading", "table", "rule"}


def _default_col_widths(headers, content_width):
    if len(headers) == 3:
        return [1.701, 1.701, 3.097]
    share = content_width / len(headers)
    return [round(share, 3)] * len(headers)


def _even_col_widths(n_cols, content_width):
    share = content_width / n_cols
    return [round(share, 3)] * n_cols


def _fraction_widths(fractions, content_width):
    return [round(f * content_width, 3) for f in fractions]


def _grammar_conflict(btype, style):
    """The grammar the block belongs to when it doesn't belong in ``style``.

    ``rule`` is shared — it's a plain bottom-bordered paragraph and both
    templates use one — so it is exempt from the client-only set.
    """
    if style == "internal" and btype in CLIENT_BLOCKS - {"rule"}:
        return "client"
    if style == "client" and btype in INTERNAL_BLOCKS:
        return "internal"
    return None


# --------------------------------------------------------------------------
# Composite blocks — status / checklist / sign-off / milestone
#
# These render through doc_kit's table primitives rather than dk.brand_table,
# because the chrome has to switch per grammar and the status fill is per-cell.
# Every run still lands via dk._cell_text -> dk.set_font, which writes
# w:rFonts ascii/hAnsi/cs; run.font.name alone reverts to Calibri in Word.
# --------------------------------------------------------------------------

#: Status label -> cell fill. Matched case-insensitively with whitespace
#: collapsed, so 'IN PROGRESS' and 'In  progress' both land. #D9EAD3 is the
#: brand's green_pale (BRAND.md); the rest are the neutral state tints.
STATUS_FILLS = {
    "Complete": "#D9EAD3",
    "Done": "#D9EAD3",
    "In-Progress": "#C9DAF8",
    "In progress": "#C9DAF8",
    "Not started": "#EEEEEE",
    "Backlog": "#EEEEEE",
    "Blocked": "#F4CCCC",
    "Pending": "#FFF2CC",
}

#: Table chrome per grammar. 'client' is the brand table of document_patterns.md
#: §D.2 — nil outer frame, single #000000 sz=4 hairline interiors, #074B40
#: header band, tcMar 140/200/140/120. 'internal' is the Meeting_Brief grid of
#: §D.1 — per-cell #CCCCCC hairlines, #EEF3F8 label band, Arial throughout.
CHROME = {
    "client": {
        "outer": {"val": "nil", "sz": 0, "color": "000000"},
        "inside": {"val": "single", "sz": 4, "color": "000000"},
        "cell_border": None,
        "margins": dk.CELL_MARGINS,
        "header_fill": dk.PALETTE["green_deep"],
        "header_font": dk.BODY_SEMIBOLD,
        "header_color": dk.PALETTE["white"],
        "header_size": 10.5,
        "label_font": dk.BODY_SEMIBOLD,
        "label_fill": dk.PALETTE["paper"],
        "label_bold": False,
        "body_font": dk.BODY,
        "body_color": dk.PALETTE["ink_alt"],
        "size": 10.5,
    },
    "internal": {
        "outer": {"val": "single", "sz": 4, "color": "auto"},
        "inside": {"val": "single", "sz": 4, "color": "auto"},
        "cell_border": {"val": "single", "sz": 1, "color": dk.PALETTE["internal_rule"]},
        "margins": dk.FACT_CELL_MARGINS,
        "header_fill": dk.PALETTE["internal_label"],
        "header_font": dk.INTERNAL_FONT,
        "header_color": "#000000",
        "header_size": 9.5,
        "label_font": dk.INTERNAL_FONT,
        "label_fill": dk.PALETTE["internal_label"],
        "label_bold": True,
        "body_font": dk.INTERNAL_FONT,
        "body_color": "#000000",
        "size": 9.5,
    },
}

#: Checked against the vendored TTF cmaps: neither Wix Madefor Display (any cut)
#: nor system Arial carries U+2610 BALLOT BOX or U+2611 BALLOT BOX WITH CHECK, so
#: those glyphs render as tofu. ASCII brackets exist in every cut.
CHECK_TODO = "[ ]"
CHECK_DONE = "[x]"

MILESTONE_HEADERS = ["Milestone", "Date", "Owner", "Status"]
MILESTONE_KEYS = ("milestone", "date", "owner", "status")
MILESTONE_FRACTIONS = (0.37, 0.19, 0.23, 0.21)

SIGNOFF_HEADERS = ["Role", "Name", "Date"]
SIGNOFF_KEYS = ("role", "name", "date")
SIGNOFF_FRACTIONS = (0.36, 0.39, 0.25)
#: Blank name/date cells get signed by hand, so give the rows room to write in.
SIGNOFF_ROW_IN = 0.34


def _norm_status(text):
    return " ".join(str(text).split()).lower()


def _status_map(override=None):
    table = {_norm_status(k): v for k, v in STATUS_FILLS.items()}
    if isinstance(override, dict):
        table.update({_norm_status(k): v for k, v in override.items()})
    return table


def _row_min_height(row, inches):
    tr_pr = row._tr.get_or_add_trPr()
    tr_pr.append(dk._el("w:trHeight", val=int(round(inches * 1440)), hRule="atLeast"))


def _grid(doc, style, headers, rows, col_widths_in, size=None, fills=None,
          label_col=True, min_row_in=None):
    """A header + body grid in the grammar's table chrome.

    ``fills`` overrides a body cell's background as {(row, col): '#RRGGBB'},
    row indices counted over ``rows``; the header row always carries the
    grammar's header fill.
    """
    chrome = CHROME[style]
    size = chrome["size"] if size is None else size
    fills = fills or {}
    n_cols = len(headers)

    table = doc.add_table(rows=len(rows) + 1, cols=n_cols)
    widths = dk._configure_table(
        table, col_widths_in, outer=chrome["outer"], inside=chrome["inside"],
        target_total_in=dk.CONTENT_WIDTH[style])

    header = table.rows[0]
    dk._header_row(header)
    for c_idx, cell in enumerate(header.cells):
        dk._cell_width(cell, widths[c_idx])
        if chrome["cell_border"]:
            dk._cell_borders(cell, chrome["cell_border"])
        dk._shade(cell, chrome["header_fill"])
        dk._cell_margins(cell, chrome["margins"])
        dk._cell_text(cell, headers[c_idx], chrome["header_font"],
                      chrome["header_size"], chrome["header_color"], bold=True)

    for r_idx, row_values in enumerate(rows):
        row = table.rows[r_idx + 1]
        if min_row_in:
            _row_min_height(row, min_row_in)
        for c_idx, cell in enumerate(row.cells):
            is_label = label_col and c_idx == 0
            font_name = chrome["label_font"] if is_label else chrome["body_font"]
            base_fill = chrome["label_fill"] if is_label else None
            fill = fills.get((r_idx, c_idx), base_fill)
            # tcPr is a schema *sequence*: tcW, tcBorders, shd, tcMar. Emit the
            # shade out of order and Word offers to repair the file.
            dk._cell_width(cell, widths[c_idx])
            if chrome["cell_border"]:
                dk._cell_borders(cell, chrome["cell_border"])
            if fill:
                dk._shade(cell, fill)
            dk._cell_margins(cell, chrome["margins"])
            dk._cell_text(cell, row_values[c_idx], font_name, size,
                          chrome["body_color"],
                          bold=is_label and chrome["label_bold"])
    return table


def _status_table(doc, style, headers, rows, status_col=None, statuses=None,
                  col_widths_in=None, size=None):
    n_cols = len(headers)
    if status_col is None:
        status_col = n_cols - 1
    elif status_col < 0:
        status_col += n_cols
    lookup = _status_map(statuses)
    fills = {}
    for r_idx, row in enumerate(rows):
        if 0 <= status_col < len(row):
            fill = lookup.get(_norm_status(row[status_col]))
            if fill:
                fills[(r_idx, status_col)] = fill
    widths = col_widths_in or _even_col_widths(n_cols, dk.CONTENT_WIDTH[style])
    return _grid(doc, style, headers, rows, widths, size=size, fills=fills)


def _dict_rows(rows, keys):
    return [[str(row.get(key, "")) for key in keys] for row in rows]


def _milestone_table(doc, style, rows, col_widths_in=None):
    widths = col_widths_in or _fraction_widths(
        MILESTONE_FRACTIONS, dk.CONTENT_WIDTH[style])
    return _status_table(doc, style, MILESTONE_HEADERS,
                         _dict_rows(rows, MILESTONE_KEYS),
                         status_col=len(MILESTONE_HEADERS) - 1,
                         col_widths_in=widths)


def _signoff_table(doc, style, rows, col_widths_in=None):
    widths = col_widths_in or _fraction_widths(
        SIGNOFF_FRACTIONS, dk.CONTENT_WIDTH[style])
    return _grid(doc, style, SIGNOFF_HEADERS, _dict_rows(rows, SIGNOFF_KEYS),
                 widths, min_row_in=SIGNOFF_ROW_IN)


def _body_font(style):
    if style == "client":
        return dk.BODY, dk.PALETTE["ink_alt"]
    return dk.INTERNAL_FONT, "#000000"


def _checklist(doc, style, items, size=10, space_after=3):
    font, color = _body_font(style)
    out = []
    for item in items:
        if isinstance(item, dict):
            text, done = str(item.get("text", "")), bool(item.get("done"))
        else:
            text, done = str(item), False
        paragraph = doc.add_paragraph()
        paragraph.paragraph_format.left_indent = dk.Inches(0.25)
        dk._spacing(paragraph, after=space_after)
        glyph = CHECK_DONE if done else CHECK_TODO
        dk.set_font(paragraph.add_run("{}  {}".format(glyph, text)), font,
                    size=size, color=color)
        out.append(paragraph)
    return out


def _grouped_bullets(doc, style, groups, size=10, space_after=3):
    font, color = _body_font(style)
    out = []
    for idx, group in enumerate(groups):
        paragraph = doc.add_paragraph()
        dk._spacing(paragraph, before=(6 if idx else 0), after=2)
        dk.set_font(paragraph.add_run(str(group.get("label", ""))), font,
                    size=size, bold=True, color=color)
        out.append(paragraph)
        out.extend(dk.bullets(doc, group.get("items", []) or [], size=size,
                              space_after=space_after, font=font, color=color))
    return out


#: Scaffolded specs carry annotation keys prefixed with this — '_type' on the
#: spec, '_slot' and '_when' on each block. They are stripped before anything is
#: validated or built, so a filled-in scaffold needs no cleanup to build.
MARKER_PREFIX = "_"


def strip_markers(spec):
    """A copy of ``spec`` with scaffold marker keys removed, top level and per
    block. Idempotent, and a no-op on a hand-written spec."""
    if not isinstance(spec, dict):
        return spec
    out = {k: v for k, v in spec.items() if not str(k).startswith(MARKER_PREFIX)}
    blocks = out.get("blocks")
    if isinstance(blocks, list):
        out["blocks"] = [
            {k: v for k, v in block.items() if not str(k).startswith(MARKER_PREFIX)}
            if isinstance(block, dict) else block
            for block in blocks
        ]
    return out


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def validate(spec):
    """Return a list of human-readable problems; empty means the spec is good."""
    errors = []
    if not isinstance(spec, dict):
        return ["spec must be a JSON object with a 'blocks' array"]
    spec = strip_markers(spec)
    style = spec.get("style", "client")
    if style not in ("client", "internal"):
        errors.append("style: {!r} is not 'client' or 'internal'".format(style))
    blocks = spec.get("blocks")
    if blocks is None:
        return errors + ["spec has no 'blocks' array"]
    if not isinstance(blocks, list):
        return errors + ["'blocks' must be an array"]
    if not blocks:
        errors.append("'blocks' is empty — nothing to build")

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

        if btype in ("table", "status_table") and "headers" in block and "rows" in block:
            headers, rows = block["headers"], block["rows"]
            if not isinstance(headers, list) or not headers:
                errors.append("{}: 'headers' must be a non-empty array".format(where))
            elif not isinstance(rows, list):
                errors.append("{}: 'rows' must be an array of arrays".format(where))
            else:
                for r_idx, row in enumerate(rows):
                    if not isinstance(row, list):
                        errors.append("{}: rows[{}] must be an array".format(where, r_idx))
                    elif len(row) != len(headers):
                        errors.append("{}: rows[{}] has {} cells, headers has {}".format(
                            where, r_idx, len(row), len(headers)))
                widths = block.get("col_widths_in")
                if widths is not None:
                    if not isinstance(widths, list) or len(widths) != len(headers):
                        errors.append("{}: 'col_widths_in' must have {} numbers".format(
                            where, len(headers)))
        if btype == "facts" and isinstance(block.get("pairs"), list):
            for p_idx, pair in enumerate(block["pairs"]):
                if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                    errors.append("{}: pairs[{}] must be [label, value]".format(where, p_idx))
        if btype == "bullets" and "items" in block and not isinstance(block["items"], list):
            errors.append("{}: 'items' must be an array of strings".format(where))
        if btype == "logo" and block.get("variant") not in (None, "square", "wordmark"):
            errors.append("{}: variant must be 'square' or 'wordmark'".format(where))

        if btype == "status_table":
            n_cols = len(block["headers"]) if isinstance(block.get("headers"), list) else 0
            col = block.get("status_col")
            if col is not None:
                if isinstance(col, bool) or not isinstance(col, int):
                    errors.append("{}: 'status_col' must be a 0-indexed "
                                  "integer".format(where))
                elif n_cols and not -n_cols <= col < n_cols:
                    errors.append("{}: status_col {} is outside the {} "
                                  "columns".format(where, col, n_cols))
            statuses = block.get("statuses")
            if statuses is not None and not isinstance(statuses, dict):
                errors.append("{}: 'statuses' must be an object of "
                              "{{label: '#RRGGBB'}}".format(where))
        if btype == "checklist" and "items" in block:
            if not isinstance(block["items"], list):
                errors.append("{}: 'items' must be an array of strings or "
                              "{{text, done}} objects".format(where))
            else:
                for i_idx, item in enumerate(block["items"]):
                    if isinstance(item, dict):
                        if "text" not in item:
                            errors.append("{}: items[{}] object needs a 'text' "
                                          "key".format(where, i_idx))
                        extra = set(item) - {"text", "done"}
                        if extra:
                            errors.append("{}: items[{}] has unknown key(s) {} "
                                          "(known: text, done)".format(
                                              where, i_idx, ", ".join(sorted(extra))))
                    elif not isinstance(item, str):
                        errors.append("{}: items[{}] must be a string or a "
                                      "{{text, done}} object".format(where, i_idx))
        if btype in ("signoff_table", "milestone_table") and "rows" in block:
            keys = SIGNOFF_KEYS if btype == "signoff_table" else MILESTONE_KEYS
            first = keys[0]
            if not isinstance(block["rows"], list):
                errors.append("{}: 'rows' must be an array of objects with "
                              "{}".format(where, ", ".join(keys)))
            else:
                for r_idx, row in enumerate(block["rows"]):
                    if not isinstance(row, dict):
                        errors.append("{}: rows[{}] must be an object with "
                                      "{}".format(where, r_idx, ", ".join(keys)))
                        continue
                    if first not in row:
                        errors.append("{}: rows[{}] is missing {!r}".format(
                            where, r_idx, first))
                    extra = set(row) - set(keys)
                    if extra:
                        errors.append("{}: rows[{}] has unknown key(s) {} "
                                      "(known: {})".format(where, r_idx,
                                                           ", ".join(sorted(extra)),
                                                           ", ".join(keys)))
            widths = block.get("col_widths_in")
            if widths is not None and (not isinstance(widths, list)
                                       or len(widths) != len(keys)):
                errors.append("{}: 'col_widths_in' must have {} numbers".format(
                    where, len(keys)))
        if btype == "grouped_bullets" and "groups" in block:
            if not isinstance(block["groups"], list):
                errors.append("{}: 'groups' must be an array of "
                              "{{label, items}} objects".format(where))
            else:
                for g_idx, group in enumerate(block["groups"]):
                    if not isinstance(group, dict):
                        errors.append("{}: groups[{}] must be a {{label, items}} "
                                      "object".format(where, g_idx))
                        continue
                    if "label" not in group:
                        errors.append("{}: groups[{}] is missing 'label'".format(
                            where, g_idx))
                    if not isinstance(group.get("items", []), list):
                        errors.append("{}: groups[{}].items must be an array of "
                                      "strings".format(where, g_idx))
                    extra = set(group) - {"label", "items"}
                    if extra:
                        errors.append("{}: groups[{}] has unknown key(s) {} "
                                      "(known: items, label)".format(
                                          where, g_idx, ", ".join(sorted(extra))))

        conflict = _grammar_conflict(btype, style)
        if conflict:
            errors.append("{}: {!r} is a {}-grammar block; style is {!r}".format(
                where, btype, conflict, style))
    return errors


# --------------------------------------------------------------------------
# Type contracts — the ordered slot plan per document type, transcribed from
# references/doc_types.md. One slot may render as several blocks in sequence
# (a heading plus its table); each type pins the grammar it is written in.
#
# req: 'yes' = always · 'cond' = only when the include-rule fires · 'rep' = once
# per item in the source data. A cond slot that doesn't fire is dropped
# entirely, heading included — but it is all-or-nothing, never half a slot.
# --------------------------------------------------------------------------

TYPES = {
    "pricing_summary": {
        "style": "client",
        "reference": "assets/brand/doc_templates/Pricing_Document.docx",
        "purpose": "a specific customer's commercial terms — named client, real figures",
        "audience": "client-facing, at quote stage",
        "length": "1 page",
        "slots": [
            {"slot": "logo", "blocks": ["logo"], "req": "yes",
             "when": "always — square mark, 0.646in"},
            {"slot": "title", "blocks": ["title"], "req": "yes",
             "when": "always"},
            {"slot": "prepared_for", "blocks": ["subtitle"], "req": "yes",
             "when": "always — Prepared for <Client>  ·  <Month YYYY>"},
            {"slot": "lede", "blocks": ["body"], "req": "yes",
             "when": "always — 3-5 sentences naming the headline figures"},
            {"slot": "context", "blocks": ["body"], "req": "yes",
             "when": "always — headroom, what's included, what changes at the "
                     "ceiling"},
            {"slot": "section", "blocks": ["heading"], "req": "yes",
             "when": "always — e.g. Your Investment"},
            {"slot": "fees", "blocks": ["table"], "req": "yes",
             "when": "always — Component / Fee / What it covers, total_row: true",
             "presets": {"table": {"headers": ["Component", "Fee", "What it covers"],
                                   "total_row": True}}},
            {"slot": "section", "blocks": ["heading", "table"], "req": "rep",
             "when": "once per further commercial section"},
        ],
    },
    "meeting_brief": {
        "style": "internal",
        "reference": "assets/brand/doc_templates/Meeting_Brief.docx",
        "purpose": "a one-page prep sheet before a sales or discovery call",
        "audience": "internal only",
        "length": "1 page",
        "slots": [
            {"slot": "title", "blocks": ["internal_title"], "req": "yes",
             "when": "always — Meeting Prep — <Contact>, <Company>"},
            {"slot": "meta", "blocks": ["meta"], "req": "yes",
             "when": "always — day, date, time, platform, meeting type, sourced by"},
            {"slot": "who", "blocks": ["internal_heading", "facts"], "req": "yes",
             "when": "always — Name/Role, Background, Read, Contact"},
            {"slot": "company", "blocks": ["internal_heading", "bullets"], "req": "yes",
             "when": "always — 4 bullets"},
            {"slot": "status", "blocks": ["internal_heading", "bullets"], "req": "yes",
             "when": "always — where things stand, 4 bullets"},
            {"slot": "angle", "blocks": ["internal_heading", "bullets"], "req": "yes",
             "when": "always — angle for the call, 4 bullets"},
            {"slot": "sources", "blocks": ["sources"], "req": "yes",
             "when": "always"},
        ],
    },
    "client_debrief_note": {
        "style": "internal",
        "reference": "the client-debrief-note team skill",
        "purpose": "post-meeting summary — takeaways and action items — "
                   "circulated after a customer session",
        "audience": "the joint attendee list",
        "length": "under 200 words of body",
        "slots": [
            {"slot": "greeting", "blocks": ["body"], "req": "yes",
             "when": "always — Hi All – (em dash, not a comma)"},
            {"slot": "thanks", "blocks": ["body"], "req": "yes",
             "when": "always — one line thanking them for the session"},
            {"slot": "takeaways", "blocks": ["internal_heading", "bullets"], "req": "cond",
             "when": "substantive discussion happened. Up to 4 bullets, bold "
                     "label + description"},
            {"slot": "actions", "blocks": ["internal_heading", "bullets"], "req": "cond",
             "when": "actions were agreed. Org-labelled: [FurtherAI] …, "
                     "[Client team] …"},
            {"slot": "signoff", "blocks": ["body"], "req": "yes",
             "when": "always — one warm contextual line, then Best, and the sender"},
        ],
    },
    "implementation_scoping_plan": {
        "style": "internal",
        "reference": "the implementation-scoping-notion team skill",
        "purpose": "the single source of truth for what an implementation "
                   "covers — scope, workflow, data model, systems, plan, "
                   "acceptance, sign-off",
        "audience": "internal plus customer counterparts",
        "length": "4-8 pages",
        "slots": [
            {"slot": "title", "blocks": ["internal_title"], "req": "yes",
             "when": "<Customer> — <Program>, or just the customer when there's "
                     "no specific LOB"},
            {"slot": "snapshot", "blocks": ["internal_heading", "facts"], "req": "yes",
             "when": "always — legal name, program/LOB, engagement type, stage, "
                     "FurtherAI lead, customer sponsor, counterparts, IT contact, "
                     "invoicing contact, kickoff date, target go-live, Drive "
                     "folder, plan link, data-model link, Slack channel, Linear "
                     "project"},
            {"slot": "context", "blocks": ["internal_heading", "bullets"], "req": "yes",
             "when": "always — users and goal, how it works today, volume"},
            {"slot": "scope", "blocks": ["internal_heading", "body", "status_table"],
             "req": "yes",
             "when": "always — a \"Building:\" paragraph, then in-scope / "
                     "out-of-scope"},
            {"slot": "workflow", "blocks": ["internal_heading", "status_table"],
             "req": "yes",
             "when": "always — # / Step / What happens / Output or hand-off, "
                     "rewritten per engagement",
             "presets": {"status_table": {
                 "headers": ["#", "Step", "What happens", "Output or hand-off"]}}},
            {"slot": "data_model", "blocks": ["internal_heading", "body"], "req": "cond",
             "when": "a field-level data model exists or is being built"},
            {"slot": "systems", "blocks": ["internal_heading", "status_table"],
             "req": "yes",
             "when": "always — System / Purpose / Read-Write / Owner / Access "
                     "status",
             "presets": {"status_table": {
                 "headers": ["System", "Purpose", "Read-Write", "Owner",
                             "Access status"]}}},
            {"slot": "readiness", "blocks": ["internal_heading", "checklist"],
             "req": "yes",
             "when": "always — sample inputs, business rules, templates, "
                     "reference data, named reviewers"},
            {"slot": "milestones", "blocks": ["internal_heading", "milestone_table"],
             "req": "yes", "when": "always"},
            {"slot": "acceptance", "blocks": ["internal_heading", "bullets"], "req": "yes",
             "when": "always — accuracy, coverage, outcome, acceptance"},
            {"slot": "open_items", "blocks": ["internal_heading", "status_table"],
             "req": "yes",
             "when": "always — open questions, risks, assumptions, with owner "
                     "and needed-by date"},
            {"slot": "signoff", "blocks": ["internal_heading", "signoff_table"],
             "req": "yes",
             "when": "always — customer sponsor, FurtherAI lead, EM reviewer; "
                     "name and date"},
        ],
    },
}

#: Empty-but-well-formed content per block type, so a fresh scaffold already
#: passes --check and the caller only fills in copy.
_SCAFFOLD_CONTENT = {
    "logo": {},
    "rule": {},
    "page_break": {},
    "title": {"text": ""},
    "subtitle": {"text": ""},
    "body": {"text": ""},
    "heading": {"text": ""},
    "internal_title": {"text": ""},
    "internal_heading": {"text": ""},
    "meta": {"text": ""},
    "sources": {"text": ""},
    "bullets": {"items": [""]},
    "facts": {"pairs": [["", ""]]},
    "table": {"headers": ["", "", ""], "rows": [["", "", ""]]},
    "status_table": {"headers": ["", "", ""], "rows": [["", "", ""]]},
    "checklist": {"items": [{"text": "", "done": False}]},
    "milestone_table": {"rows": [{"milestone": "", "date": "", "owner": "",
                                  "status": ""}]},
    "signoff_table": {"rows": [{"role": "", "name": "", "date": ""}]},
    "grouped_bullets": {"groups": [{"label": "", "items": [""]}]},
}


def _scaffold_content(btype, preset=None):
    """Empty content for a block, shaped so validation passes as-is."""
    content = json.loads(json.dumps(_SCAFFOLD_CONTENT.get(btype, {})))
    if preset:
        content.update(json.loads(json.dumps(preset)))
    if btype in ("table", "status_table") and content.get("headers"):
        # A preset may name more columns than the generic empty shape has.
        content["rows"] = [[""] * len(content["headers"])]
    return content


def scaffold(type_name):
    """The type's blocks in contract order with empty content.

    Every block carries a ``_slot`` marker; ``cond`` and ``rep`` slots also carry
    ``_when``, so the caller can see which blocks are droppable and delete the
    ones that don't apply. Markers are stripped before validating or building.
    """
    contract = TYPES[type_name]
    blocks = []
    for slot in contract["slots"]:
        presets = slot.get("presets", {})
        for btype in slot["blocks"]:
            block = {"type": btype, "_slot": slot["slot"]}
            if slot["req"] != "yes":
                block["_when"] = "{}: {}".format(slot["req"], slot["when"])
            block.update(_scaffold_content(btype, presets.get(btype)))
            blocks.append(block)
    return {"_type": type_name, "style": contract["style"], "blocks": blocks}


def _align(slots, types):
    """Cheapest alignment of a block-type sequence onto a slot plan.

    Matched slot-first rather than block-first, because ``internal_heading``
    opens most slots and a block-at-a-time walk cannot tell which slot a lone
    heading belongs to: it happily feeds the systems heading to a dropped
    data_model slot and then reports every slot after it as broken.

    So: each unmet requirement costs one error — a missing required slot, half a
    slot, or a block the plan cannot place — while dropping a ``cond`` or
    ``rep`` slot costs nothing, and the alignment that explains the document
    with the fewest complaints wins. A plan is a dozen slots at most, so the
    table is tiny.

    Returns the chosen steps in document order as
    ``(kind, slot_idx, block_idx, consumed)``, kind being one of match /
    partial / skip_slot / extra.
    """
    n_slots, n_blocks = len(slots), len(types)
    inf = float("inf")
    cost = [[inf] * (n_blocks + 1) for _ in range(n_slots + 1)]
    move = [[None] * (n_blocks + 1) for _ in range(n_slots + 1)]
    cost[n_slots][n_blocks] = 0

    # Backwards over both axes: every transition moves right or down, so the
    # states it depends on are already filled in.
    for s_idx in range(n_slots, -1, -1):
        for p_idx in range(n_blocks, -1, -1):
            if s_idx == n_slots and p_idx == n_blocks:
                continue
            best, best_move = inf, None
            if s_idx < n_slots:
                slot = slots[s_idx]
                wanted = slot["blocks"]
                size = len(wanted)
                if types[p_idx:p_idx + size] == wanted:
                    # a rep slot may take its group again before moving on
                    nxt = s_idx if slot["req"] == "rep" else s_idx + 1
                    candidate = cost[nxt][p_idx + size]
                    if candidate < best:
                        best, best_move = candidate, ("match", size, nxt)
                prefix = 0
                while (prefix < size and p_idx + prefix < n_blocks
                       and types[p_idx + prefix] == wanted[prefix]):
                    prefix += 1
                if 0 < prefix < size:
                    candidate = 1 + cost[s_idx + 1][p_idx + prefix]
                    if candidate < best:
                        best, best_move = candidate, ("partial", prefix, s_idx + 1)
                penalty = 1 if slot["req"] == "yes" else 0
                candidate = penalty + cost[s_idx + 1][p_idx]
                if candidate < best:
                    best, best_move = candidate, ("skip_slot", 0, s_idx + 1)
            if p_idx < n_blocks:
                candidate = 1 + cost[s_idx][p_idx + 1]
                if candidate < best:
                    best, best_move = candidate, ("extra", 1, s_idx)
            cost[s_idx][p_idx] = best
            move[s_idx][p_idx] = best_move

    steps, s_idx, p_idx = [], 0, 0
    while move[s_idx][p_idx] is not None:
        kind, consumed, nxt = move[s_idx][p_idx]
        steps.append((kind, s_idx, p_idx, consumed))
        p_idx += consumed
        s_idx = nxt
    return steps


def check_type(spec, type_name):
    """Problems found against a type contract; empty means the spec conforms.

    Asserts the pinned grammar, that every required slot is present, that the
    contract's order is respected, that no block sits outside its own grammar,
    and that nothing the contract doesn't list has been added. A dropped ``cond``
    slot is fine; half a slot is not.
    """
    contract = TYPES.get(type_name)
    if contract is None:
        return ["unknown type {!r} — run --list-types for the supported "
                "set".format(type_name)]
    spec = strip_markers(spec)
    errors = []
    style = spec.get("style", "client")
    if style != contract["style"]:
        errors.append("style: {!r} is written in the {!r} grammar, spec says "
                      "{!r}".format(type_name, contract["style"], style))
    blocks = spec.get("blocks")
    if not isinstance(blocks, list):
        return errors + ["spec has no 'blocks' array to check against "
                         "{!r}".format(type_name)]

    slots = contract["slots"]
    sanctioned = {b for slot in slots for b in slot["blocks"]}

    def slot_label(idx):
        slot = slots[idx]
        return "slot {} {!r} ({})".format(idx + 1, slot["slot"],
                                          " + ".join(slot["blocks"]))

    for idx, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        conflict = _grammar_conflict(btype, contract["style"])
        # validate() already reports conflicts against the *declared* style; only
        # say something when the declared style hid one the contract catches.
        if conflict and not _grammar_conflict(btype, style):
            errors.append("block[{}] ({}): a {}-grammar block in {!r}, which is "
                          "an {} document".format(idx, btype, conflict, type_name,
                                                  contract["style"]))

    types = [block.get("type") if isinstance(block, dict) else None
             for block in blocks]
    ends_at = ("the document ends at block[{}]".format(len(blocks) - 1) if blocks
               else "the spec has no blocks")
    for kind, s_idx, p_idx, consumed in _align(slots, types):
        if kind == "match":
            continue
        if kind == "partial":
            errors.append("block[{}]: {} is half-filled — missing {}; a slot is "
                          "all-or-nothing".format(
                              p_idx, slot_label(s_idx),
                              " + ".join(slots[s_idx]["blocks"][consumed:])))
        elif kind == "skip_slot":
            if slots[s_idx]["req"] != "yes":
                continue  # a dropped cond/rep slot is the contract working
            if p_idx < len(blocks):
                errors.append("block[{}]: required {} is missing before this "
                              "block".format(p_idx, slot_label(s_idx)))
            else:
                errors.append("required {} is missing — {}".format(
                    slot_label(s_idx), ends_at))
        elif types[p_idx] in sanctioned:
            errors.append("block[{}] ({}): out of contract order — {!r} expects "
                          "it {}".format(p_idx, types[p_idx], type_name,
                                         _expected_at(slots, types[p_idx])))
        else:
            errors.append("block[{}] ({}): not a block {!r} sanctions — run "
                          "--list-types".format(p_idx, types[p_idx], type_name))
    return errors


def _expected_at(slots, btype):
    """Where the contract puts a block, for an out-of-order message."""
    where = [str(idx + 1) for idx, slot in enumerate(slots) if btype in slot["blocks"]]
    if not where:
        return "nowhere"
    if len(where) > 3:  # internal_heading opens most slots; the list stops helping
        return "at slots {} and {} more".format(", ".join(where[:3]), len(where) - 3)
    return "at slot {}".format(", ".join(where))


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------


def build(spec, out_path):
    spec = strip_markers(spec)
    style = spec.get("style", "client")
    doc = dk.new_doc(style)
    content_width = dk.CONTENT_WIDTH[style]

    for block in spec["blocks"]:
        btype = block["type"]
        opts = dict(BLOCKS[btype]["optional"])
        opts.update({k: v for k, v in block.items() if k != "type"})

        if btype == "logo":
            dk.logo(doc, variant=opts["variant"], width_in=opts["width_in"],
                    space_after=opts["space_after"])
        elif btype == "title":
            dk.title(doc, opts["text"], space_after=opts["space_after"])
        elif btype == "subtitle":
            dk.subtitle(doc, opts["text"], space_after=opts["space_after"])
        elif btype == "body":
            dk.body(doc, opts["text"], space_after=opts["space_after"],
                    size=opts["size"], color=opts["color"],
                    bold=opts["bold"], italic=opts["italic"])
        elif btype == "heading":
            dk.section_heading(doc, opts["text"], space_before=opts["space_before"],
                               space_after=opts["space_after"])
        elif btype == "rule":
            dk.rule(doc, color=opts["color"], space_after=opts["space_after"], sz=opts["sz"])
        elif btype == "table":
            widths = opts["col_widths_in"] or _default_col_widths(
                opts["headers"], content_width)
            dk.brand_table(doc, opts["headers"], opts["rows"], widths,
                           total_row=bool(opts["total_row"]))
        elif btype == "status_table":
            _status_table(doc, style, opts["headers"], opts["rows"],
                          status_col=opts["status_col"], statuses=opts["statuses"],
                          col_widths_in=opts["col_widths_in"], size=opts["size"])
        elif btype == "milestone_table":
            _milestone_table(doc, style, opts["rows"],
                             col_widths_in=opts["col_widths_in"])
        elif btype == "signoff_table":
            _signoff_table(doc, style, opts["rows"],
                           col_widths_in=opts["col_widths_in"])
        elif btype == "bullets":
            dk.bullets(doc, opts["items"], size=opts["size"], space_after=opts["space_after"])
        elif btype == "checklist":
            _checklist(doc, style, opts["items"], size=opts["size"],
                       space_after=opts["space_after"])
        elif btype == "grouped_bullets":
            _grouped_bullets(doc, style, opts["groups"], size=opts["size"],
                             space_after=opts["space_after"])
        elif btype == "internal_title":
            dk.internal_title(doc, opts["text"], space_after=opts["space_after"])
        elif btype == "internal_heading":
            dk.internal_heading(doc, opts["text"], space_before=opts["space_before"],
                                space_after=opts["space_after"])
        elif btype == "meta":
            dk.meta_line(doc, opts["text"], space_after=opts["space_after"])
        elif btype == "sources":
            dk.sources_line(doc, opts["text"], space_before=opts["space_before"])
        elif btype == "facts":
            dk.label_value_table(doc, opts["pairs"], label_w=opts["label_w"],
                                 value_w=opts["value_w"], size=opts["size"])
        elif btype == "page_break":
            dk.page_break(doc)

    embed = spec.get("embed_fonts", True) and style == "client"
    path = dk.save(doc, out_path, embed=embed)
    return path, embed


def list_blocks():
    lines = ["Document spec:",
             '  {"style": "client"|"internal", "embed_fonts": true, "blocks": [...]}',
             "",
             "  style 'client'   — Pricing_Document grammar: Wix Madefor Display 11pt",
             "                     #2B2D31 defaults, margins L/R 1.0in T/B 0.9in,",
             "                     6.5in content width, brand fonts embedded on save.",
             "  style 'internal' — Meeting_Brief grammar: Arial 10pt, 0.75in margins,",
             "                     7.0in content width, no logo, no brand colors.",
             "",
             "Blocks:"]
    for name in sorted(BLOCKS):
        schema = BLOCKS[name]
        grammar = ("client" if name in CLIENT_BLOCKS
                   else "internal" if name in INTERNAL_BLOCKS else "both")
        lines.append("")
        lines.append("  {}   [{}]".format(name, grammar))
        required = ", ".join(schema["required"]) or "(none)"
        lines.append("    required: {}".format(required))
        if schema["optional"]:
            optional = ", ".join("{}={}".format(k, json.dumps(v))
                                 for k, v in schema["optional"].items())
            lines.append("    optional: {}".format(optional))
        for chunk in _wrap(schema["doc"], 72):
            lines.append("    " + chunk)
    lines.append("")
    lines.append("Example:")
    lines.append(json.dumps(EXAMPLE, indent=2))
    lines.append("")
    lines.append("Type contracts (which blocks a kind of document is made of, "
                 "and in what")
    lines.append("order): run --list-types.")
    return "\n".join(lines)


def list_types():
    lines = ["Document type contracts — which blocks a kind of document is made",
             "of, and in what order. The content rules live in",
             "references/doc_types.md; read that before composing.",
             "",
             "  python3 build_doc.py --type NAME --scaffold > spec.json",
             "  python3 build_doc.py spec.json --check --type NAME",
             "  python3 build_doc.py spec.json --out doc.docx",
             "",
             "req  yes  = always",
             "     cond = only when the include-rule fires. A cond slot that",
             "            doesn't fire is dropped entirely, heading included —",
             "            but never half of it.",
             "     rep  = once per item in the source data.",
             "",
             "Do not reorder, and do not insert a block the contract omits;",
             "--check --type reports both, with the block index."]
    for name, contract in TYPES.items():
        slots = contract["slots"]
        lines.append("")
        lines.append("  {}   [{}]   {}".format(name, contract["style"],
                                               contract["length"]))
        for chunk in _wrap(contract["purpose"], 68):
            lines.append("    " + chunk)
        lines.append("    audience:  {}".format(contract["audience"]))
        lines.append("    reference: {}".format(contract["reference"]))
        lines.append("")
        width = max(len(slot["slot"]) for slot in slots)
        for idx, slot in enumerate(slots, 1):
            lines.append("    {:>2}  {:<{w}}  {:<4}  {}".format(
                idx, slot["slot"], slot["req"], " + ".join(slot["blocks"]), w=width))
            for chunk in _wrap(slot["when"], 62):
                lines.append("        " + chunk)
    lines.append("")
    lines.append("PDF-only types — pricing_overview, capability_onepager,")
    lines.append("workflow_onepager, api_integration_guide — live in")
    lines.append("build_pdf.py --list-types.")
    return "\n".join(lines)


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
    "style": "client",
    "blocks": [
        {"type": "logo"},
        {"type": "title", "text": "Pricing Summary"},
        {"type": "subtitle", "text": "Prepared for <Client>  ·  <Month Year>"},
        {"type": "rule"},
        {"type": "body", "text": "One or two short paragraphs framing the numbers."},
        {"type": "heading", "text": "Your Investment"},
        {"type": "table",
         "headers": ["Component", "Fee", "What it covers"],
         "rows": [["Platform fee", "$0 / month", "Line one\nLine two"],
                  ["Usage fee", "$0 / month", "Cap and overage terms"],
                  ["Total annual investment", "$0", "$0/mo"]],
         "col_widths_in": [1.701, 1.701, 3.097],
         "total_row": True},
    ],
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("spec", nargs="?", help="path to the JSON document spec ('-' for stdin)")
    parser.add_argument("--out", help="output .docx path")
    parser.add_argument("--check", action="store_true", help="validate the spec, build nothing")
    parser.add_argument("--list-blocks", action="store_true",
                        help="print the block API reference and exit")
    parser.add_argument("--list-types", action="store_true",
                        help="print the document type contracts and exit")
    parser.add_argument("--type", dest="doc_type", metavar="NAME",
                        help="check the spec against a type contract "
                             "(run --list-types for the names)")
    parser.add_argument("--scaffold", action="store_true",
                        help="with --type, print that type's blocks in contract "
                             "order with empty content, and exit")
    args = parser.parse_args(argv)

    if args.list_blocks:
        print(list_blocks())
        return 0
    if args.list_types:
        print(list_types())
        return 0
    if args.doc_type and args.doc_type not in TYPES:
        sys.stderr.write("unknown type {!r} — known types: {}\n".format(
            args.doc_type, ", ".join(TYPES)))
        return 1
    if args.scaffold:
        if not args.doc_type:
            parser.error("--scaffold needs --type NAME")
        if args.out:
            parser.error("--scaffold writes the spec to stdout; redirect it "
                         "(--out is for the built .docx)")
        print(json.dumps(scaffold(args.doc_type), indent=2))
        return 0
    if not args.spec:
        parser.error("a spec path is required (or --list-blocks / --list-types "
                     "/ --type NAME --scaffold)")

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

    # A scaffolded spec remembers its own type, so --type is optional on a
    # round-trip; an explicit --type always wins.
    doc_type = args.doc_type
    if doc_type is None and isinstance(spec, dict):
        remembered = spec.get("_type")
        if remembered in TYPES:
            doc_type = remembered

    errors = validate(spec)
    if doc_type:
        errors += check_type(spec, doc_type)
    if errors:
        sys.stderr.write("spec has {} problem(s):\n".format(len(errors)))
        for error in errors:
            sys.stderr.write("  - {}\n".format(error))
        return 2
    if args.check:
        print("spec OK — {} blocks, style '{}'{}".format(
            len(spec["blocks"]), spec.get("style", "client"),
            ", type '{}' ({} slots)".format(doc_type, len(TYPES[doc_type]["slots"]))
            if doc_type else ""))
        return 0

    if not args.out:
        sys.stderr.write("--out is required when building\n")
        return 1
    path, embedded = build(spec, args.out)
    print("DOCX      {}".format(path.resolve()))
    print("BLOCKS    {}".format(len(spec["blocks"])))
    print("STYLE     {}".format(spec.get("style", "client")))
    if doc_type:
        print("TYPE      {}".format(doc_type))
    print("FONTS     {}".format("embedded" if embedded else "not embedded"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
