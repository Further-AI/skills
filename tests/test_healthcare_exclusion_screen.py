"""Spec for the Healthcare Exclusion Screen skill.

An applicant who keeps a listed name and changes an NPI, date of birth, city,
or specialty is still a name hit. The result has to stay Possible match and
the list entry has to be included. No match is reserved for a search that
finds no similar name.

These tests follow the skill against the bundled LEIE tables. They fail while
the skill tells the agent to rule that name hit out and to omit Listing.

Not exercised here, because the shipped tables do not let the path run:

- Reinstatement. No bundled row has a reinstatement date, so a conflict cannot
  be shown to skip the "Previously excluded" label.
- A hit that both agrees and conflicts, such as the listed date of birth plus
  a different NPI. The Match row and the No match row give different labels.
- Nicknames other than Bob/Robert and Liz/Elizabeth, the only pairs the skill names.
- A listed business date of birth. The entity table has none.
- Owner-to-entity links. The tables have no link column.
- A maiden name stored on the list. There is no maiden-name column. A former
  surname supplied by the applicant is tested.
- Similar names that are not the same name, a named nickname, a hyphenated
  part, or a former surname. The skill does not define a distance for
  "closest names."
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "healthcare-exclusion-screen"
SKILL_PATH = SKILL_DIR / "SKILL.md"

Status = Literal["match", "possible", "ruled_out"]
Record = dict[str, str]

_ROW_CACHE: dict[str, list[Record]] = {}
_FIELD_PATTERNS = {
    "NPI": re.compile(r"\bNPIs?\b", re.IGNORECASE),
    "date of birth": re.compile(r"\bdate of birth\b|\bDOB\b", re.IGNORECASE),
    "location": re.compile(r"\blocations?\b|\bcit(?:y|ies)\b", re.IGNORECASE),
    "specialty": re.compile(r"\bspecialt(?:y|ies)\b", re.IGNORECASE),
}
_NEGATED_RULE_OUT = re.compile(
    r"\b(do not|does not|don't|doesn't|never|cannot|can not|nothing|not)\b"
    r".{0,80}?\b(rule|rules|ruled|ruling|clear|clears)\b",
    re.IGNORECASE | re.DOTALL,
)
_RULE_OUT = re.compile(
    r"\b(rules out|ruled out|rule out|rules the hit out|rule the hit out|rules it out)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Applicant:
    """One person or business from a submission, with self-reported identifiers."""

    kind: str
    role: str
    first_name: str = ""
    last_name: str = ""
    former_last_names: tuple[str, ...] = ()
    business_name: str = ""
    dbas: tuple[str, ...] = ()
    npi: str = ""
    dob: str = ""
    city: str = ""
    state: str = ""
    specialty: str = ""


@dataclass(frozen=True)
class Policy:
    """Adjudication rules read from the skill text on this call."""

    same_npi_is_match: bool
    name_and_dob_is_match: bool
    name_location_specialty_is_match: bool
    omit_listing_on_no_match: bool
    rule_out_sentences: dict[str, tuple[str, ...]]

    def rules_out(self, field: str) -> bool:
        return bool(self.rule_out_sentences.get(field))


@dataclass(frozen=True)
class Decision:
    status: Status
    confirms: tuple[str, ...]
    conflicts: tuple[str, ...]


@dataclass(frozen=True)
class ScreenResult:
    screened: str
    result: str
    confidence: str
    why: str
    listing: str | None
    alternatives: tuple[str, ...]
    flags: str
    source: str

    def as_json(self) -> dict[str, object]:
        """Structured result. Listing is absent when the skill says to omit it."""
        payload: dict[str, object] = {
            "Screened": self.screened,
            "Result": self.result,
            "Confidence": self.confidence,
            "Why": self.why,
            "Flags": self.flags,
            "Source": self.source,
        }
        if self.listing is not None:
            payload["Listing"] = self.listing
        if self.alternatives:
            payload["Alternatives considered"] = list(self.alternatives)
        if self.confidence in {"Medium", "Low"}:
            payload["To raise confidence"] = "NPI or date of birth"
        return payload

    def as_text(self) -> str:
        lines = [
            f"Screened: {self.screened}",
            f"Result: {self.result}",
            f"Confidence: {self.confidence}",
            f"Why: {self.why}",
            "",
        ]
        if self.listing is not None:
            lines.append(f"Listing: {self.listing}")
        if self.alternatives:
            lines.append("Alternatives considered:")
            lines.extend(
                f"{index}. {alternative}"
                for index, alternative in enumerate(self.alternatives, start=1)
            )
            lines.append("")
        lines.append(f"Flags: {self.flags}")
        if self.confidence in {"Medium", "Low"}:
            lines.append("To raise confidence: NPI or date of birth")
        lines.append(f"Source: {self.source}")
        return "\n".join(lines)


def read_skill(skill_text: str | None = None) -> str:
    if skill_text is not None:
        return skill_text
    return SKILL_PATH.read_text(encoding="utf-8")


def _load_csv(path: Path) -> list[Record]:
    cached = _ROW_CACHE.get(str(path))
    if cached is not None:
        return cached
    with path.open(newline="", encoding="utf-8") as handle:
        rows: list[Record] = []
        for raw in csv.DictReader(handle):
            row = {
                str(key): "" if value is None else str(value)
                for key, value in raw.items()
                if key is not None
            }
            rows.append(row)
    _ROW_CACHE[str(path)] = rows
    return rows


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.])\s+|\n+", text)
    return [part.strip() for part in parts if part.strip()]


def load_policy(skill_text: str | None = None) -> Policy:
    """Read the match and rule-out instructions from the skill the agent follows."""
    text = read_skill(skill_text)
    rule_out: dict[str, list[str]] = {field: [] for field in _FIELD_PATTERNS}
    for sentence in _sentences(text):
        if _NEGATED_RULE_OUT.search(sentence):
            continue
        if _RULE_OUT.search(sentence) is None:
            continue
        for field, pattern in _FIELD_PATTERNS.items():
            if pattern.search(sentence):
                rule_out[field].append(sentence)
    return Policy(
        same_npi_is_match=re.search(r"\bNPI matches\b", text) is not None
        or re.search(r"\bsame NPI is a match\b", text, re.IGNORECASE) is not None,
        name_and_dob_is_match=re.search(r"\bdate of birth matches\b", text, re.IGNORECASE)
        is not None,
        name_location_specialty_is_match=re.search(
            r"city/state and specialty that fit", text, re.IGNORECASE
        )
        is not None,
        omit_listing_on_no_match=re.search(
            r"Omit `Listing` when the result is No match", text
        )
        is not None,
        rule_out_sentences={field: tuple(sentences) for field, sentences in rule_out.items()},
    )


def _individual_files(skill_text: str) -> list[tuple[str, str, Path]]:
    named = re.findall(r"leie_individuals_([a-z])_to_([a-z])\.csv", skill_text)
    if not named:
        raise AssertionError("skill does not name the individual LEIE files")
    files: list[tuple[str, str, Path]] = []
    for start, end in named:
        path = SKILL_DIR / "references" / f"leie_individuals_{start}_to_{end}.csv"
        if not path.is_file():
            raise AssertionError(f"missing {path}")
        files.append((start.upper(), end.upper(), path))
    return files


def _entity_file(skill_text: str) -> Path:
    if "leie_entities.csv" not in skill_text:
        raise AssertionError("skill does not name the entity LEIE file")
    path = SKILL_DIR / "references" / "leie_entities.csv"
    if not path.is_file():
        raise AssertionError(f"missing {path}")
    return path


def _rows_for_surname(surname: str, skill_text: str) -> list[Record]:
    initial = next(char for char in _normalize(surname) if "A" <= char <= "Z")
    for start, end, path in _individual_files(skill_text):
        if start <= initial <= end:
            return _load_csv(path)
    raise AssertionError(f"no individual file for {surname}")


def _suffixes(skill_text: str) -> frozenset[str]:
    match = re.search(r"suffixes such as ([^.]+)", skill_text, re.IGNORECASE)
    if match is None:
        return frozenset()
    ignored = {"such", "as", "or", "and"}
    return frozenset(
        token.upper()
        for token in re.findall(r"[A-Za-z]+", match.group(1))
        if token.lower() not in ignored
    )


def _business_tokens(name: str, suffixes: frozenset[str]) -> tuple[str, ...]:
    cleaned = re.sub(r"[^A-Z0-9]+", " ", name.upper())
    return tuple(token for token in cleaned.split() if token not in suffixes)


def _nickname_groups(skill_text: str) -> tuple[frozenset[str], ...]:
    groups: list[frozenset[str]] = []
    if re.search(r"Bob/Robert", skill_text):
        groups.append(frozenset({"BOB", "ROBERT"}))
    if re.search(r"Liz/Elizabeth", skill_text):
        groups.append(frozenset({"LIZ", "ELIZABETH"}))
    return tuple(groups)


def _first_names_match(query: str, listed: str, groups: tuple[frozenset[str], ...]) -> bool:
    left = _normalize(query)
    right = _normalize(listed)
    if left == right:
        return True
    return any(left in group and right in group for group in groups)


def _last_names_match(query: str, listed: str, hyphenated_parts: bool) -> bool:
    left = _normalize(query)
    right = _normalize(listed)
    if left == right:
        return True
    if not hyphenated_parts or ("-" not in left and "-" not in right):
        return False
    left_parts = {part for part in left.split("-") if part}
    right_parts = {part for part in right.split("-") if part}
    return bool(left_parts & right_parts)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().upper())


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _record_key(row: Record) -> tuple[str, ...]:
    return tuple(
        row[column]
        for column in (
            "last_name",
            "first_name",
            "middle_name",
            "business_name",
            "npi",
            "dob",
            "city",
            "state",
            "exclusion_date",
        )
    )


def search(applicant: Applicant, skill_text: str | None = None) -> list[Record]:
    """Search the bundled tables the way the skill's search section describes."""
    text = read_skill(skill_text)
    if applicant.kind == "business":
        suffixes = _suffixes(text)
        queries = [applicant.business_name]
        if re.search(r"search each DBA", text, re.IGNORECASE):
            queries.extend(applicant.dbas)
        found: list[Record] = []
        seen: set[tuple[str, ...]] = set()
        rows = _load_csv(_entity_file(text))
        for query in queries:
            wanted = _business_tokens(query, suffixes)
            if not wanted:
                continue
            for row in rows:
                if _business_tokens(row["business_name"], suffixes) != wanted:
                    continue
                key = _record_key(row)
                if key not in seen:
                    seen.add(key)
                    found.append(row)
        return found

    if applicant.kind != "individual":
        raise ValueError(applicant.kind)

    groups = _nickname_groups(text)
    hyphenated_parts = re.search(r"hyphenated parts", text, re.IGNORECASE) is not None
    surnames = [applicant.last_name]
    if re.search(r"former names|maiden", text, re.IGNORECASE):
        surnames.extend(applicant.former_last_names)
    if hyphenated_parts:
        surnames.extend(
            part for surname in list(surnames) if "-" in surname for part in surname.split("-") if part
        )
    found = []
    seen = set()
    for surname in surnames:
        if not surname.strip():
            continue
        for row in _rows_for_surname(surname, text):
            if not _last_names_match(surname, row["last_name"], hyphenated_parts):
                continue
            if not _first_names_match(applicant.first_name, row["first_name"], groups):
                continue
            key = _record_key(row)
            if key not in seen:
                seen.add(key)
                found.append(row)
    return found


