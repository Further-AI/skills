"""Plan validation and publication gates around the native form engine."""

import importlib.util
import json
import re
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

SCRIPT = Path(__file__).parents[1] / "skills/fill-forms/scripts/fill_plan.py"
spec = importlib.util.spec_from_file_location("fill_plan", SCRIPT)
assert spec is not None and spec.loader is not None
fill_plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fill_plan)


@pytest.fixture
def state():
    return {
        "fields": [
            {"id": "insured", "kind": "text"},
            {"id": "symbol48", "kind": "checkbox"},
            {"id": "signature", "kind": "signature"},
            {"id": "fax", "kind": "text"},
        ]
    }


@pytest.fixture
def plan():
    return {
        "fields": [
            {
                "id": "insured",
                "value": "Example LLC",
                "source": "brief.txt: named insured",
            }
        ],
        "blanks": [{"id": "fax", "reason": "Not supplied"}],
    }


def test_scope_only_contains_source_backed_writes(state, plan):
    assert fill_plan.planned_values(plan, state) == {"insured": "Example LLC"}
    plan["fields"].append({"id": "symbol48", "value": True, "source": ""})
    with pytest.raises(ValueError, match="source reference"):
        fill_plan.planned_values(plan, state)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Policy EffectiveDate", "Policy effective date"),
        ("NamedInsured TaxIdentifier", "Named insured tax identifier"),
        (
            "CommercialStructure InsuredInterest OwnerIndicator A",
            "Commercial structure insured interest owner indicator",
        ),
        (
            "CommercialStructure InsuredInterest OwnerIndicator B",
            "Commercial structure insured interest owner indicator (2)",
        ),
        ("OwnerIndicator_C", "Owner indicator (3)"),
    ],
)
def test_humanize_inspection_labels(raw, expected):
    assert fill_plan.humanize_label(raw) == expected


def test_plan_labels_are_validated_and_optional(state, plan):
    plan["fields"][0]["label"] = "Named insured"
    plan["blanks"][0]["label"] = "Fax number"
    assert fill_plan.planned_values(plan, state) == {"insured": "Example LLC"}

    plan["blanks"][0]["label"] = " "
    with pytest.raises(ValueError, match="plan label"):
        fill_plan.planned_values(plan, state)


@pytest.mark.parametrize(
    "mutation",
    ["unknown", "duplicate", "blank_reason", "signature", "readonly", "empty"],
)
def test_invalid_plan_cannot_reach_fill(state, plan, mutation):
    if mutation == "unknown":
        plan["fields"][0]["id"] = "not-inspected"
    elif mutation == "duplicate":
        plan["blanks"][0]["id"] = "insured"
    elif mutation == "blank_reason":
        plan["blanks"][0]["reason"] = ""
    elif mutation == "signature":
        plan["fields"][0]["id"] = "signature"
    elif mutation == "readonly":
        state["fields"][0]["read_only"] = True
    else:
        plan["fields"] = []
    with pytest.raises(ValueError):
        fill_plan.planned_values(plan, state)


@pytest.mark.parametrize("failure", [None, "fill", "verify", "missing", "duplicate"])
def test_only_complete_independent_verification_publishes(
    tmp_path, monkeypatch, state, plan, failure
):
    original = tmp_path / "original.pdf"
    original.write_bytes(b"original")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    output = tmp_path / "output.pdf"
    calls = []

    def native(command, *args):
        calls.append(command)
        if command == "inspect":
            return state
        if command == "fill":
            values = json.loads(Path(args[1][1:]).read_text())
            assert values == {
                "insured": "Example LLC"
            }  # unrelated checkbox is never submitted
            Path(args[2]).write_bytes(b"filled")
        if command == failure:
            return {
                "outcomes": [
                    {"id": "insured", "placed": False, "reason": "readback_mismatch"}
                ]
            }
        if command == "verify" and failure == "missing":
            return {"outcomes": []}
        count = 2 if command == "verify" and failure == "duplicate" else 1
        return {"outcomes": [{"id": "insured", "placed": True}] * count}

    monkeypatch.setattr(fill_plan, "native", native)
    if failure:
        with pytest.raises(ValueError):
            fill_plan.fill(original, plan_path, output)
        assert not output.exists()
    else:
        result = fill_plan.fill(original, plan_path, output)
        assert result["verified"] and result["blanks"] == plan["blanks"]
        assert output.read_bytes() == b"filled"
    assert original.read_bytes() == b"original"
    assert calls == (
        ["inspect", "fill"] if failure == "fill" else ["inspect", "fill", "verify"]
    )
    assert not list(tmp_path.glob("form-plan-*"))


def test_existing_destination_is_preserved(tmp_path, monkeypatch):
    original = tmp_path / "original.pdf"
    output = tmp_path / "output.pdf"
    output.write_bytes(b"prior result")
    monkeypatch.setattr(
        fill_plan, "native", lambda *args: pytest.fail("must fail before inspection")
    )
    with pytest.raises(ValueError, match="new destination"):
        fill_plan.fill(original, tmp_path / "plan.json", output)
    assert output.read_bytes() == b"prior result"


