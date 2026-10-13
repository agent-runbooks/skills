# Writing flow.py

`flow.py` declares the steps of a runbook in Python, on top of the engine `agent_runbooks.py`, which is copied from this skill unchanged or declared from PyPI as `template.md` shows, and imported as `agent_runbooks` either way. It writes the flow as one generator that calls the steps. `flow.py` is also the command the orchestrator runs: the engine gives it `start`, `reply`, `answer`, `interrupted`, `relaunch`, `log`, a status call and `--check`. This is the language of engine 2.1, which added code steps; the 1.x language and how to port from it are in [`CHANGELOG.md`](../CHANGELOG.md).

```python
#!/usr/bin/env python3
"""Steps and the flow of runbook-<name>. Run with --help for the commands."""

from agent_runbooks import Runbook, StepFailed, end, files, foreach, parallel

rb = Runbook()

rb.inputs(brief=str, repo=str, coder='session', maxFixRounds=2)

session = rb.executor('session', 'the model of the main session, high effort where the tool has one')
other = rb.executor(
    'other',
    'a model from another vendor, through the tool that launches it, working directory = repo',
)

# An illustration of the language, not a real runbook. A complete one: runbook-review-loop/ next to this file.

implement = rb.step(
    'implement',
    executor=session,
    prompt='prompts/01-implement.md',
    reads=['brief.md', 'working tree'],
    writes=['implement.md'],
)

review = rb.step(
    'review',
    executor=session,
    prompt='prompts/02-review.md',
    reads=['implement.md', 'fixes.index', 'working tree'],
    writes=['review.md'],
    reply={'findings': {'type': 'integer', 'description': 'headings under ## Findings'}},
)

triage = rb.step(
    'triage',
    executor=session,
    prompt='prompts/03-triage.md',
    writes=['triage.md', 'fixes.json'],
    reply={'to_fix': int},
)

fix = rb.step(
    'fix',
    executor=session,
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
        return (yield review(executor=other))
    except StepFailed:
        return (yield review())


@rb.flow
def main(ctx):
    yield implement(executor=ctx.inputs.coder)
    budget, used = ctx.inputs.maxFixRounds, 0
    while True:
        reviews = yield parallel('reviews', a=review(), b=second_review)
        if reviews.a.findings + reviews.b.findings == 0:
            return end('ready', f'read {files(reviews.a)["review.md"]}')
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

Declares an executor and returns it for the steps to take: `coder = rb.executor('coder', '…')`. Its name is what `state.json`, the log and the launches record; it is letters, digits, `_ . -`, and a malformed or taken one raises `ValueError`. The description is what the orchestrator launches for it. The engine prints ``launch step `<call>` as a new subagent, executor `<name>`: <description>`` with every launch, so the orchestrator never looks it up, and the Execution rules say a subagent is launched through the orchestrator's own subagent tool unless the description names another. The description therefore holds only what differs: model, effort, working directory, another tool. Name a tool by what it is, with a harness's name for it in brackets at most: a description that says only `the Agent tool` runs in one harness. A step takes the executor, so it is declared before the steps that use it. A call picks another with `executor=`: `review(executor=other)`, or by name when a run input picks it, `implement(executor=ctx.inputs.coder)`.

## `rb.step(name, ...)`

A step an executor runs. `rb.step` returns the step, and the flow launches it by calling it: `r = yield review()`. One step can be called from many places, each call a launch of its own.

Names of steps, groups and branch keys are letters, digits, `_ . -`, since they are parts of an address. Step names are unique across `rb.step` and `rb.human`: a malformed or taken one raises `ValueError` at declaration. A malformed group name or branch key stops the command when the group is reached.

Collection parameters `inputs`, `reads`, `writes` and `choices` take iterables, such as lists or tuples. A lone string raises `TypeError` naming the step and parameter. `rb.human(writes=...)` takes one file name as a string.

- `executor`: what `rb.executor` of this `flow.py` returned. A name, or an executor of another `Runbook`, raises `TypeError`.
- `prompt`: the step's prompt file, relative to the runbook directory.
- `inputs`: what the launch message carries beyond `repo`, `run` and the files. A string is the name of a run input, passed with its value. A `(key, value)` pair is passed as it is. A call adds lines of its own, see the flow below.
- `reply`: the fields the executor's JSON carries beyond `status`, each with a type, `{'passed': bool}`, or with the field's JSON Schema, `{'findings': {'type': 'integer', 'description': '…'}}`. A type is shorthand for `{'type': …}`: `bool`, `int`, `float`, `str`, `list`, `dict`. Anything else raises `TypeError` at declaration, and a field named `status` or `reason` raises `ValueError`: those are keys of the reply JSON. Any other name works, `id` and `files` included. From the fields the engine writes the reply's schema for each launch to `<run>/schemas/<NN>-<step>.json`, and the launch message gives its path as `reply schema: <path>`: the executor reads it, and a harness that can hold a subagent to a schema is given it. The schema is one closed object with every property required, `status`, `reason` and the fields, each field allowing null next to its own values: the form such harnesses accept for scalar fields. A `list` or `dict` shorthand gives an open array or object, which a harness that enforces schemas refuses: give such a field its full schema, `items` and closed `properties` included, or better, put the content in a file. A field's schema stands alone, without `$ref`. Its description says which go with which status, and the engine drops the nulls before recording. The flow reads the fields as `r.field`. A reply that is not one JSON object with a known `status`, or a `done` reply that lacks one of the fields or carries the wrong `type`, goes back to its executor once: `flow.py` prints a correction that the orchestrator sends into the executor's session, asking for the JSON only and not for the step's work again. A second such reply, or an executor that cannot be asked again, is recorded as `failed`, `invalid reply: <what is wrong>`, and reaches the flow as a failure. The check reads the schema written for that launch, not the current declaration. The engine checks nothing but the type: `enum`, `minimum` and the rest bind only where the harness enforces the schema, so the flow handles a value outside them. Keep to `type`, `enum` and `description` unless you know the harnesses the runbook runs in: some refuse a schema with `minimum` or `minLength` in it.
- `side_effects`: what the step does outside the tree and `<run>`: `'commit'`, `'PR comment'`. Such a step is never relaunched by the engine alone. Its `failed` or `blocked` reply, and its interruption, do not reach the flow: `flow.py` asks for the human's yes first, and `relaunch` opens the next attempt.
- `writes`: names of the files the step writes, `['checks.md']`. Each launch writes its own file, `<run>/<NN>-checks.md`, `NN` being its record's place in `state.json`: `02-checks.md`, then `04-checks.md` after a fix. Groups and items take places too, so the numbers have gaps where a group opened. Nothing is overwritten, and the run directory lists in the order things happened. The launch message gives the path as `write checks.md: <path>`. A declared file the launch did not write is not in `files(r)` of its result, nor in an index, and a later `reads` of the name gets the latest file that was written; the reply's line in `progress.md` says `did not write checks.md`.
- `reads`: names of the files the step reads. Another step's output is passed as the latest such file in the calling generator's scope, `read checks.md: <path>`, or as absent, `read checks.md: absent, no earlier step wrote it`; a step that reads its own name gets its previous pass in that scope. The scope is under Files. A step inside a group does not see its pass from an earlier group, which sits in that group's history: the flow passes it in, `a=review(previous=reviews.a)`, and the step reads `read previous/review.md: <path>`. A name no step writes is an input file under `<run>` and is passed as it is. `'working tree'` is not passed; it is there for the reader of `flow.py`.

## `rb.human(name, ...)`

A question to the human. No executor. `rb.human` returns the step, and the flow asks it with `a = yield ask_rounds()`. `flow.py` prints the question and the orchestrator asks it.

- `question`: the text, with the choices in it if there are any. `<run>/<name>` in it becomes the latest file of that name in the calling generator's scope, as for `reads`, and any other `<run>` the run directory.
- `choices`: the strings `a.choice` is one of. `flow.py` prints them with the question, the orchestrator maps the answer to one of them, and `answer` accepts it case aside. Without `choices` the step takes free text and `a.choice` is the words whole.
- `reply`: fields the orchestrator takes from the human's words next to the choice, declared as a step's `reply` is, with `choice` refused next to `status` and `reason`: `{'rounds': {'type': 'integer', 'default': 1, 'description': 'how many more fix rounds'}}`. `flow.py` prints each with its type and description, and `answer` then takes a JSON object, `'{"choice": "more rounds", "rounds": 2}'`. The flow reads them as `a.rounds`. A field the human did not give is its schema's `default`, or `None`. Only the `type` is checked, and a wrong one is refused, not recorded.
- `writes`: a file name the engine writes the human's verbatim words to, numbered like a step's output, for the steps that follow. The words are kept in `state.json` and the log either way: `answer <call> '<choice>' '<the words>'`, or `-` to read the words from stdin.

## `rb.code(name, ...)`

A step `flow.py` runs itself: a function of `flow.py`, called inside the command that reaches the step, whose return value is recorded as the call's reply. A result a fixed algorithm computes is a code step: the tests, a lint, a build, a git operation. It launches no executor, so it costs no model and no launch, and its result is only as trustworthy as what the function checks. Anything that needs judgement stays an agent step.

```python
import subprocess

