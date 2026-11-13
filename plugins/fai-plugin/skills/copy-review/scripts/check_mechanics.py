#!/usr/bin/env python3
"""Mechanical FurtherAI style-guide checks over extracted document text.

Companion to extract_text.py. It runs only the checks a regex can decide —
terminology, AIisms, punctuation, numbers, dates, heading case. The judgment
rules (voice A/B2, R6 length, R15 sections, R26 paragraph shape) are not here;
the model does those after reading the copy.

Rules implemented (IDs and severities match SKILL.md):

  blocking  R28 contrast framing        R29 formulaic openers
            R42 FurtherAI               R43 AI            R44 LLMs
  fix       R16 table-as-screenshot     R18 placeholder table cell
            R32 exclamation mark        R33 unspaced em dash
            R34 heading case            R35 capital after : or ;
            R36 number words            R37 currency / percent symbols
            R38 date format             R39 date-range en dash
  consider  R30 rule-of-three budget    R33 em-dash density
            R16 chart image (weaker signal than a table image)

Usage:
  check_mechanics.py <file> [--artifact blog|deck|doc|email|reference] [--json]
  check_mechanics.py --stdin-json [--artifact ...]     # units on stdin
  extract_text.py <file> --json | check_mechanics.py --stdin-json --artifact doc
  check_mechanics.py --list-rules

--artifact is inferred when omitted: a path under skills/ or docs/ is a
`reference` (R34 heading case and R33 density do not run there); otherwise
.pptx->deck, .docx/.pdf->doc, .md/.html->blog, .txt->email.

Exit codes:
  0  clean, or findings but none `blocking`
  1  at least one `blocking` finding (so it can gate a build)
  2  usage / read error

Known limitation: R18 cannot see truly empty table cells — extract_text.py
drops them before this script ever runs. What it does catch is a cell holding
a placeholder (`-`, `--`, `—`, `TBD`, `?`). To audit real emptiness, compare
the table's cell locations against its row/column shape in the source.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

# --------------------------------------------------------------- setup -----

ARTIFACTS = ("blog", "deck", "doc", "email", "reference")
ALL = frozenset(ARTIFACTS)
# Technical reference docs — skill files, runbooks, API notes — use headings as
# labels, not sentences, and lean on the em dash in tables and command blocks.
# R34's sentence case and R33's density budget are blog-prose rules; they do
# not describe a defect here.
PROSE = ALL - {"reference"}

EXT_ARTIFACT = {
    ".pptx": "deck",
    ".docx": "doc",
    ".pdf": "doc",
    ".md": "blog",
    ".markdown": "blog",
    ".html": "blog",
    ".txt": "email",
}

# 1:1 character map — length is preserved so match offsets stay valid.
NORMALIZE = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', " ": " "})

MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")
MONTH_RE = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*"
MONTH_NUM = {m[:3].lower(): i + 1 for i, m in enumerate(MONTHS)}

DIGIT_WORD = {"1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
              "6": "six", "7": "seven", "8": "eight", "9": "nine"}
WORD_DIGIT = {"ten": "10", "eleven": "11", "twelve": "12", "twenty": "20",
              "thirty": "30", "hundred": "100"}

# Words that make a following bare digit a label, not a count.
LABEL_BEFORE = {
    "step", "slide", "page", "pages", "figure", "fig", "table", "phase", "tier",
    "level", "version", "v", "part", "chapter", "section", "appendix", "exhibit",
    "item", "no", "num", "q", "soc", "iso", "type", "class", "grade", "row",
    "column", "col", "line", "note", "option", "week", "day", "unit", "gpt",
    "claude", "python", "http", "acord", "sov",
}
# Nouns a bare digit is allowed to precede without triggering R36 (unit-less).
COUNT_UNITS = {
    "year", "years", "month", "months", "week", "weeks", "day", "days",
    "hour", "hours", "minute", "minutes", "second", "seconds", "quarter",
    "quarters", "decade", "decades", "person", "people", "customer",
    "customers", "carrier", "carriers", "broker", "brokers", "program",
    "programs", "workflow", "workflows", "step", "steps", "way", "ways",
    "reason", "reasons", "team", "teams", "state", "states", "line", "lines",
    "point", "points", "thing", "things", "time", "times", "x", "times",
}
# `-s` words that are not plural counts.
NOT_PLURAL = {
    "is", "was", "has", "its", "this", "thus", "plus", "less", "across",
    "business", "process", "success", "class", "gas", "us", "as", "yes",
    "always", "perhaps", "unless", "versus", "vs", "analysis", "basis",
    "status", "bonus", "focus", "campus", "series", "species",
}

SMALL_WORDS = {
    "a", "an", "the", "and", "but", "or", "nor", "for", "so", "yet", "at",
    "by", "in", "of", "on", "to", "up", "as", "if", "per", "via", "with",
    "from", "into", "over", "than", "that", "vs",
}

# Proper nouns that legitimately follow a colon or open a subheading.
PROPER_ALLOW = {
    "i", "ai", "furtherai", "acord", "sov", "mga", "mgu", "llm", "llms",
    "soc", "iso", "crm", "pas", "api", "pdf", "us", "usa", "eu", "uk",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday", "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
}

# Nouns that make "Further AI <noun>" the ordinary English phrase ("further
# AI investment") rather than the company name. Everything else after
# "Further AI" reads as the brand, so the check flags it.
BRAND_FOLLOW_NOUN = {
    "investment", "investments", "adoption", "research", "work", "works",
    "development", "capability", "capabilities", "tools", "tooling",
    "models", "spending", "progress", "funding", "innovation", "innovations",
    "integration", "integrations", "use", "usage", "experimentation",
    "project", "projects", "initiative", "initiatives", "deployment",
    "deployments", "feature", "features", "effort", "efforts", "training",
    "automation", "systems", "applications", "pilots", "rollout", "rollouts",
    "coverage", "exposure", "questions", "discussion", "discussions",
    "conversations", "capacity", "maturity", "governance", "regulation",
    "regulations", "vendors", "startups", "companies", "products",
}
# Words that make "further" the ordinary English adverb.
BRAND_PREV = {
    "go", "going", "goes", "went", "gone", "push", "pushed", "pushes",
    "take", "takes", "taking", "took", "taken", "dig", "digs", "digging",
    "move", "moves", "moving", "moved", "look", "looks", "looking",
    "read", "reads", "reading", "explore", "explores", "exploring",
    "no", "any", "much", "even", "step", "stepped", "nothing", "anything",
    "without", "or", "and",
}

ADJ_SUFFIX = re.compile(r"(ing|ed|ive|able|ible|ful|ous|less|ent|ant)$")
ADJ_WORDS = {
    "fast", "faster", "slow", "slower", "clean", "cleaner", "clear", "clearer",
    "smart", "smarter", "simple", "simpler", "hard", "harder", "easy", "easier",
    "cheap", "cheaper", "quick", "quicker", "safe", "safer", "new", "newer",
    "better", "best", "strong", "stronger", "small", "smaller", "big", "bigger",
    "good", "great", "bold", "sharp", "tight", "lean", "rich", "deep", "broad",
    "modern", "native", "secure", "robust", "precise", "accurate", "reliable",
}


def _fail(msg: str, code: int = 2) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


# ------------------------------------------------------------- masking -----

_URL = re.compile(
    r"https?://\S+"
    r"|www\.\S+"
    r"|\b[\w.+-]+@[\w-]+\.[\w.-]+\b"
    r"|\b[\w-]+\.(?:com|io|ai|net|org|dev|co|gov|edu)\b(?:/\S*)?",
    re.I,
)
_CODE = re.compile(r"`[^`]*`")
_LINK_TARGET = re.compile(r"\]\([^)]*\)")
_HTML_TAG = re.compile(r"<[^>]+>")


def _blank(m) -> str:
    return "\x00" * (m.end() - m.start())


def mask(text: str, markup: bool) -> str:
    """Same-length shadow of `text` with URLs, emails, code spans and link
    targets blanked out, and curly quotes flattened. Offsets stay valid, and
    no rule can fire inside a URL or a code span."""
    out = text.translate(NORMALIZE)
    out = _URL.sub(_blank, out)
    if markup:
        out = _CODE.sub(_blank, out)
        out = _LINK_TARGET.sub(_blank, out)
        out = _HTML_TAG.sub(_blank, out)
    return out


# ---------------------------------------------------------- unit helpers ---

_MD_DECOR = re.compile(r"^\s*(?:[-*+>]\s+|\d+[.)]\s+|#{1,6}\s+|\*\*|__|\"|')+")


def is_quoted(text: str) -> bool:
    """A unit that is wholly wrapped in quotes reads as someone else's words —
    a customer testimonial. Style rules on authored voice don't apply."""
    t = text.strip().translate(NORMALIZE)
    return len(t) > 20 and t[0] == '"' and t[-1] in '".'


