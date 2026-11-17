#!/usr/bin/env python3
"""Shared vocabulary for the verify sub-skill: schema language, path resolver,
type compatibility, findings, bounded output, thresholds.

The public API is pinned in CONTRACT.md ("common.py public API — exact
signatures"). Every other pass imports from here; nothing here imports from a
pass.

Every platform rule carries a comment naming the workflow-skill reference doc
it came from. This sub-skill syncs with the workflow skill, not the backend
repo; where a rule cannot be sourced the code emits `unknown` plus a note
rather than inventing platform behavior (CONTRACT.md "Reference docs").

stdlib only. Python 3.9 compatible.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA_VERSION = 1

# "structural" is pass 1 — the pre-existing ../validate_workflow.py. It takes a
# workflow rather than facts and prints text rather than writing a payload, so
# verify.py parses its output and wraps it in a normal PassOutput. It belongs
# here: the roll-up reads structural.json, and without this name pass 1's
# findings would be silently absent from the verdict.
PASS_NAMES = ("facts", "structural", "flow", "paths", "modules", "html",
              "simplify")

# --------------------------------------------------------------------------- #
# Thresholds — CONTRACT.md "Thresholds". Keys are exactly the table's names.
# --------------------------------------------------------------------------- #

THRESHOLDS: Dict[str, Any] = {
    "MAX_FIELDS_PER_STEP": 40,
    "MAX_PROMPT_CHARS": 8000,
    "DUP_STEP_OVERLAP": 0.75,
    "ADOPT_MIN_OVERLAP": 0.50,
    "ADOPT_STRONG_OVERLAP": 0.75,
    # Both sides need at least this many fields before an overlap score counts
    # as evidence. Jaccard over single-field sets manufactures perfect 1.00
    # matches, and every dashboard producer in the catalog emits exactly one
    # field (`email_body`), so without a floor they all "match" each other.
    "ADOPT_MIN_FIELDS": 3,
    # Job 3 (extract candidate) gates. Both change which candidates a human is
    # shown, so by CONTRACT amendment B4's test they are policy, not
    # implementation limits.
    "MIN_EXTRACT_FIELDS": 4,
    "MAX_EXTRACT_INPUTS": 3,
    # CONTRACT.md B9. METRIC, stated here because it is the whole point of the
    # key: measured against `fields[].depth` and nothing else — specifically
    # the per-step MAXIMUM of that value. NOT resolver traversal depth, NOT
    # `schema_depth`, NOT any count that descends cell members. Those are all
    # different numbers on the same corpus (per-step max `fields[].depth` 6,
    # `schema_leaf_paths` segment count 6, `counters.max_schema_depth` 7,
    # `common.schema_depth` 8), so a pass that measures "depth" without saying
    # which one will disagree with this threshold and never notice — the same
    # failure mode B2 exists to prevent.
    #
    # Value measured, not chosen: pooled per-step max over the 141 corpus
    # steps is depth 1:37, 2:45, 3:23, 4:34, 5:1, 6:1. A threshold of 4 flags
    # 26% of steps, which reads as noise because depth 4 is ordinary for
    # insurance extraction. 5 flags 2 steps.
    "MAX_SCHEMA_DEPTH": 5,
    # Floors on duplicate-step and wholesale-duplication claims, for the same
    # reason as ADOPT_MIN_FIELDS: an overlap score over one-field sets
    # manufactures a perfect match out of nothing.
    "MIN_DUP_STEP_FIELDS": 2,
    "MIN_WHOLESALE_FIELDS": 2,
    "MAX_CASE_COMPARISONS": 6,
    "MAX_RESHAPE_CHAIN": 2,
    "MAX_PATHS_SHOWN": 20,
    "MAX_FINDINGS_PRINTED": 40,
    "SHOT_WIDTH": 900,
    "SHOT_MAX_HEIGHT": 2000,
}

# Implementation limits, not policy. CONTRACT.md B4 gives the one test for
# which side of the line a value falls on: does changing it change which
# findings a human sees? If yes it is policy and belongs in THRESHOLDS above.
# If no it is an implementation limit and stays here. Recursion depth caps,
# buffer sizes and print widths are the "no" cases — moving one of these
# changes how deep a walk goes or how wide a column prints, never the verdict.
MAX_LEAF_DEPTH = 12
MAX_AVAILABLE_KEYS = 25
MAX_TABLE_ROWS = 10
PROMPT_MIN_CHARS = 200

# --------------------------------------------------------------------------- #
# The schema language (CONTRACT.md "The schema language")
# --------------------------------------------------------------------------- #

#: The canonical kinds from the CONTRACT.
KINDS = (
    "object", "array", "string", "number", "boolean",
    "file", "cell", "table", "knowledge_base", "unknown", "any",
)

#: CONTRACT EXTENSION. reference/input_mappings.md ("Save-time type
#: compatibility") names five more platform named types that participate in
#: type checks. They cannot collapse into `object` without losing the
#: directional rules, so they are additional kinds.
NAMED_TYPE_KINDS = ("video_file", "email_headers", "citation", "docx_image", "sov_field")

ALL_KINDS = KINDS + NAMED_TYPE_KINDS

#: Kinds that carry `properties` and therefore accept name segments.
_MAPPING_KINDS = frozenset((
    "object", "table", "file", "cell", "knowledge_base",
    "video_file", "email_headers", "citation", "docx_image", "sov_field",
))

_SCALAR_KINDS = frozenset(("string", "number", "boolean"))

#: The five cell members. reference/input_mappings.md: "Every leaf in an
#: extraction step's data.<field> is wrapped at runtime as a cell —
#: {value, confidence_score, confidence_reason, citations, thinking_steps}".
CELL_MEMBERS = ("value", "confidence_score", "confidence_reason",
                "citations", "thinking_steps")


def sch(kind: str, **kw: Any) -> Dict[str, Any]:
    """Build a normalized schema node. Extra keys (title, description,
    allowed_values, origin, open, items, properties, value_type,
    dynamic_keys, note) are carried through when not None."""
    node: Dict[str, Any] = {"kind": kind}
    for key, value in kw.items():
        if value is not None:
            node[key] = value
    return node


def unknown(note: Optional[str] = None) -> Dict[str, Any]:
    return sch("unknown", note=note)


def cell_properties(value_type: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    """The five cell members as resolvable properties.

    reference/input_mappings.md documents `data.<f>.value`,
    `data.<f>.confidence_score` and `data.<f>.citations.0` as valid paths.
    """
    return {
        "value": value_type or unknown("cell value type not declared"),
        "confidence_score": sch("number"),
        "confidence_reason": sch("string"),
        "citations": sch("array", items=sch("citation", properties={
            "content": sch("string"), "page": sch("number"),
        }, open=True)),
        "thinking_steps": sch("array", items=unknown("thinking step shape not documented")),
    }


def make_cell(value_type: Optional[Dict[str, Any]] = None, **kw: Any) -> Dict[str, Any]:
    return sch("cell", value_type=value_type or unknown("cell value type not declared"),
               properties=cell_properties(value_type), **kw)


def make_file(**kw: Any) -> Dict[str, Any]:
    """A WorkflowDocument reference.

    step_types/prepare_documents.md: "Each WorkflowDocument exposes at least
    user_document_id, filename, file_hash, and (after classify) category."
    `open=True` because "at least" is explicit.
    """
    return sch("file", properties={
        "user_document_id": sch("string"),
        "filename": sch("string"),
        "file_hash": sch("string"),
        "category": sch("string"),
    }, open=True, **kw)


# --------------------------------------------------------------------------- #
# Display
# --------------------------------------------------------------------------- #

def type_name(schema: Optional[Dict[str, Any]]) -> str:
    """Short display string: "array<file>", "cell<string>", "object"."""
    if not isinstance(schema, dict):
        return "unknown"
    kind = schema.get("kind", "unknown")
    if kind == "array":
        items = schema.get("items")
        return "array<%s>" % (type_name(items) if isinstance(items, dict) else "any")
    if kind == "cell":
        return "cell<%s>" % type_name(schema.get("value_type"))
    if kind == "string" and schema.get("allowed_values"):
        return "enum"
    return kind


# --------------------------------------------------------------------------- #
# Type compatibility
# reference/input_mappings.md "Save-time type compatibility (directional)"
# --------------------------------------------------------------------------- #

#: Expected kind -> the actual kinds it accepts. Transcribed from the table in
#: reference/input_mappings.md. That table's `enum` and `integer` rows collapse
#: into `string` / `number` here (an enum is a string carrying
#: allowed_values), so those rows are already covered.
_ACCEPTS: Dict[str, frozenset] = {
    "string": frozenset(("string",)),
    "number": frozenset(("number",)),
    "boolean": frozenset(("boolean",)),
    "array": frozenset(("array",)),
    # "object accepts object, and any named type (file, video_file, cell,
    # email_headers, citation, docx_image, sov_field)". `table` and
    # `knowledge_base` are dict-shaped step outputs, so they qualify too.
    "object": frozenset(("object", "table", "knowledge_base", "file", "cell")
                        + NAMED_TYPE_KINDS),
    # "file accepts file, video_file"
    "file": frozenset(("file", "video_file")),
    # "video_file, cell, email_headers, docx_image, citation, sov_field: only
    # themselves". cell accepting only cell is the missing-.value detector.
    #
    # DELIBERATE DIVERGENCE FROM THE PLATFORM — do not "fix" this to match.
    # reference/input_mappings.md "A bare data.<field> is not rejected at save
    # time — it fails at runtime": the platform keeps two views of an
    # extraction schema. Its save-time validator uses the view where primitive
    # leaves stay plain, so a bare `data.applicant_name` type-checks as
    # `string`, saves cleanly, and then hands the consumer the whole
    # {value, confidence_score, ...} dict at runtime. We model the runtime
    # shape instead (`cell<string>`), which is stricter than the platform's own
    # save validation on purpose: it catches a real defect that validation lets
    # through. A consumer that genuinely wants the cell declares the parameter
    # `object`, and the `object` row above accepts `cell`, so that stays legal.
    "cell": frozenset(("cell",)),
    "video_file": frozenset(("video_file",)),
    "email_headers": frozenset(("email_headers",)),
    "citation": frozenset(("citation",)),
    "docx_image": frozenset(("docx_image",)),
    "sov_field": frozenset(("sov_field",)),
    "table": frozenset(("table", "object")),
    "knowledge_base": frozenset(("knowledge_base", "object")),
}


def types_compatible(expected: Optional[Dict[str, Any]],
                     actual: Optional[Dict[str, Any]]) -> bool:
    """Directional: can `actual` feed a parameter declared `expected`?

    `unknown` or `any` on either side is always compatible — this sub-skill
    never blocks on its own ignorance (CONTRACT.md "Kinds").
    """
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return True
    exp_kind = expected.get("kind", "unknown")
    act_kind = actual.get("kind", "unknown")
    if exp_kind in ("unknown", "any") or act_kind in ("unknown", "any"):
        return True
    accepted = _ACCEPTS.get(exp_kind)
    if accepted is None:
        return True  # a kind we have no rule for: do not block
    if act_kind not in accepted:
        return False
    if exp_kind == "array" and act_kind == "array":
        exp_items, act_items = expected.get("items"), actual.get("items")
        if isinstance(exp_items, dict) and isinstance(act_items, dict):
            return types_compatible(exp_items, act_items)
    return True


# --------------------------------------------------------------------------- #
# The path resolver
# --------------------------------------------------------------------------- #

def _available_keys(schema: Dict[str, Any]) -> List[str]:
    return sorted((schema.get("properties") or {}).keys())[:MAX_AVAILABLE_KEYS]


def _element_keys(array_schema: Dict[str, Any]) -> List[str]:
    """The row object's property names, for an actionable array error."""
    items = array_schema.get("items")
    element = items if isinstance(items, dict) else {}
    return sorted((element.get("properties") or {}).keys())[:MAX_AVAILABLE_KEYS]


