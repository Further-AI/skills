"""Publish a form copy only after a source-backed plan passes native verification."""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def native(*args: str) -> dict[str, Any]:
    result = subprocess.run(
        ["fai-form", *args], capture_output=True, text=True, check=False, timeout=180
    )
    if result.returncode:
        raise ValueError(f"Native {args[0]} failed: {result.stdout}\n{result.stderr}")
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise ValueError("Native command did not return an object")
    return payload


def planned_values(
    plan: dict[str, Any], state: dict[str, Any]
) -> dict[str, str | bool]:
    fields = {field["id"]: field for field in state["fields"]}
    if state.get("unsupported_reason"):
        raise ValueError(f"Unsupported template: {state['unsupported_reason']}")
    if (
        set(plan) != {"fields", "blanks"}
        or not isinstance(plan["fields"], list)
        or not isinstance(plan["blanks"], list)
    ):
        raise ValueError("Plan requires fields and blanks lists")
    values: dict[str, str | bool] = {}
    seen = set()
    for entry in [*plan["fields"], *plan["blanks"]]:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("id"), str)
            or entry["id"] not in fields
            or entry["id"] in seen
        ):
            raise ValueError("Every plan entry needs a unique inspected field id")
        seen.add(entry["id"])
    for entry in plan["fields"]:
        if (
            set(entry) != {"id", "value", "source"}
            or not isinstance(entry["source"], str)
            or not entry["source"].strip()
        ):
            raise ValueError("Every write requires a source reference")
        value = entry["value"]
        if not isinstance(value, (str, bool)) or (
            isinstance(value, str) and not value.strip()
        ):
            raise ValueError(
                "Writes require a nonempty text value or an explicit checkbox choice"
            )
        field = fields[entry["id"]]
        if (
            field["kind"] == "signature"
            or field.get("read_only")
            or field.get("calculated")
        ):
            raise ValueError(f"Field cannot be authored: {entry['id']}")
        values[entry["id"]] = value
    for entry in plan["blanks"]:
        if (
            set(entry) != {"id", "reason"}
            or not isinstance(entry["reason"], str)
            or not entry["reason"].strip()
        ):
            raise ValueError("Every requested blank requires a reason")
    if not values:
        raise ValueError(
            "No supported writes; explain the requested blanks without producing a filled copy"
        )
    return values


def require_outcomes(payload: dict[str, Any], values: dict[str, str | bool]) -> None:
    outcomes = payload.get("outcomes")
    if not isinstance(outcomes, list):
        raise ValueError("Native verification omitted outcomes")
    if any(
        not isinstance(outcome, dict) or not isinstance(outcome.get("id"), str)
        for outcome in outcomes
    ):
        raise ValueError("Native verification returned malformed outcomes")
    ids = [outcome["id"] for outcome in outcomes]
    if (
        len(ids) != len(set(ids))
        or set(ids) != set(values)
        or any(o.get("placed") is not True for o in outcomes)
    ):
        raise ValueError(
            f"Native verification did not place every planned answer: {json.dumps(outcomes)}"
        )


def fill(source: Path, plan_path: Path, output: Path) -> dict[str, Any]:
    if (
        source.suffix.lower() not in {".pdf", ".docx"}
        or output.suffix.lower() != source.suffix.lower()
    ):
        raise ValueError(
            "Source and destination must have the same supported PDF or DOCX format"
        )
    if source.resolve() == output.resolve() or output.exists():
        raise ValueError(
            "Use a new destination; the original and existing outputs are preserved"
        )
    state = native("inspect", str(source))
    plan = json.loads(plan_path.read_text())
    if not isinstance(plan, dict):
        raise ValueError("Plan must be an object")
    values = planned_values(plan, state)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="form-plan-", dir=output.parent
    ) as directory:
        work = Path(directory)
        state_file, values_file = work / "state.json", work / "values.json"
        state_file.write_text(json.dumps(state))
        values_file.write_text(json.dumps(values))
        candidate = work / ("candidate" + source.suffix.lower())
        result = native(
            "fill",
            str(source),
            f"@{values_file}",
            str(candidate),
            "--state",
            f"@{state_file}",
        )
        require_outcomes(result, values)
        verified = native("verify", str(candidate), f"@{state_file}", f"@{values_file}")
        require_outcomes(verified, values)
        # Exclusive creation also protects an output created during verification.
        output.hardlink_to(candidate)
    return {
        "verified": True,
        "path": str(output),
        "outcomes": verified["outcomes"],
        "blanks": plan["blanks"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("plan", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        result = fill(args.source, args.plan, args.output)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"verified": False, "error": str(exc)}))
        raise SystemExit(1) from exc
    print(json.dumps(result))


if __name__ == "__main__":
    main()
