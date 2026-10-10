# Engine 2.0: the flow as a generator

Design note for `runbook.py` 2.0.0. It replaces the flow language of 1.x: the steps stay declarative, the flow becomes one Python generator, and the engine replays that generator against the recorded replies on every command. The orchestrator's side, the Execution rules of a `SKILL.md`, keeps its commands and messages; what the author writes in `flow.py` changes entirely.

## Why

In 1.x a flow is a flat graph of step names: `next` is a goto, a `parallel(...)` is joined by a step with `after=(...)` that repeats the branch names, a loop is a step name reached again with `s.done(name) < budget` as its counter, and a pipeline is a scope patched onto the graph. The engine reconstructs the structure from the names, so the join must guess which section of a branch belongs to which round, `--check` carries a dozen rules about where a literal target may sit, and a step is tied to one place in the graph. The join written twice, `next='join'` on each branch and `after=('a', 'b')` on the join, is the visible symptom.

In 2.0 the unit is a call: a step is launched, ends, and returns its reply to the code that launched it. Branches know nothing of a join; the caller does. The control flow is Python's own: `if`, `while`, `return`, `try`/`except`, local counters.

## The author's API

```python
#!/usr/bin/env python3
"""Steps and the flow of runbook-review-loop. Run with --help for the commands."""
from runbook import Runbook, StepFailed, end, foreach, parallel

rb = Runbook()
rb.inputs(brief=str, repo=str, checks='', maxFixRounds=2)
rb.executor('coder', 'general-purpose, on the model of the main session')
rb.executor('reviewer', 'general-purpose, on the model of the main session')

implement = rb.step('implement', executor='coder', prompt='prompts/01-implement.md',
                    inputs=['checks'], reads=['brief.md', 'working tree'], writes=['implement.md'])
review = rb.step('review', executor='reviewer', prompt='prompts/02-review.md',
                 inputs=['checks'], reads=['brief.md', 'implement.md', 'fix.md', 'review.md', 'working tree'],
                 writes=['review.md'], reply={'findings': {'type': 'integer', 'description': 'findings still open'}})
fix = rb.step('fix', executor='coder', prompt='prompts/03-fix.md',
              inputs=['checks'], reads=['brief.md', 'review.md', 'rounds.md', 'working tree'], writes=['fix.md'])
ask_rounds = rb.human('ask-rounds', writes='rounds.md', choices=['more rounds', 'stop'],
                      reply={'rounds': {'type': 'integer', 'default': 1, 'description': 'how many more'}},
                      question='Fix rounds are spent and `<run>/review.md` still lists findings. More, or stop?')


@rb.flow
def main(ctx):
    yield implement()
    budget, used = ctx.inputs.maxFixRounds, 0
    while True:
        r = yield review()
        if r.findings == 0:
            return end('ready', 'read <run>/review.md')
        if used >= budget:
            a = yield ask_rounds()
            if a.choice == 'stop':
                return end('needs_attention', 'read <run>/review.md')
            budget += a.rounds
        yield fix()
        used += 1


if __name__ == '__main__':
    raise SystemExit(rb.main())
```

The whole language: `rb.inputs`, `rb.executor`, `rb.step`, `rb.human`, `@rb.flow`, `yield`, `parallel`, `foreach`, `StepFailed`, `end`. Nothing else.

### Declarations

`rb.inputs(...)`, `rb.executor(name, description)`, `rb.step(name, executor=, prompt=, inputs=, reads=, writes=, reply=, side_effects=)` and `rb.human(name, question=, choices=, reply=, writes=)` keep their 1.x meaning and checks, except that a step has no `next`, `after`, `skip` or `on_failure`, and `executor` is a declared name only. `rb.step` and `rb.human` return the step, which the flow calls. There is no `rb.start`: the flow begins where the generator begins.

A declaration mistake is refused at declaration, naming the step and the parameter, as AGENTS.md says. A reply field or a branch key named `id`, `files`, `status`, `reason` or `choice` is refused: those are the engine's attributes on a result.

### The flow

`@rb.flow` marks the generator function `main(ctx)`. `ctx.inputs.<name>` are the run's inputs. The function is called afresh on every command and must depend on its arguments and on what `yield` returns only: no clock, no file reads, no module-level mutable state. Local variables are fine; they are recomputed.

- `yield step(...)` launches the step and returns its result once the reply is `done`. The result has the reply's fields as attributes (`r.findings`), `choice` for a human step, plus `id` (the call's address) and `files` (name to path, what the launch wrote).
- A `failed` or `blocked` reply is thrown into the generator at that `yield` as `StepFailed`, with `.id` and `.reply` (`status`, `reason`, and any fields). `try`/`except StepFailed` is the 1.x `on_failure`. An unhandled `StepFailed` fails the enclosing group's branch or item, or the run.
- `return end(status, report)` ends the run. `report` is what the orchestrator tells the human, with `<run>/<file>` replaced by the latest file of that name in the flow's scope and `<run>/<foreach>.index` by that foreach's latest index. Returning anything else from `main` is a `FlowError`.
- Keyword arguments of a call are inputs of the launch. `executor=` picks a declared executor for this call. A JSON value goes into the launch message as `<key>: <value>`. A result (of a step, a parallel, a foreach), or a dict or list holding results, is passed: its fields go as `<key>.<path>: <value>` and its files as `read <key>.<path>/<name>: <path>` (see Files). Anything else is a `FlowError` at launch.

