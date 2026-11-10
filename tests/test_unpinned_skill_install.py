"""Skill install commands must not run code from an untrusted release.

The Documents and Presentation skills tell an agent to install paper-docx and
paper-pptx when the import is missing. These tests follow that procedure in a
fresh environment. They fail while a new release, a replaced documented
release, or a command taken from the live package README can run.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import shlex
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

REPO = Path(__file__).parents[1]
README = REPO / "README.md"
SKILLS = (
    REPO / "skills-backlog" / "docx" / "SKILL.md",
    REPO / "skills-backlog" / "pptx" / "SKILL.md",
)
@dataclass(frozen=True)
class SkillInstall:
    """Install procedure an agent is told to follow."""

    path: Path
    text: str
    project: str
    version: str
    import_name: str
    imported_symbols: tuple[str, ...]
    modules: tuple[str, ...]
    show_commands: tuple[str, ...]
    commands: tuple[str, ...]
    remote_urls: tuple[str, ...]


def _documented_version(project: str) -> str:
    match = re.search(rf"{re.escape(project)}==([0-9][^`\s]*)", README.read_text(encoding="utf-8"))
    if match is None:
        raise AssertionError(f"{README} does not document a release of {project}")
    return match.group(1)


def _fenced_commands(text: str) -> list[str]:
    commands = []
    for block in re.findall(r"```(?:bash|sh|shell)\n(.*?)```", text, re.S):
        for line in block.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                commands.append(stripped)
    return commands


def _remote_install_urls(text: str) -> list[str]:
    """URLs whose install commands the skill tells the agent to follow."""
    urls: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        if not re.search(r"\bfollow\b", paragraph, re.I):
            continue
        if not re.search(r"\binstall\b", paragraph, re.I):
            continue
        for label, url in re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", paragraph):
            if re.search(r"readme|installation", f"{label} {url}", re.I) and url not in urls:
                urls.append(url)
    return urls


def _local_command_files(text: str, skill_dir: Path) -> list[Path]:
    """Repository files the skill tells the agent to follow for install commands."""
    files: list[Path] = []
    for paragraph in re.split(r"\n\s*\n", text):
        if not re.search(r"\bfollow\b", paragraph, re.I):
            continue
        if not re.search(r"\binstall\b", paragraph, re.I):
            continue
        for _label, url in re.findall(r"\[([^\]]+)\]\(([^)\s]+)\)", paragraph):
            if re.match(r"https?://", url):
                continue
            path = (skill_dir / url).resolve()
            if path.is_file() and path not in files:
                files.append(path)
    return files


def load_skill(path: Path) -> SkillInstall:
    text = path.read_text(encoding="utf-8")
    show_commands = tuple(dict.fromkeys(re.findall(r"python -m pip show ([A-Za-z0-9_.-]+)", text)))
    projects = list(show_commands)
    if not projects:
        projects = re.findall(r"pip install(?:\s+\S+)*\s+[\"']?([A-Za-z0-9_.-]+)", text)
    if not projects:
        raise AssertionError(f"{path} does not name a package to install")
    project = projects[0]
    show_commands = tuple(f"python -m pip show {name}" for name in show_commands)
    commands = _fenced_commands(text)
    for extra in _local_command_files(text, path.parent):
        commands.extend(_fenced_commands(extra.read_text(encoding="utf-8")))
    imports = re.findall(r"provides the `([A-Za-z0-9_]+)` import", text)
    if not imports:
        raise AssertionError(f"{path} does not name the import its package provides")
    symbols = tuple(
        symbol.strip()
        for match in re.findall(rf"from {imports[0]} import ([A-Za-z0-9_, ]+)", text)
        for symbol in match.split(",")
        if symbol.strip()
    )
    modules = tuple(
        dict.fromkeys(re.findall(r"python -m (?!pip\b)([A-Za-z0-9_]+)", text))
    )
    return SkillInstall(
        path=path,
        text=text,
        project=project,
        version=_documented_version(project),
        import_name=imports[0],
        imported_symbols=symbols or ("Document",),
        modules=modules,
        show_commands=show_commands,
        commands=tuple(commands),
        remote_urls=tuple(_remote_install_urls(text)),
    )


def _record_digest(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
    return f"sha256={digest}"


def build_wheel(
    directory: Path,
    project: str,
    version: str,
    import_name: str,
    symbols: tuple[str, ...],
    modules: tuple[str, ...],
    marker: Path | None,
) -> Path:
    """Build a pure wheel. A marker path makes import and startup write that file."""
    distribution = project.replace("-", "_")
    info = f"{distribution}-{version}.dist-info"
    payload = ""
    if marker is not None:
        payload = f"import pathlib; pathlib.Path({str(marker)!r}).write_text('executed\\n')\n"
    classes = "\n".join(
        f"class {symbol}:\n    def __init__(self, *args, **kwargs):\n        pass\n" for symbol in symbols
    )
    files = {
        f"{import_name}/__init__.py": (payload + classes).encode(),
        f"{info}/METADATA": (
            f"Metadata-Version: 2.1\nName: {project}\nVersion: {version}\n"
        ).encode(),
        f"{info}/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ).encode(),
    }
    for module in modules:
        files[f"{module}.py"] = payload.encode() or b"\n"
    if marker is not None:
        # site.py executes .pth lines that start with "import ".
        files[f"_{distribution}_install.pth"] = payload.encode()

    record_lines = [f"{name},{_record_digest(data)},{len(data)}" for name, data in sorted(files.items())]
    record_lines.append(f"{info}/RECORD,,")
    files[f"{info}/RECORD"] = ("\n".join(record_lines) + "\n").encode()

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{distribution}-{version}-py3-none-any.whl"
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return path


class PackageIndex:
    """PEP 503 index served from disk. Rewriting a page is visible to the next pip."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.packages = root / "packages"
        self.simple = root / "simple"
        self.packages.mkdir(parents=True)
        self.simple.mkdir(parents=True)
        self._pages: dict[str, list[str]] = {}
        handler = lambda *args, **kwargs: SimpleHTTPRequestHandler(  # noqa: E731
            *args, directory=str(root), **kwargs
        )
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}/simple/"

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def publish(self, project: str, wheel: Path, *, replace: bool = False) -> None:
        target = self.packages / wheel.name
        target.write_bytes(wheel.read_bytes())
        names = [] if replace else list(self._pages.get(project, []))
        if wheel.name not in names:
            names.append(wheel.name)
        if replace:
            names = [wheel.name]
        self._pages[project] = names
        page = self.simple / project
        page.mkdir(parents=True, exist_ok=True)
        links = "\n".join(
            f'<a href="../../packages/{name}">{name}</a>' for name in names
        )
        (page / "index.html").write_text(
            "<!DOCTYPE html><html><body>\n" + links + "\n</body></html>\n",
            encoding="utf-8",
        )


