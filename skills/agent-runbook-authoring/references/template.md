# Runbook template

A runbook is a skill directory:

```
runbook-<name>/
  SKILL.md          the procedure for the orchestrator: inputs, rules, executors, end of run
  flow.py           inputs, executors, steps and transitions in Python, and the command the orchestrator runs
  runbook.py        the engine behind flow.py, copied from this skill
  prompts/
    common.md       what every executor reads first: project preamble, executor constraints
    <nn>-<step>.md  one file per step: the task, the deliverable, what the reply's fields mean
```

Angle-bracket parts are filled by the author. Three things are copied from this skill as they are, so the runbook runs where this skill is not installed: the "Execution rules" section below into `SKILL.md`, the "Executor constraints" section below into `common.md`, and [`runbook.py`](runbook.py) into the runbook directory. `flow.py` is written per runbook, see [`flow-language.md`](flow-language.md). It declares the inputs and the executors too, so `SKILL.md` has no Executors section.

Three placeholders are never filled by the author. `<repo>` is the repository the steps work in: the directory the runbook is invoked in, or an input when the code lives elsewhere, in a worktree for instance. It is always among the run's inputs. `<run>` is the run directory. `<skill>` is the directory this `SKILL.md` was loaded from. In prompts, repository files are `<repo>/…`, input files are `<run>/…`, and step outputs are named without a path, `checks.md`: the launch message gives each its numbered path.

## SKILL.md

````markdown
---
name: runbook-<name>
description: <What this runbook does, in one sentence, and the inputs it takes.>
---

# <Name>

<One paragraph: what the run leaves behind and in what state.>

## Inputs

- `repo`: <the repository root, or how it is chosen>
- `<input>`: <where it comes from, how to ask if missing. A text longer than a line is saved to `<run>/<name>.md` and passed to `start` as that file name>
- `run-id`: given only to resume an interrupted run
- Smoke input: <a small input with a predictable path through the flow. Who cleans up after it>

## Run directory

`<the directory this session started in>/.agent-runbooks/runs/<YYYYMMDD>-<slug>/`. Slug: <how it is formed from the inputs, kebab-case>.

## Execution rules

<copied from agent-runbook-authoring/references/template.md>

## Steps

Declared in `flow.py` next to this file: inputs, executors, steps with their prompts, what each reads and writes, and the transitions between them. `flow.py` drives the run and prints, for every launch, the executor's model and tool. This section is a pointer, not a copy.

## End of run

- `<status>`: <what it means, what the human reads next, what the human does>
- `failed`: <always present: a step failed or was blocked, or the human stopped the run>
````

## Execution rules

You are the orchestrator of this run. Orchestrating takes a session that can launch subagents and learn when they finish. If yours cannot, stop and say so. During the run you do only these things:

- run `python3 <skill>/flow.py …` as written below
- save the input files the Inputs section names into the run directory `start` created
- launch steps as subagents, with the message `flow.py` prints, and with the reply schema where your tool takes one
- send an executor the correction `flow.py` prints for its reply
- read a step's output file only to quote it to the human
- ask the human, and report the end of the run
- show the human the run's status, as the Status group below says

Nothing else. No command beyond these, no reading of `flow.py`, `state.json`, `progress.md` or the prompt files, no editing of anything in the run directory. `<skill>` is the directory this `SKILL.md` was loaded from.

Starting

- The run directory is `.agent-runbooks/runs/<YYYYMMDD>-<slug>` under the directory your session started in, with today's local date. Run `python3 <skill>/flow.py <run> start '<the inputs you were given, as one JSON object>'` first, with every input the Inputs section saves to a file given as that file name: it creates the directory, or refuses because it exists, in which case add `-2`, `-3` to the name and start again. Then save the input files the Inputs section names into the directory it created.
- Resume: `python3 <skill>/flow.py <run>`.

flow.py

