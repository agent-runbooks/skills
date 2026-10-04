---
name: runbook-review-loop
description: One code change in a git repository. A coder implements a brief, a reviewer reads the changes and runs the checks, and the coder fixes the findings until none are left or the fix rounds run out, every step a subagent of this session. Inputs brief, repo, optional checks and maxFixRounds. Leaves the changes uncommitted.
---

# Review loop

Leaves the brief implemented in a repository, uncommitted, with a report: the last `review.md` of the run says what the changes do and lists the findings still open, if any. Commit and PR happen outside.

Every step runs as a subagent through this session's own subagent tool, the Agent tool in Claude Code. No MCP server is needed.

## Inputs

- `brief`: the change to make, as text. The orchestrator saves it to `<run>/brief.md` and passes `"brief": "brief.md"` to `start`
- `repo`: absolute path of the git repository, the directory this session started in unless the human names another
- `checks`: the shell command that checks the project, run from `repo`, e.g. `python3 -m unittest` or `pnpm test`. Optional: when it is omitted, the executors use the checks the repository's `AGENTS.md` or `CLAUDE.md` names
- `maxFixRounds`: integer, default 2. One round is one fix and the review after it. When the rounds are spent and findings remain, the human is asked, and may grant one more round or several
- `run-id`: given only to resume an interrupted run
- Smoke input, in any small git repository with a clean tree: brief "Add a file `SMOKE.md` with the single line `smoke`", checks `true`. The expected path is the shortest one: the reviewer finds nothing and the run ends `ready`. A second smoke input reaches the loop and the human step: the same brief, checks `exit 1`, `maxFixRounds` 1, and the answer `stop`. The run ends `needs_attention`. The human deletes `SMOKE.md` and the run directory after either

## Run directory

`<the directory this session started in>/.agent-runbooks/runs/<YYYYMMDD>-<slug>/`. Slug: three or four words from the brief in kebab-case, for example `add-smoke-file`.

## Execution rules

You are the orchestrator of this run. Orchestrating takes a session that can launch subagents and learn when they finish. If yours cannot, stop and say so. During the run you do only these things:

- run `python3 <skill>/flow.py …` as written below
- save the input files the Inputs section names into the run directory `start` created
- launch steps as subagents, with the message `flow.py` prints, and with the reply schema where your tool takes one
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
- A step's message arrives: take the last JSON object in it and run the `reply` command printed for that step with that JSON. No JSON object in the message: pass `{"status": "failed", "reason": "invalid reply"}`. Any JSON argument, for `start`, `reply` or `answer`, with a single quote (`'`) in it goes through stdin: put `-` in place of the JSON and pass the JSON on stdin, in a POSIX shell with a quoted heredoc. One argument of a command at most.
- The human answers a question: map the answer to one of the choices `flow.py` listed, ask again if none fits, and run the `answer` command printed with that choice and the human's words verbatim. A free-text question takes the words alone. When `flow.py` lists fields with the question, the command takes a JSON object in place of the choice: the choice and the fields the human gave. A field they did not give is left out, never guessed. `flow.py` keeps the words and writes them where the steps that follow read them.
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

## Steps

Declared in `flow.py` next to this file: inputs, executors, steps with their prompts, what each reads and writes, and the transitions between them. `flow.py` drives the run and prints, for every launch, the executor's model and tool. This section is a pointer, not a copy.

## End of run

- `ready`: the last review found nothing, the checks included. The human reads the review file `flow.py` names, then commits.
- `needs_attention`: the fix rounds ran out with findings open, and the human chose to stop. The review file `flow.py` names lists them, and the human decides what to do with them.
- `failed`: a step failed or was blocked. `progress.md`, which `flow.py` names, says which step and why.
