# Reviewing a runbook

Three passes: a cold read, the checklist before the first run, and a run's files after. All are jobs for a second agent with the runbook and, for the last pass, the run directory. Report each finding as step name, the check that failed, and the line that shows it.

## Cold read

A subagent that has not loaded the `agent-runbook-authoring` skill reads the runbook the way the orchestrator will. Its prompt:

> Read every file in `<runbook directory>` and nothing else. Do not load any skill. You are about to execute this runbook as its orchestrator, on this input: `<smoke input>`. Walk through it from the first step to the end of the run, and for each step write what you would do, what you would send, and what you would wait for. Then answer: where did you have to guess, where could a line be read two ways, what did you need that the text did not give you. Do not run anything.

Every guess and every alternative reading the subagent reports is a defect in the runbook. Fix the text and repeat until it reports none.

## Before the run: the text

`python3 flow.py --check` in the runbook directory passes, and a scratch run fed every branch by hand ended where the author expected. The rest is read by eye:

flow.py:

- Every loop has a budget in local variables of the generator and a way out, a human step or a `return end(...)`. A retry around a `try`/`except StepFailed` counts its failures the same way.
- The generator computes from `ctx` and what its yields return, and acts only through its yields: no clock, file reads, directory listings, iteration over a `set`, or module-level state it changes; no file writes, commands or prints in its body; no `finally`, `with` or bare `except:` around a `yield`. A branch on what a step found reads a reply field.
- Branches of one `parallel(...)` and items of one `foreach(...)` that run together change the working tree one at a time: a branch that changes it runs in a group where no other branch reads the tree, and a foreach whose body changes it has `max_concurrent=1`.
- Every name in `reads` is an input file, the working tree, or written by a step that runs before it, in the same generator or one around it, on every path through the flow. Otherwise the prompt says what to do when it is absent. A file written inside a group reaches a later step only through its result passed as an argument, and that step's prompt names it by its path in the argument, `reviews.a/review.md`. A step in a group that needs its own pass from an earlier group gets it the same way, `review(previous=reviews.a)`. Every name in `writes` is read by a later step or named in End of run. Every file a prompt names is in the step's `reads` or `writes`, or comes through a passed result.
- The `over` file of every `foreach` is written by an earlier step whose prompt states its format: a JSON array of objects with a unique `key`, an empty array when there is nothing.
- A step with side effects has `side_effects`, applies a file, and takes no judgement calls.
- Every launch-message value a prompt expects is in the step's `inputs` or a keyword argument of every call of it.
- Every `end(...)` status is under End of run in `SKILL.md`, and `failed` is there. An end report names a file written inside a group through the result's `files`, since `<run>/<name>` does not reach into a group.

Prompts:

- Each prompt file stands alone with `common.md`: what to read, what to produce and where, what good looks like, what each reply field means. Nothing refers to the conversation or to another step's prompt.
- No prompt carries a reply schema of its own: the engine writes it from the step's `reply`. Every field `reply` declares is explained in the prompt, and the flow survives a value outside a field's schema bounds. A field's schema uses `minimum`, `minLength` and the like only if every harness the runbook targets accepts them.
- A prompt that runs commands says where their exit codes and failing output go, and what counts as skipped.
- An output file another step parses has its format stated: headings, fields, what to write when there is nothing.
- `common.md` says what "the changes" are and how the checks are run.
- The copied sections match this skill's references word for word, and `runbook.py` is this skill's `runbook.py`.

SKILL.md:

- Inputs match `rb.inputs`, with a smoke input and its cleanup.
- Every executor description names a model, and a launch tool only when it is not the orchestrator's own subagent tool. None depends on one harness's name for a tool.

## After the run: the run directory against flow.py

- For every `done` step record in `state.json`, the `<NN>-<name>` file of each name its step `writes` exists, `NN` being the record's place in `calls`.
- Each `done` reply's fields agree with the step's output file: a `passed: true` with failures in the Checks section, or a `to_fix: 0` with findings marked to fix in the triage file, fails.
- Every deviation the orchestrator made is in `progress.md` as an `orchestrator:` line.
- If the orchestrator's transcript is available: every action it took is on the list at the top of the Execution rules. Anything else fails.

Count the failures per pass. The number over several runs is the runbook's drift metric.
