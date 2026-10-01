"""Check skill metadata and reproducible packaging."""

import os
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from scripts import skill_bundle
from scripts.skill_bundle import build_bundle, read_skill

VALID_SKILL = "---\nname: test-skill\ndescription: Extract fields.\n---\nExtract the fields.\n"


@pytest.mark.parametrize(
    "directory", sorted((Path(__file__).parents[1] / "skills").iterdir()), ids=lambda path: path.name,
)
def test_repository_skill_packages_original_content(directory: Path, tmp_path: Path) -> None:
    """Check every committed skill against the same validator used for publishing."""
    output = tmp_path / f"{directory.name}.zip"
    build_bundle(directory, output)
    with ZipFile(output) as archive:
        assert archive.read("SKILL.md") == (directory / "SKILL.md").read_bytes()
        assert not any(name.startswith("tests/") for name in archive.namelist())


@pytest.fixture
def skill_dir(tmp_path: Path) -> Path:
    """Create an isolated skill folder."""
    directory = tmp_path / "test-skill"
    directory.mkdir()
    (directory / "SKILL.md").write_text(VALID_SKILL, encoding="utf-8")
    return directory


def test_build_bundle_preserves_content_excludes_tests_and_is_repeatable(
    skill_dir: Path, tmp_path: Path,
) -> None:
    (skill_dir / "tests").mkdir()
    (skill_dir / "tests" / "fixture.txt").write_text("Not a runtime resource")
    output = tmp_path / "dist" / "skill.zip"
    build_bundle(skill_dir, output)
    first = output.read_bytes()
    with ZipFile(output) as archive:
        assert archive.namelist() == ["SKILL.md"]
        assert archive.read("SKILL.md") == VALID_SKILL.encode()
    build_bundle(skill_dir, output)
    assert output.read_bytes() == first
    (skill_dir / "SKILL.md").write_text(VALID_SKILL + "Keep field order.\n")
    build_bundle(skill_dir, output)
    assert output.read_bytes() != first


@pytest.mark.parametrize("content", [
    "No frontmatter",
    "---\nname: test-skill\n",
    "---\nname: [\n---\nInstructions",
    "---\n- test-skill\n---\nInstructions",
    VALID_SKILL.replace("name: test-skill\n", ""),
    VALID_SKILL.replace("name: test-skill", "name: Wrong_Name"),
    VALID_SKILL.replace("name: test-skill", "name: other-skill"),
    VALID_SKILL.replace("description: Extract fields.", "description: 123"),
    VALID_SKILL.replace("description: Extract fields.", 'description: " "'),
    VALID_SKILL.replace("Extract fields.", "x" * 1025),
    VALID_SKILL.replace("Extract the fields.\n", ""),
])
def test_read_skill_invalid_content_raises(skill_dir: Path, content: str) -> None:
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    with pytest.raises((ValueError, yaml.YAMLError)):
        read_skill(skill_dir)


def test_read_skill_missing_manifest_raises(skill_dir: Path) -> None:
    (skill_dir / "SKILL.md").unlink()
    with pytest.raises(ValueError, match="Missing SKILL.md"):
        read_skill(skill_dir)


