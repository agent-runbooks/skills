# AGENTS.md

## Self-contained skills

A skill gets copied as plain files into someone else's project and runs there with nothing installed. Everything it needs ships in its own folder:

- Python code uses the standard library of Python 3.9 only; tests use `unittest`.
- The viewer page loads only files from `skills/runbook-viewer/`; its Content-Security-Policy blocks every other origin. `marked.umd.js` is an upstream release copied as is: to update it, replace the whole file with a newer release and refresh `marked-LICENSE.md`.

## Engine

`skills/agent-runbook-authoring/references/runbook.py` is the engine's source. `references/runbook-review-loop/runbook.py` is a byte-for-byte copy, and CI compares the two: edit the source, then copy it over.

A change in the engine's behavior is a release:

1. Bump `__version__` in `runbook.py`.
2. Add a `CHANGELOG.md` entry; a major version says what to change in an existing `flow.py`.
3. Update `references/flow-language.md` and `references/template.md` in the same change when the `flow.py` API or the Execution rules move.
4. Tag `agent-runbook-authoring/v<version>` once the commit is on `main`.

## Checks

Run the steps of `.github/workflows/test.yml` locally before calling a change done. `tests/fixtures/` holds real run folders that `test_view.py` reads; change them together with the tests.

## Commits

A commit message says what changed in behavior, not which files moved. An engine release opens with `Engine <version>:`.
