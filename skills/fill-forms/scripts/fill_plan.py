"""Publish a form copy only after a source-backed plan passes native verification."""

import argparse
import json
import os
import shutil
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
            not {"id", "value", "source"} <= set(entry)
            or not set(entry) <= {"id", "value", "source", "assumption"}
            or not isinstance(entry["source"], str)
            or not entry["source"].strip()
        ):
            raise ValueError("Every write requires a source reference")
        if "assumption" in entry and (
            not isinstance(entry["assumption"], str)
            or not entry["assumption"].strip()
        ):
            raise ValueError("Every assumption requires a reason")
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
            not {"id", "reason"} <= set(entry)
            or not set(entry) <= {"id", "reason", "ask", "why", "replies"}
            or not isinstance(entry["reason"], str)
            or not entry["reason"].strip()
        ):
            raise ValueError("Every requested blank requires a reason")
        if "ask" in entry and (
            not isinstance(entry["ask"], str) or not entry["ask"].strip()
        ):
            raise ValueError("Every question requires nonempty ask text")
        if "why" in entry and (
            not isinstance(entry["why"], str) or not entry["why"].strip()
        ):
            raise ValueError("Every question reason must be nonempty text")
        if "replies" in entry and (
            not isinstance(entry["replies"], list)
            or len(entry["replies"]) > 3
            or any(
                not isinstance(reply, str) or not reply.strip()
                for reply in entry["replies"]
            )
        ):
            raise ValueError("Question replies require at most three nonempty options")
    if not values:
        raise ValueError(
            "No supported writes; explain the requested blanks without producing a filled copy"
        )
    return values


def field_label(field: dict[str, Any]) -> str:
    """Return the inspection's human label, with the stable ID as a fallback."""
    label = field.get("label")
    return label.strip() if isinstance(label, str) and label.strip() else field["id"]


def display_value(value: str | bool) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return value


def has_answer(field: dict[str, Any]) -> bool:
    """Recognize a pre-existing answer from the original inspection."""
    value = field.get("value")
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def question_entries(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry for entry in plan["blanks"] if isinstance(entry.get("ask"), str)]


