#!/usr/bin/env python3
"""Deck type contracts -- which slides a kind of deck is made of, and in what order.

`deck_kit.PATTERNS` says how to draw one slide; this module says which slides a
given kind of deck is made of and in what sequence. It is the machine-readable
half of `references/deck_types.md` -- that file is the prose contract (purpose,
audience, content rules), this one is what `build_deck.py --list-types` and
`--type` read. When the two disagree, the .md wins and this file is the bug.

Pure data plus a few helpers. stdlib only, and importable without python-pptx:
`deck_kit` is imported lazily, inside scaffold() / missing_patterns() only.

    TYPES[name]["slots"]   ordered slot dicts -- {slot, pattern, req, when, seed?}
    seed  optional  fields merged into the scaffold for that slot -- used where
          a pattern's requirement is conditional (two_column_list) or where the
          contract pins a value, e.g. column headers that must read the same
          every time the slot is built
    req   yes   always present
          cond  only when the "when" rule fires. A cond slot that does not fire
                is DROPPED, never filled with placeholder copy
          rep   repeats once per item in the source data

`pattern` is a pattern name, or a list of names where the contract genuinely
sanctions alternatives (no type does today; the helpers handle both).

Two types do not fit a flat slot list and are modelled explicitly:

    implementation_checkin    two forms, "full" and "tracker_only", under
                              TYPES[name]["forms"][form]; pick with "form_rule"
    enterprise_capabilities   specified by SECTION, not slide, under
                              TYPES[name]["sections"] -- each section carries its
                              own "slots". slot_plan() flattens them and tags
                              every slot with the section it came from.
"""

from __future__ import annotations

import copy

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# New decks are 13.333 x 7.5in.  Reference decks at 10 x 5.625in are a Google
# Slides export artifact -- scale their geometry by 0.75, never inherit the
# canvas (deck_types.md "Canvas").
CANVAS = [13.3333, 7.5]

REQ_KINDS = ("yes", "cond", "rep")


