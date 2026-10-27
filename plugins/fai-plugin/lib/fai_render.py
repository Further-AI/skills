"""Rendering helpers shared by artifact-producing skills (accuracy, doc).
stdlib only.

- tokens_css() / font_face_css(): the shared design tokens and embedded
  Wix Madefor Text @font-face, for inlining into self-contained HTML.
- chartjs_js() / datalabels_js(): vendored Chart.js 4.4.1 and its
  datalabels plugin, inlined so generated reports never hit a CDN.
- print_to_pdf(): HTML file -> PDF via headless Chrome/Chromium. Falls
  back gracefully: returns None when no browser binary is found, so
  callers ship the HTML and tell the user to print it themselves.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

ASSETS = Path(__file__).resolve().parent / "assets"

_CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "google-chrome",
    "chromium",
    "chromium-browser",
    "msedge",
)


def tokens_css() -> str:
    return (ASSETS / "tokens.css").read_text()


def font_face_css() -> str:
    b64 = (ASSETS / "wix-madefor-text.woff2.b64").read_text().strip()
    return (
        "@font-face { font-family: 'Wix Madefor Text'; "
        f"src: url(data:font/woff2;base64,{b64}) format('woff2'); "
        "font-weight: 400 700; font-display: swap; }"
    )


def chartjs_js() -> str:
    return (ASSETS / "chart.umd.min.js").read_text()


def datalabels_js() -> str:
    return (ASSETS / "chartjs-plugin-datalabels.min.js").read_text()


def find_chrome() -> Optional[str]:
    for candidate in _CHROME_CANDIDATES:
        if candidate.startswith("/"):
            if Path(candidate).exists():
                return candidate
        elif shutil.which(candidate):
            return shutil.which(candidate)
    return None


def print_to_pdf(html_path: str | Path, pdf_path: str | Path,
                 landscape: bool = False, timeout: int = 120) -> Optional[Path]:
    """Render an HTML file to PDF with headless Chrome. Returns the PDF path,
    or None when no Chrome/Chromium/Edge binary exists on this machine."""
    chrome = find_chrome()
    if not chrome:
        return None
    html_uri = Path(html_path).resolve().as_uri()
    pdf = Path(pdf_path).resolve()
    pdf.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer",
        f"--print-to-pdf={pdf}",
    ]
    if landscape:
        cmd.append("--landscape")
    cmd.append(html_uri)
    subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
    return pdf
