"""Tests of view.py: run with `python3 -m unittest test_view -v` from this directory."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from unittest import mock

import view

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.realpath(os.path.join(HERE, '..', '..', 'tests', 'fixtures'))
# Made by tests/fixtures/make.py. Each NOW_ is the clock the fixture's open attempts are timed against.
READY = os.path.join(FIXTURES, '20261007-slugify-max-length')
QUESTION = os.path.join(FIXTURES, '20261007-slugify-transliterate-question')
RUNNING = os.path.join(FIXTURES, '20261007-migrate-sites-running')
NESTED = os.path.join(FIXTURES, '20261007-port-modules-running')
NOW_READY = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
NOW_QUESTION = datetime(2026, 10, 7, 14, 20, 0, tzinfo=timezone.utc)
NOW_RUNNING = datetime(2026, 10, 7, 9, 14, 0, tzinfo=timezone.utc)
NOW_NESTED = datetime(2026, 10, 7, 16, 10, 0, tzinfo=timezone.utc)

# A run as engine 1.x recorded it.
FORMAT_1 = {
    'runbook': 'runbook-task-cycle',
    'status': 'ready',
    'inputs': {},
    'sections': [{'id': 'preflight', 'name': 'preflight', 'status': 'done', 'reply': {'status': 'done'}}],
}


class RunDirTestCase(unittest.TestCase):
    """A copy of `fixture`, the running one by default, in a temporary directory: <root>/runs/<name>."""

    fixture = RUNNING

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.realpath(tmp.name)
        self.runs = os.path.join(self.root, 'runs')
        self.run_dir = os.path.join(self.runs, os.path.basename(self.fixture))
        shutil.copytree(self.fixture, self.run_dir)

    def write(self, name: str, text: str, run: str | None = None) -> None:
        with open(os.path.join(run or self.run_dir, name), 'w', encoding='utf-8') as f:
            f.write(text)


class StatusTest(unittest.TestCase):
    def test_finished_run(self) -> None:
        self.assertEqual(
            view.status_text(READY, NOW_READY),
            """\
