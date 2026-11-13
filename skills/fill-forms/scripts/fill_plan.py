"""Publish a form copy only after a source-backed plan passes native verification."""

import argparse
import json
import os
import re
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
        if "label" in entry and (
            not isinstance(entry["label"], str) or not entry["label"].strip()
        ):
            raise ValueError("Every plan label requires nonempty text")
        seen.add(entry["id"])
    for entry in plan["fields"]:
        if (
            not {"id", "value", "source"} <= set(entry)
            or not set(entry) <= {"id", "value", "source", "assumption", "label"}
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
            or not set(entry)
            <= {
                "id",
                "reason",
                "ask",
                "why",
                "replies",
                "kind",
                "source",
                "label",
            }
            or not isinstance(entry["reason"], str)
            or not entry["reason"].strip()
        ):
            raise ValueError("Every requested blank requires a reason")
        if entry.get("kind") not in {None, "missing", "unclear", "only_you"}:
            raise ValueError("Question kind must be missing, unclear, or only_you")
        if "source" in entry and (
            not isinstance(entry["source"], str) or not entry["source"].strip()
        ):
            raise ValueError("Every blank source requires a nonempty reference")
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
            or len(entry["replies"]) > 4
            or any(
                not isinstance(reply, str) or not reply.strip()
                for reply in entry["replies"]
            )
        ):
            raise ValueError("Question replies require at most four nonempty options")
    if not values:
        raise ValueError(
            "No supported writes; explain the requested blanks without producing a filled copy"
        )
    return values


def humanize_label(label: str) -> str:
    """Turn an AcroForm-style label into short sentence case."""
    text = re.sub(r"_+", " ", label.strip())
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    suffix = re.search(r"(?:^|\s)([A-Z])$", text)
    repeat = ""
    if suffix:
        letter = suffix.group(1)
        text = text[: suffix.start()].rstrip()
        if letter != "A":
            repeat = f" ({ord(letter) - ord('A') + 1})"
    if not text:
        text = label.strip()
    words = [word if len(word) > 1 and word.isupper() else word.lower() for word in text.split(" ")]
    sentence = " ".join(words)
    return f"{sentence[:1].upper()}{sentence[1:]}{repeat}"


def field_label(field: dict[str, Any]) -> str:
    """Return a humanized inspection label, with the stable ID as a fallback."""
    label = field.get("label")
    raw = label.strip() if isinstance(label, str) and label.strip() else field["id"]
    return humanize_label(raw)


def entry_label(entry: dict[str, Any], field: dict[str, Any]) -> str:
    """Prefer the plan's purpose-written label over inspection metadata."""
    label = entry.get("label")
    if isinstance(label, str) and label.strip():
        return label.strip()
    return field_label(field)


def inspection_label_aliases(field: dict[str, Any]) -> set[str]:
    """Return legacy and humanized inspection labels for follow-up matching."""
    aliases = {field["id"], humanize_label(field["id"]), field_label(field)}
    label = field.get("label")
    if isinstance(label, str) and label.strip():
        aliases.add(label.strip())
    return aliases


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


MAX_QUESTIONS = 5
MAX_DONE = 6
QUESTION_KINDS = {"missing", "unclear", "only_you"}


def question_kind(entry: dict[str, Any]) -> str:
    """Return an explicit question kind or classify a plan blank."""
    explicit = entry.get("kind")
    if explicit in QUESTION_KINDS:
        return explicit
    reason = entry.get("reason", "")
    if isinstance(reason, str) and re.search(r"\b(?:conflict\w*|ambigu\w*)\b", reason, re.I):
        return "unclear"
    source = entry.get("source")
    if isinstance(entry.get("ask"), str) and not (
        isinstance(source, str) and source.strip()
    ):
        return "missing"
    return "only_you"