def test_destination_filename_must_differ_from_template(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    delivery_dir = tmp_path / "delivery"
    source_dir.mkdir()
    delivery_dir.mkdir()
    original = source_dir / "ACORD_125.pdf"
    original.write_bytes(b"original")
    output = delivery_dir / "ACORD_125.pdf"
    monkeypatch.setattr(
        fill_plan, "native", lambda *args: pytest.fail("must fail before inspection")
    )

    with pytest.raises(ValueError, match="filename must differ"):
        fill_plan.fill(original, tmp_path / "plan.json", output)

    assert original.read_bytes() == b"original"
    assert not output.exists()


@pytest.mark.parametrize("stdout,returncode", [("{}", 1), ("not json", 0), ("[]", 0)])
def test_native_command_errors_cannot_be_treated_as_success(
    monkeypatch, stdout, returncode
):
    monkeypatch.setattr(
        fill_plan.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args, returncode, stdout, "error"),
    )
    with pytest.raises(ValueError):
        fill_plan.native("verify", "copy.pdf")


def test_output_created_during_verification_is_preserved(
    tmp_path, monkeypatch, state, plan
):
    original = tmp_path / "original.pdf"
    original.write_bytes(b"original")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    output = tmp_path / "output.pdf"

    def native(command, *args):
        if command == "inspect":
            return state
        if command == "fill":
            Path(args[2]).write_bytes(b"filled")
        if command == "verify":
            output.write_bytes(b"concurrent result")
        return {"outcomes": [{"id": "insured", "placed": True}]}

    monkeypatch.setattr(fill_plan, "native", native)
    with pytest.raises(FileExistsError):
        fill_plan.fill(original, plan_path, output)
    assert output.read_bytes() == b"concurrent result"
    assert not list(tmp_path.glob("form-plan-*"))


@pytest.mark.parametrize("copy_fails", [False, True])
def test_publication_works_without_links_and_cleans_up_failed_copies(
    tmp_path, monkeypatch, state, plan, copy_fails
):
    original = tmp_path / "original.pdf"
    original.write_bytes(b"original")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    output = tmp_path / "output.pdf"

    def native(command, *args):
        if command == "inspect":
            return state
        if command == "fill":
            Path(args[2]).write_bytes(b"verified form")
        return {"outcomes": [{"id": "insured", "placed": True}]}

    def unsupported_link(*args, **kwargs):
        raise OSError("hard links unavailable on workspace")

    def partial_copy(source, destination):
        destination.write(b"partial")
        raise OSError("copy interrupted")

    monkeypatch.setattr(fill_plan, "native", native)
    monkeypatch.setattr(fill_plan.os, "link", unsupported_link)
    if copy_fails:
        monkeypatch.setattr(fill_plan.shutil, "copyfileobj", partial_copy)
        with pytest.raises(OSError, match="copy interrupted"):
            fill_plan.fill(original, plan_path, output)
        assert not output.exists()
    else:
        assert fill_plan.fill(original, plan_path, output)["verified"]
        assert output.read_bytes() == b"verified form"
    assert original.read_bytes() == b"original"
    assert not list(tmp_path.glob("form-plan-*"))


