# Changelog

Versions of `references/runbook.py`. A major version changes the `flow.py` API and says what to change in an existing `flow.py`. Each release is tagged `agent-runbook-authoring/v<version>`.

## 1.1.1

- The source link in the docstring points to `agent-runbooks/skills`, where the engine now lives. No change in behavior.

## 1.1.0

- A section in `state.json` records `executor`, the executor name it was launched with (`null` for a human step), `started_at` and `ended_at`, UTC as `2026-10-03T14:05:12Z`. A `state.json` from 1.0.0 loads and resumes; its sections read the three fields as `null`. No change to `flow.py` or `progress.md`.

## 1.0.0

First public release.

- `rb.inputs`, `rb.executor`, `rb.start`, `rb.step`, `rb.human`; targets `end(...)` and `parallel(...)`.
- `skip` on a step, `s.done(step)` and `s.reply(step)` in conditions.
- Step outputs are numbered by launch, `<run>/<NN>-<name>`: `writes` and `reads` name the files, and the launch message gives the paths. `<run>/<name>` in an `end` report or a question becomes the latest such file.
- A `done` reply is checked against the step's `reply` fields: a missing field or a wrong type records it as `failed`.
- Commands `start`, `reply`, `answer`, `interrupted`, `relaunch`, `log`, status, `--check`.