def build_summary(
    *,
    form_name: str,
    output: Path,
    draft: int,
    state: dict[str, Any],
    plan: dict[str, Any],
    previous_plan: dict[str, Any] | None = None,
    answered: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the deterministic version 1 chat-card payload."""
    answered = answered or {}
    fields = {field["id"]: field for field in state["fields"]}
    planned = {entry["id"]: entry for entry in plan["fields"]}

    grouped_questions: dict[str, dict[str, Any]] = {}
    question_order: list[str] = []
    question_sources = question_entries(plan)
    if previous_plan is not None:
        question_sources.extend(
            entry
            for entry in question_entries(previous_plan)
            if entry["ask"] in answered
        )
    for entry in question_sources:
        ask = entry["ask"]
        if ask not in grouped_questions:
            grouped_questions[ask] = {
                "ask": ask,
                "why": entry.get("why", entry["reason"]),
                "replies": entry.get("replies", []),
                "field_ids": [],
            }
            question_order.append(ask)
        if entry["id"] not in grouped_questions[ask]["field_ids"]:
            grouped_questions[ask]["field_ids"].append(entry["id"])

    questions = []
    for ask in question_order[:5]:
        question = grouped_questions[ask]
        is_answered = ask in answered
        questions.append(
            {
                "ask": ask,
                "why": question["why"],
                "replies": question["replies"],
                "fields": [
                    field_label(fields[field_id])
                    for field_id in question["field_ids"]
                ],
                "status": "answered" if is_answered else "open",
                "answer": answered[ask] if is_answered else None,
            }
        )

    assumptions = [
        {
            "statement": (
                f"{field_label(fields[entry['id']])} is "
                f"{display_value(entry['value'])}"
            ),
            "reason": entry["assumption"],
        }
        for entry in plan["fields"]
        if "assumption" in entry
    ][:5]

    changes: list[dict[str, Any]] = []
    if draft >= 2 and previous_plan is not None:
        previous_fields = {entry["id"]: entry for entry in previous_plan["fields"]}
        changed = [
            entry
            for entry in plan["fields"]
            if entry["id"] not in previous_fields
            or entry["value"] != previous_fields[entry["id"]]["value"]
        ]

        answered_memberships: dict[str, list[str]] = {}
        for source_plan in (previous_plan, plan):
            for entry in question_entries(source_plan):
                ask = entry["ask"]
                if ask in answered:
                    memberships = answered_memberships.setdefault(entry["id"], [])
                    if ask not in memberships:
                        memberships.append(ask)

        grouped_changes: dict[str, list[dict[str, Any]]] = {}
        ungrouped_changes: list[dict[str, Any]] = []
        for entry in changed:
            memberships = answered_memberships.get(entry["id"], [])
            if len(memberships) == 1:
                grouped_changes.setdefault(memberships[0], []).append(entry)
            else:
                ungrouped_changes.append(entry)

        for ask, entries in grouped_changes.items():
            labels = [field_label(fields[entry["id"]]) for entry in entries]
            values = [display_value(entry["value"]) for entry in entries]
            changes.append(
                {
                    "label": labels[0],
                    "value": values[0] if len(set(values)) == 1 else answered[ask],
                    "fields": len(entries),
                }
            )
        changes.extend(
            {
                "label": field_label(fields[entry["id"]]),
                "value": display_value(entry["value"]),
                "fields": 1,
            }
            for entry in ungrouped_changes
        )

    filled_ids = {field_id for field_id, field in fields.items() if has_answer(field)}
    filled_ids.update(planned)
    return {
        "version": 1,
        "form": form_name,
        "document": output.name,
        "draft": draft,
        "filled": len(filled_ids),
        "total": len(fields),
        "questions": questions,
        "assumptions": assumptions,
        "changes": changes,
    }


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


def execute_fill(
    source: Path, plan_path: Path, output: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
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
        # Copy, don't hard-link: the sandbox workspace volume can reject os.link
        # with EIO. O_EXCL keeps the exclusive create that protects an output
        # appearing during verification, and the copy outlives the work dir.
        fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        try:
            with os.fdopen(fd, "wb") as destination, candidate.open("rb") as src_file:
                shutil.copyfileobj(src_file, destination)
        except BaseException:
            output.unlink(missing_ok=True)
            raise
    result = {
        "verified": True,
        "path": str(output),
        "outcomes": verified["outcomes"],
        "blanks": plan["blanks"],
    }
    return result, state, plan


def fill(source: Path, plan_path: Path, output: Path) -> dict[str, Any]:
    result, _, _ = execute_fill(source, plan_path, output)
    return result


def load_object(path: Path, description: str) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"{description} must be an object")
    return payload


def load_answers(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    payload = load_object(path, "Answered questions")
    if any(
        not isinstance(ask, str)
        or not ask.strip()
        or not isinstance(answer, str)
        or not answer.strip()
        for ask, answer in payload.items()
    ):
        raise ValueError("Answered questions require nonempty text keys and answers")
    return payload


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("plan", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--form-name")
    parser.add_argument("--draft", type=positive_int, default=1)
    parser.add_argument("--previous-plan", type=Path)
    parser.add_argument("--answered", type=Path)
    args = parser.parse_args()
    try:
        summary = None
        if args.form_name is None:
            result = fill(args.source, args.plan, args.output)
        else:
            if not args.form_name.strip():
                raise ValueError("Form name must be nonempty")
            previous_plan = (
                load_object(args.previous_plan, "Previous plan")
                if args.previous_plan is not None
                else None
            )
            answered = load_answers(args.answered)
            result, state, plan = execute_fill(args.source, args.plan, args.output)
            summary = build_summary(
                form_name=args.form_name,
                output=args.output,
                draft=args.draft,
                state=state,
                plan=plan,
                previous_plan=previous_plan,
                answered=answered,
            )
            summary_text = json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
            args.plan.with_name(f"{args.plan.stem}.summary.json").write_text(
                summary_text + "\n"
            )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"verified": False, "error": str(exc)}))
        raise SystemExit(1) from exc
    print(json.dumps(result))
    if summary is not None:
        print("```fai-form-summary")
        print(summary_text)
        print("```")


if __name__ == "__main__":
    main()