def test_skill_names_no_removed_pdf_renderer():
    skill = SCRIPT.parents[1]
    for path in sorted(skill.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            text = path.read_text()
            assert not re.search(r"pymupdf|\bfitz\b", text, re.IGNORECASE), path


def test_summary_groups_questions_and_lists_assumptions(tmp_path, monkeypatch, capsys):
    state = {
        "fields": [
            {"id": "insured", "label": "Named insured", "kind": "text", "value": ""},
            {"id": "policy", "label": "Policy number", "kind": "text", "value": "P-1"},
            {"id": "mail1", "label": "Mailing address", "kind": "text", "value": ""},
            {"id": "mail2", "label": "Mailing city", "kind": "text", "value": ""},
            {"id": "signature", "label": "Signature", "kind": "signature", "value": ""},
        ]
    }
    plan = {
        "fields": [
            {
                "id": "insured",
                "value": "Example LLC",
                "source": "older application: named insured",
                "assumption": "taken from last year's application",
            }
        ],
        "blanks": [
            {
                "id": "mail1",
                "reason": "Only the premises is listed",
                "ask": "Is the mailing address the same as the premises address?",
                "why": "The documents only list the premises.",
                "replies": ["Yes, same", "No, different"],
            },
            {
                "id": "mail2",
                "reason": "Only the premises is listed",
                "ask": "Is the mailing address the same as the premises address?",
                "why": "The documents only list the premises.",
                "replies": ["Yes, same", "No, different"],
            },
            {"id": "signature", "reason": "Requires the applicant's signature"},
        ],
    }
    work = tmp_path / "work"
    delivery = tmp_path / "delivery"
    work.mkdir()
    delivery.mkdir()
    original = work / "original.pdf"
    original.write_bytes(b"original")
    plan_path = work / "plan.json"
    plan_path.write_text(json.dumps(plan))
    output = delivery / "ACORD-125-draft.pdf"

    def native(command, *args):
        if command == "inspect":
            return state
        if command == "fill":
            Path(args[2]).write_bytes(b"filled")
        return {"outcomes": [{"id": "insured", "placed": True}]}

    monkeypatch.setattr(fill_plan, "native", native)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fill_plan.py",
            str(original),
            str(plan_path),
            str(output),
            "--form-name",
            "ACORD 125",
        ],
    )
    fill_plan.main()

    stdout = capsys.readouterr().out
    lines = stdout.splitlines()
    assert json.loads(lines[0])["verified"] is True
    assert lines[1] == "```fai-form-summary"
    summary = json.loads(lines[2])
    assert lines[3] == "```"
    assert summary == {
        "version": 1,
        "form": "ACORD 125",
        "document": "ACORD-125-draft.pdf",
        "draft": 1,
        "filled": 2,
        "total": 5,
        "done": [{"label": "Named insured", "value": "Example LLC"}],
        "questions": [
            {
                "ask": "Is the mailing address the same as the premises address?",
                "kind": "missing",
                "why": "The documents only list the premises.",
                "replies": ["Yes, same", "No, different"],
                "fields": ["Mailing address", "Mailing city"],
                "field_ids": ["mail1", "mail2"],
                "status": "open",
                "answer": None,
            }
        ],
        "assumptions": [
            {
                "statement": "Named insured is Example LLC",
                "reason": "taken from last year's application",
            }
        ],
        "changes": [],
    }
    assert json.loads((work / "plan.summary.json").read_text()) == summary
    assert list(delivery.iterdir()) == [output]


def test_summary_env_enables_block_with_inferred_defaults(
    tmp_path, monkeypatch, capsys
):
    state = {
        "fields": [
            {"id": "insured", "label": "Named insured", "kind": "text", "value": ""}
        ]
    }
    plan = {
        "fields": [
            {"id": "insured", "value": "Example LLC", "source": "brief.txt"}
        ],
        "blanks": [],
    }
    original = tmp_path / "ACORD_125.pdf"
    original.write_bytes(b"original")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    output = tmp_path / "ACORD 125 - Example LLC (filled).pdf"

    def native(command, *args):
        if command == "inspect":
            return state
        if command == "fill":
            Path(args[2]).write_bytes(b"filled")
        return {"outcomes": [{"id": "insured", "placed": True}]}

    monkeypatch.setattr(fill_plan, "native", native)
    monkeypatch.setenv("FAI_FORM_SUMMARY_CARDS", "1")
    monkeypatch.setattr(
        sys,
        "argv",
        ["fill_plan.py", str(original), str(plan_path), str(output)],
    )

    fill_plan.main()

    lines = capsys.readouterr().out.splitlines()
    assert lines[1] == "```fai-form-summary"
    summary = json.loads(lines[2])
    assert (summary["form"], summary["draft"]) == ("ACORD 125", 1)
    assert summary["done"] == [
        {"label": "Named insured", "value": "Example LLC"}
    ]


def test_summary_defaults_advance_previous_draft():
    assert fill_plan.summary_draft(
        None,
        previous_plan={"fields": [], "blanks": []},
        previous_state=None,
        previous_summary=None,
    ) == 2
    assert fill_plan.summary_draft(
        None,
        previous_plan=None,
        previous_state={},
        previous_summary={"draft": 4},
    ) == 5
    assert fill_plan.summary_draft(
        9,
        previous_plan={},
        previous_state=None,
        previous_summary={"draft": 4},
    ) == 9


def test_inferred_form_name_prefers_inspection_title():
    assert (
        fill_plan.inferred_form_name(
            Path("uploaded_template.pdf"), {"title": "ACORD 125"}
        )
        == "ACORD 125"
    )


def test_follow_up_summary_marks_answers_and_groups_changes():
    state = {
        "fields": [
            {"id": "mail1", "label": "Mailing address", "kind": "text"},
            {"id": "mail2", "label": "Mailing city", "kind": "text"},
            {"id": "payroll", "label": "Total payroll", "kind": "text"},
        ]
    }
    ask = "Is the mailing address the same as the premises address?"
    previous = {
        "fields": [
            {"id": "payroll", "value": "$4,000,000", "source": "prior SOV"}
        ],
        "blanks": [
            {
                "id": field_id,
                "reason": "Mailing address not supplied",
                "ask": ask,
                "why": "The documents only list the premises.",
                "replies": ["Yes, same", "No, different"],
            }
            for field_id in ("mail1", "mail2")
        ],
    }
    current = {
        "fields": [
            {"id": "payroll", "value": "$4,820,000", "source": "updated SOV"},
            {"id": "mail1", "value": "1200 W Fulton St", "source": "user message"},
            {"id": "mail2", "value": "Chicago", "source": "user message"},
        ],
        "blanks": [],
    }

    summary = fill_plan.build_summary(
        form_name="ACORD 125",
        output=Path("ACORD-125.pdf"),
        draft=2,
        state=state,
        plan=current,
        previous_plan=previous,
        answered={ask: "Yes, same"},
    )

    assert summary["questions"] == [
        {
            "ask": ask,
            "kind": "missing",
            "why": "The documents only list the premises.",
            "replies": ["Yes, same", "No, different"],
            "fields": ["Mailing address", "Mailing city"],
            "field_ids": ["mail1", "mail2"],
            "status": "answered",
            "answer": "Yes, same",
        }
    ]
    assert summary["changes"] == [
        {"label": "Mailing address", "value": "Yes, same", "fields": 2},
        {"label": "Total payroll", "value": "$4,820,000", "fields": 1},
    ]