TYPES = {
    # ------------------------------------------------------------------
    "implementation_kickoff": {
        "purpose": "align a new (or restarted) implementation on scope, "
                   "timeline, dependencies and owners",
        "audience": "customer stakeholders plus the FurtherAI delivery team",
        "sources": "Novacore, Balance Partners, LICB, FSLSO, DealerGuard",
        "theme": "brand",
        "length": (6, 10),
        "last_pattern": "next_steps",
        "slots": [
            {"slot": "cover", "pattern": "cover", "req": "yes",
             "when": "always"},
            {"slot": "objectives", "pattern": "two_column_list", "req": "cond",
             "seed": {"mode": "columns", "columns": [{"header": "Our objectives", "items": [""]}, {"header": "What we need from today", "items": [""]}]},
             "when": "the customer sponsor set an explicit give/get frame for "
                     'the session. Columns: "Our objectives" | "What we need '
                     'from today"'},
            {"slot": "agenda", "pattern": "agenda", "req": "yes",
             "when": "always"},
            {"slot": "scope_recap", "pattern": "stat_hero", "req": "cond",
             "when": "you have real scale figures (volume, LOB count, entity "
                     "count). 2-3 stats, dark: false"},
            {"slot": "where_we_are", "pattern": "timeline", "req": "cond",
             "when": "this kickoff follows earlier work and the customer needs "
                     "orienting. Use `today` to mark the current point"},
            {"slot": "workflow", "pattern": "stepper_row", "req": "yes",
             "when": "always -- the target workflow, 4-6 steps"},
            {"slot": "plan", "pattern": "workstream_swimlane", "req": "yes",
             "when": "always -- workstreams x weeks, with milestone gates"},
            {"slot": "dependencies", "pattern": "status_table", "req": "yes",
             "when": "always -- what we need from the customer, with owner and "
                     "status"},
            {"slot": "demo", "pattern": "section_divider", "req": "cond",
             "when": "a live demo runs in this meeting"},
            {"slot": "next_steps", "pattern": "next_steps", "req": "yes",
             "when": "always, and always last"},
        ],
    },
    # ------------------------------------------------------------------
    "implementation_checkin": {
        "purpose": "recurring sync during an active implementation -- what "
                   "shipped, what's blocked, what's next",
        "audience": "the joint working team",
        "sources": "FWG, LIA, PMA, CNA",
        "theme": "brand",
        "length": (4, 12),
        "default_form": "full",
        "form_rule": 'use "full" when the user wants a meeting deck; use '
                     '"tracker_only" when the user handed you an issue list '
                     "and nothing else (the PMA case) -- then the deck IS the "
                     "tracker: no cover, no agenda, no next steps",
        "forms": {
            "full": {
                "length": (4, 12),
                "last_pattern": "next_steps",
                "slots": [
                    {"slot": "cover", "pattern": "cover", "req": "yes",
                     "when": "always"},
                    {"slot": "agenda", "pattern": "agenda", "req": "yes",
                     "when": "always"},
                    {"slot": "timeline_review", "pattern": "timeline",
                     "req": "yes",
                     "when": "always -- workflow rows against weeks, with "
                             "`today` set"},
                    {"slot": "issues", "pattern": "status_table", "req": "yes",
                     "when": "always -- the running tracker"},
                    {"slot": "updates", "pattern": "card_grid", "req": "rep",
                     "when": "once per product-update theme, when there are "
                             "updates to report"},
                    {"slot": "usage", "pattern": "stat_hero", "req": "cond",
                     "when": "submission volume or accuracy figures are "
                             "available for the window"},
                    {"slot": "feedback", "pattern": "two_column_list",
                     "seed": {"mode": "columns", "columns": [{"header": "Prior follow-ups", "items": [""]}, {"header": "Live feedback", "items": [""]}]},
                     "req": "cond",
                     "when": "the session collects live feedback. Columns: "
                             '"Prior follow-ups" | "Live feedback"'},
                    {"slot": "next_steps", "pattern": "next_steps",
                     "req": "yes", "when": "always, and always last"},
                ],
            },
            "tracker_only": {
                # The type is documented 4-12; that range describes the full
                # form.  A one-page tracker is a legitimate deck, so the floor
                # drops to 1 here.
                "length": (1, 12),
                "last_pattern": None,
                "slots": [
                    {"slot": "issues", "pattern": "status_table", "req": "rep",
                     "when": "paginate at ~8 rows per slide; title carries the "
                             "count, e.g. `Running Issue Tracker (1/2)`"},
                ],
            },
        },
    },
    # ------------------------------------------------------------------
    "build_scoping": {
        "purpose": "agree exactly what a workflow build covers, step by step, "
                   "including what stays human",
        "audience": "joint working session -- customer underwriting/IT "
                    "leadership plus FurtherAI delivery",
        "sources": "Aviva_Scoping (the fullest, most on-brand reference deck "
                   "-- follow it closely)",
        "theme": "consulting",
        "length": (10, 12),
        "last_pattern": "closing",
        "slots": [
            {"slot": "cover", "pattern": "cover", "req": "yes",
             "when": "always"},
            {"slot": "agenda", "pattern": "agenda", "req": "yes",
             "when": "always"},
            {"slot": "objectives", "pattern": "card_grid", "req": "yes",
             "when": "always -- 4 numbered build objectives, cols: 2"},
            {"slot": "timeline", "pattern": "workstream_swimlane",
             "req": "yes",
             "when": "always -- calendar columns, phase-coloured bars"},
            {"slot": "risks", "pattern": "two_column_list", "req": "yes",
             "seed": {"mode": "ledger", "headers": ["Risk", "Mitigation"], "rows": [{"left": "", "right": ""}]},
             "when": "always -- risk | mitigation ledger"},
            {"slot": "process_overview", "pattern": "card_grid", "req": "yes",
             "when": "always -- one card per agent role in the process"},
            {"slot": "step_detail", "pattern": "step_detail_io", "req": "rep",
             "when": "once per process step -- this is the substance of the "
                     "deck"},
            {"slot": "asks", "pattern": "two_column_list", "req": "yes",
             "seed": {"mode": "columns", "columns": [{"header": "Data we need", "items": [""]}, {"header": "Next steps", "items": [""]}]},
             "when": "always -- data we need | next steps"},
            {"slot": "closing", "pattern": "closing", "req": "yes",
             "when": "always"},
        ],
    },
    # ------------------------------------------------------------------
    "partnership_discussion": {
        "purpose": "pitch a channel, reseller, or SI partnership",
        "audience": "prospective partner executives, usually a first meeting",
        "sources": "ResourcePro, Deloitte",
        "theme": "brand",
        "length": (6, 8),
        # next_steps is slide 7 but a cond appendix divider may follow it, so
        # there is no fixed last pattern for this type.
        "last_pattern": None,
        "slots": [
            {"slot": "cover", "pattern": "cover", "req": "yes",
             "when": "always"},
            {"slot": "agenda", "pattern": "agenda", "req": "yes",
             "when": "always"},
            {"slot": "mutual_value", "pattern": "two_column_list",
             "seed": {"mode": "columns", "columns": [{"header": "<Partner>", "items": [""]}, {"header": "FurtherAI", "items": [""]}]},
             "req": "yes",
             "when": "always -- what each side brings and gets. Columns are "
                     "the two company names"},
            {"slot": "use_cases", "pattern": "data_table", "req": "cond",
             "when": "specific joint use cases are on the table"},
            {"slot": "model", "pattern": "phase_chevrons", "req": "yes",
             "when": "always -- the phased partnership model"},
            {"slot": "commercials", "pattern": "pricing_table", "req": "cond",
             "when": "the partner asked about economics, or a revenue split is "
                     "being proposed"},
            {"slot": "next_steps", "pattern": "next_steps", "req": "yes",
             "when": "always"},
            {"slot": "appendix", "pattern": "section_divider", "req": "cond",
             "when": "backup material follows"},
        ],
    },
    # ------------------------------------------------------------------
    "workflow_solution": {
        "purpose": "pitch one specific workflow to one prospect",
        "audience": "a single-workflow buyer",
        "sources": "FurtherAI_DOXA_Submission_Gateway (slide_patterns.md C.2) "
                   "-- structure only, port it onto the brand palette",
        "theme": "brand",
        "length": (11, 11),
        "last_pattern": "next_steps",
        "note": "the most parameterized type -- the slide plan does not vary "
                "at all, only the content does",
        "slots": [
            {"slot": "cover", "pattern": "cover", "req": "yes",
             "when": "always"},
            {"slot": "context", "pattern": "stat_hero", "req": "yes",
             "when": "always"},
            {"slot": "problem", "pattern": "card_grid", "req": "yes",
             "when": "always"},
            {"slot": "approach", "pattern": "card_grid", "req": "yes",
             "when": "always -- 3 cards"},
            {"slot": "pipeline", "pattern": "stepper_row", "req": "yes",
             "when": "always"},
            {"slot": "benefits", "pattern": "card_grid", "req": "yes",
             "when": "always"},
            {"slot": "outcome", "pattern": "stat_hero", "req": "yes",
             "when": "always"},
            {"slot": "proof", "pattern": "use_case_rail", "req": "yes",
             "when": "always"},
            {"slot": "trust", "pattern": "security_trust", "req": "yes",
             "when": "always"},
            {"slot": "differentiation", "pattern": "why_us", "req": "yes",
             "when": "always"},
            {"slot": "next_steps", "pattern": "next_steps", "req": "yes",
             "when": "always"},
        ],
    },
    # ------------------------------------------------------------------
    "enterprise_capabilities": {
        "purpose": "full platform pitch to a new enterprise logo",
        "audience": "broad -- exec sponsor through to security review",
        "sources": "Enterprise_Deck (slide_patterns.md C.1)",
        "theme": "brand",
        "length": (20, 37),
        "last_pattern": "closing",
        "note": "built in sections, not slides. The section spine is fixed; "
                "depth within a section flexes. Sections 4-6 and 9 are the "
                "flexible ones; 1-3, 7, 8 and 10 always ship. Cut sections "
                "rather than thinning them",
        "sections": [
            {"section": "Open", "req": "yes", "when": "always",
             "slots": [
                 {"slot": "cover", "pattern": "cover", "req": "yes",
                  "when": "always"},
                 {"slot": "team", "pattern": "two_column_list", "req": "yes",
                  "seed": {"mode": "columns", "columns": [{"header": "FurtherAI", "items": [""]}, {"header": "<Customer>", "items": [""]}]},
                  "when": "always -- the roster, FurtherAI in one column and "
                          "the customer in the other, each entry 'Name -- role' "
                          "(CNA p13). Enterprise_Deck 2-10 runs a headshot "
                          "variant; that needs a `team_roster` pattern that "
                          "does not exist yet, so the text roster is the "
                          "buildable form"},
             ]},
            {"section": "Problem", "req": "yes", "when": "always",
             "slots": [
                 {"slot": "problem", "pattern": "before_after", "req": "yes",
                  "when": "always"},
             ]},
            {"section": "Platform", "req": "yes", "when": "always",
             "slots": [
                 {"slot": "architecture", "pattern": "architecture_hero",
                  "req": "yes", "when": "always"},
                 {"slot": "use_cases", "pattern": "card_grid", "req": "yes",
                  "when": "always -- the use-case grid"},
             ]},
            {"section": "Demo", "req": "cond", "when": "a demo runs",
             "slots": [
                 {"slot": "demo_divider", "pattern": "section_divider",
                  "req": "cond", "when": "a demo runs"},
                 {"slot": "demo_rail", "pattern": "use_case_rail",
                  "req": "rep",
                  "when": "once per use case shown in the demo"},
             ]},
            {"section": "Agents", "req": "cond",
             "when": "the agent story is relevant",
             "slots": [
                 {"slot": "agents", "pattern": "agent_diagram", "req": "cond",
                  "when": "the agent story is relevant"},
                 {"slot": "agent_detail", "pattern": "step_detail_io",
                  "req": "rep", "when": "once per agent"},
             ]},
            {"section": "Fit", "req": "cond",
             "when": "a build-vs-buy or vendor comparison is live",
             "slots": [
                 {"slot": "stack_fit", "pattern": "rag_matrix", "req": "cond",
                  "when": "a build-vs-buy or vendor comparison is live -- "
                          "stack fit"},
                 {"slot": "capabilities", "pattern": "data_table",
                  "req": "cond",
                  "when": "a build-vs-buy or vendor comparison is live -- "
                          "capability table"},
             ]},
            {"section": "Trust", "req": "yes", "when": "always",
             "slots": [
                 {"slot": "trust", "pattern": "security_trust", "req": "yes",
                  "when": "always"},
             ]},
            {"section": "Why us", "req": "yes", "when": "always",
             "slots": [
                 {"slot": "why_us", "pattern": "why_us", "req": "yes",
                  "when": "always"},
             ]},
            {"section": "Delivery", "req": "cond",
             "when": "the deal is far enough along",
             "slots": [
                 {"slot": "delivery", "pattern": "workstream_swimlane",
                  "req": "cond", "when": "the deal is far enough along"},
                 {"slot": "commercials", "pattern": "pricing_table",
                  "req": "cond", "when": "the deal is far enough along"},
             ]},
            {"section": "Close", "req": "yes", "when": "always",
             "slots": [
                 {"slot": "next_steps", "pattern": "next_steps", "req": "yes",
                  "when": "always"},
                 {"slot": "closing", "pattern": "closing", "req": "yes",
                  "when": "always, and always last"},
             ]},
        ],
    },
    # ------------------------------------------------------------------
    "internal_process_enablement": {
        "purpose": "teach the internal team a process -- how to scope a POC, "
                   "what each workflow does, who owns which phase",
        "audience": "internal only. Mark it so, on the slide and not just in "
                    "the filename",
        "sources": "POC scoping and execution AE-EM swim lanes, FurtherAI "
                   "Workflow Overviews (both off-brand -- take the structure, "
                   "apply brand tokens, never sample a colour)",
        "theme": "consulting",
        "length": (7, 9),
        # A cond appendix / rubric may close the deck, so no fixed last slide.
        "last_pattern": None,
        "slots": [
            {"slot": "cover", "pattern": "cover", "req": "yes",
             "when": "always"},
            {"slot": "framing", "pattern": "two_column_list", "req": "yes",
             "seed": {"mode": "columns", "columns": [{"header": "What this is", "items": [""]}, {"header": "What this is not", "items": [""]}]},
             "when": 'always -- "what this is" | "what this is not"'},
            {"slot": "ownership", "pattern": "workstream_swimlane",
             "req": "yes",
             "when": "always -- role rows x phase columns, not calendar "
                     "columns"},
            {"slot": "decision_path", "pattern": "two_column_list",
             "seed": {"mode": "columns", "columns": [{"header": "Out-of-box path", "items": [""]}, {"header": "Complex POC path", "items": [""]}]},
             "req": "cond",
             "when": "the process branches (e.g. out-of-box vs custom)"},
            {"slot": "reference", "pattern": "data_table", "req": "rep",
             "when": "once per table page when cataloguing workflows or tools; "
                     "paginate (1/3)"},
            {"slot": "tooling", "pattern": "numbered_steps", "req": "cond",
             "when": "there's a tool the reader has to actually operate"},
            {"slot": "appendix", "pattern": "section_divider", "req": "cond",
             "when": "a rubric or backup follows"},
            {"slot": "rubric", "pattern": "scorecard", "req": "cond",
             "when": "the process includes scoring or qualification"},
        ],
    },
    # ------------------------------------------------------------------
    "account_update": {
        "purpose": "what changed for this account recently",
        "audience": "the account team, or the customer",
        "sources": "gather_updates.py, plus the update slides in LIA and FWG",
        "theme": "brand",
        "length": (5, 8),
        "last_pattern": "next_steps",
        "slots": [
            {"slot": "cover", "pattern": "cover", "req": "yes",
             "when": "always"},
            {"slot": "headline", "pattern": "stat_hero", "req": "yes",
             "when": "always -- submissions and accuracy over the window"},
            {"slot": "shipped", "pattern": "card_grid", "req": "rep",
             "when": "once per workflow with activity in the window"},
            {"slot": "quiet", "pattern": "data_table", "req": "cond",
             "when": "one or more workflows shipped nothing -- list them "
                     "plainly"},
            {"slot": "blockers", "pattern": "status_table", "req": "cond",
             "when": "there are open blockers"},
            {"slot": "next_steps", "pattern": "next_steps", "req": "yes",
             "when": "always"},
        ],
    },
}


