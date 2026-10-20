"""Validate a workflow JSON file for common issues.

Usage:
    python3 validate_workflow.py <workflow.json>

Exit code 0 when the workflow passes (warnings allowed), 1 on errors.
"""

import ast
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional, cast

# Valid step types mirror the platform's StepType enum.
#
# Deprecated types are intentionally excluded — they are hidden from the
# builder UI's step picker. The script errors on them so authors are steered
# toward the supported replacement:
#   - extract_from_email      → use extract_from_multiple_sources (with email inputs)
#   - extract_from_document   → use extract_from_multiple_sources (with documents) or agentic_extraction
#   - generate_qa_table_from_kb → use generate_qa_table_from_agent
# Web Agent step allowlisted models mirror the platform's allowlist.
WEB_AGENT_ALLOWED_MODELS = {
    "anthropic/claude-haiku-4-5",
    "anthropic/claude-opus-4-8",
    "anthropic/claude-sonnet-4-6",
    "openai/gpt-5.4",
    "openai/gpt-5.4-mini",
    "openai/gpt-5.5",
}

VALID_STEP_TYPES = {
    "prepare_documents",
    "classify_documents",
    "extract_from_multiple_sources",
    "extract_rows_from_multiple_sources",
    "extract_mixed_schema_from_multiple_sources",
    "sov_mapping",
    "function",
    "custom_step",
    "email",
    "pause",
    "hold",
    "manual_input",
    "decision",
    "workflow_dispatcher",
    "knowledge_base",
    "generate_qa_table_from_agent",
    "multi_column_qa",
    "compare_documents",
    "document_viewer",
    "submission_summary_generator",
    "ofac_agent",
    "osha_agent",
    "trellis_law",
    "extract_guidelines",
    "check_guidelines",
    "fill_docx",
    "agentic_extraction",
    "agentic_guideline_check",
    "text_block",
    "web_agent",
    "web_search",
    "compare_document_data",
    "ams360",
    "applied_epic",
    "qqcatalyst",
    "agencyzoom",
    "aim",
    "alis_dx",
    "riskmeter",
    "sambasafety",
    "sagitta",
    "hazardhub",
    "maprisk",
    "nhtsa",
    "pitchbook",
    "financepro",
    "cotality_valuation",
    "sharepoint",
    "snapsheet",
    "benefitpoint",
    # snapsheet_payments moves money: valid to EDIT, but never author a new one —
    # the backend rejects any net increase with RESTRICTED_STEP_TYPE.
    "snapsheet_payments",
    "outlook_mail",
    "imageright",
    "google_maps",
    "run_workflow",
    "combine_kv_tables",
    "execution_matching",
    "loop",
}

DEFAULT_TABLE_OUTPUT_STEP_TYPES = {
    "extract_from_multiple_sources",
    "extract_rows_from_multiple_sources",
    "extract_mixed_schema_from_multiple_sources",
    "sov_mapping",
    "generate_qa_table_from_agent",
    "multi_column_qa",
    "ofac_agent",
    "trellis_law",
    "extract_guidelines",
    "check_guidelines",
    "agentic_extraction",
    "agentic_guideline_check",
    "osha_agent",
    "compare_document_data",
    "run_workflow",
    "ams360",
    "combine_kv_tables",
    "execution_matching",
    "agencyzoom",
    "aim",
    "alis_dx",
    "riskmeter",
    "sambasafety",
    "hazardhub",
    "maprisk",
    "nhtsa",
    "pitchbook",
    "financepro",
    "cotality_valuation",
    "sharepoint",
    "snapsheet",
    "benefitpoint",
    "snapsheet_payments",
    "outlook_mail",
    "imageright",
    "google_maps",
}

# Deprecated step types — present in the BE enum but no longer pickable in the
# UI. We hard-error so Claude doesn't generate workflows that customers can't
# edit in the builder UI.
DEPRECATED_STEP_TYPES: dict[str, str] = {
    "extract_from_email": "extract_from_multiple_sources (with email inputs)",
    "extract_from_document": "extract_from_multiple_sources (with documents) or agentic_extraction",
    "generate_qa_table_from_kb": "generate_qa_table_from_agent",
    "enrich_addresses_with_gmaps": "google_maps (action_type geocode_address / reverse_geocode / validate_address)",
}

# The implicit first step that the system auto-injects
IMPLICIT_STEPS = {"Workflow Dispatcher"}


