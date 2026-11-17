#!/usr/bin/env python3
"""Pass 6 — simplification: unused fields, duplicates, size, confusing-logic smells.

This pass is entirely advisory. It emits no blockers, ever: nothing it finds is a
correctness failure, only "a human could probably make this workflow smaller".
Where a detector cannot tell design from accident it says so in the finding text
instead of guessing — the repeated Quote/Binder/Policy extraction in
`cna_dua_audit_lpl.json` is deliberate (the point is comparing the same fields
across documents), and a pass that called it an error would be worse than no
pass at all.

Reads `facts.json` only (CONTRACT.md hard rule 2). Thresholds come from
`common.THRESHOLDS` so they stay tunable in one edit. Never prints a schema,
prompt, or code body.

Detectors:
  UNUSED_FIELDS              a field nothing downstream reads and no human sees
  FIELDS_READ_WHOLESALE      fields we cannot prove either way (note)
  OVERSIZED_SCHEMA           > MAX_FIELDS_PER_STEP fields on one step
  DEEP_SCHEMA                nesting past the depth the path resolver verifies
  LONG_PROMPT                prompt_chars > MAX_PROMPT_CHARS
  DUPLICATE_FIELD            one normalized field name extracted in >1 step
  DUPLICATE_STEP             same type, overlapping field set, same producers
  RESHAPE_CHAIN              > MAX_RESHAPE_CHAIN chained rename/repack code steps
  ORPHAN_STEP                output consumed by nobody, not displayed, not terminal
plus a scale report, printed as context with no finding attached.

stdlib only. Python 3.9 compatible.
"""
from __future__ import annotations

import argparse
import collections
import itertools
import json
import os
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import (  # noqa: E402
    OUTPUT_ENVELOPE_KEYS,
    THRESHOLDS,
    PassOutput,
    add_common_args,
    comparable_fields,
    field_identity,
    finding,
    load_facts,
    table,
    truncate,
)

PASS_NAME = "simplify"

# --------------------------------------------------------------------------- #
# Platform facts, each sourced from a workflow-skill reference doc.
# CONTRACT.md "Reference docs — the source of truth for rules".
# --------------------------------------------------------------------------- #

#: reference/output_types.md, "Pattern A — DEFAULT": the backend auto-fills the
#: whole output shape for these step types. Their fields are not the author's to
#: trim, so an unread one is not a simplification the author can act on.
PLATFORM_SHAPED_TYPES = frozenset(("prepare_documents", "knowledge_base"))

#: reference/output_types.md "OutputType enum": the platform itself consumes
#: these outputs — `set_title` sets the execution title, `update_workflow_log`
#: writes execution-log fields. "No downstream step reads it" proves nothing.
PLATFORM_CONSUMED_OUTPUT_TYPES = frozenset(("set_title", "update_workflow_log"))

#: CONTRACT.md "Notes on producing it": terminal step types. Data that reaches
#: one of these reaches a person.
USER_FACING_TYPES = frozenset((
    "email", "fill_docx", "submission_summary_generator", "document_viewer",
    "text_block", "hold",
))

#: CONTRACT.md "Notes on producing it": code step types.
CODE_TYPES = frozenset(("custom_step", "function"))

#: CONTRACT.md "Cell wrapping": the step types whose `data` leaves are cells.
EXTRACTION_TYPES = frozenset((
    "extract_from_multiple_sources", "extract_rows_from_multiple_sources",
    "extract_mixed_schema_from_multiple_sources", "agentic_extraction",
    "agentic_guideline_check", "sov_mapping", "generate_qa_table_from_agent",
    "multi_column_qa",
))

#: A decision step's output (`branches_to_execute`, `evaluation_results`) is read
#: by the engine, not by a wired consumer. CONTRACT.md "decisions".
CONTROL_FLOW_TYPES = frozenset(("decision",))

# --------------------------------------------------------------------------- #
# Degeneracy guards. Deliberately NOT in THRESHOLDS: these are not policy the
# user tunes, they stop a ratio from being computed over a set too small to mean
# anything, and they cap how many examples a finding carries.
# --------------------------------------------------------------------------- #

#: Jaccard overlap over a one-element field set is uninformative — every
#: single-field step "fully overlaps" every other. Two ads steps
#: (`Extract Account Analysis Fields`, `Extract Supplemental Application Fields`)
#: each declare exactly one field named `extracted_fields` and would pair at 1.00.
#:
#: CONTRACT.md amendment B4 makes this policy, not an implementation limit:
#: changing it changes which findings a human sees (at 1 it resurrects the
#: es_umbrella `Guideline Check - Alpha/Beta/Gamma` loop candidate). Requested as
#: THRESHOLDS["MIN_DUP_STEP_FIELDS"] and granted there at 2.
MIN_DUP_STEP_FIELDS = THRESHOLDS.get("MIN_DUP_STEP_FIELDS", 2)

#: With one field in the bucket there is no "which of them is read" to ask, so a
#: single-field step earns no wholesale note. Same B4 status as the guard above.
MIN_WHOLESALE_FIELDS = THRESHOLDS.get("MIN_WHOLESALE_FIELDS", 2)

#: CONTRACT.md amendment B9 sets this to 5 in THRESHOLDS and pins its metric to
#: `fields[].depth` — authored path segments — and nothing else. An earlier
#: build measured `schema_depth(step.output)` instead, which walks the
#: normalized schema and so counts the four cell members underneath every
#: extraction leaf; that reads 7-8 where B9's metric reads 4-6 and would make
#: any threshold calibrated on one metric meaningless against the other.
MAX_SCHEMA_DEPTH = THRESHOLDS.get("MAX_SCHEMA_DEPTH", 5)

#: Presentation only: how many examples a finding's detail line carries. Changing
#: it changes how much of a finding is shown, never which findings exist, so by
#: B4 it stays local.
MAX_EXAMPLES = 5

#: CONTRACT.md amendment B2: the platform-added output envelope. Owned by
#: `common.OUTPUT_ENVELOPE_KEYS`; re-exported under the name this module's
#: self-test asserts against so there is exactly one definition.
ENVELOPE_ROOT_KEYS = tuple(sorted(OUTPUT_ENVELOPE_KEYS))

#: How each duplicate-field verdict reads in the report.
VERDICT_LABEL = {
    "passthrough": "not a duplicate",
    "cross_source": "deliberate",
    "per_step_annotation": "deliberate",
    "type_mismatch": "possibly accidental",
    "redundant": "possibly accidental",
}

#: Every code this pass can emit. The self-test walks the corpus and asserts
#: this list is exactly right, so it cannot rot into a stale comment.
ALL_CODES = (
    "UNUSED_FIELDS", "FIELDS_READ_WHOLESALE", "OVERSIZED_SCHEMA", "DEEP_SCHEMA",
    "LONG_PROMPT", "DUPLICATE_FIELD", "DUPLICATE_STEP", "RESHAPE_CHAIN",
    "ORPHAN_STEP",
)

#: Detectors that have never fired on a real workflow — proven to fire and to
#: stay silent at the boundary, but with a false-positive rate that is measured
#: nowhere. Reported in the output so a reader knows which findings are backed by
#: corpus evidence. `DEEP_SCHEMA` left this list when B9 moved the metric to
#: `fields[].depth` and the threshold to 5; it now fires on 2 of 141 steps.
#: The self-test asserts membership against the corpus, both directions.
SYNTHETIC_ONLY_DETECTORS = ("RESHAPE_CHAIN",)

# Field usage buckets. Mutually exclusive and exhaustive over `facts["fields"]`.
USED_NAMED = "used_named"          # something downstream names this field
USED_VISIBLE = "used_visible"      # a person reads it on the canvas or in a doc
OPAQUE = "read_wholesale"          # inside a wholesale handoff; cannot tell
UNUSED = "unused"                  # nothing reads it, nobody sees it
OUT_OF_SCOPE = "out_of_scope"      # platform-shaped or platform-consumed
BUCKETS = (USED_NAMED, USED_VISIBLE, OPAQUE, UNUSED, OUT_OF_SCOPE)


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #

def normalize_name(name: Any) -> str:
    """Lowercase and strip everything that is not a letter or digit."""
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


#: The envelope roots as they appear in a normalized path key.
ENVELOPE_ROOTS = frozenset(normalize_name(k) for k in ENVELOPE_ROOT_KEYS)


def comparison_set(step_output: Optional[Dict[str, Any]]) -> Set[str]:
    """The sanctioned input to every overlap score in this pass.

    `common.comparable_fields()` is the one implementation (CONTRACT.md B2),
    shared byte-for-byte with the module pass; this wraps it in the token form
    from `common.field_identity` so two steps that spell one field differently
    (`named_insured` vs `Named Insured`) still line up. Nothing here recomputes
    what that helper decides — not the envelope strip, not the cell collapse,
    not the `dynamic_keys` handling.
    """
    return {field_identity(path)[1] for path in comparable_fields(step_output)
            if field_identity(path)[1]}


def normalize_path(path: Any) -> str:
    """Normalize a dotted path for cross-step comparison, dropping index
    segments so `data.limits.0.amount` and `data.limits.3.amount` are one field.
    """
    segments = [normalize_name(s) for s in str(path).split(".") if not s.isdigit()]
    return ".".join(s for s in segments if s)


# --------------------------------------------------------------------------- #
# Shared derivations
# --------------------------------------------------------------------------- #

