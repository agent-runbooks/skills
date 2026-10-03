# Files of a run

`runbook.py` keeps two files in the run directory. The orchestrator opens neither. Next to them are the input files and the steps' outputs, `<NN>-<name>`, numbered by launch.

## state.json

The machine state: the run's inputs, its status, and one entry per launched section with `id` (`fix`, `fix-2`), `name`, `status` (`running`, `done`, `failed`, `blocked`, `waiting_for_human`), the `reply` as recorded, and a `note` (the human's choice, or why the section was abandoned: `interrupted`, `relaunched on the human's yes`). Since engine 1.1.0 a section also has `executor`, the executor name it was launched with, `null` for a human step; `started_at`, when it was opened; and `ended_at`, when it left `running` or `waiting_for_human`: a reply, an answer, `interrupted`. Times are UTC, `2026-10-03T14:05:12Z`. A `state.json` from an older engine has none of the three. Sections are in launch order. A step that runs again gets a new section with a counter in its id. An abandoned section stays, and the replay skips it.

## progress.md

The inputs, then an append-only log, one line per event:

```markdown
# Run 20261002-t14

Runbook `runbook-task-cycle`. State in `state.json`. Inputs:

- ticket: T14
- repo: /path/to/worktree
- package: packages/core
- coder: main
- maxFixRounds: 2

## Log

- preflight: launched
- preflight: {"status": "done", "clean": true}
- implement: launched
- implement: {"status": "done"}
- checks: launched
- checks: {"status": "done", "passed": false}
- fix-checks: launched
- fix-checks: {"status": "done", "fixed": true}
- checks-2: launched
- checks-2: {"status": "done", "passed": true}
- review-a: launched
- review-b: launched
- review-a: {"status": "done", "findings": 2}
- review-b: {"status": "done", "findings": 1}
- triage: launched
- triage: {"status": "done", "to_fix": 0}
- polish: launched
- polish: {"status": "done", "passed": true}
- end: ready (after step polish)
```

Questions and answers, interruptions, and whatever the orchestrator logs with `flow.py log` appear the same way. The reviewer of a run reads this file next to the steps' output files.