def test_follow_up_summary_compares_previous_state_by_id_then_label():
    state = {
        "fields": [
            {"id": "new-mail", "label": "Mailing address", "kind": "text"},
            {"id": "payroll", "label": "Total payroll", "kind": "text"},
            {"id": "contact", "label": "Contact", "kind": "text"},
        ]
    }
    plan = {
        "fields": [
            {"id": "new-mail", "value": "1200 W Fulton St", "source": "user"},
            {"id": "payroll", "value": "$4,820,000", "source": "SOV"},
            {"id": "contact", "value": "A. Smith", "source": "user"},
        ],
        "blanks": [],
    }
    previous_state = {
        "fields": [
            {
                "id": "old-mail",
                "label": "Mailing address",
                "value": "1200 W Fulton St",
            },
            {"id": "payroll", "label": "Prior payroll", "value": "$4,000,000"},
        ]
    }

    summary = fill_plan.build_summary(
        form_name="ACORD 125",
        output=Path("ACORD-125.pdf"),
        draft=2,
        state=state,
        plan=plan,
        previous_state=previous_state,
    )

    assert summary["changes"] == [
        {"label": "Total payroll", "value": "$4,820,000", "fields": 1},
        {"label": "Contact", "value": "A. Smith", "fields": 1},
    ]


def test_previous_state_cli_builds_changes(tmp_path, monkeypatch, capsys):
    state = {
        "fields": [
            {"id": "insured", "label": "Named insured", "kind": "text", "value": ""}
        ]
    }
    plan = {
        "fields": [
            {"id": "insured", "value": "Example LLC", "source": "user message"}
        ],
        "blanks": [],
    }
    previous_state = {
        "fields": [
            {"id": "prior-insured", "label": "Named insured", "value": "Old LLC"}
        ]
    }
    original = tmp_path / "original.pdf"
    original.write_bytes(b"original")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    previous_state_path = tmp_path / "previous-state.json"
    previous_state_path.write_text(json.dumps(previous_state))
    output = tmp_path / "output.pdf"

    def native(command, *args):
        if command == "inspect":
            return state
        if command == "fill":
            Path(args[2]).write_bytes(b"filled")
        return {"outcomes": [{"id": "insured", "placed": True}]}

    monkeypatch.setattr(fill_plan, "native", native)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fill_plan.py",
            str(original),
            str(plan_path),
            str(output),
            "--form-name",
            "ACORD 125",
            "--draft",
            "2",
            "--previous-state",
            str(previous_state_path),
        ],
    )
    fill_plan.main()

    summary = json.loads(capsys.readouterr().out.splitlines()[2])
    assert summary["changes"] == [
        {"label": "Named insured", "value": "Example LLC", "fields": 1}
    ]


def test_summary_failure_prints_no_card(tmp_path, monkeypatch, capsys):
    original = tmp_path / "original.pdf"
    original.write_bytes(b"original")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "fields": [
                    {"id": "insured", "value": "Example LLC", "source": "brief"}
                ],
                "blanks": [],
            }
        )
    )
    output = tmp_path / "output.pdf"

    def native(command, *args):
        if command == "inspect":
            return {"fields": [{"id": "insured", "kind": "text"}]}
        if command == "fill":
            Path(args[2]).write_bytes(b"filled")
            return {"outcomes": [{"id": "insured", "placed": True}]}
        return {"outcomes": [{"id": "insured", "placed": False}]}

    monkeypatch.setattr(fill_plan, "native", native)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fill_plan.py",
            str(original),
            str(plan_path),
            str(output),
            "--form-name",
            "ACORD 125",
        ],
    )
    with pytest.raises(SystemExit):
        fill_plan.main()

    stdout = capsys.readouterr().out
    assert "fai-form-summary" not in stdout
    assert json.loads(stdout)["verified"] is False
    assert not (tmp_path / "plan.summary.json").exists()
    assert not output.exists()


