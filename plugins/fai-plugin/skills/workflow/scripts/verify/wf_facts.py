#!/usr/bin/env python3
"""wf_facts.py — pass 0. The ONLY reader of workflow.json in the verify sub-skill.

Produces facts.json: resolved output schemas, a flat field inventory, the edge
list with every output_attribute resolved to a leaf type, the DAG, decision
cases, manual gates, HTML producers, and counters. Every later pass reads that
file and never touches the workflow again (CONTRACT.md hard rule 2).

Usage:
    python3 wf_facts.py <workflow.json> --out <dir>
    python3 wf_facts.py --self-test

Every platform rule below carries a comment naming the workflow-skill
reference doc it came from. This sub-skill syncs with the workflow skill, not
the backend repo; where a rule is not in those docs the code emits `unknown`
plus a note rather than inventing behavior.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    MAX_LEAF_DEPTH, PROMPT_MIN_CHARS, SCHEMA_VERSION, PassOutput,
    comparable_field_map, comparable_fields, comparable_raw_paths,
    field_identity, finding, make_cell, make_file, resolve_path, sch,
    schema_leaf_paths, slug, table, type_name, types_compatible, unknown,
)

PASS = "facts"

# --------------------------------------------------------------------------- #
# Step-type tables. Sources named per table.
# --------------------------------------------------------------------------- #

#: reference/input_mappings.md "Extraction outputs are cell-wrapped — .value
#: matters": the step types whose data leaves are cell-wrapped at runtime.
CELL_WRAPPED_TYPES = frozenset((
    "extract_from_multiple_sources",
    "extract_rows_from_multiple_sources",
    "extract_mixed_schema_from_multiple_sources",
    "agentic_extraction",
    "agentic_guideline_check",
    "sov_mapping",
    "generate_qa_table_from_agent",
    "multi_column_qa",
))

#: CONTRACT.md "Notes on producing it" — terminal output step types.
TERMINAL_TYPES = frozenset((
    "email", "fill_docx", "submission_summary_generator",
    "document_viewer", "text_block", "hold",
))

#: CONTRACT.md — manual gate step types. step_types/{pause,hold,manual_input}.md
MANUAL_TYPES = frozenset(("pause", "hold", "manual_input"))

#: CONTRACT.md — code step types. step_types/custom_step.md is the only place
#: new code belongs; `function` is legacy, edit-only.
CODE_TYPES = frozenset(("custom_step", "function"))

#: reference/output_types.md "Auto-fill map" — the step families whose output
#: is the TableV1 envelope {schema, data, user_documents, metadata}.
TABLE_ENVELOPE_TYPES = frozenset((
    "extract_from_multiple_sources", "extract_rows_from_multiple_sources",
    "extract_mixed_schema_from_multiple_sources", "agentic_extraction",
    "agentic_guideline_check", "extract_guidelines", "check_guidelines",
    "generate_qa_table_from_agent", "generate_qa_table_from_kb",
    "multi_column_qa", "sov_mapping", "ofac_agent", "osha_agent",
    "trellis_law", "compare_document_data", "combine_kv_tables",
    "execution_matching", "run_workflow",
    # Integration connectors (step_types/integrations.md — shared connector
    # idiom, all emit the table envelope).
    "google_maps", "nhtsa", "riskmeter", "hazardhub", "maprisk", "pitchbook",
    "cotality_valuation", "snapsheet", "snapsheet_payments", "applied_epic",
    "benefitpoint", "qqcatalyst", "ams360", "sharepoint", "outlook_mail",
    "imageright", "financepro", "enrich_addresses_with_gmaps",
    "extract_from_document", "extract_from_email",
))

#: The config keys that can hold a declared schema, in lookup order.
#: Sourced from the per-type docs: `extraction_schema` (the extraction family,
#: agentic_extraction), `output_schema` (code steps, ofac/osha, multi_column_qa,
#: web_agent, integrations), `comparison_schema` (compare_document_data).
SCHEMA_CONFIG_KEYS = ("extraction_schema", "comparison_schema", "output_schema", "schema")

#: reference/input_mappings.md — named types recognized inside a JSON Schema
#: `type` slot. step_types/custom_step.md: 'declare {"type": "file"}'.
NAMED_TYPE_SLOTS = {
    "file": "file",
    "video_file": "video_file",
    "cell": "cell",
    "email_headers": "email_headers",
    "citation": "citation",
    "docx_image": "docx_image",
    "sov_field": "sov_field",
}

#: step_types/manual_input.md "Input field types" + reference/output_types.md
#: "Dynamic schema for manual_input" — DataType enum -> output schema entry.
MANUAL_FIELD_TYPES = {
    "text": lambda: sch("string"),
    "number": lambda: sch("number"),
    "boolean": lambda: sch("boolean"),
    "file": make_file,
    "file_list": lambda: sch("array", items=make_file()),
    "docx_image": lambda: sch("docx_image", properties={
        "document": make_file(),
        "settings": sch("object", properties={
            "position": sch("string"), "resize_mode": sch("string"),
            "target_width_inches": sch("number"),
            "target_height_inches": sch("number"),
        }),
    }),
}

#: reference/patterns/html_summary_dashboard.md — "Returning HTML in a key
#: other than email_body: the email step expects this exact key." Additional
#: keys accepted because HtmlOutputConfig.content_key is author-chosen
#: (reference/output_types.md "HtmlOutputConfig").
HTML_OUTPUT_KEYS = ("email_body", "html_content", "html", "body_html", "markdown_content")


# --------------------------------------------------------------------------- #
# JSON Schema -> normalized schema
# --------------------------------------------------------------------------- #

def _first_type(node: Dict[str, Any]) -> Optional[str]:
    raw = node.get("type")
    if isinstance(raw, list):
        for item in raw:
            if item != "null":
                return item
        return None
    return raw if isinstance(raw, str) else None


def norm_schema(node: Any, _depth: int = 0) -> Dict[str, Any]:
    """Normalize a JSON Schema fragment into the CONTRACT schema language."""
    if _depth > MAX_LEAF_DEPTH:
        return unknown("nesting deeper than %d not walked" % MAX_LEAF_DEPTH)
    if not isinstance(node, dict):
        return unknown("schema fragment is not an object")

    extras: Dict[str, Any] = {}
    for key in ("title", "description"):
        if isinstance(node.get(key), str) and node[key]:
            extras[key] = node[key]
    if isinstance(node.get("enum"), list) and node["enum"]:
        extras["allowed_values"] = node["enum"]
    extras["origin"] = "declared"

    declared = _first_type(node)

    if declared in NAMED_TYPE_SLOTS:
        kind = NAMED_TYPE_SLOTS[declared]
        if kind == "file":
            return make_file(**extras)
        if kind == "cell":
            return make_cell(None, **extras)
        return sch(kind, open=True, **extras)

    if declared == "object" or (declared is None and isinstance(node.get("properties"), dict)):
        props = {}
        for name, child in (node.get("properties") or {}).items():
            props[name] = norm_schema(child, _depth + 1)
        additional = node.get("additionalProperties")
        return sch("object", properties=props,
                   open=bool(additional) if additional is not None else False,
                   **extras)

    if declared == "array":
        items = node.get("items")
        inner = norm_schema(items, _depth + 1) if isinstance(items, dict) else unknown(
            "array item type not declared")
        return sch("array", items=inner, **extras)

    if declared == "enum":
        # The platform dropdown convention, NOT JSON Schema `enum: [...]`:
        # reference/validation_errors.md ('Use "type": "enum" with "options":
        # [{id, name, value}, ...]') and step_types/agentic_extraction.md.
        # `enum_type` names the underlying primitive, so an enum is a
        # primitive carrying allowed_values — which means it cell-wraps like
        # any other primitive inside an extraction schema.
        underlying = node.get("enum_type") or "string"
        if underlying in ("number", "integer"):
            kind = "number"
        elif underlying == "boolean":
            kind = "boolean"
        else:
            kind = "string"
        if "allowed_values" not in extras:
            values = [o.get("value") for o in (node.get("options") or [])
                      if isinstance(o, dict) and o.get("value") is not None]
            if values:
                extras["allowed_values"] = values
        return sch(kind, **extras)

    if declared == "string":
        return sch("string", **extras)
    if declared in ("number", "integer"):
        return sch("number", **extras)
    if declared == "boolean":
        return sch("boolean", **extras)

    return unknown("no usable `type` in declared schema")


#: reference/input_mappings.md "Only primitive leaves are wrapped" — the three
#: primitive kinds that become cells. Every container keeps its shape.
_PRIMITIVE_KINDS = frozenset(("string", "number", "boolean"))


def wrap_cells(schema: Dict[str, Any], _depth: int = 0) -> Dict[str, Any]:
    """Apply extraction cell wrapping to a normalized data schema.

    reference/input_mappings.md "Only primitive leaves are wrapped — nested
    arrays and objects stay navigable": wrapping is a leaf-by-leaf structural
    walk. An array stays an array with its element schema transformed in turn
    (the element gains a `.0` glom segment); an object stays an object with
    each property transformed; only a primitive becomes a cell.

    So for a nested schedule, `data.vehicles.0.vin.value` is the correct
    spelling and `data.vehicles` is the whole list of row objects.
    `data.vehicles.value` is wrong: an array node has no `value` key, so that
    path resolves to nothing at runtime and every downstream step then runs
    green on empty data — the same silent-failure class as a classify-class
    typo.
    """
    if not isinstance(schema, dict) or _depth > MAX_LEAF_DEPTH:
        return schema
    kind = schema.get("kind")
    if kind == "object":
        props = {}
        for name, child in (schema.get("properties") or {}).items():
            props[name] = wrap_cells(child, _depth + 1)
        out = dict(schema)
        out["properties"] = props
        return out
    if kind == "array":
        out = dict(schema)
        items = schema.get("items")
        if isinstance(items, dict):
            out["items"] = wrap_cells(items, _depth + 1)
        return out
    if kind in _PRIMITIVE_KINDS:
        keep = {k: schema[k] for k in ("title", "description", "allowed_values")
                if k in schema}
        return make_cell(schema, **keep)
    # cell, unknown, any, file and the other named types keep their shape.
    return schema


# --------------------------------------------------------------------------- #
# Per-type output derivation
# --------------------------------------------------------------------------- #

def _email_headers() -> Dict[str, Any]:
    # step_types/prepare_documents.md — headers shape.
    return sch("email_headers", properties={
        "from": sch("string"), "to": sch("string"), "cc": sch("string"),
        "subject": sch("string"), "date": sch("string"),
    })


def dispatcher_output() -> Dict[str, Any]:
    """CONTRACT.md: the implicit root's synthetic output."""
    return sch("object", properties={
        "user_document_ids": sch("array", items=sch("string")),
        "documents": sch("array", items=make_file()),
    }, open=True, origin="auto")


