---
name: runbook-viewer
description: "Shows a runbook run on a page on localhost that follows it live: the tree of steps, each step's log lines and output files. Load it when the human asks to see a runbook run, its page, or a step's output."
---

# Runbook viewer

`<viewer>` is the directory this `SKILL.md` was loaded from. `view.py` shows runs of engine 1.x and 2.x, and only reads the run directory.

## When the human asks

Start `python3 <viewer>/view.py <run>` as a background process that outlives the turn, give the human the URL it prints as its first line, and open it in the harness's own browser pane if there is one. Without a run named, pass the runs directory, `.agent-runbooks/runs` under the directory the session started in: the viewer takes the run modified last. The server runs until the human says to stop it, the session ends, or no page has been open for 30 minutes. If the human asks for the page after that, start it again.
