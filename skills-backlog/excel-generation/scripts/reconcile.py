"""Deterministic reconciliation of two record lists.

Typical inputs are a policy schedule and the insured's own list. Matching
runs in fixed tiers, so the same inputs always produce the same buckets:

1. Exact match on the normalized primary key (VIN, serial, policy number).
2. Exact match on a secondary key (for example year + make + unit number) for
   records still unmatched, when that secondary key is unique on both sides.
   If both records carry primary keys that disagree, the pair goes to review.
3. Primary keys one or two characters apart become review candidates.
4. Records still unmatched that share a secondary key become review candidates.

Review pairs are never matched automatically: a person decides. Everything
left over is "only in left" or "only in right". Every input record lands in
exactly one of matched, review, or only-in lists, so the counts reconcile.
Duplicate primary keys within one source are reported separately as well.

Typical usage example:

  result = reconcile(
      policy_vehicles,
      fleet_vehicles,
      primary=lambda record: normalize_vin(record.get("VIN")),
      secondary=lambda record: (record.get("Year"), record.get("Unit #")),
      primary_label="VIN",
      secondary_label="Year + Unit #",
      vin_keys=True,
  )
  assert result.accounted_for(len(policy_vehicles), len(fleet_vehicles))
"""

import math
import re
from collections import defaultdict, deque
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

type Record = Mapping[str, object]
type SecondaryKey = tuple[str, ...]

_MISSING_KEYS: Final = frozenset(
    {
        "",
        "n/a",
        "na",
        "none",
        "null",
        "nan",
        "nat",
        "undefined",
        "<empty>",
        "<na>",
        "[object object]",
    }
)
_NON_ALPHANUMERIC: Final = re.compile(r"[^0-9A-Z]")
# VINs never contain I, O, or Q; when they appear they are misread 1s and 0s.
_VIN_LOOKALIKES: Final = str.maketrans({"I": "1", "O": "0", "Q": "0"})
_VIN_VALUES: Final = {
    **{str(digit): digit for digit in range(10)},
    **dict(zip("ABCDEFGH", range(1, 9), strict=True)),
    **dict(zip("JKLMN", range(1, 6), strict=True)),
    "P": 7,
    "R": 9,
    **dict(zip("STUVWXYZ", range(2, 10), strict=True)),
}
_VIN_WEIGHTS: Final = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)
_VIN_LENGTH: Final = 17
# Position 10 encodes the model year on a 30-year cycle starting in 1980.
_VIN_YEAR_CODES: Final = "ABCDEFGHJKLMNPRSTVWXY123456789"
_VIN_YEAR_CYCLE_START: Final = 1980
_VIN_YEAR_CYCLE_LENGTH: Final = 30
_MAX_LISTED_POSITIONS: Final = 3


@dataclass(frozen=True, slots=True)
class Pair:
    """Two records judged to be, or possibly be, the same item.

    Attributes:
        left: Record from the left source.
        right: Record from the right source.
        method: How they were paired, e.g. "Exact VIN" or "Near VIN".
        detail: Human-readable evidence, e.g. "position 17: '8' vs '9'".
    """

    left: Record
    right: Record
    method: str
    detail: str


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    """The outcome of reconciling two lists.

    Attributes:
        matched: Pairs matched automatically.
        review: Candidate pairs a person should confirm.
        left_only: Left records with no match or candidate.
        right_only: Right records with no match or candidate.
        left_duplicates: Normalized primary keys appearing more than once on
            the left, mapped to their records.
        right_duplicates: The same for the right.
    """

    matched: list[Pair]
    review: list[Pair]
    left_only: list[Record]
    right_only: list[Record]
    left_duplicates: dict[str, list[Record]]
    right_duplicates: dict[str, list[Record]]

    def accounted_for(self, left_total: int, right_total: int) -> bool:
        """Returns whether every input record landed in exactly one bucket.

        Args:
            left_total: Number of records in the left input.
            right_total: Number of records in the right input.
        """
        paired = len(self.matched) + len(self.review)
        return (
            paired + len(self.left_only) == left_total
            and paired + len(self.right_only) == right_total
        )


