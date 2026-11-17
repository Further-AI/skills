#!/usr/bin/env python3
"""Crop and annotate captured screenshots. Coordinates are image pixels.

Take coordinates from the capture manifest (`--from-rects`), not from eyeballing
a rendered PNG — capture.py records every named element's box at capture time.

    # box the Add New button using the recorded rect, then crop to the header strip
    annotate.py annotate shots/workflow_runs.png --out fig/add_new.png \\
        --from-rects shots/manifest.json --box-rect add_new --pad 10 \\
        --arrow 2420,205,2950,155 --crop 2200,55,1060,200

    # the two-part citation figure: the selected cell above its source below
    annotate.py stack --out fig/citation.png \\
        --top shots/citation.png:200,1436,1500,84 \\
        --bottom shots/citation.png:1890,120,1150,230 --box-bottom

Annotations use sinopia #b53b18 — the platform's own error/attention colour.
Keep them rare: at most one per screenshot, and on a minority of screenshots.

Exit codes: 0 ok, 1 render failed, 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Optional

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover
    print(
        "error: annotate.py needs Pillow: python3 -m pip install Pillow",
        file=sys.stderr,
    )
    raise SystemExit(1)

ACCENT = (181, 59, 24, 255)        # sinopia #b53b18
ACCENT_WASH = (181, 59, 24, 38)
HAIRLINE = (205, 203, 194, 255)

BOX_WIDTH = 6
BOX_RADIUS = 12
ARROW_WIDTH = 7
ARROW_HEAD = 34


def die(msg: str, code: int = 2) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


def nums(text: str, count: int, label: str) -> list[float]:
    try:
        vals = [float(v) for v in text.replace(" ", "").split(",")]
    except ValueError:
        die(f"{label} takes comma-separated numbers, got {text!r}")
    if len(vals) != count:
        die(f"{label} needs {count} numbers, got {len(vals)}")
    return vals


def load_rects(path: str) -> dict:
    data = json.loads(Path(path).read_text())
    if "screens" in data:  # a capture manifest: merge every screen's rects
        merged: dict = {}
        for screen in data["screens"]:
            merged.update({k: v for k, v in (screen.get("rects") or {}).items()
                           if not k.startswith("_")})
        return merged
    return {k: v for k, v in data.items() if not k.startswith("_")}


def draw_box(d: ImageDraw.ImageDraw, x, y, w, h, wash: bool = False) -> None:
    if wash:
        d.rounded_rectangle([x, y, x + w, y + h], radius=BOX_RADIUS, fill=ACCENT_WASH)
    d.rounded_rectangle([x, y, x + w, y + h], radius=BOX_RADIUS, outline=ACCENT, width=BOX_WIDTH)


def draw_arrow(d: ImageDraw.ImageDraw, x1, y1, x2, y2) -> None:
    d.line([x1, y1, x2, y2], fill=ACCENT, width=ARROW_WIDTH)
    angle = math.atan2(y2 - y1, x2 - x1)
    for spread in (0.45, -0.45):
        d.line([x2, y2,
                x2 - ARROW_HEAD * math.cos(angle + spread),
                y2 - ARROW_HEAD * math.sin(angle + spread)], fill=ACCENT, width=ARROW_WIDTH)


def do_crop(im: Image.Image, box: tuple) -> Image.Image:
    """Crop, clamped to the image. A crop aimed off-image is an error, not a black frame."""
    x, y, w, h = (int(v) for v in box)
    x2, y2 = x + w, y + h
    cx, cy = max(0, x), max(0, y)
    cx2, cy2 = min(im.width, x2), min(im.height, y2)
    if cx2 <= cx or cy2 <= cy:
        die(f"crop {x},{y},{w},{h} lies outside the {im.width}x{im.height} image — the "
            f"capture used a different window size. Use --crop-rect with a recorded "
            f"element instead of fixed numbers.", 2)
    if (cx, cy, cx2, cy2) != (x, y, x2, y2):
        print(f"note   crop clamped to {cx},{cy},{cx2 - cx},{cy2 - cy} "
              f"(image is {im.width}x{im.height})", file=sys.stderr)
    return im.crop((cx, cy, cx2, cy2))


def finish(im: Image.Image, out: str, maxw: int) -> None:
    if maxw and im.width > maxw:
        im = im.resize((maxw, round(im.height * maxw / im.width)), Image.LANCZOS)
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.convert("RGB").save(path, quality=95)
    print(f"image  {path}  {im.width}x{im.height}")
    if im.width > 1900:
        print("note   wider than ~1900px: at a 5in column this renders under ~4pt. "
              "Crop to the panel, not the window.", file=sys.stderr)


def cmd_annotate(args: argparse.Namespace) -> int:
    im = Image.open(args.src).convert("RGBA")
    overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    rects = load_rects(args.from_rects) if args.from_rects else {}
    for name in args.box_rect or []:
        if name not in rects:
            die(f"no rect named {name!r} in {args.from_rects} "
                f"(have: {', '.join(sorted(rects)) or 'none'})")
        x, y, w, h = rects[name]
        draw_box(d, x - args.pad, y - args.pad, w + 2 * args.pad, h + 2 * args.pad,
                 wash=args.wash)
    for spec in args.box or []:
        x, y, w, h = nums(spec, 4, "--box")
        draw_box(d, x, y, w, h, wash=args.wash)
    for spec in args.arrow or []:
        x1, y1, x2, y2 = nums(spec, 4, "--arrow")
        draw_arrow(d, x1, y1, x2, y2)

    im = Image.alpha_composite(im, overlay)

    crop = None
    if args.crop_rect:
        if args.crop_rect not in rects:
            die(f"no rect named {args.crop_rect!r} in {args.from_rects} "
                f"(have: {', '.join(sorted(rects)) or 'none'})")
        x, y, w, h = rects[args.crop_rect]
        crop = (x + args.inset, y + args.inset, w - 2 * args.inset, h - 2 * args.inset)
    elif args.crop:
        crop = tuple(nums(args.crop, 4, "--crop"))
    if crop:
        im = do_crop(im, crop)

    finish(im, args.out, args.maxw)
    return 0


def _part(spec: str) -> Image.Image:
    """'path.png:x,y,w,h' -> cropped image. The crop is optional."""
    if ":" in spec and not spec.rsplit(":", 1)[1].endswith(".png"):
        path, box = spec.rsplit(":", 1)
        x, y, w, h = nums(box, 4, "region")
        return Image.open(path).convert("RGB").crop((int(x), int(y), int(x + w), int(y + h)))
    return Image.open(spec).convert("RGB")


def cmd_stack(args: argparse.Namespace) -> int:
    top, bottom = _part(args.top), _part(args.bottom)
    pad, gap = 28, 96
    width = max(top.width, bottom.width) + pad * 2
    height = pad + top.height + gap + bottom.height + pad
    canvas = Image.new("RGB", (width, height), (255, 255, 255))
    d = ImageDraw.Draw(canvas)

    tx = (width - top.width) // 2
    canvas.paste(top, (tx, pad))
    d.rectangle([tx - 1, pad - 1, tx + top.width, pad + top.height], outline=HAIRLINE, width=2)

    bx = (width - bottom.width) // 2
    by = pad + top.height + gap
    canvas.paste(bottom, (bx, by))
    if args.box_bottom:
        d.rounded_rectangle([bx - 4, by - 4, bx + bottom.width + 3, by + bottom.height + 3],
                            radius=8, outline=ACCENT, width=BOX_WIDTH)
    else:
        d.rectangle([bx - 1, by - 1, bx + bottom.width, by + bottom.height],
                    outline=HAIRLINE, width=2)

    mid = width // 2
    draw_arrow(d, mid, pad + top.height + 14, mid, by - 16)
    finish(canvas, args.out, args.maxw)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("annotate", help="box / arrow / crop one screenshot")
    p.add_argument("src")
    p.add_argument("--out", required=True)
    p.add_argument("--from-rects", help="manifest.json or rects.json from capture.py")
    p.add_argument("--box-rect", action="append", help="name of a recorded rect to box")
    p.add_argument("--box", action="append", metavar="X,Y,W,H")
    p.add_argument("--arrow", action="append", metavar="X1,Y1,X2,Y2")
    p.add_argument("--crop", metavar="X,Y,W,H", help="applied after annotation")
    p.add_argument("--crop-rect", metavar="NAME",
                   help="crop to a recorded element (e.g. 'content' to drop the sidebar) — "
                        "survives a different window size, unlike fixed numbers")
    p.add_argument("--inset", type=int, default=0, help="shrink --crop-rect by N px per side")
    p.add_argument("--pad", type=int, default=8, help="padding around a --box-rect")
    p.add_argument("--wash", action="store_true", help="tint the box interior")
    p.add_argument("--maxw", type=int, default=1500, help="downscale to this width (0 = never)")
    p.set_defaults(func=cmd_annotate)

    p = sub.add_parser("stack", help="two crops, one above the other, joined by an arrow")
    p.add_argument("--top", required=True, metavar="PNG[:X,Y,W,H]")
    p.add_argument("--bottom", required=True, metavar="PNG[:X,Y,W,H]")
    p.add_argument("--out", required=True)
    p.add_argument("--box-bottom", action="store_true", help="box the lower region")
    p.add_argument("--maxw", type=int, default=1500)
    p.set_defaults(func=cmd_stack)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as e:
        die(f"{e.filename}: no such file", 1)
    except OSError as e:
        die(str(e), 1)


if __name__ == "__main__":
    raise SystemExit(main())