def _publisher_readme(marker: Path) -> str:
    """Install commands a package publisher can put on the live README."""
    code = f"from pathlib import Path; Path({str(marker)!r}).write_text('readme\\n')"
    return "## Installation\n\n```bash\npython -c " + shlex.quote(code) + "\n```\n"


def _run(command: str, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", command],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )


def _environment(venv: Path, index_url: str) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PATH"] = str(venv / "bin") + os.pathsep + env.get("PATH", "")
    env["VIRTUAL_ENV"] = str(venv)
    env["PIP_INDEX_URL"] = index_url
    env["PIP_TRUSTED_HOST"] = "127.0.0.1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_CACHE_DIR"] = "1"
    env["PIP_NO_INPUT"] = "1"
    env["PIP_CONFIG_FILE"] = os.devnull
    return env


def _installed_version(python_env: dict[str, str], project: str, cwd: Path) -> str | None:
    result = _run(f"python -m pip show {shlex.quote(project)}", python_env, cwd)
    if result.returncode != 0:
        return None
    match = re.search(r"^Version: (.+)$", result.stdout, re.M)
    return match.group(1).strip() if match else None


def follow_install(
    skill: SkillInstall,
    root: Path,
    *,
    on_after_check: Callable[[PackageIndex, Path, Path, SkillInstall], None],
    follow_remote: bool,
) -> tuple[Path, str | None, str]:
    """Run the skill's check, then its install commands, then import the package.

    `on_after_check` runs after the absence check and before install, so a
    release published in that gap is what an unpinned install resolves.
    """
    venv = root / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, timeout=120)
    index = PackageIndex(root / "index")
    marker = root / "code-executed"
    log: list[str] = []
    try:
        benign = build_wheel(
            root / "benign",
            skill.project,
            skill.version,
            skill.import_name,
            skill.imported_symbols,
            skill.modules,
            marker=None,
        )
        index.publish(skill.project, benign)
        env = _environment(venv, index.url)
        for command in skill.show_commands:
            result = _run(command, env, root)
            log.append(f"$ {command}\n{result.stdout}{result.stderr}")
            if result.returncode == 0:
                raise AssertionError(f"package was already installed before the skill install:\n{log[-1]}")
        on_after_check(index, marker, root, skill)
        commands = list(skill.commands)
        if follow_remote:
            for url in skill.remote_urls:
                # The skill points this URL at the live README. The body below is
                # that document after its publisher changes the install commands.
                log.append(f"$ fetch {url}")
                commands = _fenced_commands(_publisher_readme(marker)) + commands
        for command in commands:
            result = _run(command, env, root)
            log.append(f"$ {command}\n{result.stdout}{result.stderr}")
        if _installed_version(env, skill.project, root) is not None:
            import_list = ", ".join(skill.imported_symbols)
            command = f"python -c {shlex.quote(f'from {skill.import_name} import {import_list}')}"
            result = _run(command, env, root)
            log.append(f"$ {command}\n{result.stdout}{result.stderr}")
        version = _installed_version(env, skill.project, root)
        return marker, version, "\n".join(log)
    finally:
        index.close()


