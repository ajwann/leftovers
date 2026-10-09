import subprocess

import pytest

from conftest import run_hook
from leftovers.core import handle, scan


def sh(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(project):
    sh(project, "init", "-q")
    (project / "app.ts").write_text("export const a = 1;\nconsole.log('pre-existing');\n")
    (project / "m.py").write_text("def f():\n    return 1\n")
    sh(project, "add", ".")
    sh(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    (project / "wip.py").write_text("x = 1  # TODO: user's own pending work\n")  # dirty before the session
    return project


def ev(project, name, **kw):
    return {"hook_event_name": name, "cwd": str(project), "session_id": "sess-1", **kw}


def test_only_lines_added_this_session_are_flagged(repo):
    handle(ev(repo, "SessionStart", source="startup"))
    (repo / "app.ts").write_text("export const a = 1;\nconsole.log('pre-existing');\nconsole.log('debug', a);\n"
                                 "describe.only('x', () => {});\n")
    (repo / "m.py").write_text("def f():\n    breakpoint()\n    return 1\n")
    (repo / "new_test.py").write_text("import pytest\n@pytest.mark.skip\ndef test_x():\n    pass\n# FIXME later\n")
    out = handle(ev(repo, "Stop", stop_hook_active=False))
    assert out["decision"] == "block"
    r = out["reason"]
    assert "app.ts:3: console.log call" in r and "app.ts:4: focused test (describe.only)" in r
    assert "m.py:2: debugger call" in r and "new_test.py:2: skipped test" in r and "new FIXME" in r
    assert "pre-existing" not in r and "wip.py" not in r


def test_same_findings_block_only_once_and_active_flag_notifies(repo):
    handle(ev(repo, "SessionStart", source="startup"))
    (repo / "m.py").write_text("import pdb\ndef f():\n    return 1\n")
    assert handle(ev(repo, "Stop", stop_hook_active=False))["decision"] == "block"
    assert handle(ev(repo, "Stop", stop_hook_active=False)) is None
    (repo / "x.rs").write_text("fn main() { dbg!(1); }\n")
    out = handle(ev(repo, "Stop", stop_hook_active=True))
    assert "decision" not in out and "dbg! macro" in out["systemMessage"]


def test_clean_session_and_user_index_untouched(repo):
    before = sh(repo, "status", "--porcelain")
    handle(ev(repo, "SessionStart", source="startup"))
    (repo / "m.py").write_text("def f():\n    return 2\n")
    assert handle(ev(repo, "Stop")) is None
    assert sh(repo, "diff", "--cached", "--name-only") == ""
    assert sh(repo, "status", "--porcelain").replace(" M m.py\n", "") == before


def test_resume_keeps_baseline(repo):
    handle(ev(repo, "SessionStart", source="startup"))
    (repo / "m.py").write_text("breakpoint()\n")
    handle(ev(repo, "SessionStart", source="compact"))
    assert handle(ev(repo, "Stop"))["decision"] == "block"


def test_notify_mode_and_ignore(repo):
    (repo / ".claude").mkdir()
    (repo / ".claude" / "leftovers.json").write_text('{"mode": "notify", "ignore": ["scripts/*"]}')
    handle(ev(repo, "SessionStart", source="startup"))
    (repo / "scripts").mkdir()
    (repo / "scripts" / "dev.js").write_text("console.log('ok here')\n")
    (repo / "a.go").write_text("package a\nfunc T(t *testing.T) { t.Skip(\"x\") }\n")
    out = handle(ev(repo, "Stop"))
    assert "decision" not in out and "a.go:2: skipped test" in out["systemMessage"]
    assert "dev.js" not in out["systemMessage"]


def test_not_a_repo_is_silent(project):
    assert handle(ev(project, "SessionStart", source="startup")) is None
    (project / "a.js").write_text("console.log(1)\n")
    assert handle(ev(project, "Stop")) is None


def test_scan_rules_unit():
    cfg = {"todos": False, "ignore": []}
    found = scan([("a.js", 1, "  debugger;"), ("b.md", 1, "console.log(x)"), ("c.rb", 4, "binding.pry"),
                  ("d.java", 2, "@Disabled"), ("e.py", 9, "    raise NotImplementedError"),
                  ("f.ts", 1, "// TODO: x")], cfg)
    assert len(found) == 4 and not any(f.startswith("b.md") or f.startswith("f.ts") for f in found)


def test_script_end_to_end(repo, isolated):
    run_hook(ev(repo, "SessionStart", source="startup"), isolated)
    (repo / "z.py").write_text("import ipdb\n")
    out = run_hook(ev(repo, "Stop", stop_hook_active=False), isolated)
    assert out["decision"] == "block"
