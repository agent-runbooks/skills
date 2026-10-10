# Writing flow.py

`flow.py` declares the steps of a runbook in Python, on top of `runbook.py`, which is copied from this skill unchanged, and writes the flow as one generator that calls them. `flow.py` is also the command the orchestrator runs: `runbook.py` gives it `start`, `reply`, `answer`, `interrupted`, `relaunch`, `log`, a status call and `--check`. This is the language of engine 2.0; the 1.x language and how to port from it are in [`CHANGELOG.md`](../CHANGELOG.md).

```python
#!/usr/bin/env python3
"""Steps and the flow of runbook-<name>. Run with --help for the commands."""

from runbook import Runbook, StepFailed, end, foreach, parallel

rb = Runbook()

rb.inputs(brief=str, repo=str, coder='main', maxFixRounds=2)

rb.executor('main', 'the model of the main session, high effort where the tool has one')
rb.executor(
    'other',
    'a model from another vendor, through the tool that launches it, working directory = repo',
)

# An illustration of the language, not a real runbook. A complete one: runbook-review-loop/ next to this file.

implement = rb.step(
    'implement',
    executor='main',
    prompt='prompts/01-implement.md',
    reads=['brief.md', 'working tree'],
    writes=['implement.md'],
)

review = rb.step(
    'review',
    executor='main',
    prompt='prompts/02-review.md',
    reads=['implement.md', 'fixes.index', 'working tree'],
    writes=['review.md'],
    reply={'findings': {'type': 'integer', 'description': 'headings under ## Findings'}},
)

triage = rb.step(
    'triage',
    executor='main',
    prompt='prompts/03-triage.md',
    writes=['triage.md', 'fixes.json'],
    reply={'to_fix': int},
)

fix = rb.step(
    'fix',
    executor='main',
    prompt='prompts/04-fix.md',
    reads=['triage.md', 'rounds.md', 'working tree'],
    writes=['fix.md'],
)

ask_rounds = rb.human(
    'ask-rounds',
    writes='rounds.md',
    choices=['more rounds', 'stop'],
    reply={'rounds': {'type': 'integer', 'default': 1, 'description': 'how many more fix rounds'}},
    question='Fix rounds are spent, see `<run>/triage.md`. More rounds, and how many, or stop here?',
)


def second_review(ctx):
    """A branch: another vendor's review, or the main model's when that tool cannot run here."""
    try:
        return (yield review(executor='other'))
    except StepFailed:
        return (yield review())


@rb.flow
def main(ctx):
    yield implement(executor=ctx.inputs.coder)
    budget, used = ctx.inputs.maxFixRounds, 0
    while True:
        reviews = yield parallel('reviews', a=review(), b=second_review)
        if reviews.a.findings + reviews.b.findings == 0:
            return end('ready', f'read {reviews.a.files["review.md"]}')
        t = yield triage(reviews=reviews, round=used + 1)
        if t.to_fix == 0:
            return end('ready', 'read <run>/triage.md')
        if used >= budget:
            a = yield ask_rounds()
            if a.choice == 'stop':
                return end('needs_attention', 'read <run>/triage.md')
            budget += a.rounds
        # Each fix changes the working tree, so one at a time; a finding that cannot be fixed stays in the index.
        yield foreach('fixes', over='fixes.json', body=fix(), max_concurrent=1, on_item_failure='skip')
        used += 1


if __name__ == '__main__':
    raise SystemExit(rb.main())
```

## `rb.inputs(...)`

The inputs of a run, by name. A type (`str`, `int`, `bool`) is a required input. A value is a default of its type. `start` takes the inputs the orchestrator was given, applies the defaults, refuses wrong types and unknown names, and records the result. `repo` is always among them. The flow reads them as `ctx.inputs.<name>`.

## `rb.executor(name, description)`

What the orchestrator launches for an executor name. The engine prints ``launch step `<call>` as a new subagent, executor `<name>`: <description>`` with every launch, so the orchestrator never looks it up, and the Execution rules say a subagent is launched through the orchestrator's own subagent tool unless the description names another. The description therefore holds only what differs: model, effort, working directory, another tool. Name a tool by what it is, with a harness's name for it in brackets at most: a description that says only `the Agent tool` runs in one harness. A step's `executor` is a declared name, and a call picks another declared one with `executor=`: `review(executor='other')`, `implement(executor=ctx.inputs.coder)`.

## `rb.step(name, ...)`

A step an executor runs. `rb.step` returns the step, and the flow launches it by calling it: `r = yield review()`. One step can be called from many places, each call a launch of its own.

