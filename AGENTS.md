# AGENTS.md

## Self-contained skills

A skill gets copied as plain files into someone else's project and runs there with nothing installed. Everything it needs ships in its own folder:

- Python code uses the standard library of Python 3.9 only; tests use `unittest`.
- The viewer page loads only files from `skills/runbook-viewer/`; its Content-Security-Policy blocks every other origin. `marked.umd.js` is an upstream release copied as is: to update it, replace the whole file with a newer release and refresh `marked-LICENSE.md`.

## Python code

- Raise `CommandError` for a refused command; only `main` prints it and returns the exit code. Never call `sys.exit` below `main`.
- Reject declaration mistakes at declaration, naming the step and parameter. Never coerce or replace silently. Refuse a lone string where a collection is required.
- Keep commands linear in the run's length. Every command replays the whole history, so never scan the calls or the log inside a loop over them.
- Check a recorded call's current status before changing it. Refuse unsupported statuses without changing state or progress.
- Write state files whole. Serialize first, write a temporary file next to the target, then use `os.replace`.
- Pass every path and value in a shell command printed for the orchestrator through `shlex.quote`.
- Type the public `flow.py` API precisely. Use `Any` only for unknown types and `Literal` for a closed set of strings.
- Keep enums free of `str` mixins, since their formatting differs across supported Python versions.
- Match the module's style. Use `os.path`, the existing docstring voice, and leave untouched code's layout alone.

## Engine

`skills/agent-runbook-authoring/references/agent_runbooks.py` is the engine's source. `references/runbook-review-loop/agent_runbooks.py` is a byte-for-byte copy, and CI compares the two: edit the source, then copy it over.

A change in the engine's behavior is a release:

1. Bump `__version__` in `agent_runbooks.py`.
2. Add a `CHANGELOG.md` entry; a major version says what to change in an existing `flow.py`.
3. Update `references/flow-language.md` and `references/template.md` in the same change when the `flow.py` API or the Execution rules move. A major version also moves the `agent-runbooks` pin in `template.md`.
4. Tag `agent-runbook-authoring/v<version>` once the commit is on `main`. The tag publishes the engine to PyPI as `agent-runbooks` through `.github/workflows/publish.yml`, which refuses a tag that differs from `__version__`.

## Checks

Run the steps of `.github/workflows/test.yml` locally before calling a change done. `tests/fixtures/` holds real run folders that `test_view.py` reads; change them together with the tests.

The `lint` job runs ruff and pyright from the root `pyproject.toml` through `uv run`; `uv run ruff format` fixes the layout. These are dev tools only: no skill imports them. The same file builds the engine's PyPI package; `uv build` checks it locally.

## Commits

A commit message says what changed in behavior, not which files moved. An engine release opens with `Engine <version>:`.