def _array_segment_error(array_schema: Dict[str, Any], segment: str,
                         walked: Sequence[str]) -> str:
    """Message for a name segment read straight off an array node.

    reference/input_mappings.md "Only primitive leaves are wrapped — nested
    arrays and objects stay navigable": an array node has no `value` key, so
    `<array>.value` resolves to nothing at runtime and every downstream step
    then runs green on empty data. Name the row-index fix explicitly.
    """
    prefix = ".".join(walked[:-1])
    columns = _element_keys(array_schema)
    column = columns[0] if columns else "<column>"
    if segment == "value":
        return ("an %s node has no 'value' key — index a row first, then take the "
                "cell's value (e.g. %s.0.%s.value)"
                % (type_name(array_schema), prefix, column))
    return ("cannot read '%s' out of %s — index a row first (e.g. %s.0.%s)"
            % (segment, type_name(array_schema), prefix, segment))


def resolve_path(schema: Optional[Dict[str, Any]],
                 path: Optional[str]) -> Dict[str, Any]:
    """Resolve a glom-style dotted path against a normalized schema.

    Returns either
      {"ok": True,  "type": <normalized schema>, "note": <str|None>}
      {"ok": False, "error": <str>, "at": <str>, "available": [<str>, ...]}

    Glom semantics per reference/input_mappings.md: dot-delimited keys,
    numeric segments index arrays, `null` returns the whole output dict.
    """
    if not isinstance(schema, dict):
        return {"ok": True, "type": unknown("source output schema not derived"),
                "note": "source output schema not derived"}
    if path is None or path == "":
        return {"ok": True, "type": schema, "note": None}

    current = schema
    walked: List[str] = []

    for segment in str(path).split("."):
        walked.append(segment)
        at = ".".join(walked)
        kind = current.get("kind", "unknown")

        # Ignorance short-circuits: stop verifying, say so, never fail.
        if kind == "unknown":
            return {"ok": True, "type": unknown(),
                    "note": "path not verified past %s (unknown schema)" % segment}
        if kind == "any":
            return {"ok": True, "type": sch("any"),
                    "note": "path not verified past %s (untyped)" % segment}

        if segment.isdigit():
            if kind == "array":
                items = current.get("items")
                current = items if isinstance(items, dict) else unknown(
                    "array item type not declared")
                continue
            if current.get("open"):
                # An open node makes no promise about its members, so an index
                # into it cannot be proven wrong. Degrade, never block.
                return {"ok": True, "type": unknown(),
                        "note": "index not verified; %s is open" % type_name(current)}
            return {"ok": False,
                    "error": "cannot index %s with [%s] (not an array)" % (
                        type_name(current), segment),
                    "at": at, "available": []}

        if kind in _SCALAR_KINDS:
            return {"ok": False,
                    "error": "cannot read '%s' out of %s" % (segment, type_name(current)),
                    "at": at, "available": []}

        if kind == "array":
            return {"ok": False,
                    "error": _array_segment_error(current, segment, walked),
                    "at": at,
                    "available": _element_keys(current)}

        if kind in _MAPPING_KINDS:
            props = current.get("properties") or {}
            if segment in props:
                nxt = props[segment]
                current = nxt if isinstance(nxt, dict) else unknown()
                continue
            dynamic = current.get("dynamic_keys")
            if dynamic is not None:
                # The classify class-name typo. Must stay catchable: a wrong
                # class silently returns [] at runtime and every downstream
                # step runs green on nothing.
                return {"ok": False,
                        "error": "'%s' is not a configured key" % segment,
                        "at": at,
                        "available": sorted(dynamic)[:MAX_AVAILABLE_KEYS]}
            if current.get("open"):
                return {"ok": True, "type": unknown(),
                        "note": "key not declared; object is open"}
            return {"ok": False,
                    "error": "'%s' is not a property of %s" % (segment, type_name(current)),
                    "at": at, "available": _available_keys(current)}

        return {"ok": True, "type": unknown(),
                "note": "path not verified past %s (kind %s has no rule)" % (segment, kind)}

    return {"ok": True, "type": current, "note": None}