Names of steps, groups and branch keys are letters, digits, `_ . -`, since they are parts of an address. Step names are unique across `rb.step` and `rb.human`: a malformed or taken one raises `ValueError` at declaration. A malformed group name or branch key stops the command when the group is reached.

Collection parameters `inputs`, `reads`, `writes` and `choices` take iterables, such as lists or tuples. A lone string raises `TypeError` naming the step and parameter. `rb.human(writes=...)` takes one file name as a string.

- `executor`: a name declared with `rb.executor`.
- `prompt`: the step's prompt file, relative to the runbook directory.
- `inputs`: what the launch message carries beyond `repo`, `run` and the files. A string is the name of a run input, passed with its value. A `(key, value)` pair is passed as it is. A call adds lines of its own, see the flow below.
- `reply`: the fields the executor's JSON carries beyond `status`, each with a type, `{'passed': bool}`, or with the field's JSON Schema, `{'findings': {'type': 'integer', 'description': '…'}}`. A type is shorthand for `{'type': …}`: `bool`, `int`, `float`, `str`, `list`, `dict`. Anything else raises `TypeError` at declaration, and a field named `id`, `files`, `status`, `reason` or `choice` raises `ValueError`: those are the engine's attributes on a result. From the fields the engine writes the reply's schema for each launch to `<run>/schemas/<NN>-<step>.json`, and the launch message gives its path as `reply schema: <path>`: the executor reads it, and a harness that can hold a subagent to a schema is given it. The schema is one closed object with every property required, `status`, `reason` and the fields, each field allowing null next to its own values: the form such harnesses accept for scalar fields. A `list` or `dict` shorthand gives an open array or object, which a harness that enforces schemas refuses: give such a field its full schema, `items` and closed `properties` included, or better, put the content in a file. A field's schema stands alone, without `$ref`. Its description says which go with which status, and the engine drops the nulls before recording. The flow reads the fields as `r.field`. A reply that is not one JSON object with a known `status`, or a `done` reply that lacks one of the fields or carries the wrong `type`, goes back to its executor once: `flow.py` prints a correction that the orchestrator sends into the executor's session, asking for the JSON only and not for the step's work again. A second such reply, or an executor that cannot be asked again, is recorded as `failed`, `invalid reply: <what is wrong>`, and reaches the flow as a failure. The check reads the schema written for that launch, not the current declaration. The engine checks nothing but the type: `enum`, `minimum` and the rest bind only where the harness enforces the schema, so the flow handles a value outside them. Keep to `type`, `enum` and `description` unless you know the harnesses the runbook runs in: some refuse a schema with `minimum` or `minLength` in it.
- `side_effects`: what the step does outside the tree and `<run>`: `'commit'`, `'PR comment'`. Such a step is never relaunched by the engine alone. Its `failed` or `blocked` reply, and its interruption, do not reach the flow: `flow.py` asks for the human's yes first, and `relaunch` opens the next attempt.
- `writes`: names of the files the step writes, `['checks.md']`. Each launch writes its own file, `<run>/<NN>-checks.md`, `NN` being its record's place in `state.json`: `02-checks.md`, then `04-checks.md` after a fix. Groups and items take places too, so the numbers have gaps where a group opened. Nothing is overwritten, and the run directory lists in the order things happened. The launch message gives the path as `write checks.md: <path>`. A declared file the launch did not write is not in its result's `files`, nor in an index, and a later `reads` of the name gets the latest file that was written; the reply's line in `progress.md` says `did not write checks.md`.
- `reads`: names of the files the step reads. Another step's output is passed as the latest such file in the calling generator's scope, `read checks.md: <path>`, or as absent, `read checks.md: absent, no earlier step wrote it`; a step that reads its own name gets its previous pass in that scope. The scope is under Files. A step inside a group does not see its pass from an earlier group, which sits in that group's history: the flow passes it in, `a=review(previous=reviews.a)`, and the step reads `read previous/review.md: <path>`. A name no step writes is an input file under `<run>` and is passed as it is. `'working tree'` is not passed; it is there for the reader of `flow.py`.

## `rb.human(name, ...)`

A question to the human. No executor. `rb.human` returns the step, and the flow asks it with `a = yield ask_rounds()`. `flow.py` prints the question and the orchestrator asks it.

