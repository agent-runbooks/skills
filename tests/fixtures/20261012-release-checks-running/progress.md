# Run 20261012-release-checks-running

Runbook `runbook-release-checks`. State in `state.json`. Inputs:

- brief: brief.md
- repo: /tmp/rb-demo

## Log

- main/changes: started (pid 4242)
- main/changes: {"status": "done", "commits": 3}
- main/lint: started (pid 4242)
- main/lint: {"status": "failed", "reason": "KeyError: 'max-warnings'"}
- main/build: started (pid 4242)
- main/build: interrupted (owner process lost)
- main/build@2: started (pid 4242)
- main/build@2: {"status": "done", "wheel": "dist/textkit-1.5.0-py3-none-any.whl"}
- main/notes: launched
- main/notes: {"status": "done"}
- main/tests: started (pid 4242)