def schema_leaf_paths(schema: Optional[Dict[str, Any]], prefix: str = "",
                      _depth: int = 0) -> List[str]:
    """Every leaf path in a normalized schema. Depth-capped."""
    if not isinstance(schema, dict) or _depth > MAX_LEAF_DEPTH:
        return [prefix] if prefix else []
    kind = schema.get("kind", "unknown")
    if kind == "cell":
        return ["%s.value" % prefix if prefix else "value"]
    if kind == "array":
        items = schema.get("items")
        if isinstance(items, dict):
            return schema_leaf_paths(items, "%s.0" % prefix if prefix else "0", _depth + 1)
        return [prefix] if prefix else []
    if kind in _MAPPING_KINDS:
        props = schema.get("properties") or {}
        if not props:
            return [prefix] if prefix else []
        out: List[str] = []
        for key in sorted(props):
            child = "%s.%s" % (prefix, key) if prefix else key
            out.extend(schema_leaf_paths(props[key], child, _depth + 1))
        return out
    return [prefix] if prefix else []


def count_leaves(schema: Optional[Dict[str, Any]]) -> int:
    return len(schema_leaf_paths(schema))


# --------------------------------------------------------------------------- #
# Field identities for similarity comparison (CONTRACT.md B2)
# --------------------------------------------------------------------------- #

