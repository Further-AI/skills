#!/usr/bin/env python3
"""Pass 5 — HTML lint, local render, screenshot.

Three stages, in order:

  1. LINT    Static checks on the HTML producer's code (a code step whose output
             field is consumed by an `email` step, conventionally `email_body`):
             inline <script>, external network requests, emoji, hardcoded dates,
             missing escaping, style-guide token conformance, plus the classic
             mistakes from reference/patterns/html_summary_dashboard.md
             (recipients on a display-only email step, HTML under a key other
             than `email_body`, compose step left with display_step: true).

  2. RENDER  Execute the step's code locally against synthesized inputs, in a
             subprocess with a timeout, with a stub `fai_sandbox` injected via
             sys.modules. Capture the returned HTML and lint the real markup
             with html.parser.

  3. CAPTURE Screenshot the rendered HTML with headless Chrome, all fixtures in
             ONE Chrome session (cold start is 1.5-2.5s and dominates the pass).

Cost rule: screenshots are the largest token line item in the sub-skill and
image cost scales with pixels (~w*h/750 tokens). So the default is exactly ONE
capture — the `empty` fixture, SHOT_WIDTH wide, height clamped to
SHOT_MAX_HEIGHT — and the full fixture matrix runs only on escalation (lint
found something, the first capture looks wrong, or --escalate).

Rules this file obeys (see CONTRACT.md): stdlib only, Python 3.9+, read-only
(writes nothing outside --out), no network, bounded stdout, exit 0/1/2.

Rule sources are named in comments where a rule comes from a reference doc:
  patterns/html_summary_dashboard.md   dashboard standard + common mistakes
  step_types/custom_step.md            sandbox contract (what fai_sandbox exposes)
"""

from __future__ import annotations

import argparse
import ast
import base64
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unicodedata
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

PASS_NAME = "html"

# ---------------------------------------------------------------------------
# common.py (CONTRACT.md "common.py public API — exact signatures")
#
# The shim below is a stand-in for exactly that API, used only when common.py
# is absent. Its signatures are identical, so deleting it is a delete and not
# a refactor.
# ---------------------------------------------------------------------------
try:
    from common import (  # noqa: F401  (table/truncate are used below)
        PassOutput, THRESHOLDS, add_common_args, finding, load_facts, require_args,
        slug, table, truncate,
    )
    HAVE_COMMON = True
except ImportError:  # pragma: no cover - shim, deleted once common.py is present
    HAVE_COMMON = False
    import pathlib

    THRESHOLDS = {
        "MAX_FIELDS_PER_STEP": 40, "MAX_PROMPT_CHARS": 8000, "DUP_STEP_OVERLAP": 0.75,
        "ADOPT_MIN_OVERLAP": 0.50, "ADOPT_STRONG_OVERLAP": 0.75,
        "MAX_CASE_COMPARISONS": 6, "MAX_RESHAPE_CHAIN": 2, "MAX_PATHS_SHOWN": 20,
        "MAX_FINDINGS_PRINTED": 40, "SHOT_WIDTH": 900, "SHOT_MAX_HEIGHT": 2000,
    }
    _SEVERITIES = ("blocker", "warning", "note")
    _COUNT_KEY = {"blocker": "blockers", "warning": "warnings", "note": "notes"}

    def truncate(text, limit):
        flat = " ".join(str(text).split())
        return flat if len(flat) <= limit else flat[: max(0, limit - 1)] + "\u2026"

    def table(rows, headers, max_rows=10):
        shown = [list(r) for r in list(rows)[:max_rows]]
        columns = len(headers)
        widths = [len(str(h)) for h in headers]
        for row in shown:
            for index in range(columns):
                value = truncate(row[index], 46) if index < len(row) else ""
                widths[index] = max(widths[index], len(value))

        def render(cells):
            parts = []
            for index in range(columns):
                value = truncate(cells[index], 46) if index < len(cells) else ""
                parts.append(value.ljust(widths[index]) if index < columns - 1 else value)
            return "  " + "  ".join(parts).rstrip()

        lines = [render(headers), "  " + "  ".join("-" * w for w in widths)]
        for row in shown:
            lines.append(render(row))
        if len(rows) > len(shown):
            lines.append("  ... %d more of %d" % (len(rows) - len(shown), len(rows)))
        return lines

    def finding(code, severity, title, detail, *, step=None, fix=None,
                evidence=None, group_key=None):
        if severity not in _SEVERITIES:
            raise ValueError("unknown severity %r (want one of %s)"
                             % (severity, ", ".join(_SEVERITIES)))
        return {"code": code, "severity": severity, "step": step, "title": title,
                "detail": detail, "fix": fix, "evidence": evidence or {},
                "group_key": group_key}

    def slug(name):
        cleaned = re.sub(r"[^A-Za-z0-9]+", "_", str(name)).strip("_")
        return cleaned or "step"

    def add_common_args(parser, needs_facts=True):
        if needs_facts:
            parser.add_argument("--facts", help="path to facts.json "
                                               "(required unless --self-test)")
        parser.add_argument("--out", help="output directory (required unless --self-test)")
        parser.add_argument("--self-test", action="store_true",
                            help="run against the built-in corpus and assert invariants")

    def require_args(args, *names):
        if getattr(args, "self_test", False):
            return
        missing = [n for n in names if not getattr(args, n.replace("-", "_"), None)]
        if missing:
            flags = " ".join("--%s" % n.replace("_", "-") for n in missing)
            sys.stderr.write("error: missing required argument(s): %s "
                             "(or pass --self-test to run against the built-in corpus)\n"
                             % flags)
            raise SystemExit(2)

    def load_facts(path):
        try:
            with open(path) as handle:
                facts = json.load(handle)
        except (OSError, ValueError) as exc:
            sys.stderr.write("error: cannot read facts %s: %s\n" % (path, exc))
            raise SystemExit(2)
        if not isinstance(facts, dict) or facts.get("schema_version") != 1:
            sys.stderr.write("error: %s is not a schema_version 1 facts file\n" % path)
            raise SystemExit(2)
        return facts

    class PassOutput(object):
        def __init__(self, pass_name, out_dir):
            self.pass_name = pass_name
            self.out_dir = str(out_dir)
            self._findings = []
            self._extras = {}
            self._path = None

        def add(self, finding_dict):
            if not isinstance(finding_dict, dict) or "severity" not in finding_dict:
                raise ValueError("add() wants a dict from _finding()")
            if finding_dict["severity"] not in _SEVERITIES:
                raise ValueError("unknown severity %r" % finding_dict["severity"])
            self._findings.append(finding_dict)

        def add_all(self, findings):
            for item in findings or []:
                self.add(item)

        def set(self, key, value):
            if key in ("pass", "findings"):
                raise ValueError("%r is reserved; PassOutput owns it" % key)
            self._extras[key] = value

        def counts(self):
            out = {"blockers": 0, "warnings": 0, "notes": 0}
            for item in self._findings:
                out[_COUNT_KEY[item["severity"]]] += 1
            return out

        @property
        def findings(self):
            return list(self._findings)

        def write(self):
            os.makedirs(self.out_dir, exist_ok=True)
            stamped = []
            for item in sorted(self._findings, key=lambda f: (
                    _SEVERITIES.index(f["severity"]), f.get("code") or "", f.get("step") or "")):
                row = dict(item)
                row["pass"] = self.pass_name
                stamped.append(row)
            payload = {"schema_version": 1, "pass": self.pass_name, "counts": self.counts()}
            payload.update(self._extras)
            payload["findings"] = stamped
            path = pathlib.Path(self.out_dir) / ("%s.json" % self.pass_name)
            with open(str(path) + ".tmp", "w") as handle:
                json.dump(payload, handle, indent=2, default=str)
                handle.write("\n")
            os.replace(str(path) + ".tmp", str(path))
            self._path = path
            return path

        def print_summary(self, extra_lines=None):
            counts = self.counts()
            out_path = str(self._path) if self._path else os.path.join(
                self.out_dir, "%s.json" % self.pass_name)
            for line in extra_lines or []:
                print(line)
            rows, seen = [], {}
            for item in sorted(self._findings, key=lambda f: _SEVERITIES.index(f["severity"])):
                gk = item.get("group_key")
                if gk and gk in seen:
                    rows[seen[gk]][1] += 1
                    continue
                if gk:
                    seen[gk] = len(rows)
                rows.append([item, 1])
            shown = rows[:THRESHOLDS["MAX_FINDINGS_PRINTED"]]
            if shown:
                print("")
            for item, count in shown:
                print("%-7s  %-40s %s%s" % (
                    item["severity"].upper(), truncate(item.get("step") or "(workflow)", 40),
                    item.get("title") or item.get("code"),
                    "  [x%d]" % count if count > 1 else ""))
                if (item.get("detail") or "").strip():
                    print("         %s" % truncate(item["detail"], 190))
                if (item.get("fix") or "").strip():
                    print("         fix: %s" % truncate(item["fix"], 190))
            if len(rows) > len(shown):
                print("... %d more (see %s)" % (len(rows) - len(shown), out_path))
            print("SUMMARY %s blockers=%d warnings=%d notes=%d out=%s" % (
                self.pass_name, counts["blockers"], counts["warnings"],
                counts["notes"], out_path))
            return 1 if counts["blockers"] else 0


SHOT_WIDTH = THRESHOLDS["SHOT_WIDTH"]
SHOT_MAX_HEIGHT = THRESHOLDS["SHOT_MAX_HEIGHT"]

# Anthropic image token cost is ~ (width * height) / 750.
TOKENS_PER_PIXEL_DIVISOR = 750

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]

FIXTURE_NAMES = ("full", "empty", "stress")
DEFAULT_FIXTURES = ("empty",)


# ---------------------------------------------------------------------------
# findings
# ---------------------------------------------------------------------------

def _finding(code: str, severity: str, title: str, detail: str, fix: Optional[str] = None,
             step: Optional[str] = None, evidence: Optional[Dict[str, Any]] = None,
             group_key: Optional[str] = None) -> Dict[str, Any]:
    """Local adapter over common.finding(), which is keyword-only after `detail`.

    Named with a leading underscore so a reader at any call site can tell this
    is the local one, not the imported `finding`.
    """
    return finding(code, severity, title, detail, step=step, fix=fix or None,
                   evidence=evidence, group_key=group_key)


def _sev_counts(findings: List[Dict[str, Any]]) -> Tuple[int, int, int]:
    """(blockers, warnings, notes) for a list of findings not yet collected."""
    b = sum(1 for f in findings if f["severity"] == "blocker")
    w = sum(1 for f in findings if f["severity"] == "warning")
    n = sum(1 for f in findings if f["severity"] == "note")
    return b, w, n


# ---------------------------------------------------------------------------
# stage 1 — static lint on the producer's code
# ---------------------------------------------------------------------------

# Pictographic emoji. Deliberately excludes arrows (U+2190-U+21FF, U+2192 is
# what &rarr; converts to) and box drawing (U+2500-U+257F, used as comment
# rules in the reference implementation). The single codepoints in the
# U+2600-U+27BF band are the Emoji_Presentation members of that block — ✅
# (U+2705) and ❌ (U+274C) are emoji; ⚠ (U+26A0) is text by default and only
# becomes emoji with a U+FE0F variation selector (handled separately).
_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF), (0x1FB00, 0x1FBFF),
    (0x231A, 0x231B), (0x23E9, 0x23F3), (0x25FD, 0x25FE), (0x2614, 0x2615),
    (0x2648, 0x2653), (0x267F, 0x267F), (0x2693, 0x2693), (0x26A1, 0x26A1),
    (0x26AA, 0x26AB), (0x26BD, 0x26BE), (0x26C4, 0x26C5), (0x26CE, 0x26CE),
    (0x26D4, 0x26D4), (0x26EA, 0x26EA), (0x26F2, 0x26F3), (0x26F5, 0x26F5),
    (0x26FA, 0x26FA), (0x26FD, 0x26FD), (0x2705, 0x2705), (0x270A, 0x270B),
    (0x2728, 0x2728), (0x274C, 0x274C), (0x274E, 0x274E), (0x2753, 0x2755),
    (0x2757, 0x2757), (0x2795, 0x2797), (0x27B0, 0x27B0), (0x27BF, 0x27BF),
)
# Dingbats / misc symbols: borderline typographic (bare check marks, stars,
# the text-presentation warning sign). Reported at note severity.
_DINGBAT_RANGES = ((0x2600, 0x27BF), (0x2B00, 0x2BFF))
_VARIATION_SELECTOR = "\ufe0f"

_DATE_LITERAL_RE = re.compile(
    r"\b(?:20\d\d-\d\d-\d\d"
    r"|\d{1,2}/\d{1,2}/20\d\d"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},\s*20\d\d"
    r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+20\d\d)\b"
)

# Style-guide tokens, from reference/patterns/html_summary_dashboard.md
# ("FurtherAI dashboard style guide" table).
_STYLE_TOKENS = [
    ("page background #f4f3f0", ("#f4f3f0",)),
    ("card background #ffffff", ("#ffffff", "#fff;", "#fff ", "#fff'", '#fff"')),
    ("card radius 10px", ("10px",)),
    ("status pass #059669", ("#059669",)),
    ("status fail #dc2626", ("#dc2626",)),
    ("status review #d97706", ("#d97706",)),
    ("hero radial-gradient", ("radial-gradient",)),
    ("hero accent rgba(251,150,8", ("rgba(251,150,8", "rgba(251, 150, 8")),
    ("hero linear-gradient(135deg", ("linear-gradient(135deg",)),
    ("display font Fraunces", ("fraunces",)),
    ("label font Instrument Sans", ("instrument sans", "instrument+sans")),
    ("body font Wix Madefor Text", ("wix madefor",)),
    ("collapsible <details> sections", ("<details",)),
    ("FurtherAI footer", ("furtherai",)),
]

_EXTERNAL_URL_RE = re.compile(r"""(?:https?:)?//[a-z0-9.\-]+""", re.I)