def _exclusion_types(skill_text: str) -> dict[str, tuple[str, str]]:
    if "exclusion_types.md" not in skill_text:
        return {}
    text = (SKILL_DIR / "references" / "exclusion_types.md").read_text(encoding="utf-8")
    found: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        code = cells[0].strip("`")
        if not re.fullmatch(r"[0-9A-Za-z]+", code) or cells[1].lower() == "reason":
            continue
        period = cells[2].strip() if len(cells) > 2 else ""
        found[code] = (cells[1].strip(), period)
    return found


def _listed_name(row: Record) -> str:
    if row["business_name"].strip() and not row["first_name"].strip():
        return row["business_name"].strip()
    return " ".join(
        part.strip() for part in (row["first_name"], row["middle_name"], row["last_name"]) if part.strip()
    )


def _listing(row: Record, skill_text: str) -> str:
    types = _exclusion_types(skill_text)
    code = row["exclusion_type"].strip()
    reason, period = types.get(code, ("meaning wasn't found here", ""))
    detail = reason if not period else f"{reason}; minimum period {period}"
    return (
        f"{_listed_name(row)}, {row['specialty'].strip()}, "
        f"{row['city'].strip()}, {row['state'].strip()}, "
        f"excluded {row['exclusion_date'].strip()} under {code}: {detail}"
    )


