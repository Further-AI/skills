#!/usr/bin/env python3
"""Field-level scoring core for the accuracy skill.

Compares an AI-Generated step payload against its Ground Truth counterpart and
emits one *claim* per scored value. Claims aggregate additively all the way up:
a scalar contributes one claim, a list contributes one identity claim per item
in the union plus one aux claim per matched-item sub-field.

Everything tunable lives on `ScoringConfig` and is threaded through explicitly.
No function here reads a module global that a caller is expected to mutate —
callers build a config and pass it down.

See references/scoring.md for the semantics and references/config.md for the
knobs. stdlib only.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# --------------------------------------------------------------------------- #
# Defaults — domain-neutral. Anything domain-specific is opt-in config.        #
# --------------------------------------------------------------------------- #

# Path markers for identifier fields, where a partial value is a real error and
# so must be exempt from token-subset matching. Matched at a left word boundary
# inside the field path (see _marker_hit), so "id" hits "location_id" but not
# "guideline".
DEFAULT_IDENTIFIER_FIELDS: Tuple[str, ...] = (
    "id", "_id", "number", "code", "ssn", "ein", "tax_id", "account",
)

# Candidate identity keys for list-row matching, in priority order. Entries
# beginning with "*" are suffix patterns matched against the item's own keys.
DEFAULT_IDENTITY_CANDIDATE_KEYS: Tuple[str, ...] = (
    "id", "*_id", "uuid", "key",
    "number", "*_number", "code", "*_code",
    "name", "*_name", "title", "label",
    "address", "*_address", "email",
)

# Ordered preference for naming a list row in the UI (consumed by the renderer
# via meta["record_label_fields"]).
DEFAULT_RECORD_LABEL_FIELDS: Tuple[str, ...] = (
    "name", "*_name", "title", "label", "description",
    "id", "*_id", "number", "*_number", "code", "*_code", "address",
)

# Tokens uppercased whole when humanizing a field key into a label.
DEFAULT_ACRONYMS: Tuple[str, ...] = (
    "id", "ids", "url", "urls", "uri", "api", "vin", "ssn", "ein", "uw",
    "crm", "dba", "sku", "pdf", "csv", "json", "xml", "html", "zip",
)

# "Missing / not applicable" sentinels — all considered equivalent. Compared
# against normalize_text() output.
MISSING_TOKENS = frozenset({
    "", "n/a", "na", "none", "null", "not found", "not found in uploaded documents",
    "not found in uploaded documents.", "unknown", "not specified", "not applicable",
    "not provided", "not available",
})

# Address / unit abbreviation canonicalization. Maps full forms and variants to
# a single token so "Suite 200" == "Ste 200" == "Ste. 200". Applied only in the
# comparison path (normalize_compare), NOT in normalize_text, so the is_missing()
# sentinel set is left untouched.
ADDR_ABBREV = {
    "suite": "ste", "ste": "ste",
    "apartment": "apt", "apt": "apt",
    "building": "bldg", "bldg": "bldg",
    "floor": "fl", "flr": "fl",
    "room": "rm", "rm": "rm",
    "department": "dept", "dept": "dept",
    "street": "st", "str": "st",
    "avenue": "ave", "av": "ave", "ave": "ave",
    "boulevard": "blvd", "blvd": "blvd",
    "road": "rd", "rd": "rd",
    "drive": "dr", "dr": "dr",
    "lane": "ln", "ln": "ln",
    "court": "ct", "ct": "ct",
    "place": "pl", "pl": "pl",
    "parkway": "pkwy", "pkwy": "pkwy",
    "highway": "hwy", "hwy": "hwy",
    "fort": "ft", "ft": "ft",
    "mount": "mt", "mt": "mt",
}

# Connective words that must not, on their own, bridge two values in the
# token-subset match (so "of the" can't make unrelated values "match").
_SUBSET_STOPWORDS = frozenset({"of", "the", "and", "for", "a", "an", "to", "in", "on", "or"})

# Extraction-metadata keys that mark a dict as a value cell rather than a record.
_CELL_META_KEYS = ("confidence_score", "confidence_reason", "citations", "thinking_steps")


# --------------------------------------------------------------------------- #
# Config                                                                       #
# --------------------------------------------------------------------------- #

@dataclass
class ScoringConfig:
    """Every knob the scorer reads. Build one, thread it down.

    step_aliases        {raw step name: canonical name}, applied to BOTH sides
                        before step matching.
    exclude_steps       step names dropped from both sides (post-alias).
    exclude_fields      {(step, field label)} dropped from the claim stream
                        before any aggregation.
    intersect_keys_only score only dict keys present on both sides. Default
                        True: one-sided keys are dataset-shape noise, not
                        extraction errors (see references/scoring.md).
    identifier_fields   path markers where token-subset matching is suppressed.
    synonyms            {path marker: {from: to}} value equivalences, gated to
                        paths containing the marker. Empty by default; the
                        insurance preset ships in references/insurance_synonyms.json.
    drop_empty_rows     {step: [field, ...]} — drop list rows where all listed
                        fields are blank/zero, on both sides.
    long_text_min_chars values this long or longer are skipped, not scored.
    address_normalization  apply ADDR_ABBREV in the comparison path.
    record_label_fields ordered preference for naming a list row in the UI.
    identity_candidate_keys  ordered candidates for list-row identity matching;
                        "*_suffix" entries are patterns.
    identity_overrides  {step or dotted path: field} forcing the identity key.
    auto_identity_keys  when no candidate fits, auto-detect a viable identity
                        key from the data before falling back to a deep hash.
    acronyms            tokens uppercased whole when humanizing a field key.
    field_labels        {field key: display label} explicit label overrides.
    """

    step_aliases: Dict[str, str] = field(default_factory=dict)
    exclude_steps: Set[str] = field(default_factory=set)
    exclude_fields: Set[Tuple[str, str]] = field(default_factory=set)
    intersect_keys_only: bool = True
    identifier_fields: Tuple[str, ...] = DEFAULT_IDENTIFIER_FIELDS
    synonyms: Dict[str, Dict[str, str]] = field(default_factory=dict)
    drop_empty_rows: Dict[str, List[str]] = field(default_factory=dict)
    long_text_min_chars: int = 40
    address_normalization: bool = True
    record_label_fields: Tuple[str, ...] = DEFAULT_RECORD_LABEL_FIELDS
    identity_candidate_keys: Tuple[str, ...] = DEFAULT_IDENTITY_CANDIDATE_KEYS
    identity_overrides: Dict[str, str] = field(default_factory=dict)
    auto_identity_keys: bool = True
    acronyms: Tuple[str, ...] = DEFAULT_ACRONYMS
    field_labels: Dict[str, str] = field(default_factory=dict)

    # -- normalization of user-supplied values ------------------------------ #
    def __post_init__(self) -> None:
        self.exclude_steps = set(self.exclude_steps or ())
        self.exclude_fields = {tuple(p) for p in (self.exclude_fields or ())}
        self.identifier_fields = tuple(self.identifier_fields or ())
        self.record_label_fields = tuple(self.record_label_fields or ())
        self.identity_candidate_keys = tuple(self.identity_candidate_keys or ())
        self.acronyms = tuple(a.lower() for a in (self.acronyms or ()))
        # Keys beginning with "_" are comments (JSON has none) and are ignored.
        self.synonyms = {
            str(marker).lower(): {str(k).lower(): str(v).lower() for k, v in (table or {}).items()}
            for marker, table in (self.synonyms or {}).items()
            if not str(marker).startswith("_")
        }
        self.drop_empty_rows = {k: list(v) for k, v in (self.drop_empty_rows or {}).items()}
        self._synonym_markers = tuple(self.synonyms)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "ScoringConfig":
        data = dict(data or {})
        excl = data.get("exclude_fields")
        if excl:
            data["exclude_fields"] = {tuple(p) for p in excl}
        known = {f for f in cls.__dataclass_fields__}  # noqa: SLF001
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(
                f"unknown config key(s): {', '.join(unknown)}. Known: {', '.join(sorted(known))}"
            )
        return cls(**data)

    def to_dict(self) -> dict:
        out = asdict(self)
        out["exclude_steps"] = sorted(self.exclude_steps)
        out["exclude_fields"] = sorted([list(p) for p in self.exclude_fields])
        out["identifier_fields"] = list(self.identifier_fields)
        out["record_label_fields"] = list(self.record_label_fields)
        out["identity_candidate_keys"] = list(self.identity_candidate_keys)
        out["acronyms"] = list(self.acronyms)
        return out


DEFAULT_CONFIG = ScoringConfig()


# --------------------------------------------------------------------------- #
# Path marker matching                                                         #
# --------------------------------------------------------------------------- #

def _marker_hit(path: Optional[str], markers: Sequence[str]) -> bool:
    """True when any marker appears in `path` at a left word boundary.

    The boundary matters: a bare "id" marker must hit "location_id" and
    "id" but not "guideline". Suffixed forms still hit ("order_number" in
    "legacy_order_numbers"), which is the intent — the field is still an
    identifier.
    """
    p = (path or "").lower()
    if not p:
        return False
    for marker in markers:
        m = str(marker).strip().lower().lstrip("_")
        if not m:
            continue
        start = 0
        while True:
            i = p.find(m, start)
            if i < 0:
                break
            if i == 0 or not p[i - 1].isalnum():
                return True
            start = i + 1
    return False


def _is_identifier_path(path: Optional[str], config: ScoringConfig) -> bool:
    return _marker_hit(path, config.identifier_fields)


def _synonym_table(path: Optional[str], config: ScoringConfig) -> Optional[Dict[str, str]]:
    """The synonym table whose path marker matches this path, if any."""
    if not config.synonyms:
        return None
    p = (path or "").lower()
    for marker, table in config.synonyms.items():
        if marker in p:
            return table
    return None


# --------------------------------------------------------------------------- #
# Value normalization                                                          #
# --------------------------------------------------------------------------- #

def normalize_text(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip().lower()
    s = re.sub(r"\s+", " ", s)      # collapse whitespace
    s = s.strip("\"'")              # strip surrounding quotes
    s = s.rstrip(".")               # trailing period is noise
    return s


def canon_tokens(s: str, config: ScoringConfig = DEFAULT_CONFIG) -> List[str]:
    """Lowercase alphanumeric tokens of an already-normalized string, with
    address/unit abbreviations canonicalized. Punctuation is dropped, so
    'Ste.', 'Ste', and 'Suite' all yield the token 'ste'."""
    toks = re.findall(r"[a-z0-9]+", s)
    if not config.address_normalization:
        return toks
    return [ADDR_ABBREV.get(t, t) for t in toks]


def normalize_compare(v: Any, config: ScoringConfig = DEFAULT_CONFIG) -> str:
    """normalize_text + abbreviation canonicalization, for value comparison."""
    return " ".join(canon_tokens(normalize_text(v), config))


def is_missing(v: Any) -> bool:
    return normalize_text(v) in MISSING_TOKENS


def is_zero(v: Any) -> bool:
    """True if v is a numeric zero (0, 0.0, "0", "$0", "0.00"). Booleans are
    excluded so that False is not mistaken for a zero quantity."""
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return abs(v) < 1e-9
    if isinstance(v, str):
        s = re.sub(r"[$,]", "", v.strip())
        try:
            return abs(float(s)) < 1e-9
        except ValueError:
            return False
    return False


def is_blank_or_zero(v: Any) -> bool:
    """Treat a blank/null/"not found" sentinel and a numeric zero as the same
    thing: null == 0 == 0.0. An extracted 0 and an unreported field count as a
    match (and vice versa)."""
    return is_missing(v) or is_zero(v)


# Calendar-date parsing. All separators (",", "/", "-", ".") collapse to a
# single space first, so one entry covers a whole family of renderings
# (e.g. "%m %d %Y" matches 12/30/2025, 12-30-2025 and 12.30.2025).
_DATE_FORMATS = (
    "%B %d %Y", "%b %d %Y",   # december 30 2025 / dec 30 2025
    "%d %B %Y", "%d %b %Y",   # 30 december 2025 / 30 dec 2025
    "%m %d %Y", "%m %d %y",   # 12 30 2025 / 12 30 25  (covers / - . separators)
    "%Y %m %d",               # 2025 12 30  (covers 2025-12-30, 2025/12/30)
)


def _parse_date(v: Any) -> Optional[Tuple[int, int, int]]:
    """Parse a value that is ENTIRELY a calendar date into (year, month, day),
    so different renderings of the same day compare equal ("December 30, 2025"
    / "Dec 30, 2025" / "12/30/2025" / "2025-12-30").

    Returns None when the whole string is not a recognizable full date, so
    non-dates — and partial dates like a bare year or "December 2025" — fall
    through to the normal number/text/subset path. Only fires as a match when
    BOTH sides parse, so the parser can't bridge a date to a non-date.
    """
    if v is None or isinstance(v, bool):
        return None
    s = str(v).strip().lower()
    if not s:
        return None
    s = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", s)   # 30th -> 30
    s = re.sub(r"[,/.\-]", " ", s)                 # unify separators to space
    s = re.sub(r"\s+", " ", s).strip()
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
        return (dt.year, dt.month, dt.day)
    return None


def _to_num(x: Any) -> Optional[float]:
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return float(x)
    if isinstance(x, bool):
        return float(x)
    if isinstance(x, str):
        s = re.sub(r"[$,]", "", x.strip())
        try:
            return float(s)
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------- #
# Leaf comparison                                                              #
# --------------------------------------------------------------------------- #

def compare_values(gv: Any, tv: Any, path: str,
                   config: ScoringConfig = DEFAULT_CONFIG) -> Tuple[Optional[bool], str, Any]:
    """Return (is_match, leaf_kind, detail) for a leaf-ish comparison.

    is_match is True / False / None, where None means "not scored" (long text).
    Decision order matters; the first hit wins. See references/scoring.md.
    """
    # null == 0: if both sides are blank-or-zero (e.g. gen 0 vs gt "Not Found"),
    # it's a match.
    if is_blank_or_zero(gv) and is_blank_or_zero(tv):
        return True, "missing-match", None
    # Exactly one side is a genuine blank/null and the other is a real value.
    if is_missing(gv) or is_missing(tv):
        return False, "missing-mismatch", (gv, tv)

    # Calendar-date equivalence. Only fires when BOTH sides parse as a full
    # date; two genuinely different dates are a decisive mismatch, so the
    # token-subset rule below can't bridge "Dec 30 2025" and "Dec 31 2025".
    d1, d2 = _parse_date(gv), _parse_date(tv)
    if d1 and d2:
        if d1 == d2:
            return True, "date", None
        return False, "mismatch", (gv, tv)

    # Numeric comparison if both look numeric. Decisive either way.
    n1, n2 = _to_num(gv), _to_num(tv)
    if n1 is not None and n2 is not None:
        if abs(n1 - n2) < 1e-6 or (abs(n1) > 0 and abs(n1 - n2) / max(abs(n1), abs(n2)) < 0.005):
            return True, "numeric", None
        return False, "mismatch", (gv, tv)

    s1 = normalize_text(gv)
    s2 = normalize_text(tv)

    # Long text is not scored — in BOTH directions. Fuzzy matching of long free
    # text proved unreliable, so it stays disabled. Crediting only the exact
    # long-text matches while dropping the mismatches was a one-way upward bias:
    # a long field could score correct but never wrong. Checking length BEFORE
    # the exact test makes the skip symmetric.
    if max(len(s1), len(s2)) >= config.long_text_min_chars:
        return None, "skipped-long-text", (gv, tv)

    if s1 == s2:
        return True, "exact", None

    # Abbreviation-normalized equality: "Suite 200" == "Ste 200" == "Ste. 200",
    # "Main Street" == "Main St". Strict equality after canonicalization.
    c1 = normalize_compare(gv, config)
    c2 = normalize_compare(tv, config)
    if c1 and c1 == c2:
        return True, "normalized", None

    # Configured synonyms, gated to paths containing the marker so an acronym
    # map can't bridge unrelated short values elsewhere. Acronyms do not
    # token-subset-match their expansions, so this is the only thing that
    # equates them.
    table = _synonym_table(path, config)
    if table:
        l1, l2 = table.get(s1, s1), table.get(s2, s2)
        if l1 and l1 == l2:
            return True, "synonym", None

    # Token-subset match for short categorical values: one side's token set is
    # fully contained in the other, so a refinement equals its generalization
    # ("Commercial" is a subset of "Umbrella - Commercial"). Generalizes without
    # hardcoding any vocabulary. Stopword-only overlaps are excluded. NOT
    # applied to identifier fields, where a partial value is a genuine error.
    if not _is_identifier_path(path, config):
        t1 = set(canon_tokens(s1, config)) - _SUBSET_STOPWORDS
        t2 = set(canon_tokens(s2, config)) - _SUBSET_STOPWORDS
        if t1 and t2 and (t1 <= t2 or t2 <= t1):
            return True, "subset-match", None

    return False, "mismatch", (gv, tv)


# --------------------------------------------------------------------------- #
# Envelope unwrapping                                                          #
# --------------------------------------------------------------------------- #

def unwrap_envelope(v: Any) -> Any:
    """Unwrap a value-cell dict to its inner value.

    Handles the compact envelopes ({value, confidence} / {value}) and the full
    table-cell shape emitted by agentic extraction ({value, confidence_score,
    confidence_reason, citations, thinking_steps}), including cells nested
    inside arrays. Mirrors the backend's cell heuristic: a dict with 'value'
    plus any extraction-metadata key is a cell.
    """
    if isinstance(v, dict) and "value" in v:
        keys = set(v.keys())
        if keys == {"value", "confidence"} or keys == {"value"}:
            return v["value"]
        if any(k in v for k in _CELL_META_KEYS):
            return v["value"]
    return v


def deep_unwrap(v: Any) -> Any:
    """Recursively unwrap value envelopes anywhere in a structure. AI-Generated
    data wraps every leaf; Ground Truth carries bare values, so both sides are
    stripped before identity matching."""
    v = unwrap_envelope(v)
    if isinstance(v, dict):
        return {k: deep_unwrap(val) for k, val in v.items()}
    if isinstance(v, list):
        return [deep_unwrap(x) for x in v]
    return v


# --------------------------------------------------------------------------- #
# List identity matching                                                       #
# --------------------------------------------------------------------------- #

def _safe_path_token(s: Any) -> str:
    """Strip characters that would break the bracket-stripping regex later."""
    return str(s).replace("]", "_").replace("[", "_")


def _identity_key(val: Any, config: ScoringConfig = DEFAULT_CONFIG) -> str:
    """Path-safe identity key for set-based list matching.

    Strings are normalized the way compare_values normalizes text, so a
    formatting-only difference in the identity field — "123 Main St" vs
    "123 Main Street" — keys to the SAME bucket and strict-matches as one item
    instead of splitting into an FP + FN pair. Non-strings keep their JSON
    identity. Empty normalization falls back to the raw value so a value never
    collapses to "".
    """
    if isinstance(val, str):
        return _safe_path_token(normalize_compare(val, config) or val)
    return _safe_path_token(json.dumps(val, default=str))


def _deep_eq_hash(item: Any) -> str:
    s = json.dumps(deep_unwrap(item), sort_keys=True, default=str)
    return "deep_" + hashlib.sha1(s.encode()).hexdigest()[:10]


def _identity_value(d: dict, cand: str) -> Any:
    """Pull the candidate identity value from an item, unwrapping envelopes."""
    return unwrap_envelope(d.get(cand))


def _field_overlap(g_item: Any, t_item: Any, config: ScoringConfig) -> int:
    """Count how many top-level fields agree between two dict items."""
    if not (isinstance(g_item, dict) and isinstance(t_item, dict)):
        return 0
    matches = 0
    for k in set(g_item.keys()) & set(t_item.keys()):
        ok, _kind, _detail = compare_values(
            unwrap_envelope(g_item.get(k)),
            unwrap_envelope(t_item.get(k)),
            k,
            config,
        )
        if ok:
            matches += 1
    return matches


def _greedy_soft_pair(unmatched_gen: dict, unmatched_gt: dict,
                      config: ScoringConfig = DEFAULT_CONFIG,
                      min_match_count: int = 2, min_match_ratio: float = 0.4):
    """Greedily pair remaining items by field overlap.

    Returns (pairs, still_unmatched_gen, still_unmatched_gt) where pairs is a
    list of (gen_key, gen_item, gt_key, gt_item). A pair must have at least
    `min_match_count` agreeing fields OR `min_match_ratio` of the wider item's
    field count — whichever is lower — to be accepted.

    Items that aren't dicts are not soft-paired (no fields to overlap on).
    """
    if not unmatched_gen or not unmatched_gt:
        return [], unmatched_gen, unmatched_gt
    sample = next(iter(unmatched_gen.values()))
    if not isinstance(sample, dict):
        return [], unmatched_gen, unmatched_gt

    gen_items = list(unmatched_gen.items())
    gt_items = list(unmatched_gt.items())

    candidates = []  # (score, gi, ti)
    for gi, (_gk, gv) in enumerate(gen_items):
        for ti, (_tk, tv) in enumerate(gt_items):
            sc = _field_overlap(gv, tv, config)
            if sc > 0:
                candidates.append((sc, gi, ti))
    candidates.sort(key=lambda x: -x[0])

    pairs = []
    used_g: Set[int] = set()
    used_t: Set[int] = set()
    for sc, gi, ti in candidates:
        if gi in used_g or ti in used_t:
            continue
        gk, gv = gen_items[gi]
        tk, tv = gt_items[ti]
        max_fields = max(len(gv) if isinstance(gv, dict) else 1,
                         len(tv) if isinstance(tv, dict) else 1)
        if sc < min_match_count and sc < min_match_ratio * max_fields:
            continue
        pairs.append((gk, gv, tk, tv))
        used_g.add(gi)
        used_t.add(ti)

    still_gen = {k: v for i, (k, v) in enumerate(gen_items) if i not in used_g}
    still_gt = {k: v for i, (k, v) in enumerate(gt_items) if i not in used_t}
    return pairs, still_gen, still_gt


def _expand_candidates(item_keys: Set[str], config: ScoringConfig) -> List[str]:
    """Resolve the configured candidate patterns against the keys actually
    present. "*_id" matches any key ending in "_id"; matches within one pattern
    are sorted so the choice is deterministic."""
    out: List[str] = []
    seen: Set[str] = set()
    for pat in config.identity_candidate_keys:
        if pat.startswith("*"):
            suffix = pat[1:]
            hits = sorted(k for k in item_keys if k.endswith(suffix) and k not in seen)
        else:
            hits = [pat] if pat in item_keys and pat not in seen else []
        for k in hits:
            out.append(k)
            seen.add(k)
    return out


def _viable_identity_key(cand: str, gen_list: list, gt_list: list,
                         config: ScoringConfig) -> Optional[Tuple[float, float]]:
    """Return (gen uniqueness, gt uniqueness) when `cand` can serve as the
    identity key: present and non-empty on EVERY item of both sides, and unique
    on at least one populated side. None otherwise."""
    items = list(gen_list) + list(gt_list)
    if not all(isinstance(d, dict) and cand in d
               and _identity_value(d, cand) not in (None, "") for d in items):
        return None
    gen_vals = [_identity_key(_identity_value(d, cand), config) for d in gen_list]
    gt_vals = [_identity_key(_identity_value(d, cand), config) for d in gt_list]
    gu = (len(set(gen_vals)) / len(gen_vals)) if gen_vals else 0.0
    tu = (len(set(gt_vals)) / len(gt_vals)) if gt_vals else 0.0
    if not ((gen_vals and gu == 1.0) or (gt_vals and tu == 1.0)):
        return None
    return (gu, tu)


def _override_key(path: Optional[str], config: ScoringConfig) -> Optional[str]:
    """Identity key forced by config for this path, by exact dotted path first,
    then by step (the leading path segment)."""
    if not config.identity_overrides:
        return None
    clean = re.sub(r"\[[^\]]*\]", "", path or "")
    parts = [p for p in clean.split(".") if p]
    for probe in (clean, parts[0] if parts else ""):
        if probe and probe in config.identity_overrides:
            return config.identity_overrides[probe]
    return None


def build_identity_maps(gen_list: Any, gt_list: Any,
                        config: ScoringConfig = DEFAULT_CONFIG,
                        path: str = ""):
    """Return (gen_map, gt_map, key_used) using the best available identity key.

    Map keys are PATH-safe (no "[" or "]"), so paths built as f"{prefix}[{key}]"
    stay parseable by the bracket-stripping regex. Order of preference:
    config override, configured candidates, auto-detection, deep-equality hash.
    """
    if not isinstance(gen_list, list) or not isinstance(gt_list, list):
        return None, None, None
    sample = (gen_list[0] if gen_list else None) or (gt_list[0] if gt_list else None)
    if sample is None:
        return {}, {}, "<empty>"

    # Scalar items: the value itself (unwrapped, normalized, path-safe) is the
    # identity.
    if not isinstance(sample, dict):
        def _scalar_key(x: Any) -> str:
            return _identity_key(unwrap_envelope(x), config)
        gm: Dict[str, Any] = {}
        tm: Dict[str, Any] = {}
        for x in gen_list:
            gm.setdefault(_scalar_key(x), x)
        for x in gt_list:
            tm.setdefault(_scalar_key(x), x)
        return gm, tm, "<value>"

    def _maps(cand: str):
        return ({_identity_key(_identity_value(d, cand), config): d for d in gen_list},
                {_identity_key(_identity_value(d, cand), config): d for d in gt_list},
                cand)

    # 1. Explicit override — honored whenever the key is usable at all.
    forced = _override_key(path, config)
    if forced and all(isinstance(d, dict) and forced in d for d in list(gen_list) + list(gt_list)):
        return _maps(forced)

    item_keys: Set[str] = set()
    for d in list(gen_list) + list(gt_list):
        if isinstance(d, dict):
            item_keys.update(d.keys())

    # 2. Configured candidates, in priority order.
    for cand in _expand_candidates(item_keys, config):
        if _viable_identity_key(cand, gen_list, gt_list, config):
            return _maps(cand)

    # 3. Auto-detect: any key present on every item and unique on one side is
    #    viable; rank by uniqueness, then by name for determinism.
    if config.auto_identity_keys:
        scored = []
        for cand in sorted(item_keys):
            uniq = _viable_identity_key(cand, gen_list, gt_list, config)
            if uniq:
                scored.append((-(uniq[0] + uniq[1]), cand))
        if scored:
            scored.sort()
            return _maps(scored[0][1])

    # 4. Fallback: deep equality via SHA1 hash (always path-safe).
    return ({_deep_eq_hash(d): d for d in gen_list},
            {_deep_eq_hash(d): d for d in gt_list},
            "<deep-eq>")


# --------------------------------------------------------------------------- #
# Structural walk                                                              #
# --------------------------------------------------------------------------- #

def walk_compare_v3(gen: Any, gt: Any, path_prefix: str = "",
                    config: ScoringConfig = DEFAULT_CONFIG) -> Iterable[dict]:
    """Yield claims: {path, kind, match, category, gen, gt, leaf_kind}.

    kind      'scalar' (top-level value) | 'identity' (list item presence)
              | 'aux' (sub-field inside a matched list item)
    category  'TP' / 'FP' / 'FN' on identity claims only, else None
    match     True / False / None (None = skipped, currently only long text)
    """
    gen = unwrap_envelope(gen)
    gt = unwrap_envelope(gt)

    # Both dicts -> recurse into the union of keys (the intersection when
    # intersect_keys_only; one-sided keys then yield no claims at all).
    if isinstance(gen, dict) and isinstance(gt, dict):
        if config.intersect_keys_only:
            keys = set(gen.keys()) & set(gt.keys())
        else:
            keys = set(gen.keys()) | set(gt.keys())
        for k in sorted(keys):
            sub = f"{path_prefix}.{k}" if path_prefix else k
            if k in gen and k in gt:
                yield from walk_compare_v3(gen[k], gt[k], sub, config)
            elif k in gen:
                # null == 0: a blank or bare 0 against an absent GT key isn't an error.
                if not is_blank_or_zero(gen[k]):
                    yield {"path": sub, "kind": "scalar", "match": False,
                           "category": None, "gen": gen[k], "gt": None,
                           "leaf_kind": "extra-in-gen"}
            else:
                if not is_blank_or_zero(gt[k]):
                    yield {"path": sub, "kind": "scalar", "match": False,
                           "category": None, "gen": None, "gt": gt[k],
                           "leaf_kind": "missing-in-gen"}
        return

    # Both lists -> set-based identity comparison, with a soft-pair fallback so
    # rows that almost match still get value-by-value credit.
    if isinstance(gen, list) and isinstance(gt, list):
        if not gen and not gt:
            return
        gm, tm, _key_used = build_identity_maps(gen, gt, config, path_prefix)
        if gm is None:
            return

        # Strict identity matches first.
        strict_keys = set(gm.keys()) & set(tm.keys())
        for k in sorted(strict_keys):
            item_path = f"{path_prefix}[{k}]"
            g_item, t_item = gm[k], tm[k]
            yield {"path": item_path, "kind": "identity", "match": True,
                   "category": "TP", "gen": g_item, "gt": t_item,
                   "leaf_kind": "identity-match"}
            if isinstance(g_item, dict) and isinstance(t_item, dict):
                for child in walk_compare_v3(g_item, t_item, item_path, config):
                    if child["kind"] == "scalar":
                        child["kind"] = "aux"
                    yield child

        # Soft-pair the rest by field overlap. Soft pairs emit aux claims (each
        # agreeing field is a correct claim) but NO identity claim — so P/R/F1
        # still reflect strict-identity correctness while accuracy gets full
        # value-by-value partial credit.
        remaining_gen = {k: v for k, v in gm.items() if k not in strict_keys}
        remaining_gt = {k: v for k, v in tm.items() if k not in strict_keys}
        soft_pairs, true_fp, true_fn = _greedy_soft_pair(remaining_gen, remaining_gt, config)

        for gk, gv, tk, tv in soft_pairs:
            # Composite path so strict and soft pairs can't collide.
            item_path = f"{path_prefix}[{gk}~{tk}]"
            if isinstance(gv, dict) and isinstance(tv, dict):
                for child in walk_compare_v3(gv, tv, item_path, config):
                    if child["kind"] == "scalar":
                        child["kind"] = "aux"
                    yield child

        # Truly unpaired items: identity FP / FN with no aux claims.
        for k in sorted(true_fp.keys()):
            yield {"path": f"{path_prefix}[{k}]", "kind": "identity", "match": False,
                   "category": "FP", "gen": true_fp[k], "gt": None,
                   "leaf_kind": "extra-in-gen"}
        for k in sorted(true_fn.keys()):
            yield {"path": f"{path_prefix}[{k}]", "kind": "identity", "match": False,
                   "category": "FN", "gen": None, "gt": true_fn[k],
                   "leaf_kind": "missing-in-gen"}
        return

    # Type mismatch (one list / one dict / one scalar), except numeric vs
    # string-numeric which is allowed through to the normal leaf path.
    if type(gen) is not type(gt) and not (
        (isinstance(gen, (int, float)) and isinstance(gt, (int, float)))
        or (isinstance(gen, str) and isinstance(gt, (int, float)))
        or (isinstance(gt, str) and isinstance(gen, (int, float)))
    ):
        match, leaf_kind, _detail = compare_values(gen, gt, path_prefix, config)
        yield {"path": path_prefix, "kind": "scalar", "match": match,
               "category": None, "gen": gen, "gt": gt, "leaf_kind": leaf_kind}
        return

    # Scalar leaf
    match, leaf_kind, _detail = compare_values(gen, gt, path_prefix, config)
    yield {"path": path_prefix, "kind": "scalar", "match": match,
           "category": None, "gen": gen, "gt": gt, "leaf_kind": leaf_kind}


# --------------------------------------------------------------------------- #
# Labels                                                                       #
# --------------------------------------------------------------------------- #

def humanize(key: str, config: ScoringConfig = DEFAULT_CONFIG) -> str:
    if key in config.field_labels:
        return config.field_labels[key]
    out = re.sub(r"[_\.]+", " ", key).strip()
    parts = []
    for w in out.split():
        if w.lower() in config.acronyms:
            parts.append(w.upper())
        elif w.isupper() and len(w) <= 4:
            parts.append(w)
        else:
            parts.append(w[:1].upper() + w[1:])
    return " ".join(parts)


def field_label(key: str, config: ScoringConfig = DEFAULT_CONFIG) -> str:
    if not key:
        return "(items)"
    return humanize(key, config)


def field_root_v3(path: str) -> Tuple[str, str]:
    """Strip array indices / identity segments and return (step, field_key).

    field_key is the grouping unit on the lens chart:
      - top-level dict step field           -> the first sub-key
      - nested list inside a dict step      -> the list's name
        ('Contacts.phones[X].label' -> ('Contacts', 'phones'))
      - top-level list step items           -> '' (the '(items)' bucket)
      - aux field inside a top-level list   -> the sub-field name
        ('Line Items[X].sku' -> ('Line Items', 'sku'))
    """
    clean = re.sub(r"\[[^\]]*\]", "", path)
    parts = [p for p in clean.split(".") if p]
    if not parts:
        return ("", "")
    step = parts[0]
    if len(parts) == 1:
        return (step, "")
    return (step, parts[1])


# --------------------------------------------------------------------------- #
# Row filtering                                                                #
# --------------------------------------------------------------------------- #

def _drop_empty_rows(value: Any, fields: Sequence[str]) -> Any:
    """Remove list rows where every listed field is blank or zero.

    A row that carries none of the listed fields is kept untouched. The original
    (possibly cell-wrapped) row object is preserved so downstream comparison is
    unchanged. Rationale: a row whose quantity columns are all empty is not a
    real row, and the two sides disagreeing on which empty rows to enumerate
    produces spurious FN + FP pairs.
    """
    if not isinstance(value, list) or not fields:
        return value
    kept = []
    for row in value:
        r = deep_unwrap(row)
        if isinstance(r, dict) and any(f in r for f in fields):
            if all(is_blank_or_zero(r.get(f)) for f in fields):
                continue
        kept.append(row)
    return kept


# --------------------------------------------------------------------------- #
# Per-sample collection                                                        #
# --------------------------------------------------------------------------- #

def collect_v3(sample: dict, config: ScoringConfig = DEFAULT_CONFIG) -> dict:
    """Score one sample.

    `sample` is {"submission_id", "submission_name", "generated": {"steps": {...}},
    "ground_truth": {"steps": {...}}, optional "gen_file"/"gt_file"} — the shape
    studio_fetch.fetch_dataset() and the --from-dir loader both produce.

    Returns a bundle: {submission_id, submission_name, gen_file, gt_file,
    claims, only_in_generated, only_in_gt}. Steps present on one side only
    contribute NO claims; they are reported in the only_in_* lists.
    """
    gen_steps = dict(((sample.get("generated") or {}).get("steps")) or {})
    gt_steps = dict(((sample.get("ground_truth") or {}).get("steps")) or {})

    # Canonicalize step names on BOTH sides, so a variant label that doesn't
    # correlate between the two sides still compares whenever the step is
    # present on both. A one-directional rename silently breaks the samples
    # where both sides already agreed.
    aliases = config.step_aliases
    gen_steps = {aliases.get(k, k): v for k, v in gen_steps.items()}
    gt_steps = {aliases.get(k, k): v for k, v in gt_steps.items()}

    # Drop excluded steps from both sides (post-alias).
    gen_steps = {k: v for k, v in gen_steps.items() if k not in config.exclude_steps}
    gt_steps = {k: v for k, v in gt_steps.items() if k not in config.exclude_steps}

    # Drop all-blank rows from both sides for the configured steps.
    for step, fields in config.drop_empty_rows.items():
        if step in gen_steps:
            gen_steps[step] = _drop_empty_rows(gen_steps[step], fields)
        if step in gt_steps:
            gt_steps[step] = _drop_empty_rows(gt_steps[step], fields)

    shared = sorted(set(gen_steps) & set(gt_steps))
    only_gen = sorted(set(gen_steps) - set(gt_steps))
    only_gt = sorted(set(gt_steps) - set(gen_steps))

    excluded = config.exclude_fields
    claims = []
    for step in shared:
        for c in walk_compare_v3(gen_steps[step], gt_steps[step], step, config):
            block, key = field_root_v3(c["path"])
            c["block"] = block
            c["field_key"] = key
            c["field_label"] = field_label(key, config)
            if excluded and (block, c["field_label"]) in excluded:
                continue
            claims.append(c)

    return {
        "submission_id": sample.get("submission_id", ""),
        "submission_name": sample.get("submission_name") or sample.get("submission_id") or "(unnamed)",
        "gen_file": sample.get("gen_file", ""),
        "gt_file": sample.get("gt_file", ""),
        "claims": claims,
        "only_in_generated": only_gen,
        "only_in_gt": only_gt,
    }


# --------------------------------------------------------------------------- #
# Aggregation                                                                  #
# --------------------------------------------------------------------------- #

def aggregate(claims: Sequence[dict]) -> dict:
    """Roll a list of claims up into the four metrics.

    Skipped claims (match is None) are excluded from every count. Precision /
    recall / F1 come from identity claims only, so a scope with no list rows
    reports None rather than a synthesized value.
    """
    total = correct = 0
    tp = fp = fn = 0
    skipped = 0
    for c in claims:
        if c["match"] is None:
            skipped += 1
            continue
        total += 1
        if c["match"]:
            correct += 1
        if c["category"] == "TP":
            tp += 1
        elif c["category"] == "FP":
            fp += 1
        elif c["category"] == "FN":
            fn += 1
    return {
        "total": total, "correct": correct,
        "tp": tp, "fp": fp, "fn": fn,
        "skipped": skipped,
        "accuracy": (correct / total) if total else None,
        "precision": (tp / (tp + fp)) if (tp + fp) else None,
        "recall": (tp / (tp + fn)) if (tp + fn) else None,
        "f1": (2 * tp / (2 * tp + fp + fn)) if (2 * tp + fp + fn) else None,
    }


def metric_or_none(stat: dict, name: str) -> Optional[float]:
    """A JSON-friendly metric: percentage 0-100, or None."""
    v = stat.get(name)
    return None if v is None else v * 100


def aggregate_payload(claims: Sequence[dict]) -> dict:
    """As aggregate() but JSON-safe, with percentages on 0-100."""
    a = aggregate(claims)
    return {
        "total": a["total"], "correct": a["correct"],
        "tp": a["tp"], "fp": a["fp"], "fn": a["fn"],
        "accuracy": metric_or_none(a, "accuracy"),
        "precision": metric_or_none(a, "precision"),
        "recall": metric_or_none(a, "recall"),
        "f1": metric_or_none(a, "f1"),
    }