# Which type to reach for, from what the user said (deck_types.md
# "Choosing between types").  Keyed by type so it survives a rename.
TYPE_CUES = {
    "implementation_kickoff": '"kickoff", "we\'re starting with X", '
                              '"implementation kickoff"',
    "implementation_checkin": '"check-in", "weekly sync", "status deck", or '
                              "hands over an issue list",
    "build_scoping": '"scoping", "what\'s in the build", "walk through the '
                     'workflow steps"',
    "partnership_discussion": '"partnership", "reseller", "channel", "co-sell"',
    "workflow_solution": '"a deck for <workflow> for <prospect>"',
    "enterprise_capabilities": '"the enterprise deck", "full platform pitch", '
                               '"new logo"',
    "internal_process_enablement": '"how do we scope POCs", "internal '
                                   'enablement", "workflow reference"',
    "account_update": '"what changed", "account review", "recent updates"',
}


class UnknownType(ValueError):
    """Raised for a type name that is not in TYPES."""


class UnknownForm(ValueError):
    """Raised for a form name a type does not define."""


# ------------------------------------------------------------------ lookup ---
def type_names():
    """Contract names, in the order deck_types.md documents them."""
    return list(TYPES)


def get(name):
    """The raw contract dict.  Raises UnknownType with the valid names."""
    try:
        return TYPES[name]
    except KeyError:
        raise UnknownType("unknown deck type %r -- valid types are: %s"
                          % (name, ", ".join(type_names()))) from None