def _source_line(skill_text: str) -> str:
    match = re.search(r"^Source: (.+)$", skill_text, re.MULTILINE)
    if match is None:
        raise AssertionError("skill output template has no Source line")
    return match.group(1).strip()


def _decide(policy: Policy, applicant: Applicant, row: Record) -> Decision:
    app_npi = _digits(applicant.npi)
    row_npi = _digits(row["npi"])
    if app_npi and row_npi and app_npi == row_npi and policy.same_npi_is_match:
        return Decision("match", ("NPI",), ())

    conflicts: list[str] = []
    if app_npi and row_npi and app_npi != row_npi and policy.rules_out("NPI"):
        conflicts.append("NPI")

    app_dob = _digits(applicant.dob)
    row_dob = _digits(row["dob"])
    dob_matches = bool(app_dob and row_dob and app_dob == row_dob)
    if (
        app_dob
        and row_dob
        and app_dob != row_dob
        and policy.rules_out("date of birth")
    ):
        conflicts.append("date of birth")

    app_city = _normalize(applicant.city)
    row_city = _normalize(row["city"])
    app_state = _normalize(applicant.state)
    row_state = _normalize(row["state"])
    location_conflicts = bool(app_city and row_city and app_city != row_city) or bool(
        app_state and row_state and app_state != row_state
    )
    location_fits = bool(
        app_city and row_city and app_state and row_state and app_city == row_city and app_state == row_state
    )
    if location_conflicts and policy.rules_out("location"):
        conflicts.append("location")

    app_specialty = _normalize(applicant.specialty)
    row_specialty = _normalize(row["specialty"])
    specialty_fits = bool(app_specialty and row_specialty and app_specialty == row_specialty)
    if app_specialty and row_specialty and app_specialty != row_specialty and policy.rules_out("specialty"):
        conflicts.append("specialty")

    if conflicts:
        return Decision("ruled_out", (), tuple(conflicts))
    if dob_matches and policy.name_and_dob_is_match:
        return Decision("match", ("date of birth",), ())
    if location_fits and specialty_fits and policy.name_location_specialty_is_match:
        return Decision("match", ("city/state", "specialty"), ())
    return Decision("possible", (), ())


