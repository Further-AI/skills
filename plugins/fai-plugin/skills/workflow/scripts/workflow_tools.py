#!/usr/bin/env python3
"""workflow_tools.py — CLI for reading and editing workflow JSON without loading into LLM context.

Usage:
    python3 workflow_tools.py <command> <workflow.json> [args...]

Read Commands:
    summary <path>                              Compact overview of all steps, types, sizes, dependencies
    get-step <path> <step_name>                 Extract one step's full JSON
    get-steps <path> <name1> [name2 ...]        Extract multiple steps
    get-code <path> <step_name>                 Extract inline Python code from a function step
    get-config <path> <step_name>               Extract the config block from a step
    get-input-mappings <path> <step_name>        Extract input mappings for a step
    get-output-schema <path> <step_name>         Extract output schema for a step
    get-options <path>                           Extract the workflow options block
    get-prompts <path>                           List all prompts, instructions, and schemas with previews
    deps <path> <step_name>                     Show upstream and downstream dependencies for a step
    search <path> <regex_pattern>               Search step names, config values, code, and schemas

Write Commands:
    set-step <path> <step_name> [file]          Replace a step entirely (reads JSON from file or stdin)
    set-code <path> <step_name> [file]          Replace inline code in a function step (reads text from file or stdin)
    set-config <path> <step_name> [file]        Replace the config block (reads JSON from file or stdin)
    set-input-mappings <path> <step_name> [file] Replace input mappings (reads JSON from file or stdin)
    set-output-schema <path> <step_name> [file]  Replace output schema (reads JSON from file or stdin)
    set-prompt <path> <step_name> <key> [file]  Replace a specific config string field (reads text from file or stdin)
    set-options <path> [file]                   Replace the workflow options block (reads JSON from file or stdin)
    add-step <path> <index> [file]              Insert a new step at array position (reads JSON from file or stdin)
    remove-step <path> <step_name>              Remove a step (warns about broken downstream refs)
    rename-step <path> <old_name> <new_name>    Rename a step and update all input_mapping references

STATE.md Commands:
    log-change <path> "<entry>" [--date YYYY-MM-DD] [--note N] [--author A] [--status S] [--max N] [--dry-run]
                                                Prepend an entry to STATE.md "Recent Meaningful Changes",
                                                auto-roll overflow (beyond the rolling window) into
                                                archive/CHANGES.md by date, and bump frontmatter last-updated.
                                                <path> may be the workflow dir, the STATE.md, or workflow.json.
                                                The date prefix ("- YYYY-MM-DD: ") is added automatically; pass
                                                only the entry body (e.g. "**Title.** details"). --dry-run
                                                prints the plan without writing. Fails loudly if STATE.md's
                                                structure no longer matches the tool's assumptions.
"""

import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# ── Helpers ───────────────────────────────────────────────────

def _load(filepath: str) -> dict:
    """Load and parse a workflow JSON file."""
    try:
        with open(filepath) as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {filepath}: {e}", file=sys.stderr)
        sys.exit(1)


def _save(filepath: str, data: dict):
    """Write workflow data back to JSON file."""
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _find_step(steps: list, name: str) -> Tuple[int, dict]:
    """Find a step by exact name. Returns (index, step) or exits with error."""
    for i, step in enumerate(steps):
        if step.get("name") == name:
            return i, step
    available = [s.get("name", "<unnamed>") for s in steps]
    print(f"Error: Step '{name}' not found.", file=sys.stderr)
    print(f"Available steps:", file=sys.stderr)
    for s in available:
        print(f"  - {s}", file=sys.stderr)
    sys.exit(1)


def _read_input(file_arg: Optional[str] = None) -> str:
    """Read content from a file path argument, or from stdin if not provided."""
    if file_arg and file_arg != "-":
        with open(file_arg) as f:
            return f.read()
    return sys.stdin.read()


def _step_line_map(filepath: str, step_names: List[str]) -> Dict[str, int]:
    """Find the approximate line number where each step's name appears."""
    result = {}
    remaining = set(step_names)
    with open(filepath) as f:
        for line_num, line in enumerate(f, 1):
            if not remaining:
                break
            for name in list(remaining):
                if '"name"' in line and name in line:
                    pattern = r'"name"\s*:\s*"' + re.escape(name) + r'"'
                    if re.search(pattern, line):
                        result[name] = line_num
                        remaining.discard(name)
    return result


