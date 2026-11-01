"""The root README's status sample is what the engine prints, and the engine lays out a run as the viewer does: run
with `python3 -m unittest tests.test_readme -v` from the repository root."""

from __future__ import annotations

import importlib
import os
import sys
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'skills', 'agent-runbook-authoring', 'references'))
sys.path.insert(0, os.path.join(ROOT, 'skills', 'runbook-viewer'))
agent_runbooks = importlib.import_module('agent_runbooks')
view = importlib.import_module('view')

FIXTURES = os.path.join(ROOT, 'tests', 'fixtures')
RUNNING = os.path.join(FIXTURES, '20261007-migrate-sites-running')
# The clock the fixture's open attempts are timed against.
NOW_RUNNING = datetime(2026, 10, 7, 9, 14, 0, tzinfo=timezone.utc)
# The fields of a line of the tree that the engine's copy of the tree code and the viewer's both have.
TREE_KEYS = ('type', 'id', 'depth', 'within', 'group', 'label', 'step', 'status', 'executor', 'note', 'header')


class ReadmeTest(unittest.TestCase):
    def test_status_sample_is_the_engines_output(self) -> None:
        state = agent_runbooks.RunState.load(RUNNING)
        text = agent_runbooks.status_text(RUNNING, state, NOW_RUNNING)
        with open(os.path.join(ROOT, 'README.md'), encoding='utf-8') as f:
            self.assertIn('```\n' + text + '\n```\n', f.read())


class TreeTest(unittest.TestCase):
    def test_the_engine_and_the_viewer_lay_out_every_fixture_alike(self) -> None:
        runs = [os.path.join(FIXTURES, name) for name in sorted(os.listdir(FIXTURES))]
        runs = [run for run in runs if os.path.isfile(os.path.join(run, 'state.json'))]
        runs = [run for run in runs if view.read_state(run).get('format') == agent_runbooks.FORMAT]
        self.assertGreater(len(runs), 1)
        for run in runs:
            with self.subTest(run=os.path.basename(run)):
                calls = [record.to_json() for record in agent_runbooks.RunState.load(run).calls]
                engine = [{k: e.get(k) for k in TREE_KEYS} for e in agent_runbooks._layout(calls)]
                viewer = [{k: e.get(k) for k in TREE_KEYS} for e in view.layout(view.read_state(run))]
                self.assertEqual(engine, viewer)


if __name__ == '__main__':
    unittest.main()
