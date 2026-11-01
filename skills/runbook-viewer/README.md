# runbook-viewer

Shows where a [runbook](../agent-runbook-authoring) run is and what its steps wrote, on a page on localhost, so you don't have to dig through the run directory. The status in the chat comes from the runbook itself: `flow.py <run> status` prints it, and `flow.py` prints a table of every step when the run ends.

The viewer reads runs of engine 1.x and 2.x.

## The page

On the left is the tree of steps, the same as `flow.py <run> status` prints. Each row is a step, and its mark says how it went:

- `✓` done
- `●` running
- `?` waiting for your answer
- `✗` failed, interrupted or cancelled
- `!` blocked
- `·` not started yet

The steps of a parallel or a foreach are grouped under a line that counts its items or branches by status, and each item or branch gets one row with the step it is on now or the one it ended with. `review#2` is the second run of `review`, and `fix@2` is `fix` started again after it was interrupted. Below the tree are the run's own files: `brief.md`, `progress.md`, the diff, and the item list of each foreach. Click a row to see everything that happened in it on the right: every attempt with its lines from `progress.md` and the files it wrote, a code step's log among them, markdown rendered, diffs coloured.

The page refreshes every two seconds and follows the run, jumping to the running step or to the question waiting for you. Clicking a row stops that, and "following" turns it back on. The page only shows the run. You answer the orchestrator's questions in the chat.

## Usage

Python 3.9 or later, nothing to install. In a session, ask for it: "show me the run".

From a terminal, pass a run directory, or `.agent-runbooks/runs` for the latest run in it:

```bash
python3 skills/runbook-viewer/view.py .agent-runbooks/runs/20261003-add-version-constant
```

It starts the page and prints its `http://127.0.0.1:<port>/` address. The page server stops on Ctrl-C, or after 30 minutes with no page open. `--idle-minutes N` changes that limit, `0` turns it off, and `--port N` picks the port.

## Contents

- [`SKILL.md`](SKILL.md): how the page is started
- `view.py`: the server. `test_view.py` holds its tests: `python3 -m unittest test_view`
- `page.html`, `page.js`: the page, plain HTML, CSS and JavaScript. The server sends a Content-Security-Policy that keeps the page off other origins, and it answers only requests to a `127.0.0.1` or `localhost` Host
- `marked.umd.js`: [marked](https://github.com/markedjs/marked) 18.0.14, the markdown renderer, vendored as published, MIT licence in [`marked-LICENSE.md`](marked-LICENSE.md)
