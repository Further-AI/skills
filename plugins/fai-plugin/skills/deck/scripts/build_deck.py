#!/usr/bin/env python3
"""Build a branded .pptx from a JSON deck spec.

    python3 build_deck.py spec.json --out deck.pptx
    python3 build_deck.py spec.json --check          # validate only, build nothing
    python3 build_deck.py --list-patterns            # the API reference
    python3 build_deck.py --list-types               # the deck type contracts
    python3 build_deck.py --type NAME --scaffold     # that type's slides, empty
    python3 build_deck.py spec.json --check --type NAME   # + type conformance

Spec shape:

    {
      "canvas": [13.3333, 7.5],         # optional; OMIT it to get exactly
                                        # 12192000 x 6858000 EMU
      "theme": "brand",                 # brand | consulting
      "page_numbers": true,             # optional, default true
      "footer": {"left": "AI Workspace for Insurance",
                 "center": "furtherai.com"},
      "slides": [
        {"pattern": "cover", "headline": "Agentic Workspace for Insurance"},
        {"pattern": "agenda", "title": "Agenda", "items": ["...", "..."]}
      ]
    }

Validation runs over the WHOLE spec before anything is built, so every unknown
pattern and missing field is reported at once, with its slide index.

A spec may carry `_slot` / `_optional` / `_when` annotations from --scaffold.
They are the type-contract bookkeeping, are stripped before validation, and
never reach deck_kit.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import deck_kit as kit                                        # noqa: E402
import deck_types as dtypes                                   # noqa: E402

# Per-pattern field documentation: pattern -> field -> (type, description).
# ``type`` is enforced for list / dict; anything else is free-form.
# This table IS the model-facing API reference printed by --list-patterns.
FIELDS = {
    "cover": {
        "headline": ("str", "the big serif line, ~2 lines at 49.3pt"),
        "subhead": ("str", "one supporting line under the headline"),
        "eyebrow": ("str", "small all-caps kicker above the headline"),
        "background": ("path", "full-bleed image; omit to use the brand gradient"),
        "gradient": ("list", '[[0.0,"#074B40"],[1.0,"#203E13"]] stop list'),
        "client_logo": ("path", "client logo dropped at L0.647 T5.508 W1.454"),
        "client_logo_label": ("str", '"ADD LOGO HERE" style drop-zone text'),
    },
    "section_divider": {
        "title": ("str", "section name, Fraunces Light on the gradient"),
        "nav": ("list", '["Module", ...] small labels under the title'),
        "eyebrow": ("str", "kicker above the title"),
    },
    "agenda": {
        "title": ("str", 'panel title, e.g. "Agenda"'),
        "items": ("list", '["text", ...] or [{"text","note"}, ...]'),
        "panel_title": ("str", "small caps line above the panel title"),
    },
    "before_after": {
        "title": ("str", "slide title"),
        "left": ("dict", '{"eyebrow":"FROM","title":str,"bullets":[str,...]}'),
        "right": ("dict", '{"eyebrow":"TO","title":str,"bullets":[str,...]}'),
        "subtitle": ("str", "sub-headline under the title"),
        "arrow": ("str", 'glyph between the cards, default "->"'),
        "recommend": ("str", '"right" (default) | "left" | null -- which side wins'),
    },
    "two_pillars": {
        "title": ("str", "slide title"),
        "pillars": ("list", '[{"eyebrow","title","summary",'
                            '"blocks":[["label","text"],...],"output",'
                            '"focus":bool}] -- max 2'),
        "subtitle": ("str", "sub-headline"),
        "legend": ("str", 'legend chip text for the tinted pillar, e.g. "Current focus"'),
    },
    "numbered_steps": {
        "title": ("str", "slide title"),
        "steps": ("list", '["title", ...] or [{"title","caption"}, ...]'),
        "eyebrow": ("str", "card header kicker"),
        "header": ("str", "card header title"),
        "options": ("list", '[{"title","bullets":[str,...]}] right-hand panel'),
        "status": ("str", "green status line, lower right"),
        "note": ("str", "open question / risk, rendered in #C00000"),
    },
    "scorecard": {
        "title": ("str", "slide title"),
        "dimensions": ("list", '[{"label","levels":[str,str,str],'
                               '"selected":0|1|2}]'),
        "narrative": ("list", '["bullet", ...] left column'),
        "narrative_title": ("str", "heading above the left column"),
        "eyebrow": ("str", "card header kicker"),
        "header": ("str", "card header title"),
    },
    "process_audit": {
        "title": ("str", "slide title"),
        "steps": ("list", '[{"title","caption"}, ...] the as-is process'),
        "issues": ("list", '[{"code":"M1","title","detail",'
                           '"severity":"critical|high|med"}]'),
        "meta": ("str", "italic owner / duration / tools strip"),
        "left_header": ("str", "left pane header, default CURRENT PROCESS - DETAILED"),
        "right_header": ("str", "right pane header, default MANUAL ACTIVITIES IDENTIFIED"),
    },
    "rag_matrix": {
        "title": ("str", "slide title"),
        "rows": ("list", 'arrows mode: criteria labels; summary mode: option labels'),
        "cols": ("list", "arrows mode: option column labels"),
        "cells": ("list", 'arrows: [[{"rating":"up|side|down","text"},...],...]; '
                          'summary: ["verdict sentence", ...]'),
        "subtitle": ("str", "sub-headline"),
        "mode": ("str", '"arrows" (default) | "summary"'),
        "legend": ("bool", "show the favorable/neutral/unfavorable key"),
        "row_header": ("str", 'corner label, default "Metrics"'),
        "summary_header": ("str", "summary-mode column header"),
    },
    "timeline": {
        "title": ("str", "slide title"),
        "periods": ("list", '["Apr","May", ...] column headers'),
        "lanes": ("list", '[{"label","start":int,"span":int,"output",'
                          '"milestones":[float period offsets]}]'),
        "today": ("dict", '{"label":"Today (5/14)","at": float period offset}'),
        "output_header": ("str", 'right column header, default "Key outputs"'),
        "legend": ("bool", "show the milestone / current-focus key"),
    },
    "next_steps": {
        "items": ("list", '["action", ...] or [["Owner","action"], ...] -- the '
                          'owner becomes a green chip'),
        "title": ("str", 'slide title, default "Next steps"'),
        "subtitle": ("str", "sub-headline under the title"),
    },
    "phase_chevrons": {
        "title": ("str", "slide title"),
        "phases": ("list", '["Phase 1","Phase 2","Phase 3"] chevron labels'),
        "rows": ("list", '["Customer first","Solutioning first"] row labels'),
        "cells": ("list", '[[{"title","body"}, ...], ...] rows x phases'),
        "recommendation": ("str", "summary box under the matrix"),
    },
    "pricing_table": {
        "title": ("str", "slide title"),
        "headers": ("list", '["Component","Description","Fee type"]'),
        "rows": ("list", "[[cell, cell, cell], ...] -- cells may be strings"),
        "col_widths": ("list", "inches per column, default [2.3, 6.0, 2.5]"),
        "note": ("str", "brace annotation to the right of the table"),
        "section_title": ("str", "heading above the option cards"),
        "options": ("list", '[{"title","body"}] -- up to 3 option cards'),
    },
    "architecture_hero": {
        "headline": ("str", "centred Fraunces Light headline"),
        "left_label": ("str", "left node label"),
        "right_label": ("str", "right node label"),
        "props": ("list", '["value prop", ...] captions across the lower half'),
        "certs": ("list", '["SOC2","GDPR","ISO 27001","HIPAA"]'),
        "background": ("path", "full-bleed art; omit for the dark gradient"),
    },
    "use_case_rail": {
        "category": ("str", 'category title, e.g. "Distribution"'),
        "items": ("list", '[{"title","body"}, ...] -- 4 reads best'),
        "screenshot": ("path", "product shot; omit for a placeholder panel"),
        "dark": ("bool", "dark left panel variant (Enterprise 19-23)"),
    },
    "agent_diagram": {
        "title": ("str", "banner title"),
        "hub": ("str", 'centre card label; pass "" to drop the wordmark in instead'),
        "nodes": ("list", '[{"title","body"}] or ["label", ...]'),
        "subtitle": ("str", "line under the banner title"),
    },
    "security_trust": {
        "headline": ("str", "two-line Fraunces headline"),
        "certs": ("list", '["SOC2","GDPR","ISO 27001","HIPAA"] left rail'),
        "cards": ("list", '[{"title","body"}] -- 4 stacked cards'),
    },
    "why_us": {
        "headline": ("str", 'e.g. "Why FurtherAI?"'),
        "body": ("str", "the long differentiation paragraph"),
        "callouts": ("list", '[{"title","body"}] -- max 3'),
        "screenshot": ("path", "product shot; omit for a placeholder panel"),
    },
    "closing": {
        "message": ("str", "optional line beside the logo; omit to centre the logo"),
        "background": ("path", "full-bleed art; omit for the brand gradient"),
        "footer_left": ("str", "footer credit, left"),
        "footer_center": ("str", "footer credit, right"),
    },
    "stat_hero": {
        "title": ("str", "slide title"),
        "stats": ("list", '[{"value":"95%","label":"..."}]'),
        "eyebrow": ("str", "kicker above the title"),
        "subtitle": ("str", "deck line under the title"),
        "disclaimer": ("str", "small italic line under the tiles"),
        "cols": ("int", "tiles per row, default 3"),
        "dark": ("bool", "green background (default true)"),
    },
    "card_grid": {
        "title": ("str", "slide title"),
        "cards": ("list", '[{"title","body","badge","cap":bool}]'),
        "eyebrow": ("str", "kicker above the title"),
        "subtitle": ("str", "deck line under the title"),
        "cols": ("int", "grid columns, default 2"),
        "dark": ("bool", "green background (default false)"),
    },
    "data_table": {
        "title": ("str", "Fraunces Light green title"),
        "headers": ("list", "column header strings"),
        "rows": ("list", "[[cell, ...], ...]"),
        "subtitle": ("str", "line under the title"),
        "col_widths": ("list", "inches per column"),
        "total_row": ("bool", "tint the last row #D9EAD3 (default false)"),
        "label_column": ("bool", "tint column 1 #FBFBF9 (default true)"),
    },
    "workstream_swimlane": {
        "title": ("str", "slide title"),
        "rows": ("list", '["label", ...] or [{"label","owner"}, ...] -- owner '
                         'renders italic + muted in the left rail'),
        "columns": ("list", '["Wk 1", ...] or ["Jun", ...] column headers'),
        "bars": ("list", '[{"row":int,"start":int,"span":int,"label",'
                         '"kind":"normal|critical|buffer"}] -- row/start '
                         '0-indexed, span in columns'),
        "gates": ("list", '[{"label","at":float,"sublines":[str]}] -- diamond '
                          'plus caption under the grid; at may be fractional'),
        "today": ("dict", '{"label":"Today (5/14)","at": float column offset}'),
        "legend": ("bool", "show the milestone / critical-path / buffer key"),
        "subtitle": ("str", "sub-headline under the title"),
    },
    "two_column_list": {
        "title": ("str", "slide title"),
        "mode": ("str", '"columns" (default) | "ledger"'),
        "columns": ("list", 'mode=columns: [{"header","items":[str, ...]}] -- '
                            'exactly 2'),
        "headers": ("list", "mode=ledger: [str, str] the two column headers"),
        "rows": ("list", 'mode=ledger: [{"left","right"}, ...] paired rows'),
        "subtitle": ("str", "sub-headline under the title"),
    },
    "stepper_row": {
        "title": ("str", "slide title"),
        "steps": ("list", '[{"title","body","number"}] -- auto-numbered when '
                          '"number" is absent; 4-6 reads best'),
        "connector": ("str", '"triangle" (default) | "arrow" | "none"'),
        "terminal_inverted": ("bool", "render the LAST card dark fill / light text"),
        "callout": ("dict", '{"text","at":int} -- flag pinned above step index at'),
        "badge": ("str", "pill flag, top right, for an unresolved item"),
        "subtitle": ("str", "sub-headline under the title"),
    },
    "status_table": {
        "title": ("str", "slide title"),
        "headers": ("list", "column header strings"),
        "rows": ("list", '[[cell, ...], ...] -- "\\n" splits a cell onto more '
                         'than one line'),
        "status_col": ("int", "0-indexed column whose value colours its cell fill"),
        "statuses": ("dict", '{"Complete":"#D9EAD3", ...} override the default '
                             'map, matched case-insensitively'),
        "col_widths": ("list", "inches per column"),
        "note": ("str", "small line under the table"),
        "subtitle": ("str", "sub-headline under the title"),
    },
    "step_detail_io": {
        "title": ("str", "slide title"),
        "inputs": ("list", "[str] top row left, header INPUTS"),
        "does": ("list", "[str] top row middle, header FURTHERAI DOES"),
        "outputs": ("list", "[str] top row right, header OUTPUTS"),
        "hitl": ("list", "[str] wider card below left, header HUMAN REVIEW"),
        "needs": ("list", "[str] wider card below right, header WHAT WE NEED"),
        "phase": ("str", 'small badge, e.g. "PHASE 2"'),
        "confirm": ("str", '"TO CONFIRM" callout for anything unresolved'),
        "subtitle": ("str", "sub-headline under the title"),
    },
}
# Fields every pattern shares.
COMMON_FIELDS = {"page": ("int", "page number in the footer; auto-filled")}


def field_doc(pattern, field):
    return FIELDS.get(pattern, {}).get(field) or COMMON_FIELDS.get(field)


# Recolour map for porting a systematic navy/teal deck onto the brand palette
# (spec section C.3).  Kept as documentation only -- that navy/teal set is a
# client one-off and is deliberately NOT shipped as a token set.
SYSTEMATIC_RECOLOR = [
    ("#16233F navy background", "#074B40 green_deep"),
    ("#0E1730 deeper navy", "#0B0B12 black_deep"),
    ("#1E2F54 card on navy", "#1D4438 green_dark_text"),
    ("#12B5A6 teal accent", "#25654F green"),
    ("#0E8C82 teal on light", "#074B40 green_deep"),
    ("#F4F7FC light card", "#F8F7F5 card"),
    ("#DDE6F2 card border", "#D0CEC3 tan"),
    ("#CADCFC body on dark", "#D0CEC3 tan"),
    ("#5A6B86 muted", "#595959 muted"),
    ("#33415C body on light", "#2B2D31 ink_alt"),
    ("Georgia", "Fraunces 72pt Light"),
    ("Calibri", "Wix Madefor Display"),
]


class SpecError(Exception):
    pass


def _contract_puts_next_steps_after(plan, slot):
    """Does `plan` put next_steps straight after `slot`?

    cond / rep slots in between do not count -- they may be dropped, which
    still leaves the two adjacent.  True when the slot is unrecognised, so an
    unknown slot keeps the generic advice.
    """
    names = [s["slot"] for s in plan]
    if slot not in names:
        return True
    for s in plan[names.index(slot) + 1:]:
        if "next_steps" in dtypes.slot_patterns(s):
            return True
        if s["req"] == "yes":
            return False
    return False


def warnings_for(spec, plan=None, assignments=None):
    """Non-fatal advice.  Never blocks a build.

    `plan` / `assignments` come from check_type.  Where a type contract puts
    required slides between the timeline and next steps -- implementation_checkin
    does, and so does a kickoff whose timeline is the `where_we_are` orientation
    slide -- the contract wins and the generic advice is dropped.
    """
    out = []
    slides = spec.get("slides")
    if not isinstance(slides, list):
        return out
    for i, s in enumerate(slides, start=1):
        if not isinstance(s, dict) or s.get("pattern") != "timeline":
            continue
        if plan and assignments and i - 1 < len(assignments):
            slot = assignments[i - 1]
            if slot and not _contract_puts_next_steps_after(plan, slot):
                continue
        nxt = slides[i] if i < len(slides) else None
        if not (isinstance(nxt, dict) and nxt.get("pattern") == "next_steps"):
            out.append('slide %d (timeline): no "next_steps" slide follows it. '
                       "Next steps always get their own slide, immediately "
                       "after the timeline -- never folded into it." % i)
    return out


def validate(spec):
    """Return a list of human-readable problems; empty means the spec is good."""
    problems = []
    if not isinstance(spec, dict):
        return ["spec root must be a JSON object"]

    theme = spec.get("theme", "brand")
    if theme not in kit.THEMES:
        problems.append('theme: %r is not one of %s'
                        % (theme, " | ".join(sorted(kit.THEMES))))
    canvas = spec.get("canvas")
    if canvas is not None:
        if (not isinstance(canvas, (list, tuple)) or len(canvas) != 2
                or not all(isinstance(v, (int, float)) for v in canvas)):
            problems.append("canvas: must be [width_in, height_in]")
    footer = spec.get("footer")
    if footer is not None and not isinstance(footer, dict):
        problems.append('footer: must be an object with "left" / "center"')

    slides = spec.get("slides")
    if not isinstance(slides, list) or not slides:
        problems.append('slides: must be a non-empty list')
        return problems

    for i, s in enumerate(slides, start=1):
        tag = "slide %d" % i
        if not isinstance(s, dict):
            problems.append("%s: must be an object" % tag)
            continue
        name = s.get("pattern")
        if not name:
            problems.append('%s: missing "pattern" (one of: %s)'
                            % (tag, ", ".join(sorted(kit.PATTERNS))))
            continue
        if name not in kit.PATTERNS:
            problems.append('%s: unknown pattern %r -- valid patterns are: %s'
                            % (tag, name, ", ".join(sorted(kit.PATTERNS))))
            continue
        _fn, required, optional = kit.PATTERNS[name]
        allowed = set(required) | set(optional) | {"pattern", "notes"}
        for field in required:
            if field not in s or s[field] in (None, "", [], {}):
                doc = field_doc(name, field) or ("", "value")
                problems.append('%s (%s): missing required field "%s"  [%s: %s]'
                                % (tag, name, field, doc[0], doc[1]))
        for key in s:
            if key not in allowed:
                problems.append('%s (%s): unknown field "%s" -- allowed: %s'
                                % (tag, name, key,
                                   ", ".join(sorted(allowed - {"pattern", "notes"}))))
        for key, value in s.items():
            doc = field_doc(name, key)
            if not doc or key not in allowed:
                continue
            if doc[0] == "list" and not isinstance(value, list):
                problems.append('%s (%s): "%s" must be a list -- %s'
                                % (tag, name, key, doc[1]))
            elif doc[0] == "dict" and not isinstance(value, dict):
                problems.append('%s (%s): "%s" must be an object -- %s'
                                % (tag, name, key, doc[1]))
            elif doc[0] == "int" and not isinstance(value, int):
                problems.append('%s (%s): "%s" must be an integer -- %s'
                                % (tag, name, key, doc[1]))
            elif doc[0] == "bool" and not isinstance(value, bool):
                problems.append('%s (%s): "%s" must be true/false -- %s'
                                % (tag, name, key, doc[1]))
    return problems


def _coerce(name, kwargs):
    """JSON has no tuples; convert the few places the kit expects them."""
    if "gradient" in kwargs and kwargs["gradient"]:
        kwargs["gradient"] = [tuple(stop) for stop in kwargs["gradient"]]
    if name == "two_pillars":
        for p in kwargs.get("pillars", []):
            if isinstance(p, dict) and p.get("blocks"):
                p["blocks"] = [tuple(b) for b in p["blocks"]]
    return kwargs


def build(spec, out_path):
    theme = spec.get("theme", "brand")
    kit.set_theme(theme)
    canvas = spec.get("canvas") or list(kit.CANVAS)
    prs = kit.new_deck(canvas[0], canvas[1])
    footer_cfg = spec.get("footer") or {}
    numbers = spec.get("page_numbers", True)

    for i, s in enumerate(spec["slides"], start=1):
        name = s["pattern"]
        fn, required, optional = kit.PATTERNS[name]
        kwargs = {k: v for k, v in s.items() if k not in ("pattern", "notes")}
        if numbers and "page" in optional and "page" not in kwargs:
            kwargs["page"] = i
        if name in ("cover", "closing"):
            if footer_cfg.get("left") and "footer_left" not in kwargs:
                kwargs["footer_left"] = footer_cfg["left"]
            if footer_cfg.get("center") and "footer_center" not in kwargs:
                kwargs["footer_center"] = footer_cfg["center"]
        fn(prs, **_coerce(name, kwargs))

    out_path = Path(out_path).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out_path))
    return prs, out_path


def list_patterns(verbose=True):
    print("deck patterns -- pass one as \"pattern\" in each slide object")
    print("")
    for name in sorted(kit.PATTERNS):
        fn, required, optional = kit.PATTERNS[name]
        doc = (fn.__doc__ or "").strip().split("\n")[0]
        print("%s" % name)
        print("    %s" % doc)
        print("    required: %s" % (", ".join(required) or "(none)"))
        print("    optional: %s" % ", ".join(optional))
        if verbose:
            for field in list(required) + list(optional):
                doc = field_doc(name, field)
                if doc:
                    print("      %-18s %-5s %s" % (field, doc[0], doc[1]))
        print("")
    print("themes: %s" % " | ".join(sorted(kit.THEMES)))
    print("  brand      product palette #074B40 / #25654F, 4-stop cover gradient")
    print("  consulting analysis palette #143524 / #2C7A4B, flat gradient")
    print("  The analytical patterns always use the consulting greens, matching")
    print("  Example_Slides 2-12; the theme only moves covers, dividers, closings")
    print("  and the generic card / stat / table chrome.")
    print("")
    print("fonts: set by the kit from fonts.py FAMILY --")
    for key, fam in kit.FAMILY.items():
        print("  %-16s %s" % (key, fam))
    print("  Run `python3 fonts.py check` first; the template names")
    print("  'Fraunces Light' / 'Fraunces 9pt' / bare 'Fraunces' are never used")
    print("  because none of them resolve from the vendored TTFs.")
    print("")
    print("porting a systematic navy/teal layout onto the brand palette")
    print("(spec C.3) -- the navy/teal set is a client one-off, not a brand token set:")
    for src, dst in SYSTEMATIC_RECOLOR:
        print("  %-28s -> %s" % (src, dst))


# ======================================================== type contracts ====
# deck_types.py holds the contracts; everything below reads them.  A contract
# says which slides a kind of deck is made of and in what order -- see
# references/deck_types.md for the prose version and the content rules.

SLOT_W, PAT_W = 18, 20
TABLE_INDENT = " " * (6 + SLOT_W + 1 + PAT_W + 1 + 5 + 1)


def _slot_row(num, slot):
    """One row of the --list-types slot table, wrapped on the 'include when'."""
    when = slot.get("when") or ""
    head = "  %2s  %-*s %-*s %-5s " % (num, SLOT_W, slot["slot"],
                                       PAT_W, "/".join(dtypes.slot_patterns(slot)),
                                       slot["req"])
    lines = textwrap.wrap(when, 92 - len(head)) or [""]
    out = [head + lines[0]]
    out += [TABLE_INDENT + extra for extra in lines[1:]]
    return out


def _slot_table(plan):
    lines = ["   #  %-*s %-*s %-5s %s"
             % (SLOT_W, "slot", PAT_W, "pattern", "req", "include when")]
    section = None
    for i, slot in enumerate(plan, start=1):
        if slot.get("section") and slot["section"] != section:
            section = slot["section"]
            lines.append("  -- section: %s (%s)" % (section, slot["section_req"]))
        lines += _slot_row(i, slot)
    return lines


def list_types():
    print('deck types -- pass one as --type NAME')
    print("")
    for name in dtypes.type_names():
        spec = dtypes.get(name)
        lo, hi = dtypes.length_range(name)
        print("%s" % name)
        for line in textwrap.wrap(spec["purpose"], 86):
            print("    %s" % line)
        for line in textwrap.wrap("audience: " + spec["audience"], 86):
            print("    %s" % line)
        print("    theme: %-11s length: %s slides"
              % (spec["theme"], lo if lo == hi else "%d-%d" % (lo, hi)))
        for line in textwrap.wrap("sources: " + spec["sources"], 86):
            print("    %s" % line)
        if spec.get("note"):
            for line in textwrap.wrap("note: " + spec["note"], 86):
                print("    %s" % line)
        forms = dtypes.forms_for(name)
        if forms:
            for line in textwrap.wrap("form rule: " + dtypes.form_rule(name), 86):
                print("    %s" % line)
            for form in forms:
                flo, fhi = dtypes.length_range(name, form)
                mark = "  (default)" if form == dtypes.default_form(name) else ""
                print("")
                print("  form: %s%s   length: %s slides"
                      % (form, mark,
                         flo if flo == fhi else "%d-%d" % (flo, fhi)))
                for line in _slot_table(dtypes.slot_plan(name, form)):
                    print(line)
        else:
            for line in _slot_table(dtypes.slot_plan(name)):
                print(line)
        gaps = dtypes.missing_patterns(name)
        if gaps:
            print("    NOT YET IN deck_kit: %s" % ", ".join(gaps))
        print("")
    print("req: yes = always present | cond = only when the rule fires "
          "(otherwise DROP the")
    print("     slide, never fill it with placeholder copy) | rep = once per "
          "source item")
    print("")
    print("choosing a type -- what the user says:")
    for name, cues in dtypes.TYPE_CUES.items():
        for i, line in enumerate(textwrap.wrap(cues, 58)):
            print("  %-30s %s" % (name if i == 0 else "", line))
    print("")
    print("content rules, source decks and the give/get on every slot live in")
    print("references/deck_types.md -- read the type's section before writing copy.")


def _annotations(obj):
    """The underscore-prefixed keys on a spec or slide object."""
    return [k for k in list(obj) if isinstance(k, str) and k.startswith("_")]


def strip_annotations(spec):
    """Pull the scaffold's underscore keys off a spec, in place.

    They are contract bookkeeping (`_slot`, `_optional`, `_when`, `_form`);
    validate() would reject them as unknown fields and deck_kit would choke on
    them, so nothing downstream ever sees one.  Returns what they said.
    """
    hints = {"form": None, "slots": []}
    if not isinstance(spec, dict):
        return hints
    hints["form"] = spec.pop("_form", None)
    for key in _annotations(spec):
        spec.pop(key)
    slides = spec.get("slides")
    if not isinstance(slides, list):
        return hints
    for slide in slides:
        if not isinstance(slide, dict):
            hints["slots"].append(None)
            continue
        hints["slots"].append(slide.pop("_slot", None))
        for key in _annotations(slide):
            slide.pop(key)
    return hints


def _match_forward(plan, cursor, pattern):
    """Index of the next slot at or after `cursor` this pattern can fill."""
    for k in range(max(cursor, 0), len(plan)):
        if pattern in dtypes.slot_patterns(plan[k]):
            return k
    return None


def _match_any(plan, pattern):
    for k, slot in enumerate(plan):
        if pattern in dtypes.slot_patterns(slot):
            return k
    return None


def check_type(spec, hints, type_name, form=None):
    """Assert a spec conforms to its type contract.

    Returns (problems, warnings, assignments) -- assignments is the slot each
    slide was matched to, parallel to spec["slides"], None where unmatched.

    Slides match slots by their `_slot` annotation when present, otherwise by
    pattern name in contract order.  A rep slot may repeat consecutively; a
    dropped cond slot is never a problem.  Every violation is reported with its
    slide index, not just the first.
    """
    try:
        plan = dtypes.slot_plan(type_name, form)
    except (dtypes.UnknownType, dtypes.UnknownForm) as exc:
        return [str(exc)], [], []

    problems, warns = [], []
    slides = spec.get("slides") if isinstance(spec.get("slides"), list) else []
    assignments = [None] * len(slides)
    index = {slot["slot"]: i for i, slot in enumerate(plan)}
    allowed = dtypes.sanctioned_patterns(type_name, form)
    unbuilt = set(dtypes.missing_patterns(type_name, form))
    lo, hi = dtypes.length_range(type_name, form)

    want_theme = dtypes.theme(type_name)
    got_theme = spec.get("theme", "brand")
    if got_theme != want_theme:
        warns.append('theme: %s is a "%s" type, spec says "%s" -- '
                     "deck_types.md names one theme per type"
                     % (type_name, want_theme, got_theme))

    counts = {}
    cursor = -1
    for i, slide in enumerate(slides, start=1):
        if not isinstance(slide, dict):
            continue
        pattern = slide.get("pattern")
        slot_hint = hints["slots"][i - 1] if i - 1 < len(hints["slots"]) else None
        if slot_hint:
            if slot_hint not in index:
                problems.append('slide %d: "_slot": %r is not a slot in %s -- '
                                "slots are: %s"
                                % (i, slot_hint, type_name,
                                   ", ".join(s["slot"] for s in plan)))
                continue
            j = index[slot_hint]
            if pattern not in dtypes.slot_patterns(plan[j]):
                problems.append('slide %d: slot "%s" takes pattern %s, got %r'
                                % (i, slot_hint,
                                   " | ".join(dtypes.slot_patterns(plan[j])),
                                   pattern))
        else:
            j = _match_forward(plan, cursor, pattern)
            if j is None:
                j = _match_any(plan, pattern)
            if j is None:
                problems.append("slide %d: pattern %r is not sanctioned for "
                                "type %s -- allowed: %s"
                                % (i, pattern, type_name, ", ".join(allowed)))
                continue
        slot = plan[j]
        assignments[i - 1] = slot["slot"]
        counts[slot["slot"]] = counts.get(slot["slot"], 0) + 1
        if j < cursor:
            problems.append('slide %d (%s): slot "%s" is out of contract order '
                            '-- it belongs before "%s"'
                            % (i, pattern, slot["slot"], plan[cursor]["slot"]))
        else:
            cursor = j
        if counts[slot["slot"]] > 1 and slot["req"] != "rep":
            problems.append('slide %d: slot "%s" appears %d times but is not '
                            "repeatable (req: %s)"
                            % (i, slot["slot"], counts[slot["slot"]],
                               slot["req"]))
        if pattern in unbuilt:
            warns.append("slide %d: pattern %r is in the %s contract but not "
                         "implemented in deck_kit yet -- it cannot be built"
                         % (i, pattern, type_name))

    for slot in plan:
        if slot["req"] == "yes" and not counts.get(slot["slot"]):
            problems.append('missing required slot "%s" (%s) -- %s'
                            % (slot["slot"],
                               "/".join(dtypes.slot_patterns(slot)),
                               slot.get("when") or "always"))

    n = len(slides)
    if n < lo or n > hi:
        warns.append("%d slides; %s documents %s -- rep slots legitimately "
                     "stretch this, an empty deck does not"
                     % (n, type_name, "%d-%d" % (lo, hi) if lo != hi
                        else "exactly %d" % lo))
    return problems, warns, assignments


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("spec", nargs="?", help="path to the JSON deck spec")
    ap.add_argument("--out", help="output .pptx path")
    ap.add_argument("--check", action="store_true", help="validate only")
    ap.add_argument("--list-patterns", action="store_true",
                    help="print every pattern with its fields")
    ap.add_argument("--list-types", action="store_true",
                    help="print every deck type with its ordered slot plan")
    ap.add_argument("--type", metavar="NAME",
                    help="deck type contract to check the spec against "
                         "(see --list-types)")
    ap.add_argument("--form", metavar="NAME",
                    help="which form of a multi-form type, e.g. "
                         "--type implementation_checkin --form tracker_only")
    ap.add_argument("--scaffold", action="store_true",
                    help="with --type: print that type's slides, in order, "
                         "with empty content, as JSON on stdout")
    args = ap.parse_args(argv)

    if args.list_patterns:
        list_patterns()
        return 0
    if args.list_types:
        list_types()
        return 0
    if args.form and not args.type:
        ap.error("--form needs --type NAME")
    if args.type:
        try:
            dtypes.get(args.type)
            dtypes.slot_plan(args.type, args.form)
        except (dtypes.UnknownType, dtypes.UnknownForm) as exc:
            print("%s" % exc, file=sys.stderr)
            return 2
    if args.scaffold:
        if not args.type:
            ap.error("--scaffold needs --type NAME (see --list-types)")
        print(json.dumps(dtypes.scaffold(args.type, args.form), indent=2))
        return 0
    if not args.spec:
        if args.type:
            print("--type %s needs a spec to check, or --scaffold to emit one"
                  % args.type, file=sys.stderr)
            return 2
        ap.error("spec is required (or use --list-patterns / --list-types / "
                 "--type NAME --scaffold)")

    path = Path(args.spec).expanduser()
    if not path.exists():
        print("no such spec: %s" % path, file=sys.stderr)
        return 2
    try:
        spec = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        print("spec is not valid JSON: %s" % exc, file=sys.stderr)
        return 2

    hints = strip_annotations(spec)
    form = args.form or hints["form"]
    if form and args.type:
        try:
            dtypes.slot_plan(args.type, form)
        except dtypes.UnknownForm as exc:
            print("%s" % exc, file=sys.stderr)
            return 2

    problems = validate(spec)
    type_problems, type_warns, slots = [], [], []
    if args.type:
        type_problems, type_warns, slots = check_type(spec, hints, args.type,
                                                      form)
    if problems or type_problems:
        print("spec has %d problem(s):" % (len(problems) + len(type_problems)),
              file=sys.stderr)
        for p in problems:
            print("  %s" % p, file=sys.stderr)
        for p in type_problems:
            print("  [%s] %s" % (args.type, p), file=sys.stderr)
        # A contract can name a pattern deck_kit has not grown yet; say so
        # here, or the only clue is a bare "unknown pattern" from validate().
        for w in type_warns:
            print("  note: %s" % w, file=sys.stderr)
        print("", file=sys.stderr)
        print("run `python3 build_deck.py --list-patterns` for the field reference",
              file=sys.stderr)
        if type_problems:
            print("run `python3 build_deck.py --list-types` for the slot plan",
                  file=sys.stderr)
        return 2
    plan = dtypes.slot_plan(args.type, form) if args.type else None
    warns = warnings_for(spec, plan, slots) + type_warns
    # --type on its own is a check; --type with --out gates the build instead.
    if args.check or (args.type and not args.out):
        print("spec OK: %d slides, theme=%s"
              % (len(spec["slides"]), spec.get("theme", "brand")))
        if args.type:
            print("conforms to type %s%s"
                  % (args.type, " (form: %s)" % form if form else ""))
        for i, s in enumerate(spec["slides"], start=1):
            slot = slots[i - 1] if i - 1 < len(slots) else None
            print("  %2d  %-20s %s" % (i, s["pattern"], slot) if slot
                  else "  %2d  %s" % (i, s["pattern"]))
        for w in warns:
            print("warning: %s" % w)
        return 0

    out = args.out or str(path.with_suffix(".pptx"))
    prs, out_path = build(spec, out)
    print("built  %s" % out_path)
    print("slides %d   canvas %d x %d EMU (%.3f x %.3f in)"
          % (len(prs.slides), prs.slide_width,
             prs.slide_height, prs.slide_width / 914400.0,
             prs.slide_height / 914400.0))
    for w in warns:
        print("warning: %s" % w)
    for note in kit.notes():
        print("note:  %s" % note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
