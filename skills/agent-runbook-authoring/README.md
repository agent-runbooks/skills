# agent-runbook-authoring

A skill that writes [runbooks](../../README.md) with you: ask the session for one, and it designs the steps, writes the prompts, `flow.py` and the execution rules, and has a fresh subagent read the result cold. A complete sample is [`references/runbook-review-loop`](references/runbook-review-loop).

## Contents

- [`SKILL.md`](SKILL.md): how to write a runbook, for the agent
- [`references/template.md`](references/template.md): the shape of a runbook and the sections copied into it
- [`references/flow-language.md`](references/flow-language.md): the `flow.py` API
- [`references/review-checklist.md`](references/review-checklist.md): cold read, checklist, run review
- [`references/runbook.py`](references/runbook.py): the engine, Python 3.9+, no dependencies; also on PyPI as `agent-runbooks`
- [`references/runbook-review-loop`](references/runbook-review-loop): a complete sample runbook, implement and review in a loop with a human step, on the harness's own subagents
- [`CHANGELOG.md`](CHANGELOG.md): engine versions