def _flags(applicant: Applicant, row: Record) -> str:
    notes: list[str] = []
    if row["waiver_date"].strip():
        notes.append(f"waiver {row['waiver_date'].strip()} {row['waiver_state'].strip()}")
    if applicant.kind == "individual":
        if _normalize(applicant.first_name) != _normalize(row["first_name"]):
            notes.append(f"name variation {applicant.first_name}/{row['first_name']}")
        elif _normalize(applicant.last_name) != _normalize(row["last_name"]):
            notes.append(f"name variation {applicant.last_name}/{row['last_name']}")
    return ", ".join(notes) if notes else "none"


def _confidence(decision: Decision, result: str) -> str:
    if result.startswith("Previously excluded"):
        return "High"
    if "NPI" in decision.confirms or "date of birth" in decision.confirms:
        return "High"
    if "NPI" in decision.conflicts or "date of birth" in decision.conflicts:
        return "High"
    if decision.status == "match" or decision.conflicts:
        return "Medium"
    if result == "No match":
        return "High"
    return "Low"


def _why(policy: Policy, decision: Decision, hit_count: int) -> str:
    if hit_count == 0:
        return "No similar name in the bundled LEIE tables."
    if decision.status == "ruled_out":
        quoted: list[str] = []
        for field in decision.conflicts:
            quoted.extend(policy.rule_out_sentences.get(field, ()))
        unique = list(dict.fromkeys(quoted))
        return (
            "Name hit ruled out by a different " + ", ".join(decision.conflicts) + ". " + " ".join(unique)
        )
    if decision.status == "match":
        return "Confirmed by " + ", ".join(decision.confirms) + "."
    return (
        "The name matches. No listed identifier confirms the hit, "
        "and no supplied identifier rules it out."
    )


