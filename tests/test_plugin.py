"""The Claude Code plugin ships under the engine's version: run with `python3 -m unittest tests.test_plugin -v` from the
repository root."""

from __future__ import annotations

import importlib
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'skills', 'agent-runbook-authoring', 'references'))
agent_runbooks = importlib.import_module('agent_runbooks')


class PluginTest(unittest.TestCase):
    def test_plugin_version_is_the_engines(self) -> None:
        with open(os.path.join(ROOT, '.claude-plugin', 'plugin.json'), encoding='utf-8') as f:
            self.assertEqual(json.load(f)['version'], agent_runbooks.__version__)


if __name__ == '__main__':
    unittest.main()