def forms_for(name):
    """Form names for a multi-form type; [] when the type has a single form."""
    return list(get(name).get("forms") or [])


def default_form(name):
    """The form used when the caller does not name one; None for single-form."""
    spec = get(name)
    if not spec.get("forms"):
        return None
    return spec.get("default_form") or next(iter(spec["forms"]))


def form_rule(name):
    """How to pick between a multi-form type's forms.  None for single-form."""
    return get(name).get("form_rule")


def _resolve(name, form=None):
    """Return (type_spec, form_spec_or_type_spec, resolved_form_name)."""
    spec = get(name)
    forms = spec.get("forms")
    if not forms:
        if form:
            raise UnknownForm("deck type %r has a single form -- drop --form %r"
                              % (name, form))
        return spec, spec, None
    resolved = form or default_form(name)
    if resolved not in forms:
        raise UnknownForm("deck type %r has no form %r -- valid forms are: %s"
                          % (name, form, ", ".join(forms)))
    return spec, forms[resolved], resolved


def slot_patterns(slot):
    """The pattern names a slot sanctions, always as a list."""
    pattern = slot.get("pattern")
    return list(pattern) if isinstance(pattern, (list, tuple)) else [pattern]


def slot_plan(type_name, form=None):
    """Flat ordered list of slot dicts (copies -- safe for the caller to edit).

    Sectioned types are flattened; every slot carries the "section" it came
    from and inherits the section's req when the section itself is conditional.
    """
    spec, scoped, _f = _resolve(type_name, form)
    out = []
    if "sections" in spec:
        for section in spec["sections"]:
            for slot in section["slots"]:
                item = dict(slot)
                item["section"] = section["section"]
                item["section_req"] = section["req"]
                if section["req"] != "yes" and item["req"] == "yes":
                    item["req"] = "cond"
                out.append(item)
        return out
    for slot in scoped["slots"]:
        out.append(dict(slot))
    return out