def _display_name(applicant: Applicant) -> str:
    if applicant.kind == "business":
        return applicant.business_name.strip()
    return f"{applicant.first_name} {applicant.last_name}".strip()


def screen(applicant: Applicant, skill_text: str | None = None) -> ScreenResult:
    """Adjudicate one submission against the bundled list using the skill text."""
    text = read_skill(skill_text)
    policy = load_policy(text)
    hits = search(applicant, text)
    decisions = [(row, _decide(policy, applicant, row)) for row in hits]
    kept = [(row, decision) for row, decision in decisions if decision.status != "ruled_out"]
    source = _source_line(text)

    if not kept:
        ruled_out = [(row, decision) for row, decision in decisions if decision.status == "ruled_out"]
        primary = ruled_out[0][1] if ruled_out else Decision("ruled_out", (), ())
        alternatives = tuple(
            f"{_listed_name(row)}: ruled out by a different {', '.join(decision.conflicts)}"
            for row, decision in ruled_out[:3]
        )
        include_listing = bool(ruled_out) and not policy.omit_listing_on_no_match
        return ScreenResult(
            screened=f"{_display_name(applicant)} ({applicant.role})",
            result="No match",
            confidence=_confidence(primary, "No match"),
            why=_why(policy, primary, len(hits)),
            listing=_listing(ruled_out[0][0], text) if include_listing else None,
            alternatives=alternatives,
            flags="none",
            source=source,
        )

    matches = [(row, decision) for row, decision in kept if decision.status == "match"]
    chosen_row, chosen = matches[0] if matches else kept[0]
    others = [(row, decision) for row, decision in kept if row is not chosen_row]
    reinstated = chosen_row["reinstatement_date"].strip()
    if reinstated:
        result = f"Previously excluded (reinstated {reinstated})"
    else:
        result = "Match" if chosen.status == "match" else "Possible match"
    return ScreenResult(
        screened=f"{_display_name(applicant)} ({applicant.role})",
        result=result,
        confidence=_confidence(chosen, result),
        why=_why(policy, chosen, len(hits)),
        listing=_listing(chosen_row, text),
        alternatives=tuple(
            f"{_listed_name(row)}: {decision.status}" for row, decision in others[:3]
        ),
        flags=_flags(applicant, chosen_row),
        source=source,
    )


def _require_individual(last_name: str, first_name: str) -> Record:
    found = [
        row
        for path in (SKILL_DIR / "references").glob("leie_individuals_*.csv")
        for row in _load_csv(path)
        if row["last_name"].upper() == last_name and row["first_name"].upper() == first_name
    ]
    assert len(found) == 1, (last_name, first_name, len(found))
    return found[0]