def validate_workflow(filepath: str) -> tuple[list[str], list[str]]:
    """Validate a workflow JSON file.

    Returns:
        Tuple of (errors, warnings). Errors are fatal, warnings are advisory.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Parse JSON
    try:
        with open(filepath) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        errors.append(f"Invalid JSON: {e}")
        return errors, warnings

    # 2. Top-level structure
    if "name" not in data:
        errors.append("Missing top-level 'name' field")
    if "steps" not in data:
        errors.append("Missing top-level 'steps' field")
        return errors, warnings
    if "options" not in data:
        warnings.append("Missing top-level 'options' field (will use defaults)")

    steps = data["steps"]
    if not isinstance(steps, list):
        errors.append("'steps' must be an array")
        return errors, warnings

    # Collect step names for reference validation
    step_names: set[str] = set()
    all_step_names_with_implicit = IMPLICIT_STEPS.copy()

    for step in steps:
        name = step.get("name", "<unnamed>")
        if name in step_names:
            errors.append(f"Duplicate step name: '{name}'")
        step_names.add(name)
        all_step_names_with_implicit.add(name)

    # 3. Validate each step
    for i, step in enumerate(steps):
        name = step.get("name", f"<step {i}>")
        prefix = f"Step '{name}'"

        # 3a. Required fields
        if "name" not in step:
            errors.append(f"Step {i}: missing 'name'")
        if "type" not in step:
            errors.append(f"{prefix}: missing 'type'")

        # 3b. Valid step type
        step_type = step.get("type", "")
        if step_type in DEPRECATED_STEP_TYPES:
            errors.append(
                f"{prefix}: step type '{step_type}' is deprecated and removed from the workflow builder UI. "
                f"Use {DEPRECATED_STEP_TYPES[step_type]} instead."
            )
        elif step_type not in VALID_STEP_TYPES:
            errors.append(f"{prefix}: invalid step type '{step_type}'. Valid types: {sorted(VALID_STEP_TYPES)}")

        # 3c. CRITICAL: dependencies must be empty
        deps = step.get("dependencies", [])
        if deps and len(deps) > 0:
            has_content = False
            for dep in deps:
                if (isinstance(dep, dict) and dep.get("step_name")) or (isinstance(dep, str) and dep):
                    has_content = True
                    break
            if has_content:
                # Skill is intentionally stricter than BE here. BE still
                # accepts legacy `dependencies` content alongside
                # `input_mappings` for already-saved workflows, but new
                # workflows generated by Claude must not include it. This
                # error catches that.
                errors.append(
                    f"{prefix}: 'dependencies' field has content — this is DEPRECATED. "
                    f"Use 'input_mappings' exclusively. Set dependencies to []."
                )

        # 3d. Validate input_mappings references
        input_mappings = step.get("input_mappings", [])
        if not isinstance(input_mappings, list):
            errors.append(f"{prefix}: 'input_mappings' must be an array")
            continue

        _check_web_agent_contract(step, prefix, errors)

        for j, mapping in enumerate(input_mappings):
            mapping_prefix = f"{prefix}, mapping {j}"

            if not isinstance(mapping, dict):
                errors.append(f"{mapping_prefix}: mapping must be an object")
                continue

            # Check input_type
            input_type = mapping.get("input_type")
            if input_type not in ("dependency", "static", "variable", "prompt", "function"):
                errors.append(f"{mapping_prefix}: invalid input_type '{input_type}'")

            # Check input_parameter_name
            if not mapping.get("input_parameter_name"):
                errors.append(f"{mapping_prefix}: missing 'input_parameter_name'")

            # For static type, value must be a JSON primitive OR a file
            # reference. Mirrors the backend InputMapping model
            # (workflow_v1.py), which accepts
            # Union[primitive, file-ref dict, list[file-ref dict]] for static —
            # the shape produced when a build-time uploaded document is wired as a
            # static input (FAI-7769).
            if input_type == "static":
                value = mapping.get("value")
                if value is None or isinstance(value, (str, int, float, bool)):
                    pass
                elif isinstance(value, dict):
                    if not value.get("user_document_id"):
                        errors.append(
                            f"{mapping_prefix}: static dict value must be a file reference "
                            "with a non-empty 'user_document_id'"
                        )
                elif isinstance(value, list):
                    if not all(isinstance(it, dict) and it.get("user_document_id") for it in value):
                        errors.append(
                            f"{mapping_prefix}: static list value must contain only file "
                            "references, each with a non-empty 'user_document_id'"
                        )
                else:
                    errors.append(
                        f"{mapping_prefix}: static value must be a primitive, a file reference, "
                        f"or a list of file references, got {type(value).__name__}"
                    )

            # For dependency type, validate step references
            if input_type == "dependency":
                value = mapping.get("value", {})
                if not isinstance(value, dict):
                    errors.append(f"{mapping_prefix}: 'value' must be an object for dependency type")
                    continue

                dep_outputs = value.get("dependency_step_outputs", [])
                for dep in dep_outputs:
                    ref_step = dep.get("step_name", "")
                    if ref_step and ref_step not in all_step_names_with_implicit:
                        errors.append(
                            f"{mapping_prefix}: references non-existent step '{ref_step}'. "
                            f"Available: {sorted(all_step_names_with_implicit)}"
                        )

        # 3e. Function step validations
        if step_type == "function":
            config = step.get("config", {})
            if config.get("func") is not None and config.get("func") not in ("null", ""):
                errors.append(
                    f"{prefix}: function step has config.func='{config['func']}'. "
                    f"Must be null — use config.code for inline code instead."
                )
            if not config.get("code"):
                warnings.append(f"{prefix}: function step has no config.code (inline code)")

        # 3f. Custom-code steps (function + custom_step) with inline code MUST have
        #     config.input_schema and config.output_schema (required by validators.py for
        #     workflow save validation). Both step types share the same custom-schema
        #     contract and entry-function convention, so the same checks apply.
        if step_type in ("function", "custom_step"):
            config = step.get("config", {})
            has_code = bool(config.get("code"))
            if has_code:
                label = "custom_step" if step_type == "custom_step" else "custom code function"
                if not config.get("input_schema"):
                    errors.append(f"{prefix}: {label} step MUST have config.input_schema")
                if not config.get("output_schema"):
                    errors.append(f"{prefix}: {label} step MUST have config.output_schema")
                # 3g. The entry function MUST accept **kwargs. Mirrors the
                #     backend save-time check in workflow/builder/validators.py
                #     (FAI-7847 follow-up). Without **kwargs, the runtime
                #     injection of execution_log_id raises TypeError on every
                #     fire. AST-based so comments / docstrings don't trigger
                #     false positives.
                if not _function_step_entry_has_kwargs(config.get("code", "")):
                    errors.append(
                        f"{prefix}: {label} step entry function must accept "
                        f"`**kwargs`. Add `**kwargs` to the signature."
                    )

    # 4. DAG cycle detection
    _check_dag_cycles(steps, errors, all_step_names_with_implicit)

    # 5. agentic_extraction must not wire `documents` from bare Classify Documents.documents
    _check_agentic_extraction_documents(steps, errors)

    # 5a. agentic_extraction backend/preprocess config enum validation.
    _check_agentic_extraction_config(steps, errors)

    # 5b. prepare_documents must wire `user_document_ids` from Workflow Dispatcher
    _check_prepare_documents_user_document_ids(steps, errors)

    # 5c. Function steps should not import internal backend/workflow block functions
    _check_function_step_forbidden_imports(steps, errors)

    # 5d. system_prompt should be in input_mappings, not config
    _check_system_prompt_in_input_mappings(steps, warnings)

    # 5e. extract_rows_from_multiple_sources extraction_schema must be array-typed
    _check_extract_rows_schema_shape(steps, errors)

    # 5f. Function steps returning a document/email-headers/etc. should declare
    #     the named-type tag rather than plain `object` — backend save-time
    #     type-compat is directional (`object` accepts `file`, but `file` rejects
    #     plain `object`). Surface as warnings so authors see the issue locally.
    _check_named_type_compat_on_function_outputs(steps, warnings)

    # 5g. classify_documents config field-name validation.
    #     Reported by Kush in FAI-7092: skill doc previously said `categories`,
    #     runtime expects `classes`. Pasted JSONs with `categories` passed this
    #     script cleanly but blew up in the WF Builder UI. Catch all five known
    #     wrong field names locally.
    _check_classify_documents_config(steps, errors)

    # 5g.1. multi_column_qa config and get_step allowlist validation.
    _check_multi_column_qa_config(steps, errors, warnings)

    # 5g. trellis_law config field-name validation. Keep this in sync with
    #     TrellisLawConfig in workflow/builder/step_models.py.
    _check_trellis_law_config(steps, errors, warnings)

    # 5g2. riskmeter integration-loop contract. Keep this in sync with
    #      RiskMeterStepConfig plus workflow/builder/integration_runtime.py.
    _check_riskmeter_contract(steps, errors, all_step_names_with_implicit)

    # 5g2a. pitchbook integration-loop contract. Keep this in sync with
    #       PitchBookStepConfig in workflow/builder/step_models.py.
    _check_pitchbook_contract(steps, errors, all_step_names_with_implicit)

    # 5g2a. maprisk integration-loop + batch-action contract. Keep this in sync with
    #       MapriskStepConfig in workflow/builder/step_models.py.
    _check_maprisk_contract(steps, errors, all_step_names_with_implicit)

    # 5g2b. sharepoint per-action required-input contract. Keep this in sync with
    #       the SharePoint operation input_schemas in
    #       integrations/connectors/sharepoint/metadata.py.
    _check_sharepoint_contract(steps, errors)

    # 5g2b2. outlook_mail per-action required-input contract. Keep this in sync with
    #        the Outlook mail operation input_schemas in
    #        integrations/connectors/outlook_mail/metadata.py.
    _check_outlook_mail_contract(steps, errors)

    # 5g2b3. financepro single-call contract. Keep this in sync with
    #        workflow/builder/validators.py _SINGLE_CALL_ONLY_INTEGRATIONS.
    _check_financepro_contract(steps, errors)

    # 5g2c. unified nhtsa contract. Keep this in sync with NhtsaStepConfig in
    #       workflow/builder/step_models.py, the action metadata in
    #       integrations/connectors/nhtsa/{metadata,lookup_metadata}.py, and the
    #       per-action run_type rules in workflow/builder/validators.py.
    _check_nhtsa_contract(steps, errors, all_step_names_with_implicit)

    # 5h. Downstream wirings that reference `<classify>.documents.<X>` must
    #     point at a configured class name. A typo silently returns an empty
    #     list at runtime — all downstream steps run green with empty data,
    #     same silent-failure mode as Danny's prepare_documents miss.
    _check_classify_category_references(steps, errors)

    # 5i. Generic required-input-mapping check for every step type.
    #     The BE save validator rejects steps that lack user-required input
    #     mappings; mirror that here so a workflow passing this script will
    #     also pass the BE save. Source of truth: STEP_INPUT_SCHEMAS in
    #     workflow/builder/step_schemas.py with SYSTEM_INJECTED_PARAMS and
    #     CONFIG_PROVIDED_PARAMS subtracted (see _STEP_REQUIRED_INPUTS below).
    _check_required_input_mappings(steps, errors)

    # 5j. extract_from_multiple_sources at-least-one-source + matching prompts.
    #     Mirrors workflow/builder/validators.py exactly — the BE raises errors
    #     (not warnings) for these cases.
    _check_extract_from_multiple_sources_pairing(steps, errors)

    # 5j2. Rows/mixed extraction and knowledge_base source disjunctions.
    #       These mirror BE custom validation because STEP_INPUT_SCHEMAS cannot
    #       express documents-or-emails requirements.
    _check_documents_or_emails_sources(steps, errors)
    _check_knowledge_base_sources(steps, errors)

    # 5k. Function step `return {"error": "", ...}` literal pattern. Historic
    #     bug: executor classified a function output as FAILED if it had an
    #     "error" KEY (membership), not by truthy check — so returning
    #     `{"error": "", "data": ...}` on success was misclassified. Runtime
    #     fixed in BE 76fdb9f23 (truthy check), but the validator catches the
    #     misleading literal at save time so future authors don't bake the
    #     pattern back in. Mirrors BE validators.py AST scan.
    _check_function_step_empty_error_return(steps, errors)

    # custom_step steps may only declare packages baked into the sandbox
    # image; an unavailable name is a BE save-time error, mirrored here.
    _check_custom_step_packages(steps, errors)

    # KNOWN GAP — not implemented client-side (intentional):
    # The BE save validator also walks each `output_attribute` glom path
    # against the source step's output_schema and rejects type-mismatched
    # wirings (e.g. an input typed `object` consuming a path that resolves
    # to `string`). Reproducing that here would require porting:
    #   - workflow/builder/output_schema_generator.py
    #   - workflow/builder/named_types.py
    #   - workflow/builder/validators.py :: resolve_type_at_path
    # Out of scope for this skill — the BE save validator catches it on
    # paste, with a precise error message. Verified against 50 random
    # staging workflows: this is the ONLY error class our script doesn't
    # mirror (FAI-6817 differential test).

    # 6. Options validation
    options = data.get("options", {})
    if options and not options.get("enable_parallel_execution"):
        warnings.append("options.enable_parallel_execution is not true — steps will run sequentially")

    return errors, warnings


def _check_web_agent_contract(step: dict[str, object], prefix: str, errors: list[str]) -> None:
    """Validate the Web Agent prompt and canonical output shape."""
    if step.get("type") != "web_agent":
        return

    mappings = step.get("input_mappings")
    if not isinstance(mappings, list):
        return
    if len(mappings) != 1 or not isinstance(mappings[0], dict):
        errors.append(f"{prefix}: Web Agent requires exactly one input mapping named 'prompt'.")
        return

    prompt_mapping = cast("dict[str, object]", mappings[0])
    if prompt_mapping.get("input_parameter_name") != "prompt":
        errors.append(f"{prefix}: Web Agent requires exactly one input mapping named 'prompt'.")
        return

    allowed_models = WEB_AGENT_ALLOWED_MODELS
    config = step.get("config")
    configured_model = config.get("model") if isinstance(config, dict) else None
    if configured_model is not None and configured_model not in allowed_models:
        errors.append(
            f"{prefix}: Web Agent model '{configured_model}' is not supported. "
            f"Allowed: {', '.join(sorted(allowed_models))}."
        )

    input_type = prompt_mapping.get("input_type")
    if input_type not in ("static", "dependency"):
        errors.append(f"{prefix}: Web Agent prompt must use input_type='static' or 'dependency'.")
    elif input_type == "static":
        value = prompt_mapping.get("value")
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{prefix}: Web Agent prompt must be a non-empty string.")
        elif re.search(r"%[A-Za-z_][A-Za-z0-9_]*%", value):
            errors.append(f"{prefix}: Web Agent secret references require target-origin enforcement.")

    if step.get("output_type") not in (None, "table", "document_viewer"):
        errors.append(f"{prefix}: Web Agent output_type must be 'table', 'document_viewer', or null.")

    # output_schema is OPTIONAL and selects the output mode:
    #   present (non-empty object) -> structured-response TABLE
    #   absent                     -> run-recording DOCUMENT_VIEWER (video)
    output_schema = config.get("output_schema") if isinstance(config, dict) else None
    if output_schema is None:
        return
    properties = output_schema.get("properties") if isinstance(output_schema, dict) else None
    if (
        not isinstance(output_schema, dict)
        or output_schema.get("type") != "object"
        or not isinstance(properties, dict)
        or not properties
    ):
        errors.append(
            f"{prefix}: Web Agent config.output_schema, when set, must be a non-empty object schema "
            f"(omit it entirely for a recording-only step)."
        )
        return
    _check_web_agent_output_types(output_schema, prefix, errors)


_WEB_AGENT_OUTPUT_SCHEMA_MAX_DEPTH = 20


def _check_web_agent_output_types(
    schema: dict[str, object],
    prefix: str,
    errors: list[str],
    *,
    depth: int = 0,
) -> None:
    """Reject output types that a browser task cannot safely create."""
    if depth > _WEB_AGENT_OUTPUT_SCHEMA_MAX_DEPTH:
        errors.append(
            f"{prefix}: Web Agent output schema exceeds the maximum nesting depth of "
            f"{_WEB_AGENT_OUTPUT_SCHEMA_MAX_DEPTH}."
        )
        return

    unsupported_keywords = sorted({"$defs", "$ref", "allOf", "anyOf", "not", "oneOf"}.intersection(schema))
    if unsupported_keywords:
        errors.append(f"{prefix}: Web Agent does not support JSON Schema keyword '{unsupported_keywords[0]}'.")
        return

    schema_type = schema.get("type")
    if schema_type is None:
        errors.append(f"{prefix}: Web Agent output fields require a type.")
        return

    supported_types = (
        "array",
        "boolean",
        "date",
        "datetime",
        "enum",
        "integer",
        "null",
        "number",
        "object",
        "string",
    )
    if not isinstance(schema_type, (str, type(None))) or schema_type not in supported_types:
        errors.append(f"{prefix}: Web Agent does not support output type '{schema_type}'.")
        return
    if schema_type == "enum":
        options = schema.get("options")
        values = (
            [option.get("value") for option in options if isinstance(option, dict) and "value" in option]
            if isinstance(options, list)
            else []
        )
        if not values:
            errors.append(f"{prefix}: Web Agent enum outputs require at least one option.")
            return
        enum_type = schema.get("enum_type", "string")
        if enum_type == "object" or any(isinstance(value, (dict, list)) for value in values):
            errors.append(f"{prefix}: Web Agent does not support object-valued enum outputs.")
            return
        if not all(isinstance(value, (str, int, float, bool)) for value in values):
            errors.append(f"{prefix}: Web Agent enum options must contain scalar values.")
            return
        if enum_type == "number":
            if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
                errors.append(f"{prefix}: Web Agent numeric enum options must contain only numbers.")
                return
        elif enum_type == "string":
            if not all(isinstance(value, str) for value in values):
                errors.append(f"{prefix}: Web Agent string enum options must contain only strings.")
                return
        else:
            errors.append(f"{prefix}: Web Agent does not support enum type '{enum_type}'.")
            return

    properties = schema.get("properties")
    if properties is not None and not isinstance(properties, dict):
        errors.append(f"{prefix}: Web Agent output properties must be JSON Schema objects.")
        return
    if schema_type == "object" and not properties:
        errors.append(f"{prefix}: Web Agent object outputs require at least one property.")
        return
    if isinstance(properties, dict):
        for name, property_schema in properties.items():
            if (
                not isinstance(name, str)
                or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) is None
                or name.startswith("model_")
            ):
                errors.append(f"{prefix}: Web Agent output field '{name}' has an unsupported name.")
                continue
            if not isinstance(property_schema, dict):
                errors.append(f"{prefix}: Web Agent output properties must be JSON Schema objects.")
                continue
            _check_web_agent_output_types(property_schema, prefix, errors, depth=depth + 1)

    required = schema.get("required")
    if required is not None:
        if not isinstance(required, list) or not all(isinstance(name, str) for name in required):
            errors.append(f"{prefix}: Web Agent output required fields must be a list of field names.")
        elif not isinstance(properties, dict) or not set(required).issubset(properties):
            errors.append(f"{prefix}: Web Agent output required fields must exist in properties.")

    items = schema.get("items")
    if schema_type == "array" and items is None:
        errors.append(f"{prefix}: Web Agent array outputs require an items schema.")
        return
    if items is not None and not isinstance(items, dict):
        errors.append(f"{prefix}: Web Agent output items must be a JSON Schema object.")
        return
    if isinstance(items, dict):
        _check_web_agent_output_types(items, prefix, errors, depth=depth + 1)


# Packages baked into the fai-sandbox-py sandbox image and therefore importable
# by custom_step steps. MIRRORS src.backend.sandbox_python_libs
# .SANDBOX_AVAILABLE_PACKAGES (normalized distribution names) — keep in lockstep
# with that backend source of truth when a package is added/removed.
_SANDBOX_AVAILABLE_PACKAGES: set[str] = {
    "pandas",
    "numpy",
    "python-dateutil",
    "pytz",
    "dateparser",
    "openpyxl",
    "python-docx",
    "pypdf",
    "pypdfium2",
    "lxml",
    "beautifulsoup4",
    "pillow",
    "rapidfuzz",
    "glom",
    "httpx",
    "pydantic",
}


def _normalize_package_name(name: str) -> str:
    """PEP 503 normalization (mirror of the backend helper)."""
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def _check_custom_step_packages(steps: list[dict], errors: list[str]) -> None:
    """custom_step steps may declare only packages baked into the image.

    Mirrors BE validators.py :: validate_custom_step_packages so a workflow
    this script calls valid also passes the BE save validator.
    """
    for step in steps:
        if step.get("type") != "custom_step":
            continue
        packages = (step.get("config") or {}).get("packages") or []
        unavailable = [p for p in packages if _normalize_package_name(p) not in _SANDBOX_AVAILABLE_PACKAGES]
        if unavailable:
            step_name = step.get("name", "<unnamed>")
            errors.append(
                f"Step '{step_name}': declares package(s) not available in the sandbox: "
                f"{', '.join(unavailable)}. Available: {sorted(_SANDBOX_AVAILABLE_PACKAGES)}."
            )


def _function_step_entry_has_kwargs(code: str) -> bool:
    """True if the entry function in ``code`` declares ``**kwargs``.

    Entry function = ``run`` if present, else the first top-level function
    defined. Mirrors backend's ``workflow/builder/function_step_utils.py``.
    Returns True (= "we couldn't tell, don't flag") on syntax errors or
    when no function is found — those are surfaced by separate checks.
    """
    try:
        module = ast.parse(code)
    except SyntaxError:
        return True

    fns = [n for n in module.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if not fns:
        return True

    entry = next((f for f in fns if f.name == "run"), fns[0])
    return entry.args.kwarg is not None


def _check_function_step_forbidden_imports(steps: list[dict], errors: list[str]) -> None:
    """Error if function steps import internal backend functions that should be step types instead.

    Uses Python AST to check actual imports and function calls, not string matching.
    """
    # Map: forbidden function/module names → recommended step type
    FORBIDDEN_NAMES = {
        "generate_submission_summary_from_data_points": "submission_summary_generator",
        "extract_from_multiple_sources": "extract_from_multiple_sources (step type)",
        "guideline_check_qa": "agentic_guideline_check (step type)",
        "extract_rows_from_multiple_documents": "extract_rows_from_multiple_sources (step type)",
        "generate_qa_table_from_agent": "generate_qa_table_from_agent (step type)",
        "classify_documents": "classify_documents (step type)",
        "reply_email_using_zapier": "email (step type with reply_to_original)",
        "summary_agent": "submission_summary_generator",
        "create_summary_agent": "submission_summary_generator",
    }

    for step in steps:
        if step.get("type") != "function":
            continue
        code = step.get("config", {}).get("code", "")
        if not code:
            continue
        name = step.get("name", "?")

        try:
            tree = ast.parse(code)
        except SyntaxError:
            continue  # Syntax errors are caught elsewhere

        # Walk the AST looking for imports and calls
        for node in ast.walk(tree):
            # Check "from X import Y" statements
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_name = alias.name
                    if imported_name in FORBIDDEN_NAMES:
                        errors.append(
                            f"Step '{name}': imports '{imported_name}' — "
                            f"use the '{FORBIDDEN_NAMES[imported_name]}' step type instead. "
                            f"Function steps should only do data transformation."
                        )
            # Check "import X" statements
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    module_name = alias.name.split(".")[-1]
                    if module_name in FORBIDDEN_NAMES:
                        errors.append(
                            f"Step '{name}': imports module containing '{module_name}' — "
                            f"use the '{FORBIDDEN_NAMES[module_name]}' step type instead."
                        )


def _check_system_prompt_in_input_mappings(steps: list[dict], warnings: list[str]) -> None:
    """Warn if steps have system_prompt in config but not in input_mappings."""
    # Step types that accept system_prompt
    SYSTEM_PROMPT_STEP_TYPES = {
        "classify_documents",
        "extract_rows_from_multiple_sources",
        "extract_mixed_schema_from_multiple_sources",
        "multi_column_qa",
        "agentic_extraction",
        "agentic_guideline_check",
        "compare_document_data",
        "submission_summary_generator",
        "web_search",
    }
    # extract_from_multiple_sources uses email_system_prompt / document_system_prompt
    MULTI_SOURCE_PROMPT_PARAMS = {"email_system_prompt", "document_system_prompt"}

    for step in steps:
        step_type = step.get("type", "")
        step_name = step.get("name", "<unnamed>")
        config = step.get("config", {}) or {}
        input_mappings = step.get("input_mappings", []) or []
        mapping_params = {m.get("input_parameter_name") for m in input_mappings if isinstance(m, dict)}

        if step_type in SYSTEM_PROMPT_STEP_TYPES:
            has_config_sp = bool(config.get("system_prompt"))
            has_mapping_sp = "system_prompt" in mapping_params
            if has_config_sp and not has_mapping_sp:
                warnings.append(
                    f"Step '{step_name}': system_prompt is in config but not in input_mappings. "
                    f"Move it to input_mappings with input_type='static'. "
                    f"Config system_prompt is deprecated."
                )

        if step_type == "extract_from_multiple_sources":
            for param in MULTI_SOURCE_PROMPT_PARAMS:
                has_config = bool(config.get(param))
                has_mapping = param in mapping_params
                if has_config and not has_mapping:
                    warnings.append(
                        f"Step '{step_name}': {param} is in config but not in input_mappings. "
                        f"Move it to input_mappings with input_type='static'. "
                        f"Config {param} is deprecated."
                    )


def _check_agentic_extraction_documents(steps: list[dict], errors: list[str]) -> None:
    """Error if agentic_extraction wires `documents` from bare `Classify Documents.documents`.

    `documents` on agentic_extraction is the optional Filtered Documents filter; it
    must resolve to an array. Bare `Classify Documents.documents` is a category-keyed
    object and the backend rejects it at save with `Parameter 'documents' expects
    type 'array' but step 'Classify Documents' outputs type 'object'`. Either omit
    the mapping (recommended default) or use a category path like `documents.<ClassName>`.
    """
    classify_step_names = {s.get("name") for s in steps if s.get("type") == "classify_documents"}
    if not classify_step_names:
        return

    for step in steps:
        if step.get("type") != "agentic_extraction":
            continue
        step_name = step.get("name", "<unnamed>")
        for mapping in step.get("input_mappings", []):
            if mapping.get("input_parameter_name") != "documents":
                continue
            if mapping.get("input_type") != "dependency":
                continue
            for dep in mapping.get("value", {}).get("dependency_step_outputs", []):
                if dep.get("step_name") not in classify_step_names:
                    continue
                attr = dep.get("output_attribute") or ""
                # Empty / "documents" / "documents." all resolve to the top-level dict.
                # Any deeper path (e.g. "documents.ACORD") resolves to an array — fine.
                if attr in ("", "documents") or attr.rstrip(".") == "documents":
                    errors.append(
                        f"Step '{step_name}': `documents` wired from bare "
                        f"`{dep.get('step_name')}.documents` (type: object). "
                        f"Use a category path (e.g. `documents.<ClassName>`) or omit "
                        f"the mapping to search the full KB."
                    )


_AGENTIC_EXTRACTION_BACKENDS: set[str] = {
    "pydantic-ai",
    "claude-agent-sdk",
    "openai-agents",
}
_AGENTIC_EXTRACTION_PREPROCESS_MODES: set[str] = {
    "full_text",
    "ocr",
    "raw",
}


def _validate_preprocess_modes_value(
    *,
    step_name: str,
    field_name: str,
    value: object,
    errors: list[str],
) -> None:
    """Mirror AgenticExtractionConfig.preprocess_modes validation."""
    if value is None:
        return

    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list):
        errors.append(
            f"Step '{step_name}': invalid agentic_extraction config.{field_name} "
            f"{value!r}. Expected a string mode or non-empty list of modes. "
            f"Valid values: {sorted(_AGENTIC_EXTRACTION_PREPROCESS_MODES)}."
        )
        return
    if not values:
        errors.append(
            f"Step '{step_name}': invalid agentic_extraction config.{field_name} []. "
            f"At least one preprocess mode is required."
        )
        return
    invalid = [mode for mode in values if mode not in _AGENTIC_EXTRACTION_PREPROCESS_MODES]
    if invalid:
        errors.append(
            f"Step '{step_name}': invalid agentic_extraction config.{field_name} "
            f"{invalid!r}. Valid values: {sorted(_AGENTIC_EXTRACTION_PREPROCESS_MODES)}."
        )


def _check_agentic_extraction_config(steps: list[dict], errors: list[str]) -> None:
    """Catch invalid enum values on agentic_extraction's advanced backend config."""

    for step in steps:
        if step.get("type") != "agentic_extraction":
            continue
        step_name = step.get("name", "<unnamed>")
        config = step.get("config") or {}
        if not isinstance(config, dict):
            continue

        backend = config.get("extraction_backend")
        if backend is not None and backend not in _AGENTIC_EXTRACTION_BACKENDS:
            errors.append(
                f"Step '{step_name}': invalid agentic_extraction config.extraction_backend "
                f"{backend!r}. Valid values: {sorted(_AGENTIC_EXTRACTION_BACKENDS)}."
            )

        preprocess_modes = config.get("preprocess_modes")
        if preprocess_modes is not None:
            _validate_preprocess_modes_value(
                step_name=step_name,
                field_name="preprocess_modes",
                value=preprocess_modes,
                errors=errors,
            )
        else:
            _validate_preprocess_modes_value(
                step_name=step_name,
                field_name="preprocess_mode",
                value=config.get("preprocess_mode"),
                errors=errors,
            )