def test_legacy_cli_output_and_artifacts_are_unchanged(
    tmp_path, monkeypatch, capsys, state, plan
):
    monkeypatch.delenv("FAI_FORM_SUMMARY_CARDS", raising=False)
    original = tmp_path / "original.pdf"
    original.write_bytes(b"original")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    output = tmp_path / "output.pdf"

    def native(command, *args):
        if command == "inspect":
            return state
        if command == "fill":
            Path(args[2]).write_bytes(b"filled")
        return {"outcomes": [{"id": "insured", "placed": True}]}

    monkeypatch.setattr(fill_plan, "native", native)
    monkeypatch.setattr(
        sys,
        "argv",
        ["fill_plan.py", str(original), str(plan_path), str(output)],
    )
    fill_plan.main()

    stdout = capsys.readouterr().out
    assert "fai-form-summary" not in stdout
    assert stdout.count("\n") == 1
    assert json.loads(stdout) == {
        "verified": True,
        "path": str(output),
        "outcomes": [{"id": "insured", "placed": True}],
        "blanks": plan["blanks"],
    }
    assert not (tmp_path / "plan.summary.json").exists()


def _card_cli(tmp_path, monkeypatch, *extra, state=None, plan=None):
    """Run the helper's card path against a fake native engine."""
    state = state or {
        "fields": [{"id": "insured", "label": "Named insured", "kind": "text"}]
    }
    plan = plan or {
        "fields": [{"id": "insured", "value": "Example LLC", "source": "brief"}],
        "blanks": [],
    }
    original = tmp_path / "original.pdf"
    original.write_bytes(b"original")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    output = tmp_path / "output.pdf"

    def native(command, *args):
        if command == "inspect":
            return state
        if command == "fill":
            Path(args[2]).write_bytes(b"filled")
        return {
            "outcomes": [
                {"id": entry["id"], "placed": True} for entry in plan["fields"]
            ]
        }

    monkeypatch.setattr(fill_plan, "native", native)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fill_plan.py",
            str(original),
            str(plan_path),
            str(output),
            "--form-name",
            "ACORD 125",
            *extra,
        ],
    )
    return output


def test_card_failure_removes_the_new_draft_and_allows_a_retry(
    tmp_path, monkeypatch, capsys
):
    previous_plan = tmp_path / "previous-plan.json"
    previous_plan.write_text(json.dumps({"blanks": []}))
    output = _card_cli(
        tmp_path,
        monkeypatch,
        "--draft",
        "2",
        "--previous-plan",
        str(previous_plan),
    )

    with pytest.raises(SystemExit) as exit_info:
        fill_plan.main()

    assert exit_info.value.code == 1
    lines = capsys.readouterr().out.splitlines()
    assert json.loads(lines[0])["verified"] is False
    assert "fai-form-summary" not in "\n".join(lines)
    assert not output.exists()

    previous_plan.write_text(json.dumps({"fields": [], "blanks": []}))
    fill_plan.main()
    assert output.exists()
    assert json.loads(capsys.readouterr().out.splitlines()[0])["verified"] is True


def test_recycled_follow_up_rebuilds_answered_cards_from_previous_summary(
    tmp_path, monkeypatch, capsys
):
    ask = "Is the mailing address the same as the premises address?"
    state = {
        "fields": [
            {"id": "mail1", "label": "Mailing address", "kind": "text"},
            {"id": "mail2", "label": "Mailing city", "kind": "text"},
        ]
    }
    plan = {
        "fields": [
            {"id": "mail1", "value": "1200 W Fulton St", "source": "user message"},
            {"id": "mail2", "value": "Chicago", "source": "user message"},
        ],
        "blanks": [],
    }
    previous_summary = tmp_path / "previous-summary.json"
    previous_summary.write_text(
        json.dumps(
            {
                "version": 1,
                "questions": [
                        {
                            "ask": ask,
                            "kind": "missing",
                            "why": "The documents only list the premises.",
                        "replies": ["Yes, same", "No, different"],
                        "fields": ["Mailing address", "Mailing city"],
                        "status": "open",
                        "answer": None,
                    }
                ],
            }
        )
    )
    previous_state = tmp_path / "previous-state.json"
    previous_state.write_text(
        json.dumps(
            {
                "fields": [
                    {"id": "mail1", "label": "Mailing address", "value": ""},
                    {"id": "mail2", "label": "Mailing city", "value": ""},
                ]
            }
        )
    )
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({ask: "Yes, same"}))
    _card_cli(
        tmp_path,
        monkeypatch,
        "--draft",
        "2",
        "--previous-state",
        str(previous_state),
        "--previous-summary",
        str(previous_summary),
        "--answered",
        str(answers),
        state=state,
        plan=plan,
    )

    fill_plan.main()

    summary = json.loads(capsys.readouterr().out.splitlines()[2])
    assert summary["questions"] == [
        {
            "ask": ask,
            "kind": "missing",
            "why": "The documents only list the premises.",
            "replies": ["Yes, same", "No, different"],
            "field_ids": ["mail1", "mail2"],
            "fields": ["Mailing address", "Mailing city"],
            "status": "answered",
            "answer": "Yes, same",
        }
    ]
    assert summary["changes"] == [
        {"label": "Mailing address", "value": "Yes, same", "fields": 2}
    ]