def _require_entity(business_name: str) -> Record:
    found = [
        row
        for row in _load_csv(SKILL_DIR / "references" / "leie_entities.csv")
        if row["business_name"].upper() == business_name.upper()
    ]
    assert len(found) == 1, (business_name, len(found))
    return found[0]


def _different_npi(npi: str) -> str:
    assert re.fullmatch(r"\d{10}", npi), npi
    altered = npi[:-1] + ("0" if npi[-1] != "0" else "1")
    assert altered != npi
    return altered


def _different_dob(dob: str) -> str:
    assert re.fullmatch(r"\d{8}", dob), dob
    return "19000102" if dob == "19000101" else "19000101"


def _different_place(city: str, state: str) -> tuple[str, str]:
    if _normalize(city) == "HONOLULU" and _normalize(state) == "HI":
        return "JUNEAU", "AK"
    return "HONOLULU", "HI"


def _different_specialty(specialty: str) -> str:
    if _normalize(specialty) == "DERMATOLOGY":
        return "CARDIOLOGY"
    return "DERMATOLOGY"


def _individual(
    row: Record,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    former_last_names: tuple[str, ...] = (),
    npi: str = "",
    dob: str = "",
    city: str = "",
    state: str = "",
    specialty: str = "",
) -> Applicant:
    return Applicant(
        kind="individual",
        role="practitioner",
        first_name=row["first_name"] if first_name is None else first_name,
        last_name=row["last_name"] if last_name is None else last_name,
        former_last_names=former_last_names,
        npi=npi,
        dob=dob,
        city=city,
        state=state,
        specialty=specialty,
    )


def _business(
    row: Record,
    *,
    business_name: str | None = None,
    dbas: tuple[str, ...] = (),
    npi: str = "",
    dob: str = "",
    city: str = "",
    state: str = "",
    specialty: str = "",
) -> Applicant:
    return Applicant(
        kind="business",
        role="insured",
        business_name=row["business_name"] if business_name is None else business_name,
        dbas=dbas,
        npi=npi,
        dob=dob,
        city=city,
        state=state,
        specialty=specialty,
    )


def _assert_found(applicant: Applicant, row: Record) -> None:
    hits = search(applicant)
    assert [_record_key(hit) for hit in hits] == [_record_key(row)], [
        _listed_name(hit) for hit in hits
    ]


def _assert_listing(result: ScreenResult, row: Record) -> None:
    assert result.listing is not None, result
    for needle in (
        row["city"].strip(),
        row["state"].strip(),
        row["exclusion_type"].strip(),
        row["exclusion_date"].strip(),
    ):
        assert needle in result.listing, result.listing
    assert _listed_name(row) in result.listing
    text = result.as_text()
    payload = result.as_json()
    assert f"Result: {result.result}" in text
    assert "\nListing:" in text
    assert payload["Result"] == result.result
    assert payload["Listing"] == result.listing


def _assert_possible(applicant: Applicant, row: Record) -> None:
    _assert_found(applicant, row)
    result = screen(applicant)
    assert result.result == "Possible match" and result.listing is not None, (
        result.result,
        result.why,
        result.listing,
        result.flags,
        result.alternatives,
    )
    _assert_listing(result, row)


def _assert_match(applicant: Applicant, row: Record) -> None:
    _assert_found(applicant, row)
    result = screen(applicant)
    assert result.result == "Match" and result.listing is not None, (
        result.result,
        result.why,
        result.listing,
    )
    _assert_listing(result, row)


def test_name_only_individual_is_possible_match_and_lists_the_row() -> None:
    """A listed name with no other identifier stays a possible match and is shown."""
    row = _require_individual("ACEVEDO", "ELIZABETH")
    _assert_possible(_individual(row), row)