def _check_prepare_documents_user_document_ids(steps: list[dict], errors: list[str]) -> None:
    """Error if prepare_documents lacks `user_document_ids` from Workflow Dispatcher.

    Without this mapping the step runs against an empty list and every downstream
    step silently produces empty output. The backend save validator does not catch
    this because `user_document_ids` is not declared `required` in step_metadata.
    """
    for step in steps:
        if step.get("type") != "prepare_documents":
            continue
        step_name = step.get("name", "<unnamed>")
        wired = False
        for mapping in step.get("input_mappings", []):
            if mapping.get("input_parameter_name") != "user_document_ids":
                continue
            if mapping.get("input_type") != "dependency":
                continue
            for dep in mapping.get("value", {}).get("dependency_step_outputs", []):
                if dep.get("step_name") == "Workflow Dispatcher" and dep.get("output_attribute") == "user_document_ids":
                    wired = True
                    break
            if wired:
                break
        if not wired:
            errors.append(
                f"Step '{step_name}': prepare_documents MUST wire "
                f"`user_document_ids` from `Workflow Dispatcher.user_document_ids`. "
                f"Without it, all downstream steps run against empty content."
            )


def _check_extract_rows_schema_shape(steps: list[dict], errors: list[str]) -> None:
    """Error if extract_rows_from_multiple_sources has a non-array extraction_schema.

    The block reads rows from `extraction_schema.items.properties` — an object-typed
    schema (top-level `properties`) trips the backend's draft-state guard and the
    step returns the static envelope (`data: array` with no items). Downstream paths
    like `data.0.<col>.value` then can't resolve.

    Correct shape: `{"type": "array", "items": {"type": "object", "properties": {...}}}`.
    """
    for step in steps:
        if step.get("type") != "extract_rows_from_multiple_sources":
            continue
        step_name = step.get("name", "<unnamed>")
        schema = step.get("config", {}).get("extraction_schema") or {}
        if not isinstance(schema, dict):
            continue
        schema_type = schema.get("type")
        items = schema.get("items") if isinstance(schema.get("items"), dict) else {}
        if schema_type != "array":
            errors.append(
                f"Step '{step_name}': extract_rows_from_multiple_sources requires "
                f"extraction_schema.type == 'array' (got '{schema_type}'). Wrap rows as "
                f"`{{type: 'array', items: {{type: 'object', properties: {{...}}}}}}`."
            )
            continue
        if items.get("type") != "object" or not items.get("properties"):
            errors.append(
                f"Step '{step_name}': extract_rows_from_multiple_sources extraction_schema "
                f"must have `items.type == 'object'` with row columns under `items.properties`."
            )


def _check_named_type_compat_on_function_outputs(steps: list[dict], warnings: list[str]) -> None:
    """Warn when a function step's `output_schema` declares a generic `object`
    that has the FurtherAI Document shape (`user_document_id` + `filename`).

    Save-time type-compat is directional: a downstream input typed `file` accepts
    only `file`-tagged sources, never plain `{type: object, properties: {...}}`.
    Authors who write the object-with-fields form will pass this script's earlier
    structural checks but be rejected by the backend on save.

    Heuristic is intentionally narrow: only flag when both signature properties
    are present AND the object has at most 5 properties. `user_document_id` is
    a FurtherAI-specific identifier, so this combo has effectively zero false
    positives in the codebase. We deliberately do NOT scan for email_headers
    (`from`/`to`/`subject`) or docx_image (`document`/`settings`) — their
    property names are too generic and the false-positive risk outweighs the
    catch rate. Authors writing those shapes intentionally are also rare.
    """
    for step in steps:
        if step.get("type") != "function":
            continue
        step_name = step.get("name", "<unnamed>")
        config = step.get("config") or {}
        output_schema = config.get("output_schema")
        if not isinstance(output_schema, dict):
            continue
        _scan_for_misdeclared_file_type(step_name, output_schema, "config.output_schema", warnings)


