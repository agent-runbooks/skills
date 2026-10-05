"""Tests of view.py: run with `python3 -m unittest test_view -v` from this directory."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

import view

EXAMPLE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tests', 'fixtures', '20261002-slugify-max-length'
)
NOW = datetime(2026, 10, 3, 14, 30, 0, tzinfo=timezone.utc)


def section(
    sid: str,
    status: str,
    reply: dict[str, Any] | None = None,
    note: str | None = None,
    executor: str | None = 'strong',
    started_at: str | None = None,
    ended_at: str | None = None,
) -> dict[str, Any]:
    """A section as state.json records it; fix-2 is a section of step fix."""
    return dict(
        id=sid,
        name=re.sub(r'-[0-9]+$', '', sid),
        status=status,
        reply=reply,
        note=note,
        answer=None,
        executor=executor,
        started_at=started_at,
        ended_at=ended_at,
    )


SYNTHETIC = {
    'runbook': 'runbook-task-cycle',
    'status': 'waiting_for_human',
    'inputs': {'repo': '/repo', 'maxFixRounds': 2},
    'sections': [
        section(
            'preflight',
            'done',
            {'status': 'done', 'clean': True},
            executor='light',
            started_at='2026-10-03T14:00:00Z',
            ended_at='2026-10-03T14:00:42Z',
        ),
        section(
            'fix', 'failed', note='interrupted', started_at='2026-10-03T14:01:00Z', ended_at='2026-10-03T14:01:10Z'
        ),
        section(
            'fix-2', 'done', {'status': 'done'}, started_at='2026-10-03T14:01:10Z', ended_at='2026-10-03T15:02:13Z'
        ),
        section(
            'verify',
            'failed',
            {'status': 'failed', 'reason': 'tests red'},
            started_at='2026-10-03T14:10:00Z',
            ended_at='2026-10-03T14:12:00Z',
        ),
        section('review-a', 'running', started_at='2026-10-03T14:27:47Z'),
        section('ask-rounds', 'waiting_for_human', executor=None, started_at='2026-10-03T14:29:00Z'),
    ],
}
PROGRESS = """# Run synthetic

## Log

- preflight: launched
- preflight: {"status": "done", "clean": true}
- fix: launched
- fix: interrupted
- fix-2: launched
- fix-2: {"status": "done"}
- ask-rounds: asked: One more round, or stop?
"""


class RunDirTestCase(unittest.TestCase):
    """A synthetic run in a temporary directory: <root>/runs/synthetic."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.realpath(tmp.name)
        self.runs = os.path.join(self.root, 'runs')
        self.run_dir = os.path.join(self.runs, 'synthetic')
        os.makedirs(self.run_dir)
        self.write('state.json', json.dumps(SYNTHETIC))
        self.write('progress.md', PROGRESS)
        for name in ('brief.md', '00-preflight.md', '01-fix.md', '02-fix.md', '03-verify.md', 'changes.diff'):
            self.write(name, f'# {name}\n')

    def write(self, name: str, text: str, run: str | None = None) -> None:
        with open(os.path.join(run or self.run_dir, name), 'w', encoding='utf-8') as f:
            f.write(text)


class StatusTest(RunDirTestCase):
    def test_old_format_example(self) -> None:
        self.assertEqual(
            view.status_text(os.path.realpath(EXAMPLE), NOW).splitlines(),
            [
                '20261002-slugify-max-length · runbook-task-cycle · ready',
                '✓ preflight  clean: true',
                '✓ implement',
                '✓ checks     passed: true',
                '✓ review-a   findings: 1',
                '✓ review-b   findings: 0',
                '✓ triage     to_fix: 1',
                '✓ fix',
                '✓ verify     unresolved: 0, passed: true',
                '✓ polish     passed: true',
            ],
        )

    def test_synthetic_run(self) -> None:
        self.assertEqual(
            view.status_text(self.run_dir, NOW).splitlines(),
            [
                'synthetic · runbook-task-cycle · waiting_for_human',
                '✓ preflight   light      0:42  clean: true',
                '✗ fix         strong     0:10  interrupted',
                '✓ fix-2       strong  1:01:03',
                '✗ verify      strong     2:00  tests red',
                '● review-a    strong     2:13',
                '? ask-rounds             1:00  waiting for the human',
            ],
        )

    def test_answered_human_step_shows_the_choice(self) -> None:
        answered = section('ask-rounds', 'done', note='one more round', executor=None)
        self.assertEqual(view.summary(answered), 'one more round')

    def test_duration_without_timestamps_is_empty(self) -> None:
        self.assertEqual(view.duration(section('fix', 'running'), NOW), '')
        self.assertEqual(view.duration(section('fix', 'done', started_at='2026-10-03T14:00:00Z'), NOW), '')