class Model:
    """Everything the detectors need, derived from facts once."""

    def __init__(self, facts: Dict[str, Any]) -> None:
        self.facts = facts
        self.steps: Dict[str, Dict[str, Any]] = {
            s.get("name"): s for s in facts.get("steps", []) if s.get("name")
        }
        self.fields: List[Dict[str, Any]] = list(facts.get("fields", []))
        self.code_steps: Dict[str, Any] = dict(facts.get("code_steps", {}) or {})
        self.graph: Dict[str, Any] = dict(facts.get("graph", {}) or {})

        self.edges_from: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
        self.dep_producers: Dict[str, Set[str]] = collections.defaultdict(set)
        self.dep_sources: Dict[str, Set[Tuple[str, Optional[str]]]] = collections.defaultdict(set)
        self.dependency_edges = 0
        for edge in facts.get("edges", []):
            src = edge.get("from_step")
            if src:
                self.edges_from[src].append(edge)
            if edge.get("input_type") == "dependency":
                self.dependency_edges += 1
                if src:
                    self.dep_producers[edge.get("to_step")].add(src)
                    self.dep_sources[edge.get("to_step")].add(
                        (src, edge.get("output_attribute")))

        # `fields[]` is the reporting inventory (CONTRACT.md B3), so it is what
        # per-step counts and printed examples come from. `comparable` is the
        # comparison input: envelope keys stripped, per B2.
        self.step_field_paths: Dict[str, Set[str]] = collections.defaultdict(set)
        self.step_field_count: Dict[str, int] = collections.Counter()
        for field in self.fields:
            step = field.get("step")
            key = normalize_path(field.get("path"))
            if key:
                self.step_field_paths[step].add(key)
            self.step_field_count[step] += 1

        sinks = self.graph.get("sinks")
        self._sinks: Optional[Set[str]] = set(sinks) if isinstance(sinks, list) else None

        # B3: `fields[]` above is the reporting inventory. This is the
        # comparison input, and it is derived from the step's normalized output
        # because that is what `common.comparable_fields()` reads.
        self.comparable: Dict[str, Set[str]] = {
            name: comparison_set(step.get("output"))
            for name, step in self.steps.items()
        }

        # Authored nesting depth per step, B9's metric: max `fields[].depth`.
        self.authored_depth: Dict[str, int] = collections.defaultdict(int)
        for field in self.fields:
            step_name = field.get("step")
            depth = field.get("depth") or 0
            if depth > self.authored_depth[step_name]:
                self.authored_depth[step_name] = depth

        # html_producers[].interpolated_paths carries paths a producer's code
        # interpolates. CONTRACT.md does not say which step each path belongs to,
        # so match defensively by exact-or-suffix against every field. Empty on
        # all five corpus workflows, so this arm is untested against real data.
        self.interpolated: Set[str] = set()
        for producer in facts.get("html_producers", []) or []:
            for path in producer.get("interpolated_paths") or []:
                self.interpolated.add(str(path))

    # -- step predicates -------------------------------------------------- #

    def step_type(self, name: Optional[str]) -> str:
        return (self.steps.get(name) or {}).get("type") or ""

    def is_user_facing(self, name: Optional[str]) -> bool:
        step = self.steps.get(name) or {}
        return bool(step.get("display_step")) or bool(step.get("is_terminal")) \
            or step.get("type") in USER_FACING_TYPES

    def out_of_scope_reason(self, name: Optional[str]) -> Optional[str]:
        """Why this step's fields are not the author's to simplify, or None."""
        step = self.steps.get(name) or {}
        if step.get("type") in CONTROL_FLOW_TYPES:
            return "decision step; the engine reads its output, not a wired consumer"
        if step.get("type") in PLATFORM_SHAPED_TYPES:
            return "output shape is auto-filled by the platform (reference/output_types.md)"
        if step.get("output_type") in PLATFORM_CONSUMED_OUTPUT_TYPES:
            return "output_type=%s; the platform consumes this output" % step.get("output_type")
        return None

    def is_consumed(self, name: str) -> bool:
        """Does anything read this step's output?

        `graph.sinks` answers exactly this and is the authority. `graph.terminals`
        is type-based and answers a different question — whether the workflow
        delivers through an email/docx/summary/display step — so an
        extraction-only workflow has zero terminals and eleven real endpoints.
        Reading `terminals` here would call every one of them an orphan.
        """
        if self._sinks is not None:
            return name not in self._sinks
        return bool(self.edges_from.get(name))


# --------------------------------------------------------------------------- #
# Detector 1 — unused fields
# --------------------------------------------------------------------------- #

def _match_attribute(output_attribute: Optional[str],
                     spellings: Sequence[str]) -> Optional[str]:
    """How one edge's `output_attribute` relates to one field.

    Returns "named" when the edge names the field (or something beneath it),
    "ancestor" when the edge takes a whole subtree the field sits inside, and
    None when they are unrelated.
    """
    if output_attribute is None or output_attribute == "":
        return "ancestor"                      # the whole output dict is taken
    for path in spellings:
        if output_attribute == path or output_attribute.startswith(path + "."):
            return "named"
    for path in spellings:
        if path.startswith(output_attribute + "."):
            remainder = path[len(output_attribute) + 1:].split(".")
            # Only index segments below the consumed node means there are no
            # sibling names to distinguish: taking the array takes the element.
            return "named" if all(seg.isdigit() for seg in remainder) else "ancestor"
    return None


def classify_fields(model: Model) -> List[Dict[str, Any]]:
    """Bucket every field in `facts["fields"]`. Buckets are exclusive."""
    rows: List[Dict[str, Any]] = []
    for field in model.fields:
        step = field.get("step")
        path = str(field.get("path") or "")
        spellings = [p for p in {path, str(field.get("value_path") or "")} if p]

        reason = model.out_of_scope_reason(step)
        if reason:
            rows.append({
                "step": step, "path": path, "name": field.get("name"),
                "type": field.get("type"), "bucket": OUT_OF_SCOPE, "why": reason,
            })
            continue

        named = False
        wholesale = False
        wholesale_seen_by_person = False
        readers: List[str] = []
        for edge in model.edges_from.get(step, []):
            relation = _match_attribute(edge.get("output_attribute"), spellings)
            if relation is None:
                continue
            target = edge.get("to_step")
            if relation == "named":
                named = True
                readers.append(str(target))
            else:
                wholesale = True
                if model.is_user_facing(target):
                    wholesale_seen_by_person = True

        if not named and model.interpolated:
            for rendered in model.interpolated:
                if any(rendered == p or rendered.endswith("." + p) for p in spellings):
                    named = True
                    readers.append("(html interpolation)")
                    break

        own_display = model.is_user_facing(step)
        if named:
            bucket, why = USED_NAMED, "read by %s" % truncate(", ".join(sorted(set(readers))), 80)
        elif own_display:
            bucket, why = USED_VISIBLE, "step is display_step: a person reads it on the canvas"
        elif wholesale_seen_by_person:
            bucket, why = USED_VISIBLE, "reaches a displayed or terminal step inside a wholesale handoff"
        elif wholesale:
            bucket, why = OPAQUE, "only ever handed on as part of a whole-output mapping"
        else:
            bucket, why = UNUSED, "no downstream mapping, condition or renderer names it"

        rows.append({"step": step, "path": path, "name": field.get("name"),
                     "type": field.get("type"), "bucket": bucket, "why": why})
    return rows


def detect_unused_fields(model: Model, classified: Sequence[Dict[str, Any]],
                         out: PassOutput) -> Dict[str, Any]:
    """One finding per step for unread fields, one note per step for the fields
    this pass genuinely cannot judge."""
    per_step: Dict[str, Dict[str, List[Dict[str, Any]]]] = collections.defaultdict(
        lambda: collections.defaultdict(list))
    for row in classified:
        per_step[row["step"]][row["bucket"]].append(row)

    wholesale_steps: List[Tuple[str, int, int]] = []
    for step in sorted(per_step):
        buckets = per_step[step]
        total = sum(len(v) for v in buckets.values())
        unused = buckets.get(UNUSED) or []
        opaque = buckets.get(OPAQUE) or []
        info = model.steps.get(step) or {}

        if unused:
            examples = ", ".join(r["path"] for r in unused[:MAX_EXAMPLES])
            more = len(unused) - min(len(unused), MAX_EXAMPLES)
            out.add(finding(
                "UNUSED_FIELDS", "warning",
                "%d of %d field%s read by nothing" % (
                    len(unused), total, "s are" if len(unused) > 1 else " is"),
                "Nothing downstream maps, tests or renders %s, and the step is not "
                "shown on the canvas%s. Worst: %s%s."
                % ("these %d fields" % len(unused) if len(unused) > 1 else "this field",
                   " (display_step is false)" if info.get("is_hidden") else "",
                   examples, " (+%d more)" % more if more else ""),
                step=step,
                fix="Drop them from the schema, or wire them to the consumer that "
                    "was meant to read them. Full list in %s.json." % PASS_NAME,
                evidence={"unused_count": len(unused), "field_count": total,
                          "is_hidden": bool(info.get("is_hidden")),
                          "step_type": info.get("type"),
                          "fields": [r["path"] for r in unused]},
                group_key="UNUSED_FIELDS:%s" % step))

        if len(opaque) >= MIN_WHOLESALE_FIELDS:
            wholesale_steps.append((step, len(opaque), total))

    # One note for the whole workflow: on the ads workflow this covers 14 steps,
    # and 14 copies of the same sentence would be noise. Per-step counts print in
    # the field-usage table and live in full in the JSON payload.
    if wholesale_steps:
        wholesale_steps.sort(key=lambda row: (-row[1], row[0]))
        worst = ", ".join("%s (%d)" % (name, count)
                          for name, count, _ in wholesale_steps[:MAX_EXAMPLES])
        more = len(wholesale_steps) - min(len(wholesale_steps), MAX_EXAMPLES)
        out.add(finding(
            "FIELDS_READ_WHOLESALE", "note",
            "%d fields on %d steps: cannot determine, not unread"
            % (sum(row[1] for row in wholesale_steps), len(wholesale_steps)),
            "Consumers take these steps' output wholesale into code rather than "
            "naming fields, and a docx template is download-only so its reads are "
            "never visible here. Counted separately from the unread total on "
            "purpose. Widest: %s%s."
            % (worst, " (+%d more)" % more if more else ""),
            step=None,
            fix="Read the consuming code, or narrow the input mappings to the fields "
                "they need so the next run can prove it. Per-step lists in %s.json."
                % PASS_NAME,
            evidence={"steps": [{"step": name, "wholesale": count, "fields": total}
                                for name, count, total in wholesale_steps]},
            group_key="FIELDS_READ_WHOLESALE"))

    totals = collections.Counter(row["bucket"] for row in classified)
    return {
        "totals": {bucket: totals.get(bucket, 0) for bucket in BUCKETS},
        # B10: named explicitly so a consumer of this JSON cannot mistake the
        # cannot-determine count for part of the unread verdict.
        "unread_count": totals.get(UNUSED, 0),
        "cannot_determine_count": totals.get(OPAQUE, 0),
        "cannot_determine_reason": (
            "consumed wholesale into code, or readable only by a docx template; "
            "templates are download-only and these passes have no network"),
        "field_count": len(classified),
        "per_step": {
            step: {bucket: len(rows) for bucket, rows in sorted(buckets.items())}
            for step, buckets in sorted(per_step.items())
        },
        "unused": [
            {"step": r["step"], "path": r["path"], "type": r["type"]}
            for r in classified if r["bucket"] == UNUSED
        ],
        "read_wholesale": [
            {"step": r["step"], "path": r["path"], "type": r["type"]}
            for r in classified if r["bucket"] == OPAQUE
        ],
    }


