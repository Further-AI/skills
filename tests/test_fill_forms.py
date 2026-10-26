"""Plan validation and publication gates around the native form engine."""

import importlib.util
import json
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