#: Root-level keys the platform wraps around the real payload. The table
#: viewer output is `{schema, data, user_documents, metadata}`
#: (reference/output_types.md "OutputType enum"), plus `table_v1_log_id` from
#: the `table` kind. They are boilerplate, present on some steps and not
#: others, and about 13% of all catalog leaves — counting them deflates every
#: overlap score by a near-constant and makes every threshold in the CONTRACT
#: wrong by that amount. Dropped at the ROOT only; a real nested field of the
#: same name survives.
OUTPUT_ENVELOPE_KEYS = frozenset(("schema", "user_documents", "metadata",
                                  "table_v1_log_id"))

#: Kinds that are one field and are never descended into. `cell` is a single
#: extraction leaf (CONTRACT.md "Cell wrapping"); `file`, `knowledge_base` and
#: the five named types from A3 are opaque platform types whose internal
#: members (user_document_id, filename, ...) are not authored fields.
OPAQUE_FIELD_KINDS = frozenset(("cell", "file", "knowledge_base") + NAMED_TYPE_KINDS)

_NONALNUM = re.compile(r"[^a-z0-9]+")


def field_identity(path: Any) -> Tuple[str, str]:
    """Normalize a field path into (display, token).

    display — the path with numeric array-index segments and a leading `data.`
              removed, e.g. "limits.each_occurrence".
    token   — display lowercased with every non-alphanumeric character stripped
              per segment, e.g. "limits.eachoccurrence".

    Segment structure survives (the dots stay) so `a.bc` and `ab.c` cannot
    collide. For the overwhelmingly common flat extraction case
    (`data.<field>`) this reduces to the normalized leaf field name.
    """
    if isinstance(path, (list, tuple)):
        segments = [str(s) for s in path]
    else:
        segments = str(path).split(".")
    segments = [s for s in segments if s != "" and not s.isdigit()]
    if segments and segments[0] == "data":
        segments = segments[1:]
    display: List[str] = []
    tokens: List[str] = []
    for segment in segments:
        token = _NONALNUM.sub("", segment.lower())
        if token:
            tokens.append(token)
            display.append(segment)
    return ".".join(display), ".".join(tokens)