def normalize_key(value: object) -> str:
    """Returns an identifier uppercased with spaces and punctuation removed.

    `"1ft-ex14h 1nka72991"` and `"1FTEX14H1NKA72991"` both normalize to the
    same key.

    Args:
        value: Raw identifier. Missing markers and non-finite numbers become
            an empty string and cannot match other missing keys.
    """
    if value is None:
        return ""
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        if value.is_integer():
            value = int(value)
    text = str(value).strip()
    if text.casefold() in _MISSING_KEYS:
        return ""
    return _NON_ALPHANUMERIC.sub("", text.upper())


def normalize_vin(value: object) -> str:
    """Returns a normalized VIN, correcting I, O, and Q in 17-character VINs.

    Shorter serials (pre-1981 vehicles, trailers, equipment) keep their
    letters, since the I/O/Q rule only applies to modern VINs.

    Args:
        value: Raw VIN or serial number.
    """
    key = normalize_key(value)
    return key.translate(_VIN_LOOKALIKES) if len(key) == _VIN_LENGTH else key


def vin_check_digit_ok(value: object) -> bool | None:
    """Returns whether a 17-character VIN's check digit (position 9) is valid.

    The check digit is mandatory for North American vehicles since 1981, so a
    failure there is strong evidence of a transcription error. Many
    non-North-American VINs don't use it.

    Args:
        value: The VIN to check.

    Returns:
        True or False for a 17-character VIN of valid characters, None when
        the check doesn't apply.
    """
    vin = normalize_vin(value)
    if len(vin) != _VIN_LENGTH or any(char not in _VIN_VALUES for char in vin):
        return None
    total = sum(
        _VIN_VALUES[char] * weight
        for char, weight in zip(vin, _VIN_WEIGHTS, strict=True)
    )
    remainder = total % 11
    expected = "X" if remainder == 10 else str(remainder)
    return vin[8] == expected


def vin_model_years(value: object) -> tuple[int, int] | None:
    """Returns the two model years a 17-character VIN's 10th character can mean.

    The code repeats every 30 years, so "W" means 1998 or 2028; the stated
    year usually settles which. A stated year matching neither suggests a
    wrong year or a wrong VIN.

    Args:
        value: The VIN to decode.

    Returns:
        The earlier and later candidate years, or None if the VIN isn't 17
        characters or position 10 isn't a valid year code.
    """
    vin = normalize_vin(value)
    if len(vin) != _VIN_LENGTH or vin[9] not in _VIN_YEAR_CODES:
        return None
    first = _VIN_YEAR_CYCLE_START + _VIN_YEAR_CODES.index(vin[9])
    return first, first + _VIN_YEAR_CYCLE_LENGTH


def key_distance(a: str, b: str, limit: int) -> int:
    """Returns the edit distance between two keys, capped at `limit + 1`.

    Uses a banded Levenshtein computation, so comparing many keys stays fast
    when only small distances matter.

    Args:
        a: First key.
        b: Second key.
        limit: Largest distance of interest; anything larger returns
            `limit + 1`.
    """
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i] + [limit + 1] * len(b)
        low, high = max(1, i - limit), min(len(b), i + limit)
        for j in range(low, high + 1):
            cost = 0 if char_a == b[j - 1] else 1
            current[j] = min(
                previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost
            )
        if min(current) > limit:
            return limit + 1
        previous = current
    return min(previous[len(b)], limit + 1)


def describe_key_difference(
    a: str, b: str, *, vin: bool = False, names: tuple[str, str] = ("left", "right")
) -> str:
    """Describes how two keys differ, for a reviewer.

    Equal-length keys list the differing positions ("position 17: '8' vs '9'").
    For VINs, check-digit results are appended when exactly one side passes,
    because that usually reveals which side has the typo.

    Args:
        a: Left key, normalized.
        b: Right key, normalized.
        vin: Whether the keys are road-vehicle VINs. Leave False for equipment
            serials, which don't use the VIN check digit.
        names: How to refer to the two sources in the text, e.g.
            ("policy", "list").

    Returns:
        A short description such as "position 6: '5' vs '3'; policy check digit
        valid, list invalid".
    """
    if len(a) == len(b):
        positions = [
            f"position {index}: {char_a!r} vs {char_b!r}"
            for index, (char_a, char_b) in enumerate(zip(a, b, strict=True), start=1)
            if char_a != char_b
        ]
        detail = (
            "; ".join(positions)
            if len(positions) <= _MAX_LISTED_POSITIONS
            else f"{len(positions)} of {len(a)} characters differ"
        )
    else:
        detail = f"lengths differ ({len(a)} vs {len(b)} characters)"
    if not vin:
        return detail
    left_ok, right_ok = vin_check_digit_ok(a), vin_check_digit_ok(b)
    if left_ok is not None and right_ok is not None and left_ok != right_ok:
        verdict = {True: "valid", False: "invalid"}
        left_name, right_name = names
        left_verdict, right_verdict = verdict[left_ok], verdict[right_ok]
        detail += (
            f"; {left_name} check digit {left_verdict}, {right_name} {right_verdict}"
        )
    return detail


