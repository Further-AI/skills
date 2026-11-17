#!/usr/bin/env python3
"""Brand font resolver for the FurtherAI `deck` and `doc` skills.

The bundled PowerPoint/Word templates name three Fraunces families that do NOT
exist on disk under those names:

  * ``Fraunces Light`` -- no TTF carries it as name ID 1.
  * ``Fraunces 9pt``   -- that string is the *full name* (ID 4) of the variable
                          font's default instance, never a family (ID 1).
  * ``Fraunces``       -- resolves, but the variable font's default instance is
                          ``wght 900 / opsz 9``, so it renders BLACK, not Regular.

Generated artifacts should therefore use the ``FAMILY`` mapping below, which
names only families that a static TTF actually registers.  ``install --aliases``
additionally synthesises TTFs registered under the three template names (with
``Fraunces`` and ``Fraunces 9pt`` pinned to wght 400, not the 900 default) so
that pre-existing decks keep rendering.

CLI
    python3 fonts.py check   [--aliases] [--dir DIR] [--json]
    python3 fonts.py install [--aliases] [--dir DIR] [--force]

``check`` exits 1 when a required family does not resolve, so a build script can
gate on it.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Canonical family mapping (spec section G.4): intent -> family name to put in
# run.font.name.  These are the ONLY Fraunces names that resolve from the
# vendored static TTFs.
#
# NOTE: the templates also reference "Wix Madefor Text" (Example_Slides 17,
# Enterprise 34).  That family is NOT vendored in assets/brand/fonts -- always
# substitute "Wix Madefor Display".  Same for Poppins / Nunito / Quattrocento /
# Georgia, which appear in the source decks but ship with nothing.
# --------------------------------------------------------------------------
FAMILY = {
    "display_light": "Fraunces 72pt Light",       # covers, section titles
    "display": "Fraunces 72pt",                   # slide/card titles (regular)
    "display_semibold": "Fraunces 72pt SemiBold",
    "body": "Wix Madefor Display",                # bold via font.bold = True
    "body_medium": "Wix Madefor Display Medium",  # separate ID1 family
    "body_semibold": "Wix Madefor Display SemiBold",
    "body_extrabold": "Wix Madefor Display ExtraBold",
    "mono": "Courier New",                        # system font, not vendored
}

# Families that must resolve for a brand-correct render.  "Courier New" is a
# system font on macOS/Windows and is deliberately not gated on.
REQUIRED = [
    "Fraunces 72pt Light",
    "Fraunces 72pt",
    "Fraunces 72pt SemiBold",
    "Wix Madefor Display",
    "Wix Madefor Display Medium",
    "Wix Madefor Display SemiBold",
    "Wix Madefor Display ExtraBold",
]

# The three names the shipped templates use.  Only present after
# `install --aliases`.
ALIAS_FAMILIES = ["Fraunces Light", "Fraunces 9pt", "Fraunces"]

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
FONT_SRC = PLUGIN_ROOT / "assets" / "brand" / "fonts"
FRAUNCES_DIR = FONT_SRC / "Fraunces"
WIX_DIR = FONT_SRC / "Wix_Madefor_Display"

# Statics copied by `install`.
BUNDLED = [
    FRAUNCES_DIR / "static" / "Fraunces_72pt-Regular.ttf",
    FRAUNCES_DIR / "static" / "Fraunces_72pt-Bold.ttf",
    FRAUNCES_DIR / "static" / "Fraunces_72pt-Italic.ttf",
    FRAUNCES_DIR / "static" / "Fraunces_72pt-BoldItalic.ttf",
    FRAUNCES_DIR / "static" / "Fraunces_72pt-Light.ttf",
    FRAUNCES_DIR / "static" / "Fraunces_72pt-LightItalic.ttf",
    FRAUNCES_DIR / "static" / "Fraunces_72pt-SemiBold.ttf",
    WIX_DIR / "static" / "WixMadeforDisplay-Regular.ttf",
    WIX_DIR / "static" / "WixMadeforDisplay-Bold.ttf",
    WIX_DIR / "static" / "WixMadeforDisplay-Medium.ttf",
    WIX_DIR / "static" / "WixMadeforDisplay-SemiBold.ttf",
    WIX_DIR / "static" / "WixMadeforDisplay-ExtraBold.ttf",
]

VAR_ROMAN = FRAUNCES_DIR / "Fraunces-VariableFont_SOFT,WONK,opsz,wght.ttf"
VAR_ITALIC = FRAUNCES_DIR / "Fraunces-Italic-VariableFont_SOFT,WONK,opsz,wght.ttf"

# family, subfamily, axis pins, variable source, static fallback, weight, italic
ALIAS_SPECS = [
    ("Fraunces Light", "Regular", {"wght": 300, "opsz": 72, "SOFT": 0, "WONK": 0},
     VAR_ROMAN, "Fraunces_72pt-Light.ttf", 300, False),
    ("Fraunces Light", "Italic", {"wght": 300, "opsz": 72, "SOFT": 0, "WONK": 0},
     VAR_ITALIC, "Fraunces_72pt-LightItalic.ttf", 300, True),
    # opsz 9 matches the name; wght 400 is the trap -- the variable default is 900.
    ("Fraunces 9pt", "Regular", {"wght": 400, "opsz": 9, "SOFT": 0, "WONK": 0},
     VAR_ROMAN, "Fraunces_72pt-Regular.ttf", 400, False),
    ("Fraunces 9pt", "Bold", {"wght": 700, "opsz": 9, "SOFT": 0, "WONK": 0},
     VAR_ROMAN, "Fraunces_72pt-Bold.ttf", 700, False),
    ("Fraunces 9pt", "Italic", {"wght": 400, "opsz": 9, "SOFT": 0, "WONK": 0},
     VAR_ITALIC, "Fraunces_72pt-Italic.ttf", 400, True),
    ("Fraunces", "Regular", {"wght": 400, "opsz": 72, "SOFT": 0, "WONK": 0},
     VAR_ROMAN, "Fraunces_72pt-Regular.ttf", 400, False),
    ("Fraunces", "Bold", {"wght": 700, "opsz": 72, "SOFT": 0, "WONK": 0},
     VAR_ROMAN, "Fraunces_72pt-Bold.ttf", 700, False),
    ("Fraunces", "Italic", {"wght": 400, "opsz": 72, "SOFT": 0, "WONK": 0},
     VAR_ITALIC, "Fraunces_72pt-Italic.ttf", 400, True),
]

# Filename stem -> family, used only when fontTools is unavailable.
FILENAME_FAMILY = {
    "Fraunces_72pt-Regular": "Fraunces 72pt",
    "Fraunces_72pt-Bold": "Fraunces 72pt",
    "Fraunces_72pt-Italic": "Fraunces 72pt",
    "Fraunces_72pt-BoldItalic": "Fraunces 72pt",
    "Fraunces_72pt-Light": "Fraunces 72pt Light",
    "Fraunces_72pt-LightItalic": "Fraunces 72pt Light",
    "Fraunces_72pt-SemiBold": "Fraunces 72pt SemiBold",
    "WixMadeforDisplay-Regular": "Wix Madefor Display",
    "WixMadeforDisplay-Bold": "Wix Madefor Display",
    "WixMadeforDisplay-Medium": "Wix Madefor Display Medium",
    "WixMadeforDisplay-SemiBold": "Wix Madefor Display SemiBold",
    "WixMadeforDisplay-ExtraBold": "Wix Madefor Display ExtraBold",
    "FrauncesLight-Regular": "Fraunces Light",
    "FrauncesLight-Italic": "Fraunces Light",
    "Fraunces9pt-Regular": "Fraunces 9pt",
    "Fraunces9pt-Bold": "Fraunces 9pt",
    "Fraunces9pt-Italic": "Fraunces 9pt",
    "Fraunces-Regular": "Fraunces",
    "Fraunces-Bold": "Fraunces",
    "Fraunces-Italic": "Fraunces",
}

SCAN_DIRS = [
    Path.home() / "Library" / "Fonts",
    Path("/Library/Fonts"),
    Path("/System/Library/Fonts"),
    Path.home() / ".fonts",
    Path.home() / ".local" / "share" / "fonts",
]

INTEREST = ("fraunces", "madefor", "wix")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _have_fonttools():
    try:
        import fontTools  # noqa: F401
        return True
    except ImportError:
        return False


def user_font_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Fonts"
    if sys.platform.startswith("win"):
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Microsoft" / "Windows" / "Fonts"
    xdg = Path.home() / ".local" / "share" / "fonts"
    return xdg if xdg.exists() or not (Path.home() / ".fonts").exists() else Path.home() / ".fonts"


def _fc_files():
    """Candidate font files from fc-list, prefiltered to families we care about."""
    exe = shutil.which("fc-list")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--format", "%{file}\t%{family}\n"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    files = []
    for line in out.stdout.splitlines():
        path, _, fam = line.partition("\t")
        blob = (path + " " + fam).lower()
        if any(tok in blob for tok in INTEREST):
            p = Path(path)
            if p.exists():
                files.append(p)
    return sorted(set(files))


def _scan_files():
    files = []
    for d in SCAN_DIRS:
        if not d.is_dir():
            continue
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for p in entries:
            if p.suffix.lower() not in (".ttf", ".otf", ".ttc"):
                continue
            if any(tok in p.name.lower() for tok in INTEREST):
                files.append(p)
    return sorted(set(files))


def _id1(path: Path):
    """name ID 1 (the family PowerPoint shows) for a font file, or None."""
    try:
        from fontTools.ttLib import TTFont, TTLibError
    except ImportError:
        return None
    try:
        font = TTFont(str(path), lazy=True, fontNumber=0)
    except Exception:
        return None
    try:
        rec = font["name"].getDebugName(1) if "name" in font else None
    except Exception:
        rec = None
    finally:
        try:
            font.close()
        except Exception:
            pass
    return rec


def check(include_aliases: bool = False):
    """Report which brand families actually resolve on this machine.

    Returns ``(results, meta)`` where results maps family -> dict(ok, files) and
    meta records how the answer was obtained (``fc-list`` / ``dir-scan`` and
    ``fonttools`` / ``filename``).
    """
    files = _fc_files()
    source = "fc-list"
    if files is None:
        files = _scan_files()
        source = "dir-scan"
    else:
        # fc-list only sees directories in its config; also sweep the usual dirs.
        files = sorted(set(files) | set(_scan_files()))

    use_ft = _have_fonttools()
    found = {}
    for p in files:
        fam = _id1(p) if use_ft else None
        if fam is None:
            fam = FILENAME_FAMILY.get(p.stem)
        if fam:
            found.setdefault(fam, []).append(str(p))

    wanted = list(REQUIRED) + (list(ALIAS_FAMILIES) if include_aliases else [])
    results = {}
    for fam in wanted:
        hits = found.get(fam, [])
        results[fam] = {"ok": bool(hits), "files": hits}
    meta = {
        "discovery": source,
        "identification": "fonttools" if use_ft else "filename",
        "scanned": len(files),
        "alias_families": {f: bool(found.get(f)) for f in ALIAS_FAMILIES},
    }
    return results, meta


# --------------------------------------------------------------------------
# install
# --------------------------------------------------------------------------
def _copy(src: Path, dest_dir: Path, force: bool, log):
    dst = dest_dir / src.name
    if dst.exists():
        if dst.stat().st_size == src.stat().st_size and dst.read_bytes() == src.read_bytes():
            log("  unchanged  %s" % dst.name)
            return "unchanged"
        if dst.stat().st_mtime > src.stat().st_mtime and not force:
            log("  SKIPPED    %s -- installed copy is NEWER than the bundled one; "
                "rerun with --force to overwrite" % dst.name)
            return "skipped"
        shutil.copy2(src, dst)
        log("  replaced   %s" % dst.name)
        return "replaced"
    shutil.copy2(src, dst)
    log("  installed  %s" % dst.name)
    return "installed"


def _set_names(font, family, subfamily, psname):
    from fontTools.ttLib.tables._n_a_m_e import NameRecord  # noqa: F401
    full = family if subfamily == "Regular" else "%s %s" % (family, subfamily)
    values = {
        1: family,
        2: subfamily,
        3: "FurtherAI alias; %s" % psname,
        4: full,
        6: psname,
        16: family,
        17: subfamily,
    }
    name = font["name"]
    drop = set(values) | {21, 22}
    name.names = [r for r in name.names if r.nameID not in drop]
    for platform_id, enc_id, lang_id in ((3, 1, 0x409), (1, 0, 0)):
        for nid, val in values.items():
            name.setName(val, nid, platform_id, enc_id, lang_id)


def _stamp_style(font, weight, italic):
    os2 = font["OS/2"]
    os2.usWeightClass = weight
    fs = os2.fsSelection & ~(0x01 | 0x20 | 0x40)   # clear ITALIC / BOLD / REGULAR
    head = font["head"]
    mac = head.macStyle & ~0x03
    if italic:
        fs |= 0x01
        mac |= 0x02
    elif weight >= 700:
        fs |= 0x20
        mac |= 0x01
    else:
        fs |= 0x40
    os2.fsSelection = fs
    head.macStyle = mac


def _build_alias(spec, dest_dir: Path, force: bool, log):
    family, subfamily, pins, var_src, static_name, weight, italic = spec
    from fontTools.ttLib import TTFont
    psname = ("%s-%s" % (family, subfamily)).replace(" ", "")
    out = dest_dir / ("%s.ttf" % psname)

    src = None
    instanced = False
    if var_src.exists():
        try:
            from fontTools.varLib import instancer
            font = TTFont(str(var_src))
            instancer.instantiateVariableFont(font, pins, inplace=True, updateFontNames=False)
            instanced = True
            src = var_src
        except Exception as exc:                                  # noqa: BLE001
            log("  note: could not instance %s (%s); using the static cut"
                % (var_src.name, exc))
            font = None
    else:
        font = None
    if not instanced:
        static = FRAUNCES_DIR / "static" / static_name
        if not static.exists():
            log("  SKIPPED    %s -- no source font (%s missing)" % (out.name, static_name))
            return "skipped"
        font = TTFont(str(static))
        src = static

    _set_names(font, family, subfamily, psname)
    _stamp_style(font, weight, italic)
    if out.exists() and not force and out.stat().st_mtime > src.stat().st_mtime:
        log("  SKIPPED    %s -- existing alias is newer; rerun with --force" % out.name)
        font.close()
        return "skipped"
    existed = out.exists()
    font.save(str(out))
    font.close()
    log("  %s  %s  (ID1=%r ID2=%r wght=%d%s)"
        % ("replaced " if existed else "generated", out.name, family, subfamily, weight,
           ", instanced from the variable font" if instanced else ", renamed static"))
    return "replaced" if existed else "generated"


def install(dest_dir=None, aliases=False, force=False, log=print):
    """Copy the bundled statics into the user font dir; optionally add aliases."""
    dest_dir = Path(dest_dir).expanduser() if dest_dir else user_font_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    log("font dir: %s" % dest_dir)

    counts = {}
    missing = [p for p in BUNDLED if not p.exists()]
    for p in missing:
        log("  MISSING    %s (not in assets/brand/fonts)" % p.name)
    for src in BUNDLED:
        if src.exists():
            r = _copy(src, dest_dir, force, log)
            counts[r] = counts.get(r, 0) + 1

    if aliases:
        if not _have_fonttools():
            log("")
            log("--aliases needs fontTools, which is not installed.")
            log("  python3 -m pip install fonttools")
            log("  (the seven statics above were still installed)")
        else:
            log("")
            log("template-name aliases (so decks naming 'Fraunces Light' / "
                "'Fraunces 9pt' / 'Fraunces' still render):")
            for spec in ALIAS_SPECS:
                r = _build_alias(spec, dest_dir, force, log)
                counts[r] = counts.get(r, 0) + 1

    exe = shutil.which("fc-cache")
    if exe:
        try:
            subprocess.run([exe, "-f", str(dest_dir)], capture_output=True, timeout=120)
            log("")
            log("fc-cache -f %s" % dest_dir)
        except (OSError, subprocess.SubprocessError) as exc:
            log("fc-cache failed: %s" % exc)
    elif sys.platform == "darwin":
        log("")
        log("no fc-cache on PATH; macOS picks up ~/Library/Fonts automatically "
            "(restart PowerPoint to see new families).")
    else:
        log("")
        log("no fc-cache on PATH -- refresh your font cache manually.")
    return counts


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _print_check(results, meta, include_aliases):
    width = max(len(f) for f in results) if results else 20
    print("font check  (discovery: %s, identification: %s, %d candidate files)"
          % (meta["discovery"], meta["identification"], meta["scanned"]))
    if meta["identification"] == "filename":
        print("CAVEAT: fontTools is not installed, so families were guessed from "
              "FILENAMES, not from name ID 1. Install it for a real answer: "
              "python3 -m pip install fonttools")
    print("")
    for fam, info in results.items():
        mark = "OK     " if info["ok"] else "MISSING"
        print("  %s  %-*s  %s" % (mark, width, fam, info["files"][0] if info["files"] else ""))
        for extra in info["files"][1:]:
            print("  %s  %-*s  %s" % ("       ", width, "", extra))
    if not include_aliases:
        print("")
        print("template alias families (only needed to open the shipped decks; "
              "install with `fonts.py install --aliases`):")
        for fam, ok in meta["alias_families"].items():
            print("  %s  %s" % ("present" if ok else "absent ", fam))
    bad = [f for f, i in results.items() if not i["ok"]]
    print("")
    if bad:
        print("%d of %d required families missing. Fix with:" % (len(bad), len(results)))
        print("  python3 %s install --aliases" % Path(__file__).name)
    else:
        print("all %d required families resolve." % len(results))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="report which brand families resolve here")
    c.add_argument("--aliases", action="store_true",
                   help="also require the template names (Fraunces Light / 9pt / Fraunces)")
    c.add_argument("--dir", help="extra directory to scan")
    c.add_argument("--json", action="store_true")

    i = sub.add_parser("install", help="copy the bundled static TTFs into the user font dir")
    i.add_argument("--aliases", action="store_true",
                   help="also generate TTFs registered under the template family names")
    i.add_argument("--dir", help="install into this directory instead of the user font dir")
    i.add_argument("--force", action="store_true", help="overwrite newer installed copies")

    args = ap.parse_args(argv)

    if args.cmd == "check":
        if args.dir:
            SCAN_DIRS.append(Path(args.dir).expanduser())
        results, meta = check(include_aliases=args.aliases)
        if args.json:
            print(json.dumps({"results": results, "meta": meta}, indent=2))
            return 1 if any(not v["ok"] for v in results.values()) else 0
        return _print_check(results, meta, args.aliases)

    install(dest_dir=args.dir, aliases=args.aliases, force=args.force)
    SCAN_DIRS.append(Path(args.dir).expanduser() if args.dir else user_font_dir())
    print("")
    results, meta = check(include_aliases=args.aliases)
    return _print_check(results, meta, args.aliases)


if __name__ == "__main__":
    sys.exit(main())