def comparable_fields(step_output: Optional[Dict[str, Any]], *,
                      authored_only: bool = True) -> List[str]:
    """Field identities for similarity comparison, envelope stripped.

    One entry per authored field. Cells count once, not as `.value` plus
    siblings. Opaque named types are not descended. `dynamic_keys` count as
    fields so classify steps are comparable. Root envelope keys removed.
    The ONLY sanctioned input to any overlap score (CONTRACT.md B2).

    `authored_only=False` keeps the platform envelope keys, for a caller that
    wants the full root inventory; the walk is otherwise identical.

    Deliberately NOT `schema_leaf_paths`, which answers the type resolver's
    question: it renders a cell as `<path>.value`, descends `file` into its
    four members, and ignores `dynamic_keys`. Using the resolver's walker here
    would make every classify step a single field and every file a fan of four,
    and the two sides of a comparison would stop lining up.
    """
    return _comparable_walk(step_output, authored_only)[0]


def comparable_raw_paths(step_output: Optional[Dict[str, Any]], *,
                         authored_only: bool = True) -> List[str]:
    """The same walk as `comparable_fields`, un-normalized.

    Keeps the leading `data.` and the synthetic `.0` array segments, so every
    entry is a real path that `resolve_path` can verify. `comparable_fields`
    strips those for matching, which makes its output deliberately
    non-resolvable — use this one for any check that has to round-trip.

    ASYMMETRY, and it has already broken one caller: the two lists are NOT the
    same length modulo normalization. This walk emits paths that normalize away
    to nothing, and `comparable_fields` correctly drops them. There are two
    shapes, both real in the corpus (25 paths across 25 steps):

    - a bare `data` on a step declaring an empty payload object (20 cases,
      e.g. `LPL Quote Fields Display`, `Check OFAC Sanctions`). `data` is the
      one segment `field_identity` strips, so nothing survives.
    - a bare `data.0` on a step whose payload is an empty ROW object (5 cases,
      e.g. `Normalize LPL Comparison Groups`). Both segments are stripped —
      `data` by name and `0` as a numeric index.

    `resolve_path` confirms every one of them is a real path, so they are not
    walker bugs. The correct parity check is: the display set equals the raw
    set minus the entries whose `field_identity` token is empty. Never compare
    lengths, and do not special-case the literal string "data" — key on the
    empty token, which covers both shapes.
    """
    return _comparable_walk(step_output, authored_only)[1]