from agent_runbooks import Runbook, StepFailed, end


def run_tests(ctx, target):
    with open(ctx.writes['tests.log'], 'w') as log:
        p = subprocess.run(ctx.inputs.checks, shell=True, cwd=ctx.repo, stdout=log, stderr=subprocess.STDOUT)
    return {'status': 'done', 'passed': p.returncode == 0}


tests = rb.code('tests', run=run_tests, reply={'passed': bool}, reads=['brief.md'], writes=['tests.log'])


@rb.flow
def main(ctx):
    r = yield tests(target='auth')
    ...
```

The function is a step and not code in the generator's body because the generator runs again from the top on every command: a command in its body runs on every command. A code step runs once, and its result is recorded with the call.

- `run`: a plain function `run(ctx, **kwargs)`. A generator function, a coroutine function, an async generator function, or anything that cannot be called raises `TypeError` at declaration.
- `reply`, `reads`, `writes`, `side_effects`: as for `rb.step`, checked the same way. There is no `executor`, `prompt` or `inputs`: the run's inputs are on `ctx.inputs`. A `writes` name `<name>.code.log` raises `ValueError`: that is the call's own log.
- `ctx`: `inputs`, a copy of the run's inputs; `run`, the run directory; `repo`, the `repo` input; `reads`, each name of `reads` to its path, resolved as for an agent step, or `None` when absent, `'working tree'` left out; `writes`, each name of `writes` to the path to write; `item`, a copy of the fields of the innermost foreach item, or `None` outside a foreach; `log`, the call's log; `attempt`, 1, then 2 after its process was lost; `address`, the call's address. Every path is absolute. Nothing on `ctx` runs anything: the function calls `subprocess` or plain Python.
- Keyword arguments of the call are the function's keyword arguments, as the flow passed them and not copied: a result is the object the yield returned, so `files(r)` and `address(r)` work on it. A signature that does not take them fails the call with a `TypeError`. The record keeps them as an agent call's are, as `<key>.<field>` and `<key>/<name>`, and what the function changes in them reaches neither the record nor the flow, which is replayed from the record.
- The function returns a dict: `status`, `done`, `failed` or `blocked`, a `reason` with a failure, and the reply's fields. It is checked before it is recorded: it is JSON as it is, so a set, a `Path`, a cycle, NaN, or a dict with a key that is not a string fails it; it is a dict with a known `status`; and a `done` result carries every field of `reply` with its `type`. Nulls are dropped, and a failure without a reason gets `no reason given`, as for an agent's reply. A result that fails the check fails the call, `invalid result: <what is wrong>`. There is no correction round: nobody is there to ask.
- `raise StepFailed('3 tests fail')` in the function fails the call with that reason, and `StepFailed(reply={'status': 'blocked', 'reason': ...})` with that reply. Any other exception fails it with `<type>: <message>`, the traceback in its log. A failed or blocked code call reaches the flow as `StepFailed` at its `yield`, as an agent step's does, and one with `side_effects` asks the human first.
- Its stdout and stderr, the descriptors its child processes inherit included, go to the call's log, `<run>/<NN>-<name>.code.log`, and its stdin is at end of file: nothing it prints reaches the orchestrator, which reads `flow.py`'s output as instructions. The working directory, the descriptors and `sys.stdout` and `sys.stderr` are restored after it. The log is not an output: it is not in `files(r)`, `reads` never resolve to it, and it is not passed on. What the next step needs goes into a file the step `writes`.
- `progress.md` gets `<call>: started (pid <N>)` when the call starts, and its result in the line an agent's reply gets.

### How a code step runs

Code runs inside whichever `flow.py` command reaches it: `start`, `reply`, `answer`, `relaunch` or the status call. A command holds `<run>/state.lock` while it reads and writes the state, and releases it while code runs. A replay that reaches a code call records it as `running` with the command's pid, together with the groups and items that replay opened, runs it, records its result, and replays again. The launches and questions a replay proposes are recorded and printed only by a replay that reaches no code. So a chain of code calls runs in one command, and an agent step in a branch next to a code call is launched once the code is done. One code call runs at a time in a command, so a group that fails stops its other code calls before they start.

Another `flow.py` command on the same run, such as a `reply` for a parallel agent step while a long command runs in the background, waits on the lock for moments only. It prints a code call that runs in another command as `running in another flow.py command, code steps: ...`, and it may run another code call that is ready: branches of a parallel and items of a foreach that the flow made concurrent may run in different processes at once, as agents do. No call is run by two commands.

At most once per attempt. A recorded result is never run again, but the process can end after the function returned and before its result was saved. The next command closes a running code call whose process is gone, by its pid, as `interrupted`, with the note `owner process lost`, and runs the next attempt, `@2`, at once; with `side_effects`, the human is asked first, as for an interrupted agent step. A call whose previous attempt lost its process too is closed `failed`, `the owner process disappeared without saving a result twice`, and no further attempt opens; a relaunch on the human's yes counts afresh. So a code step without `side_effects` tolerates a repeat: a function that creates a worktree finds the one it made. The function waits for the child processes it starts before it returns. The engine kills nothing, and does not promise that a repeat after a kill is safe. The pid alone identifies the process, so a pid reused by another process keeps the call waiting.

A code call costs what an agent reply costs in its own command: one replay, one load and two saves of `state.json`. A chain of K code calls in one command costs what K replies cost in K commands; the CHANGELOG entry of 2.1.0 gives a measurement.

### Worktrees

Steps that change files can run side by side, each in a worktree of its own. Code steps create the worktrees, run the tests in them, merge them back one after another and remove them; an agent step works in the worktree its message names. The executor's description says so: `working directory = the worktree from the message, else repo`.

```python
import os
import subprocess

