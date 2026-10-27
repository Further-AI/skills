"""Minimal Chrome driver for capturing the FurtherAI app UI. stdlib only.

Why this exists: some artifacts need pixels — a user guide with screenshots, a
deck slide showing a real submission. Everything else about the platform is
faster and more accurate through the API (`fai_client.FaiClient`). Opening a
browser to read a value is the wrong tool.

Design notes, each one paid for by a real failure:

- **`fromSurface` is mode-dependent.** Headless: `true` — the renderer path
  returns a blank dark frame there. Headed: `false` — the surface path needs a
  compositor frame that an occluded window never produces, so it hangs. Getting
  this backwards yields a plausible-looking blank image, not an error.
- **Never SIGKILL the browser.** Cookies and localStorage flush on graceful
  exit; `kill -9` loses the session and forces another interactive login.
  `Browser.quit()` sends SIGTERM.
- **Never match Chrome processes by name.** Killing `Google Chrome` broadly
  takes down the user's real browser. Only the PID this module launched, or a
  process whose `--user-data-dir` is exactly our profile, is ever signalled.
- **Clicks go through JS `element.click()`.** The app layers absolutely
  positioned panels over its own rows; synthesized input events land on the
  overlay and never reach the target.
- **Do not set device metrics without resizing the window.** An
  `Emulation.setDeviceMetricsOverride` wider than the real window captures a
  black band. Size the window instead and capture natively.
- **Captures never raise the window.** `Page.bringToFront` is what yanks focus
  away from the user, and it is only ever needed because a *background tab*
  will not answer `captureScreenshot`. Keeping exactly one tab per window
  removes the need entirely: an unfocused, fully occluded window captures fine.
- **Do not minimise instead.** macOS freezes a minimised renderer: captures of
  already-painted content still succeed, but `Runtime.evaluate` times out and
  navigations never render. Verified the hard way.
- **Why not headless: it cannot be done without destroying the session.** A
  Claude-launched Chrome has no Keychain access, so it can never decrypt a
  profile's cookies on a later launch. Copying the cookies into a headless
  browser does authenticate briefly, but PropelAuth rotates the refresh token
  on use, so the replay invalidates the original — the signed-in window is
  logged out as a side effect. Verified, twice, at the cost of a real login.
  There is exactly one supported model: keep the signed-in window alive and
  never raise it.
- **The refresh token rotates on every use.** An exported cookie set goes stale
  the moment the source browser refreshes again, so the export has to be the
  last thing a signed-in browser does before it dies, and every later run
  re-saves the rotated pair. A 401 from
  `POST auth.furtherai.com/api/v1/refresh_token` is what a stale copy looks
  like.
- **Session file is a credential.** `~/.fai/browser-session.json`, chmod 600,
  holds live auth cookies.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fai_render import find_chrome  # noqa: E402  (shared Chrome lookup)

DEFAULT_PORT = 9222
DEFAULT_PROFILE = Path.home() / ".fai" / "browser-profile"
HEADLESS_PROFILE = Path.home() / ".fai" / "browser-profile-headless"
ALLOWED_HOST_SUFFIX = ".furtherai.com"
LAUNCH_FLAGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-features=CalculateNativeWinOcclusion,DestroyProfileOnBrowserClose",
]


class BrowserError(Exception):
    """Anything that stops a capture: no Chrome, no session, no such element."""


# --------------------------------------------------------------------------
# websocket (RFC 6455 client, text frames only)
# --------------------------------------------------------------------------

class _WebSocket:
    def __init__(self, url: str, timeout: float = 30.0):
        parts = urlparse(url)
        if parts.scheme != "ws":
            raise BrowserError(f"only ws:// devtools endpoints are supported, got {url}")
        self.sock = socket.create_connection((parts.hostname, parts.port or 80), timeout=timeout)
        self.sock.settimeout(timeout)
        self._buf = b""
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parts.hostname}:{parts.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(handshake.encode())
        header = self._read_until(b"\r\n\r\n")
        if b" 101 " not in header.split(b"\r\n")[0]:
            raise BrowserError(f"devtools websocket upgrade failed: {header[:120]!r}")

    # -- low level -------------------------------------------------------
    def _read_until(self, marker: bytes) -> bytes:
        while marker not in self._buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise BrowserError("devtools socket closed during handshake")
            self._buf += chunk
        idx = self._buf.index(marker) + len(marker)
        out, self._buf = self._buf[:idx], self._buf[idx:]
        return out

    def _read_exactly(self, n: int) -> bytes:
        while len(self._buf) < n:
            try:
                chunk = self.sock.recv(max(65536, n - len(self._buf)))
            except socket.timeout as exc:
                raise BrowserError("devtools read timed out — the page is busy or the "
                                   "call got no reply") from exc
            if not chunk:
                raise BrowserError("devtools socket closed mid-frame")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send(self, payload: str) -> None:
        data = payload.encode()
        header = bytearray([0x81])  # FIN + text
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < (1 << 16):
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def recv(self) -> str:
        """Return the next complete text message, handling fragments and pings."""
        chunks: list[bytes] = []
        while True:
            b0, b1 = self._read_exactly(2)
            fin = b0 & 0x80
            opcode = b0 & 0x0F
            masked = b1 & 0x80
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read_exactly(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read_exactly(8))[0]
            mask = self._read_exactly(4) if masked else b""
            payload = self._read_exactly(length) if length else b""
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

            if opcode == 0x8:  # close
                raise BrowserError("devtools websocket closed by browser")
            if opcode == 0x9:  # ping -> pong
                self._send_control(0x8A, payload)
                continue
            if opcode == 0xA:  # pong
                continue
            chunks.append(payload)
            if fin:
                return b"".join(chunks).decode("utf-8", "replace")

    def _send_control(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes([opcode, 0x80 | len(payload)]) + mask + masked)

    def close(self) -> None:
        try:
            self._send_control(0x88, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------
# browser
# --------------------------------------------------------------------------

def _http_json(url: str, timeout: float = 5.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _port_alive(port: int) -> bool:
    try:
        _http_json(f"http://127.0.0.1:{port}/json/version", timeout=2.0)
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _pids_using_profile(profile: Path) -> list[int]:
    """PIDs whose command line carries exactly our --user-data-dir. Never a name match."""
    try:
        out = subprocess.run(["ps", "-eo", "pid=,command="], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    needle = f"--user-data-dir={profile}"
    pids = []
    for line in out.splitlines():
        line = line.strip()
        if needle in line:
            head = line.split(None, 1)[0]
            if head.isdigit():
                pids.append(int(head))
    return pids


class Browser:
    """A Chrome window on the FurtherAI app, driven over CDP."""

    def __init__(self, port: int = DEFAULT_PORT, profile: Optional[Path | str] = None,
                 app_url: str = "https://app.furtherai.com", timeout: float = 30.0,
                 headless: bool = False):
        self.headless = headless
        self.port = port
        self.profile = Path(profile) if profile else DEFAULT_PROFILE
        self.app_url = app_url.rstrip("/")
        self.app_host = urlparse(self.app_url).hostname or "app.furtherai.com"
        self.timeout = timeout
        self._ws: Optional[_WebSocket] = None
        self._msg_id = 0
        self._launched_pid: Optional[int] = None

    # -- lifecycle -------------------------------------------------------
    def launch_or_attach(self, start_url: Optional[str] = None, wait: float = 30.0,
                         takeover: bool = True) -> str:
        """Attach to a live debug port, or launch Chrome. Returns 'attached'/'launched'.

        A headless run cannot share the profile with a visible one, so when we
        want headless and a visible instance owns the profile, that instance is
        closed gracefully first (SIGTERM, session preserved).
        """
        if _port_alive(self.port):
            if not (self.headless and takeover and self._running_is_headed()):
                return "attached"
            self.quit()
            for _ in range(20):
                if not _port_alive(self.port):
                    break
                time.sleep(0.5)

        chrome = find_chrome()
        if not chrome:
            raise BrowserError(
                "no Chrome/Chromium/Edge found. Install Google Chrome, or capture the "
                "screenshots by hand and pass the files in.")

        self.profile.mkdir(parents=True, exist_ok=True)
        os.chmod(self.profile, 0o700)  # holds a live app session cookie
        if not _pids_using_profile(self.profile):
            for lock in self.profile.glob("Singleton*"):
                lock.unlink(missing_ok=True)  # stale lock from a crashed run

        cmd = [chrome, f"--user-data-dir={self.profile}",
               f"--remote-debugging-port={self.port}", *LAUNCH_FLAGS,
               f"--window-size={1640 if self.headless else 1180},"
               f"{1100 if self.headless else 820}",
               "--window-position=60,60"]
        if self.headless:
            # Headless is the default for captures: no window, so nothing steals
            # focus from whatever the user is doing.
            cmd += ["--headless=new", "--disable-gpu", "--hide-scrollbars"]
        cmd.append(start_url or f"{self.app_url}/home")
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._launched_pid = proc.pid

        deadline = time.time() + wait
        while time.time() < deadline:
            if _port_alive(self.port):
                return "launched"
            time.sleep(0.5)
        raise BrowserError(f"Chrome did not open a debug port on {self.port} within {wait:.0f}s")

    def _running_is_headed(self) -> bool:
        """True when the process on our port was started without --headless."""
        try:
            out = subprocess.run(["ps", "-eo", "command="], capture_output=True,
                                 text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        for line in out.splitlines():
            if f"--remote-debugging-port={self.port}" in line and "--type=" not in line:
                return "--headless" not in line
        return False

    def targets(self) -> list[dict]:
        return [t for t in _http_json(f"http://127.0.0.1:{self.port}/json/list")
                if t.get("type") == "page"]

    def new_page(self, url: Optional[str] = None) -> dict:
        """Open a tab. Chrome keeps running on macOS after its last window closes,
        so an attached browser can legitimately have zero page targets."""
        target = url or f"{self.app_url}/home"
        self._check_host(target)
        endpoint = (f"http://127.0.0.1:{self.port}/json/new?"
                    + urllib.parse.quote(target, safe=""))
        for method in ("PUT", "GET"):  # PUT since Chrome 111; GET on older builds
            try:
                req = urllib.request.Request(endpoint, method=method)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                if e.code not in (405, 400):
                    raise BrowserError(f"could not open a tab: HTTP {e.code}")
            except (urllib.error.URLError, OSError) as e:
                raise BrowserError(f"could not open a tab: {e}")
        raise BrowserError("could not open a tab on the debug port")

    def attach_page(self, prefer_host: Optional[str] = None) -> dict:
        """Attach to the app tab (or the first tab). Closes any previous attachment."""
        host = prefer_host or self.app_host
        pages = self.targets()
        if not pages:
            self.new_page()
            time.sleep(1.5)
            pages = self.targets()
        if not pages:
            raise BrowserError("browser has no open page targets and would not open one")
        pick = next((t for t in pages if host in (t.get("url") or "")), None) or pages[0]
        self.detach()
        self._ws = _WebSocket(pick["webSocketDebuggerUrl"], timeout=self.timeout)
        return pick

    def _reattach(self) -> None:
        """Re-open the devtools socket after a navigation tore the context down."""
        try:
            self.attach_page()
        except BrowserError:
            pass

    def detach(self) -> None:
        if self._ws:
            self._ws.close()
            self._ws = None

    def quit(self) -> None:
        """Graceful shutdown (SIGTERM). Only ever our own launched process."""
        self.detach()
        pids = [self._launched_pid] if self._launched_pid else _pids_using_profile(self.profile)
        for pid in [p for p in pids if p]:
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

    # -- CDP -------------------------------------------------------------
    def send(self, method: str, params: Optional[dict] = None, timeout: Optional[float] = None) -> dict:
        if not self._ws:
            self.attach_page()
        assert self._ws is not None
        self._msg_id += 1
        mid = self._msg_id
        budget = timeout or self.timeout
        self._ws.sock.settimeout(budget)
        self._ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + budget
        while time.time() < deadline:
            msg = json.loads(self._ws.recv())
            if msg.get("id") != mid:
                continue  # an event, or a reply to an abandoned call
            if "error" in msg:
                raise BrowserError(f"{method} failed: {msg['error'].get('message')}")
            return msg.get("result", {})
        raise BrowserError(f"{method} timed out after {timeout or self.timeout:.0f}s")

    # -- page ------------------------------------------------------------
    def _check_host(self, url: str) -> None:
        if url in ("about:blank", "chrome://newtab/"):
            return
        host = urlparse(url).hostname or ""
        if not (host.endswith(ALLOWED_HOST_SUFFIX) or host in ("localhost", "127.0.0.1")):
            raise BrowserError(
                f"refusing to navigate to {host}: this driver only visits *{ALLOWED_HOST_SUFFIX}")

    def js(self, expression: str, timeout: Optional[float] = None) -> Any:
        res = self.send("Runtime.evaluate",
                        {"expression": expression, "returnByValue": True,
                         "awaitPromise": True}, timeout=timeout)
        if res.get("exceptionDetails"):
            desc = res["exceptionDetails"].get("exception", {}).get("description", "js error")
            raise BrowserError(f"page script failed: {str(desc)[:200]}")
        return res.get("result", {}).get("value")

    def url(self) -> str:
        return self.js("location.href") or ""

    def text(self, limit: int = 4000) -> str:
        return (self.js("document.body ? document.body.innerText : ''") or "")[:limit]

    def goto(self, url: str, wait_text: Optional[str] = None, settle: float = 2.5,
             timeout: float = 60.0) -> None:
        self._check_host(url)
        self.send("Page.navigate", {"url": url}, timeout=timeout)
        self.wait_ready(timeout=timeout)
        if wait_text:
            self.wait_text(wait_text, timeout=timeout)
        time.sleep(settle)  # let the SPA paint after data lands

    def wait_ready(self, timeout: float = 60.0) -> None:
        time.sleep(0.6)  # let the navigation commit before the first evaluate
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.js("document.readyState", timeout=5) == "complete":
                    return
            except BrowserError:
                self._reattach()  # a cross-document navigation kills the context
            time.sleep(0.4)
        raise BrowserError("page never reached readyState=complete")

    def wait_text(self, needle: str, timeout: float = 30.0) -> None:
        deadline = time.time() + timeout
        seen = ""
        while time.time() < deadline:
            try:
                seen = self.js("document.body ? document.body.innerText : ''", timeout=8) or ""
            except BrowserError:
                self._reattach()
                seen = ""
            if needle.lower() in seen.lower():
                return
            time.sleep(0.5)
        raise BrowserError(
            f"anchor text {needle!r} never appeared — the screen did not load, or the UI "
            f"renamed it. Current page starts: {seen[:160]!r}")

    def window_size(self, width: int = 1640, height: int = 1100) -> None:
        """Resize, then wait for the viewport to stop moving.

        A capture taken before the resize settles paints the *old* (often
        tiny, freshly-opened-tab) layout onto the new canvas: a correctly
        sized PNG with the app crammed into one corner.
        """
        if self.headless:
            # No real window to mismatch, so an override is safe here — and it
            # pins dpr 2 regardless of the display the machine happens to have.
            self.send("Emulation.setDeviceMetricsOverride",
                      {"width": width, "height": height - 100, "deviceScaleFactor": 2,
                       "mobile": False})
            self.settle(tries=4)
            return
        wid = self.send("Browser.getWindowForTarget")["windowId"]
        self.send("Browser.setWindowBounds",
                  {"windowId": wid,
                   "bounds": {"left": 20, "top": 20, "width": width, "height": height,
                              "windowState": "normal"}})
        self.settle()

    def settle(self, tries: int = 12, pause: float = 0.4) -> dict:
        """Block until two consecutive viewport readings agree."""
        last = None
        for _ in range(tries):
            time.sleep(pause)
            try:
                now = self.viewport()
            except BrowserError:
                continue
            if now and now == last:
                return now
            last = now
        return last or {}

    def viewport(self) -> dict:
        return self.js("({w: innerWidth, h: innerHeight, dpr: devicePixelRatio})") or {}

    # -- interaction -----------------------------------------------------
    def click_text(self, label: str, exact: bool = True, nth: int | str = 0,
                   settle: float = 3.0) -> int:
        """JS-click the nth element whose trimmed text matches. nth may be 'last'.

        Returns how many candidates matched. Raises when nothing matched.
        """
        script = """
        (() => {
          const want = %s, exact = %s, nth = %s;
          const nodes = [...document.querySelectorAll('button,[role=button],a,[role=row],li,div,span')];
          const hits = nodes.filter(el => {
            const t = (el.innerText || '').trim();
            if (!t || t.length > 400) return false;
            return exact ? t === want : t.toLowerCase().includes(want.toLowerCase());
          }).filter(el => {
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
          });
          if (!hits.length) return {n: 0};
          const pick = nth === 'last' ? hits[hits.length - 1] : hits[Math.min(nth, hits.length - 1)];
          pick.scrollIntoView({block: 'center'});
          pick.click();
          return {n: hits.length};
        })()
        """ % (json.dumps(label), json.dumps(exact), json.dumps(nth))
        out = self.js(script) or {}
        count = int(out.get("n") or 0)
        if not count:
            raise BrowserError(
                f"no clickable element with text {label!r}. The screen may not be loaded, or the "
                f"UI renamed the control — check references/screens.md before editing a recipe.")
        time.sleep(settle)
        return count

    def click_selector(self, selector: str, nth: int = 0, settle: float = 3.0) -> None:
        out = self.js("""
        (() => {
          const els = [...document.querySelectorAll(%s)];
          if (!els.length) return false;
          const el = els[Math.min(%d, els.length - 1)];
          el.scrollIntoView({block: 'center'});
          el.click();
          return true;
        })()
        """ % (json.dumps(selector), nth))
        if not out:
            raise BrowserError(f"selector {selector!r} matched nothing")
        time.sleep(settle)

    def set_files(self, paths: Iterable[str | Path], selector: str = "input[type=file]",
                  settle: float = 8.0) -> list[str]:
        """Attach local files to a file input (the drag-and-drop zone's hidden input)."""
        files = [str(Path(p).resolve()) for p in paths]
        for f in files:
            if not Path(f).exists():
                raise BrowserError(f"file not found: {f}")
        doc = self.send("DOM.getDocument", {"depth": 0})
        node = self.send("DOM.querySelector",
                         {"nodeId": doc["root"]["nodeId"], "selector": selector})
        if not node.get("nodeId"):
            raise BrowserError(f"no file input matching {selector!r} on this screen")
        self.send("DOM.setFileInputFiles", {"files": files, "nodeId": node["nodeId"]})
        time.sleep(settle)  # uploads must finish before the shot
        return files

    # -- geometry + capture ----------------------------------------------
    def rects(self, selectors: Optional[dict] = None, texts: Optional[dict] = None) -> dict:
        """Bounding boxes in *image* pixels (CSS px x devicePixelRatio).

        Feed these to annotate.py instead of eyeballing coordinates off a PNG.
        `selectors` and `texts` are {name: query} maps.
        """
        script = """
        (() => {
          const dpr = devicePixelRatio || 1;
          const out = {_dpr: dpr, _viewport: [innerWidth, innerHeight]};
          const box = el => { const r = el.getBoundingClientRect();
            return [Math.round(r.x*dpr), Math.round(r.y*dpr),
                    Math.round(r.width*dpr), Math.round(r.height*dpr)]; };
          const sels = %s, txts = %s;
          for (const [name, sel] of Object.entries(sels)) {
            const el = document.querySelector(sel);
            if (el) out[name] = box(el);
          }
          for (const [name, want] of Object.entries(txts)) {
            const el = [...document.querySelectorAll('button,[role=button],a,td,div,span,h1,h2')]
              .filter(e => (e.innerText || '').trim() === want)
              .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })[0];
            if (el) out[name] = box(el);
          }
          return out;
        })()
        """ % (json.dumps(selectors or {}), json.dumps(texts or {}))
        return self.js(script) or {}

    def shot(self, out_path: str | Path, clip: Optional[dict] = None,
             beyond_viewport: bool = False) -> Path:
        """Capture the page. `clip` is CSS px: {x, y, width, height, scale}.

        `Page.bringToFront` first: a background tab never answers
        captureScreenshot at all — the call hangs until it times out.
        """
        # Deliberately no Page.bringToFront: it raises the window and steals
        # focus. A minimized window with a single tab captures fine without it.
        self.settle(tries=2)
        # Headless has no window surface to be occluded, and its renderer path
        # returns a blank dark frame — capture from the surface there. A headed
        # window is the opposite: the surface needs a compositor frame it will
        # not produce while occluded, so render from the renderer instead.
        params: dict[str, Any] = {"format": "png", "fromSurface": self.headless,
                                  "captureBeyondViewport": beyond_viewport}
        if clip:
            params["clip"] = {"x": clip["x"], "y": clip["y"], "width": clip["width"],
                              "height": clip["height"], "scale": clip.get("scale", 1)}
        data = self.send("Page.captureScreenshot", params, timeout=max(self.timeout, 60))["data"]
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data))
        return path

    # -- session ----------------------------------------------------------
    def set_active_org(self, org_id: str) -> None:
        """Point the app at one workspace.

        A fresh sign-in lands in whichever org the account defaults to, which is
        rarely the one being documented ("This workflow isn't available in this
        workspace"). The app reads `active_org_id`; setting it and navigating is
        enough — it mints the matching org token from the session.
        """
        self.send("Network.enable", timeout=10)
        self.send("Network.setCookie",
                  {"name": "active_org_id", "value": org_id,
                   "domain": self.app_host, "path": "/", "secure": True}, timeout=10)

    def active_org_label(self) -> Optional[str]:
        """The 'user | org' text from the account control, for verification."""
        return self.js("""
        (() => {const el=[...document.querySelectorAll('button,[role=button]')]
          .filter(e => /\n/.test((e.innerText||'').trim()) && (e.innerText||'').length < 60).pop();
          return el ? el.innerText.trim().replace('\n', ' | ') : null})()""", timeout=15)

    # -- window hygiene ------------------------------------------------------
    def window_state(self) -> Optional[str]:
        try:
            return self.send("Browser.getWindowForTarget")["bounds"].get("windowState")
        except BrowserError:
            return None

    def close_other_tabs(self) -> int:
        """Keep exactly one tab. A background tab never answers captureScreenshot,
        and the only cure for that is fronting it — which steals focus."""
        try:
            pages = self.targets()
        except (urllib.error.URLError, OSError):
            return 0
        keep = next((t["id"] for t in pages if self.app_host in (t.get("url") or "")), None)
        keep = keep or (pages[0]["id"] if pages else None)
        closed = 0
        for t in pages:
            if t["id"] == keep:
                continue
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json/close/{t['id']}", timeout=5).read()
                closed += 1
            except (urllib.error.URLError, OSError):
                pass
        if closed:
            self.attach_page()
        return closed

    def park(self) -> None:
        """Capture state: exactly one tab, window (if any) left exactly where it is.

        Never minimise — macOS freezes a minimised renderer, so evaluation times
        out and navigations stop rendering. Never front — that steals focus.
        """
        self.close_other_tabs()

    # -- magic-link login (the only way a headless browser gets a session) ---
    def request_magic_link(self, email: str) -> dict:
        """Ask the auth page to email a sign-in link to `email`.

        The same browser must open the resulting link: the request leaves state
        behind (an `re_ce` cookie), and a link opened elsewhere can be rejected.
        """
        self.goto(f"{self.app_url}/home", settle=2.0, timeout=60)
        body = self.text(400)
        if "Magic Link" not in body and "Log in" not in body:
            raise BrowserError(f"not on the login page (page reads: {body[:90]!r})")
        try:
            self.click_text("Sign in with Magic Link", exact=True, settle=2.5)
        except BrowserError:
            pass  # some builds land straight on the passwordless form
        filled = self.js("""
        (() => {
          const inputs = [...document.querySelectorAll('input')];
          const el = inputs.find(i => i.type === 'email')
                  || inputs.find(i => /email/i.test(i.placeholder + i.name + i.id))
                  || inputs.find(i => i.type === 'text');
          if (!el) return null;
          const setter = Object.getOwnPropertyDescriptor(
              window.HTMLInputElement.prototype, 'value').set;
          setter.call(el, %s);                        // React-safe value set
          el.dispatchEvent(new Event('input', {bubbles: true}));
          el.dispatchEvent(new Event('change', {bubbles: true}));
          return el.type + ':' + (el.placeholder || el.name || el.id);
        })()
        """ % json.dumps(email), timeout=20)
        if not filled:
            raise BrowserError("no email field on the passwordless page")
        time.sleep(0.6)
        clicked = self.js("""
        (() => {
          const btns = [...document.querySelectorAll('button,[role=button],input[type=submit]')];
          const want = /magic link|send|continue|sign in with email|log in/i;
          const hit = btns.filter(b => want.test((b.innerText || b.value || '').trim()))
                          .filter(b => { const r = b.getBoundingClientRect();
                                         return r.width > 0 && r.height > 0; })[0];
          if (!hit) return null;
          hit.click();
          return (hit.innerText || hit.value || '').trim();
        })()
        """, timeout=20)
        if not clicked:
            raise BrowserError("no submit button on the passwordless page")
        time.sleep(4)
        return {"field": filled, "button": clicked, "page": self.text(200)}

    def open_magic_link(self, url: str, timeout: float = 60.0) -> bool:
        """Consume a magic link in THIS browser. True when it lands signed in."""
        if "furtherai.com" not in url:
            raise BrowserError("that does not look like a FurtherAI sign-in link")
        self.goto(url, settle=4.0, timeout=timeout)
        for _ in range(8):          # the callback bounces through a few redirects
            if self.signed_in():
                return True
            time.sleep(2)
            try:
                self.js("1", timeout=8)
            except BrowserError:
                self._reattach()
        return self.signed_in()

    def session_available(self) -> bool:
        """Is there a live, signed-in browser on our port to attach to?"""
        if not _port_alive(self.port):
            return False
        try:
            self.attach_page()
            if self.signed_in():
                return True
            self.goto(f"{self.app_url}/home", settle=2.0, timeout=45)
            return self.signed_in()
        except BrowserError:
            return False

    def signed_in(self) -> bool:
        try:
            url = self.url()
        except BrowserError:
            return False
        host = urlparse(url).hostname or ""
        return host == self.app_host and "/login" not in url

    def ensure_session(self, start_url: Optional[str] = None, login_timeout: float = 300.0,
                       on_prompt=None) -> str:
        """Attach or launch, then block until a human has signed in.

        Returns 'ready' if the session was already live, 'logged-in' after a wait.
        """
        state = self.launch_or_attach(start_url)
        self.attach_page()
        if self.signed_in():
            return "ready"

        target = start_url or f"{self.app_url}/home"
        try:
            self.goto(target, settle=1.0, timeout=30)
        except BrowserError:
            pass  # an auth redirect during navigation is expected
        if self.signed_in():
            return "ready"

        if self.headless:
            raise BrowserError(
                "a headless browser cannot sign in, and copying a session into one logs the "
                "original out (PropelAuth rotates its refresh token). Use the parked-window "
                "mode: `capture.py session`.")

        if on_prompt:
            on_prompt(state)
        deadline = time.time() + login_timeout
        while time.time() < deadline:
            time.sleep(3)
            try:
                self.attach_page()  # login may land in a different tab
                if self.signed_in():
                    time.sleep(2)
                    return "logged-in"
            except BrowserError:
                continue
        raise BrowserError(
            f"no signed-in session after {login_timeout:.0f}s. Sign in to the Chrome window "
            f"that was opened, then re-run.")


def redact(value: str) -> str:
    """Never print a token or a cookie into a transcript."""
    return re.sub(r"[A-Za-z0-9_\-]{24,}", "<redacted>", value or "")