- `question`: the text, with the choices in it if there are any. `<run>/<name>` in it becomes the latest file of that name in the calling generator's scope, as for `reads`, and any other `<run>` the run directory.
- `choices`: the strings `a.choice` is one of. `flow.py` prints them with the question, the orchestrator maps the answer to one of them, and `answer` accepts it case aside. Without `choices` the step takes free text and `a.choice` is the words whole.
- `reply`: fields the orchestrator takes from the human's words next to the choice, declared as a step's `reply` is: `{'rounds': {'type': 'integer', 'default': 1, 'description': 'how many more fix rounds'}}`. `flow.py` prints each with its type and description, and `answer` then takes a JSON object, `'{"choice": "more rounds", "rounds": 2}'`. The flow reads them as `a.rounds`. A field the human did not give is its schema's `default`, or `None`. Only the `type` is checked, and a wrong one is refused, not recorded.
- `writes`: a file name the engine writes the human's verbatim words to, numbered like a step's output, for the steps that follow. The words are kept in `state.json` and the log either way: `answer <call> '<choice>' '<the words>'`, or `-` to read the words from stdin.

## `@rb.flow` and `yield`

`@rb.flow` marks the flow: a generator function `main(ctx)`, one per runbook. A function that is not a generator raises `TypeError`. The run begins where the generator begins and ends where it returns.

Every command runs `main(ctx)` again from the top against the calls recorded in `state.json`, and drops the generator at the first call that is not done. A call that is done returns its recorded result at once, so the generator walks to where the run is, and a call with no record is launched. Local variables are recomputed on that walk, not kept. In the example above, `budget, used = ctx.inputs.maxFixRounds, 0` runs on every command, `used += 1` runs once per round already recorded, and `budget += a.rounds` takes `a.rounds` from the recorded answer, so both stand where the run left them.

So the generator computes from `ctx` and what its yields return, and acts on the world only through its yields:

- An action in its body repeats on every command: a file it writes, a command it runs, a `print`.
- A dropped generator is closed, so a `finally` or a `with` around the `yield` it stopped at runs on every command, once or more, not at the end of the run. A bare `except:` around a `yield` catches that close, `GeneratorExit`; `except StepFailed` is the one a flow needs.
- The clock, a directory listing, a file it reads, the order of a `set`, which differs between processes, or module-level state it changes gives another answer on the next command, and the replay goes another way than the run went.
- A branch on what a step found is a reply field: the field is checked against the step's schema and recorded with the call, while a file is read again on every command and can change on disk in between. The engine does the same for the `over` of a foreach, see Groups.

- `r = yield step(...)` launches the step and returns once its reply is `done`. `r` has the reply's fields as attributes, `r.findings`, and `r.choice` for a human step; `r.id`, the call's address; and `r.files`, each name the launch wrote with its path.
- Keyword arguments of a call are lines of its launch message. A JSON value is `<key>: <value>`: `round=used + 1` is `round: 1`. A result, or a dict or list holding results, passes its fields and files, see Files. `executor=` picks a declared executor for this call. Any other value stops the command as a defect of `flow.py`.
- A `failed` or `blocked` reply is thrown into the generator at that `yield` as `StepFailed`: `e.id` is the call's address, `e.status` and `e.reason` are the reply's, `e.reply` is the whole reply. A `try`/`except StepFailed` around the `yield` handles it. A failure no generator handles ends the run `failed`, and the human is pointed at `progress.md`. A generator raises `StepFailed('<reason>')` itself to fail its branch or item.
- `return end('status', 'read <run>/checks.md')` ends the run. The second argument is what `flow.py` tells the orchestrator to report, with `<run>/checks.md` replaced by the latest `checks.md` in `main`'s scope and any other `<run>` by the run directory. A file written inside a group is not in that scope: name it through the result, `f'read {reviews.a.files["review.md"]}'`. Returning anything else from `main` is a defect of `flow.py`. Describe every status under End of run in `SKILL.md`, `failed` included.
- A loop is a `while` with its budget in local variables: `budget, used = ctx.inputs.maxFixRounds, 0`, `used += 1` per round, `budget += a.rounds` when the human grants more. A retry is the same around a `try`/`except StepFailed`, counting its failures. Every loop has a budget and a way out: a human step, or a `return end(...)`.

## Groups

`reviews = yield parallel('reviews', a=review(), b=second_review)` launches the branches together and returns once every branch is done. A branch is a step call, or a generator function `(ctx)`, a chain of calls whose `return` value is the branch's result. The result has one attribute per branch key: `reviews.a` is the call's result, `reviews.b` what `second_review` returned. A branch key is not `id`, `files`, `status`, `reason` or `choice`. Branches read what the generator around them wrote before the group. A branch that changes the working tree runs in a group where no other branch reads the tree; branches that do not touch it may run next to it.