_FILE_REQUIRED_PROPS: set[str] = {"user_document_id", "filename"}
_FILE_MAX_PROPS = 5  # any more and it's probably a custom shape that happens to include these


def _scan_for_misdeclared_file_type(
    step_name: str,
    node: object,
    path: str,
    warnings: list[str],
) -> None:
    """Recursively scan an output_schema for object-shaped fields that look like
    the FurtherAI Document shape. Emits one warning per match."""
    if not isinstance(node, dict):
        return
    node_type = node.get("type")
    properties = node.get("properties")

    if (
        node_type == "object"
        and isinstance(properties, dict)
        and _FILE_REQUIRED_PROPS.issubset(properties.keys())
        and len(properties) <= _FILE_MAX_PROPS
    ):
        warnings.append(
            f"Step '{step_name}': {path} declares a generic `object` with "
            f"`user_document_id` + `filename` — looks like a Document. Save-time "
            f"type-compat is directional: an input typed `file` will reject this shape. "
            f'Use `{{"type": "file"}}` instead (the validator auto-expands the '
            f"registry properties)."
        )

    # Recurse into nested properties and array items so nested fields are caught.
    if isinstance(properties, dict):
        for prop_name, prop_def in properties.items():
            _scan_for_misdeclared_file_type(step_name, prop_def, f"{path}.properties.{prop_name}", warnings)
    items = node.get("items")
    if isinstance(items, dict):
        _scan_for_misdeclared_file_type(step_name, items, f"{path}.items", warnings)


# Source of truth: PrepareDocumentsConfig / ClassifyDocumentsConfig in
# workflow/builder/step_models.py. The pydantic configs reject unknown keys at
# load, but the failure surfaces inside the WF Builder UI rather than at JSON
# paste time. Listing the known wrong names lets us catch them locally.
_CLASSIFY_KNOWN_WRONG_KEYS: dict[str, str] = {
    "categories": "classes",
    "default_category": "fallback_class",
    "split_excel_sheets": "split_sheets",
    "split_pages_pdf": "split_pages",
    "allow_multiple_categories": "allow_multiple_categories_per_sheet",
}

_CLASSIFY_VALID_CONFIG_KEYS: set[str] = {
    "classes",
    "fallback_class",
    "split_pages",
    "split_sheets",
    "allow_multiple_categories_per_sheet",
    "output_empty_classes",
    "knowledge_source_category",
    "split_rules",
    "include_hidden_sheets",
    "use_vision_for_classification",
    "use_agent",
    "model",
    "reasoning_effort",
    "verbosity",
    "system_prompt",  # tolerated at config but separately warned about elsewhere
}


_GET_STEP_CONFIG_KEYS: set[str] = {
    "enable_get_step_tool",
    "allowed_get_step_dependencies",
}

_GET_STEP_SUPPORTED_STEP_TYPES: set[str] = {
    "agentic_extraction",
    "agentic_guideline_check",
    "multi_column_qa",
}

_MULTI_COLUMN_QA_VALID_CONFIG_KEYS: set[str] = {
    "input_schema",
    "schema_instances",
    "ai_schema",
    "system_prompt",
    "table_title",
    "table_description",
    "answer_column_display_name",
    "options",
    "show_confidence_score",
    "enable_get_step_tool",
    "allowed_get_step_dependencies",
    "model",
    "reasoning_effort",
    "verbosity",
}


def _check_classify_documents_config(steps: list[dict], errors: list[str]) -> None:
    """Catch wrong field names under `classify_documents.config`.

    The pydantic ClassifyDocumentsConfig in the BE rejects unknown keys, but
    only after the JSON is pasted into the WF Builder. Authors building
    workflows from the skill doc want to find out at validation time, not when
    the UI shows 'required field Classes is empty'.
    """
    for step in steps:
        if step.get("type") != "classify_documents":
            continue
        step_name = step.get("name", "<unnamed>")
        config = step.get("config") or {}
        if not isinstance(config, dict):
            continue

        if "classes" not in config or not isinstance(config.get("classes"), list) or not config["classes"]:
            errors.append(
                f"Step '{step_name}': classify_documents requires a non-empty `config.classes` list "
                f"of `{{name, description}}` entries."
            )

        for key in config:
            if key in _CLASSIFY_KNOWN_WRONG_KEYS:
                correct = _CLASSIFY_KNOWN_WRONG_KEYS[key]
                errors.append(
                    f"Step '{step_name}': `config.{key}` is not a valid classify_documents field. "
                    f"Use `config.{correct}` instead. The runtime rejects this on save."
                )
            elif key not in _CLASSIFY_VALID_CONFIG_KEYS:
                errors.append(
                    f"Step '{step_name}': unknown classify_documents config key `{key}`. "
                    f"Valid keys: {sorted(_CLASSIFY_VALID_CONFIG_KEYS)}."
                )


def _check_multi_column_qa_config(steps: list[dict], errors: list[str], warnings: list[str]) -> None:
    """Catch drift from MultiColumnQAConfig, including get_step allowlists."""

    step_names = {step.get("name") for step in steps if isinstance(step.get("name"), str)}

    for step in steps:
        step_type = step.get("type")
        step_name = step.get("name", "<unnamed>")
        config = step.get("config") or {}
        if not isinstance(config, dict):
            continue

        if step_type != "multi_column_qa":
            unsupported_get_step_keys = sorted(_GET_STEP_CONFIG_KEYS.intersection(config.keys()))
            if unsupported_get_step_keys and step_type not in _GET_STEP_SUPPORTED_STEP_TYPES:
                errors.append(
                    f"Step '{step_name}': get_step config keys {unsupported_get_step_keys} are not supported "
                    f"on step type `{step_type}`."
                )
            continue

        errors.extend(
            (
                f"Step '{step_name}': unknown multi_column_qa config key `{key}`. "
                f"Valid keys: {sorted(_MULTI_COLUMN_QA_VALID_CONFIG_KEYS)}."
            )
            for key in config
            if key not in _MULTI_COLUMN_QA_VALID_CONFIG_KEYS
        )

        enable_get_step = config.get("enable_get_step_tool", False)
        if not isinstance(enable_get_step, bool):
            errors.append(
                f"Step '{step_name}': `config.enable_get_step_tool` must be a JSON boolean, "
                f"not {type(enable_get_step).__name__}."
            )

        allowlist = config.get("allowed_get_step_dependencies")
        if allowlist is None:
            continue
        if not isinstance(allowlist, list):
            errors.append(
                f"Step '{step_name}': `config.allowed_get_step_dependencies` must be null or a list of step names."
            )
            continue
        if not enable_get_step:
            warnings.append(
                f"Step '{step_name}': `allowed_get_step_dependencies` is ignored unless `enable_get_step_tool` is true."
            )
        if enable_get_step and not allowlist:
            warnings.append(
                f"Step '{step_name}': get_step is enabled with an empty allowlist, so the tool can read nothing."
            )

        seen: set[str] = set()
        for index, dep_name in enumerate(allowlist):
            if not isinstance(dep_name, str) or not dep_name.strip():
                errors.append(
                    f"Step '{step_name}': `config.allowed_get_step_dependencies[{index}]` must be "
                    "a non-empty step name string."
                )
                continue

            if dep_name != dep_name.strip():
                errors.append(
                    f"Step '{step_name}': `config.allowed_get_step_dependencies[{index}]` must not include "
                    "leading or trailing whitespace."
                )
                continue

            if dep_name in seen:
                errors.append(f"Step '{step_name}': duplicate get_step allowlist entry `{dep_name}`.")
            seen.add(dep_name)

            if dep_name == step_name:
                errors.append(f"Step '{step_name}': get_step allowlist cannot reference the step itself.")
            elif dep_name not in step_names:
                errors.append(
                    f"Step '{step_name}': get_step allowlist references unknown step `{dep_name}`. "
                    f"Available steps: {sorted(step_names)}."
                )


_TRELLIS_KNOWN_WRONG_KEYS: dict[str, str] = {
    "extract_payout_from_documents": "extract_payouts_from_documents",
    "extract_payouts_from_document": "extract_payouts_from_documents",
    "extract_payouts": "extract_payouts_from_documents",
    "search_documents": "extract_payouts_from_documents",
}

_TRELLIS_VALID_CONFIG_KEYS: set[str] = {
    "model",
    "reasoning_effort",
    "extract_payouts_from_documents",
}


def _check_trellis_law_config(steps: list[dict], errors: list[str], warnings: list[str]) -> None:
    """Catch Trellis Law config drift from the backend step contract."""
    for step in steps:
        if step.get("type") != "trellis_law":
            continue
        step_name = step.get("name", "<unnamed>")
        config = step.get("config") or {}
        if not isinstance(config, dict):
            continue

        for key, value in config.items():
            if key in _TRELLIS_KNOWN_WRONG_KEYS:
                correct = _TRELLIS_KNOWN_WRONG_KEYS[key]
                errors.append(
                    f"Step '{step_name}': `config.{key}` is not a valid trellis_law field. "
                    f"Use `config.{correct}` instead."
                )
            elif key not in _TRELLIS_VALID_CONFIG_KEYS:
                warnings.append(
                    f"Step '{step_name}': unknown trellis_law config key `{key}`. "
                    f"Known keys: {sorted(_TRELLIS_VALID_CONFIG_KEYS)}."
                )

            if key == "extract_payouts_from_documents" and not isinstance(value, bool):
                errors.append(
                    f"Step '{step_name}': `config.extract_payouts_from_documents` must be a JSON boolean "
                    f"(`true` or `false`), not {type(value).__name__}."
                )


def _iter_dependency_sources(mapping: dict[str, object]) -> list[dict[str, object]]:
    value = mapping.get("value") or {}
    if not isinstance(value, dict):
        return []
    dependency_outputs = value.get("dependency_step_outputs") or []
    if not isinstance(dependency_outputs, list):
        return []
    return [dep for dep in dependency_outputs if isinstance(dep, dict)]


def _enabled_output_schema_properties(config: dict[str, object]) -> dict[str, dict[str, object]]:
    output_schema = config.get("output_schema")
    if not isinstance(output_schema, dict):
        return {}
    raw_properties = output_schema.get("properties")
    if not isinstance(raw_properties, dict):
        return {}
    return {
        str(name): prop
        for name, prop in raw_properties.items()
        if isinstance(prop, dict) and prop.get("enabled") is not False
    }


def _declares_output_schema_properties(config: dict[str, object]) -> bool:
    output_schema = config.get("output_schema")
    if not isinstance(output_schema, dict):
        return False
    raw_properties = output_schema.get("properties")
    return isinstance(raw_properties, dict) and bool(raw_properties)


_RISKMETER_REQUIRED_INPUTS_CACHE: Optional[dict[str, set[str]]] = None


def _riskmeter_required_inputs_by_action_type() -> dict[str, set[str]]:
    """Read RiskMeter required action params from the checked-in OpenAPI spec.

    Keep this self-contained instead of importing the runtime connector package:
    the package imports clients, and local validator runs should not require
    Redis or backend server env.
    """

    global _RISKMETER_REQUIRED_INPUTS_CACHE
    if _RISKMETER_REQUIRED_INPUTS_CACHE is not None:
        return _RISKMETER_REQUIRED_INPUTS_CACHE

    spec_path = Path(__file__).resolve().parents[4] / "integrations/connectors/riskmeter/openapi.json"
    if not spec_path.exists():
        _RISKMETER_REQUIRED_INPUTS_CACHE = {}
        return _RISKMETER_REQUIRED_INPUTS_CACHE

    try:
        spec = json.loads(spec_path.read_text())
    except (OSError, json.JSONDecodeError):
        _RISKMETER_REQUIRED_INPUTS_CACHE = {}
        return _RISKMETER_REQUIRED_INPUTS_CACHE

    entries: list[tuple[str, dict[str, Any]]] = []
    for path, path_item in sorted((spec.get("paths") or {}).items()):
        if not isinstance(path, str) or not isinstance(path_item, dict):
            continue
        operation = path_item.get("get")
        if isinstance(operation, dict):
            entries.append((path, operation))

    action_types_by_path = _riskmeter_action_types_by_path([path for path, _operation in entries])
    required_by_action_type: dict[str, set[str]] = {}
    for path, operation in entries:
        required_inputs = {
            str(param.get("name"))
            for param in operation.get("parameters", [])
            if isinstance(param, dict)
            and param.get("in", "query") == "query"
            and param.get("required") is True
            and param.get("name")
        }
        required_by_action_type[action_types_by_path[path]] = required_inputs

    _RISKMETER_REQUIRED_INPUTS_CACHE = required_by_action_type
    return required_by_action_type


