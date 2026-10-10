#!/usr/bin/env python3
"""Regenerates the run folders test_view.py reads: python3 tests/fixtures/make.py, from anywhere.

Each run is driven through agent_runbooks.py's commands, the engine in skills/agent-runbook-authoring/references, with
fake executors: a reply is passed as an orchestrator would pass it, after writing the files its launch names. The
runs are made under /tmp/rb-demo/.agent-runbooks/runs, so the paths recorded in them do not depend on the machine,
then moved here, and the directories left empty removed. The engine's clock is fixed, so the times, and the
durations the viewer shows, are the same on every regeneration.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import runpy
import shutil
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..', '..', 'skills', 'agent-runbook-authoring', 'references')
REPO = '/tmp/rb-demo'
RUNS = os.path.join(REPO, '.agent-runbooks', 'runs')

sys.path.insert(0, ROOT)
import agent_runbooks  # noqa: E402

REVIEW_LOOP = os.path.join(ROOT, 'runbook-review-loop', 'flow.py')
MIGRATE_SITES = os.path.join(HERE, 'runbook-migrate-sites', 'flow.py')
PORT_MODULES = os.path.join(HERE, 'runbook-port-modules', 'flow.py')


class Run:
    """A run of one flow.py, driven command by command; `after` moves the engine's clock before the next one."""

    def __init__(self, flow: str, name: str, start: str, inputs: dict[str, Any], files: dict[str, str]) -> None:
        argv0 = sys.argv[0]
        sys.argv[0] = flow  # the Runbook takes its directory, and the runbook's name, from it
        try:
            self.rb = runpy.run_path(flow)['rb']
        finally:
            sys.argv[0] = argv0
        self.flow = flow
        self.dir = os.path.join(RUNS, name)
        self.name = name
        self.clock = datetime.strptime(start, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        shutil.rmtree(self.dir, ignore_errors=True)
        os.makedirs(self.dir)
        for file, text in files.items():
            self.write(os.path.join(self.dir, file), text)
        self.command('start', json.dumps(inputs))

    @staticmethod
    def write(path: str, text: str) -> None:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)

    def after(self, minutes: int, seconds: int = 0) -> Run:
        self.clock += timedelta(minutes=minutes, seconds=seconds)
        return self

    def command(self, *args: str) -> None:
        agent_runbooks.utc_now = lambda: self.clock.strftime('%Y-%m-%dT%H:%M:%SZ')
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = self.rb.main([self.flow, self.dir, *args])
        if code != 0:
            raise SystemExit(f'{self.name}: {" ".join(args)} exited {code}:\n{out.getvalue()}')

    def reply(self, label: str, reply: dict[str, Any], files: dict[str, str] | None = None) -> None:
        """The executor of the attempt writes files, by the names it was told to write, then replies."""
        with open(os.path.join(self.dir, 'state.json'), encoding='utf-8') as f:
            calls = json.load(f)['calls']
        address, _, attempt = label.partition('@')
        record = next(c for c in calls if c['id'] == address and c['attempt'] == int(attempt or 1))
        for name, text in (files or {}).items():
            self.write(record['writes'][name], text)
        self.command('reply', label, json.dumps(reply))

    def keep(self) -> None:
        """Moves the run here, in place of the fixture of that name."""
        target = os.path.join(HERE, self.name)
        shutil.rmtree(target, ignore_errors=True)
        shutil.move(self.dir, target)


BRIEF_SLUG = """# Slug length

`textkit.slug.slugify` takes `max_length`: the slug is cut at a word boundary to at most that many characters.
"""


def ready() -> None:
    """The review loop to its end: a fix interrupted and launched again, the human granting one more round."""
    run = Run(
        REVIEW_LOOP,
        '20261007-slugify-max-length',
        '2026-10-07T10:00:00Z',
        {'brief': 'brief.md', 'repo': REPO, 'checks': 'python3 -m unittest', 'maxFixRounds': 1},
        {'brief.md': BRIEF_SLUG},
    )
    run.after(5, 20).reply(
        'main/implement',
        {'status': 'done'},
        {'implement.md': '# Implement\n\n`slugify(text, max_length=None)` cuts at the last hyphen that fits.\n'},
    )
    run.after(3, 5).reply(
        'main/review',
        {'status': 'done', 'findings': 2},
        {
            'review.md': '# Review\n\n1. A word longer than `max_length` gives an empty slug.\n2. The docstring omits `max_length`.\n'
        },
    )
    run.after(0, 40).command('interrupted', 'main/fix')
    run.after(2, 13).reply(
        'main/fix@2',
        {'status': 'done'},
        {
            'fix.md': '# Fix\n\nA first word longer than `max_length` is cut mid-word. The docstring is not changed yet.\n'
        },
    )
    run.after(2, 30).reply(
        'main/review#2',
        {'status': 'done', 'findings': 1},
        {'review.md': '# Review\n\n1. The docstring omits `max_length`.\n'},
    )
    run.after(1, 50).command(
        'answer', 'main/ask-rounds', json.dumps({'choice': 'more rounds', 'rounds': 1}), 'one more, the docstring only'
    )
    run.after(1, 12).reply(
        'main/fix#2', {'status': 'done'}, {'fix.md': '# Fix\n\nThe docstring describes `max_length`.\n'}
    )
    run.after(2, 1).reply(
        'main/review#3',
        {'status': 'done', 'findings': 0},
        {'review.md': '# Review\n\n`slugify` takes `max_length` and cuts at a word boundary. No findings.\n'},
    )
    run.keep()