def _comparable_walk(step_output: Optional[Dict[str, Any]],
                     authored_only: bool) -> Tuple[List[str], List[str]]:
    """Shared walker: returns (display identities, raw resolvable paths)."""
    out: List[str] = []
    raw_out: List[str] = []
    seen: Set[str] = set()
    seen_raw: Set[str] = set()

    def emit(prefix: List[str]) -> None:
        raw = ".".join(str(x) for x in prefix)
        if raw and raw not in seen_raw:
            seen_raw.add(raw)
            raw_out.append(raw)
        display, token = field_identity(prefix)
        if not token or token in seen:
            return
        seen.add(token)
        out.append(display)

    def walk(node: Any, prefix: List[str], depth: int) -> None:
        if depth > MAX_LEAF_DEPTH or not isinstance(node, dict):
            emit(prefix)
            return
        if node.get("kind") in OPAQUE_FIELD_KINDS:
            emit(prefix)
            return
        props = node.get("properties")
        dynamic = node.get("dynamic_keys")
        descended = False
        if isinstance(props, dict) and props:
            descended = True
            for key in props:
                if depth == 0 and authored_only and key in OUTPUT_ENVELOPE_KEYS:
                    continue
                walk(props[key], prefix + [key], depth + 1)
        if isinstance(dynamic, list) and dynamic:
            descended = True
            for key in dynamic:
                emit(prefix + [str(key)])
        if descended:
            return
        items = node.get("items")
        if isinstance(items, dict):
            walk(items, prefix + ["0"], depth + 1)
            return
        emit(prefix)

    if isinstance(step_output, dict):
        walk(step_output, [], 0)
    return out, raw_out


def comparable_field_map(step_output: Optional[Dict[str, Any]], *,
                         authored_only: bool = True) -> Dict[str, str]:
    """{match token: display path} for one step, same walk as comparable_fields.

    Scoring wants the tokens; a human-readable loss/gain list wants the
    display paths. Both passes that compare field sets need the pair, so the
    mapping is built here once rather than derived twice.
    """
    out: Dict[str, str] = {}
    for display in comparable_fields(step_output, authored_only=authored_only):
        _, token = field_identity(display)
        if token and token not in out:
            out[token] = display
    return out


def schema_depth(schema: Optional[Dict[str, Any]], _depth: int = 0) -> int:
    if not isinstance(schema, dict) or _depth > MAX_LEAF_DEPTH:
        return _depth
    kind = schema.get("kind", "unknown")
    children: List[Dict[str, Any]] = []
    if kind == "array" and isinstance(schema.get("items"), dict):
        children = [schema["items"]]
    elif kind in _MAPPING_KINDS:
        children = [v for v in (schema.get("properties") or {}).values()
                    if isinstance(v, dict)]
    if not children:
        return _depth
    return max(schema_depth(c, _depth + 1) for c in children)


# --------------------------------------------------------------------------- #
# Findings (CONTRACT.md "Findings" + "common.py public API")
# --------------------------------------------------------------------------- #

SEVERITIES = ("blocker", "warning", "note")
_SEVERITY_ORDER = {"blocker": 0, "warning": 1, "note": 2}
_COUNT_KEY = {"blocker": "blockers", "warning": "warnings", "note": "notes"}


def finding(code: str, severity: str, title: str, detail: str, *,
            step: Optional[str] = None, fix: Optional[str] = None,
            evidence: Optional[Dict[str, Any]] = None,
            group_key: Optional[str] = None) -> Dict[str, Any]:
    """Build one finding, WITHOUT the "pass" key — PassOutput.write() stamps it.

    Raises ValueError on an unknown severity so a typo fails loudly instead of
    silently dropping a blocker.
    """
    if severity not in SEVERITIES:
        raise ValueError("unknown severity %r (want one of %s)"
                         % (severity, ", ".join(SEVERITIES)))
    return {
        "code": code,
        "severity": severity,
        "step": step,
        "title": title,
        "detail": detail,
        "fix": fix,
        "evidence": evidence or {},
        "group_key": group_key,
    }


