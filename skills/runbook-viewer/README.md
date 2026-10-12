# runbook-viewer

Shows where a [runbook](../agent-runbook-authoring) run is and what its steps wrote, so you don't have to dig through the run directory. The orchestrator prints a short text status into the chat after every step, and a page on localhost lets you read each step's output.

## The status

```
20261007-migrate-sites-running · runbook-migrate-sites · running
✓ plan  coder   2:10
  foreach sites: 1 failed (auth), 1 cancelled, 1 not started
  ✗ [auth]     verify       reviewer   6:50  users.email loses its NOT NULL constraint
  ✗ [billing]  approve                 6:50  cancelled
  · [search]   not started
  parallel reviews: 1 done, 1 running
  ✓ a  review    reviewer   3:00  findings: 1
  ● b  review#2  coder      5:00
```

Each row is a step. The mark says how it went:

- `✓` done
- `●` running
- `?` waiting for your answer
- `✗` failed, interrupted or cancelled
- `!` blocked
- `·` not started yet

After the mark come the step's name, the executor it ran on (a name from the runbook's `flow.py`, like `coder`), how long it took, and what it replied: the fields of its answer, why it failed, or what you chose. `review#2` is the second run of `review`, and `fix@2` is `fix` started again after it was interrupted.

The steps of a parallel or a foreach are grouped. A group opens with a line like `foreach sites: 1 failed (auth), 1 cancelled, 1 not started`, which counts its items and names the failed ones. Under it, each item, `[auth]`, or branch, `a`, gets one row with the step it is on now or the one it ended with. A group inside an item or a branch sits one level deeper.

In the chat the orchestrator shows only the last 10 rows, so a long run doesn't flood it. The first line counts the rows cut, and the group lines above the first row stay, so a row is never shown without its group. When the run ends, the orchestrator prints the full status once more.

```
20261007-port-modules-running · runbook-port-modules · running
… 6 rows above
  foreach modules: 1 done, 2 running, 1 not started
    parallel modules[cli]/checks: 2 done
    ✓ tests  test  reviewer   2:50  passed: true
  ● [web]   port                coder      2:40
  · [docs]  not started
```

The viewer reads runs of engine 1.x and 2.x.

## The page

On the left is the same tree of steps, and below it the run's own files: `brief.md`, `progress.md`, the diff, and the item list of each foreach. Click a row to see everything that happened in it on the right: every attempt with its lines from `progress.md` and the files it wrote, markdown rendered, diffs coloured.

The page refreshes every two seconds and follows the run, jumping to the running step or to the question waiting for you. Clicking a row stops that, and "following" turns it back on. The page only shows the run. You answer the orchestrator's questions in the chat.

## Usage

Python 3.9 or later, nothing to install. In a session, ask for it: "show me the run", "what's the status of the run". A runbook whose Execution rules mention the viewer prints the status by itself when this skill is installed. The runbooks in this repository do.

From a terminal, pass a run directory, or `.agent-runbooks/runs` for the latest run in it:

```bash
python3 skills/runbook-viewer/view.py .agent-runbooks/runs --status --tail 10
python3 skills/runbook-viewer/view.py .agent-runbooks/runs/20261003-add-version-constant
```

The first prints the status. Without `--tail` it prints every row. The second starts the page and prints its `http://127.0.0.1:<port>/` address. The page server stops on Ctrl-C, or after 30 minutes with no page open. `--idle-minutes N` changes that limit, `0` turns it off, and `--port N` picks the port.

## Contents

- [`SKILL.md`](SKILL.md): when the orchestrator prints the status, how the page is started
- `view.py`: the text status and the server. `test_view.py` holds its tests: `python3 -m unittest test_view`
- `page.html`, `page.js`: the page, plain HTML, CSS and JavaScript. The server sends a Content-Security-Policy that keeps the page off other origins, and it answers only requests to a `127.0.0.1` or `localhost` Host
- `marked.umd.js`: [marked](https://github.com/markedjs/marked) 18.0.14, the markdown renderer, vendored as published, MIT licence in [`marked-LICENSE.md`](marked-LICENSE.md)