from agent_runbooks import Runbook, StepFailed, end, foreach

rb = Runbook()

rb.inputs(repo=str, checks=str)

coder = rb.executor(
    'coder', 'general-purpose, on the model of the main session, working directory = the worktree from the message, else repo'
)


def git(ctx, *args, cwd=None):
    return subprocess.run(['git', *args], cwd=cwd or ctx.repo, check=True, stdout=subprocess.PIPE, text=True).stdout


def record_base(ctx):
    return {'status': 'done', 'commit': git(ctx, 'rev-parse', 'HEAD').strip()}


def create_worktree(ctx, base):
    path = os.path.join(ctx.run, 'worktrees', ctx.item.key)
    if not os.path.isdir(path):  # a repeat finds the worktree it made
        git(ctx, 'worktree', 'add', '-B', f'migrate/{ctx.item.key}', path, base)
    return {'status': 'done', 'path': path}


def run_tests(ctx, worktree):
    with open(ctx.writes['tests.log'], 'w') as log:
        p = subprocess.run(ctx.inputs.checks, shell=True, cwd=worktree, stdout=log, stderr=subprocess.STDOUT)
    return {'status': 'done', 'passed': p.returncode == 0}


def merge_worktree(ctx, path):
    branch = git(ctx, 'branch', '--show-current', cwd=path).strip()
    if subprocess.run(['git', 'merge', '--no-edit', branch], cwd=ctx.repo).returncode == 0:
        return {'status': 'done', 'conflict': False}
    if not git(ctx, 'diff', '--name-only', '--diff-filter=U'):
        raise StepFailed(f'git merge {branch} failed without a conflict')
    git(ctx, 'merge', '--abort')
    return {'status': 'done', 'conflict': True}