def _assert_no_execution(marker: Path, transcript: str) -> None:
    if marker.exists():
        raise AssertionError(
            "install executed code from a release or README the repository does not pin:\n"
            + transcript
        )


@pytest.fixture(params=SKILLS, ids=lambda path: path.parent.name)
def skill(request: pytest.FixtureRequest) -> SkillInstall:
    return load_skill(request.param)


def test_every_pip_install_instruction_is_covered() -> None:
    """Every file that tells an agent to pip install is one of the skills under test."""
    covered = {path.resolve() for path in SKILLS}
    found: set[Path] = set()
    for path in REPO.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".md", ".py", ".sh", ".toml", ".txt"}:
            continue
        if path.parts[-2:] == ("tests", path.name) and path.name.startswith("test_"):
            continue
        if ".git" in path.parts or ".venv" in path.parts:
            continue
        if "pip install" in path.read_text(encoding="utf-8", errors="ignore"):
            found.add(path.resolve())
    assert found == covered


def test_current_release_does_not_run_when_package_is_absent(skill: SkillInstall, tmp_path: Path) -> None:
    """A release published after the absence check must not execute."""

    def publish_current_release(index: PackageIndex, marker: Path, root: Path, item: SkillInstall) -> None:
        hostile = build_wheel(
            root / "current",
            item.project,
            "9.9.9",
            item.import_name,
            item.imported_symbols,
            item.modules,
            marker,
        )
        index.publish(item.project, hostile)

    marker, version, transcript = follow_install(
        skill, tmp_path, on_after_check=publish_current_release, follow_remote=False,
    )
    _assert_no_execution(marker, transcript)
    if version is not None:
        assert version == skill.version, transcript


def test_replaced_documented_release_does_not_run_without_a_hash(
    skill: SkillInstall, tmp_path: Path,
) -> None:
    """The documented version's file is swapped after the check. Only a hash stops it."""

    def replace_documented_release(
        index: PackageIndex, marker: Path, root: Path, item: SkillInstall,
    ) -> None:
        hostile = build_wheel(
            root / "replaced",
            item.project,
            item.version,
            item.import_name,
            item.imported_symbols,
            item.modules,
            marker,
        )
        index.publish(item.project, hostile, replace=True)

    marker, _version, transcript = follow_install(
        skill, tmp_path, on_after_check=replace_documented_release, follow_remote=False,
    )
    _assert_no_execution(marker, transcript)


def test_live_readme_install_commands_do_not_run(skill: SkillInstall, tmp_path: Path) -> None:
    """Commands from the README URL must not run when the package is absent."""
    marker, _version, transcript = follow_install(
        skill,
        tmp_path,
        on_after_check=lambda *_args: None,
        follow_remote=True,
    )
    _assert_no_execution(marker, transcript)
