#!/usr/bin/env python3
"""registry_match.py -- verify pass 4: module upgrade / adopt / extract candidates.

Compares a workflow (through its facts.json, per CONTRACT.md rule 2 -- nothing
here reads workflow.json) against the local module registry and proposes three
kinds of work:

  Job 1  upgrade  -- a workflow step name exactly matches a module's declared
                     step name, so that module is already spliced into the
                     workflow. If a newer non-yanked release exists, report the
                     version delta (added / removed / renamed steps) and which
                     of the workflow's live edges a rename would break.
  Job 2  adopt    -- a workflow step that is not an exact name match but whose
                     normalized leaf-field set overlaps a module step's field
                     set above ADOPT_MIN_OVERLAP, with agreeing step types.
                     Reports the gain list, the loss list (what adopting would
                     throw away -- this is the list that matters), and the
                     downstream consumers that would need rewiring.
  Job 3  extract  -- a step matching nothing that looks reusable. Emits a
                     ready-to-run `registry.py extract` command.

This script NEVER modifies a workflow or a module artifact. The only files it
writes are `<out>/modules.json` and the catalog index cache (see below).
Exit codes per CONTRACT: 0 = clean, 1 = blockers (this pass raises none), 2 =
the script itself failed.

THE COST RULE
-------------
`modules/` is 5.9 MB of module.json artifacts -- roughly 1.5 million tokens.
Module artifacts must never reach model context and must not be re-read on
every run. So this pass keeps a catalog index cache holding only what matching
needs (module id, version, yank state, declared step names, step types, per
step normalized leaf field names, ports, options, requires). The cache lives at
`<registry>/.verify_index.json`, or in `--out` when the registry path is not
writable. It is keyed on the sha256 of `index.json`, so adding, yanking or
re-minifying a module invalidates it automatically; `CACHE_VERSION` invalidates
it when this file's extraction logic changes.

CLI BOUNDARY
------------
The registry repo's documented rule is to read the catalog through its own CLI,
so enumeration and catalog-level facts come from `registry.py list --json` and
`registry.py info <id> --json`. Per-step output schemas are the one thing the
CLI does not expose (`info` returns step *names* only, and only for the latest
version), and field-set matching is built entirely on those schemas -- so the
cold cache build reads `modules/<id>/<version>/module.json` directly. That is
the single sanctioned bypass; it happens once per index.json change, never on a
warm run, and the artifact bytes are reduced to field-name lists before
anything is kept in memory.

Reference docs behind the rules used here:
  ../../reference/input_mappings.md   -- glom paths, cell wrapping
  <registry>/docs/MODULE_SCHEMA.md    -- _meta, ports, options, minified steps
  <registry>/README.md                -- immutable versions, yank, config.lock
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402  -- path is set above
    THRESHOLDS, MAX_TABLE_ROWS, OPAQUE_FIELD_KINDS, OUTPUT_ENVELOPE_KEYS,
    PassOutput, add_common_args, comparable_field_map, comparable_fields,
    CELL_MEMBERS, comparable_raw_paths, field_identity, finding, load_facts,
    require_args, resolve_path, schema_leaf_paths, table,
)

ADOPT_MIN_OVERLAP = float(THRESHOLDS["ADOPT_MIN_OVERLAP"])
ADOPT_STRONG_OVERLAP = float(THRESHOLDS["ADOPT_STRONG_OVERLAP"])
# Minimum field count on BOTH sides before an overlap score is evidence.
# Jaccard over one-field sets manufactures perfect matches: every dashboard
# producer in the catalog emits exactly `email_body`, so without a floor a
# workflow's summary step scores 1.00 against three unrelated module steps.
ADOPT_MIN_FIELDS = int(THRESHOLDS["ADOPT_MIN_FIELDS"])

PASS_NAME = "modules"
# Normalized root envelope keys, for the B2 guard. Cell members are resolved
# per schema instead (see walker_drift rule 2) because a field merely NAMED
# like a cell member is legitimate.
_ENVELOPE_TOKENS = frozenset(field_identity(k)[1] for k in OUTPUT_ENVELOPE_KEYS)
CACHE_VERSION = 6
CACHE_BASENAME = ".verify_index.json"
MAX_SCHEMA_DEPTH = 12  # mirrors common.schema_leaf_paths' documented cap
DEFAULT_REGISTRY = os.environ.get("MODULE_REGISTRY") or os.path.expanduser(
    "~/fai/module-registry"
)

# Step types that cannot stand alone as a reusable module (Job 3 rejects them).
# Terminal + manual lists come from CONTRACT.md "Notes on producing it".
TERMINAL_STEP_TYPES = {
    "email",
    "fill_docx",
    "submission_summary_generator",
    "document_viewer",
    "text_block",
    "hold",
}
MANUAL_STEP_TYPES = {"pause", "hold", "manual_input"}
NON_EXTRACTABLE_TYPES = TERMINAL_STEP_TYPES | MANUAL_STEP_TYPES | {
    "decision",
    "prepare_documents",  # workflow_base owns this one already
    "knowledge_base",     # a KB handle is a platform primitive, not a module
}

# --------------------------------------------------------------------------
# Job 3 reusability heuristics -- documented here, applied in extract_candidates()
# --------------------------------------------------------------------------
# A step is an extract candidate only when ALL of these hold. Each rule exists
# to keep the proposal from being noise a human has to argue with:
#
#  H1  no exact name match in the catalog, and best adopt overlap against every
#      indexed module step is below ADOPT_MIN_OVERLAP. (Covered by the caller.)
#  H2  more than a trivial number of fields: >= MIN_EXTRACT_FIELDS leaf fields.
#      A 1-3 field step is a wiring detail, not a module.
#  H3  self-contained input boundary: at most MAX_EXTRACT_INPUTS distinct
#      upstream steps feed it, and every incoming dependency edge resolved in
#      facts. If we cannot name the provider of every input we cannot write the
#      `--input port=module.port` mappings, so the proposal would not be
#      runnable -- and a step with a sprawling input boundary is coupled to its
#      workflow, not reusable.
#  H4  producer step type: not terminal, not manual, not a decision. Those are
#      presentation or human-gate steps; a module wants the step that computes.
#  H5  no obvious carrier- or customer-specific hardcoding. Rather than keep an
#      unmaintainable deny-list of carrier names, derive the specificity tokens
#      from the workflow's own name (facts.workflow.name) minus a small generic
#      stopword set: a step or field named after the workflow's own subject is
#      by construction workflow-specific. A short all-caps acronym in the step
#      name that is not a known industry-generic one is the second signal.
MIN_EXTRACT_FIELDS = int(THRESHOLDS["MIN_EXTRACT_FIELDS"])
MAX_EXTRACT_INPUTS = int(THRESHOLDS["MAX_EXTRACT_INPUTS"])

# Generic words that appear in workflow names and carry no carrier/customer
# identity, so they are not treated as specificity markers.
GENERIC_NAME_TOKENS = {
    "audit", "auto", "business", "check", "commercial", "data", "demo", "doc",
    "docs", "document", "documents", "extract", "extraction", "flow", "general",
    "intake", "liability", "loss", "new", "policy", "process", "processing",
    "prod", "quote", "renewal", "review", "run", "runs", "staging", "submission",
    "summary", "test", "umbrella", "underwriting", "workflow",
}
# All-caps acronyms that are industry-generic, not customer identity.
GENERIC_ACRONYMS = {
    "ACORD", "AI", "API", "CSV", "DOCX", "EPA", "ECHO", "GL", "HTML", "ID",
    "JSON", "KB", "LOB", "NAICS", "NHTSA", "OCR", "PDF", "QA", "SOV", "SQL",
    "URL", "UW", "VIN", "XLSX", "ZIP",
}

# JSON Schema / platform keywords that are never a field name.
_SCHEMA_KEYWORDS = {
    "type", "properties", "items", "description", "title", "glom_path",
    "additionalProperties", "required", "enum", "anyOf", "oneOf", "allOf",
    "default", "format", "display_field", "nullable", "examples", "minimum",
    "maximum", "minItems", "maxItems", "pattern", "definitions", "const",
    "table_v1_log_id",
}
_NONALNUM = re.compile(r"[^a-z0-9]+")
_IDENT_BAD = re.compile(r"[^a-z0-9]+")
_ACRONYM = re.compile(r"\b[A-Z]{2,5}\b")

# ==========================================================================
# normalization + scoring
# ==========================================================================
def jaccard(a, b):
    """|A n B| / |A u B|. Empty on either side scores 0.0 (never a candidate)."""
    if not a or not b:
        return 0.0
    a = set(a)
    b = set(b)
    union = len(a | b)
    if union == 0:
        return 0.0
    return len(a & b) / float(union)


def _snake(text):
    s = _IDENT_BAD.sub("_", str(text).lower()).strip("_")
    return re.sub(r"_+", "_", s) or "module"


def semver_key(v):
    parts = str(v).split(".")
    out = []
    for p in parts[:3]:
        try:
            out.append(int(p))
        except ValueError:
            out.append(-1)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


# ==========================================================================
# leaf extraction -- module side (raw platform output_schema)
# ==========================================================================
def _module_props(node):
    """The child map of a module schema node, or None if it is a leaf."""
    props = node.get("properties")
    if isinstance(props, dict) and props:
        return props
    # Some platform named types are stored as a bare nested dict with no
    # JSON Schema keywords at all (e.g. knowledge_base: {"kb": {...}}). Treat
    # its non-keyword keys as properties so the step still has a field set.
    if node.get("type") in (None, "object") and not isinstance(node.get("items"), dict):
        cand = {
            k: v for k, v in node.items()
            if k not in _SCHEMA_KEYWORDS and not k.startswith("$") and not k.startswith("__")
        }
        if cand:
            return cand
    return None


def module_leaf_fields(schema):
    """Leaf field display-paths of a module step's raw output_schema.

    Leaf rule (kept deliberately symmetric with facts_leaf_fields):
      * a node typed `cell` or `file` is a leaf -- do not descend into
        value/confidence_score/citations or user_document_id/filename. facts.json
        stops at the cell and at the file for the same reason, and the two field
        sets have to be comparable.
      * a node with properties descends; a node with items descends through a
        synthetic "0" segment which normalization then drops.
      * anything else is a leaf.
    """
    out = []
    seen = set()

    def walk(node, prefix, depth):
        if depth > MAX_SCHEMA_DEPTH or not isinstance(node, dict):
            emit(prefix)
            return
        t = node.get("type")
        if t in ("cell", "file"):
            emit(prefix)
            return
        props = _module_props(node)
        if props:
            for k in props:
                if depth == 0 and k in OUTPUT_ENVELOPE_KEYS:
                    continue
                walk(props[k], prefix + [k], depth + 1)
            return
        items = node.get("items")
        if isinstance(items, dict):
            walk(items, prefix + ["0"], depth + 1)
            return
        emit(prefix)

    def emit(prefix):
        disp, tok = field_identity(prefix)
        if not tok or tok in seen:
            return
        seen.add(tok)
        out.append(disp)

    if isinstance(schema, dict):
        walk(schema, [], 0)
    return out


# ==========================================================================
# leaf extraction -- workflow side (normalized facts schema language)
# ==========================================================================
# ==========================================================================
# catalog index cache
# ==========================================================================
def _registry_cli(registry, args, timeout=60):
    """Run the registry's own CLI and parse its --json output."""
    cmd = [sys.executable, os.path.join(registry, "scripts", "registry.py")] + list(args)
    proc = subprocess.run(
        cmd, cwd=registry, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "registry.py %s failed (%d): %s"
            % (" ".join(args), proc.returncode,
               proc.stderr.decode("utf-8", "replace").strip()[:400])
        )
    return json.loads(proc.stdout.decode("utf-8", "replace"))