# --------------------------------------------------------------------------- #
# Detectors 2 and 3 — size
# --------------------------------------------------------------------------- #

def detect_size(model: Model, out: PassOutput) -> Dict[str, Any]:
    max_fields = THRESHOLDS["MAX_FIELDS_PER_STEP"]
    max_prompt = THRESHOLDS["MAX_PROMPT_CHARS"]
    oversized: List[Dict[str, Any]] = []
    deep: List[Dict[str, Any]] = []
    long_prompts: List[Dict[str, Any]] = []

    for name in sorted(model.steps):
        step = model.steps[name]
        if model.out_of_scope_reason(name):
            continue

        count = model.step_field_count.get(name, 0)
        if count > max_fields:
            oversized.append({"step": name, "type": step.get("type"), "fields": count})
            out.add(finding(
                "OVERSIZED_SCHEMA", "warning",
                "%d fields on one step" % count,
                "%d fields, over the %d-field guideline. One step this wide is "
                "slower to run, harder to review and harder to correct by hand."
                % (count, max_fields),
                step=name,
                fix="Split it along the documents or sections it reads, or drop the "
                    "fields nothing consumes.",
                evidence={"field_count": count, "threshold": max_fields,
                          "step_type": step.get("type")},
                group_key="OVERSIZED_SCHEMA:%s" % name))

        depth = model.authored_depth.get(name, 0)
        if depth >= MAX_SCHEMA_DEPTH:
            deep.append({"step": name, "depth": depth})
            out.add(finding(
                "DEEP_SCHEMA", "warning",
                "schema nests %d authored segments deep" % depth,
                "%d authored path segments, at or over the %d-segment guideline. Two "
                "of 141 corpus steps reach this, so it marks a genuine outlier rather "
                "than ordinary nesting — a consumer has to spell out every segment to "
                "reach a value here." % (depth, MAX_SCHEMA_DEPTH),
                step=name,
                fix="Flatten the schema so a consumer reaches a value within %d "
                    "segments." % (MAX_SCHEMA_DEPTH - 1),
                evidence={"depth": depth, "limit": MAX_SCHEMA_DEPTH},
                group_key="DEEP_SCHEMA:%s" % name))

        chars = step.get("prompt_chars") or 0
        if chars > max_prompt:
            long_prompts.append({"step": name, "prompt_chars": chars})
            out.add(finding(
                "LONG_PROMPT", "warning",
                "prompt is %s chars" % format(chars, ","),
                "%s prompt characters, over the %s-char guideline. Long prompts bury "
                "the instructions that matter and cost tokens on every run."
                % (format(chars, ","), format(max_prompt, ",")),
                step=name,
                fix="Move the stable reference material into a knowledge base or a "
                    "guideline document and keep the prompt to the task.",
                evidence={"prompt_chars": chars, "threshold": max_prompt,
                          "prompts": [
                              {"key": p.get("key"), "where": p.get("where"),
                               "chars": p.get("chars")}
                              for p in (step.get("prompts") or [])]},
                group_key="LONG_PROMPT:%s" % name))

    return {"oversized_schemas": oversized, "deep_schemas": deep,
            "long_prompts": long_prompts}


# --------------------------------------------------------------------------- #
# Detector 4 — duplicate fields across steps
# --------------------------------------------------------------------------- #

def _authored_fields(model: Model) -> List[Dict[str, Any]]:
    """Fields an author wrote into an extraction schema, as opposed to platform
    plumbing (`schema`, `metadata`, `user_documents`, classify buckets)."""
    kept: List[Dict[str, Any]] = []
    for field in model.fields:
        if model.out_of_scope_reason(field.get("step")):
            continue
        # B2 applies to this comparison too, not only to the overlap scores.
        if str(field.get("path") or "").split(".")[0] in ENVELOPE_ROOTS:
            continue
        if field.get("is_cell"):
            kept.append(field)
            continue
        step_type = model.step_type(field.get("step"))
        path = str(field.get("path") or "")
        if step_type in EXTRACTION_TYPES and path.startswith("data."):
            kept.append(field)
    return kept


def _reaches(model: Model, source: str, target: str, limit: int = 64) -> bool:
    """Is `target` downstream of `source` in the step graph?"""
    adjacency = model.graph.get("adjacency") or {}
    seen: Set[str] = set()
    queue = collections.deque([source])
    while queue and len(seen) < limit:
        node = queue.popleft()
        for nxt in adjacency.get(node, []):
            if nxt == target:
                return True
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return False


def _step_overlap(model: Model, a: str, b: str) -> float:
    left, right = model.comparable.get(a, set()), model.comparable.get(b, set())
    if not left or not right:
        return 0.0
    return len(left & right) / float(len(left | right))


def _duplicate_field_groups(model: Model) -> List[Dict[str, Any]]:
    """One row per normalized field name that more than one step extracts, with a
    verdict on whether it looks deliberate."""
    by_name: Dict[str, Dict[str, Dict[str, Any]]] = collections.defaultdict(dict)
    for field in _authored_fields(model):
        # One representative per step: a name repeated inside one step is a
        # repeated sub-object (`data.limits.0.amount`), not a duplicate.
        by_name[normalize_name(field.get("name"))].setdefault(field.get("step"), field)

    overlap_floor = THRESHOLDS["DUP_STEP_OVERLAP"]
    rows: List[Dict[str, Any]] = []
    for name in sorted(by_name):
        holders = by_name[name]
        # An all-digit name is an array index (`data.conflicts.0`), not a field.
        if len(holders) < 2 or not name or name.isdigit():
            continue
        steps = sorted(holders)
        types = sorted({str(holders[s].get("type")) for s in steps})
        # CONTRACT.md "Kinds": `unknown` means the resolver gave up and `any`
        # means the platform accepts anything, so neither is evidence that two
        # copies disagree. Only known types can conflict.
        known_types = [t for t in types if t not in ("unknown", "any")]
        sources = {s: model.dep_sources.get(s, frozenset()) for s in steps}
        distinct_sources = len({frozenset(v) for v in sources.values()}) > 1
        # A copy that sits downstream of another copy is a re-emission, not a
        # second extraction. Judge the originals; report the re-emitters.
        reemitters = sorted(
            b for b in steps if any(_reaches(model, a, b) for a in steps if a != b))
        originals = [s for s in steps if s not in reemitters]
        judged = originals or steps
        sources = {s: sources[s] for s in judged}
        distinct_sources = len({frozenset(v) for v in sources.values()}) > 1
        sibling_overlap = max(
            [_step_overlap(model, a, b) for a, b in itertools.combinations(judged, 2)]
            or [0.0])
        carry = ("" if not reemitters else
                 " %d of the copies are downstream re-emissions, not extractions."
                 % len(reemitters))

        if len(judged) < 2:
            kind, severity = "passthrough", "note"
            verdict = ("Only one step extracts this; the other copies are "
                       "downstream re-emissions of it.")
        elif len(known_types) > 1:
            kind, severity = "type_mismatch", "warning"
            verdict = ("The copies disagree on type (%s), which is what accidental "
                       "duplication looks like: a consumer reading by name gets "
                       "whichever step it is wired to.%s"
                       % (", ".join(known_types), carry))
        elif distinct_sources:
            kind, severity = "cross_source", "note"
            verdict = ("Each copy reads a different source — what deliberate "
                       "cross-document comparison looks like.%s" % carry)
        elif sibling_overlap < overlap_floor:
            kind, severity = "per_step_annotation", "note"
            verdict = ("The steps otherwise share little (%.2f overlap, under the "
                       "%.2f bar), so this reads as a per-step annotation, not a "
                       "duplicated extraction.%s"
                       % (sibling_overlap, overlap_floor, carry))
        else:
            kind, severity = "redundant", "warning"
            verdict = ("Same type, same sources, and the steps largely overlap (%.2f), "
                       "so one of these extractions is probably redundant.%s"
                       % (sibling_overlap, carry))

        rows.append({
            "field": holders[steps[0]].get("name"), "normalized": name,
            "steps": steps, "types": types, "known_types": known_types,
            "distinct_sources": distinct_sources,
            "sibling_overlap": round(sibling_overlap, 3), "kind": kind,
            "severity": severity, "verdict_text": verdict,
            "reemitters": reemitters, "extracting_steps": judged,
            "verdict": VERDICT_LABEL[kind],
            "paths": {s: holders[s].get("path") for s in steps},
        })
    return rows


