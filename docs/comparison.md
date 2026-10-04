# Runbooks compared with workflow engines

A runbook has two kinds of relatives. [Claude Code workflows](https://code.claude.com/docs/en/workflows) and [Copilot dynamic workflows](https://docs.github.com/en/copilot/concepts/agents/dynamic-workflows) are JavaScript scripts that fan out their harness's own subagents. Code orchestrators such as [LangGraph](https://github.com/langchain-ai/langgraph), [Mastra](https://mastra.ai) and the [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) are libraries you build an application on. Like `flow.py`, all of them compute the next step in code instead of leaving it to a model. They differ in everything around that.

| | Runbook | Claude Code workflow | Copilot dynamic workflow | Code orchestrator |
|---|---|---|---|---|
| Branching | computed by `flow.py` | computed by the script | computed by the script | computed by the code, or handed to a model |
| Executors | coding harnesses, any vendor, mixed | Claude subagents | Copilot subagents | models through SDKs, coding harnesses through adapters |
| Install | install the skill | a script in `.claude/workflows/` or a plugin | a Copilot extension or plugin | an application to build and deploy |
| Runs in | any harness that loads skills and launches subagents, its own or through throng-mcp | Claude Code: CLI, Desktop, IDE, `claude -p`, Agent SDK | Copilot CLI, the Copilot app, the Copilot SDK | wherever you deploy it |
| Reading | prompt files and a step list | JavaScript | JavaScript | an application |
| How a step is done | the orchestrator's call | the script's | the script's | the code's |
| Talking to the human | a question in the session, any time | none mid-run; pause or stop from `/workflows` | checkpoints and questions the script declares | an interrupt in code; a UI from the framework or yours |
| Resume | in any session, from `state.json` | in the same session | from the steps the script journaled | from a checkpointer or storage you configure |

## Executors are harnesses

A runbook step runs in a coding harness, Claude Code, Codex or OpenCode, with its tools, skills, MCP servers and project instructions. An executor is a description, "a model from another vendor, through the tool that launches it", so one runbook runs a Codex coder under a Claude orchestrator, or a Claude reviewer under Codex.

The runbook adds no harness of its own. The orchestrating session launches the ones the executors name, through its own subagent tool or through an MCP server such as [throng-mcp](https://github.com/agent-runbooks/throng-mcp).

- **Claude Code workflow:** every agent is a Claude session.
- **Copilot workflow:** every agent is a Copilot agent, with a model picked per call.
- **Code orchestrators** reach harnesses through adapters. Mastra runs Claude Code, Codex or OpenCode as subagents over ACP, the Agents SDK has a Codex tool, LangGraph's docs wrap the Claude Agent SDK in a task. The harness becomes a component of an application you build.

## Installation is a copy

A runbook is a skill directory plus one Python file, and Python 3.10 is the whole dependency. It installs like any skill and runs in any harness that loads skills and can launch subagents, with its own subagent tool or through throng-mcp, headless runs such as `claude -p` or `codex exec` included.

- **Claude Code workflow:** runs only in Claude Code.
- **Copilot workflow:** runs only in Copilot.
- **Code orchestrator:** needs a runtime, packages, credentials and a place to run, and the harness you work in is not that place. In exchange it runs wherever you deploy it, a server or CI included.

## You can read it

The steps are prompt files, the flow is a step per line with its branches next to it. A reviewer reads the prompts as prompts and the flow as a list. The cold read in [`review-checklist.md`](../skills/agent-runbook-authoring/references/review-checklist.md) checks that a model reads it the same way.

In a workflow script or an orchestrator app the prompts are strings inside the code, and the control flow is the code. Reviewing one is reading a program.

## The orchestrator keeps its judgement

A script decides everything. `flow.py` decides only what comes next; how a step is carried out stays with the orchestrating model. It maps the executor onto its harness and reads the step as a task, not an instruction.

One example from real runs: an implementation step split across three or four subagents when the brief divided well. A script would have needed that case written in.

## The human is in the room

The run is a conversation in the session the human already has open. A human step is a question in that chat, and the answer is words, not a button: the orchestrator maps them onto a choice, and `flow.py` writes them to a file for the steps that follow. Between steps the human can interject the same way: ask what a step found, argue with a review, change the brief, stop.

- **Claude Code workflow:** takes no input mid-run. The human can pause or stop it, and the docs suggest a separate workflow per stage for a sign-off.
- **Copilot workflow:** stops at checkpoints and asks questions where its author put them.
- **Code orchestrators** pause in code: LangGraph's `interrupt()`, Mastra's `suspend()`, the Agents SDK's tool approvals. LangGraph also ships UIs for its interrupts, which need a LangGraph server behind them.

## A run outlives its session

`flow.py` keeps the state of the run in `state.json` inside the run directory. `python3 flow.py <run>` in any session, a day later or after a crash, prints what to do next. Steps that ended are not run again, because their outputs are files in that directory.

- **Claude Code workflow:** resumes only in the session that ran it, `claude --resume` included, and reruns every agent from the first one whose prompt changed.
- **Copilot workflow:** replays the results its script saved with `ctx.step` and reruns the rest.
- **Code orchestrators** resume from what you configured: a checkpointer in LangGraph, snapshot storage in Mastra, a serialized run state you keep yourself in the Agents SDK.

## Where a script fits better

- **Mass jobs.** A typical procedure, a dozen steps with a review loop and a human step, fits the flow language with room to spare: it branches on a few typed fields of a reply, loops within a budget and joins parallel steps with `after`. A job like porting a whole project to another language, hundreds of agents over hundreds of files, is where a script earns its keep: a Claude Code workflow fans out over up to 1,000 agents a run and loops on anything JavaScript can test.
- **Spending limits.** A Copilot workflow caps a run by subagents, time and AI credits. A runbook has none.
- **Guarantees.** A script enforces its rules; in a runbook they hold as far as the model follows them, see [Limits](../README.md#compatibility-and-limits). Current models follow them well.

Checked against each tool's documentation on 2026-10-04. Copilot dynamic workflows are in public preview.
