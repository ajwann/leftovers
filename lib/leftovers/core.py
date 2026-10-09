"""Flag debug/test leftovers among the lines added since the session started."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import hookio

PLUGIN = "leftovers"

DEFAULTS = {
    "mode": "block",        # block (stop once, Claude must fix or justify) | notify (banner only)
    "todos": True,          # also flag new TODO / FIXME / XXX / HACK comments
    "ignore": [],           # project-relative globs never scanned
    "max_findings": 25,
}

JS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte"}
PY = {".py"}
RB = {".rb"}
RS = {".rs"}
GO = {".go"}
JAVA = {".java", ".kt", ".scala"}
PHP = {".php"}
CODE = JS | PY | RB | RS | GO | JAVA | PHP | {".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".swift", ".m", ".sh",
                                               ".lua", ".ex", ".exs", ".dart", ".sql"}

# (extensions, regex, label)
RULES = [
    (JS, r"\bconsole\.(log|debug|trace|dir)\s*\(", "console.{0} call"),
    (JS, r"^\s*debugger\s*;?\s*$", "debugger statement"),
    (JS, r"\b(it|test|describe|context|suite)\.only\s*\(", "focused test ({0}.only)"),
    (JS, r"^\s*(fit|fdescribe|fcontext)\s*\(", "focused test ({0})"),
    (JS, r"\b(it|test|describe)\.skip\s*\(|^\s*(xit|xdescribe|xtest)\s*\(", "skipped test"),
    (PY, r"\bbreakpoint\s*\(\s*\)|\b(i?pdb|pudb)\.set_trace\s*\(|^\s*import\s+(i?pdb|pudb)\b", "debugger call"),
    (PY, r"@pytest\.mark\.(skip|xfail)\b|@unittest\.skip|\bpytest\.skip\s*\(", "skipped test"),
    (PY, r"^\s*raise\s+NotImplementedError\b", "NotImplementedError stub"),
    (RB, r"\bbinding\.(pry|irb)\b|^\s*byebug\b|^\s*debugger\b", "debugger call"),
    (RB, r"\b(fit|fdescribe|fcontext)\s|focus:\s*true", "focused spec"),
    (RS, r"\bdbg!\s*\(", "dbg! macro"),
    (RS, r"\b(todo|unimplemented)!\s*\(", "{0}! stub"),
    (RS, r"#\[ignore\]", "ignored test"),
    (GO, r"\bt\.Skip(Now|f)?\s*\(", "skipped test"),
    (GO, r"\bpanic\(\s*\"(TODO|not implemented|unimplemented)", "panic stub"),
    (JAVA, r"@(Disabled|Ignore)\b", "disabled test"),
    (JAVA, r"\bSystem\.(out|err)\.print(ln)?\s*\(", "System.{0} print"),
    (PHP, r"\b(var_dump|dd|dump|print_r)\s*\(", "{0}() call"),
    (CODE, r"^(<{7}|>{7}|={7})(\s|$)", "merge conflict marker"),
]
TODO_RULE = re.compile(r"(?:#|//|/\*|--|<!--|\*)\s*\b(TODO|FIXME|XXX|HACK)\b")
COMPILED = [(exts, re.compile(rx), label) for exts, rx, label in RULES]


# ---------------------------------------------------------------- git snapshots

def git(project: Path, *args: str, env: Optional[dict] = None, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True,
                       env=env, timeout=25, errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()[:300]}")
    return r.stdout


def repo_root(project: Path) -> Optional[Path]:
    if not shutil.which("git"):
        return None
    try:
        return Path(git(project, "rev-parse", "--show-toplevel").strip())
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return None


def snapshot_tree(root: Path) -> str:
    """Tree object of the working tree (tracked + untracked, honouring .gitignore). Touches nothing visible:
    it uses a throwaway index file seeded from the real one so unchanged files are not re-hashed."""
    gitdir = Path(git(root, "rev-parse", "--absolute-git-dir").strip())
    fd, tmp_index = tempfile.mkstemp(prefix="leftovers-index-")
    os.close(fd)
    try:
        real = gitdir / "index"
        if real.is_file():
            shutil.copyfile(real, tmp_index)
        else:
            os.unlink(tmp_index)
        env = dict(os.environ, GIT_INDEX_FILE=tmp_index, GIT_OPTIONAL_LOCKS="0")
        git(root, "add", "-A", "--ignore-errors", ".", env=env, check=False)
        return git(root, "write-tree", env=env).strip()
    finally:
        try:
            os.unlink(tmp_index)
        except OSError:
            pass


def state_file(session: str) -> Path:
    d = hookio.data_dir(PLUGIN) / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session or "nosession")
    return d / f"{safe}.json"


def added_lines(root: Path, base: str, head: str) -> list[tuple[str, int, str]]:
    """[(path, new_line_no, text)] for every line added between two trees."""
    diff = git(root, "diff", "--no-color", "--no-ext-diff", "-U0", "--diff-filter=AM", base, head)
    out, path, line_no = [], None, 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            line_no = int(m.group(1)) if m else 0
        elif line.startswith("+") and path:
            out.append((path, line_no, line[1:]))
            line_no += 1
    return out


def scan(lines: list[tuple[str, int, str]], cfg: dict) -> list[str]:
    findings = []
    ignore = cfg.get("ignore") or []
    for path, no, text in lines:
        if any(fnmatch.fnmatch(path, g) for g in ignore):
            continue
        ext = Path(path).suffix.lower()
        for exts, rx, label in COMPILED:
            if ext not in exts:
                continue
            m = rx.search(text)
            if m:
                groups = [g for g in m.groups() if g] if m.groups() else []
                findings.append(f"{path}:{no}: {label.format(*(groups or ['']))} — `{text.strip()[:120]}`")
                break
        else:
            if cfg.get("todos", True) and ext in CODE and TODO_RULE.search(text):
                findings.append(f"{path}:{no}: new {TODO_RULE.search(text).group(1)} — `{text.strip()[:120]}`")
    return findings


# ---------------------------------------------------------------- hook

def handle(event: dict) -> Optional[dict]:
    name = event.get("hook_event_name")
    project = hookio.project_dir(event)
    session = event.get("session_id") or ""
    cfg = hookio.load_config(PLUGIN, project, DEFAULTS)
    if name == "SessionStart":
        sf = state_file(session)
        if sf.exists() and event.get("source") in ("resume", "compact"):
            return None  # keep the original baseline
        root = repo_root(project)
        if root:
            sf.write_text(json.dumps({"root": str(root), "tree": snapshot_tree(root), "reported": []}))
        return None
    if name != "Stop":
        return None
    sf = state_file(session)
    if not sf.exists():
        return None
    state = json.loads(sf.read_text())
    root = Path(state["root"])
    findings = scan(added_lines(root, state["tree"], snapshot_tree(root)), cfg)
    if not findings:
        return None
    digest = hashlib.sha1("\n".join(findings).encode()).hexdigest()
    if digest in state.get("reported", []):
        return None  # already raised these exact findings once this session
    state.setdefault("reported", []).append(digest)
    sf.write_text(json.dumps(state))
    shown = findings[: cfg["max_findings"]]
    more = len(findings) - len(shown)
    body = "\n".join("- " + f for f in shown) + (f"\n- …and {more} more" if more > 0 else "")
    if cfg.get("mode") == "notify" or event.get("stop_hook_active"):
        return hookio.with_system_message(None, "leftovers: lines added this session that look like debug/test "
                                                f"leftovers:\n{body}")
    return hookio.block_stop(
        "leftovers: these lines were added during this session and look like debug or test leftovers:\n"
        f"{body}\nEach one is either removed now or kept on purpose; if kept, the final reply says why.")
