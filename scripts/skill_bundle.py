"""Validate and package an Agent Skill and its resources."""

import argparse
import unicodedata
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path, PureWindowsPath
from zipfile import ZIP_STORED, ZipFile, ZipInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_BUNDLE_BYTES = 30 * 1024 * 1024
MAX_BUNDLE_FILES = 1024


class SkillMetadata(BaseModel):
    """Validate the standard metadata fields at the YAML boundary."""

    model_config = ConfigDict(strict=True, extra="forbid")

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(min_length=1, max_length=1024)
    license: str | None = None
    compatibility: str | None = Field(default=None, min_length=1, max_length=500)
    metadata: dict[str, str] = Field(default_factory=dict)
    allowed_tools: str | None = Field(default=None, alias="allowed-tools")


def _iter_skill_files(skill_dir: Path) -> Iterator[Path]:
    """Yield regular files, excluding root tests/ and Python cache directories."""
    if skill_dir.is_symlink() or not skill_dir.is_dir():
        raise ValueError(f"Expected a real skill directory: {skill_dir}")

    directories = [skill_dir]
    while directories:
        for entry in directories.pop().iterdir():
            relative_path = entry.relative_to(skill_dir).as_posix()
            if entry.is_symlink():
                raise ValueError(f"Symlinks are not supported: {entry}")
            if entry.is_dir():
                if relative_path != "tests" and entry.name != "__pycache__":
                    directories.append(entry)
                continue
            if not entry.is_file():
                raise ValueError(f"Expected a regular file: {entry}")
            # Match the backend's portable-path checks.
            if (
                "\\" in relative_path
                or PureWindowsPath(relative_path).drive
                or any(unicodedata.category(char) == "Cc" for char in relative_path)
            ):
                raise ValueError(f"Unsafe bundle path: {relative_path!r}")
            yield entry


def _validate_skill_content(content: bytes, expected_name: str) -> None:
    """Validate frontmatter and instructions without changing the source bytes."""
    lines = content.decode("utf-8").splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise ValueError("SKILL.md must start with YAML frontmatter")
    for closing, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") == "---":
            break
    else:
        raise ValueError("YAML frontmatter has no closing delimiter")

    frontmatter = "".join(lines[1:closing])
    if len(frontmatter.encode("utf-8")) > 64 * 1024:
        raise ValueError("YAML frontmatter exceeds the backend's 64 KiB limit")
    metadata = SkillMetadata.model_validate(yaml.safe_load(frontmatter))
    if metadata.name != expected_name:
        raise ValueError("Skill name must match its directory name")
    if not metadata.description.strip() or not "".join(lines[closing + 1 :]).strip():
        raise ValueError("Description and instructions must not be blank")


def read_skill(skill_dir: Path) -> dict[str, bytes]:
    """Validate a skill and collect its instructions and resources.

    Args:
        skill_dir: Skill folder whose name matches the frontmatter name.

    Returns:
        Relative paths mapped to original bytes, excluding tests and Python caches.
    """
    contents: dict[str, bytes] = {}
    total_bytes = 0
    for path in _iter_skill_files(skill_dir):
        if len(contents) >= MAX_BUNDLE_FILES:
            raise ValueError("Bundle exceeds the backend's file count limit")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"File exceeds the backend's size limit: {path}")
        content = path.read_bytes()
        total_bytes += len(content)
        if total_bytes > MAX_BUNDLE_BYTES:
            raise ValueError("Bundle exceeds the backend's total size limit")
        contents[path.relative_to(skill_dir).as_posix()] = content

    if "SKILL.md" not in contents:
        raise ValueError(f"Missing SKILL.md: {skill_dir}")
    _validate_skill_content(contents["SKILL.md"], expected_name=skill_dir.name)
    return contents


def build_bundle(skill_dir: Path, output: Path) -> None:
    """Write a validated ZIP with a root SKILL.md and reproducible metadata.

    Args:
        skill_dir: Skill folder to validate and package.
        output: Destination ZIP outside the skill folder. Overwritten after validation.
    """
    if output.resolve().is_relative_to(skill_dir.resolve()):
        raise ValueError("Write the bundle outside the skill directory")
    contents = read_skill(skill_dir)

    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_STORED) as archive:
        for path, content in sorted(contents.items()):
            # Normalize timestamps and permissions while retaining executable scripts.
            mode = 0o755 if (skill_dir / path).stat().st_mode & 0o111 else 0o644
            info = ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100000 | mode) << 16
            archive.writestr(info, content)

    archive_bytes = buffer.getvalue()
    if len(archive_bytes) > MAX_BUNDLE_BYTES:
        raise ValueError("ZIP exceeds the backend's total size limit")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(archive_bytes)


def main() -> None:
    """Validate the selected skill and optionally write its bundle."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output is None:
        read_skill(args.skill)
        print(f"Valid: {args.skill}")
    else:
        build_bundle(args.skill, args.output)
        print(f"Built: {args.output}")


if __name__ == "__main__":
    main()
