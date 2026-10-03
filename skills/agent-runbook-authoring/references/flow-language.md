# Writing flow.py

`flow.py` declares the steps and transitions of a runbook in Python, on top of `runbook.py`, which is copied from this skill unchanged. `flow.py` is also the command the orchestrator runs: `runbook.py` gives it `start`, `reply`, `answer`, `interrupted`, `relaunch`, `log`, a status call and `--check`.

```python
#!/usr/bin/env python3
"""Steps and transitions of runbook-<name>. Run with --help for the commands."""
from runbook import Runbook, end, parallel

rb = Runbook()

rb.inputs(ticket=str, brief=str, repo=str, package=str, coder='main', maxFixRounds=2)

rb.executor('main', 'the model of the main session, through the subagent tool, high effort where it has one')
rb.executor('light', 'the cheapest fast model, through the subagent tool, low effort')
rb.executor('other', 'a model from another vendor, through the tool that launches it, working directory = repo')

coder = lambda s: 'other' if s.inputs.coder == 'other' else 'main'

rb.start('preflight')

# An illustration of the language, not a real runbook. A complete one: runbook-review-loop/ next to this file.

rb.step('preflight', executor='light', prompt='prompts/00-preflight.md', inputs=['package'],
        reads=['working tree'], writes=['preflight.md'], reply={'clean': bool},
        next=lambda r, s: 'implement' if r.clean else 'ask-dirty')

rb.step('checks', executor='light', prompt='prompts/02-checks.md', inputs=['package'],
        writes=['checks.md'], reply={'passed': bool},
        next=lambda r, s: parallel('review-a', 'review-b') if r.passed
        else ('fix-checks' if not s.done('fix-checks') else end('failed', 'read <run>/checks.md')))

for letter in 'ab':
    rb.step(f'review-{letter}', executor='main', prompt='prompts/04-review.md',
            inputs=[('id-prefix', letter)],
            writes=[f'review-{letter}.md'], reply={'findings': int},
            next='triage')

rb.step('triage', executor='main', prompt='prompts/05-triage.md', after=('review-a', 'review-b'),
        reads=['review-a.md', 'review-b.md'], writes=['triage.md'], reply={'to_fix': int},
        skip=lambda s: 'polish' if all(s.reply(f'review-{x}').findings == 0 for x in 'ab') else None,
        next=lambda r, s: 'polish' if r.to_fix == 0 else 'fix')

rb.human('ask-rounds', writes='rounds.md', choices=['one more round', 'stop'],
         question='Fix rounds are spent, see `<run>/verify.md`. One more round, or stop here?',
         next=lambda choice, s: 'fix' if choice == 'one more round' else end('needs_attention', 'read <run>/verify.md'))

if __name__ == '__main__':
    raise SystemExit(rb.main())
```

## `rb.inputs(...)`

The inputs of a run, by name. A type (`str`, `int`, `bool`) is a required input. A value is a default of its type. `start` takes the inputs the orchestrator was given, applies the defaults, refuses wrong types and unknown names, and records the result. `repo` is always among them. Conditions read them as `s.inputs.<name>`.

## `rb.executor(name, description)`

What the orchestrator launches for an executor name: model, tool, effort, working directory. The description is printed with every launch, so the orchestrator never looks it up. A step's `executor` is a declared name or a function `(s) -> name` that picks one by the inputs.

## `rb.start(name)`

The step a run begins with. Required: `--check` fails without it.

## `rb.step(name, ...)`

A step an executor runs. The name is its id: in `next`, in `after`, in `s.done`, in the launch messages and in `state.json`. A step that runs again gets a counter: `fix`, `fix-2`, `fix-3`.