def test_build_bundle_invalid_input_preserves_existing_output(skill_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "skill.zip"
    build_bundle(skill_dir, output)
    original = output.read_bytes()
    (skill_dir / "SKILL.md").write_text("Invalid")
    with pytest.raises(ValueError):
        build_bundle(skill_dir, output)
    assert output.read_bytes() == original


def test_read_skill_symlink_raises(skill_dir: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text(VALID_SKILL)
    (skill_dir / "SKILL.md").unlink()
    (skill_dir / "SKILL.md").symlink_to(outside)
    with pytest.raises(ValueError, match="Symlinks"):
        read_skill(skill_dir)


def test_build_bundle_includes_nested_resources_and_executable_scripts(
    skill_dir: Path, tmp_path: Path,
) -> None:
    resources = {
        "scripts/extract.sh": b"#!/bin/sh\nprintf 'done'\n",
        "references/forms/guide.md": b"Field definitions",
        "assets/template.bin": bytes([0, 255, 128, 42]),
        "assets/tests/example.json": b"{}",
        "examples/input.txt": b"Sample input",
        "LICENSE.txt": b"License terms",
    }
    for name, content in resources.items():
        path = skill_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (skill_dir / "scripts/extract.sh").chmod(0o755)
    (skill_dir / "tests").mkdir()
    (skill_dir / "tests/test_example.py").write_text("assert True")

    expected = {"SKILL.md": VALID_SKILL.encode(), **resources}
    assert read_skill(skill_dir) == expected
    output = tmp_path / "skill.zip"
    build_bundle(skill_dir, output)
    first = output.read_bytes()
    with ZipFile(output) as archive:
        assert archive.namelist() == sorted(expected)
        assert {name: archive.read(name) for name in archive.namelist()} == expected
        assert archive.getinfo("scripts/extract.sh").external_attr >> 16 == 0o100755
    os.utime(skill_dir / "scripts/extract.sh", (1234567890, 1234567890))
    build_bundle(skill_dir, output)
    assert output.read_bytes() == first
    (skill_dir / "references/forms/guide.md").write_text("Updated definitions")
    build_bundle(skill_dir, output)
    assert output.read_bytes() != first


def test_read_skill_nested_directory_symlink_raises(skill_dir: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data.txt").write_text("Outside the skill")
    (skill_dir / "references").mkdir()
    (skill_dir / "references/link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlinks"):
        read_skill(skill_dir)


@pytest.mark.parametrize("name", ["references\\outside.md", "C:outside.md", "control\x01.txt"])
def test_read_skill_unsafe_resource_path_raises(skill_dir: Path, name: str) -> None:
    (skill_dir / name).write_text("Resource")
    with pytest.raises(ValueError, match="Unsafe bundle path"):
        read_skill(skill_dir)


def test_read_skill_special_file_raises(skill_dir: Path) -> None:
    os.mkfifo(skill_dir / "pipe")
    with pytest.raises(ValueError, match="regular file"):
        read_skill(skill_dir)


@pytest.mark.parametrize(("limit", "value", "message"), [
    ("MAX_FILE_BYTES", len(VALID_SKILL) - 1, "File exceeds"),
    ("MAX_BUNDLE_BYTES", len(VALID_SKILL), "total size"),
    ("MAX_BUNDLE_FILES", 1, "file count"),
])
def test_read_skill_resource_limits_raise(
    skill_dir: Path, monkeypatch: pytest.MonkeyPatch, limit: str, value: int, message: str,
) -> None:
    (skill_dir / "resource.txt").write_text("Resource")
    monkeypatch.setattr(skill_bundle, limit, value)
    with pytest.raises(ValueError, match=message):
        read_skill(skill_dir)


def test_build_bundle_archive_limit_preserves_output(
    skill_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "skill.zip"
    output.write_bytes(b"Previous bundle")
    monkeypatch.setattr(skill_bundle, "MAX_BUNDLE_BYTES", len(VALID_SKILL))
    with pytest.raises(ValueError, match="ZIP exceeds"):
        build_bundle(skill_dir, output)
    assert output.read_bytes() == b"Previous bundle"


def test_build_bundle_output_cannot_overwrite_source(skill_dir: Path) -> None:
    with pytest.raises(ValueError, match="outside the skill directory"):
        build_bundle(skill_dir, skill_dir / "SKILL.md")
    assert (skill_dir / "SKILL.md").read_text() == VALID_SKILL


def test_read_skill_directory_symlink_raises(skill_dir: Path, tmp_path: Path) -> None:
    link = tmp_path / "linked-skill"
    link.symlink_to(skill_dir, target_is_directory=True)
    with pytest.raises(ValueError, match="real skill directory"):
        read_skill(link)
