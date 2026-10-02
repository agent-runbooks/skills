---
name: agent-runbook-authoring
description: "Design, write, or review a runbook: a SKILL.md procedure that a main session executes step by step through subagents, coordinating through files. Use when asked to write a runbook, to turn a workflow script or a repeated multi-agent procedure into one, or to check an existing runbook or the progress.md of one of its runs."
---

# Agent runbooks

A runbook is an ordinary skill whose body is a procedure: a flow of steps, who executes each, what each reads and writes, where to stop and ask a human. No DSL and no runner: the steps are prompts, and the model does the work. One small Python script, `flow.py`, keeps the model on the procedure. It holds the transitions and the state of a run, and it tells the orchestrator what to launch next, so the part prose drifts on, which branch comes next, is never left to reasoning. The session that invokes the runbook becomes its **orchestrator**. Every step but a human one is carried out by a subagent, the **executor**. State lives in a run directory, `.agent-runbooks/runs/<run-id>/`, and steps hand work to each other through files there. Coordination goes through the environment, not through the orchestrator's context.

A finished runbook is self-sufficient. It carries its own execution rules, executor constraints, its steps and transitions as a small Python file, and a copy of the engine that runs them, so it runs where this skill is not installed. This skill is for the author, not the orchestrator.

## When a runbook fits

Write one when the procedure has three or more steps, will be repeated, and involves several executors (models, harnesses, roles). A one-off task or a single subagent call is a prompt, not a runbook.

## What the orchestrator sees

Design for a reader that reads the runbook's `SKILL.md` and what `flow.py` prints, and copies each step's JSON reply into `flow.py`. It opens no other file and runs no other command. Transitions are computed by `flow.py` from the replies, never reasoned about. So anything you want checked lives in a prompt, or in a following step that runs the command and reports a field. Anything the next step needs to know lives in a file the next step's prompt names. The shape of a report is taken on the executor's word. Facts about the code and the outcome of commands are checked by a step.

## Writing a runbook

Work through these in order. The output is a skill directory shaped like [`references/template.md`](references/template.md). A complete one: [runbook-task-cycle](https://github.com/Nodge/skills/tree/main/skills/runbook-task-cycle).

1. **Inputs, slug, smoke input.** Name each input and where it comes from. Define the slug the run directory takes from the inputs. Pick a smoke input with a predictable path through the flow. It is the first thing you run.
2. **Flow first.** Write `flow.py` with the API in [`references/flow-language.md`](references/flow-language.md): a `step` per step with its `next` on the reply's fields, what launches together, what joins with `after`, where a loop points up and what its budget is, which branches `end` the run and with what status. `flow.py` is the only place steps and transitions live. Write it before the prompts.
3. **Declarations.** In `flow.py`: the run's inputs with their types and defaults, each executor as a name with the model, tool, effort and working directory the orchestrator launches, and for each step its executor, what it reads, what it writes under `<run>`, the reply fields `next` branches on, the inputs its launch message carries, and `side_effects` if it acts outside the working tree and `<run>` (commit, push, comment, ticket). Reply fields exist for one reason, a branch between two successful outcomes. "Nothing was done" is `failed`, not a zero. A count the next step needs goes to a file. Only one step at a time changes the working tree.
4. **Prompt files.** One per step, plus `common.md`. A prompt stands alone: what to read, what to produce and where, what good looks like, the format of any file another step parses, and a JSON schema for the reply. The schema's `done` branch requires exactly the fields the step's `reply` declares. Name repository files as `<repo>/…` and step outputs by name, `checks.md`: the engine numbers them per launch and passes the paths. Never paste content a file can carry.
5. **Human steps.** Where the orchestrator must ask, add `rb.human(...)` with the question and its choices. Its `next` branches on the choice. If a later step needs the answer's text, give it `writes`.
6. **Copy the fixed parts.** Execution rules into `SKILL.md`, executor constraints into `common.md`, word for word, and `runbook.py` into the runbook directory. Then `python3 flow.py --check` there, and walk every branch by hand with a scratch run the way `flow-language.md` shows. The rationale for what the fixed parts say is below. They carry only instructions.
7. **Cold read.** Give the runbook directory to a fresh subagent that has not loaded this skill, and tell it not to. It reads the runbook as the orchestrator would and answers: what it would do at each step, and where it was unsure. Any hesitation, any place it had to guess, any two readings of one line is a defect in the runbook, not in the reader. Rewrite and read again until it reports none. The prompt is in [`references/review-checklist.md`](references/review-checklist.md).
8. **Checklist and smoke.** Read the text against the checklist in the same file, or hand that to a second agent. Then run the smoke input and read the resulting `progress.md` with the checklist.

## Upgrading a runbook

The `runbook.py` in a runbook is a copy. Its docstring names the source and `__version__` its release, tagged `agent-runbook-authoring/v<version>`. To upgrade, diff the copy against the release it claims, carry any local patch into the new release, and read `CHANGELOG.md` from that version up: a major version changes the `flow.py` API, and its entry says what to change. Then `python3 flow.py --check` and the scratch-run walk of every branch. Whoever changes `runbook.py` itself runs `python3 -m unittest test_runbook` in `references/`.

## Why the fixed sections say what they say

Keep these in mind while writing. They do not go into the runbook.

- **Only the main session orchestrates.** In most harnesses a subagent gets no notification when background work finishes and cannot spawn subagents of its own. Nesting inside a session is one level deep. A subagent that launches something in the background and ends its turn returns a placeholder, and the run proceeds on nothing. A step that runs the checks after the work is what catches such a reply.
- **The orchestrator runs nothing itself during the run.** Not even a `git status`. The bright line is cheaper than judging each case, and it keeps the orchestrator's context small. Twenty steps at a JSON reply each survive compaction. Twenty steps with artifacts pasted in do not. The consequence is the verification model: the orchestrator judges by reply fields, and checks are steps.
- **Waiting costs zero turns.** Any call made while steps run is drift: reading ahead, "preparing" the next step, polling. The completion notification is the only event.
- **Files, not messages.** Launch messages carry paths and scalar inputs, files carry the content. The executor's reply is a few fields, not content. Resume then costs nothing: the files are still there.
- **Executors are fenced in every prompt.** An executor reads the project's instructions file and may decide the project's rituals (task cycles, double review, commit) apply to it. That is recursion. The prompt is the only thing the executor sees, so the fence travels in `common.md`.
- **A section per launch.** `flow.py` records `running` before launch and the reply after. Otherwise, after an interruption, "never started" and "died midway" look the same, and a side-effect step gets re-run.
- **Transitions are computed.** A workflow script branches on typed fields. Prose drifted exactly there: when `s.done(name)` counts, which branch wins, what a failed reply does. So the reply is a small JSON with a schema, `flow.py` branches on its fields in Python, and the orchestrator copies replies in and launches out.
