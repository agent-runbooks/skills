# Agent Runbooks

[![skills.sh](https://skills.sh/b/agent-runbooks/skills)](https://skills.sh/agent-runbooks/skills/agent-runbook-authoring)

A **runbook** lets you give an agent session a procedure to run step by step through subagents. It is an ordinary [Agent Skill](https://agentskills.io) with prompts and a flow you can read and edit, written once for a procedure that repeats and installed like any skill. Code determines what comes next, while the session handles how to carry out each step.

## How it works

The steps are prompt files. The flow is `flow.py`: it declares the steps and calls them from one Python generator, usually a few dozen lines. A copy of the small engine sits next to it, so a finished runbook runs where nothing from this repository is installed.

The orchestrating session launches what `flow.py` prints, waits for the step to finish, and passes the executor's JSON reply back to `flow.py`. It never decides what comes next. Steps hand work to each other through files in a run directory, which keeps the orchestrator's context small. An interrupted run resumes from `state.json`.

One step, and the flow that calls it:

```python
review = rb.step(
    'review',
    executor=reviewer,
    prompt='prompts/02-review.md',
    reads=['implement.md', 'fix.md'],
    writes=['review.md'],
    reply={'findings': int},
)


@rb.flow
def main(ctx):
    yield implement()
    rounds = 0
    while True:
        reviews = yield parallel('reviews', a=review(), b=review(executor=other))
        if reviews.a.findings + reviews.b.findings == 0:
            return end('ready', 'read <run>/implement.md')
        if rounds >= ctx.inputs.maxFixRounds:
            return end('needs_attention', 'read <run>/progress.md')
        yield fix(reviews=reviews)
        rounds += 1
```

A step names its executor by description, such as "the cheapest fast model" or "a model from another vendor". The orchestrating session maps it onto what it can launch: its own subagents, or, with [throng-mcp](https://github.com/agent-runbooks/throng-mcp), Claude Code, Codex or OpenCode, so one run can mix vendors.

For example, here is a run of [runbook-task-cycle](https://github.com/agent-runbooks/gallery/tree/main/skills/runbook-task-cycle) halfway through, as runbook-viewer prints it into the chat after every step:

```
20261003-add-version-constant · runbook-task-cycle · running
✓ preflight     sonnet       0:42  clean: true
✓ implement     opus         5:20
✓ checks        sonnet       0:43  passed: false
✗ fix-checks    opus         0:10  interrupted
✓ fix-checks-2  opus         2:13  fixed: true
✓ checks-2      sonnet       0:38  passed: true
● review-a      opus         4:05
✓ review-b      gpt-6.1-sol  2:45  findings: 0
```

Compared with [Claude Code workflows](https://code.claude.com/docs/en/workflows), [Copilot dynamic workflows](https://docs.github.com/en/copilot/concepts/agents/dynamic-workflows) and code orchestrators such as [LangGraph](https://github.com/langchain-ai/langgraph) or [Mastra](https://mastra.ai):

| | Runbook | Claude Code workflow | Copilot dynamic workflow | Code orchestrator |
|---|---|---|---|---|
| Written | ahead of time, as a skill, for a procedure that repeats | by the agent in the session, for the task at hand; a script can be saved | by the agent in the session, for the task at hand | ahead of time, as an application |
| Reading | prompt files and a step list | JavaScript | JavaScript | an application |
| Install | install the skill | a script in `.claude/workflows/` or a plugin | a Copilot extension or plugin | an application to build and deploy |
| Runs in | any harness that loads skills and launches subagents, its own or through throng-mcp | Claude Code: CLI, Desktop, IDE, `claude -p`, Agent SDK | Copilot CLI, the Copilot app, the Copilot SDK | wherever you deploy it |
| Executors | coding harnesses, any vendor, mixed | Claude subagents | Copilot subagents | models through SDKs, coding harnesses through adapters |
| Branching | computed by `flow.py` | computed by the script | computed by the script | computed by the code, or handed to a model |
| How a step is done | left to the orchestrating model | fixed in the script | fixed in the script | fixed in the code |
| Talking to the human | a question in the session, any time | none mid-run; pause or stop from `/workflows` | checkpoints and questions the script declares | an interrupt in code; a UI from the framework or yours |
| Resume | in any session, from `state.json` | in the same session | from the steps the script journaled | from a checkpointer or storage you configure |

The reasoning behind each row, and where a script fits better, is in [`docs/comparison.md`](docs/comparison.md).

## What is here

| You want to | Go to | You get |
|---|---|---|
| write a runbook for your own procedure | [agent-runbook-authoring](skills/agent-runbook-authoring) | a self-contained runbook that runs where this skill is not installed |
| watch a run | [runbook-viewer](skills/runbook-viewer) | the status above in the chat after every step, and a read-only page on localhost with each step's log and output files |
| run a ready-made runbook | [agent-runbooks/gallery](https://github.com/agent-runbooks/gallery) | runbooks to install and adapt |

The two skills here stand apart: a runbook needs neither to run, and prints the status by itself when the viewer is installed.

## Install

You need Python 3.9 or newer, and a harness whose session can launch subagents and learn when they finish.

Two ways in. The **Claude Code plugin** installs both skills as one managed bundle. The **[skills CLI](https://github.com/vercel-labs/skills)** copies the skill files into your project or home directory, for any agent, as files you own and can edit. Pick one, otherwise each skill shows up twice.

<details>
<summary><strong>Claude Code plugin</strong></summary>

```bash
claude plugin marketplace add agent-runbooks/skills
claude plugin install agent-runbooks@agent-runbooks
```

Or from inside a session:

```
/plugin marketplace add agent-runbooks/skills
/plugin install agent-runbooks@agent-runbooks
```

</details>

<details>
<summary><strong>skills CLI: Claude Code, Codex, opencode, Cursor and others</strong></summary>

```bash
npx skills add agent-runbooks/skills
```

It asks which skills to take and which agents to install them on. Non-interactive, into the user directory of one agent:

```bash
npx skills add agent-runbooks/skills --skill agent-runbook-authoring -g -a claude-code -y
```

`npx skills update` pulls later changes.

</details>

<details>
<summary><strong>By hand</strong></summary>

Copy `skills/<name>` into your harness's skills directory: `~/.claude/skills`, `~/.codex/skills`, `~/.config/opencode/skills`.

</details>

## Write your own

A runbook pays off for a procedure that repeats: three or more steps, several executors, run again and again. A task that happens once is a prompt or a workflow script, not a runbook.

Ask the session for a runbook, and [agent-runbook-authoring](skills/agent-runbook-authoring) writes it with you: the prompts, `flow.py`, the execution rules, and a cold read by a fresh subagent that has not seen the skill. A complete sample to read first is [runbook-review-loop](skills/agent-runbook-authoring/references/runbook-review-loop): a coder and a reviewer in a loop with a human step, on the harness's own subagents, so a copy of it in a skills directory runs as it is.

To change the checks, the models or the project rules of a gallery runbook, you usually do not need a new one: runbook-task-cycle takes a [profile file](https://github.com/agent-runbooks/gallery/tree/main/skills/runbook-task-cycle#the-profile) per project.

## Compatibility and limits

- The skills load in any harness that reads Agent Skills. Running a runbook takes more: the orchestrating session must launch subagents and learn when they finish.
- The engine computes the transitions. Whether the orchestrator follows the execution rules is still up to the model; `progress.md` and the review checklist make a deviation visible, not impossible.

## License

[MIT](LICENSE)
