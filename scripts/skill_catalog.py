"""Package the complete skills directory, including an intentionally empty catalog."""

import argparse
from pathlib import Path
from typing import Annotated, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from scripts.skill_bundle import build_bundle

SkillName = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]


class EnvironmentAvailability(BaseModel):
    """Skills that may be exposed in one deployment environment."""

    model_config = ConfigDict(extra="forbid")

    enabled_skills: list[SkillName]

    @field_validator("enabled_skills")
    @classmethod
    def unique_names(cls, names: list[str]) -> list[str]:
        """Reject duplicate entries so rollout changes remain unambiguous."""
        if len(names) != len(set(names)):
            raise ValueError("Enabled skill names must be unique")
        return names


class SkillAvailability(BaseModel):
    """Explicit, independent staging and production allowlists."""

    model_config = ConfigDict(extra="forbid")

    staging: EnvironmentAvailability
    production: EnvironmentAvailability


class SkillCatalog(BaseModel):
    """Names packaged together; a missing ZIP must never look like a deletion."""

    model_config = ConfigDict(extra="forbid")

    skills: list[SkillName]
    availability: SkillAvailability

    @field_validator("skills")
    @classmethod
    def unique_names(cls, names: list[str]) -> list[str]:
        """Reject ambiguous manifests before publishing."""
        if len(names) != len(set(names)):
            raise ValueError("Skill names must be unique")
        return names

    @model_validator(mode="after")
    def known_enabled_skills(self) -> Self:
        """Require every enabled name to have a packaged bundle."""
        enabled = set(self.availability.staging.enabled_skills) | set(self.availability.production.enabled_skills)
        unknown = enabled - set(self.skills)
        if unknown:
            raise ValueError(f"Availability names skills missing from the catalog: {sorted(unknown)}")
        return self


def package_catalog(skills_dir: Path, output: Path) -> SkillCatalog:
    """Validate every repository entry and write the manifest only after packaging."""
    availability = SkillAvailability.model_validate(
        yaml.safe_load((skills_dir.parent / "availability.yaml").read_text())
    )
    catalog = SkillCatalog(
        skills=sorted(path.name for path in skills_dir.iterdir()),
        availability=availability,
    )
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
