---
name: runbook-viewer
description: "Shows the progress of a runbook run: a text status of its steps for the chat, and a page on localhost with each step's log lines and output files. Load it when the human asks for the progress or status of a runbook run or to read a step's output, and when a runbook's Execution rules point here."
---

# Runbook viewer

`<viewer>` is the directory this `SKILL.md` was loaded from. `view.py` only reads the run directory.

## During a run

For the orchestrator of a runbook. After every `reply` and `answer` command, and once after `start`, run `python3 <viewer>/view.py <run> --status` and show its output to the human as it is, in a code block, with no commentary. Nothing while waiting for a step. Do not start the page unless the human asks for it.

## When the human asks

- For the page: start `python3 <viewer>/view.py <run>` as a background process that outlives the turn, give the human the URL it prints as its first line, and open it in the harness's own browser pane if there is one. Without a run named, pass the runs directory, `.agent-runbooks/runs` under the directory the session started in: the viewer takes the run modified last. The server runs until the human says to stop it, the session ends, or no page has been open for 30 minutes; if the human asks for the page after that, start it again.
- For the status only: run `python3 <viewer>/view.py <run> --status`, with the runs directory in place of `<run>` when no run is named, and show its output in a code block.