def test_previous_summary_must_carry_questions(tmp_path):
    path = tmp_path / "previous-summary.json"
    path.write_text(json.dumps({"version": 1, "questions": [{"ask": "Q"}]}))

    with pytest.raises(ValueError, match="fai-form-summary payload"):
        fill_plan.load_previous_summary(path)


def test_filled_counts_each_field_by_its_final_value():
    state = {
        "fields": [
            {"id": "box_a", "label": "Box A", "kind": "checkbox", "value": False},
            {"id": "box_b", "label": "Box B", "kind": "checkbox", "value": False},
            {"id": "box_c", "label": "Box C", "kind": "checkbox", "value": True},
            {"id": "name", "label": "Name", "kind": "text", "value": ""},
        ]
    }
    plan = {
        "fields": [
            {"id": "box_a", "value": False, "source": "brief"},
            {"id": "box_c", "value": False, "source": "brief"},
            {"id": "name", "value": "Example LLC", "source": "brief"},
        ],
        "blanks": [],
    }

    summary = fill_plan.build_summary(
        form_name="ACORD 125",
        output=Path("ACORD-125.pdf"),
        draft=1,
        state=state,
        plan=plan,
    )

    assert (summary["filled"], summary["total"]) == (1, 4)


def test_done_rows_prefer_key_answers_and_cap_at_six():
    specs = [
        ("contact", "Contact name", "Alex"),
        ("phone", "Phone", "555-0100"),
        ("email", "Email", "alex@example.com"),
        ("insured", "Named insured", "Example LLC"),
        ("period", "Policy period", "2027-01-01 to 2028-01-01"),
        ("lob", "Lines of business", "GL, Auto"),
        ("locations", "Number of locations", "3"),
        ("notes", "Notes", "Renewal"),
    ]
    state = {
        "fields": [
            {"id": field_id, "label": label, "kind": "text"}
            for field_id, label, _ in specs
        ]
    }
    plan = {
        "fields": [
            {"id": field_id, "value": value, "source": "submission"}
            for field_id, _, value in specs
        ],
        "blanks": [],
    }

    summary = fill_plan.build_summary(
        form_name="ACORD 125",
        output=Path("filled.pdf"),
        draft=1,
        state=state,
        plan=plan,
    )

    assert summary["done"] == [
        {"label": "Named insured", "value": "Example LLC"},
        {"label": "Policy period", "value": "2027-01-01 to 2028-01-01"},
        {"label": "Lines of business", "value": "GL, Auto"},
        {"label": "Number of locations", "value": "3"},
        {"label": "Contact name", "value": "Alex"},
        {"label": "Phone", "value": "555-0100"},
    ]


def test_summary_prefers_plan_labels_and_groups_key_done_rows():
    state = {
        "fields": [
            {"id": "NamedInsured", "kind": "text"},
            {"id": "PolicyEffectiveDate", "kind": "text"},
            {"id": "PolicyExpirationDate", "kind": "text"},
            {"id": "PolicyLineOfBusinessCommercialGeneralLiability", "kind": "checkbox"},
            {"id": "PolicyLineOfBusinessCommercialAuto", "kind": "checkbox"},
            {"id": "PolicyLineOfBusinessWorkersCompensation", "kind": "checkbox"},
            {"id": "NamedInsuredTaxIdentifier", "kind": "text"},
            {"id": "LocationCount", "kind": "text"},
            {"id": "OwnerIndicatorB", "kind": "checkbox"},
            {"id": "UnresolvedCode", "kind": "text"},
        ]
    }
    plan = {
        "fields": [
            {
                "id": "NamedInsured",
                "label": "Named insured",
                "value": "Example LLC",
                "source": "application",
            },
            {
                "id": "PolicyEffectiveDate",
                "label": "Effective date",
                "value": "2027-01-01",
                "source": "application",
            },
            {
                "id": "PolicyExpirationDate",
                "label": "Expiration date",
                "value": "2028-01-01",
                "source": "application",
            },
            {
                "id": "PolicyLineOfBusinessCommercialGeneralLiability",
                "label": "Lines of business: General liability",
                "value": True,
                "source": "application",
            },
            {
                "id": "PolicyLineOfBusinessCommercialAuto",
                "label": "Lines of business: Commercial auto",
                "value": True,
                "source": "application",
            },
            {
                "id": "PolicyLineOfBusinessWorkersCompensation",
                "label": "Lines of business: Workers compensation",
                "value": False,
                "source": "application",
            },
            {
                "id": "NamedInsuredTaxIdentifier",
                "label": "FEIN",
                "value": "12-3456789",
                "source": "W-9",
                "assumption": "Taken from the current W-9",
            },
            {
                "id": "LocationCount",
                "label": "Locations",
                "value": "2",
                "source": "schedule",
            },
            {
                "id": "OwnerIndicatorB",
                "value": False,
                "source": "application",
            },
        ],
        "blanks": [
            {
                "id": "UnresolvedCode",
                "label": "Entity code",
                "reason": "The sources conflict",
                "ask": "Which entity code is current?",
                "why": "Two supplied forms disagree.",
                "kind": "unclear",
            }
        ],
    }

    summary = fill_plan.build_summary(
        form_name="ACORD 125",
        output=Path("filled.pdf"),
        draft=1,
        state=state,
        plan=plan,
    )

    assert summary["done"] == [
        {"label": "Named insured", "value": "Example LLC"},
        {"label": "Policy period", "value": "2027-01-01 – 2028-01-01"},
        {
            "label": "Lines of business",
            "value": "General liability, Commercial auto",
        },
        {"label": "FEIN", "value": "12-3456789"},
        {"label": "Locations", "value": "2"},
    ]
    assert summary["questions"][0]["fields"] == ["Entity code"]
    assert summary["assumptions"][0]["statement"] == "FEIN is 12-3456789"