- It keeps the state of the run and prints what to do: which steps to launch, with which executor and what message, whom to wait for, what to ask the human, or that the run has ended. Do all of what it prints, then wait. Every command it asks you to run next is printed in full.
- An executor is a named way to launch a step: a new subagent through your own subagent tool, with the model and settings its description gives, or through another tool when the description names one.
- A step's message arrives: take the last JSON object in it and run the `reply` command printed for that step with that JSON. No JSON object in the message: pass `{}`. Any JSON argument, for `start`, `reply` or `answer`, with a single quote (`'`) in it goes through stdin: put `-` in place of the JSON and pass the JSON on stdin, in a POSIX shell with a quoted heredoc. One argument of a command at most.
- The human answers a question: map the answer to one of the choices `flow.py` listed, ask again if none fits, and run the `answer` command printed with that choice and the human's words verbatim. A free-text question takes the words alone. When `flow.py` lists fields with the question, the command takes a JSON object in place of the choice: the choice and the fields the human gave. A field they did not give is left out, never guessed. `flow.py` keeps the words and writes them where the steps that follow read them.
- A reply did not pass the check: `flow.py` prints a correction. Send it to the subagent that ran the step, as a follow-up message in its session, not as a new launch, and wait for its answer the way you wait for a step. Its answer goes to the same `reply` command. If your tool cannot send a message to a subagent that has finished, run the command `flow.py` printed for that case instead. An executor that only relays another agent's reply passes the correction on to that agent, in its session, and returns its answer verbatim.
- A running step's executor is gone, because the session is new or the tool reports it dead: `flow.py <run> interrupted <section>`. Executors you launched in this conversation are not gone: wait for them.
- You departed from these rules, or did something `flow.py` does not know about: `flow.py <run> log '<one line>'`.

Launching

- Launch every step `flow.py` lists, with the executor it names, and send exactly the text between `--- message ---` and `--- end of message ---`. Add nothing, apart from lines your harness or your own rules require in every subagent prompt. An executor that only relays another agent's reply gets one more line: "Return the agent's final message verbatim."
- Steps `flow.py` lists together are launched together, in one turn.
- The message names a `reply schema` file. If your subagent tool can hold a subagent's final message to a JSON schema, give it this one: the path, or the file's content when the tool takes only that. Otherwise leave the file to the executor.

Waiting

- Waiting costs zero turns. Pick the branch that matches your harness.
  - You can launch a subagent in the background and get woken up when it finishes, and ending your turn does not end your session: launch the ready steps that way and end your turn. On a wake-up, record the reply, do what `flow.py` prints, end your turn.
  - Otherwise, which includes running nested in another agent where the end of your turn is the end of your run: launch the ready steps in the foreground, in parallel if your harness allows several calls at once, otherwise one after another. Set every timeout or yield parameter your tool accepts to 24 hours or its maximum. If a call returns while the step still runs, call the wait again and do nothing else.
- Either way the step's completion is the only event. No polling, no sleeping, no reading ahead, no status messages while it runs. An hour-long step is a normal working state.

Status

- If a skill named `runbook-viewer` is available in this session, load it before `start` and do what its "During a run" section says: the status is printed in the same turn as the `flow.py` command that recorded an event, never while waiting for a step. Without the skill, there is no status.

Human steps and side effects

- `flow.py` tells you when to ask the human and what. Ask, then wait for the answer the way you wait for a step. A failed step with side effects is relaunched only after the human says yes: `flow.py <run> relaunch <section>`.

Ending

- When `flow.py` prints `end`, report what it says to the human: the status, the run directory, the file to read. The run is over and these rules no longer bind you.

## common.md

````markdown
# Common

<Project preamble. Where the conventions file is. What the inputs are and where the step finds them under <run>. What "the changes" means for review: which diff, what to exclude. How the project checks are run, what counts as skipped, and where their output goes in a report. Four or five sentences.>

## Executor constraints

<copied from agent-runbook-authoring/references/template.md>
````

## Executor constraints

You run one step of a larger procedure. The project's procedures for task cycles, review and commit are not yours to start. Change repository files only as your step instructs, and leave the changes uncommitted unless it instructs a commit. A file name in your step's prompt is a name, not a path: the launch message gives a `write <name>: <path>` line for each file you write, and a `read <name>: <path>` line for each file you read, or says it is absent. Write other files only at the paths your launch message gives. Commits, pushes, comments, tickets and other external writes happen only when your step instructs them. Nobody will answer a question. If you cannot proceed, stop and reply `blocked`.

Your final message is one JSON object that fits the schema in the file your launch message gives as `reply schema`, and nothing else. `status` is `done` when the deliverable exists as described, `failed` when you tried and it does not, `blocked` when you cannot proceed. The schema lists every property as required: with `done`, `reason` is null and the other fields are set; with `failed` or `blocked`, `reason` is one line and the other fields are null. Explanations and evidence go into your step's output file.

## Step prompt file

````markdown
# <Step name>

<The task. Which files under <run> and <repo> to read. What to produce and where, under <run>. What a good result looks like. Two to six short paragraphs. A step that runs commands says where their exit codes and failing output go in its report. A file another step parses has its format stated, including what to write when there is nothing.>

<What `done` means for this step, and for each field the step's `reply` declares, what it says and how it is counted. One line each.>
````

The reply's JSON Schema comes from the step's `reply` in `flow.py`: the engine writes it to `<run>/schemas/<step>.json` and gives the executor its path in the launch message.

## Notes for the author

- Add `.agent-runbooks/` to the `.gitignore` of the directory the runs will live in when you create the runbook.
- A step that runs a command and reports the outcome (`passed`) writes the command's exit code and failing output to a file under `<run>` and changes nothing in the tree. The cheapest model that can run the command is enough for it. A step that judges needs the model the judgement needs. A check that exists but cannot run (no binary, no dependencies installed) is a failure, not a skip.
- A step with side effects applies a file a former step wrote and takes no judgement calls. If it needs judgement, split it.
- Only one step at a time changes the working tree, and nothing reads the tree while it changes. Parallel readers are fine.
- Every pass writes a new numbered file, so nothing is overwritten. A step that needs the previous pass lists its own output in `reads` and tells a first pass from a later one by that file being absent, never by a counter it is not given.
- A prompt shared by two steps takes its differences as `inputs` (`('id-prefix', 'b')`) and `writes` (`review-b.md`).
- A step that earlier replies can make pointless gets `skip`, and the prompts of the steps after it say what to do when its file is absent. Skipping is cheaper than launching an executor to report that there is nothing to do.
- Step names are the names of their outputs where possible: step `checks` writes `checks.md`. The run directory then reads as the run: `00-preflight.md`, `01-implement.md`, `02-checks.md`, `03-fix-checks.md`, `04-checks.md`.
- A smoke input that takes the shortest path proves the launch, not the flow. Add a second one that reaches the loops and the human steps before the runbook is trusted with real work.
