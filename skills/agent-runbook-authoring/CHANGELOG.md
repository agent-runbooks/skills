# Changelog

Versions of `references/runbook.py`. A major version changes the `flow.py` API and says what to change in an existing `flow.py`. Each release is tagged `agent-runbook-authoring/v<version>`.

## 2.0.0

The flow of a runbook is one Python generator, and the engine replays it against the recorded replies on every command. Steps stay declarative. An existing `flow.py` is rewritten, and a run started by 1.x does not load. The design is in [`docs/engine-2.0-design.md`](https://github.com/agent-runbooks/skills/blob/main/docs/engine-2.0-design.md) of the repository.

What to change in an existing `flow.py`:

- Delete `next`, `after`, `skip` and `on_failure` from every step, and `rb.start(...)`. Keep what `rb.step` and `rb.human` return, and write the routing as a generator function `main(ctx)` under `@rb.flow`: `r = yield review()` launches the step and returns its reply's fields as `r.<field>`, a human step's answer as `a.choice`. `ctx.inputs.<name>` are the run's inputs.
- `end(...)` is returned, not routed to. `parallel(...)` and `foreach(...)` are yielded, and `then` is the code after the `yield`. `parallel` takes the group's name first and the branches as keywords, each a step call or a generator function `(ctx)`: `yield parallel('reviews', a=review_a(), b=chain)`. `pipeline(...)` is `foreach(...)`, `item_done(...)` is `return`, `item_failed(...)` is `raise StepFailed(...)`.
- `on_failure` is `try`/`except StepFailed` around the `yield`. A failure no generator handles fails the run, as before.
- `s.done(name)`, `s.failed(name)`, `s.reply(name)` and `s.replies(name)` are local variables of the generator. `executor=lambda s: ...` is `step(executor=...)` at the call.
- A step after a group reads the group's files through an explicit pass, `yield triage(reviews=reviews)`, and its prompt names them as `reviews.a/review-a.md`. Its `reads` no longer see files written inside the group.
- Step names, group names and branch keys are letters, digits, `_ . -`, since they are parts of an address. A reply field named `id`, `files`, `status`, `reason` or `choice` is refused at declaration: those are the engine's attributes on a result. So is a reply field that is neither a JSON type nor a schema, which 1.x left to `--check`. `executor` of a step is a declared name only.

What changes for the orchestrator: commands, texts and the progress log keep their 1.x shape, with a call's address in place of a section id. An address is a path, `main/review`, `main/review#2` for the second call of `review` in the same generator, `main/reviews/a` for a branch, `main/migrate[auth]/verify` inside an item, and `main/review@2` for the second attempt of a call; printed commands quote it for the shell. The end line says ``after `<address>` `` where 1.x said ``after step `<id>` ``.

- Replay: every command loads `state.json`, runs `main(ctx)` from the top, and looks each yield up by its address: a done call returns its result, a failed or blocked one throws `StepFailed` with `.id` and `.reply`, an open one waits, and a call with no record is launched. A record whose kind or step differs from what the flow now yields at its address stops the command: start a new run. A defect in the generator leaves what the command recorded, and the run goes on once `flow.py` is fixed. A group or an item is driven in the replay that opens it, so a command takes one replay, plus at most one that finds nothing to do, however many groups and items it opens and closes.
- Groups nest freely. A branch or item that does not handle a `StepFailed` fails its group: nothing new is launched in it, the launches already running are waited for, and the group throws the failure into the generator that yielded it. A cut chain is `cancelled` in the parallel's record, a cut item `cancelled` and an item that never started `not_started` in the foreach's index, and a question asked inside the group is closed as `cancelled`, with a line telling the orchestrator to withdraw it. The question whether to relaunch a side-effect step in the group gets the same line, and the step's record is noted `cancelled with its group`.
- `foreach('name', over=, body=, max_concurrent=4, max_items=50, on_item_failure='fail')` runs the body, a generator function `(ctx, item)` or one step call, for each item of a JSON array; the launch message of a step inside an item carries the fields of the innermost item. The result has `items` with `key`, `status`, `reply`, `reason` and `files` each, and `<run>/<NN>-<name>.index.json` holds the same, written when the foreach ends, done or failed. A bad `over` file fails the foreach with the reason; bad parameters are a defect of `flow.py`.
- Files: `reads` resolve in the calling generator's own history, then its ancestors', never a sibling's or a child's. What a launch is told is recorded with it as `files_in`, so the order in which branches reply never changes it. A foreach's index is in its caller's history as `<name>.index`, also for `<run>/<name>.index` in an end report or a question.
- Attempts: `interrupted` closes the attempt and opens the next one at once, `<address>@2`, except for a step with `side_effects`, which now asks the human as a failed one does; 1.x relaunched it. `reply`, `answer`, `interrupted` and `relaunch` take the latest attempt only and name it when given another one, so a late reply to an interrupted attempt is refused. `relaunch` takes only a call the run is asking the human about, never one in a group that failed.
- A reply is checked against the contract recorded with its attempt: the reply schema written for it, its executor, inputs and files. The schema file is per attempt, `<run>/schemas/<NN>-<step>.json`, so a later launch of the step under a changed declaration does not rewrite the file a running executor fits.
- `state.json` is format 2, a list of `calls`, each attempt of a call one record: steps and human steps, and the records of groups and items, which take positions in the file numbering too. An engine reading another format refuses the run. `start` refuses a directory that holds a `state.json`; an existing directory without one is used.
- `--check` checks declarations only: executors, prompts, inputs, questions, `repo`, `prompts/common.md`, and that a flow is declared; `@rb.flow` refuses a function that is not a generator. Routes are walked by hand with `start` and `reply`.

## 1.4.3

No change is required in an existing `flow.py` or for the orchestrator.

- `Runbook.main` returns 2 on a refused command instead of exiting the process. This affects callers in the same process. The engine raises `CommandError` for refusals. `FlowError` still identifies defects in flow functions. `main` prints their existing messages. Shell commands still exit 2. Commands save what they recorded as before.

## 1.4.2

An existing `flow.py` needs no change unless it declares a step twice, uses another step's name followed by `-<digits>`, or passes a lone string to a collection parameter. These mistakes now stop it at import with a message naming the step. Collection errors also name the parameter.

- `interrupted` used to supersede a section in any status, so a late command could repeat a completed external effect. It now accepts only `running`. `relaunch` accepts only `failed` or `blocked` sections. Their step must declare `side_effects`, and the section must not already be superseded. A refused command leaves state and progress unchanged.
- A step named `work-2` could collide with the second launch of `work`, leaving the run stuck. Both `rb.step` and `rb.human` refuse duplicate names and colliding names in either declaration order. Collection parameters refuse lone strings instead of splitting them into characters. The public declaration API now types these collections as iterables.
- Printed commands now quote the interpreter, `flow.py`, run directory and section ids for the shell. Paths with spaces no longer split into several arguments. Placeholder arguments keep their existing quoting.
- State is serialized before any file is opened, then written to `state.json.tmp` and replaced with `os.replace`. A failed serialization or an interrupted write no longer truncates the previous state.
- The flow language now says which edits to `flow.py` are allowed during a run.

## 1.4.1

No change is required in an existing `flow.py`.

- Fixed: a run past about 330 sections in a row of one loop could not be replayed, and every command replays the whole history. The command failed with `` `next` of step `<name>` raised RecursionError `` and called it a defect in `flow.py`. The replay walks the flow on its own stack now, and indexes the sections by step: a replay of 20,000 sections takes about 30 ms, where 5,000 took 260 ms before.
- A `skip` that leads back to its own step with no section recorded and no join made on the way, as `a` skipping to `b` and `b` back to `a`, would go round forever. It stops the command: ``flow.py: `skip` goes round in a circle with nothing to launch: `a` -> `b` -> `a` ``. Until 1.4.1 it ended in the same RecursionError. The check relies on what `references/flow-language.md` now says outright: a flow function answers from its arguments alone, since every command calls it again.

## 1.4.0

No change is required in an existing `flow.py`.

- Runs on Python 3.9, the `/usr/bin/python3` of macOS without Homebrew; until 1.4.0 it needed 3.10. On an older Python, importing the engine exits with a message that names the interpreter and its version.

## 1.3.0

No change is required in an existing `flow.py`. A runbook that takes this engine also takes the new Execution rules from `references/template.md`.

- A reply that does not pass the check goes back to its executor once before the step fails: not one JSON object, no `status` or an unknown one, or a `done` reply without a declared field or with a wrong `type`. `reply` leaves the section running and prints a correction for the orchestrator to send into the executor's session: what was wrong, the path of the reply schema, and that the step's work is not redone. The answer goes to the same `reply` command. A second such reply is recorded as `failed`, `invalid reply: <what is wrong>`, and goes to `on_failure` as before. An orchestrator that cannot send a message to a finished subagent runs the `failed` reply `flow.py` prints for that case. The Execution rules say how, and that a missing JSON object is passed as `{}`.
- A section in `state.json` records `invalid_replies`: each reply that did not pass the check, as passed, with what was wrong. Until 1.3.0 such a reply was replaced by the failure before it was recorded. A `state.json` from 1.2.0 loads and resumes. `progress.md` has a line for each.
- `s.failed(step)`: how many sections of a step ended `failed` or `blocked`, interrupted and relaunched ones aside. A loop through `on_failure` back to the same step had no budget it could count: `s.done` grows only on `done`.

## 1.2.0

No change is required in an existing `flow.py`. A runbook that takes this engine also takes the new Execution rules and Executor constraints from `references/template.md`, and drops the `## Reply schema` section from its prompts.

- Fixed: a step with `after` behind a `parallel(...)` that the flow reaches a second time launched as soon as one branch of the new round was done, with the other branch's file from the round before, and launched again when that branch finished. A step with `after` now waits for sections it has not joined on yet. When a loop runs only some of its `after` steps again, it takes the earlier section of the others once nothing else is running, as before.
- The engine writes the JSON Schema of a step's reply to `<run>/schemas/<step>.json` from the step's `reply`, and the launch message carries `reply schema: <path>`. Prompts no longer hold a schema. The schema is one closed object with every property required and the unused ones null, the form harnesses that enforce a schema accept; a `null` `reason` of a `done` reply and the `null` fields of a `failed` or `blocked` one are dropped before the reply is recorded, and a `failed` or `blocked` reply without a `reason` gets `no reason given`.
- A `reply` field may be a JSON Schema, `{'type': 'integer', 'minimum': 0, 'description': '…'}`, in place of a type, and `list` and `dict` are types too. The engine still checks the `type` only. Type names in an `invalid reply` reason are JSON's: `boolean`, `integer`.
- `rb.human(reply={...})`: fields the orchestrator takes from the human's words. `answer` then takes a JSON object with `choice` and the fields, and `next` gets them as `a.choice`, `a.<field>`. `--check` refuses a `reply` field named `status`, `reason` or `choice`, and one that is neither a JSON type nor a schema.
- Only one argument of a command can be `-`.
- A `next`, `on_failure`, `skip` or `executor` function that raises no longer leaves a reply in `progress.md` and not in `state.json`: what the command recorded is saved first, the engine names the function, and `flow.py <run>` goes on once `flow.py` is fixed.
- `s.replies(step)`: the replies of every `done` section of a step, for a budget the human extends.
- Output: step names are in backticks; a launch reads ``launch step `fix` as a new subagent, executor `coder`: …``; several launches are announced by `N steps to launch together, in one turn:` and set apart by empty lines. The `end:` line of `progress.md` has the step name in backticks.

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
