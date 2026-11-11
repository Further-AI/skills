"""Embed the FurtherAI brand fonts into a finished .docx or .pptx.

Usage: python scripts/embed_fonts.py OUTPUT.docx|OUTPUT.pptx

Run it after the file is saved and before you deliver it. It embeds only the brand
fonts the file uses, so Word, PowerPoint and the in-app preview render Fraunces, Wix
Madefor and IBM Plex Mono even where they are not installed. Running it twice is safe.
"""

import json
import re
import sys
import uuid
import zipfile
from pathlib import Path

FONTS = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/font"
EMPTY_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>')


def _used_families(items: dict[str, bytes], families: list[str]) -> list[str]:
    xml = b"".join(v for k, v in items.items() if k.endswith(".xml")).decode("utf-8", "ignore")
    return [f for f in families if f'"{f}"' in xml]


def _add_rel(rels: str, target: str) -> tuple[str, str]:
    used = set(re.findall(r'Id="(rId\d+)"', rels))
    n = 900
    while f"rId{n}" in used:
        n += 1
    entry = f'<Relationship Id="rId{n}" Type="{FONT_REL}" Target="{target}"/>'
    return rels.replace("</Relationships>", entry + "</Relationships>"), f"rId{n}"


def _add_default(ct: str, ext: str, mime: str) -> str:
    if f'Extension="{ext}"' in ct:
        return ct
    return ct.replace("<Default ", f'<Default Extension="{ext}" ContentType="{mime}"/><Default ', 1)


def _docx(items: dict[str, bytes], families: dict[str, dict[str, str]]) -> None:
    rels_name = "word/_rels/fontTable.xml.rels"
    rels = items.get(rels_name, EMPTY_RELS.encode()).decode()
    rels = re.sub(r'<Relationship [^>]*Target="fonts/brand-[^"]*"/>', "", rels)
    table = items["word/fontTable.xml"].decode()
    for name in [n for n in items if n.startswith("word/fonts/brand-")]:
        del items[name]
    n = 0
    for family, faces in families.items():
        embeds = ""
        for style, tag in (("regular", "embedRegular"), ("italic", "embedItalic")):
            if style not in faces:
                continue
            n += 1
            data = bytearray((FONTS / f"{faces[style]}.ttf").read_bytes())
            guid = str(uuid.uuid4()).upper()
            key = bytes.fromhex(guid.replace("-", ""))[::-1]
            for j in range(32):
                data[j] ^= key[j % 16]
            part = f"fonts/brand-{n}.odttf"
            items[f"word/{part}"] = bytes(data)
            rels, rid = _add_rel(rels, part)
            embeds += f'<w:{tag} r:id="{rid}" w:fontKey="{{{guid}}}"/>'
        table = re.sub(rf'<w:font w:name="{re.escape(family)}"(?:\s*/>|[^>]*>.*?</w:font>)', "", table, flags=re.S)
        table = table.replace("</w:fonts>", f'<w:font w:name="{family}"><w:charset w:val="00"/><w:family w:val="auto"/>'
                              f'<w:pitch w:val="variable"/>{embeds}</w:font></w:fonts>')
    if "xmlns:r=" not in table.split(">", 2)[1]:
        table = table.replace("<w:fonts ", '<w:fonts xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" ', 1)
    settings = items["word/settings.xml"].decode()
    if "<w:embedTrueTypeFonts" not in settings:
        settings = re.sub(r"(<w:settings[^>]*>)", r"\1<w:embedTrueTypeFonts/>", settings, count=1)
    ct = _add_default(items["[Content_Types].xml"].decode(), "odttf",
                      "application/vnd.openxmlformats-officedocument.obfuscatedFont")
    items.update({rels_name: rels.encode(), "word/fontTable.xml": table.encode(),
                  "word/settings.xml": settings.encode(), "[Content_Types].xml": ct.encode()})


def _pptx(items: dict[str, bytes], families: dict[str, dict[str, str]]) -> None:
    rels_name = "ppt/_rels/presentation.xml.rels"
    rels = re.sub(r'<Relationship [^>]*Target="fonts/brand-[^"]*"/>', "", items[rels_name].decode())
    for name in [n for n in items if n.startswith("ppt/fonts/brand-")]:
        del items[name]
    entries, n = "", 0
    for family, faces in families.items():
        refs = ""
        for style in ("regular", "italic"):
            if style not in faces:
                continue
            n += 1
            part = f"fonts/brand-{n}.fntdata"
            items[f"ppt/{part}"] = (FONTS / f"{faces[style]}.fntdata").read_bytes()
            rels, rid = _add_rel(rels, part)
            refs += f'<p:{style} r:id="{rid}"/>'
        entries += f'<p:embeddedFont><p:font typeface="{family}" charset="0"/>{refs}</p:embeddedFont>'
    pres = re.sub(r"<p:embeddedFontLst>.*?</p:embeddedFontLst>", "", items["ppt/presentation.xml"].decode(), flags=re.S)
    pres = re.sub(r"(<p:notesSz[^>]*/>)", lambda m: m.group(1) + f"<p:embeddedFontLst>{entries}</p:embeddedFontLst>", pres, count=1)
    ct = _add_default(items["[Content_Types].xml"].decode(), "fntdata", "application/x-fontdata")
    items.update({rels_name: rels.encode(), "ppt/presentation.xml": pres.encode(), "[Content_Types].xml": ct.encode()})


def main(path: Path) -> None:
    manifest = json.loads((FONTS / "fonts.json").read_text())
    with zipfile.ZipFile(path) as src:
        items = {i.filename: src.read(i.filename) for i in src.infolist() if not i.is_dir()}
    used = {f: manifest[f] for f in _used_families(items, list(manifest))}
    if not used:
        print(f"{path.name}: no brand fonts referenced; nothing embedded")
        return
    (_docx if path.suffix.lower() == ".docx" else _pptx)(items, used)
    tmp = path.with_name(path.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        out.writestr("[Content_Types].xml", items.pop("[Content_Types].xml"))
        for name, data in items.items():
            out.writestr(name, data)
    tmp.replace(path)
    print(f"{path.name}: embedded {', '.join(used)}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].lower().endswith((".docx", ".pptx")):
        sys.exit("usage: python scripts/embed_fonts.py OUTPUT.docx|OUTPUT.pptx")
    main(Path(sys.argv[1]))