`done = yield foreach('fixes', over='fixes.json', body=fix(), max_concurrent=1, on_item_failure='skip')` runs the body for each item of the JSON array in the file `over`, and returns once every item has ended.

- `over`: a name resolved as `reads` are, or a path, such as a result's file. The file holds objects with a unique `key` of letters, digits, `_ . -`, at most `max_items` of them, 50 by default. The engine reads it once, when the foreach opens, and records its items with the foreach, so a later change to the file does not reach the run. A file that is absent, not such an array, or too long fails the foreach with that reason, which the flow can catch as any `StepFailed`.
- `body`: a step call, whose reply is the item's reply, or a generator function `(ctx, item)` that reads the item's fields as `item.<field>` and whose `return` value, a JSON value, is the item's reply. A step launched in an item gets the fields of the innermost item as lines of its message, `key: f1`, `title: off by one`.
- `max_concurrent`: how many items run at once, 4 by default. A body that changes the working tree takes 1.
- `on_item_failure`: `'fail'`, the default, fails the foreach when an item fails. `'skip'` keeps the failure as the item's outcome and goes on.
- The result has `items` in the order of the file, each with `key`, `status` (`done` or `failed`), `reply`, `reason`, and `files`, those of the item's latest done launches. Its `files` holds the index, see Files.

`max_concurrent` and `max_items` below 1, or another `on_item_failure`, stop the command as a defect of `flow.py` when the foreach is reached.

Groups nest: a parallel in a foreach item, a foreach in a branch or an item. A loop that reaches a group again opens a new one.

One failure rule for both. A branch or an item that does not handle a `StepFailed` fails its group: nothing new is launched in it, a question asked in it is withdrawn, the launches already running are waited for and their replies recorded, and then the group throws that `StepFailed` into the generator that yielded the group. A chain cut short is `cancelled` in the parallel's record. In the foreach's index an item cut short is `cancelled`, and one that never started `not_started`.

## Files

`reads` resolve in a scope. A name another step writes is passed as the file of the latest done launch in the calling generator's own history, then in the histories of the generators around it as they stood when they yielded the group: a branch sees what `main` wrote before the parallel, an item what was written before the foreach. Never a sibling branch's or a child group's.

So a group's files are not in the history of the generator that yielded it. A step after the group gets them by taking the group's result as a keyword argument: `yield triage(reviews=reviews, round=used + 1)` puts these lines into its message, between the step's own inputs and the `write` lines, and its `read` lines after the `reads`:

```
reviews.a.findings: 2
reviews.b.findings: 1
round: 1
write triage.md: /tmp/try/05-triage.md
write fixes.json: /tmp/try/05-fixes.json
read reviews.a/review.md: /tmp/try/02-review.md
read reviews.b/review.md: /tmp/try/04-review.md
```

A step's result passes its fields as `<key>.<field>: <value>` and its files as `read <key>/<name>: <path>`. A parallel's result passes each branch under `<key>.<branch>`, a dict or a list holding results each entry under `<key>.<entry>`, its plain values as `<key>.<entry>: <value>`. A foreach's result passes its index, `read <key>/fixes.index: <path>`; one of its items, `done.items[0]`, passes its `key`, `status`, `reply` and `reason` as `<key>.<field>` and its files as `read <key>/<name>`, and `done.items` passes them all under `<key>.0`, `<key>.1`. Two calls of one step in two branches thus arrive without renaming, and the prompt names them `reviews.a/review.md` and `reviews.b/review.md`. The `reads` lines stay unprefixed.

What a launch is told is computed when it is launched and recorded with it, so the order in which the branches replied never changes it.

The index of a foreach is `<run>/<NN>-<name>.index.json`, written when the foreach ends, done or failed: one entry per item in the order of the file, with `key`, `status` (`done`, `failed`, `cancelled`, `not_started`), `reply`, `reason` and `files`. In the scope of the generator that yielded the foreach it is the name `<name>.index`: a step with `reads=['fixes.index']` gets it, and so does `<run>/fixes.index` in an end report or a question.

## Addresses and attempts

Every call has an address, its path through the flow. `flow.py` prints it with the launch or the question, and `reply`, `answer`, `interrupted` and `relaunch` take it.