def _declared_config_schema(cfg: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """The step's declared schema and which config key it came from."""
    for key in SCHEMA_CONFIG_KEYS:
        raw = cfg.get(key)
        if isinstance(raw, dict) and (raw.get("properties") or raw.get("type") or raw.get("items")):
            return norm_schema(raw), key
    return None, None


def _table_envelope(data_schema: Dict[str, Any], with_kb: bool) -> Dict[str, Any]:
    """The TableV1 envelope shared by the extraction / agent / connector family.

    Sources: step_types/agentic_extraction.md, extract_from_multiple_sources.md,
    extract_rows_from_multiple_sources.md, agentic_guideline_check.md,
    ofac_agent.md, compare_document_data.md, combine_kv_tables.md — all four
    keys {schema, data, user_documents, metadata}.
    """
    props: Dict[str, Any] = {
        "schema": sch("object", open=True, origin="auto"),
        "data": data_schema,
        "user_documents": sch("array", items=make_file(), origin="auto"),
        "metadata": sch("object", open=True, origin="auto"),
    }
    if with_kb:
        # agentic_extraction.md / agentic_guideline_check.md: `kb` is a valid
        # output_attribute — "The KB handle the agent used."
        props["kb"] = kb_handle()
    # Extraction data lives in TableV1; the runtime output dict also carries
    # table_v1_log_id (step_types/agentic_extraction.md).
    props["table_v1_log_id"] = sch("string", origin="auto")
    return sch("object", properties=props, origin="auto")


def kb_handle() -> Dict[str, Any]:
    # step_types/knowledge_base.md — the `kb` sub-object is the handle.
    return sch("knowledge_base", properties={
        "collection_name": sch("string"),
        "sub_collection_name": sch("string"),
        "embedding_model_type": sch("string"),
        "pdf_chunker_type": sch("string"),
        "query_modes": sch("array", items=sch("string")),
    }, origin="auto")


def derive_output(step: Dict[str, Any], notes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The resolved output schema for one step."""
    stype = step.get("type")
    name = step.get("name") or "(unnamed)"
    cfg = step.get("config") or {}
    declared_step_schema = step.get("output_schema")

    # Pattern C (reference/output_types.md): function / custom_step declare
    # their own output_schema in config. Step-level output_schema also honored
    # when present (auto_generate_output_schema fills it for table/email).
    if stype in CODE_TYPES:
        raw = cfg.get("output_schema")
        if isinstance(raw, dict) and (raw.get("properties") or raw.get("type")):
            return norm_schema(raw)
        if isinstance(declared_step_schema, dict) and declared_step_schema.get("properties"):
            return norm_schema(declared_step_schema)
        notes.append(finding(
            "CODE_STEP_NO_OUTPUT_SCHEMA", "warning",
            "Code step declares no output schema",
            "%s is a %s with no config.output_schema, so nothing downstream of it "
            "can be type-checked." % (name, stype),
            fix="Add config.output_schema — it is required at save time "
                "(step_types/custom_step.md).",
            step=name, group_key="CODE_STEP_NO_OUTPUT_SCHEMA"))
        return sch("object", open=True, origin="inferred")

    if stype == "prepare_documents":
        # step_types/prepare_documents.md "Output schema"
        return sch("object", properties={
            "documents": sch("array", items=make_file()),
            "email_body": sch("string"),
            "eml_user_document_id": sch("string"),
            "headers": _email_headers(),
            "emails": sch("array", items=sch("object", properties={
                "eml_user_document_id": sch("string"),
                "filename": sch("string"),
                "email_body": sch("string"),
                "headers": _email_headers(),
                "attachments": sch("array", items=make_file()),
            }, open=True)),
            "eml_files": sch("array", items=make_file()),
        }, origin="auto")

    if stype == "classify_documents":
        # step_types/classify_documents.md "Output schema": one key per
        # configured class. With output_empty_classes true (the default) every
        # declared class is present, so a declared class name always resolves
        # and an undeclared one is a typo.
        classes = _classify_class_names(cfg)
        props = {cls: sch("array", items=make_file()) for cls in classes}
        fallback = cfg.get("fallback_class")
        if isinstance(fallback, str) and fallback and fallback not in props:
            props[fallback] = sch("array", items=make_file())
            classes = classes + [fallback]
        return sch("object", properties={
            "documents": sch("object", properties=props,
                             dynamic_keys=sorted(props.keys()), origin="config"),
        }, origin="auto")

    if stype == "knowledge_base":
        # step_types/knowledge_base.md "Output schema". open=True because
        # reference/input_mappings.md notes the resolver wraps KB output.
        return sch("object", properties={
            "knowledge_base_initialized": sch("boolean"),
            "collection_name": sch("string"),
            "sub_collection_name": sch("string"),
            "embedding_model_type": sch("string"),
            "pdf_chunker_type": sch("string"),
            "query_modes": sch("array", items=sch("string")),
            "document_count": sch("number"),
            "kb": kb_handle(),
        }, open=True, origin="auto")

    if stype == "decision":
        # step_types/decision.md "Output schema"
        return sch("object", properties={
            "branches_to_execute": sch("array", items=sch("string")),
            "evaluation_results": sch("array", items=unknown(
                "evaluation_results row shape is explicitly not a contract")),
        }, origin="auto")

    if stype == "pause":
        # step_types/pause.md "Output schema"
        return sch("object", properties={"status": sch("string")}, origin="auto")

    if stype == "hold":
        # step_types/hold.md: declared schema is {data}, but the runtime
        # spreads every input_parameter_name into the result, so the object is
        # open and the extra members are `any`.
        props: Dict[str, Any] = {"data": sch("any")}
        for mapping in step.get("input_mappings") or []:
            param = mapping.get("input_parameter_name")
            if isinstance(param, str) and param and param not in props:
                props[param] = sch("any")
        return sch("object", properties=props, open=True, origin="auto")

    if stype == "manual_input":
        # reference/output_types.md "Dynamic schema for manual_input" +
        # step_types/manual_input.md field-type table.
        props = {}
        schema_cfg = cfg.get("input_schema") or {}
        for fname, field in (schema_cfg.get("properties") or {}).items():
            props[fname] = _manual_field_schema(field)
        props["status"] = sch("string", allowed_values=["paused", "completed"])
        return sch("object", properties=props, open=True, origin="config")

    if stype == "email":
        # step_types/email.md "Output schema"
        return sch("object", properties={
            "email_body": sch("string"),
            "headers": _email_headers(),
            "attachments": sch("array", items=make_file()),
        }, origin="auto")

    if stype == "fill_docx":
        # step_types/fill_docx.md "Output schema" — singular nested path.
        return sch("object", properties={
            "generated_document": sch("object", properties={
                "user_document_id": sch("string"),
                "filename": sch("string"),
            }),
        }, origin="auto")

    if stype == "submission_summary_generator":
        # step_types/submission_summary_generator.md "Output schema"
        return sch("object", properties={"summary": sch("string")}, origin="auto")

    if stype == "compare_documents":
        # reference/output_types.md auto-fill map -> compare_log. The shape is
        # not documented in the workflow skill's references.
        return sch("object", open=True, origin="auto")

    if stype == "web_search":
        # reference/output_types.md auto-fill map -> text.
        return sch("object", open=True, origin="auto")

    if stype in ("text_block", "document_viewer", "loop", "workflow_dispatcher"):
        return sch("object", open=True, origin="auto")

    if stype in TABLE_ENVELOPE_TYPES:
        declared, _key = _declared_config_schema(cfg)
        data_schema = _extraction_data_schema(stype, declared, name, notes)
        with_kb = stype in ("agentic_extraction", "agentic_guideline_check",
                            "generate_qa_table_from_agent", "multi_column_qa")
        return _table_envelope(data_schema, with_kb)

    # An unrecognized step type. Do not invent a shape.
    notes.append(finding(
        "UNKNOWN_STEP_TYPE_OUTPUT", "note",
        "Output shape not derivable for step type",
        "%s has type '%s', which has no output schema in the workflow skill's "
        "references. Paths off it are not verified." % (name, stype),
        fix="If this type is real, add step_types/%s.md; otherwise the type is wrong."
            % stype,
        step=name, group_key="UNKNOWN_STEP_TYPE_OUTPUT:%s" % stype))
    return sch("object", open=True, origin="inferred")


def _classify_class_names(cfg: Dict[str, Any]) -> List[str]:
    """Class names from a classify_documents config.

    step_types/classify_documents.md: the config key is `classes` (runtime
    rejects `categories`; validate_workflow.py catches that separately).
    Entries are objects carrying `name`, or bare strings.
    """
    raw = cfg.get("classes")
    names: List[str] = []
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict):
                value = entry.get("name")
                if isinstance(value, str) and value:
                    names.append(value)
            elif isinstance(entry, str) and entry:
                names.append(entry)
    elif isinstance(raw, dict):
        names = [k for k in raw.keys() if isinstance(k, str)]
    return names


def _manual_field_schema(field: Any) -> Dict[str, Any]:
    if not isinstance(field, dict):
        return unknown("manual_input field is not an object")
    ftype = field.get("type")
    builder = MANUAL_FIELD_TYPES.get(ftype)
    base = builder() if builder else unknown(
        "manual_input field type %r is not in the documented DataType enum" % ftype)
    # step_types/manual_input.md: multi_select wraps the field's output type
    # in an array.
    if field.get("input_type") == "multi_select":
        base = sch("array", items=base)
    options = field.get("options")
    if isinstance(options, list) and options and base.get("kind") == "string":
        values = [o.get("value") for o in options
                  if isinstance(o, dict) and isinstance(o.get("value"), str)]
        if values:
            base = dict(base)
            base["allowed_values"] = values
    return base


def _extraction_data_schema(stype: str, declared: Optional[Dict[str, Any]],
                            name: str, notes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The `data` member for a table-envelope step.

    Shape comes from the declared schema's own type: an array-typed
    extraction_schema means row output (step_types/
    extract_rows_from_multiple_sources.md — "extraction_schema must be
    array-typed"), an object-typed one means field-keyed output
    (step_types/agentic_extraction.md).
    """
    wrap = stype in CELL_WRAPPED_TYPES
    if declared is None:
        return sch("object", open=True, origin="inferred")

    kind = declared.get("kind")
    if kind == "array":
        items = declared.get("items")
        row = items if isinstance(items, dict) else sch("object", open=True)
        return sch("array", items=wrap_cells(row) if wrap else row, origin="declared")
    if kind == "object":
        return wrap_cells(declared) if wrap else declared
    notes.append(finding(
        "EXTRACTION_SCHEMA_NOT_OBJECT_OR_ARRAY", "warning",
        "Declared extraction schema is neither object nor array",
        "%s declares a %s extraction schema, so its `data` shape cannot be "
        "derived." % (name, type_name(declared)),
        fix="Declare the extraction schema as an object (field-keyed) or an "
            "array (rows).",
        step=name, group_key="EXTRACTION_SCHEMA_NOT_OBJECT_OR_ARRAY"))
    return sch("object", open=True, origin="inferred")


# --------------------------------------------------------------------------- #
# Prompts, code, dependency walking
# --------------------------------------------------------------------------- #

#: CONTRACT.md: prompt_chars counts config strings over 200 chars plus static
#: input_mappings values over 200 chars. One helper owns the rule.
def _collect_prompts(step: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], int]:
    limit = PROMPT_MIN_CHARS
    out: List[Dict[str, Any]] = []
    cfg = step.get("config") or {}
    for key, value in cfg.items():
        if key == "code":
            continue
        if isinstance(value, str) and len(value) > limit:
            out.append({"key": key, "where": "config", "chars": len(value)})
    for mapping in step.get("input_mappings") or []:
        if mapping.get("input_type") in ("static", "prompt"):
            value = mapping.get("value")
            if isinstance(value, str) and len(value) > limit:
                out.append({"key": mapping.get("input_parameter_name") or "(unnamed)",
                            "where": "input_mappings", "chars": len(value)})
    return out, sum(p["chars"] for p in out)


class _ReturnScanner(ast.NodeVisitor):
    """Collect return-statement dict keys within one function, not nested ones."""

    def __init__(self) -> None:
        self.keys: Set[str] = set()
        self.dynamic = False

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return  # do not descend into nested defs

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        return

    def visit_Return(self, node: ast.Return) -> None:  # noqa: N802
        value = node.value
        if isinstance(value, ast.Dict):
            for key in value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    self.keys.add(key.value)
                else:
                    self.dynamic = True
        elif value is not None:
            self.dynamic = True


def analyze_code(code: str) -> Dict[str, Any]:
    """AST facts about a code step. Never returns the code itself."""
    info: Dict[str, Any] = {
        "is_async": False, "params": [], "has_kwargs": False,
        "returned_keys": [], "returns_dynamic": False,
        "imports": [], "syntax_ok": True, "parse_error": None,
        "entry_found": False,
    }
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        info["syntax_ok"] = False
        info["parse_error"] = "line %s: %s" % (getattr(exc, "lineno", "?"), exc.msg)
        return info

    imports: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.add("%s.%s" % (module, alias.name) if module else alias.name)
    info["imports"] = sorted(imports)

    # step_types/custom_step.md: the entry function is `run`.
    entry = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run":
            entry = node
            break
    if entry is None:
        return info

    info["entry_found"] = True
    info["is_async"] = isinstance(entry, ast.AsyncFunctionDef)
    args = entry.args
    params = [a.arg for a in list(getattr(args, "posonlyargs", [])) + list(args.args)
              if a.arg != "self"]
    params += [a.arg for a in args.kwonlyargs]
    info["params"] = params
    info["has_kwargs"] = args.kwarg is not None

    scanner = _ReturnScanner()
    for child in entry.body:
        scanner.visit(child)
    info["returned_keys"] = sorted(scanner.keys)
    info["returns_dynamic"] = scanner.dynamic
    return info


def _iter_dependency_refs(value: Any) -> List[Dict[str, Any]]:
    """Every {step_name, output_attribute} dict nested anywhere in `value`.

    Used for decision operands, which reference upstream steps inline while
    input_mappings stays empty (step_types/decision.md "One gotcha specific to
    decision"). CONTRACT.md flags missing these as the worst possible false
    positive.
    """
    found: List[Dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("step_name"), str):
                found.append(node)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return found


# --------------------------------------------------------------------------- #
# Raw leaf counting — kept comparable to an independent recount
# --------------------------------------------------------------------------- #

def _raw_leaves(node: Any, depth: int = 0) -> Tuple[int, int]:
    """Leaf count and max depth over a RAW JSON Schema fragment.

    Deliberately independent of norm_schema so the self-test can recompute
    counters.schema_leaves without trusting the normalizer.
    """
    if not isinstance(node, dict):
        return 0, depth
    props = node.get("properties") or {}
    if not props:
        items = node.get("items")
        if isinstance(items, dict):
            return _raw_leaves(items, depth + 1)
        return 1, depth
    total = 0
    deepest = depth
    for child in props.values():
        count, reached = _raw_leaves(child, depth + 1)
        total += count
        deepest = max(deepest, reached)
    return total, deepest


def _declared_schema_fragments(step: Dict[str, Any]) -> List[Dict[str, Any]]:
    cfg = step.get("config") or {}
    out = []
    for key in ("extraction_schema", "output_schema", "input_schema", "schema"):
        value = cfg.get(key)
        if isinstance(value, dict):
            out.append(value)
    if isinstance(step.get("output_schema"), dict):
        out.append(step["output_schema"])
    return out


# --------------------------------------------------------------------------- #
# Field inventory
# --------------------------------------------------------------------------- #

def _collect_fields(step_name: str, output: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flat inventory of the data-bearing leaves of one step's output."""
    fields: List[Dict[str, Any]] = []
    props = output.get("properties") or {}
    data = props.get("data")
    if isinstance(data, dict):
        _walk_fields(step_name, data, "data", fields, 1)
        return fields
    # Code steps and other producers: inventory their declared top level.
    for name in sorted(props):
        _walk_fields(step_name, props[name], name, fields, 1)
    return fields


def _walk_fields(step_name: str, node: Dict[str, Any], path: str,
                 out: List[Dict[str, Any]], depth: int) -> None:
    if not isinstance(node, dict) or depth > MAX_LEAF_DEPTH:
        return
    kind = node.get("kind")
    if kind == "cell":
        value = node.get("value_type") or unknown()
        out.append({
            "step": step_name,
            "path": path,
            "value_path": "%s.value" % path,
            "name": path.rsplit(".", 1)[-1],
            "type": type_name(value),
            "title": node.get("title") or value.get("title"),
            "description": node.get("description") or value.get("description"),
            "allowed_values": node.get("allowed_values") or value.get("allowed_values") or [],
            "is_cell": True,
            "depth": depth,
        })
        return
    if kind == "array":
        items = node.get("items")
        if isinstance(items, dict):
            _walk_fields(step_name, items, "%s.0" % path, out, depth + 1)
        return
    if kind == "object":
        props = node.get("properties") or {}
        if not props:
            out.append(_plain_field(step_name, node, path, depth))
            return
        for name in sorted(props):
            _walk_fields(step_name, props[name], "%s.%s" % (path, name), out, depth + 1)
        return
    out.append(_plain_field(step_name, node, path, depth))


def _plain_field(step_name: str, node: Dict[str, Any], path: str, depth: int) -> Dict[str, Any]:
    return {
        "step": step_name,
        "path": path,
        "value_path": path,
        "name": path.rsplit(".", 1)[-1],
        "type": type_name(node),
        "title": node.get("title"),
        "description": node.get("description"),
        "allowed_values": node.get("allowed_values") or [],
        "is_cell": False,
        "depth": depth,
    }


# --------------------------------------------------------------------------- #
# The build
# --------------------------------------------------------------------------- #

DISPATCHER = "Workflow Dispatcher"


def build_facts(path: str, out_dir: Optional[str] = None,
                ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    notes: List[Dict[str, Any]] = []
    abspath = os.path.abspath(path)
    size = os.path.getsize(abspath)
    with open(abspath) as handle:
        raw = json.load(handle)
    # Platform downloads wrap the definition in `content` (wb.py download).
    workflow = raw.get("content") if isinstance(raw.get("content"), dict) else raw
    steps_raw = workflow.get("steps")
    if not isinstance(steps_raw, list):
        raise ValueError("%s: no steps array" % path)

    # ---- steps + outputs
    steps: List[Dict[str, Any]] = []
    outputs: Dict[str, Dict[str, Any]] = {DISPATCHER: dispatcher_output()}
    code_steps: Dict[str, Any] = {}
    code_text: Dict[str, str] = {}
    all_names: Set[str] = set()

    for index, step in enumerate(steps_raw):
        name = step.get("name")
        if not isinstance(name, str) or not name:
            name = "(unnamed step %d)" % index
        if name in all_names:
            notes.append(finding(
                "DUPLICATE_STEP_NAME", "blocker",
                "Two steps share a name",
                "'%s' appears more than once. Every input_mapping that names it is "
                "ambiguous." % name,
                fix="Rename one of them with workflow_tools.py rename-step.",
                step=name, group_key="DUPLICATE_STEP_NAME:%s" % name))
        all_names.add(name)
        stype = step.get("type")
        cfg = step.get("config") or {}

        output = derive_output(step, notes)
        outputs[name] = output

        prompts, prompt_chars = _collect_prompts(step)
        code = cfg.get("code")
        code_chars = len(code) if isinstance(code, str) else 0
        code_info = None
        if stype in CODE_TYPES and isinstance(code, str) and code:
            info = analyze_code(code)
            info["declared_input_properties"] = sorted(
                (cfg.get("input_schema") or {}).get("properties", {}).keys())
            info["declared_output_properties"] = sorted(
                (cfg.get("output_schema") or {}).get("properties", {}).keys())
            # A code step's parameter types ARE declared — step_types/
            # custom_step.md makes config.input_schema and config.output_schema
            # both required at save time — they were simply not carried
            # through, leaving every code-step mapping untypeable. Publishing
            # them through the same normalizer as everything else lets
            # types_compatible() consume them directly, which is what brings
            # the cell-versus-scalar and container-`.value` detectors to code
            # steps at all, and that is where most custom logic lives.
            info["declared_input_types"] = {
                param: norm_schema(spec)
                for param, spec in ((cfg.get("input_schema") or {}).get("properties")
                                    or {}).items()
                if isinstance(spec, dict)
            }
            info["declared_output_types"] = {
                param: norm_schema(spec)
                for param, spec in ((cfg.get("output_schema") or {}).get("properties")
                                    or {}).items()
                if isinstance(spec, dict)
            }
            info["code_chars"] = code_chars
            info["code_file"] = None  # stamped below when out_dir is given
            code_steps[name] = info
            code_text[name] = code
            code_info = {"entry_found": info["entry_found"], "syntax_ok": info["syntax_ok"]}

        incremental = cfg.get("incremental_config") or step.get("incremental_config") or {}
        display = step.get("display_step")
        display = True if display is None else bool(display)

        steps.append({
            "index": index,
            "name": name,
            "type": stype,
            "display_step": display,
            "start_title": _title_text(step.get("start_title")),
            "end_title": _title_text(step.get("end_title")),
            "output_type": step.get("output_type"),
            "enable_rerun": bool(step.get("enable_rerun")),
            "incremental_behavior": (incremental.get("behavior")
                                     if isinstance(incremental, dict) else None),
            "parent_conditions": step.get("parent_conditions") or [],
            "loop_membership": step.get("loop_membership") or [],
            "is_terminal": stype in TERMINAL_TYPES,
            "is_manual": stype in MANUAL_TYPES,
            "is_hidden": not display,
            "size_bytes": len(json.dumps(step, default=str)),
            "model": cfg.get("model"),
            "reasoning_effort": cfg.get("reasoning_effort"),
            "verbosity": cfg.get("verbosity"),
            "prompt_chars": prompt_chars,
            "prompts": prompts,
            "code_chars": code_chars,
            "code": code_info,
            "packages": cfg.get("packages") or [],
            "output": output,
            "declared_output_schema_present": isinstance(step.get("output_schema"), dict),
        })

    # ---- edges
    edges: List[Dict[str, Any]] = []
    for step in steps_raw:
        to_step = step.get("name") or "(unnamed)"
        for mapping in step.get("input_mappings") or []:
            edges.extend(_edges_from_mapping(mapping, to_step, outputs, "input_mappings"))
        if step.get("type") == "decision":
            # The gotcha: operands hold dependencies while input_mappings is [].
            for ref in _iter_dependency_refs((step.get("config") or {}).get("cases")):
                edges.append(_make_edge(
                    to_step=to_step, to_param="(decision operand)",
                    input_type="dependency", from_step=ref.get("step_name"),
                    output_attribute=ref.get("output_attribute"),
                    source_index=0, multi_source=False, resolution_operator=None,
                    via="decision_operand", outputs=outputs))
        # Legacy `dependencies` wiring. SKILL.md forbids authoring it and the
        # migration is one-way and only on request, so record it faithfully
        # and never modernize it. These are real execution-order and data
        # edges: omitting them leaves holes in the DAG, which makes a step
        # with only legacy wiring look like a workflow entry point.
        for index, dep in enumerate(step.get("dependencies") or []):
            if not isinstance(dep, dict) or not dep.get("step_name"):
                continue
            edges.append(_make_edge(
                to_step=to_step, to_param=None,
                input_type="dependency", from_step=dep.get("step_name"),
                # field_selector is this shape's analogue of output_attribute;
                # null means the whole output dict, same as elsewhere.
                output_attribute=dep.get("field_selector"),
                source_index=index, multi_source=False, resolution_operator=None,
                via="dependencies", outputs=outputs))

    legacy_steps = sorted(
        s.get("name") for s in steps_raw
        if isinstance(s.get("dependencies"), list) and s.get("dependencies")
        and s.get("name"))
    if legacy_steps:
        notes.append(finding(
            "LEGACY_DEPENDENCIES", "note",
            "Workflow still uses legacy `dependencies` wiring",
            "%d step(s) wire upstream steps through `dependencies` rather than "
            "`input_mappings`: %s. The wiring works and is recorded faithfully, "
            "but SKILL.md's rule is that new steps leave `dependencies` empty "
            "and use `input_mappings` for both data flow and ordering."
            % (len(legacy_steps), ", ".join(legacy_steps[:6])
               + (" …" if len(legacy_steps) > 6 else "")),
            fix="Leave it alone unless you are explicitly migrating; the "
                "migration is one-way (`dependencies` to `input_mappings`, "
                "never the reverse).",
            evidence={"steps": legacy_steps}))

    for edge in edges:
        from_step = edge["from_step"]
        if from_step and from_step != DISPATCHER and from_step not in all_names:
            notes.append(finding(
                "MISSING_SOURCE_STEP", "blocker",
                "Input mapping names a step that does not exist",
                "%s reads from '%s', which is not a step in this workflow. Step "
                "names are case-sensitive." % (edge["to_step"], from_step),
                fix="Fix the step_name, or add the step.",
                step=edge["to_step"], evidence={"from_step": from_step},
                group_key="MISSING_SOURCE_STEP:%s" % from_step))

    # ---- graph
    adjacency: Dict[str, List[str]] = {}
    reverse: Dict[str, List[str]] = {}
    for edge in edges:
        src, dst = edge["from_step"], edge["to_step"]
        if not src or not dst or src == dst:
            continue
        adjacency.setdefault(src, [])
        if dst not in adjacency[src]:
            adjacency[src].append(dst)
        reverse.setdefault(dst, [])
        if src not in reverse[dst]:
            reverse[dst].append(src)
    for key in adjacency:
        adjacency[key].sort()
    for key in reverse:
        reverse[key].sort()

    # Entry steps: no in-workflow predecessor. The dispatcher is the platform's
    # implicit root and is not in `steps`, so a step fed only by it IS an entry
    # step. Counting it as a predecessor made `roots` empty on every real
    # workflow, because every entry step wires user_document_ids from it.
    roots = sorted(n for n in all_names
                   if not (set(reverse.get(n) or []) - {DISPATCHER}))
    # Two DIFFERENT questions, deliberately kept apart so a consumer cannot
    # accidentally ask one and read the other:
    #   terminals — steps whose TYPE is a terminal output type. Answers "does
    #               this workflow deliver something?" An extraction-only
    #               workflow legitimately has none.
    #   sinks     — steps with no consumer, structurally. Answers "where does
    #               the graph actually end?" Never empty on a valid DAG.
    terminals = sorted(s["name"] for s in steps if s["is_terminal"])
    sinks = sorted(n for n in all_names if not (adjacency.get(n) or []))
    cycles = _find_cycles(adjacency, all_names)
    for cycle in cycles:
        notes.append(finding(
            "DEPENDENCY_CYCLE", "blocker", "Dependency cycle",
            "These steps depend on each other in a loop: %s." % " -> ".join(cycle),
            fix="Break the cycle; input_mappings define execution order.",
            step=cycle[0], group_key="DEPENDENCY_CYCLE:%s" % cycle[0]))

    # ---- fields
    fields: List[Dict[str, Any]] = []
    for step in steps:
        fields.extend(_collect_fields(step["name"], step["output"]))

    # ---- decisions
    decisions = [_decision_facts(s, all_names, outputs)
                 for s in steps_raw if s.get("type") == "decision"]

    # ---- manual gates
    manual_steps = []
    for step in steps_raw:
        if step.get("type") not in MANUAL_TYPES:
            continue
        cfg = step.get("config") or {}
        form = list((cfg.get("input_schema") or {}).get("properties", {}).keys())
        manual_steps.append({
            "step": step.get("name"),
            "type": step.get("type"),
            "field_count": len(form),
            "fields": sorted(form),
            "passthrough_params": sorted(
                m.get("input_parameter_name") for m in (step.get("input_mappings") or [])
                if isinstance(m.get("input_parameter_name"), str)),
            "enable_rerun": bool(step.get("enable_rerun")),
        })

    # ---- html producers
    # CONTRACT.md hard rule 2: nothing but this script reads workflow.json.
    # html_probe needs the producer's source and the consuming email step's
    # routing, so both are published here rather than re-read downstream.
    raw_by_name = {s.get("name"): s for s in steps_raw if s.get("name")}
    _write_code_sidecars(code_steps, code_text, out_dir)
    html_producers = _html_producers(steps, code_steps, edges, raw_by_name, code_text)

    # CONTRACT.md B10: evidence the unused-field detector needs, and the
    # evidence it can never have.
    text_field_references = _text_field_references(steps_raw, fields)
    opaque_consumers = _opaque_consumers(steps_raw, fields, reverse, edges)

    # ---- counters
    leaves = 0
    max_depth = 0
    for step in steps_raw:
        for fragment in _declared_schema_fragments(step):
            count, depth = _raw_leaves(fragment)
            leaves += count
            max_depth = max(max_depth, depth)

    counters = {
        "steps": len(steps),
        "schema_leaves": leaves,
        # CONTRACT.md A2: `edges` counts every declared input (a non-dependency
        # input is recorded with from_step null so flow_check sees every
        # parameter); `dependency_edges` counts only the wired ones.
        "edges": len(edges),
        "dependency_edges": sum(1 for e in edges if e["input_type"] == "dependency"),
        # Separable by provenance so a workflow still carrying legacy wiring
        # is visible in the numbers, not only in the edge list.
        "input_mapping_edges": sum(1 for e in edges if e["via"] == "input_mappings"),
        "decision_operand_edges": sum(1 for e in edges if e["via"] == "decision_operand"),
        "dependencies_edges": sum(1 for e in edges if e["via"] == "dependencies"),
        "max_schema_depth": max_depth,
        "prompt_chars": sum(s["prompt_chars"] for s in steps),
        "code_chars": sum(s["code_chars"] for s in steps),
        "decisions": len(decisions),
        "cases": sum(len(d["cases"]) for d in decisions),
        "manual_steps": len(manual_steps),
        "hidden_steps": sum(1 for s in steps if s["is_hidden"]),
        "html_producers": len(html_producers),
        "text_field_references": sum(len(t["references"]) for t in text_field_references),
        # B10: fields whose usage can never be proven or disproven offline.
        # NOT the same as unused — the verdict is "cannot determine".
        "fields_evidence_unavailable": sum(o["fields_affected"] for o in opaque_consumers),
        "unresolved_edges": sum(1 for e in edges if not e["resolved"]),
        "fields": len(fields),
    }

    options = workflow.get("options") or {}
    facts = {
        "schema_version": SCHEMA_VERSION,
        "generated_by": "wf_facts.py",
        "workflow": {
            "path": abspath,
            "name": workflow.get("name"),
            "bytes": size,
            "step_count": len(steps),
            "options": options,
            "control_plane_mappings": _safe_control_plane(options),
        },
        "steps": steps,
        "code_steps": code_steps,
        "edges": edges,
        "fields": fields,
        "decisions": decisions,
        "manual_steps": manual_steps,
        "graph": {
            "adjacency": adjacency,
            "reverse": reverse,
            "roots": roots,
            "terminals": terminals,
            "sinks": sinks,
            "cycles": cycles,
        },
        "html_producers": html_producers,
        "text_field_references": text_field_references,
        "opaque_consumers": opaque_consumers,
        "counters": counters,
    }
    return facts, notes


def _title_text(value: Any) -> Optional[str]:
    """start_title / end_title may be a bare string OR a WorkflowField
    (reference/input_mappings.md "WorkflowField — the same pattern")."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        inner = value.get("value")
        return inner if isinstance(inner, str) else None
    return None


def _safe_control_plane(options: Dict[str, Any]) -> Dict[str, Any]:
    """Secret KEYS only, never values (CONVENTIONS.md: never print secrets)."""
    mappings = options.get("control_plane_mappings")
    if isinstance(mappings, dict):
        return {"keys": sorted(str(k) for k in mappings.keys())}
    if isinstance(mappings, list):
        return {"keys": sorted(str(k) for k in mappings)}
    return {"keys": []}


def _make_edge(to_step: str, to_param: str, input_type: str, from_step: Optional[str],
               output_attribute: Optional[str], source_index: int, multi_source: bool,
               resolution_operator: Optional[str], via: str,
               outputs: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    source_output = outputs.get(from_step) if from_step else None
    result = resolve_path(source_output, output_attribute) if source_output is not None else {
        "ok": False, "error": "source step output unknown", "at": "", "available": []}
    resolved = bool(result.get("ok"))
    schema = result.get("type") if resolved else None
    return {
        "to_step": to_step,
        "to_param": to_param,
        "input_type": input_type,
        "from_step": from_step,
        "output_attribute": output_attribute,
        "source_index": source_index,
        "multi_source": multi_source,
        "resolution_operator": resolution_operator,
        "via": via,
        "resolved": resolved,
        "resolved_type": type_name(schema) if resolved else None,
        "resolved_schema": schema,
        "resolve_error": None if resolved else result.get("error"),
        "resolve_at": None if resolved else result.get("at"),
        "resolve_available": [] if resolved else result.get("available") or [],
        "resolve_note": result.get("note") if resolved else None,
    }


def _edges_from_mapping(mapping: Dict[str, Any], to_step: str,
                        outputs: Dict[str, Dict[str, Any]], via: str) -> List[Dict[str, Any]]:
    if not isinstance(mapping, dict):
        return []
    param = mapping.get("input_parameter_name") or "(unnamed)"
    itype = mapping.get("input_type")
    if itype != "dependency":
        # Non-dependency inputs carry no graph edge, but record them so
        # flow_check can see every declared parameter.
        return [{
            "to_step": to_step, "to_param": param, "input_type": itype,
            "from_step": None, "output_attribute": None, "source_index": 0,
            "multi_source": False, "resolution_operator": None, "via": via,
            "resolved": True, "resolved_type": None, "resolved_schema": None,
            "resolve_error": None, "resolve_at": None, "resolve_available": [],
            "resolve_note": None,
        }]
    value = mapping.get("value") or {}
    sources = value.get("dependency_step_outputs") or []
    operator = value.get("resolution_operator")
    multi = len(sources) > 1
    out = []
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            continue
        out.append(_make_edge(
            to_step=to_step, to_param=param, input_type="dependency",
            from_step=source.get("step_name"),
            output_attribute=source.get("output_attribute"),
            source_index=index, multi_source=multi, resolution_operator=operator,
            via=via, outputs=outputs))
    return out


def _decision_facts(step: Dict[str, Any], all_names: Set[str],
                    outputs: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Decision cases, branches, and how each variable is wired.

    step_types/decision.md: outer condition list is OR, inner is AND; all
    cases evaluate; an empty default_branch means nothing fires.
    """
    cfg = step.get("config") or {}
    name = step.get("name")
    cases_out = []
    for index, case in enumerate(cfg.get("cases") or []):
        if not isinstance(case, dict):
            continue
        condition = case.get("condition") or []
        groups = []
        count = 0
        for group in condition:
            if not isinstance(group, list):
                continue
            row = []
            for comparison in group:
                if not isinstance(comparison, dict):
                    continue
                count += 1
                row.append({
                    "left": _operand_facts(comparison.get("left_operand")),
                    "op": comparison.get("operator"),
                    "right": _operand_facts(comparison.get("right_operand")),
                })
            groups.append(row)
        branch = case.get("branch")
        cases_out.append({
            "index": index,
            "branch": branch,
            "branch_step_exists": branch in all_names,
            "comparison_count": count,
            "condition": groups,
        })

    variables: Dict[str, Any] = {}
    declared = list((cfg.get("input_schema") or {}).get("properties", {}).keys())
    wired = {m.get("input_parameter_name"): m for m in (step.get("input_mappings") or [])
             if isinstance(m, dict)}
    for var in declared:
        mapping = wired.get(var)
        entry: Dict[str, Any] = {"wired": mapping is not None, "from_step": None,
                                 "output_attribute": None, "resolved_type": None}
        if mapping and mapping.get("input_type") == "dependency":
            sources = (mapping.get("value") or {}).get("dependency_step_outputs") or []
            if sources and isinstance(sources[0], dict):
                entry["from_step"] = sources[0].get("step_name")
                entry["output_attribute"] = sources[0].get("output_attribute")
                result = resolve_path(outputs.get(entry["from_step"]),
                                      entry["output_attribute"])
                if result.get("ok"):
                    entry["resolved_type"] = type_name(result.get("type"))
        variables[var] = entry

    default_branch = cfg.get("default_branch")
    return {
        "step": name,
        "default_branch": default_branch,
        "default_branch_step_exists": default_branch in all_names if default_branch else None,
        "input_schema_properties": sorted(declared),
        "cases": cases_out,
        "variables": variables,
    }


def _operand_facts(operand: Any) -> Dict[str, Any]:
    if not isinstance(operand, dict):
        return {"kind": "unknown", "ref": None}
    itype = operand.get("input_type")
    value = operand.get("value")
    if itype == "dependency":
        sources = (value or {}).get("dependency_step_outputs") or []
        first = sources[0] if sources and isinstance(sources[0], dict) else {}
        return {"kind": "dependency", "operand_step": first.get("step_name"),
                "output_attribute": first.get("output_attribute"), "ref": None}
    if itype == "variable":
        return {"kind": "variable", "ref": value if isinstance(value, str) else None}
    if itype == "static":
        return {"kind": "static", "value": value if isinstance(
            value, (str, int, float, bool, type(None))) else "(complex)"}
    return {"kind": itype or "unknown", "ref": None}


def _dynamic_key_names(schema: Any, _depth: int = 0) -> Set[str]:
    """Every dynamic_keys name anywhere in a normalized schema."""
    out: Set[str] = set()
    if not isinstance(schema, dict) or _depth > MAX_LEAF_DEPTH:
        return out
    for key in schema.get("dynamic_keys") or []:
        out.add(str(key))
    for child in (schema.get("properties") or {}).values():
        out |= _dynamic_key_names(child, _depth + 1)
    items = schema.get("items")
    if isinstance(items, dict):
        out |= _dynamic_key_names(items, _depth + 1)
    return out


def _literal_config_text(raw_step: Dict[str, Any], key: str) -> Optional[str]:
    """A config field's LITERAL string, from config or a static mapping only.

    Unlike `_effective_value` this never reports a wired placeholder: a value
    that only exists at runtime cannot be scanned for field references.
    """
    cfg = raw_step.get("config") or {}
    value = cfg.get(key)
    if isinstance(value, str) and value.strip():
        return value
    for mapping in raw_step.get("input_mappings") or []:
        if mapping.get("input_parameter_name") != key:
            continue
        if mapping.get("input_type") == "static":
            mapped = mapping.get("value")
            if isinstance(mapped, str) and mapped.strip():
                return mapped
    return None


#: Plain-text config strings that can name a field. CONTRACT.md B10: an
#: `email` step's body and subject are literal strings in the workflow, so a
#: field mentioned only there is still used, and the unused-field detector
#: must see it.
_SCANNABLE_TEXT_KEYS = {"email": ("email_body", "subject")}

#: A field name shorter than this is too generic to match on as a word.
_MIN_REFERENCE_NAME = 3


def _text_field_references(steps_raw: List[Dict[str, Any]],
                           fields: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Field names referenced from literal config text (CONTRACT.md B10).

    Evidence for the unused-field detector: a field named in an email body is
    used, even though no input_mapping points at it.
    """
    owners: Dict[str, Set[str]] = {}
    for field in fields:
        name = field.get("name")
        if isinstance(name, str) and len(name) >= _MIN_REFERENCE_NAME:
            owners.setdefault(name, set()).add(field["step"])
    if not owners:
        return []
    out: List[Dict[str, Any]] = []
    for step in steps_raw:
        for key in _SCANNABLE_TEXT_KEYS.get(step.get("type"), ()):
            text = _literal_config_text(step, key)
            if not text:
                continue
            references = []
            for name in sorted(owners):
                if re.search(r"\b%s\b" % re.escape(name), text):
                    for owner in sorted(owners[name]):
                        references.append({"field": name, "step": owner})
            if references:
                out.append({"step": step.get("name"), "where": "config.%s" % key,
                            "chars": len(text), "references": references})
    return out


def _opaque_consumers(steps_raw: List[Dict[str, Any]], fields: List[Dict[str, Any]],
                      reverse: Dict[str, List[str]],
                      edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Steps whose field usage can never be determined offline (CONTRACT.md B10).

    `fill_docx` merges its `context` input into `{{placeholder}}` markers
    inside a .docx template, and step_types/fill_docx.md makes the template a
    `{user_document_id, filename}` document reference — a platform document
    reachable only by download. These passes are forbidden network access, so
    that evidence is permanently unavailable and a field routed into `context`
    resolves to "cannot determine", never to "unused".

    Only genuinely unreadable producers count. A code step that writes a
    document from readable inline code does NOT, because its field usage is in
    the code sidecar.
    """
    fields_by_step: Dict[str, int] = {}
    for field in fields:
        fields_by_step[field["step"]] = fields_by_step.get(field["step"], 0) + 1

    out: List[Dict[str, Any]] = []
    for step in steps_raw:
        if step.get("type") != "fill_docx":
            continue
        name = step.get("name")
        # Upstream closure of the `context` input: every step whose fields can
        # reach a placeholder we cannot read.
        seeds = [e["from_step"] for e in edges
                 if e["to_step"] == name and e["to_param"] == "context" and e["from_step"]]
        closure: Set[str] = set()
        queue = list(seeds)
        while queue:
            node = queue.pop()
            if node in closure or node == DISPATCHER:
                continue
            closure.add(node)
            queue.extend(reverse.get(node) or [])
        affected = sorted(closure)
        out.append({
            "step": name,
            "type": "fill_docx",
            "reason": "docx_template",
            "param": "context",
            "detail": ("placeholders live inside a .docx template referenced by "
                       "user_document_id; reading it needs a download and these "
                       "passes have no network access"),
            "upstream_steps": affected,
            "fields_affected": sum(fields_by_step.get(s, 0) for s in affected),
        })
    return out


def _interpolated_paths(producer: str, code: str,
                        edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Field paths a producer's code reads, each with the step that owns it.

    CONTRACT.md B11: an entry carries the producing step name so a consumer
    can attribute a path instead of matching defensively by exact-or-suffix.
    """
    sources: Dict[str, Dict[str, Any]] = {}
    for edge in edges:
        if edge["to_step"] == producer and edge["input_type"] == "dependency" \
                and isinstance(edge["to_param"], str):
            sources.setdefault(edge["to_param"], edge)
    if not code or not sources:
        return []
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for param in sorted(sources):
        edge = sources[param]
        pattern = re.compile(
            r"\b%s\s*(?:\.get\(\s*|\[\s*)['\"]([A-Za-z0-9_]+)['\"]" % re.escape(param))
        for match in pattern.finditer(code):
            key = match.group(1)
            path = "%s.%s" % (param, key)
            if path in seen:
                continue
            seen.add(path)
            out.append({
                "path": path,
                "param": param,
                "key": key,
                # B11: the step that produces this data, not the step reading it.
                "step": edge["from_step"],
                "source_attribute": edge["output_attribute"],
            })
    return out


def _write_code_sidecars(code_steps: Dict[str, Any], code_text: Dict[str, str],
                         out_dir: Optional[str]) -> None:
    """Write each code step's source to <out_dir>/code/<slug>.py.

    CONTRACT.md hard rule 2 gives this script sole ownership of reading
    workflow.json, and the facts JSON deliberately excludes code text to stay
    small. A sidecar satisfies both: the source is reachable without a second
    reader, and it never bloats the JSON other passes load.

    The path lands in code_steps[<step>]["code_file"], slugged with
    common.slug so a step's sidecar is predictable from its name alone.
    """
    if not out_dir or not code_text:
        return
    code_dir = os.path.join(out_dir, "code")
    os.makedirs(code_dir, exist_ok=True)
    used: Dict[str, int] = {}
    for name, source in code_text.items():
        base = slug(name)
        seen = used.get(base, 0)
        used[base] = seen + 1
        filename = "%s.py" % base if not seen else "%s_%d.py" % (base, seen + 1)
        path = os.path.join(code_dir, filename)
        tmp = path + ".tmp"
        with open(tmp, "w") as handle:
            handle.write(source)
            if not source.endswith("\n"):
                handle.write("\n")
        os.replace(tmp, path)
        if name in code_steps:
            code_steps[name]["code_file"] = path


#: step_types/email.md "Config keys" — the fields that decide where an email
#: actually goes when `reply_to_original` is false.
_EMAIL_ROUTING_FIELDS = ("to_email", "to_names", "from_email", "from_name",
                         "cc_emails", "cc_names", "subject", "thread_id")


def _effective_value(raw_step: Dict[str, Any], key: str) -> Any:
    """A config field's effective value, counting input_mappings.

    step_types/email.md: a field set in config OR wired via input_mappings
    both count as "effectively set", and "empty strings count as 'not set'".
    A non-static mapping is reported as its input_type, because the value is
    only knowable at runtime.
    """
    cfg = raw_step.get("config") or {}
    value = cfg.get(key)
    if isinstance(value, str):
        if value.strip():
            return value
    elif value not in (None, "", [], {}):
        return value
    for mapping in raw_step.get("input_mappings") or []:
        if mapping.get("input_parameter_name") != key:
            continue
        mapped = mapping.get("value")
        if mapping.get("input_type") == "static":
            if isinstance(mapped, str):
                if mapped.strip():
                    return mapped
            elif mapped not in (None, "", [], {}):
                return mapped
        else:
            return "<%s>" % (mapping.get("input_type") or "wired")
    return None


def _email_routing(raw_step: Dict[str, Any]) -> Dict[str, Any]:
    """Whether a consuming email step displays or actually sends.

    step_types/email.md: "The send path only executes when `zapier_reply_url`
    and a thread id are present. Without `zapier_reply_url` the step still
    succeeds with sent=False — compose-only mode ... this is how UI-only
    'display email' summary steps work: no recipients, no webhook, the email
    renders on the canvas only."

    That makes `zapier_reply_url` the decisive signal for the mistake
    html_probe is looking for: a dashboard that is accidentally emailed.
    """
    cfg = raw_step.get("config") or {}
    routing = {key: _effective_value(raw_step, key) for key in _EMAIL_ROUTING_FIELDS}
    webhook = _effective_value(raw_step, "zapier_reply_url")
    return {
        "sends": webhook is not None,
        "display_only": webhook is None,
        "zapier_reply_url_set": webhook is not None,
        "reply_to_original": cfg.get("reply_to_original"),
        "routing": {k: v for k, v in routing.items() if v is not None},
        "routing_absent": sorted(k for k, v in routing.items() if v is None),
    }


def _html_producers(steps: List[Dict[str, Any]], code_steps: Dict[str, Any],
                    edges: List[Dict[str, Any]],
                    raw_by_name: Optional[Dict[str, Dict[str, Any]]] = None,
                    code_text: Optional[Dict[str, str]] = None,
                    ) -> List[Dict[str, Any]]:
    """Code steps emitting an HTML string that a display step consumes.

    reference/patterns/html_summary_dashboard.md — the two-step shape: a
    hidden custom_step returning {email_body: "<html>"} feeding a UI-only
    email step.
    """
    by_name = {s["name"]: s for s in steps}
    out = []
    for step in steps:
        if step["type"] not in CODE_TYPES:
            continue
        info = code_steps.get(step["name"]) or {}
        declared = info.get("declared_output_properties") or []
        candidates = [k for k in declared if k in HTML_OUTPUT_KEYS]
        if not candidates:
            candidates = [k for k in (info.get("returned_keys") or [])
                          if k in HTML_OUTPUT_KEYS]
        if not candidates:
            continue
        for key in candidates:
            consumers = []
            for edge in edges:
                if edge["from_step"] != step["name"]:
                    continue
                if edge["output_attribute"] not in (key, None):
                    continue
                consumer_type = by_name.get(edge["to_step"], {}).get("type")
                if consumer_type not in ("email", "text_block", "document_viewer"):
                    continue
                entry = {"step": edge["to_step"], "param": edge["to_param"],
                         "type": consumer_type}
                if consumer_type == "email":
                    raw_consumer = (raw_by_name or {}).get(edge["to_step"])
                    if raw_consumer is not None:
                        # html_probe cannot read the workflow (hard rule 2), so
                        # publish the routing it needs to catch a dashboard
                        # that is accidentally emailed rather than displayed.
                        entry["email_routing"] = _email_routing(raw_consumer)
                consumers.append(entry)
            out.append({
                "step": step["name"],
                "output_field": key,
                "consumed_by": consumers,
                "code_chars": step["code_chars"],
                "interpolated_paths": _interpolated_paths(
                    step["name"], (code_text or {}).get(step["name"]) or "", edges),
                "display_step": step["display_step"],
            })
    return out


def _find_cycles(adjacency: Dict[str, List[str]], nodes: Set[str]) -> List[List[str]]:
    """Iterative DFS cycle detection. Reports each back edge once."""
    WHITE, GREY, BLACK = 0, 1, 2
    color: Dict[str, int] = {}
    cycles: List[List[str]] = []
    seen: Set[Tuple[str, ...]] = set()

    for start in sorted(nodes):
        if color.get(start, WHITE) != WHITE:
            continue
        stack: List[Tuple[str, int]] = [(start, 0)]
        path: List[str] = [start]
        color[start] = GREY
        while stack:
            node, position = stack[-1]
            children = adjacency.get(node) or []
            if position >= len(children):
                color[node] = BLACK
                stack.pop()
                if path:
                    path.pop()
                continue
            stack[-1] = (node, position + 1)
            child = children[position]
            state = color.get(child, WHITE)
            if state == GREY:
                if child in path:
                    cycle = path[path.index(child):] + [child]
                else:
                    cycle = [node, child]
                key = tuple(cycle)
                if key not in seen:
                    seen.add(key)
                    cycles.append(cycle)
            elif state == WHITE:
                color[child] = GREY
                stack.append((child, 0))
                path.append(child)
    return cycles


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

CORPUS = [
    "/Users/andrewjeffers/Documents/Work/contractors/workflows/cna_dua_audit_ads.json",
    "/Users/andrewjeffers/Documents/Work/contractors/workflows/cna_dua_audit_lpl.json",
    "/Users/andrewjeffers/Documents/Work/contractors/workflows/submission_intake.json",
    "/Users/andrewjeffers/Documents/Work/contractors/workflows/quantum_cny.json",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", "reference", "examples",
                 "submission_intake_es_umbrella.json"),
]


def run_build(path: str, out_dir: str) -> int:
    started = time.time()
    facts, notes = build_facts(path, out_dir)
    elapsed = time.time() - started

    # The facts payload IS pass 0's output. PassOutput writes
    # <out_dir>/<pass_name>.json, and this pass is named "facts", so that path
    # is exactly the facts file every other pass reads via --facts. Writing
    # both separately collides on one filename; carrying the facts as the
    # pass extras yields a single file that satisfies the facts-file shape and
    # the per-pass {pass, findings, <pass data>} shape at once.
    out = PassOutput(PASS, out_dir)
    out.add_all(notes)
    for key, value in facts.items():
        if key in ("pass", "findings"):
            continue
        out.set(key, value)
    facts_path = str(out.write())
    counters = facts["counters"]
    lines = [
        facts["workflow"].get("name") or os.path.basename(path),
        "  %d steps, %d edges (%d unresolved), %d schema leaves, %d fields" % (
            counters["steps"], counters["edges"], counters["unresolved_edges"],
            counters["schema_leaves"], counters["fields"]),
        "  %d decisions / %d cases, %d manual gates, %d hidden steps, %d html producers" % (
            counters["decisions"], counters["cases"], counters["manual_steps"],
            counters["hidden_steps"], counters["html_producers"]),
        "  facts: %s (%.1f KB, built in %.2fs)" % (
            facts_path, os.path.getsize(facts_path) / 1024.0, elapsed),
    ]
    return out.print_summary(lines)


def _recount(path: str) -> Dict[str, int]:
    """Independent recount straight from the raw JSON, for the self-test."""
    with open(path) as handle:
        raw = json.load(handle)
    workflow = raw.get("content") if isinstance(raw.get("content"), dict) else raw
    steps = workflow.get("steps") or []
    leaves = 0
    edges = 0
    decisions = 0
    manual = 0
    hidden = 0
    for step in steps:
        for fragment in _declared_schema_fragments(step):
            leaves += _raw_leaves(fragment)[0]
        for mapping in step.get("input_mappings") or []:
            if mapping.get("input_type") == "dependency":
                sources = (mapping.get("value") or {}).get("dependency_step_outputs") or []
                edges += len([s for s in sources if isinstance(s, dict)])
            else:
                edges += 1
        if step.get("type") == "decision":
            decisions += 1
            edges += len(_iter_dependency_refs((step.get("config") or {}).get("cases")))
        edges += len([d for d in (step.get("dependencies") or [])
                      if isinstance(d, dict) and d.get("step_name")])
        if step.get("type") in MANUAL_TYPES:
            manual += 1
        if step.get("display_step") is False:
            hidden += 1
    return {"steps": len(steps), "schema_leaves": leaves, "edges": edges,
            "decisions": decisions, "manual_steps": manual, "hidden_steps": hidden}


TABLE_HEADERS = ("workflow", "steps", "edges", "leaves", "fields",
                 "dec", "man", "hid", "html", "secs", "facts KB", "blk")


def self_test(out_dir: str) -> int:
    failures: List[str] = []
    table_rows: List[List[Any]] = []

    for path in CORPUS:
        path = os.path.abspath(path)
        label = os.path.basename(path)
        if not os.path.isfile(path):
            failures.append("%s: corpus file missing at %s" % (label, path))
            continue
        started = time.time()
        case_dir = os.path.join(out_dir, slug(os.path.basename(path)))
        try:
            facts, notes = build_facts(path, case_dir)
        except Exception as exc:  # noqa: BLE001 - self-test reports, never crashes
            failures.append("%s: build raised %s: %s" % (label, type(exc).__name__, exc))
            continue
        elapsed = time.time() - started
        counters = facts["counters"]
        names = {s["name"] for s in facts["steps"]}

        # 1. every edge source exists or is the implicit dispatcher
        for edge in facts["edges"]:
            src = edge["from_step"]
            if src and src != DISPATCHER and src not in names:
                failures.append("%s: edge from unknown step %r into %r"
                                % (label, src, edge["to_step"]))
                break

        # 2. counters match an independent recount
        expected = _recount(path)
        for key, value in expected.items():
            if counters.get(key) != value:
                failures.append("%s: counter %s is %r, independent recount says %r"
                                % (label, key, counters.get(key), value))

        # 3. no cycles on these workflows (all known-acyclic)
        if facts["graph"]["cycles"]:
            failures.append("%s: reported %d cycle(s) on a known-acyclic workflow: %s"
                            % (label, len(facts["graph"]["cycles"]),
                               facts["graph"]["cycles"][0]))

        # 4. the 2.7 MB workflow builds inside the performance bar
        if "ads" in label and elapsed > 3.0:
            failures.append("%s: build took %.2fs, bar is 3.00s" % (label, elapsed))

        # 5. the decision-operand gotcha, on the workflow that relies on it
        if "lpl" in label:
            operand_edges = [e for e in facts["edges"] if e["via"] == "decision_operand"]
            if not operand_edges:
                failures.append("%s: no decision_operand edges — the LPL comparison "
                                "gate reads its dependency inline and MUST appear"
                                % label)
            else:
                gate = "LPL Comparison Gate"
                src = "Prepare LPL Comparison Data"
                if not any(e["to_step"] == gate and e["from_step"] == src
                           for e in operand_edges):
                    failures.append("%s: expected a decision_operand edge %s -> %s, got %s"
                                    % (label, src, gate,
                                       [(e["from_step"], e["to_step"]) for e in operand_edges]))
                if gate not in (facts["graph"]["adjacency"].get(src) or []):
                    failures.append("%s: graph.adjacency[%r] is missing %r — the gate "
                                    "would be reported unreachable" % (label, src, gate))
                if gate in facts["graph"]["roots"]:
                    failures.append("%s: %r is in graph.roots, so its inline dependency "
                                    "was missed" % (label, gate))

        # 7. code sidecars exist and are recorded (CONTRACT.md A-series:
        #    html_probe reads code from here instead of the workflow).
        for step_name, info in (facts["code_steps"] or {}).items():
            recorded = info.get("code_file")
            if not recorded:
                failures.append("%s: code step %r has no code_file sidecar"
                                % (label, step_name))
                break
            if not os.path.isfile(recorded):
                failures.append("%s: code_file %r does not exist" % (label, recorded))
                break
            # Compare CHARACTERS, not bytes: code_chars is len(str) and these
            # sources contain non-ASCII, so getsize() runs ahead of it.
            with open(recorded) as handle:
                written = handle.read()
            declared_chars = info.get("code_chars", -1)
            if len(written) not in (declared_chars, declared_chars + 1):
                failures.append("%s: sidecar for %r holds %d chars, code_chars says %d"
                                % (label, step_name, len(written), declared_chars))
                break

        # 8a. Entry steps must exist. An empty roots list means the graph has
        #     no starting point and any reachability pass built on it is void.
        if not facts["graph"]["roots"]:
            failures.append("%s: graph.roots is empty — no entry step, so a "
                            "reachability pass has nothing to start from" % label)

        # 8c. comparable_fields drift guard, contributed by the module pass:
        #     every emitted identity must (a) resolve, so the walker can never
        #     invent a field, and (b) be a prefix of some schema_leaf_paths
        #     entry, so both walkers agree on the tree's shape. dynamic-key
        #     paths are exempt from (b) only — schema_leaf_paths does not
        #     enumerate them. Run against the RAW paths: the display form
        #     drops `data.` and `.0` on purpose and cannot round-trip.
        for step in facts["steps"]:
            output = step["output"]
            resolver_paths = schema_leaf_paths(output)
            dynamic_names = _dynamic_key_names(output)
            drifted = False
            for raw in comparable_raw_paths(output):
                if not resolve_path(output, raw).get("ok"):
                    failures.append("%s: comparable_fields emitted %r on %r, which "
                                    "does not resolve — the walker invented a field"
                                    % (label, raw, step["name"]))
                    drifted = True
                    break
                if raw.rsplit(".", 1)[-1] in dynamic_names:
                    continue
                if not any(p == raw or p.startswith(raw + ".") for p in resolver_paths):
                    failures.append("%s: comparable_fields emitted %r on %r, which is "
                                    "not a prefix of any resolver leaf — the two "
                                    "walkers have drifted" % (label, raw, step["name"]))
                    drifted = True
                    break
            if drifted:
                break

        if not facts["graph"]["sinks"]:
            failures.append("%s: graph.sinks is empty — impossible on an acyclic "
                            "graph with steps" % label)

        # 8b. Legacy `dependencies` wiring must appear as edges, or the DAG has
        #     holes and a legacy-wired step looks like an entry point.
        raw_legacy = [s2 for s2 in json.load(open(path)).get("steps", [])
                      if isinstance(s2.get("dependencies"), list) and s2.get("dependencies")]
        recorded = [e for e in facts["edges"] if e["via"] == "dependencies"]
        expected_legacy = sum(len([d for d in s2["dependencies"]
                                   if isinstance(d, dict) and d.get("step_name")])
                              for s2 in raw_legacy)
        if len(recorded) != expected_legacy:
            failures.append("%s: %d legacy dependency edges recorded, workflow declares %d"
                            % (label, len(recorded), expected_legacy))
        for edge in recorded:
            if edge["to_step"] not in (facts["graph"].get("reverse") or {}) or \
                    edge["from_step"] not in (facts["graph"]["reverse"][edge["to_step"]]):
                failures.append("%s: legacy edge %s -> %s missing from graph.reverse"
                                % (label, edge["from_step"], edge["to_step"]))
                break

        # 8c2. Parity between the two walkers, contributed by the module pass:
        #      the display set must equal the raw set minus the entries whose
        #      identity normalizes to nothing (a bare `data` on an
        #      empty-payload step). Comparing LENGTHS is wrong and is what
        #      broke a caller.
        for step in facts["steps"]:
            output = step["output"]
            raw = comparable_raw_paths(output)
            display = comparable_fields(output)
            expected = []
            seen_tok = set()
            for item in raw:
                disp, tok = field_identity(item)
                if not tok or tok in seen_tok:
                    continue
                seen_tok.add(tok)
                expected.append(disp)
            if display != expected:
                failures.append("%s: walker parity broken on %r — display set is not the "
                                "raw set minus empty identities (%d vs %d)"
                                % (label, step["name"], len(display), len(expected)))
                break

        # 8d. Code-step parameter types must be carried through. Without them
        #     every code-step mapping is untypeable and the cell-versus-scalar
        #     detectors cannot reach the steps holding most custom logic.
        for step_name, info in (facts["code_steps"] or {}).items():
            declared = info.get("declared_input_properties") or []
            types = info.get("declared_input_types")
            if types is None:
                failures.append("%s: code step %r has no declared_input_types"
                                % (label, step_name))
                break
            missing = [p2 for p2 in declared if p2 not in types]
            if missing:
                failures.append("%s: code step %r declares %s but carries no type for %s"
                                % (label, step_name, declared, missing))
                break
            for param, spec in types.items():
                if not isinstance(spec, dict) or "kind" not in spec:
                    failures.append("%s: %r param %r type is not a normalized schema: %r"
                                    % (label, step_name, param, spec))
                    break

        # 8. A2: both edge counters present.
        for key in ("edges", "dependency_edges", "dependencies_edges",
                    "input_mapping_edges", "decision_operand_edges"):
            if key not in facts["counters"]:
                failures.append("%s: counters.%s missing (CONTRACT A2)" % (label, key))

        blockers = sum(1 for n in notes if n["severity"] == "blocker")
        table_rows.append([
            label, counters["steps"], counters["edges"], counters["schema_leaves"],
            counters["fields"], counters["decisions"], counters["manual_steps"],
            counters["hidden_steps"], counters["html_producers"],
            "%.2f" % elapsed,
            "%.0f" % (len(json.dumps(facts, default=str)) / 1024.0), blockers,
        ])

    # 6. resolver invariants — the rules the rest of the sub-skill leans on
    classify = derive_output({
        "name": "C", "type": "classify_documents",
        "config": {"classes": [{"name": "ACORD"}, {"name": "Loss Run"}]},
    }, [])
    good = resolve_path(classify, "documents.Loss Run")
    if not good.get("ok") or type_name(good["type"]) != "array<file>":
        failures.append("resolver: documents.'Loss Run' should be array<file>, got %r" % good)
    typo = resolve_path(classify, "documents.Loss_Run")
    if typo.get("ok") or "ACORD" not in (typo.get("available") or []):
        failures.append("resolver: a classify class typo must fail and list the real "
                        "classes, got %r" % typo)

    extraction = derive_output({
        "name": "E", "type": "agentic_extraction",
        "config": {"extraction_schema": {"type": "object", "properties": {
            "named_insured": {"type": "string", "title": "Named Insured"},
            "limits": {"type": "object", "properties": {
                "each_occurrence": {"type": "number"}}}}}},
    }, [])
    nested_object = resolve_path(extraction, "data.limits.each_occurrence.value")
    if not nested_object.get("ok") or nested_object["type"].get("kind") != "number":
        failures.append("resolver: a nested object's field is reached by name, got %r"
                        % nested_object)
    cell = resolve_path(extraction, "data.named_insured")
    if not cell.get("ok") or cell["type"].get("kind") != "cell":
        failures.append("resolver: data.<field> must resolve to a cell, got %r" % cell)
    value = resolve_path(extraction, "data.named_insured.value")
    if not value.get("ok") or value["type"].get("kind") != "string":
        failures.append("resolver: data.<field>.value must resolve to string, got %r" % value)

    deep = resolve_path(extraction, "data.no_such_field.value")
    if deep.get("ok") or "named_insured" not in (deep.get("available") or []):
        failures.append("resolver: a typo PAST the first path segment must fail and list "
                        "the real fields — this is the gap validate_workflow.py "
                        "documents as unimplemented; got %r" % deep)

    rows = derive_output({
        "name": "R", "type": "extract_rows_from_multiple_sources",
        "config": {"extraction_schema": {"type": "array", "items": {
            "type": "object", "properties": {"vin": {"type": "string"}}}}},
    }, [])
    indexed = resolve_path(rows, "data.0.vin.value")
    if not indexed.get("ok") or indexed["type"].get("kind") != "string":
        failures.append("resolver: row extraction data.0.<col>.value must resolve to the "
                        "scalar, got %r" % indexed)
    unindexed = resolve_path(rows, "data.vin.value")
    if unindexed.get("ok"):
        failures.append("resolver: reading a column off a row array without an index must "
                        "fail, got %r" % unindexed)

    # An array field inside an object extraction schema stays navigable
    # (reference/input_mappings.md "Only primitive leaves are wrapped").
    nested = derive_output({
        "name": "N", "type": "agentic_extraction",
        "config": {"extraction_schema": {"type": "object", "properties": {
            "vehicles": {"type": "array", "items": {
                "type": "object", "properties": {"vin": {"type": "string"}}}}}}},
    }, [])
    container = resolve_path(nested, "data.vehicles")
    if not container.get("ok") or container["type"].get("kind") != "array":
        failures.append("resolver: data.<array field> must stay an array of row objects, "
                        "got %r" % container)
    row_cell = resolve_path(nested, "data.vehicles.0.vin.value")
    if not row_cell.get("ok") or row_cell["type"].get("kind") != "string":
        failures.append("resolver: data.<array>.0.<col>.value must resolve to the scalar, "
                        "got %r" % row_cell)
    bad_value = resolve_path(nested, "data.vehicles.value")
    if bad_value.get("ok") or "vin" not in (bad_value.get("available") or []):
        failures.append("resolver: `.value` on an array node must FAIL and list the row's "
                        "columns — it resolves to nothing at runtime and every "
                        "downstream step runs green on empty data; got %r" % bad_value)
    obj_value = resolve_path(extraction, "data.limits.value")
    if obj_value.get("ok") or "each_occurrence" not in (obj_value.get("available") or []):
        failures.append("resolver: `.value` on a nested object node must FAIL and list "
                        "the object's real fields, got %r" % obj_value)

    # CONTRACT.md A4 stands: an index into a genuinely OPEN non-array node
    # still degrades. That rule is about open objects, not arrays.
    open_index = resolve_path(sch("object", open=True), "0")
    if not open_index.get("ok") or not open_index.get("note"):
        failures.append("resolver: A4 regression — indexing an open object must degrade "
                        "to unknown plus a note, got %r" % open_index)

    if types_compatible(sch("string"), cell.get("type")):
        failures.append("types_compatible: a cell must NOT satisfy a string parameter "
                        "(this is the .value bug detector)")
    if not types_compatible(sch("object"), make_file()):
        failures.append("types_compatible: object must accept file")
    if types_compatible(make_file(), sch("object")):
        failures.append("types_compatible: file must NOT accept a plain object")

    print("wf_facts self-test")
    for line in table(table_rows, TABLE_HEADERS, max_rows=len(table_rows)):
        print(line)
    if failures:
        print("\nFAILED (%d):" % len(failures))
        for item in failures:
            print("  - %s" % item)
        return 1
    print("\nall invariants hold")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wf_facts.py",
        description="Pass 0: index a workflow into facts.json (the only workflow.json reader).")
    parser.add_argument("workflow", nargs="?", help="path to workflow.json")
    parser.add_argument("--out", help="output directory")
    parser.add_argument("--self-test", action="store_true",
                        help="run against the corpus and assert invariants")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test(args.out or os.path.join(
            os.environ.get("TMPDIR", "/tmp"), "verify-selftest"))

    if not args.workflow or not args.out:
        parser.error("need <workflow.json> and --out DIR (or --self-test)")
    if not os.path.isfile(args.workflow):
        print("error: no such file: %s" % args.workflow, file=sys.stderr)
        return 2
    try:
        return run_build(args.workflow, args.out)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
