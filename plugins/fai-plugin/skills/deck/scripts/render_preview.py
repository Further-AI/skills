#!/usr/bin/env python3
"""Render a .pptx to PNGs so the build can actually be looked at.

    python3 render_preview.py deck.pptx [--out-dir DIR] [--width 1400]
                                        [--slides 1-4] [--keep-pdf]

Two engines, tried in order:

  1. LibreOffice  ``soffice --headless --convert-to pdf`` and then a PDF->PNG
     step (pdftoppm, then ImageMagick, then macOS sips).  This renders EVERY
     slide and honours the installed brand fonts.
  2. macOS Quick Look  ``qlmanage -t`` -- renders SLIDE 1 ONLY.

A partial render is a correctness trap: one image from a twelve-slide deck is
not a review of the deck.  So when fewer slides are rendered than the deck
holds, this script prints a banner before AND after the file list and **exits
3**.  Pass --allow-partial to accept a partial render and exit 0.

Exit codes: 0 every slide rendered (or --allow-partial), 2 bad input,
3 partial render.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

SOFFICE_CANDIDATES = [
    "soffice", "libreoffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
]


def _which(names):
    for name in names:
        found = shutil.which(name) if "/" not in name else (
            name if Path(name).exists() else None)
        if found:
            return found
    return None


def _run(cmd, timeout=300):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 1, "", str(exc))


def _parse_range(text, total=None):
    if not text:
        return None
    out = set()
    for part in str(text).split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return sorted(out)


def _pdf_to_png(pdf, out_dir, stem, width, pages):
    """Return (paths, engine_note)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm:
        cmd = [pdftoppm, "-png", "-r", str(int(width / 13.333)), str(pdf),
               str(out_dir / stem)]
        if pages:
            cmd[2:2] = ["-f", str(min(pages)), "-l", str(max(pages))]
        res = _run(cmd)
        if res.returncode == 0:
            return sorted(out_dir.glob("%s*.png" % stem)), "pdftoppm"
    magick = _which(["magick", "convert"])
    if magick:
        cmd = [magick, "-density", "150", str(pdf), "-resize", "%dx" % width,
               str(out_dir / ("%s-%%02d.png" % stem))]
        res = _run(cmd)
        if res.returncode == 0:
            return sorted(out_dir.glob("%s-*.png" % stem)), "imagemagick"
    sips = shutil.which("sips")
    if sips:
        target = out_dir / ("%s-01.png" % stem)
        res = _run([sips, "-s", "format", "png", "--resampleWidth", str(width),
                    str(pdf), "--out", str(target)])
        if res.returncode == 0 and target.exists():
            return [target], "sips (FIRST PAGE ONLY)"
    return [], None


def slide_count(pptx):
    try:
        from pptx import Presentation
        return len(Presentation(str(pptx)).slides)
    except Exception:                                             # noqa: BLE001
        return None


def render(pptx, out_dir=None, width=1400, pages=None, keep_pdf=False):
    pptx = Path(pptx).expanduser().resolve()
    if not pptx.exists():
        raise SystemExit("no such file: %s" % pptx)
    out_dir = Path(out_dir).expanduser() if out_dir else pptx.parent / "preview"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = pptx.stem

    soffice = _which(SOFFICE_CANDIDATES)
    if soffice:
        res = _run([soffice, "--headless", "--norestore", "--convert-to", "pdf",
                    "--outdir", str(out_dir), str(pptx)], timeout=600)
        pdf = out_dir / ("%s.pdf" % stem)
        if pdf.exists():
            paths, engine = _pdf_to_png(pdf, out_dir, stem, width, pages)
            if not keep_pdf and paths:
                pass  # keep the PDF; it is often the more useful artifact
            if paths:
                return paths, "libreoffice + %s" % engine, None
            return [pdf], "libreoffice (PDF only)", (
                "No PDF->PNG converter found (pdftoppm / ImageMagick / sips). "
                "The PDF above has every slide.")
        note = (res.stderr or res.stdout or "").strip()[:300]
        warn = "LibreOffice failed to convert (%s); falling back." % (note or "no output")
    else:
        warn = ("LibreOffice is not installed, so only slide 1 can be rendered. "
                "Install it for full-deck previews: brew install --cask libreoffice")

    qlmanage = shutil.which("qlmanage")
    if qlmanage:
        res = _run([qlmanage, "-t", "-s", str(width), "-o", str(out_dir), str(pptx)])
        produced = sorted(out_dir.glob("%s.pptx.png" % stem))
        if produced:
            return produced, "qlmanage (SLIDE 1 ONLY)", warn
    raise SystemExit("no renderer available. %s" % warn)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("pptx")
    ap.add_argument("--out-dir")
    ap.add_argument("--width", type=int, default=1400)
    ap.add_argument("--slides", help="page range, e.g. 1-4 or 1,3,7")
    ap.add_argument("--keep-pdf", action="store_true")
    ap.add_argument("--allow-partial", action="store_true",
                    help="exit 0 even when only some slides were rendered")
    args = ap.parse_args(argv)

    wanted = _parse_range(args.slides)
    paths, engine, warn = render(args.pptx, args.out_dir, args.width, wanted,
                                 args.keep_pdf)

    total = slide_count(args.pptx)
    expected = len(wanted) if wanted else total
    images = [p for p in paths if p.suffix.lower() == ".png"]
    partial = bool(expected and images and len(images) < expected)

    def banner():
        got, want = len(images), expected
        missing = "2-%d" % want if got == 1 and want and want > 1 else \
            "%d of %d" % (want - got, want)
        print("=" * 72)
        print("  PARTIAL RENDER -- %d of %s slides" % (got, want))
        print("  Engine: %s" % engine)
        print("  Slides %s were NOT rendered and have NOT been checked." % missing)
        print("  One image is not a review of the deck. Do NOT report this deck")
        print("  as looked at, previewed or verified on the strength of it.")
        print("  Fix: brew install --cask libreoffice   (renders every slide)")
        print("=" * 72)

    if partial:
        banner()
    elif warn:
        print("WARNING: %s" % warn)
    print("engine: %s" % engine)
    print("%d file(s):" % len(paths))
    for p in paths:
        print("  %s" % p)
    if partial:
        banner()
        return 0 if args.allow_partial else 3
    if expected:
        print("rendered all %d slide(s)." % expected)
    return 0


if __name__ == "__main__":
    sys.exit(main())