- `main/implement`: a call `main` yields.
- `main/triage#2`: the second call of `triage` in the same run of the same generator, the next round of a loop. The counter is per generator and per name.
- `main/reviews/a`: branch `a` of the parallel `reviews`, a step call. `main/reviews/b/review` and `main/reviews/b/review#2`: calls in branch `b`, a chain.
- `main/fixes[f1]/fix`: the call in item `f1` of the foreach `fixes`. `main/fixes#2[f1]/fix` when the loop reaches the foreach again.

The address depends on the flow and the recorded replies alone. Printed commands quote it for the shell, since `#` and `[` mean things there.

An interrupted launch, or a relaunch on the human's yes, opens a new attempt of the same call: `main/fixes[f1]/fix@2`, the first attempt being the plain address. The new attempt writes new numbered files, and its message ends with `The tree may hold a partial earlier attempt.` Commands take the latest attempt only, and name it when given another: `call main/fixes[f1]/fix is a closed attempt (interrupted); the latest attempt is main/fixes[f1]/fix@2`. The flow never sees attempts.

- `interrupted <call>` takes a running step. A step without `side_effects` is launched again at once. One with `side_effects` asks the human first, as its failed reply does: ``ask the human: step `main/commit` has side effects and ended interrupted. On yes: <python> <runbook>/flow.py <run> relaunch main/commit. On no: <python> <runbook>/flow.py <run> log '<their decision>' and stop.``
- `relaunch <call>` takes only a call the run is asking the human about, never one in a group that has failed.
- A question asked inside a group that fails is closed as `cancelled`, and `flow.py` prints ``withdraw the question `main/group/c/ask`: its group failed; tell the human no answer is needed``.
- A step with `side_effects` whose question a failing group drops is noted `cancelled with its group`, in its record and in `progress.md`, and `relaunch` refuses it. When the human was asked about it, `flow.py` prints ``withdraw the question whether to relaunch step `main/group/p`: its group failed, and it is not relaunched; tell the human no answer is needed``.

## Files of a run

- `state.json`: format 2. The run's inputs, its status (`running`, `waiting_for_human` or the end status), and `calls`, one record per attempt in the order they were opened: steps and human steps, and the records of parallels, foreaches and items. Each holds `id` (the address), `attempt`, `kind`, `step` and `status`; a launch holds the contract its executor was given, the replies, and the replies that did not pass the check. Written by `runbook.py` only. An engine that reads another format refuses the run, and `start` refuses a directory that holds a `state.json`.
- `progress.md`: the inputs and a text log, one line per launch, reply, question, answer and end. Append-only, for humans.
- The steps' output files, `<NN>-<name>`, the indexes, `<NN>-<name>.index.json`, and the input files under their own names.
- `schemas/<NN>-<step>.json`: the JSON Schema of each launch's reply. Written by `runbook.py` only.

## Checking

`python3 flow.py --check` from the runbook directory: a flow is declared, `repo` is an input, every step's executor and every `inputs` name is declared, every prompt file exists, every human step has a question, `prompts/common.md` exists. Declaration mistakes, such as a malformed name, a reserved or untyped reply field or a lone string, raise when `flow.py` is loaded, naming the step and the parameter. Routes are Python and are not checked.

A defect found while the flow runs, such as an exception in a generator, a yield of something that is neither a call nor a group, or a value a call cannot pass, stops the command with `This is a defect in flow.py: report it to the human and stop.` and the command that resumes the run once it is fixed. What the command recorded is kept, so after `flow.py` is fixed, `flow.py <run>` goes on from there.

During a run, change `flow.py` only to fix a defect the engine reported. The replay runs the current flow against the recorded calls. A record whose kind or step differs from what the flow yields at its address stops the command: `flow.py changed under this run. Start a new run.` Any other change sends the run down the new path without a word.

Walk the flow by hand before the first run all the same:

```
python3 flow.py /tmp/try start '{"brief": "brief.md", "repo": "/tmp/x", "maxFixRounds": 1}'
python3 flow.py /tmp/try reply main/implement '{"status": "done"}'
python3 flow.py /tmp/try reply main/reviews/a '{"status": "done", "findings": 2}'
python3 flow.py /tmp/try reply main/reviews/b/review '{"status": "blocked", "reason": "no other vendor here"}'
python3 flow.py /tmp/try reply 'main/reviews/b/review#2' '{"status": "done", "findings": 1}'
```

A file the flow reads, such as the `over` of a foreach, has to exist before the reply of the step that writes it: write it at the path the launch printed, `write fixes.json: /tmp/try/05-fixes.json`. Feed the walk every path: a failed reply, an invalid reply twice, an interruption, a human answer, the loop budget running out, an item that fails.