def heading_level(unit: dict, ext: str):
    """Return (level, heading_text) or None."""
    text = unit["text"].strip()
    if ext in (".md", ".markdown"):
        m = re.match(r"^(#{1,6})\s+(.*\S)\s*$", text)
        if m:
            return len(m.group(1)), m.group(2)
        return None
    style = (unit.get("style") or "")
    if style == "Title":
        return 1, text
    m = re.match(r"^Heading(\d)", style)
    if m:
        return int(m.group(1)), text
    return None


def build_context(units, ext, artifact):
    """Doc-wide facts the per-unit checks lean on."""
    proper = set()
    for u in units:
        # Headings are Title Cased by convention, so they are not evidence
        # that a word is a proper noun — that would make R34 self-justifying.
        if heading_level(u, ext):
            continue
        t = u["text"].translate(NORMALIZE)
        for sent in re.split(r"(?<=[.!?])\s+", t):
            toks = list(re.finditer(r"[A-Za-z][\w'-]*", sent))
            for i, tok in enumerate(toks):
                w = tok.group(0)
                if i == 0:
                    continue
                # Skip evidence drawn from the very position R35 inspects,
                # otherwise every capital after a colon proves itself proper.
                before = sent[max(0, tok.start() - 2):tok.start()]
                if before.strip().endswith((":", ";", ".", "!", "?")):
                    continue
                if w[0].isupper():
                    proper.add(w.lower())
    # Tokens the document itself sets in code spans are product names, not
    # prose — Title Case must leave them alone.
    code_tokens = set()
    if ext in (".md", ".markdown", ".html"):
        for u in units:
            for m in _CODE.finditer(u["text"]):
                for tok in re.findall(r"[A-Za-z][\w-]*", m.group(0)):
                    code_tokens.add(tok.lower())
    return {
        "proper": proper,
        "code_tokens": code_tokens,
        "ext": ext,
        "artifact": artifact,
        "markup": ext in (".md", ".markdown", ".html"),
    }


