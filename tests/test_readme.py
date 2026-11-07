"""The root README's table of a finished run is what the engine prints, and the engine lays out a run as the viewer does: run
with `python3 -m unittest tests.test_readme -v` from the repository root."""

from __future__ import annotations

import importlib
import os
import sys
import unittest
import urllib.parse
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'skills', 'agent-runbook-authoring', 'references'))
sys.path.insert(0, os.path.join(ROOT, 'skills', 'runbook-viewer'))
agent_runbooks = importlib.import_module('agent_runbooks')
view = importlib.import_module('view')

FIXTURES = os.path.join(ROOT, 'tests', 'fixtures')
ENDED = os.path.join(FIXTURES, '20261031-slugify-max-length')
# No attempt of the fixture is open, so the clock changes nothing.
NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
# The fields of a line of the tree that the engine's copy of the tree code and the viewer's both have.
TREE_KEYS = ('type', 'id', 'depth', 'within', 'group', 'label', 'step', 'status', 'executor', 'note', 'header')


class ReadmeTest(unittest.TestCase):
    def test_final_table_is_the_engines_output(self) -> None:
        state = agent_runbooks.RunState.load(ENDED)
        table = '\n'.join(agent_runbooks.final_table(ENDED, state, NOW))
        # The engine links by absolute path, the README relative to the repository root.
        table = table.replace(urllib.parse.quote((ROOT + os.sep).replace(os.sep, '/'), safe='/:'), '')
        with open(os.path.join(ROOT, 'README.md'), encoding='utf-8') as f:
            self.assertIn('\n\n' + table + '\n\n', f.read())


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
