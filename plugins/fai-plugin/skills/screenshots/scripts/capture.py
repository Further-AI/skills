#!/usr/bin/env python3
"""Capture screenshots of the FurtherAI app for documents and decks.

Only reach for this when an artifact needs *pixels*. Every fact about a
workflow, a submission, or a step output is faster and more reliable through
the API — see the "use the API instead" table in SKILL.md.

    capture.py session --account andrew-sandbox
    capture.py screens
    capture.py shoot --screen workflow_runs --account andrew-sandbox \\
        --workflow "Novacore Submission Intake" --out shots/
    capture.py pick-run --account andrew-sandbox --workflow "Novacore Submission Intake"

Generic escape hatches (`goto`, `click`, `shot`, `rects`) drive screens the
catalog does not cover yet. Screens that create or delete anything on the
platform require --allow-write.

Exit codes: 0 ok, 1 capture failed (anchor missing, no session), 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from fai_accounts import default_env, resolve_org_id, resolve_workflow_id  # noqa: E402
from fai_browser import (Browser, BrowserError, DEFAULT_PROFILE,  # noqa: E402
                         HEADLESS_PROFILE)
from fai_client import FaiClient, FaiError  # noqa: E402

ENVIRONMENTS = ("prod", "staging", "local")
DEFAULT_WINDOW = (1640, 1100)
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


# --------------------------------------------------------------------------
# context
# --------------------------------------------------------------------------

class Ctx:
    """Resolved ids + a browser, built lazily so read-only commands stay cheap."""

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.env = args.env or default_env()
        self._client: Optional[FaiClient] = None
        self._browser: Optional[Browser] = None
        self.org_id = args.org_id
        if not self.org_id and getattr(args, "account", None):
            self.org_id = resolve_org_id(args.account, self.env)
        self.workflow_id = getattr(args, "workflow", None)
        if self.workflow_id and not _UUID_RE.match(self.workflow_id):
            if not getattr(args, "account", None):
                die(f"{self.workflow_id!r} is a name, not a UUID — pass --account so it "
                    f"can be resolved", 2)
            self.workflow_id = resolve_workflow_id(args.account, self.workflow_id, self.env)
        self.execution_id = getattr(args, "execution", None)

    @property
    def client(self) -> FaiClient:
        if self._client is None:
            self._client = FaiClient(env=self.env, org_id=self.org_id, skill_name="screenshots")
        return self._client

    @property
    def app_url(self) -> str:
        return self.client.app_url

    @property
    def browser(self) -> Browser:
        if self._browser is None:
            b = self._resolve_browser()
            w, h = self.args.window
            b.window_size(w, h)
            b.park()          # one tab, never raised
            if self.org_id:
                b.set_active_org(self.org_id)   # a fresh login lands in the wrong workspace
            self._browser = b
        return self._browser

    def _resolve_browser(self) -> Browser:
        """Headless if a magic-link session is live there; else the parked window."""
        head = Browser(port=9223, profile=HEADLESS_PROFILE,
                       app_url=self.app_url, headless=True)
        if head.session_available():
            return head
        parked = Browser(port=self.args.port or 9222,
                         profile=self.args.profile or DEFAULT_PROFILE,
                         app_url=self.app_url, headless=False)
        if parked.session_available():
            return parked
        die("no signed-in browser. Either `capture.py login --email you@furtherai.com` "
            "(headless, no window at all) or `capture.py session` (parked window).", 1)


def add_common(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--env", choices=ENVIRONMENTS, help="default: accounts.json defaults.env")
    ap.add_argument("--account", help="account slug/name/domain from ~/.fai/accounts.json")
    ap.add_argument("--org-id", help="explicit org UUID (wins over --account)")
    ap.add_argument("--port", type=int, help="devtools port (default 9222 headed / 9223 headless)")
    ap.add_argument("--profile", help="Chrome profile dir (default ~/.fai/browser-profile[-headless])")
    ap.add_argument("--window", nargs=2, type=int, default=list(DEFAULT_WINDOW),
                    metavar=("W", "H"), help="viewport size, default 1640 1100")
    # No --headless: a headless browser cannot sign in, and copying a session
    # into one logs the parked window out. See SKILL.md.


# --------------------------------------------------------------------------
# screen catalog
# --------------------------------------------------------------------------

class Screen:
    def __init__(self, name: str, summary: str, anchor: str, run: Callable[[Ctx, Browser], dict],
                 needs: tuple[str, ...] = (), writes: bool = False,
                 rect_texts: Optional[dict] = None, rect_selectors: Optional[dict] = None,
                 verified: bool = True):
        self.name = name
        self.summary = summary
        self.anchor = anchor
        self.run = run
        self.needs = needs
        self.writes = writes
        self.rect_texts = rect_texts or {}
        self.rect_selectors = rect_selectors or {}
        self.verified = verified


def _workflow_url(ctx: Ctx) -> str:
    return f"{ctx.app_url}/workflows/{ctx.workflow_id}"


def _execution_url(ctx: Ctx) -> str:
    return f"{ctx.app_url}/workflow-execution/{ctx.execution_id}"


def _s_workflow_runs(ctx: Ctx, b: Browser) -> dict:
    b.goto(_workflow_url(ctx), wait_text="Add New")
    return {}


def _s_filters(ctx: Ctx, b: Browser) -> dict:
    b.goto(_workflow_url(ctx), wait_text="Add filter")
    b.click_text("Add filter", settle=2.5)
    b.wait_text("Status", timeout=15)
    return {}


def _s_new_submission(ctx: Ctx, b: Browser) -> dict:
    b.goto(_workflow_url(ctx), wait_text="Add New")
    b.click_text("Add New", exact=False, settle=4.0)
    b.wait_text("Drag & Drop", timeout=45)
    draft = b.url().rstrip("/").split("/")[-1].split("?")[0]
    return {"draft_execution_id": draft}


def _s_staged_files(ctx: Ctx, b: Browser) -> dict:
    files = ctx.args.files or []
    if not files:
        die("--files is required for the staged_files screen", 2)
    out = {}
    if "Drag & Drop" not in b.text():
        out = _s_new_submission(ctx, b)
    b.set_files(files)
    b.wait_text("Confirm Details & Submit", timeout=60)
    out["staged"] = [Path(f).name for f in files]
    return out


def _s_submission_overview(ctx: Ctx, b: Browser) -> dict:
    b.goto(_execution_url(ctx), wait_text="Steps", settle=5.0)
    return {}


def _s_step_output(ctx: Ctx, b: Browser) -> dict:
    step = ctx.args.step
    if not step:
        die("--step is required for the step_output screen (use the UI's label, "
            "e.g. 'Extracted Insured Data')", 2)
    b.goto(_execution_url(ctx), wait_text="Steps", settle=5.0)
    b.click_text(step, settle=6.0)
    b.wait_text("Value", timeout=30)
    return {"step": step}


def _s_citation(ctx: Ctx, b: Browser) -> dict:
    value = ctx.args.value
    if not value:
        die("--value is required for the citation screen: the exact cell value to click", 2)
    _s_step_output(ctx, b)
    b.click_text(value, settle=6.0)
    b.wait_text("Share", timeout=30)  # the document viewer's toolbar
    return {"step": ctx.args.step, "value": value}


def _s_final_summary(ctx: Ctx, b: Browser) -> dict:
    b.goto(_execution_url(ctx), wait_text="Steps", settle=5.0)
    b.click_text("View Email", nth="last", settle=7.0)
    return {}


def _s_generated_file(ctx: Ctx, b: Browser) -> dict:
    _s_final_summary(ctx, b)
    needle = ctx.args.file or ".xlsx"
    b.click_text(needle, exact=False, nth="last", settle=7.0)
    b.wait_text("Share", timeout=30)
    return {"file": needle}


SCREENS: dict[str, Screen] = {
    "workflow_runs": Screen(
        "workflow_runs", "the workflow's Runs queue — every submission with status and columns",
        "Add New", _s_workflow_runs, needs=("workflow",),
        rect_texts={"add_new": "Add New", "runs_tab": "Runs"},
        rect_selectors={"content": "#main-content"}),
    "filters": Screen(
        "filters", "the Add filter menu open over the queue",
        "Status", _s_filters, needs=("workflow",),
        rect_texts={"add_filter": "Add filter"}),
    "new_submission": Screen(
        "new_submission", "the empty upload screen for a new submission",
        "Drag & Drop", _s_new_submission, needs=("workflow",), writes=True),
    "staged_files": Screen(
        "staged_files", "the upload screen with files attached, before submitting",
        "Confirm Details & Submit", _s_staged_files, needs=("workflow", "files"), writes=True,
        rect_texts={"submit": "Confirm Details & Submit", "add_more": "Add More Files"}),
    "submission_overview": Screen(
        "submission_overview", "one submission's step list",
        "Steps", _s_submission_overview, needs=("execution",),
        rect_selectors={"content": "#main-content"}),
    "step_output": Screen(
        "step_output", "one step's output table, with its source-document chips",
        "Value", _s_step_output, needs=("execution", "step"),
        rect_texts={"export": "Export", "edit": "Edit"}),
    "citation": Screen(
        "citation", "a value selected, with the source document open and the passage highlighted",
        "Share", _s_citation, needs=("execution", "step", "value")),
    "final_summary": Screen(
        "final_summary", "the rendered summary output of the last step",
        "Share", _s_final_summary, needs=("execution",)),
    "generated_file": Screen(
        "generated_file", "a generated workbook or PDF open in the in-app viewer",
        "Share", _s_generated_file, needs=("execution",)),
}


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_screens(args: argparse.Namespace) -> int:
    print("Screen catalog — `capture.py shoot --screen NAME`\n")
    width = max(len(n) for n in SCREENS)
    for name, s in SCREENS.items():
        flags = []
        if s.writes:
            flags.append("WRITES")
        if s.needs:
            flags.append("needs: " + ",".join(s.needs))
        print(f"  {name:<{width}}  {s.summary}")
        print(f"  {'':<{width}}  anchor {s.anchor!r}" + (f"  [{'; '.join(flags)}]" if flags else ""))
    print("\nScreens not in this list (cell editing, step re-run, delete, download menus) are")
    print("unverified — drive them with goto/click/shot and add a recipe once they hold up.")
    return 0


def _ink_ratio(path: Path) -> float:
    """Fraction of non-white pixels — a blank capture is a failed capture."""
    try:
        from PIL import Image
    except ImportError:
        return 1.0
    with Image.open(path) as im:
        small = im.convert("L").resize((80, 45))
        return sum(1 for v in small.getdata() if v < 250) / 3600


def cmd_session(args: argparse.Namespace) -> int:
    """Sign in once and park the window. Captures attach to it and never raise it."""
    ctx = Ctx.__new__(Ctx)
    ctx.args, ctx.env = args, args.env or default_env()
    ctx._client = ctx._browser = None
    ctx.org_id = args.org_id or (resolve_org_id(args.account, ctx.env) if args.account else None)
    ctx.workflow_id = ctx.execution_id = None

    b = Browser(port=args.port or 9222, profile=args.profile or DEFAULT_PROFILE,
                app_url=ctx.app_url, headless=False)
    b.ensure_session(on_prompt=lambda _s: print(
        "\n  A Chrome window is open. Sign in to FurtherAI in it — this is the only\n"
        "  time a window appears.\n", file=sys.stderr))
    print(f"signed in   {b.url()}")

    w, h = args.window
    b.window_size(w, h)
    b.park()                                    # one tab; never raised again

    probe = Path.home() / ".fai" / "session-probe.png"
    b.goto(f"{ctx.app_url}/home", settle=3.0, timeout=60)
    b.shot(probe)
    ink = _ink_ratio(probe)
    if ink < 0.02 or not b.signed_in():
        print(f"\nerror: could not verify a capture from the parked window "
              f"(ink {ink:.0%}, signed_in={b.signed_in()})", file=sys.stderr)
        return 1
    print(f"verified    captured the app from the unfocused window ({ink:.0%} ink)")
    print("\nLeave this window open and ignore it. Captures attach without raising it, so")
    print("it stays behind your work — move it to another Space if it is in the way.")
    print("`capture.py close` ends the session and costs a new sign-in.")
    return 0


def _ink_ratio(path: Path) -> float:
    """Fraction of non-white pixels — a blank capture is a failed capture."""
    try:
        from PIL import Image
    except ImportError:
        return 1.0
    with Image.open(path) as im:
        small = im.convert("L").resize((80, 45))
        return sum(1 for v in small.get_flattened_data() if v < 250) / 3600


def _headless(ctx: "Ctx") -> Browser:
    b = Browser(port=9223, profile=HEADLESS_PROFILE, app_url=ctx.app_url, headless=True)
    b.launch_or_attach()
    b.attach_page()
    return b


def cmd_login(args: argparse.Namespace) -> int:
    """Step 1 of the headless login: have the app email a sign-in link.

    The headless browser stays running so it can consume the link itself —
    a link opened in a different browser can be rejected, and a session
    copied between browsers logs the original out.
    """
    ctx = Ctx.__new__(Ctx)
    ctx.args, ctx.env = args, args.env or default_env()
    ctx._client = ctx._browser = None
    ctx.org_id = args.org_id or (resolve_org_id(args.account, ctx.env) if args.account else None)
    ctx.workflow_id = ctx.execution_id = None

    b = _headless(ctx)
    if b.signed_in():
        print("already signed in headless — nothing to do")
        return 0
    info = b.request_magic_link(args.email)
    print(f"requested   magic link for {args.email}")
    print(f"field       {info['field']}   button {info['button']!r}")
    print(f"page says   {info['page'][:120].strip()!r}")
    print("\nOpen the email, copy the sign-in link, then run:")
    print("  capture.py login-open --url '<paste the link>'")
    print("(the headless browser is still running and holds the request state)")
    return 0


def cmd_login_open(args: argparse.Namespace) -> int:
    """Step 2: consume the magic link inside the headless browser."""
    ctx = Ctx.__new__(Ctx)
    ctx.args, ctx.env = args, args.env or default_env()
    ctx._client = ctx._browser = None
    ctx.org_id = args.org_id or (resolve_org_id(args.account, ctx.env) if args.account else None)
    ctx.workflow_id = ctx.execution_id = None

    b = _headless(ctx)
    ok = b.open_magic_link(args.url)
    if not ok:
        print(f"error: link did not sign in — landed on {b.url()}", file=sys.stderr)
        print(f"page: {b.text(140)!r}", file=sys.stderr)
        return 1
    b.park()
    probe = Path.home() / ".fai" / "session-probe.png"
    b.goto(f"{ctx.app_url}/home", settle=3.0, timeout=60)
    b.shot(probe)
    ink = _ink_ratio(probe)
    print(f"signed in   {b.url()}")
    print(f"verified    headless capture ({ink:.0%} ink) -> {probe}")
    print("\nNo window exists. Captures attach to this headless browser.")
    return 0 if ink >= 0.02 else 1


def cmd_pick_run(args: argparse.Namespace) -> int:
    """Choose an exemplar submission to screenshot: newest completed, real title, most steps."""
    ctx = Ctx(args)
    if not ctx.workflow_id:
        die("--workflow is required", 2)
    try:
        data = ctx.client.get("/api/v1/workflow-execution/logs",
                              params={"workflow_id": ctx.workflow_id, "limit": args.scan})
    except FaiError as e:
        die(str(e))
    rows = data.get("workflow_executions", []) if isinstance(data, dict) else []
    good = [r for r in rows
            if r.get("status") == "completed"
            and (r.get("title") or "").strip()
            and (r.get("title") or "").strip().lower() != "not found"]
    if not good:
        die("no completed submission with a real title — screenshot a run that finished, "
            "or run one first")
    pick = good[0]
    exec_id = pick.get("_id") or pick.get("id")
    print(f"execution   {exec_id}")
    print(f"title       {pick.get('title')}")
    print(f"created     {pick.get('created_at')}")
    print(f"url         {ctx.client.execution_url(exec_id)}")
    print(f"candidates  {len(good)} completed of {len(rows)} scanned")
    return 0


def _write_manifest(out_dir: Path, entry: dict) -> None:
    path = out_dir / "manifest.json"
    manifest = {"screens": []}
    if path.exists():
        try:
            manifest = json.loads(path.read_text())
        except json.JSONDecodeError:
            pass
    manifest["screens"] = [s for s in manifest.get("screens", []) if s.get("name") != entry["name"]]
    manifest["screens"].append(entry)
    path.write_text(json.dumps(manifest, indent=2))


def cmd_shoot(args: argparse.Namespace) -> int:
    screen = SCREENS.get(args.screen)
    if not screen:
        die(f"unknown screen {args.screen!r} — run `capture.py screens`", 2)
    if screen.writes and not args.allow_write:
        die(f"screen {screen.name!r} creates a draft submission in the org. Re-run with "
            f"--allow-write, and clean it up afterwards with `capture.py delete-draft`.", 2)

    ctx = Ctx(args)
    if "workflow" in screen.needs and not ctx.workflow_id:
        die(f"screen {screen.name!r} needs --workflow", 2)
    if "execution" in screen.needs and not ctx.execution_id:
        die(f"screen {screen.name!r} needs --execution (find one with `capture.py pick-run`)", 2)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = args.name or screen.name
    b = ctx.browser
    try:
        extra = screen.run(ctx, b)
        rects = b.rects(selectors=screen.rect_selectors, texts=screen.rect_texts)
        image = b.shot(out_dir / f"{name}.png")
    except BrowserError as e:
        die(str(e))

    entry = {"name": name, "screen": screen.name, "image": image.name,
             "url": b.url(), "anchor": screen.anchor, "anchor_found": True,
             "viewport": b.viewport(), "rects": rects, **extra}
    _write_manifest(out_dir, entry)

    print(f"image     {image}")
    print(f"manifest  {out_dir / 'manifest.json'}")
    if rects:
        named = [k for k in rects if not k.startswith("_")]
        print(f"rects     {', '.join(named) if named else '(none matched)'}")
    if extra.get("draft_execution_id"):
        print(f"draft     {extra['draft_execution_id']}  <- delete this when the shots are done")
    return 0


def cmd_goto(args: argparse.Namespace) -> int:
    ctx = Ctx(args)
    b = ctx.browser
    try:
        b.goto(args.url, wait_text=args.wait_text)
    except BrowserError as e:
        die(str(e))
    print(f"url   {b.url()}")
    if args.out and args.name:
        print(f"image {b.shot(Path(args.out) / f'{args.name}.png')}")
    return 0


def cmd_click(args: argparse.Namespace) -> int:
    ctx = Ctx(args)
    b = ctx.browser
    try:
        n = b.click_text(args.text, exact=not args.contains, nth=args.nth)
        if args.wait_text:
            b.wait_text(args.wait_text)
    except BrowserError as e:
        die(str(e))
    print(f"clicked {args.text!r} ({n} candidates)")
    print(f"url     {b.url()}")
    if args.out and args.name:
        print(f"image   {b.shot(Path(args.out) / f'{args.name}.png')}")
    return 0


def cmd_shot(args: argparse.Namespace) -> int:
    ctx = Ctx(args)
    b = ctx.browser
    out_dir = Path(args.out)
    try:
        image = b.shot(out_dir / f"{args.name}.png")
        rects = b.rects(selectors=dict(s.split("=", 1) for s in args.selector or []),
                        texts=dict(t.split("=", 1) for t in args.text or []))
    except BrowserError as e:
        die(str(e))
    except ValueError:
        die("--selector/--text take name=query pairs", 2)
    _write_manifest(out_dir, {"name": args.name, "screen": "ad-hoc", "image": image.name,
                              "url": b.url(), "rects": rects, "viewport": b.viewport()})
    print(f"image     {image}")
    print(f"manifest  {out_dir / 'manifest.json'}")
    return 0


def cmd_rects(args: argparse.Namespace) -> int:
    ctx = Ctx(args)
    try:
        rects = ctx.browser.rects(
            selectors=dict(s.split("=", 1) for s in args.selector or []),
            texts=dict(t.split("=", 1) for t in args.text or []))
    except ValueError:
        die("--selector/--text take name=query pairs", 2)
    except BrowserError as e:
        die(str(e))
    print(json.dumps(rects, indent=2))
    return 0


def cmd_delete_draft(args: argparse.Namespace) -> int:
    ctx = Ctx(args)
    if not args.execution:
        die("--execution is required", 2)
    try:
        log = ctx.client.execution_log(args.execution)
    except FaiError as e:
        die(str(e))
    status = log.get("status")
    if status != "not_started" and not args.force:
        die(f"execution {args.execution} is {status!r}, not an unstarted draft. This command "
            f"exists to clean up capture scaffolding — pass --force only if you are certain.")
    try:
        ctx.client.delete(f"/api/v1/workflow-execution/logs/{args.execution}")
    except FaiError as e:
        die(str(e))
    print(f"deleted   {args.execution} (was {status})")
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    """Close both the visible login browser and any headless capture browser."""
    b = Browser(port=args.port or 9222, profile=args.profile or DEFAULT_PROFILE)
    b.quit()
    print("browser closed (SIGTERM). The next capture needs `capture.py session` again.")
    return 0


# --------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("screens", help="list the screen catalog")
    p.set_defaults(func=cmd_screens)

    p = sub.add_parser("session", help="open the browser and make sure a login exists")
    add_common(p)
    p.set_defaults(func=cmd_session)

    p = sub.add_parser("login", help="headless sign-in, step 1: email me a magic link")
    add_common(p)
    p.add_argument("--email", required=True)
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("login-open", help="headless sign-in, step 2: open the emailed link")
    add_common(p)
    p.add_argument("--url", required=True)
    p.set_defaults(func=cmd_login_open)

    p = sub.add_parser("pick-run", help="choose an exemplar completed submission to screenshot")
    add_common(p)
    p.add_argument("--workflow", required=True, help="workflow UUID or name (with --account)")
    p.add_argument("--scan", type=int, default=25, help="how many recent runs to consider")
    p.set_defaults(func=cmd_pick_run)

    p = sub.add_parser("shoot", help="capture one catalogued screen")
    add_common(p)
    p.add_argument("--screen", required=True)
    p.add_argument("--out", required=True, help="output directory")
    p.add_argument("--name", help="output basename (default: the screen name)")
    p.add_argument("--workflow")
    p.add_argument("--execution")
    p.add_argument("--step", help="step label for step_output/citation")
    p.add_argument("--value", help="exact cell value to click for citation")
    p.add_argument("--file", help="substring of the generated file name")
    p.add_argument("--files", nargs="+", help="local files to stage for staged_files")
    p.add_argument("--allow-write", action="store_true",
                   help="permit screens that create a draft submission")
    p.set_defaults(func=cmd_shoot)

    p = sub.add_parser("goto", help="navigate (escape hatch for uncatalogued screens)")
    add_common(p)
    p.add_argument("--url", required=True)
    p.add_argument("--wait-text")
    p.add_argument("--out")
    p.add_argument("--name")
    p.set_defaults(func=cmd_goto)

    p = sub.add_parser("click", help="click an element by its text")
    add_common(p)
    p.add_argument("--text", required=True)
    p.add_argument("--contains", action="store_true", help="substring instead of exact match")
    p.add_argument("--nth", default=0, help="index, or 'last'")
    p.add_argument("--wait-text")
    p.add_argument("--out")
    p.add_argument("--name")
    p.set_defaults(func=cmd_click)

    p = sub.add_parser("shot", help="capture whatever is on screen now")
    add_common(p)
    p.add_argument("--out", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--selector", action="append", help="name=cssSelector, repeatable")
    p.add_argument("--text", action="append", help="name=exactText, repeatable")
    p.set_defaults(func=cmd_shot)

    p = sub.add_parser("rects", help="print element boxes in image pixels")
    add_common(p)
    p.add_argument("--selector", action="append")
    p.add_argument("--text", action="append")
    p.set_defaults(func=cmd_rects)

    p = sub.add_parser("delete-draft", help="delete a draft submission created for a capture")
    add_common(p)
    p.add_argument("--execution", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_delete_draft)

    p = sub.add_parser("close", help="shut the browser(s) down gracefully")
    p.add_argument("--port", type=int)
    p.add_argument("--profile")
    p.set_defaults(func=cmd_close)

    args = ap.parse_args(argv)
    if hasattr(args, "nth") and isinstance(args.nth, str) and args.nth.isdigit():
        args.nth = int(args.nth)
    try:
        return args.func(args)
    except BrowserError as e:
        die(str(e))          # no session, missing anchor, dead browser
    except FaiError as e:
        die(f"API: {e}")
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
