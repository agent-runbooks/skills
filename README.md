# Agent Runbooks

[![skills.sh](https://skills.sh/b/agent-runbooks/skills)](https://skills.sh/agent-runbooks/skills/agent-runbook-authoring)

Agent skills I use in my daily work, in the [Agent Skills](https://agentskills.io) format: a directory with a `SKILL.md`. They work in Claude Code, Codex CLI, opencode and any other harness that loads skills.

## Install

You need Python 3.10 or newer, and a harness whose session can launch subagents and learn when they finish.

Two ways in. The **Claude Code plugin** installs both skills as one managed bundle that updates when I push. The **[skills CLI](https://github.com/vercel-labs/skills)** copies the skill files into your project or home directory, for any agent, as files you own and can edit. Pick one, otherwise each skill shows up twice.

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

`npx skills update` pulls my changes later.

</details>

<details>
<summary><strong>By hand</strong></summary>

Copy `skills/<name>` into your harness's skills directory: `~/.claude/skills`, `~/.codex/skills`, `~/.config/opencode/skills`.

</details>

## Skills

### ◆ agent-runbook-authoring

Writes **runbooks**: procedures an agent session runs through subagents. The steps are prompt files, the transitions are a few lines of Python on a small engine, and the orchestrator never reasons about what comes next. [Read more](skills/agent-runbook-authoring).

### ◆ runbook-viewer

Shows where a runbook run is and what its steps wrote. After every step the orchestrator prints a text status into the chat, one line per step with its executor, time and reply. On request it starts a read-only page on localhost: the steps on the left, the selected step's log and output files on the right, following the run as it goes. Standard library Python, nothing to install. [Read more](skills/runbook-viewer).

## Gallery

Ready-made runbooks to install and adapt live in [agent-runbooks/gallery](https://github.com/agent-runbooks/gallery). The first one is [runbook-task-cycle](https://github.com/agent-runbooks/gallery/tree/main/skills/runbook-task-cycle): one coding task from brief to reviewed changes.

## License

MIT