def detect_duplicate_fields(model: Model, out: PassOutput) -> List[Dict[str, Any]]:
    """Cluster the per-field rows by the step set they span, so the LPL
    comparison pattern is one question about 13 fields, not 13 questions."""
    rows = _duplicate_field_groups(model)
    clusters: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = collections.OrderedDict()
    for row in rows:
        clusters.setdefault((tuple(row["steps"]), row["kind"], row["severity"]),
                            []).append(row)

    for key, members in clusters.items():
        steps, kind, severity = list(key[0]), key[1], key[2]
        names = [m["field"] for m in members]
        examples = ", ".join(str(n) for n in names[:MAX_EXAMPLES])
        more = len(names) - min(len(names), MAX_EXAMPLES)
        if len(names) == 1:
            title = "`%s` is extracted on %d steps" % (truncate(names[0], 44), len(steps))
        else:
            title = "%d field names are extracted on the same %d steps" % (
                len(names), len(steps))
        out.add(finding(
            "DUPLICATE_FIELD", severity, title,
            "%s Fields: %s%s. Judged %s — this pass cannot read the prompts, so "
            "treat it as a question; see the table above for the steps." % (
                members[0]["verdict_text"], examples,
                " (+%d more)" % more if more else "", members[0]["verdict"]),
            step=None,
            fix=("Confirm the re-extraction is wanted. If it is, no change."
                 if severity == "note" else
                 "Keep one extraction and have the other consumer read it, or give the "
                 "fields names that say how they differ."),
            evidence={"kind": kind, "steps": steps, "field_count": len(names),
                      "fields": names,
                      "sibling_overlap": members[0]["sibling_overlap"],
                      "distinct_sources": members[0]["distinct_sources"],
                      "types": sorted({t for m in members for t in m["types"]})},
            group_key="DUPLICATE_FIELD:%s:%s" % ("|".join(steps), kind)))
    return rows


# --------------------------------------------------------------------------- #
# Detector 5 — duplicate steps
# --------------------------------------------------------------------------- #

