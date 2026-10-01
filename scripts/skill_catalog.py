"""Package the complete skills directory, including an intentionally empty catalog."""

import argparse
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from scripts.skill_bundle import build_bundle

SkillName = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]


class SkillCatalog(BaseModel):
    """Names packaged together; a missing ZIP must never look like a deletion."""

    model_config = ConfigDict(extra="forbid")

    skills: list[SkillName]

    @field_validator("skills")
    @classmethod
    def unique_names(cls, names: list[str]) -> list[str]:
        """Reject ambiguous manifests before publishing."""
        if len(names) != len(set(names)):
            raise ValueError("Skill names must be unique")
        return names


def package_catalog(skills_dir: Path, output: Path) -> SkillCatalog:
    """Validate every repository entry and write the manifest only after packaging."""
    catalog = SkillCatalog(skills=sorted(path.name for path in skills_dir.iterdir()))
    output.mkdir(parents=True, exist_ok=False)
    for name in catalog.skills:
        build_bundle(skills_dir / name, output / f"{name}.zip")
    (output / "catalog.json").write_text(catalog.model_dump_json())
    return catalog


def main() -> None:
    """Create one artifact containing the complete repository snapshot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skills", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package_catalog(args.skills, args.output)


if __name__ == "__main__":
    main()