def test_recycled_follow_up_maps_question_fields_through_plan_labels():
    ask = "What is the mailing street?"
    state = {
        "fields": [
            {"id": "ApplicantMailingAddressLine1", "kind": "text", "value": ""}
        ]
    }
    plan = {
        "fields": [
            {
                "id": "ApplicantMailingAddressLine1",
                "label": "Mailing street",
                "value": "1200 W Fulton St",
                "source": "AskUserQuestion answer: 1200 W Fulton St",
            }
        ],
        "blanks": [],
    }
    previous_summary = {
        "version": 1,
        "questions": [
            {
                "ask": ask,
                "kind": "missing",
                "why": "No mailing street was supplied.",
                "replies": [],
                "fields": ["Mailing street"],
                "status": "open",
                "answer": None,
            }
        ],
    }

    summary = fill_plan.build_summary(
        form_name="ACORD 125",
        output=Path("filled.pdf"),
        draft=1,
        state=state,
        plan=plan,
        previous_summary=previous_summary,
        answered={ask: "1200 W Fulton St"},
    )

    assert summary["questions"] == [
        {
            "ask": ask,
            "kind": "missing",
            "why": "No mailing street was supplied.",
            "replies": [],
            "fields": ["Mailing street"],
            "field_ids": ["ApplicantMailingAddressLine1"],
            "status": "answered",
            "answer": "1200 W Fulton St",
        }
    ]


def test_changes_prefer_current_plan_label():
    state = {"fields": [{"id": "TotalPayroll", "kind": "text"}]}
    previous = {
        "fields": [
            {"id": "TotalPayroll", "value": "$4,000,000", "source": "old SOV"}
        ],
        "blanks": [],
    }
    current = {
        "fields": [
            {
                "id": "TotalPayroll",
                "label": "Annual payroll",
                "value": "$4,820,000",
                "source": "new SOV",
            }
        ],
        "blanks": [],
    }

    summary = fill_plan.build_summary(
        form_name="ACORD 125",
        output=Path("filled.pdf"),
        draft=2,
        state=state,
        plan=current,
        previous_plan=previous,
    )

    assert summary["changes"] == [
        {"label": "Annual payroll", "value": "$4,820,000", "fields": 1}
    ]


def test_question_kinds_are_explicit_or_deterministically_inferred(state, plan):
    state["fields"].extend(
        [
            {"id": "conflict", "label": "Conflict", "kind": "text"},
            {"id": "choice", "label": "Choice", "kind": "text"},
            {"id": "override", "label": "Override", "kind": "text"},
        ]
    )
    plan["blanks"] = [
        {
            "id": "fax",
            "reason": "No source supplies a fax number",
            "ask": "What is the fax number?",
        },
        {
            "id": "conflict",
            "reason": "The two applications conflict",
            "ask": "Which value is current?",
            "source": "applications A and B",
        },
        {
            "id": "choice",
            "reason": "The applicant must choose",
            "ask": "Which deductible do you want?",
            "source": "application requests a selection",
        },
        {
            "id": "override",
            "reason": "The wording is ambiguous",
            "ask": "Should this be included?",
            "kind": "only_you",
        },
    ]

    assert fill_plan.planned_values(plan, state) == {"insured": "Example LLC"}
    summary = fill_plan.build_summary(
        form_name="Form",
        output=Path("filled.pdf"),
        draft=1,
        state=state,
        plan=plan,
    )
    assert [question["kind"] for question in summary["questions"]] == [
        "missing",
        "unclear",
        "only_you",
        "only_you",
    ]

    plan["blanks"][0]["kind"] = "unknown"
    with pytest.raises(ValueError, match="Question kind"):
        fill_plan.planned_values(plan, state)