def _get_deps_for_step(step: dict) -> List[Tuple[str, Optional[str], str]]:
    """Extract (source_step, output_attribute, input_parameter) tuples from input_mappings."""
    deps = []
    for mapping in step.get("input_mappings", []):
        if mapping.get("input_type") != "dependency":
            continue
        value = mapping.get("value", {})
        if not isinstance(value, dict):
            continue
        for dep in value.get("dependency_step_outputs", []):
            step_name = dep.get("step_name", "")
            attr = dep.get("output_attribute")
            param = mapping.get("input_parameter_name", "")
            if step_name:
                deps.append((step_name, attr, param))
    return deps


def _step_size(step: dict) -> int:
    """Approximate line count when a step is serialized as formatted JSON."""
    return json.dumps(step, indent=2).count("\n") + 1


# ── Read Commands ─────────────────────────────────────────────

def cmd_summary(filepath: str):
    """Print a compact summary of the entire workflow."""
    data = _load(filepath)
    steps = data.get("steps", [])
    options = data.get("options", {})
    line_count = sum(1 for _ in open(filepath))

    step_names = [s.get("name", "?") for s in steps]
    line_map = _step_line_map(filepath, step_names)

    print(f"Workflow: {data.get('name', '(unnamed)')}")
    print(f"File: {filepath} ({line_count:,} lines, {len(steps)} steps)")
    print()

    # Column headers
    hdr = f"{'#':>3}  {'Name':<42} {'Type':<35} {'~Ln':>5}  Inputs From"
    print(hdr)
    print(f"{'─'*3}  {'─'*42} {'─'*35} {'─'*5}  {'─'*40}")

    for i, step in enumerate(steps):
        name = step.get("name", "?")
        stype = step.get("type", "?")
        size = _step_size(step)
        start_line = line_map.get(name, "?")

        dep_tuples = _get_deps_for_step(step)
        if dep_tuples:
            dep_str = ", ".join(sorted(set(d[0] for d in dep_tuples)))
        else:
            dep_str = "—"

        # Truncate display values
        d_name = (name[:40] + "..") if len(name) > 42 else name
        d_type = (stype[:33] + "..") if len(stype) > 35 else stype
        d_deps = (dep_str[:58] + "..") if len(dep_str) > 60 else dep_str

        print(f"{i+1:>3}. {d_name:<42} {d_type:<35} {size:>5}  {d_deps}")

    # Options summary
    print()
    opt_parts = []
    if "enable_parallel_execution" in options:
        opt_parts.append(f"parallel={options['enable_parallel_execution']}")
    if "reducto_version" in options:
        opt_parts.append(f"reducto={options['reducto_version']}")
    model = options.get("model")
    if model:
        opt_parts.append(f"model={model}")
    ff = options.get("feature_flags", {})
    if ff:
        opt_parts.append(f"feature_flags={json.dumps(ff)}")
    fac = options.get("file_access_config", {})
    if fac:
        opt_parts.append(f"file_access={fac.get('type', '?')}")
    if opt_parts:
        print(f"Options: {', '.join(opt_parts)}")
    else:
        print("Options: (none set)")


def cmd_get_step(filepath: str, step_name: str):
    """Print a single step's full JSON."""
    data = _load(filepath)
    _, step = _find_step(data["steps"], step_name)
    print(json.dumps(step, indent=2, ensure_ascii=False))


def cmd_get_steps(filepath: str, step_names: List[str]):
    """Print multiple steps' JSON, separated by headers."""
    data = _load(filepath)
    for name in step_names:
        _, step = _find_step(data["steps"], name)
        print(f"// ── Step: {name} ──")
        print(json.dumps(step, indent=2, ensure_ascii=False))
        print()


def cmd_get_code(filepath: str, step_name: str):
    """Print the inline Python code from a function step."""
    data = _load(filepath)
    _, step = _find_step(data["steps"], step_name)
    code = step.get("config", {}).get("code", "")
    if not code:
        print(
            f"Error: Step '{step_name}' has no inline code (type: {step.get('type', '?')})",
            file=sys.stderr,
        )
        sys.exit(1)
    print(code)


def cmd_get_config(filepath: str, step_name: str):
    """Print a step's config block."""
    data = _load(filepath)
    _, step = _find_step(data["steps"], step_name)
    config = step.get("config", {})
    print(json.dumps(config, indent=2, ensure_ascii=False))


def cmd_get_input_mappings(filepath: str, step_name: str):
    """Print a step's input_mappings array."""
    data = _load(filepath)
    _, step = _find_step(data["steps"], step_name)
    mappings = step.get("input_mappings", [])
    print(json.dumps(mappings, indent=2, ensure_ascii=False))


def cmd_get_output_schema(filepath: str, step_name: str):
    """Print a step's output schema (checks both step-level and config-level)."""
    data = _load(filepath)
    _, step = _find_step(data["steps"], step_name)
    schema = step.get("output_schema") or step.get("config", {}).get("output_schema")
    if not schema:
        print(f"Error: Step '{step_name}' has no output_schema", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(schema, indent=2, ensure_ascii=False))


