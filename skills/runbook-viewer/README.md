# runbook-viewer

See where a [runbook](../agent-runbook-authoring) run is and read what its steps wrote, without opening the run directory. Two views of the same files: a text status the orchestrator prints into the chat after every step, and a page on localhost.

```
20261007-migrate-sites-running · runbook-migrate-sites · running
✓ plan                    coder      2:10
✓ sites[auth]/migrate     coder      4:30  risky: false
✓ sites[billing]/migrate  coder      3:20  risky: true
✗ sites[billing]/approve             3:30  cancelled
✗ sites[auth]/verify      reviewer   2:20  users.email loses its NOT NULL constraint
✓ reviews/a               reviewer   3:00  findings: 1
! reviews/b/review        reviewer   3:30  no second reviewer is set up
● reviews/b/review#2      coder      1:30
```

One line per attempt of a step or a human step, in the order they were opened: its mark (`✓` done, `●` running, `?` waiting for the human, `✗` failed, interrupted or cancelled, `!` blocked), its address, the executor it ran on, how long it took or has been running, and the reply fields, the reason of a failure, the human's choice, or `interrupted` / `cancelled`. The address is the call's place in the flow without the leading `main/`: `review#2` is the second call of `review`, `reviews/a` a branch of the parallel `reviews`, `sites[auth]/verify` a call inside the item `auth` of the foreach `sites`, and `fix@2` the attempt opened after `fix` was interrupted. Groups and items have no line of their own. The viewer reads runs of engine 2.x, `state.json` format 2; a run of engine 1.x is refused with a line that says so.

The page shows the same list on the left with the run's own files below it, `brief.md`, `progress.md`, a diff, and each foreach's index, and on the right the selected attempt: its lines from `progress.md`, then the files it was told to write, markdown rendered, diffs coloured. It polls every two seconds and follows the run, the running step or the question waiting for the human, until you click something; "follow" brings it back. A question shows what was asked and that the answer goes into the chat. The page is read-only: the run is driven from the chat, never from here.

## Usage

Python 3.9 or later, standard library only. `<run>` is a run directory, or `.agent-runbooks/runs` for the latest run in it.

```bash
python3 skills/runbook-viewer/view.py .agent-runbooks/runs --status
python3 skills/runbook-viewer/view.py .agent-runbooks/runs/20261003-add-version-constant
```

The second prints a `http://127.0.0.1:<port>/` URL and serves until Ctrl-C, or until 30 minutes pass without a request, that is with the page closed; `--idle-minutes N` changes that, 0 serves until Ctrl-C. `--port N` fixes the port. In a session, ask for it: "show me the run", "status of the run". A runbook whose Execution rules mention the viewer prints the status by itself when this skill is installed; the runbooks in this repository do.

## Contents

- [`SKILL.md`](SKILL.md): when the orchestrator prints the status, how the page is started
- `view.py`: the text status and the server; `test_view.py` its tests, `python3 -m unittest test_view`
- `page.html`, `page.js`: the page, plain HTML, CSS and JavaScript; the server sends a Content-Security-Policy that keeps it off other origins and answers only to a `127.0.0.1` or `localhost` Host
- `marked.umd.js`: [marked](https://github.com/markedjs/marked) 18.0.14, the markdown renderer, vendored as published, MIT licence in [`marked-LICENSE.md`](marked-LICENSE.md)