def reconcile(
    left: Sequence[Record],
    right: Sequence[Record],
    *,
    primary: Callable[[Record], object],
    secondary: Callable[[Record], Sequence[object] | None] | None = None,
    primary_label: str = "key",
    secondary_label: str = "secondary key",
    max_key_distance: int = 2,
    min_key_length: int = 6,
    vin_keys: bool = False,
    source_names: tuple[str, str] = ("left", "right"),
) -> ReconcileResult:
    """Reconciles two record lists in deterministic tiers.

    Args:
        left: Records from the first source, typically the policy or carrier
            document.
        right: Records from the second source, typically the insured's or
            agency's list.
        primary: Returns a record's primary identifier; it is normalized with
            `normalize_key`. Wrap VINs in `normalize_vin`.
        secondary: Returns a tuple of fields identifying a record when the
            primary key is missing or wrong, or None to skip. Each part is
            normalized with `normalize_key`; tuples with a blank part are
            ignored.
        primary_label: Name of the primary key in method labels, e.g. "VIN".
        secondary_label: Name of the secondary key, e.g. "Year + Make + Unit".
        max_key_distance: Largest edit distance for near-miss review
            candidates.
        min_key_length: Shortest key eligible for near-miss comparison; very
            short keys are too similar by chance.
        vin_keys: Whether primary keys are road-vehicle VINs, which adds
            check-digit evidence to review notes.
        source_names: How review notes refer to the two sources, e.g.
            ("policy", "list"), so the notes can go straight into a workbook.

    Returns:
        Buckets in which every input record appears exactly once, in input
        order within each bucket.
    """
    left_keys = [normalize_key(primary(record)) for record in left]
    right_keys = [normalize_key(primary(record)) for record in right]
    open_left = dict.fromkeys(range(len(left)))  # Ordered set of unmatched indexes.
    open_right = dict.fromkeys(range(len(right)))
    matched: list[tuple[int, int, str, str]] = []
    review: list[tuple[int, int, str, str]] = []

    # Tier 1: exact primary key.
    right_index: dict[str, list[int]] = defaultdict(list)
    for j, key in enumerate(right_keys):
        if key:
            right_index[key].append(j)
    for i, key in enumerate(left_keys):
        candidates = (
            [j for j in right_index.get(key, []) if j in open_right] if key else []
        )
        if candidates:
            _take(open_left, open_right, i, candidates[0])
            matched.append((i, candidates[0], f"Exact {primary_label}", ""))

    # Tier 2: secondary key, only where it is unique on both sides. If both
    # records have primary keys and they disagree, a person has to decide.
    left_secondary = _secondary_keys(left, open_left, secondary)
    right_secondary = _secondary_keys(right, open_right, secondary)
    left_groups, right_groups = _group(left_secondary), _group(right_secondary)
    for combo, left_members in left_groups.items():
        right_members = right_groups.get(combo, [])
        if len(left_members) == 1 and len(right_members) == 1:
            i, j = left_members[0], right_members[0]
            _take(open_left, open_right, i, j)
            detail = _key_note(
                left_keys[i], right_keys[j], primary_label, vin_keys, source_names
            )
            conflict = bool(left_keys[i] and right_keys[j])
            bucket = review if conflict else matched
            bucket.append((i, j, secondary_label, detail))

    # Tier 3: keep as many one-to-one review candidates as possible.
    near: list[tuple[int, int, int]] = []
    for i in open_left:
        if len(left_keys[i]) < min_key_length:
            continue
        for j in open_right:
            if len(right_keys[j]) < min_key_length:
                continue
            distance = key_distance(left_keys[i], right_keys[j], max_key_distance)
            if 0 < distance <= max_key_distance:
                near.append((distance, i, j))
    for i, j in _assign_review_pairs(near):
        _take(open_left, open_right, i, j)
        detail = describe_key_difference(
            left_keys[i], right_keys[j], vin=vin_keys, names=source_names
        )
        review.append((i, j, f"Near {primary_label}", detail))

    # Tier 4: shared secondary key that wasn't unique enough to auto-match.
    for combo, left_members in left_groups.items():
        open_members = [j for j in right_groups.get(combo, []) if j in open_right]
        for i in (member for member in left_members if member in open_left):
            if not open_members:
                break
            j = open_members.pop(0)
            _take(open_left, open_right, i, j)
            detail = f"same {secondary_label}; " + (
                _key_note(
                    left_keys[i], right_keys[j], primary_label, vin_keys, source_names
                )
                or "no keys to compare"
            )
            review.append((i, j, f"Same {secondary_label}", detail))

    return ReconcileResult(
        matched=[
            Pair(left[i], right[j], method, detail)
            for i, j, method, detail in sorted(matched)
        ],
        review=[
            Pair(left[i], right[j], method, detail)
            for i, j, method, detail in sorted(review)
        ],
        left_only=[left[i] for i in open_left],
        right_only=[right[j] for j in open_right],
        left_duplicates=_duplicates(left, left_keys),
        right_duplicates=_duplicates(right, right_keys),
    )


