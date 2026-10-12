# Engine 2.0: the flow as a generator

Design note for engine 2.0.0, `agent_runbooks.py`. The steps are declarative, the flow is one Python generator, and the engine replays that generator against the recorded replies on every command.

## Why

In 1.x a flow is a flat graph of step names: `next` is a goto, a `parallel(...)` is joined by a step with `after=(...)` that repeats the branch names, a loop is a step name reached again with `s.done(name) < budget` as its counter, and a pipeline is a scope patched onto the graph. The engine reconstructs the structure from the names, so the join must guess which section of a branch belongs to which round, `--check` carries a dozen rules about where a literal target may sit, and a step is tied to one place in the graph. The join written twice, `next='join'` on each branch and `after=('a', 'b')` on the join, is the visible symptom.

In 2.0 the unit is a call: a step is launched, ends, and returns its reply to the code that launched it. A branch knows nothing about the join, only the caller does. The control flow is Python's own: `if`, `while`, `return`, `try`/`except`, local counters.

## The author's API

```python
#!/usr/bin/env python3
"""Steps and the flow of runbook-review-loop. Run with --help for the commands."""

from agent_runbooks import Runbook, end

rb = Runbook()

rb.inputs(brief=str, repo=str, checks='', maxFixRounds=2)

coder = rb.executor('coder', 'general-purpose, on the model of the main session')
reviewer = rb.executor('reviewer', 'general-purpose, on the model of the main session')

implement = rb.step(
    'implement',
    executor=coder,
    prompt='prompts/01-implement.md',
    inputs=['checks'],
    reads=['brief.md', 'working tree'],
    writes=['implement.md'],
)

review = rb.step(
    'review',
    executor=reviewer,
    prompt='prompts/02-review.md',
    inputs=['checks'],
    reads=['brief.md', 'implement.md', 'fix.md', 'review.md', 'working tree'],
    writes=['review.md'],
    reply={'findings': {'type': 'integer', 'description': 'findings still open'}},
)

fix = rb.step(
    'fix',
    executor=coder,
    prompt='prompts/03-fix.md',
    inputs=['checks'],
    reads=['brief.md', 'review.md', 'rounds.md', 'working tree'],
    writes=['fix.md'],
)

ask_rounds = rb.human(
    'ask-rounds',
    writes='rounds.md',
    choices=['more rounds', 'stop'],
    reply={'rounds': {'type': 'integer', 'default': 1, 'description': 'how many more'}},
    question='Fix rounds are spent and `<run>/review.md` still lists findings. More, or stop?',
)


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

The whole language: `rb.inputs`, `rb.executor`, `rb.step`, `rb.human`, `@rb.flow`, `yield`, `parallel`, `foreach`, `StepFailed`, `end`, `files`, `address`. Nothing else.

### Declarations

`rb.inputs(...)`, `rb.executor(name, description)`, `rb.step(name, executor=, prompt=, inputs=, reads=, writes=, reply=, side_effects=)` and `rb.human(name, question=, choices=, reply=, writes=)` declare the run's inputs, its executors and its steps. Their parameters and checks are in `flow-language.md`. A step holds no routing: the order of the steps lives in the flow alone.

`rb.executor` returns the executor, and a step's `executor` takes that value. A name or another `Runbook`'s executor raises `TypeError`. An executor's name is letters, digits, `_ . -`, like a step's, and a taken one raises `ValueError`. `rb.step` and `rb.human` return the step, which the flow calls. The flow begins where the generator begins, so there is no start step to declare.

A declaration mistake is refused at declaration, naming the step and the parameter, as AGENTS.md says. A reply field named `status` or `reason` is refused, and `choice` in a human step: those are keys of the reply JSON. A branch key reserves nothing.

### The flow

`@rb.flow` marks the generator function `main(ctx)`. `ctx.inputs.<name>` are the run's inputs. The function is called afresh on every command, so it depends only on its arguments and on what `yield` returns: no clock, no file reads, no module-level mutable state. Local variables are fine, since every replay recomputes them.

- `yield step(...)` launches the step and returns its result once the reply is `done`. The result has the reply's fields as attributes (`r.findings`), `choice` for a human step, and nothing else. `address(r)` gives the call's address and `files(r)` what the launch wrote, name to path. Both take every kind of result and an item of a foreach, and raise `TypeError` for anything else. The engine keeps a result's address and files outside its attributes, so no name an author gives can reach them.
- A `failed` or `blocked` reply is thrown into the generator at that `yield` as `StepFailed`, with `.id` and `.reply` (`status`, `reason`, and any fields). A `try`/`except StepFailed` around the `yield` handles it. An unhandled `StepFailed` fails the enclosing group's branch or item, or the run.
- `return end(status, report)` ends the run. `report` is what the orchestrator tells the human, with `<run>/<file>` replaced by the latest file of that name in the flow's scope and `<run>/<foreach>.index` by that foreach's latest index. Returning anything else from `main` is a `FlowError`.
- Keyword arguments of a call are inputs of the launch. `executor=` picks another executor for this call: what `rb.executor` returned, or the name of a declared one. A JSON value goes into the launch message as `<key>: <value>`. A result (of a step, a parallel, a foreach), or a dict or list holding results, is passed: its fields go as `<key>.<path>: <value>` and its files as `read <key>.<path>/<name>: <path>` (see Files). Anything else is a `FlowError` at launch.

### Groups

`r = yield parallel('reviews', a=review_a(), b=chain)` launches the branches together and returns once every branch is done. A branch is a call, or a generator function `chain(ctx)` whose `return` value is the branch's result. The group's name is positional-only, so `name` is a valid branch key. The result has one attribute per branch key, and nothing else.

`done = yield foreach('migrate', over='sites.json', body=migrate_one, max_concurrent=4, max_items=50, on_item_failure='fail')` runs `body` for each item of the JSON array in the file `over` and returns once every item has ended. `over` is a name resolved as `reads` are, or a result's file. `body` is a call, or a generator `body(ctx, item)` that reads the item's fields as `item.<field>`. The body's `return` value is the item's reply, and a `StepFailed` it does not handle fails the item. The result has `items`, a list in the order of the file, each with `key`, `status` (`done`, `failed`, `cancelled`, `not_started`), `reply` and `reason`, and `files(item)` gives an item's files. The foreach also writes an index file, `files(done)`, see Files.

The `over` file holds objects with a unique `key` of `[A-Za-z0-9_.-]+`, at most `max_items` of them. The engine reads it once, when the foreach is reached, and records its items. A file that is absent, not such an array, or too long fails the foreach with that reason. `max_concurrent` and `max_items` must be at least 1, and `on_item_failure` is `fail` or `skip`. These are checked when the foreach is reached, and a wrong one is a `FlowError`.

Groups nest freely: a parallel inside a foreach item, a foreach inside a branch, a foreach inside an item.

### Failure in a group

One rule covers both groups. A branch whose generator does not handle a `StepFailed`, or an item whose body does not, fails the group. Nothing new is launched in it, the launches already running are waited for and their replies recorded, and then the group throws the `StepFailed` into the generator that yielded it. A chain cut short is `cancelled`. In a foreach's index, an item cut short is `cancelled` and one that never started is `not_started`. With `on_item_failure='skip'`, an item's failure is its outcome and the foreach goes on.

### Side effects, interruptions

A step with `side_effects` is never relaunched by the engine alone. Its `failed` or `blocked` reply is not thrown into the generator. The engine tells the orchestrator to ask the human instead, and `relaunch` opens the next attempt after a yes. `interrupted` of such a step asks the human the same way, and for any other step it opens the next attempt at once. `relaunch` of a call that is not open, or sits in a cancelled group, is refused without changing anything.

## Semantics

### Addresses and attempts

A call's address is its path. `main/review` is the first yield of `review` in a generator run, and `main/review#2` the second. A branch is `main/reviews/a`, a call inside an item `main/migrate[auth]/verify#3`, and the same call is `main/migrate#2[auth]/verify` when a loop reaches the foreach again. The counter is per generator frame and per step name, so the address is a pure function of the flow and the recorded replies.