def remove_worktrees(ctx, done):
    for item in done.items:
        if item.status == 'done' and os.path.isdir(item.reply['worktree']):
            git(ctx, 'worktree', 'remove', '--force', item.reply['worktree'])
    return {'status': 'done'}


base_commit = rb.code('base-commit', run=record_base, reply={'commit': str})
wt_create = rb.code('wt-create', run=create_worktree, reply={'path': str})
tests = rb.code('tests', run=run_tests, reply={'passed': bool}, writes=['tests.log'])
merge = rb.code('merge', run=merge_worktree, reply={'conflict': bool}, side_effects='merges a branch into the checked-out one')
cleanup = rb.code('cleanup', run=remove_worktrees)
migrate = rb.step('migrate', executor=coder, prompt='prompts/01-migrate.md', reads=['working tree'], writes=['migrate.md'])
resolve = rb.step('resolve', executor=coder, prompt='prompts/02-resolve.md', reads=['working tree'], writes=['resolve.md'])


@rb.flow
def main(ctx):
    base = yield base_commit()

    def migrate_one(ctx, item):
        wt = yield wt_create(base=base.commit)
        yield migrate(worktree=wt.path)
        r = yield tests(worktree=wt.path)
        if not r.passed:
            raise StepFailed(f'the tests fail in {wt.path}')
        return {'worktree': wt.path}

    done = yield foreach('sites', over='sites.json', body=migrate_one, on_item_failure='skip')
    for item in done.items:
        if item.status == 'done':
            m = yield merge(path=item.reply['worktree'])
            if m.conflict:
                yield resolve(worktree=item.reply['worktree'])
    yield cleanup(done=done)
    return end('ready', 'read <run>/sites.index')