def sort_findings(findings: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(findings, key=lambda f: (
        _SEVERITY_ORDER.get(f.get("severity"), 9),
        f.get("code") or "",
        f.get("step") or "",
    ))


class PassOutput:
    """Collector, writer, and bounded printer for one pass."""

    def __init__(self, pass_name: str, out_dir: str) -> None:
        if pass_name not in PASS_NAMES:
            raise ValueError("unknown pass %r (want one of %s)"
                             % (pass_name, ", ".join(PASS_NAMES)))
        self.pass_name = pass_name
        self.out_dir = str(out_dir)
        self._findings: List[Dict[str, Any]] = []
        self._extras: Dict[str, Any] = {}
        self._path: Optional[pathlib.Path] = None

    # -- collection ------------------------------------------------------- #

    def add(self, finding_dict: Dict[str, Any]) -> None:
        if not isinstance(finding_dict, dict) or "severity" not in finding_dict:
            raise ValueError("add() wants a dict from finding(), got %r" % (finding_dict,))
        if finding_dict["severity"] not in SEVERITIES:
            raise ValueError("unknown severity %r" % finding_dict["severity"])
        self._findings.append(finding_dict)

    def add_all(self, findings: Iterable[Dict[str, Any]]) -> None:
        for item in findings or []:
            self.add(item)

    def set(self, key: str, value: Any) -> None:
        if key in ("pass", "findings"):
            raise ValueError("%r is reserved; PassOutput owns it" % key)
        self._extras[key] = value

    # -- reporting -------------------------------------------------------- #

    def counts(self) -> Dict[str, int]:
        out = {"blockers": 0, "warnings": 0, "notes": 0}
        for item in self._findings:
            key = _COUNT_KEY.get(item.get("severity"))
            if key:
                out[key] += 1
        return out

    @property
    def findings(self) -> List[Dict[str, Any]]:
        return list(self._findings)

    def write(self) -> pathlib.Path:
        """Write <out_dir>/<pass_name>.json, stamping "pass" on every finding."""
        os.makedirs(self.out_dir, exist_ok=True)
        stamped = []
        for item in sort_findings(self._findings):
            row = dict(item)
            row["pass"] = self.pass_name
            stamped.append(row)
        payload: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "pass": self.pass_name,
            "counts": self.counts(),
        }
        payload.update(self._extras)
        payload["findings"] = stamped
        path = pathlib.Path(self.out_dir) / ("%s.json" % self.pass_name)
        tmp = str(path) + ".tmp"
        with open(tmp, "w") as handle:
            json.dump(payload, handle, indent=2, default=str)
            handle.write("\n")
        os.replace(tmp, str(path))
        self._path = path
        return path

    def print_summary(self, extra_lines: Optional[Sequence[str]] = None) -> int:
        """Bounded print per CONTRACT.md "Output discipline".

        Returns the intended process exit code: 1 if any blocker, else 0. Does
        not call sys.exit — the caller does `sys.exit(out.print_summary())`.
        """
        counts = self.counts()
        out_path = str(self._path) if self._path else os.path.join(
            self.out_dir, "%s.json" % self.pass_name)

        for line in extra_lines or []:
            print(line)

        rows = self._collapse()
        cap = THRESHOLDS["MAX_FINDINGS_PRINTED"]
        shown = rows[:cap]
        if shown:
            print("")
        for item, count in shown:
            where = item.get("step") or "(workflow)"
            suffix = "  [x%d]" % count if count > 1 else ""
            print("%-7s  %-40s %s%s" % (
                item["severity"].upper(), truncate(where, 40),
                item.get("title") or item.get("code"), suffix))
            detail = (item.get("detail") or "").strip()
            if detail:
                print("         %s" % truncate(detail, 190))
            fix = (item.get("fix") or "").strip()
            if fix:
                print("         fix: %s" % truncate(fix, 190))
        remaining = len(rows) - len(shown)
        if remaining > 0:
            print("... %d more (see %s)" % (remaining, out_path))

        print("SUMMARY %s blockers=%d warnings=%d notes=%d out=%s" % (
            self.pass_name, counts["blockers"], counts["warnings"],
            counts["notes"], out_path))
        return 1 if counts["blockers"] else 0

    def _collapse(self) -> List[Tuple[Dict[str, Any], int]]:
        """Collapse findings sharing a group_key into one row plus a count."""
        out: List[Tuple[Dict[str, Any], int]] = []
        seen: Dict[str, int] = {}
        for item in sort_findings(self._findings):
            key = item.get("group_key")
            if not key:
                out.append((item, 1))
                continue
            if key in seen:
                index = seen[key]
                out[index] = (out[index][0], out[index][1] + 1)
            else:
                seen[key] = len(out)
                out.append((item, 1))
        return out


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #

