#!/usr/bin/env python3
"""Fleet rollout for Workflow Builder: apply an edit to the SAME workflow across many orgs.

For each org it:
  1. downloads the org's OWN current published workflow JSON,
  2. optionally applies a transform (a python file exposing `transform(wf: dict) -> dict`),
  3. uploads the result as the active draft (validation deferred to publish),
  4. publishes a new version (normal publish first; `--allow-override` retries with
     override=True only when the draft-test gate blocks — override never bypasses
     publish validation).

Always transforms each org's own current version — never blasts one org's JSON
fleet-wide (per-org copies of a shared workflow_id can sit at different
versions/configs). Write a transform that HARD-ASSERTS its anchors and run
`--dry-run` first: it fetches and transforms everywhere but writes nothing, so
anchor drift surfaces before any org is touched.

Auth: requires the restricted skill service key (FAI_SKILL_{ENV}_PERSONAL_API_KEY
in ~/.fai/credentials.env). Personal API keys act only as their owner and cannot
reach orgs the owner is not a member of — which is the whole point of a fleet
run — so this script refuses to start without the restricted key. The restricted
token cannot DELETE workflows; deletion is out of scope here. No secrets in this
file, on the command line, or in the results output.

Orgs file: {"organizations": [{"organization_name": "...", "owner_oid": "<uuid>"}, ...]}

Example:
  python3 wb_fleet_publish.py \
    --env prod \
    --orgs-file orgs.json \
    --workflow-id <uuid> \
    --transform transform_lib.py \
    --change-summary "Remove review-notes pipeline" \
    --publish-name "No review notes in report" \
    --publish-notes "Deleted the review-notes steps" \
    --results-file fleet_results.json \
    --dry-run   # drop this flag to actually upload + publish

Keep the human in the loop at two points: approving the transform module before
the real run, and the publish itself (this writes to customer-facing prod
workflows across many orgs). Never go straight to a non-dry run.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from fai_client import FaiClient, FaiError  # noqa: E402

BUILDER = "/api/v1/workflow-builder/workflows"

PUBLISH_GATE_ERRORS = {"TEST_RUN_REQUIRED", "TEST_RUN_IN_PROGRESS", "TEST_RUN_FAILED"}


def _load_transform(path: str | None) -> Callable[[dict], dict]:
    if not path:
        return lambda wf: wf
    spec = importlib.util.spec_from_file_location("fleet_transform", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load transform module {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not callable(getattr(mod, "transform", None)):
        raise RuntimeError(f"{path} must expose transform(wf: dict) -> dict")
    return mod.transform


def _gate_error(e: FaiError) -> str | None:
    if e.status != 400:
        return None
    try:
        detail = json.loads(e.body).get("detail")
    except (json.JSONDecodeError, AttributeError):
        return None
    if isinstance(detail, dict) and detail.get("error") in PUBLISH_GATE_ERRORS:
        return detail["error"]
    return None


def _publish(client: FaiClient, workflow_id: str, args: argparse.Namespace,
             log: dict) -> None:
    body = {
        "published_name": args.publish_name,
        "publish_notes": args.publish_notes,
        "override": False,
    }
    try:
        payload = client.post(f"{BUILDER}/{workflow_id}/publish", body, timeout=120)
        log["publish_status"] = 200
        log["published_version"] = payload.get("published_version")
        return
    except FaiError as e:
        gate = _gate_error(e)
        if gate is None or not args.allow_override:
            log["publish_status"] = e.status
            log["publish_detail"] = (gate or e.body or str(e))[:200]
            return
        log["gate_error"] = gate
    # Gate hit and --allow-override given: override bypasses ONLY the
    # draft-test gate; publish validation still runs and cannot be bypassed.
    try:
        payload = client.post(f"{BUILDER}/{workflow_id}/publish",
                              {**body, "override": True}, timeout=120)
        log["used_override"] = True
        log["publish_status"] = 200
        log["published_version"] = payload.get("published_version")
    except FaiError as e:
        log["used_override"] = True
        log["publish_status"] = e.status
        log["publish_detail"] = (e.body or str(e))[:200]


def run_org(client: FaiClient, org: dict, args: argparse.Namespace,
            transform: Callable[[dict], dict]) -> dict[str, Any]:
    log: dict[str, Any] = {"org": org["organization_name"], "oid": org["owner_oid"]}
    client.org_id = org["owner_oid"]  # restricted tokens scope per request

    try:
        data = client.get(f"/api/v1/workflows/{args.workflow_id}", timeout=60)
    except FaiError as e:
        if e.status == 404:
            log["skipped"] = "workflow not present in org"
            return log
        raise
    wf = data.get("content", data)
    log["prev_published_version"] = data.get("published_version")
    if not wf.get("steps"):
        # Never-published workflow (published_version=0) returns shell content.
        # Proceeding would clobber the org's active draft with an empty one.
        log["skipped"] = ("no published content — workflow never published; this "
                          "script rolls out on top of published versions only")
        return log

    try:
        wf_new = transform(copy.deepcopy(wf))
    except AssertionError as e:
        log["transform_failed"] = str(e)[:200]  # anchor drift — org left untouched
        return log

    if args.dry_run:
        log["dry_run"] = "transform OK, no writes"
        return log

    try:
        draft = client.put(
            f"{BUILDER}/{args.workflow_id}",
            {
                # Identity fields stay the org's own; transforms edit steps/options.
                "name": wf["name"],
                "description": wf.get("comments") or wf.get("description", ""),
                "welcome_message": wf.get("welcome_message"),
                "steps": wf_new["steps"],
                "options": wf_new.get("options"),
                "change_summary": args.change_summary,
                # validation runs at publish; matches the UI JSON-import path
                "enable_validation": False,
            },
            timeout=180,
        )
    except FaiError as e:
        log["draft_error"] = f"{e.status}: {(e.body or str(e))[:200]}"
        return log
    log["draft_version"] = (draft.get("version") if draft.get("version") is not None
                            else draft.get("published_version"))
    log["workflow_url"] = client.workflow_url(args.workflow_id)

    _publish(client, args.workflow_id, args, log)
    return log


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env", choices=["prod", "staging"], required=True)
    p.add_argument("--orgs-file", required=True,
                   help='JSON: {"organizations": [{organization_name, owner_oid}]}')
    p.add_argument("--workflow-id", required=True,
                   help="workflow_id shared by the per-org copies")
    p.add_argument("--transform",
                   help="python file exposing transform(wf)->wf; omit to republish as-is")
    p.add_argument("--change-summary", default="fleet rollout")
    p.add_argument("--publish-name", default="fleet rollout")
    p.add_argument("--publish-notes", default="")
    p.add_argument("--allow-override", action="store_true",
                   help="retry publish with override=True if the draft-test gate "
                        "blocks (validation still runs)")
    p.add_argument("--dry-run", action="store_true",
                   help="fetch + transform everywhere, write nothing")
    p.add_argument("--results-file", help="write per-org results JSON here")
    args = p.parse_args()

    client = FaiClient(env=args.env, skill_name="wb-fleet")
    if not client.restricted:
        print(
            f"error: fleet-publish requires the restricted skill service key "
            f"(FAI_SKILL_{args.env.upper()}_PERSONAL_API_KEY in "
            f"~/.fai/credentials.env). A personal API key acts only as its "
            f"owner and cannot reach orgs the owner is not a member of, so it "
            f"cannot run a fleet rollout. Run the fai:setup skill to add the "
            f"restricted key (FDE/admin provisioning required).",
            file=sys.stderr,
        )
        return 1

    orgs = json.loads(Path(args.orgs_file).read_text())["organizations"]
    transform = _load_transform(args.transform)

    results = []
    for org in orgs:
        try:
            log = run_org(client, org, args, transform)
        except Exception as e:  # keep the fleet loop alive; org marked failed
            log = {"org": org["organization_name"], "oid": org["owner_oid"],
                   "error": str(e)[:300]}
        results.append(log)
        print(json.dumps(log), flush=True)

    if args.results_file:
        out = Path(args.results_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=2) + "\n")

    published = sum(1 for x in results if x.get("publish_status") == 200)
    eligible = sum(1 for x in results if "skipped" not in x)
    print(f"DONE: {published}/{eligible} published ({len(results) - eligible} skipped)")
    return 0 if (args.dry_run or published == eligible) else 1


if __name__ == "__main__":
    sys.exit(main())