def is_proper(word: str, ctx) -> bool:
    w = word.strip("'’.,;:!?()[]\"")
    if not w:
        return True
    if w.lower() in PROPER_ALLOW:
        return True
    if w.isupper() and len(w) >= 2:          # acronym: AI, MGA, ACORD
        return True
    if any(c.isupper() for c in w[1:]):      # internal capital: HubSpot
        return True
    if re.match(r"^[A-Z]\.", w):             # initial: J. Smith
        return True
    return w.lower() in ctx["proper"]


# ------------------------------------------------------ citation guard -----
# A document that discusses writing standards quotes the patterns it bans —
# a style guide, an editorial brief, review feedback, this plugin's own docs.
# Those citations are not violations. Two signals mark them, and the guard is
# deliberately conjunctive so a banned phrase in ordinary running prose, which
# is where someone actually wrote it, still fires.

_QUOTES = "\"'\u201c\u201d\u2018\u2019"
_QUOTE_CLOSERS = "\u201d\u2019"
_QUOTE_OPENERS = "\u201c\u2018"

# List item, numbered item, blockquote, table row, or heading — where a guide
# parks its examples.
_CITATION_CONTEXT = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|>|\||#{1,6}\s)")

# Naming the offence is itself the citation. `never` and `don't` are ordinary
# words, so they only count in their imperative forms — a sentence that merely
# contains "never" must still be checked.
_META_MARKER = re.compile(
    r"\bbanned\b|\bavoid\b|\bcliche\b|\bAIisms?\b|\bstyle guide\b"
    r"|\bnever (?:use|open|start|write|say|begin)\b"
    r"|\bdon'?t (?:use|start|open|write|say)\b|\bdo not use\b"
    r"|\binstead of\b|\u2192|\bR\d{2}\b",
    re.I,
)


def is_citation_context(text: str) -> bool:
    return bool(_CITATION_CONTEXT.match(text))


def has_meta_marker(text: str) -> bool:
    return bool(_META_MARKER.search(text))


def quoted_span(text: str, s: int, e: int) -> bool:
    """True when text[s:e] sits inside a quoted region — the nearest quote on
    each side exists, and the directional ones point the right way."""
    left = -1
    for i in range(s - 1, -1, -1):
        if text[i] in _QUOTES:
            left = i
            break
    if left < 0 or text[left] in _QUOTE_CLOSERS:
        return False
    right = -1
    for i in range(e, len(text)):
        if text[i] in _QUOTES:
            right = i
            break
    if right < 0 or text[right] in _QUOTE_OPENERS:
        return False
    return True


# ------------------------------------------------------------- windows -----