def detect_duplicate_steps(model: Model, out: PassOutput) -> List[Dict[str, Any]]:
    floor = THRESHOLDS["DUP_STEP_OVERLAP"]
    names = sorted(
        n for n in model.steps
        if len(model.comparable.get(n, set())) >= MIN_DUP_STEP_FIELDS
        and not model.out_of_scope_reason(n)
    )

    parent: Dict[str, str] = {n: n for n in names}

    def root(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    pairs: List[Tuple[str, str, float]] = []
    for a, b in itertools.combinations(names, 2):
        if model.step_type(a) != model.step_type(b):
            continue
        if model.dep_producers.get(a, frozenset()) != model.dep_producers.get(b, frozenset()):
            continue
        overlap = _step_overlap(model, a, b)
        if overlap <= floor:
            continue
        pairs.append((a, b, overlap))
        parent[root(a)] = root(b)

    clusters: Dict[str, List[str]] = collections.defaultdict(list)
    for a, b, _ in pairs:
        for node in (a, b):
            if node not in clusters[root(node)]:
                clusters[root(node)].append(node)

    groups: List[Dict[str, Any]] = []
    for key in sorted(clusters, key=lambda k: sorted(clusters[k])):
        members = sorted(clusters[key])
        overlaps = [o for a, b, o in pairs if a in members and b in members]
        source_sets = {frozenset(model.dep_sources.get(m, frozenset())) for m in members}
        same_sources = len(source_sets) == 1
        producers = sorted(model.dep_producers.get(members[0], frozenset()))
        attributes = sorted({
            str(attribute) for member in members
            for _, attribute in model.dep_sources.get(member, frozenset())
        })

        if same_sources:
            severity = "warning"
            reading = ("They read exactly the same inputs, so the only difference is "
                       "inside their prompts or code.")
            fix = ("If they differ only by a parameter (a carrier, a guideline set), a "
                   "loop over that parameter replaces all %d." % len(members))
        else:
            severity = "note"
            reading = ("They read different sources (%s), which is what comparing the "
                       "same fields across documents looks like."
                       % truncate(", ".join(attributes), 100))
            fix = ("Probably intended — confirm it. If the per-step prompts really are "
                   "identical, a loop over the sources would replace all %d."
                   % len(members))

        row = {"steps": members, "type": model.step_type(members[0]),
               "min_overlap": round(min(overlaps), 3) if overlaps else None,
               "max_overlap": round(max(overlaps), 3) if overlaps else None,
               "same_sources": same_sources, "producers": producers,
               "source_attributes": attributes,
               "verdict": "possible duplicate" if same_sources else "likely deliberate",
               "loop_candidate": True}
        groups.append(row)
        out.add(finding(
            "DUPLICATE_STEP", severity,
            "%d %s steps have near-identical field sets — one loop instead?" % (
                len(members), model.step_type(members[0])),
            "Is this %d steps by design, or one loop? %s They overlap %.2f-%.2f on "
            "field names and all take the same producers. Steps: %s." % (
                len(members), reading,
                min(overlaps) if overlaps else 0, max(overlaps) if overlaps else 0,
                truncate(", ".join(members), 110)),
            step=None, fix=fix, evidence=row,
            group_key="DUPLICATE_STEP:%s" % "|".join(members)))
    return groups


# --------------------------------------------------------------------------- #
# Detector 6 — reshape chains
# --------------------------------------------------------------------------- #

def _reshape_kind(model: Model, name: str) -> Optional[str]:
    """"identity" when a code step declares the same property names in and out,
    "projection" when the outputs are a strict subset of the inputs, else None.

    Inferred from `code_steps[].declared_input_properties` /
    `declared_output_properties` — this pass never reads a code body, so a pure
    rename (`basic_info_raw` -> `basic_info`) is indistinguishable from real work
    and is deliberately not claimed.
    """
    if model.step_type(name) not in CODE_TYPES:
        return None
    code = model.code_steps.get(name) or {}
    inputs = set(code.get("declared_input_properties") or [])
    outputs = set(code.get("declared_output_properties") or [])
    if not inputs or not outputs:
        return None
    if inputs == outputs:
        return "identity"
    if outputs < inputs:
        return "projection"
    return None


def detect_reshape_chains(model: Model, out: PassOutput) -> List[Dict[str, Any]]:
    limit = THRESHOLDS["MAX_RESHAPE_CHAIN"]
    kinds = {n: _reshape_kind(model, n) for n in model.steps}
    reshapers = {n for n, k in kinds.items() if k}

    # A link only counts when the step's sole dependency producer is the previous
    # reshaper: that is what makes it a chain rather than a step that happens to
    # re-declare its parameter names.
    def sole_producer(name: str) -> Optional[str]:
        producers = model.dep_producers.get(name, frozenset())
        return sorted(producers)[0] if len(producers) == 1 else None

    successor: Dict[str, str] = {}
    for name in reshapers:
        upstream = sole_producer(name)
        if upstream in reshapers and upstream != name:
            successor[upstream] = name

    heads = [n for n in reshapers if n not in successor.values()]
    chains: List[List[str]] = []
    for head in sorted(heads):
        chain = [head]
        seen = {head}
        node = head
        while node in successor and successor[node] not in seen:
            node = successor[node]
            chain.append(node)
            seen.add(node)
        if len(chain) > 1:
            chains.append(chain)

    rows: List[Dict[str, Any]] = []
    for chain in chains:
        sizes = [(model.steps.get(n) or {}).get("code_chars") or 0 for n in chain]
        row = {"steps": chain, "length": len(chain),
               "kinds": [kinds[n] for n in chain], "code_chars": sizes,
               "over_threshold": len(chain) > limit}
        rows.append(row)
        if not row["over_threshold"]:
            continue
        out.add(finding(
            "RESHAPE_CHAIN", "note",
            "%d chained code steps only re-declare their inputs" % len(chain),
            "%s each declare the same property names in and out and each feeds only "
            "the next, over the %d-step guideline. Code sizes are %s chars, which is "
            "the one thing that argues against calling them pure reshapes — this pass "
            "reads property names, not code." % (
                truncate(" -> ".join(chain), 130), limit,
                ", ".join(format(s, ",") for s in sizes)),
            step=None,
            fix="Collapse the chain into one step, unless the intermediate outputs are "
                "displayed or consumed elsewhere.",
            evidence=row, group_key="RESHAPE_CHAIN:%s" % "|".join(chain)))
    return rows


# --------------------------------------------------------------------------- #
# Detector 7 — orphan steps
# --------------------------------------------------------------------------- #

def detect_orphan_steps(model: Model, out: PassOutput) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for name in sorted(model.steps):
        step = model.steps[name]
        if model.is_consumed(name) or model.is_user_facing(name):
            continue
        if model.out_of_scope_reason(name):
            continue
        row = {"step": name, "type": step.get("type"),
               "output_type": step.get("output_type"),
               "field_count": model.step_field_count.get(name, 0),
               "size_bytes": step.get("size_bytes"),
               "code_chars": step.get("code_chars")}
        rows.append(row)
        out.add(finding(
            "ORPHAN_STEP", "warning",
            "nothing reads this step and nobody sees it",
            "No input mapping or decision condition reads its output, display_step is "
            "false, and it is not terminal, so its %d output fields go nowhere. It "
            "still runs on every execution." % row["field_count"],
            step=name,
            fix="Delete it, or set display_step: true if it exists to be read on the "
                "canvas.",
            evidence=row, group_key="ORPHAN_STEP:%s" % name))
    return rows


# --------------------------------------------------------------------------- #
# Stated limitations (context, no findings)
# --------------------------------------------------------------------------- #

def opaque_output_steps(model: Model) -> List[Dict[str, Any]]:
    """Steps that would be duplicate-step candidates but cannot be compared,
    because their whole output is one opaque field.

    CONTRACT.md ruling: this is recorded as a limitation, not tuned away. The
    es_umbrella `Guideline Check - Alpha/Beta/Gamma` trio is the known case —
    three steps differing only by guideline set, each emitting a single `data`
    object. Lowering MIN_DUP_STEP_FIELDS to 1 would recover them and at the same
    time pair every unrelated single-field step in the ads workflow, because the
    real problem is that a one-field output carries no comparable field set at
    all. Field-set similarity is the wrong instrument here; catching this shape
    needs a different signal, such as prompt similarity.
    """
    small = [
        name for name in sorted(model.steps)
        if not model.out_of_scope_reason(name)
        and 0 < len(model.comparable.get(name, set())) < MIN_DUP_STEP_FIELDS
    ]
    clusters: Dict[Tuple[Any, ...], List[str]] = collections.OrderedDict()
    for name in small:
        key = (model.step_type(name),
               tuple(sorted(model.dep_producers.get(name, frozenset()))))
        clusters.setdefault(key, []).append(name)
    return [{"steps": members, "type": key[0], "producers": list(key[1]),
             "fields": len(model.comparable.get(members[0], set()))}
            for key, members in clusters.items() if len(members) > 1]


def limitation_lines(uncomparable: Sequence[Dict[str, Any]]) -> List[str]:
    """Bounded, and always printed: a reader has to be able to tell which part of
    the answer is solid from the output alone, not from the source."""
    lines = ["", "what this pass cannot see:"]
    for row in uncomparable[:MAX_EXAMPLES]:
        lines.append("  %d %s steps compare as %d opaque %s, so field-set "
                     "similarity cannot judge them: %s" % (
                         len(row["steps"]), row["type"], row["fields"],
                         "field" if row["fields"] == 1 else "fields",
                         truncate(", ".join(row["steps"]), 90)))
    if uncomparable:
        lines.append("    a lower threshold would not fix this — it needs prompt "
                     "similarity, which this pass does not do")
    if len(uncomparable) > MAX_EXAMPLES:
        lines.append("  ... %d more groups" % (len(uncomparable) - MAX_EXAMPLES))
    lines.append("  docx templates are download-only and these passes have no "
                 "network, so a template's field reads are never visible")
    if SYNTHETIC_ONLY_DETECTORS:
        lines.append("  synthetic coverage only, never fired on a real workflow: %s"
                     % ", ".join(SYNTHETIC_ONLY_DETECTORS))
    return lines


# --------------------------------------------------------------------------- #
# Detector 8 — scale report (context, no findings)
# --------------------------------------------------------------------------- #

def _longest_chain(model: Model) -> Tuple[int, List[str]]:
    adjacency = model.graph.get("adjacency") or {}
    reverse = model.graph.get("reverse") or {}
    nodes = set(adjacency) | set(reverse)
    if not nodes:
        return 0, []
    memo: Dict[str, Tuple[int, List[str]]] = {}

    def walk(node: str, stack: Tuple[str, ...]) -> Tuple[int, List[str]]:
        if node in memo:
            return memo[node]
        if node in stack:                      # graph.cycles is non-empty
            return 0, []
        best = (0, [])
        for nxt in adjacency.get(node, []):
            length, path = walk(nxt, stack + (node,))
            if length + 1 > best[0]:
                best = (length + 1, [nxt] + path)
        if len(stack) < 2:                     # only memoize near the roots
            memo[node] = best
        return best

    roots = [n for n in nodes if not reverse.get(n)] or sorted(nodes)
    best = (0, [])
    for root_node in sorted(roots):
        length, path = walk(root_node, ())
        if length + 1 > best[0]:
            best = (length + 1, [root_node] + path)
    return best


def scale_report(model: Model) -> Tuple[Dict[str, Any], List[str]]:
    counters = model.facts.get("counters", {}) or {}
    reverse = model.graph.get("reverse") or {}
    fan_in = sorted(((len(set(v)), k) for k, v in reverse.items()), reverse=True)
    widest = fan_in[0] if fan_in else (0, None)
    chain_length, chain = _longest_chain(model)

    payload = {
        "steps": counters.get("steps", len(model.steps)),
        "hidden_steps": counters.get("hidden_steps"),
        "fields": counters.get("fields", len(model.fields)),
        "schema_leaves": counters.get("schema_leaves"),
        "prompt_chars": counters.get("prompt_chars", 0),
        "code_chars": counters.get("code_chars", 0),
        "edges": counters.get("edges"),
        # CONTRACT.md A2 requires counters.dependency_edges; facts.json does not
        # carry it, so it is recomputed here. Reported as a contract gap.
        "dependency_edges": counters.get("dependency_edges", model.dependency_edges),
        "longest_chain": chain_length,
        "longest_chain_path": chain,
        "widest_fan_in": widest[0],
        "widest_fan_in_step": widest[1],
    }
    lines = ["scale (context, not judged):"]
    lines.extend(table(
        [["steps", payload["steps"], "%s hidden" % payload["hidden_steps"]],
         ["fields", payload["fields"], "%s schema leaves" % payload["schema_leaves"]],
         ["prompt bytes", format(payload["prompt_chars"], ","), ""],
         ["code bytes", format(payload["code_chars"], ","), ""],
         ["edges", payload["edges"], "%s dependency" % payload["dependency_edges"]],
         ["longest chain", chain_length, truncate(" -> ".join(chain), 46)],
         ["widest fan-in", widest[0], truncate(widest[1] or "-", 46)]],
        headers=["metric", "value", "detail"], max_rows=8))
    return payload, lines


# --------------------------------------------------------------------------- #
# Analysis entry point
# --------------------------------------------------------------------------- #

def analyze(facts: Dict[str, Any], out: PassOutput) -> Dict[str, Any]:
    """Run every detector, register findings on `out`, return the JSON payload."""
    model = Model(facts)
    classified = classify_fields(model)

    usage = detect_unused_fields(model, classified, out)
    size = detect_size(model, out)
    duplicate_fields = detect_duplicate_fields(model, out)
    duplicate_steps = detect_duplicate_steps(model, out)
    reshape_chains = detect_reshape_chains(model, out)
    orphans = detect_orphan_steps(model, out)
    scale, scale_lines = scale_report(model)
    uncomparable = opaque_output_steps(model)

    payload = {
        "field_usage": usage,
        "oversized_schemas": size["oversized_schemas"],
        "deep_schemas": size["deep_schemas"],
        "long_prompts": size["long_prompts"],
        "duplicate_fields": duplicate_fields,
        "duplicate_steps": duplicate_steps,
        "reshape_chains": reshape_chains,
        "orphan_steps": orphans,
        "scale": scale,
        "limitations": {
            "uncomparable_opaque_output_steps": uncomparable,
            "docx_placeholders": "download-only, no network: never inspectable",
            "synthetic_only_detectors": list(SYNTHETIC_ONLY_DETECTORS),
        },
        "thresholds": {key: THRESHOLDS[key] for key in (
            "MAX_FIELDS_PER_STEP", "MAX_PROMPT_CHARS", "DUP_STEP_OVERLAP",
            "MAX_RESHAPE_CHAIN", "MAX_SCHEMA_DEPTH", "MIN_DUP_STEP_FIELDS",
            "MIN_WHOLESALE_FIELDS") if key in THRESHOLDS},
    }
    for key, value in payload.items():
        out.set(key, value)
    return {"payload": payload,
            "extra_lines": (scale_lines + _usage_lines(usage)
                            + _duplicate_lines(duplicate_fields)
                            + limitation_lines(uncomparable)),
            "model": model}


def _duplicate_lines(clusters: Sequence[Dict[str, Any]]) -> List[str]:
    """A bounded table of the duplicate-field clusters, so the finding lines can
    stay short while the step names stay visible."""
    seen: Dict[Tuple[Any, ...], List[str]] = collections.OrderedDict()
    verdicts: Dict[Tuple[Any, ...], str] = {}
    for row in clusters:
        key = (tuple(row["steps"]), row["kind"])
        seen.setdefault(key, []).append(str(row["field"]))
        verdicts[key] = row["verdict"]
    if not seen:
        return []
    rows = []
    for key, names in seen.items():
        steps = list(key[0])
        rows.append(["%d: %s" % (len(names), ", ".join(names)) if len(names) > 1
                     else names[0],
                     "%d: %s" % (len(steps), ", ".join(steps)) if len(steps) > 2
                     else ", ".join(steps),
                     verdicts[key]])
    rows.sort(key=lambda r: (r[2], r[0]))
    return ["", "fields extracted on more than one step:"] + table(
        rows, headers=["field names", "steps", "verdict"], max_rows=MAX_EXAMPLES * 2)


def _usage_lines(usage: Dict[str, Any]) -> List[str]:
    """A bounded per-step table of the field verdict. The ads workflow carries 425
    fields, so only the worst rows are printed; the rest live in the JSON."""
    totals = usage["totals"]
    # CONTRACT.md amendment B10: the cannot-determine count is reported beside
    # the unread count, never folded into it. A docx template is reachable only
    # by download and these passes have no network, so a field whose only
    # possible consumer is a template can never be resolved either way. An
    # inventory that presented those as a verdict would be worse than one that
    # admits the boundary.
    lines = ["",
             "fields: %d unread (solid), %d cannot determine, out of %d" % (
                 totals[UNUSED], totals[OPAQUE], usage["field_count"]),
             "  breakdown: %d named, %d shown to a person, %d platform-owned" % (
                 totals[USED_NAMED], totals[USED_VISIBLE], totals[OUT_OF_SCOPE]),
             "  cannot determine = handed on wholesale into code, or readable only "
             "by a docx template (not inspectable: no network)"]
    rows = [
        [step, counts.get(UNUSED, 0), counts.get(OPAQUE, 0), sum(counts.values())]
        for step, counts in usage["per_step"].items()
        if counts.get(UNUSED) or counts.get(OPAQUE)
    ]
    rows.sort(key=lambda r: (-r[1], -r[2], r[0]))
    if rows:
        lines.extend(table(rows, headers=["step", "unread", "cannot tell", "fields"],
                           max_rows=MAX_EXAMPLES * 2))
    return lines


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="verify pass 6 (simplify): unused fields, duplicates, size, smells")
    add_common_args(parser, needs_facts=False)
    parser.add_argument("--facts", help="path to facts.json")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test(args.out)
    if not args.facts:
        print("error: --facts is required (or pass --self-test)", file=sys.stderr)
        return 2

    facts = load_facts(args.facts)
    out = PassOutput(PASS_NAME, args.out)
    result = analyze(facts, out)
    out.write()
    return out.print_summary(result["extra_lines"])


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

