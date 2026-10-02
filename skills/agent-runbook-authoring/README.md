# agent-runbook-authoring

A skill for writing **runbooks**: procedures an agent session executes step by step through subagents. Implement, run the checks, review with two models, triage, fix, verify, ask a human when the fix rounds run out.

## The problem

Write such a procedure as prose in a `SKILL.md`, and the orchestrating model drifts exactly where the procedure branches. It counts loop rounds wrong, takes a failed reply for a done one, reads files it was told to leave alone, pastes artifacts into its context until compaction eats the run. A workflow script fixes the branching but needs a runner, and the steps stop being prompts you can read and edit.

## The approach

A runbook is an ordinary skill. The steps are prompt files. Transitions live in `flow.py`, a few dozen lines of Python on top of `runbook.py`, a small engine copied into every runbook. The orchestrator never reasons about what comes next: it launches what `flow.py` prints, waits, and copies each executor's JSON reply back into `flow.py`. Steps hand work to each other through files in a run directory, so the orchestrator's context stays small, and an interrupted run resumes from `state.json`.

```python
rb.step('checks', executor='light', prompt='prompts/02-checks.md',
        writes=['checks.md'], reply={'passed': bool},
        next=lambda r, s: parallel('review-a', 'review-b') if r.passed
        else ('fix-checks' if not s.done('fix-checks') else end('failed', 'read <run>/checks.md')))
```

A finished runbook is self-sufficient: it runs where this skill is not installed.

## Compared with workflow engines

The nearest relatives are [Claude Code workflows](https://code.claude.com/docs/en/workflows), a JavaScript script that fans out Claude subagents, and code orchestrators such as [LangGraph](https://github.com/langchain-ai/langgraph), [Mastra](https://mastra.ai) or the [OpenAI Agents SDK](https://github.com/openai/openai-agents-python), where the procedure is an application you write and run. All of them fix the branching the way `flow.py` does. A runbook differs in what sits around the branching.

**Executors are harnesses, not API calls.** A code orchestrator can call any model, but it calls it bare: a chat completion through an SDK and an API key, with whatever tools you wrote for it. A runbook step runs in a coding harness, Claude Code, Codex or OpenCode, with its tools, skills, MCP servers and project instructions, on the subscription you already pay for. And the harnesses mix: an executor is a description, "a model from another vendor, through the tool that launches it", so the same runbook runs a Codex coder under a Claude orchestrator, or a Claude reviewer under Codex. A Claude Code workflow gives you the harness too, but only Claude subagents inside Claude Code. The runbook itself adds no harness to the mix: the orchestrating session must be able to launch the ones the executors name, through its own subagent tool or through an MCP server such as [throng](https://github.com/Nodge/throng-mcp).

**Installation is a copy.** A runbook is a skill directory plus one Python file, and Python 3.10 is the whole dependency. Install it like any skill, in any harness that loads them. Workflows exist only inside Claude Code. A code orchestrator needs a runtime, packages, credentials and a place to run, and the harness you work in is not that place.

**You can read it.** The steps are prompt files, the flow is a step per line with its branches next to it. A reviewer reads the prompts as prompts and the flow as a list, and the cold read in [`review-checklist.md`](references/review-checklist.md) checks that a model reads it the same way. In a workflow script or an orchestrator app the prompts are strings inside the code, and the control flow is the code. Reviewing one is reading a program.

**The orchestrator keeps its judgement.** A script decides everything. `flow.py` decides only what comes next; how a step is carried out stays with the orchestrating model. It maps the executor onto its harness and reads the step as a task, not an instruction. One example from real runs: an implementation step split across three or four subagents when the brief divided well. A script would have needed that case written in.

What this costs. The flow branches on a few typed fields of a reply, loops within a budget and joins parallel steps with `after`; a workflow script can fan out over a thousand items or loop until a count. And the rules hold as far as the model follows them, see [Limits](#limits).

| | Runbook | Claude Code workflow | Code orchestrator |
|---|---|---|---|
| Executors | coding harnesses, any vendor, mixed | Claude subagents | bare models through SDKs |
| Install | install the skill | built into Claude Code | runtime, packages, deployment |
| Reading | prompt files and a step list | JavaScript | an application |
| Branching | computed by `flow.py` | computed by the script | computed by the code |
| How a step is done | the orchestrator's call | the script's | the code's |

## Mixing models

Runbooks pair well with [throng](https://github.com/Nodge/throng-mcp), an MCP server that runs Claude Code, Codex or OpenCode as subagents of each other. Any step of a runbook can go to any harness and model: a Codex coder, an OpenCode model as a cheap checker, a reviewer from another vendor that catches what the first one missed. [`runbook-task-cycle`](../runbook-task-cycle) runs every step that way, with the agent of each executor as an input of the run.

## Limits

- The orchestrating session must be able to launch subagents and learn when they finish.
- The engine computes transitions. Whether the orchestrator follows the execution rules is still up to the model; `progress.md` and the review checklist make deviations visible, not impossible.
- Tested only with Claude Code as the orchestrator. Other harnesses that meet the first point will likely work; I haven't tried them.
- Six runs so far, one of them a real ticket with two fix rounds. No wrong transition in any; the departures the orchestrator logged were about the harness (foreground launches when nested under throng, an Agent tool with no cwd), not the flow. The failure path held once: a run with a failing test planted outside the brief ended as `failed` pointing at `checks.md`.

## Contents

- [`SKILL.md`](SKILL.md): how to write a runbook, for the agent
- [`references/template.md`](references/template.md): the shape of a runbook and the sections copied into it
- [`references/flow-language.md`](references/flow-language.md): the `flow.py` API
- [`references/review-checklist.md`](references/review-checklist.md): cold read, checklist, run review
- [`references/runbook.py`](references/runbook.py): the engine, Python 3.10+, no dependencies
- [`CHANGELOG.md`](CHANGELOG.md): engine versions