WIDTH = 58


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def snippet(text: str, s: int, e: int) -> str:
    if e - s >= WIDTH:
        return _clean(text[s:s + WIDTH - 1]) + "…"
    slack = WIDTH - (e - s)
    left = max(0, s - slack // 2)
    right = min(len(text), e + (WIDTH - (e - s) - (s - left)))
    left = max(0, min(left, right - WIDTH))
    frag = _clean(text[left:right])
    if left > 0:
        frag = "…" + frag
    if right < len(text):
        frag = frag + "…"
    return frag[:60]


# --------------------------------------------------------------- checks ----
# Per-unit callables return a list of (start, end, rewrite_text_or_None).
# Doc-level callables return a list of finding dicts directly.


def _brand_further(text: str, m_text: str, ctx):
    """R42, split out because 'further AI' is real English and 'FurtherAI' is
    a name. Only flag when the phrase reads as the company."""
    out = []
    # Unambiguous misspellings — always the brand, never English.
    for m in re.finditer(r"\bFurther[-– ]?Ai\b|\bFurtherAi\b|\bfurther[- ]ai\b|\bFurther-AI\b", m_text):
        out.append((m.start(), m.end(), "FurtherAI"))
    # "Further AI" with a space: brand only when a verb or the end follows and
    # no 'go further' style verb precedes.
    for m in re.finditer(r"\bFurther\s+AI\b", m_text):
        if any(m.start() == o[0] for o in out):
            continue
        prev = m_text[:m.start()].rstrip()
        prev_word = re.search(r"([A-Za-z'’-]+)$", prev)
        if prev_word and prev_word.group(1).lower() in BRAND_PREV:
            continue
        tail = m_text[m.end():]
        if tail[:1] in ("-", "–"):
            continue  # "Further AI-driven automation" — compound modifier
        nxt = re.match(r"\s+([A-Za-z'-]+)", tail)
        if nxt and nxt.group(1).islower() and nxt.group(1) in BRAND_FOLLOW_NOUN:
            continue  # "Further AI investment" — the English phrase
        out.append((m.start(), m.end(), "FurtherAI"))
    return out


def _ai_caps(text, m_text, ctx):
    out = []
    for m in re.finditer(r"\bA\.\s?I\.(?!\w)", m_text):
        out.append((m.start(), m.end(), "AI"))
    # Standalone lowercase "ai" only when the surrounding words make it the
    # noun. Bare `\bai\b` alone is far too loose.
    for m in re.finditer(r"\bai\b", m_text):
        before = re.search(r"([a-z]+)\s+$", m_text[:m.start()].lower())
        after = re.match(r"\s+([a-z']+)", m_text[m.end():].lower())
        b = before.group(1) if before else ""
        a = after.group(1) if after else ""
        ok_before = b in {"the", "an", "our", "your", "their", "with", "of",
                          "using", "use", "by", "and", "to", "is", "in", "for",
                          "generative", "agentic"}
        ok_after = a in {"agent", "agents", "model", "models", "tool", "tools",
                         "platform", "assistant", "assistants", "is", "can",
                         "will", "that", "which", "helps", "system", "systems",
                         "adoption", "workflow", "workflows"}
        if ok_before or ok_after:
            out.append((m.start(), m.end(), "AI"))
    for m in re.finditer(r"\bAi\b", m_text):
        nxt = re.match(r"\s+([A-Z])", m_text[m.end():])
        if nxt:
            continue  # "Ai Weiwei" — a name, not the acronym
        out.append((m.start(), m.end(), "AI"))
    return out


def _llm(text, m_text, ctx):
    out = []
    for m in re.finditer(r"\bL\.\s?L\.\s?M\.?(s|’s|'s)?\b", m_text):
        out.append((m.start(), m.end(), "LLMs" if (m.group(1) or "").startswith("s") else "LLM"))
    # LLM's as a plural. A genuine possessive is followed by a noun, so only
    # flag when a plural verb or a clause boundary follows.
    for m in re.finditer(r"\bLLM's\b", m_text):
        tail = m_text[m.end():]
        if re.match(r"\s*(?:[.,;:)\]]|$)", tail) or re.match(
            r"\s+(are|were|have|can|will|do|don't|aren't|haven't|and|or|remain|keep)\b", tail
        ):
            out.append((m.start(), m.end(), "LLMs"))
    return out


# R28 keys off the construction's real tell: the second clause restates the
# SAME subject affirmatively. `\1` enforces that, so plain negation ("it's not
# clear whether…", "this is not a supported configuration") never trips.
# mask() has already flattened curly apostrophes, so only `'` needs handling.
_SUBJ = r"(it|this|that|we|they|you)"
_NEG = r"(?:'s|s|'re|\s+is|\s+are|\s+was|\s+were)?\s*(?:not|isn't|aren't|wasn't|weren't)"
# The restatement must carry a copula — "it's"/"it is", never a bare "it".
_SAME = r"\1(?:'s|s|'re|\s+is|\s+are|\s+was|\s+were)"

CONTRAST = [
    # "It's not just X, it's also Y."  /  "We are not just X, we are Y."
    re.compile(rf"\b{_SUBJ}{_NEG}\s+just\b.{{0,60}}?,?\s+{_SAME}\b", re.I),
    # "It's not about X, it's about Y."  /  "This is not about X, this is about Y."
    re.compile(rf"\b{_SUBJ}{_NEG}\s+about\b.{{0,60}}?\b{_SAME}\s+about\b", re.I),
    # "It's not A. It's not even B. It's C."
    re.compile(rf"\b{_SUBJ}{_NEG}\b.{{0,40}}?\.\s*{_SAME}\s*(?:not|isn't|aren't)\s+even\b", re.I),
    # "That's not a feature, that's a workaround." — the parallel determiner is
    # what makes it rhetorical; without one it is ordinary prose.
    re.compile(rf"\b{_SUBJ}{_NEG}\s+(?:a|an|the)\s+[\w-]+(?:\s+[\w-]+){{0,2}},\s*{_SAME}\s+(?:a|an|the)\s+[\w-]+", re.I),
    # "It's not faster, it's cheaper." — parallel comparatives, same tell.
    re.compile(
        rf"\b{_SUBJ}{_NEG}\s+(?:\w+er|more\s+\w+|less\s+\w+)\b,\s*"
        rf"{_SAME}\s+(?:\w+er|more\s+\w+|less\s+\w+)\b", re.I),
]


def _contrast(text, m_text, ctx):
    out = []
    for rx in CONTRAST:
        for m in rx.finditer(m_text):
            out.append((m.start(), m.end(), None))
    return out


OPENERS = [
    re.compile(r"^here(?:'s| is)\s+the\s+thing\b", re.I),
    re.compile(r"^in the world of\b", re.I),
    re.compile(r"^at its core\b", re.I),
]
ANYWHERE = [
    re.compile(r"\bquietly revolutioni[sz]ing\b", re.I),
    re.compile(r"\bgame[- ]chang(?:er|ing|ers)\b", re.I),
]


def _openers(text, m_text, ctx):
    out = []
    lead = _MD_DECOR.match(m_text)
    offset = lead.end() if lead else 0
    body = m_text[offset:]
    for rx in OPENERS:
        m = rx.match(body)
        if m:
            out.append((offset + m.start(), offset + m.end(), None))
    for rx in ANYWHERE:
        for m in rx.finditer(m_text):
            out.append((m.start(), m.end(), None))
    return out


# R30 is a budget, not a per-instance rule, so the triad detectors only count.
TRIAD_COPULA = re.compile(
    r"\b(?:it'?s|is|are|was|were|feels?|looks?|becomes?)\s+"
    r"([a-z][\w-]{2,}),\s+([a-z][\w-]{2,}),?\s+and\s+([a-z][\w-]{2,})\b"
)
TRIAD_ADJ = re.compile(r"\b([a-z][\w-]{2,}),\s+([a-z][\w-]{2,}),\s+and\s+([a-z][\w-]{2,})\b")


def _adjish(w: str) -> bool:
    return bool(ADJ_SUFFIX.search(w)) or w in ADJ_WORDS


def _triads(units, ctx):
    """Count rule-of-three constructions across the artifact and report once
    if the budget (two) is blown. Deliberately narrow: only the three shapes
    the guide names, so a plain three-item noun list is not counted."""
    hits = []
    for u in units:
        if is_quoted(u["text"]) or has_meta_marker(u["text"]):
            continue
        cited = is_citation_context(u["text"])
        m_text = mask(u["text"], ctx["markup"])
        seen = []
        for rx, need_adj in ((TRIAD_COPULA, False), (TRIAD_ADJ, True)):
            for m in rx.finditer(m_text):
                words = [m.group(1), m.group(2), m.group(3)]
                if need_adj and not all(_adjish(w) for w in words):
                    continue
                # The two detectors overlap on the same triad — count it once.
                if any(m.start() < e and s < m.end() for s, e in seen):
                    continue
                if cited and quoted_span(u["text"], m.start(), m.end()):
                    continue
                seen.append((m.start(), m.end()))
                hits.append((u["loc"], snippet(u["text"], m.start(), m.end())))
        # "[Verb] X. [Verb] Y. [Verb] Z." — three consecutive clipped sentences.
        sents = [s for s in re.split(r"(?<=[.!?])\s+", u["text"].strip()) if s]
        run = 0
        for s in sents:
            if len(s.split()) <= 6 and re.search(r"[.!?]$", s):
                run += 1
                if run == 3:
                    hits.append((u["loc"], snippet(u["text"], 0, min(len(u["text"]), 58))))
                    run = 0
            else:
                run = 0
    if len(hits) <= 2:
        return []
    return [{
        "loc": hits[0][0],
        "current": f"{len(hits)} rule-of-three constructions, e.g. {hits[0][1]}",
        "rewrite": "Keep at most two per piece; rewrite the rest as plain statements.",
        "extra": sorted({h[0] for h in hits[1:]}, key=loc_key),
    }]


def _exclamation(text, m_text, ctx):
    if is_quoted(text):
        return []
    out = []
    for m in re.finditer(r"!", m_text):
        i = m.start()
        if m_text[i + 1:i + 2] == "[":          # markdown image ![alt](src)
            continue
        if m_text[i:i + 2] == "!=":
            continue
        if m_text[i:i + 11].lower() == "!important":
            continue
        if m_text[i - 1:i + 3] == "<!--":
            continue
        out.append((i, i + 1, "."))
    return out


def _em_unspaced(text, m_text, ctx):
    return [(m.start(1), m.end(1), " — ")
            for m in re.finditer(r"\S(—)\S", m_text)]


def _em_density(units, ctx):
    words = sum(len(u["text"].split()) for u in units)
    dashes = 0
    first = None
    for u in units:
        n = u["text"].count("—")
        if n and first is None:
            i = u["text"].index("—")
            first = (u["loc"], snippet(u["text"], i, i + 1))
        dashes += n
    if not words or first is None:
        return []
    budget = max(1, round(words / 300))
    if dashes <= budget:
        return []
    return [{
        "loc": first[0],
        "current": f"{dashes} em dashes in {words} words (budget {budget})",
        "rewrite": "Cut to roughly one per 300 words; commas, colons, or a full stop do the same work.",
        "extra": [],
    }]


def _colon_case(text, m_text, ctx):
    """R35. Guarded hard: label lines, parentheticals, and proper nouns are
    the three big false-positive sources, and all three are excluded."""
    if re.match(r"^\s*\S+(?:\s+\S+)?\s*:", m_text):
        return []  # "Sources: ..." — a label line, not prose
    out = []
    for m in re.finditer(r"([:;])\s+([A-Z][\w'-]*)", m_text):
        word = m.group(2)
        head = m_text[:m.start()]
        sent = re.split(r"(?<=[.!?])\s+", head)[-1]
        if len(sent.split()) < 3:
            continue  # short lead-in reads as a label
        if sent.count("(") > sent.count(")"):
            continue  # "(status: Lead)" — parenthetical label
        if is_proper(word, ctx):
            continue
        if re.match(r"^[A-Z]\b", word) and len(word) == 1:
            continue
        low = word[0].lower() + word[1:]
        out.append((m.start(2), m.end(2), low))
    return out


# The lookbehind rejects a range ("3–4 lines"), money, a version, a section
# number, and a comparison spec ("<=5", "≤5 words") — none are narrative counts.
_BARE_NUM = re.compile(r"(?<![\w$€£+.,/:#\-–—≤≥<>=~])([1-9])\s+([a-z][\w'-]*)")
_SPELLED = re.compile(r"\b(ten|eleven|twelve|twenty|thirty|hundred)\s+([a-z][\w'-]*)\b", re.I)


def _counts(w: str) -> bool:
    lw = w.lower()
    if lw in COUNT_UNITS:
        return True
    if lw in NOT_PLURAL:
        return False
    return len(lw) >= 4 and lw.endswith("s") and not lw.endswith("ss")


def _numbers(text, m_text, ctx):
    out = []
    for m in _BARE_NUM.finditer(m_text):
        head = m_text[:m.start()]
        token = re.search(r"(\S+)\s+$", head)
        if token and (token.group(1).startswith("-") or "=" in token.group(1)):
            continue  # a CLI flag's value ("--retries 3"), not a count
        prev = re.search(r"([A-Za-z][\w'-]*)\s+$", head)
        if prev and prev.group(1).lower() in LABEL_BEFORE:
            continue
        if prev and prev.group(1).isupper():
            continue
        if not _counts(m.group(2)):
            continue
        out.append((m.start(1), m.end(1), DIGIT_WORD[m.group(1)]))
    for m in _SPELLED.finditer(m_text):
        if not _counts(m.group(2)):
            continue
        out.append((m.start(1), m.end(1), WORD_DIGIT[m.group(1).lower()]))
    return out


def _units_symbols(text, m_text, ctx):
    out = []
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s+per ?cent\b", m_text, re.I):
        out.append((m.start(), m.end(), f"{m.group(1)}%"))
    for m in re.finditer(r"\$?(\d[\d,]*(?:\.\d+)?)\s+(million|billion|trillion|thousand)\b", m_text, re.I):
        suffix = {"million": "M", "billion": "B", "trillion": "T", "thousand": "K"}[m.group(2).lower()]
        out.append((m.start(), m.end(), f"${m.group(1)}{suffix}"))
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s+dollars\b", m_text, re.I):
        out.append((m.start(), m.end(), f"${m.group(1)}"))
    return out


def _fmt_date(y: int, mo: int, d: int) -> str:
    return f"{MONTHS[mo - 1]} {d}, {y}"


def _dates(text, m_text, ctx):
    out = []
    for m in re.finditer(rf"\b(\d{{1,2}})\s+({MONTH_RE})\.?,?\s+(\d{{4}})\b", m_text):
        d, mo, y = int(m.group(1)), MONTH_NUM.get(m.group(2)[:3].lower()), int(m.group(3))
        if not mo or d > 31 or not (1900 <= y <= 2099):
            continue
        out.append((m.start(), m.end(), _fmt_date(y, mo, d)))
    for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", m_text):
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            continue  # 24/7/365 and other non-dates
        y = y + 2000 if y < 100 else y
        if not (1900 <= y <= 2099):
            continue
        out.append((m.start(), m.end(), _fmt_date(y, mo, d)))
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", m_text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1900 <= y <= 2099 and 1 <= mo <= 12 and 1 <= d <= 31):
            continue
        out.append((m.start(), m.end(), _fmt_date(y, mo, d)))
    return out


def _date_range(text, m_text, ctx):
    """R39. A month name is required — without it, phone numbers, time ranges
    and quantity ranges all look like date ranges."""
    out = []
    rx = rf"\b({MONTH_RE})\.?\s+(\d{{1,2}})(\s*)([-–—])(\s*)(\d{{1,2}})\b(,?\s*\d{{4}})?"
    for m in re.finditer(rx, m_text):
        if int(m.group(2)) > 31 or int(m.group(6)) > 31:
            continue
        if m.group(3) == " " and m.group(4) == "–" and m.group(5) == " ":
            continue  # already "March 22 – 25"
        fixed = f"{m.group(1)} {m.group(2)} – {m.group(6)}{m.group(7) or ''}"
        out.append((m.start(), m.end(), fixed))
    return out


def _title_case(s: str, code_tokens=frozenset()) -> str:
    words = s.split()
    out = []
    for i, w in enumerate(words):
        bare = w.strip("`*_'\"()[].,:;").lower()
        if any(c.isupper() for c in w) or bare in code_tokens:
            out.append(w)  # acronym, brand, or a lowercase product name
        elif w.lower() in SMALL_WORDS and 0 < i < len(words) - 1:
            out.append(w.lower())
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def _heading_case(units, ctx):
    """R34. H1 Title Case is a blog-title rule; sentence case for subheads
    applies everywhere."""
    findings = []
    for u in units:
        h = heading_level(u, ctx["ext"])
        if not h:
            continue
        level, htext = h
        body = htext.strip()
        if not body or len(body.split()) < 2:
            continue
        if level == 1:
            if ctx["artifact"] != "blog":
                continue
            words = body.split()
            bad = [w for i, w in enumerate(words)
                   if w[:1].islower()
                   and w.strip("`*_'\"()[].,:;").lower() not in ctx["code_tokens"]
                   and not (0 < i < len(words) - 1 and w.lower() in SMALL_WORDS)]
            if len(bad) >= 2:
                findings.append({
                    "loc": u["loc"],
                    "current": snippet(body, 0, min(len(body), 58)),
                    "rewrite": _title_case(body, ctx["code_tokens"]),
                    "extra": [],
                })
        else:
            labelled = re.sub(r"^(?:[A-Z]\d?|\d+|[ivx]+)[.)]\s+", "", body, count=1)
            words = re.findall(r"[A-Za-z][\w'-]*", labelled)
            bad = [w for w in words[1:] if w[:1].isupper() and not is_proper(w, ctx)]
            if bad:
                fixed = body
                for w in bad:
                    fixed = re.sub(rf"\b{re.escape(w)}\b", w[0].lower() + w[1:], fixed)
                findings.append({
                    "loc": u["loc"],
                    "current": snippet(body, 0, min(len(body), 58)),
                    "rewrite": fixed,
                    "extra": [],
                })
    return findings


PLACEHOLDER = {"-", "--", "---", "–", "—", "tbd", "?", "n/a?", "todo", "xxx"}


def _placeholder_cells(units, ctx):
    out = []
    for u in units:
        if u.get("kind") != "table-cell":
            continue
        if u["text"].strip().lower() in PLACEHOLDER and u["text"].strip().lower() != "n/a":
            out.append({"loc": u["loc"], "current": u["text"].strip(),
                        "rewrite": "N/A", "extra": []})
    return out


_IMG = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)|<img[^>]*?(?:alt=[\"']([^\"']*)[\"'])?[^>]*?src=[\"']([^\"']+)[\"']", re.I)


def _image_rule(keywords, message_kind):
    rx = re.compile(keywords, re.I)

    def check(text, m_text, ctx):
        if not ctx["markup"]:
            return []
        out = []
        for m in _IMG.finditer(text):
            alt = m.group(1) or m.group(3) or ""
            src = m.group(2) or m.group(4) or ""
            if rx.search(alt) or rx.search(Path(src).name):
                out.append((m.start(), m.end(), None))
        return out

    check.__name__ = f"_image_{message_kind}"
    return check


# ----------------------------------------------------------- rule table ----
# (rule_id, severity, artifact_scope, check, message, rewrite_hint)
# check: a per-unit callable (text, masked_text, ctx) -> [(start, end, repl)]
#        or DOC(callable(units, ctx) -> [finding dicts]) for artifact-wide rules.
# rewrite_hint is used when the check returns no concrete replacement.


class DOC:
    """Marks an artifact-wide check rather than a per-unit one."""

    def __init__(self, fn):
        self.fn = fn


RULES = [
    ("R42", "blocking", ALL, _brand_further,
     "Brand name must be FurtherAI — one word, capital F and AI",
     "FurtherAI"),
    ("R43", "blocking", ALL, _ai_caps,
     "AI is all caps; no periods",
     "AI"),
    ("R44", "blocking", ALL, _llm,
     "LLMs — no periods between letters, and no apostrophe in the plural",
     "LLMs"),
    ("R28", "blocking", ALL, _contrast,
     "Contrast framing is banned",
     "Say the thing directly — state the claim, the number, or the outcome."),
    ("R29", "blocking", ALL, _openers,
     "Formulaic opener / AI cliche",
     "Open with the substance."),
    ("R30", "consider", ALL, DOC(_triads),
     "Rule-of-three budget blown (max two per piece)",
     "Keep two at most; rewrite the rest as plain statements."),
    ("R32", "fix", ALL, _exclamation,
     "No exclamation marks in body copy",
     "."),
    ("R33", "fix", ALL, _em_unspaced,
     "Em dash needs a space on each side",
     " — "),
    ("R33", "consider", PROSE, DOC(_em_density),
     "Em dashes overused (guide says sparingly)",
     "Cut to roughly one per 300 words."),
    ("R34", "fix", PROSE, DOC(_heading_case),
     "Heading case — Title Case for H1, sentence case for subheads",
     "See rewrite."),
    ("R35", "fix", ALL, _colon_case,
     "Lower case after a colon or semicolon",
     "Lowercase the first word."),
    ("R36", "fix", ALL, _numbers,
     "Spell out one–nine; use digits for 10 and up",
     "See rewrite."),
    ("R37", "fix", ALL, _units_symbols,
     "Currency and percentage take symbols with digits",
     "See rewrite."),
    ("R38", "fix", ALL, _dates,
     "Dates use American Month Day, Year",
     "See rewrite."),
    ("R39", "fix", ALL, _date_range,
     "Date ranges take a spaced en dash",
     "See rewrite."),
    ("R18", "fix", frozenset({"blog", "doc"}), DOC(_placeholder_cells),
     "Placeholder table cell — an empty-looking cell reads as a broken table",
     "N/A"),
    ("R16", "fix", frozenset({"blog", "doc"}),
     _image_rule(r"\b(table|matrix|grid)\b|table|matrix", "table"),
     "Image looks like a table screenshot — models can't read it",
     "Rebuild it as a real table with descriptive headers."),
    ("R16", "consider", frozenset({"blog", "doc"}),
     _image_rule(r"\bchart\b|chart", "chart"),
     "Image may be a pasted chart holding tabular data",
     "If the numbers matter, add a real table alongside it."),
]

SEV_RANK = {"blocking": 0, "fix": 1, "consider": 2}


# ---------------------------------------------------------------- runner ---

def infer_artifact(path: Path) -> str:
    """A file under skills/ or docs/ is a technical reference; otherwise the
    extension decides. --artifact always overrides."""
    try:
        parts = {q.lower() for q in path.resolve().parts}
    except OSError:
        parts = {q.lower() for q in path.parts}
    if parts & {"skills", "docs"}:
        return "reference"
    return EXT_ARTIFACT.get(path.suffix.lower(), "doc")


def load_units(path: Path, include_notes: bool, min_chars: int):
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("fai_extract_text", here / "extract_text.py")
    if spec is None or spec.loader is None:
        _fail("cannot import extract_text.py from the skill's scripts directory")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ext = path.suffix.lower()
    if ext not in mod.EXTRACTORS:
        _fail(f"unsupported file type {ext or '(none)'} — supported: {', '.join(sorted(mod.EXTRACTORS))}")
    fn = mod.EXTRACTORS[ext]
    units = fn(path, include_notes) if ext in (".docx", ".pptx") else fn(path)
    if min_chars:
        units = [u for u in units if len(u["text"]) >= min_chars]
    return units


def drop_code_blocks(units, ext):
    """Fenced code in markdown is not copy. Skip it wholesale."""
    if ext not in (".md", ".markdown"):
        return units
    out, fenced = [], False
    for u in units:
        if re.match(r"^\s*(```|~~~)", u["text"]):
            fenced = not fenced
            continue
        if fenced:
            continue
        out.append(u)
    return out


def loc_key(loc: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", loc)]


def run(units, artifact, ext, only=None):
    ctx = build_context(units, ext, artifact)
    findings = []
    for rid, sev, scope, check, message, hint in RULES:
        if only and rid not in only:
            continue
        if artifact not in scope:
            continue
        if isinstance(check, DOC):
            for f in check.fn(units, ctx):
                findings.append({
                    "rule": rid, "sev": sev, "loc": f["loc"],
                    "current": f["current"],
                    "rewrite": f.get("rewrite") or hint,
                    "message": message,
                    "also": f.get("extra", []),
                })
            continue
        for u in units:
            text = u["text"]
            if has_meta_marker(text):
                continue  # the unit names the offence: it is citing, not doing
            m_text = mask(text, ctx["markup"])
            cited = is_citation_context(text)
            for start, end, repl in check(text, m_text, ctx):
                if cited and quoted_span(text, start, end):
                    continue  # a quoted example inside a list, table, or heading
                if repl is None:
                    rewrite = hint
                else:
                    new_text = text[:start] + repl + text[end:]
                    rewrite = snippet(new_text, start, start + len(repl))
                findings.append({
                    "rule": rid, "sev": sev, "loc": u["loc"],
                    "current": snippet(text, start, end),
                    "rewrite": rewrite,
                    "message": message,
                    "also": [],
                })
    findings.sort(key=lambda f: (SEV_RANK[f["sev"]], loc_key(f["loc"]), f["rule"]))
    for i, f in enumerate(findings, start=1):
        f["n"] = i
    return findings


def render(findings, label, artifact):
    blocking = sum(1 for f in findings if f["sev"] == "blocking")
    lines = [f"## Copy review (mechanical) — {label} ({artifact})", ""]
    if not findings:
        lines.append("**Verdict:** clean — no mechanical style-guide violations found.")
        lines.append("")
        lines.append("_Mechanical checks only. Voice (R1–R12), structure (R13–R15, R17, R19–R21)"
                     " and readability (R22–R27) still need the model pass._")
        return "\n".join(lines)
    lines.append(f"**Verdict:** {len(findings)} finding{'s' if len(findings) != 1 else ''},"
                 f" {blocking} blocking")
    lines.append("")
    lines.append("| # | Loc | Rule | Sev | Current | Rewrite |")
    lines.append("|---|---|---|---|---|---|")
    for f in findings:
        cur = f["current"].replace("|", "\\|")
        rw = str(f["rewrite"]).replace("|", "\\|")
        lines.append(f"| {f['n']} | {f['loc']} | {f['rule']} | {f['sev']} | {cur} | {rw} |")
    extra = [f for f in findings if f["also"]]
    if extra:
        lines.append("")
        for f in extra:
            lines.append(f"- Also at: {', '.join(f['also'])} (#{f['n']}, {f['rule']})")
    lines.append("")
    lines.append("_Mechanical checks only. Voice (R1–R12), structure (R13–R15, R17, R19–R21)"
                 " and readability (R22–R27) still need the model pass._")
    return "\n".join(lines)


def list_rules():
    print(f"{'Rule':<6} {'Severity':<9} {'Artifacts':<24} Check")
    print("-" * 96)
    for rid, sev, scope, check, message, _hint in RULES:
        scope_s = "all" if scope == ALL else ",".join(sorted(scope))
        name = check.fn.__name__ if isinstance(check, DOC) else check.__name__
        kind = "artifact-wide" if isinstance(check, DOC) else "per-unit"
        print(f"{rid:<6} {sev:<9} {scope_s:<24} {message} [{name}, {kind}]")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("file", nargs="?", help="document to check (.docx .pptx .pdf .md .txt .html)")
    ap.add_argument("--stdin-json", action="store_true",
                    help="read the unit list from stdin instead (extract_text.py --json)")
    ap.add_argument("--artifact", choices=ARTIFACTS,
                    help="rule scope; inferred from the path and extension when omitted")
    ap.add_argument("--rule", action="append", metavar="RID",
                    help="run only this rule (repeatable), e.g. --rule R42 --rule R28")
    ap.add_argument("--list-rules", action="store_true", help="print the rule table and exit")
    ap.add_argument("--json", action="store_true", help="emit findings as JSON")
    ap.add_argument("--include-notes", action="store_true",
                    help="also check speaker notes (pptx) / headers and footers (docx)")
    ap.add_argument("--min-chars", type=int, default=0, help="skip units shorter than this")
    args = ap.parse_args()

    if args.list_rules:
        list_rules()
        return 0

    if args.stdin_json:
        try:
            units = json.load(sys.stdin)
        except json.JSONDecodeError as exc:
            _fail(f"stdin is not valid JSON: {exc}")
        if not isinstance(units, list) or any("text" not in u or "loc" not in u for u in units):
            _fail("stdin JSON must be a list of extract_text.py units with 'loc' and 'text'")
        label = args.file or "stdin"
        ext = Path(args.file).suffix.lower() if args.file else ""
        artifact = args.artifact or (infer_artifact(Path(args.file)) if args.file else "doc")
    else:
        if not args.file:
            _fail("give a file, or pass --stdin-json with units on stdin")
        path = Path(args.file).expanduser()
        if not path.exists():
            _fail(f"no such file: {path}")
        ext = path.suffix.lower()
        artifact = args.artifact or infer_artifact(path)
        units = load_units(path, args.include_notes, args.min_chars)
        label = str(path)

    units = drop_code_blocks(units, ext)
    if not units:
        print(f"No reviewable text in {label} — nothing to check.")
        return 0

    only = set(args.rule) if args.rule else None
    if only:
        known = {r[0] for r in RULES}
        unknown = only - known
        if unknown:
            _fail(f"unknown rule id(s): {', '.join(sorted(unknown))} — try --list-rules")

    findings = run(units, artifact, ext, only)

    if args.json:
        print(json.dumps({"file": label, "artifact": artifact,
                          "findings": findings}, indent=2, ensure_ascii=False))
    else:
        print(render(findings, label, artifact))

    return 1 if any(f["sev"] == "blocking" for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