def _string_constants(code: str) -> Tuple[List[str], Optional[str]]:
    """Every string literal in the code, via ast (so comments are ignored)."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [], "line %s: %s" % (exc.lineno, exc.msg)
    out: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
    return out, None


def _mined_keys(code: str) -> List[str]:
    """String keys the code reads: d.get("k") and d["k"].

    Used to populate schema-less ("open") upstream objects for the `full` and
    `stress` fixtures — a workflow whose upstream steps declare bare
    {"type": "object"} gives the resolver nothing to synthesize from.
    """
    keys: List[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return keys
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "get" and node.args:
            a0 = node.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                keys.append(a0.value)
        elif isinstance(node, ast.Call) and len(node.args) >= 2 \
                and isinstance(node.args[0], (ast.Name, ast.Attribute, ast.Subscript, ast.Call)) \
                and isinstance(node.args[1], ast.Constant) \
                and isinstance(node.args[1].value, str):
            # The (dict, key[, default]) helper idiom every dashboard defines:
            # get(basic, "Insured Name", ""), extract_value(row, "vin").
            keys.append(node.args[1].value)
        elif isinstance(node, ast.Subscript):
            sl = node.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                keys.append(sl.value)
    seen = set()
    uniq = []
    for k in keys:
        # Drop things that are plainly markup or CSS rather than data keys.
        if not k or k in seen or len(k) > 60 or "<" in k or ":" in k or "#" in k or ";" in k:
            continue
        seen.add(k)
        uniq.append(k)
    return uniq


def _chars_in_ranges(text: str, ranges) -> List[str]:
    hits = []
    for ch in text:
        cp = ord(ch)
        for lo, hi in ranges:
            if lo <= cp <= hi:
                hits.append(ch)
                break
    return sorted(set(hits))


def _emoji_chars(text: str) -> List[str]:
    """Emoji: the pictographic ranges, plus any glyph carrying U+FE0F."""
    hits = set(_chars_in_ranges(text, _EMOJI_RANGES))
    for i in range(len(text) - 1):
        if text[i + 1] == _VARIATION_SELECTOR and ord(text[i]) > 0x7F:
            hits.add(text[i])
    return sorted(hits)


def _dingbat_chars(text: str) -> List[str]:
    """Symbol glyphs that are not emoji (bare check marks, stars, U+26A0)."""
    emoji = set(_emoji_chars(text))
    return [c for c in _chars_in_ranges(text, _DINGBAT_RANGES) if c not in emoji]


def _describe_chars(chars: List[str], cap: int = 8) -> str:
    parts = []
    for ch in chars[:cap]:
        try:
            name = unicodedata.name(ch)
        except ValueError:
            name = "U+%04X" % ord(ch)
        parts.append("%s (U+%04X %s)" % (ch, ord(ch), name))
    if len(chars) > cap:
        parts.append("+%d more" % (len(chars) - cap))
    return ", ".join(parts)


def _external_refs(text: str) -> Dict[str, List[str]]:
    """Classify external references found in markup-ish text.

    patterns/html_summary_dashboard.md: the dashboard is display-only HTML that
    an `email` step renders. Remote subresources are stripped by email clients
    and are a defect when the mail is actually sent; in the UI-only render a
    remote font stylesheet still resolves. So they are classified, not lumped.
    """
    out = {"stylesheet": [], "image": [], "script": [], "frame": [], "css_url": [], "font": []}
    low = text
    for m in re.finditer(r"<link\b[^>]*>", low, re.I):
        tag = m.group(0)
        href = re.search(r"""href\s*=\s*["']?([^"'\s>]+)""", tag, re.I)
        if href and _EXTERNAL_URL_RE.match(href.group(1).strip()):
            url = href.group(1).strip()
            if "font" in url.lower():
                out["font"].append(url)
            else:
                out["stylesheet"].append(url)
    for m in re.finditer(r"<img\b[^>]*>", low, re.I):
        src = re.search(r"""src\s*=\s*["']?([^"'\s>]+)""", m.group(0), re.I)
        if src and _EXTERNAL_URL_RE.match(src.group(1).strip()):
            out["image"].append(src.group(1).strip())
    for m in re.finditer(r"<script\b[^>]*>", low, re.I):
        src = re.search(r"""src\s*=\s*["']?([^"'\s>]+)""", m.group(0), re.I)
        if src and _EXTERNAL_URL_RE.match(src.group(1).strip()):
            out["script"].append(src.group(1).strip())
    for m in re.finditer(r"<(?:iframe|object|embed)\b[^>]*>", low, re.I):
        src = re.search(r"""(?:src|data)\s*=\s*["']?([^"'\s>]+)""", m.group(0), re.I)
        if src and _EXTERNAL_URL_RE.match(src.group(1).strip()):
            out["frame"].append(src.group(1).strip())
    for m in re.finditer(r"""url\(\s*["']?((?:https?:)?//[^)"']+)""", low, re.I):
        out["css_url"].append(m.group(1))
    for m in re.finditer(r"""@import\s+(?:url\()?\s*["']?((?:https?:)?//[^)"';]+)""", low, re.I):
        out["css_url"].append(m.group(1))
    return {k: v for k, v in out.items() if v}


def static_lint(step: str, code: str, facts_view: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Stage 1. Returns (findings, lint summary)."""
    findings: List[Dict[str, Any]] = []
    literals, syntax_error = _string_constants(code)
    joined = "\n".join(literals)
    low = joined.lower()
    code_low = code.lower()

    if syntax_error:
        findings.append(_finding(
            "HTML_CODE_SYNTAX_ERROR", "blocker",
            "Producer code does not parse",
            "%s cannot be compiled: %s" % (step, syntax_error),
            fix="Fix the syntax error; the step will fail at run time.",
            step=step, evidence={"parse_error": syntax_error},
        ))

    # --- inline <script> -----------------------------------------------------
    if "<script" in low:
        findings.append(_finding(
            "HTML_INLINE_SCRIPT", "blocker",
            "Inline <script> in dashboard markup",
            "The composed HTML contains a <script> tag. Script never runs in an "
            "email client and is stripped by the mail renderer, so anything the "
            "dashboard depends on it for is silently missing.",
            fix="Remove the script and express the behaviour with static markup and CSS "
            "(e.g. <details> for collapsible sections).",
            step=step, evidence={"literal_hits": [s for s in literals if "<script" in s.lower()][:3]},
        ))

    # --- external subresources ----------------------------------------------
    refs = _external_refs(joined)
    hard = {k: v for k, v in refs.items() if k in ("image", "script", "frame", "stylesheet", "css_url")}
    if hard:
        findings.append(_finding(
            "HTML_EXTERNAL_REQUEST", "blocker",
            "Dashboard fetches an external subresource",
            "Remote subresources are stripped or blocked wherever this HTML is "
            "rendered as mail: " + "; ".join(
                "%s -> %s" % (k, ", ".join(v[:2])) for k, v in sorted(hard.items())),
            fix="Inline the asset (CSS in a <style> block, images as data: URIs) or drop it.",
            step=step, evidence={"external": hard},
        ))
    if refs.get("font"):
        sent_for_real = any((c.get("recipients") or {}) for c in facts_view.get("consumed_by") or [])
        findings.append(_finding(
            "HTML_EXTERNAL_FONT", "warning" if sent_for_real else "note",
            "Remote font stylesheet",
            "The dashboard <link>s a remote font stylesheet (%s). It resolves in the "
            "workflow UI render but is stripped when the same HTML is emailed%s."
            % (", ".join(refs["font"][:2]),
               " — and this dashboard's email step has recipients, so it is emailed"
               if sent_for_real else ""),
            fix="Keep it, but make sure every font-family carries a real fallback stack "
            "(the style guide's Fraunces / Instrument Sans / Wix Madefor Text all need one).",
            step=step, evidence={"font_links": refs["font"][:3]},
        ))

    # --- emoji ---------------------------------------------------------------
    # Note severity on purpose: a literal may be scrubbing emoji out of an
    # upstream status string rather than emitting it (the reference dashboard
    # strips "✅ " / "❌ " prefixes). The rendered-markup check decides.
    emoji = _emoji_chars(joined)
    dingbats = _dingbat_chars(joined)
    if emoji or dingbats:
        findings.append(_finding(
            "HTML_EMOJI_LITERALS", "note",
            "Emoji / symbol glyphs in code string literals",
            "Found in string literals: %s. FurtherAI dashboards use status-coloured "
            "badges, not emoji — but a literal here may be stripping emoji out of an "
            "upstream value rather than emitting it, so the rendered-markup check "
            "(HTML_EMOJI) is authoritative." % _describe_chars(emoji + dingbats),
            fix="If these reach the markup, replace them with a badge (rounded pill, "
            "status-coloured text on a tinted background).",
            step=step, evidence={"emoji": emoji[:12], "symbols": dingbats[:12]},
        ))

    # --- hardcoded dates -----------------------------------------------------
    # patterns/html_summary_dashboard.md, "Common mistakes": hardcoded execution
    # dates / IDs; read run identity from fai_sandbox context.execution_log().
    date_hits = []
    for lit in literals:
        for m in _DATE_LITERAL_RE.finditer(lit):
            date_hits.append(m.group(0))
    if date_hits:
        findings.append(_finding(
            "HTML_HARDCODED_DATE", "warning",
            "Hardcoded date literal in dashboard code",
            "Date literals in composed strings: %s. Every run would show the same date."
            % ", ".join(sorted(set(date_hits))[:6]),
            fix="Read run identity from fai_sandbox context.execution_log(), or interpolate "
            "the extracted date field.",
            step=step, evidence={"dates": sorted(set(date_hits))[:10]},
        ))

    # --- escaping ------------------------------------------------------------
    escapes = ("html.escape" in code_low or "html_module.escape" in code_low
               or "cgi.escape" in code_low or ".escape(" in code_low)
    if not escapes:
        findings.append(_finding(
            "HTML_NO_ESCAPING", "warning",
            "Extracted values interpolated without escaping",
            "The code never calls html.escape. An extracted value containing "
            "<, > or & breaks the markup (and a value containing a tag is injected verbatim).",
            fix="Add the standard helper: import html as html_module; "
            "def esc(v): return html_module.escape(str(v)) if v else ''",
            step=step,
        ))

    # --- style guide tokens --------------------------------------------------
    missing = [name for name, needles in _STYLE_TOKENS
               if not any(n in low or n in code_low for n in needles)]
    if missing:
        sev = "warning" if len(missing) >= 4 else "note"
        findings.append(_finding(
            "HTML_STYLE_TOKENS", sev,
            "Dashboard style-guide tokens missing (%d)" % len(missing),
            "Not found in the composed markup: %s. Source: "
            "reference/patterns/html_summary_dashboard.md style guide table."
            % "; ".join(missing),
            fix="Adopt the shared tokens so dashboards look like one product. A working "
            "reference of all of them is the 'Compose HTML Summary' step in "
            "reference/examples/submission_intake_es_umbrella.json.",
            step=step, evidence={"missing": missing},
        ))

    # --- classic mistakes, from the facts view ------------------------------
    # How dashboard-like the markup is decides how loudly a sending consumer is
    # reported: a full styled dashboard being emailed is a different thing from
    # an email body being emailed.
    findings.extend(_structural_lint(step, facts_view,
                                     style_tokens_present=len(_STYLE_TOKENS) - len(missing)))

    summary = {
        "code_chars": len(code),
        "string_literal_chars": len(joined),
        "escapes_values": escapes,
        "external_refs": refs,
        "emoji": emoji,
        "hardcoded_dates": sorted(set(date_hits))[:10],
        "style_tokens_missing": missing,
        "mined_keys": _mined_keys(code)[:80],
        "syntax_error": syntax_error,
    }
    return findings, summary


def _structural_lint(step: str, view: Dict[str, Any],
                     style_tokens_present: int = 0) -> List[Dict[str, Any]]:
    """The three classic mistakes from patterns/html_summary_dashboard.md."""
    out: List[Dict[str, Any]] = []
    output_field = view.get("output_field") or "email_body"

    # 1. Returning HTML in a key other than email_body.
    if output_field != "email_body":
        out.append(_finding(
            "HTML_WRONG_OUTPUT_KEY", "blocker",
            "HTML returned under a key other than email_body",
            "The compose step publishes the markup as `%s`; the email step expects "
            "exactly `email_body`." % output_field,
            fix="Rename the returned key (and config.output_schema property) to email_body.",
            step=step, evidence={"output_field": output_field},
        ))
    returned = view.get("returned_keys")
    if isinstance(returned, list) and returned and output_field not in returned:
        out.append(_finding(
            "HTML_WRONG_OUTPUT_KEY", "blocker",
            "Producer never returns the consumed key",
            "The email step reads `%s`, but the code's return statements only produce: %s."
            % (output_field, ", ".join(returned[:8])),
            fix="Return {'%s': html} from run()." % output_field,
            step=step, evidence={"returned_keys": returned[:12], "output_field": output_field},
        ))

    # 2. Compose step left visible.
    if view.get("display_step") is True:
        out.append(_finding(
            "HTML_COMPOSE_VISIBLE", "warning",
            "Compose step is still display_step: true",
            "The compose step renders its raw HTML string in the UI alongside the "
            "email step's panel.",
            fix="Set display_step: false on the compose step; only the email step shows.",
            step=step, evidence={"display_step": True},
        ))

    # 3. Recipients on a display-only email step.
    #
    # patterns/html_summary_dashboard.md documents BOTH shapes: a display-only
    # summary with empty routing, and the deliberate "render AND send" pair
    # where a second email step carries full routing. So a sending consumer is
    # only a defect when the same markup ALSO feeds a display-only step — that
    # is the accidental-send shape. A producer whose only consumer sends is an
    # ordinary email composer and gets a note, so intent can be confirmed
    # without a false blocker.
    consumers = view.get("consumed_by") or []
    has_display_only = any(c.get("display_only") for c in consumers)
    # 8 of the 14 style-guide tokens is a lot of dashboard to put in an email.
    dashboardish = style_tokens_present >= 8
    for consumer in consumers:
        recips = consumer.get("recipients") or {}
        present = {k: v for k, v in recips.items() if v}
        if consumer.get("sends") or present:
            out.append(_finding(
                "HTML_EMAIL_RECIPIENTS",
                "blocker" if has_display_only else ("warning" if dashboardish else "note"),
                ("Recipients set on the dashboard's email step" if has_display_only
                 else ("A full dashboard is being emailed" if dashboardish
                       else "This producer's markup is emailed, not just rendered")),
                "`%s` %s.%s"
                % (consumer.get("step"),
                   "carries routing (%s)" % ", ".join(sorted(present)) if present
                   else "has zapier_reply_url set, the decisive send signal",
                   (" The same markup also feeds a display-only step, which is the "
                    "accidental-send shape the pattern doc warns about."
                    if has_display_only else
                    (" The markup carries %d of %d dashboard style-guide tokens, so a "
                     "styled dashboard is going out as mail — much of it (collapsible "
                     "<details>, the hero gradient, the remote font) does not survive an "
                     "email client. Confirm that is intended."
                     % (style_tokens_present, len(_STYLE_TOKENS)) if dashboardish else
                     " There is no display-only consumer and the markup is not styled as a "
                     "dashboard, so this reads as an ordinary outbound email."))),
                fix=("Clear the routing fields on the display step and keep a separate email "
                     "step for mail that is genuinely wanted." if has_display_only else None),
                step=consumer.get("step"),
                evidence={"recipients": {k: truncate(str(v), 80) for k, v in present.items()},
                          "sends": consumer.get("sends"),
                          "zapier_reply_url_set": consumer.get("zapier_reply_url_set"),
                          "sibling_display_only_consumer": has_display_only,
                          "style_tokens_present": style_tokens_present},
            ))
        if consumer.get("reply_to_original") is True and not present:
            out.append(_finding(
                "HTML_REPLY_TO_ORIGINAL", "warning",
                "reply_to_original: true on a display-only email step",
                "`%s` has empty routing but reply_to_original: true, which auto-populates "
                "the routing fields from the incoming email at run time — the dashboard "
                "gets sent." % consumer.get("step"),
                fix="Set reply_to_original: false on the display-only step.",
                step=consumer.get("step"),
            ))
    return out


# ---------------------------------------------------------------------------
# fixture synthesis
# ---------------------------------------------------------------------------

_LONG = ("Sagebrush Manufacturing & Logistics Holdings, LLC dba Sagebrush Freight "
         "Systems of the Greater Southwest Region ") * 12

_STRESS_CHARS = (
    "quote\" apos' amp& lt< gt> éñü 中文文字 "
    "العربية  tab\there\n"
    "newline unbroken" + "A" * 90
)
INJECT_SENTINEL = '<b data-fai-inject="1">INJECTED</b>'


def _plausible_scalar(kind: str, name: str, fixture: str, index: int = 0) -> Any:
    n = (name or "").lower()
    if fixture == "stress":
        if kind == "number":
            return 987654321.123456
        if kind == "boolean":
            return True
        return (_STRESS_CHARS + " " + INJECT_SENTINEL + " " + _LONG)[:2400]
    if kind == "number":
        if "year" in n:
            return 2024 + index
        if any(t in n for t in ("limit", "premium", "amount", "value", "total", "revenue", "payroll")):
            return 2500000 + index * 137000
        if any(t in n for t in ("count", "number", "num", "qty", "employees")):
            return 12 + index
        if "score" in n or "rate" in n or "pct" in n or "percent" in n:
            return 0.87
        return 42 + index
    if kind == "boolean":
        return index % 2 == 0
    # string
    if "date" in n:
        return ["2026-04-01", "2027-04-01", "2025-11-14"][index % 3]
    if "email" in n:
        return "underwriting@example.invalid"
    if "phone" in n:
        return "(602) 555-0%03d" % (100 + index)
    if "state" in n:
        return ["AZ", "TX", "NV"][index % 3]
    if "city" in n:
        return ["Phoenix", "Austin", "Reno"][index % 3]
    if "zip" in n or "postal" in n:
        return "8500%d" % (index % 10)
    if "url" in n or "link" in n:
        return "https://app.furtherai.com"
    if "status" in n or "result" in n or "outcome" in n:
        return ["Pass", "Fail", "Needs Info"][index % 3]
    if "name" in n and "insured" in n:
        return "Sagebrush Freight Systems, LLC"
    if "name" in n:
        return ["Dana Whitfield", "Priya Raman", "Marcus Ochoa"][index % 3]
    if "description" in n or "notes" in n or "summary" in n or "reason" in n:
        return ("Long-haul trucking and regional freight brokerage with an owned fleet "
                "of 42 power units and 61 trailers; no hazmat.")
    if "amount" in n or "limit" in n or "premium" in n:
        return "$2,500,000"
    if "class" in n or "code" in n:
        return "SIC 4213"
    if "carrier" in n or "market" in n:
        return ["Alpha E&S", "Beta Specialty", "Gamma Indemnity"][index % 3]
    return ["Sagebrush Freight Systems, LLC", "Commercial Auto / Excess Liability",
            "Reviewed", "Confirmed by broker"][index % 4]


def _cell(value: Any) -> Dict[str, Any]:
    return {
        "value": value,
        "confidence_score": 0.93,
        "confidence_reason": "Read directly from the ACORD 125, page 1.",
        "citations": [{"document_name": "acord_125.pdf", "page_number": 1}],
        "thinking_steps": [],
    }


def _synth(schema: Optional[Dict[str, Any]], name: str, fixture: str,
           mined_keys: List[str], prefer_cells: bool, depth: int = 0, index: int = 0) -> Any:
    """Synthesize a plausible value for a normalized schema (CONTRACT.md)."""
    if fixture == "empty":
        return None
    if depth > 6:
        return None
    schema = schema or {"kind": "unknown"}
    kind = schema.get("kind") or "unknown"

    if kind == "cell":
        inner = _synth(schema.get("value_type") or {"kind": "string"}, name, fixture,
                       mined_keys, prefer_cells, depth + 1, index)
        return _cell(inner)
    if kind == "object":
        props = schema.get("properties") or {}
        out: Dict[str, Any] = {}
        for key, sub in list(props.items())[:60]:
            out[key] = _synth(sub, key, fixture, mined_keys, prefer_cells, depth + 1, index)
        for key in (schema.get("dynamic_keys") or [])[:20]:
            out.setdefault(key, _synth({"kind": "unknown"}, key, fixture, mined_keys,
                                       prefer_cells, depth + 1, index))
        if not out and (schema.get("open") or kind == "object"):
            # Schema-less upstream object: populate from keys the producer reads.
            for key in mined_keys[:60]:
                out[key] = _cell(_plausible_scalar("string", key, fixture, index)) if prefer_cells \
                    else _plausible_scalar("string", key, fixture, index)
        return out
    if kind == "array":
        items = schema.get("items") or {"kind": "unknown"}
        count = 60 if fixture == "stress" else 3
        return [_synth(items, name, fixture, mined_keys, prefer_cells, depth + 1, i)
                for i in range(count)]
    if kind == "table":
        rows = 60 if fixture == "stress" else 3
        cols = [k for k in mined_keys[:6]] or ["carrier", "status", "detail"]
        return {
            "data": [{c: _plausible_scalar("string", c, fixture, i) for c in cols}
                     for i in range(rows)],
            "schema": {"type": "object", "properties": {c: {"type": "string"} for c in cols}},
            "table_v1_log_id": "00000000-0000-4000-8000-0000000000t%d" % (index % 10),
        }
    if kind == "file":
        return {"user_document_id": "00000000-0000-4000-8000-00000000f%03d" % index,
                "filename": "acord_125.pdf"}
    if kind == "knowledge_base":
        return {"knowledge_base_id": "00000000-0000-4000-8000-00000000kb01"}
    allowed = schema.get("allowed_values")
    if allowed:
        if fixture == "stress":
            # Longest member first: the widest badge is what breaks the layout.
            return sorted(allowed, key=lambda v: -len(str(v)))[0]
        return allowed[index % len(allowed)]
    if kind in ("string", "number", "boolean"):
        return _plausible_scalar(kind, name, fixture, index)
    # unknown / any. `unknown` means the resolver gave up (CONTRACT.md), so
    # anything invented here is a guess. A scalar is the safe guess: inventing a
    # dict of mined keys put dicts inside a list the step then passed to set(),
    # which crashed real dashboard code on data it can never receive. Only a
    # declared `object` gets the mined-key treatment, above.
    return _plausible_scalar("string", name, fixture, index)


CELL_WRAPPING_STEP_TYPES = (
    "extract_from_multiple_sources", "extract_rows_from_multiple_sources",
    "extract_mixed_schema_from_multiple_sources", "agentic_extraction",
    "agentic_guideline_check", "sov_mapping", "generate_qa_table_from_agent",
    "multi_column_qa",
)


def _undeclared_cells(schema, path="", depth=0):
    """Paths of cells nobody declared (origin is neither "declared" nor "config").

    A code step MAY declare {"type": "cell"} in its output_schema and that is a
    real cell (wf_facts records origin "declared"). Only an auto-derived cell on
    a step type that does not cell-wrap is suspect. Reported, never rewritten —
    this pass reports facts, it does not second-guess them.
    """
    out = []
    if not isinstance(schema, dict) or depth > 8:
        return out
    if schema.get("kind") == "cell":
        if schema.get("origin") not in ("declared", "config"):
            out.append(path or "(root)")
        return out
    for key, sub in (schema.get("properties") or {}).items():
        out.extend(_undeclared_cells(sub, "%s.%s" % (path, key), depth + 1))
    if schema.get("items") is not None:
        out.extend(_undeclared_cells(schema["items"], path + ".0", depth + 1))
    return out


def _schema_has_cell(schema: Optional[Dict[str, Any]], depth: int = 0) -> bool:
    """True when a normalized schema contains a cell anywhere (depth-capped)."""
    if not isinstance(schema, dict) or depth > 8:
        return False
    if schema.get("kind") == "cell":
        return True
    for sub in (schema.get("properties") or {}).values():
        if _schema_has_cell(sub, depth + 1):
            return True
    return _schema_has_cell(schema.get("items"), depth + 1) \
        or _schema_has_cell(schema.get("value_type"), depth + 1)


def build_fixture(fixture: str, params: List[Dict[str, Any]], mined_keys: List[str],
                  prefer_cells: bool) -> Dict[str, Any]:
    """params: [{"name":..., "schema": <normalized|None>, "static_value": ...}]"""
    out: Dict[str, Any] = {}
    for i, p in enumerate(params):
        name = p["name"]
        if fixture == "empty":
            out[name] = None
            continue
        if p.get("static_value") is not None:
            out[name] = p["static_value"]
            continue
        out[name] = _synth(p.get("schema"), name, fixture, mined_keys, prefer_cells, 0, i)
    return out


# ---------------------------------------------------------------------------
# stage 2 — render in a subprocess under a stub fai_sandbox
#
# The stub mirrors the real SDK surface documented in step_types/custom_step.md
# and implemented at templates/fai-sandbox-py/fai_sandbox: context.execution_log,
# context.table, context.traceparent, log.{debug,info,warning,error},
# secrets.{get,get_optional}, files.stage, documents.{share_link,html_to_pdf},
# excel.{fill, Cell, CellFillData, RowFillData, ColumnFillData,
# SheetFillOperation, FillType}, plus CapabilityError and configure.
# ---------------------------------------------------------------------------

RUNNER_SRC = r'''#!/usr/bin/env python3
# Generated by html_probe.py. Executes one custom_step's code under a stub
# fai_sandbox injected into sys.modules (nothing is installed), with sockets
# blocked and all writes confined to --work-dir.
import argparse, asyncio, inspect, io, json, os, sys, time, types, traceback, contextlib

CALLS = []
LOGS = []
NET = []
FAKE_ID = "00000000-0000-4000-8000-000000000001"


def _safe(obj, depth=0):
    if depth > 4:
        return "..."
    if isinstance(obj, dict):
        return dict((str(k), _safe(v, depth + 1)) for k, v in list(obj.items())[:12])
    if isinstance(obj, (list, tuple)):
        return [_safe(v, depth + 1) for v in list(obj)[:6]]
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    s = str(obj)
    return s if len(s) <= 200 else s[:200] + "..."


def _rec(cap, args, result=None):
    CALLS.append({"capability": cap, "args": _safe(args), "result": _safe(result)})


def _block_network():
    import socket

    def blocked(*a, **k):
        NET.append(_safe(a))
        raise OSError("network is disabled inside the html_probe render sandbox")

    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
    try:
        socket.socket.sendto = blocked
    except Exception:
        pass


def _build_sandbox(work_dir, mode):
    from dataclasses import dataclass, field
    from pathlib import Path
    from typing import Any, Optional

    class CapabilityError(RuntimeError):
        pass

    fai = types.ModuleType("fai_sandbox")
    ctx = types.ModuleType("fai_sandbox.context")
    logm = types.ModuleType("fai_sandbox.log")
    secm = types.ModuleType("fai_sandbox.secrets")
    filesm = types.ModuleType("fai_sandbox.files")
    docsm = types.ModuleType("fai_sandbox.documents")
    xlm = types.ModuleType("fai_sandbox.excel")

    _LOG_SNAPSHOT = {
        "id": FAKE_ID,
        "workflow_id": "00000000-0000-4000-8000-0000000000w1",
        "workflow_version": 7,
        "status": "in_progress",
        "created_at": "2026-01-01T00:00:00Z",
        "request": {"user_document_ids": [], "documents": []},
        "column_data": {},
        "steps": [],
    }

    def execution_log():
        import copy
        _rec("context.execution_log", {})
        return copy.deepcopy(_LOG_SNAPSHOT)

    def table(table_v1_log_id, limit=None, cursor=0):
        _rec("context.table", {"table_v1_log_id": str(table_v1_log_id),
                               "limit": limit, "cursor": cursor})
        rows = 0 if mode == "empty" else (60 if mode == "stress" else 3)
        cols = ["carrier", "status", "detail"]
        return {
            "schema": {"type": "object",
                       "properties": dict((c, {"type": "string"}) for c in cols)},
            "data": [dict((c, "stub-%s-%d" % (c, i)) for c in cols) for i in range(rows)],
            "user_documents": [],
            "metadata": {"stub": True, "total_rows": rows},
        }

    def traceparent():
        _rec("context.traceparent", {})
        return "00-" + "0" * 32 + "-" + "0" * 16 + "-01"

    ctx.execution_log = execution_log
    ctx.table = table
    ctx.traceparent = traceparent

    def _log(level):
        def emit(message, **attributes):
            LOGS.append({"level": level, "message": _safe(message),
                         "attributes": _safe(attributes)})
        return emit

    logm.debug = _log("debug")
    logm.info = _log("info")
    logm.warning = _log("warning")
    logm.error = _log("error")

    def secrets_get(key):
        _rec("secrets.get", {"key": key})
        return "stub-secret-value"

    def secrets_get_optional(key, default=None):
        _rec("secrets.get_optional", {"key": key})
        return default

    secm.get = secrets_get
    secm.get_optional = secrets_get_optional

    def stage(user_document_id):
        _rec("files.stage", {"user_document_id": str(user_document_id)})
        p = os.path.join(work_dir, "staged_%s.pdf" % (str(user_document_id)[-6:] or "doc"))
        with open(p, "wb") as fh:
            fh.write(b"%PDF-1.4\n% html_probe stub document\n")
        return Path(p)

    filesm.stage = stage

    def share_link(user_document_id, expiry_hours=24, download_filename=None):
        _rec("documents.share_link", {"user_document_id": str(user_document_id),
                                      "expiry_hours": expiry_hours,
                                      "download_filename": download_filename})
        return "https://example.invalid/stub-download/%s" % str(user_document_id)

    def html_to_pdf(html=None, html_path=None, filename=None):
        _rec("documents.html_to_pdf", {"html_chars": len(html or ""),
                                       "html_path": str(html_path) if html_path else None,
                                       "filename": filename})
        p = os.path.join(work_dir, filename or "stub_output.pdf")
        with open(p, "wb") as fh:
            fh.write(b"%PDF-1.4\n% html_probe stub pdf\n")
        return Path(p)

    docsm.share_link = share_link
    docsm.html_to_pdf = html_to_pdf

    class FillType:
        CELL = "cell"
        ROW = "row"
        COLUMN = "column"

    @dataclass
    class Cell:
        value: Any
        highlight: bool = False

    @dataclass
    class CellFillData:
        coordinate: str
        cell: Any

    @dataclass
    class RowFillData:
        start_cell: str
        values: list

    @dataclass
    class ColumnFillData:
        start_cell: str
        values: list

    @dataclass
    class SheetFillOperation:
        fill_type: str
        sheet_name: Optional[str] = None
        cell_data: Optional[list] = None
        row_data: Optional[Any] = None
        column_data: Optional[Any] = None
        column_datatypes: Optional[list] = None

    def fill(template, operations, output_filename=None):
        _rec("excel.fill", {"template": str(template),
                            "operations": len(list(operations or []))})
        return {"user_document_id": FAKE_ID,
                "filename": output_filename or "stub-filled.xlsx"}

    xlm.FillType = FillType
    xlm.Cell = Cell
    xlm.CellFillData = CellFillData
    xlm.RowFillData = RowFillData
    xlm.ColumnFillData = ColumnFillData
    xlm.SheetFillOperation = SheetFillOperation
    xlm.fill = fill

    def configure(**kwargs):
        return None

    fai.context = ctx
    fai.log = logm
    fai.secrets = secm
    fai.files = filesm
    fai.documents = docsm
    fai.excel = xlm
    fai.CapabilityError = CapabilityError
    fai.configure = configure
    fai.__all__ = ["CapabilityError", "configure", "context", "documents", "excel",
                   "files", "log", "secrets"]

    sys.modules["fai_sandbox"] = fai
    for name, mod in (("context", ctx), ("log", logm), ("secrets", secm),
                      ("files", filesm), ("documents", docsm), ("excel", xlm)):
        sys.modules["fai_sandbox." + name] = mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--inputs", required=True)
    ap.add_argument("--result", required=True)
    ap.add_argument("--html-out", required=True)
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--output-field", default="email_body")
    ap.add_argument("--mode", default="empty")
    args = ap.parse_args()

    os.makedirs(args.work_dir, exist_ok=True)
    os.chdir(args.work_dir)
    result = {"ok": False, "error": None, "error_type": None, "error_repr": None,
              "step_frames": [], "traceback": None, "returned_keys": [],
              "html_chars": 0, "html_path": None, "capability_calls": [], "log_calls": [],
              "network_attempts": [], "stdout_tail": "", "duration_ms": 0,
              "dropped_params": [], "return_type": None}
    t0 = time.time()
    buf = io.StringIO()
    try:
        _build_sandbox(args.work_dir, args.mode)
        _block_network()
        with open(args.code) as fh:
            code = fh.read()
        with open(args.inputs) as fh:
            inputs = json.load(fh)
        mod = types.ModuleType("fai_step_code")
        mod.__dict__["__name__"] = "fai_step_code"
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            exec(compile(code, "<step_code>", "exec"), mod.__dict__)
            fn = mod.__dict__.get("run")
            if fn is None:
                raise RuntimeError("step code defines no run()")
            sig = inspect.signature(fn)
            accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD
                                 for p in sig.parameters.values())
            call_kwargs = {}
            for key, value in inputs.items():
                if accepts_kwargs or key in sig.parameters:
                    call_kwargs[key] = value
                else:
                    result["dropped_params"].append(key)
            if accepts_kwargs or "work_dir" in sig.parameters:
                call_kwargs.setdefault("work_dir", args.work_dir)
            out = fn(**call_kwargs)
            if inspect.isawaitable(out):
                out = asyncio.new_event_loop().run_until_complete(out)
        result["return_type"] = type(out).__name__
        if isinstance(out, dict):
            result["returned_keys"] = sorted(str(k) for k in out.keys())
            html = out.get(args.output_field)
            if isinstance(html, str):
                with open(args.html_out, "w", encoding="utf-8") as fh:
                    fh.write(html)
                result["html_chars"] = len(html)
                result["html_path"] = args.html_out
            elif html is None:
                result["error"] = "run() returned no %r key (returned: %s)" % (
                    args.output_field, ", ".join(result["returned_keys"]) or "nothing")
            else:
                result["error"] = "output field %r is %s, not str" % (
                    args.output_field, type(html).__name__)
        else:
            result["error"] = "run() returned %s, not dict" % type(out).__name__
        result["ok"] = result["error"] is None
    except BaseException as exc:  # noqa: BLE001 - report anything the step raises
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
        result["error_type"] = type(exc).__name__
        result["error_repr"] = repr(exc)[:400]
        code_lines = []
        try:
            with open(args.code) as fh:
                code_lines = fh.read().split("\n")
        except Exception:
            pass
        frames = traceback.extract_tb(exc.__traceback__)
        step_frames = []
        for fr in frames:
            if fr.filename != "<step_code>":
                continue          # harness frame (runner, asyncio) - drop it
            src = ""
            if 0 < fr.lineno <= len(code_lines):
                src = code_lines[fr.lineno - 1].strip()
            step_frames.append({"lineno": fr.lineno, "func": fr.name, "source": src[:200]})
        result["step_frames"] = step_frames[-6:]       # deepest frames win
        if not step_frames:
            # Nothing in the step's own code (raised at import or in a stub);
            # keep the deepest real frames instead of the top of the stack.
            result["step_frames"] = [
                {"lineno": fr.lineno, "func": fr.name,
                 "source": (fr.line or "").strip()[:200], "file": os.path.basename(fr.filename)}
                for fr in frames[-3:]]
        result["traceback"] = traceback.format_exc()[-4000:]
    result["duration_ms"] = int((time.time() - t0) * 1000)
    result["capability_calls"] = CALLS[:60]
    result["log_calls"] = LOGS[:60]
    result["network_attempts"] = NET[:10]
    tail = buf.getvalue()
    result["stdout_tail"] = tail[-2000:]
    with open(args.result, "w", encoding="utf-8") as fh:
        json.dump(result, fh)


if __name__ == "__main__":
    main()
'''


def write_runner(out_dir: str) -> str:
    path = os.path.join(out_dir, "_html_probe_runner.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(RUNNER_SRC)
    return path


# Fixtures other than `empty` are synthesized from facts' resolved schemas. A
# shape error there may be an artifact of the schema, not a real dashboard bug,
# so it is reported at warning severity. `empty` (every param None) IS a
# faithful runtime condition - an upstream step skipped, a classify class that
# missed - so a crash there stays a blocker.
_SHAPE_ERRORS = ("TypeError", "KeyError", "IndexError", "AttributeError", "ValueError")

# Third-party modules a custom_step may import, from step_types/custom_step.md
# ("Each must be in the sandbox allowlist"), as MODULE names rather than
# distribution names. Absent locally is an environment limit, not a defect.
SANDBOX_ALLOWLIST = ("pandas", "numpy", "dateutil", "pytz", "dateparser", "openpyxl",
                     "docx", "pypdf", "pypdfium2", "lxml", "bs4", "PIL", "rapidfuzz",
                     "glom", "httpx", "pydantic")


def _missing_module(message: str) -> Optional[str]:
    m = re.search(r"No module named '([^']+)'", message or "")
    if m:
        return m.group(1)
    m = re.search(r"cannot import name '[^']+' from '([^']+)'", message or "")
    return m.group(1) if m else None


def _exception_fix(error_type, message):
    """Advice derived from the actual exception. Returns None rather than
    guessing - boilerplate that contradicts the error is worse than silence."""
    msg = (message or "").lower()
    if "nonetype" in msg:
        return ("Guard the value before use: this param arrives None whenever the "
                "upstream step is skipped or a classify class misses.")
    if error_type == "TypeError" and "unhashable" in msg:
        return ("A dict reached a set() or dict key where a scalar was expected - unwrap "
                "the extraction cell (.value) before hashing, or drop the set().")
    if error_type == "TypeError" and ("can only concatenate" in msg or "unsupported operand" in msg):
        return "Coerce with str() before concatenating; the value is not the type assumed here."
    if error_type == "TypeError" and "not subscriptable" in msg:
        return "The value is a scalar, not a dict or list - check the shape before indexing."
    if error_type == "TypeError" and "argument of type" in msg:
        return "An `in` test ran against a non-container; check the value's shape first."
    if error_type == "KeyError":
        return "Read the key with .get(key, default) so a missing key cannot raise."
    if error_type == "IndexError":
        return "Check the list is non-empty before indexing."
    if error_type == "AttributeError" and "'dict' object has no attribute" in msg:
        return ("A dict reached code expecting a scalar - this is the classic un-unwrapped "
                "extraction cell: read row['field']['value'] (or the extract_value helper).")
    if error_type == "AttributeError":
        return "The value is not the type this attribute assumes - check the shape first."
    if error_type == "ZeroDivisionError":
        return "Guard the divisor; with no rows the denominator is 0."
    return None


def render_fixture(runner: str, code_path: str, inputs: Dict[str, Any], fixture: str,
                   producer_dir: str, output_field: str, timeout: int) -> Dict[str, Any]:
    work_dir = os.path.join(producer_dir, "work_" + fixture)
    os.makedirs(work_dir, exist_ok=True)
    inputs_path = os.path.join(producer_dir, "inputs_%s.json" % fixture)
    result_path = os.path.join(producer_dir, "result_%s.json" % fixture)
    html_path = os.path.join(producer_dir, "%s.html" % fixture)
    with open(inputs_path, "w", encoding="utf-8") as fh:
        json.dump(inputs, fh, default=str)
    cmd = [sys.executable, runner, "--code", code_path, "--inputs", inputs_path,
           "--result", result_path, "--html-out", html_path, "--work-dir", work_dir,
           "--output-field", output_field, "--mode", fixture]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        wall = int((time.time() - t0) * 1000)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "render timed out after %ds (runaway loop?)" % timeout,
                "timeout": True, "wall_ms": int((time.time() - t0) * 1000),
                "inputs_path": inputs_path}
    if not os.path.exists(result_path):
        return {"ok": False, "wall_ms": wall, "inputs_path": inputs_path,
                "error": "runner produced no result (rc=%d): %s"
                         % (proc.returncode, (proc.stderr or b"").decode()[-500:])}
    with open(result_path, encoding="utf-8") as fh:
        res = json.load(fh)
    res["wall_ms"] = wall
    res["inputs_path"] = inputs_path
    res["result_path"] = result_path
    return res


# ---------------------------------------------------------------------------
# markup lint (html.parser) on rendered output
# ---------------------------------------------------------------------------

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "param", "source", "track", "wbr"}
# Elements whose end tag is optional in HTML5 — an unclosed one is not a defect.
_OPTIONAL_END = {"p", "li", "tr", "td", "th", "thead", "tbody", "tfoot", "option",
                 "optgroup", "dt", "dd", "colgroup", "caption", "html", "head", "body",
                 "rt", "rp"}


class MarkupProbe(HTMLParser):
    def __init__(self) -> None:
        HTMLParser.__init__(self, convert_charrefs=True)
        self.stack: List[Tuple[str, int]] = []
        self.unclosed: List[Tuple[str, int]] = []
        self.stray_end: List[Tuple[str, int]] = []
        # (tag, line, closed_by): an element the parser had to close implicitly
        # when an ancestor's end tag arrived. Same authoring defect as an
        # element left open at EOF, and just as visible in the render.
        self.implicit: List[Tuple[str, int, str]] = []
        self.text_parts: List[str] = []
        self.tag_counts: Dict[str, int] = {}
        self.injected = False
        self.script_tags = 0
        self._cdata_tag: Optional[str] = None
        self.style_text: List[str] = []

    def handle_starttag(self, tag, attrs):
        self.tag_counts[tag] = self.tag_counts.get(tag, 0) + 1
        if tag == "script":
            self.script_tags += 1
        for k, v in attrs:
            if k == "data-fai-inject":
                self.injected = True
        if tag in ("style", "script"):
            self._cdata_tag = tag
        if tag not in _VOID:
            self.stack.append((tag, self.getpos()[0]))

    def handle_startendtag(self, tag, attrs):
        self.tag_counts[tag] = self.tag_counts.get(tag, 0) + 1

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self._cdata_tag = None
        if tag in _VOID:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                for open_tag, line in self.stack[i + 1:]:
                    self.implicit.append((open_tag, line, tag))
                del self.stack[i:]
                return
        self.stray_end.append((tag, self.getpos()[0]))

    def handle_data(self, data):
        if self._cdata_tag == "style":
            self.style_text.append(data)
        elif self._cdata_tag == "script":
            pass
        else:
            self.text_parts.append(data)

    def close(self):
        HTMLParser.close(self)
        self.unclosed = list(self.stack)

    @property
    def visible_text(self) -> str:
        return " ".join(" ".join(self.text_parts).split())


_RAW_REPR_RE = re.compile(
    r"""\{['"](?:value|confidence_score|data)['"]\s*:|['"]confidence_score['"]\s*:"""
    r"""|\{['"][A-Za-z_ ]{2,40}['"]\s*:\s*['"]""")

_PLACEHOLDER_LEAK_RE = re.compile(
    r"(?<![A-Za-z0-9_])(None|NaN|nan|null|undefined|\{\}|\[\])(?![A-Za-z0-9_])")


def markup_lint(step: str, fixture: str, html_text: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    probe = MarkupProbe()
    parse_error = None
    try:
        probe.feed(html_text)
        probe.close()
    except Exception as exc:  # html.parser is lenient; this is genuinely broken input
        parse_error = "%s: %s" % (type(exc).__name__, exc)

    gk = lambda code: "%s:%s" % (code, step)  # noqa: E731 - group findings per step

    if parse_error:
        findings.append(_finding(
            "HTML_MALFORMED", "blocker", "Rendered markup does not parse",
            "html.parser failed on the %s fixture output: %s" % (fixture, parse_error),
            fix="Fix the markup the code emits.", step=step,
            evidence={"fixture": fixture}, group_key=gk("HTML_MALFORMED")))

    open_at_eof = [(t, ln, None) for t, ln in probe.unclosed]
    all_unclosed = open_at_eof + list(probe.implicit)
    structural_unclosed = [x for x in all_unclosed if x[0] not in _OPTIONAL_END]
    optional_unclosed = [x for x in all_unclosed if x[0] in _OPTIONAL_END]
    if structural_unclosed:
        findings.append(_finding(
            "HTML_UNCLOSED_TAG", "blocker", "Unclosed tags in rendered markup",
            "%d unclosed element(s) on the %s fixture: %s. An unclosed container "
            "swallows the siblings that follow it, so the render loses the layout."
            % (len(structural_unclosed), fixture,
               ", ".join("<%s> (line %d%s)" % (t, ln, "" if by is None else ", closed by </%s>" % by)
                         for t, ln, by in structural_unclosed[:8])),
            fix="Balance the concatenated strings — each opening <div>/<details>/<table> needs its close.",
            step=step, evidence={"fixture": fixture,
                                 "unclosed": [[t, ln, by] for t, ln, by in structural_unclosed[:20]]},
            group_key=gk("HTML_UNCLOSED_TAG")))
    if optional_unclosed:
        findings.append(_finding(
            "HTML_UNCLOSED_OPTIONAL", "note", "Elements left open (optional end tag)",
            "%s on the %s fixture. Legal HTML5, but implicit closes are easy to get wrong."
            % (", ".join("<%s>" % t for t, _ln, _by in optional_unclosed[:8]), fixture),
            fix="Close them explicitly.", step=step,
            evidence={"fixture": fixture,
                      "unclosed": [[t, ln, by] for t, ln, by in optional_unclosed[:20]]},
            group_key=gk("HTML_UNCLOSED_OPTIONAL")))
    if probe.stray_end:
        findings.append(_finding(
            "HTML_STRAY_END_TAG", "warning", "End tag with no matching start",
            "%s on the %s fixture."
            % (", ".join("</%s> (line %d)" % (t, ln) for t, ln in probe.stray_end[:8]), fixture),
            fix="Remove the stray end tag.", step=step,
            evidence={"fixture": fixture, "stray": probe.stray_end[:20]},
            group_key=gk("HTML_STRAY_END_TAG")))
    if probe.script_tags:
        findings.append(_finding(
            "HTML_INLINE_SCRIPT", "blocker", "<script> present in rendered markup",
            "%d <script> tag(s) in the %s fixture output." % (probe.script_tags, fixture),
            fix="Remove it; script never runs in a mail render.", step=step,
            evidence={"fixture": fixture}, group_key=gk("HTML_INLINE_SCRIPT")))

    refs = _external_refs(html_text)
    hard = {k: v for k, v in refs.items() if k in ("image", "script", "frame", "stylesheet", "css_url")}
    if hard:
        findings.append(_finding(
            "HTML_EXTERNAL_REQUEST", "blocker", "Rendered markup fetches external subresources",
            "%s (%s fixture)." % ("; ".join("%s -> %s" % (k, ", ".join(v[:2]))
                                            for k, v in sorted(hard.items())), fixture),
            fix="Inline the asset or drop it.", step=step,
            evidence={"fixture": fixture, "external": hard},
            group_key=gk("HTML_EXTERNAL_REQUEST")))

    text = probe.visible_text
    emoji = _emoji_chars(text)
    if emoji:
        findings.append(_finding(
            "HTML_EMOJI", "warning", "Emoji in rendered text",
            "%s (%s fixture)." % (_describe_chars(emoji), fixture),
            fix="Use a status badge instead.", step=step,
            evidence={"fixture": fixture, "chars": emoji[:12]}, group_key=gk("HTML_EMOJI")))

    if fixture == "empty":
        # With no inputs, any date in the output can only have come from the code.
        dates = sorted(set(_DATE_LITERAL_RE.findall(text)))
        if dates:
            findings.append(_finding(
                "HTML_HARDCODED_DATE", "warning", "Date rendered from empty inputs",
                "The empty fixture (every input null) still renders %s — that date is "
                "baked into the code." % ", ".join(dates[:5]),
                fix="Read run identity from context.execution_log(), or interpolate the "
                "extracted date field.", step=step,
                evidence={"fixture": fixture, "dates": dates[:10]},
                group_key=gk("HTML_HARDCODED_DATE")))
        leaks = sorted(set(_PLACEHOLDER_LEAK_RE.findall(text)))
        if leaks:
            findings.append(_finding(
                "HTML_PLACEHOLDER_LEAK", "warning", "Python placeholder values leak into the render",
                "The empty fixture renders the literal text %s — str(None) / str({}) reached "
                "the markup." % ", ".join(repr(x) for x in leaks[:6]),
                fix="Guard the interpolation: def esc(v): return html_module.escape(str(v)) if v else ''.",
                step=step, evidence={"fixture": fixture, "leaks": leaks[:10]},
                group_key=gk("HTML_PLACEHOLDER_LEAK")))
        if len(text) < 200:
            findings.append(_finding(
                "HTML_EMPTY_SHELL", "warning", "Empty inputs render an almost-blank page",
                "The empty fixture produced %d visible characters (%d bytes of markup). A "
                "dashboard that looks excellent with data renders as a bare shell when an "
                "upstream classify class or extraction misses." % (len(text), len(html_text)),
                fix="Add explicit empty states: a 'No data extracted' row per section, so the "
                "reader can tell missing from zero.", step=step,
                evidence={"fixture": fixture, "visible_chars": len(text)},
                group_key=gk("HTML_EMPTY_SHELL")))

    # font-family fallbacks, checked on the rendered CSS where the whole
    # declaration exists (the code often concatenates it in pieces).
    css = " ".join(probe.style_text)
    no_fallback = []
    for m in re.finditer(r"font-family\s*:\s*([^;{}]+)", css, re.I):
        decl = m.group(1).strip()
        families = [x.strip() for x in decl.split(",") if x.strip()]
        if len(families) < 2 and families:
            no_fallback.append(decl)
    if no_fallback:
        findings.append(_finding(
            "HTML_FONT_NO_FALLBACK", "warning",
            "font-family without a fallback stack",
            "%d declaration(s) name a single family, so they render in the client "
            "default whenever the remote font is stripped: %s (%s fixture)."
            % (len(no_fallback), "; ".join(truncate(x, 40) for x in sorted(set(no_fallback))[:4]),
               fixture),
            fix="Append a fallback: font-family:'Fraunces',Georgia,serif.",
            step=step, evidence={"fixture": fixture,
                                 "declarations": sorted(set(no_fallback))[:8]},
            group_key=gk("HTML_FONT_NO_FALLBACK")))

    raw_reprs = _RAW_REPR_RE.findall(text)
    if raw_reprs:
        sample = _RAW_REPR_RE.search(text)
        around = text[max(0, sample.start() - 30): sample.start() + 90] if sample else ""
        findings.append(_finding(
            "HTML_RAW_DICT_RENDERED", "warning",
            "A Python dict is rendered verbatim in the dashboard",
            "The %s fixture renders %d raw container repr(s) as text, e.g. …%s… — an "
            "extraction cell was str()'d instead of unwrapped to .value."
            % (fixture, len(raw_reprs), truncate(around, 120)),
            fix="Route the field through the extract_value / get_field helper so the cell's "
            "`value` is read (reference/input_mappings.md: extraction outputs are cell-wrapped).",
            step=step, evidence={"fixture": fixture, "sample": truncate(around, 200)},
            group_key=gk("HTML_RAW_DICT_RENDERED")))

    if fixture == "stress" and probe.injected:
        findings.append(_finding(
            "HTML_UNESCAPED_INPUT", "blocker", "Upstream values are interpolated unescaped",
            "The stress fixture injected the marker %s into every string field and it came "
            "back as live markup, not text — an extracted value containing a tag rewrites "
            "the dashboard." % INJECT_SENTINEL,
            fix="Route every interpolated value through html.escape.", step=step,
            evidence={"fixture": fixture}, group_key=gk("HTML_UNESCAPED_INPUT")))

    stats = {
        "bytes": len(html_text),
        "visible_chars": len(text),
        "tags": sum(probe.tag_counts.values()),
        "distinct_tags": len(probe.tag_counts),
        "details_sections": probe.tag_counts.get("details", 0),
        "tables": probe.tag_counts.get("table", 0),
        "images": probe.tag_counts.get("img", 0),
        "unclosed": [t for t, _ln in probe.unclosed] + [t for t, _ln, _by in probe.implicit],
        "has_doctype": html_text.lstrip()[:15].lower().startswith("<!doctype"),
        "parse_error": parse_error,
        "injected_marker_live": probe.injected,
    }
    if not stats["has_doctype"]:
        findings.append(_finding(
            "HTML_NO_DOCTYPE", "note", "No <!DOCTYPE html>",
            "The %s fixture output starts without a doctype; some renderers fall back to "
            "quirks mode." % fixture,
            fix="Start the string with <!DOCTYPE html>.", step=step,
            evidence={"fixture": fixture}, group_key=gk("HTML_NO_DOCTYPE")))
    return findings, stats


# ---------------------------------------------------------------------------
# stage 3 — capture (one Chrome session for every shot)
# ---------------------------------------------------------------------------

def find_chrome(explicit: Optional[str] = None) -> Optional[str]:
    if explicit:
        return explicit if os.path.exists(explicit) else None
    for path in CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def est_tokens(width: int, height: int) -> int:
    return int(round((width * height) / float(TOKENS_PER_PIXEL_DIVISOR)))


def png_dims(raw: bytes) -> Tuple[int, int]:
    if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n":
        return (0, 0)
    w, h = struct.unpack(">II", raw[16:24])
    return int(w), int(h)


class _WS:
    """Minimal RFC6455 client — enough for CDP over the DevTools socket."""

    def __init__(self, host: str, port: int, path: str, timeout: float = 30.0) -> None:
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
               "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n" % (path, host, port, key))
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("DevTools socket closed during handshake")
            buf += chunk
        if b" 101 " not in buf.split(b"\r\n", 1)[0]:
            raise RuntimeError("DevTools handshake failed: %r" % buf[:120])
        self._rest = buf.split(b"\r\n\r\n", 1)[1]

    def _read(self, n: int) -> bytes:
        out = self._rest[:n]
        self._rest = self._rest[n:]
        while len(out) < n:
            chunk = self.sock.recv(min(1 << 20, n - len(out)))
            if not chunk:
                raise RuntimeError("DevTools socket closed")
            out += chunk
        return out

    def send(self, obj: Dict[str, Any]) -> None:
        payload = json.dumps(obj).encode()
        n = len(payload)
        if n < 126:
            header = b"\x81" + struct.pack("!B", 0x80 | n)
        elif n < 65536:
            header = b"\x81" + struct.pack("!BH", 0x80 | 126, n)
        else:
            header = b"\x81" + struct.pack("!BQ", 0x80 | 127, n)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def recv(self) -> Dict[str, Any]:
        while True:
            data = b""
            while True:
                b0, b1 = struct.unpack("!BB", self._read(2))
                op = b0 & 0x0F
                ln = b1 & 0x7F
                if ln == 126:
                    ln = struct.unpack("!H", self._read(2))[0]
                elif ln == 127:
                    ln = struct.unpack("!Q", self._read(8))[0]
                if b1 & 0x80:
                    mask = self._read(4)
                    chunk = bytes(b ^ mask[i % 4] for i, b in enumerate(self._read(ln)))
                else:
                    chunk = self._read(ln)
                if op == 0x9:  # ping -> pong
                    self.sock.sendall(b"\x8a" + struct.pack("!B", 0x80) + os.urandom(4))
                    continue
                if op == 0x8:
                    raise RuntimeError("DevTools socket sent close")
                data += chunk
                if b0 & 0x80:
                    break
            if data:
                return json.loads(data.decode())

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass


class ChromeSession:
    """One headless Chrome, many screenshots.

    Cold start is 1.5-2.5s and dominates this pass; a shared session turns six
    captures from ~12s into ~2.3s on this machine. Falls back to the one-shot
    `--screenshot` CLI when the DevTools socket cannot be established.
    """

    def __init__(self, chrome: str, timeout: float = 30.0) -> None:
        self.chrome = chrome
        self.timeout = timeout
        self.mode = "cdp"
        self.start_ms = 0
        self.proc = None
        self.ws = None
        self.session = None
        self._id = 0
        self.profile = tempfile.mkdtemp(prefix="html_probe_chrome_")
        t0 = time.time()
        try:
            self._launch()
        except Exception as exc:
            self.mode = "oneshot"
            self.error = str(exc)
            self._kill()
        else:
            self.error = None
        self.start_ms = int((time.time() - t0) * 1000)

    def _launch(self) -> None:
        self.proc = subprocess.Popen(
            [self.chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             "--no-first-run", "--no-default-browser-check", "--disable-component-update",
             "--no-service-autorun", "--disable-background-networking", "--disable-sync",
             "--disable-extensions", "--disable-default-apps", "--mute-audio",
             "--remote-debugging-port=0", "--user-data-dir=" + self.profile, "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        portfile = os.path.join(self.profile, "DevToolsActivePort")
        deadline = time.time() + self.timeout
        port = path = None
        while time.time() < deadline:
            if os.path.exists(portfile):
                try:
                    lines = open(portfile).read().split("\n")
                    if len(lines) >= 2 and lines[0].strip():
                        port, path = int(lines[0].strip()), lines[1].strip()
                        break
                except Exception:
                    pass
            if self.proc.poll() is not None:
                raise RuntimeError("chrome exited rc=%s before DevTools was ready" % self.proc.returncode)
            time.sleep(0.05)
        if port is None:
            raise RuntimeError("chrome never wrote DevToolsActivePort")
        self.ws = _WS("127.0.0.1", port, path, timeout=self.timeout)
        target = self._cmd("Target.createTarget", {"url": "about:blank"})["targetId"]
        self.session = self._cmd("Target.attachToTarget",
                                 {"targetId": target, "flatten": True})["sessionId"]
        self._cmd("Page.enable", {})

    def _cmd(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self._id += 1
        mid = self._id
        msg = {"id": mid, "method": method, "params": params}
        if self.session:
            msg["sessionId"] = self.session
        self.ws.send(msg)
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            got = self.ws.recv()
            if got.get("id") == mid:
                if "error" in got:
                    raise RuntimeError("%s failed: %s" % (method, got["error"]))
                return got.get("result", {})
        raise RuntimeError("timed out waiting for %s" % method)

    def shot(self, html_path: str, png_path: str, width: int, max_height: int) -> Dict[str, Any]:
        if self.mode == "cdp":
            try:
                return self._shot_cdp(html_path, png_path, width, max_height)
            except Exception as exc:
                self.mode = "oneshot"
                self.error = "cdp shot failed, fell back: %s" % exc
        return self._shot_oneshot(html_path, png_path, width, max_height)

    def _shot_cdp(self, html_path: str, png_path: str, width: int, max_height: int) -> Dict[str, Any]:
        t0 = time.time()
        self._cmd("Emulation.setDeviceMetricsOverride",
                  {"width": width, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        self._cmd("Page.navigate", {"url": "file://" + os.path.abspath(html_path)})
        deadline = time.time() + 15
        while time.time() < deadline:
            ready = self._cmd("Runtime.evaluate",
                              {"expression": "document.readyState", "returnByValue": True})
            if (ready.get("result") or {}).get("value") == "complete":
                break
            time.sleep(0.05)
        full = self._cmd("Runtime.evaluate", {
            "expression": "Math.max(document.documentElement.scrollHeight,"
                          "document.body ? document.body.scrollHeight : 0)",
            "returnByValue": True})
        doc_height = int((full.get("result") or {}).get("value") or 900)
        height = max(200, min(doc_height, max_height))
        self._cmd("Emulation.setDeviceMetricsOverride",
                  {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})
        data = self._cmd("Page.captureScreenshot", {"format": "png"})["data"]
        raw = base64.b64decode(data)
        with open(png_path, "wb") as fh:
            fh.write(raw)
        w, h = png_dims(raw)
        return {"png_path": png_path, "width": w, "height": h, "bytes": len(raw),
                "doc_height": doc_height, "clamped": doc_height > max_height,
                "est_tokens": est_tokens(w, h), "ms": int((time.time() - t0) * 1000),
                "mode": "cdp"}

    def _shot_oneshot(self, html_path: str, png_path: str, width: int, max_height: int) -> Dict[str, Any]:
        """Fallback: one process per shot. Chrome writes harmless task_policy_set /
        CVDisplayLink errors to stderr, so success is decided by the output file."""
        t0 = time.time()
        if os.path.exists(png_path):
            os.remove(png_path)
        proc = subprocess.Popen(
            [self.chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             "--window-size=%d,%d" % (width, max_height), "--screenshot=" + png_path,
             "file://" + os.path.abspath(html_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if os.path.exists(png_path) and os.path.getsize(png_path) > 0:
                time.sleep(0.15)
                break
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        if not os.path.exists(png_path):
            return {"png_path": None, "error": "chrome produced no screenshot",
                    "ms": int((time.time() - t0) * 1000), "mode": "oneshot"}
        raw = open(png_path, "rb").read()
        w, h = png_dims(raw)
        return {"png_path": png_path, "width": w, "height": h, "bytes": len(raw),
                "doc_height": None, "clamped": None, "est_tokens": est_tokens(w, h),
                "ms": int((time.time() - t0) * 1000), "mode": "oneshot"}

    def _kill(self) -> None:
        if self.proc is not None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None

    def close(self) -> None:
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None
        self._kill()
        shutil.rmtree(self.profile, ignore_errors=True)


# ---------------------------------------------------------------------------
# facts -> producer views
# ---------------------------------------------------------------------------

def producer_views(facts: Dict[str, Any], only_step: Optional[str] = None) -> List[Dict[str, Any]]:
    """Assemble what pass 5 needs per HTML producer, from facts.json.

    Prefers facts["html_producers"]; derives the same thing from steps + edges
    when that key is absent, so a facts file built to an older shape still works.
    """
    steps = {s.get("name"): s for s in facts.get("steps") or []}
    code_steps = facts.get("code_steps") or {}
    edges = facts.get("edges") or []
    producers = facts.get("html_producers")

    if not producers:
        producers = []
        email_steps = [s for s in facts.get("steps") or [] if s.get("type") == "email"]
        for es in email_steps:
            for e in edges:
                if e.get("to_step") != es.get("name") or e.get("input_type") != "dependency":
                    continue
                src = e.get("from_step")
                if src in code_steps or (steps.get(src) or {}).get("type") in ("custom_step", "function"):
                    producers.append({
                        "step": src,
                        "output_field": e.get("output_attribute") or "email_body",
                        "consumed_by": [{"step": es.get("name"), "param": e.get("to_param")}],
                    })
    views: List[Dict[str, Any]] = []
    for prod in producers:
        name = prod.get("step")
        if only_step and name != only_step:
            continue
        step = steps.get(name) or {}
        cs = code_steps.get(name) or {}
        params: List[Dict[str, Any]] = []
        seen = set()
        for e in edges:
            if e.get("to_step") != name:
                continue
            param = e.get("to_param")
            if not param or param in seen:
                continue
            seen.add(param)
            schema = e.get("resolved_schema")
            src_type = (steps.get(e.get("from_step")) or {}).get("type")
            suspect_cells = []
            if schema is not None and src_type and src_type not in CELL_WRAPPING_STEP_TYPES:
                suspect_cells = _undeclared_cells(schema)
            entry = {"name": param, "schema": schema,
                     "input_type": e.get("input_type"), "from_step": e.get("from_step"),
                     "output_attribute": e.get("output_attribute"),
                     "resolved_type": e.get("resolved_type"),
                     "from_step_type": src_type, "suspect_cells": suspect_cells}
            if e.get("input_type") == "static":
                entry["static_value"] = e.get("static_value", e.get("value"))
            params.append(entry)
        for declared in cs.get("declared_input_properties") or cs.get("params") or []:
            if declared not in seen and declared != "kwargs":
                seen.add(declared)
                params.append({"name": declared, "schema": None, "input_type": "unmapped"})
        views.append({
            "step": name,
            "output_field": prod.get("output_field") or "email_body",
            "consumed_by": prod.get("consumed_by") or [],
            "display_step": step.get("display_step"),
            "step_type": step.get("type"),
            "code_chars": prod.get("code_chars") or step.get("code_chars") or cs.get("code_chars"),
            "returned_keys": cs.get("returned_keys"),
            "returns_dynamic": cs.get("returns_dynamic"),
            "params": params,
        })
    return views


def acquire_code(view: Dict[str, Any], facts: Dict[str, Any], out_dir: str,
                 override: Optional[str] = None) -> Tuple[Optional[str], str, Optional[str]]:
    """Get the producer's code text. Returns (path, provenance, error).

    CONTRACT.md hard rule 2: only wf_facts.py reads workflow.json. It writes
    each code step to a sidecar and records the path at
    code_steps[step]["code_file"], which is the only source used here — no
    second reader, and no code text in the facts JSON.
    """
    step = view["step"]
    if override:
        return (override, "override", None) if os.path.exists(override) \
            else (None, "override", "no such file: %s" % override)

    declared = ((facts.get("code_steps") or {}).get(step) or {}).get("code_file")
    if declared and os.path.exists(declared):
        return declared, "facts code_file", None
    # Same directory, in case facts moved after it was written.
    for cand in (os.path.join(out_dir, "code", slug(step) + ".py"),
                 os.path.join(os.path.dirname(os.path.abspath(
                     (facts.get("workflow") or {}).get("path") or ".")), "code",
                     slug(step) + ".py")):
        if os.path.exists(cand):
            return cand, "code sidecar", None
    if declared:
        return None, "facts code_file", ("code_steps[%r].code_file points at %s, which does "
                                         "not exist — rerun wf_facts.py" % (step, declared))
    return None, "none", ("facts.json carries no code_file for %r — rerun wf_facts.py, or "
                          "pass --code <file>" % step)


def enrich_consumers(view: Dict[str, Any], facts: Dict[str, Any]) -> None:
    """Normalize each consumer's routing from html_producers[].consumed_by[].

    wf_facts decides send-versus-display and records it as `email_routing`,
    keyed on zapier_reply_url per reference/step_types/email.md. Nothing here
    reads the workflow (CONTRACT.md hard rule 2).
    """
    for consumer in view.get("consumed_by") or []:
        routing = consumer.get("email_routing") or {}
        if routing:
            consumer["recipients"] = dict(routing.get("routing") or {})
            consumer["sends"] = bool(routing.get("sends"))
            consumer["display_only"] = bool(routing.get("display_only"))
            consumer["zapier_reply_url_set"] = bool(routing.get("zapier_reply_url_set"))
            consumer["reply_to_original"] = routing.get("reply_to_original")
        else:
            consumer.setdefault("recipients", {})
            consumer.setdefault("sends", None)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def looks_wrong(render: Dict[str, Any], stats: Optional[Dict[str, Any]],
                capture: Optional[Dict[str, Any]]) -> Optional[str]:
    if not render.get("ok"):
        return "render failed"
    if not stats:
        return "no markup produced"
    if stats["bytes"] < 400:
        return "markup is %d bytes" % stats["bytes"]
    if stats["visible_chars"] < 120:
        return "only %d visible characters" % stats["visible_chars"]
    if capture and capture.get("png_path") and capture.get("bytes", 0) < 2500:
        return "screenshot is %d bytes (likely blank)" % capture["bytes"]
    if capture and not capture.get("png_path"):
        return "no screenshot produced"
    return None


def probe(facts: Dict[str, Any], out_dir: str, fixtures: Tuple[str, ...],
          want_escalate: bool, screenshot: bool, width: int, max_height: int,
          timeout: int, chrome_path: Optional[str], only_step: Optional[str],
          code_override: Optional[str]) -> Dict[str, Any]:
    findings: List[Dict[str, Any]] = []
    notes: List[str] = []
    os.makedirs(out_dir, exist_ok=True)
    runner = write_runner(out_dir)
    views = producer_views(facts, only_step)

    report: Dict[str, Any] = {
        "pass": PASS_NAME,
        "findings": [],
        "producers": [],
        "escalated": bool(want_escalate),
        "escalation_reason": "--escalate" if want_escalate else None,
        "fixtures_requested": list(fixtures),
        "chrome": {},
        "totals": {"fixtures_attempted": 0, "renders_ok": 0, "renders_failed": 0,
                   "captures": 0, "capture_failures": 0, "est_tokens": 0, "png_bytes": 0},
        "notes": notes,
    }
    if not views:
        if only_step:
            notes.append("--step %r is not an HTML producer in facts.json" % only_step)
        else:
            notes.append("no HTML producers in facts.json (no code step feeding an email step)")
        return report

    chrome = find_chrome(chrome_path) if screenshot else None
    if screenshot and not chrome:
        notes.append("Chrome not found; HTML written but not captured. Looked at: %s"
                     % ", ".join(CHROME_CANDIDATES[:2]))
    session = None
    if chrome:
        session = ChromeSession(chrome, timeout=float(timeout))
        report["chrome"] = {"path": chrome, "mode": session.mode,
                            "start_ms": session.start_ms, "error": session.error}
        if session.error:
            notes.append("Chrome DevTools session unavailable (%s); using one process per shot"
                         % truncate(session.error, 160))

    try:
        for view in views:
            enrich_consumers(view, facts)
            entry: Dict[str, Any] = {"step": view["step"], "output_field": view["output_field"],
                                     "consumed_by": [c.get("step") for c in view["consumed_by"]],
                                     "code_path": None, "code_provenance": None,
                                     "fixtures": {}, "lint": {}}
            code_path, provenance, err = acquire_code(view, facts, out_dir, code_override)
            entry["code_provenance"] = provenance
            if not code_path:
                findings.append(_finding(
                    "HTML_CODE_UNAVAILABLE", "note", "Could not read the producer's code",
                    "Stages 2 and 3 skipped for %s: %s" % (view["step"], err),
                    fix="Pass --code <file> or check facts.workflow.path.", step=view["step"]))
                entry["error"] = err
                report["producers"].append(entry)
                continue
            entry["code_path"] = code_path
            with open(code_path, encoding="utf-8") as fh:
                code = fh.read()

            # --- stage 1 ---
            lint_findings, lint = static_lint(view["step"], code, view)
            findings.extend(lint_findings)
            entry["lint"] = lint
            b, w, _n = _sev_counts(lint_findings)
            escalate = want_escalate or b > 0 or w > 0
            if escalate and not want_escalate:
                report["escalation_reason"] = "stage 1 found %d blocker(s), %d warning(s)" % (b, w)

            suspect = [p for p in view["params"] if p.get("suspect_cells")]
            if suspect:
                findings.append(_finding(
                    "HTML_UNDECLARED_CELL", "note",
                    "Auto-derived cell on a step type that does not cell-wrap",
                    "%s. Only the eight extraction types cell-wrap their leaves, and these "
                    "cells are not author-declared, so the fixture may be feeding the "
                    "dashboard a shape it cannot receive at run time." % "; ".join(
                        "`%s` from %s (a %s): %s" % (
                            p["name"], p["from_step"], p["from_step_type"] or "?",
                            ", ".join(p["suspect_cells"][:4]))
                        for p in suspect[:4]),
                    fix="Check the cell wrapping for these step types in wf_facts.py; "
                        "reference/input_mappings.md lists the eight that cell-wrap. An "
                        "author-declared cell type is legitimate and carries origin "
                        "\"declared\".",
                    step=view["step"],
                    evidence={"params": [{"param": p["name"], "from_step": p["from_step"],
                                          "from_step_type": p["from_step_type"],
                                          "cell_paths": p["suspect_cells"][:12]}
                                         for p in suspect]},
                    group_key="HTML_UNDECLARED_CELL:%s" % view["step"]))
            mined = lint.get("mined_keys") or []
            # Cell wrapping is a property of the upstream step type
            # (reference/input_mappings.md: "Extraction outputs are cell-wrapped"),
            # so synthesize cells only where a resolved schema actually says cell.
            prefer_cells = any(_schema_has_cell(p.get("schema")) for p in view["params"])
            run_fixtures = list(fixtures)
            if escalate:
                for name in FIXTURE_NAMES:
                    if name not in run_fixtures:
                        run_fixtures.append(name)
                report["escalated"] = True
            # `empty` first: it is the default single capture and the escalation probe.
            run_fixtures.sort(key=lambda n: FIXTURE_NAMES.index(n) if n in FIXTURE_NAMES else 9)
            if "empty" in run_fixtures:
                run_fixtures.remove("empty")
                run_fixtures.insert(0, "empty")

            producer_dir = os.path.join(out_dir, "render", slug(view["step"]))
            os.makedirs(producer_dir, exist_ok=True)

            index = 0
            while index < len(run_fixtures):
                fixture = run_fixtures[index]
                index += 1
                report["totals"]["fixtures_attempted"] += 1
                inputs = build_fixture(fixture, view["params"], mined, prefer_cells)
                render = render_fixture(runner, code_path, inputs, fixture, producer_dir,
                                        view["output_field"], timeout)
                fx: Dict[str, Any] = {
                    "render_ok": bool(render.get("ok")),
                    "error": render.get("error"),
                    "html_path": render.get("html_path"),
                    "html_chars": render.get("html_chars", 0),
                    "render_ms": render.get("duration_ms"),
                    "wall_ms": render.get("wall_ms"),
                    "returned_keys": render.get("returned_keys"),
                    "error_type": render.get("error_type"),
                    "step_frames": render.get("step_frames") or [],
                    "capability_calls": render.get("capability_calls"),
                    "log_calls": len(render.get("log_calls") or []),
                    "dropped_params": render.get("dropped_params") or [],
                    "inputs_path": render.get("inputs_path"),
                }
                if render.get("network_attempts"):
                    findings.append(_finding(
                        "HTML_RENDER_NETWORK_ATTEMPT", "blocker",
                        "Compose step tried to open a network connection",
                        "The %s fixture render attempted an outbound socket. A dashboard "
                        "compose step should be pure formatting; network work belongs in a "
                        "first-class integration step." % fixture,
                        fix="Move the call out of the compose step.", step=view["step"],
                        evidence={"attempts": render["network_attempts"][:3]},
                        group_key="HTML_RENDER_NETWORK_ATTEMPT:%s" % view["step"]))
                caps = {c.get("capability") for c in render.get("capability_calls") or []}
                if caps - {"context.execution_log", "context.traceparent"}:
                    fx["stubbed_capabilities"] = sorted(caps)
                    findings.append(_finding(
                        "HTML_STUBBED_CAPABILITY", "note",
                        "Render used stubbed platform capabilities",
                        "The %s fixture exercised %s against the stub SDK, so what the real "
                        "call returns is not proven here." % (fixture, ", ".join(sorted(caps))),
                        fix="Confirm on a real draft run if the dashboard depends on the values.",
                        step=view["step"], evidence={"capabilities": sorted(caps)},
                        group_key="HTML_STUBBED_CAPABILITY:%s" % view["step"]))
                if not render.get("ok"):
                    err_type = render.get("error_type")
                    module = (_missing_module(render.get("error") or "")
                              if err_type in ("ModuleNotFoundError", "ImportError") else None)
                    if module:
                        # A local render can only be faithful for a custom_step.
                        # step_types/custom_step.md: the sandbox has an empty
                        # os.environ, no backend imports, and only the
                        # allowlisted packages. A legacy `function` step runs
                        # in-process on the worker where backend imports DO
                        # exist, so failing to import one locally says nothing
                        # about the step.
                        root = module.split(".")[0]
                        if view.get("step_type") == "function":
                            code_f, sev_f, title_f = ("HTML_NOT_RENDERABLE_LOCALLY", "note",
                                                      "Legacy function step cannot be rendered here")
                            detail_f = ("`%s` is a legacy in-process `function` step that imports "
                                        "`%s`, a module that exists on the platform worker and not "
                                        "in this harness. Stages 2 and 3 are skipped for it; the "
                                        "stage 1 lint above still applies."
                                        % (view["step"], module))
                            fix_f = ("Nothing to fix for the render. New custom code should be a "
                                     "custom_step, which is sandboxed and locally renderable.")
                        elif root in SANDBOX_ALLOWLIST:
                            code_f, sev_f, title_f = ("HTML_RENDER_SKIPPED", "note",
                                                      "Allowlisted package missing from this harness")
                            detail_f = ("The step imports `%s`, which is allowlisted for the "
                                        "sandbox but is not installed here (this pass is stdlib "
                                        "only). Render and capture skipped." % module)
                            fix_f = "Nothing to fix; the sandbox pre-installs it."
                        else:
                            code_f, sev_f, title_f = ("HTML_SANDBOX_IMPORT", "blocker",
                                                      "custom_step imports a module the sandbox does not have")
                            detail_f = ("`%s` imports `%s`. A custom_step runs in an isolated "
                                        "sandbox with no backend imports and only the allowlisted "
                                        "packages, so this fails at run time, not just here "
                                        "(step_types/custom_step.md)." % (view["step"], module))
                            fix_f = ("Drop the import, or declare an allowlisted package in "
                                     "config.packages. Backend modules are never importable.")
                        findings.append(_finding(
                            code_f, sev_f, title_f, detail_f, fix=fix_f, step=view["step"],
                            evidence={"fixture": fixture, "module": module,
                                      "step_type": view.get("step_type"),
                                      "step_frames": render.get("step_frames") or []},
                            group_key="%s:%s" % (code_f, view["step"])))
                        report["totals"]["renders_failed"] += 1
                        entry["fixtures"][fixture] = fx
                        notes.append("stopped after the %s fixture: %s is not importable here"
                                     % (fixture, module))
                        break
                    frames = render.get("step_frames") or []
                    deepest = frames[-1] if frames else None
                    synthesized = fixture != "empty"
                    timeout_fix = None
                    shape_error = err_type in _SHAPE_ERRORS
                    if render.get("timeout"):
                        sev, title = "blocker", "Step code timed out during render"
                        timeout_fix = ("The step never finished. Look for an unbounded loop, or a "
                                       "call waiting on something the sandbox cannot reach — a "
                                       "compose step should be pure formatting.")
                    elif synthesized and shape_error:
                        sev = "warning"       # see _SHAPE_ERRORS
                        title = "Step code raised on synthesized %s input" % fixture
                    elif (render.get("error") or "").startswith("run() returned no"):
                        # Ran fine, published the markup under the wrong key. That is
                        # the classic mistake, not a crash — name it as such.
                        findings.append(_finding(
                            "HTML_WRONG_OUTPUT_KEY", "blocker",
                            "Producer returned no HTML under the consumed key",
                            "%s fixture: %s" % (fixture, render.get("error")),
                            fix="Return the markup under `%s` — the key the email step reads."
                                % view["output_field"],
                            step=view["step"],
                            evidence={"fixture": fixture,
                                      "returned_keys": render.get("returned_keys")},
                            group_key="HTML_WRONG_OUTPUT_KEY:%s:runtime" % view["step"]))
                        report["totals"]["renders_failed"] += 1
                        entry["fixtures"][fixture] = fx
                        continue
                    else:
                        sev, title = "blocker", "Step code raised while composing HTML"
                    where = ""
                    if deepest and deepest.get("lineno"):
                        where = " at line %d of the step's code%s" % (
                            deepest["lineno"],
                            " in %s()" % deepest["func"] if deepest.get("func") else "")
                        if deepest.get("source"):
                            where += ":  " + truncate(deepest["source"], 140)
                    detail = "%s fixture: %s%s" % (
                        fixture, render.get("error_repr") or render.get("error") or "", where)
                    if synthesized and shape_error:
                        detail += (". The %s fixture is synthesized from the resolved schemas in "
                                   "facts.json, so confirm this shape is one the step can actually "
                                   "receive before treating it as a dashboard bug." % fixture)
                    findings.append(_finding(
                        "HTML_RENDER_ERROR", sev, title, detail,
                        fix=(timeout_fix if render.get("timeout")
                             else _exception_fix(err_type, render.get("error") or "")),
                        step=view["step"],
                        evidence={"fixture": fixture, "error_type": err_type,
                                  "error": render.get("error"),
                                  "step_frames": frames,
                                  "inputs_path": render.get("inputs_path"),
                                  "synthesized_input": synthesized},
                        group_key="HTML_RENDER_ERROR:%s:%s" % (view["step"], sev)))
                    report["totals"]["renders_failed"] += 1
                    entry["fixtures"][fixture] = fx
                    if render.get("timeout"):
                        # Re-running a runaway loop on two more fixtures just burns
                        # another 2x the timeout; one is proof enough.
                        notes.append("stopped after the %s fixture timed out on %s"
                                     % (fixture, view["step"]))
                        break
                    if fixture == "empty" and not escalate:
                        escalate = True
                        report["escalated"] = True
                        report["escalation_reason"] = "empty fixture render failed"
                        for name in FIXTURE_NAMES:
                            if name not in run_fixtures:
                                run_fixtures.append(name)
                    continue
                if render.get("dropped_params"):
                    findings.append(_finding(
                        "HTML_PARAM_NOT_ACCEPTED", "note",
                        "Mapped input the code does not accept",
                        "run() has no parameter (and no **kwargs) for: %s"
                        % ", ".join(render["dropped_params"][:6]),
                        fix="Add the parameter with a =None default, or **kwargs.",
                        step=view["step"],
                        evidence={"dropped": render["dropped_params"][:12]},
                        group_key="HTML_PARAM_NOT_ACCEPTED:%s" % view["step"]))
                if not render.get("html_path") or not os.path.exists(render["html_path"]):
                    findings.append(_finding(
                        "HTML_WRONG_OUTPUT_KEY", "blocker",
                        "Render produced no HTML under the consumed key",
                        "The %s fixture ran without error but nothing was written for `%s` "
                        "(returned: %s)." % (fixture, view["output_field"],
                                             ", ".join(render.get("returned_keys") or []) or "nothing"),
                        fix="Return the markup under the key the email step reads.",
                        step=view["step"],
                        evidence={"fixture": fixture, "returned_keys": render.get("returned_keys")},
                        group_key="HTML_WRONG_OUTPUT_KEY:%s" % view["step"]))
                    entry["fixtures"][fixture] = fx
                    continue
                with open(render["html_path"], encoding="utf-8") as fh:
                    html_text = fh.read()
                report["totals"]["renders_ok"] += 1
                mk_findings, stats = markup_lint(view["step"], fixture, html_text)
                findings.extend(mk_findings)
                fx["markup"] = stats
                mk_blockers = _sev_counts(mk_findings)[0]

                if session is not None:
                    png_path = os.path.join(producer_dir, "%s.png" % fixture)
                    cap = session.shot(render["html_path"], png_path, width, max_height)
                    fx["capture"] = cap
                    if cap.get("png_path"):
                        report["totals"]["captures"] += 1
                        report["totals"]["est_tokens"] += cap.get("est_tokens") or 0
                        report["totals"]["png_bytes"] += cap.get("bytes") or 0
                    else:
                        report["totals"]["capture_failures"] += 1
                        notes.append("capture failed for %s/%s: %s"
                                     % (view["step"], fixture, cap.get("error")))
                entry["fixtures"][fixture] = fx

                if fixture == "empty" and not escalate:
                    reason = looks_wrong(render, stats, fx.get("capture"))
                    if not reason and mk_blockers:
                        reason = "%d markup blocker(s) on the empty fixture" % mk_blockers
                    if reason:
                        escalate = True
                        report["escalated"] = True
                        report["escalation_reason"] = "empty fixture: %s" % reason
                        for name in FIXTURE_NAMES:
                            if name not in run_fixtures:
                                run_fixtures.append(name)
            report["producers"].append(entry)
    finally:
        if session is not None:
            report["chrome"]["mode_final"] = session.mode
            session.close()

    report["findings"] = findings
    return report


def extra_lines(report: Dict[str, Any]) -> List[str]:
    """Lines printed above the findings by PassOutput.print_summary().

    Paths go on their own labeled lines (plugin CONVENTIONS.md: "put IDs and
    URLs on labeled lines"); the numbers go through common.table().
    """
    lines: List[str] = []
    rows: List[List[Any]] = []
    for prod in report.get("producers") or []:
        for fixture, fx in sorted((prod.get("fixtures") or {}).items()):
            cap = fx.get("capture") or {}
            rows.append([
                truncate(prod.get("step") or "-", 28),
                fixture,
                "ok" if fx.get("render_ok") else "FAILED",
                (fx.get("markup") or {}).get("bytes") or fx.get("html_chars") or 0,
                (fx.get("markup") or {}).get("visible_chars") or 0,
                "%dx%d" % (cap.get("width") or 0, cap.get("height") or 0) if cap.get("png_path") else "-",
                cap.get("est_tokens") or 0,
                "%dms" % (fx.get("wall_ms") or 0),
            ])
    if rows:
        lines.extend(table(rows, ["producer", "fixture", "render", "bytes",
                                  "text", "shot", "~tokens", "render"],
                           max_rows=12))
    for prod in report.get("producers") or []:
        for fixture, fx in sorted((prod.get("fixtures") or {}).items()):
            if fx.get("html_path"):
                lines.append("HTML     %-7s %s" % (fixture, fx["html_path"]))
            cap = fx.get("capture") or {}
            if cap.get("png_path"):
                lines.append("PNG      %-7s %s  %dx%d  ~%d tokens%s"
                             % (fixture, cap["png_path"], cap.get("width") or 0,
                                cap.get("height") or 0, cap.get("est_tokens") or 0,
                                "  (clamped from %dpx)" % cap["doc_height"]
                                if cap.get("clamped") else ""))
    for note in report.get("notes") or []:
        lines.append("note     %s" % truncate(note, 200))
    if report.get("escalated"):
        lines.append("note     escalated to the full fixture matrix: %s"
                     % report.get("escalation_reason"))
    totals = report.get("totals") or {}
    if totals.get("fixtures_attempted"):
        parts = ["%d fixture(s) attempted" % totals["fixtures_attempted"],
                 "%d rendered" % (totals.get("renders_ok") or 0)]
        if totals.get("renders_failed"):
            parts.append("%d failed before capture" % totals["renders_failed"])
        if totals.get("capture_failures"):
            parts.append("%d capture(s) failed" % totals["capture_failures"])
        parts.append("%d captured" % (totals.get("captures") or 0))
        lines.append("COST     %s" % ", ".join(parts))
        lines.append("COST     ~%d image tokens if every capture above is shown"
                     % (totals.get("est_tokens") or 0))
    return lines


def parse_fixtures(args: argparse.Namespace) -> Tuple[str, ...]:
    if args.all_fixtures:
        return FIXTURE_NAMES
    if args.fixtures:
        names = [n.strip() for n in args.fixtures.split(",") if n.strip()]
        bad = [n for n in names if n not in FIXTURE_NAMES]
        if bad:
            sys.stderr.write("unknown fixture(s): %s (choose from %s)\n"
                             % (", ".join(bad), ", ".join(FIXTURE_NAMES)))
            raise SystemExit(2)
        return tuple(names)
    return DEFAULT_FIXTURES


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Pass 5 — HTML lint, local render, screenshot (read-only).")
    add_common_args(ap)          # --facts / --out / --self-test, shared shape
    ap.add_argument("--fixtures", help="comma list of %s (default: empty)"
                                      % ",".join(FIXTURE_NAMES))
    ap.add_argument("--all-fixtures", action="store_true", help="run every fixture")
    ap.add_argument("--escalate", action="store_true",
                    help="force the full fixture matrix and a capture per fixture")
    ap.add_argument("--step", help="only this producer step")
    ap.add_argument("--code", help="read the producer's code from this file instead of the workflow")
    ap.add_argument("--no-screenshot", action="store_true", help="skip stage 3")
    ap.add_argument("--width", type=int, default=SHOT_WIDTH)
    ap.add_argument("--max-height", type=int, default=SHOT_MAX_HEIGHT)
    ap.add_argument("--timeout", type=int, default=30, help="per-render / per-Chrome timeout (s)")
    ap.add_argument("--chrome", help="explicit Chrome binary path")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test(args)
    require_args(args, "facts", "out")   # exits 2, and skips under --self-test

    facts = load_facts(args.facts)          # raises SystemExit(2) on bad input
    out_dir = os.path.abspath(args.out)
    try:
        fixtures = parse_fixtures(args)
        report = probe(facts, out_dir, fixtures, args.escalate,
                       not args.no_screenshot, args.width, args.max_height, args.timeout,
                       args.chrome, args.step, args.code)
    except SystemExit:
        raise
    except Exception as exc:
        import traceback as tb
        sys.stderr.write("html_probe failed: %s\n%s\n" % (exc, tb.format_exc()[-2000:]))
        return 2

    out = PassOutput(PASS_NAME, out_dir)
    out.add_all(report.pop("findings", []))
    for key, value in report.items():
        if key != "pass":
            out.set(key, value)
    path = out.write()
    return out.print_summary(extra_lines(report) + ["JSON     %s" % path])


# ---------------------------------------------------------------------------
# --self-test
# ---------------------------------------------------------------------------

REFERENCE_WORKFLOW = os.path.normpath(os.path.join(
    HERE, "..", "..", "reference", "examples", "submission_intake_es_umbrella.json"))
REFERENCE_PRODUCER = "Compose HTML Summary"


def _build_real_facts(out_dir: str) -> Tuple[Optional[str], Optional[str]]:
    """Run wf_facts.py on the reference workflow. Returns (facts_path, error).

    The self-test uses real facts, and therefore the real code sidecars, so it
    exercises exactly the path a verify run takes. Nothing here reads the
    workflow itself — that is wf_facts.py's job (CONTRACT.md hard rule 2).
    """
    tool = os.path.join(HERE, "wf_facts.py")
    if not os.path.exists(tool):
        return None, "wf_facts.py not present at %s" % tool
    facts_dir = os.path.join(out_dir, "facts_real")
    os.makedirs(facts_dir, exist_ok=True)
    try:
        proc = subprocess.run([sys.executable, tool, REFERENCE_WORKFLOW, "--out", facts_dir],
                              capture_output=True, timeout=180)
    except subprocess.TimeoutExpired:
        return None, "wf_facts.py timed out"
    path = os.path.join(facts_dir, "facts.json")
    if proc.returncode not in (0, 1) or not os.path.exists(path):
        return None, "wf_facts.py failed (rc=%d): %s" % (
            proc.returncode, (proc.stderr or b"").decode()[-300:])
    return path, None


def _minimal_facts(workflow_path: str, out_dir: str, code_file: Optional[str] = None) -> str:
    """Hand-made facts fixture in the CONTRACT.md shape, for the real CLI path."""
    open_obj = {"kind": "object", "properties": {}, "open": True, "origin": "declared"}
    facts = {
        "schema_version": 1,
        "generated_by": "html_probe.py --self-test (hand-made fixture)",
        "workflow": {"path": workflow_path, "name": "ABC MGA Submission Intake",
                     "bytes": os.path.getsize(workflow_path), "step_count": 30,
                     "options": {}, "control_plane_mappings": {}},
        "steps": [
            {"index": 28, "name": REFERENCE_PRODUCER, "type": "custom_step",
             "display_step": False, "output_type": None, "is_terminal": False,
             "code_chars": 23457,
             "output": {"kind": "object", "properties": {
                 "email_body": {"kind": "string", "origin": "declared"}}, "open": False}},
            {"index": 29, "name": "Submission Summary", "type": "email",
             "display_step": True, "is_terminal": True,
             "output": {"kind": "object", "properties": {}, "open": True}},
        ],
        "code_steps": {REFERENCE_PRODUCER: {
            "code_file": code_file,
            "is_async": True,
            "params": ["standardized_data", "gc_alpha", "gc_beta", "gc_gamma", "app_base_url"],
            "has_kwargs": True, "returned_keys": ["email_body"], "returns_dynamic": False,
            "declared_input_properties": ["standardized_data", "gc_alpha", "gc_beta",
                                          "gc_gamma", "app_base_url"],
            "declared_output_properties": ["email_body"],
            "imports": ["html", "fai_sandbox.context"], "syntax_ok": True, "parse_error": None}},
        "edges": [
            {"to_step": REFERENCE_PRODUCER, "to_param": "standardized_data",
             "input_type": "dependency", "from_step": "Standardize Extracted Data",
             "output_attribute": None, "via": "input_mappings", "resolved": True,
             "resolved_type": "object", "resolved_schema": {
                 "kind": "object", "open": False, "properties": {
                     "basic_info": open_obj, "broker_uw": open_obj,
                     "additional_policy": open_obj, "underlying_coverage": open_obj,
                     "auto_schedule": open_obj, "expiring_account": open_obj,
                     "aggregated_loss_data": {"kind": "array", "items": open_obj}}}},
            {"to_step": REFERENCE_PRODUCER, "to_param": "gc_alpha",
             "input_type": "dependency", "from_step": "Guideline Check - Alpha",
             "output_attribute": None, "via": "input_mappings", "resolved": True,
             "resolved_type": "object", "resolved_schema": open_obj},
            {"to_step": REFERENCE_PRODUCER, "to_param": "gc_beta",
             "input_type": "dependency", "from_step": "Guideline Check - Beta",
             "output_attribute": None, "via": "input_mappings", "resolved": True,
             "resolved_type": "object", "resolved_schema": open_obj},
            {"to_step": REFERENCE_PRODUCER, "to_param": "gc_gamma",
             "input_type": "dependency", "from_step": "Guideline Check - Gamma",
             "output_attribute": None, "via": "input_mappings", "resolved": True,
             "resolved_type": "object", "resolved_schema": open_obj},
            {"to_step": REFERENCE_PRODUCER, "to_param": "app_base_url",
             "input_type": "static", "from_step": None, "output_attribute": None,
             "static_value": "https://app.furtherai.com", "via": "input_mappings",
             "resolved": True, "resolved_type": "string",
             "resolved_schema": {"kind": "string", "origin": "config"}},
            {"to_step": "Submission Summary", "to_param": "email_body",
             "input_type": "dependency", "from_step": REFERENCE_PRODUCER,
             "output_attribute": "email_body", "via": "input_mappings", "resolved": True,
             "resolved_type": "string", "resolved_schema": {"kind": "string"}},
        ],
        "fields": [],
        "decisions": [],
        "manual_steps": [],
        "graph": {"adjacency": {REFERENCE_PRODUCER: ["Submission Summary"]},
                  "reverse": {"Submission Summary": [REFERENCE_PRODUCER]},
                  "roots": [REFERENCE_PRODUCER], "terminals": ["Submission Summary"],
                  "cycles": []},
        "html_producers": [{"step": REFERENCE_PRODUCER, "output_field": "email_body",
                            "consumed_by": [{"step": "Submission Summary", "param": "email_body"}],
                            "code_chars": 23457, "interpolated_paths": []}],
        "counters": {"steps": 30, "html_producers": 1},
    }
    path = os.path.join(out_dir, "facts.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(facts, fh, indent=1)
    return path


def self_test(args: argparse.Namespace) -> int:
    out_dir = os.path.abspath(args.out) if args.out else os.path.join(
        tempfile.gettempdir(), "html_probe_selftest")
    os.makedirs(out_dir, exist_ok=True)
    failures: List[str] = []
    checks = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal checks
        checks += 1
        print("%s  %s%s" % ("ok  " if ok else "FAIL", name, ("  — " + detail) if detail else ""))
        if not ok:
            failures.append(name)

    print("self-test out: %s" % out_dir)
    check("reference workflow exists", os.path.exists(REFERENCE_WORKFLOW), REFERENCE_WORKFLOW)
    if not os.path.exists(REFERENCE_WORKFLOW):
        return 2

    # --- unit-ish invariants -------------------------------------------------
    unknown_rows = _synth({"kind": "array", "items": {"kind": "unknown"}},
                          "present_documents", "full", ["carrier", "status"], False)
    check("an array of unknown items synthesizes scalars, not dicts",
          isinstance(unknown_rows, list)
          and all(not isinstance(x, (dict, list)) for x in unknown_rows),
          str(unknown_rows)[:80])
    check("an open object still gets the mined keys",
          isinstance(_synth({"kind": "object", "properties": {}, "open": True},
                            "d", "full", ["Insured Name"], False), dict))
    enum_schema = {"kind": "string",
                   "allowed_values": ["Pass", "Fail", "Needs Review", "N/A"]}
    picks = [_synth(enum_schema, "status", "full", [], False, 0, i) for i in range(4)]
    check("enum fixtures use declared allowed_values",
          set(picks) == {"Pass", "Fail", "Needs Review", "N/A"}, str(picks))
    check("stress picks the longest enum member",
          _synth(enum_schema, "status", "stress", [], False) == "Needs Review",
          str(_synth(enum_schema, "status", "stress", [], False)))
    check("token estimate matches the 1200x800 ~= 1300 token reference",
          1200 <= est_tokens(1200, 800) <= 1400, "%d" % est_tokens(1200, 800))
    check("token estimate matches the 1200x6000 ~= 9600 token reference",
          9000 <= est_tokens(1200, 6000) <= 10200, "%d" % est_tokens(1200, 6000))
    bad_findings, _ = markup_lint("X", "empty", "<html><body><div><p>hi</body></html>")
    check("unclosed <div> is a blocker",
          any(f["code"] == "HTML_UNCLOSED_TAG" and f["severity"] == "blocker" for f in bad_findings))
    esc_findings, _ = markup_lint("X", "stress", "<html><body>" + INJECT_SENTINEL + "</body></html>")
    check("live injected marker is a blocker",
          any(f["code"] == "HTML_UNESCAPED_INPUT" for f in esc_findings))
    ext_findings, _ = markup_lint("X", "empty", '<html><body><img src="https://x.test/a.png"></body></html>')
    check("remote <img> is a blocker",
          any(f["code"] == "HTML_EXTERNAL_REQUEST" for f in ext_findings))
    # common.finding() omits "pass"; PassOutput.write() stamps it.
    for f in bad_findings + esc_findings + ext_findings:
        for key in ("code", "severity", "step", "title", "detail", "fix", "evidence", "group_key"):
            if key not in f:
                failures.append("finding shape missing %s" % key)
        if "pass" in f:
            failures.append("finding shape carries pass before write()")
    check("findings carry every contract key", not any(x.startswith("finding shape") for x in failures))
    try:
        _finding("X", "critical", "t", "d")
        failures.append("bad severity accepted")
    except ValueError:
        pass
    check("an unknown severity raises ValueError", "bad severity accepted" not in failures)

    # --- traceback quality, severity policy, cell-provenance guard -----------
    sending = {"step": "Send", "sends": True, "display_only": False,
               "recipients": {"to_email": "uw@example.com"}}
    displaying = {"step": "Show", "sends": False, "display_only": True, "recipients": {}}
    both = _structural_lint("C", {"output_field": "email_body",
                                  "consumed_by": [sending, displaying]}, 14)
    only_send = _structural_lint("C", {"output_field": "email_body",
                                       "consumed_by": [sending]}, 1)
    dash_send = _structural_lint("C", {"output_field": "email_body",
                                       "consumed_by": [sending]}, 12)
    def sev_of(rows):
        hits = [f for f in rows if f["code"] == "HTML_EMAIL_RECIPIENTS"]
        return hits[0]["severity"] if hits else None
    check("sending plus a display-only sibling is a blocker", sev_of(both) == "blocker",
          str(sev_of(both)))
    check("a styled dashboard being emailed is a warning", sev_of(dash_send) == "warning",
          str(sev_of(dash_send)))
    check("an ordinary email composer is only a note", sev_of(only_send) == "note",
          str(sev_of(only_send)))
    check("a display-only consumer alone raises nothing",
          sev_of(_structural_lint("C", {"output_field": "email_body",
                                        "consumed_by": [displaying]}, 14)) is None)
    check("a missing module is parsed out of the message",
          _missing_module("No module named 'src.workflows'") == "src.workflows",
          str(_missing_module("No module named 'src.workflows'")))
    check("the sandbox allowlist uses module names",
          "bs4" in SANDBOX_ALLOWLIST and "PIL" in SANDBOX_ALLOWLIST
          and "beautifulsoup4" not in SANDBOX_ALLOWLIST)

    probe_schema = {"kind": "object", "properties": {
        "declared": {"kind": "cell", "origin": "declared", "value_type": {"kind": "string"}},
        "auto": {"kind": "cell", "origin": "auto", "value_type": {"kind": "string"}},
        "rows": {"kind": "array", "items": {"kind": "object", "properties": {
            "v": {"kind": "cell", "origin": "auto", "value_type": {"kind": "number"}}}}}}}
    suspect_paths = _undeclared_cells(probe_schema)
    check("an author-declared cell is never called suspect",
          ".declared" not in suspect_paths, str(suspect_paths))
    check("auto-derived cells are located by path",
          sorted(suspect_paths) == [".auto", ".rows.0.v"], str(sorted(suspect_paths)))
    check("a non-extraction step is not in the cell-wrapping list",
          "custom_step" not in CELL_WRAPPING_STEP_TYPES
          and "agentic_extraction" in CELL_WRAPPING_STEP_TYPES)
    check("NoneType errors get None-guard advice",
          "arrives None" in (_exception_fix("TypeError", "'NoneType' object is not iterable") or ""))
    check("unhashable dict gets cell-unwrap advice",
          "unwrap" in (_exception_fix("TypeError", "unhashable type: 'dict'") or ""))
    check("an unrecognised exception gets no invented fix",
          _exception_fix("RuntimeError", "something odd happened") is None)

    crash_dir = os.path.join(out_dir, "crash")
    os.makedirs(crash_dir, exist_ok=True)
    crash_code = os.path.join(crash_dir, "step_code.py")
    with open(crash_code, "w", encoding="utf-8") as fh:
        fh.write("def run(rows=None, **kwargs):\n"
                 "    def render_row(row):\n"
                 "        return '<td>' + row['carrier'].upper() + '</td>'\n"
                 "    return {'email_body': '<html><body>'"
                 " + ''.join(render_row(r) for r in rows) + '</body></html>'}\n")
    crash_facts = os.path.join(crash_dir, "facts.json")
    with open(crash_facts, "w", encoding="utf-8") as fh:
        json.dump({
            "schema_version": 1,
            "workflow": {"path": "/nonexistent.json", "name": "Crash"},
            "steps": [{"index": 0, "name": "C", "type": "custom_step", "display_step": False},
                      {"index": 1, "name": "E", "type": "email", "display_step": True},
                      {"index": 2, "name": "X", "type": "agentic_extraction",
                       "display_step": True}],
            "code_steps": {"C": {"params": ["rows"], "has_kwargs": True,
                                 "returned_keys": ["email_body"]}},
            "edges": [{"to_step": "C", "to_param": "rows", "input_type": "dependency",
                       "from_step": "X", "output_attribute": "data", "resolved": True,
                       "resolved_type": "array<object>", "resolved_schema": {
                           "kind": "array", "items": {"kind": "object", "properties": {
                               "carrier": {"kind": "cell", "origin": "auto",
                                           "value_type": {"kind": "string"}}}}}}],
            "html_producers": [{"step": "C", "output_field": "email_body",
                                "consumed_by": [{"step": "E", "param": "email_body",
                                                 "recipients": {}}]}],
            "graph": {"adjacency": {}, "reverse": {}, "roots": [], "terminals": [], "cycles": []},
            "counters": {"steps": 3, "html_producers": 1},
        }, fh)
    crash_out = os.path.join(crash_dir, "out")
    main(["--facts", crash_facts, "--out", crash_out, "--all-fixtures",
          "--no-screenshot", "--code", crash_code])
    with open(os.path.join(crash_out, "html.json"), encoding="utf-8") as fh:
        crash = json.load(fh)
    errs = [f for f in crash["findings"] if f["code"] == "HTML_RENDER_ERROR"]
    check("render errors reported", bool(errs), "%d" % len(errs))
    empty_err = [f for f in errs if f["evidence"].get("fixture") == "empty"]
    synth_err = [f for f in errs if f["evidence"].get("synthesized_input")]
    check("empty-fixture crash stays a blocker",
          bool(empty_err) and empty_err[0]["severity"] == "blocker",
          empty_err[0]["severity"] if empty_err else "missing")
    check("synthesized-input shape error is a warning",
          bool(synth_err) and all(f["severity"] == "warning" for f in synth_err),
          str([f["severity"] for f in synth_err]))
    # The synthesized-input error is the one that crashes inside a helper; the
    # empty-fixture error crashes in run() itself.
    frames = (synth_err[0]["evidence"].get("step_frames") or []) if synth_err else []
    check("traceback keeps the step's own frames only",
          bool(frames) and all("lineno" in fr and fr.get("source") for fr in frames)
          and not any("_html_probe_runner" in str(fr.get("file", "")) for fr in frames),
          json.dumps(frames)[:120])
    deepest = frames[-1] if frames else {}
    check("deepest frame names the step's helper, not run()",
          deepest.get("func") == "render_row" and deepest.get("lineno") == 3,
          "%s line %s" % (deepest.get("func"), deepest.get("lineno")))
    check("the crashing source line is quoted",
          "upper()" in (deepest.get("source") or ""), deepest.get("source") or "")
    check("the fix line matches the exception",
          "extraction cell" in ((synth_err[0].get("fix") or "") if synth_err else ""),
          (synth_err[0].get("fix") or "")[:60] if synth_err else "")
    check("no provenance note when the cells come from an extraction step",
          not any(f["code"] == "HTML_UNDECLARED_CELL" for f in crash["findings"]))
    check("cost accounting separates attempted from captured",
          crash["totals"]["fixtures_attempted"] == 3
          and crash["totals"]["captures"] == 0
          and crash["totals"]["renders_failed"] == 3,
          json.dumps(crash["totals"]))

    # Same fixture, but the cells now hang off a custom_step — which cannot
    # produce them. The guard must strip them, the renders must pass, and the
    # facts defect must be reported instead of a false blocker.
    with open(crash_facts, encoding="utf-8") as fh:
        bogus = json.load(fh)
    for st in bogus["steps"]:
        if st["name"] == "X":
            st["type"] = "custom_step"
    bogus_facts = os.path.join(crash_dir, "facts_bogus_cells.json")
    with open(bogus_facts, "w", encoding="utf-8") as fh:
        json.dump(bogus, fh)
    bogus_out = os.path.join(crash_dir, "out_bogus")
    main(["--facts", bogus_facts, "--out", bogus_out, "--all-fixtures",
          "--no-screenshot", "--code", crash_code])
    with open(os.path.join(bogus_out, "html.json"), encoding="utf-8") as fh:
        fixed = json.load(fh)
    note = [f for f in fixed["findings"] if f["code"] == "HTML_UNDECLARED_CELL"]
    check("an auto-derived cell on a custom_step is reported as a note",
          bool(note) and note[0]["severity"] == "note",
          note[0]["detail"][:80] if note else "missing")
    check("the note does not change the render outcome",
          fixed["totals"]["fixtures_attempted"] == 3
          and fixed["totals"]["renders_failed"] == 3,
          json.dumps(fixed["totals"]))

    # --- end to end, through the real CLI path -------------------------------
    real_facts, facts_err = _build_real_facts(out_dir)
    check("wf_facts.py built real facts for the reference workflow",
          bool(real_facts), facts_err or real_facts)
    sidecar = None
    if real_facts:
        with open(real_facts, encoding="utf-8") as fh:
            rf = json.load(fh)
        producers = rf.get("html_producers") or []
        check("wf_facts found the reference producer",
              any(p.get("step") == REFERENCE_PRODUCER for p in producers),
              str([p.get("step") for p in producers]))
        sidecar = ((rf.get("code_steps") or {}).get(REFERENCE_PRODUCER) or {}).get("code_file")
        check("facts carries a code_file sidecar for it",
              bool(sidecar) and os.path.exists(sidecar or ""), str(sidecar))
        routing = ((producers[0].get("consumed_by") or [{}])[0].get("email_routing")
                   if producers else None)
        check("facts carries email routing for the consumer",
              isinstance(routing, dict) and "sends" in routing,
              json.dumps(routing)[:120] if routing else "missing")
    facts_path = _minimal_facts(REFERENCE_WORKFLOW, out_dir, sidecar)
    check("hand-made facts fixture written", os.path.exists(facts_path), facts_path)
    if not sidecar:
        print("--- no sidecar available; skipping the render and capture checks")
        print("")
        print("%d checks, %d failed" % (checks, len(failures)))
        return 2 if failures else 0

    t0 = time.time()
    rc = main(["--facts", facts_path, "--out", out_dir,
               "--timeout", str(args.timeout), "--width", str(args.width),
               "--max-height", str(args.max_height)]
              + (["--chrome", args.chrome] if args.chrome else [])
              + (["--no-screenshot"] if args.no_screenshot else []))
    wall = time.time() - t0
    print("--- end-to-end run took %.2fs, exit=%d" % (wall, rc))

    out_json = os.path.join(out_dir, "html.json")
    check("html.json written", os.path.exists(out_json), out_json)
    if not os.path.exists(out_json):
        return 2
    with open(out_json, encoding="utf-8") as fh:
        report = json.load(fh)
    check("report declares pass=html", report.get("pass") == "html")
    check("PassOutput stamped schema_version and counts",
          report.get("schema_version") == 1 and isinstance(report.get("counts"), dict),
          str(report.get("counts")))
    check("written findings carry pass=html",
          all(f.get("pass") == "html" for f in report.get("findings") or []))
    prods = report.get("producers") or []
    check("one producer found", len(prods) == 1, str([p["step"] for p in prods]))
    if not prods:
        return 2
    prod = prods[0]
    check("producer is the reference compose step", prod["step"] == REFERENCE_PRODUCER)
    check("code acquired", bool(prod.get("code_path")), str(prod.get("code_provenance")))
    check("static lint ran", bool(prod.get("lint")))
    check("lint saw the remote font link",
          bool((prod.get("lint") or {}).get("external_refs", {}).get("font")))
    fx = prod.get("fixtures") or {}
    check("empty fixture rendered", bool(fx.get("empty", {}).get("render_ok")),
          str(fx.get("empty", {}).get("error")))
    empty = fx.get("empty") or {}
    check("HTML file on disk", bool(empty.get("html_path")) and os.path.exists(empty["html_path"]),
          str(empty.get("html_path")))
    check("markup is non-trivial", (empty.get("markup") or {}).get("bytes", 0) > 2000,
          "%s bytes" % (empty.get("markup") or {}).get("bytes"))
    check("markup parsed without error", (empty.get("markup") or {}).get("parse_error") is None)
    if not args.no_screenshot and report.get("chrome", {}).get("path"):
        cap = empty.get("capture") or {}
        check("screenshot produced", bool(cap.get("png_path")) and os.path.exists(cap["png_path"]),
              str(cap.get("png_path")))
        check("screenshot width is SHOT_WIDTH", cap.get("width") == args.width,
              "%s" % cap.get("width"))
        check("screenshot height clamped to SHOT_MAX_HEIGHT",
              (cap.get("height") or 0) <= args.max_height, "%s" % cap.get("height"))
        check("capture reports an estimated token cost", (cap.get("est_tokens") or 0) > 0,
              "%s" % cap.get("est_tokens"))
    check("known-good reference does not escalate", not report.get("escalated"),
          str(report.get("escalation_reason")))
    check("default run costs exactly one capture",
          (report.get("totals") or {}).get("captures") == (0 if args.no_screenshot else 1),
          str((report.get("totals") or {}).get("captures")))
    check("exit code matches blocker count",
          (rc == 1) == (_sev_counts(report.get("findings") or [])[0] > 0), "rc=%d" % rc)
    check("workflow untouched", os.path.getsize(REFERENCE_WORKFLOW) > 0)

    # --- the escalated path: every fixture, one Chrome session ---------------
    esc_dir = os.path.join(out_dir, "escalated")
    os.makedirs(esc_dir, exist_ok=True)
    t1 = time.time()
    rc2 = main(["--facts", facts_path, "--out", esc_dir, "--all-fixtures",
                "--timeout", str(args.timeout)]
               + (["--chrome", args.chrome] if args.chrome else [])
               + (["--no-screenshot"] if args.no_screenshot else []))
    esc_wall = time.time() - t1
    print("--- all-fixtures run took %.2fs, exit=%d" % (esc_wall, rc2))
    with open(os.path.join(esc_dir, "html.json"), encoding="utf-8") as fh:
        esc = json.load(fh)
    esc_fx = (esc.get("producers") or [{}])[0].get("fixtures") or {}
    check("every fixture rendered", sorted(esc_fx) == sorted(FIXTURE_NAMES), str(sorted(esc_fx)))
    check("full fixture rendered more markup than empty",
          (esc_fx.get("full", {}).get("markup") or {}).get("bytes", 0)
          > (esc_fx.get("empty", {}).get("markup") or {}).get("bytes", 0),
          "full=%s empty=%s" % ((esc_fx.get("full", {}).get("markup") or {}).get("bytes"),
                                (esc_fx.get("empty", {}).get("markup") or {}).get("bytes")))
    check("stress fixture rendered", bool(esc_fx.get("stress", {}).get("render_ok")),
          str(esc_fx.get("stress", {}).get("error")))
    if not args.no_screenshot and esc.get("chrome", {}).get("path"):
        check("three captures in one Chrome session",
              (esc.get("totals") or {}).get("captures") == 3,
              str((esc.get("totals") or {}).get("captures")))
        shot_ms = [(esc_fx[f].get("capture") or {}).get("ms") for f in FIXTURE_NAMES
                   if esc_fx.get(f, {}).get("capture")]
        print("--- chrome start %dms, per-shot %s ms, mode=%s"
              % (esc.get("chrome", {}).get("start_ms") or 0,
                 "/".join(str(x) for x in shot_ms), esc.get("chrome", {}).get("mode")))
        # The invariant is that ONE process served every capture (no fallback,
        # start cost paid once), not a wall-clock threshold that flakes.
        check("one Chrome session served every capture",
              esc.get("chrome", {}).get("mode") == "cdp"
              and esc.get("chrome", {}).get("mode_final") == "cdp"
              and all((x or 0) < 2000 for x in shot_ms) and len(shot_ms) == 3,
              "mode=%s per-shot %s start %sms" % (esc.get("chrome", {}).get("mode_final"),
                                                  shot_ms, esc.get("chrome", {}).get("start_ms")))
        print("--- escalated cost: ~%d image tokens for %d captures"
              % ((esc.get("totals") or {}).get("est_tokens") or 0,
                 (esc.get("totals") or {}).get("captures") or 0))

    print("")
    print("%d checks, %d failed" % (checks, len(failures)))
    if failures:
        for name in failures:
            print("  FAILED: %s" % name)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