def cmd_get_options(filepath: str):
    """Print the workflow options block."""
    data = _load(filepath)
    options = data.get("options", {})
    print(json.dumps(options, indent=2, ensure_ascii=False))


def cmd_get_prompts(filepath: str):
    """List all prompts, instructions, questions, and extraction schemas across the workflow."""
    data = _load(filepath)

    # Config keys that commonly contain prompt text
    PROMPT_KEYS = [
        "prompt",
        "instructions",
        "system_prompt",
        "extraction_instructions",
        "classification_instructions",
        "additional_instructions",
        "guideline_instructions",
        "question",
    ]

    EXTRACTION_TYPES = {
        "extract_from_document",
        "extract_from_multiple_sources",
        "extract_from_email",
        "extract_mixed_schema_from_multiple_sources",
        "extract_rows_from_multiple_sources",
        "agentic_extraction",
    }

    found_any = False
    for i, step in enumerate(data.get("steps", [])):
        name = step.get("name", "?")
        stype = step.get("type", "?")
        config = step.get("config", {})
        entries = []

        # Check common prompt fields in config
        for key in PROMPT_KEYS:
            val = config.get(key)
            if val and isinstance(val, str) and len(val.strip()) > 0:
                preview = val.strip().replace("\n", " \\n ")[:200]
                if len(val.strip()) > 200:
                    preview += "..."
                entries.append((key, preview, len(val)))

        # Check input_mappings of type "prompt"
        for mapping in step.get("input_mappings", []):
            if mapping.get("input_type") == "prompt":
                val = mapping.get("value", "")
                if isinstance(val, str) and val.strip():
                    preview = val.strip().replace("\n", " \\n ")[:200]
                    if len(val.strip()) > 200:
                        preview += "..."
                    param = mapping.get("input_parameter_name", "?")
                    entries.append((f"input_mapping[{param}]", preview, len(val)))

        # Check questions list (QA steps)
        questions = config.get("questions", [])
        if questions and isinstance(questions, list):
            q_count = len(questions)
            q_total = len(json.dumps(questions))
            entries.append(("questions", f"{q_count} questions ({q_total} chars total)", q_total))

        # Check output schema for extraction steps (field descriptions are prompts)
        schema = config.get("output_schema") or step.get("output_schema")
        if schema and isinstance(schema, dict) and stype in EXTRACTION_TYPES:
            props = schema.get("properties", {})
            field_count = len(props)
            schema_size = len(json.dumps(schema))
            entries.append(
                ("output_schema", f"{field_count} extraction fields ({schema_size} chars)", schema_size)
            )

        if entries:
            found_any = True
            print(f"\n{'='*70}")
            print(f"Step {i+1}: {name} ({stype})")
            print(f"{'='*70}")
            for key, preview, size in entries:
                print(f"  [{key}] ({size:,} chars)")
                # Show first few lines of preview
                lines = preview.split(" \\n ")[:3]
                for line in lines:
                    print(f"    {line.strip()[:120]}")
                if len(preview.split(" \\n ")) > 3:
                    print(f"    ...")

    if not found_any:
        print("No prompts or instructions found in this workflow.")


def cmd_deps(filepath: str, step_name: str):
    """Show upstream and downstream dependencies for a step."""
    data = _load(filepath)
    steps = data["steps"]
    idx, target = _find_step(steps, step_name)

    # Upstream
    upstream = _get_deps_for_step(target)

    # Downstream
    downstream = []
    for step in steps:
        for dep_name, attr, param in _get_deps_for_step(step):
            if dep_name == step_name:
                downstream.append((step.get("name", "?"), attr, param))

    print(f"Step: {step_name}")
    print(f"Type: {target.get('type', '?')}")
    print(f"Index: {idx} (1-based: {idx+1})")
    print(f"Size: ~{_step_size(target)} lines")
    print()

    print("Upstream (this step depends on):")
    if upstream:
        for dep_name, attr, param in upstream:
            attr_str = f".{attr}" if attr else " (whole output)"
            print(f"  ← {dep_name}{attr_str}  as  {param}")
    else:
        print("  (none — root step or static-only inputs)")

    print()
    print("Downstream (steps that depend on this):")
    if downstream:
        for dep_name, attr, param in downstream:
            print(f"  → {dep_name}  as  {param}")
    else:
        print("  (none — terminal step)")