def test_same_npi_is_match_and_lists_the_row() -> None:
    """The listed NPI confirms the same person."""
    row = _require_individual("ABAD-SANTOS", "CRISELDA")
    assert row["npi"].strip()
    _assert_match(_individual(row, npi=row["npi"]), row)


def test_same_date_of_birth_is_match_and_lists_the_row() -> None:
    """The listed date of birth, supplied in a different written form, confirms the hit."""
    row = _require_individual("ABDUR-RAHIM", "SHABIBUDHEEN")
    dob = row["dob"]
    assert re.fullmatch(r"\d{8}", dob)
    _assert_match(_individual(row, dob=f"{dob[0:4]}-{dob[4:6]}-{dob[6:8]}"), row)


def test_same_city_and_specialty_is_match_and_lists_the_row() -> None:
    """The listed city, state, and specialty together confirm the hit."""
    row = _require_individual("ABARQUEZ", "WILLIAM")
    _assert_match(
        _individual(row, city=row["city"], state=row["state"], specialty=row["specialty"]),
        row,
    )


def test_unknown_name_is_no_match_and_omits_listing() -> None:
    """A name that is not on the list is a real No match, with no listing to show."""
    applicant = Applicant(
        kind="individual",
        role="practitioner",
        first_name="ZZZNOTREAL",
        last_name="QQQNOMATCH",
    )
    assert search(applicant) == []
    result = screen(applicant)
    assert result.result == "No match"
    assert result.listing is None
    assert "\nListing:" not in result.as_text()
    assert "Listing" not in result.as_json()


def test_supplied_npi_does_not_clear_a_row_with_no_listed_npi() -> None:
    """A missing listed NPI decides nothing, so an applicant NPI does not clear the name."""
    row = _require_individual("ABDUR-RAHIM", "SHABIBUDHEEN")
    assert not row["npi"].strip()
    _assert_possible(_individual(row, npi="1234567893"), row)


def test_supplied_dob_does_not_clear_an_entity_with_no_listed_dob() -> None:
    """A business with no listed date of birth is not cleared by a date on the application."""
    row = _require_entity("14 LAWRENCE AVE PHARMACY")
    assert not row["dob"].strip()
    assert not row["npi"].strip()
    _assert_possible(_business(row, dob="1900-01-01", npi="1234567893"), row)


def test_conflicting_npi_keeps_individual_name_hit() -> None:
    """The listed name plus a different self-reported NPI stays on the result."""
    row = _require_individual("ABAD-SANTOS", "CRISELDA")
    assert row["npi"].strip()
    _assert_possible(_individual(row, npi=_different_npi(row["npi"])), row)


def test_conflicting_date_of_birth_keeps_individual_name_hit() -> None:
    """The listed name plus a different self-reported date of birth stays on the result."""
    row = _require_individual("ACEVEDO", "ELIZABETH")
    assert row["dob"].strip()
    _assert_possible(_individual(row, dob=_different_dob(row["dob"])), row)


def test_conflicting_city_keeps_individual_name_hit() -> None:
    """The listed name plus a different self-reported city and state stays on the result."""
    row = _require_individual("ACEVEDO", "ELIZABETH")
    assert row["city"].strip() and row["state"].strip()
    city, state = _different_place(row["city"], row["state"])
    _assert_possible(_individual(row, city=city, state=state), row)


def test_conflicting_specialty_keeps_individual_name_hit() -> None:
    """The listed name plus a different self-reported specialty stays on the result."""
    row = _require_individual("ACEVEDO", "ELIZABETH")
    assert row["specialty"].strip()
    _assert_possible(_individual(row, specialty=_different_specialty(row["specialty"])), row)


def test_conflicting_npi_in_m_to_z_file_keeps_name_hit() -> None:
    """The same NPI rule-out applies to a person stored in the M–Z individual file."""
    row = _require_individual("MACARTHUR", "KAYLA")
    assert row["last_name"][0].upper() >= "M"
    assert row["npi"].strip()
    _assert_possible(_individual(row, npi=_different_npi(row["npi"])), row)


