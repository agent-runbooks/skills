# runbook-viewer

See where a [runbook](../agent-runbook-authoring) run is and read what its steps wrote, without opening the run directory. Two views of the same files: a text status the orchestrator prints into the chat after every step, and a page on localhost.

```
20261003-add-version-constant · runbook-task-cycle · running
✓ preflight     light    0:42  clean: true
✓ implement     strong   5:20
✓ checks        light    0:43  passed: false
✗ fix-checks    strong   0:10  interrupted
✓ fix-checks-2  strong   2:13  fixed: true
✓ checks-2      light    0:38  passed: true
● review-a      strong   4:05
✓ review-b      second   2:45  findings: 0
```

One line per launched section: its mark (`✓` done, `●` running, `?` waiting for the human, `✗` failed, `!` blocked), the executor it ran on, how long it took or has been running, and the reply fields, the reason of a failure or the human's choice. A run recorded before engine 1.1.0 has no executor or times, and its lines show neither.

The page shows the same list on the left with the run's own files below it, `brief.md`, `progress.md`, a diff, and on the right the selected section: its lines from `progress.md`, then its output files, markdown rendered, diffs coloured. It polls every two seconds and follows the run, the running step or the question waiting for the human, until you click something; "follow" brings it back. A question shows what was asked and that the answer goes into the chat. The page is read-only: the run is driven from the chat, never from here.

## Usage

Python 3.10 or later, standard library only. `<run>` is a run directory, or `.agent-runbooks/runs` for the latest run in it.

```bash
python3 skills/runbook-viewer/view.py .agent-runbooks/runs --status
python3 skills/runbook-viewer/view.py .agent-runbooks/runs/20261003-add-version-constant
```

The second prints a `http://127.0.0.1:<port>/` URL and serves until Ctrl-C; `--port N` fixes the port. In a session, ask for it: "show me the run", "status of the run". A runbook whose Execution rules mention the viewer prints the status by itself when this skill is installed; the runbooks in this repository do.

## Contents

- [`SKILL.md`](SKILL.md): when the orchestrator prints the status, how the page is started
- `view.py`: the text status and the server; `test_view.py` its tests, `python3 -m unittest test_view`
- `page.html`, `page.js`: the page, plain HTML, CSS and JavaScript; the server sends a Content-Security-Policy that keeps it off other origins and answers only to a `127.0.0.1` or `localhost` Host
- `marked.umd.js`: [marked](https://github.com/markedjs/marked) 18.0.14, the markdown renderer, vendored as published, MIT licence in [`marked-LICENSE.md`](marked-LICENSE.md)