20261007-slugify-max-length · runbook-review-loop · ready
✓ implement   coder      5:20
✓ review      reviewer   3:05  findings: 2
✓ fix@2       coder      2:13
✓ review#2    reviewer   2:30  findings: 1
✓ ask-rounds             1:50  more rounds, rounds: 1
✓ fix#2       coder      1:12
✓ review#3    reviewer   2:01  findings: 0""",
        )

    def test_run_waiting_for_the_human(self) -> None:
        self.assertEqual(
            view.status_text(QUESTION, NOW_QUESTION),
            """\
20261007-slugify-transliterate-question · runbook-review-loop · waiting_for_human
✓ implement   coder      6:02
✓ review      reviewer   4:10  findings: 3
✓ fix         coder      3:30
✓ review#2    reviewer   1:10  findings: 1
? ask-rounds             5:08  waiting for the human""",
        )

    def test_run_with_groups(self) -> None:
        self.assertEqual(
            view.status_text(RUNNING, NOW_RUNNING),
            """\
20261007-migrate-sites-running · runbook-migrate-sites · running
✓ plan  coder   2:10
  foreach sites: 1 failed (auth), 1 cancelled, 1 not started
  ✗ [auth]     verify       reviewer   6:50  users.email loses its NOT NULL constraint
  ✗ [billing]  approve                 6:50  cancelled
  · [search]   not started
  parallel reviews: 1 done, 1 running
  ✓ a  review    reviewer   3:00  findings: 1
  ● b  review#2  coder      5:00""",
        )

    def test_nested_groups(self) -> None:
        self.assertEqual(
            view.status_text(NESTED, NOW_NESTED),
            """\
20261007-port-modules-running · runbook-port-modules · running
✓ plan  coder   1:00
  foreach modules: 1 done, 2 running, 1 not started
  ● [core]  checks/tests/fix@2  coder      9:00
    parallel checks: 1 done, 1 running
    ✓ lint   lint   reviewer   0:30  warnings: 0
    ● tests  fix@2  coder      7:00
  ✓ [cli]   checks/tests/test   reviewer   6:20  warnings: 2, passed: true
    parallel checks: 2 done
    ✓ lint   lint  reviewer   1:00  warnings: 2
    ✓ tests  test  reviewer   2:50  passed: true
  ● [web]   port                coder      2:40
  · [docs]  not started""",
        )

    def test_tail_inside_a_nested_group(self) -> None:
        self.assertEqual(
            view.status_text(NESTED, NOW_NESTED, tail=3),
            """\
20261007-port-modules-running · runbook-port-modules · running
… 6 rows above
  foreach modules: 1 done, 2 running, 1 not started
    parallel modules[cli]/checks: 2 done
    ✓ tests  test  reviewer   2:50  passed: true
  ● [web]   port                coder      2:40
  · [docs]  not started""",
        )

    def test_tail_at_a_groups_first_row(self) -> None:
        self.assertEqual(
            view.status_text(RUNNING, NOW_RUNNING, tail=2),
            """\
20261007-migrate-sites-running · runbook-migrate-sites · running
… 4 rows above
  parallel reviews: 1 done, 1 running
  ✓ a  review    reviewer   3:00  findings: 1
  ● b  review#2  coder      5:00""",
        )

    def test_tail_at_the_top_level(self) -> None:
        self.assertEqual(
            view.status_text(READY, NOW_READY, tail=1),
            """\
20261007-slugify-max-length · runbook-review-loop · ready
… 6 rows above
✓ review#3    reviewer   2:01  findings: 0""",
        )

    def test_tail_longer_than_the_rows_shows_everything(self) -> None:
        for tail in (9, 10, 50):
            with self.subTest(tail=tail):
                self.assertEqual(view.status_text(NESTED, NOW_NESTED, tail), view.status_text(NESTED, NOW_NESTED))
        self.assertIn('… 1 row above', view.status_text(NESTED, NOW_NESTED, 8))

    def test_tail_from_the_command_line(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(view.main([NESTED, '--status', '--tail', '1']), 0)
        self.assertEqual(
            out.getvalue().splitlines()[1:],
            ['… 8 rows above', '  foreach modules: 1 done, 2 running, 1 not started', '  · [docs]  not started'],
        )

    def test_readme_shows_the_fixtures(self) -> None:
        with open(os.path.join(HERE, 'README.md'), encoding='utf-8') as f:
            samples = f.read().split('```\n')
        self.assertEqual(samples[1], view.status_text(RUNNING, NOW_RUNNING) + '\n')
        self.assertEqual(samples[3], view.status_text(NESTED, NOW_NESTED, tail=3) + '\n')

    def test_duration_without_timestamps_is_empty(self) -> None:
        self.assertEqual(view.duration({'status': 'running'}, NOW_READY), '')
        self.assertEqual(view.duration({'status': 'done', 'started_at': '2026-10-07T10:00:00Z'}, NOW_READY), '')

    def test_free_text_answer_is_the_choice(self) -> None:
        answered = {'kind': 'human', 'status': 'done', 'reply': {'choice': 'stop after this round'}}
        self.assertEqual(view.summary(answered), 'stop after this round')


class FormatTest(RunDirTestCase):
    def test_other_formats_are_refused(self) -> None:
        path = os.path.join(self.run_dir, 'state.json')
        for state, engine in (
            (FORMAT_1, 'format 1, from runbook.py 1.x'),
            ({'format': 3}, 'format 3, from agent_runbooks.py 3.x'),
        ):
            self.write('state.json', json.dumps(state))
            with self.subTest(engine=engine):
                with self.assertRaises(view.WrongFormat) as caught:
                    view.read_state(self.run_dir)
                self.assertEqual(
                    str(caught.exception), f'{path} is {engine}; this viewer reads format 2, from agent_runbooks.py 2.x'
                )

    def test_status_and_page_refuse_with_one_line(self) -> None:
        self.write('state.json', json.dumps(FORMAT_1))
        for argv in ([self.run_dir, '--status'], [self.runs]):
            err = io.StringIO()
            with self.subTest(argv=argv), contextlib.redirect_stderr(err):
                self.assertEqual(view.main(argv), 2)
            self.assertRegex(err.getvalue(), r'^view\.py: .* is format 1, from runbook\.py 1\.x; [^\n]*\n$')


class FilesTest(RunDirTestCase):
    def test_temporary_state_is_not_listed_or_served(self) -> None:
        self.write('state.json.tmp', '{"runbook": ')
        stat = os.stat

        def check_stat(path: str) -> os.stat_result:
            self.assertNotEqual(path, os.path.join(self.run_dir, 'state.json.tmp'))
            return stat(path)

        with mock.patch.object(view.os, 'stat', side_effect=check_stat):
            snap = view.snapshot(self.run_dir, NOW_RUNNING)
            self.assertIsNone(view.run_file(self.run_dir, 'state.json.tmp'))
        self.assertNotIn('state.json.tmp', [f['name'] for f in snap['files']])
        self.assertNotIn('state.json.tmp', snap['run_files'])

    def test_file_disappearing_before_stat_is_skipped(self) -> None:
        path = os.path.join(self.run_dir, '04-migrate.md')
        isfile = os.path.isfile

        def remove_after_isfile(candidate: str) -> bool:
            result = isfile(candidate)
            if candidate == path:
                os.remove(candidate)
            return result

        with mock.patch.object(view.os.path, 'isfile', side_effect=remove_after_isfile):
            snap = view.snapshot(self.run_dir, NOW_RUNNING)
        self.assertNotIn('04-migrate.md', [f['name'] for f in snap['files']])
        self.assertEqual(snap['calls'][1]['files'], [])

    def test_logs_match_the_whole_label(self) -> None:
        lines = [
            '- main/a: first',
            '- main/a@2: second',
            '- main/a#2: third',
            '- main/a:no space',
            '- main/a/b: nested',
            '- end: ready (after `main/a`)',
            'main/a: not a log line',
            '- main/a: last',
        ]
        self.assertEqual(
            view.call_logs(lines, ['main/a', 'main/a@2', 'main/a#2', 'absent']),
            {
                'main/a': ['- main/a: first', '- main/a: last'],
                'main/a@2': ['- main/a@2: second'],
                'main/a#2': ['- main/a#2: third'],
                'absent': [],
            },
        )

    def test_logs_match_any_spelling_of_the_attempt(self) -> None:
        lines = [
            '- main/a: launched',
            '- main/a@1: interrupted',
            '- main/a@01: relaunched',
            '- main/a@02: done',
            '- main/a@3: done',
        ]
        self.assertEqual(
            view.call_logs(lines, ['main/a', 'main/a@2']),
            {
                'main/a': ['- main/a: launched', '- main/a@1: interrupted', '- main/a@01: relaunched'],
                'main/a@2': ['- main/a@02: done'],
            },
        )

    def test_snapshot_reads_and_scans_progress_once(self) -> None:
        class Lines(list[str]):
            scans = 0

            def __iter__(self):
                self.scans += 1
                return super().__iter__()

        progress = Lines(view.read_progress(self.run_dir))
        with mock.patch.object(view, 'read_progress', return_value=progress) as read:
            view.snapshot(self.run_dir, NOW_RUNNING)
        read.assert_called_once_with(self.run_dir)
        self.assertEqual(progress.scans, 1)

    def test_attempts_of_groups_and_items(self) -> None:
        snap = view.snapshot(self.run_dir, NOW_RUNNING)
        self.assertEqual(snap['now'], '2026-10-07T09:14:00Z')
        self.assertEqual(snap['state']['format'], 2)
        self.assertEqual(
            [(c['label'], c['name'], c['mark'], c['files']) for c in snap['calls']],
            [
                ('main/plan', 'plan', '✓', ['00-sites.json']),
                ('main/sites[auth]/migrate', 'sites[auth]/migrate', '✓', ['04-migrate.md']),
                ('main/sites[billing]/migrate', 'sites[billing]/migrate', '✓', ['05-migrate.md']),
                ('main/sites[billing]/approve', 'sites[billing]/approve', '✗', []),
                ('main/sites[auth]/verify', 'sites[auth]/verify', '✗', ['07-verify.md']),
                ('main/reviews/a', 'reviews/a', '✓', ['09-review.md']),
                ('main/reviews/b/review', 'reviews/b/review', '!', []),
                ('main/reviews/b/review#2', 'reviews/b/review#2', '●', []),
            ],
        )
        self.assertEqual(snap['run_files'], ['01-sites.index.json', 'brief.md', 'progress.md'])
        calls = {c['label']: c for c in snap['calls']}
        self.assertEqual(calls['main/sites[billing]/approve']['note'], 'cancelled')
        self.assertEqual(calls['main/sites[billing]/approve']['log'][1], '- main/sites[billing]/approve: cancelled')
        self.assertEqual(calls['main/reviews/b/review#2']['log'], ['- main/reviews/b/review#2: launched'])
        self.assertIn(
            {
                'name': 'state.json',
                'size': os.path.getsize(os.path.join(self.run_dir, 'state.json')),
                'mtime': os.path.getmtime(os.path.join(self.run_dir, 'state.json')),
            },
            snap['files'],
        )

    def test_numbered_files_no_attempt_was_told_to_write_are_run_files(self) -> None:
        self.write('10-review.md', '# the blocked review left a note\n')
        self.write('99-notes.md', '# notes\n')
        snap = view.snapshot(self.run_dir, NOW_RUNNING)
        self.assertEqual(snap['calls'][6]['files'], ['10-review.md'])
        self.assertEqual(snap['run_files'], ['01-sites.index.json', '99-notes.md', 'brief.md', 'progress.md'])

    def test_latest_run_in_a_runs_directory(self) -> None:
        older = os.path.join(self.runs, 'older')
        shutil.copytree(READY, older)
        os.makedirs(os.path.join(self.runs, 'not-a-run'))
        os.utime(os.path.join(older, 'state.json'), (1_000_000_000, 1_000_000_000))
        self.assertEqual(view.find_run(self.runs), self.run_dir)
        self.assertEqual(view.find_run(older), older)
        with self.assertRaises(view.RunError):
            view.find_run(self.root)

    def test_file_names_outside_the_run_are_refused(self) -> None:
        self.write('secret.txt', 'secret', run=self.root)
        os.symlink(os.path.join(self.root, 'secret.txt'), os.path.join(self.run_dir, 'link.md'))
        self.assertEqual(view.run_file(self.run_dir, 'brief.md'), os.path.join(self.run_dir, 'brief.md'))
        for name in ('', '.', '..', '../secret.txt', '/etc/passwd', 'schemas', 'schemas/00-plan.json', 'link.md'):
            with self.subTest(name=name):
                self.assertIsNone(view.run_file(self.run_dir, name))

    def test_half_written_state(self) -> None:
        with open(os.path.join(self.run_dir, 'state.json'), encoding='utf-8') as f:
            text = f.read()
        self.write('state.json', text[:40])
        with self.assertRaises(view.StateUnreadable):
            view.snapshot(self.run_dir, NOW_RUNNING)


class ReadyFilesTest(RunDirTestCase):
    fixture = READY

    def test_attempts_of_one_call(self) -> None:
        calls = {c['label']: c for c in view.snapshot(self.run_dir, NOW_READY)['calls']}
        self.assertEqual(calls['main/fix']['files'], [])
        self.assertEqual(calls['main/fix']['log'], ['- main/fix: launched', '- main/fix: interrupted'])
        self.assertEqual((calls['main/fix@2']['name'], calls['main/fix@2']['files']), ('fix@2', ['03-fix.md']))
        self.assertEqual(calls['main/fix@2']['log'], ['- main/fix@2: launched', '- main/fix@2: {"status": "done"}'])
        self.assertEqual(calls['main/ask-rounds']['files'], ['05-rounds.md'])
        self.assertEqual(
            calls['main/ask-rounds']['log'][1:],
            [
                '- main/ask-rounds: answered: more rounds {"rounds": 1}',
                '- main/ask-rounds: said: one more, the docstring only',
            ],
        )

    def test_an_interrupted_attempts_partial_output_is_its_own(self) -> None:
        self.write('02-fix.md', '# half a fix\n')
        snap = view.snapshot(self.run_dir, NOW_READY)
        self.assertEqual(snap['calls'][2]['files'], ['02-fix.md'])
        self.assertNotIn('02-fix.md', snap['run_files'])


class RowsTest(unittest.TestCase):
    """The rows /api/state gives the page: what a selected row shows on the right is every attempt in `attempts`."""

    def rows(self, run: str, now: datetime) -> dict[str, dict]:
        return {r['id']: r for r in view.snapshot(run, now)['rows'] if r['type'] not in view.GROUPS}

    def test_branch_and_item_hold_every_attempt_in_them(self) -> None:
        rows = self.rows(NESTED, NOW_NESTED)
        tests = rows['main/modules[core]/checks/tests']
        self.assertEqual(
            {k: tests[k] for k in ('type', 'group', 'depth', 'label', 'step', 'status', 'mark', 'executor', 'open')},
            {
                'type': 'branch',
                'group': 'main/modules[core]/checks',
                'depth': 2,
                'label': 'tests',
                'step': 'fix@2',
                'status': 'running',
                'mark': '●',
                'executor': 'coder',
                'open': True,
            },
        )
        self.assertEqual(
            tests['attempts'],
            [
                'main/modules[core]/checks/tests/test',
                'main/modules[core]/checks/tests/fix',
                'main/modules[core]/checks/tests/fix@2',
            ],
        )
        self.assertEqual(
            rows['main/modules[core]']['attempts'],
            [
                'main/modules[core]/port',
                'main/modules[core]/checks/lint',
                'main/modules[core]/checks/tests/test',
                'main/modules[core]/checks/tests/fix',
                'main/modules[core]/checks/tests/fix@2',
            ],
        )
        cli = rows['main/modules[cli]']
        self.assertEqual(
            (cli['status'], cli['note'], cli['started_at'], cli['ended_at']),
            ('done', 'warnings: 2, passed: true', '2026-10-07T16:01:00Z', '2026-10-07T16:07:20Z'),
        )

    def test_not_started_item(self) -> None:
        docs = self.rows(NESTED, NOW_NESTED)['main/modules[docs]']
        self.assertEqual(
            (docs['label'], docs['status'], docs['mark'], docs['step'], docs['attempts'], docs['started_at']),
            ('[docs]', 'not_started', '·', 'not started', [], None),
        )

    def test_failed_and_cancelled_items(self) -> None:
        rows = self.rows(RUNNING, NOW_RUNNING)
        self.assertEqual(
            [(rows[a]['status'], rows[a]['attempts']) for a in ('main/sites[auth]', 'main/sites[billing]')],
            [
                ('failed', ['main/sites[auth]/migrate', 'main/sites[auth]/verify']),
                ('cancelled', ['main/sites[billing]/migrate', 'main/sites[billing]/approve']),
            ],
        )
        self.assertEqual(rows['main/reviews/a']['attempts'], ['main/reviews/a'])

    def test_relaunched_call_is_one_row(self) -> None:
        snap = view.snapshot(READY, NOW_READY)
        fix = [r for r in snap['rows'] if r['id'] == 'main/fix']
        self.assertEqual(len(fix), 1)
        self.assertEqual(
            (fix[0]['type'], fix[0]['label'], fix[0]['status'], fix[0]['attempts']),
            ('call', 'fix@2', 'done', ['main/fix', 'main/fix@2']),
        )
        self.assertEqual([c['label'] for c in snap['calls']][2:4], ['main/fix', 'main/fix@2'])

    def test_branch_past_its_foreach_while_the_parallel_runs(self) -> None:
        def record(address: str, kind: str, status: str, start: str, end: str | None = None, **fields) -> dict:
            step = address.rsplit('/', 1)[-1].split('[', 1)[0]
            times = {'started_at': f'2026-10-07T09:{start}Z', 'ended_at': end and f'2026-10-07T09:{end}Z'}
            return {'id': address, 'attempt': 1, 'kind': kind, 'step': step, 'status': status, **fields, **times}

        def lines(*inner: dict) -> list[dict]:
            return view.layout(
                {
                    'calls': [
                        record('main/p', 'parallel', 'running', '00:00'),
                        *inner,
                        record('main/p/b', 'step', 'running', '00:00', executor='coder'),
                    ]
                }
            )

        empty = lines(record('main/p/a/sites', 'foreach', 'done', '00:00', '00:00', items=[], index=[]))
        skipped = lines(
            record('main/p/a/sites', 'foreach', 'done', '00:00', '02:00', items=[{'key': 'x'}]),
            record('main/p/a/sites[x]', 'item', 'failed', '00:00', '02:00', reason='no access'),
            record('main/p/a/sites[x]/migrate', 'step', 'failed', '00:00', '02:00', executor='coder'),
        )
        for case in (empty, skipped):
            a = next(e for e in case if e['id'] == 'main/p/a')
            self.assertEqual(case[0]['counts'], '1 done, 1 running')
            self.assertEqual((a['status'], a['mark'], a['step'], a['note']), ('done', '✓', 'sites', ''))
        self.assertEqual(next(e for e in skipped if e['id'] == 'main/p/a')['ended_at'], '2026-10-07T09:02:00Z')

    def test_groups_sit_where_they_opened(self) -> None:
        lines = view.snapshot(NESTED, NOW_NESTED)['rows']
        self.assertEqual(
            [(e['type'], e['id'], e['depth']) for e in lines],
            [
                ('call', 'main/plan', 0),
                ('foreach', 'main/modules', 1),
                ('item', 'main/modules[core]', 1),
                ('parallel', 'main/modules[core]/checks', 2),
                ('branch', 'main/modules[core]/checks/lint', 2),
                ('branch', 'main/modules[core]/checks/tests', 2),
                ('item', 'main/modules[cli]', 1),
                ('parallel', 'main/modules[cli]/checks', 2),
                ('branch', 'main/modules[cli]/checks/lint', 2),
                ('branch', 'main/modules[cli]/checks/tests', 2),
                ('item', 'main/modules[web]', 1),
                ('item', 'main/modules[docs]', 1),
            ],
        )
        self.assertEqual(lines[3]['within'], ['main/modules'])
        self.assertEqual(lines[4]['within'], ['main/modules', 'main/modules[core]/checks'])


class ServerTest(RunDirTestCase):
    fixture = QUESTION

    def setUp(self) -> None:
        super().setUp()
        self.server = view.RunServer(self.run_dir)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path: str, headers: dict[str, str] | None = None) -> tuple[int, str, bytes]:
        request = urllib.request.Request(self.server.url.rstrip('/') + path, headers=headers or {})
        try:
            with urllib.request.urlopen(request) as res:
                return res.status, res.headers['Content-Type'], res.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.headers['Content-Type'], e.read()

    def test_endpoints(self) -> None:
        status, content_type, body = self.get('/')
        self.assertEqual((status, content_type), (200, 'text/html; charset=utf-8'))
        self.assertIn(b'marked.umd.js', body)
        self.assertEqual(self.get('/marked.umd.js')[0], 200)
        self.assertEqual(self.get('/page.js')[0], 200)
        status, _, body = self.get('/api/state')
        self.assertEqual(status, 200)
        snap = json.loads(body)
        self.assertEqual(
            (snap['name'], snap['state']['status']), ('20261007-slugify-transliterate-question', 'waiting_for_human')
        )
        waiting = snap['calls'][-1]
        self.assertEqual((waiting['label'], waiting['status'], waiting['mark']), ('main/ask-rounds', 'waiting', '?'))
        self.assertTrue(waiting['log'][0].startswith('- main/ask-rounds: asked: Fix rounds are spent and `/'))
        review = snap['calls'][3]
        self.assertEqual(
            (review['name'], review['files'], review['note']), ('review#2', ['03-review.md'], 'findings: 1')
        )
        self.assertEqual(
            self.get('/api/file?name=03-review.md'),
            (200, 'text/plain; charset=utf-8', b'# Review\n\n1. No test for mixed scripts.\n'),
        )
        self.write('state.json.tmp', '{"runbook": ')
        self.assertEqual(self.get('/api/file?name=state.json.tmp')[0], 404)
        self.assertEqual(self.get('/api/file?name=..%2F' + os.path.basename(self.run_dir) + '%2Fbrief.md')[0], 404)
        self.assertEqual(self.get('/nope')[0], 404)
        self.write('state.json', '{"runbook": ')
        self.assertEqual(self.get('/api/state')[0], 503)
        self.write('state.json', json.dumps(FORMAT_1))
        status, _, body = self.get('/api/state')
        self.assertEqual(status, 409)
        self.assertIn(b'is format 1, from runbook.py 1.x', body)

    def test_page_runs_under_its_csp(self) -> None:
        with urllib.request.urlopen(self.server.url) as res:
            csp = res.headers['Content-Security-Policy']
            body = res.read()
        self.assertIn("script-src 'self';", csp)
        self.assertNotIn(b'<script>', body)

    def test_foreign_host_is_refused(self) -> None:
        port = self.server.server_address[1]
        self.assertEqual(self.get('/', {'Host': f'localhost:{port}'})[0], 200)
        for host in (f'evil.example:{port}', '127.0.0.1', f'127.0.0.1:{port + 1}'):
            with self.subTest(host=host):
                self.assertEqual(self.get('/api/state', {'Host': host})[0], 403)


class IdleTest(unittest.TestCase):
    def test_stops_after_no_requests(self) -> None:
        server = view.RunServer(tempfile.gettempdir())
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_until_idle, args=(0.3,), daemon=True)
        thread.start()
        for _ in range(3):
            time.sleep(0.2)
            with urllib.request.urlopen(server.url + 'page.js'):
                pass
        thread.join(0.1)
        self.assertTrue(thread.is_alive())
        thread.join(2)
        self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()