A relaunch or an interruption opens a new attempt of the same call. The orchestrator sees it as `main/review@2`, and the first attempt is plain `main/review`. `reply` and `answer` take the attempt's address and refuse any other attempt, naming the open one. The flow never sees attempts.

### Replay

Every command loads `state.json`, runs `main(ctx)` from the top, and at each `yield` looks the call up by its address. A `done` record returns its result, and a `failed` or `blocked` one throws `StepFailed`. An open record stops this generator there, and an address with no record proposes a launch. A group drives its branches or items as nested generators and collects what they propose. A group with an unhandled failure drops its proposals. Whatever reaches the top is launched: the records are opened, the files numbered, the messages printed. So the replay only proposes and the command commits, and nothing is ever launched inside a failed group.

A record whose `kind` or `step` differs from what the flow yields at that address stops the command: `flow.py` changed under the run, and the human starts a new one. Nothing else about a changed flow is detected or handled.

Replay is linear in the run's length. The records are indexed by address and by parent once per command, and the walk never scans the list. Nesting is bounded by Python's stack, which allows a few hundred nested groups, far more than any runbook needs.

### Files

Each attempt's outputs are `<run>/<NN>-<name>`, `NN` being the record's position in `state.json`. Group and item records take positions too. Nothing is overwritten.