def truncate(text: Any, limit: int) -> str:
    """Collapse whitespace and cap length with a single-char ellipsis."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: max(0, limit - 1)] + "…"


def slug(name: str) -> str:
    """Filesystem-safe form of a step name, for sidecar paths.

    Case-preserving on purpose: this is the one implementation, and it has to
    keep matching the paths html_probe already builds, so a step's sidecar is
    predictable from its name alone.
    """
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", str(name)).strip("_")
    return cleaned or "step"


def table(rows: Sequence[Sequence[Any]], headers: Sequence[str],
          max_rows: int = MAX_TABLE_ROWS) -> List[str]:
    """Format a bounded text table. Returns lines; appends '... N more' when
    truncated. Column widths are computed from the header plus shown rows."""
    shown = [list(r) for r in list(rows)[:max_rows]]
    columns = len(headers)
    widths = [len(str(h)) for h in headers]
    cell_cap = 46
    for row in shown:
        for index in range(columns):
            value = truncate(row[index], cell_cap) if index < len(row) else ""
            widths[index] = max(widths[index], len(value))

    def render(cells: Sequence[Any]) -> str:
        parts = []
        for index in range(columns):
            value = truncate(cells[index], cell_cap) if index < len(cells) else ""
            parts.append(value.ljust(widths[index]) if index < columns - 1 else value)
        return "  " + "  ".join(parts).rstrip()

    lines = [render(headers), "  " + "  ".join("-" * w for w in widths)]
    for row in shown:
        lines.append(render(row))
    total = len(rows)
    if total > len(shown):
        lines.append("  ... %d more of %d" % (total - len(shown), total))
    return lines


# --------------------------------------------------------------------------- #
# Facts loading
# --------------------------------------------------------------------------- #

def load_facts(path: str) -> Dict[str, Any]:
    """Read and lightly validate facts.json.

    Raises SystemExit(2) when the file is missing, unparseable, or carries the
    wrong schema_version — exit 2 is reserved for the script itself failing,
    never for finding blockers (CONTRACT.md hard rule 5).
    """
    try:
        with open(path) as handle:
            facts = json.load(handle)
    except FileNotFoundError:
        _die("error: no facts file at %s — run wf_facts.py <workflow.json> --out %s first"
             % (path, os.path.dirname(path) or "."))
    except OSError as exc:
        _die("error: cannot read %s: %s" % (path, exc))
    except json.JSONDecodeError as exc:
        _die("error: %s is not valid JSON (%s) — rerun wf_facts.py" % (path, exc))

    if not isinstance(facts, dict):
        _die("error: %s does not hold a facts object" % path)
    version = facts.get("schema_version")
    if version != SCHEMA_VERSION:
        _die("error: %s has schema_version %r, this build expects %d — rerun wf_facts.py"
             % (path, version, SCHEMA_VERSION))
    return facts


def _die(message: str) -> None:
    """Print to stderr and exit 2 — reserved for the script itself failing."""
    print(message, file=sys.stderr)
    raise SystemExit(2)


def steps_by_name(facts: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {s["name"]: s for s in facts.get("steps", [])}


def add_common_args(parser: Any, needs_facts: bool = True) -> None:
    """The shared CLI shape: --facts <facts.json> --out <dir> [--self-test].

    Nothing is marked `required` here. argparse enforces required arguments
    before main ever runs, which made `--self-test` on its own impossible —
    a self-test has no facts file and no output directory to be given. Call
    `require_args()` from main instead, which skips the check when
    `--self-test` was passed.
    """
    if needs_facts:
        parser.add_argument("--facts", help="path to facts.json "
                                           "(required unless --self-test)")
    parser.add_argument("--out", help="output directory "
                                      "(required unless --self-test)")
    parser.add_argument("--self-test", action="store_true",
                        help="run against the built-in corpus and assert invariants")


def require_args(args: Any, *names: str) -> None:
    """Enforce the arguments main actually needs, unless --self-test was given.

    Exits 2 with a clear message on a missing argument, per CONTRACT.md hard
    rule 5 — exit 2 is the script itself failing to run, never a finding.
    """
    if getattr(args, "self_test", False):
        return
    missing = [n for n in names if not getattr(args, n.replace("-", "_"), None)]
    if missing:
        flags = " ".join("--%s" % n.replace("_", "-") for n in missing)
        _die("error: missing required argument(s): %s "
             "(or pass --self-test to run against the built-in corpus)" % flags)