def required_slots(type_name, form=None):
    """Ordered slot names that must be present -- req == "yes"."""
    return [s["slot"] for s in slot_plan(type_name, form) if s["req"] == "yes"]


def sanctioned_patterns(type_name, form=None):
    """Every pattern the contract allows for this type, sorted."""
    out = set()
    for slot in slot_plan(type_name, form):
        out.update(p for p in slot_patterns(slot) if p)
    return sorted(out)


def length_range(type_name, form=None):
    """(min_slides, max_slides).  A form may narrow the type's range."""
    spec, scoped, _f = _resolve(type_name, form)
    return tuple(scoped.get("length") or spec["length"])


def last_pattern(type_name, form=None):
    """The pattern the deck must end on, or None when the contract allows
    several endings (a cond appendix after next steps, say)."""
    spec, scoped, _f = _resolve(type_name, form)
    if "last_pattern" in scoped:
        return scoped["last_pattern"]
    return spec.get("last_pattern")


def theme(type_name):
    return get(type_name)["theme"]


# ---------------------------------------------------------------- scaffold ---
def _kit_required():
    """{pattern: [required field names]} from deck_kit, {} when unimportable.

    Imported lazily so this module stays usable without python-pptx.
    """
    try:
        import deck_kit
        return {n: list(v[1]) for n, v in deck_kit.PATTERNS.items()}
    except Exception:
        return {}