def cmd_search(filepath: str, pattern: str):
    """Search across step names, config values, code, and schema fields."""
    data = _load(filepath)
    regex = re.compile(pattern, re.IGNORECASE)
    total_matches = 0

    for i, step in enumerate(data.get("steps", [])):
        name = step.get("name", "?")
        step_matches = []

        # Search step name
        if regex.search(name):
            step_matches.append(("name", name))

        # Search config string values
        config = step.get("config", {})
        for key, val in config.items():
            if isinstance(val, str) and regex.search(val):
                match_lines = []
                for ln, line in enumerate(val.split("\n"), 1):
                    if regex.search(line):
                        match_lines.append((ln, line.strip()[:120]))
                for ln, text in match_lines[:3]:
                    step_matches.append((f"config.{key}:{ln}", text))
                if len(match_lines) > 3:
                    step_matches.append((f"config.{key}", f"... +{len(match_lines)-3} more matches"))

        # Search input_mapping parameter names
        for mapping in step.get("input_mappings", []):
            param = mapping.get("input_parameter_name", "")
            if regex.search(param):
                step_matches.append(("input_parameter", param))

        # Search output schema field names and descriptions
        schema = step.get("output_schema") or config.get("output_schema")
        if schema and isinstance(schema, dict):
            for prop_name, prop_def in schema.get("properties", {}).items():
                if regex.search(prop_name):
                    step_matches.append(("schema_field", prop_name))
                desc = prop_def.get("description", "") if isinstance(prop_def, dict) else ""
                if desc and regex.search(desc):
                    step_matches.append((f"schema_desc[{prop_name}]", desc[:120]))

        if step_matches:
            total_matches += len(step_matches)
            print(f"\nStep {i+1}: {name} ({step.get('type', '?')})")
            for location, text in step_matches[:8]:
                print(f"  [{location}] {text}")
            if len(step_matches) > 8:
                print(f"  ... +{len(step_matches)-8} more matches")

    if total_matches == 0:
        print(f"No matches for pattern: {pattern}")
    else:
        print(f"\n{total_matches} total matches")


# ── Write Commands ────────────────────────────────────────────

def cmd_set_step(filepath: str, step_name: str, input_file: Optional[str] = None):
    """Replace a step entirely with new JSON."""
    data = _load(filepath)
    idx, _ = _find_step(data["steps"], step_name)

    new_step = json.loads(_read_input(input_file))
    data["steps"][idx] = new_step
    _save(filepath, data)

    new_name = new_step.get("name", "?")
    print(f"Replaced step '{step_name}' at index {idx}")
    if new_name != step_name:
        print(f"  Note: step name changed to '{new_name}' — references NOT auto-updated")
        print(f"  Run: rename-step to update downstream references if needed")


CODE_STEP_TYPES = ("function", "custom_step")


def cmd_set_code(filepath: str, step_name: str, input_file: Optional[str] = None):
    """Replace inline code in a function or custom_step step."""
    data = _load(filepath)
    idx, step = _find_step(data["steps"], step_name)

    if step.get("type") not in CODE_STEP_TYPES:
        print(
            f"Error: Step '{step_name}' is type '{step.get('type')}', "
            f"not one of {'/'.join(CODE_STEP_TYPES)}",
            file=sys.stderr,
        )
        sys.exit(1)

    new_code = _read_input(input_file)

    if "config" not in data["steps"][idx]:
        data["steps"][idx]["config"] = {}
    data["steps"][idx]["config"]["code"] = new_code
    _save(filepath, data)
    print(f"Updated code for '{step_name}' ({len(new_code):,} chars, ~{new_code.count(chr(10))+1} lines)")


def cmd_set_config(filepath: str, step_name: str, input_file: Optional[str] = None):
    """Replace a step's entire config block."""
    data = _load(filepath)
    idx, _ = _find_step(data["steps"], step_name)

    new_config = json.loads(_read_input(input_file))
    data["steps"][idx]["config"] = new_config
    _save(filepath, data)
    print(f"Updated config for '{step_name}'")


def cmd_set_input_mappings(filepath: str, step_name: str, input_file: Optional[str] = None):
    """Replace a step's input_mappings array."""
    data = _load(filepath)
    idx, _ = _find_step(data["steps"], step_name)

    new_mappings = json.loads(_read_input(input_file))
    if not isinstance(new_mappings, list):
        print("Error: input_mappings must be a JSON array", file=sys.stderr)
        sys.exit(1)
    data["steps"][idx]["input_mappings"] = new_mappings
    _save(filepath, data)
    print(f"Updated input_mappings for '{step_name}' ({len(new_mappings)} mappings)")