def _normalize_outputs(outputs):
    """_meta.outputs -> {port: {"steps": [...], "attribute": str|None}}.

    Handles the three documented forms: bare string, {"from": [...anchors]},
    and {"attribute": ..., "from": ...}. See MODULE_SCHEMA.md "outputs".
    """
    norm = {}
    if not isinstance(outputs, dict):
        return norm
    for port, spec in outputs.items():
        steps = []
        attribute = None
        if isinstance(spec, str):
            steps = [spec]
        elif isinstance(spec, dict):
            attribute = spec.get("attribute")
            frm = spec.get("from")
            if isinstance(frm, str):
                steps = [frm]
            elif isinstance(frm, list):
                for anchor in frm:
                    if isinstance(anchor, dict) and isinstance(anchor.get("step"), str):
                        steps.append(anchor["step"])
                    elif isinstance(anchor, str):
                        steps.append(anchor)
        norm[port] = {"steps": steps, "attribute": attribute}
    return norm


def _normalize_inputs(inputs):
    """_meta.inputs -> {port: source-ref string}."""
    norm = {}
    if not isinstance(inputs, dict):
        return norm
    for port, spec in inputs.items():
        if isinstance(spec, str):
            norm[port] = spec
        elif isinstance(spec, dict) and isinstance(spec.get("source"), str):
            norm[port] = spec["source"]
    return norm


def build_catalog(registry):
    """Cold build of the catalog index. Reads every indexed artifact once."""
    index_path = os.path.join(registry, "index.json")
    catalog = {
        "cache_version": CACHE_VERSION,
        "registry": os.path.abspath(registry),
        "index_sha256": sha256_file(index_path),
        "modules": {},
    }
    artifact_bytes = 0
    step_names = 0
    field_count = 0

    # Enumeration + catalog-level facts through the registry CLI (its rule).
    listing = _registry_cli(registry, ["list", "--json"])
    ids = [m["id"] for m in listing if isinstance(m, dict) and m.get("id")]
    yanked = {}
    for mid in ids:
        info = _registry_cli(registry, ["info", mid, "--json"])
        for row in info.get("all_versions") or []:
            yanked[(mid, row.get("version"))] = bool(row.get("yanked"))

    # index.json is the authority on where each artifact lives.
    with open(index_path, "r", encoding="utf-8") as fh:
        index = json.load(fh)
    entries = index.get("modules") or []

    for entry in entries:
        mid = entry.get("id")
        ver = entry.get("version")
        rel = entry.get("artifact")
        if not (mid and ver and rel):
            continue
        path = os.path.join(registry, rel)
        if not os.path.isfile(path):
            continue
        artifact_bytes += os.path.getsize(path)
        # --- the sanctioned CLI bypass: per-step output schemas only ---
        with open(path, "r", encoding="utf-8") as fh:
            art = json.load(fh)
        meta = art.get("_meta") or {}
        steps = []
        for s in art.get("steps") or []:
            if not isinstance(s, dict):
                continue
            fields = module_leaf_fields(s.get("output_schema"))
            steps.append({
                "name": s.get("name"),
                "type": s.get("type"),
                "fields": fields,
            })
            step_names += 1
            field_count += len(fields)
        options = {}
        for oname, ospec in (meta.get("options") or {}).items():
            if not isinstance(ospec, dict):
                continue
            eff = ospec.get("effect") or {}
            options[oname] = {
                "type": ospec.get("type"),
                "default": ospec.get("default"),
                "values": ospec.get("values"),
                "effect_kind": eff.get("kind"),
                "effect_steps": eff.get("steps"),
                "effect_mapping": eff.get("mapping"),
            }
        mod = catalog["modules"].setdefault(mid, {"versions": {}})
        mod["versions"][ver] = {
            "yanked": bool(yanked.get((mid, ver), entry.get("yanked", False))),
            "description": meta.get("description") or entry.get("description"),
            "tags": meta.get("tags") or entry.get("tags") or [],
            "requires": meta.get("requires") or entry.get("requires") or [],
            "inputs": _normalize_inputs(meta.get("inputs")),
            "outputs": _normalize_outputs(meta.get("outputs")),
            "options": options,
            "artifact": rel,
            "steps": steps,
        }

    for mid, mod in catalog["modules"].items():
        live = [v for v, d in mod["versions"].items() if not d["yanked"]]
        pool = live or list(mod["versions"])
        mod["latest"] = sorted(pool, key=semver_key)[-1] if pool else None

    # Which module ids claim each step name. Older releases of a module can
    # bundle steps that were later split into their own module, so a step name
    # is NOT a unique key: in this registry `Prepare Documents` and
    # `Initialize Knowledge Base` are claimed by both pc_common_foundation and
    # workflow_base, and both Tier 1/Tier 2 classify steps by both
    # pc_classification and pc_common_foundation. A name claimed by two or more
    # module ids proves nothing about which one (if any) is installed, so it is
    # recorded here and treated as non-distinguishing at match time. Derived
    # from the catalog, never hardcoded.
    owners = {}
    for mid, mod in catalog["modules"].items():
        for rel in mod["versions"].values():
            for st in rel["steps"]:
                if st.get("name"):
                    owners.setdefault(st["name"], set()).add(mid)
    catalog["step_name_owners"] = dict(
        (name, sorted(ids)) for name, ids in owners.items())
    catalog["non_distinguishing_steps"] = sorted(
        name for name, ids in owners.items() if len(ids) > 1)

    catalog["counters"] = {
        "modules": len(catalog["modules"]),
        "non_distinguishing_step_names": len(catalog["non_distinguishing_steps"]),
        "releases": sum(len(m["versions"]) for m in catalog["modules"].values()),
        "step_names": step_names,
        "distinct_step_names": len({
            s["name"]
            for m in catalog["modules"].values()
            for v in m["versions"].values()
            for s in v["steps"]
        }),
        "fields": field_count,
        "artifact_bytes": artifact_bytes,
    }
    return catalog


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(262144), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_path_for(registry, out_dir):
    """`<registry>/.verify_index.json`, or `<out>` when the registry is read-only."""
    if os.access(registry, os.W_OK):
        return os.path.join(registry, CACHE_BASENAME)
    return os.path.join(out_dir, CACHE_BASENAME)


