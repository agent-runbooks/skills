# Files of a run

`agent_runbooks.py` keeps two files in the run directory. The orchestrator opens neither. It also writes `schemas/<NN>-<step>.json`, the JSON Schema of each launch's reply, and `<NN>-<name>.index.json`, the index of each foreach that ended. Next to them are the input files and the steps' outputs, `<NN>-<name>`, numbered by the record that wrote them.

## state.json

The machine state, `"format": 2`: the run's inputs, its status (`running`, `waiting_for_human` or the end status), and `calls`, one record per attempt of a call in the order they were opened. `NN` in a file name is the record's place in `calls`.

Every record has `id`, the call's address (`main/triage#2`, `main/reviews/a`, `main/fixes[f1]/fix`); `attempt`, 1, then 2 for the attempt an interruption or a relaunch opened; `kind`, one of `step`, `human`, `parallel`, `foreach`, `item`; `step`, the step's or the group's name; `status`, one of `running`, `waiting` (a human step), `done`, `failed`, `blocked`, `cancelled`, `interrupted`; `started_at`, when it was opened, and `ended_at`, when it left `running` or `waiting`. Times are UTC, `2026-10-03T14:05:12Z`. A closed attempt stays in the list with its status, and only the latest attempt of an address is open.

A step or a human step also holds the contract its executor was given and what came back: `executor`, `null` for a human step; `inputs`, the lines the call added; `files_in`, each name it was told to read with its path, `null` for absent; `writes` and `schema`; `reply` as recorded, with `choice` and the step's `reply` fields for a human step; `answer`, the human's words; `files`, what a done launch wrote; `note`, `relaunched on the human's yes` on an attempt the human let go again. A step has `invalid_replies` too: each reply that did not pass the check, as the orchestrator passed it, with what was wrong, `{"reply": "{\"status\": \"done\"}", "problem": "no field 'passed'"}`.

A parallel has `branches`, how each ended: `done`, `failed` or `cancelled`. A foreach has `over`, the path of the file it read; `items`, as they were when it was reached; `index`, the path of its index; and `problem`, why the file could not be used. An item has `fields`, the item's object, and its outcome as `reply`, `reason` and `files`.

## progress.md

The inputs, then an append-only log, one line per event, for a run of the illustration in [`flow-language.md`](flow-language.md):

```markdown
# Run 20261007-add-version-constant

Runbook `runbook-review-fix`. State in `state.json`. Inputs:

- brief: brief.md
- repo: /path/to/repo
- coder: main
- maxFixRounds: 1

## Log

- main/implement: launched
- main/implement: {"status": "done"}
- main/reviews/a: launched
- main/reviews/b/review: launched
- main/reviews/a: {"status": "done", "findings": 2}
- main/reviews/b/review: {"status": "blocked", "reason": "no other vendor here"}
- main/reviews/b/review#2: launched
- main/reviews/b/review#2: {"status": "done", "findings": 1}
- main/triage: launched
- main/triage: invalid reply (field 'to_fix' must be integer, got "two"): {"status": "done", "to_fix": "two"}
- main/triage: its executor is asked to correct the reply
- main/triage: {"status": "done", "to_fix": 2}
- main/fixes[f1]/fix: launched
- main/fixes[f1]/fix: interrupted
- main/fixes[f1]/fix@2: launched
- main/fixes[f1]/fix@2: {"status": "done"}
- main/fixes[f2]/fix: launched
- main/fixes[f2]/fix: {"status": "failed", "reason": "the test needs a fixture this repo lacks"}
- main/reviews#2/a: launched
- main/reviews#2/b/review: launched
- main/reviews#2/a: {"status": "done", "findings": 1}
- main/reviews#2/b/review: {"status": "done", "findings": 0}
- main/triage#2: launched
- main/triage#2: {"status": "done", "to_fix": 1}
- main/ask-rounds: asked: Fix rounds are spent, see `/path/to/repo/.agent-runbooks/runs/20261007-add-version-constant/15-triage.md`. More rounds, and how many, or stop here?
- main/ask-rounds: answered: more rounds {"rounds": 1}
- main/ask-rounds: said: one more, and fix the typo only
- main/fixes#2[f3]/fix: launched
- main/fixes#2[f3]/fix: {"status": "done"}
- main/reviews#3/a: launched
- main/reviews#3/b/review: launched
- main/reviews#3/a: {"status": "done", "findings": 0}
- main/reviews#3/b/review: {"status": "done", "findings": 0}
- end: ready (after `main/reviews#3`)
```

Groups and items have no lines of their own: their calls do. A side-effect step the human lets go again adds `<call>: relaunched on the human's yes`, a question withdrawn by a failing group `<call>: cancelled`, and a failure no generator handles ends the log with ``end: failed (`<call>` <status>: <reason>)``. Whatever the orchestrator logs with `flow.py log` appears as `orchestrator: <text>`. The reviewer of a run reads this file next to the steps' output files.