def test_answered_cards_are_not_crowded_out_by_five_open_questions():
    field_ids = [f"f{index}" for index in range(6)]
    state = {
        "fields": [
            {"id": field_id, "label": field_id.upper(), "kind": "text"}
            for field_id in field_ids
        ]
    }
    answered_ask = "Question 0?"
    previous = {
        "fields": [],
        "blanks": [
            {"id": "f0", "reason": "missing", "ask": answered_ask, "why": "w"}
        ],
    }
    current = {
        "fields": [{"id": "f0", "value": "Answer", "source": "user message"}],
        "blanks": [
            {"id": field_id, "reason": "missing", "ask": f"Open {field_id}?", "why": "w"}
            for field_id in field_ids[1:]
        ],
    }

    summary = fill_plan.build_summary(
        form_name="ACORD 125",
        output=Path("ACORD-125.pdf"),
        draft=2,
        state=state,
        plan=current,
        previous_plan=previous,
        answered={answered_ask: "Answer"},
    )

    statuses = [(question["ask"], question["status"]) for question in summary["questions"]]
    assert statuses[0] == (answered_ask, "answered")
    assert [status for _, status in statuses].count("open") == 5


def test_humanize_label_keeps_acronyms() -> None:
    assert fill_plan.humanize_label("NAICS Code") == "NAICS code"
    assert fill_plan.humanize_label("Producer_FEIN_B") == "Producer FEIN (2)"


def _done_fields(*specs: tuple[str, str, str]) -> dict[str, dict[str, object]]:
    return {field_id: {"id": field_id, "label": label, "kind": kind} for field_id, label, kind in specs}


def test_done_rows_keep_separate_terms_apart() -> None:
    fields = _done_fields(
        ("prop_eff", "Property effective date", "text"),
        ("auto_eff", "Auto effective date", "text"),
        ("auto_exp", "Auto expiration date", "text"),
    )
    plan = {
        "fields": [
            {"id": "prop_eff", "value": "2027-01-01", "source": "dec"},
            {"id": "auto_eff", "value": "2027-03-01", "source": "dec"},
            {"id": "auto_exp", "value": "2027-12-31", "source": "dec"},
        ],
        "blanks": [],
    }
    labels = [row["label"] for row in fill_plan.done_rows(plan, fields)]
    assert "Policy period" not in labels
    assert labels == ["Property effective date", "Auto effective date", "Auto expiration date"]


def test_done_rows_rank_insured_name_ahead_of_other_fields() -> None:
    specs = [(f"contact_{n}", f"Contact {n}", "text") for n in range(6)]
    fields = _done_fields(*specs, ("insured", "Insured Name", "text"))
    plan = {
        "fields": [
            *({"id": field_id, "value": "x", "source": "dec"} for field_id, _, _ in specs),
            {"id": "insured", "value": "Northstar Cabinet Works LLC", "source": "dec"},
        ],
        "blanks": [],
    }
    rows = fill_plan.done_rows(plan, fields)
    assert rows[0] == {"label": "Insured name", "value": "Northstar Cabinet Works LLC"}
    assert len(rows) == 6


def test_done_rows_skip_unchecked_boxes() -> None:
    specs = [(f"owns_{n}", f"Owns location {n}", "checkbox") for n in range(6)]
    fields = _done_fields(*specs, ("contact", "Contact name", "text"))
    plan = {
        "fields": [
            *({"id": field_id, "value": False, "source": "sov"} for field_id, _, _ in specs),
            {"id": "contact", "value": "Alex", "source": "dec"},
        ],
        "blanks": [],
    }
    assert fill_plan.done_rows(plan, fields) == [{"label": "Contact name", "value": "Alex"}]


def test_humanize_label_keeps_letters_in_prose_labels() -> None:
    assert fill_plan.humanize_label("Plan B") == "Plan B"
    assert fill_plan.humanize_label("OwnerIndicator B") == "Owner indicator (2)"
    assert fill_plan.humanize_label("Owner_Indicator_C") == "Owner indicator (3)"


def test_recovery_prefers_stable_field_ids_over_labels() -> None:
    fields = {
        "ApplicantMailingAddressLine1": {
            "id": "ApplicantMailingAddressLine1",
            "label": "Applicant Mailing Address Line1",
            "kind": "text",
        }
    }
    previous_summary = {
        "questions": [
            {
                "ask": "What is the mailing street?",
                "why": "No mailing street was supplied.",
                "replies": [],
                "fields": ["Mailing street"],
                "field_ids": ["ApplicantMailingAddressLine1"],
                "kind": "missing",
            }
        ]
    }
    relabelled_plan = {
        "fields": [
            {
                "id": "ApplicantMailingAddressLine1",
                "label": "Street address",
                "value": "1200 W Fulton St",
                "source": "user",
            }
        ],
        "blanks": [],
    }
    entries = fill_plan.recovered_question_entries(
        previous_summary,
        fields,
        {"What is the mailing street?": "1200 W Fulton St"},
        relabelled_plan,
    )
    assert [(entry["id"], entry["label"]) for entry in entries] == [
        ("ApplicantMailingAddressLine1", "Mailing street")
    ]
