# Fixtures

Run folders of engine 2.x that `skills/runbook-viewer/test_view.py` reads. They are made by the engine, never edited by hand:

- `20261007-slugify-max-length`: `runbook-review-loop` to `ready`; `fix` interrupted and launched again as `fix@2`, the human grants one more round
- `20261007-slugify-transliterate-question`: `runbook-review-loop` waiting for the human after its fix round, with one reply corrected on the way
- `20261007-migrate-sites-running`: `runbook-migrate-sites/flow.py`, kept here; a foreach failed by one item, with a question withdrawn and an item cancelled, then a parallel in flight with a blocked branch step called again

To make them again, after a change in the engine or in a flow:

```bash
python3 tests/fixtures/make.py
```

`make.py` drives each run through the engine's commands with fake executors and a fixed clock, under `/tmp/rb-demo/.agent-runbooks/runs` so the recorded paths stay the same, and moves the runs here in place of the old ones. Then run `test_view.py` and update what it expects together with the fixtures.