CORPUS_DIR = "/Users/andrewjeffers/Documents/Work/contractors/workflows"
CORPUS = ("cna_dua_audit_ads", "cna_dua_audit_lpl", "submission_intake", "quantum_cny")
EXAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "..", "reference", "examples",
                       "submission_intake_es_umbrella.json")

_failures: List[str] = []


def _check(condition: bool, message: str) -> None:
    if condition:
        print("  ok    %s" % message)
    else:
        print("  FAIL  %s" % message)
        _failures.append(message)


def _build_facts(workflow_path: str, out_dir: str) -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(out_dir, exist_ok=True)
    proc = subprocess.run(
        [sys.executable, os.path.join(here, "wf_facts.py"), workflow_path,
         "--out", out_dir],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    facts_path = os.path.join(out_dir, "facts.json")
    if proc.returncode not in (0, 1) or not os.path.exists(facts_path):
        raise SystemExit(
            "cannot self-test: pass 0 did not produce facts for %s (exit %d). This is "
            "wf_facts.py failing, not redundancy.py. Its output was:\n%s"
            % (workflow_path, proc.returncode,
               proc.stdout.decode("utf-8", "replace")[-2000:]))
    return facts_path


def _run(facts_path: str, out_dir: str) -> Tuple[PassOutput, Dict[str, Any]]:
    facts = load_facts(facts_path)
    out = PassOutput(PASS_NAME, out_dir)
    result = analyze(facts, out)
    return out, result


def _synthetic(steps: Sequence[Dict[str, Any]],
               fields: Sequence[Dict[str, Any]] = (),
               edges: Sequence[Dict[str, Any]] = (),
               code_steps: Optional[Dict[str, Any]] = None,
               adjacency: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
    """A minimal facts.json good enough for one detector.

    Steps get an output schema derived from the fields handed in, so the
    fixtures reach the detectors through `common.comparable_fields()` exactly
    the way real facts do. A fixture that left `output` empty would compare
    empty sets and pass for the wrong reason.
    """
    by_step: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for field in fields:
        by_step[field.get("step")].append(field)

    full_steps = []
    for index, step in enumerate(steps):
        owned = by_step.get(step["name"]) or []
        derived = None
        if owned and "output" not in step:
            leaves: Dict[str, Any] = {}
            for field in owned:
                segments = [s for s in str(field.get("path") or "").split(".")
                            if s and s != "data"]
                node: Dict[str, Any] = {
                    "kind": "cell",
                    "value_type": {"kind": field.get("type") or "string"},
                }
                for segment in reversed(segments[1:]):
                    node = {"kind": "object", "properties": {segment: node}}
                if segments:
                    leaves[segments[0]] = node
            derived = {"kind": "object",
                       "properties": {"data": {"kind": "object", "properties": leaves}}}
        row = {"index": index, "name": step["name"], "type": step.get("type", "custom_step"),
               "display_step": step.get("display_step", False),
               "is_terminal": step.get("is_terminal", False),
               "is_hidden": not step.get("display_step", False),
               "is_manual": False, "output_type": step.get("output_type"),
               "prompt_chars": step.get("prompt_chars", 0), "prompts": [],
               "code_chars": step.get("code_chars", 0), "size_bytes": 100,
               "output": step.get("output", derived
                                   or {"kind": "object", "properties": {}}),
               "declared_output_schema_present": True, "parent_conditions": [],
               "loop_membership": [], "packages": [], "model": None, "code": None,
               "enable_rerun": False, "incremental_behavior": "rerun",
               "start_title": None, "end_title": None, "reasoning_effort": None,
               "verbosity": None}
        full_steps.append(row)
    reverse: Dict[str, List[str]] = collections.defaultdict(list)
    for source, targets in (adjacency or {}).items():
        for target in targets:
            reverse[target].append(source)
    return {
        "schema_version": 1, "pass": "facts", "generated_by": "self-test",
        "counts": {"blockers": 0, "warnings": 0, "notes": 0}, "findings": [],
        "workflow": {"path": "synthetic", "name": "synthetic", "bytes": 0,
                     "step_count": len(full_steps), "options": {},
                     "control_plane_mappings": {}},
        "steps": full_steps, "fields": list(fields), "edges": list(edges),
        "decisions": [], "manual_steps": [], "html_producers": [],
        "code_steps": dict(code_steps or {}),
        "graph": {"adjacency": dict(adjacency or {}), "reverse": dict(reverse),
                  "roots": [], "terminals": [], "cycles": []},
        "counters": {"steps": len(full_steps), "fields": len(fields),
                     "edges": len(edges), "prompt_chars": 0, "code_chars": 0,
                     "schema_leaves": len(fields), "hidden_steps": 0},
    }


def _cell_field(step: str, name: str, ftype: str = "string",
                description: str = "") -> Dict[str, Any]:
    return {"step": step, "path": "data.%s" % name, "value_path": "data.%s.value" % name,
            "name": name, "type": ftype, "title": None, "description": description,
            "allowed_values": [], "is_cell": True, "depth": 2}


def _codes(out: PassOutput) -> Set[str]:
    return {f["code"] for f in out.findings}


def _nested_output(depth: int) -> Dict[str, Any]:
    node: Dict[str, Any] = {"kind": "string"}
    for level in range(depth):
        node = {"kind": "object", "properties": {"l%d" % level: node}}
    return node


def _synthetic_matrix(work_dir: str) -> None:
    print("\nsynthetic cases")

    # 1. unused fields, and the display_step credit that must suppress them.
    facts = _synthetic(
        steps=[{"name": "Hidden Extract", "type": "agentic_extraction"},
               {"name": "Shown Extract", "type": "agentic_extraction", "display_step": True},
               {"name": "Reader", "type": "custom_step", "display_step": True}],
        fields=[_cell_field("Hidden Extract", "wanted"),
                _cell_field("Hidden Extract", "orphaned"),
                _cell_field("Shown Extract", "seen_on_canvas")],
        edges=[{"to_step": "Reader", "to_param": "x", "input_type": "dependency",
                "from_step": "Hidden Extract", "output_attribute": "data.wanted",
                "via": "input_mappings"}])
    out, result = _run_synthetic(facts, work_dir)
    usage = result["payload"]["field_usage"]
    _check("UNUSED_FIELDS" in _codes(out), "UNUSED_FIELDS fires on an unread hidden field")
    _check(usage["totals"][UNUSED] == 1 and usage["unused"][0]["path"] == "data.orphaned",
           "only the unread field is flagged (%s)" % [f["path"] for f in usage["unused"]])
    _check(usage["totals"][USED_VISIBLE] == 1,
           "display_step: true earns its field a used verdict")

    # 1b. the wholesale handoff must not be called unused.
    facts = _synthetic(
        steps=[{"name": "Hidden Extract", "type": "agentic_extraction"},
               {"name": "Code", "type": "custom_step"}],
        fields=[_cell_field("Hidden Extract", "a"), _cell_field("Hidden Extract", "b")],
        edges=[{"to_step": "Code", "to_param": "x", "input_type": "dependency",
                "from_step": "Hidden Extract", "output_attribute": "data",
                "via": "input_mappings"}])
    out, result = _run_synthetic(facts, work_dir)
    _check(result["payload"]["field_usage"]["totals"][OPAQUE] == 2
           and result["payload"]["field_usage"]["totals"][UNUSED] == 0,
           "a wholesale `data` handoff buckets fields as wholesale, never unused")
    _check("FIELDS_READ_WHOLESALE" in _codes(out), "FIELDS_READ_WHOLESALE fires on it")

    # 2. oversized schema, exactly at the boundary and one over.
    limit = THRESHOLDS["MAX_FIELDS_PER_STEP"]
    for count, expected in ((limit, False), (limit + 1, True)):
        facts = _synthetic(
            steps=[{"name": "Wide", "type": "agentic_extraction", "display_step": True}],
            fields=[_cell_field("Wide", "f%d" % i) for i in range(count)])
        out, _ = _run_synthetic(facts, work_dir)
        _check(("OVERSIZED_SCHEMA" in _codes(out)) is expected,
               "OVERSIZED_SCHEMA %s at %d fields (threshold %d)"
               % ("fires" if expected else "silent", count, limit))

    # 2b. nesting depth, measured as B9 pins it: max `fields[].depth`.
    for depth, expected in ((MAX_SCHEMA_DEPTH - 1, False), (MAX_SCHEMA_DEPTH, True)):
        path = ".".join(["data"] + ["l%d" % i for i in range(depth - 1)])
        facts = _synthetic(
            steps=[{"name": "Deep", "type": "agentic_extraction", "display_step": True,
                    "output": _nested_output(depth)}],
            fields=[{"step": "Deep", "path": path, "value_path": path + ".value",
                     "name": "l%d" % max(depth - 2, 0), "type": "string", "title": None,
                     "description": "", "allowed_values": [], "is_cell": True,
                     "depth": depth}])
        out, _ = _run_synthetic(facts, work_dir)
        _check(("DEEP_SCHEMA" in _codes(out)) is expected,
               "DEEP_SCHEMA %s at fields[].depth %d (threshold %d)"
               % ("fires" if expected else "silent", depth, MAX_SCHEMA_DEPTH))

    # 3. prompt length, exactly at the boundary and one over.
    limit = THRESHOLDS["MAX_PROMPT_CHARS"]
    for chars, expected in ((limit, False), (limit + 1, True)):
        facts = _synthetic(steps=[{"name": "Chatty", "type": "agentic_extraction",
                                   "display_step": True, "prompt_chars": chars}])
        out, _ = _run_synthetic(facts, work_dir)
        _check(("LONG_PROMPT" in _codes(out)) is expected,
               "LONG_PROMPT %s at %d chars (threshold %d)"
               % ("fires" if expected else "silent", chars, limit))

    # 4. duplicate fields: same source -> warning, different source -> note.
    same_source_edges = [
        {"to_step": step, "to_param": "docs", "input_type": "dependency",
         "from_step": "Classify", "output_attribute": "documents.Quote",
         "via": "input_mappings"} for step in ("Extract A", "Extract B")]
    facts = _synthetic(
        steps=[{"name": "Classify", "type": "classify_documents", "display_step": True},
               {"name": "Extract A", "type": "agentic_extraction", "display_step": True},
               {"name": "Extract B", "type": "agentic_extraction", "display_step": True}],
        fields=[_cell_field("Extract A", "named_insured", "string", "The insured."),
                _cell_field("Extract A", "policy_number", "string", "Number."),
                _cell_field("Extract B", "named_insured", "string", "The insured."),
                _cell_field("Extract B", "policy_number", "string", "Number.")],
        edges=same_source_edges)
    out, result = _run_synthetic(facts, work_dir)
    dup = result["payload"]["duplicate_fields"]
    _check(len(dup) == 2 and all(d["verdict"] == "possibly accidental" for d in dup),
           "DUPLICATE_FIELD calls same-source re-extraction possibly accidental")
    _check(any(f["severity"] == "warning" for f in out.findings
               if f["code"] == "DUPLICATE_FIELD"),
           "...and raises it at warning severity")

    diff_source_edges = [
        {"to_step": "Extract A", "to_param": "docs", "input_type": "dependency",
         "from_step": "Classify", "output_attribute": "documents.Quote",
         "via": "input_mappings"},
        {"to_step": "Extract B", "to_param": "docs", "input_type": "dependency",
         "from_step": "Classify", "output_attribute": "documents.Binder",
         "via": "input_mappings"}]
    facts = _synthetic(
        steps=[{"name": "Classify", "type": "classify_documents", "display_step": True},
               {"name": "Extract A", "type": "agentic_extraction", "display_step": True},
               {"name": "Extract B", "type": "agentic_extraction", "display_step": True}],
        fields=[_cell_field("Extract A", "named_insured", "string", "The insured."),
                _cell_field("Extract A", "policy_number", "string", "Number."),
                _cell_field("Extract B", "named_insured", "string", "The insured."),
                _cell_field("Extract B", "policy_number", "string", "Number.")],
        edges=diff_source_edges)
    out, result = _run_synthetic(facts, work_dir)
    dup = result["payload"]["duplicate_fields"]
    _check(len(dup) == 2 and all(d["verdict"] == "deliberate" for d in dup),
           "DUPLICATE_FIELD calls different-source re-extraction deliberate")
    _check(all(f["severity"] == "note" for f in out.findings
               if f["code"] == "DUPLICATE_FIELD"), "...at note severity")

    # 5. duplicate steps: the overlap boundary, then same vs different sources.
    floor = THRESHOLDS["DUP_STEP_OVERLAP"]
    at_boundary = _synthetic(
        steps=[{"name": "Feed", "type": "custom_step", "display_step": True},
               {"name": "Extract A", "type": "agentic_extraction", "display_step": True},
               {"name": "Extract B", "type": "agentic_extraction", "display_step": True}],
        fields=[_cell_field("Extract A", n) for n in ("a", "b", "c")]
               + [_cell_field("Extract B", n) for n in ("a", "b", "c", "d")],
        edges=[{"to_step": s, "to_param": "x", "input_type": "dependency",
                "from_step": "Feed", "output_attribute": "out", "via": "input_mappings"}
               for s in ("Extract A", "Extract B")])
    out, result = _run_synthetic(at_boundary, work_dir)
    _check("DUPLICATE_STEP" not in _codes(out),
           "DUPLICATE_STEP silent at overlap exactly %.2f (must be above)" % floor)

    over = _synthetic(
        steps=[{"name": "Feed", "type": "custom_step", "display_step": True},
               {"name": "Extract A", "type": "agentic_extraction", "display_step": True},
               {"name": "Extract B", "type": "agentic_extraction", "display_step": True}],
        fields=[_cell_field("Extract A", n) for n in ("a", "b", "c", "d")]
               + [_cell_field("Extract B", n) for n in ("a", "b", "c", "d")],
        edges=[{"to_step": s, "to_param": "x", "input_type": "dependency",
                "from_step": "Feed", "output_attribute": "out", "via": "input_mappings"}
               for s in ("Extract A", "Extract B")])
    out, result = _run_synthetic(over, work_dir)
    groups = result["payload"]["duplicate_steps"]
    _check(len(groups) == 1 and groups[0]["verdict"] == "possible duplicate",
           "DUPLICATE_STEP fires above the bar and calls identical sources a duplicate")
    _check(any(f["severity"] == "warning" for f in out.findings
               if f["code"] == "DUPLICATE_STEP"), "...at warning severity")

    over_diff = json.loads(json.dumps(over))
    over_diff["edges"][1]["output_attribute"] = "other"
    out, result = _run_synthetic(over_diff, work_dir)
    groups = result["payload"]["duplicate_steps"]
    _check(len(groups) == 1 and groups[0]["verdict"] == "likely deliberate",
           "DUPLICATE_STEP calls different sources likely deliberate")
    _check(all(f["severity"] == "note" for f in out.findings
               if f["code"] == "DUPLICATE_STEP"), "...at note severity")

    # 6. reshape chain, at the boundary and one over.
    limit = THRESHOLDS["MAX_RESHAPE_CHAIN"]
    for length, expected in ((limit, False), (limit + 1, True)):
        names = ["Reshape %d" % i for i in range(length)]
        code_steps = {
            n: {"declared_input_properties": ["data", "schema"],
                "declared_output_properties": ["data", "schema"],
                "is_async": False, "params": [], "has_kwargs": False,
                "returned_keys": ["data", "schema"], "returns_dynamic": False,
                "imports": [], "syntax_ok": True, "parse_error": None} for n in names}
        edges = [{"to_step": names[i], "to_param": "x", "input_type": "dependency",
                  "from_step": names[i - 1], "output_attribute": None,
                  "via": "input_mappings"} for i in range(1, length)]
        facts = _synthetic(
            steps=[{"name": n, "type": "custom_step", "display_step": True,
                    "code_chars": 400} for n in names],
            edges=edges, code_steps=code_steps)
        out, result = _run_synthetic(facts, work_dir)
        _check(("RESHAPE_CHAIN" in _codes(out)) is expected,
               "RESHAPE_CHAIN %s at a %d-step chain (threshold %d)"
               % ("fires" if expected else "silent", length, limit))

    # 7. orphan step, and the three things that excuse one.
    facts = _synthetic(steps=[
        {"name": "Dead End", "type": "custom_step"},
        {"name": "Shown", "type": "custom_step", "display_step": True},
        {"name": "Terminal", "type": "email", "is_terminal": True},
        {"name": "Titler", "type": "custom_step", "output_type": "set_title"}])
    out, result = _run_synthetic(facts, work_dir)
    orphans = [row["step"] for row in result["payload"]["orphan_steps"]]
    _check(orphans == ["Dead End"],
           "ORPHAN_STEP flags only the dead end, excusing displayed / terminal / "
           "set_title steps (got %s)" % orphans)

    # 8. scale report needs no findings of its own.
    facts = _synthetic(
        steps=[{"name": "A", "display_step": True}, {"name": "B", "display_step": True},
               {"name": "C", "display_step": True}],
        adjacency={"A": ["B"], "B": ["C"]})
    out, result = _run_synthetic(facts, work_dir)
    scale = result["payload"]["scale"]
    _check(scale["longest_chain"] == 3 and scale["widest_fan_in"] == 1,
           "scale report measures the chain (%s) and fan-in (%s)"
           % (scale["longest_chain"], scale["widest_fan_in"]))
    _check(not any(f["code"].startswith("SCALE") for f in out.findings),
           "scale report attaches no finding")


def _run_synthetic(facts: Dict[str, Any], work_dir: str) -> Tuple[PassOutput, Dict[str, Any]]:
    out = PassOutput(PASS_NAME, os.path.join(work_dir, "synthetic"))
    return out, analyze(facts, out)


def self_test(out_dir: str) -> int:
    del _failures[:]
    work_dir = out_dir or tempfile.mkdtemp(prefix="simplify-selftest-")
    os.makedirs(work_dir, exist_ok=True)

    workflows: List[Tuple[str, str]] = [
        (name, os.path.join(CORPUS_DIR, "%s.json" % name)) for name in CORPUS]
    example = os.path.normpath(EXAMPLE)
    if os.path.exists(example):
        workflows.append(("submission_intake_es_umbrella", example))

    headline: List[List[Any]] = []
    fired_somewhere: Set[str] = set()
    known_limitation_seen = False
    for name, path in workflows:
        print("\n%s" % name)
        if not os.path.exists(path):
            _check(False, "corpus workflow missing: %s" % path)
            continue
        facts_path = _build_facts(path, os.path.join(work_dir, name))

        out_a, result_a = _run(facts_path, os.path.join(work_dir, name, "a"))
        out_b, result_b = _run(facts_path, os.path.join(work_dir, name, "b"))
        _check(json.dumps(result_a["payload"], sort_keys=True, default=str)
               == json.dumps(result_b["payload"], sort_keys=True, default=str),
               "two runs over the same facts agree exactly")

        counts = out_a.counts()
        _check(counts["blockers"] == 0, "no blockers (this pass emits none)")

        facts = load_facts(facts_path)
        model = Model(facts)
        classified = classify_fields(model)
        _check(len(classified) == len(facts.get("fields", [])),
               "every field in facts.json gets exactly one verdict (%d)" % len(classified))
        buckets = collections.Counter(row["bucket"] for row in classified)
        _check(set(buckets) <= set(BUCKETS),
               "no verdict outside the declared buckets (%s)" % sorted(buckets))
        _check(sum(buckets.values()) == len(classified),
               "verdicts partition the field list")
        used = {(r["step"], r["path"]) for r in classified
                if r["bucket"] in (USED_NAMED, USED_VISIBLE)}
        unused = {(r["step"], r["path"]) for r in classified if r["bucket"] == UNUSED}
        _check(not (used & unused), "no field is counted both used and unused")
        usage = result_a["payload"]["field_usage"]
        _check(len(usage["unused"]) == buckets.get(UNUSED, 0)
               and len(usage["read_wholesale"]) == buckets.get(OPAQUE, 0),
               "payload lists match the bucket counts")

        # B2: no envelope key may reach an overlap score. If `fields[]` starts
        # carrying them this fails loudly and points at comparable_fields().
        leaked = sorted({
            path for paths in model.comparable.values() for path in paths
            if path.split(".")[0] in ENVELOPE_ROOTS})
        _check(not leaked,
               "no envelope key reaches an overlap score (%s)" % (leaked or "clean"))
        envelope_inventory = sorted({
            path for paths in model.step_field_paths.values() for path in paths
            if path.split(".")[0] in ENVELOPE_ROOTS})
        _check(True, "envelope-rooted entries in fields[]: %d%s" % (
            len(envelope_inventory),
            " — stripping changes no score" if envelope_inventory else ""))

        # graph.sinks is the authority on "nothing reads this", and terminals is
        # a different question. An extraction-only workflow has zero terminals
        # and many real endpoints, so the two must not be conflated.
        sinks = set(facts.get("graph", {}).get("sinks") or [])
        computed = {n for n in model.steps if not model.edges_from.get(n)}
        _check(sinks == computed,
               "graph.sinks agrees with the unconsumed set (%d vs %d)"
               % (len(sinks), len(computed)))
        terminals = set(facts.get("graph", {}).get("terminals") or [])
        orphans = {row["step"] for row in result_a["payload"]["orphan_steps"]}
        _check(not (orphans & (sinks - computed | terminals)),
               "no terminal or delivered endpoint is called an orphan")
        _check(orphans <= sinks,
               "every orphan is a sink (%s)" % (sorted(orphans - sinks) or "yes"))

        # flow_check and this pass must agree that a platform-consumed output is
        # not an unread one. Asserted, not observed.
        platform_consumed = {
            n for n, s in model.steps.items()
            if s.get("output_type") in PLATFORM_CONSUMED_OUTPUT_TYPES}
        flagged = {row["step"] for row in result_a["payload"]["field_usage"]["unused"]}
        _check(not (platform_consumed & (flagged | orphans)),
               "no set_title / update_workflow_log step is flagged unread or orphan "
               "(%s)" % (sorted(platform_consumed) or "none present"))

        # Thresholds honored exactly, checked against the facts themselves.
        expected_wide = sorted(
            n for n in model.steps
            if model.step_field_count.get(n, 0) > THRESHOLDS["MAX_FIELDS_PER_STEP"]
            and not model.out_of_scope_reason(n))
        reported_wide = sorted(r["step"] for r in result_a["payload"]["oversized_schemas"])
        _check(expected_wide == reported_wide,
               "OVERSIZED_SCHEMA matches the >%d rule exactly (%d steps)"
               % (THRESHOLDS["MAX_FIELDS_PER_STEP"], len(reported_wide)))
        expected_deep = sorted(
            n for n in model.steps
            if model.authored_depth.get(n, 0) >= MAX_SCHEMA_DEPTH
            and not model.out_of_scope_reason(n))
        reported_deep = sorted(r["step"] for r in result_a["payload"]["deep_schemas"])
        _check(expected_deep == reported_deep,
               "DEEP_SCHEMA matches fields[].depth >= %d exactly (%s)"
               % (MAX_SCHEMA_DEPTH, reported_deep or "none"))
        _check(max(model.authored_depth.values() or [0]) <= 6,
               "authored depth stays within B9's measured corpus range (max %d)"
               % max(model.authored_depth.values() or [0]))

        expected_long = sorted(
            n for n, s in model.steps.items()
            if (s.get("prompt_chars") or 0) > THRESHOLDS["MAX_PROMPT_CHARS"]
            and not model.out_of_scope_reason(n))
        reported_long = sorted(r["step"] for r in result_a["payload"]["long_prompts"])
        _check(expected_long == reported_long,
               "LONG_PROMPT matches the >%d rule exactly (%d steps)"
               % (THRESHOLDS["MAX_PROMPT_CHARS"], len(reported_long)))
        for group in result_a["payload"]["duplicate_steps"]:
            _check((group["min_overlap"] or 0) > THRESHOLDS["DUP_STEP_OVERLAP"],
                   "duplicate-step group %s is strictly above the %.2f bar"
                   % (truncate(", ".join(group["steps"]), 50),
                      THRESHOLDS["DUP_STEP_OVERLAP"]))
        for chain in result_a["payload"]["reshape_chains"]:
            if chain["over_threshold"]:
                _check(chain["length"] > THRESHOLDS["MAX_RESHAPE_CHAIN"],
                       "reported reshape chain is longer than %d"
                       % THRESHOLDS["MAX_RESHAPE_CHAIN"])

        # Bounded print, measured on the real thing.
        out_a.write()
        printed = _capture(out_a, result_a["extra_lines"])
        _check(printed[-1].startswith("SUMMARY %s " % PASS_NAME),
               "SUMMARY is the last line printed")
        # The SUMMARY and "... N more" lines carry an absolute out path whose
        # length belongs to the caller, not to this pass, so measure the rest.
        content = [line for line in printed
                   if not line.startswith("SUMMARY ") and not line.startswith("... ")]
        widest = max(len(line) for line in content)
        _check(widest <= 220,
               "no printed content line over 220 chars (widest %d)" % widest)
        _check(len(printed) <= 400,
               "printed output stays under 400 lines (%d)" % len(printed))
        finding_lines = sum(1 for line in printed
                            if line[:7].strip() in ("BLOCKER", "WARNING", "NOTE"))
        _check(finding_lines <= THRESHOLDS["MAX_FINDINGS_PRINTED"],
               "at most %d finding rows printed (%d)"
               % (THRESHOLDS["MAX_FINDINGS_PRINTED"], finding_lines))

        fired_somewhere.update(f["code"] for f in out_a.findings)
        _check(set(f["code"] for f in out_a.findings) <= set(ALL_CODES),
               "every emitted code is declared in ALL_CODES")
        if result_a["payload"]["limitations"]["uncomparable_opaque_output_steps"]:
            known_limitation_seen = True

        totals = usage["totals"]
        headline.append([name, totals[UNUSED], totals[OPAQUE], totals[USED_NAMED],
                         totals[USED_VISIBLE], totals[OUT_OF_SCOPE],
                         usage["field_count"]])

    # The synthetic-only list is maintained by this assertion, both directions,
    # so it cannot rot: a detector that starts firing on the corpus must be
    # removed from it, and one that stops firing must be added.
    print("\ncorpus coverage")
    never_fired = sorted(set(ALL_CODES) - fired_somewhere)
    _check(never_fired == sorted(SYNTHETIC_ONLY_DETECTORS),
           "SYNTHETIC_ONLY_DETECTORS matches the corpus exactly (never fired: %s, "
           "declared: %s)" % (never_fired or "none", list(SYNTHETIC_ONLY_DETECTORS)))
    _check(known_limitation_seen,
           "the opaque-output limitation is reported where it applies "
           "(es_umbrella Guideline Check trio)")

    _synthetic_matrix(work_dir)

    print("\nper-workflow field verdicts")
    for line in table(headline,
                      headers=["workflow", "unread", "wholesale", "named", "shown",
                               "platform", "total"], max_rows=len(headline) or 1):
        print(line)

    if _failures:
        print("\nSELF-TEST FAILED: %d check(s)" % len(_failures))
        for message in _failures:
            print("  - %s" % message)
        return 2
    print("\nself-test ok")
    return 0


def _capture(out: PassOutput, extra_lines: Sequence[str]) -> List[str]:
    import io
    import contextlib
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        out.print_summary(list(extra_lines))
    return buffer.getvalue().rstrip("\n").split("\n")


if __name__ == "__main__":
    sys.exit(main())
