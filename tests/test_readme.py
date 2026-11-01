"""The root README's status sample is what the engine prints: run with `python3 -m unittest tests.test_readme -v`
from the repository root."""

from __future__ import annotations

import importlib
import os
import sys
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'skills', 'agent-runbook-authoring', 'references'))
agent_runbooks = importlib.import_module('agent_runbooks')

RUNNING = os.path.join(ROOT, 'tests', 'fixtures', '20261007-migrate-sites-running')
# The clock the fixture's open attempts are timed against.
NOW_RUNNING = datetime(2026, 10, 7, 9, 14, 0, tzinfo=timezone.utc)


class ReadmeTest(unittest.TestCase):
    def test_status_sample_is_the_engines_output(self) -> None:
        state = agent_runbooks.RunState.load(RUNNING)
        text = agent_runbooks.status_text(RUNNING, state, NOW_RUNNING)
        with open(os.path.join(ROOT, 'README.md'), encoding='utf-8') as f:
            self.assertIn('```\n' + text + '\n```\n', f.read())


if __name__ == '__main__':
    unittest.main()