def load_catalog(registry, cache_path, force=False):
    """Return (catalog, cache_hit, cache_path). Self-invalidates on index.json."""
    index_path = os.path.join(registry, "index.json")
    want = sha256_file(index_path)
    if not force and os.path.isfile(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as fh:
                cached = json.load(fh)
            if (cached.get("cache_version") == CACHE_VERSION
                    and cached.get("index_sha256") == want
                    and isinstance(cached.get("modules"), dict)):
                return cached, True, cache_path
        except (ValueError, OSError):
            pass
    catalog = build_catalog(registry)
    try:
        tmp = cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(catalog, fh, separators=(",", ":"), sort_keys=True)
        os.replace(tmp, cache_path)
    except OSError:
        pass  # a cache we cannot write is a slow run, not a failed one
    return catalog, False, cache_path


# ==========================================================================
# catalog views
# ==========================================================================
def index_step_names(catalog):
    """step name -> [(module_id, version, step_dict)] across every release."""
    by_name = {}
    for mid, mod in catalog["modules"].items():
        for ver, rel in mod["versions"].items():
            for s in rel["steps"]:
                if s.get("name"):
                    by_name.setdefault(s["name"], []).append((mid, ver, s))
    return by_name


def step_port(catalog, mid, ver, step_name):
    """The port a module release exposes for one of its steps, if any."""
    rel = catalog["modules"].get(mid, {}).get("versions", {}).get(ver)
    if not rel:
        return None
    for port, spec in rel["outputs"].items():
        if step_name in (spec.get("steps") or []):
            return port
    return None


def provider_for_step(catalog, step_name):
    """(module_id, port) that publishes `step_name`, preferring latest releases."""
    best = None
    for mid, mod in catalog["modules"].items():
        latest = mod.get("latest")
        order = ([latest] if latest else []) + sorted(
            [v for v in mod["versions"] if v != latest], key=semver_key, reverse=True
        )
        for ver in order:
            rel = mod["versions"].get(ver)
            if not rel:
                continue
            if not any(s.get("name") == step_name for s in rel["steps"]):
                continue
            port = step_port(catalog, mid, ver, step_name)
            if port:
                cand = (mid, port, ver == latest)
                if best is None or (cand[2] and not best[2]):
                    best = cand
                break
    if best:
        return best[0], best[1]
    return None, None


# ==========================================================================
# facts views
# ==========================================================================
class Facts(object):
    def __init__(self, data, path):
        self.path = path
        self.raw = data
        self.workflow = data.get("workflow") or {}
        self.steps = [s for s in (data.get("steps") or []) if isinstance(s, dict)]
        self.edges = [e for e in (data.get("edges") or []) if isinstance(e, dict)]
        self.step_by_name = {s.get("name"): s for s in self.steps if s.get("name")}
        self.fields = {}   # step -> [display path]
        self.tokens = {}   # step -> set(token)
        self.token_disp = {}  # step -> {token: display}
        for s in self.steps:
            name = s.get("name")
            if not name:
                continue
            disp = comparable_fields(s.get("output") or {})
            self.fields[name] = disp
            tmap = {}
            for d in disp:
                _, tok = field_identity(d)
                if tok:
                    tmap[tok] = d
            self.tokens[name] = set(tmap)
            self.token_disp[name] = tmap
        self.out_edges = {}
        self.in_edges = {}
        for e in self.edges:
            if e.get("from_step"):
                self.out_edges.setdefault(e["from_step"], []).append(e)
            if e.get("to_step"):
                self.in_edges.setdefault(e["to_step"], []).append(e)

    # -- specificity tokens for heuristic H5 -------------------------------
    def specificity_tokens(self):
        name = self.workflow.get("name") or ""
        toks = set()
        for raw in re.split(r"[^A-Za-z0-9]+", name):
            t = raw.lower()
            if len(t) >= 3 and t not in GENERIC_NAME_TOKENS and not t.isdigit():
                toks.add(t)
        return toks


def consumers_of(facts, step_name):
    """Live downstream consumers of a step: [(to_step, to_param, attribute)]."""
    out = []
    for e in facts.out_edges.get(step_name, []):
        out.append((e.get("to_step"), e.get("to_param"), e.get("output_attribute")))
    return out


def _attr_token(attr):
    """Normalize an edge output_attribute for comparison against field tokens.

    A consumer reading a cell writes `data.x.value`; the field token for that
    cell is `x`. Strip one trailing `value` segment so the two line up. An empty
    result means "consumes the whole step output", which makes every field live.
    """
    if not attr:
        return ""
    segs = [s for s in str(attr).split(".") if s != ""]
    if segs and segs[-1] == "value":
        segs = segs[:-1]
    _, tok = field_identity(segs)
    return tok


def consumers_touching(facts, step_name, field_token):
    """Consumers whose wiring would lose `field_token` if the step were swapped."""
    hits = []
    for e in facts.out_edges.get(step_name, []):
        at = _attr_token(e.get("output_attribute"))
        if (at == "" or at == field_token
                or at.startswith(field_token + ".")
                or field_token.startswith(at + ".")):
            hits.append(e)
    return hits


# ==========================================================================
# mechanism
# ==========================================================================
def workflow_lock_path(facts):
    """config.lock.json sitting beside the workflow, or None."""
    wf = facts.workflow.get("path")
    if not wf:
        return None
    cand = os.path.join(os.path.dirname(os.path.abspath(wf)), "config.lock.json")
    return cand if os.path.isfile(cand) else None


def workflow_config_path(facts):
    """config.json sitting beside the workflow, or None."""
    wf = facts.workflow.get("path")
    if not wf:
        return None
    cand = os.path.join(os.path.dirname(os.path.abspath(wf)), "config.json")
    return cand if os.path.isfile(cand) else None


def declared_module_ids(config_path, lock_path):
    """Module ids named by a config.json / config.lock.json beside the workflow.

    This is the only *proof* available that a module is installed: the registry
    built this workflow from that config. Everything else is inference.
    """
    ids = set()
    for path in (config_path, lock_path):
        if not path:
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (ValueError, OSError):
            continue
        for row in doc.get("modules") or []:
            if isinstance(row, dict) and row.get("id"):
                ids.add(row["id"])
    return ids


def config_path_for(lock_path):
    """The config.json that produced a lock, for use in emitted commands."""
    if not lock_path:
        return "<config.json>"
    cand = os.path.join(os.path.dirname(lock_path), "config.json")
    return cand if os.path.isfile(cand) else "<config.json>"


def locked_versions(lock_path):
    if not lock_path:
        return {}
    try:
        with open(lock_path, "r", encoding="utf-8") as fh:
            lock = json.load(fh)
    except (ValueError, OSError):
        return {}
    out = {}
    for row in lock.get("modules") or []:
        if isinstance(row, dict) and row.get("id") and row.get("version"):
            out[row["id"]] = row["version"]
    return out


def mechanism_for(job, has_lock):
    """`config-merge` when a lock sits beside the workflow or the job is an
    upgrade (the registry rebuilds module-owned steps); else `direct-splice`."""
    if job == "upgrade" or has_lock:
        return "config-merge"
    return "direct-splice"


# ==========================================================================
# Job 1 -- upgrade
# ==========================================================================
def corroborate_install(catalog, facts, mid, matched_names, declared_ids):
    """Is there real evidence that module `mid` is spliced into this workflow?

    An exact step-name match alone is NOT evidence. Platform-conventional step
    names (`Prepare Documents`, `Initialize Knowledge Base`) appear in almost
    every workflow whether or not a module put them there, and this registry
    has four step names claimed by two module ids each. So a match counts only
    when corroborated by one of:

      * `config`  -- a config.json or config.lock.json beside the workflow
                     names this module id. This is proof, not inference.
      * `fields`  -- the workflow's step and the module's same-named step agree
                     on their field sets at or above ADOPT_MIN_OVERLAP, with
                     both sides carrying at least ADOPT_MIN_FIELDS fields so a
                     one-field step cannot corroborate anything.

    Returns a dict; `ok` False means emit no upgrade proposal at all.
    """
    owners = catalog.get("step_name_owners") or {}
    weak = sorted(n for n in matched_names if len(owners.get(n, [])) > 1)
    if mid in declared_ids:
        return {"ok": True, "via": "config", "overlap": None, "step": None,
                "non_distinguishing": weak}

    versions = catalog["modules"].get(mid, {}).get("versions", {})
    best_score, best_step = 0.0, None
    for name in sorted(matched_names):
        wf_tokens = facts.tokens.get(name) or set()
        if len(wf_tokens) < ADOPT_MIN_FIELDS:
            continue
        for rel in versions.values():
            for st in rel["steps"]:
                if st.get("name") != name:
                    continue
                mtok = set()
                for fld in st["fields"]:
                    tok = field_identity(fld)[1]
                    if tok:
                        mtok.add(tok)
                if len(mtok) < ADOPT_MIN_FIELDS:
                    continue
                score = jaccard(wf_tokens, mtok)
                if score > best_score:
                    best_score, best_step = score, name
    ok = best_score >= ADOPT_MIN_OVERLAP
    return {"ok": ok, "via": "fields" if ok else None,
            "overlap": round(best_score, 3), "step": best_step,
            "non_distinguishing": weak}


def upgrade_verdict(affected, also_dropped, mech):
    """Severity + fix text for one upgrade, derived from the affected set.

    Single source of truth so the two can never contradict each other: the
    phrase "no live edge is affected" is reachable only on the branch where
    `affected` is empty. Callers must not compose their own fix text.
    """
    if affected:
        names = sorted({"%s.%s" % (a["consumer"], a["param"]) for a in affected})
        return "warning", (
            "%d live edge%s would lose its source (%s). Repoint them at the "
            "renamed or replacement steps BEFORE rebuilding, or the consumers "
            "run green on empty data." % (
                len(affected), "" if len(affected) == 1 else "s",
                ", ".join(names[:MAX_TABLE_ROWS])))
    if also_dropped:
        return "warning", (
            "This release drops %d step%s the workflow still has (%s). None of "
            "them has a live consumer, so confirm they are genuinely unused "
            "before rebuilding -- upgrading deletes them." % (
                len(also_dropped), "" if len(also_dropped) == 1 else "s",
                ", ".join(also_dropped[:MAX_TABLE_ROWS])))
    return "note", "Upgrade via %s; no live edge is affected." % mech


def assert_proposal_safe(proposal, facts):
    """Structural guard for the invariant the fix text must never violate.

    A proposal may not claim no live edge is affected while anything it drops
    or renames has a downstream consumer in facts.edges. Raises rather than
    warns: a wrong reassurance here deletes load-bearing steps from a live
    customer workflow.
    """
    if proposal.get("job") != "upgrade":
        return
    claims_clear = "no live edge is affected" in (proposal.get("fix") or "")
    touched = list(proposal.get("loss") or [])
    touched += [r["from"] for r in (proposal.get("delta") or {}).get("renames", [])]
    with_consumers = [n for n in touched if facts.out_edges.get(n)]
    if claims_clear and (proposal.get("affected_edges") or with_consumers):
        raise AssertionError(
            "registry_match invariant violated: %s@%s -> @%s claims no live "
            "edge is affected, but %s still has downstream consumers"
            % (proposal.get("module"), proposal.get("installed_version"),
               proposal.get("target_version"), with_consumers))


def infer_installed_version(catalog, facts, mid, locked):
    """Which release of `mid` the workflow already carries, and how we know."""
    if mid in locked and locked[mid] in catalog["modules"].get(mid, {}).get("versions", {}):
        return locked[mid], "config.lock.json", False
    # No lock: score every release's declared step names against the workflow's
    # and take the best. Releases whose step names are identical (a patch or a
    # schema-only minor) tie, and a tie is genuine ambiguity -- so assume the
    # NEWEST tied release. Assuming the oldest would invent an upgrade proposal
    # for every module that has ever shipped a patch.
    versions = catalog["modules"].get(mid, {}).get("versions", {})
    wf_names = set(facts.step_by_name)
    scored = []
    for ver in sorted(versions, key=semver_key):
        names = {s["name"] for s in versions[ver]["steps"] if s.get("name")}
        scored.append((jaccard(names, wf_names), ver))
    if not scored:
        return None, "no releases indexed", True
    best_score = max(s for s, _v in scored)
    tied = [v for s, v in scored if abs(s - best_score) < 1e-9]
    best = sorted(tied, key=semver_key)[-1]
    if len(tied) > 1:
        how = ("%d releases declare exactly these step names (%.2f overlap), so "
               "the installed one cannot be told apart from the artifacts"
               % (len(tied), best_score))
    else:
        how = "best step-name overlap (%.2f)" % best_score
    return best, how, len(tied) > 1


def detect_renames(old_steps, new_steps):
    """[(old_name, new_name, score)] for removed/added pairs that look renamed.

    A rename is a removed step and an added step of the same type whose field
    sets overlap at or above ADOPT_STRONG_OVERLAP. One-to-many splits are
    reported as a rename per surviving descendant, which is what the workflow's
    edges have to be repointed at anyway.
    """
    old_by = {s["name"]: s for s in old_steps if s.get("name")}
    new_by = {s["name"]: s for s in new_steps if s.get("name")}
    removed = [n for n in old_by if n not in new_by]
    added = [n for n in new_by if n not in old_by]
    pairs = []
    for o in sorted(removed):
        os_ = old_by[o]
        o_toks = {field_identity(f)[1] for f in os_["fields"]}
        for n in sorted(added):
            ns = new_by[n]
            if ns.get("type") != os_.get("type"):
                continue
            score = jaccard(o_toks, {field_identity(f)[1] for f in ns["fields"]})
            if score >= ADOPT_STRONG_OVERLAP:
                pairs.append((o, n, round(score, 3)))
    return pairs


def upgrade_jobs(catalog, facts, locked, has_lock, lock_path=None,
                 config_path=None):
    findings = []
    proposals = []
    by_name = index_step_names(catalog)
    matched_steps = set()
    declared_ids = declared_module_ids(config_path, lock_path)
    owners = catalog.get("step_name_owners") or {}

    # workflow step name -> the module ids that declare it
    hits = {}
    for s in facts.steps:
        name = s.get("name")
        if not name or name not in by_name:
            continue
        # Every exact catalog-name match is off the table for the adopt and
        # extract jobs whatever the corroboration verdict says: `registry.py
        # validate` rejects `step name collision: 'S' produced by both A and B`,
        # so proposing to extract a name the catalog already ships is invalid,
        # and proposing to adopt a step by the name it already has is
        # incoherent. Corroboration governs only the upgrade claim below.
        matched_steps.add(name)
        for mid, _ver, _step in by_name[name]:
            hits.setdefault(mid, set()).add(name)

    for mid in sorted(hits):
        mod = catalog["modules"][mid]
        present = sorted(hits[mid])

        # --- gate 1: is the module actually installed? --------------------
        eviction = corroborate_install(catalog, facts, mid, present, declared_ids)
        if not eviction["ok"]:
            weak = eviction["non_distinguishing"]
            findings.append(finding(
                "MODULE_MATCH_UNCONFIRMED", "note",
                "Step names match a module, but it may not be installed",
                "%s declares %d step name%s this workflow also uses (%s), but "
                "nothing corroborates that the module is installed: best field "
                "overlap on those steps is %.2f (floor %.2f) and no config.json "
                "or config.lock.json beside the workflow names it.%s"
                % (mid, len(present), "" if len(present) == 1 else "s",
                   ", ".join("`%s`" % n for n in present[:MAX_TABLE_ROWS]),
                   eviction["overlap"] or 0.0, ADOPT_MIN_OVERLAP,
                   (" %s claimed by more than one module (%s), so the name alone "
                    "proves nothing." % (
                        ", ".join("`%s`" % n for n in weak[:MAX_TABLE_ROWS]),
                        ", ".join(sorted(set(
                            oid for n in weak for oid in owners.get(n, []))))))
                   if weak else ""),
                fix="No action. If this module IS installed, put a config.json "
                    "beside the workflow so the next run can tell; the pass "
                    "will not propose an upgrade on a name match alone.",
                evidence={"module": mid, "matched_steps": present[:MAX_TABLE_ROWS],
                          "best_field_overlap": eviction["overlap"],
                          "non_distinguishing": weak,
                          "corroborated": False},
                group_key="MODULE_MATCH_UNCONFIRMED:%s" % mid,
            ))
            continue

        installed, how, ambiguous = infer_installed_version(
            catalog, facts, mid, locked)
        if not installed:
            continue
        newer = sorted(
            [v for v, d in mod["versions"].items()
             if not d["yanked"] and semver_key(v) > semver_key(installed)],
            key=semver_key,
        )
        installed_names = [x["name"] for x in mod["versions"][installed]["steps"]
                           if x.get("name")]
        coverage = (len(present) / float(len(installed_names))) if installed_names else 0.0
        # Two different epistemic states, and the wording must not blur them.
        # `config` is proof: the registry built this workflow from that config.
        # `fields` is inference from schema similarity -- strong (the corpus
        # separates cleanly, corroborated matches at 0.92-0.93 against nothing
        # above 0.29) but a workflow hand-built by copying a module's schema
        # scores the same as one the registry generated, and a user may act on
        # the difference. So only `config` gets an unqualified "is installed".
        proven = eviction["via"] == "config"
        if proven:
            via = "a config file beside the workflow names it"
            claim = "is installed"
        else:
            via = "field overlap %.2f on `%s`" % (eviction["overlap"],
                                                  eviction["step"])
            claim = ("is almost certainly installed (no config.json beside the "
                     "workflow to confirm)")

        # --- gate 2: ambiguous inference is a question, not an instruction --
        if ambiguous and newer:
            cands = [v for v in sorted(mod["versions"], key=semver_key)
                     if not mod["versions"][v]["yanked"]]
            findings.append(finding(
                "MODULE_VERSION_AMBIGUOUS", "note",
                "Module is installed but its version cannot be determined",
                "%s %s (%s), but %s. Which release is installed "
                "decides whether an upgrade exists and what it would change, "
                "so no upgrade is proposed. Candidate releases: %s."
                % (mid, claim, via, how, ", ".join(cands[:MAX_TABLE_ROWS])),
                fix="Which release of %s is this workflow built from? A "
                    "config.lock.json beside the workflow answers it exactly; "
                    "with one present this pass reports the delta precisely."
                    % mid,
                evidence={"module": mid, "candidates": cands[:MAX_TABLE_ROWS],
                          "matched_steps": present[:MAX_TABLE_ROWS],
                          "corroborated_by": eviction["via"],
                          "best_field_overlap": eviction["overlap"],
                          "coverage": round(coverage, 3)},
                group_key="MODULE_VERSION_AMBIGUOUS:%s" % mid,
            ))
            continue

        if not newer:
            findings.append(finding(
                "MODULE_UP_TO_DATE", "note",
                "Module already at the newest release",
                "%s@%s %s: %s, %d of %d step names match. No newer non-yanked "
                "release in the catalog."
                % (mid, installed, claim, via, len(present),
                   len(installed_names)),
                fix="Nothing to do." if proven else
                    "Nothing to do. To make this certain rather than inferred, "
                    "put the config.json this workflow was built from beside "
                    "it.",
                evidence={"module": mid, "installed": installed,
                          "coverage": round(coverage, 3),
                          "corroborated_by": eviction["via"],
                          "install_certainty": "proven" if proven else "inferred",
                          "best_field_overlap": eviction["overlap"],
                          "matched_steps": present[:MAX_TABLE_ROWS]},
                group_key="MODULE_UP_TO_DATE:%s" % mid,
            ))
            continue

        target = newer[-1]
        old = mod["versions"][installed]
        new = mod["versions"][target]
        old_names = [x["name"] for x in old["steps"] if x.get("name")]
        new_names = [x["name"] for x in new["steps"] if x.get("name")]
        added = sorted(set(new_names) - set(old_names))
        removed = sorted(set(old_names) - set(new_names))
        renames = detect_renames(old["steps"], new["steps"])
        renamed_old = set(o for o, _n, _s in renames)
        added = [n for n in added if n not in set(x for _o, x, _s in renames)]
        removed = [n for n in removed if n not in renamed_old]
        also_dropped = [n for n in removed if n in facts.step_by_name]

        # --- the affected set, computed BEFORE any wording ------------------
        # Everything the workflow would lose a source for: a renamed step's
        # consumers, and a dropped step's consumers. Only downstream consumer
        # edges count -- per amendment A2 an inbound edge can carry
        # `from_step: null` (static/prompt/variable), which no rename breaks,
        # and the module regenerates its own steps' inputs on build.
        affected = []
        inbound = []
        for old_name, new_name, score in renames:
            if old_name not in facts.step_by_name:
                continue
            for e in facts.out_edges.get(old_name, []):
                affected.append({
                    "reason": "rename", "old_step": old_name,
                    "new_step": new_name, "rename_score": score,
                    "consumer": e.get("to_step"), "param": e.get("to_param"),
                    "output_attribute": e.get("output_attribute"),
                    "input_type": e.get("input_type"),
                })
            for e in facts.in_edges.get(old_name, []):
                if e.get("input_type") != "dependency" or not e.get("from_step"):
                    continue
                inbound.append({
                    "old_step": old_name, "new_step": new_name,
                    "producer": e.get("from_step"), "param": e.get("to_param"),
                    "output_attribute": e.get("output_attribute"),
                })
        for dropped in also_dropped:
            for e in facts.out_edges.get(dropped, []):
                affected.append({
                    "reason": "dropped", "old_step": dropped, "new_step": None,
                    "rename_score": None,
                    "consumer": e.get("to_step"), "param": e.get("to_param"),
                    "output_attribute": e.get("output_attribute"),
                    "input_type": e.get("input_type"),
                })

        mech = mechanism_for("upgrade", has_lock)
        severity, fix_text = upgrade_verdict(affected, also_dropped, mech)

        detail_bits = ["%s@%s -> @%s." % (mid, installed, target)]
        if affected:
            names = sorted({"%s.%s" % (a["consumer"], a["param"])
                            for a in affected})
            detail_bits.append("AFFECTS %d live edge%s: %s." % (
                len(affected), "" if len(affected) == 1 else "s",
                ", ".join(names[:MAX_TABLE_ROWS])))
        if also_dropped:
            detail_bits.append("DROPS step%s the workflow still has: %s."
                               % ("" if len(also_dropped) == 1 else "s",
                                  ", ".join(also_dropped[:MAX_TABLE_ROWS])))
        if renames:
            detail_bits.append("renames " + "; ".join(
                "`%s` -> `%s` (%.2f)" % (o, n, sc)
                for o, n, sc in renames[:MAX_TABLE_ROWS]) + ".")
        detail_bits.append("adds %d, removes %d, renames %d. %s %s; installed "
                           "version from %s."
                           % (len(added), len(removed), len(renames), mid,
                              claim, how))
        if coverage < 0.5:
            detail_bits.append("PARTIAL: only %d of %d of this module's step "
                               "names appear." % (len(present),
                                                  len(installed_names)))

        # A caret constraint never crosses a major (README: npm rules), so
        # `update` only reaches the new release when the major is unchanged.
        cfg = config_path_for(lock_path) if lock_path else (
            config_path or "<config.json>")
        if semver_key(target)[0] == semver_key(installed)[0]:
            command = ("python3 scripts/registry.py update %s --registry %s"
                       % (cfg, catalog["registry"]))
        else:
            command = ('edit %s: set "%s" version to "^%d", then '
                       "python3 scripts/registry.py build %s --registry %s"
                       % (cfg, mid, semver_key(target)[0], cfg, catalog["registry"]))

        findings.append(finding(
            "MODULE_UPGRADE_AVAILABLE", severity,
            "Newer module release available",
            " ".join(detail_bits),
            fix=fix_text,
            evidence={"module": mid, "installed": installed, "target": target,
                      "coverage": round(coverage, 3),
                      "corroborated_by": eviction["via"],
                      "best_field_overlap": eviction["overlap"],
                      "added": added[:MAX_TABLE_ROWS],
                      "removed": removed[:MAX_TABLE_ROWS],
                      "renames": renames[:MAX_TABLE_ROWS],
                      "affected_edges": affected[:MAX_TABLE_ROWS],
                      "inbound_rewire": inbound[:MAX_TABLE_ROWS]},
            group_key="MODULE_UPGRADE_AVAILABLE:%s" % mid,
        ))
        proposal = {
            "job": "upgrade",
            "module": mid,
            "installed_version": installed,
            "installed_version_source": how,
            "corroborated_by": eviction["via"],
            "install_certainty": "proven" if proven else "inferred",
            "best_field_overlap": eviction["overlap"],
            "step_name_coverage": round(coverage, 3),
            "target_version": target,
            "steps_in_workflow": present,
            "mechanism": mech,
            "delta": {"added": added, "removed": removed, "renames": [
                {"from": o, "to": n, "field_overlap": sc} for o, n, sc in renames
            ]},
            "loss": also_dropped,
            "affected_edges": affected,
            "inbound_rewire": inbound,
            "command": command,
            "fix": fix_text,
            "severity": severity,
        }
        assert_proposal_safe(proposal, facts)
        proposals.append(proposal)

    return findings, proposals, matched_steps


# ==========================================================================
# Job 2 -- adopt
# ==========================================================================
def adopt_jobs(catalog, facts, matched_steps, has_lock, lock_path=None):
    findings = []
    proposals = []
    best_scores = {}
    mech = mechanism_for("adopt", has_lock)

    for s in facts.steps:
        name = s.get("name")
        if not name or name in matched_steps:
            continue
        wf_tokens = facts.tokens.get(name) or set()
        wf_disp = facts.token_disp.get(name) or {}
        if len(wf_tokens) < ADOPT_MIN_FIELDS:
            best_scores[name] = 0.0
            continue
        cands = []
        for mid in sorted(catalog["modules"]):
            mod = catalog["modules"][mid]
            ver = mod.get("latest")
            rel = mod["versions"].get(ver) if ver else None
            if not rel:
                continue
            for ms in rel["steps"]:
                if ms.get("type") != s.get("type"):
                    continue  # step types must agree
                mtok = {}
                for f in ms["fields"]:
                    _, t = field_identity(f)
                    if t:
                        mtok[t] = f
                if len(mtok) < ADOPT_MIN_FIELDS:
                    continue
                score = jaccard(wf_tokens, set(mtok))
                if score <= 0:
                    continue
                cands.append((score, mid, ver, ms, mtok))
        best = max([c[0] for c in cands]) if cands else 0.0
        best_scores[name] = best
        cands = [c for c in cands if c[0] >= ADOPT_MIN_OVERLAP]
        if not cands:
            continue
        cands.sort(key=lambda c: (-c[0], c[1], c[3].get("name") or ""))

        for score, mid, ver, ms, mtok in cands[:3]:
            gain_toks = sorted(set(mtok) - wf_tokens)
            loss_toks = sorted(wf_tokens - set(mtok))
            gain = [mtok[t] for t in gain_toks]
            loss = [wf_disp[t] for t in loss_toks]
            live_loss = []
            for t in loss_toks:
                for e in consumers_touching(facts, name, t):
                    live_loss.append({
                        "field": wf_disp[t],
                        "consumer": e.get("to_step"),
                        "param": e.get("to_param"),
                        "output_attribute": e.get("output_attribute"),
                    })
            rewire = [{"consumer": c[0], "param": c[1], "output_attribute": c[2]}
                      for c in consumers_of(facts, name)]
            strong = score >= ADOPT_STRONG_OVERLAP
            severity = "warning" if live_loss else "note"
            port = step_port(catalog, mid, ver, ms.get("name"))

            # Loss first, and the named live consumer before that: the
            # printer truncates detail at 190 chars, and "which consumer
            # loses which field" is the whole decision. Counts come last.
            bits = ["`%s` -> %s@%s `%s` at %.2f%s." % (
                name, mid, ver, ms.get("name"), score,
                " (strong)" if strong else "")]
            if live_loss:
                named = sorted({"`%s` -> %s.%s" % (x["field"], x["consumer"], x["param"])
                                for x in live_loss})
                bits.append("LIVE LOSS: " + "; ".join(named[:MAX_TABLE_ROWS])
                            + (" (+%d more)" % (len(named) - MAX_TABLE_ROWS)
                               if len(named) > MAX_TABLE_ROWS else "") + ".")
            if loss:
                bits.append("loses " + ", ".join("`%s`" % f
                                                 for f in loss[:MAX_TABLE_ROWS])
                            + (" (+%d more)" % (len(loss) - MAX_TABLE_ROWS)
                               if len(loss) > MAX_TABLE_ROWS else "") + ".")
            bits.append("module adds %d field%s; %d downstream consumer%s to rewire."
                        % (len(gain), "" if len(gain) == 1 else "s",
                           len(rewire), "" if len(rewire) == 1 else "s"))
            detail = " ".join(bits)

            findings.append(finding(
                "MODULE_ADOPT_CANDIDATE", severity,
                "Step could be replaced by a registry module"
                + (" (strong match)" if strong else ""),
                detail,
                fix=("Adopt %s@%s via %s, but first re-source the live-loss fields "
                 "above or keep this step." % (mid, ver, mech)) if live_loss
                else "Adopt %s@%s via %s." % (mid, ver, mech),
                step=name,
                evidence={"module": mid, "version": ver, "module_step": ms.get("name"),
                          "port": port, "overlap": round(score, 3), "strong": strong,
                          "gain": gain[:MAX_TABLE_ROWS], "loss": loss[:MAX_TABLE_ROWS],
                          "live_loss": live_loss[:MAX_TABLE_ROWS]},
                group_key="MODULE_ADOPT_CANDIDATE:%s" % name,
            ))
            proposals.append({
                "job": "adopt",
                "step": name,
                "step_type": s.get("type"),
                "module": mid,
                "version": ver,
                "module_step": ms.get("name"),
                "port": port,
                "overlap": round(score, 3),
                "strong": strong,
                "mechanism": mech,
                "gain": gain,
                "loss": loss,
                "live_loss": live_loss,
                "rewire": rewire,
                "command": (
                    'add {"id": "%s", "version": "^%d"} to %s, then '
                    "python3 scripts/registry.py build %s --registry %s   "
                    "# then delete the workflow's `%s` step"
                    % (mid, semver_key(ver)[0], config_path_for(lock_path),
                       config_path_for(lock_path), catalog["registry"], name)
                ) if mech == "config-merge" else (
                    "splice %s@%s step `%s` in place of `%s` (no config.lock.json "
                    "beside the workflow)" % (mid, ver, ms.get("name"), name)
                ),
                "severity": severity,
            })

    return findings, proposals, best_scores


# ==========================================================================
# Job 3 -- extract candidates
# ==========================================================================
def acronym_flags(text):
    return sorted({a for a in _ACRONYM.findall(str(text)) if a not in GENERIC_ACRONYMS})


def extract_jobs(catalog, facts, matched_steps, best_scores, has_lock, lock_path=None):
    findings = []
    proposals = []
    spec_tokens = facts.specificity_tokens()
    mech = mechanism_for("extract", has_lock)
    wf_path = facts.workflow.get("path") or "<workflow.json>"

    for s in facts.steps:
        name = s.get("name")
        if not name or name in matched_steps:
            continue
        if best_scores.get(name, 0.0) >= ADOPT_MIN_OVERLAP:      # H1
            continue
        stype = s.get("type")
        if stype in NON_EXTRACTABLE_TYPES or s.get("is_manual") or s.get("is_terminal"):
            continue                                             # H4
        fields = facts.fields.get(name) or []
        if len(fields) < MIN_EXTRACT_FIELDS:                     # H2
            continue

        # H5 -- carrier / customer specificity
        name_toks = {t.lower() for t in re.split(r"[^A-Za-z0-9]+", name) if t}
        field_toks = set()
        for f in fields:
            for part in re.split(r"[^A-Za-z0-9]+", f):
                if part:
                    field_toks.add(part.lower())
        specific = sorted((name_toks | field_toks) & spec_tokens)
        acronyms = acronym_flags(name)
        if specific or acronyms:
            continue

        # H3 -- input boundary
        deps = [e for e in facts.in_edges.get(name, [])
                if e.get("input_type") == "dependency" and e.get("from_step")]
        upstream = sorted({e["from_step"] for e in deps})
        if len(upstream) > MAX_EXTRACT_INPUTS:
            continue
        if any(e.get("resolved") is False for e in deps):
            continue

        # --input port=module.port[=STEP] mappings, worked out from the edges
        inputs = []
        seen_ports = {}
        unresolved = []
        for e in deps:
            port = _snake(e.get("to_param") or "input")
            if seen_ports.get(port) not in (None, e["from_step"]):
                port = "%s_%s" % (port, _snake(e["from_step"]))
            seen_ports[port] = e["from_step"]
            mid, mport = provider_for_step(catalog, e["from_step"])
            if mid is None and e["from_step"] == "Workflow Dispatcher":
                # The dispatcher is implicit (CONTRACT "Notes on producing it");
                # workflow_base is the platform-standard provider of documents.
                wb = catalog["modules"].get("workflow_base")
                if wb and wb.get("latest"):
                    wbrel = wb["versions"][wb["latest"]]
                    if port in wbrel["outputs"]:
                        mid, mport = "workflow_base", port
                    elif "documents" in wbrel["outputs"]:
                        mid, mport = "workflow_base", "documents"
            if mid is None:
                unresolved.append(e["from_step"])
                mid, mport = "TODO_provider_module", port
            inputs.append({
                "port": port, "source": "%s.%s" % (mid, mport),
                "external_step": e["from_step"],
                "resolved_provider": not mid.startswith("TODO_"),
            })

        # --output port=STEP, per MODULE_SCHEMA.md "outputs" resolution rules:
        #   rule 2 -- the step has a `data` payload, so the port name is a free
        #             alias; use a snake_case of the step name.
        #   rule 3 -- the step declares no properties at all; the alias is
        #             trusted and nothing is validated against the schema.
        #   rule 1 -- otherwise a port name MUST be a declared property, so
        #             emit one port per top-level property. An invented alias
        #             here would be a build error (rule 4).
        top = [k for k in ((s.get("output") or {}).get("properties") or {}).keys()
               if k not in OUTPUT_ENVELOPE_KEYS]
        if "data" in top or not top:
            out_ports = [_snake(name)]
            ports_capped = False
        else:
            out_ports = top[:MAX_TABLE_ROWS]
            ports_capped = len(top) > MAX_TABLE_ROWS
        module_id = _snake(name)

        cmd = [
            "python3 scripts/registry.py extract",
            "--id %s" % module_id,
            '--steps "%s"' % name,
            '--description "TODO one sentence: what this module DOES."',
            "--workflow %s" % wf_path,
        ]
        for i in inputs:
            cmd.append('--input %s=%s="%s"' % (i["port"], i["source"], i["external_step"]))
        for op in out_ports:
            cmd.append('--output %s="%s"' % (op, name))
        command = " \\\n    ".join(cmd)
        command += ("\n# then: python3 scripts/registry.py add "
                    "modules/%s/1.0.0/module.json && "
                    "python3 scripts/registry.py validate" % module_id)
        if mech == "config-merge":
            command += ('\n# then: add {"id": "%s", "version": "^1"} to %s and '
                        "rebuild, so the module owns the step"
                        % (module_id, config_path_for(lock_path)))

        detail = (
            "`%s` (%s) matches nothing in the catalog (best overlap %.2f, floor %.2f) "
            "and passes the reusability heuristics: %d fields, %d upstream "
            "dependenc%s, no workflow-specific tokens in its name or fields."
            % (name, stype, best_scores.get(name, 0.0), ADOPT_MIN_OVERLAP,
               len(fields), len(upstream), "y" if len(upstream) == 1 else "ies")
        )
        if unresolved:
            detail += (" Provider unresolved for %s -- the --input source is a TODO."
                       % ", ".join("`%s`" % u for u in sorted(set(unresolved))[:MAX_TABLE_ROWS]))

        findings.append(finding(
            "MODULE_EXTRACT_CANDIDATE", "note",
            "Step looks reusable and is not in the registry",
            detail,
            fix="Run the `registry.py extract` command in this pass's "
            "modules.json proposal, then `add` and `validate`.",
            step=name,
            evidence={"fields": len(fields), "upstream": upstream,
                      "inputs": inputs, "output_ports": out_ports,
                      "module_id": module_id,
                      "unresolved_providers": sorted(set(unresolved))},
            group_key="MODULE_EXTRACT_CANDIDATE:%s" % name,
        ))
        proposals.append({
            "job": "extract",
            "step": name,
            "step_type": stype,
            "module_id": module_id,
            "mechanism": mech,
            "best_overlap": round(best_scores.get(name, 0.0), 3),
            "field_count": len(fields),
            "inputs": inputs,
            "output_ports": out_ports,
            "output_ports_capped": ports_capped,
            "unresolved_providers": sorted(set(unresolved)),
            "loss": [],
            "command": command,
            "severity": "note",
        })

    return findings, proposals


# ==========================================================================
# output
# ==========================================================================
def summary_lines(catalog, proposals, cache_hit, cache_file, elapsed_ms):
    """The `extra_lines` PassOutput.print_summary() prints above the findings.

    Bounded by construction: two header lines, one counts line, and a
    common.table() capped at MAX_TABLE_ROWS rows.
    """
    c = catalog.get("counters") or {}
    lines = [
        "modules: %d modules / %d releases, %d step names (%d distinct), "
        "%d leaf fields indexed" % (
            c.get("modules", 0), c.get("releases", 0), c.get("step_names", 0),
            c.get("distinct_step_names", 0), c.get("fields", 0)),
        "catalog index: %s (%s, %.0f ms total)" % (
            cache_file, "cache hit" if cache_hit else "cold build", elapsed_ms),
    ]
    if not proposals:
        lines.append("proposals: none")
        return lines

    jobs = {}
    for p in proposals:
        jobs[p["job"]] = jobs.get(p["job"], 0) + 1
    lines.append("proposals: "
                 + ", ".join("%s=%d" % (k, jobs[k]) for k in sorted(jobs)))
    rows = []
    for p in proposals:
        if p["job"] == "upgrade":
            left = p["module"]
            target = "@%s -> @%s" % (p["installed_version"], p["target_version"])
            bits = []
            if p["affected_edges"]:
                bits.append("%d edge(s) affected" % len(p["affected_edges"]))
            if p["loss"]:
                bits.append("%d step(s) dropped" % len(p["loss"]))
            note = ", ".join(bits) or "clean"
        elif p["job"] == "adopt":
            left = p["step"]
            target = "%s@%s" % (p["module"], p["version"])
            note = "ovl %.2f, loses %d%s" % (
                p["overlap"], len(p["loss"]), ", LIVE" if p["live_loss"] else "")
        else:
            left = p["step"]
            target = "new module `%s`" % p["module_id"]
            note = "%d fields" % p["field_count"]
        rows.append([p["job"], left, target, p["mechanism"], note])
    lines.extend(table(rows, ["JOB", "STEP / MODULE", "TARGET", "MECHANISM", "NOTE"],
                       max_rows=MAX_TABLE_ROWS))
    return lines


def emit(out_dir, findings, proposals, catalog=None, cache_file=None,
         cache_hit=None):
    """Collect into a PassOutput and write <out_dir>/modules.json."""
    out = PassOutput(PASS_NAME, out_dir)
    out.add_all(findings)
    out.set("proposals", proposals)
    out.set("catalog", {
        "registry": (catalog or {}).get("registry"),
        "index_sha256": (catalog or {}).get("index_sha256"),
        "cache_file": cache_file,
        "cache_hit": cache_hit,
        "counters": (catalog or {}).get("counters") or {},
    })
    out.write()
    return out


# ==========================================================================
# main
# ==========================================================================
def run(facts_path, out_dir, registry, force_rebuild=False):
    t0 = time.time()
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    if not os.path.isdir(registry) or not os.path.isfile(
            os.path.join(registry, "index.json")):
        f = finding(
            "REGISTRY_NOT_FOUND", "note", "Module registry not found",
            "No module registry at `%s` (expected an index.json there), so no "
            "upgrade, adopt or extract proposals were computed." % registry,
            fix="Clone the registry to ~/fai/module-registry or pass --registry.",
            evidence={"registry": registry},
        )
        # Single note, no proposals, no catalog work. The SUMMARY line stays:
        # CONTRACT "Output discipline" makes it the last line of every pass.
        out = emit(out_dir, [f], [])
        return out.print_summary()

    facts = Facts(load_facts(facts_path), facts_path)

    cache_file = cache_path_for(registry, out_dir)
    catalog, cache_hit, cache_file = load_catalog(registry, cache_file, force_rebuild)

    lock_path = workflow_lock_path(facts)
    config_path = workflow_config_path(facts)
    locked = locked_versions(lock_path)
    has_lock = lock_path is not None

    up_f, up_p, matched = upgrade_jobs(catalog, facts, locked, has_lock,
                                       lock_path, config_path)
    ad_f, ad_p, best = adopt_jobs(catalog, facts, matched, has_lock, lock_path)
    ex_f, ex_p = extract_jobs(catalog, facts, matched, best, has_lock, lock_path)

    findings = up_f + ad_f + ex_f
    proposals = up_p + ad_p + ex_p
    if has_lock:
        findings.append(finding(
            "WORKFLOW_HAS_CONFIG_LOCK", "note",
            "config.lock.json found beside the workflow",
            "`%s` pins %d module release%s, so every proposal in this pass uses "
            "the config-merge mechanism." % (lock_path, len(locked),
                                             "" if len(locked) == 1 else "s"),
            fix="Edit config.json and rebuild; never hand-edit module-owned steps.",
            evidence={"lock": lock_path, "locked": locked},
        ))

    out = emit(out_dir, findings, proposals, catalog, cache_file, cache_hit)
    elapsed = (time.time() - t0) * 1000.0
    return out.print_summary(summary_lines(catalog, proposals, cache_hit,
                                           cache_file, elapsed))


# ==========================================================================
# self-test
# ==========================================================================
FIXTURE = {
    "schema_version": 1,
    "generated_by": "registry_match.py --self-test (hand-written fixture)",
    "workflow": {
        "path": "/Users/andrewjeffers/Documents/Work/contractors/workflows/cna_dua_audit_lpl.json",
        "name": "CNA DUA Audit - LPL",
        "bytes": 680895,
        "step_count": 7,
        "options": {},
        "control_plane_mappings": {},
    },
    "steps": [
        # --- Job 1: exact name match, module has a newer release ------------
        {
            "index": 0, "name": "Prepare Documents", "type": "prepare_documents",
            "is_terminal": False, "is_manual": False,
            "output": {"kind": "object", "properties": {
                "documents": {"kind": "array", "items": {"kind": "file"}}}},
        },
        {
            "index": 1, "name": "Classify Documents", "type": "classify_documents",
            "is_terminal": False, "is_manual": False,
            "output": {"kind": "object", "properties": {
                "documents": {"kind": "object", "properties": {},
                              "dynamic_keys": ["Application", "CurrentlyInForce",
                                               "LossRun", "Other", "Schedule",
                                               "SubmissionEmail", "Supplemental",
                                               "SupportingDocument"],
                              "open": False}}},
        },
        # --- Job 2: field overlap with account_information_extraction --------
        # 12 field names shared with that module's `Extract Insured data` step,
        # 2 of its own -> Jaccard 12/22 = 0.545, inside the adopt band.
        # `metadata` / `user_documents` are the platform envelope and must not
        # count on either side (../../reference/output_types.md).
        {
            "index": 2, "name": "Extract Insured Details", "type": "agentic_extraction",
            "is_terminal": False, "is_manual": False,
            "output": {"kind": "object", "properties": {
                "metadata": {"kind": "object", "properties": {
                    "source_type": {"kind": "string"}}},
                "user_documents": {"kind": "array", "items": {"kind": "file"}},
                "data": {"kind": "object", "properties": dict(
                    [(k, {"kind": "cell", "value_type": {"kind": "string"}})
                     for k in ["insured_name", "fein", "dba_names",
                               "insured_address_city", "insured_address_state",
                               "insured_address_zip", "insured_address_street_1",
                               "effective_date", "expiration_date",
                               "operations_description", "years_in_business",
                               "target_premium",
                               "prior_carrier_name", "sic_code"]]
                )}}},
        },
        # --- Job 3: reusable, matches nothing --------------------------------
        {
            "index": 3, "name": "Compute Premium Trend", "type": "custom_step",
            "is_terminal": False, "is_manual": False,
            "output": {"kind": "object", "properties": {
                "prior_term_written": {"kind": "number"},
                "current_term_written": {"kind": "number"},
                "trend_pct": {"kind": "number"},
                "trend_driver": {"kind": "string"},
                "trend_confidence": {"kind": "number"}}},
        },
        # --- Job 3 rejections ------------------------------------------------
        {   # H5: carries a workflow-name token (`lpl`)
            "index": 4, "name": "LPL Bridge Table", "type": "custom_step",
            "is_terminal": False, "is_manual": False,
            "output": {"kind": "object", "properties": {
                "alpha": {"kind": "string"}, "beta": {"kind": "string"},
                "gamma": {"kind": "string"}, "delta": {"kind": "string"}}},
        },
        {   # H2: too few fields
            "index": 5, "name": "Tiny Passthrough", "type": "custom_step",
            "is_terminal": False, "is_manual": False,
            "output": {"kind": "object", "properties": {
                "one": {"kind": "string"}, "two": {"kind": "string"}}},
        },
        {   # H4: terminal presentation step
            "index": 6, "name": "Audit Summary Email", "type": "email",
            "is_terminal": True, "is_manual": False,
            "output": {"kind": "object", "properties": {
                "subject": {"kind": "string"}, "body": {"kind": "string"},
                "recipients": {"kind": "array", "items": {"kind": "string"}},
                "sent_at": {"kind": "string"}, "message_id": {"kind": "string"}}},
        },
    ],
    "code_steps": {},
    "edges": [
        {"to_step": "Classify Documents", "to_param": "documents",
         "input_type": "dependency", "from_step": "Prepare Documents",
         "output_attribute": "documents", "via": "input_mappings",
         "resolved": True, "resolved_type": "array<file>"},
        # live consumer of the renamed Classify Documents step
        {"to_step": "Extract Insured Details", "to_param": "documents",
         "input_type": "dependency", "from_step": "Classify Documents",
         "output_attribute": "documents.Application", "via": "input_mappings",
         "resolved": True, "resolved_type": "array<file>"},
        # live consumer of a field the adopt candidate would lose
        {"to_step": "Compute Premium Trend", "to_param": "prior_carrier",
         "input_type": "dependency", "from_step": "Extract Insured Details",
         "output_attribute": "data.prior_carrier_name.value",
         "via": "input_mappings", "resolved": True, "resolved_type": "string"},
        {"to_step": "Compute Premium Trend", "to_param": "documents",
         "input_type": "dependency", "from_step": "Prepare Documents",
         "output_attribute": "documents", "via": "input_mappings",
         "resolved": True, "resolved_type": "array<file>"},
        {"to_step": "Audit Summary Email", "to_param": "trend",
         "input_type": "dependency", "from_step": "Compute Premium Trend",
         "output_attribute": "trend_pct", "via": "input_mappings",
         "resolved": True, "resolved_type": "number"},
    ],
    "fields": [],
    "decisions": [],
    "manual_steps": [],
    "graph": {"adjacency": {}, "reverse": {}, "roots": ["Prepare Documents"],
              "terminals": ["Audit Summary Email"], "cycles": []},
    "html_producers": [],
    "counters": {"steps": 7, "edges": 5},
}


def walker_drift(facts):
    """Assert common.comparable_fields still obeys the four B2 rules.

    The walk itself now lives in common.py and is shared with redundancy.py,
    which is the point of B2 -- but that also means a change there silently
    moves this pass's scores and thresholds. So this checks the RULES rather
    than the implementation: each assertion below maps to one clause of B2 and
    holds whatever way the walk is written.

      1. no invented fields -- every emitted path round-trips through
         resolve_path, and its normalized form is a prefix of some
         schema_leaf_paths entry. Both halves run on comparable_raw_paths,
         which keeps the leading `data.` and the `.0` segments; the display
         identities from comparable_fields strip those and are deliberately
         non-resolvable, so checking them against resolve_path would fail on
         every field. common.py added the raw variant for exactly this guard.
      2. cells count once -- no emitted identity is a member OF AN ACTUAL
         cell node, the specific `<path>.value` regression that would make
         module and workflow field sets stop lining up. Checked against the
         exact member paths of the cells in this schema, not against the
         member names: `citations` is a legitimate authored array field on a
         custom_step object, and the ads workflow has one.
      3. envelope stripped -- no emitted identity starts at a root envelope key;
      4. dynamic keys count as fields -- every classify class is emitted, the
         regression that would collapse a classify step to one field.
    """
    problems = []
    for step in facts.steps:
        schema = step.get("output") or {}
        if not schema:
            continue
        name = step.get("name")
        leaf_tokens = set()
        for path in schema_leaf_paths(schema):
            token = field_identity(path)[1]
            if token:
                leaf_tokens.add(token)
        dynamic = set()
        cell_members = set()

        def collect(node, prefix, depth=0):
            if not isinstance(node, dict) or depth > MAX_SCHEMA_DEPTH:
                return
            for key in node.get("dynamic_keys") or []:
                dynamic.add(field_identity(prefix + [str(key)])[1])
            if node.get("kind") == "cell":
                for member in CELL_MEMBERS:
                    cell_members.add(field_identity(prefix + [member])[1])
                return
            for key, child in (node.get("properties") or {}).items():
                collect(child, prefix + [key], depth + 1)
            items = node.get("items")
            if isinstance(items, dict):
                collect(items, prefix + ["0"], depth + 1)

        collect(schema, [])
        emitted = set()
        for raw in comparable_raw_paths(schema):
            got = resolve_path(schema, raw)
            if not got.get("ok"):
                problems.append("%s: `%s` does not resolve (%s)"
                                % (name, raw, got.get("error")))
                continue
            display, token = field_identity(raw)
            if not token:
                # `data` alone (a step declaring an empty payload object), or a
                # path of only numeric segments: no field identity survives
                # normalization, so comparable_fields correctly drops it. Real
                # in the schema, worth nothing to a comparison.
                continue
            emitted.add(token)
            segments = token.split(".")
            # rule 2 -- never a member of a real cell node in THIS schema
            if token in cell_members:
                problems.append("%s: `%s` descends into a cell" % (name, raw))
            # rule 3 -- the root envelope must be gone
            if segments[0] in _ENVELOPE_TOKENS:
                problems.append("%s: `%s` is root envelope, should be stripped"
                                % (name, raw))
            # rule 1 -- and it must be a real path in the schema
            if token in dynamic:
                continue
            if not any(leaf == token or leaf.startswith(token + ".")
                       for leaf in leaf_tokens):
                problems.append("%s: `%s` is not a prefix of any schema leaf"
                                % (name, raw))
        # rule 4 -- every dynamic key survives as a field
        for token in sorted(dynamic - emitted):
            problems.append("%s: dynamic key `%s` was dropped" % (name, token))
        # and the two walks must describe the same field set: comparable_fields
        # is the raw list normalized, minus the identities that normalize away.
        display_tokens = set()
        for item in comparable_fields(schema):
            token = field_identity(item)[1]
            if token:
                display_tokens.add(token)
        if display_tokens != emitted:
            only_display = sorted(display_tokens - emitted)[:3]
            only_raw = sorted(emitted - display_tokens)[:3]
            problems.append(
                "%s: comparable_fields and comparable_raw_paths disagree "
                "(display-only %s, raw-only %s)" % (name, only_display, only_raw))
    return problems


def _check(ok, label, detail=""):
    print("  %s %s%s" % ("PASS" if ok else "FAIL", label,
                         ("  -- " + detail) if detail else ""))
    return bool(ok)


def self_test(out_dir, registry):
    print("registry_match.py --self-test")
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    ok = True

    # ---- 1. normalization + Jaccard on known input ----------------------
    print("normalization / scoring:")
    ok &= _check(field_identity("data.Named_Insured") == ("Named_Insured", "namedinsured"),
                 "norm data.Named_Insured -> namedinsured",
                 str(field_identity("data.Named_Insured")))
    ok &= _check(field_identity("data.0.VIN") == ("VIN", "vin"),
                 "norm data.0.VIN -> vin", str(field_identity("data.0.VIN")))
    ok &= _check(field_identity("documents.ACORD_125.0")
                 == ("documents.ACORD_125", "documents.acord125"),
                 "norm documents.ACORD_125.0 -> documents.acord125",
                 str(field_identity("documents.ACORD_125.0")))
    ok &= _check(field_identity(["data", "limits", "each_occurrence"])
                 == ("limits.each_occurrence", "limits.eachoccurrence"),
                 "norm keeps path structure")
    ok &= _check(abs(jaccard({"a", "b", "c"}, {"b", "c", "d"}) - 0.5) < 1e-9,
                 "jaccard({a,b,c},{b,c,d}) == 0.50",
                 "%.4f" % jaccard({"a", "b", "c"}, {"b", "c", "d"}))
    ok &= _check(jaccard({"a"}, {"a"}) == 1.0, "jaccard identical == 1.0")
    ok &= _check(jaccard(set(), {"a"}) == 0.0, "jaccard empty == 0.0")
    ok &= _check(abs(jaccard(set("abcdefghijkl"), set("abcdefghi")) - 0.75) < 1e-9,
                 "jaccard 9/12 == 0.75 (strong threshold boundary)")
    # leaf walkers agree on the same logical schema
    mod_leaves = module_leaf_fields({"properties": {"data": {"properties": {
        "vin": {"type": "cell", "properties": {"value": {"type": "string"},
                                               "citations": {"type": "array"}}}}}}})
    fac_leaves = comparable_fields({"kind": "object", "properties": {"data": {
        "kind": "object", "properties": {
            "vin": {"kind": "cell", "value_type": {"kind": "string"}}}}}})
    ok &= _check(mod_leaves == fac_leaves == ["vin"],
                 "module and facts leaf walkers agree on a cell",
                 "%s vs %s" % (mod_leaves, fac_leaves))
    mod_file = module_leaf_fields({"properties": {"documents": {
        "type": "array", "items": {"type": "file", "properties": {
            "filename": {"type": "string"}, "user_document_id": {"type": "string"}}}}}})
    fac_file = comparable_fields({"kind": "object", "properties": {"documents": {
        "kind": "array", "items": {"kind": "file"}}}})
    ok &= _check(mod_file == fac_file == ["documents"],
                 "both walkers stop at a file object",
                 "%s vs %s" % (mod_file, fac_file))
    env_mod = module_leaf_fields({"properties": {
        "data": {"properties": {"vin": {"type": "cell"}}},
        "metadata": {"properties": {"source_type": {"type": "string"}}},
        "user_documents": {"type": "array", "items": {"type": "file"}},
        "schema": {"type": "object"},
        "api_errors": {"type": "array", "items": {"type": "string"}}}})
    env_fac = comparable_fields({"kind": "object", "properties": {
        "data": {"kind": "object", "properties": {"vin": {"kind": "cell"}}},
        "metadata": {"kind": "object", "properties": {"source_type": {"kind": "string"}}},
        "user_documents": {"kind": "array", "items": {"kind": "file"}},
        "schema": {"kind": "object"},
        "api_errors": {"kind": "array", "items": {"kind": "string"}}}})
    ok &= _check(env_mod == env_fac == ["vin", "api_errors"],
                 "both walkers drop the root output envelope, keep real siblings",
                 "%s vs %s" % (env_mod, env_fac))

    if not os.path.isdir(registry):
        print("registry %s missing -- catalog checks skipped" % registry)
        return 0 if ok else 1

    # ---- 2. cold build vs warm read -------------------------------------
    print("catalog index cache (%s):" % registry)
    cache_file = cache_path_for(registry, out_dir)
    t = time.time()
    cold, hit, cache_file = load_catalog(registry, cache_file, force=True)
    cold_s = time.time() - t
    ok &= _check(not hit, "forced rebuild is a cold build")
    c = cold["counters"]
    ok &= _check(c["modules"] > 0 and c["step_names"] > 0 and c["fields"] > 0,
                 "catalog is non-empty",
                 "%d modules / %d releases / %d step names (%d distinct) / %d fields"
                 % (c["modules"], c["releases"], c["step_names"],
                    c["distinct_step_names"], c["fields"]))
    ok &= _check(os.path.isfile(cache_file), "cache written", cache_file)
    csize = os.path.getsize(cache_file)
    print("       cold build %.3fs, read %.1f KB of artifacts, cache %.1f KB"
          % (cold_s, c["artifact_bytes"] / 1024.0, csize / 1024.0))

    t = time.time()
    warm, hit, _ = load_catalog(registry, cache_file, force=False)
    warm_s = time.time() - t
    ok &= _check(hit, "second load is a cache hit")
    ok &= _check(
        json.dumps(cold, sort_keys=True) == json.dumps(warm, sort_keys=True),
        "cache round-trips byte-identically")
    print("       warm load %.4fs (%.0fx faster than cold)"
          % (warm_s, (cold_s / warm_s) if warm_s > 0 else 0))

    # ---- 3. invalidation -------------------------------------------------
    tamper = os.path.join(out_dir, "tampered_index_cache.json")
    bad = dict(warm)
    bad["index_sha256"] = "0" * 64
    with open(tamper, "w", encoding="utf-8") as fh:
        json.dump(bad, fh, separators=(",", ":"), sort_keys=True)
    _, hit2, _ = load_catalog(registry, tamper, force=False)
    ok &= _check(not hit2, "stale index_sha256 invalidates the cache")
    with open(tamper, "r", encoding="utf-8") as fh:
        healed = json.load(fh)
    ok &= _check(healed["index_sha256"] == warm["index_sha256"],
                 "invalidated cache is rewritten with the current index digest")
    bad2 = dict(warm)
    bad2["cache_version"] = CACHE_VERSION - 1
    with open(tamper, "w", encoding="utf-8") as fh:
        json.dump(bad2, fh, separators=(",", ":"), sort_keys=True)
    _, hit3, _ = load_catalog(registry, tamper, force=False)
    ok &= _check(not hit3, "stale cache_version invalidates the cache")
    os.remove(tamper)

    # ---- 4. read-only promise -------------------------------------------
    idx = os.path.join(registry, "index.json")
    sig_before = (os.path.getmtime(idx), sha256_file(idx))
    sample = None
    for m in cold["modules"].values():
        for rel in m["versions"].values():
            sample = os.path.join(registry, rel["artifact"])
            break
        if sample:
            break
    art_before = (os.path.getmtime(sample), os.path.getsize(sample))

    # ---- 5. end-to-end over the fixture ---------------------------------
    print("end-to-end over the hand-written fixture:")
    fixture_path = os.path.join(out_dir, "selftest_facts.json")
    with open(fixture_path, "w", encoding="utf-8") as fh:
        json.dump(FIXTURE, fh, indent=1)
    facts = Facts(FIXTURE, fixture_path)
    lock_path = workflow_lock_path(facts)
    locked = locked_versions(lock_path)
    has_lock = lock_path is not None
    up_f, up_p, matched = upgrade_jobs(cold, facts, locked, has_lock, lock_path,
                                       workflow_config_path(facts))
    ad_f, ad_p, best = adopt_jobs(cold, facts, matched, has_lock, lock_path)
    ex_f, ex_p = extract_jobs(cold, facts, matched, best, has_lock, lock_path)
    findings = up_f + ad_f + ex_f
    proposals = up_p + ad_p + ex_p

    ok &= _check("Prepare Documents" in matched and "Classify Documents" in matched,
                 "exact catalog-name matches found", ", ".join(sorted(matched)))
    codes = dict((f["code"], f) for f in findings)
    wb = [f for f in findings if f["code"] == "MODULE_MATCH_UNCONFIRMED"
          and (f["evidence"] or {}).get("module") == "workflow_base"]
    ok &= _check(len(wb) == 1 and not any(p.get("module") == "workflow_base"
                                          for p in up_p),
                 "a 1-field conventional-name match is unconfirmed, not an "
                 "upgrade", str([p.get("module") for p in up_p]))
    if wb:
        ok &= _check("Prepare Documents" in str(wb[0]["evidence"]["matched_steps"])
                     and wb[0]["evidence"]["corroborated"] is False,
                     "the unconfirmed note names the matched steps")
    pcf = [f for f in findings if f["code"] == "MODULE_MATCH_UNCONFIRMED"
           and (f["evidence"] or {}).get("module") == "pc_common_foundation"]
    ok &= _check(len(pcf) == 1 and pcf[0]["evidence"]["non_distinguishing"],
                 "non-distinguishing step names are named in the note",
                 str(pcf[0]["evidence"]["non_distinguishing"]) if pcf else "-")
    ok &= _check(len(cold["non_distinguishing_steps"]) >= 4
                 and "Prepare Documents" in cold["non_distinguishing_steps"],
                 "catalog derives the non-distinguishing name set",
                 str(cold["non_distinguishing_steps"]))
    cls = [p for p in up_p if p["module"] == "pc_classification"]
    ok &= _check(len(cls) == 1, "pc_classification upgrade proposed (corroborated "
                 "by an 8-class field match)", str([p["module"] for p in up_p]))
    if cls:
        p = cls[0]
        renames = {(r["from"], r["to"]) for r in p["delta"]["renames"]}
        ok &= _check(("Classify Documents", "Classify Documents (Tier 1)") in renames,
                     "rename detected 1.0.0 -> 2.0.0", str(sorted(renames)))
        ok &= _check(any(b["consumer"] == "Extract Insured Details"
                         for b in p["affected_edges"]),
                     "rename affects the live consumer edge",
                     str(p["affected_edges"][:2]))
        ok &= _check(p["severity"] == "warning", "breaking upgrade is a warning")
        ok &= _check(p["mechanism"] == "config-merge", "upgrade uses config-merge")
    ad = [p for p in ad_p if p["step"] == "Extract Insured Details"]
    ok &= _check(len(ad) >= 1, "adopt candidate found for Extract Insured Details",
                 str([(p["step"], p["module"], p["overlap"]) for p in ad_p]))
    if ad:
        p = ad[0]
        ok &= _check(p["module"] == "account_information_extraction"
                     and p["module_step"] == "Extract Insured data",
                     "adopt names the right module step",
                     "%s@%s %s" % (p["module"], p["version"], p["module_step"]))
        ok &= _check(ADOPT_MIN_OVERLAP <= p["overlap"] < ADOPT_STRONG_OVERLAP
                     and not p["strong"], "overlap in the non-strong band",
                     "%.3f" % p["overlap"])
        ok &= _check("prior_carrier_name" in p["loss"] and "sic_code" in p["loss"],
                     "loss list carries the fields the module lacks", str(p["loss"]))
        ok &= _check(any(x["field"] == "prior_carrier_name"
                         and x["consumer"] == "Compute Premium Trend"
                         for x in p["live_loss"]),
                     "live loss names the consumer", str(p["live_loss"]))
        ok &= _check(p["severity"] == "warning", "live loss is a warning")
        ok &= _check("fein" not in p["loss"] and "insured_name" not in p["loss"],
                     "shared fields are not in the loss list")
    ex_steps = {p["step"] for p in ex_p}
    ok &= _check("Compute Premium Trend" in ex_steps, "extract candidate found",
                 str(sorted(ex_steps)))
    ok &= _check("LPL Bridge Table" not in ex_steps,
                 "H5 rejects a workflow-name-specific step")
    ok &= _check("Tiny Passthrough" not in ex_steps, "H2 rejects a 2-field step")
    ok &= _check("Audit Summary Email" not in ex_steps,
                 "H4 rejects a terminal email step")
    ok &= _check("Extract Insured Details" not in ex_steps,
                 "H1 rejects a step that has an adopt candidate")
    # Tiny sets score 1.00 on nothing: every dashboard step in the catalog
    # outputs only `email_body`, so a 1-field step must not adopt-match.
    tiny = Facts({"schema_version": 1, "workflow": {"name": "T", "path": "/tmp/t.json"},
                  "steps": [{"index": 0, "name": "Build Summary", "type": "custom_step",
                             "output": {"kind": "object", "properties": {
                                 "email_body": {"kind": "string"}}}}],
                  "edges": []}, "tiny")
    _tf, tp, _tb = adopt_jobs(cold, tiny, set(), False, None)
    ok &= _check(tp == [], "a 1-field step raises no adopt candidate",
                 str([(x["module"], x["overlap"]) for x in tp]))
    ext = [p for p in ex_p if p["step"] == "Compute Premium Trend"]
    if ext:
        p = ext[0]
        srcs = {i["port"]: i["source"] for i in p["inputs"]}
        ok &= _check(srcs.get("documents") == "workflow_base.documents",
                     "extract resolves a module provider from the edge list", str(srcs))
        ok &= _check(any(i["source"].startswith("TODO_") for i in p["inputs"]),
                     "extract marks an unresolvable provider as TODO", str(srcs))
        ok &= _check("registry.py extract" in p["command"]
                     and '--steps "Compute Premium Trend"' in p["command"]
                     and "--input documents=workflow_base.documents" in p["command"],
                     "extract command is ready to run")
        ok &= _check(p["mechanism"] == "direct-splice",
                     "extract without a lock uses direct-splice", p["mechanism"])
    ok &= _check(all(f["severity"] != "blocker" for f in findings),
                 "no blockers from this pass")
    # ---- the hard invariant, exercised directly -------------------------
    guard = {"job": "upgrade", "module": "m", "installed_version": "1.0.0",
             "target_version": "2.0.0", "loss": ["Prepare Documents"],
             "affected_edges": [],
             "delta": {"added": [], "removed": ["Prepare Documents"],
                       "renames": []},
             "fix": "Upgrade via config-merge; no live edge is affected."}
    raised = False
    try:
        assert_proposal_safe(guard, facts)
    except AssertionError:
        raised = True
    ok &= _check(raised, "invariant rejects 'no live edge' while dropping a step "
                         "that has consumers")
    sev, txt = upgrade_verdict(
        [{"consumer": "C", "param": "p"}], ["Prepare Documents"], "config-merge")
    ok &= _check(sev == "warning" and "no live edge is affected" not in txt,
                 "upgrade_verdict never reassures when the affected set is "
                 "non-empty", txt[:80])
    sev2, txt2 = upgrade_verdict([], ["Prepare Documents"], "config-merge")
    ok &= _check(sev2 == "warning" and "no live edge is affected" not in txt2,
                 "a drop with no consumers is still a warning, not a "
                 "reassurance", txt2[:80])
    sev3, txt3 = upgrade_verdict([], [], "config-merge")
    ok &= _check(sev3 == "note" and "no live edge is affected" in txt3,
                 "the reassuring branch is reachable only when nothing is "
                 "affected")

    ok &= _check(all("pass" not in f for f in findings)
                 and all(set(["code", "severity", "step", "title", "detail",
                              "fix", "evidence"]) <= set(f) for f in findings),
                 "findings match common.finding()'s shape (no `pass` key yet)")

    out = emit(out_dir, findings, proposals, cold, cache_file, False)
    out_file = os.path.join(out_dir, "%s.json" % PASS_NAME)
    ok &= _check(os.path.isfile(out_file), "modules.json written", out_file)
    with open(out_file, "r", encoding="utf-8") as fh:
        written = json.load(fh)
    ok &= _check(written.get("pass") == PASS_NAME
                 and isinstance(written.get("proposals"), list)
                 and all(f.get("pass") == PASS_NAME for f in written["findings"]),
                 "PassOutput stamped the pass name on the file and its findings")
    ok &= _check(out.counts()["blockers"] == 0,
                 "PassOutput counts no blockers", str(out.counts()))

    # ---- 6. warm end-to-end timing --------------------------------------
    t = time.time()
    rc = run(fixture_path, out_dir, registry, force_rebuild=False)
    warm_run_s = time.time() - t
    ok &= _check(rc == 0, "warm run exits 0")
    ok &= _check(warm_run_s < 0.2, "warm run under 0.2s", "%.4fs" % warm_run_s)

    # ---- 7. the real corpus, through the real facts producer -------------
    # CONTRACT "Test corpus": the self-test runs against the corpus. Facts come
    # from wf_facts.py in a subprocess -- it is the only sanctioned reader of
    # workflow.json, and this is exactly how the orchestrator drives the pass.
    corpus_dir = "/Users/andrewjeffers/Documents/Work/contractors/workflows"
    producer = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wf_facts.py")
    here = os.path.dirname(os.path.abspath(__file__))
    sources = [(n, os.path.join(corpus_dir, "%s.json" % n)) for n in
               ("cna_dua_audit_ads", "cna_dua_audit_lpl", "submission_intake",
                "quantum_cny")]
    # CONTRACT "Test corpus" also names the validator-clean reference example,
    # which lives with the skill rather than in the corpus directory. It is the
    # workflow carrying the reference `Compose HTML Summary` step.
    sources.append(("submission_intake_es_umbrella", os.path.join(
        here, "..", "..", "reference", "examples",
        "submission_intake_es_umbrella.json")))
    available = [(n, p) for n, p in sources if os.path.isfile(p)]
    if not (available and os.path.isfile(producer)):
        print("corpus or wf_facts.py unavailable -- corpus checks skipped")
    else:
        print("real corpus via wf_facts.py:")
        for name, source in available:
            work = os.path.join(out_dir, "corpus", name)
            if not os.path.isdir(work):
                os.makedirs(work)
            facts_file = os.path.join(work, "facts.json")
            if not os.path.isfile(facts_file):
                proc = subprocess.run(
                    [sys.executable, producer, source, "--out", work],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
                if proc.returncode != 0 or not os.path.isfile(facts_file):
                    ok &= _check(False, "%s: wf_facts.py produced facts" % name,
                                 proc.stderr.decode("utf-8", "replace")[:200])
                    continue
            started = time.time()
            rc = run(facts_file, work, registry, force_rebuild=False)
            took = time.time() - started
            with open(os.path.join(work, "%s.json" % PASS_NAME), "r",
                      encoding="utf-8") as fh:
                res = json.load(fh)
            props = res["proposals"]
            counts = {"upgrade": 0, "adopt": 0, "extract": 0}
            for x in props:
                counts[x["job"]] = counts.get(x["job"], 0) + 1
            bad = []
            for x in props:
                if not (x.get("job") and x.get("mechanism") and x.get("command")
                        and x.get("severity") and "loss" in x):
                    bad.append("incomplete proposal: %s" % x.get("job"))
                if x["job"] == "upgrade":
                    if x["mechanism"] != "config-merge":
                        bad.append("upgrade not config-merge")
                    for b in x["affected_edges"]:
                        # The bug real facts caught: amendment A2 edges carry
                        # from_step: null, and those are not broken consumers.
                        if not b.get("consumer"):
                            bad.append("broken edge with no consumer")
                if x["job"] == "adopt" and x["overlap"] < ADOPT_MIN_OVERLAP:
                    bad.append("adopt below the floor")
                if x["job"] == "extract" and x["field_count"] < MIN_EXTRACT_FIELDS:
                    bad.append("extract below the field floor")
            # The regression that matters most: no proposal may reassure while
            # dropping or renaming a step that still has live consumers.
            wf_facts_obj = Facts(load_facts(facts_file), facts_file)
            drift = walker_drift(wf_facts_obj)
            ok &= _check(not drift,
                         "%s: field walker still agrees with common's schema "
                         "language" % name, "; ".join(drift[:3]))
            for x in props:
                try:
                    assert_proposal_safe(x, wf_facts_obj)
                except AssertionError as exc:
                    bad.append(str(exc)[:120])
                touched = list(x.get("loss") or [])
                touched += [r["from"] for r in
                            (x.get("delta") or {}).get("renames", [])]
                for t in touched:
                    if wf_facts_obj.out_edges.get(t) and not x.get("affected_edges"):
                        bad.append("drops `%s` (has consumers) with an empty "
                                   "affected set" % t)
            if name == "cna_dua_audit_lpl":
                # The false positive this rule exists for: pc_common_foundation
                # matched only on `Prepare Documents` / `Initialize Knowledge
                # Base`, the platform-conventional names, with 0.07 field
                # overlap. Acting on it would delete document preparation and
                # the knowledge base from a live customer workflow.
                for x in props:
                    if x.get("module") in ("pc_common_foundation", "workflow_base"):
                        bad.append("uncorroborated %s upgrade proposed"
                                   % x["module"])
            ok &= _check(rc == 0 and not res["counts"]["blockers"] and not bad,
                         "%s: %d proposals (u%d/a%d/x%d), %d findings, %.3fs"
                         % (name, len(props), counts["upgrade"], counts["adopt"],
                            counts["extract"], len(res["findings"]), took),
                         "; ".join(sorted(set(bad)))[:200])
            ok &= _check(took < 0.2, "%s: warm pass under 0.2s" % name,
                         "%.4fs on a %.1f KB facts file"
                         % (took, os.path.getsize(facts_file) / 1024.0))

    sig_after = (os.path.getmtime(idx), sha256_file(idx))
    art_after = (os.path.getmtime(sample), os.path.getsize(sample))
    ok &= _check(sig_before == sig_after, "index.json untouched")
    ok &= _check(art_before == art_after, "module artifact untouched", sample)

    print("SELFTEST %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="verify pass 4 -- module upgrade / adopt / extract candidates")
    add_common_args(ap)
    ap.add_argument("--registry", default=DEFAULT_REGISTRY,
                    help="module registry root (default: %s)" % DEFAULT_REGISTRY)
    ap.add_argument("--rebuild-index", action="store_true",
                    help="ignore the catalog index cache and rebuild it")
    args = ap.parse_args(argv)

    require_args(args, "facts", "out")
    registry = os.path.abspath(os.path.expanduser(args.registry))
    out_dir = os.path.abspath(os.path.expanduser(args.out))

    try:
        if args.self_test:
            return self_test(out_dir, registry)
        # common.load_facts raises SystemExit(2) on a missing or bad facts file.
        facts_path = os.path.abspath(os.path.expanduser(args.facts))
        return run(facts_path, out_dir, registry, args.rebuild_index)
    except Exception as exc:  # noqa: BLE001 - CONTRACT: internal error is exit 2
        sys.stderr.write("registry_match: %s: %s\n" % (type(exc).__name__, exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