def _empty(pattern, field):
    """An empty value of the right JSON shape for a required field."""
    kind = None
    try:
        import build_deck
        doc = build_deck.field_doc(pattern, field)
        kind = doc[0] if doc else None
    except Exception:
        pass
    if kind is None:
        # No field table available -- fall back on the naming convention the
        # kit follows: plural field names take lists.
        kind = "list" if field.endswith("s") else "str"
    return {"list": [], "dict": {}, "int": 0, "bool": False}.get(kind, "")


def missing_patterns(type_name, form=None):
    """Patterns this contract names that deck_kit does not implement (yet)."""
    known = _kit_required()
    if not known:
        return []
    return [p for p in sanctioned_patterns(type_name, form) if p not in known]


def scaffold(type_name, form=None):
    """A deck spec with the type's slides, in order, with empty content.

    cond / rep slots are included carrying "_optional": true and "_when" so the
    caller can see the rule and delete the slide when it does not fire.
    "_slot" ties each slide back to the contract for --check --type; build_deck
    strips every underscore key before validating or building.
    """
    spec, _scoped, resolved = _resolve(type_name, form)
    known = _kit_required()
    slides = []
    for slot in slot_plan(type_name, form):
        pattern = slot_patterns(slot)[0]
        slide = {"pattern": pattern, "_slot": slot["slot"]}
        for field in known.get(pattern, []):
            slide[field] = _empty(pattern, field)
        if pattern not in known and known:
            slide["_pattern_missing"] = True
        # A slot may seed fields the required-field pass cannot supply: either
        # the pattern's requirement is conditional (two_column_list needs
        # "columns" or "rows" depending on mode, so neither is declared
        # required), or the contract pins a value -- column headers that should
        # read the same every time this slot is built.
        for field, value in (slot.get("seed") or {}).items():
            slide[field] = copy.deepcopy(value)
        if slot["req"] != "yes":
            slide["_optional"] = True
            slide["_when"] = slot["when"]
            if slot["req"] == "rep":
                slide["_repeat"] = True
        if slot.get("section"):
            slide["_section"] = slot["section"]
        slides.append(slide)
    out = {"canvas": list(CANVAS), "theme": spec["theme"], "slides": slides}
    if resolved:
        out["_form"] = resolved
    return out