def cmd_set_output_schema(filepath: str, step_name: str, input_file: Optional[str] = None):
    """Replace a step's output schema."""
    data = _load(filepath)
    idx, step = _find_step(data["steps"], step_name)

    new_schema = json.loads(_read_input(input_file))

    # Write to wherever the schema currently lives; default to config for function steps
    if step.get("config", {}).get("output_schema") is not None:
        data["steps"][idx]["config"]["output_schema"] = new_schema
    elif step.get("output_schema") is not None:
        data["steps"][idx]["output_schema"] = new_schema
    elif step.get("type") in CODE_STEP_TYPES:
        if "config" not in data["steps"][idx]:
            data["steps"][idx]["config"] = {}
        data["steps"][idx]["config"]["output_schema"] = new_schema
    else:
        data["steps"][idx]["output_schema"] = new_schema

    _save(filepath, data)
    print(f"Updated output_schema for '{step_name}'")


def cmd_set_prompt(filepath: str, step_name: str, prompt_key: str, input_file: Optional[str] = None):
    """Replace a specific string field in a step's config (prompt, instructions, etc.)."""
    data = _load(filepath)
    idx, _ = _find_step(data["steps"], step_name)

    new_text = _read_input(input_file)

    if "config" not in data["steps"][idx]:
        data["steps"][idx]["config"] = {}
    data["steps"][idx]["config"][prompt_key] = new_text
    _save(filepath, data)
    print(f"Updated config.{prompt_key} for '{step_name}' ({len(new_text):,} chars)")


def cmd_set_options(filepath: str, input_file: Optional[str] = None):
    """Replace the workflow options block."""
    data = _load(filepath)

    new_options = json.loads(_read_input(input_file))
    data["options"] = new_options
    _save(filepath, data)
    print(f"Updated workflow options")


def cmd_add_step(filepath: str, index: str, input_file: Optional[str] = None):
    """Insert a new step at the given array position."""
    data = _load(filepath)
    idx = int(index)

    new_step = json.loads(_read_input(input_file))
    step_count = len(data["steps"])

    if idx < 0 or idx > step_count:
        print(f"Error: Index {idx} out of range (0-{step_count})", file=sys.stderr)
        sys.exit(1)

    data["steps"].insert(idx, new_step)
    _save(filepath, data)
    print(f"Inserted step '{new_step.get('name', '?')}' at index {idx} (workflow now has {step_count+1} steps)")


def cmd_remove_step(filepath: str, step_name: str):
    """Remove a step by name, warning about broken downstream references."""
    data = _load(filepath)
    idx, _ = _find_step(data["steps"], step_name)

    # Check for downstream dependencies
    downstream = []
    for step in data["steps"]:
        if step.get("name") == step_name:
            continue
        for dep_name, _, param in _get_deps_for_step(step):
            if dep_name == step_name:
                downstream.append((step.get("name", "?"), param))

    if downstream:
        print(f"Warning: These steps reference '{step_name}' and will break:", file=sys.stderr)
        for d_name, d_param in downstream:
            print(f"  - {d_name} (parameter: {d_param})", file=sys.stderr)

    data["steps"].pop(idx)
    _save(filepath, data)
    print(f"Removed step '{step_name}' (was index {idx})")
    if downstream:
        print(f"⚠  {len(downstream)} downstream reference(s) need updating")


def cmd_rename_step(filepath: str, old_name: str, new_name: str):
    """Rename a step and update all input_mapping references to it."""
    data = _load(filepath)
    idx, _ = _find_step(data["steps"], old_name)

    # Check new name doesn't collide
    for step in data["steps"]:
        if step.get("name") == new_name:
            print(f"Error: Step '{new_name}' already exists", file=sys.stderr)
            sys.exit(1)

    # Rename the step
    data["steps"][idx]["name"] = new_name

    # Update all references in input_mappings
    updated_refs = 0
    for step in data["steps"]:
        for mapping in step.get("input_mappings", []):
            if mapping.get("input_type") != "dependency":
                continue
            value = mapping.get("value", {})
            if not isinstance(value, dict):
                continue
            for dep in value.get("dependency_step_outputs", []):
                if dep.get("step_name") == old_name:
                    dep["step_name"] = new_name
                    updated_refs += 1

    _save(filepath, data)
    print(f"Renamed '{old_name}' → '{new_name}' (updated {updated_refs} reference(s))")


# ── Dispatch ──────────────────────────────────────────────────

# ── STATE.md change logging (log-change) ──────────────────────
# These functions encode the EXPECTED structure of STATE.md and archive/
# CHANGES.md. If either file's layout changes, _parse_state() raises
# StateStructureError so the failure is loud and this tool gets updated —
# rather than silently mangling the file.

class StateStructureError(Exception):
    """STATE.md no longer matches the structure log-change was written against."""