def recovered_question_entries(
    previous_summary: dict[str, Any],
    fields: dict[str, dict[str, Any]],
    answered: dict[str, str],
    plan: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Answered questions rebuilt from the previous turn's summary card.

    Used when the previous plan is gone (a recycled sandbox): the card's
    question text survives in the chat, and its field labels are mapped back to
    the current inspection's IDs by unique label match. A question whose labels
    match nothing is still returned, with no field ID.
    """
    by_label: dict[str, list[str]] = {}
    for field_id, field in fields.items():
        for label in inspection_label_aliases(field):
            by_label.setdefault(label, []).append(field_id)
    if plan is not None:
        for entry in [*plan["fields"], *plan["blanks"]]:
            label = entry.get("label")
            field_id = entry.get("id")
            if (
                isinstance(label, str)
                and label.strip()
                and isinstance(field_id, str)
                and field_id in fields
            ):
                by_label.setdefault(label.strip(), []).append(field_id)
    by_label = {
        label: list(dict.fromkeys(field_ids)) for label, field_ids in by_label.items()
    }
    entries: list[dict[str, Any]] = []
    for question in previous_summary["questions"]:
        ask = question["ask"]
        if ask not in answered:
            continue
        base = {
            "ask": ask,
            "why": question["why"],
            "replies": question["replies"],
            "kind": question.get("kind", "only_you"),
        }
        matched = [
            (label, by_label[label][0])
            for label in question["fields"]
            if len(by_label.get(label, [])) == 1
        ]
        entries.extend(
            {**base, "id": field_id, "label": label} for label, field_id in matched
        )
        if not matched:
            entries.append(base)
    return entries


def load_previous_summary(path: Path) -> dict[str, Any]:
    payload = load_object(path, "Previous summary")
    questions = payload.get("questions")
    if not isinstance(questions, list) or any(
        not isinstance(question, dict)
        or not isinstance(question.get("ask"), str)
        or not isinstance(question.get("why"), str)
        or not isinstance(question.get("replies"), list)
        or not isinstance(question.get("fields"), list)
        or question.get("kind", "only_you") not in QUESTION_KINDS
        or not all(isinstance(label, str) for label in question["fields"])
        for question in questions
    ):
        raise ValueError("Previous summary must be a fai-form-summary payload with questions")
    draft = payload.get("draft")
    if draft is not None and (
        not isinstance(draft, int) or isinstance(draft, bool) or draft < 1
    ):
        raise ValueError("Previous summary draft must be a positive integer")
    return payload


def question_entries(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry for entry in plan["blanks"] if isinstance(entry.get("ask"), str)]


def done_priority(entry: dict[str, Any], field: dict[str, Any]) -> int:
    """Rank the most useful source-backed answers for the compact card."""
    text = f"{entry_label(entry, field)} {field.get('id', '')}".lower().replace(
        "_", " "
    )
    if "named insured" in text or "insured name" in text:
        return 0
    if "fein" in text or "tax identifier" in text:
        return 3
    if any(
        phrase in text
        for phrase in (
            "policy period",
            "effective date",
            "expiration date",
            "policy effective",
            "policy expiration",
        )
    ):
        return 1
    if "line of business" in text or "lines of business" in text:
        return 2
    if any(
        phrase in text
        for phrase in (
            "location count",
            "locations count",
            "number of locations",
            "no. of locations",
        )
    ):
        return 4
    if "location" in text:
        return 4
    return 5


def line_of_business_option(label: str) -> str | None:
    match = re.match(r"^(?:policy )?lines? of business(?::|\s)+(.*)$", label, re.I)
    if not match:
        return None
    option = match.group(1).strip()
    option = re.sub(r"^commercial\s+(?=general liability\b)", "", option, flags=re.I)
    return f"{option[:1].upper()}{option[1:]}" if option else None


def done_rows(
    plan: dict[str, Any], fields: dict[str, dict[str, Any]]
) -> list[dict[str, str]]:
    """Return up to six important filled, source-backed answers."""
    candidates = [
        (index, entry, fields[entry["id"]])
        for index, entry in enumerate(plan["fields"])
        if entry["id"] in fields
        and isinstance(entry.get("source"), str)
        and entry["source"].strip()
        and (
            isinstance(entry.get("value"), bool)
            or has_answer({"value": entry.get("value")})
        )
    ]
    candidates.sort(key=lambda item: (done_priority(item[1], item[2]), item[0]))

    rows: list[tuple[int, dict[str, str]]] = []
    consumed: set[str] = set()

    effective = next(
        (
            item
            for item in candidates
            if "effective date" in entry_label(item[1], item[2]).lower()
            and not isinstance(item[1]["value"], bool)
        ),
        None,
    )
    expiration = next(
        (
            item
            for item in candidates
            if "expiration date" in entry_label(item[1], item[2]).lower()
            and not isinstance(item[1]["value"], bool)
        ),
        None,
    )
    if effective is not None and expiration is not None:
        consumed.update((effective[1]["id"], expiration[1]["id"]))
        rows.append(
            (
                min(effective[0], expiration[0]),
                {
                    "label": "Policy period",
                    "value": (
                        f"{display_value(effective[1]['value'])} – "
                        f"{display_value(expiration[1]['value'])}"
                    ),
                },
            )
        )

    business_options: list[str] = []
    business_indexes: list[int] = []
    for index, entry, field in candidates:
        if field.get("kind") != "checkbox":
            continue
        option = line_of_business_option(entry_label(entry, field))
        if option is not None:
            consumed.add(entry["id"])
            if entry["value"] is True:
                business_options.append(option)
                business_indexes.append(index)
    if business_options:
        rows.append(
            (
                min(business_indexes),
                {
                    "label": "Lines of business",
                    "value": ", ".join(dict.fromkeys(business_options)),
                },
            )
        )

    rows.extend(
        (
            index,
            {
                "label": entry_label(entry, field),
                "value": display_value(entry["value"]),
            },
        )
        for index, entry, field in candidates
        if entry["id"] not in consumed
    )
    rows.sort(
        key=lambda item: (
            0
            if "named insured" in item[1]["label"].lower()
            else 1
            if item[1]["label"] == "Policy period"
            else 2
            if item[1]["label"] == "Lines of business"
            else 3
            if "fein" in item[1]["label"].lower()
            or "tax identifier" in item[1]["label"].lower()
            else 4
            if "location" in item[1]["label"].lower()
            else 5,
            item[0],
        )
    )
    return [row for _, row in rows[:MAX_DONE]]


def changed_fields_from_state(
    plan: dict[str, Any],
    state: dict[str, Any],
    previous_state: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return planned writes whose values differ from a prior inspection."""
    prior_fields = previous_state.get("fields")
    if not isinstance(prior_fields, list) or any(
        not isinstance(field, dict) for field in prior_fields
    ):
        raise ValueError("Previous state requires an inspected fields list")

    previous_by_id = {
        field["id"]: field
        for field in prior_fields
        if isinstance(field.get("id"), str)
    }
    previous_by_label: dict[str, list[dict[str, Any]]] = {}
    for field in prior_fields:
        label = field.get("label")
        if isinstance(label, str) and label.strip():
            previous_by_label.setdefault(label.strip(), []).append(field)

    current_fields = {field["id"]: field for field in state["fields"]}
    changed = []
    for entry in plan["fields"]:
        current_field = current_fields[entry["id"]]
        previous_field = previous_by_id.get(entry["id"])
        label = current_field.get("label")
        if previous_field is None and isinstance(label, str) and label.strip():
            label_matches = previous_by_label.get(label.strip(), [])
            if len(label_matches) == 1:
                previous_field = label_matches[0]
        if previous_field is None or previous_field.get("value") != entry["value"]:
            changed.append(entry)
    return changed


def build_summary(
    *,
    form_name: str,
    output: Path,
    draft: int,
    state: dict[str, Any],
    plan: dict[str, Any],
    previous_plan: dict[str, Any] | None = None,
    previous_state: dict[str, Any] | None = None,
    previous_summary: dict[str, Any] | None = None,
    answered: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the deterministic version 1 chat-card payload."""
    if previous_plan is not None and previous_state is not None:
        raise ValueError("Use either a previous plan or previous state, not both")
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
    recovered = (
        recovered_question_entries(previous_summary, fields, answered, plan)
        if previous_summary is not None
        else []
    )
    for entry in [*question_sources, *recovered]:
        ask = entry["ask"]
        if ask not in grouped_questions:
            grouped_questions[ask] = {
                "ask": ask,
                "kind": question_kind(entry),
                "why": entry.get("why", entry.get("reason", "")),
                "replies": entry.get("replies", []),
                "field_ids": [],
                "field_labels": [],
            }
            question_order.append(ask)
        elif question_kind(entry) == "unclear":
            # If one grouped field is ambiguous, the combined question is too.
            grouped_questions[ask]["kind"] = "unclear"
        field_id = entry.get("id")
        if field_id is not None and field_id not in grouped_questions[ask]["field_ids"]:
            grouped_questions[ask]["field_ids"].append(field_id)
            grouped_questions[ask]["field_labels"].append(
                entry_label(entry, fields[field_id])
            )

    # Answered and open questions are capped separately, answered first, so a
    # follow-up's confirmed answers are never crowded out by open questions.
    answered_order = [ask for ask in question_order if ask in answered][:MAX_QUESTIONS]
    open_order = [ask for ask in question_order if ask not in answered][:MAX_QUESTIONS]
    questions = []
    for ask in [*answered_order, *open_order]:
        question = grouped_questions[ask]
        is_answered = ask in answered
        questions.append(
            {
                "ask": ask,
                "kind": question["kind"],
                "why": question["why"],
                "replies": question["replies"],
                "fields": question["field_labels"],
                "status": "answered" if is_answered else "open",
                "answer": answered[ask] if is_answered else None,
            }
        )

    assumptions = [
        {
            "statement": (
                f"{entry_label(entry, fields[entry['id']])} is "
                f"{display_value(entry['value'])}"
            ),
            "reason": entry["assumption"],
        }
        for entry in plan["fields"]
        if "assumption" in entry
    ][:5]

    changes: list[dict[str, Any]] = []
    if draft >= 2 and (previous_plan is not None or previous_state is not None):
        if previous_plan is not None:
            previous_fields = {
                entry["id"]: entry for entry in previous_plan["fields"]
            }
            changed = [
                entry
                for entry in plan["fields"]
                if entry["id"] not in previous_fields
                or entry["value"] != previous_fields[entry["id"]]["value"]
            ]
        else:
            assert previous_state is not None
            changed = changed_fields_from_state(plan, state, previous_state)

        answered_memberships: dict[str, list[str]] = {}
        source_plans = [plan]
        if previous_plan is not None:
            source_plans.insert(0, previous_plan)
        membership_sources = [
            entry for source_plan in source_plans for entry in question_entries(source_plan)
        ]
        membership_sources.extend(entry for entry in recovered if "id" in entry)
        for entry in membership_sources:
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
            labels = [entry_label(entry, fields[entry["id"]]) for entry in entries]
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
                "label": entry_label(entry, fields[entry["id"]]),
                "value": display_value(entry["value"]),
                "fields": 1,
            }
            for entry in ungrouped_changes
        )

    # A field counts as filled by its final value: the planned write when there
    # is one, otherwise what the form already held. A planned unchecked box is
    # therefore not filled, the same as an untouched unchecked box.
    filled = sum(
        has_answer({"value": planned[field_id]["value"]} if field_id in planned else field)
        for field_id, field in fields.items()
    )
    return {
        "version": 1,
        "form": form_name,
        "document": output.name,
        "draft": draft,
        "filled": filled,
        "total": len(fields),
        "done": done_rows(plan, fields),
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
    if source.name.casefold() == output.name.casefold():
        raise ValueError("Destination filename must differ from the template filename")
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


def inferred_form_name(source: Path, state: dict[str, Any]) -> str:
    """Infer a readable form name from inspection metadata or the template name."""
    containers = [state]
    for key in ("metadata", "document"):
        nested = state.get(key)
        if isinstance(nested, dict):
            containers.append(nested)
    for container in containers:
        for key in ("form_name", "title", "document_title"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    readable_stem = re.sub(r"[_-]+", " ", source.stem)
    readable_stem = re.sub(r"\s+", " ", readable_stem).strip()
    return readable_stem or source.stem


def summary_draft(
    explicit: int | None,
    *,
    previous_plan: dict[str, Any] | None,
    previous_state: dict[str, Any] | None,
    previous_summary: dict[str, Any] | None,
) -> int:
    """Use an explicit draft, otherwise advance a supplied previous draft."""
    if explicit is not None:
        return explicit
    if previous_summary is not None and isinstance(previous_summary.get("draft"), int):
        return previous_summary["draft"] + 1
    if any(item is not None for item in (previous_plan, previous_state, previous_summary)):
        return 2
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("plan", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--form-name")
    parser.add_argument("--draft", type=positive_int)
    previous = parser.add_mutually_exclusive_group()
    previous.add_argument("--previous-plan", type=Path)
    previous.add_argument("--previous-state", type=Path)
    parser.add_argument("--previous-summary", type=Path)
    parser.add_argument("--answered", type=Path)
    args = parser.parse_args()
    try:
        summary = None
        summary_enabled = (
            args.form_name is not None
            or os.environ.get("FAI_FORM_SUMMARY_CARDS") == "1"
        )
        if not summary_enabled:
            result = fill(args.source, args.plan, args.output)
        else:
            if args.form_name is not None and not args.form_name.strip():
                raise ValueError("Form name must be nonempty")
            previous_plan = (
                load_object(args.previous_plan, "Previous plan")
                if args.previous_plan is not None
                else None
            )
            previous_state = (
                load_object(args.previous_state, "Previous state")
                if args.previous_state is not None
                else None
            )
            previous_summary = (
                load_previous_summary(args.previous_summary)
                if args.previous_summary is not None
                else None
            )
            answered = load_answers(args.answered)
            result, state, plan = execute_fill(args.source, args.plan, args.output)
            form_name = (
                args.form_name.strip()
                if args.form_name is not None
                else inferred_form_name(args.source, state)
            )
            draft = summary_draft(
                args.draft,
                previous_plan=previous_plan,
                previous_state=previous_state,
                previous_summary=previous_summary,
            )
            # execute_fill only publishes to a destination that did not exist, so
            # removing it on a card failure undoes exactly this run and leaves
            # the same path free for the retry.
            try:
                summary = build_summary(
                    form_name=form_name,
                    output=args.output,
                    draft=draft,
                    state=state,
                    plan=plan,
                    previous_plan=previous_plan,
                    previous_state=previous_state,
                    previous_summary=previous_summary,
                    answered=answered,
                )
                summary_text = json.dumps(
                    summary, ensure_ascii=False, separators=(",", ":")
                )
                args.plan.with_name(f"{args.plan.stem}.summary.json").write_text(
                    summary_text + "\n"
                )
            except Exception as exc:
                args.output.unlink(missing_ok=True)
                if isinstance(exc, (OSError, ValueError)):
                    raise
                raise ValueError(f"Could not build the form summary: {exc!r}") from exc
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