- `executor`: a name declared with `rb.executor`, or a function `(s) -> name`.
- `prompt`: the step's prompt file, relative to the runbook directory.
- `inputs`: what the launch message carries beyond `repo`, `run` and the files. A string is the name of a run input, passed with its value. A `(key, value)` pair is passed as it is.
- `reply`: `{field: type}` the executor's JSON carries beyond `status`. The step's schema in the prompt file requires exactly these with `done`. `next` reads them as `r.field`. A `done` reply that lacks one of them or carries the wrong type is recorded as `failed`, `invalid reply: <what is wrong>`.
- `next`: where to go on a `done` reply. A step name, `parallel(...)`, `end(...)`, or a function `(r, s)` returning one of those. `r` is the reply with its fields as attributes, `s` is the state: `s.done(name)` and `s.inputs.<name>`.
- `on_failure`: the same, for `failed` and `blocked` replies. Without it they end the run as `failed`, and the human is pointed at `progress.md`.
- `after`: step names whose latest sections must all be `done` before this one launches. Use it on the step that follows a `parallel(...)`. If one of them is not `done`, the run ends `failed`.
- `side_effects`: what the step does outside the tree and `<run>`: `'commit'`, `'PR comment'`. Such a step is never relaunched by the orchestrator alone. On a reply that is not `done`, `flow.py` asks for the human's yes first.
- `skip`: a function `(s) -> target or None`, called when the step is reached and its `after` is satisfied. A target means the step is not launched and the run goes there instead: triage has nothing to do when both reviews report `findings == 0`. Its file is then absent for the steps after it, and their prompts say what to do without it.
- `writes`: names of the files the step writes, `['checks.md']`. Each launch writes its own file, `<run>/<NN>-checks.md`, `NN` being the launch's place in the run: `02-checks.md`, then `04-checks.md` after a fix. Nothing is overwritten, and the run directory lists in the order things happened. The launch message gives the path as `write checks.md: <path>`.
- `reads`: names of the files the step reads. Another step's output is passed as the latest `done` launch's file, `read checks.md: <path>`, or as absent when none has written it yet; a step that reads its own name gets its previous pass. A name no step writes is an input file under `<run>` and is passed as it is. `'working tree'` is not passed; it is there for the reader of `flow.py`.

## `rb.human(name, ...)`

A question to the human. No executor. `flow.py` prints the question with `<run>` replaced by the run directory, and the orchestrator asks it.

- `question`: the text, with the choices in it if there are any.
- `choices`: the strings `next` compares against. `flow.py` prints them with the question, the orchestrator maps the answer to one of them, and `answer` accepts it case aside. Without `choices` the step takes free text and `next` gets the words whole.
- `next`: a function `(choice, s)`. `choice` is one of `choices`, or the words themselves.
- `writes`: a file name the engine writes the human's verbatim words to, numbered like a step's output, for the steps that follow. The words are kept in `state.json` and the log either way: `answer <section> '<choice>' '<the words>'`, or `-` to read the words from stdin.

## Targets

- a step name: launch it. A step that already ran runs again as a new section (`fix-2`, `fix-3`) that writes new numbered files, and `s.done(name)` grows. That is a loop: give it a budget and a way out.
- `parallel('review-a', 'review-b')`: launch both. They write different files, and at most one changes the working tree.
- `end('status', 'read <run>/checks.md')`: the run ends. The second argument is what `flow.py` tells the orchestrator to report, with `<run>/checks.md` replaced by the latest numbered `checks.md` and any other `<run>` by the run directory. A human step's question gets the same replacement. Describe every status under End of run in `SKILL.md`, `failed` included.

## `s.done(name)` and `s.reply(name)`

`s.done(name)` is how many sections of that step are `done` so far, the one whose reply is being routed included. `s.done('fix-checks')` is 0 before the step has run once. `s.done('verify') < s.inputs.maxFixRounds` is a loop budget.

`s.reply(name)` is the reply of that step's latest section reached so far, with its fields as attributes, or `None` when the step has not finished a section yet. It lets a `skip` or a `next` look at what an earlier step reported: `s.reply('review-a').findings`. Every input the run needs in a condition is passed at `start`, defaults included.

## Files of a run

- `state.json`: the sections with their statuses and replies. Written by `runbook.py` only.
- `progress.md`: the inputs and a text log, one line per launch, reply, question, answer and end. Append-only, for humans.
- The steps' output files, `<NN>-<name>`, and the input files under their own names.

## Checking

`python3 flow.py --check` from the runbook directory: a start step is set and declared, `repo` is an input, every executor name and every `inputs` name is declared, every prompt file exists, every `after` and every literal target is declared, `skip` is a function, every human step has a question, `prompts/common.md` exists. Conditions are Python; a wrong field name fails at run time with the reply that caused it, so walk the Flow by hand before the first run:

```
python3 flow.py /tmp/try start '{"ticket": "T", "brief": "b", "repo": "/tmp/x", "package": "p"}'
python3 flow.py /tmp/try reply preflight '{"status": "done", "clean": true}'
python3 flow.py /tmp/try reply implement '{"status": "done"}'
```

Feed it every branch: a red check, a failed reply, a human answer, the loop budget running out.
