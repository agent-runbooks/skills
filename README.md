# Skills

[![skills.sh](https://skills.sh/b/nodge/skills)](https://skills.sh/nodge/skills/agent-runbook-authoring)

Agent skills I use in my daily work, in the [Agent Skills](https://agentskills.io) format: a directory with a `SKILL.md`. They work in Claude Code, Codex CLI, opencode and any other harness that loads skills.

## Install

Two ways in. The **Claude Code plugin** installs the skills as a managed bundle that updates when I push. The **[skills CLI](https://github.com/vercel-labs/skills)** copies the skill files into your project or home directory, for any agent, as files you own and can edit. Pick one, otherwise each skill shows up twice.

<details>
<summary><strong>Claude Code plugin</strong></summary>

```bash
claude plugin marketplace add Nodge/skills
claude plugin install agent-runbook-authoring@nodge-skills
claude plugin install runbook-task-cycle@nodge-skills
claude plugin install runbook-viewer@nodge-skills
claude plugin install consensus@nodge-skills
```

Or from inside a session:

```
/plugin marketplace add Nodge/skills
/plugin install agent-runbook-authoring@nodge-skills
/plugin install runbook-task-cycle@nodge-skills
/plugin install runbook-viewer@nodge-skills
/plugin install consensus@nodge-skills
```

</details>

<details>
<summary><strong>skills CLI: Claude Code, Codex, opencode, Cursor and others</strong></summary>

```bash
npx skills add Nodge/skills
```

It asks which skills to take and which agents to install them on. Non-interactive, into the user directory of one agent:

```bash
npx skills add Nodge/skills --skill agent-runbook-authoring -g -a claude-code -y
```

`npx skills update` pulls my changes later.

</details>

<details>
<summary><strong>By hand</strong></summary>

Copy `skills/<name>` into your harness's skills directory: `~/.claude/skills`, `~/.codex/skills`, `~/.config/opencode/skills`.

</details>

## Skills

### ◆ agent-runbook-authoring

Writes **runbooks**: procedures an agent session runs through subagents, of which the task cycle below is one. The steps are prompt files, the transitions are a few lines of Python on a small engine, and the orchestrator never reasons about what comes next. [Read more](skills/agent-runbook-authoring).

[`examples/`](examples) has a tiny project to try the task cycle on and the files of a real run.

### ◆ runbook-task-cycle

One coding task end to end: a coder implements the brief, a cheap model runs the checks, two models from different vendors review independently, an arbiter triages their findings, the coder fixes, a verifier checks the fixes, a last pass cleans up comments and wording. Every step is a [throng](https://github.com/Nodge/throng-mcp) thronglet. A project adapts it with one profile file: its checks, its version control, its rules, its models. With runbook-viewer installed, the orchestrator prints the run's status after every step. [Read more](skills/runbook-task-cycle).

### ◆ runbook-viewer

Shows where a runbook run is and what its steps wrote. After every step the orchestrator prints a text status into the chat, one line per step with its executor, time and reply. On request it starts a read-only page on localhost: the steps on the left, the selected step's log and output files on the right, following the run as it goes. Standard library Python, nothing to install. [Read more](skills/runbook-viewer).

### ◆ consensus

Think a question through with a second model: `/consensus codex/gpt-6-sol <question>`. The session you are in and the model you named answer **independently**, then reconcile round by round under one set of rules: reply to every open point with what convinced you or what you checked, concede only when refuted, stop at two CONVERGED in a row. The report is what both stand behind, what was contested, and what stayed open. Needs [throng](https://github.com/Nodge/throng-mcp). [Read more](skills/consensus).

## License

MIT