def _riskmeter_action_types_by_path(paths: list[str]) -> dict[str, str]:
    leaf_counts = Counter(_riskmeter_leaf_action_type(path) for path in paths)
    assigned: dict[str, str] = {}
    used: dict[str, str] = {}

    for path in sorted(paths):
        leaf_action_type = _riskmeter_leaf_action_type(path)
        action_type = leaf_action_type if leaf_counts[leaf_action_type] <= 1 else _riskmeter_slug(path.strip("/"))
        base_action_type = action_type
        suffix = 2
        while action_type in used and used[action_type] != path:
            action_type = f"{base_action_type}_{suffix}"
            suffix += 1
        used[action_type] = path
        assigned[path] = action_type

    return assigned


def _riskmeter_leaf_action_type(path: str) -> str:
    return _riskmeter_slug(path.rstrip("/").split("/")[-1])


def _riskmeter_slug(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", value.lower())).strip("_")


# SharePoint per-action required inputs. Mirror of the operation input_schemas'
# `required` lists in integrations/connectors/sharepoint/metadata.py. The Excel
# write actions additionally require exactly one of workbook_path/workbook_item_id
# (a runner-validated XOR that JSON Schema `required` cannot express), handled
# below.
_SHAREPOINT_REQUIRED_INPUTS_BY_ACTION_TYPE: dict[str, set[str]] = {
    "list_items": set(),
    "download_items": {"items"},
    "upload_documents": {"documents"},
    "update_excel_range": {"worksheet_name", "range_address", "values"},
    "append_excel_table_rows": {"table_name", "rows"},
}
_SHAREPOINT_WORKBOOK_REF_ACTION_TYPES = {"update_excel_range", "append_excel_table_rows"}


def _check_financepro_contract(steps: list[dict], errors: list[str]) -> None:
    """Mirror FinancePro's backend single-call-only validation."""
    for step in steps:
        if step.get("type") != "financepro":
            continue
        run_type = step.get("run_type", "single_call")
        if run_type != "single_call":
            step_name = step.get("name", "<unnamed>")
            errors.append(
                f"Step '{step_name}': financepro supports `run_type: \"single_call\"` only, got `{run_type}`."
            )


def _check_sharepoint_contract(steps: list[dict], errors: list[str]) -> None:
    """Mirror the SharePoint per-action input contract used by backend execution."""
    for step in steps:
        if step.get("type") != "sharepoint":
            continue

        step_name = step.get("name", "<unnamed>")
        config = step.get("config") or {}
        if not isinstance(config, dict):
            errors.append(f"Step '{step_name}': `config` must be an object.")
            continue

        action_type = config.get("action_type")
        if not isinstance(action_type, str) or not action_type.strip():
            errors.append(f"Step '{step_name}': sharepoint requires `config.action_type`.")
            continue
        action_type = action_type.strip()
        if action_type not in _SHAREPOINT_REQUIRED_INPUTS_BY_ACTION_TYPE:
            errors.append(
                f"Step '{step_name}': unknown sharepoint `config.action_type` '{action_type}'. "
                f"Valid: {sorted(_SHAREPOINT_REQUIRED_INPUTS_BY_ACTION_TYPE)}."
            )
            continue

        run_type = step.get("run_type", "single_call")
        if run_type != "single_call":
            errors.append(
                f"Step '{step_name}': sharepoint supports `run_type: \"single_call\"` only, got `{run_type}`."
            )

        mapped = {
            m.get("input_parameter_name") for m in (step.get("input_mappings") or []) if m.get("input_parameter_name")
        }
        missing = _SHAREPOINT_REQUIRED_INPUTS_BY_ACTION_TYPE[action_type] - mapped
        if missing:
            errors.append(
                f"Step '{step_name}': sharepoint `{action_type}` is missing required input mappings: "
                f"{sorted(missing)}. Add input_mappings entries for each."
            )

        if action_type in _SHAREPOINT_WORKBOOK_REF_ACTION_TYPES:
            has_path = "workbook_path" in mapped
            has_item_id = "workbook_item_id" in mapped
            if has_path == has_item_id:
                errors.append(
                    f"Step '{step_name}': sharepoint `{action_type}` requires exactly one of "
                    "`workbook_path` or `workbook_item_id` input mappings (not both, not neither)."
                )


# Outlook mail per-action required inputs. Mirror of the operation input_schemas'
# `required` lists in integrations/connectors/outlook_mail/metadata.py. Message
# actions additionally require exactly one of graph_message_id/internet_message_id
# (a runner-validated XOR that JSON Schema `required` cannot express), handled below.
_OUTLOOK_MAIL_REQUIRED_INPUTS_BY_ACTION_TYPE: dict[str, set[str]] = {
    "move_to_folder": {"destination_folder"},
    "apply_category": {"categories"},
    "resolve_folder": {"destination_folder"},
    "find_message": {"internet_message_id"},
    "get_message": set(),
    "list_folders": set(),
    "create_folder": {"folder_name"},
    "list_attachments": set(),
    "download_attachment": set(),
    "read_message": set(),
    "send_mail": {"to_recipients", "subject", "body"},
    "reply": {"comment"},
    "get_incoming_message": set(),
}
# Actions that target one message via graph_message_id XOR internet_message_id.
_OUTLOOK_MAIL_MESSAGE_IDENTITY_ACTIONS = {
    "move_to_folder",
    "apply_category",
    "get_message",
    "list_attachments",
    "download_attachment",
    "read_message",
    "reply",
}


def _check_outlook_mail_contract(steps: list[dict], errors: list[str]) -> None:
    """Mirror the Outlook mail per-action input contract used by backend execution."""
    for step in steps:
        if step.get("type") != "outlook_mail":
            continue

        step_name = step.get("name", "<unnamed>")
        config = step.get("config") or {}
        if not isinstance(config, dict):
            errors.append(f"Step '{step_name}': `config` must be an object.")
            continue

        action_type = config.get("action_type")
        if not isinstance(action_type, str) or not action_type.strip():
            errors.append(f"Step '{step_name}': outlook_mail requires `config.action_type`.")
            continue
        action_type = action_type.strip()
        if action_type not in _OUTLOOK_MAIL_REQUIRED_INPUTS_BY_ACTION_TYPE:
            errors.append(
                f"Step '{step_name}': unknown outlook_mail `config.action_type` '{action_type}'. "
                f"Valid: {sorted(_OUTLOOK_MAIL_REQUIRED_INPUTS_BY_ACTION_TYPE)}."
            )
            continue

        run_type = step.get("run_type", "single_call")
        if run_type != "single_call":
            errors.append(
                f"Step '{step_name}': outlook_mail supports `run_type: \"single_call\"` only, got `{run_type}`."
            )

        mapped = {
            m.get("input_parameter_name")
            for m in (step.get("input_mappings") or [])
            if isinstance(m, dict) and m.get("input_parameter_name")
        }
        missing = _OUTLOOK_MAIL_REQUIRED_INPUTS_BY_ACTION_TYPE[action_type] - mapped
        if missing:
            errors.append(
                f"Step '{step_name}': outlook_mail `{action_type}` is missing required input mappings: "
                f"{sorted(missing)}. Add input_mappings entries for each."
            )

        if action_type in _OUTLOOK_MAIL_MESSAGE_IDENTITY_ACTIONS:
            has_graph_id = "graph_message_id" in mapped
            has_internet_id = "internet_message_id" in mapped
            if has_graph_id == has_internet_id:
                errors.append(
                    f"Step '{step_name}': outlook_mail `{action_type}` requires exactly one of "
                    "`graph_message_id` or `internet_message_id` input mappings (not both, not neither)."
                )


# action_type -> required input parameter names for the single_call lookup actions.
# Mirror of the `required` lists in integrations/connectors/nhtsa/lookup_metadata.py.
# The batch "vin_decode" action is handled separately below; it requires a
# row-source `vin` input_mapping from config.table_to_enrich.
# Keep in sync.
_NHTSA_LOOKUP_REQUIRED_INPUTS: dict[str, list[str]] = {
    "decode_vin": ["vin"],
    "decode_vin_values": ["vin"],
    "decode_vin_extended": ["vin"],
    "decode_vin_values_extended": ["vin"],
    "decode_wmi": ["wmi"],
    "get_wmis_for_manufacturer": ["manufacturer"],
    "get_all_makes": [],
    "get_make_for_manufacturer": ["manufacturer"],
    "get_makes_for_manufacturer_and_year": ["manufacturer", "year"],
    "get_makes_for_vehicle_type": ["vehicleType"],
    "get_vehicle_types_for_make": ["make"],
    "get_vehicle_types_for_make_id": ["makeId"],
    "get_models_for_make": ["make"],
    "get_models_for_make_id": ["makeId"],
    "get_models_for_make_year": ["make", "modelYear"],
    "get_models_for_make_id_year": ["makeId", "modelYear"],
    "get_all_manufacturers": [],
    "get_manufacturer_details": ["manufacturer"],
    "get_vehicle_variable_list": [],
    "get_vehicle_variable_values_list": ["variable"],
    "get_equipment_plant_codes": ["year", "equipmentType", "reportType"],
    "get_parts": ["type", "fromDate", "toDate"],
    "get_canadian_vehicle_specifications": ["year"],
}
# Full action_type space for the unified `nhtsa` step: batch decode + lookups.
_NHTSA_VALID_ACTIONS = {"vin_decode", *_NHTSA_LOOKUP_REQUIRED_INPUTS}
# Lookup actions whose Results are a multi-record list (key-value decode variants),
# so per-row loop enrichment ("first match") is meaningless — single_call only.
# Mirror of supports_table_enrichment=False in lookup_metadata.py.
_NHTSA_LOOP_INCOMPATIBLE = {"decode_vin", "decode_vin_extended"}


def _has_row_source_mapping(step: dict, table_name: str, input_parameter_name: Optional[str] = None) -> bool:
    """True if any input_mapping pulls a row value from ``table_name`` via schema.<column>.

    Mirrors the backend loop rule (_validate_integration_loop_step): a loop step must
    map at least one param from the enriched table's row columns.
    """
    for mapping in step.get("input_mappings") or []:
        if not isinstance(mapping, dict) or mapping.get("input_type") != "dependency":
            continue
        if input_parameter_name is not None and mapping.get("input_parameter_name") != input_parameter_name:
            continue
        value = mapping.get("value") or {}
        for dep in value.get("dependency_step_outputs", []) if isinstance(value, dict) else []:
            attr = dep.get("output_attribute") or ""
            if dep.get("step_name") == table_name and attr.startswith("schema."):
                return True
    return False


def _check_nhtsa_contract(steps: list[dict], errors: list[str], all_names: set[str]) -> None:
    """Mirror the unified NHTSA vPIC step contract used by backend execution.

    One `nhtsa` step, keyless. ``action_type`` selects the operation:
    - ``vin_decode`` — single_call decodes one mapped ``vin`` into a fresh table;
      loop maps ``vin`` from config.table_to_enrich rows and appends decoded
      columns, batching 50/request.
    - any other action — lookup: that action's required path/query params must be
      supplied via input_mappings. Single-call produces a fresh Results table; loop
      mode enriches rows from config.table_to_enrich when the action supports it.
    """
    steps_by_name = {step.get("name"): step for step in steps if isinstance(step.get("name"), str)}

    for step in steps:
        if step.get("type") != "nhtsa":
            continue

        step_name = step.get("name", "<unnamed>")
        config = step.get("config") or {}
        if not isinstance(config, dict):
            errors.append(f"Step '{step_name}': `config` must be an object.")
            continue

        action_type = config.get("action_type", "vin_decode")
        if not isinstance(action_type, str) or not action_type.strip():
            errors.append(f"Step '{step_name}': nhtsa requires `config.action_type`.")
            continue
        action_type = action_type.strip()
        if action_type not in _NHTSA_VALID_ACTIONS:
            errors.append(
                f"Step '{step_name}': unknown nhtsa `config.action_type` '{action_type}'. "
                f"Valid: {sorted(_NHTSA_VALID_ACTIONS)}."
            )
            continue

        run_type = step.get("run_type", "single_call")
        is_batch_decode = action_type == "vin_decode"

        if is_batch_decode:
            if run_type not in {"single_call", "loop"}:
                errors.append(
                    f"Step '{step_name}': nhtsa action 'vin_decode' supports `run_type` 'single_call' or "
                    f"'loop', got `{run_type}`."
                )
            mapped_params = {
                mapping.get("input_parameter_name")
                for mapping in (step.get("input_mappings") or [])
                if isinstance(mapping, dict)
            }
            if "vin" not in mapped_params:
                errors.append(f"Step '{step_name}': nhtsa 'vin_decode' requires input_mapping `vin`.")
            table_to_enrich = config.get("table_to_enrich")
            table_name = table_to_enrich.strip() if isinstance(table_to_enrich, str) else ""
            if run_type == "loop":
                if not table_name:
                    errors.append(f"Step '{step_name}': nhtsa 'vin_decode' requires `config.table_to_enrich`.")
                elif table_name not in all_names:
                    errors.append(
                        f"Step '{step_name}': `config.table_to_enrich` references non-existent step "
                        f"'{table_name}'. Available: {sorted(all_names)}"
                    )
                elif table_name in steps_by_name and not _step_has_table_output(steps_by_name[table_name]):
                    errors.append(
                        f"Step '{step_name}': `config.table_to_enrich` must reference a step with `output_type: table`."
                    )
                if table_name and not _has_row_source_mapping(step, table_name, "vin"):
                    errors.append(
                        f"Step '{step_name}': nhtsa 'vin_decode' needs at least one input_mapping from "
                        f"`{table_name}` using `schema.<column>`; map `vin` from the enriched table row."
                    )
            elif table_name:
                errors.append(
                    f"Step '{step_name}': nhtsa 'vin_decode' is single_call and takes no `config.table_to_enrich`."
                )
            if run_type == "single_call" and (config.get("mode") == "validate" or config.get("validate_columns")):
                errors.append(
                    f"Step '{step_name}': nhtsa 'vin_decode' validate mode only applies to `run_type: \"loop\"`; "
                    'remove `config.mode: "validate"` / `config.validate_columns` or use loop mode.'
                )
        elif run_type == "loop":
            if action_type in _NHTSA_LOOP_INCOMPATIBLE:
                errors.append(
                    f"Step '{step_name}': nhtsa action '{action_type}' does not support `run_type: \"loop\"` "
                    "(its results are not a single enrichable record); use `single_call`."
                )
            # Per-row enrichment: params come from row sources, so we require a
            # table_to_enrich (referencing a TABLE step) rather than static params.
            table_to_enrich = config.get("table_to_enrich")
            table_name = table_to_enrich.strip() if isinstance(table_to_enrich, str) else ""
            if not table_name:
                errors.append(
                    f"Step '{step_name}': nhtsa lookup action '{action_type}' in `run_type: \"loop\"` requires "
                    "`config.table_to_enrich`."
                )
            elif table_name not in all_names:
                errors.append(
                    f"Step '{step_name}': `config.table_to_enrich` references non-existent step "
                    f"'{table_name}'. Available: {sorted(all_names)}"
                )
            elif table_name in steps_by_name and not _step_has_table_output(steps_by_name[table_name]):
                errors.append(
                    f"Step '{step_name}': `config.table_to_enrich` must reference a step with `output_type: table`."
                )
            # Mirror the backend loop rule: at least one input_mapping must be a row
            # source — a dependency on table_to_enrich with `schema.<column>` — so
            # each row queries with its own params (matches RiskMeter's skill check).
            if table_name and not _has_row_source_mapping(step, table_name):
                errors.append(
                    f"Step '{step_name}': nhtsa lookup action '{action_type}' in loop mode needs at least one "
                    f"input_mapping from `{table_name}` using `schema.<column>` (its params, e.g. "
                    f"{_NHTSA_LOOKUP_REQUIRED_INPUTS.get(action_type, [])}, come from row columns)."
                )
        else:
            # single_call: params supplied as static/dependency input_mappings; no table_to_enrich.
            if run_type != "single_call":
                errors.append(
                    f"Step '{step_name}': nhtsa action '{action_type}' supports `run_type` 'single_call' or "
                    f"'loop', got `{run_type}`."
                )
            if config.get("table_to_enrich"):
                errors.append(
                    f"Step '{step_name}': nhtsa lookup action '{action_type}' is single_call and takes no "
                    "`config.table_to_enrich`."
                )
            mapped_params = {
                mapping.get("input_parameter_name")
                for mapping in (step.get("input_mappings") or [])
                if isinstance(mapping, dict)
            }
            errors.extend(
                f"Step '{step_name}': nhtsa action '{action_type}' requires input_mapping `{required_param}`."
                for required_param in _NHTSA_LOOKUP_REQUIRED_INPUTS.get(action_type, [])
                if required_param not in mapped_params
            )

        # Shared output_schema check (both modes).
        if _declares_output_schema_properties(config):
            enabled_properties = _enabled_output_schema_properties(config)
            if not enabled_properties:
                errors.append(
                    f"Step '{step_name}': explicit nhtsa `config.output_schema.properties` must include "
                    "at least one enabled property."
                )
            else:
                missing_source_path = [
                    name
                    for name, prop in enabled_properties.items()
                    if not isinstance(prop.get("source_path"), str) or not prop["source_path"].strip()
                ]
                if missing_source_path:
                    errors.append(
                        f"Step '{step_name}': enabled output_schema properties must declare "
                        f"`source_path`: {sorted(missing_source_path)}."
                    )


_MAPRISK_REPORT_ACTION_TYPES = {
    "geocode",
    "hail_risk",
    "wildfire_risk",
    "crime",
    "quake_temblor",
    "county",
    "risk_bundle",
}
_MAPRISK_BATCH_ACTION = "batch"
_MAPRISK_ACTION_TYPES = _MAPRISK_REPORT_ACTION_TYPES | {_MAPRISK_BATCH_ACTION}


def _check_maprisk_batch_action(step: dict, config: dict, step_name: str, errors: list[str]) -> None:
    """Mirror the maprisk `batch` action: requires report_list + column_map + a `document` input.

    Batch is single-call only (it processes an entire CSV in one async job), so
    `run_type: loop` and `config.table_to_enrich` are rejected.
    """
    report_list = config.get("report_list")
    if not isinstance(report_list, list) or not [r for r in report_list if isinstance(r, str) and r.strip()]:
        errors.append(
            f"Step '{step_name}': maprisk `batch` action requires a non-empty `config.report_list` (e.g. ['dss'])."
        )

    column_map = config.get("column_map")
    if column_map is not None and (not isinstance(column_map, dict) or not column_map):
        errors.append(f"Step '{step_name}': maprisk `batch` `config.column_map` must be a non-empty object when set.")

    if step.get("run_type", "single_call") == "loop":
        errors.append(
            f"Step '{step_name}': maprisk `batch` action is single-call only and cannot use `run_type: loop`."
        )
    if isinstance(config.get("table_to_enrich"), str) and config["table_to_enrich"].strip():
        errors.append(f"Step '{step_name}': maprisk `batch` action does not support `config.table_to_enrich`.")

    mapped = {
        m.get("input_parameter_name")
        for m in (step.get("input_mappings") or [])
        if isinstance(m, dict) and m.get("input_parameter_name")
    }
    if "document" not in mapped:
        errors.append(
            f"Step '{step_name}': maprisk `batch` action requires a `document` input mapping (the CSV to process)."
        )


def _check_maprisk_contract(steps: list[dict], errors: list[str], all_names: set[str]) -> None:
    """Mirror the Maprisk integration step contract used by backend execution.

    Report/geocode actions: run_type single_call|loop, address or latitude+longitude
    input, loop mode requires config.table_to_enrich referencing a TABLE step. The
    `batch` action is single-call only and takes a CSV `document` input.
    """
    steps_by_name = {step.get("name"): step for step in steps if isinstance(step.get("name"), str)}

    for step in steps:
        if step.get("type") != "maprisk":
            continue

        step_name = step.get("name", "<unnamed>")
        config = step.get("config") or {}
        if not isinstance(config, dict):
            errors.append(f"Step '{step_name}': `config` must be an object.")
            continue

        action_type = config.get("action_type")
        if not isinstance(action_type, str) or not action_type.strip():
            errors.append(f"Step '{step_name}': maprisk requires `config.action_type`.")
            continue
        if action_type.strip() not in _MAPRISK_ACTION_TYPES:
            errors.append(
                f"Step '{step_name}': unknown maprisk `config.action_type` '{action_type}'. "
                f"Valid: {sorted(_MAPRISK_ACTION_TYPES)}."
            )
            continue

        # The async bulk action has a distinct contract (CSV document, no loop).
        if action_type.strip() == _MAPRISK_BATCH_ACTION:
            _check_maprisk_batch_action(step, config, step_name, errors)
            continue

        run_type = step.get("run_type", "single_call")
        if run_type not in {"single_call", "loop"}:
            errors.append(f"Step '{step_name}': `run_type` must be `single_call` or `loop`, got `{run_type}`.")

        table_to_enrich = config.get("table_to_enrich")
        table_name = table_to_enrich.strip() if isinstance(table_to_enrich, str) else ""

        enabled_properties = (
            _enabled_output_schema_properties(config) if _declares_output_schema_properties(config) else {}
        )
        if _declares_output_schema_properties(config) and not enabled_properties:
            errors.append(
                f"Step '{step_name}': explicit maprisk `config.output_schema.properties` must include "
                "at least one enabled property."
            )
        else:
            missing_source_path = [
                name
                for name, prop in enabled_properties.items()
                if not isinstance(prop.get("source_path"), str) or not prop["source_path"].strip()
            ]
            if missing_source_path:
                errors.append(
                    f"Step '{step_name}': enabled output_schema properties must declare "
                    f"`source_path`: {sorted(missing_source_path)}."
                )

        mapped_inputs = {
            mapping.get("input_parameter_name")
            for mapping in step.get("input_mappings", []) or []
            if isinstance(mapping, dict) and mapping.get("input_parameter_name")
        }
        has_address = "address" in mapped_inputs
        has_coords = "latitude" in mapped_inputs and "longitude" in mapped_inputs
        action = action_type.strip() if isinstance(action_type, str) else ""
        if action == "geocode":
            # The geocode action only geocodes an address; coordinates-only fails at runtime.
            if not has_address:
                errors.append(f"Step '{step_name}': maprisk `geocode` action requires an `address` input mapping.")
        elif not has_address and not has_coords:
            errors.append(
                f"Step '{step_name}': maprisk requires an `address` input mapping or both `latitude` and `longitude`."
            )

        if run_type == "loop":
            if not table_name:
                errors.append(f"Step '{step_name}': `run_type: loop` requires `config.table_to_enrich`.")
            elif table_name not in all_names:
                errors.append(
                    f"Step '{step_name}': `config.table_to_enrich` references non-existent step "
                    f"'{table_name}'. Available: {sorted(all_names)}"
                )
            elif table_name in steps_by_name and not _step_has_table_output(steps_by_name[table_name]):
                errors.append(
                    f"Step '{step_name}': `config.table_to_enrich` must reference a step with `output_type: table`."
                )
        elif table_name:
            errors.append(f"Step '{step_name}': `config.table_to_enrich` is only valid when `run_type` is `loop`.")


def _check_pitchbook_contract(steps: list[dict], errors: list[str], all_names: set[str]) -> None:
    """Mirror the PitchBook integration step contract used by backend execution.

    Single action (company_enrichment), run_type single_call|loop, company_name input,
    and loop mode requires config.table_to_enrich referencing a TABLE step.
    """
    steps_by_name = {step.get("name"): step for step in steps if isinstance(step.get("name"), str)}

    for step in steps:
        if step.get("type") != "pitchbook":
            continue

        step_name = step.get("name", "<unnamed>")
        config = step.get("config") or {}
        if not isinstance(config, dict):
            errors.append(f"Step '{step_name}': `config` must be an object.")
            continue

        action_type = config.get("action_type", "company_enrichment")
        if isinstance(action_type, str) and action_type.strip() and action_type.strip() != "company_enrichment":
            errors.append(
                f"Step '{step_name}': unknown pitchbook `config.action_type` "
                f"'{action_type}'. Valid: ['company_enrichment']."
            )

        run_type = step.get("run_type", "single_call")
        if run_type not in {"single_call", "loop"}:
            errors.append(f"Step '{step_name}': `run_type` must be `single_call` or `loop`, got `{run_type}`.")

        table_to_enrich = config.get("table_to_enrich")
        table_name = table_to_enrich.strip() if isinstance(table_to_enrich, str) else ""

        enabled_properties = (
            _enabled_output_schema_properties(config) if _declares_output_schema_properties(config) else {}
        )
        if _declares_output_schema_properties(config) and not enabled_properties:
            errors.append(
                f"Step '{step_name}': explicit pitchbook `config.output_schema.properties` must include "
                "at least one enabled property."
            )
        else:
            missing_source_path = [
                name
                for name, prop in enabled_properties.items()
                if not isinstance(prop.get("source_path"), str) or not prop["source_path"].strip()
            ]
            if missing_source_path:
                errors.append(
                    f"Step '{step_name}': enabled output_schema properties must declare "
                    f"`source_path`: {sorted(missing_source_path)}."
                )

        mapped_inputs = {
            mapping.get("input_parameter_name")
            for mapping in step.get("input_mappings", []) or []
            if isinstance(mapping, dict) and mapping.get("input_parameter_name")
        }
        if "company_name" not in mapped_inputs:
            errors.append(f"Step '{step_name}': pitchbook requires a `company_name` input mapping.")

        if run_type == "loop":
            if not table_name:
                errors.append(f"Step '{step_name}': `run_type: loop` requires `config.table_to_enrich`.")
            elif table_name not in all_names:
                errors.append(
                    f"Step '{step_name}': `config.table_to_enrich` references non-existent step "
                    f"'{table_name}'. Available: {sorted(all_names)}"
                )
            elif table_name not in steps_by_name:
                available_table_steps = sorted(
                    name for name, source_step in steps_by_name.items() if _step_has_table_output(source_step)
                )
                errors.append(
                    f"Step '{step_name}': `config.table_to_enrich` must reference a concrete workflow step "
                    f"with `output_type: table`. Available table steps: {available_table_steps}"
                )
            elif not _step_has_table_output(steps_by_name[table_name]):
                errors.append(
                    f"Step '{step_name}': `config.table_to_enrich` must reference a step with `output_type: table`."
                )
        elif table_name:
            errors.append(f"Step '{step_name}': `config.table_to_enrich` is only valid when `run_type` is `loop`.")

        # Row-source rule (mirrors backend _validate_integration_loop_step, like
        # RiskMeter/NHTSA): in loop mode at least one input mapping must pull from
        # config.table_to_enrich via `output_attribute: schema.<column>`. A loop
        # step that maps company_name to a static value / non-table dependency
        # passes input completeness but the backend rejects it at publish.
        row_source_params: set[str] = set()
        for mapping in step.get("input_mappings", []) or []:
            if not isinstance(mapping, dict):
                continue
            input_name = mapping.get("input_parameter_name")
            if mapping.get("input_type") == "variable":
                value = mapping.get("value")
                if isinstance(value, str) and value.startswith("current_row."):
                    errors.append(
                        f"Step '{step_name}': do not use legacy `{value}` variable mappings. "
                        "Map loop-table columns as dependency sources with `output_attribute: schema.<column>`."
                    )
            if mapping.get("input_type") != "dependency" or not table_name:
                continue
            for dep in _iter_dependency_sources(mapping):
                if dep.get("step_name") != table_name:
                    continue
                output_attribute = dep.get("output_attribute")
                if not isinstance(output_attribute, str) or not output_attribute.startswith("schema."):
                    errors.append(
                        f"Step '{step_name}', input `{input_name}`: loop-table dependency sources must use "
                        "`output_attribute: schema.<column_name>`."
                    )
                elif not output_attribute.removeprefix("schema.").strip():
                    errors.append(
                        f"Step '{step_name}', input `{input_name}`: loop-table dependency source is missing "
                        "the column name after `schema.`."
                    )
                else:
                    row_source_params.add(str(input_name))
        if run_type == "loop" and table_name and not row_source_params:
            errors.append(
                f"Step '{step_name}': `run_type: loop` requires at least one input mapping from "
                "`config.table_to_enrich` using `output_attribute: schema.<column_name>`."
            )


def _check_riskmeter_contract(steps: list[dict], errors: list[str], all_names: set[str]) -> None:
    """Mirror the RiskMeter integration step contract used by backend execution."""
    required_inputs_by_action_type = _riskmeter_required_inputs_by_action_type()
    steps_by_name = {step.get("name"): step for step in steps if isinstance(step.get("name"), str)}

    for step in steps:
        if step.get("type") != "riskmeter":
            continue

        step_name = step.get("name", "<unnamed>")
        config = step.get("config") or {}
        if not isinstance(config, dict):
            errors.append(f"Step '{step_name}': `config` must be an object.")
            continue

        action_type = config.get("action_type")
        if not isinstance(action_type, str) or not action_type.strip():
            errors.append(f"Step '{step_name}': riskmeter requires `config.action_type`.")
            action_type = ""
        else:
            action_type = action_type.strip()
            if required_inputs_by_action_type and action_type not in required_inputs_by_action_type:
                errors.append(
                    f"Step '{step_name}': unknown riskmeter `config.action_type` '{action_type}'. "
                    f"Valid: {sorted(required_inputs_by_action_type)}."
                )

        if "actions" in config:
            errors.append(
                f"Step '{step_name}': riskmeter supports one action per step; "
                "use `config.action_type`, not `config.actions`."
            )
        if "execution_mode" in config:
            errors.append(
                f"Step '{step_name}': use step-level `run_type` (`single_call` or `loop`), not `config.execution_mode`."
            )

        run_type = step.get("run_type", "single_call")
        if run_type not in {"single_call", "loop"}:
            errors.append(f"Step '{step_name}': `run_type` must be `single_call` or `loop`, got `{run_type}`.")

        table_to_enrich = config.get("table_to_enrich")
        table_name = table_to_enrich.strip() if isinstance(table_to_enrich, str) else ""
        enabled_properties = (
            _enabled_output_schema_properties(config) if _declares_output_schema_properties(config) else {}
        )
        if _declares_output_schema_properties(config) and not enabled_properties:
            errors.append(
                f"Step '{step_name}': explicit riskmeter `config.output_schema.properties` must include "
                "at least one enabled property."
            )
        elif enabled_properties:
            missing_source_path = [
                name
                for name, prop in enabled_properties.items()
                if not isinstance(prop.get("source_path"), str) or not prop["source_path"].strip()
            ]
            if missing_source_path:
                errors.append(
                    f"Step '{step_name}': enabled output_schema properties must declare "
                    f"`source_path`: {sorted(missing_source_path)}."
                )

        if run_type == "loop":
            if not table_name:
                errors.append(f"Step '{step_name}': `run_type: loop` requires `config.table_to_enrich`.")
            elif table_name not in all_names:
                errors.append(
                    f"Step '{step_name}': `config.table_to_enrich` references non-existent step "
                    f"'{table_name}'. Available: {sorted(all_names)}"
                )
            elif table_name not in steps_by_name:
                available_table_steps = sorted(
                    name for name, source_step in steps_by_name.items() if _step_has_table_output(source_step)
                )
                errors.append(
                    f"Step '{step_name}': `config.table_to_enrich` must reference a concrete workflow step "
                    f"with `output_type: table`. Available table steps: {available_table_steps}"
                )
            elif not _step_has_table_output(steps_by_name[table_name]):
                errors.append(
                    f"Step '{step_name}': `config.table_to_enrich` must reference a step with `output_type: table`."
                )
        elif isinstance(table_to_enrich, str) and table_to_enrich.strip():
            errors.append(f"Step '{step_name}': `config.table_to_enrich` is only valid when `run_type` is `loop`.")

        mapped_inputs = {
            mapping.get("input_parameter_name")
            for mapping in step.get("input_mappings", []) or []
            if isinstance(mapping, dict) and mapping.get("input_parameter_name")
        }
        required_inputs = (
            required_inputs_by_action_type.get(action_type, set()) if isinstance(action_type, str) else set()
        )
        missing_inputs = required_inputs - mapped_inputs
        if missing_inputs:
            errors.append(
                f"Step '{step_name}': riskmeter action `{action_type}` is missing required input mappings: "
                f"{sorted(missing_inputs)}."
            )

        row_source_params: set[str] = set()
        for mapping in step.get("input_mappings", []) or []:
            if not isinstance(mapping, dict):
                continue
            input_name = mapping.get("input_parameter_name")
            if input_name == "risk_locations":
                errors.append(
                    f"Step '{step_name}': do not map legacy `risk_locations`; "
                    "put the selected table in `config.table_to_enrich`."
                )
            if mapping.get("input_type") == "variable":
                value = mapping.get("value")
                if isinstance(value, str) and value.startswith("current_row."):
                    errors.append(
                        f"Step '{step_name}': do not use legacy `{value}` variable mappings. "
                        "Map loop-table columns as dependency sources with `output_attribute: schema.<column>`."
                    )
            if mapping.get("input_type") != "dependency" or not table_name:
                continue
            for dep in _iter_dependency_sources(mapping):
                if dep.get("step_name") != table_name:
                    continue
                output_attribute = dep.get("output_attribute")
                if not isinstance(output_attribute, str) or not output_attribute.startswith("schema."):
                    errors.append(
                        f"Step '{step_name}', input `{input_name}`: loop-table dependency sources must use "
                        "`output_attribute: schema.<column_name>`."
                    )
                elif not output_attribute.removeprefix("schema.").strip():
                    errors.append(
                        f"Step '{step_name}', input `{input_name}`: loop-table dependency source is missing "
                        "the column name after `schema.`."
                    )
                else:
                    row_source_params.add(str(input_name))
        if run_type == "loop" and table_name and not row_source_params:
            errors.append(
                f"Step '{step_name}': `run_type: loop` requires at least one input mapping from "
                "`config.table_to_enrich` using `output_attribute: schema.<column_name>`."
            )


def _step_has_table_output(step: dict[str, object]) -> bool:
    output_type = step.get("output_type")
    if isinstance(output_type, str):
        return output_type == "table"
    return step.get("type") in DEFAULT_TABLE_OUTPUT_STEP_TYPES


def _check_classify_category_references(steps: list[dict], errors: list[str]) -> None:
    """Catch downstream wirings to undefined classify categories.

    `classify_documents.documents` is a dict keyed by class name. A downstream
    wiring like `output_attribute: "documents.LossRun"` against a step whose
    `classes` is `[{name: "Loss_Run"}, ...]` returns an empty list at runtime
    (key miss, no error). Every downstream step then runs green with empty
    data — same silent-failure mode as a missing prepare_documents wiring.
    Catch the typo here.
    """
    # Map classify step name -> set of valid class names from its config.
    classify_classes: dict[str, set[str]] = {}
    for step in steps:
        if step.get("type") != "classify_documents":
            continue
        name = step.get("name")
        classes = (step.get("config") or {}).get("classes")
        if not name or not isinstance(classes, list):
            continue
        class_names = {c.get("name") for c in classes if isinstance(c, dict) and c.get("name")}
        if class_names:
            classify_classes[name] = class_names

    if not classify_classes:
        return

    for step in steps:
        step_name = step.get("name", "<unnamed>")
        for mapping in step.get("input_mappings", []) or []:
            if mapping.get("input_type") != "dependency":
                continue
            for dep in (mapping.get("value") or {}).get("dependency_step_outputs", []) or []:
                src_name = dep.get("step_name")
                if src_name not in classify_classes:
                    continue
                attr = dep.get("output_attribute") or ""
                # We care about `documents.<X>...` paths only.
                if not attr.startswith("documents."):
                    continue
                # First segment after "documents" is the category name.
                parts = attr.split(".", 2)  # ["documents", "<class>", "..."]
                if len(parts) < 2 or not parts[1]:
                    continue
                referenced = parts[1]
                if referenced not in classify_classes[src_name]:
                    errors.append(
                        f"Step '{step_name}': output_attribute `{attr}` references class "
                        f"'{referenced}' on `{src_name}`, but that step's `config.classes` "
                        f"only defines {sorted(classify_classes[src_name])}. A typo here returns "
                        f"an empty list at runtime — every downstream step runs green with no data."
                    )


# Required user-mappable input parameters per step type. Mirrors:
#   STEP_INPUT_SCHEMAS - SYSTEM_INJECTED_PARAMS - CONFIG_PROVIDED_PARAMS
# in workflow/builder/step_schemas.py (BE source of truth, FAI-6817 era).
#
# *** MAINTENANCE NOTE ***
# This table is the single source of "what passes validate_workflow.py" for
# required-input completeness. The BE save validator
# (workflow/builder/validators.py :: validate_step_input_mappings) runs the
# same subtraction (STEP_INPUT_SCHEMAS - SYSTEM_INJECTED_PARAMS -
# CONFIG_PROVIDED_PARAMS) against the same source. If a step type's required
# inputs change in step_schemas.py, this table AND the cheat-sheet table at
# the top of `## Step Type Details` in SKILL.md MUST change too — otherwise
# the script either rejects valid workflows (false positive) or lets broken
# workflows through to a save-time rejection that this script was supposed
# to catch.
#
# To regenerate:
#   PYTHONPATH=. python -c "from workflow.builder.step_schemas import \
#     STEP_INPUT_SCHEMAS, SYSTEM_INJECTED_PARAMS, CONFIG_PROVIDED_PARAMS; \
#     from workflow.models.workflow_v1 import StepType
#   for st in StepType: ..."
#
# Step types intentionally absent here because JSON Schema cannot express
# their source disjunctions; custom checks below mirror BE save validation:
#   - extract_from_multiple_sources (documents OR email source, with prompt)
#   - extract_rows_from_multiple_sources (documents OR emails)
#   - extract_mixed_schema_from_multiple_sources (documents OR emails)
#   - knowledge_base (documents OR email source)
#
# Other absent step types where BE marks no user inputs as required:
#   - agentic_guideline_check
#   - email, web_search, pause, decision, manual_input
#   - workflow_dispatcher (auto-injected)
#   - function, ams360, document_viewer, compare_documents (no STEP_INPUT_SCHEMAS entry)
_STEP_REQUIRED_INPUTS: dict[str, set[str]] = {
    "prepare_documents": {"user_document_ids"},
    "classify_documents": {"documents"},
    "sov_mapping": {"documents"},
    "generate_qa_table_from_agent": {"documents", "kb"},
    "multi_column_qa": {"documents", "kb"},
    "extract_guidelines": {"guideline_document"},
    "check_guidelines": {"extracted_guidelines", "guideline_document", "submission_documents"},
    "hold": {"data"},
    "fill_docx": {"context", "template"},
    "enrich_addresses_with_gmaps": {"data", "metadata", "schema", "user_documents"},
    "text_block": {"markdown_content"},
    "run_workflow": {"documents", "target_workflow_id"},
    "compare_document_data": {"data_points", "user_documents"},
    "submission_summary_generator": {"data_points", "system_prompt"},
    "ofac_agent": {"entity_data"},
    "osha_agent": {"insured_data"},
    "trellis_law": {"insured_names"},
    "agentic_extraction": {"kb"},
    "combine_kv_tables": {"sources"},
    "execution_matching": {"account_name"},
    "loop": {"iteration_source"},
    "web_agent": {"prompt"},
}


def _check_function_step_empty_error_return(steps: list[dict], errors: list[str]) -> None:
    """AST-scan function step `code` for misleading empty-`error` return patterns.

    The BE executor uses `step_output.get("error")` to classify failure (truthy
    check), so an empty error no longer breaks the step at runtime. But authors
    should not include `error` on success at all — it's misleading. Mirrors the
    BE validator check.

    Catches three shapes:
      1. return {"error": "", ...}     — dict literal, empty string
      2. return {"error": None, ...}   — dict literal, None
      3. return dict(error="" | None)  — call-style construction

    Skipped (would require dataflow analysis):
      - return {"error": err} where err = "" earlier
      - result["error"] = ""; return result (assignment-then-return)
    """

    def _is_empty_sentinel(node: ast.AST) -> bool:
        return isinstance(node, ast.Constant) and (node.value == "" or node.value is None)

    for step in steps:
        if step.get("type") != "function":
            continue
        step_name = step.get("name", "<unnamed>")
        code = (step.get("config") or {}).get("code")
        if not code:
            continue
        try:
            tree = ast.parse(code)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            fired = False
            # Shape 1 + 2
            if isinstance(node.value, ast.Dict):
                for k, v in zip(node.value.keys, node.value.values, strict=True):
                    if not (isinstance(k, ast.Constant) and k.value == "error"):
                        continue
                    if _is_empty_sentinel(v):
                        fired = True
                        break
            # Shape 3
            elif (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "dict"
            ):
                for kw in node.value.keywords:
                    if kw.arg == "error" and _is_empty_sentinel(kw.value):
                        fired = True
                        break
            if fired:
                errors.append(
                    f"Step '{step_name}': remove `error` from the return on success — only set it on failure."
                )


_EFMS_SCALAR_EMAIL_INPUT_NAMES = frozenset({"eml_user_document_id", "headers", "email_body", "body_html", "body_plain"})
_EFMS_EMAIL_INPUT_NAMES = frozenset({"emails"}) | _EFMS_SCALAR_EMAIL_INPUT_NAMES
_DOCS_OR_EMAILS_STEP_TYPES = frozenset(
    {"extract_rows_from_multiple_sources", "extract_mixed_schema_from_multiple_sources"}
)
_KB_SCALAR_EMAIL_INPUT_NAMES = frozenset({"email_body", "email_body_plain", "email_headers", "headers"})
_KB_EMAIL_DOC_ID_INPUT_NAMES = frozenset({"email_document_id", "email_user_document_id"})


def _mapped_param_names(step: dict[str, object]) -> set[str]:
    return {
        str(mapping.get("input_parameter_name"))
        for mapping in (step.get("input_mappings") or [])
        if isinstance(mapping, dict) and mapping.get("input_parameter_name")
    }


def _check_extract_from_multiple_sources_pairing(steps: list[dict], errors: list[str]) -> None:
    """Mirror BE's extract_from_multiple_sources rule in workflow/builder/validators.py.

    The BE rejects:
      1. No source mapped — neither `documents` nor any email input
         (`emails`, `eml_user_document_id`, `headers`, `email_body`,
         `body_html`, `body_plain`).
      2. `documents` mapped without a `document_system_prompt`.
      3. Any email input mapped without an `email_system_prompt`.
      4. Scalar email inputs mapped without `eml_user_document_id`.

    JSON Schema can't express the "documents OR email" rule, which is why
    `STEP_INPUT_SCHEMAS[EXTRACT_FROM_MULTIPLE_SOURCES]` lists no required
    inputs and BE enforces the real rule in code. We do the same here.
    """
    for step in steps:
        if step.get("type") != "extract_from_multiple_sources":
            continue
        step_name = step.get("name", "<unnamed>")
        # Treat a mapping as "effectively mapped" if its input_parameter_name
        # is present and the value isn't trivially empty. Mirror BE's
        # _is_effectively_mapped at the loose end (presence-only); BE adds an
        # emptiness check on top, but author-generated workflows never have
        # the param key without intent, so presence-only catches every case
        # we've seen in staging without false positives.
        mapped = _mapped_param_names(step)

        docs_mapped = "documents" in mapped
        emails_mapped = "emails" in mapped
        scalar_email_mapped = bool(mapped & _EFMS_SCALAR_EMAIL_INPUT_NAMES)
        email_mapped = emails_mapped or scalar_email_mapped

        if not (docs_mapped or email_mapped):
            errors.append(
                f"Step '{step_name}': must map at least one of `documents` "
                f"OR an email input (`emails`, `eml_user_document_id`, `headers`, or `email_body`)."
            )
        if docs_mapped and "document_system_prompt" not in mapped:
            errors.append(
                f"Step '{step_name}': `documents` is mapped, so "
                f"`document_system_prompt` must be a non-empty static value or dependency."
            )
        if email_mapped and "email_system_prompt" not in mapped:
            errors.append(
                f"Step '{step_name}': email inputs are mapped, so "
                f"`email_system_prompt` must be a non-empty static value or dependency."
            )
        if scalar_email_mapped and not emails_mapped and "eml_user_document_id" not in mapped:
            errors.append(f"Step '{step_name}': wire `eml_user_document_id` when email inputs are mapped.")


def _check_documents_or_emails_sources(steps: list[dict], errors: list[str]) -> None:
    """Mirror BE rows/mixed source rule: each step needs documents or emails."""
    for step in steps:
        step_type = step.get("type")
        if step_type not in _DOCS_OR_EMAILS_STEP_TYPES:
            continue
        step_name = step.get("name", "<unnamed>")
        mapped = _mapped_param_names(step)
        if "documents" not in mapped and "emails" not in mapped:
            errors.append(f"Step '{step_name}': must map `documents` or `emails`.")


def _check_knowledge_base_sources(steps: list[dict], errors: list[str]) -> None:
    """Mirror BE knowledge_base source rule in workflow/builder/validators.py."""
    for step in steps:
        if step.get("type") != "knowledge_base":
            continue
        step_name = step.get("name", "<unnamed>")
        mapped = _mapped_param_names(step)
        docs_mapped = "documents" in mapped
        emails_mapped = "emails" in mapped
        scalar_email_mapped = bool(mapped & _KB_SCALAR_EMAIL_INPUT_NAMES)
        email_doc_id_mapped = bool(mapped & _KB_EMAIL_DOC_ID_INPUT_NAMES)
        if not (docs_mapped or emails_mapped or scalar_email_mapped):
            errors.append(f"Step '{step_name}': must map `documents`, `emails`, or scalar email inputs.")
        if scalar_email_mapped and not emails_mapped and not email_doc_id_mapped:
            errors.append(f"Step '{step_name}': wire `email_document_id` when scalar email inputs are mapped.")


def _check_required_input_mappings(steps: list[dict], errors: list[str]) -> None:
    """Generic check: every step type's BE-declared required inputs must be
    present in the step's `input_mappings`.

    A workflow passing this check should also pass the BE save validator's
    `Missing required input mappings` rejection. Function steps are excluded
    because their input_schema is config-defined and varies per step.
    """
    for step in steps:
        step_type = step.get("type")
        if step_type not in _STEP_REQUIRED_INPUTS:
            continue
        step_name = step.get("name", "<unnamed>")
        required = _STEP_REQUIRED_INPUTS[step_type]
        mapped = {
            m.get("input_parameter_name") for m in (step.get("input_mappings") or []) if m.get("input_parameter_name")
        }
        missing = required - mapped
        if missing:
            errors.append(
                f"Step '{step_name}': {step_type} is missing required input mappings: "
                f"{sorted(missing)}. Add input_mappings entries for each."
            )


def _check_dag_cycles(steps: list[dict], errors: list[str], all_names: set[str]) -> None:
    """Check for cycles in the step dependency graph."""
    # Build adjacency list from input_mappings
    graph: dict[str, set[str]] = {step["name"]: set() for step in steps if "name" in step}

    # Integration step types whose loop mode enriches an upstream table via
    # config.table_to_enrich — that table is an implicit dependency edge the
    # graph must include so a cycle through it is detected.
    _TABLE_ENRICH_LOOP_TYPES = {
        "alis_dx",
        "riskmeter",
        "hazardhub",
        "maprisk",
        "nhtsa",
        "pitchbook",
        "sambasafety",
        "cotality_valuation",
        "snapsheet",
        "benefitpoint",
        "snapsheet_payments",
        "google_maps",
    }

    for step in steps:
        name = step.get("name", "")
        if step.get("type") in _TABLE_ENRICH_LOOP_TYPES and step.get("run_type") == "loop":
            config = step.get("config") or {}
            table_to_enrich = config.get("table_to_enrich") if isinstance(config, dict) else None
            table_name = table_to_enrich.strip() if isinstance(table_to_enrich, str) else ""
            if name in graph and table_name in graph:
                graph[name].add(table_name)
        for mapping in step.get("input_mappings", []):
            if mapping.get("input_type") != "dependency":
                continue
            value = mapping.get("value", {})
            for dep in value.get("dependency_step_outputs", []):
                ref = dep.get("step_name", "")
                if ref and ref in graph:
                    graph[name].add(ref)

    # Topological sort via DFS
    visited: set[str] = set()
    in_stack: set[str] = set()

    def dfs(node: str) -> bool:
        if node in in_stack:
            return True  # Cycle detected
        if node in visited:
            return False
        visited.add(node)
        in_stack.add(node)
        has_cycle = False
        for dep in graph.get(node, set()):
            if dfs(dep):
                errors.append(f"Dependency cycle detected involving step '{node}' -> '{dep}'")
                has_cycle = True
                break
        in_stack.remove(node)
        return has_cycle

    for node in graph:
        if node not in visited:
            dfs(node)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python tools/validate_workflow.py <workflow.json>")
        sys.exit(1)

    filepath = sys.argv[1]
    if not Path(filepath).exists():
        print(f"Error: File not found: {filepath}")
        sys.exit(1)

    errors, warnings = validate_workflow(filepath)

    # Print results
    if warnings:
        print(f"\n⚠  {len(warnings)} warning(s):")
        for w in warnings:
            print(f"   - {w}")

    if errors:
        print(f"\n✗  {len(errors)} error(s):")
        for e in errors:
            print(f"   - {e}")
        sys.exit(1)
    else:
        # Count steps
        with open(filepath) as f:
            data = json.load(f)
        step_count = len(data.get("steps", []))
        print(f"\n✓  Valid workflow: '{data.get('name', 'unnamed')}' with {step_count} steps, 0 errors")
        sys.exit(0)


if __name__ == "__main__":
    main()