```

- The base commit is recorded before the foreach, so every worktree starts from the same commit, however long the items take.
- An agent step gets the worktree as a line of its message, `worktree: <path>`, from its keyword argument. Its prompt has it commit its change on the worktree's branch: a merge takes commits, not files left in a worktree.
- A known conflict is `done` with `conflict: true`, and the flow decides what follows, here an agent step that merges and resolves it. A failed `merge`, a step with `side_effects`, would ask the human about a relaunch instead. A git failure that is not a conflict fails the call.
- The merges run one after another, into one checked-out branch, after every item has ended.
- `cleanup` removes the worktrees of the items that are done, and keeps those of failed items for the human to look at.

## `@rb.flow` and `yield`

`@rb.flow` marks the flow: a generator function `main(ctx)`, one per runbook. A function that is not a generator raises `TypeError`. The run begins where the generator begins and ends where it returns.

Every command runs `main(ctx)` again from the top against the calls recorded in `state.json`, and drops the generator at the first call that is not done. A call that is done returns its recorded result at once, so the generator walks to where the run is, and a call with no record is launched. Local variables are recomputed on that walk, not kept. In the example above, `budget, used = ctx.inputs.maxFixRounds, 0` runs on every command, `used += 1` runs once per round already recorded, and `budget += a.rounds` takes `a.rounds` from the recorded answer, so both stand where the run left them.

So the generator computes from `ctx` and what its yields return, and acts on the world only through its yields:

- An action in its body repeats on every command: a file it writes, a command it runs, a `print`. An action belongs in a code step.
- A dropped generator is closed, so a `finally` or a `with` around the `yield` it stopped at runs on every command, once or more, not at the end of the run. A bare `except:` around a `yield` catches that close, `GeneratorExit`; `except StepFailed` is the one a flow needs.
- The clock, a directory listing, a file it reads, the order of a `set`, which differs between processes, or module-level state it changes gives another answer on the next command, and the replay goes another way than the run went.
- A branch on what a step found is a reply field: the field is checked against the step's schema and recorded with the call, while a file is read again on every command and can change on disk in between. The engine does the same for the `over` of a foreach, see Groups.

- `r = yield step(...)` launches the step and returns once its reply is `done`. `r` has the reply's fields as attributes, `r.findings`, and `r.choice` for a human step, and nothing else. What the engine knows of the call is read through two functions: `files(r)`, each name the launch wrote with its path, and `address(r)`, the call's address. Both take every result a yield returns and an item of a foreach, and raise `TypeError` for anything else.
- Keyword arguments of a call are lines of its launch message. A JSON value is `<key>: <value>`: `round=used + 1` is `round: 1`. A result, or a dict or list holding results, passes its fields and files, see Files. `executor=` picks another executor for this call: what `rb.executor` returned, or the name of a declared one. An undeclared name, or any other value, stops the command as a defect of `flow.py`.
- A `failed` or `blocked` reply is thrown into the generator at that `yield` as `StepFailed`: `e.id` is the call's address, `e.status` and `e.reason` are the reply's, `e.reply` is the whole reply. A `try`/`except StepFailed` around the `yield` handles it. A failure no generator handles ends the run `failed`, and the human is pointed at `progress.md`. A generator raises `StepFailed('<reason>')` itself to fail its branch or item.
- `return end('status', 'read <run>/checks.md')` ends the run. The second argument is what `flow.py` tells the orchestrator to report, with `<run>/checks.md` replaced by the latest `checks.md` in `main`'s scope and any other `<run>` by the run directory. A file written inside a group is not in that scope: name it through the result, `f'read {files(reviews.a)["review.md"]}'`. Returning anything else from `main` is a defect of `flow.py`. Describe every status under End of run in `SKILL.md`, `failed` included.
- A loop is a `while` with its budget in local variables: `budget, used = ctx.inputs.maxFixRounds, 0`, `used += 1` per round, `budget += a.rounds` when the human grants more. A retry is the same around a `try`/`except StepFailed`, counting its failures. Every loop has a budget and a way out: a human step, or a `return end(...)`.

## Groups

`reviews = yield parallel('reviews', a=review(), b=second_review)` launches the branches together and returns once every branch is done. A branch is a step call, or a generator function `(ctx)`, a chain of calls whose `return` value is the branch's result. The result has one attribute per branch key: `reviews.a` is the call's result, `reviews.b` what `second_review` returned. The result has nothing else on it, so any branch key the naming rule allows works. Branches read what the generator around them wrote before the group. A branch that changes the working tree runs in a group where no other branch reads the tree; branches that do not touch it may run next to it.

`done = yield foreach('fixes', over='fixes.json', body=fix(), max_concurrent=1, on_item_failure='skip')` runs the body for each item of the JSON array in the file `over`, and returns once every item has ended.

- `over`: a name resolved as `reads` are, or a path, such as a result's file. The file holds objects with a unique `key` of letters, digits, `_ . -`, at most `max_items` of them, 50 by default. The engine reads it once, when the foreach opens, and records its items with the foreach, so a later change to the file does not reach the run. A file that is absent, not such an array, or too long fails the foreach with that reason, which the flow can catch as any `StepFailed`.
- `body`: a step call, whose reply is the item's reply, or a generator function `(ctx, item)` that reads the item's fields as `item.<field>` and whose `return` value, a JSON value, is the item's reply. A step launched in an item gets the fields of the innermost item as lines of its message, `key: f1`, `title: off by one`.
- `max_concurrent`: how many items run at once, 4 by default. A body that changes the working tree takes 1.
- `on_item_failure`: `'fail'`, the default, fails the foreach when an item fails. `'skip'` keeps the failure as the item's outcome and goes on.
- The result has `items` in the order of the file, each with `key`, `status` (`done` or `failed`), `reply` and `reason`; `files(item)` gives the files of the item's latest done launches. `files(done)` holds the index, see Files.

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

- `interrupted <call>` takes a running agent step, never a code step: `flow.py` closes a code call whose process is gone itself, see `rb.code`. A step without `side_effects` is launched again at once. One with `side_effects` asks the human first, as its failed reply does: ``ask the human: step `main/commit` has side effects and ended interrupted. On yes: <python> <runbook>/flow.py <run> relaunch main/commit. On no: <python> <runbook>/flow.py <run> log '<their decision>' and stop.``
- `relaunch <call>` takes only a call the run is asking the human about, never one in a group that has failed.
- A question asked inside a group that fails is closed as `cancelled`, and `flow.py` prints ``withdraw the question `main/group/c/ask`: its group failed; tell the human no answer is needed``.
- A step with `side_effects` whose question a failing group drops is noted `cancelled with its group`, in its record and in `progress.md`, and `relaunch` refuses it. When the human was asked about it, `flow.py` prints ``withdraw the question whether to relaunch step `main/group/p`: its group failed, and it is not relaunched; tell the human no answer is needed``.

## Files of a run

- `state.json`: format 2. The run's inputs, its status (`running`, `waiting_for_human` or the end status), and `calls`, one record per attempt in the order they were opened: steps, human steps and code steps, and the records of parallels, foreaches and items. Each holds `id` (the address), `attempt`, `kind` (`step`, `human`, `code`, `parallel`, `foreach` or `item`), `step` and `status`; a launch holds the contract its executor was given, the replies, and the replies that did not pass the check; a code call the same contract without an executor, its result, the `pid` of the process that runs it and its `log`. Written by `agent_runbooks.py` only. An engine that reads another format refuses the run, and `start` refuses a directory that holds a `state.json`.
- `progress.md`: the inputs and a text log, one line per launch, reply, question, answer and end. Append-only, for humans.
- The steps' output files, `<NN>-<name>`, the indexes, `<NN>-<name>.index.json`, the logs of code calls, `<NN>-<name>.code.log`, and the input files under their own names.
- `schemas/<NN>-<step>.json`: the JSON Schema of each launch's reply. Written by `agent_runbooks.py` only.
- `state.lock`: the lock a command holds while it reads and writes the state.

## Checking

`python3 flow.py --check` from the runbook directory: a flow is declared, `repo` is an input, every `inputs` name is declared, every prompt file exists, every human step has a question, and `prompts/common.md` exists when an agent step is declared. Declaration mistakes, such as a malformed name, a step's executor given by name, a reserved or untyped reply field, a lone string, or a code step's `run` that is not a plain function, raise when `flow.py` is loaded, naming the step and the parameter. Routes are Python and are not checked.

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

A file the flow reads, such as the `over` of a foreach, has to exist before the reply of the step that writes it: write it at the path the launch printed, `write fixes.json: /tmp/try/05-fixes.json`. Feed the walk every path: a failed reply, an invalid reply twice, an interruption, a human answer, the loop budget running out, an item that fails. Code steps run during the walk for real, so point `repo` at a throwaway copy when they change it, and make each fail once.