_LASTUPDATED_RE = re.compile(r'^last-updated:\s*.*$')
_STATUS_RE = re.compile(r'^status:\s*.*$')
_RECENT_HEADER_RE = re.compile(r'^##\s+Recent Meaningful Changes\b', re.I)
_RECENT_CAP_RE = re.compile(r'last\s+(\d+)', re.I)
_OLDER_MARKER_RE = re.compile(r'^- \(older entries\b')
_ENTRY_DATE_RE = re.compile(r'^- (\d{4}-\d{2}-\d{2}):\s*(.*)$')
_ISO_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_CHANGES_SECTION_RE = re.compile(r'^##\s+(\d{4}-\d{2}-\d{2})')


def _resolve_state_path(path_arg: str) -> Path:
    """Accept a workflow dir, a STATE.md path, or any file in the workflow dir
    (e.g. workflow.json) and return the STATE.md path."""
    p = Path(path_arg)
    if p.is_dir():
        return p / "STATE.md"
    if p.name == "STATE.md":
        return p
    return p.parent / "STATE.md"


def _parse_state(text: str) -> dict:
    """Split STATE.md into structural parts, validating the invariants
    log-change depends on. Raises StateStructureError on any deviation."""
    lines = text.split('\n')
    n = len(lines)

    # 1. Frontmatter fence + last-updated line.
    if not lines or lines[0].strip() != '---':
        raise StateStructureError("file does not start with a '---' frontmatter fence")
    fm_end = next((i for i in range(1, n) if lines[i].strip() == '---'), None)
    if fm_end is None:
        raise StateStructureError("frontmatter has no closing '---' fence")
    lastupd_idx = next((i for i in range(1, fm_end) if _LASTUPDATED_RE.match(lines[i])), None)
    if lastupd_idx is None:
        raise StateStructureError("frontmatter has no 'last-updated:' line")
    status_idx = next((i for i in range(1, fm_end) if _STATUS_RE.match(lines[i])), None)

    # 2. Exactly one "Recent Meaningful Changes" header.
    header_idxs = [i for i in range(n) if _RECENT_HEADER_RE.match(lines[i])]
    if len(header_idxs) != 1:
        raise StateStructureError(
            f"expected exactly one '## Recent Meaningful Changes' header, found {len(header_idxs)}"
        )
    header_idx = header_idxs[0]
    cap_m = _RECENT_CAP_RE.search(lines[header_idx])
    cap = int(cap_m.group(1)) if cap_m else None

    # 3. Rolling list. The section opens with two helper paragraphs before the
    #    bullets, so "first non-blank line after the header" is NOT the insert
    #    point -- anchoring there put every new entry above that prose. Anchor on
    #    the first real '- ' bullet (or the older-entries marker) inside the
    #    section instead, and when the list is empty fall in behind the prose.
    sect_end = n
    for i in range(header_idx + 1, n):
        if lines[i].startswith('## '):
            sect_end = i
            break

    j = None
    for i in range(header_idx + 1, sect_end):
        if lines[i].startswith('- ') or _OLDER_MARKER_RE.match(lines[i]):
            j = i
            break
    if j is None:
        # No entries yet: sit after the last non-blank line in the section
        # (keeping one blank line between the prose and the new bullet).
        last_text = header_idx
        for i in range(header_idx + 1, sect_end):
            if lines[i].strip():
                last_text = i
        j = last_text + 1
        if j < sect_end and lines[j].strip() == '':
            j += 1

    entries_start = j
    entries, marker = [], None
    k = j
    while k < n:
        ln = lines[k]
        if _OLDER_MARKER_RE.match(ln):
            marker = ln
            k += 1
            break
        if ln.startswith('- '):
            entries.append(ln)
            k += 1
            continue
        break  # blank or non-list line ends the rolling block
    return {
        "lines": lines, "lastupd_idx": lastupd_idx, "status_idx": status_idx,
        "entries_start": entries_start, "entries_end": k,
        "entries": entries, "marker": marker, "cap": cap,
    }


def _insert_change_bullet(clines: List[str], date: str, bullet: str) -> List[str]:
    """Insert `bullet` (a '- ...' line) under the '## <date>' section in CHANGES.md
    lines, creating the section in reverse-chronological position if absent."""
    # Existing section with this exact date → prepend bullet under it.
    for i, ln in enumerate(clines):
        m = _CHANGES_SECTION_RE.match(ln)
        if m and m.group(1) == date:
            ins = i + 1
            if ins < len(clines) and clines[ins].strip() == '':
                ins += 1
            clines.insert(ins, bullet)
            return clines
    # No section: create one before the first older-dated section (reverse-chron).
    section = [f"## {date}", "", bullet, ""]
    for i, ln in enumerate(clines):
        m = _CHANGES_SECTION_RE.match(ln)
        if m and m.group(1) < date:
            clines[i:i] = section
            return clines
    # No older section → append at end.
    while clines and clines[-1].strip() == '':
        clines.pop()
    clines += [""] + section[:-1]
    return clines


