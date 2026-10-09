# leftovers

Before Claude says it's done, leftovers checks the lines **Claude added during this session** for
debug and test leftovers:
- `console.log` and `debugger`
- `breakpoint()`, `pdb`, `binding.pry` and `dbg!`
- focused tests (`it.only`, `fdescribe`, `fit`)
- newly skipped or disabled tests
- `NotImplementedError` / `todo!()` stubs
- `var_dump` / `System.out.println`
- merge-conflict markers
- new TODO/FIXME/XXX/HACK comments

Lines that were already there, including your own uncommitted work at session start, are never
flagged. At `SessionStart` it snapshots the working tree as a git tree object, using a throwaway copy of
the index, so your staging area is untouched. At `Stop` it diffs against that snapshot.

When it finds something, it blocks the stop once with the list. Claude then removes each item or says
in its reply why it stays. The same findings never block twice.

**Hooks:** `SessionStart`, `Stop`. **Config:** `{"mode": "block" | "notify", "todos": true, "ignore": ["scripts/*"]}`.
Git repositories only.

## Install

From Anthropic's plugin directory, once listed: search for `leftovers` in `/plugin directory` in Claude Code, or under **Customize > Plugins** on claude.ai.

To try it from a clone for one session: `claude --plugin-dir /path/to/leftovers`.

This plugin is for Claude Code: it is built on hooks, which claude.ai chat does not load.

## Configuration

Settings are read from `~/.claude/leftovers.json` and then `<project>/.claude/leftovers.json`. The project file wins, objects merge by key, and a broken file is skipped and logged.

## What it runs and stores

It is Python 3.9+ standard library only, makes no network requests, and sends nothing anywhere. Every hook fails open: an internal error is logged to `~/.claude/leftovers/errors.log` and the hook exits 0.

- Runs `git` in the project to snapshot the working tree into a git tree object (using a temporary index, so your staging area is untouched) and to diff against it.
- Stores per-session state in `~/.claude/leftovers/sessions/`.

## Development

```
python3 -m pytest -q
claude plugin validate --strict .
```

`docs/HOOK_SCHEMA.md` records the hook payloads this plugin depends on.

## License

MIT. See `LICENSE`.