### Groups

`r = yield parallel('reviews', a=review_a(), b=chain)` launches the branches together and returns once every branch is done. A branch is a call, or a generator function `chain(ctx)` whose `return` value is the branch's result. The group's name is positional-only, so `name` is a valid branch key. The result has one attribute per branch key.

`done = yield foreach('migrate', over='sites.json', body=migrate_one, max_concurrent=4, max_items=50, on_item_failure='fail')` runs `body` for each item of the JSON array in the file `over` (a name resolved as `reads` are, or a result's file) and returns once every item has ended. `body(ctx, item)` is a generator, with the item's fields as `item.<field>`, or a call. The body's `return` value is the item's reply; a `StepFailed` it does not handle fails the item. The result has `items`, a list in the order of the file, each with `key`, `status` (`done`, `failed`, `cancelled`, `not_started`), `reply`, `reason` and `files`, and the index file is written next to the run's files for the step that reads it (see Files). `over`'s rules are 1.x's: objects with a unique `key` of `[A-Za-z0-9_.-]+`, at most `max_items`; a file that is absent, not such an array, or too long fails the foreach with that reason. `max_concurrent` and `max_items` must be at least 1 and `on_item_failure` one of `fail` and `skip`; these are checked when the foreach is reached, as `FlowError`.

Groups nest freely: a parallel inside a foreach item, a foreach inside a branch, a foreach inside an item. There is no top-level-only rule.

### Failure in a group

One rule for both groups. A branch whose generator does not handle a `StepFailed`, or an item whose body does not, fails the group: nothing new is launched in the group, launches already running are waited for and their replies recorded, then the group throws the `StepFailed` into the generator that yielded it. Chains cut short and items that never started get the status `cancelled` and, in a foreach's index, `not_started` for items that never started. With `on_item_failure='skip'`, an item's failure is its outcome and the foreach goes on.

### Side effects, interruptions

A step with `side_effects` is never relaunched by the engine alone. Its `failed` or `blocked` reply is not thrown into the generator: the engine tells the orchestrator to ask the human, and `relaunch` opens the next attempt after a yes. `interrupted` of such a step does the same; of any other step it opens the next attempt at once. `relaunch` of a call that is not open, or sits in a cancelled group, is refused without changing anything.

## Semantics

### Addresses and attempts

A call's address is its path: `main/review`, then `main/review#2` for the second yield of `review` in the same generator run; `main/reviews/a` for a branch; `main/migrate[auth]/verify#3` inside an item; `main/migrate#2[auth]/verify` when a loop reaches the foreach again. The counter is per generator frame and per step name, so the address is a pure function of the flow and the recorded replies.

A relaunch or an interruption opens a new attempt of the same call: the orchestrator sees it as `main/review@2`, the first attempt being plain `main/review`. `reply` and `answer` take the attempt's address and are refused for any other attempt, with the open attempt named. The flow never sees attempts.

### Replay

Every command loads `state.json`, runs `main(ctx)` from the top, and at each `yield` looks the call up by address: a `done` record returns its result, a `failed` or `blocked` one throws, an open one stops this generator here, no record proposes a launch. A group drives its branches or items as nested generators and collects their proposals; a group with an unhandled failure drops its proposals. What reaches the top is launched: records opened, files numbered, messages printed. So a replay proposes and the command commits; nothing is launched inside a failed group.

A record whose `kind` or `step` differs from what the flow yields at that address stops the command: flow.py changed under a run, start a new run. Nothing else about a changed flow is detected or handled.

Replay is linear in the run's length: index the records by address and by parent once per command, never scan the list inside the walk. Nesting is bounded by Python's stack; a few hundred nested groups is the documented limit, which no runbook approaches.

### Files

Each attempt's outputs are `<run>/<NN>-<name>` with `NN` the record's position in `state.json`, as in 1.x; group and item records take positions too. Nothing is overwritten.

`reads=['x.md']` resolves to the latest `done` record that wrote `x.md` in the yielding generator's own history, then its ancestors' (a branch sees what `main` wrote before the group; an item sees the top of the run), never a sibling's or a child's. A name no step writes is an input file, `<run>/x.md`, passed as it is. A group's results are not in its parent's history; a foreach's index is. So a step after a group gets the group's files by explicit pass only: `yield triage(reviews=reviews)` puts `read reviews.a/review-a.md: <path>` and `reviews.a.findings: 2` into the message, keyed by the argument's path, so two calls of one step in two branches pass without renaming. Explicit `reads` lines stay unprefixed. A `foreach` result passes its index as `read <key>/<foreach>.index: <path>`.

The paths a launch is told are computed once, at launch, and recorded as `files_in`; replay never recomputes them, so the order in which parallel branches report cannot change what a later step was told.

The foreach index, `<run>/<NN>-<name>.index.json`, is written whenever the foreach ends, done or failed, from the item records: one entry per item in the file's order with `key`, `status`, `reply`, `reason` and `files`, the latter the files of the item's latest done launch per name. It is a derived file for executors; replay rebuilds the result from the records and never reads it.

### Replies and the contract of a launch

The reply check is 1.x's: one JSON object with `status` in `done`, `failed`, `blocked`; for `done`, every declared field present with its `type`. The first reply that fails goes back to the executor once, with the correction text; the second is recorded as `failed`, `invalid reply: <problem>`. The record of an attempt holds the contract the executor was given: executor, the reply schema written for it, the `writes` paths, the inputs and `files_in`. The check reads the record's contract, not the current declaration.

A human step's `answer` is 1.x's: a choice matched case aside, `reply` fields with defaults, free text when there are no choices, the words written to the step's `writes` file.

### What the orchestrator sees

Commands keep their names and output: `start`, `reply <address> <json>`, `answer <address> <json or words>`, `interrupted <address>`, `relaunch <address>`, `log`, a status call, `--check`. Addresses go through `shlex.quote` in printed commands, since `[`, `#` and `@` mean things to a shell. Launch messages keep 1.x's lines (`launch step ... executor ...`, `prompt:`, `repo:`, `run:`, inputs, item fields of the innermost item, `read`, `write`, `reply schema:`), with the passed-result lines added. The question of a human step, the side-effect question, the correction, the end-of-run line, `wait for` and the progress log keep their 1.x texts.

### state.json

```json
{"format": 2, "runbook": "...", "status": "running", "inputs": {...},
 "calls": [
  {"id": "main/review", "attempt": 1, "kind": "step", "step": "review", "executor": "reviewer",
   "status": "done", "reply": {"findings": 2}, "note": null, "answer": null,
   "inputs": {}, "files_in": {"implement.md": "<run>/01-implement.md"}, "files": {"review.md": "<run>/02-review.md"},
   "schema": {...}, "writes": {"review.md": "<run>/02-review.md"},
   "started_at": "...", "ended_at": "...", "invalid_replies": []}
 ]}
```

`kind` is `step`, `human`, `parallel`, `foreach` or `item`. A foreach record carries `over`, `items` (frozen when reached), `index` and `problem`; an item record carries `fields` and the item's outcome. `status` of a call is `running`, `waiting` (a human step), `done`, `failed`, `blocked`, `cancelled` or `interrupted`; a closed attempt stays in the list with its status, and only the latest attempt of an address is open. An engine that reads another `format` refuses the run. The file is written whole: serialize, write a temporary file next to it, `os.replace`; a value that does not serialize is a `FlowError` before anything is written. `progress.md` is unchanged. `start` refuses a directory that already holds a `state.json`.

### `--check`

A start-less flow: the decorated generator function exists and is a generator function. Every step: executor declared, prompt file exists, `inputs` names declared, `reply` fields are types or schemas and none is reserved; every human step has a question; `repo` is an input; `prompts/common.md` exists. Routes are not checked statically: the author walks the flow by hand with `start` and `reply`, as 1.x's flow-language.md already says.

## What changes for an existing flow.py

- Delete `next`, `after`, `skip`, `on_failure` from every step and `rb.start(...)`. Write the routing as a generator under `@rb.flow`.
- `end(...)` is returned, not routed to. `parallel(...)` and `foreach(...)` are yielded; `then` is the code after the `yield`. `pipeline(...)` is `foreach(...)`, `item_done(...)` is `return`, `item_failed(...)` is `raise StepFailed(...)`.
- `s.done(name)`, `s.failed(name)`, `s.reply(name)`, `s.replies(name)` are local variables. `executor=lambda s: ...` is `step(executor=...)` at the call.
- A step after a group reads the group's files through an explicit pass, `yield triage(reviews=reviews)`, and its prompt names them as `reviews.a/review-a.md`.
- Runs of 1.x do not load.

## Verification

Port these scenarios to `test_runbook.py`, each a run directory driven through the commands with fake executors that write the files a launch names: the review loop end to end with the human extending the budget; the task cycle on its three paths (clean reviews skip triage; findings, a fix round, the human stops; dirty tree, the human stops); an invalid reply corrected once, then failed, with the schema file checked; a parallel with a chain branch that handles its own failure, an explicit pass of the group's result, and a side-effect step whose failure asks the human and is relaunched; a group that waits for a running sibling after a failure and launches nothing new, with `cancelled` on the cut chain; the files a join is told do not depend on the order the branches replied in; a late reply to a closed attempt is refused; foreach with `fail`, with `skip`, with `max_concurrent`, with a bad `over` file (not a list, absent, repeated key), reached twice by a loop, nested parallel and foreach inside an item, a body that is one call, an item's step interrupted at the concurrency limit; a nested foreach's `skip` failure does not stop the outer one; a flow changed under a run is refused; an author's bug in the generator leaves the recorded reply and the run resumes after the fix; a body that returns a non-JSON value is refused before state is written; `start` into an existing run is refused; a human step inside a branch, free text, a question substituting the branch's own files; `--check` and `start` refusing each declaration and input mistake; a replay of 600 records in well under a second.
