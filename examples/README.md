# Examples

## textkit

A tiny Python project to try [runbook-task-cycle](../skills/runbook-task-cycle) on: a `slugify` function, its tests, an `AGENTS.md`. Standard library only, checks `python3 -m unittest`.

```bash
cp -r textkit /tmp/textkit && cd /tmp/textkit && git init -q && git add -A && git commit -qm init
```

Start a session in `/tmp/textkit` and ask: "Run runbook-task-cycle: add an optional `max_length` to `slugify`, cut on a word boundary; checks `python3 -m unittest`."

## runs

[`runs/20261002-slugify-max-length`](runs/20261002-slugify-max-length) is a real run of that task on the fixture: `progress.md`, `state.json`, every step's output, and the resulting `changes.diff`. Step outputs are numbered by launch, so the directory reads top to bottom: one reviewer found nothing, the other found that `max_length` cut words like `3.14` in the middle, triage confirmed it, the coder fixed it, and the verifier checked the fix, fuzzing included. The run predates the profile and the move of every executor to throng; the steps and their outputs are the same.