def test_all_conflicting_identifiers_keep_individual_name_hit() -> None:
    """Changing NPI, date of birth, city, and specialty together still leaves the name hit."""
    row = _require_individual("ABAD-SANTOS", "CRISELDA")
    city, state = _different_place(row["city"], row["state"])
    _assert_possible(
        _individual(
            row,
            npi=_different_npi(row["npi"]),
            dob=_different_dob(row["dob"]),
            city=city,
            state=state,
            specialty=_different_specialty(row["specialty"]),
        ),
        row,
    )


def test_nickname_with_conflicting_date_of_birth_keeps_name_hit() -> None:
    """Liz/Elizabeth is a name hit, and a different date of birth does not clear it."""
    row = _require_individual("ACEVEDO", "ELIZABETH")
    applicant = _individual(row, first_name="LIZ", dob=_different_dob(row["dob"]))
    _assert_possible(applicant, row)


def test_hyphenated_surname_with_conflicting_date_of_birth_keeps_name_hit() -> None:
    """A hyphenated surname part is a name hit, and a different date of birth does not clear it."""
    row = _require_individual("ABAD-SANTOS", "CRISELDA")
    applicant = _individual(row, last_name="ABAD", dob=_different_dob(row["dob"]))
    _assert_possible(applicant, row)


def test_former_surname_with_conflicting_specialty_keeps_name_hit() -> None:
    """A former surname is a name hit, and a different specialty does not clear it."""
    row = _require_individual("ABAD-SANTOS", "CRISELDA")
    applicant = _individual(
        row,
        last_name="ZZZNOTREAL",
        former_last_names=("ABAD-SANTOS",),
        specialty=_different_specialty(row["specialty"]),
    )
    _assert_possible(applicant, row)


def test_conflicting_npi_keeps_business_name_hit() -> None:
    """A listed business name plus a different self-reported NPI stays on the result."""
    row = _require_entity("101 FIRST CARE PHARMACY INC")
    assert row["npi"].strip()
    _assert_possible(_business(row, npi=_different_npi(row["npi"])), row)


def test_conflicting_city_keeps_business_name_hit() -> None:
    """A listed business name plus a different self-reported city stays on the result."""
    row = _require_entity("3707 OREGON LLC")
    assert row["city"].strip() and row["state"].strip()
    city, state = _different_place(row["city"], row["state"])
    _assert_possible(_business(row, city=city, state=state), row)


def test_conflicting_specialty_keeps_business_name_hit() -> None:
    """A listed business name plus a different self-reported specialty stays on the result."""
    row = _require_entity("A CARING ALTERNATIVE, INC")
    assert row["specialty"].strip()
    _assert_possible(_business(row, specialty=_different_specialty(row["specialty"])), row)


def test_dba_with_conflicting_npi_keeps_business_name_hit() -> None:
    """A DBA that is the listed business name stays a hit when the NPI conflicts."""
    row = _require_entity("101 FIRST CARE PHARMACY INC")
    applicant = _business(
        row,
        business_name="ZZZNOTREAL CLINIC",
        dbas=("101 FIRST CARE PHARMACY",),
        npi=_different_npi(row["npi"]),
    )
    _assert_possible(applicant, row)


def test_conflicting_date_of_birth_keeps_waiver_listing() -> None:
    """A waived provider stays listed, including the waiver, when the date of birth conflicts."""
    row = _require_individual("WAHIB", "SAMIR")
    assert row["waiver_date"].strip() and row["waiver_state"].strip()
    applicant = _individual(row, dob=_different_dob(row["dob"]))
    _assert_possible(applicant, row)
    result = screen(applicant)
    assert row["waiver_date"] in result.flags
    assert row["waiver_state"] in result.flags
    assert "waiver" in result.flags.lower()