def question() -> None:
    """The review loop waiting for the human after its fix round, with a reply corrected on the way."""
    run = Run(
        REVIEW_LOOP,
        '20261007-slugify-transliterate-question',
        '2026-10-07T14:00:00Z',
        {'brief': 'brief.md', 'repo': REPO, 'checks': 'python3 -m unittest', 'maxFixRounds': 1},
        {'brief.md': '# Transliterate\n\n`slugify` transliterates Cyrillic to Latin before it drops other letters.\n'},
    )
    run.after(6, 2).reply(
        'main/implement',
        {'status': 'done'},
        {'implement.md': '# Implement\n\n`textkit/translit.py` maps Cyrillic letters; `slugify` calls it first.\n'},
    )
    run.after(4, 10).reply(
        'main/review',
        {'status': 'done', 'findings': 3},
        {
            'review.md': '# Review\n\n1. `ё` is dropped.\n2. `Щ` gives `Sch`, not `Shch`.\n3. No test for mixed scripts.\n'
        },
    )
    run.after(3, 30).reply(
        'main/fix',
        {'status': 'done'},
        {'fix.md': '# Fix\n\n`ё` maps to `e`, `Щ` to `Shch`. The mixed-script test is still missing.\n'},
    )
    run.after(0, 50).reply(
        'main/review#2', {'status': 'done'}, {'review.md': '# Review\n\n1. No test for mixed scripts.\n'}
    )
    run.after(0, 20).command('reply', 'main/review#2', json.dumps({'status': 'done', 'findings': 1}))
    run.keep()


def groups() -> None:
    """A foreach ended by a failed item, with a question withdrawn and an item cancelled, then a parallel in flight."""
    sites = [
        {'key': 'auth', 'table': 'users'},
        {'key': 'billing', 'table': 'invoices'},
        {'key': 'search', 'table': 'documents'},
    ]
    run = Run(
        MIGRATE_SITES,
        '20261007-migrate-sites-running',
        '2026-10-07T09:00:00Z',
        {'brief': 'brief.md', 'repo': REPO},
        {'brief.md': '# Migrate sites\n\nMove every site to the new schema, one table per site.\n'},
    )
    run.after(2, 10).reply('main/plan', {'status': 'done'}, {'sites.json': json.dumps(sites, indent=2) + '\n'})
    run.after(3, 20).reply(
        'main/sites[billing]/migrate',
        {'status': 'done', 'risky': True},
        {'migrate.md': '# Migrate billing\n\n`invoices` moves to the new schema; `ledger` reads it too.\n'},
    )
    run.after(1, 10).reply(
        'main/sites[auth]/migrate',
        {'status': 'done', 'risky': False},
        {'migrate.md': '# Migrate auth\n\n`users` moves to the new schema.\n'},
    )
    run.after(2, 20).reply(
        'main/sites[auth]/verify',
        {'status': 'failed', 'reason': 'users.email loses its NOT NULL constraint'},
        {
            'verify.md': '# Verify auth\n\n`test_signup_requires_email` fails: the new schema lets `users.email` be null.\n'
        },
    )
    run.after(3, 0).reply(
        'main/reviews/a',
        {'status': 'done', 'findings': 1},
        {'review.md': '# Review\n\n1. auth failed verification; billing and search are not migrated.\n'},
    )
    run.after(0, 30).reply('main/reviews/b/review', {'status': 'blocked', 'reason': 'no second reviewer is set up'})
    run.keep()


def nested() -> None:
    """A foreach whose items each run a parallel: one item done, one with a branch step interrupted and launched
    again, one porting, one not started."""
    modules = [
        {'key': 'core', 'path': 'src/core'},
        {'key': 'cli', 'path': 'src/cli'},
        {'key': 'web', 'path': 'src/web'},
        {'key': 'docs', 'path': 'docs'},
    ]
    run = Run(
        PORT_MODULES,
        '20261007-port-modules-running',
        '2026-10-07T16:00:00Z',
        {'brief': 'brief.md', 'repo': REPO},
        {'brief.md': '# Port modules\n\nPort every module to the new API.\n'},
    )
    run.after(1, 0).reply('main/plan', {'status': 'done'}, {'modules.json': json.dumps(modules, indent=2) + '\n'})
    run.after(2, 0).reply(
        'main/modules[core]/port', {'status': 'done'}, {'port.md': '# Port core\n\n`core.api` takes the new client.\n'}
    )
    run.after(0, 30).reply('main/modules[core]/checks/lint', {'status': 'done', 'warnings': 0})
    run.after(1, 0).reply(
        'main/modules[cli]/port', {'status': 'done'}, {'port.md': '# Port cli\n\nThe commands call `core.api`.\n'}
    )
    run.after(0, 40).reply(
        'main/modules[core]/checks/tests/test',
        {'status': 'done', 'passed': False},
        {'test.md': '# Test core\n\n`test_retry` fails: the new client does not retry on 503.\n'},
    )
    run.after(0, 20).reply('main/modules[cli]/checks/lint', {'status': 'done', 'warnings': 2})
    run.after(0, 50).command('interrupted', 'main/modules[core]/checks/tests/fix')
    run.after(1, 0).reply(
        'main/modules[cli]/checks/tests/test',
        {'status': 'done', 'passed': True},
        {'test.md': '# Test cli\n\nAll tests pass.\n'},
    )
    run.keep()


if __name__ == '__main__':
    os.makedirs(RUNS, exist_ok=True)
    ready()
    question()
    groups()
    nested()
    os.removedirs(RUNS)