`reads=['x.md']` resolves to the latest `done` record that wrote `x.md` in the yielding generator's own history, then in its ancestors' histories. A branch sees what `main` wrote before the group, an item what was written before the foreach. A generator never sees a sibling's or a child's files. A name no step writes is an input file, `<run>/x.md`, passed as it is.

A group's results are not in its parent's history, though a foreach's index is. So a step after a group gets the group's files only by an explicit pass. `yield triage(reviews=reviews)` puts `read reviews.a/review-a.md: <path>` and `reviews.a.findings: 2` into the message. The lines are keyed by the argument's path, so two calls of one step in two branches pass without renaming. Explicit `reads` lines stay unprefixed. A `foreach` result passes its index as `read <key>/<foreach>.index: <path>`.

The paths a launch is told are computed once, at launch, and recorded as `files_in`. Replay never recomputes them, so the order in which parallel branches report cannot change what a later step was told.

The foreach index, `<run>/<NN>-<name>.index.json`, is written whenever the foreach ends, done or failed, from the item records. It has one entry per item in the file's order, with `key`, `status`, `reply`, `reason` and `files`, the last being the files of the item's latest done launch per name. The index exists for executors to read. Replay rebuilds the result from the records and never reads it.

### Replies and the contract of a launch

A reply passes the check when it is one JSON object with `status` in `done`, `failed` or `blocked`, and a `done` reply carries every declared field with its `type`. The first reply that fails goes back to the executor once, with the correction text. The second is recorded as `failed`, `invalid reply: <problem>`. The record of an attempt holds the contract the executor was given: the executor, the reply schema written for it, the `writes` paths, the inputs and `files_in`. The check reads the record's contract, not the current declaration.

A human step's `answer` takes a choice matched case aside, or free text when the step has no choices, plus the `reply` fields with their defaults. The engine writes the human's words to the step's `writes` file.

### What the orchestrator sees

The commands are `start`, `reply <address> <json>`, `answer <address> <json or words>`, `interrupted <address>`, `relaunch <address>`, `log`, a status call and `--check`. Printed commands pass addresses through `shlex.quote`, since `[`, `#` and `@` mean things to a shell.

A launch is a headline, `launch step <address> as a new subagent, executor <name>: <description>`, and a message. The message names the prompt files to read, then gives `repo:`, `run:`, the step's inputs, the call's inputs with the lines of passed results, the fields of the innermost item, the `write` and `read` lines, and `reply schema:`. Every text the orchestrator or an executor reads, from these lines to the questions, the correction and the end of the run, is in `TEXT` in `agent_runbooks.py`.

### state.json

```json
{
  "format": 2,
  "runbook": "runbook-review-loop",
  "status": "running",
  "inputs": {"brief": "brief.md", "repo": "/path/to/repo", "checks": "", "maxFixRounds": 2},
  "calls": [
    {
      "id": "main/review",
      "attempt": 1,
      "kind": "step",
      "step": "review",
      "status": "done",
      "executor": "reviewer",
      "reply": {"findings": 2},
      "note": null,
      "answer": null,
      "inputs": {},
      "files_in": {"brief.md": "<run>/brief.md", "implement.md": "<run>/00-implement.md"},
      "files": {"review.md": "<run>/01-review.md"},
      "schema": {"type": "object", "properties": {"...": "..."}},
      "writes": {"review.md": "<run>/01-review.md"},
      "started_at": "2026-10-07T10:05:20Z",
      "ended_at": "2026-10-07T10:08:25Z",
      "invalid_replies": []
    }
  ]
}
```

`kind` is `step`, `human`, `parallel`, `foreach` or `item`. A foreach record carries `over`, the `items` read when the foreach was reached, `index` and `problem`. An item record carries `fields` and the item's outcome. The `status` of a call is `running`, `waiting` (a human step), `done`, `failed`, `blocked`, `cancelled` or `interrupted`. A closed attempt stays in the list with its status, and only the latest attempt of an address is open. An engine that reads another `format` refuses the run.

The file is written whole: serialized first, written to a temporary file next to it, then moved into place with `os.replace`. A value that does not serialize is a `FlowError` before anything is written. `start` refuses a directory that already holds a `state.json`.

### `--check`

`--check` reads the declarations. A generator function is decorated with `@rb.flow`. Every step's prompt file exists, its `inputs` names are declared, and its `reply` fields are types or schemas with none reserved. Every human step has a question, `repo` is an input, and `prompts/common.md` exists. Routes are not checked statically: the author walks the flow by hand with `start` and `reply`, as `flow-language.md` describes.