def _assign_review_pairs(
    candidates: Sequence[tuple[int, int, int]],
) -> list[tuple[int, int]]:
    """Finds a maximum-cardinality assignment of near-key review candidates.

    Visit closer candidates first, but allow reassignment along an augmenting
    path so one early choice cannot discard another valid review pair. This
    chooses pairs for human review, not confirmed matches or a minimum-cost
    assignment.
    """
    neighbors: dict[int, list[int]] = defaultdict(list)
    for _, left, right in sorted(candidates):
        neighbors[left].append(right)
    left_matches: dict[int, int] = {}
    right_matches: dict[int, int] = {}
    for start in neighbors:
        pending = deque([start])
        parents: dict[int, int] = {}
        endpoint: int | None = None
        while pending and endpoint is None:
            left = pending.popleft()
            for right in neighbors[left]:
                if right in parents:
                    continue
                parents[right] = left
                if right not in right_matches:
                    endpoint = right
                    break
                pending.append(right_matches[right])
        # Reverse the alternating path to free a partner for this left record.
        while endpoint is not None:
            left = parents[endpoint]
            previous = left_matches.get(left)
            left_matches[left] = endpoint
            right_matches[endpoint] = left
            endpoint = previous
    return sorted(left_matches.items())


def _take(
    open_left: dict[int, None], open_right: dict[int, None], i: int, j: int
) -> None:
    """Removes a paired left and right index from the unmatched sets."""
    del open_left[i]
    del open_right[j]


def _secondary_keys(
    records: Sequence[Record],
    indexes: Mapping[int, None],
    secondary: Callable[[Record], Sequence[object] | None] | None,
) -> dict[int, SecondaryKey]:
    """Computes normalized secondary keys for unmatched records.

    Records whose secondary key is None or has a blank part are left out, so
    they can't match on incomplete information.
    """
    if secondary is None:
        return {}
    keys: dict[int, SecondaryKey] = {}
    for index in indexes:
        parts = secondary(records[index])
        if parts is None:
            continue
        normalized = tuple(normalize_key(part) for part in parts)
        if all(normalized):
            keys[index] = normalized
    return keys


def _group[K: Hashable](keys: Mapping[int, K]) -> dict[K, list[int]]:
    """Groups record indexes by key, preserving input order."""
    groups: dict[K, list[int]] = defaultdict(list)
    for index, key in keys.items():
        groups[key].append(index)
    return groups


def _key_note(
    left_key: str, right_key: str, label: str, vin: bool, names: tuple[str, str]
) -> str:
    """Notes how two records' primary keys compare after a secondary match."""
    if not left_key or not right_key:
        missing = names[0] if not left_key else names[1]
        return f"{label} missing on {missing}"
    if left_key == right_key:
        return ""
    difference = describe_key_difference(left_key, right_key, vin=vin, names=names)
    return f"{label} differs: {difference}"


def _duplicates(
    records: Sequence[Record], keys: Sequence[str]
) -> dict[str, list[Record]]:
    """Maps each primary key used by more than one record to those records."""
    groups: dict[str, list[Record]] = defaultdict(list)
    for record, key in zip(records, keys, strict=True):
        if key:
            groups[key].append(record)
    return {key: members for key, members in groups.items() if len(members) > 1}
