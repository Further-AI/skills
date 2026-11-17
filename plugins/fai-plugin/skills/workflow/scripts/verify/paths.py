#!/usr/bin/env python3
"""paths.py — pass 3: decision lattice, reachability, termination, manual gates.

Reads facts.json (never workflow.json) and answers: which steps can actually
run, under what condition set, how many distinguishable outcomes the workflow
has, and whether the human gates on those paths earn their keep.

The execution model is transcribed from step_types/decision.md. The four rules
that matter, because getting any of them wrong produces confidently wrong
output:

  1. EVERY case is evaluated — a decision is not a switch with a break, so
     several branches can fire concurrently from one decision.
  2. If no case matches, `default_branch` fires when it is a non-empty string.
     When it is "", nothing fires and every step downstream goes
     BRANCH_SKIPPED. That is intentional in gate patterns.
  3. A branch value is the NAME OF A BRANCH-ROOT STEP, not an arbitrary label.
     `parent_conditions` are stamped at save time by walking each branch root
     through the dependency graph.
  4. For a step reached by several branches: for each decision in
     `parent_conditions`, at least one of its listed branches must be selected.
     AND across decisions, OR within one decision.

Rule 2 also makes the default branch mutually exclusive with every case
branch — the default only fires when nothing matched — which is what makes a
diamond join provably unsatisfiable in the AND-out case.

Conservatism: a false unreachability claim is the worst output this pass can
produce, so reachability is computed twice. The LENIENT model assumes every
case can be true; the STRICT model drops cases this pass believes impossible.
Only unreachability under the lenient model with a complete world enumeration
is ever a blocker. Anything resting on an inference, or on a truncated
enumeration, is a warning.

stdlib only. Python 3.9 compatible.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import (  # noqa: E402
    THRESHOLDS,
    PassOutput,
    finding,
    load_facts,
    table,
    truncate,
    type_name,
)

#: CONTRACT.md "Notes on producing it" — the implicit source step. It is a
#: legal from_step and is not in facts["steps"].
DISPATCHER = "Workflow Dispatcher"

#: The entry step of every corpus workflow, pinned so a change to how
#: wf_facts.py records wiring cannot quietly move the reachability baseline.
#: All five currently reach every step from a single entry; a bare-`dependencies`
#: step that carried no edge would show up here as an extra entry.
CORPUS_ENTRIES = {
    "cna_dua_audit_ads": ["Prepare Documents"],
    "cna_dua_audit_lpl": ["Prepare Documents"],
    "quantum_cny": ["Prepare Documents"],
    "submission_intake": ["Prepare Documents"],
    "submission_intake_es_umbrella": ["Prepare Documents"],
}

#: Implementation guards, deliberately not in THRESHOLDS: these bound the
#: search, they are not policy anyone tunes.
MAX_WORLDS = 2000          # hard cap on enumerated decision worlds
MAX_SUBSET_BRANCHES = 5    # enumerate all 2^n-1 concurrent-branch subsets up to n
MAX_EVIDENCE_LIST = 12     # cap any list stashed in a finding's evidence
MAX_PATHS_PERSISTED = 500  # rows of the full matrix written to disk

#: step_types/decision.md "Operator notes".
NUMERIC_OPS = frozenset(("gt", "gte", "lt", "lte"))
DATE_OPS = frozenset(("after", "before"))
UNARY_OPS = frozenset(("is_true", "is_false", "is_empty", "is_not_empty"))
MEMBERSHIP_OPS = frozenset(("contains", "not_contains", "starts_with", "ends_with"))
EQUALITY_OPS = frozenset(("eq", "neq"))

#: A slash-formatted date literal, which step_types/decision.md warns is
#: locale-dependent under after/before.
_AMBIGUOUS_DATE = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$")

#: Kinds that are dict- or list-shaped at runtime, so a scalar `eq` against
#: them can never be true.
_CONTAINER_KINDS = frozenset(("object", "array", "table", "cell", "file",
                              "knowledge_base", "video_file", "email_headers",
                              "citation", "docx_image", "sov_field"))

#: Kinds gt/gte/lt/lte cannot be applied to. step_types/decision.md:
#: "gt/gte/lt/lte are numeric — wiring a string source through them is a
#: runtime crash, not a save-time error."
_NON_NUMERIC_KINDS = frozenset(("string", "boolean")) | _CONTAINER_KINDS


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def _group_conditions(parent_conditions: Sequence[Dict[str, Any]]
                      ) -> "Dict[str, List[str]]":
    """parent_conditions -> {decision_step: [branch, ...]}, order preserved.

    step_types/decision.md: AND across decisions, OR within one decision's
    listed branches.
    """
    grouped: Dict[str, List[str]] = {}
    for entry in parent_conditions or []:
        if not isinstance(entry, dict):
            continue
        decision = entry.get("decision_step")
        branch = entry.get("branch")
        if not isinstance(decision, str) or not decision:
            continue
        grouped.setdefault(decision, [])
        if isinstance(branch, str) and branch and branch not in grouped[decision]:
            grouped[decision].append(branch)
    return grouped


def _condition_expr(grouped: "Dict[str, List[str]]") -> str:
    """Human-readable gate expression: "D1[a|b] AND D2[c]"."""
    if not grouped:
        return "(unconditional)"
    return " AND ".join(
        "%s[%s]" % (decision, "|".join(branches) if branches else "-")
        for decision, branches in grouped.items())


def _cap(values: Iterable[Any], limit: int = MAX_EVIDENCE_LIST) -> List[Any]:
    listed = list(values)
    return listed[:limit]


def _first_segment(path: Optional[str]) -> Optional[str]:
    if not isinstance(path, str) or not path:
        return None
    return path.split(".")[0]


# --------------------------------------------------------------------------- #
# The analysis
# --------------------------------------------------------------------------- #

class PathAnalysis:
    """Everything pass 3 knows about one workflow."""

    def __init__(self, facts: Dict[str, Any]) -> None:
        self.facts = facts
        self.findings: List[Dict[str, Any]] = []

        self.steps: List[Dict[str, Any]] = facts.get("steps") or []
        self.by_name: Dict[str, Dict[str, Any]] = {s["name"]: s for s in self.steps}
        self.names: List[str] = [s["name"] for s in self.steps]
        self.nameset: Set[str] = set(self.names)

        graph = facts.get("graph") or {}
        self.adjacency: Dict[str, List[str]] = graph.get("adjacency") or {}
        self.reverse: Dict[str, List[str]] = graph.get("reverse") or {}
        self.cycles: List[List[str]] = graph.get("cycles") or []
        self.terminals: List[str] = [n for n in (graph.get("terminals") or [])
                                     if n in self.nameset]

        self.edges: List[Dict[str, Any]] = facts.get("edges") or []
        self.decisions: List[Dict[str, Any]] = facts.get("decisions") or []
        self.decisions_by_name: Dict[str, Dict[str, Any]] = {
            d["step"]: d for d in self.decisions if isinstance(d.get("step"), str)}
        self.manual_steps: List[Dict[str, Any]] = facts.get("manual_steps") or []

        self.conditions: Dict[str, Dict[str, List[str]]] = {
            s["name"]: _group_conditions(s.get("parent_conditions"))
            for s in self.steps}

        self._index_operands()
        self._graph_reachability()

        # Case impossibility must be decided before worlds are enumerated: the
        # strict model drops impossible cases.
        self.impossible_cases: Dict[Tuple[str, int], str] = {}
        self._analyse_cases()

        self.decision_order = self._decision_topo_order()

        self.lenient = self._enumerate(strict=False)
        self.strict = self._enumerate(strict=True)

    # -- indexing --------------------------------------------------------- #

    def _index_operands(self) -> None:
        """Map decision operands to the resolved schema wf_facts already found.

        Two wiring styles, both live (step_types/decision.md "Two equivalent
        ways to read upstream values inside a condition"):
          - variable: declared in config.input_schema, wired in input_mappings
          - inline dependency: referenced inside the comparison's operand while
            input_mappings stays []. wf_facts records these as edges with
            via="decision_operand".
        """
        self.var_schema: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.operand_schema: Dict[Tuple[str, str, Optional[str]], Dict[str, Any]] = {}
        for edge in self.edges:
            to_step = edge.get("to_step")
            if to_step not in self.decisions_by_name:
                continue
            schema = edge.get("resolved_schema")
            if not isinstance(schema, dict):
                continue
            if edge.get("via") == "decision_operand":
                key = (to_step, edge.get("from_step"), edge.get("output_attribute"))
                self.operand_schema.setdefault(key, schema)
            elif edge.get("input_type") == "dependency":
                param = edge.get("to_param")
                if isinstance(param, str):
                    self.var_schema.setdefault((to_step, param), schema)

    def _operand_schema_for(self, decision: str,
                            operand: Optional[Dict[str, Any]]
                            ) -> Tuple[Optional[Dict[str, Any]], str]:
        """(schema or None, display label) for one comparison operand."""
        if not isinstance(operand, dict):
            return None, "(unparsed operand)"
        kind = operand.get("kind")
        if kind == "variable":
            ref = operand.get("ref")
            label = "variable %s" % ref
            schema = self.var_schema.get((decision, ref))
            if schema is None:
                # Fall back to the type_name string wf_facts recorded.
                dec = self.decisions_by_name.get(decision) or {}
                info = (dec.get("variables") or {}).get(ref) or {}
                resolved = info.get("resolved_type")
                if isinstance(resolved, str) and resolved:
                    schema = {"kind": resolved.split("<")[0]}
            return schema, label
        if kind == "dependency":
            step = operand.get("operand_step")
            attribute = operand.get("output_attribute")
            label = "%s.%s" % (step, attribute)
            return self.operand_schema.get((decision, step, attribute)), label
        if kind == "static":
            return None, "static %r" % (operand.get("value"),)
        return None, "%s operand" % (kind or "unknown")

    # -- graph ------------------------------------------------------------ #

    def _graph_reachability(self) -> None:
        """Steps reachable through the dependency graph, ignoring conditions.

        Entry steps are those with no in-workflow predecessor. A step wired
        only through the implicit `Workflow Dispatcher`, or through legacy bare
        `dependencies` (which carry no edge in facts), therefore counts as an
        entry rather than an orphan — that is deliberate, it is exactly the
        false-positive this pass must not produce.
        """
        self.entries: List[str] = [
            name for name in self.names
            if not [p for p in (self.reverse.get(name) or [])
                    if p in self.nameset and p != name]]
        seen: Set[str] = set()
        stack = list(self.entries)
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            for child in self.adjacency.get(node) or []:
                if child in self.nameset and child not in seen:
                    stack.append(child)
        self.graph_reachable: Set[str] = seen

        self.descendants: Dict[str, Set[str]] = {}
        for name in self.names:
            self.descendants[name] = self._descendants_of(name)

        # Steps nothing reads and which are not a terminal output type. Common
        # and legitimate (display-only extractions, decisions, title setters),
        # so this is payload data, never a finding.
        self.sinks: List[str] = [
            s["name"] for s in self.steps
            if not s.get("is_terminal")
            and not [c for c in (self.adjacency.get(s["name"]) or [])
                     if c in self.nameset]]

    def _descendants_of(self, start: str) -> Set[str]:
        seen: Set[str] = set()
        stack = [c for c in (self.adjacency.get(start) or []) if c in self.nameset]
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            for child in self.adjacency.get(node) or []:
                if child in self.nameset and child not in seen:
                    stack.append(child)
        return seen

    def _decision_topo_order(self) -> List[str]:
        """Decisions in dependency order, so a nested decision is evaluated
        after the decision that gates it."""
        order: List[str] = []
        seen: Set[str] = set()
        temp: Set[str] = set()

        def visit(node: str) -> None:
            if node in seen or node in temp or node not in self.nameset:
                return
            temp.add(node)
            for parent in self.reverse.get(node) or []:
                if parent in self.nameset:
                    visit(parent)
            temp.discard(node)
            seen.add(node)
            order.append(node)

        for name in self.names:
            visit(name)
        ordered = [n for n in order if n in self.decisions_by_name]
        # Any decision the DFS could not place (cycle member) still has to be
        # evaluated; append it so the model stays total.
        ordered += [n for n in self.decisions_by_name if n not in ordered]
        return ordered

    # -- cases ------------------------------------------------------------ #

    def _analyse_cases(self) -> None:
        """Per-comparison checks. Only genuinely provable impossibility marks a
        case impossible; type risks are reported but never narrow the model."""
        for dec in self.decisions:
            name = dec["step"]
            for case in dec.get("cases") or []:
                groups = case.get("condition") or []
                branch = case.get("branch")

                if not groups:
                    self.findings.append(finding(
                        "EMPTY_CONDITION", "warning",
                        "Decision case has no condition",
                        "Case %s (branch '%s') on %s declares an empty condition. "
                        "step_types/decision.md does not define the truth value of "
                        "an empty condition list, so this pass treats the case as "
                        "possibly-true and verifies nothing about it."
                        % (case.get("index"), branch, name),
                        fix="Give the case a condition, or delete it and rely on "
                            "default_branch.",
                        step=name, evidence={"case": case.get("index"), "branch": branch}))
                    continue

                group_impossible: List[Optional[str]] = []
                for group_index, group in enumerate(groups):
                    if not group:
                        self.findings.append(finding(
                            "EMPTY_AND_GROUP", "note",
                            "Decision case has an empty AND group",
                            "Case %s group %d on %s is an empty list. An AND of "
                            "nothing is vacuously true, so this case always fires "
                            "and every later case's branch is effectively "
                            "unreachable through the default."
                            % (case.get("index"), group_index, name),
                            fix="Remove the empty group.",
                            step=name,
                            evidence={"case": case.get("index"), "group": group_index}))
                        group_impossible.append(None)
                        continue
                    reasons = [self._check_comparison(name, case, group_index,
                                                      position, comparison)
                               for position, comparison in enumerate(group)]
                    hard = [r for r in reasons if r]
                    # Inner list is AND: one impossible comparison kills the group.
                    group_impossible.append(hard[0] if hard else None)

                # Outer list is OR: every group must be impossible for the case
                # to be impossible.
                if group_impossible and all(r is not None for r in group_impossible):
                    self.impossible_cases[(name, case.get("index"))] = group_impossible[0] or ""

    def _variable_defect(self, decision: str, ref: Optional[str]
                         ) -> Optional[Tuple[bool, bool, str]]:
        """(declared, wired, note) when a condition variable is dead, else None."""
        if not isinstance(ref, str) or not ref:
            return None
        dec = self.decisions_by_name.get(decision) or {}
        declared = ref in (dec.get("input_schema_properties") or [])
        if not declared:
            return (False, False,
                    "is not declared in config.input_schema (declared: %s)"
                    % (", ".join(_cap(dec.get("input_schema_properties") or [], 8))
                       or "nothing"))
        info = (dec.get("variables") or {}).get(ref) or {}
        if not info.get("wired"):
            return (True, False, "is declared but has no input_mappings entry")
        return None

    def _check_comparison(self, decision: str, case: Dict[str, Any],
                          group_index: int, position: int,
                          comparison: Dict[str, Any]) -> Optional[str]:
        """Return an impossibility reason, or None. Emits findings as it goes."""
        if not isinstance(comparison, dict):
            return None
        operator = comparison.get("op")
        left = comparison.get("left") or {}
        right = comparison.get("right") or {}
        schema, label = self._operand_schema_for(decision, left)
        where = "case %s, comparison %d.%d" % (case.get("index"), group_index, position)
        branch = case.get("branch")
        base_evidence = {
            "case": case.get("index"), "branch": branch, "operator": operator,
            "left": label, "group": group_index, "position": position,
        }

        # -- a condition variable that resolves to nothing at runtime.
        # step_types/decision.md: "Variables in condition operands must be
        # declared in config.input_schema — otherwise no save-time check fires
        # and the runtime lookup returns None, making every comparison silently
        # false." The same is true of a declared-but-unwired variable: the
        # save-time required-mapping check only rejects when required is True.
        if left.get("kind") == "variable":
            reason = self._variable_defect(decision, left.get("ref"))
            if reason:
                declared, wired, note = reason
                self.findings.append(finding(
                    "CONDITION_VARIABLE_DEAD", "blocker",
                    "Decision condition reads a variable that is always None",
                    "%s on %s reads variable '%s', which %s. The runtime lookup "
                    "returns None, so this comparison is silently false on every "
                    "run and branch '%s' never fires."
                    % (where, decision, left.get("ref"), note, branch),
                    fix=("Declare '%s' in config.input_schema and wire it in "
                         "input_mappings." % left.get("ref")) if not declared else
                        ("Add an input_mappings entry with "
                         "input_parameter_name '%s'." % left.get("ref")),
                    step=decision,
                    evidence=dict(base_evidence, declared=declared, wired=wired),
                    group_key="CONDITION_VARIABLE_DEAD:%s" % decision))
                return "variable '%s' %s" % (left.get("ref"), note)

        if not isinstance(schema, dict):
            return None  # no evidence, no claim
        kind = schema.get("kind", "unknown")
        if kind in ("unknown", "any"):
            return None  # CONTRACT.md: never raise on our own ignorance

        allowed = schema.get("allowed_values") or []
        right_static = right.get("kind") == "static"
        right_value = right.get("value") if right_static else None

        # -- the missing .value bug. reference/input_mappings.md: extraction
        # leaves are cell-wrapped, so `data.<f>` is the whole cell dict and
        # `data.<f>.value` is the value.
        if kind == "cell":
            if operator in UNARY_OPS:
                detail = ("%s on %s compares the whole cell dict, which is always "
                          "truthy and never empty, so `%s` is a constant."
                          % (where, label, operator))
            else:
                detail = ("%s compares %s, a cell, against a scalar. A cell is the "
                          "dict {value, confidence_score, confidence_reason, "
                          "citations, thinking_steps}; it never equals a scalar."
                          % (where, label))
            self.findings.append(finding(
                "OPERAND_MISSING_VALUE", "warning",
                "Decision operand reads a cell, not its value",
                "%s Decision %s." % (detail, decision),
                fix="Append `.value` to the output_attribute (%s.value)." % label,
                step=decision, evidence=dict(base_evidence, left_type=type_name(schema)),
                group_key="OPERAND_MISSING_VALUE:%s" % decision))
            if operator in EQUALITY_OPS or operator in MEMBERSHIP_OPS \
                    or operator in NUMERIC_OPS:
                return "operand is a cell dict, never equal to a scalar"
            return None

        # -- numeric operator on a non-numeric source: a runtime crash, per
        # step_types/decision.md. Not impossible — a crash, so the model keeps
        # the case satisfiable and this stays a warning.
        if operator in NUMERIC_OPS and kind in _NON_NUMERIC_KINDS:
            self.findings.append(finding(
                "COMPARISON_TYPE_RISK", "warning",
                "Numeric comparison on a non-numeric operand",
                "%s on %s applies `%s` to %s. step_types/decision.md: "
                "gt/gte/lt/lte are numeric — wiring a non-numeric source through "
                "them is a runtime crash, not a save-time error."
                % (where, decision, operator, type_name(schema)),
                fix="Normalize the value to a number in an upstream custom_step, "
                    "or use after/before for dates.",
                step=decision, evidence=dict(base_evidence, left_type=type_name(schema)),
                group_key="COMPARISON_TYPE_RISK:%s" % decision))
            return None

        # -- ambiguous literal date. step_types/decision.md: "after / before
        # parse both operands as dates — strings are fine, but ambiguous dates
        # ('01/02/2026') follow the default locale."
        if operator in DATE_OPS and isinstance(right_value, str) \
                and _AMBIGUOUS_DATE.match(right_value.strip()):
            self.findings.append(finding(
                "AMBIGUOUS_DATE_LITERAL", "note",
                "Date comparison against an ambiguous literal",
                "%s on %s compares against the literal %r. step_types/decision.md: "
                "after/before parse both operands as dates, and a slash-formatted "
                "date follows the default locale — %r is read as month-first or "
                "day-first depending on where the runtime thinks it is."
                % (where, decision, right_value, right_value),
                fix="Use an unambiguous ISO literal (YYYY-MM-DD).",
                step=decision, evidence=dict(base_evidence, right=right_value),
                group_key="AMBIGUOUS_DATE_LITERAL:%s" % decision))

        # -- eq against a value outside a declared enum: provably never true.
        if allowed and operator in EQUALITY_OPS and isinstance(right_value, str) \
                and right_value not in allowed:
            if operator == "eq":
                self.findings.append(finding(
                    "IMPOSSIBLE_CASE", "warning",
                    "Decision case can never be true",
                    "%s on %s compares %s (allowed_values: %s) with `eq` against "
                    "%r, which is not in that set. Branch '%s' can never fire "
                    "through this comparison."
                    % (where, decision, label, ", ".join(_cap(allowed, 8)),
                       right_value, branch),
                    fix="Use one of the declared values, or widen the upstream enum.",
                    step=decision,
                    evidence=dict(base_evidence, allowed_values=_cap(allowed),
                                  right=right_value),
                    group_key="IMPOSSIBLE_CASE:%s" % decision))
                return ("`eq` against %r, outside allowed_values %s"
                        % (right_value, _cap(allowed, 6)))
            self.findings.append(finding(
                "ALWAYS_TRUE_CASE", "note",
                "Decision comparison is always true",
                "%s on %s compares %s (allowed_values: %s) with `neq` against %r, "
                "which is not in that set, so the comparison is a constant true."
                % (where, decision, label, ", ".join(_cap(allowed, 8)), right_value),
                fix="Drop the comparison, or compare against a declared value.",
                step=decision,
                evidence=dict(base_evidence, allowed_values=_cap(allowed)),
                group_key="ALWAYS_TRUE_CASE:%s" % decision))
            return None

        # -- boolean eq against a string: Python never equates them.
        if kind == "boolean" and operator == "eq" and isinstance(right_value, str):
            self.findings.append(finding(
                "IMPOSSIBLE_CASE", "warning",
                "Boolean operand compared with a string",
                "%s on %s compares %s (boolean) with `eq` against the string %r. "
                "A boolean never equals a string, so branch '%s' can never fire "
                "through this comparison."
                % (where, decision, label, right_value, branch),
                fix="Use `is_true` / `is_false`, which ignore right_operand.",
                step=decision, evidence=dict(base_evidence, right=right_value),
                group_key="IMPOSSIBLE_CASE:%s" % decision))
            return "boolean operand compared by `eq` against the string %r" % right_value

        # -- container eq against a scalar.
        if kind in _CONTAINER_KINDS and operator in EQUALITY_OPS and right_static \
                and not isinstance(right_value, (dict, list)):
            if operator == "eq":
                self.findings.append(finding(
                    "IMPOSSIBLE_CASE", "warning",
                    "Container operand compared with a scalar",
                    "%s on %s compares %s (%s) with `eq` against the scalar %r. "
                    "Branch '%s' can never fire through this comparison."
                    % (where, decision, label, type_name(schema), right_value, branch),
                    fix="Compare a scalar field inside the container, or use "
                        "is_empty / is_not_empty.",
                    step=decision,
                    evidence=dict(base_evidence, left_type=type_name(schema),
                                  right=right_value),
                    group_key="IMPOSSIBLE_CASE:%s" % decision))
                return "%s compared by `eq` against the scalar %r" % (
                    type_name(schema), right_value)
        return None

    # -- worlds ----------------------------------------------------------- #

    def _branch_exists(self, branch: Optional[str]) -> bool:
        return isinstance(branch, str) and branch in self.nameset

    def _decision_outcomes(self, dec: Dict[str, Any], strict: bool
                           ) -> Tuple[List[FrozenSet[str]], bool]:
        """Every branch-set this decision could emit, plus an approx flag.

        step_types/decision.md: all cases evaluate, so any non-empty subset of
        the satisfiable cases can fire together. The default fires only when
        NOTHING matched, which makes it mutually exclusive with every case
        branch — that exclusion is what proves an AND-out diamond.
        """
        name = dec["step"]
        usable: List[str] = []
        for case in dec.get("cases") or []:
            branch = case.get("branch")
            if not case.get("branch_step_exists") or not isinstance(branch, str) \
                    or not branch:
                continue  # a dead branch stamps nothing, so it selects nothing
            if strict and (name, case.get("index")) in self.impossible_cases:
                continue
            if branch not in usable:
                usable.append(branch)

        approx = False
        outcomes: List[FrozenSet[str]] = []
        if usable:
            if len(usable) <= MAX_SUBSET_BRANCHES:
                for size in range(1, len(usable) + 1):
                    for combo in itertools.combinations(usable, size):
                        outcomes.append(frozenset(combo))
            else:
                approx = True
                outcomes.extend(frozenset((branch,)) for branch in usable)
                outcomes.append(frozenset(usable))

        default = dec.get("default_branch")
        if isinstance(default, str) and default:
            outcomes.append(frozenset((default,)))
        else:
            outcomes.append(frozenset())

        deduped: List[FrozenSet[str]] = []
        for outcome in outcomes:
            if outcome not in deduped:
                deduped.append(outcome)
        return deduped, approx

    def _enumerate(self, strict: bool) -> Dict[str, Any]:
        """Enumerate decision worlds and the step set that runs in each."""
        per_decision: "Dict[str, List[FrozenSet[str]]]" = {}
        approx = False
        # The exact number of distinguishable outcome combinations, computed
        # before any reduction so the report can quote the real figure. Every
        # case evaluates independently, so a decision with n live branches has
        # 2^n - 1 non-empty subsets plus the nothing-matched outcome = 2^n.
        total_worlds = 1
        for name in self.decision_order:
            outcomes, is_approx = self._decision_outcomes(
                self.decisions_by_name[name], strict)
            per_decision[name] = outcomes
            approx = approx or is_approx
            total_worlds *= max(1, 2 ** self._live_branch_count(
                self.decisions_by_name[name], strict))

        total = 1
        for outcomes in per_decision.values():
            total *= max(1, len(outcomes))

        truncated = False
        if total > MAX_WORLDS:
            # Fall back to singletons + all-together + nothing-matched. Enough
            # to decide "is there a world where this step runs", which is the
            # only question reachability asks.
            reduced: "Dict[str, List[FrozenSet[str]]]" = {}
            for name, outcomes in per_decision.items():
                if len(outcomes) <= 3:
                    reduced[name] = outcomes
                    continue
                singles = [o for o in outcomes if len(o) == 1]
                keep = list(singles)
                for candidate in (outcomes[-1], outcomes[len(outcomes) - 2]):
                    if candidate not in keep:
                        keep.append(candidate)
                reduced[name] = keep
            per_decision = reduced
            approx = True

        order = list(self.decision_order)
        worlds: List[Dict[str, Any]] = []
        combos: Iterable[Tuple[FrozenSet[str], ...]]
        if order:
            combos = itertools.product(*[per_decision[name] for name in order])
        else:
            combos = [()]
        enumerated = 0
        for combo in combos:
            if enumerated >= MAX_WORLDS:
                truncated = True
                break
            enumerated += 1
            choice = {order[i]: combo[i] for i in range(len(order))}
            selected, running = self._run_world(choice)
            worlds.append({
                "selected": selected,
                "running": running,
                "terminals": frozenset(t for t in self.terminals if t in running),
            })

        return {
            "worlds": worlds,
            # The true count of distinguishable decision outcomes.
            "total_worlds": total_worlds,
            # How many of them this pass actually walked.
            "enumerated": len(worlds),
            "sampled": approx or truncated or len(worlds) < total_worlds,
            "truncated": truncated,
            "approx": approx,
            "complete": (not truncated) and (not approx)
            and len(worlds) >= total_worlds,
            "per_decision": {k: len(v) for k, v in per_decision.items()},
        }

    def _live_branch_count(self, dec: Dict[str, Any], strict: bool) -> int:
        """Distinct branch names this decision could put in branches_to_execute
        through a case, ignoring the default."""
        name = dec["step"]
        live: Set[str] = set()
        for case in dec.get("cases") or []:
            branch = case.get("branch")
            if not case.get("branch_step_exists") or not isinstance(branch, str) \
                    or not branch:
                continue
            if strict and (name, case.get("index")) in self.impossible_cases:
                continue
            live.add(branch)
        return len(live)

    def _run_world(self, choice: "Dict[str, FrozenSet[str]]"
                   ) -> "Tuple[Dict[str, FrozenSet[str]], Set[str]]":
        """Resolve one world: which branches each decision emits, and which
        steps therefore run."""
        selected: "Dict[str, FrozenSet[str]]" = {}
        for name in self.decision_order:
            # A decision that does not itself run emits nothing, so everything
            # under it skips. This is what makes nesting work.
            if self._step_runs(name, selected):
                selected[name] = choice.get(name, frozenset())
            else:
                selected[name] = frozenset()
        running: Set[str] = set()
        for name in self.names:
            if name not in self.graph_reachable:
                continue
            if self._step_runs(name, selected):
                running.add(name)
        return selected, running

    def _step_runs(self, name: str,
                   selected: "Dict[str, FrozenSet[str]]") -> bool:
        """AND across decisions, OR within one decision (step_types/decision.md)."""
        for decision, branches in (self.conditions.get(name) or {}).items():
            if decision not in self.decisions_by_name:
                continue  # stale condition: reported separately, never blocks here
            chosen = selected.get(decision)
            if chosen is None:
                continue  # not yet resolved (cycle): stay lenient
            if not (set(branches) & set(chosen)):
                return False
        return True

    # ----------------------------------------------------------------- #
    # Reporting
    # ----------------------------------------------------------------- #

    def run(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[str]]:
        payload: Dict[str, Any] = {}
        self._report_dead_branches()
        self._report_stale_conditions()
        self._report_islands()
        reachability = self._report_reachability()
        payload["reachability"] = reachability
        payload["decisions"] = self._decision_payload()
        payload["termination"] = self._report_termination(reachability)
        payload["gates"] = self._report_gates(reachability)
        payload["paths"], groups = self._path_matrix()
        payload["path_groups"] = groups
        payload["paths_truncated"] = (self.lenient["enumerated"]
                                      > self.paths_persisted)
        self._report_sprawl()
        payload["world_enumeration"] = {
            "lenient": {k: v for k, v in self.lenient.items()
                        if k not in ("worlds",)},
            "strict": {k: v for k, v in self.strict.items()
                       if k not in ("worlds",)},
        }
        payload["graph"] = {
            "entries": self.entries,
            "terminal_steps": self.terminals,
            "sinks_not_terminal": self.sinks,
            "graph_unreachable": sorted(n for n in self.names
                                        if n not in self.graph_reachable),
        }
        return self.findings, payload, self._summary_lines(reachability, groups)

    # -- dead branches ---------------------------------------------------- #

    def _report_dead_branches(self) -> None:
        for dec in self.decisions:
            name = dec["step"]
            for case in dec.get("cases") or []:
                branch = case.get("branch")
                if case.get("branch_step_exists"):
                    self._check_branch_stamped(name, branch, "case %s"
                                               % case.get("index"))
                    continue
                self.findings.append(finding(
                    "DEAD_BRANCH", "blocker",
                    "Decision branch names no step",
                    "%s case %s routes to branch '%s', but no step has that name. "
                    "step_types/decision.md: a branch value is the name of a "
                    "branch-root step. The platform logs a warning and saves, so "
                    "the branch is silently dead and nothing downstream ever runs "
                    "through it." % (name, case.get("index"), branch),
                    fix="Rename the step or the branch so they match exactly — "
                        "matching is case- and whitespace-sensitive.",
                    step=name,
                    evidence={"case": case.get("index"), "branch": branch,
                              "closest": _cap(self._closest(branch), 5)},
                    group_key="DEAD_BRANCH:%s" % name))

            default = dec.get("default_branch")
            if isinstance(default, str) and default:
                if default not in self.nameset:
                    self.findings.append(finding(
                        "DEAD_DEFAULT_BRANCH", "blocker",
                        "default_branch names no step",
                        "%s declares default_branch '%s', but no step has that "
                        "name. When no case matches, the decision selects a branch "
                        "nothing is stamped with, so the whole fallback path is "
                        "dead." % (name, default),
                        fix="Point default_branch at an existing step, or set it to "
                            "\"\" to mean \"no fallback, let downstream skip\".",
                        step=name,
                        evidence={"default_branch": default,
                                  "closest": _cap(self._closest(default), 5)},
                        group_key="DEAD_DEFAULT_BRANCH:%s" % name))
                else:
                    self._check_branch_stamped(name, default, "default_branch")

            usable_lenient, _ = self._decision_outcomes(dec, strict=False)
            if all(not outcome for outcome in usable_lenient):
                self.findings.append(finding(
                    "DECISION_SELECTS_NOTHING", "warning",
                    "Decision can never select a branch",
                    "%s has no live case branch and an empty default_branch, so it "
                    "never emits a branch and every step stamped with it always "
                    "goes BRANCH_SKIPPED." % name,
                    fix="Add a case with a real branch root, or set default_branch "
                        "to an existing step.",
                    step=name, evidence={"cases": len(dec.get("cases") or [])}))

    def _check_branch_stamped(self, decision: str, branch: Optional[str],
                              where: str) -> None:
        """A branch root should carry the matching parent_condition.

        step_types/decision.md: at save time the platform walks each branch root
        through the dependency graph and stamps parent_conditions. A branch root
        without the stamp means the saved graph and the config disagree.
        """
        if not isinstance(branch, str) or branch not in self.nameset:
            return
        stamped = (self.conditions.get(branch) or {}).get(decision) or []
        if branch in stamped:
            return
        self.findings.append(finding(
            "BRANCH_ROOT_NOT_STAMPED", "warning",
            "Branch root carries no parent_condition for its decision",
            "%s routes %s to '%s', but that step's parent_conditions do not "
            "include {decision_step: %s, branch: %s}. parent_conditions are "
            "stamped at save time, so either the workflow has not been re-saved "
            "since the branch was wired, or the branch root is not reachable "
            "through the dependency graph from this decision."
            % (decision, where, branch, decision, branch),
            fix="Re-save the workflow so the platform re-stamps parent_conditions, "
                "then re-run verify.",
            step=branch,
            evidence={"decision": decision, "branch": branch,
                      "parent_conditions": self.by_name.get(branch, {}).get(
                          "parent_conditions") or []},
            group_key="BRANCH_ROOT_NOT_STAMPED:%s" % decision))

    def _closest(self, target: Optional[str]) -> List[str]:
        """Step names sharing a token with the missing branch name."""
        if not isinstance(target, str) or not target:
            return []
        tokens = {t.lower() for t in target.replace("_", " ").split() if len(t) > 2}
        if not tokens:
            return []
        scored = []
        for name in self.names:
            other = {t.lower() for t in name.replace("_", " ").split() if len(t) > 2}
            overlap = len(tokens & other)
            if overlap:
                scored.append((-overlap, name))
        return [name for _, name in sorted(scored)]

    def _report_islands(self) -> None:
        """A step with no incoming edge of any kind.

        It still runs — the executor starts anything with nothing to wait for —
        so this is never an unreachability claim. But once the graph records
        every wiring style, a step that not even `Workflow Dispatcher` feeds is
        disconnected from the document flow rather than merely first.

        Manual gates are exempt: step_types/manual_input.md is explicit that
        most forms need no input_mappings at all, and that a sham mapping added
        only to force ordering is worse than none.
        """
        for step in self.steps:
            name = step["name"]
            if self.reverse.get(name):
                continue
            if step.get("is_manual"):
                continue
            self.findings.append(finding(
                "ISLAND_STEP", "note",
                "Step has no incoming wiring at all",
                "%s declares no input from any step, not even Workflow Dispatcher. "
                "It runs immediately with no upstream data, which is legal but "
                "means it cannot see the submission's documents. Its %d consumer(s) "
                "get whatever it produces from static config alone."
                % (name, len([c for c in (self.adjacency.get(name) or [])
                              if c in self.nameset])),
                fix="Wire the documents or upstream values it needs, or confirm it "
                    "is meant to run on static config.",
                step=name, evidence={"type": step.get("type"),
                                     "consumers": _cap(
                                         [c for c in (self.adjacency.get(name) or [])
                                          if c in self.nameset], 6)},
                group_key="ISLAND_STEP:%s" % name))

    def _report_stale_conditions(self) -> None:
        for step in self.steps:
            name = step["name"]
            for decision in (self.conditions.get(name) or {}):
                if decision in self.decisions_by_name:
                    continue
                self.findings.append(finding(
                    "STALE_PARENT_CONDITION", "warning",
                    "parent_conditions name a decision that no longer exists",
                    "%s is gated on decision '%s', which is not a decision step in "
                    "this workflow. The runtime has no branches to check, so this "
                    "pass treats the gate as satisfied rather than claiming the "
                    "step is unreachable." % (name, decision),
                    fix="Re-save the workflow so parent_conditions are re-stamped, "
                        "or restore the decision step.",
                    step=name, evidence={"decision_step": decision},
                    group_key="STALE_PARENT_CONDITION:%s" % decision))

    # -- reachability ----------------------------------------------------- #

    def _worlds_running(self, model: Dict[str, Any], name: str) -> int:
        return sum(1 for world in model["worlds"] if name in world["running"])

    def _report_reachability(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        lenient_total = max(1, self.lenient["enumerated"])
        for step in self.steps:
            name = step["name"]
            grouped = self.conditions.get(name) or {}
            lenient_runs = self._worlds_running(self.lenient, name)
            strict_runs = self._worlds_running(self.strict, name)
            reachable = lenient_runs > 0
            row = {
                "step": name,
                "type": step.get("type"),
                "reachable": reachable,
                "always_runs": lenient_runs == lenient_total and not grouped,
                "worlds_running": lenient_runs,
                "worlds_total": self.lenient["enumerated"],
                "strict_worlds_running": strict_runs,
                "conditions": [{"decision": d, "any_of": b}
                               for d, b in grouped.items()],
                "condition_expr": _condition_expr(grouped),
                "graph_reachable": name in self.graph_reachable,
                "is_terminal": bool(step.get("is_terminal")),
                "is_manual": bool(step.get("is_manual")),
            }
            rows.append(row)

            if not reachable:
                self._emit_unreachable(step, grouped)
            elif strict_runs == 0:
                self.findings.append(finding(
                    "UNREACHABLE_CONDITIONS", "warning",
                    "Step is reachable only through a case this pass believes cannot fire",
                    "%s runs in %d of %d decision worlds, but every one of those "
                    "worlds depends on a case flagged IMPOSSIBLE_CASE. If those "
                    "flags are right, this step never runs. Gate: %s."
                    % (name, lenient_runs, lenient_total, _condition_expr(grouped)),
                    fix="Fix the flagged comparisons, or confirm they can match and "
                        "ignore this warning.",
                    step=name, evidence={"conditions": row["conditions"]},
                    group_key="UNREACHABLE_CONDITIONS:%s" % name))
        return rows

    def _emit_unreachable(self, step: Dict[str, Any],
                          grouped: "Dict[str, List[str]]") -> None:
        name = step["name"]
        is_manual = bool(step.get("is_manual"))
        complete = self.lenient["complete"]

        if name not in self.graph_reachable:
            in_cycle = any(name in cycle for cycle in self.cycles)
            if in_cycle:
                self.findings.append(finding(
                    "UNREACHABLE_IN_CYCLE", "note",
                    "Step sits in a dependency cycle",
                    "%s is only reachable through the dependency cycle pass 0 "
                    "already reported, so path analysis cannot place it."
                    % name,
                    fix="Break the cycle (see the DEPENDENCY_CYCLE blocker), then "
                        "re-run verify.",
                    step=name, evidence={"cycles": _cap(self.cycles, 3)}))
                return
            self.findings.append(finding(
                "UNREACHABLE_STEP", "blocker",
                "Step has no dependency path from an entry step",
                "%s cannot be reached from any entry step (%s) through the "
                "dependency graph, so it never executes."
                % (name, ", ".join(_cap(self.entries, 4)) or "none"),
                fix="Wire an input_mapping from an upstream step, or delete the step.",
                step=name, evidence={"predecessors": _cap(self.reverse.get(name) or [])},
                group_key="UNREACHABLE_STEP:%s" % name))
            return

        # Condition-unreachable. Work out why, so the finding is actionable.
        unsatisfiable: List[str] = []
        for decision, branches in grouped.items():
            if decision not in self.decisions_by_name:
                continue
            if not self._decision_can_select(decision, branches):
                unsatisfiable.append(decision)

        live_decisions = [d for d in grouped if d in self.decisions_by_name]
        if unsatisfiable:
            decision = unsatisfiable[0]
            branches = grouped[decision]
            selectable = self._selectable_branches(decision)
            code, severity = ("UNREACHABLE_GATE" if is_manual else "UNREACHABLE_STEP",
                              "blocker" if complete else "warning")
            self.findings.append(finding(
                code, severity,
                "Step is gated on a branch that can never be selected",
                "%s requires %s to select one of [%s]. That decision can only ever "
                "select [%s], so this step always goes BRANCH_SKIPPED."
                % (name, decision, ", ".join(branches),
                   ", ".join(_cap(sorted(selectable), 8)) or "nothing"),
                fix="Point the branch at this step's real branch root, or re-save "
                    "the workflow so parent_conditions are re-stamped.",
                step=name,
                evidence={"decision": decision, "required_branches": branches,
                          "selectable_branches": _cap(sorted(selectable)),
                          "enumeration_complete": complete},
                group_key="%s:%s" % (code, name)))
            return

        if len(live_decisions) >= 2:
            self.findings.append(finding(
                "DIAMOND_AND_OUT", "blocker" if complete else "warning",
                "Diamond join can never be jointly satisfied",
                "%s accumulates conditions from %d decisions — %s. Each is "
                "individually satisfiable, but no combination of branch selections "
                "satisfies all of them at once, because parent_conditions AND "
                "across decisions. The step always goes BRANCH_SKIPPED."
                % (name, len(live_decisions), _condition_expr(grouped)),
                fix="Rewire the join to depend on one decision, or add a case so "
                    "the required branches can fire together. A default_branch "
                    "fires only when no case matched, so it can never co-occur "
                    "with a case branch of the same decision.",
                step=name,
                evidence={"conditions": [{"decision": d, "any_of": b}
                                         for d, b in grouped.items()],
                          "enumeration_complete": complete},
                group_key="DIAMOND_AND_OUT:%s" % name))
            return

        code = "UNREACHABLE_GATE" if is_manual else "UNREACHABLE_STEP"
        self.findings.append(finding(
            code, "blocker" if complete else "warning",
            "Step never runs in any decision world",
            "%s runs in none of the %d enumerated decision worlds. Gate: %s."
            % (name, self.lenient["enumerated"], _condition_expr(grouped)),
            fix="Check that the gating decision is itself reachable and that its "
                "branches name this step's branch root.",
            step=name,
            evidence={"conditions": [{"decision": d, "any_of": b}
                                     for d, b in grouped.items()],
                      "enumeration_complete": complete},
            group_key="%s:%s" % (code, name)))

    def _decision_can_select(self, decision: str, branches: Sequence[str]) -> bool:
        wanted = set(branches)
        for world in self.lenient["worlds"]:
            if wanted & set(world["selected"].get(decision) or ()):
                return True
        return False

    def _selectable_branches(self, decision: str) -> Set[str]:
        out: Set[str] = set()
        for world in self.lenient["worlds"]:
            out |= set(world["selected"].get(decision) or ())
        return out

    # -- decisions payload ------------------------------------------------ #

    def _decision_payload(self) -> List[Dict[str, Any]]:
        rows = []
        for dec in self.decisions:
            name = dec["step"]
            cases = dec.get("cases") or []
            rows.append({
                "step": name,
                "default_branch": dec.get("default_branch"),
                "default_fires_when": ("no case matches"
                                       if dec.get("default_branch") else
                                       "nothing fires (downstream BRANCH_SKIPPED)"),
                "cases": [{
                    "index": case.get("index"),
                    "branch": case.get("branch"),
                    "branch_step_exists": case.get("branch_step_exists"),
                    "comparison_count": case.get("comparison_count"),
                    "impossible": (name, case.get("index")) in self.impossible_cases,
                    "impossible_reason": self.impossible_cases.get(
                        (name, case.get("index"))),
                } for case in cases],
                "selectable_branches": sorted(self._selectable_branches(name)),
                "outcome_count": self.lenient["per_decision"].get(name),
                "reachable": self._worlds_running(self.lenient, name) > 0,
                "wiring": ("variable" if dec.get("input_schema_properties")
                           else "inline decision_operand"),
                "variables": dec.get("variables") or {},
            })
        return rows

    # -- termination ------------------------------------------------------ #

    def _report_termination(self, reachability: List[Dict[str, Any]]
                            ) -> Dict[str, Any]:
        """CONTRACT.md terminal types: email, fill_docx,
        submission_summary_generator, document_viewer, text_block, hold."""
        by_step = {row["step"]: row for row in reachability}
        result: Dict[str, Any] = {
            "terminal_steps": self.terminals,
            "branches": [],
            "worlds_without_terminal": 0,
        }

        if not self.terminals:
            self.findings.append(finding(
                "NO_TERMINAL_OUTPUT", "note",
                "Workflow has no terminal output step",
                "None of the %d steps is a terminal output type (email, fill_docx, "
                "submission_summary_generator, document_viewer, text_block, hold), "
                "so the results live only in step outputs. This is a note and not a "
                "blocker on purpose: an extraction-only workflow whose deliverable "
                "IS the extraction grid in the UI, or one consumed through the API, "
                "is correct exactly as it stands. The check is telling you what the "
                "workflow does, not that it is broken."
                % len(self.steps),
                fix="Nothing to fix if the grid or the API is the deliverable. Add a "
                    "display or delivery step only if a person is meant to be handed "
                    "the result.",
                step=None, evidence={"step_count": len(self.steps)}))

        # Per-branch termination. Scoped to branch roots, because the "nothing
        # fired" world of a default_branch: "" gate legitimately reaches no
        # terminal (step_types/decision.md calls that the gate pattern).
        for dec in self.decisions:
            name = dec["step"]
            branches: List[Tuple[str, str]] = []
            for case in dec.get("cases") or []:
                if case.get("branch_step_exists"):
                    branches.append((case.get("branch"), "case %s" % case.get("index")))
            default = dec.get("default_branch")
            if isinstance(default, str) and default and default in self.nameset:
                branches.append((default, "default_branch"))

            for branch, where in branches:
                subtree = {branch} | self.descendants.get(branch, set())
                stamped = {s["name"] for s in self.steps
                           if branch in ((self.conditions.get(s["name"]) or {})
                                         .get(name) or [])}
                covered = subtree | stamped
                reached = sorted(t for t in self.terminals if t in covered)
                row = {"decision": name, "branch": branch, "via": where,
                       "steps_in_branch": len(covered),
                       "terminals_reached": reached}
                result["branches"].append(row)
                if reached or not self.terminals:
                    continue
                self.findings.append(finding(
                    "BRANCH_TERMINATES_NOWHERE", "blocker",
                    "Decision branch reaches no terminal output step",
                    "%s routes %s to '%s', but nothing downstream of that branch "
                    "is a terminal output step. The workflow does have terminals "
                    "(%s), so work done on this branch is computed and then "
                    "dropped — no email, document, or display surfaces it."
                    % (name, where, branch, ", ".join(_cap(self.terminals, 4))),
                    fix="Wire this branch into the existing summary or delivery "
                        "step, or give it its own terminal step.",
                    step=branch,
                    evidence={"decision": name, "branch": branch,
                              "steps_in_branch": sorted(_cap(covered)),
                              "workflow_terminals": self.terminals},
                    group_key="BRANCH_TERMINATES_NOWHERE:%s" % name))

        # Worlds that surface nothing.
        if self.terminals:
            barren = [w for w in self.lenient["worlds"] if not w["terminals"]]
            result["worlds_without_terminal"] = len(barren)
            if barren:
                empty_default = [d["step"] for d in self.decisions
                                 if not d.get("default_branch")]
                self.findings.append(finding(
                    "PATH_WITHOUT_TERMINAL", "note",
                    "Some decision outcomes reach no terminal output step",
                    "%d of %d enumerated decision worlds run no terminal output "
                    "step. %s"
                    % (len(barren), self.lenient["enumerated"],
                       ("With default_branch \"\" on %s this is the intentional "
                        "gate pattern from step_types/decision.md: when nothing "
                        "matched, the whole downstream tree is meant to skip."
                        % ", ".join(_cap(empty_default, 3)))
                       if empty_default else
                       "Check whether the submission is meant to end silently."),
                    fix="If a person should still be told nothing happened, give "
                        "the fall-through a notice step.",
                    step=None,
                    evidence={"worlds_without_terminal": len(barren),
                              "worlds": self.lenient["enumerated"],
                              "empty_default_decisions": _cap(empty_default)}))

        # A terminal reached on zero paths is exactly an unreachable terminal,
        # which _emit_unreachable has already reported. Recording the per-
        # terminal world counts here keeps the payload self-contained without
        # duplicating the finding.
        result["terminal_world_counts"] = {
            terminal: (by_step.get(terminal) or {}).get("worlds_running", 0)
            for terminal in self.terminals}
        return result

    # -- manual gates ----------------------------------------------------- #

    def _report_gates(self, reachability: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Audit every pause / hold / manual_input.

        step_types/hold.md: a hold passes its inputs through verbatim as its
        own output, so a hold nothing reads is just a stop sign.
        step_types/manual_input.md: form values are spread directly into the
        output (no data. wrapper), so an unread form is pure time taken from
        an underwriter.
        step_types/pause.md: a pause outputs only {status}, and there is almost
        never a reason to wire off it.
        """
        by_step = {row["step"]: row for row in reachability}
        rows: List[Dict[str, Any]] = []
        readers: Dict[str, List[Dict[str, Any]]] = {}
        for edge in self.edges:
            source = edge.get("from_step")
            if source and source in self.nameset:
                readers.setdefault(source, []).append(edge)

        for gate in self.manual_steps:
            name = gate.get("step")
            gate_type = gate.get("type")
            step = self.by_name.get(name) or {}
            row_reach = by_step.get(name) or {}
            consumers = readers.get(name) or []
            read_segments = {_first_segment(e.get("output_attribute"))
                             for e in consumers}
            read_segments.discard(None)
            upstream = [p for p in (self.reverse.get(name) or []) if p in self.nameset]

            row = {
                "step": name,
                "type": gate_type,
                "reachable": bool(row_reach.get("reachable")),
                "condition_expr": row_reach.get("condition_expr"),
                "worlds_running": row_reach.get("worlds_running"),
                "enable_rerun": gate.get("enable_rerun"),
                "field_count": gate.get("field_count"),
                "fields": gate.get("fields") or [],
                "passthrough_params": gate.get("passthrough_params") or [],
                "consumer_count": len(consumers),
                "consumers": _cap([{"step": e.get("to_step"),
                                    "param": e.get("to_param"),
                                    "reads": e.get("output_attribute")}
                                   for e in consumers]),
                "upstream": _cap(upstream),
            }
            rows.append(row)

            # Reachability is reported by _emit_unreachable (as UNREACHABLE_GATE
            # for manual steps), so it is not repeated here.

            if gate_type == "hold":
                params = row["passthrough_params"]
                if not params:
                    self.findings.append(finding(
                        "HOLD_NO_PASSTHROUGH", "warning",
                        "Hold has no input to pass through",
                        "%s is a hold with no input_mappings. step_types/hold.md: "
                        "the passthrough only happens for inputs that flow through "
                        "input_mappings, so the card has nothing to display and "
                        "nothing downstream can wire off it — a pause is the "
                        "correct step type for a pure gate." % name,
                        fix="Wire the artifact the reviewer is meant to see, or "
                            "change the step type to pause.",
                        step=name, evidence={"type": gate_type}))
                elif not consumers:
                    self.findings.append(finding(
                        "HOLD_OUTPUT_UNCONSUMED", "warning",
                        "Hold passthrough is read by nothing downstream",
                        "%s passes through %s, but no step wires off this hold. "
                        "step_types/hold.md: hold's value over pause is that the "
                        "held artifact is renderable and wireable — a hold nothing "
                        "reads is just a stop sign, and pause says that more "
                        "cheaply." % (name, ", ".join(_cap(params, 6))),
                        fix="Wire a downstream step to <%s>.<param>, or switch the "
                            "step to pause." % name,
                        step=name, evidence={"passthrough_params": _cap(params)}))
                else:
                    unread = [p for p in params if p not in read_segments]
                    if unread:
                        self.findings.append(finding(
                            "HOLD_PARAM_UNCONSUMED", "note",
                            "Some hold passthrough params are read by nothing",
                            "%s passes through %s, which no downstream step reads. "
                            "They still render on the hold card, so this is only "
                            "waste if they were wired for a consumer that no longer "
                            "exists." % (name, ", ".join(_cap(unread, 6))),
                            fix="Drop the unused input_mappings entries, or wire "
                                "the intended consumer.",
                            step=name, evidence={"unread": _cap(unread)}))

            if gate_type == "manual_input":
                fields = row["fields"]
                if not fields:
                    self.findings.append(finding(
                        "MANUAL_INPUT_NO_FIELDS", "warning",
                        "manual_input declares no form fields",
                        "%s is a manual_input with an empty config.input_schema, so "
                        "it renders no form and behaves as a bare pause." % name,
                        fix="Declare the fields the reviewer should fill, or change "
                            "the step type to pause.",
                        step=name, evidence={"type": gate_type}))
                elif not consumers:
                    self.findings.append(finding(
                        "MANUAL_FORM_UNREAD", "warning",
                        "Nothing reads this form's answers",
                        "%s asks a person for %d field(s) (%s) and no step wires "
                        "off it. step_types/manual_input.md: form values are spread "
                        "directly into the step output, so they are readable — an "
                        "unread form is pure time taken from an underwriter."
                        % (name, len(fields), ", ".join(_cap(fields, 6))),
                        fix="Wire the answers into the step that should act on "
                            "them, or delete the form.",
                        step=name, evidence={"fields": _cap(fields)}))
                else:
                    unread = [f for f in fields if f not in read_segments]
                    if unread:
                        self.findings.append(finding(
                            "MANUAL_FIELD_UNREAD", "warning",
                            "Some form fields are asked for but never read",
                            "%s asks for %s, which no downstream step reads. Every "
                            "unread field is time taken from an underwriter for "
                            "nothing." % (name, ", ".join(_cap(unread, 6))),
                            fix="Wire the fields into a consumer, or remove them "
                                "from config.input_schema.",
                            step=name,
                            evidence={"unread": _cap(unread),
                                      "read": _cap(sorted(read_segments))},
                            group_key="MANUAL_FIELD_UNREAD:%s" % name))

            if gate_type == "pause" and consumers:
                self.findings.append(finding(
                    "PAUSE_OUTPUT_READ", "note",
                    "A step wires off a pause",
                    "%s is a pause, whose entire output is {status}, and %d step(s) "
                    "wire off it. step_types/pause.md: pause is a gate, not a data "
                    "source — there is almost never a reason to read it, and if a "
                    "decision routes on <pause>.status it should probably gate on "
                    "the upstream review step instead."
                    % (name, len(consumers)),
                    fix="Read the upstream artifact directly, or use a hold if the "
                        "held value is genuinely needed downstream.",
                    step=name, evidence={"consumers": row["consumers"]}))

            if gate.get("enable_rerun"):
                if not upstream:
                    self.findings.append(finding(
                        "GATE_RERUN_NO_UPSTREAM", "warning",
                        "enable_rerun is set on a gate with no upstream step",
                        "%s sets enable_rerun: true, which per "
                        "step_types/%s.md lets the user re-trigger the upstream "
                        "branch — but this gate has no in-workflow predecessor, so "
                        "there is nothing to re-trigger."
                        % (name, gate_type),
                        fix="Set enable_rerun: false, or wire the gate to the step "
                            "the reviewer should be able to re-run.",
                        step=name, evidence={"type": gate_type}))
                elif gate_type in ("hold", "pause"):
                    self.findings.append(finding(
                        "GATE_RERUN_SET", "note",
                        "enable_rerun is set on a gate",
                        "%s sets enable_rerun: true. step_types/%s.md says this is "
                        "typically left false; it is right only when the reviewer "
                        "should be able to re-run %s before resuming."
                        % (name, gate_type, ", ".join(_cap(upstream, 3))),
                        fix="Confirm the reviewer is meant to re-trigger upstream "
                            "work; otherwise set enable_rerun: false.",
                        step=name, evidence={"upstream": _cap(upstream, 5)}))

        # Gates per path.
        if self.manual_steps:
            gate_names = [g.get("step") for g in self.manual_steps]
            worst = 0
            worst_set: List[str] = []
            for world in self.lenient["worlds"]:
                on_path = [g for g in gate_names if g in world["running"]]
                if len(on_path) > worst:
                    worst, worst_set = len(on_path), on_path
            if worst >= 2:
                self.findings.append(finding(
                    "MULTIPLE_GATES_ON_PATH", "note",
                    "One execution path stops for a human more than once",
                    "%d manual gates sit on a single path: %s. Each one halts the "
                    "run until somebody returns to it, so the wall-clock cost is "
                    "additive." % (worst, ", ".join(_cap(worst_set, 6))),
                    fix="Consider merging the reviews into one gate if the same "
                        "person handles both.",
                    step=None, evidence={"gates_on_path": _cap(worst_set),
                                         "count": worst}))
        return rows

    # -- path matrix ------------------------------------------------------ #

    def _path_matrix(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Full matrix in the payload; grouped, distinguishable outcomes printed.

        Grouping is by which terminal outputs are reached, per the pass brief:
        the cross product of concurrent branches is not interesting, the set of
        surfaced results is.
        """
        gate_names = {g.get("step") for g in self.manual_steps}
        paths: List[Dict[str, Any]] = []
        groups: "Dict[FrozenSet[str], Dict[str, Any]]" = {}

        for index, world in enumerate(self.lenient["worlds"]):
            selections = {d: sorted(b) for d, b in world["selected"].items()}
            running = world["running"]
            row = {
                "path": index,
                "selected": selections,
                "terminals": sorted(world["terminals"]),
                "steps_running": len(running),
                "steps_skipped": sorted(n for n in self.names if n not in running),
                "gates": sorted(g for g in gate_names if g in running),
            }
            if index < MAX_PATHS_PERSISTED:
                paths.append(row)

            branch_set = sorted({b for branches in world["selected"].values()
                                 for b in branches})
            key = world["terminals"]
            group = groups.get(key)
            if group is None:
                groups[key] = {
                    "terminals": sorted(key),
                    "paths": 1,
                    "example_selections": selections,
                    "branch_sets": [branch_set],
                    "min_steps": len(running),
                    "max_steps": len(running),
                    "gates": row["gates"],
                }
            else:
                group["paths"] += 1
                group["min_steps"] = min(group["min_steps"], len(running))
                group["max_steps"] = max(group["max_steps"], len(running))
                if branch_set not in group["branch_sets"] \
                        and len(group["branch_sets"]) < MAX_EVIDENCE_LIST:
                    group["branch_sets"].append(branch_set)
                for gate in row["gates"]:
                    if gate not in group["gates"]:
                        group["gates"].append(gate)

        grouped = sorted(groups.values(),
                         key=lambda g: (-g["paths"], tuple(g["terminals"])))
        self.paths_persisted = len(paths)
        return paths, grouped

    # -- sprawl ----------------------------------------------------------- #

    def _report_sprawl(self) -> None:
        limit = THRESHOLDS["MAX_CASE_COMPARISONS"]
        for dec in self.decisions:
            for case in dec.get("cases") or []:
                count = case.get("comparison_count") or 0
                if count <= limit:
                    continue
                self.findings.append(finding(
                    "CASE_SPRAWL", "note",
                    "Decision case packs a lot of logic into one condition",
                    "%s case %s (branch '%s') has %d comparisons, over the %d "
                    "threshold. step_types/decision.md: keep decisions thin "
                    "comparators — compute the value upstream in a custom_step and "
                    "route on one flag here, so the condition is testable and the "
                    "execution log is readable."
                    % (dec["step"], case.get("index"), case.get("branch"), count, limit),
                    fix="Move the arithmetic into an upstream custom_step and "
                        "compare its single output field.",
                    step=dec["step"],
                    evidence={"case": case.get("index"), "comparisons": count,
                              "threshold": limit},
                    group_key="CASE_SPRAWL:%s" % dec["step"]))

    # -- printed summary -------------------------------------------------- #

    def _summary_lines(self, reachability: List[Dict[str, Any]],
                       groups: List[Dict[str, Any]]) -> List[str]:
        workflow = self.facts.get("workflow") or {}
        model = self.lenient
        reachable = [r for r in reachability if r["reachable"]]
        conditional = [r for r in reachable if r["conditions"]]
        lines = [
            "workflow: %s" % truncate(workflow.get("name") or workflow.get("path")
                                      or "(unnamed)", 70),
            "steps=%d  decisions=%d  branch-roots=%d  manual-gates=%d  terminals=%d"
            % (len(self.steps), len(self.decisions), len(self._all_branch_roots()),
               len(self.manual_steps), len(self.terminals)),
            "reachable=%d/%d  conditional=%d  unconditional=%d"
            % (len(reachable), len(self.steps), len(conditional),
               len(reachable) - len(conditional)),
            "decision worlds: total=%d enumerated=%d distinct-outcomes=%d%s"
            % (model["total_worlds"], model["enumerated"], len(groups),
               "  (sampled — unreachability reported as warnings, not blockers)"
               if not model["complete"] else ""),
        ]

        if len(groups) > 1 or self.decisions:
            rows = [[index,
                     ", ".join(group["terminals"]) or "(no terminal output)",
                     " | ".join("+".join(bs) or "(none)"
                                for bs in group["branch_sets"]) or "-",
                     group["paths"],
                     "%d" % group["min_steps"] if group["min_steps"] == group["max_steps"]
                     else "%d-%d" % (group["min_steps"], group["max_steps"]),
                     ", ".join(group["gates"]) or "-"]
                    for index, group in enumerate(groups)]
            lines.append("")
            lines.append("path matrix (grouped by terminal outputs reached; "
                         "branches column lists the distinct selections inside "
                         "each group):")
            lines.extend(table(rows,
                               ["#", "terminals reached", "branches", "paths",
                                "steps", "gates"],
                               max_rows=THRESHOLDS["MAX_PATHS_SHOWN"]))

        if conditional:
            rows = [[row["step"], row["condition_expr"],
                     "%d/%d" % (row["worlds_running"], row["worlds_total"])]
                    for row in conditional]
            lines.append("")
            lines.append("conditional steps:")
            lines.extend(table(rows, ["step", "gate", "worlds"]))
        return lines

    def _all_branch_roots(self) -> Set[str]:
        roots: Set[str] = set()
        for dec in self.decisions:
            for case in dec.get("cases") or []:
                if isinstance(case.get("branch"), str) and case["branch"]:
                    roots.add(case["branch"])
            default = dec.get("default_branch")
            if isinstance(default, str) and default:
                roots.add(default)
        return roots


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def analyse(facts: Dict[str, Any],
            out_dir: str) -> Tuple[PassOutput, List[str]]:
    """Build the pass output plus the already-capped lines to print above it."""
    out = PassOutput("paths", out_dir)
    analysis = PathAnalysis(facts)
    findings, payload, lines = analysis.run()
    out.add_all(findings)
    for key, value in payload.items():
        out.set(key, value)
    return out, lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="pass 3 — decision chains, reachability, manual gates")
    parser.add_argument("--facts", help="path to facts.json")
    parser.add_argument("--out", help="output directory")
    parser.add_argument("--self-test", action="store_true",
                        help="run against the corpus plus synthetic decision "
                             "workflows and assert invariants")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.facts or not args.out:
        parser.error("--facts and --out are required (or pass --self-test)")

    facts = load_facts(args.facts)
    out, lines = analyse(facts, args.out)
    out.write()
    return out.print_summary(lines)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS_DIR = "/Users/andrewjeffers/Documents/Work/contractors/workflows"
EXAMPLE = os.path.join(HERE, "..", "..", "reference", "examples",
                       "submission_intake_es_umbrella.json")


def _build_facts(workflow_path: str, out_dir: str) -> Dict[str, Any]:
    """CONTRACT.md hard rule 2: nothing but wf_facts.py reads workflow.json."""
    os.makedirs(out_dir, exist_ok=True)
    proc = subprocess.run(
        [sys.executable, os.path.join(HERE, "wf_facts.py"), workflow_path,
         "--out", out_dir],
        capture_output=True, text=True)
    facts_path = os.path.join(out_dir, "facts.json")
    if not os.path.exists(facts_path):
        raise RuntimeError("wf_facts.py produced no facts for %s\n%s\n%s"
                           % (workflow_path, proc.stdout[-2000:], proc.stderr[-2000:]))
    with open(facts_path) as handle:
        return json.load(handle)


def _codes(findings: Sequence[Dict[str, Any]]) -> Set[str]:
    return {f["code"] for f in findings}


def _analyse_workflow(workflow_path: str, tmp: str, label: str
                      ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], PathAnalysis]:
    out_dir = os.path.join(tmp, label)
    facts = _build_facts(workflow_path, out_dir)
    analysis = PathAnalysis(facts)
    findings, payload, _ = analysis.run()
    return findings, payload, analysis


def _model_invariants(label: str, payload: Dict[str, Any]) -> List[str]:
    """`reachable` must stay exactly `worlds_running > 0`. Every severity
    decision in this pass rests on that identity, so it is asserted rather
    than assumed."""
    broken = []
    for row in payload["reachability"]:
        if row["reachable"] != (row["worlds_running"] > 0):
            broken.append("%s: %s reachable=%s but worlds_running=%d"
                          % (label, row["step"], row["reachable"],
                             row["worlds_running"]))
        if row["strict_worlds_running"] > row["worlds_running"]:
            broken.append("%s: %s runs in more strict worlds (%d) than lenient "
                          "(%d) — the strict model must be a subset"
                          % (label, row["step"], row["strict_worlds_running"],
                             row["worlds_running"]))
    return broken


def facts_of(analysis: PathAnalysis) -> Dict[str, Any]:
    return analysis.facts


# -- synthetic workflow construction ------------------------------------- #

def _step(name: str, stype: str, **kw: Any) -> Dict[str, Any]:
    step: Dict[str, Any] = {
        "name": name, "type": stype, "input_mappings": [], "dependencies": [],
        "parent_conditions": [], "config": {},
    }
    step.update(kw)
    return step


def _dep(step_name: str, attribute: Optional[str], param: str) -> Dict[str, Any]:
    return {
        "input_type": "dependency",
        "input_parameter_name": param,
        "value": {"dependency_step_outputs": [
            {"step_name": step_name, "output_attribute": attribute}],
            "resolution_operator": None, "resolution_config": None},
    }


def _code_step(name: str, sources: Sequence[Tuple[str, Optional[str], str]],
               outputs: Dict[str, Any], **kw: Any) -> Dict[str, Any]:
    params = [param for _, _, param in sources]
    return _step(
        name, "custom_step",
        input_mappings=[_dep(s, a, p) for s, a, p in sources],
        config={
            "code": "async def main(%s):\n    return %s\n"
                    % (", ".join(params) or "**kwargs",
                       json.dumps({k: None for k in outputs})),
            "input_schema": {"type": "object",
                             "properties": {p: {"type": "string"} for p in params}},
            "output_schema": {"type": "object", "properties": outputs},
        },
        **kw)


def _decision_step(name: str, cases: Sequence[Dict[str, Any]],
                   default_branch: str, sources: Sequence[Tuple[str, str, str]] = (),
                   input_schema: Optional[Dict[str, Any]] = None,
                   **kw: Any) -> Dict[str, Any]:
    config: Dict[str, Any] = {"cases": list(cases), "default_branch": default_branch}
    if input_schema:
        config["input_schema"] = input_schema
    return _step(name, "decision",
                 input_mappings=[_dep(s, a, p) for s, a, p in sources],
                 config=config, **kw)


def _case(branch: str, comparisons: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return {"branch": branch, "condition": [list(comparisons)]}


def _cmp_inline(step_name: str, attribute: str, operator: str,
                right: Any = "") -> Dict[str, Any]:
    """Inline-dependency operand — the legacy form the LPL gate uses."""
    return {
        "left_operand": {"input_type": "dependency", "value": {
            "dependency_step_outputs": [
                {"step_name": step_name, "output_attribute": attribute}],
            "resolution_operator": None, "resolution_config": None}},
        "operator": operator,
        "right_operand": {"input_type": "static", "value": right},
    }


def _cmp_var(var: str, operator: str, right: Any = "") -> Dict[str, Any]:
    return {
        "left_operand": {"input_type": "variable", "value": var},
        "operator": operator,
        "right_operand": {"input_type": "static", "value": right},
    }


def _wf(name: str, steps: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return {"name": name, "steps": list(steps), "options": {}}


def _prepare() -> Dict[str, Any]:
    return _step("Prepare Documents", "prepare_documents",
                 input_mappings=[_dep(DISPATCHER, "user_document_ids",
                                      "user_document_ids")])


def _email(name: str, source: str) -> Dict[str, Any]:
    return _step(name, "email",
                 input_mappings=[_dep(source, None, "body")],
                 config={"to": [], "subject": "x"})


def _write(tmp: str, label: str, workflow: Dict[str, Any]) -> str:
    path = os.path.join(tmp, "%s.json" % label)
    with open(path, "w") as handle:
        json.dump(workflow, handle)
    return path


# -- the synthetic scenarios --------------------------------------------- #

def _syn_empty_default(tmp: str) -> Tuple[str, Dict[str, Any]]:
    """default_branch "" with downstream steps: the intentional gate pattern.

    Expect: no blocker, a PATH_WITHOUT_TERMINAL note, and the gated subtree
    reachable in some worlds but not all.
    """
    steps = [
        _prepare(),
        _code_step("Compute Flag", [("Prepare Documents", "documents", "documents")],
                   {"flag": {"type": "boolean"}}),
        _decision_step("Gate", [_case("Heavy Path",
                                      [_cmp_inline("Compute Flag", "flag", "is_true",
                                                   True)])],
                       default_branch=""),
        _code_step("Heavy Path", [("Compute Flag", "flag", "flag")],
                   {"body": {"type": "string"}},
                   parent_conditions=[{"decision_step": "Gate",
                                       "branch": "Heavy Path"}]),
        _step("Notify", "email",
              input_mappings=[_dep("Heavy Path", "body", "body")],
              config={"to": [], "subject": "x"},
              parent_conditions=[{"decision_step": "Gate", "branch": "Heavy Path"}]),
    ]
    return _write(tmp, "syn_empty_default", _wf("Syn Empty Default", steps)), {
        "expect_codes": {"PATH_WITHOUT_TERMINAL"},
        "forbid_codes": {"UNREACHABLE_STEP", "DIAMOND_AND_OUT",
                         "BRANCH_TERMINATES_NOWHERE"},
        "blockers": 0,
    }


def _syn_dead_branch(tmp: str) -> Tuple[str, Dict[str, Any]]:
    """A branch naming a step that does not exist."""
    steps = [
        _prepare(),
        _code_step("Compute Flag", [("Prepare Documents", "documents", "documents")],
                   {"flag": {"type": "boolean"}}),
        _decision_step("Gate", [_case("Heavy Pathh",
                                      [_cmp_inline("Compute Flag", "flag",
                                                   "is_true", True)])],
                       default_branch="Heavy Path"),
        _code_step("Heavy Path", [("Compute Flag", "flag", "flag")],
                   {"body": {"type": "string"}},
                   parent_conditions=[{"decision_step": "Gate",
                                       "branch": "Heavy Path"}]),
        _email("Notify", "Heavy Path"),
    ]
    return _write(tmp, "syn_dead_branch", _wf("Syn Dead Branch", steps)), {
        "expect_codes": {"DEAD_BRANCH"},
        "expect_blocker_codes": {"DEAD_BRANCH"},
        "blockers_min": 1,
    }


def _syn_and_out(tmp: str) -> Tuple[str, Dict[str, Any]]:
    """Two decisions that AND themselves out at a join.

    Gate A selects either case-branch "Alpha" or default "Beta" — never both,
    because a default fires only when nothing matched. Gate B lives under
    Alpha. The join requires Beta AND Gate B's branch, so it can never run.
    """
    steps = [
        _prepare(),
        _code_step("Compute Flags", [("Prepare Documents", "documents", "documents")],
                   {"a": {"type": "boolean"}, "b": {"type": "boolean"}}),
        _decision_step("Gate A", [_case("Alpha",
                                        [_cmp_inline("Compute Flags", "a",
                                                     "is_true", True)])],
                       default_branch="Beta"),
        _code_step("Alpha", [("Compute Flags", "a", "a")], {"x": {"type": "string"}},
                   parent_conditions=[{"decision_step": "Gate A", "branch": "Alpha"}]),
        _code_step("Beta", [("Compute Flags", "b", "b")], {"y": {"type": "string"}},
                   parent_conditions=[{"decision_step": "Gate A", "branch": "Beta"}]),
        _decision_step("Gate B", [_case("Gamma",
                                        [_cmp_inline("Alpha", "x", "is_not_empty")])],
                       default_branch="",
                       parent_conditions=[{"decision_step": "Gate A",
                                           "branch": "Alpha"}]),
        _code_step("Gamma", [("Alpha", "x", "x")], {"z": {"type": "string"}},
                   parent_conditions=[{"decision_step": "Gate A", "branch": "Alpha"},
                                      {"decision_step": "Gate B", "branch": "Gamma"}]),
        _code_step("Join", [("Beta", "y", "y"), ("Gamma", "z", "z")],
                   {"body": {"type": "string"}},
                   parent_conditions=[{"decision_step": "Gate A", "branch": "Beta"},
                                      {"decision_step": "Gate B", "branch": "Gamma"}]),
        _email("Notify", "Join"),
    ]
    return _write(tmp, "syn_and_out", _wf("Syn And Out", steps)), {
        "expect_codes": {"DIAMOND_AND_OUT"},
        "expect_blocker_codes": {"DIAMOND_AND_OUT"},
        "expect_blocker_steps": {"Join"},
        "blockers_min": 1,
    }


def _syn_branch_dead_end(tmp: str) -> Tuple[str, Dict[str, Any]]:
    """A branch that terminates nowhere while the workflow has a terminal."""
    steps = [
        _prepare(),
        _code_step("Compute Flag", [("Prepare Documents", "documents", "documents")],
                   {"flag": {"type": "boolean"}}),
        _decision_step("Gate", [_case("Side Quest",
                                      [_cmp_inline("Compute Flag", "flag",
                                                   "is_true", True)])],
                       default_branch="Main Path"),
        _code_step("Side Quest", [("Compute Flag", "flag", "flag")],
                   {"note": {"type": "string"}},
                   parent_conditions=[{"decision_step": "Gate",
                                       "branch": "Side Quest"}]),
        _code_step("Main Path", [("Compute Flag", "flag", "flag")],
                   {"body": {"type": "string"}},
                   parent_conditions=[{"decision_step": "Gate",
                                       "branch": "Main Path"}]),
        _step("Notify", "email",
              input_mappings=[_dep("Main Path", "body", "body")],
              config={"to": [], "subject": "x"},
              parent_conditions=[{"decision_step": "Gate", "branch": "Main Path"}]),
    ]
    return _write(tmp, "syn_branch_dead_end", _wf("Syn Branch Dead End", steps)), {
        "expect_codes": {"BRANCH_TERMINATES_NOWHERE"},
        "expect_blocker_codes": {"BRANCH_TERMINATES_NOWHERE"},
        "expect_blocker_steps": {"Side Quest"},
        "blockers_min": 1,
    }


def _syn_three_concurrent(tmp: str) -> Tuple[str, Dict[str, Any]]:
    """Three branches that can fire concurrently from one decision.

    All cases evaluate, so the decision has 2^3 - 1 = 7 non-empty branch
    subsets plus the nothing-matched outcome = 8 worlds.
    """
    steps = [
        _prepare(),
        _step("Classify", "classify_documents",
              input_mappings=[_dep("Prepare Documents", "documents", "documents")],
              config={"classes": [{"name": "GL"}, {"name": "WC"}, {"name": "AL"}]}),
        _decision_step(
            "Determine LOBs",
            [_case("Extract GL", [_cmp_var("gl", "is_not_empty")]),
             _case("Extract WC", [_cmp_var("wc", "is_not_empty")]),
             _case("Extract AL", [_cmp_var("al", "is_not_empty")])],
            default_branch="",
            sources=[("Classify", "documents.GL", "gl"),
                     ("Classify", "documents.WC", "wc"),
                     ("Classify", "documents.AL", "al")],
            input_schema={"type": "object", "properties": {
                "gl": {"type": "array"}, "wc": {"type": "array"},
                "al": {"type": "array"}}}),
    ]
    for lob in ("GL", "WC", "AL"):
        steps.append(_code_step(
            "Extract %s" % lob, [("Classify", "documents.%s" % lob, "docs")],
            {"body": {"type": "string"}},
            parent_conditions=[{"decision_step": "Determine LOBs",
                                "branch": "Extract %s" % lob}]))
    steps.append(_code_step(
        "Summarize",
        [("Extract GL", "body", "gl"), ("Extract WC", "body", "wc"),
         ("Extract AL", "body", "al")],
        {"body": {"type": "string"}},
        parent_conditions=[{"decision_step": "Determine LOBs", "branch": "Extract GL"},
                           {"decision_step": "Determine LOBs", "branch": "Extract WC"},
                           {"decision_step": "Determine LOBs", "branch": "Extract AL"}]))
    steps.append(_step("Notify", "email",
                       input_mappings=[_dep("Summarize", "body", "body")],
                       config={"to": [], "subject": "x"},
                       parent_conditions=[
                           {"decision_step": "Determine LOBs", "branch": "Extract GL"},
                           {"decision_step": "Determine LOBs", "branch": "Extract WC"},
                           {"decision_step": "Determine LOBs", "branch": "Extract AL"}]))
    return _write(tmp, "syn_three_concurrent",
                  _wf("Syn Three Concurrent", steps)), {
        "expect_codes": {"PATH_WITHOUT_TERMINAL"},
        "forbid_codes": {"UNREACHABLE_STEP", "DIAMOND_AND_OUT",
                         "BRANCH_TERMINATES_NOWHERE"},
        "blockers": 0,
        "worlds": 8,
        "max_concurrent_branches": 3,
    }


def _syn_nested(tmp: str) -> Tuple[str, Dict[str, Any]]:
    """A decision nested under another decision's branch."""
    steps = [
        _prepare(),
        _code_step("Compute Flags", [("Prepare Documents", "documents", "documents")],
                   {"a": {"type": "boolean"}, "b": {"type": "boolean"}}),
        _decision_step("Outer Gate", [_case("Deep Review",
                                            [_cmp_inline("Compute Flags", "a",
                                                         "is_true", True)])],
                       default_branch="Fast Path"),
        _code_step("Deep Review", [("Compute Flags", "b", "b")],
                   {"score": {"type": "number"}},
                   parent_conditions=[{"decision_step": "Outer Gate",
                                       "branch": "Deep Review"}]),
        _decision_step("Inner Gate", [_case("Escalate",
                                            [_cmp_inline("Deep Review", "score",
                                                         "gt", 5)])],
                       default_branch="Approve",
                       parent_conditions=[{"decision_step": "Outer Gate",
                                           "branch": "Deep Review"}]),
        _code_step("Escalate", [("Deep Review", "score", "score")],
                   {"body": {"type": "string"}},
                   parent_conditions=[{"decision_step": "Outer Gate",
                                       "branch": "Deep Review"},
                                      {"decision_step": "Inner Gate",
                                       "branch": "Escalate"}]),
        _code_step("Approve", [("Deep Review", "score", "score")],
                   {"body": {"type": "string"}},
                   parent_conditions=[{"decision_step": "Outer Gate",
                                       "branch": "Deep Review"},
                                      {"decision_step": "Inner Gate",
                                       "branch": "Approve"}]),
        _code_step("Fast Path", [("Compute Flags", "a", "a")],
                   {"body": {"type": "string"}},
                   parent_conditions=[{"decision_step": "Outer Gate",
                                       "branch": "Fast Path"}]),
        _step("Notify", "email",
              input_mappings=[_dep("Escalate", "body", "e"),
                              _dep("Approve", "body", "a"),
                              _dep("Fast Path", "body", "f")],
              config={"to": [], "subject": "x"}),
    ]
    return _write(tmp, "syn_nested", _wf("Syn Nested", steps)), {
        "forbid_codes": {"UNREACHABLE_STEP", "DIAMOND_AND_OUT",
                         "BRANCH_TERMINATES_NOWHERE"},
        "blockers": 0,
        "nested_check": True,
    }


def _syn_impossible_case(tmp: str) -> Tuple[str, Dict[str, Any]]:
    """An `eq` against a value outside the upstream enum, plus a cell operand
    and a sprawling case."""
    big = [_cmp_var("status", "neq", "Bound") for _ in range(7)]
    steps = [
        _prepare(),
        _step("Extract Fields", "agentic_extraction",
              input_mappings=[_dep("Prepare Documents", "documents", "documents")],
              config={"extraction_schema": {"type": "object", "properties": {
                  "status": {"type": "string", "enum": ["Bound", "Quoted", "Declined"]},
                  "bound_on": {"type": "string"},
                  "premium": {"type": "number"}}}}),
        _decision_step(
            "Status Gate",
            [_case("Handle Expired", [_cmp_var("status", "eq", "Expired")]),
             _case("Handle Cell", [_cmp_var("premium_cell", "eq", 100)]),
             _case("Handle Sprawl", big),
             _case("Handle Stale", [_cmp_var("bound_on", "before", "01/02/2026"),
                                    _cmp_var("status", "gt", 3)])],
            default_branch="Handle Normal",
            sources=[("Extract Fields", "data.status.value", "status"),
                     ("Extract Fields", "data.premium", "premium_cell"),
                     ("Extract Fields", "data.bound_on.value", "bound_on")],
            input_schema={"type": "object", "properties": {
                "status": {"type": "string"}, "premium_cell": {"type": "object"},
                "bound_on": {"type": "string"}}}),
    ]
    for branch in ("Handle Expired", "Handle Cell", "Handle Sprawl",
                   "Handle Stale", "Handle Normal"):
        steps.append(_code_step(
            branch, [("Extract Fields", "data.status.value", "status")],
            {"body": {"type": "string"}},
            parent_conditions=[{"decision_step": "Status Gate", "branch": branch}]))
    steps.append(_step(
        "Notify", "email",
        input_mappings=[_dep(branch, "body", "b%d" % index) for index, branch
                        in enumerate(("Handle Expired", "Handle Cell",
                                      "Handle Sprawl", "Handle Stale",
                                      "Handle Normal"))],
        config={"to": [], "subject": "x"}))
    return _write(tmp, "syn_impossible_case",
                  _wf("Syn Impossible Case", steps)), {
        "expect_codes": {"IMPOSSIBLE_CASE", "OPERAND_MISSING_VALUE", "CASE_SPRAWL",
                         "AMBIGUOUS_DATE_LITERAL", "COMPARISON_TYPE_RISK"},
        "expect_warning_codes": {"IMPOSSIBLE_CASE"},
        "forbid_codes": {"UNREACHABLE_STEP"},
        "blockers": 0,
        "impossible_branch": "Handle Expired",
    }


def _syn_gates(tmp: str) -> Tuple[str, Dict[str, Any]]:
    """Manual gate audit: an unconsumed hold, an unread form, two gates on one
    path, an unreachable gate, and enable_rerun with no upstream."""
    steps = [
        _prepare(),
        _code_step("Build Report", [("Prepare Documents", "documents", "documents")],
                   {"markdown_content": {"type": "string"}}),
        _step("Review Report", "hold",
              input_mappings=[_dep("Build Report", "markdown_content",
                                   "markdown_content")],
              config={}),
        _step("Collect Notes", "manual_input",
              input_mappings=[_dep("Build Report", "markdown_content", "context")],
              config={"message": "Notes?", "input_schema": {
                  "type": "object", "properties": {
                      "note_text": {"title": "Notes", "type": "text",
                                    "input_type": "text"},
                      "reviewer": {"title": "Reviewer", "type": "text",
                                   "input_type": "text"}}}}),
        _code_step("Finalize", [("Collect Notes", "note_text", "note_text")],
                   {"body": {"type": "string"}}),
        _step("Notify", "email",
              input_mappings=[_dep("Finalize", "body", "body")],
              config={"to": [], "subject": "x"}),
        # A gate stranded behind a decision that can never select its branch.
        _decision_step("Escalation Gate", [], default_branch=""),
        _step("Stranded Pause", "pause", config={},
              input_mappings=[_dep("Build Report", "markdown_content", "ctx")],
              parent_conditions=[{"decision_step": "Escalation Gate",
                                  "branch": "Stranded Pause"}]),
        # Same gate, non-manual step: UNREACHABLE_STEP rather than
        # UNREACHABLE_GATE.
        _code_step("Stranded Work",
                   [("Build Report", "markdown_content", "ctx")],
                   {"body": {"type": "string"}},
                   parent_conditions=[{"decision_step": "Escalation Gate",
                                       "branch": "Stranded Work"}]),
        # enable_rerun with nothing upstream to re-trigger.
        _step("Orphan Hold", "hold", enable_rerun=True, config={}),
    ]
    return _write(tmp, "syn_gates", _wf("Syn Gates", steps)), {
        "expect_codes": {"HOLD_OUTPUT_UNCONSUMED", "MANUAL_FIELD_UNREAD",
                         "MULTIPLE_GATES_ON_PATH", "UNREACHABLE_GATE",
                         "UNREACHABLE_STEP", "ISLAND_STEP",
                         "GATE_RERUN_NO_UPSTREAM", "HOLD_NO_PASSTHROUGH"},
        "expect_blocker_codes": {"UNREACHABLE_GATE", "UNREACHABLE_STEP"},
        "expect_blocker_steps": {"Stranded Pause", "Stranded Work"},
        "blockers_min": 2,
    }


def _syn_stamping(tmp: str) -> Tuple[str, Dict[str, Any]]:
    """Save-time stamping gone stale: an unstamped branch root, a
    default_branch naming no step, and a parent_condition naming a decision
    that is no longer in the workflow.

    None of the three may harden into a false unreachability: an unstamped root
    runs unconditionally, and a stale condition has no branches to check.
    """
    steps = [
        _prepare(),
        _code_step("Compute Flag", [("Prepare Documents", "documents", "documents")],
                   {"flag": {"type": "boolean"}}),
        _decision_step("Gate", [_case("Alpha",
                                      [_cmp_inline("Compute Flag", "flag",
                                                   "is_true", True)])],
                       default_branch="Ghost Path"),
        # Branch root exists but carries no parent_condition.
        _code_step("Alpha", [("Compute Flag", "flag", "flag")],
                   {"body": {"type": "string"}}),
        # Gated on a decision that is not in this workflow.
        _code_step("Legacy", [("Compute Flag", "flag", "flag")],
                   {"body": {"type": "string"}},
                   parent_conditions=[{"decision_step": "Removed Gate",
                                       "branch": "Whatever"}]),
        _step("Notify", "email",
              input_mappings=[_dep("Alpha", "body", "a"),
                              _dep("Legacy", "body", "b")],
              config={"to": [], "subject": "x"}),
    ]
    return _write(tmp, "syn_stamping", _wf("Syn Stamping", steps)), {
        "expect_codes": {"DEAD_DEFAULT_BRANCH", "BRANCH_ROOT_NOT_STAMPED",
                         "STALE_PARENT_CONDITION"},
        "expect_blocker_codes": {"DEAD_DEFAULT_BRANCH"},
        "expect_warning_codes": {"BRANCH_ROOT_NOT_STAMPED",
                                 "STALE_PARENT_CONDITION"},
        "forbid_codes": {"UNREACHABLE_STEP", "UNREACHABLE_GATE",
                         "DIAMOND_AND_OUT"},
        "blockers": 1,
        "all_reachable": True,
    }


def _syn_sampling(tmp: str) -> Tuple[str, Dict[str, Any]]:
    """Three decisions with six concurrent branches each: 2^6 outcomes per
    decision, 262,144 combinations. The enumeration must sample rather than
    explode, and a sampled enumeration must never harden into a blocker.
    """
    steps = [
        _prepare(),
        _code_step("Flags", [("Prepare Documents", "documents", "documents")],
                   {"f%d" % i: {"type": "boolean"} for i in range(18)}),
    ]
    for gate in range(3):
        steps.append(_decision_step(
            "Gate%d" % gate,
            [_case("B%d_%d" % (gate, i),
                   [_cmp_inline("Flags", "f%d" % (gate * 6 + i), "is_true", True)])
             for i in range(6)],
            default_branch=""))
        for i in range(6):
            steps.append(_code_step(
                "B%d_%d" % (gate, i), [("Flags", "f0", "f")],
                {"body": {"type": "string"}},
                parent_conditions=[{"decision_step": "Gate%d" % gate,
                                    "branch": "B%d_%d" % (gate, i)}]))
    steps.append(_step(
        "Notify", "email",
        input_mappings=[_dep("B%d_%d" % (gate, i), "body", "p%d%d" % (gate, i))
                        for gate in range(3) for i in range(6)],
        config={"to": [], "subject": "x"}))
    return _write(tmp, "syn_sampling", _wf("Syn Sampling", steps)), {
        "forbid_codes": {"UNREACHABLE_STEP", "UNREACHABLE_GATE",
                         "DIAMOND_AND_OUT", "BRANCH_TERMINATES_NOWHERE"},
        "blockers": 0,
        "worlds": 64 ** 3,
        "sampled": True,
        "all_reachable": True,
    }


def _syn_dead_variable(tmp: str) -> Tuple[str, Dict[str, Any]]:
    """A condition variable that is never declared, and one declared but not
    wired. Both read None at runtime, so both comparisons are always false."""
    steps = [
        _prepare(),
        _code_step("Compute Flags", [("Prepare Documents", "documents", "documents")],
                   {"a": {"type": "boolean"}, "b": {"type": "boolean"}}),
        _decision_step(
            "Gate",
            [_case("Undeclared Path", [_cmp_var("ghost", "is_true")]),
             _case("Unwired Path", [_cmp_var("declared_only", "is_true")])],
            default_branch="Fallback",
            sources=[("Compute Flags", "a", "wired_one")],
            input_schema={"type": "object", "properties": {
                "wired_one": {"type": "boolean"},
                "declared_only": {"type": "boolean"}}}),
    ]
    for branch in ("Undeclared Path", "Unwired Path", "Fallback"):
        steps.append(_code_step(
            branch, [("Compute Flags", "a", "a")], {"body": {"type": "string"}},
            parent_conditions=[{"decision_step": "Gate", "branch": branch}]))
    steps.append(_step(
        "Notify", "email",
        input_mappings=[_dep(b, "body", "p%d" % i) for i, b in enumerate(
            ("Undeclared Path", "Unwired Path", "Fallback"))],
        config={"to": [], "subject": "x"}))
    return _write(tmp, "syn_dead_variable", _wf("Syn Dead Variable", steps)), {
        "expect_codes": {"CONDITION_VARIABLE_DEAD", "UNREACHABLE_CONDITIONS"},
        "expect_blocker_codes": {"CONDITION_VARIABLE_DEAD"},
        "expect_warning_codes": {"UNREACHABLE_CONDITIONS"},
        # The inference must stay a warning: an unwired variable is provable,
        # but the step it strands is only unreachable if that reading is right.
        "forbid_codes": {"UNREACHABLE_STEP", "DIAMOND_AND_OUT"},
        "blockers_min": 2,
        "all_reachable": True,
    }


def _syn_condition_edges(tmp: str) -> Tuple[str, Dict[str, Any]]:
    """The condition shapes step_types/decision.md calls out but the corpus
    never exercises: a constant-true enum `neq`, a case with no condition at
    all, and a vacuously-true empty AND group."""
    steps = [
        _prepare(),
        _step("Extract Fields", "agentic_extraction",
              input_mappings=[_dep("Prepare Documents", "documents", "documents")],
              config={"extraction_schema": {"type": "object", "properties": {
                  "status": {"type": "string",
                             "enum": ["Bound", "Quoted", "Declined"]}}}}),
        _step("Gate", "decision",
              input_mappings=[_dep("Extract Fields", "data.status.value", "status")],
              config={
                  "cases": [
                      # constant true: neq against a value outside the enum
                      {"branch": "Always Path",
                       "condition": [[_cmp_var("status", "neq", "Expired")]]},
                      # no condition at all
                      {"branch": "No Condition Path", "condition": []},
                      # AND of nothing is vacuously true
                      {"branch": "Vacuous Path", "condition": [[]]},
                  ],
                  "default_branch": "Fallback",
                  "input_schema": {"type": "object",
                                   "properties": {"status": {"type": "string"}}}}),
    ]
    for branch in ("Always Path", "No Condition Path", "Vacuous Path", "Fallback"):
        steps.append(_code_step(
            branch, [("Extract Fields", "data.status.value", "status")],
            {"body": {"type": "string"}},
            parent_conditions=[{"decision_step": "Gate", "branch": branch}]))
    steps.append(_step(
        "Notify", "email",
        input_mappings=[_dep(b, "body", "p%d" % i) for i, b in enumerate(
            ("Always Path", "No Condition Path", "Vacuous Path", "Fallback"))],
        config={"to": [], "subject": "x"}))
    return _write(tmp, "syn_condition_edges",
                  _wf("Syn Condition Edges", steps)), {
        "expect_codes": {"ALWAYS_TRUE_CASE", "EMPTY_CONDITION", "EMPTY_AND_GROUP"},
        "expect_warning_codes": {"EMPTY_CONDITION"},
        "forbid_codes": {"UNREACHABLE_STEP", "DIAMOND_AND_OUT"},
        "blockers": 0,
        "all_reachable": True,
    }


def _syn_gate_variants(tmp: str) -> Tuple[str, Dict[str, Any]]:
    """The gate shapes the corpus cannot exercise, since it has no gates at
    all: a partly-read hold passthrough, a form nobody reads, a form with no
    fields, a pause somebody wires off, and enable_rerun with real upstream."""
    steps = [
        _prepare(),
        _code_step("Build Report", [("Prepare Documents", "documents", "documents")],
                   {"markdown_content": {"type": "string"},
                    "excel_url": {"type": "string"}}),
        # Two passthrough params, only one read downstream.
        _step("Review Report", "hold", enable_rerun=True,
              input_mappings=[_dep("Build Report", "markdown_content",
                                   "markdown_content"),
                              _dep("Build Report", "excel_url", "excel_url")],
              config={}),
        _code_step("Render", [("Review Report", "markdown_content", "md")],
                   {"body": {"type": "string"}}),
        # A form with fields that nothing reads.
        _step("Unread Form", "manual_input",
              input_mappings=[_dep("Build Report", "markdown_content", "ctx")],
              config={"message": "Notes?", "input_schema": {
                  "type": "object", "properties": {
                      "note_text": {"title": "Notes", "type": "text",
                                    "input_type": "text"}}}}),
        # A form with no fields at all.
        _step("Empty Form", "manual_input",
              input_mappings=[_dep("Build Report", "markdown_content", "ctx")],
              config={"message": "Continue?", "input_schema": {
                  "type": "object", "properties": {}}}),
        # Something wires off a pause, whose whole output is {status}.
        _step("Approve", "pause",
              input_mappings=[_dep("Render", "body", "ctx")], config={}),
        _code_step("After Approval", [("Approve", "status", "status")],
                   {"body": {"type": "string"}}),
        _step("Notify", "email",
              input_mappings=[_dep("After Approval", "body", "body")],
              config={"to": [], "subject": "x"}),
    ]
    return _write(tmp, "syn_gate_variants", _wf("Syn Gate Variants", steps)), {
        "expect_codes": {"HOLD_PARAM_UNCONSUMED", "MANUAL_FORM_UNREAD",
                         "MANUAL_INPUT_NO_FIELDS", "PAUSE_OUTPUT_READ",
                         "GATE_RERUN_SET", "MULTIPLE_GATES_ON_PATH"},
        "forbid_codes": {"UNREACHABLE_STEP", "UNREACHABLE_GATE",
                         "HOLD_OUTPUT_UNCONSUMED", "GATE_RERUN_NO_UPSTREAM"},
        "blockers": 0,
        "all_reachable": True,
    }


def _syn_cycle(tmp: str) -> Tuple[str, Dict[str, Any]]:
    """A two-step cycle with no way in.

    Neither step has a predecessor outside the cycle, so neither is an entry
    and neither is graph-reachable. pass 0 already reports DEPENDENCY_CYCLE as
    a blocker, so this pass must say it cannot place them and stop there rather
    than double-reporting an unreachability that has a known cause.
    """
    steps = [
        _prepare(),
        _code_step("Ping", [("Pong", "body", "b")], {"body": {"type": "string"}}),
        _code_step("Pong", [("Ping", "body", "b")], {"body": {"type": "string"}}),
        _email("Notify", "Prepare Documents"),
    ]
    return _write(tmp, "syn_cycle", _wf("Syn Cycle", steps)), {
        "expect_codes": {"UNREACHABLE_IN_CYCLE"},
        # The cycle is pass 0's blocker to raise, not ours.
        "forbid_codes": {"UNREACHABLE_STEP", "UNREACHABLE_GATE"},
        "blockers": 0,
    }


SYNTHETIC = [
    ("empty default_branch with downstream steps", _syn_empty_default),
    ("branch naming a nonexistent step", _syn_dead_branch),
    ("two decisions that AND out at a join", _syn_and_out),
    ("branch terminating in nothing", _syn_branch_dead_end),
    ("three concurrent branches from one decision", _syn_three_concurrent),
    ("nested decision under another branch", _syn_nested),
    ("impossible case / cell operand / sprawl", _syn_impossible_case),
    ("manual gate audit", _syn_gates),
    ("stale stamping / dead default_branch", _syn_stamping),
    ("sampled enumeration (262k combinations)", _syn_sampling),
    ("undeclared / unwired condition variable", _syn_dead_variable),
    ("constant-true / empty conditions", _syn_condition_edges),
    ("gate variants the corpus has none of", _syn_gate_variants),
    ("cycle with no way in", _syn_cycle),
]


def self_test() -> int:
    failures: List[str] = []
    rows: List[List[Any]] = []

    corpus = [os.path.join(CORPUS_DIR, n) for n in sorted(os.listdir(CORPUS_DIR))
              if n.endswith(".json")] if os.path.isdir(CORPUS_DIR) else []
    if os.path.exists(EXAMPLE):
        corpus.append(os.path.abspath(EXAMPLE))
    if not corpus:
        print("error: no corpus workflows found under %s" % CORPUS_DIR,
              file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        # ---- corpus: all five are validator-clean, so expect zero blockers.
        for path in corpus:
            label = os.path.basename(path)[:-5]
            try:
                findings, payload, analysis = _analyse_workflow(path, tmp, label)
            except Exception as exc:  # noqa: BLE001 - self-test reports, never crashes
                failures.append("%s: analysis raised %s: %s"
                                % (label, type(exc).__name__, exc))
                continue
            blockers = [f for f in findings if f["severity"] == "blocker"]
            if blockers:
                failures.append("%s: expected 0 blockers, got %d: %s"
                                % (label, len(blockers),
                                   ", ".join(sorted({b["code"] for b in blockers}))))
            # graph.roots and this pass's own entry computation are two
            # independent definitions of the same thing. Pin their agreement so
            # they cannot drift: paths.py never reads graph.roots, and that
            # independence is only a safeguard while the two still agree.
            roots = (facts_of(analysis).get("graph") or {}).get("roots") or []
            if sorted(roots) != sorted(analysis.entries):
                failures.append(
                    "%s: graph.roots is %s but this pass computes entries %s. "
                    "The two definitions of 'entry step' have diverged; decide "
                    "which is right before trusting reachability."
                    % (label, roots, analysis.entries))
            expected_entries = CORPUS_ENTRIES.get(label)
            if expected_entries is not None \
                    and sorted(analysis.entries) != sorted(expected_entries):
                failures.append(
                    "%s: entry steps are %s, expected %s. An extra entry means a "
                    "step's wiring is not in graph.adjacency, so reachability was "
                    "computed against an incomplete graph."
                    % (label, analysis.entries, expected_entries))
            dependency_edges = [e for e in (facts_of(analysis).get("edges") or [])
                                if e.get("via") == "dependencies"]
            if dependency_edges:
                adjacency_pairs = {
                    (src, dst) for src, dsts in analysis.adjacency.items()
                    for dst in dsts}
                for edge in dependency_edges:
                    pair = (edge.get("from_step"), edge.get("to_step"))
                    if pair[0] and pair[1] and pair not in adjacency_pairs:
                        failures.append(
                            "%s: edge %s -> %s is recorded with via='dependencies' "
                            "but is missing from graph.adjacency" % (label,) + pair)
            failures.extend(_model_invariants(label, payload))
            unreachable = [r for r in payload["reachability"] if not r["reachable"]]
            if unreachable:
                failures.append("%s: %d step(s) reported unreachable: %s"
                                % (label, len(unreachable),
                                   ", ".join(r["step"] for r in unreachable[:5])))

            # THE GOTCHA. reference: CONTRACT.md "Notes on producing it" —
            # decision operands reference upstream steps inline while
            # input_mappings is []. If the gate comes back unreachable the graph
            # lost those edges, and that is a blocker on the sub-skill itself.
            if label == "cna_dua_audit_lpl":
                gate = "LPL Comparison Gate"
                row = next((r for r in payload["reachability"]
                            if r["step"] == gate), None)
                if row is None:
                    failures.append("%s: %r is not in the reachability table"
                                    % (label, gate))
                elif not row["reachable"]:
                    failures.append(
                        "%s: %r came back UNREACHABLE. Its operand references "
                        "'Prepare LPL Comparison Data' inline while input_mappings "
                        "is [] — the graph lost the decision_operand edges. This is "
                        "a blocker on the sub-skill, not on the workflow."
                        % (label, gate))
                elif not row["graph_reachable"]:
                    failures.append("%s: %r is not graph-reachable" % (label, gate))
                # The gate has one case plus a non-empty default, so exactly
                # two worlds; both land on the same email, so they collapse to
                # one terminal-group whose branches column keeps them apart.
                if len(payload["paths"]) != 2:
                    failures.append("%s: expected 2 decision worlds, got %d"
                                    % (label, len(payload["paths"])))
                if len(payload["path_groups"]) != 1:
                    failures.append("%s: expected 1 terminal-group, got %d"
                                    % (label, len(payload["path_groups"])))
                branch_sets = payload["path_groups"][0]["branch_sets"] \
                    if payload["path_groups"] else []
                if sorted(branch_sets) != [["Compare LPL Documents"],
                                           ["LPL Comparison Skipped Notice"]]:
                    failures.append("%s: expected the two gate branches to be "
                                    "distinguished, got %r" % (label, branch_sets))

            counts = {"blocker": 0, "warning": 0, "note": 0}
            for item in findings:
                counts[item["severity"]] += 1
            rows.append([label, len(analysis.steps), len(analysis.decisions),
                         analysis.lenient["total_worlds"],
                         len(payload["path_groups"]),
                         sum(1 for r in payload["reachability"] if r["reachable"]),
                         len(analysis.manual_steps),
                         ", ".join(analysis.entries) or "none",
                         "%d/%d/%d" % (counts["blocker"], counts["warning"],
                                       counts["note"])])

        print("corpus:")
        for line in table(rows, ["workflow", "steps", "dec", "worlds", "outcomes",
                                 "reachable", "gates", "entries", "b/w/n"],
                          max_rows=10):
            print(line)

        # ---- synthetic: exercise the logic the corpus does not reach.
        syn_rows: List[List[Any]] = []
        for description, builder in SYNTHETIC:
            path, spec = builder(tmp)
            label = os.path.basename(path)[:-5]
            try:
                findings, payload, analysis = _analyse_workflow(path, tmp, label)
            except Exception as exc:  # noqa: BLE001
                failures.append("%s: analysis raised %s: %s"
                                % (label, type(exc).__name__, exc))
                syn_rows.append([description, "CRASH", "-"])
                continue
            codes = _codes(findings)
            blockers = [f for f in findings if f["severity"] == "blocker"]
            local: List[str] = _model_invariants(label, payload)

            for code in spec.get("expect_codes", ()):
                if code not in codes:
                    local.append("missing %s" % code)
            for code in spec.get("forbid_codes", ()):
                if code in codes:
                    local.append("unexpected %s (%s)"
                                 % (code, ", ".join(f["step"] or "-" for f in findings
                                                    if f["code"] == code)))
            for code in spec.get("expect_blocker_codes", ()):
                if code not in {b["code"] for b in blockers}:
                    local.append("%s is not a blocker" % code)
            for code in spec.get("expect_warning_codes", ()):
                if code not in {f["code"] for f in findings
                                if f["severity"] == "warning"}:
                    local.append("%s is not a warning" % code)
            if "blockers" in spec and len(blockers) != spec["blockers"]:
                local.append("expected %d blockers, got %d (%s)"
                             % (spec["blockers"], len(blockers),
                                ", ".join(sorted({b["code"] for b in blockers}))))
            if "blockers_min" in spec and len(blockers) < spec["blockers_min"]:
                local.append("expected >=%d blockers, got %d"
                             % (spec["blockers_min"], len(blockers)))
            for step in spec.get("expect_blocker_steps", ()):
                if step not in {b["step"] for b in blockers}:
                    local.append("no blocker on step %r" % step)
            if "worlds" in spec and analysis.lenient["total_worlds"] != spec["worlds"]:
                local.append("expected %d worlds, got %d"
                             % (spec["worlds"], analysis.lenient["total_worlds"]))
            if "max_concurrent_branches" in spec:
                widest = max((len(sel) for world in analysis.lenient["worlds"]
                              for sel in world["selected"].values()), default=0)
                if widest != spec["max_concurrent_branches"]:
                    local.append("widest concurrent branch set is %d, expected %d"
                                 % (widest, spec["max_concurrent_branches"]))
            if spec.get("impossible_branch"):
                branch = spec["impossible_branch"]
                row = next((r for r in payload["reachability"]
                            if r["step"] == branch), None)
                if row is None:
                    local.append("%r missing from reachability" % branch)
                else:
                    if not row["reachable"]:
                        local.append("%r reported unreachable — an impossible-case "
                                     "inference must never harden into a blocker"
                                     % branch)
                    if row["strict_worlds_running"] != 0:
                        local.append("%r still runs in the strict model" % branch)
            if spec.get("sampled"):
                if analysis.lenient["complete"]:
                    local.append("enumeration reported complete, but this "
                                 "workflow has more combinations than MAX_WORLDS")
                if analysis.lenient["enumerated"] > MAX_WORLDS:
                    local.append("walked %d worlds, over the %d cap"
                                 % (analysis.lenient["enumerated"], MAX_WORLDS))
            if spec.get("all_reachable"):
                stuck = [r["step"] for r in payload["reachability"]
                         if not r["reachable"]]
                if stuck:
                    local.append("expected every step reachable, but %s are not"
                                 % ", ".join(stuck[:5]))
            if spec.get("nested_check"):
                by_step = {r["step"]: r for r in payload["reachability"]}
                total = analysis.lenient["enumerated"]
                for step in ("Escalate", "Approve", "Deep Review", "Fast Path"):
                    row = by_step.get(step)
                    if row is None or not row["reachable"]:
                        local.append("%r should be reachable" % step)
                    elif row["worlds_running"] >= total:
                        local.append("%r runs in every world; the nesting is not "
                                     "constraining anything" % step)
                inner = next((d for d in payload["decisions"]
                              if d["step"] == "Inner Gate"), None)
                if inner is None or not inner["reachable"]:
                    local.append("Inner Gate should be reachable")

            fired = sorted(codes)
            syn_rows.append([description,
                             "PASS" if not local else "FAIL",
                             ", ".join(fired) or "-"])
            if local:
                failures.extend("%s: %s" % (label, item) for item in local)

        print("")
        print("synthetic:")
        for line in table(syn_rows, ["scenario", "result", "codes fired"],
                          max_rows=len(SYNTHETIC)):
            print(line)

    print("")
    if failures:
        print("SELF-TEST FAILED (%d)" % len(failures))
        for item in failures[:40]:
            print("  - %s" % item)
        if len(failures) > 40:
            print("  ... %d more" % (len(failures) - 40))
        return 1
    print("SELF-TEST OK  paths.py: %d corpus workflows, %d synthetic scenarios"
          % (len(corpus), len(SYNTHETIC)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
