#!/usr/bin/env python3
"""Pass 2 — data flow and type resolution.

Turns the resolved edges in `facts.json` into judgments. This is the pass that
closes the platform's documented validation gap: the save-time validator only
checks the FIRST segment of every `output_attribute`
(reference/input_mappings.md, "The validator only checks the first segment ...
Deeper paths fail at runtime, not at validation"), so a deep-path typo saves
green and then silently feeds empty data to every downstream step.

Checks, in the order they print:

  1. UNRESOLVED_PATH          a dependency path that does not exist upstream
     CONTAINER_VALUE_SUFFIX   `.value` read off an array or object node
  2. CELL_NOT_UNWRAPPED       a `.value` is missing (cell fed to a scalar)
     CELL_WIRED_WHOLE         a cell fed to an object parameter (legitimate)
     TYPE_MISMATCH            any other incompatible resolved type
     INDEX_MISMATCH           an array/element confusion (missing/extra `.0`)
  3. MISSING_REQUIRED_INPUT   a required input mapping nothing supplies
  4. OPERATOR_*               multi-source resolution operator problems
     DUPLICATE_INPUT_MAPPING  two mappings filling one parameter
  5. CODE_SIGNATURE_MISMATCH  a wired input the `run()` signature cannot accept
     CODE_RETURN_MISSING      a declared output the code never returns
     CODE_RETURN_EXTRA        a returned key that is not declared
  6. UNCONSUMED_FIELD         produced and never consumed (inventory 1)
     UNVERIFIED_PATH          consumed but not backed by a declared field (2)

Two inventories land in the JSON payload under stable keys, whatever the print
cap does:

  "unconsumed_fields"  [{step, step_type, path, type, hidden, terminal,
                         excuse}, ...]   nothing reads it, verdict is final
  "undecidable_fields" [{step, path, reason, detail}, ...]   CONTRACT.md B10:
                         usage can be shown neither way, so it is NOT unused
  "unproduced_paths"   [{from_step, output_attribute, to_step, to_param,
                         reason, detail, available}, ...]
  "summary"            per-check counters
  "coverage"           how much of the wiring was actually type-checked, and
                       one named bucket per reason the rest was not

Every platform rule carries a comment naming the workflow-skill reference doc
it came from (CONTRACT.md "Reference docs"). Where a rule cannot be sourced,
this pass stays silent rather than inventing behavior.

stdlib only. Python 3.9 compatible.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import (  # noqa: E402
    PassOutput,
    add_common_args,
    finding,
    load_facts,
    make_file,
    require_args,
    sch,
    table,
    truncate,
    type_name,
    types_compatible,
)

PASS_NAME = "flow"

# How many inventory rows become individual findings. The rest stay in JSON.
MAX_INVENTORY_FINDINGS = 8

# --------------------------------------------------------------------------- #
# Expected parameter types
#
# reference/input_mappings.md "Save-time type compatibility (directional)"
# defines the rule; the per-parameter expected types below are transcribed from
# each step's own contract in step_types/<type>.md ("Required inputs" table)
# and reference/required_inputs.md. A (step_type, param) pair that is NOT in
# this table is never type-checked — this pass does not guess a parameter's
# declared type, because a wrong guess is a false blocker on a working
# workflow.
#
# custom_step / function are deliberately absent: their parameter types live
# in `config.input_schema`, which facts.json records by NAME only
# (`code_steps[*].declared_input_properties`). See the report's gaps section.
# --------------------------------------------------------------------------- #


def _array(items: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return sch("array", items=items)


_STR = sch("string")
_NUM = sch("number")
_OBJ = sch("object")

EXPECTED_TYPES: Dict[str, Dict[str, Dict[str, Any]]] = {
    # step_types/prepare_documents.md "Required inputs"
    "prepare_documents": {"user_document_ids": _array(_STR)},
    # step_types/classify_documents.md "Required inputs"
    "classify_documents": {"documents": _array(make_file()), "system_prompt": _STR},
    # step_types/knowledge_base.md "Required inputs"
    "knowledge_base": {
        "documents": _array(make_file()), "emails": _array(),
        "email_body": _STR, "email_body_plain": _STR, "headers": _STR,
        "email_document_id": _STR, "email_user_document_id": _STR,
    },
    # step_types/extract_from_multiple_sources.md "Parameters available in input_mappings"
    "extract_from_multiple_sources": {
        "documents": _array(make_file()), "emails": _array(),
        "eml_user_document_id": _STR, "email_body": _STR, "subject": _STR,
        "body_plain": _STR, "body_html": _STR,
        "email_system_prompt": _STR, "document_system_prompt": _STR,
    },
    # step_types/extract_rows_from_multiple_sources.md "Required inputs"
    "extract_rows_from_multiple_sources": {
        "documents": _array(), "emails": _array(), "system_prompt": _STR,
    },
    # step_types/extract_mixed_schema_from_multiple_sources.md "Required inputs"
    "extract_mixed_schema_from_multiple_sources": {
        "documents": _array(), "emails": _array(), "system_prompt": _STR,
    },
    # step_types/extract_from_document.md "Required inputs"
    "extract_from_document": {"document": make_file(), "system_prompt": _STR},
    # step_types/extract_from_email.md "Required inputs"
    "extract_from_email": {
        "system_prompt": _STR, "subject": _STR, "body_html": _STR,
        "body_plain": _STR, "eml_user_document_id": _STR,
    },
    # step_types/sov_mapping.md "Required inputs"
    "sov_mapping": {"documents": _array()},
    # step_types/agentic_extraction.md "Required inputs"
    "agentic_extraction": {"kb": _OBJ, "documents": _array(), "system_prompt": _STR},
    # step_types/agentic_guideline_check.md "Required inputs"
    "agentic_guideline_check": {"kb": _OBJ, "documents": _array(), "system_prompt": _STR},
    # step_types/combine_kv_tables.md "Required inputs"
    "combine_kv_tables": {"sources": _array()},
    # step_types/generate_qa_table_from_agent.md "Required inputs"
    "generate_qa_table_from_agent": {
        "kb": _OBJ, "documents": _array(make_file()), "questions": _array(),
    },
    # step_types/multi_column_qa.md "Required inputs"
    "multi_column_qa": {
        "kb": _OBJ, "documents": _array(), "system_prompt": _STR,
        "schema_instances": _array(),
    },
    # step_types/extract_guidelines.md "Required inputs"
    "extract_guidelines": {"guideline_document": make_file(), "system_prompt": _STR},
    # step_types/check_guidelines.md "Required inputs" — only the one whose type
    # the doc states ("Prepare Documents.documents or a Classify bucket").
    "check_guidelines": {"submission_documents": _array()},
    # step_types/submission_summary_generator.md "Required inputs"
    "submission_summary_generator": {
        "data_points": _array(), "system_prompt": _STR, "kb": _OBJ,
        "documents": _array(make_file()),
    },
    # step_types/compare_document_data.md "Required inputs"
    "compare_document_data": {
        "data_points": _array(_OBJ), "user_documents": _array(_OBJ),
        "system_prompt": _STR,
    },
    # step_types/web_agent.md "Contract"
    "web_agent": {"prompt": _STR},
    # step_types/fill_docx.md "Required inputs"
    "fill_docx": {
        "template": _OBJ, "context": _OBJ, "docx_images": _OBJ,
        "output_filename": _STR,
    },
    # step_types/text_block.md "Inputs"
    "text_block": {"markdown_content": _STR},
    # step_types/run_workflow.md "Required inputs (via input_mappings)"
    "run_workflow": {
        "target_workflow_id": _STR, "documents": _array(),
        "execution_id": _STR, "eml_user_document_id": _STR, "user_inputs": _array(),
    },
    # step_types/execution_matching.md "Inputs" — every canonical field is a scalar.
    "execution_matching": {
        name: _STR for name in (
            "account_name", "account_address", "fein", "broker_email",
            "broker_domain", "broker_name", "broker_signature",
            "effective_date", "line_of_business", "quote_policy_ref")
    },
    # step_types/loop.md "Required inputs"
    "loop": {"iteration_source": _array()},
    # step_types/ofac_agent.md "Required inputs"
    "ofac_agent": {"entity_data": _OBJ},
    # step_types/osha_agent.md "Required inputs"
    "osha_agent": {"insured_data": _OBJ},
    # step_types/trellis_law.md "Inputs"
    "trellis_law": {
        "insured_names": _array(_STR), "insured_address": _STR, "years_back": _NUM,
    },
    # step_types/hold.md "Required inputs"
    "hold": {"data": _OBJ},
    # step_types/email.md "Required inputs"
    "email": {
        "email_body": _STR, "subject": _STR,
        "attachment_document_ids": _array(_STR), "attachment_data_list": _array(),
        "zapier_reply_url": _STR, "from_email": _STR, "to_email": _STR,
        "cc_emails": _array(_STR), "thread_id": _STR,
    },
}

# --------------------------------------------------------------------------- #
# Required inputs — reference/required_inputs.md, cross-checked against each
# step_types/<type>.md "Required inputs" table.
# --------------------------------------------------------------------------- #

#: Every listed parameter must be supplied by some input mapping.
REQUIRED_ALL: Dict[str, Tuple[str, ...]] = {
    "prepare_documents": ("user_document_ids",),
    "classify_documents": ("documents",),
    "sov_mapping": ("documents",),
    "agentic_extraction": ("kb",),
    "combine_kv_tables": ("sources",),
    "generate_qa_table_from_agent": ("documents", "kb"),
    "multi_column_qa": ("documents", "kb"),
    "extract_guidelines": ("guideline_document",),
    "check_guidelines": ("extracted_guidelines", "guideline_document",
                         "submission_documents"),
    "submission_summary_generator": ("data_points", "system_prompt"),
    "compare_document_data": ("data_points", "user_documents"),
    "web_agent": ("prompt",),
    "fill_docx": ("template", "context"),
    "text_block": ("markdown_content",),
    "run_workflow": ("target_workflow_id", "documents"),
    "execution_matching": ("account_name",),
    "loop": ("iteration_source",),
    "ofac_agent": ("entity_data",),
    "osha_agent": ("insured_data",),
    "trellis_law": ("insured_names",),
    "hold": ("data",),
}

#: At least one of each listed group must be supplied.
REQUIRED_ONE_OF: Dict[str, Tuple[Tuple[str, ...], ...]] = {
    "extract_rows_from_multiple_sources": (("documents", "emails"),),
    "extract_mixed_schema_from_multiple_sources": (("documents", "emails"),),
    "knowledge_base": (("documents", "emails", "email_body", "email_body_plain",
                        "email_headers", "headers"),),
}

#: extract_from_multiple_sources' rule is a cross-field combination, not a set
#: of independent requirements — step_types/extract_from_multiple_sources.md
#: "Required inputs" (1)-(4).
EFMS_EMAIL_SCALARS = ("eml_user_document_id", "headers", "email_body",
                      "body_html", "body_plain")

# --------------------------------------------------------------------------- #
# Resolution operators — reference/input_mappings.md "Resolution operators"
# --------------------------------------------------------------------------- #

OPERATORS = ("concat_lists", "merge_dicts", "numeric_add", "numeric_subtract",
             "numeric_multiply", "numeric_divide", "string_join",
             "custom_function")

NUMERIC_OPERATORS = ("numeric_add", "numeric_subtract", "numeric_multiply",
                     "numeric_divide")

#: Kinds a numeric operator can actually add. `unknown`/`any` are never blocked.
_NUMERIC_OK = frozenset(("number", "unknown", "any"))

#: Kinds merge_dicts can merge. reference/input_mappings.md: "Merge dicts".
#: Every named type is dict-shaped at runtime, so they all qualify.
_DICT_OK = frozenset(("object", "table", "knowledge_base", "file", "cell",
                      "video_file", "email_headers", "citation", "docx_image",
                      "sov_field", "unknown", "any"))

_SCALARS = frozenset(("string", "number", "boolean"))

#: The five cell members. reference/input_mappings.md "A bare `data.<field>` is
#: not rejected at save time": the path walker synthesizes the cell shape on
#: demand whenever a primitive leaf is followed by one of these.
CELL_MEMBERS = ("value", "confidence_score", "confidence_reason",
                "citations", "thinking_steps")

# --------------------------------------------------------------------------- #
# What counts as a consumer of a produced field
#
# An input mapping is not the only thing that can read a step's output. A
# step's `output_type` can itself be the consumer: the step returns data and
# the platform performs the declared side effect. Sourced from
# reference/output_types.md "OutputType enum" and step_types/custom_step.md.
# --------------------------------------------------------------------------- #

#: reference/output_types.md "OutputType enum": these two renderers are pure
#: side effects on the returned data — `set_title` sets the execution title
#: from the returned `title`, `update_workflow_log` writes execution-log
#: fields. The platform consumes them whether or not the step is displayed, so
#: "no input mapping reads it" proves nothing. `table`, `email`,
#: `document_viewer`, `document_classification` and `knowledge_base` are
#: RENDERERS, so they consume only on a visible step; that case is already
#: covered by the display_step / terminal / user-facing-type tests below.
PLATFORM_CONSUMED_OUTPUT_TYPES = frozenset(("set_title", "update_workflow_log"))

#: reference/output_types.md "Pattern A — DEFAULT": the backend auto-fills the
#: entire output shape for these step types, so an unread field is not
#: something the author can trim.
PLATFORM_SHAPED_TYPES = frozenset(("prepare_documents", "knowledge_base"))

#: CONTRACT.md "Notes on producing it": terminal step types. Data that reaches
#: one of these reaches a person.
USER_FACING_TYPES = frozenset((
    "email", "fill_docx", "submission_summary_generator", "document_viewer",
    "text_block", "hold",
))

#: The engine reads a decision's output to pick a branch; no wired consumer will.
CONTROL_FLOW_TYPES = frozenset(("decision", "loop"))

#: CONTRACT.md "Notes on producing it": code step types. Their parameter types
#: live in `config.input_schema`, which facts.json records by NAME only, so no
#: parameter of theirs can be type-checked. This is the pass's largest known
#: coverage gap and `coverage()` counts it explicitly rather than hiding it.
CODE_STEP_TYPES = frozenset(("custom_step", "function"))


# --------------------------------------------------------------------------- #
# Indexing
# --------------------------------------------------------------------------- #

class Mapping(object):
    """One `input_mappings` entry, reassembled from its per-source edges.

    facts.json records one edge per `dependency_step_outputs` entry (plus one
    source-less edge for every non-dependency input, CONTRACT.md A2), so a
    multi-source mapping arrives as N rows sharing (to_step, to_param).
    """

    __slots__ = ("step", "param", "edges")

    def __init__(self, step: str, param: str) -> None:
        self.step = step
        self.param = param
        self.edges: List[Dict[str, Any]] = []

    @property
    def input_type(self) -> Optional[str]:
        return self.edges[0].get("input_type") if self.edges else None

    @property
    def via(self) -> str:
        return self.edges[0].get("via") or "input_mappings" if self.edges else ""

    @property
    def operator(self) -> Optional[Any]:
        for edge in self.edges:
            if edge.get("resolution_operator") is not None:
                return edge["resolution_operator"]
        return None

    @property
    def is_dependency(self) -> bool:
        return self.input_type == "dependency"

    @property
    def is_param_mapping(self) -> bool:
        """True only for a real `input_mappings` entry.

        facts.json also records edges that carry no input parameter at all: a
        decision operand referenced inline (`via: "decision_operand"`) and a
        legacy bare entry in the step's `dependencies` array
        (`via: "dependencies"`, `to_param: null`). Those are graph edges, not
        parameters, so no parameter-level check applies to them.
        """
        return self.via == "input_mappings" and bool(self.param)

    @property
    def source_count(self) -> int:
        return len(self.edges) if self.is_dependency else 0

    @property
    def multi(self) -> bool:
        if self.source_count > 1:
            return True
        return bool(self.edges and self.edges[0].get("multi_source"))


def index_mappings(facts: Dict[str, Any]) -> List[Mapping]:
    """Group edges back into mappings, preserving declaration order.

    `source_index` restarts at 0 for every mapping, so a repeat of 0 inside one
    (to_step, to_param) group means a SECOND input_mappings entry targeting the
    same parameter — not another source of the same mapping. Splitting there
    keeps a duplicate mapping from being misread as a multi-source one.
    """
    open_mapping: Dict[Tuple[str, str], Mapping] = {}
    out: List[Mapping] = []
    for edge in facts.get("edges", []):
        key = (edge.get("to_step"), edge.get("to_param"))
        current = open_mapping.get(key)
        if current is None or edge.get("source_index") == 0:
            current = Mapping(key[0], key[1])
            open_mapping[key] = current
            out.append(current)
        current.edges.append(edge)
    return out


def check_duplicates(mappings: Sequence[Mapping]) -> List[Dict[str, Any]]:
    """Two input_mappings entries filling the same parameter. Only one wins,
    and which one is not something the reference docs pin down."""
    seen: Dict[Tuple[str, str], int] = collections.Counter()
    for mapping in mappings:
        if mapping.is_param_mapping:
            seen[(mapping.step, mapping.param)] += 1
    out: List[Dict[str, Any]] = []
    for (step, param), count in sorted(seen.items(), key=lambda kv: (
            kv[0][0] or "", kv[0][1] or "")):
        if count < 2:
            continue
        out.append(finding(
            "DUPLICATE_INPUT_MAPPING", "warning",
            "Two input mappings fill the same parameter",
            "%s has %d input_mappings entries for `%s`. Only one can win and "
            "the reference docs do not say which." % (step, count, param),
            step=step,
            fix="Delete the redundant entry, or merge the sources into one "
                "mapping with a resolution_operator.",
            evidence={"param": param, "count": count},
            group_key="DUPLICATE_INPUT_MAPPING:%s" % step))
    return out


# --------------------------------------------------------------------------- #
# 1. Unresolved paths
# --------------------------------------------------------------------------- #

def check_unresolved(facts: Dict[str, Any],
                    claimed: Optional[Set[int]] = None) -> List[Dict[str, Any]]:
    """Every edge with resolved=false is a blocker.

    The available-keys list from the resolve error is what makes the finding
    actionable, so it goes in the detail verbatim. Grouped by SOURCE step: a
    dozen typos against one classify step collapse to one printed line.

    `claimed` holds the ids of edges a more specific detector already reported
    (the container-`.value` case), so nothing is said twice.
    """
    out: List[Dict[str, Any]] = []
    for edge in facts.get("edges", []):
        if edge.get("resolved") or id(edge) in (claimed or ()):
            continue
        source = edge.get("from_step") or "(unwired)"
        path = edge.get("output_attribute")
        error = edge.get("resolve_error") or "path did not resolve"
        available = edge.get("resolve_available") or []
        at = edge.get("resolve_at")

        detail = "`%s` on %s: %s." % (path, source, error)
        if available:
            detail += " Available: %s." % ", ".join(str(a) for a in available)
        detail += (" The save-time validator only checks the first path segment,"
                   " so this saves clean and returns nothing at runtime.")
        fix = _unresolved_fix(path, at, error, available)

        out.append(finding(
            "UNRESOLVED_PATH", "blocker",
            "Path does not exist in source output",
            detail,
            step=edge.get("to_step"), fix=fix,
            evidence={"from_step": source, "output_attribute": path,
                      "to_param": edge.get("to_param"), "at": at,
                      "available": available},
            group_key="UNRESOLVED_PATH:%s" % source))
    return out


def _unresolved_fix(path: Optional[str], at: Optional[str], error: str,
                    available: Sequence[Any]) -> Optional[str]:
    """The corrected path, spelled out, whenever the resolver's error gives
    enough to name one.

    `available` holds the real keys at the failure point, which for the
    array-`.value` case (reference/input_mappings.md "Only primitive leaves are
    wrapped") are the ROW's columns, not siblings of the bad segment — so that
    case gets its own fix rather than the near-miss suggestion.
    """
    where = at or path or ""
    lowered = (error or "").lower()

    # An array node with `.value` appended: the row has to be indexed first.
    if "index a row first" in lowered:
        stem = where[: -len(".value")] if where.endswith(".value") else where
        column = str(available[0]) if available else "<column>"
        return "Index a row first — wire `%s.0.%s.value`." % (stem, column)

    # A numeric segment against something that is not a list.
    if "not an array" in lowered:
        segment = _last_segment(where)
        return "Remove the `.%s` index — `%s` is not a list." % (
            segment, where.rsplit(".", 1)[0] if "." in where else where)

    if available:
        near = _closest(_last_segment(where), available)
        if near:
            corrected = _replace_last(where, near)
            return "Change the output_attribute to `%s%s`." % (
                corrected, _tail_after(path or "", where))
        return "Point output_attribute at one of the available keys above."
    return None


def _last_segment(path: str) -> str:
    return path.rsplit(".", 1)[-1] if path else ""


def _replace_last(path: str, segment: str) -> str:
    if "." not in path:
        return segment
    return path.rsplit(".", 1)[0] + "." + segment


def _tail_after(full: str, prefix: str) -> str:
    """The part of `full` past `prefix` (the segments we never got to check)."""
    if full == prefix or not full.startswith(prefix):
        return ""
    return full[len(prefix):]


def _closest(needle: str, options: Sequence[Any]) -> Optional[str]:
    """Cheapest useful near-miss: case/underscore-insensitive, then prefix."""
    if not needle:
        return None
    flat = needle.lower().replace("_", "").replace(" ", "")
    for option in options:
        text = str(option)
        if text.lower().replace("_", "").replace(" ", "") == flat:
            return text
    # A trailing-s / missing-s typo, the classify-class case.
    for option in options:
        text = str(option)
        low = text.lower().replace("_", "").replace(" ", "")
        if low == flat.rstrip("s") or low.rstrip("s") == flat:
            return text
    # A truncated or suffixed name, e.g. "Garage Quote_OLD" vs "Garage Quote".
    for option in options:
        text = str(option)
        low = text.lower().replace("_", "").replace(" ", "")
        if len(low) >= 4 and (flat.startswith(low) or low.startswith(flat)):
            return text
    return None


# --------------------------------------------------------------------------- #
# 1b. A cell member applied to a container
#
# reference/input_mappings.md "Only primitive leaves are wrapped — nested
# arrays and objects stay navigable": cell wrapping is a leaf-by-leaf walk, so
# an array stays an array and an object stays an object. Neither has a `value`
# key, and the walker will not synthesize a cell on a node that already has
# `items` or `properties`. `data.vehicles.value` therefore resolves to nothing
# at runtime and every downstream step runs green on empty data — the same
# silent-failure class as a classify-class typo, so it earns the same severity.
# --------------------------------------------------------------------------- #

def _container_misuse(edge: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
    """(container kind, the path up to the container, the bad member) or None."""
    if edge.get("resolved"):
        return None
    at = edge.get("resolve_at") or ""
    member = _last_segment(at)
    if member not in CELL_MEMBERS:
        return None
    stem = at.rsplit(".", 1)[0] if "." in at else at
    error = (edge.get("resolve_error") or "").lower()
    if "index a row first" in error or "has no '%s' key" % member in error:
        return ("array", stem, member)
    if "is not a property of" in error:
        return ("object", stem, member)
    return None


def check_containers(mappings: Sequence[Mapping],
                     steps: Dict[str, Dict[str, Any]]) -> Tuple[
                         List[Dict[str, Any]], Set[int]]:
    """Blockers for a cell member read off an array or object node."""
    out: List[Dict[str, Any]] = []
    claimed: Set[int] = set()
    for mapping in mappings:
        step = steps.get(mapping.step) or {}
        expected = EXPECTED_TYPES.get(step.get("type") or "", {}).get(mapping.param)
        for edge in mapping.edges:
            hit = _container_misuse(edge)
            if hit is None:
                continue
            claimed.add(id(edge))
            container, stem, member = hit
            source = edge.get("from_step") or "(unwired)"
            keys = [str(k) for k in (edge.get("resolve_available") or [])]
            out.append(finding(
                "CONTAINER_VALUE_SUFFIX", "blocker",
                "`.%s` applied to a %s node" % (member, container),
                "`%s` on %s: `%s` is %s, and %s node has no `%s` key. Only "
                "primitive leaves are cell-wrapped, so this resolves to nothing "
                "at runtime and every downstream step then runs green on empty "
                "data." % (edge.get("output_attribute"), source, stem,
                           "a list" if container == "array" else "an object",
                           "an array" if container == "array" else "an object",
                           member),
                step=mapping.step,
                fix=_container_fix(container, stem, member, keys, expected),
                evidence={"from_step": source,
                          "output_attribute": edge.get("output_attribute"),
                          "to_param": mapping.param, "container": container,
                          "container_path": stem, "member": member,
                          "available": keys,
                          "expected": type_name(expected) if expected else None},
                group_key="CONTAINER_VALUE_SUFFIX:%s" % source))
    return out, claimed


def _container_fix(container: str, stem: str, member: str,
                   keys: Sequence[str],
                   expected: Optional[Dict[str, Any]]) -> str:
    """The corrected path. Two intents are possible — one column, or the whole
    container — so use the consuming parameter's declared type to choose, and
    offer both spellings when there is no declared type to go on."""
    exp_kind = (expected or {}).get("kind")

    if container == "array":
        # An array of SCALARS wraps its element, so the element IS the cell and
        # the column name step does not exist: `data.aliases.0.value`.
        scalar_list = bool(keys) and set(keys) == set(CELL_MEMBERS)
        if scalar_list:
            one = "%s.0.%s" % (stem, member)
        else:
            column = keys[0] if keys else "<column>"
            one = "%s.0.%s.%s" % (stem, column, member)
        whole = stem
        if exp_kind == "array":
            return ("Drop the `.%s` — wire `%s` for the whole list (the "
                    "parameter wants a list)." % (member, whole))
        if exp_kind in _SCALARS or exp_kind == "file":
            return "Index a row first — wire `%s`." % one
        return ("Wire `%s` for one entry, or `%s` for the whole list — the "
                "consuming parameter's type (%s) does not say which was meant."
                % (one, whole, "`%s`" % type_name(expected) if expected
                   else "undeclared"))

    named = "%s.%s.%s" % (stem, keys[0], member) if keys else "%s.<field>.%s" % (stem, member)
    if exp_kind == "object":
        return ("Drop the `.%s` — wire `%s` for the whole object, or `%s` for "
                "one field." % (member, stem, named))
    return ("Reach the field by name — wire `%s`%s." % (
        named, (" (available: %s)" % ", ".join(keys[:8])) if keys else ""))


# --------------------------------------------------------------------------- #
# 2. Type mismatches
# --------------------------------------------------------------------------- #

def effective_type(mapping: Mapping) -> Optional[Dict[str, Any]]:
    """The type the consuming parameter actually receives.

    Single source: the resolved schema. Multi-source: what the resolution
    operator produces (reference/input_mappings.md "Resolution operators").
    """
    if not mapping.is_dependency or not mapping.edges:
        return None
    if not mapping.multi:
        return mapping.edges[0].get("resolved_schema")

    operator = mapping.operator
    sources = [e.get("resolved_schema") for e in mapping.edges]
    if operator == "concat_lists":
        # "Combine values into one list. Non-list values are wrapped."
        kinds = {(s or {}).get("kind") for s in sources}
        if kinds == {"array"}:
            items = [(s or {}).get("items") for s in sources]
            first = items[0] if items else None
            same = all(type_name(i) == type_name(first) for i in items)
            return sch("array", items=first if same else None)
        return sch("array")
    if operator == "merge_dicts":
        return sch("object")
    if operator in NUMERIC_OPERATORS:
        return sch("number")
    if operator == "string_join":
        return sch("string")
    # custom_function, or an operator we have no rule for: unknowable.
    return None


#: Every mapping lands in exactly one of these. `checked` is the only bucket
#: where a real type comparison happened, so it is the pass's honest coverage
#: numerator. Order of evaluation in `coverage_bucket` defines exclusivity.
COVERAGE_BUCKETS = (
    "checked",
    "not_a_parameter",
    "non_dependency",
    "unresolved",
    "no_param_type_code_step",
    "no_param_type_builtin",
    "operator_result_unknowable",
    "actual_untyped",
)

COVERAGE_MEANING = {
    "checked": "compared against a documented parameter type",
    "not_a_parameter": "decision operand or legacy `dependencies` entry; fills no named parameter",
    "non_dependency": "static/prompt/variable/function input; no upstream type exists",
    "unresolved": "the path does not resolve; already reported as a blocker",
    "no_param_type_code_step": "custom_step/function parameter; facts.json records its name, not its type",
    "no_param_type_builtin": "built-in step parameter with no type in step_types/<type>.md",
    "operator_result_unknowable": "multi-source via custom_function; the result type cannot be derived",
    "actual_untyped": "the source resolved to unknown/any; comparison would be meaningless",
}


def coverage_bucket(mapping: Mapping,
                    steps: Dict[str, Dict[str, Any]]) -> str:
    """Which coverage bucket this mapping falls in. Exactly one, first match.

    `check_types` consumes the same function, so the reported coverage and the
    set of mappings actually compared cannot drift apart.
    """
    if not mapping.is_param_mapping:
        return "not_a_parameter"
    if not mapping.is_dependency:
        return "non_dependency"
    if any(not edge.get("resolved") for edge in mapping.edges):
        return "unresolved"
    step = steps.get(mapping.step) or {}
    stype = step.get("type") or ""
    expected = EXPECTED_TYPES.get(stype, {}).get(mapping.param)
    if expected is None:
        return ("no_param_type_code_step" if stype in CODE_STEP_TYPES
                else "no_param_type_builtin")
    actual = effective_type(mapping)
    if not isinstance(actual, dict):
        return "operator_result_unknowable"
    if actual.get("kind") in ("unknown", "any"):
        return "actual_untyped"
    return "checked"


def coverage(mappings: Sequence[Mapping],
             steps: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """How much of the wiring this pass actually type-checked, and why not the
    rest. Reported in the JSON payload permanently: "we compared 36% of the
    wiring" is something a reader deserves to know."""
    counts = collections.Counter(coverage_bucket(m, steps) for m in mappings)
    total = len(mappings)
    checked = counts.get("checked", 0)
    unaccounted = total - sum(counts.get(b, 0) for b in COVERAGE_BUCKETS)
    return {
        "mappings": total,
        "checked": checked,
        "checked_pct": round(100.0 * checked / total, 1) if total else 0.0,
        "buckets": {b: counts.get(b, 0) for b in COVERAGE_BUCKETS},
        "meaning": COVERAGE_MEANING,
        # Must stay 0. A non-zero value means a mapping fell through every
        # bucket, which is a bug in coverage_bucket, not a real category.
        "unaccounted": unaccounted,
    }


def check_types(facts: Dict[str, Any], mappings: Sequence[Mapping],
                steps: Dict[str, Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """Compare each mapping's effective type against the documented parameter
    type. Only (step_type, param) pairs present in EXPECTED_TYPES are checked.
    """
    out: List[Dict[str, Any]] = []
    checked = 0
    for mapping in mappings:
        # One classifier for both the comparison and the reported coverage, so
        # they cannot disagree.
        if coverage_bucket(mapping, steps) != "checked":
            continue
        step = steps.get(mapping.step) or {}
        expected = EXPECTED_TYPES[step["type"]][mapping.param]
        actual = effective_type(mapping)
        checked += 1
        if types_compatible(expected, actual):
            # `object` accepts `cell` (reference/input_mappings.md
            # "Save-time type compatibility"), so a bare `data.<field>` wired
            # into an object parameter never reaches the mismatch branch. That
            # is correct: reading the whole cell to gate on `confidence_score`
            # is legitimate and common. Note it, never block it.
            note = _bare_cell_note(mapping, expected, actual, steps)
            if note is not None:
                out.append(note)
            continue

        edge = mapping.edges[0]
        path = edge.get("output_attribute")
        source = edge.get("from_step") or "(unwired)"
        code, title, fix = _classify_mismatch(expected, actual, path, mapping)
        if code == "CELL_NOT_UNWRAPPED":
            # reference/input_mappings.md "A bare `data.<field>` is not
            # rejected at save time — it fails at runtime": the validator view
            # of the schema keeps primitive leaves unwrapped, so this SAVES
            # CLEAN. Never imply the platform will catch it.
            base = ("`%s` on %s is the whole cell — `{%s, ...}` — not the `%s` "
                    "inside it. The save succeeds (save-time type checking sees "
                    "the underlying %s), and at runtime %s.%s receives the dict."
                    % (path, source, ", ".join(CELL_MEMBERS[:2]),
                       type_name(actual.get("value_type")),
                       type_name(actual.get("value_type")),
                       step.get("type"), mapping.param))
        else:
            base = ("`%s` on %s resolves to `%s`, but %s.%s expects `%s`."
                    % (path, source, type_name(actual), step.get("type"),
                       mapping.param, type_name(expected)))
        out.append(finding(
            code, "blocker", title, base,
            step=mapping.step, fix=fix,
            evidence={"from_step": source, "output_attribute": path,
                      "to_param": mapping.param,
                      "expected": type_name(expected),
                      "actual": type_name(actual),
                      "resolution_operator": mapping.operator},
            group_key="%s:%s" % (code, source)))
    return out, checked


def _bare_cell_note(mapping: Mapping, expected: Dict[str, Any],
                    actual: Dict[str, Any],
                    steps: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A bare `data.<field>` wired into a parameter that accepts the cell.

    reference/input_mappings.md: "Wiring one into a parameter that genuinely
    wants the cell (a `custom_step` reading `confidence_score` to gate on
    extraction confidence, for instance) is legitimate and common." So this is
    an observation, not a defect — it exists so the roll-up can tell a
    deliberate cell read from an accidental one.
    """
    if actual.get("kind") != "cell" or expected.get("kind") != "object":
        return None
    edge = mapping.edges[0]
    path = edge.get("output_attribute")
    step = steps.get(mapping.step) or {}
    return finding(
        "CELL_WIRED_WHOLE", "note",
        "Whole extraction cell wired to an object parameter",
        "`%s` on %s delivers the whole cell `{value, confidence_score, ...}` "
        "to %s.%s, which is declared `object` and accepts it. Legitimate when "
        "the consumer reads `confidence_score` or `citations`; a missing "
        "`.value` otherwise."
        % (path, edge.get("from_step"), step.get("type"), mapping.param),
        step=mapping.step,
        fix="No change needed if the consumer wants the cell. Otherwise wire "
            "`%s.value`." % path,
        evidence={"from_step": edge.get("from_step"), "output_attribute": path,
                  "to_param": mapping.param,
                  "value_type": type_name(actual.get("value_type"))},
        group_key="CELL_WIRED_WHOLE:%s" % edge.get("from_step"))


def _classify_mismatch(expected: Dict[str, Any], actual: Dict[str, Any],
                       path: Optional[str],
                       mapping: Mapping) -> Tuple[str, str, Optional[str]]:
    """Name the mismatch and, where the reference docs give a rule, the exact
    corrected path."""
    exp_kind = expected.get("kind")
    act_kind = actual.get("kind")

    # reference/input_mappings.md "Extraction outputs are cell-wrapped —
    # `.value` matters": the single most common wiring bug on the platform.
    if act_kind == "cell" and exp_kind != "cell":
        inner = type_name(actual.get("value_type"))
        return ("CELL_NOT_UNWRAPPED",
                "Missing `.value` on an extracted field",
                "Append `.value` — wire `%s.value` (the extracted %s). Save-time "
                "validation will not flag this, so it has to be caught here."
                % (path, inner))

    if exp_kind == "array" and act_kind != "array":
        last = _last_segment(path or "")
        if last.isdigit():
            return ("INDEX_MISMATCH",
                    "Row index used where the whole list is wanted",
                    "Drop the trailing `.%s` — wire `%s`." % (last, path.rsplit(".", 1)[0]))
        return ("TYPE_MISMATCH", "Parameter expects a list", None)

    if exp_kind != "array" and act_kind == "array":
        item = type_name(actual.get("items"))
        return ("INDEX_MISMATCH",
                "Whole list wired where one element is wanted",
                "Index an element — wire `%s.0` (one %s)." % (path, item))

    if exp_kind == "file" and act_kind == "object":
        # reference/input_mappings.md: "a custom code step returning a document
        # must declare {\"type\": \"file\"} (not a generic object)".
        return ("TYPE_MISMATCH", "Generic object wired to a file parameter",
                "Declare the producing step's output property as "
                "`{\"type\": \"file\"}` — a generic object is NOT-A file.")

    if exp_kind in _SCALARS and act_kind in ("object", "table"):
        return ("TYPE_MISMATCH", "Object wired to a scalar parameter",
                "Point output_attribute at the scalar leaf inside `%s`." % path)

    if mapping.multi and mapping.operator:
        return ("TYPE_MISMATCH",
                "Resolution operator produces the wrong type",
                "`%s` yields `%s`; the parameter needs `%s`."
                % (mapping.operator, type_name(effective_type(mapping)),
                   type_name(expected)))

    return ("TYPE_MISMATCH", "Incompatible type wired to parameter", None)


# --------------------------------------------------------------------------- #
# 3. Missing required inputs
# --------------------------------------------------------------------------- #

def check_required(facts: Dict[str, Any], mappings: Sequence[Mapping],
                   steps: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Cross-reference declared inputs against reference/required_inputs.md.

    CONTRACT.md A2 puts every declared input in `edges`, including the
    non-dependency ones, so a parameter that is present-but-unwired is
    distinguishable from one that is absent.
    """
    out: List[Dict[str, Any]] = []
    supplied: Dict[str, Dict[str, Mapping]] = collections.defaultdict(dict)
    for mapping in mappings:
        if not mapping.is_param_mapping:
            continue
        supplied[mapping.step][mapping.param] = mapping

    for step in facts.get("steps", []):
        name, stype = step["name"], step.get("type")
        here = supplied.get(name, {})

        def has(param: str) -> bool:
            mapping = here.get(param)
            if mapping is None:
                return False
            # A dependency mapping whose source list is empty produces no edge
            # at all, so anything present here is genuinely supplying a value.
            return not (mapping.is_dependency and not mapping.edges)

        for param in REQUIRED_ALL.get(stype or "", ()):
            if has(param):
                continue
            out.append(finding(
                "MISSING_REQUIRED_INPUT", "blocker",
                "Required input mapping is missing",
                "%s requires an `%s` input mapping and none supplies a value."
                % (stype, param),
                step=name,
                fix="Add an input_mappings entry for `%s`. See "
                    "reference/required_inputs.md." % param,
                evidence={"step_type": stype, "param": param,
                          "declared": sorted(here)},
                group_key="MISSING_REQUIRED_INPUT:%s" % name))

        for group in REQUIRED_ONE_OF.get(stype or "", ()):
            if any(has(param) for param in group):
                continue
            out.append(finding(
                "MISSING_REQUIRED_INPUT", "blocker",
                "Required input mapping is missing",
                "%s requires at least one of %s and none is supplied."
                % (stype, ", ".join("`%s`" % p for p in group)),
                step=name,
                fix="Wire one of %s. See reference/required_inputs.md."
                    % ", ".join("`%s`" % p for p in group),
                evidence={"step_type": stype, "one_of": list(group),
                          "declared": sorted(here)},
                group_key="MISSING_REQUIRED_INPUT:%s" % name))

        if stype == "extract_from_multiple_sources":
            out.extend(_check_efms(name, here, has))
    return out


def _check_efms(name: str, here: Dict[str, Mapping], has: Any) -> List[Dict[str, Any]]:
    """step_types/extract_from_multiple_sources.md "Required inputs" (1)-(4):
    at-least-one-source, then a matching system prompt per branch."""
    out: List[Dict[str, Any]] = []
    doc_source = has("documents")
    scalar_email = [p for p in EFMS_EMAIL_SCALARS if has(p)]
    grouped_email = has("emails")
    any_email = grouped_email or bool(scalar_email)

    if not doc_source and not any_email:
        out.append(finding(
            "MISSING_REQUIRED_INPUT", "blocker",
            "Required input mapping is missing",
            "extract_from_multiple_sources needs at least one source: "
            "`documents`, or an email input (`emails`, or the scalars %s)."
            % ", ".join("`%s`" % p for p in EFMS_EMAIL_SCALARS),
            step=name,
            fix="Wire `documents` (plus `document_system_prompt`) or an email "
                "input (plus `email_system_prompt`).",
            evidence={"declared": sorted(here)},
            group_key="MISSING_REQUIRED_INPUT:%s" % name))
        return out

    if doc_source and not has("document_system_prompt"):
        out.append(finding(
            "MISSING_REQUIRED_INPUT", "blocker",
            "Required input mapping is missing",
            "`documents` is mapped, so `document_system_prompt` is required "
            "at save time and is absent.",
            step=name,
            fix="Add a static `document_system_prompt` input mapping.",
            evidence={"declared": sorted(here), "param": "document_system_prompt"},
            group_key="MISSING_REQUIRED_INPUT:%s" % name))

    if any_email and not has("email_system_prompt"):
        out.append(finding(
            "MISSING_REQUIRED_INPUT", "blocker",
            "Required input mapping is missing",
            "An email input is mapped, so `email_system_prompt` is required "
            "at save time and is absent.",
            step=name,
            fix="Add a static `email_system_prompt` input mapping.",
            evidence={"declared": sorted(here), "param": "email_system_prompt"},
            group_key="MISSING_REQUIRED_INPUT:%s" % name))

    if scalar_email and not grouped_email and not has("eml_user_document_id"):
        out.append(finding(
            "MISSING_REQUIRED_INPUT", "blocker",
            "Required input mapping is missing",
            "Scalar email inputs (%s) are mapped without grouped `emails`, so "
            "`eml_user_document_id` is required so citations link to the source "
            "`.eml`/`.msg`." % ", ".join("`%s`" % p for p in scalar_email),
            step=name,
            fix="Wire `eml_user_document_id` from the same Prepare Documents step.",
            evidence={"declared": sorted(here), "param": "eml_user_document_id"},
            group_key="MISSING_REQUIRED_INPUT:%s" % name))
    return out


# --------------------------------------------------------------------------- #
# 4. Resolution operators
# --------------------------------------------------------------------------- #

def check_operators(mappings: Sequence[Mapping]) -> List[Dict[str, Any]]:
    """reference/input_mappings.md "Resolution operators" + "Validation rules":
    multi-source needs an operator, names are lowercase, and the operands have
    to be a shape the operator can actually combine."""
    out: List[Dict[str, Any]] = []
    for mapping in mappings:
        if not mapping.is_dependency or not mapping.is_param_mapping:
            continue
        operator = mapping.operator
        where = "%s.%s" % (mapping.step, mapping.param)

        if mapping.multi and operator is None:
            out.append(finding(
                "OPERATOR_MISSING", "blocker",
                "Multi-source mapping has no resolution operator",
                "%s wires %d sources with `resolution_operator: null`. The "
                "platform rejects the save: multi-source mappings require an "
                "operator." % (where, mapping.source_count),
                step=mapping.step,
                fix="Set a resolution_operator (usually `concat_lists`) and its "
                    "resolution_config.",
                evidence={"param": mapping.param, "sources": mapping.source_count},
                group_key="OPERATOR_MISSING:%s" % mapping.step))
            continue

        if operator is None:
            continue

        if not isinstance(operator, str):
            out.append(finding(
                "OPERATOR_UNKNOWN", "blocker",
                "Resolution operator is not a name",
                "%s has resolution_operator %r." % (where, operator),
                step=mapping.step,
                fix="Use one of: %s." % ", ".join(OPERATORS),
                evidence={"param": mapping.param, "resolution_operator": operator},
                group_key="OPERATOR_UNKNOWN:%s" % mapping.step))
            continue

        if operator != operator.lower():
            lowered = operator.lower()
            known = " It is otherwise a valid operator." if lowered in OPERATORS else ""
            out.append(finding(
                "OPERATOR_NOT_LOWERCASE", "blocker",
                "Resolution operator name is not lowercase",
                "%s uses `%s`. Operator names are LOWERCASE only.%s"
                % (where, operator, known),
                step=mapping.step,
                fix="Rename it to `%s`." % lowered,
                evidence={"param": mapping.param, "resolution_operator": operator},
                group_key="OPERATOR_NOT_LOWERCASE:%s" % mapping.step))
            continue

        if operator not in OPERATORS:
            out.append(finding(
                "OPERATOR_UNKNOWN", "blocker",
                "Unknown resolution operator",
                "%s uses `%s`, which is not one of the eight operators."
                % (where, operator),
                step=mapping.step,
                fix="Use one of: %s." % ", ".join(OPERATORS),
                evidence={"param": mapping.param, "resolution_operator": operator},
                group_key="OPERATOR_UNKNOWN:%s" % mapping.step))
            continue

        if not mapping.multi:
            out.append(finding(
                "OPERATOR_ON_SINGLE_SOURCE", "note",
                "Resolution operator on a single-source mapping",
                "%s sets `%s` with one source. Operators only apply to "
                "multi-source mappings; this one is inert."
                % (where, operator),
                step=mapping.step,
                fix="Set resolution_operator and resolution_config to null.",
                evidence={"param": mapping.param, "resolution_operator": operator},
                # Workflow-wide group: the fix is identical at every site, so a
                # dozen inert operators are worth exactly one printed line.
                group_key="OPERATOR_ON_SINGLE_SOURCE"))
            continue

        out.extend(_check_operands(mapping, operator, where))
    return out


def _check_operands(mapping: Mapping, operator: str,
                    where: str) -> List[Dict[str, Any]]:
    """Operand-type coherence for the operators whose contract constrains it."""
    out: List[Dict[str, Any]] = []
    operands: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for edge in mapping.edges:
        schema = edge.get("resolved_schema")
        if isinstance(schema, dict) and edge.get("resolved"):
            operands.append((edge, schema))
    if not operands:
        return out

    if operator in NUMERIC_OPERATORS:
        bad = [(e, s) for e, s in operands if s.get("kind") not in _NUMERIC_OK]
        if bad:
            shown = ", ".join("`%s` -> %s" % (e.get("output_attribute"), type_name(s))
                              for e, s in bad[:4])
            cellish = [s for _, s in bad if s.get("kind") == "cell"]
            fix = ("Append `.value` to the extracted operand(s) — a cell is the "
                   "whole {value, confidence_score, ...} envelope, not a number."
                   if cellish else
                   "Point every operand at a numeric leaf, or use a different operator.")
            out.append(finding(
                "OPERATOR_OPERAND_TYPE", "blocker",
                "Numeric operator over non-numeric operands",
                "%s uses `%s` over %d non-numeric operand(s): %s. The runtime "
                "raises on these." % (where, operator, len(bad), shown),
                step=mapping.step, fix=fix,
                evidence={"param": mapping.param, "resolution_operator": operator,
                          "operands": [type_name(s) for _, s in operands]},
                group_key="OPERATOR_OPERAND_TYPE:%s" % mapping.step))

    elif operator == "merge_dicts":
        bad = [(e, s) for e, s in operands if s.get("kind") not in _DICT_OK]
        if bad:
            shown = ", ".join("`%s` -> %s" % (e.get("output_attribute"), type_name(s))
                              for e, s in bad[:4])
            out.append(finding(
                "OPERATOR_OPERAND_TYPE", "blocker",
                "merge_dicts over operands that are not dicts",
                "%s merges %d non-dict operand(s): %s." % (where, len(bad), shown),
                step=mapping.step,
                fix="Point every operand at an object-shaped output, or use "
                    "`concat_lists` if you wanted a list.",
                evidence={"param": mapping.param, "resolution_operator": operator,
                          "operands": [type_name(s) for _, s in operands]},
                group_key="OPERATOR_OPERAND_TYPE:%s" % mapping.step))

    elif operator == "concat_lists":
        # "Non-list values are wrapped", so a non-list operand is LEGAL — the
        # canonical combine_kv_tables `sources` mapping concatenates whole
        # object envelopes. Only a scalar or a cell is worth a note.
        odd = [(e, s) for e, s in operands
               if s.get("kind") in _SCALARS or s.get("kind") == "cell"]
        if odd:
            shown = ", ".join("`%s` -> %s" % (e.get("output_attribute"), type_name(s))
                              for e, s in odd[:4])
            out.append(finding(
                "OPERATOR_SCALAR_CONCAT", "note",
                "concat_lists over scalar operands",
                "%s concatenates %d scalar/cell operand(s): %s. They are wrapped "
                "into single-element lists, which is legal but rarely intended."
                % (where, len(odd), shown),
                step=mapping.step,
                fix="Point the operand(s) at the list you meant, or use "
                    "`string_join` for text.",
                evidence={"param": mapping.param, "resolution_operator": operator,
                          "operands": [type_name(s) for _, s in operands]},
                group_key="OPERATOR_SCALAR_CONCAT:%s" % mapping.step))

    elif operator == "string_join":
        odd = [(e, s) for e, s in operands if s.get("kind") == "cell"]
        if odd:
            shown = ", ".join("`%s`" % e.get("output_attribute") for e, _ in odd[:4])
            out.append(finding(
                "OPERATOR_OPERAND_TYPE", "warning",
                "string_join over un-unwrapped cells",
                "%s joins the string form of %d cell operand(s): %s. That "
                "renders the whole {value, confidence_score, ...} dict into the "
                "output text." % (where, len(odd), shown),
                step=mapping.step,
                fix="Append `.value` to each extracted operand.",
                evidence={"param": mapping.param, "resolution_operator": operator},
                group_key="OPERATOR_OPERAND_TYPE:%s" % mapping.step))
    return out


# --------------------------------------------------------------------------- #
# 5. Code step contracts
# --------------------------------------------------------------------------- #

def check_code_steps(facts: Dict[str, Any], mappings: Sequence[Mapping],
                     steps: Dict[str, Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """Compare each code step's wired inputs and returns against its
    `run()` signature and declared output properties.

    step_types/custom_step.md: inputs arrive as keyword arguments named after
    `input_parameter_name`, so a wired name the signature cannot accept is a
    hard runtime TypeError unless the function takes `**kwargs`.
    """
    out: List[Dict[str, Any]] = []
    code_steps = facts.get("code_steps") or {}
    wired: Dict[str, List[str]] = collections.defaultdict(list)
    for mapping in mappings:
        if mapping.is_param_mapping:
            wired[mapping.step].append(mapping.param)

    for name in sorted(code_steps):
        info = code_steps[name] or {}
        if not info.get("syntax_ok", True):
            out.append(finding(
                "CODE_UNPARSEABLE", "blocker",
                "Step code does not parse",
                "%s: %s" % (name, truncate(info.get("parse_error"), 140)),
                step=name, fix="Fix the syntax error in the step's code.",
                evidence={"parse_error": info.get("parse_error")}))
            continue
        if not info.get("entry_found", True):
            # Without an entry point there is no signature to compare against;
            # the structural pass owns the missing-entry-point rule.
            out.append(finding(
                "CODE_ENTRY_MISSING", "warning",
                "No `run()` entry point found in step code",
                "%s: the signature could not be read, so its wired inputs and "
                "returns were not checked." % name,
                step=name,
                fix="Define `def run(...)` (or `async def run(...)`) as the entry point.",
                evidence={"params": info.get("params")}))
            continue

        params = set(info.get("params") or ())
        if not info.get("has_kwargs"):
            unaccepted = sorted(set(wired.get(name, ())) - params)
            for param in unaccepted:
                out.append(finding(
                    "CODE_SIGNATURE_MISMATCH", "blocker",
                    "Wired input is not a parameter of run()",
                    "%s wires `%s`, but run() takes (%s) and has no `**kwargs`. "
                    "The call raises TypeError at runtime."
                    % (name, param, ", ".join(sorted(params)) or "no parameters"),
                    step=name,
                    fix="Add `%s` to the run() signature (or add `**kwargs`), or "
                        "remove the input mapping." % param,
                    evidence={"param": param, "signature": sorted(params),
                              "has_kwargs": False},
                    group_key="CODE_SIGNATURE_MISMATCH:%s" % name))

        if info.get("returns_dynamic"):
            continue
        returned = set(info.get("returned_keys") or ())
        declared = set(info.get("declared_output_properties") or ())
        if not declared and not returned:
            continue
        missing = sorted(declared - returned)
        extra = sorted(returned - declared)
        if missing:
            out.append(finding(
                "CODE_RETURN_MISSING", "warning",
                "Declared output property is never returned",
                "%s declares %s in config.output_schema but no return statement "
                "produces %s. Anything wired to %s resolves to nothing."
                % (name, ", ".join("`%s`" % m for m in missing[:6]),
                   "them" if len(missing) > 1 else "it",
                   "those keys" if len(missing) > 1 else "that key"),
                step=name,
                fix="Return the missing key(s), or drop them from "
                    "config.output_schema.properties.",
                evidence={"declared_not_returned": missing,
                          "returned_keys": sorted(returned)},
                group_key="CODE_RETURN_MISSING:%s" % name))
        if extra:
            out.append(finding(
                "CODE_RETURN_EXTRA", "note",
                "Returned key is not declared as an output property",
                "%s returns %s, which config.output_schema does not declare, so "
                "no downstream step can wire %s."
                % (name, ", ".join("`%s`" % e for e in extra[:6]),
                   "them" if len(extra) > 1 else "it"),
                step=name,
                fix="Declare the key(s) in config.output_schema.properties if "
                    "anything downstream needs them.",
                evidence={"returned_not_declared": extra,
                          "declared_output_properties": sorted(declared)},
                group_key="CODE_RETURN_EXTRA:%s" % name))
    return out, len(code_steps)


# --------------------------------------------------------------------------- #
# 6. The two inventories
# --------------------------------------------------------------------------- #

def _inventory_excuse(step: Dict[str, Any]) -> Optional[str]:
    """Why an unread field on this step is not a defect, or None.

    An `output_type` can be the consumer: reference/output_types.md
    "OutputType enum" lists `set_title` and `update_workflow_log` as side
    effects the platform performs on the returned data, so no input mapping
    will ever reference them and that is correct by design.
    """
    if step.get("display_step"):
        return "display_step is true; a person reads it on the canvas"
    if step.get("is_terminal") or step.get("type") in USER_FACING_TYPES:
        return "terminal step; the data reaches a person"
    if step.get("type") in CONTROL_FLOW_TYPES:
        return "control flow; the engine reads its output, not a wired consumer"
    if step.get("type") in PLATFORM_SHAPED_TYPES:
        return "output shape is auto-filled by the platform (reference/output_types.md)"
    if step.get("output_type") in PLATFORM_CONSUMED_OUTPUT_TYPES:
        return ("output_type=%s; the platform consumes the returned data "
                "(reference/output_types.md)" % step.get("output_type"))
    return None


def _consumption_index(facts: Dict[str, Any]) -> Dict[str, Set[Optional[str]]]:
    """Every path any consumer reads out of each step, keyed by step name.

    `None` means the whole output dict was taken (`output_attribute: null`),
    which proves nothing about an individual field beneath it — see
    `_field_verdict`.
    """
    index: Dict[str, Set[Optional[str]]] = collections.defaultdict(set)
    for edge in facts.get("edges", []):
        if edge.get("input_type") != "dependency":
            continue
        source = edge.get("from_step")
        if source:
            index[source].add(edge.get("output_attribute"))

    # An HTML producer interpolates upstream values into its own markup.
    # CONTRACT.md B11: each entry names the step that PRODUCES the value, the
    # `key` read out of it, and the `source_attribute` the mapping used — so
    # the consumed path belongs to that producer, not to the HTML step. Older
    # facts files carried a bare string; both shapes are accepted.
    for producer in facts.get("html_producers") or []:
        for entry in producer.get("interpolated_paths") or []:
            if isinstance(entry, dict):
                owner = entry.get("step") or producer.get("step")
                key = entry.get("key")
                prefix = entry.get("source_attribute")
                if key is None:
                    index[owner].add(prefix)
                else:
                    index[owner].add("%s.%s" % (prefix, key) if prefix else key)
            else:
                index[producer.get("step")].add(entry)

    for decision in facts.get("decisions") or []:
        for wiring in (decision.get("variables") or {}).values():
            if wiring.get("from_step"):
                index[wiring["from_step"]].add(wiring.get("output_attribute"))

    # CONTRACT.md B10: "Email body static text IS available ... a field
    # mentioned only in an email body is not called unused." wf_facts records
    # the scan; a hit names the owning step and the field.
    for entry in facts.get("text_field_references") or []:
        for reference in entry.get("references") or []:
            owner, name = reference.get("step"), reference.get("field")
            if owner and name:
                index[owner].add(name)
    return index


def _field_verdict(consumed: Iterable[Optional[str]], path: str) -> Optional[str]:
    """"named", "wholesale", or None for one produced field.

    - "named": a consumer names this field, or something beneath it, so it is
      definitely in use.
    - "wholesale": the only consumers took an ancestor (`data`, or the whole
      output dict), so nothing here can prove this particular field is read.
      CONTRACT.md B10 forbids calling that "unused".
    - None: nothing reads it or anything containing it.
    """
    target = _normalize_index(path) or ""
    verdict: Optional[str] = None
    for raw in consumed:
        if raw is None or raw == "":
            verdict = verdict or "wholesale"
            continue
        entry = _normalize_index(raw) or ""
        if entry == target or entry.startswith(target + "."):
            return "named"
        if target.startswith(entry + "."):
            verdict = "wholesale"
    return verdict


def _normalize_index(path: Optional[str]) -> Optional[str]:
    """Collapse every array index to `0`.

    facts.json spells a row field as `data.rows.0.vin`, but a consumer may
    legally read `data.rows.3.vin`. Both name the same declared field, so the
    coverage test compares them index-insensitively.
    """
    if path is None:
        return None
    return ".".join("0" if seg.isdigit() else seg for seg in path.split("."))


def check_inventories(facts: Dict[str, Any],
                      steps: Dict[str, Dict[str, Any]]) -> Tuple[
                          List[Dict[str, Any]], List[Dict[str, Any]],
                          List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Inventory 1: produced and never consumed. Inventory 2: consumed but not
    backed by a declared field. Plus the undecidable set B10 requires.

    `fields[]` is the sanctioned input here — CONTRACT.md B3: "Use it for
    reporting, unused-field analysis and the labelling-style inventory." No
    overlap score is computed in this pass, so `comparable_fields` (B2) does
    not apply.

    Returns (findings, unconsumed, undecidable, unproduced).
    """
    consumed = _consumption_index(facts)

    # CONTRACT.md B10: a field routed into a `fill_docx` context can only be
    # judged by reading placeholders inside a platform .docx, which needs a
    # download these passes are forbidden. wf_facts computes the affected
    # upstream closure; a field on one of those steps is never "unused".
    opaque: Dict[str, Dict[str, Any]] = {}
    for entry in facts.get("opaque_consumers") or []:
        for name in entry.get("upstream_steps") or []:
            opaque.setdefault(name, entry)

    unconsumed: List[Dict[str, Any]] = []
    undecidable: List[Dict[str, Any]] = []
    for field in facts.get("fields", []):
        step_name = field.get("step")
        verdict = _field_verdict(consumed.get(step_name, ()), field.get("path") or "")
        if verdict == "named":
            continue
        step = steps.get(step_name) or {}
        row = {
            "step": step_name,
            "step_type": step.get("type"),
            "output_type": step.get("output_type"),
            "path": field.get("path"),
            "value_path": field.get("value_path"),
            "type": field.get("type"),
            "hidden": bool(step.get("is_hidden")),
            "terminal": bool(step.get("is_terminal")),
        }
        # Why this field is not actionable, or None when it genuinely is.
        excuse = _inventory_excuse(step)
        if verdict is not None and excuse is not None:
            # Read wholesale AND on a step a person sees (or one the platform
            # consumes): the field's fate is determined, just not by a named
            # mapping. Neither unused nor undecidable.
            continue
        if verdict is not None:
            if step_name in opaque:
                row["reason"] = "docx_template"
                row["detail"] = opaque[step_name].get("detail")
                row["docx_step"] = opaque[step_name].get("step")
            else:
                row["reason"] = "read_wholesale"
                row["detail"] = ("the only consumer takes an ancestor path or "
                                 "the whole output dict, and nobody sees this "
                                 "step, so the field can be shown neither used "
                                 "nor unused")
            undecidable.append(row)
            continue
        if excuse is None and step_name in opaque:
            # Nothing reads it by name, but its only possible consumer is a
            # .docx template we are forbidden from downloading.
            row["reason"] = "docx_template"
            row["detail"] = opaque[step_name].get("detail")
            row["docx_step"] = opaque[step_name].get("step")
            undecidable.append(row)
            continue
        row["excuse"] = excuse
        unconsumed.append(row)

    unproduced: List[Dict[str, Any]] = []
    seen: Set[Tuple[Optional[str], Optional[str]]] = set()
    for edge in facts.get("edges", []):
        if edge.get("input_type") != "dependency":
            continue
        key = (edge.get("from_step"), edge.get("output_attribute"))
        if key in seen:
            continue
        seen.add(key)
        if not edge.get("resolved"):
            reason, detail = "unresolved", edge.get("resolve_error")
        elif edge.get("resolve_note"):
            reason, detail = "unverified", edge.get("resolve_note")
        else:
            continue
        unproduced.append({
            "from_step": edge.get("from_step"),
            "output_attribute": edge.get("output_attribute"),
            "to_step": edge.get("to_step"),
            "to_param": edge.get("to_param"),
            "reason": reason,
            "detail": detail,
            "available": edge.get("resolve_available") or [],
        })

    out: List[Dict[str, Any]] = []

    # CONTRACT.md B10: "The report must state the undecidable count alongside
    # the unused count ... An inventory that silently presents 207 unknowns as
    # a verdict is worse than one that admits the boundary." One note, always,
    # whenever the boundary exists.
    if undecidable:
        by_reason = collections.Counter(row["reason"] for row in undecidable)
        total_fields = len(facts.get("fields") or ())
        out.append(finding(
            "FIELD_EVIDENCE_UNAVAILABLE", "note",
            "Field usage is undecidable for part of the inventory",
            "%d of %d produced fields can be shown neither used nor unused (%s). "
            "The unused count below is drawn only from the %d fields where the "
            "evidence is complete."
            % (len(undecidable), total_fields,
               ", ".join("%s %d" % (reason, count)
                         for reason, count in sorted(by_reason.items())),
               total_fields - len(undecidable)),
            step=None,
            fix="Wire the named path rather than an ancestor where you want the "
                "usage to be provable. A docx template's placeholders are "
                "permanently unreadable offline — that count cannot be reduced.",
            evidence={"undecidable": len(undecidable),
                      "fields": total_fields,
                      "by_reason": dict(by_reason)}))

    # A field is only actionably dead when nothing at all gives it a
    # destination: no input mapping, no decision, no HTML interpolation, no
    # `output_type` side effect, and no human looking at the step.
    dead = [row for row in unconsumed if row["excuse"] is None]
    by_step = collections.Counter(row["step"] for row in dead)
    for step_name, count in by_step.most_common(MAX_INVENTORY_FINDINGS):
        paths = [row["path"] for row in dead if row["step"] == step_name]
        step = steps.get(step_name) or {}
        out.append(finding(
            "UNCONSUMED_FIELD", "note",
            "Hidden step produces fields nothing consumes",
            "%s (%s, output_type=%s, display_step false) produces %d field(s) "
            "that no input mapping, decision, HTML producer or output_type side "
            "effect reads: %s."
            % (step_name, step.get("type"), step.get("output_type"), count,
               ", ".join("`%s`" % p for p in paths[:6])
               + (", …" if len(paths) > 6 else "")),
            step=step_name,
            fix="Drop the unused output properties, or set display_step: true "
                "if they exist to be read on the canvas. (A document_viewer or "
                "text_block `output_config.content_key` path is not recorded in "
                "facts.json — confirm before removing.)",
            evidence={"count": count, "paths": paths[:25],
                      "output_type": step.get("output_type")},
            group_key="UNCONSUMED_FIELD:%s" % step_name))

    unverified = [row for row in unproduced if row["reason"] == "unverified"]
    for row in unverified[:MAX_INVENTORY_FINDINGS]:
        out.append(finding(
            "UNVERIFIED_PATH", "note",
            "Consumed path is not backed by a declared field",
            "`%s` on %s: %s. It may exist at runtime; nothing here can prove it."
            % (row["output_attribute"], row["from_step"], row["detail"]),
            step=row["to_step"],
            fix="Declare the key on the producing step's output schema so the "
                "path can be checked.",
            evidence={"from_step": row["from_step"],
                      "output_attribute": row["output_attribute"],
                      "reason": row["reason"]},
            group_key="UNVERIFIED_PATH:%s" % row["from_step"]))

    return out, unconsumed, undecidable, unproduced


# --------------------------------------------------------------------------- #
# Header
# --------------------------------------------------------------------------- #

def header_lines(facts: Dict[str, Any], mappings: Sequence[Mapping],
                 summary: Dict[str, Any], cover: Dict[str, Any]) -> List[str]:
    workflow = facts.get("workflow") or {}
    counters = facts.get("counters") or {}
    lines = [
        "%s — %s steps, %d input mappings (%d dependency sources), %d fields" % (
            truncate(workflow.get("name"), 60), counters.get("steps"),
            len(mappings), summary["dependency_sources"], summary["fields"]),
        "  paths      %d resolved, %d unresolved (%d a `.value` on a container), "
        "%d unverified" % (
            summary["resolved"], summary["unresolved"],
            summary["container_value_suffix"], summary["unverified"]),
        "  types      %d of %d mappings type-checked (%.0f%%), %d mismatched" % (
            cover["checked"], cover["mappings"], cover["checked_pct"],
            summary["type_mismatches"]),
        "  uncovered  " + truncate(", ".join(
            "%s %d" % (name, count)
            for name, count in sorted(cover["buckets"].items(),
                                      key=lambda kv: -kv[1])
            if count and name != "checked"), 150),
        "  operators  %d multi-source mappings, %d flagged" % (
            summary["multi_source_mappings"], summary["operator_findings"]),
        "  code       %d code steps, %d signature mismatches, %d return mismatches" % (
            summary["code_steps"], summary["code_signature"], summary["code_returns"]),
        "  unused     %d fields produced and never consumed, %d actionable "
        "(the rest are displayed, terminal or platform-consumed)" % (
            summary["unconsumed_total"], summary["unconsumed_actionable"]),
        "  undecided  %d of %d fields can be shown neither used nor unused%s" % (
            summary["undecidable_total"], summary["fields"],
            (" (%s)" % ", ".join(
                "%s %d" % (reason, count) for reason, count
                in sorted(summary["undecidable_by_reason"].items())))
            if summary["undecidable_by_reason"] else ""),
    ]
    return lines


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def run_pass(facts_path: str, out_dir: str) -> int:
    facts = load_facts(facts_path)
    out = PassOutput(PASS_NAME, out_dir)

    steps = {s["name"]: s for s in facts.get("steps", [])}
    mappings = index_mappings(facts)

    containers, claimed = check_containers(mappings, steps)
    unresolved = check_unresolved(facts, claimed)
    type_findings, types_checked = check_types(facts, mappings, steps)
    required = check_required(facts, mappings, steps)
    operators = check_operators(mappings)
    duplicates = check_duplicates(mappings)
    code, code_step_count = check_code_steps(facts, mappings, steps)
    inventory_findings, unconsumed, undecidable, unproduced = check_inventories(
        facts, steps)
    cover = coverage(mappings, steps)

    out.add_all(containers)
    out.add_all(unresolved)
    out.add_all(type_findings)
    out.add_all(required)
    out.add_all(operators)
    out.add_all(duplicates)
    out.add_all(code)
    out.add_all(inventory_findings)

    dep_edges = [e for e in facts.get("edges", []) if e.get("input_type") == "dependency"]
    dep_mappings = [m for m in mappings if m.is_dependency]
    summary = {
        "mappings": len(mappings),
        "dependency_mappings": len(dep_mappings),
        "dependency_sources": len(dep_edges),
        "fields": len(facts.get("fields") or ()),
        "resolved": sum(1 for e in dep_edges if e.get("resolved")),
        "unresolved": len(unresolved) + len(containers),
        "container_value_suffix": len(containers),
        "unverified": sum(1 for r in unproduced if r["reason"] == "unverified"),
        "types_checked": types_checked,
        "type_mismatches": len(type_findings),
        "missing_required": len(required),
        "multi_source_mappings": sum(1 for m in dep_mappings if m.multi),
        "operator_findings": len(operators),
        "duplicate_mappings": len(duplicates),
        "code_steps": code_step_count,
        "code_signature": sum(1 for f in code
                              if f["code"] == "CODE_SIGNATURE_MISMATCH"),
        "code_returns": sum(1 for f in code
                            if f["code"] in ("CODE_RETURN_MISSING",
                                             "CODE_RETURN_EXTRA")),
        "unconsumed_total": len(unconsumed),
        "unconsumed_hidden": sum(1 for r in unconsumed if r["hidden"]),
        "unconsumed_actionable": sum(1 for r in unconsumed if r["excuse"] is None),
        # CONTRACT.md B10: the undecidable count travels beside the unused one.
        "undecidable_total": len(undecidable),
        "undecidable_by_reason": dict(collections.Counter(
            row["reason"] for row in undecidable)),
        "unproduced_total": len(unproduced),
    }
    out.set("summary", summary)
    out.set("coverage", cover)
    out.set("unconsumed_fields", unconsumed)
    out.set("undecidable_fields", undecidable)
    out.set("unproduced_paths", unproduced)
    out.write()
    return out.print_summary(header_lines(facts, mappings, summary, cover))


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

CORPUS_DIR = "/Users/andrewjeffers/Documents/Work/contractors/workflows"
CORPUS = [
    ("cna_dua_audit_ads", os.path.join(CORPUS_DIR, "cna_dua_audit_ads.json")),
    ("cna_dua_audit_lpl", os.path.join(CORPUS_DIR, "cna_dua_audit_lpl.json")),
    ("submission_intake", os.path.join(CORPUS_DIR, "submission_intake.json")),
    ("quantum_cny", os.path.join(CORPUS_DIR, "quantum_cny.json")),
    ("submission_intake_es_umbrella", os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "reference", "examples", "submission_intake_es_umbrella.json")),
]

HERE = os.path.dirname(os.path.abspath(__file__))


def _build_facts(workflow_path: str, out_dir: str) -> Tuple[Dict[str, Any], float]:
    """Shell out to wf_facts.py — CONTRACT.md hard rule 2: nothing but
    wf_facts.py reads workflow.json."""
    started = time.time()
    proc = subprocess.run(
        [sys.executable, os.path.join(HERE, "wf_facts.py"), workflow_path,
         "--out", out_dir],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    elapsed = time.time() - started
    if proc.returncode not in (0, 1):
        raise RuntimeError("wf_facts.py failed on %s:\n%s"
                           % (workflow_path, proc.stdout.decode("utf-8", "replace")))
    with open(os.path.join(out_dir, "facts.json")) as handle:
        return json.load(handle), elapsed


def _collect(facts: Dict[str, Any]) -> Dict[str, Any]:
    """Run every check against an in-memory facts dict, no file output."""
    steps = {s["name"]: s for s in facts.get("steps", [])}
    mappings = index_mappings(facts)
    found: List[Dict[str, Any]] = []
    containers, claimed = check_containers(mappings, steps)
    found.extend(containers)
    found.extend(check_unresolved(facts, claimed))
    type_findings, types_checked = check_types(facts, mappings, steps)
    found.extend(type_findings)
    found.extend(check_required(facts, mappings, steps))
    found.extend(check_operators(mappings))
    found.extend(check_duplicates(mappings))
    found.extend(check_code_steps(facts, mappings, steps)[0])
    inventory, unconsumed, undecidable, unproduced = check_inventories(facts, steps)
    found.extend(inventory)
    return {"findings": found, "unconsumed": unconsumed,
            "undecidable": undecidable, "unproduced": unproduced,
            "coverage": coverage(mappings, steps), "types_checked": types_checked,
            "codes": collections.Counter(f["code"] for f in found),
            "blockers": [f for f in found if f["severity"] == "blocker"]}


def _cross_check_redundancy(work: str,
                            baselines: Dict[str, Dict[str, Any]]) -> List[str]:
    """Assert this pass's actionable unused-field set equals `redundancy.py`'s.

    The two passes compute the same thing from the same facts by different
    routes — this one from `fields[]` minus every consumer, that one from its
    own field-usage model. A divergence is a reliable bug signal in one of
    them, so it is asserted here rather than eyeballed. Skipped, loudly, when
    redundancy.py is not on disk yet.
    """
    script = os.path.join(HERE, "redundancy.py")
    print("")
    if not os.path.exists(script):
        print("cross-check vs redundancy.py: SKIPPED (not on disk)")
        return []
    failures: List[str] = []
    rows: List[List[Any]] = []
    for label, _path in CORPUS:
        if label not in baselines:
            continue
        facts_path = os.path.join(work, label, "facts.json")
        out_dir = os.path.join(work, label, "xcheck")
        proc = subprocess.run(
            [sys.executable, script, "--facts", facts_path, "--out", out_dir],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        payload_path = os.path.join(out_dir, "simplify.json")
        if proc.returncode not in (0, 1) or not os.path.exists(payload_path):
            rows.append([label, "-", "-", "redundancy.py did not produce output"])
            failures.append("cross-check: redundancy.py failed on %s" % label)
            continue
        with open(payload_path) as handle:
            payload = json.load(handle)
        unused = ((payload.get("field_usage") or {}).get("unused") or [])
        theirs = {(row.get("step"), row.get("path")) for row in unused}
        mine = {(row["step"], row["path"])
                for row in baselines[label]["unconsumed"]
                if row["excuse"] is None}
        rows.append([label, len(theirs), len(mine),
                     "identical" if theirs == mine else "DIVERGED"])
        if theirs != mine:
            only_theirs = sorted(theirs - mine)[:4]
            only_mine = sorted(mine - theirs)[:4]
            failures.append(
                "cross-check on %s: unused-field sets diverge — only "
                "redundancy: %s; only flow: %s" % (label, only_theirs, only_mine))
    print("cross-check vs redundancy.py — actionable unused fields")
    for line in table(rows, ["workflow", "redundancy", "flow", ""],
                      max_rows=len(rows)):
        print(line)
    return failures


# -- mutations -------------------------------------------------------------- #
# Each entry is (label, mutator, expected_code). A mutator takes
# (workflow_dict, baseline_facts), edits the workflow copy in place, and
# returns a short description of the site it hit, or None when the workflow
# offers no site for that mutation. `expected_code` None marks a control
# mutation: a legal edit that must introduce no new finding at all. A code
# prefixed with `!` is a narrow control: that one code must stay silent, while
# other detectors are allowed to fire for their own correct reasons.

def _steps_of(workflow: Dict[str, Any], step_type: str) -> List[Dict[str, Any]]:
    return [s for s in workflow.get("steps", []) if s.get("type") == step_type]


def _mappings_of(step: Dict[str, Any]) -> List[Dict[str, Any]]:
    return step.get("input_mappings") or []


def _value_of(mapping: Dict[str, Any]) -> Dict[str, Any]:
    """A dependency mapping's `value` dict. Non-dependency mappings carry a
    bare string or literal there, so guard the type."""
    value = mapping.get("value")
    return value if isinstance(value, dict) else {}


def _sources_of(mapping: Dict[str, Any]) -> List[Dict[str, Any]]:
    return _value_of(mapping).get("dependency_step_outputs") or []


def _find_source(workflow: Dict[str, Any], predicate: Any) -> Optional[Tuple[
        Dict[str, Any], Dict[str, Any], Dict[str, Any]]]:
    for step in workflow.get("steps", []):
        for mapping in _mappings_of(step):
            if mapping.get("input_type") != "dependency":
                continue
            for source in _sources_of(mapping):
                if predicate(step, mapping, source):
                    return step, mapping, source
    return None


def mut_break_deep_path(workflow: Dict[str, Any],
                        facts: Dict[str, Any]) -> Optional[str]:
    """A classify class-name typo: the deep segment the platform never checks."""
    hit = _find_source(workflow, lambda st, mp, src: len(
        (src.get("output_attribute") or "").split(".")) > 1
        and (src.get("output_attribute") or "").startswith("documents."))
    if not hit:
        return None
    step, mapping, source = hit
    before = source["output_attribute"]
    source["output_attribute"] = before + "s"
    return "%s.%s: %s -> %s" % (step["name"], mapping["input_parameter_name"],
                                before, source["output_attribute"])


def _scalar_extraction_field(workflow: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """(step name, field name) of some scalar leaf in an extraction schema."""
    for step in workflow.get("steps", []):
        schema = (step.get("config") or {}).get("extraction_schema") or {}
        for name, prop in (schema.get("properties") or {}).items():
            if isinstance(prop, dict) and prop.get("type") in (
                    "string", "integer", "number"):
                return step["name"], name
    return None


def _wire_email_subject(workflow: Dict[str, Any], suffix: str) -> Optional[str]:
    """Wire an extracted scalar into `email.subject` — a parameter whose type
    IS documented, unlike a custom_step's."""
    email = _steps_of(workflow, "email")
    producer = _scalar_extraction_field(workflow)
    if not email or not producer:
        return None
    step_name, field = producer
    path = "data.%s%s" % (field, suffix)
    email[0].setdefault("input_mappings", []).append({
        "input_parameter_name": "subject",
        "input_type": "dependency",
        "value": {"dependency_step_outputs": [
            {"step_name": step_name, "output_attribute": path}],
            "resolution_operator": None, "resolution_config": None},
    })
    return "%s.subject <- %s.%s" % (email[0]["name"], step_name, path)


def mut_strip_value(workflow: Dict[str, Any],
                    facts: Dict[str, Any]) -> Optional[str]:
    """Drop a `.value`, leaving a cell where a scalar is wanted."""
    return _wire_email_subject(workflow, "")


def mut_strip_value_control(workflow: Dict[str, Any],
                            facts: Dict[str, Any]) -> Optional[str]:
    """The same wiring done correctly — the control that must stay clean."""
    return _wire_email_subject(workflow, ".value")


def mut_index_a_list(workflow: Dict[str, Any],
                     facts: Dict[str, Any]) -> Optional[str]:
    """Append `.0` to a `documents` mapping: one file where a list is wanted."""
    steps = {s["name"]: s for s in facts.get("steps", [])}

    def wants_a_list(step: Dict[str, Any], mapping: Dict[str, Any],
                     source: Dict[str, Any]) -> bool:
        expected = EXPECTED_TYPES.get(step.get("type") or "", {}).get(
            mapping.get("input_parameter_name"))
        if not expected or expected.get("kind") != "array":
            return False
        if len(_sources_of(mapping)) != 1:
            return False
        edges = [e for e in facts.get("edges", [])
                 if e.get("to_step") == step.get("name")
                 and e.get("to_param") == mapping.get("input_parameter_name")]
        return bool(edges) and (edges[0].get("resolved_type") or "").startswith("array")

    hit = _find_source(workflow, wants_a_list)
    if not hit:
        return None
    step, mapping, source = hit
    before = source.get("output_attribute")
    source["output_attribute"] = "%s.0" % before if before else "0"
    return "%s.%s: %s -> %s" % (step["name"], mapping["input_parameter_name"],
                                before, source["output_attribute"])


def mut_remove_required(workflow: Dict[str, Any],
                        facts: Dict[str, Any]) -> Optional[str]:
    """Delete a classify_documents `documents` mapping."""
    for step in _steps_of(workflow, "classify_documents"):
        keep = [m for m in _mappings_of(step)
                if m.get("input_parameter_name") != "documents"]
        if len(keep) != len(_mappings_of(step)):
            step["input_mappings"] = keep
            return "%s: removed `documents` mapping" % step["name"]
    return None


def _first_multi_source(workflow: Dict[str, Any]) -> Optional[Tuple[
        Dict[str, Any], Dict[str, Any], Dict[str, Any]]]:
    for step in workflow.get("steps", []):
        for mapping in _mappings_of(step):
            value = _value_of(mapping)
            if len(_sources_of(mapping)) > 1:
                return step, mapping, value
    return None


def mut_uppercase_operator(workflow: Dict[str, Any],
                           facts: Dict[str, Any]) -> Optional[str]:
    hit = _first_multi_source(workflow)
    if not hit:
        return None
    step, mapping, value = hit
    operator = value.get("resolution_operator")
    if not isinstance(operator, str) or not operator:
        return None
    value["resolution_operator"] = operator.upper()
    return "%s.%s: %s -> %s" % (step["name"], mapping["input_parameter_name"],
                                operator, operator.upper())


def mut_drop_operator(workflow: Dict[str, Any],
                      facts: Dict[str, Any]) -> Optional[str]:
    hit = _first_multi_source(workflow)
    if not hit:
        return None
    step, mapping, value = hit
    gone = value.get("resolution_operator")
    value["resolution_operator"] = None
    return "%s.%s: dropped `%s` from a %d-source mapping" % (
        step["name"], mapping["input_parameter_name"], gone,
        len(_sources_of(mapping)))


def mut_numeric_over_lists(workflow: Dict[str, Any],
                           facts: Dict[str, Any]) -> Optional[str]:
    """Point a numeric operator at operands that are lists, not numbers."""
    hit = _first_multi_source(workflow)
    if not hit:
        return None
    step, mapping, value = hit
    before = value.get("resolution_operator")
    value["resolution_operator"] = "numeric_add"
    value["resolution_config"] = {"operand_default_value": 0}
    return "%s.%s: %s -> numeric_add over %d list operands" % (
        step["name"], mapping["input_parameter_name"], before,
        len(_sources_of(mapping)))


def mut_rename_code_param(workflow: Dict[str, Any],
                          facts: Dict[str, Any]) -> Optional[str]:
    """Rename a run() parameter AND remove `**kwargs`, so a wired input no
    longer has a slot to land in."""
    code_steps = facts.get("code_steps") or {}
    for step in workflow.get("steps", []):
        if step.get("type") not in ("custom_step", "function"):
            continue
        info = code_steps.get(step.get("name")) or {}
        if not info.get("entry_found"):
            continue
        config = step.get("config") or {}
        code = config.get("code")
        if not isinstance(code, str) or "**kwargs" not in code:
            continue
        wired = [m.get("input_parameter_name") for m in _mappings_of(step)]
        target = next((w for w in wired
                       if w and w in (info.get("params") or ())), None)
        if not target:
            continue
        config["code"] = (code
                          .replace(", **kwargs", "", 1)
                          .replace("%s=None" % target, "%s_renamed=None" % target, 1))
        return "%s: run() `%s` -> `%s_renamed`, **kwargs removed" % (
            step["name"], target, target)
    return None


def mut_undeclared_return(workflow: Dict[str, Any],
                          facts: Dict[str, Any]) -> Optional[str]:
    """Declare an output property the code never returns. Only a step with
    literal-dict returns qualifies: `returns_dynamic` skips the check."""
    code_steps = facts.get("code_steps") or {}
    for step in workflow.get("steps", []):
        info = code_steps.get(step.get("name")) or {}
        if info.get("returns_dynamic") or not info.get("declared_output_properties"):
            continue
        props = ((step.get("config") or {}).get("output_schema") or {}).get("properties")
        if not isinstance(props, dict) or not props:
            continue
        props["never_returned_field"] = {"type": "string"}
        return "%s: declared `never_returned_field` in output_schema" % step["name"]
    return None


def _extraction_container(workflow: Dict[str, Any], want: str) -> Optional[Tuple[str, str]]:
    """(step name, field name) of a declared container in an extraction schema.

    `want` is "rows" for an array of objects, "scalars" for an array of
    primitives, or "object" for a nested object.
    """
    for step in workflow.get("steps", []):
        schema = (step.get("config") or {}).get("extraction_schema") or {}
        for name, prop in (schema.get("properties") or {}).items():
            if not isinstance(prop, dict):
                continue
            kind = prop.get("type")
            items = prop.get("items") if isinstance(prop.get("items"), dict) else {}
            if want == "rows" and kind == "array" and items.get("type") == "object":
                return step["name"], name
            if want == "scalars" and kind == "array" and items.get("type") in (
                    "string", "number", "integer", "boolean"):
                return step["name"], name
            if want == "object" and kind == "object" and prop.get("properties"):
                return step["name"], name
    return None


def _repoint_source(workflow: Dict[str, Any], facts: Dict[str, Any],
                    producer: str, path: str,
                    want_kind: Optional[str] = None) -> Optional[str]:
    """Repoint one existing single-source dependency at `producer`.`path`.

    Repointing beats appending a new mapping: it needs no particular step type
    on the consuming end, so a mutation can run on any corpus workflow rather
    than only the ones that happen to contain an `email` step.

    `want_kind` prefers a consuming parameter whose documented type has that
    kind, which is how the intent-specific fix branches get exercised.
    """
    order = {st["name"]: st["index"] for st in facts.get("steps", [])}
    if producer not in order:
        return None
    candidates: List[Tuple[int, Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = []
    for step in workflow.get("steps", []):
        if step.get("name") == producer:
            continue
        if order.get(step.get("name"), -1) <= order[producer]:
            continue           # keep the edge pointing forward, never a cycle
        for mapping in _mappings_of(step):
            sources = _sources_of(mapping)
            if len(sources) != 1:
                continue
            expected = EXPECTED_TYPES.get(step.get("type") or "", {}).get(
                mapping.get("input_parameter_name"))
            rank = 0 if (expected or {}).get("kind") == want_kind else 1
            candidates.append((rank, step, mapping, sources[0]))
    if not candidates:
        return None
    candidates.sort(key=lambda row: row[0])
    _rank, step, mapping, source = candidates[0]
    source["step_name"] = producer
    source["output_attribute"] = path
    return "%s.%s <- %s.%s" % (step["name"], mapping["input_parameter_name"],
                               producer, path)


def mut_value_on_row_array(workflow: Dict[str, Any],
                           facts: Dict[str, Any]) -> Optional[str]:
    """`.value` on an array of row objects — the A6 case."""
    hit = _extraction_container(workflow, "rows")
    if not hit:
        return None
    return _repoint_source(workflow, facts, hit[0], "data.%s.value" % hit[1],
                           want_kind="string")


def mut_value_on_scalar_array(workflow: Dict[str, Any],
                              facts: Dict[str, Any]) -> Optional[str]:
    """`.value` on an array of scalars — the spelling most likely to trip an
    author who thinks of a scalar list as one leaf."""
    hit = _extraction_container(workflow, "scalars")
    if not hit:
        return None
    return _repoint_source(workflow, facts, hit[0], "data.%s.value" % hit[1],
                           want_kind="string")


def mut_value_on_object(workflow: Dict[str, Any],
                        facts: Dict[str, Any]) -> Optional[str]:
    """`.value` on a nested object node."""
    hit = _extraction_container(workflow, "object")
    if not hit:
        return None
    return _repoint_source(workflow, facts, hit[0], "data.%s.value" % hit[1],
                           want_kind="string")


def mut_whole_list_control(workflow: Dict[str, Any],
                           facts: Dict[str, Any]) -> Optional[str]:
    """The same row array wired WITHOUT `.value`, into a parameter that wants a
    list — the correct spelling for "the whole list", and it must stay clean."""
    hit = _extraction_container(workflow, "rows")
    if not hit:
        return None
    return _repoint_source(workflow, facts, hit[0], "data.%s" % hit[1],
                           want_kind="array")


def mut_row_index_control(workflow: Dict[str, Any],
                          facts: Dict[str, Any]) -> Optional[str]:
    """The indexed spelling of the same row array — must stay clean."""
    hit = _extraction_container(workflow, "rows")
    if not hit:
        return None
    step_name, field = hit
    schema: Dict[str, Any] = {}
    for step in workflow.get("steps", []):
        if step["name"] == step_name:
            schema = (((step.get("config") or {}).get("extraction_schema") or {})
                      .get("properties", {}).get(field) or {})
    columns = sorted(((schema.get("items") or {}).get("properties") or {}))
    if not columns:
        return None
    return _repoint_source(workflow, facts, step_name,
                           "data.%s.0.%s.value" % (field, columns[0]),
                           want_kind="string")


def mut_bare_cell_to_object(workflow: Dict[str, Any],
                            facts: Dict[str, Any]) -> Optional[str]:
    """A bare `data.<scalar>` wired into a parameter declared `object`.

    reference/input_mappings.md: reading the whole cell is legitimate there, so
    this must be a note and never a blocker.
    """
    hit = _scalar_extraction_field(workflow)
    if not hit:
        return None
    return _repoint_source(workflow, facts, hit[0], "data.%s" % hit[1],
                           want_kind="object")


def mut_set_title_excuse(workflow: Dict[str, Any],
                         facts: Dict[str, Any]) -> Optional[str]:
    """Flip the orphaned hidden step to output_type set_title. The platform
    then consumes its output, so UNCONSUMED_FIELD must stop firing on it."""
    for step in workflow.get("steps", []):
        if step.get("output_type") != "simple" or step.get("display_step"):
            continue
        step["output_type"] = "set_title"
        return "%s: output_type simple -> set_title" % step["name"]
    return None


MUTATIONS = [
    ("break a deep path (classify class typo)", mut_break_deep_path, "UNRESOLVED_PATH"),
    ("strip a .value (cell into a string param)", mut_strip_value, "CELL_NOT_UNWRAPPED"),
    ("the same wiring WITH .value (control)", mut_strip_value_control, None),
    ("index a list wired to an array param", mut_index_a_list, "INDEX_MISMATCH"),
    ("remove a required mapping", mut_remove_required, "MISSING_REQUIRED_INPUT"),
    ("uppercase an operator", mut_uppercase_operator, "OPERATOR_NOT_LOWERCASE"),
    ("drop a multi-source operator", mut_drop_operator, "OPERATOR_MISSING"),
    ("numeric_add over list operands", mut_numeric_over_lists, "OPERATOR_OPERAND_TYPE"),
    ("rename a code step parameter", mut_rename_code_param, "CODE_SIGNATURE_MISMATCH"),
    ("declare an un-returned output", mut_undeclared_return, "CODE_RETURN_MISSING"),
    ("`.value` on an array of row objects", mut_value_on_row_array,
     "CONTAINER_VALUE_SUFFIX"),
    ("`.value` on an array of scalars", mut_value_on_scalar_array,
     "CONTAINER_VALUE_SUFFIX"),
    ("`.value` on a nested object", mut_value_on_object, "CONTAINER_VALUE_SUFFIX"),
    ("the indexed row spelling (control)", mut_row_index_control, None),
    ("the whole row list, no `.value` (control)", mut_whole_list_control,
     "!CONTAINER_VALUE_SUFFIX"),
    ("bare cell into an object parameter", mut_bare_cell_to_object,
     "CELL_WIRED_WHOLE"),
    ("output_type simple -> set_title (control)", mut_set_title_excuse, None),
]


def self_test() -> int:
    failures: List[str] = []
    rows: List[List[Any]] = []
    baselines: Dict[str, Dict[str, Any]] = {}

    work = tempfile.mkdtemp(prefix="flow_selftest_")

    print("flow_check self-test — clean corpus (expected: 0 blockers each)")
    for label, path in CORPUS:
        path = os.path.abspath(path)
        if not os.path.exists(path):
            failures.append("corpus file missing: %s" % path)
            continue
        facts_dir = os.path.join(work, label)
        facts, facts_secs = _build_facts(path, facts_dir)
        started = time.time()
        result = _collect(facts)
        secs = time.time() - started
        baselines[label] = result
        cover = result["coverage"]
        if cover["unaccounted"]:
            failures.append("%s: %d mapping(s) fell through every coverage "
                            "bucket — coverage_bucket() is incomplete"
                            % (label, cover["unaccounted"]))
        if cover["checked"] != result["types_checked"]:
            failures.append("%s: coverage reports %d type-checked mappings but "
                            "check_types compared %d — the two have drifted"
                            % (label, cover["checked"], result["types_checked"]))
        counts = collections.Counter(f["severity"] for f in result["findings"])
        rows.append([
            label,
            facts["counters"].get("steps"),
            facts["counters"].get("edges"),
            len(facts.get("fields") or ()),
            counts["blocker"], counts["warning"], counts["note"],
            "%d/%d" % (cover["checked"], cover["mappings"]),
            len(result["unconsumed"]), len(result["undecidable"]),
            len(result["unproduced"]),
            "%.3fs" % secs, "%.2fs" % facts_secs,
        ])
        if counts["blocker"]:
            for item in result["blockers"][:6]:
                failures.append("%s: blocker on a validator-clean workflow — "
                                "%s %s: %s" % (label, item["code"],
                                               item.get("step"),
                                               truncate(item["detail"], 120)))
    for line in table(rows, ["workflow", "steps", "edges", "fields", "blk",
                             "warn", "note", "typed", "unused", "undecided",
                             "unproduced", "flow", "facts"],
                      max_rows=len(rows)):
        print(line)

    failures.extend(_cross_check_redundancy(work, baselines))

    # -- mutation matrix ---------------------------------------------------- #
    # Each mutation runs against the first corpus workflow that offers a site
    # for it. No single workflow has every shape — LPL declares no scalar array
    # and no nested object inside an extraction schema, for instance — so
    # pinning the matrix to one file would leave detectors untested.
    print("")
    print("mutation matrix")
    sources: List[Tuple[str, str, str, Dict[str, Any], collections.Counter]] = []
    for label, path in CORPUS:
        path = os.path.abspath(path)
        if not os.path.exists(path) or label not in baselines:
            continue
        with open(path) as handle:
            pristine = handle.read()
        facts, _ = _build_facts(path, os.path.join(work, "mut_base_%s" % label))
        sources.append((label, path, pristine, facts,
                        baselines[label]["codes"]))
    if not sources:
        failures.append("mutation matrix: no usable corpus baseline")

    mrows: List[List[Any]] = []
    for label, mutator, expected in MUTATIONS:
        chosen = None
        for wf_label, _path, pristine, base_facts, base_codes in sources:
            workflow = json.loads(pristine)
            try:
                description = mutator(workflow, base_facts)
            except Exception as exc:  # noqa: BLE001 — a broken mutator is a test bug
                failures.append("mutation %r raised %s on %s"
                                % (label, exc, wf_label))
                description = None
            if description is not None:
                chosen = (wf_label, workflow, description, base_codes)
                break
        if chosen is None:
            failures.append("mutation %r found no site in any corpus workflow" % label)
            mrows.append([label, "-", "no site", "-", "-"])
            continue
        wf_label, workflow, description, base_codes = chosen

        mut_dir = os.path.join(work, "mut_%d" % len(mrows))
        os.makedirs(mut_dir, exist_ok=True)
        mut_path = os.path.join(mut_dir, "workflow.json")
        with open(mut_path, "w") as handle:
            json.dump(workflow, handle)
        facts, _ = _build_facts(mut_path, mut_dir)
        result = _collect(facts)
        codes = result["codes"]

        if expected is None:
            new = [c for c in codes if codes[c] > base_codes.get(c, 0)]
            gone = [c for c in base_codes if codes.get(c, 0) < base_codes[c]]
            fired = "clean" if not new else "+".join(sorted(new))
            if gone and not new:
                fired = "cleared %s" % ",".join(sorted(gone))
            ok = not new
            if not ok:
                failures.append("control mutation %r introduced %s"
                                % (label, ", ".join(sorted(new))))
        elif expected.startswith("!"):
            # A narrow control: this ONE code must stay silent. Used where the
            # edit is legal for the detector under test but still trips another
            # detector for an unrelated and correct reason.
            forbidden = expected[1:]
            delta = codes.get(forbidden, 0) - base_codes.get(forbidden, 0)
            ok = delta == 0
            other = sorted(c for c in codes
                           if c != forbidden and codes[c] > base_codes.get(c, 0))
            fired = "no %s%s" % (forbidden,
                                 (" (+%s)" % ",".join(other)) if other else "")
            if not ok:
                failures.append("control mutation %r raised %s x%d"
                                % (label, forbidden, delta))
        else:
            delta = codes.get(expected, 0) - base_codes.get(expected, 0)
            ok = delta > 0
            fired = "%s x%d" % (expected, delta) if delta > 0 else "NOT FIRED"
            if not ok:
                failures.append("mutation %r did not raise %s on %s (codes: %s)"
                                % (label, expected, wf_label, dict(codes)))
            other = [c for c in codes
                     if c != expected and codes[c] > base_codes.get(c, 0)]
            if other:
                fired += " (+%s)" % ",".join(sorted(other))
        mrows.append([label, wf_label, truncate(description, 52), fired,
                      "PASS" if ok else "FAIL"])

    for line in table(mrows, ["mutation", "workflow", "site", "fired", ""],
                      max_rows=len(mrows)):
        print(line)

    # The corpus must be byte-identical: this pass is read-only.
    for wf_label, path, pristine, _facts, _codes in sources:
        with open(path) as handle:
            if handle.read() != pristine:
                failures.append("%s changed on disk — this pass must never "
                                "write to the corpus" % wf_label)

    print("")
    if failures:
        print("FAILED (%d):" % len(failures))
        for line in failures[:20]:
            print("  - %s" % truncate(line, 200))
        return 1
    print("flow_check self-test PASSED — %d corpus workflows, %d mutations"
          % (len(rows), len(mrows)))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pass 2 — data flow and type resolution.")
    add_common_args(parser)
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.self_test:
        return self_test()
    require_args(args, "facts", "out")
    return run_pass(args.facts, args.out)


if __name__ == "__main__":
    sys.exit(main())