class FilesTest(RunDirTestCase):
    def test_section_files_and_log(self) -> None:
        snap = view.snapshot(self.run_dir, NOW)
        self.assertEqual(snap['now'], '2026-10-03T14:30:00Z')
        self.assertEqual(snap['state'], SYNTHETIC)
        self.assertEqual(
            [(s['id'], s['files']) for s in snap['sections']],
            [
                ('preflight', ['00-preflight.md']),
                ('fix', ['01-fix.md']),
                ('fix-2', ['02-fix.md']),
                ('verify', ['03-verify.md']),
                ('review-a', []),
                ('ask-rounds', []),
            ],
        )
        self.assertEqual(snap['run_files'], ['brief.md', 'changes.diff', 'progress.md'])
        logs = {s['id']: s['log'] for s in snap['sections']}
        self.assertEqual(logs['fix'], ['- fix: launched', '- fix: interrupted'])
        self.assertEqual(logs['fix-2'], ['- fix-2: launched', '- fix-2: {"status": "done"}'])
        self.assertEqual(logs['ask-rounds'], ['- ask-rounds: asked: One more round, or stop?'])
        self.assertIn(
            {
                'name': 'state.json',
                'size': len(json.dumps(SYNTHETIC)),
                'mtime': os.path.getmtime(os.path.join(self.run_dir, 'state.json')),
            },
            snap['files'],
        )

    def test_numbering_needs_two_digits_and_a_known_section(self) -> None:
        by_section, rest = view.section_files(['00-a.md', '0-b.md', '07-c.md', '100-d.md', 'state.json'], 2)
        self.assertEqual(by_section, [['00-a.md'], []])
        self.assertEqual(rest, ['0-b.md', '07-c.md', '100-d.md'])

    def test_latest_run_in_a_runs_directory(self) -> None:
        older = os.path.join(self.runs, 'older')
        os.makedirs(older)
        self.write('state.json', json.dumps(SYNTHETIC), run=older)
        os.makedirs(os.path.join(self.runs, 'not-a-run'))
        os.utime(os.path.join(older, 'state.json'), (1_000_000_000, 1_000_000_000))
        self.assertEqual(view.find_run(self.runs), self.run_dir)
        self.assertEqual(view.find_run(older), older)
        with self.assertRaises(view.RunError):
            view.find_run(self.root)

    def test_file_names_outside_the_run_are_refused(self) -> None:
        self.write('secret.txt', 'secret', run=self.root)
        os.symlink(os.path.join(self.root, 'secret.txt'), os.path.join(self.run_dir, 'link.md'))
        os.makedirs(os.path.join(self.run_dir, 'sub'))
        self.assertEqual(view.run_file(self.run_dir, 'brief.md'), os.path.join(self.run_dir, 'brief.md'))
        for name in ('', '.', '..', '../secret.txt', '/etc/passwd', 'sub', 'sub/x', 'link.md', 'missing.md'):
            with self.subTest(name=name):
                self.assertIsNone(view.run_file(self.run_dir, name))

    def test_half_written_state(self) -> None:
        self.write('state.json', json.dumps(SYNTHETIC)[:40])
        with self.assertRaises(view.StateUnreadable):
            view.snapshot(self.run_dir, NOW)


class ServerTest(RunDirTestCase):
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
        self.assertEqual((snap['name'], snap['state']['status']), ('synthetic', 'waiting_for_human'))
        self.assertEqual(self.get('/api/file?name=01-fix.md'), (200, 'text/plain; charset=utf-8', b'# 01-fix.md\n'))
        self.assertEqual(self.get('/api/file?name=..%2Fsynthetic%2Fbrief.md')[0], 404)
        self.assertEqual(self.get('/nope')[0], 404)
        self.write('state.json', '{"runbook": ')
        self.assertEqual(self.get('/api/state')[0], 503)

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