def _file_into_changes(changes_path: Path, rolled: List[Tuple[str, str]], workflow_dir: Path) -> int:
    """Append each (date, bullet) to archive/CHANGES.md. Returns new line count."""
    if not changes_path.exists():
        changes_path.parent.mkdir(parents=True, exist_ok=True)
        changes_path.write_text(
            f"# Change Log — {workflow_dir.as_posix()}\n\n"
            f"Archived entries from STATE.md \"Recent Meaningful Changes\" once the rolling "
            f"list exceeds its window. Maintained by `workflow_tools.py log-change`.\n"
        )
    clines = changes_path.read_text().split('\n')
    for date, bullet in rolled:
        clines = _insert_change_bullet(clines, date, bullet)
    out = '\n'.join(clines)
    if not out.endswith('\n'):
        out += '\n'
    changes_path.write_text(out)
    return len(clines)


def cmd_log_change(path_arg, entry, date=None, note=None, author="main agent",
                   status=None, max_entries=None, dry_run=False):
    state_path = _resolve_state_path(path_arg)
    if not state_path.exists():
        print(f"Error: {state_path} not found — create a STATE.md for this "
              f"workflow first (any change-log format works).", file=sys.stderr)
        sys.exit(1)
    if '\n' in entry:
        print("Error: <entry> must be a single line (no newlines).", file=sys.stderr)
        sys.exit(1)

    date = date or datetime.date.today().isoformat()
    if not _ISO_DATE_RE.match(date):
        print(f"Error: --date must be YYYY-MM-DD (got '{date}')", file=sys.stderr)
        sys.exit(1)

    text = state_path.read_text()
    try:
        st = _parse_state(text)
    except StateStructureError as e:
        print(f"STATE.md STRUCTURE ERROR: {e}.\n"
              f"  log-change's assumptions about STATE.md are stale. Update "
              f"_parse_state()/cmd_log_change() in workflow_tools.py to match the new structure.",
              file=sys.stderr)
        sys.exit(2)

    lines = list(st["lines"])
    cap = max_entries if max_entries is not None else (st["cap"] or 10)
    if cap < 1:
        print(f"Error: rolling window must be >= 1 (got {cap})", file=sys.stderr)
        sys.exit(1)

    new_entry = f"- {date}: {entry.strip()}"
    new_entries = [new_entry] + st["entries"]
    overflow = max(0, len(new_entries) - cap)
    kept = new_entries[:len(new_entries) - overflow]
    rolled = new_entries[len(new_entries) - overflow:]  # the oldest, newest-first order

    # Each rolled entry must carry a 'YYYY-MM-DD:' prefix so it can be filed by date.
    parsed_rolled = []
    for r in rolled:
        m = _ENTRY_DATE_RE.match(r)
        if not m:
            print(f"STATE.md STRUCTURE ERROR: an overflowing entry has no 'YYYY-MM-DD:' "
                  f"prefix and cannot be archived by date:\n    {r[:120]}\n"
                  f"  Fix the entry or update cmd_log_change() in workflow_tools.py.",
                  file=sys.stderr)
            sys.exit(2)
        parsed_rolled.append((m.group(1), f"- {m.group(2)}"))

    # Frontmatter updates.
    lines[st["lastupd_idx"]] = f"last-updated: {date} by {author}" + (f" ({note})" if note else "")
    if status is not None:
        if st["status_idx"] is None:
            print("Error: --status given but no 'status:' line in frontmatter.", file=sys.stderr)
            sys.exit(1)
        lines[st["status_idx"]] = f"status: {status}"

    # Rebuild the rolling block.
    block = list(kept)
    if st["marker"] is not None:
        block.append(st["marker"])
    elif overflow > 0:
        block.append("- (older entries in `archive/CHANGES.md`)")
    new_lines = lines[:st["entries_start"]] + block + lines[st["entries_end"]:]
    new_text = '\n'.join(new_lines)

    # Round-trip safety: never write a STATE.md we can no longer parse.
    try:
        _parse_state(new_text)
    except StateStructureError as e:
        print(f"ABORT: log-change would produce a malformed STATE.md ({e}); no files written. "
              f"This is a tool bug — fix cmd_log_change() in workflow_tools.py.", file=sys.stderr)
        sys.exit(3)

    changes_path = state_path.parent / "archive" / "CHANGES.md"
    if dry_run:
        print(f"[dry-run] {state_path}")
        print(f"  + {new_entry[:140]}")
        print(f"  last-updated -> {lines[st['lastupd_idx']]}")
        if status is not None:
            print(f"  status -> {status}")
        if parsed_rolled:
            print(f"  roll {len(parsed_rolled)} overflow entry(ies) to {changes_path}:")
            for d, b in parsed_rolled:
                print(f"    [{d}] {b[:110]}")
        else:
            print(f"  no overflow ({len(kept)}/{cap} in window)")
        return

    state_path.write_text(new_text)
    rolled_msg = ""
    if parsed_rolled:
        nlines = _file_into_changes(changes_path, parsed_rolled, state_path.parent)
        rolled_msg = f"; rolled {len(parsed_rolled)} to archive/CHANGES.md"
        if nlines > 200:
            print(f"WARNING: {changes_path} now has {nlines} lines (>200). Per protocol, "
                  f"notify the user before further overflow.", file=sys.stderr)
    print(f"Logged change to {state_path.name} ({len(kept)}/{cap} in rolling window{rolled_msg}).")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    # Dispatch table: command -> (func, min_args, max_args, description)
    # For commands with optional file arg, max_args = min_args + 1
    dispatch = {
        # Read commands
        "summary":            (cmd_summary, 1, 1),
        "get-step":           (cmd_get_step, 2, 2),
        "get-code":           (cmd_get_code, 2, 2),
        "get-config":         (cmd_get_config, 2, 2),
        "get-input-mappings": (cmd_get_input_mappings, 2, 2),
        "get-output-schema":  (cmd_get_output_schema, 2, 2),
        "get-options":        (cmd_get_options, 1, 1),
        "get-prompts":        (cmd_get_prompts, 1, 1),
        "deps":               (cmd_deps, 2, 2),
        "search":             (cmd_search, 2, 2),
        # Write commands
        "set-step":           (cmd_set_step, 2, 3),
        "set-code":           (cmd_set_code, 2, 3),
        "set-config":         (cmd_set_config, 2, 3),
        "set-input-mappings": (cmd_set_input_mappings, 2, 3),
        "set-output-schema":  (cmd_set_output_schema, 2, 3),
        "set-prompt":         (cmd_set_prompt, 3, 4),
        "set-options":        (cmd_set_options, 1, 2),
        "add-step":           (cmd_add_step, 2, 3),
        "remove-step":        (cmd_remove_step, 2, 2),
        "rename-step":        (cmd_rename_step, 3, 3),
    }

    # Special handling for get-steps (variable args)
    if cmd == "get-steps":
        if len(args) < 2:
            print("Usage: get-steps <path> <name1> [name2 ...]", file=sys.stderr)
            sys.exit(1)
        cmd_get_steps(args[0], args[1:])
        return

    # Special handling for log-change (named flags)
    if cmd == "log-change":
        import argparse
        p = argparse.ArgumentParser(
            prog="workflow_tools.py log-change",
            description="Prepend a Recent Meaningful Changes entry to STATE.md, roll overflow "
                        "into archive/CHANGES.md, and bump last-updated.")
        p.add_argument("path", help="workflow dir, STATE.md path, or workflow.json path")
        p.add_argument("entry", help="entry body (date prefix added automatically), e.g. '**Title.** details'")
        p.add_argument("--date", help="YYYY-MM-DD (default: today)")
        p.add_argument("--note", help="parenthetical note for the last-updated line")
        p.add_argument("--author", default="main agent", help="frontmatter author (default: 'main agent')")
        p.add_argument("--status", help="set frontmatter status: line (optional)")
        p.add_argument("--max", type=int, dest="max_entries",
                       help="rolling window size (default: parsed from header, else 10)")
        p.add_argument("--dry-run", action="store_true", help="print the plan without writing")
        ns = p.parse_args(args)
        cmd_log_change(ns.path, ns.entry, date=ns.date, note=ns.note, author=ns.author,
                       status=ns.status, max_entries=ns.max_entries, dry_run=ns.dry_run)
        return

    if cmd not in dispatch:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(f"Run with --help for usage.", file=sys.stderr)
        sys.exit(1)

    func, min_args, max_args = dispatch[cmd]

    if len(args) < min_args or len(args) > max_args:
        if min_args == max_args:
            expected = str(min_args)
        else:
            expected = f"{min_args}-{max_args}"
        print(f"Error: '{cmd}' expects {expected} argument(s), got {len(args)}", file=sys.stderr)
        sys.exit(1)

    func(*args)


if __name__ == "__main__":
    main()
