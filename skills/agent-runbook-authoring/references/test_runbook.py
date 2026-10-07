"""Tests of runbook.py: run with `python3 -m unittest test_runbook -v` from this directory."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import sys
import tempfile
import unittest
from collections.abc import Callable
from typing import Any
from unittest import mock

import runbook
from runbook import Runbook, end, parallel


class RunbookTestCase(unittest.TestCase):
    """A temporary runbook directory with prompts, and a run directory inside a temporary root."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.here = os.path.join(os.path.realpath(tmp.name), 'runbook-test')
        os.makedirs(os.path.join(self.here, 'prompts'))
        for name in ('common.md', 'a.md', 'b.md', 'c.md', 'd.md'):
            with open(os.path.join(self.here, 'prompts', name), 'w') as f:
                f.write(name)
        self.run_dir = os.path.join(os.path.realpath(tmp.name), 'run')
        self.cmd = f'{sys.executable} {self.here}/flow.py'
        environ = mock.patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        os.environ.pop('UV', None)
        self.minute = 0
        clock = mock.patch.object(runbook, 'utc_now', self.tick)
        clock.start()
        self.addCleanup(clock.stop)

    def tick(self) -> str:
        """A fake clock: each call is one minute after the previous, from 2026-10-03T10:00:00Z."""
        self.minute += 1
        return f'2026-10-03T{10 + (self.minute - 1) // 60:02d}:{(self.minute - 1) % 60:02d}:00Z'

    def runbook(self, declare: Callable[[Runbook], None], **inputs: object) -> Runbook:
        """A Runbook as if constructed by <here>/flow.py, with repo=str and the given inputs."""
        with mock.patch.object(sys, 'argv', [os.path.join(self.here, 'flow.py')]):
            rb = Runbook()
        rb.inputs(repo=str, **inputs)
        rb.executor('main', 'Main model')
        rb.executor('light', 'Light model')
        declare(rb)
        return rb

    def call(self, rb: Runbook, *args: str) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = rb.main(['flow.py', self.run_dir, *args])
        self.assertEqual(code, 0)
        return out.getvalue()

    def fails(self, rb: Runbook, *args: str) -> str:
        """Runs a command, asserts it returns 2, and returns its stderr."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = rb.main(['flow.py', self.run_dir, *args])
        self.assertEqual(code, 2)
        return err.getvalue()

    def start(self, rb: Runbook, **given: object) -> str:
        return self.call(rb, 'start', json.dumps({'repo': '/repo', **given}))

    def reply(self, rb: Runbook, sid: str, **reply: object) -> str:
        return self.call(rb, 'reply', sid, json.dumps({'status': 'done', **reply}))

    def reject(self, rb: Runbook, sid: str, raw: str) -> str:
        """Passes an invalid reply, and again after the correction it asks for; returns the second output."""
        self.assertTrue(
            self.first_line(self.call(rb, 'reply', sid, raw)).startswith(f'the reply of step `{sid}` did not')
        )
        return self.call(rb, 'reply', sid, raw)

    def state(self) -> dict:
        with open(os.path.join(self.run_dir, 'state.json')) as f:
            return json.load(f)

    def progress(self) -> str:
        with open(os.path.join(self.run_dir, 'progress.md')) as f:
            return f.read()

    def first_line(self, out: str) -> str:
        return out.splitlines()[0]

    def check(self, rb: Runbook) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = rb.main(['flow.py', '--check'])
        return code, out.getvalue()


def linear(rb: Runbook) -> None:
    rb.start('first')
    rb.step('first', executor='light', prompt='prompts/a.md', next='second')
    rb.step('second', executor='main', prompt='prompts/b.md', next=end('ready', 'read <run>/b.md'))


class InputsTest(RunbookTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.rb = self.runbook(linear, ticket=str, rounds=2, strict=False)

    def test_defaults_and_string_coercion(self) -> None:
        self.start(self.rb, ticket='T-1', rounds='3', strict='true')
        self.assertEqual(self.state()['inputs'], {'repo': '/repo', 'ticket': 'T-1', 'rounds': 3, 'strict': True})

    def test_defaults_apply(self) -> None:
        self.start(self.rb, ticket='T-1')
        self.assertEqual(self.state()['inputs'], {'repo': '/repo', 'ticket': 'T-1', 'rounds': 2, 'strict': False})

    def test_missing_required(self) -> None:
        err = self.fails(self.rb, 'start', '{"repo": "/repo"}')
        self.assertIn("input 'ticket' is required", err)
        self.assertFalse(os.path.exists(self.run_dir))

    def test_wrong_types(self) -> None:
        err = self.fails(self.rb, 'start', '{"repo": "/repo", "ticket": 5, "rounds": true, "strict": "yes"}')
        self.assertIn("input 'ticket' must be str, got 5", err)
        self.assertIn("input 'rounds' must be int, got true", err)
        self.assertIn('input \'strict\' must be bool, got "yes"', err)

    def test_undeclared_name(self) -> None:
        err = self.fails(self.rb, 'start', '{"repo": "/repo", "ticket": "T", "extra": 1}')
        self.assertIn("input 'extra' is not declared", err)

    def test_not_an_object(self) -> None:
        self.assertIn('inputs must be one JSON object', self.fails(self.rb, 'start', '[1, 2]'))
        self.assertIn('inputs must be one JSON object', self.fails(self.rb, 'start', 'not json'))


class StartTest(RunbookTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.rb = self.runbook(linear, rounds=2)

    def test_creates_run_and_prints_first_launch(self) -> None:
        out = self.start(self.rb)
        self.assertEqual(
            self.state(),
            {
                'runbook': 'runbook-test',
                'status': 'running',
                'inputs': {'repo': '/repo', 'rounds': 2},
                'sections': [
                    {
                        'id': 'first',
                        'name': 'first',
                        'status': 'running',
                        'reply': None,
                        'note': None,
                        'answer': None,
                        'executor': 'light',
                        'started_at': '2026-10-03T10:00:00Z',
                        'ended_at': None,
                        'invalid_replies': [],
                    }
                ],
            },
        )
        self.assertEqual(
            self.progress(),
            (
                '# Run run\n\nRunbook `runbook-test`. State in `state.json`. Inputs:\n\n'
                '- repo: /repo\n- rounds: 2\n\n## Log\n\n- first: launched\n'
            ),
        )
        self.assertEqual(
            out.splitlines(),
            [
                'launch step `first` as a new subagent, executor `light`: Light model',
                '--- message ---',
                f'Read {self.here}/prompts/common.md, then {self.here}/prompts/a.md, and do what they say.',
                'repo: /repo',
                f'run: {self.run_dir}',
                f'reply schema: {self.run_dir}/schemas/first.json',
                '--- end of message ---',
                f"when it finishes: {self.cmd} {self.run_dir} reply first '<the last JSON object of its message>'",
            ],
        )

    def test_refuses_existing_directory(self) -> None:
        self.start(self.rb)
        self.assertIn('exists', self.fails(self.rb, 'start', '{"repo": "/repo"}'))

    def test_linear_transition_and_end_report(self) -> None:
        self.start(self.rb)
        self.assertEqual(
            self.first_line(self.reply(self.rb, 'first')),
            'launch step `second` as a new subagent, executor `main`: Main model',
        )
        out = self.reply(self.rb, 'second')
        self.assertEqual(
            out.strip(),
            f'end: ready (after step `second`). The run is over. Report to the human: '
            f'status ready, run directory {self.run_dir}, read {self.run_dir}/b.md.',
        )
        self.assertEqual(self.state()['status'], 'ready')
        self.assertTrue(self.progress().endswith('- second: {"status": "done"}\n- end: ready (after step `second`)\n'))

    def test_log_and_status(self) -> None:
        self.start(self.rb)
        before = self.state()
        self.assertEqual(self.call(self.rb).strip(), 'still running: `first`')
        self.assertEqual(self.state(), before)
        self.call(self.rb, 'log', 'went off script')
        self.assertTrue(self.progress().endswith('- orchestrator: went off script\n'))

    def test_main_returns_2_for_a_refused_command(self) -> None:
        err, out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            code = self.rb.main(['flow.py', self.run_dir, 'bogus'])
        self.assertEqual(code, 2)
        self.assertEqual(err.getvalue(), "flow.py: unknown command 'bogus'\n")
        self.assertEqual(out.getvalue(), '')
        self.assertFalse(os.path.exists(self.run_dir))

    def test_bad_commands(self) -> None:
        self.start(self.rb)
        self.assertIn("unknown command 'bogus'", self.fails(self.rb, 'bogus'))
        self.assertIn("usage: flow.py <run> reply <section> '<reply JSON>'", self.fails(self.rb, 'reply', 'first'))
        self.assertIn('no section nope in state.json', self.fails(self.rb, 'reply', 'nope', '{}'))
        self.reply(self.rb, 'first')
        self.assertIn('section first is done, not running', self.fails(self.rb, 'reply', 'first', '{"status": "done"}'))


class RoutingTest(RunbookTestCase):
    def test_branch_by_reply_field(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('probe')
            rb.step(
                'probe',
                executor='light',
                prompt='prompts/a.md',
                reply={'clean': bool},
                next=lambda r, s: 'clean' if r.clean else 'dirty',
            )
            rb.step('clean', executor='main', prompt='prompts/b.md', next=end('ready'))
            rb.step('dirty', executor='main', prompt='prompts/c.md', next=end('ready'))

        rb = self.runbook(declare)
        self.start(rb)
        self.assertEqual(
            self.first_line(self.reply(rb, 'probe', clean=False)),
            'launch step `dirty` as a new subagent, executor `main`: Main model',
        )

    def test_invalid_reply_twice_fails_the_run(self) -> None:
        rb = self.runbook(linear)
        base = self.run_dir
        cases = (
            ('garbage', 'not a JSON object'),
            ('[1]', 'not a JSON object'),
            ('{}', 'the message has no JSON object'),
            ('{"reason": "x"}', "no field 'status'"),
            ('{"status": "ok"}', 'field \'status\' must be one of done, failed, blocked, got "ok"'),
        )
        for i, (raw, problem) in enumerate(cases):
            with self.subTest(raw=raw):
                self.run_dir = f'{base}-{i}'
                self.start(rb)
                out = self.reject(rb, 'first', raw)
                self.assertIn(f'end: failed (step `first` failed: invalid reply: {problem})', out)
                self.assertEqual(
                    self.state()['sections'][0]['reply'], {'status': 'failed', 'reason': f'invalid reply: {problem}'}
                )

    def test_done_reply_without_declared_field_fails_the_run(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('checks')
            rb.step(
                'checks',
                executor='light',
                prompt='prompts/a.md',
                reply={'passed': bool, 'count': int},
                next=lambda r, s: end('ready') if r.passed else end('red'),
            )

        rb = self.runbook(declare)
        base = self.run_dir
        cases = (
            ({}, "no field 'passed'"),
            ({'passed': 'yes', 'count': 1}, 'field \'passed\' must be boolean, got "yes"'),
            ({'passed': True, 'count': True}, "field 'count' must be integer, got true"),
        )
        for i, (fields, problem) in enumerate(cases):
            with self.subTest(fields=fields):
                self.run_dir = f'{base}-{i}'
                self.start(rb)
                out = self.reject(rb, 'checks', json.dumps({'status': 'done', **fields}))
                self.assertIn(f'end: failed (step `checks` failed: invalid reply: {problem})', out)

    def test_a_raising_next_keeps_the_reply_and_the_run_resumes(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('first')
            rb.step(
                'first',
                executor='light',
                prompt='prompts/a.md',
                reply={'n': int},
                next=lambda r, s: 'second' if r.n < limit[0] else end('ready'),
            )
            rb.step('second', executor='main', prompt='prompts/b.md', next=end('ready'))

        limit: list[object] = ['3']
        rb = self.runbook(declare)
        self.start(rb)
        err = self.fails(rb, 'reply', 'first', '{"status": "done", "n": 1}')
        self.assertIn('flow.py: `next` of step `first` raised TypeError: ', err)
        self.assertIn(
            f'What you passed is recorded. This is a defect in flow.py: report it to the human and stop. '
            f'Once it is fixed, the run goes on with: {self.cmd} {self.run_dir}',
            err,
        )
        self.assertEqual(
            self.state()['sections'],
            [dict(self.state()['sections'][0], status='done', reply={'status': 'done', 'n': 1})],
        )
        self.assertIn('- first: {"status": "done", "n": 1}\n', self.progress())
        limit[0] = 3
        self.assertEqual(
            self.first_line(self.call(rb)), 'launch step `second` as a new subagent, executor `main`: Main model'
        )

    def test_a_refused_transition_keeps_the_reply_and_the_run_resumes(self) -> None:
        def declare(rb: Runbook) -> None:
            linear(rb)
            rb.steps['first'].next = lambda r, s: 'missing'

        rb = self.runbook(declare)
        self.start(rb)
        err = self.fails(rb, 'reply', 'first', '{"status": "done"}')
        self.assertEqual(err, "flow.py: step 'missing' is not declared\n")
        self.assertEqual(
            self.state()['sections'],
            [dict(self.state()['sections'][0], status='done', reply={'status': 'done'})],
        )
        self.assertIn('- first: {"status": "done"}\n', self.progress())
        rb.steps['first'].next = 'second'
        self.assertEqual(
            self.first_line(self.call(rb)), 'launch step `second` as a new subagent, executor `main`: Main model'
        )

    def test_a_raising_start_leaves_a_run_to_resume(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('first')
            rb.step('first', executor=lambda s: names[s.inputs.kind], prompt='prompts/a.md', next=end('ready'))

        names: dict[str, str] = {}
        rb = self.runbook(declare, kind='big')
        self.assertIn(
            'flow.py: `executor` of step `first` raised KeyError: ', self.fails(rb, 'start', '{"repo": "/r"}')
        )
        self.assertEqual(self.state()['sections'], [])
        names['big'] = 'main'
        self.assertEqual(
            self.first_line(self.call(rb)), 'launch step `first` as a new subagent, executor `main`: Main model'
        )

    def test_blocked_without_on_failure_fails_with_reason(self) -> None:
        rb = self.runbook(linear)
        self.start(rb)
        out = self.call(rb, 'reply', 'first', '{"status": "blocked", "reason": "brief contradicts itself"}')
        self.assertIn('end: failed (step `first` blocked: brief contradicts itself)', out)
        self.assertEqual(self.state()['status'], 'failed')

    def test_on_failure_routes(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('try')
            rb.step('try', executor='light', prompt='prompts/a.md', next=end('ready'), on_failure='recover')
            rb.step('recover', executor='main', prompt='prompts/b.md', next=end('recovered'))

        rb = self.runbook(declare)
        self.start(rb)
        out = self.call(rb, 'reply', 'try', '{"status": "failed", "reason": "x"}')
        self.assertEqual(self.first_line(out), 'launch step `recover` as a new subagent, executor `main`: Main model')

    def test_executor_function_and_inputs_in_message(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('go')
            rb.step(
                'go',
                executor=lambda s: 'main' if s.inputs.coder == 'big' else 'light',
                prompt='prompts/a.md',
                inputs=['coder', ('file', 'out.md'), 'missing'],
                next=end('ready'),
            )

        rb = self.runbook(declare, coder='big')
        out = self.start(rb)
        self.assertEqual(self.first_line(out), 'launch step `go` as a new subagent, executor `main`: Main model')
        self.assertIn('coder: big\nfile: out.md\nmissing: <not among the inputs>\n', out)


class ParallelTest(RunbookTestCase):
    def declare(self, rb: Runbook) -> None:
        rb.start('fan')
        rb.step('fan', executor='light', prompt='prompts/a.md', next=parallel('left', 'right'))
        rb.step('left', executor='main', prompt='prompts/b.md', next='join')
        rb.step('right', executor='light', prompt='prompts/c.md', next='join', on_failure='join')
        rb.step('join', executor='main', prompt='prompts/d.md', after=('left', 'right'), next=end('ready'))

    def setUp(self) -> None:
        super().setUp()
        self.rb = self.runbook(self.declare)
        self.start(self.rb)

    def launches(self, out: str) -> list[str]:
        return [line for line in out.splitlines() if line.startswith('launch ')]

    def test_join_waits_for_both(self) -> None:
        out = self.reply(self.rb, 'fan')
        self.assertEqual(
            self.launches(out),
            [
                'launch step `left` as a new subagent, executor `main`: Main model',
                'launch step `right` as a new subagent, executor `light`: Light model',
            ],
        )
        self.assertEqual(self.reply(self.rb, 'left').strip(), 'still running: `right`')
        self.assertEqual(
            self.launches(self.reply(self.rb, 'right')),
            ['launch step `join` as a new subagent, executor `main`: Main model'],
        )

    def test_failed_branch_before_join_ends_failed(self) -> None:
        self.reply(self.rb, 'fan')
        self.reply(self.rb, 'left')
        out = self.call(self.rb, 'reply', 'right', '{"status": "failed", "reason": "x"}')
        self.assertIn('end: failed (step `right` failed before the join at `join`)', out)

    def test_launches_are_announced_together_and_set_apart(self) -> None:
        lines = self.reply(self.rb, 'fan').splitlines()
        self.assertEqual(
            lines[:3],
            [
                '2 steps to launch together, in one turn:',
                '',
                'launch step `left` as a new subagent, executor `main`: Main model',
            ],
        )
        second = lines.index('launch step `right` as a new subagent, executor `light`: Light model')
        self.assertEqual(lines[second - 1], '')
        self.assertTrue(lines[second - 2].startswith('when it finishes: '))

    def test_join_waits_for_both_in_a_second_round(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('fan')
            rb.step('fan', executor='light', prompt='prompts/a.md', next=parallel('left', 'right'))
            rb.step('left', executor='main', prompt='prompts/b.md', writes=['left.md'], next='join')
            rb.step('right', executor='light', prompt='prompts/c.md', writes=['right.md'], next='join')
            rb.step(
                'join',
                executor='main',
                prompt='prompts/d.md',
                after=('left', 'right'),
                reads=['left.md', 'right.md'],
                reply={'again': bool},
                next=lambda r, s: 'fan' if r.again else end('ready'),
            )

        self.run_dir += '-rounds'
        rb = self.runbook(declare)
        self.start(rb)
        self.reply(rb, 'fan')
        self.reply(rb, 'left')
        self.reply(rb, 'right')
        self.reply(rb, 'join', again=True)
        self.reply(rb, 'fan-2')
        self.assertEqual(self.reply(rb, 'left-2').strip(), 'still running: `right-2`')
        out = self.reply(rb, 'right-2')
        self.assertEqual(self.launches(out), ['launch step `join-2` as a new subagent, executor `main`: Main model'])
        self.assertIn(f'read left.md: {self.run_dir}/05-left.md\nread right.md: {self.run_dir}/06-right.md\n', out)
        self.assertEqual(
            [s['id'] for s in self.state()['sections']],
            ['fan', 'left', 'right', 'join', 'fan-2', 'left-2', 'right-2', 'join-2'],
        )

    def test_join_waits_for_a_branch_of_several_steps_in_a_second_round(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('fan')
            rb.step('fan', executor='light', prompt='prompts/a.md', next=parallel('left', 'prep'))
            rb.step('left', executor='main', prompt='prompts/b.md', next='join')
            rb.step('prep', executor='light', prompt='prompts/c.md', next='right')
            rb.step('right', executor='light', prompt='prompts/c.md', writes=['right.md'], next='join')
            rb.step(
                'join', executor='main', prompt='prompts/d.md', after=('left', 'right'), reads=['right.md'], next='fan'
            )

        self.run_dir += '-deep'
        rb = self.runbook(declare)
        self.start(rb)
        for sid in ('fan', 'left', 'prep', 'right', 'join', 'fan-2'):
            self.reply(rb, sid)
        self.assertEqual(self.reply(rb, 'left-2').strip(), 'still running: `prep-2`')
        self.reply(rb, 'prep-2')
        out = self.reply(rb, 'right-2')
        self.assertEqual(self.launches(out), ['launch step `join-2` as a new subagent, executor `main`: Main model'])
        self.assertIn(f'read right.md: {self.run_dir}/08-right.md\n', out)

    def test_reply_of_the_round_before_stays_visible_in_a_second_round(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('fan')
            rb.step('fan', executor='light', prompt='prompts/a.md', next=parallel('left', 'right'))
            rb.step(
                'left',
                executor='main',
                prompt='prompts/b.md',
                reply={'findings': int},
                next='join',
                skip=lambda s: 'join' if s.done('left') and s.reply('left').findings == 0 else None,  # pyright: ignore[reportOptionalMemberAccess]
            )
            rb.step('right', executor='light', prompt='prompts/c.md', next='join')
            rb.step('join', executor='main', prompt='prompts/d.md', after=('left', 'right'), next='fan')

        self.run_dir += '-visible'
        rb = self.runbook(declare)
        self.start(rb)
        self.reply(rb, 'fan')
        self.reply(rb, 'left', findings=1)
        self.reply(rb, 'right')
        self.reply(rb, 'join')
        self.assertEqual(len(self.launches(self.reply(rb, 'fan-2'))), 2)

    def test_join_takes_the_section_it_has_when_only_one_branch_runs_again(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('fan')
            rb.step('fan', executor='light', prompt='prompts/a.md', next=parallel('left', 'right'))
            rb.step('left', executor='main', prompt='prompts/b.md', next='join')
            rb.step('right', executor='light', prompt='prompts/c.md', next='join')
            rb.step(
                'join',
                executor='main',
                prompt='prompts/d.md',
                after=('left', 'right'),
                reply={'again': bool},
                next=lambda r, s: 'left' if r.again else end('ready'),
            )

        self.run_dir += '-one'
        rb = self.runbook(declare)
        self.start(rb)
        for sid in ('fan', 'left', 'right'):
            self.reply(rb, sid)
        self.reply(rb, 'join', again=True)
        out = self.reply(rb, 'left-2')
        self.assertEqual(self.launches(out), ['launch step `join-2` as a new subagent, executor `main`: Main model'])
        self.assertTrue(self.first_line(self.reply(rb, 'join-2', again=False)).startswith('end: ready'))

    def test_join_with_a_step_that_never_runs_again_still_waits_for_each_round(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('setup')
            rb.step('setup', executor='light', prompt='prompts/a.md', next='fan')
            rb.step('fan', executor='light', prompt='prompts/a.md', next=parallel('left', 'right'))
            rb.step('left', executor='main', prompt='prompts/b.md', next='join')
            rb.step('right', executor='light', prompt='prompts/c.md', writes=['right.md'], next='join')
            rb.step(
                'join',
                executor='main',
                prompt='prompts/d.md',
                after=('setup', 'left', 'right'),
                reads=['right.md'],
                next='fan',
            )

        self.run_dir += '-setup'
        rb = self.runbook(declare)
        self.start(rb)
        for sid in ('setup', 'fan', 'left', 'right', 'join', 'fan-2', 'left-2', 'right-2', 'join-2', 'fan-3'):
            self.reply(rb, sid)
        self.assertEqual(self.reply(rb, 'left-3').strip(), 'still running: `right-3`')
        out = self.reply(rb, 'right-3')
        self.assertEqual(self.launches(out), ['launch step `join-3` as a new subagent, executor `main`: Main model'])
        self.assertIn(f'read right.md: {self.run_dir}/11-right.md\n', out)

    def test_full_round_after_a_round_of_one_branch(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('fan')
            rb.step('fan', executor='light', prompt='prompts/a.md', next=parallel('left', 'right'))
            rb.step('left', executor='main', prompt='prompts/b.md', next='join')
            rb.step('right', executor='light', prompt='prompts/c.md', next='join')
            rb.step(
                'join',
                executor='main',
                prompt='prompts/d.md',
                after=('left', 'right'),
                next=lambda r, s: 'left' if s.done('join') == 1 else 'fan',
            )

        self.run_dir += '-mixed'
        rb = self.runbook(declare)
        self.start(rb)
        for sid in ('fan', 'left', 'right', 'join', 'left-2', 'join-2', 'fan-2'):
            self.reply(rb, sid)
        self.assertEqual(self.reply(rb, 'left-3').strip(), 'still running: `right-2`')
        self.assertEqual(
            self.launches(self.reply(rb, 'right-2')),
            ['launch step `join-3` as a new subagent, executor `main`: Main model'],
        )

    def test_end_waits_for_running_branch(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('fan')
            rb.step('fan', executor='light', prompt='prompts/a.md', next=parallel('left', 'right'))
            rb.step('left', executor='main', prompt='prompts/b.md', next=end('ready'))
            rb.step('right', executor='light', prompt='prompts/c.md', next=end('ready'))

        self.run_dir += '-2'
        rb = self.runbook(declare)
        self.start(rb)
        self.reply(rb, 'fan')
        out = self.reject(rb, 'right', 'garbage')
        self.assertEqual(out.strip(), 'wait: `left`. The run ends failed once they are recorded.')
        self.assertEqual(self.state()['status'], 'running')

    def test_end_waits_for_running_branch_visited_later(self) -> None:
        self.reply(self.rb, 'fan')
        out = self.call(self.rb, 'reply', 'left', '{"status": "failed", "reason": "x"}')
        self.assertEqual(out.strip(), 'wait: `right`. The run ends failed once they are recorded.')
        out = self.reply(self.rb, 'right')
        self.assertIn(
            f'end: failed (step `left` failed: x). The run is over. Report to the human: status failed, '
            f'run directory {self.run_dir}, read {self.run_dir}/progress.md.',
            out,
        )


class RunFilesTest(RunbookTestCase):
    """Each launch writes <run>/<NN>-<name>; readers get the latest done file of a name, or absent."""

    def declare(self, rb: Runbook) -> None:
        rb.start('check')
        rb.step(
            'check',
            executor='light',
            prompt='prompts/a.md',
            reads=['brief.md', 'working tree'],
            writes=['check.md'],
            reply={'passed': bool},
            next=lambda r, s: end('ready', 'read <run>/check.md') if r.passed else 'fix',
        )
        rb.step(
            'fix', executor='main', prompt='prompts/b.md', reads=['check.md', 'fix.md'], writes=['fix.md'], next='check'
        )

    def setUp(self) -> None:
        super().setUp()
        self.rb = self.runbook(self.declare)

    def message(self, out: str) -> list[str]:
        lines = out.splitlines()
        return lines[lines.index('--- message ---') + 1 : lines.index('--- end of message ---')]

    def test_outputs_are_numbered_by_launch_and_readers_get_the_latest(self) -> None:
        run = self.run_dir
        out = self.start(self.rb)
        self.assertEqual(
            self.message(out)[3:],
            [
                f'write check.md: {run}/00-check.md',
                f'read brief.md: {run}/brief.md',
                f'reply schema: {run}/schemas/check.json',
            ],
        )
        out = self.reply(self.rb, 'check', passed=False)
        self.assertEqual(
            self.message(out)[3:],
            [
                f'write fix.md: {run}/01-fix.md',
                f'read check.md: {run}/00-check.md',
                'read fix.md: absent, no earlier step wrote it',
                f'reply schema: {run}/schemas/fix.json',
            ],
        )
        out = self.reply(self.rb, 'fix')
        self.assertIn(f'write check.md: {run}/02-check.md', self.message(out))
        out = self.reply(self.rb, 'check-2', passed=False)
        self.assertEqual(
            self.message(out)[3:],
            [
                f'write fix.md: {run}/03-fix.md',
                f'read check.md: {run}/02-check.md',
                f'read fix.md: {run}/01-fix.md',
                f'reply schema: {run}/schemas/fix.json',
            ],
        )
        self.reply(self.rb, 'fix-2')
        out = self.reply(self.rb, 'check-3', passed=True)
        self.assertIn(f'read {run}/04-check.md.', out)


class HumanTest(RunbookTestCase):
    def declare(self, rb: Runbook) -> None:
        rb.start('ask')
        rb.human(
            'ask',
            choices=['Continue', 'stop'],
            writes='answer.md',
            question='Read `<run>/x.md`. Continue or stop?',
            next=lambda choice, s: 'go' if choice == 'Continue' else end('stopped', 'read <run>/x.md'),
        )
        rb.step('go', executor='main', prompt='prompts/a.md', next=end('ready'))

    def setUp(self) -> None:
        super().setUp()
        self.rb = self.runbook(self.declare)
        self.out = self.start(self.rb)

    def test_prints_question_and_choices(self) -> None:
        self.assertEqual(
            self.out.splitlines(),
            [
                f'ask the human (`ask`): Read `{self.run_dir}/x.md`. Continue or stop?',
                '  choices: Continue | stop. Map the answer to one of them; ask again if none fits.',
                f"  then: {self.cmd} {self.run_dir} answer ask '<the choice>' '<their words verbatim, or - to read them from stdin>'",
            ],
        )
        self.assertEqual(self.state()['status'], 'waiting_for_human')

    def test_status_repeats_the_wait(self) -> None:
        before = self.state()
        out = self.call(self.rb)
        self.assertEqual(
            out.strip(),
            f'waiting for the human on `ask`. Choices: Continue | stop. '
            f"When they answer: {self.cmd} {self.run_dir} answer ask '<the choice>' '<their words verbatim, or - to read them from stdin>'.",
        )
        self.assertEqual(self.state(), before)

    def test_answer_ignores_case(self) -> None:
        out = self.call(self.rb, 'answer', 'ask', ' CONTINUE ')
        self.assertEqual(self.first_line(out), 'launch step `go` as a new subagent, executor `main`: Main model')
        self.assertEqual(self.state()['sections'][0]['note'], 'Continue')
        self.assertIn('- ask: answered: Continue\n', self.progress())

    def test_answer_refuses_unknown_choice(self) -> None:
        self.assertIn('answer must be one of: Continue | stop', self.fails(self.rb, 'answer', 'ask', 'maybe'))
        self.assertEqual(self.state()['sections'][0]['status'], 'waiting_for_human')

    def test_verbatim_words_are_kept_and_written(self) -> None:
        self.call(self.rb, 'answer', 'ask', 'continue', 'continue, but only touch a3')
        section = self.state()['sections'][0]
        self.assertEqual((section['note'], section['answer']), ('Continue', 'continue, but only touch a3'))
        with open(os.path.join(self.run_dir, '00-answer.md'), encoding='utf-8') as f:
            self.assertEqual(f.read(), 'continue, but only touch a3\n')
        self.assertIn('- ask: said: continue, but only touch a3\n', self.progress())

    def test_free_text_step_takes_any_words(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('ask')
            rb.human(
                'ask',
                question='What next?',
                writes='words.md',
                next=lambda words, s: 'go' if 'go' in words else end('stopped'),
            )
            rb.step('go', executor='main', prompt='prompts/a.md', next=end('done'))

        rb = self.runbook(declare)
        self.run_dir = self.run_dir + '-free'
        out = self.start(rb)
        self.assertIn('  free text: pass their words as they are.', out)
        self.assertIn("answer ask '<their words verbatim, or - to read them from stdin>'", out)
        out = self.call(rb, 'answer', 'ask', 'please go on, carefully')
        self.assertEqual(self.first_line(out), 'launch step `go` as a new subagent, executor `main`: Main model')
        with open(os.path.join(self.run_dir, '00-words.md'), encoding='utf-8') as f:
            self.assertEqual(f.read(), 'please go on, carefully\n')

    def test_reply_fields_are_taken_as_json_and_kept_for_replies(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('fix')
            rb.step(
                'fix',
                executor='main',
                prompt='prompts/a.md',
                reply={'left': int},
                next=lambda r, s: (
                    end('ready')
                    if r.left == 0
                    else ('fix' if s.done('fix') < 1 + sum(a.rounds for a in s.replies('ask')) else 'ask')
                ),
            )
            rb.human(
                'ask',
                choices=['more', 'stop'],
                question='More rounds?',
                reply={
                    'rounds': {'type': 'integer', 'minimum': 1, 'default': 1, 'description': 'how many more rounds'}
                },
                next=lambda a, s: 'fix' if a.choice == 'more' else end('stopped'),
            )

        rb = self.runbook(declare)
        self.run_dir = self.run_dir + '-fields'
        self.start(rb)
        out = self.reply(rb, 'fix', left=2)
        answer = (
            f'{self.cmd} {self.run_dir} answer ask \'{{"choice": "<the choice>", '
            f"\"rounds\": <value, if given>}}' '<their words verbatim, or - to read them from stdin>'"
        )
        self.assertEqual(
            out.splitlines(),
            [
                'ask the human (`ask`): More rounds?',
                '  choices: more | stop. Map the answer to one of them; ask again if none fits.',
                '  fields: rounds (integer): how many more rounds. Take them from their words; '
                'leave out a field they did not give.',
                f'  then: {answer}',
            ],
        )
        self.assertEqual(
            self.call(rb).strip(),
            'waiting for the human on `ask`. Choices: more | stop. '
            f'Fields: rounds (integer): how many more rounds. When they answer: {answer}.',
        )
        self.assertIn('this step takes one JSON object', self.fails(rb, 'answer', 'ask', 'more'))
        self.assertIn(
            'field \'rounds\' must be integer, got "two"',
            self.fails(rb, 'answer', 'ask', '{"choice": "more", "rounds": "two"}'),
        )
        self.assertIn(
            "field 'round' is not declared", self.fails(rb, 'answer', 'ask', '{"choice": "more", "round": 2}')
        )
        self.assertIn('answer must be one of: more | stop', self.fails(rb, 'answer', 'ask', '{"choice": "maybe"}'))
        self.assertIn('only one argument can be `-`', self.fails(rb, 'answer', 'ask', '-', '-'))
        self.assertEqual(self.state()['sections'][1]['status'], 'waiting_for_human')
        out = self.call(rb, 'answer', 'ask', '{"choice": "More", "rounds": 2}', 'two more, then leave me alone')
        self.assertEqual(self.first_line(out), 'launch step `fix-2` as a new subagent, executor `main`: Main model')
        section = self.state()['sections'][1]
        self.assertEqual(
            (section['note'], section['answer'], section['reply']),
            ('more', 'two more, then leave me alone', {'choice': 'more', 'rounds': 2}),
        )
        self.assertIn('- ask: answered: more {"rounds": 2}\n', self.progress())
        out = self.reply(rb, 'fix-2', left=1)
        self.assertEqual(self.first_line(out), 'launch step `fix-3` as a new subagent, executor `main`: Main model')
        self.assertEqual(self.first_line(self.reply(rb, 'fix-3', left=1)), 'ask the human (`ask-2`): More rounds?')
        out = self.call(rb, 'answer', 'ask-2', '{"choice": "more"}')
        self.assertEqual(self.state()['sections'][4]['reply'], {'choice': 'more', 'rounds': 1})
        self.assertEqual(self.first_line(out), 'launch step `fix-4` as a new subagent, executor `main`: Main model')
        self.assertEqual(self.first_line(self.reply(rb, 'fix-4', left=1)), 'ask the human (`ask-3`): More rounds?')


class ReplySchemaTest(RunbookTestCase):
    """Each launch leaves the JSON Schema of the step's reply in <run>/schemas for its executor."""

    def test_schema_is_written_from_the_declared_fields(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('review')
            rb.step(
                'review',
                executor='main',
                prompt='prompts/a.md',
                reply={'passed': bool, 'findings': {'type': 'integer', 'minimum': 0, 'description': 'open findings'}},
                next=end('ready'),
            )

        rb = self.runbook(declare)
        self.start(rb)
        with open(os.path.join(self.run_dir, 'schemas', 'review.json'), encoding='utf-8') as f:
            schema = json.load(f)
        self.assertEqual(
            schema,
            {
                'type': 'object',
                'description': runbook.SCHEMA_ABOUT,
                'properties': {
                    'status': {'type': 'string', 'enum': ['done', 'failed', 'blocked']},
                    'reason': {'type': ['string', 'null']},
                    'passed': {'anyOf': [{'type': 'boolean'}, {'type': 'null'}]},
                    'findings': {
                        'anyOf': [{'type': 'integer', 'minimum': 0, 'description': 'open findings'}, {'type': 'null'}]
                    },
                },
                'required': ['status', 'reason', 'passed', 'findings'],
                'additionalProperties': False,
            },
        )
        out = self.reply(rb, 'review', passed=True, findings='none')
        self.assertIn('did not pass the check (field \'findings\' must be integer, got "none")', out)

    def test_nulls_of_the_schema_are_not_recorded(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('review')
            rb.step(
                'review',
                executor='main',
                prompt='prompts/a.md',
                reply={'findings': int},
                next='review',
                on_failure='review',
            )

        rb = self.runbook(declare)
        self.start(rb)
        self.call(rb, 'reply', 'review', '{"status": "done", "reason": null, "findings": 2}')
        self.call(rb, 'reply', 'review-2', '{"status": "blocked", "reason": "no repo", "findings": null}')
        self.reject(rb, 'review-3', '{"status": "done", "reason": null, "findings": null}')
        self.call(rb, 'reply', 'review-4', '{"status": "failed", "reason": null, "findings": null}')
        self.assertEqual(
            [s['reply'] for s in self.state()['sections'][:4]],
            [
                {'status': 'done', 'findings': 2},
                {'status': 'blocked', 'reason': 'no repo'},
                {'status': 'failed', 'reason': "invalid reply: field 'findings' must be integer, got null"},
                {'status': 'failed', 'reason': 'no reason given'},
            ],
        )

    def test_field_schemas_without_a_type_and_with_several(self) -> None:
        self.assertEqual(
            runbook.reply_schema(
                {
                    'verdict': {'enum': ['ok', 'no']},
                    'n': {'type': ['integer', 'null']},
                    'kind': {'type': 'string', 'enum': ['a']},
                    'items': list,
                }
            )['properties'],
            {
                'status': {'type': 'string', 'enum': ['done', 'failed', 'blocked']},
                'reason': {'type': ['string', 'null']},
                'verdict': {'anyOf': [{'enum': ['ok', 'no']}, {'type': 'null'}]},
                'n': {'anyOf': [{'type': ['integer', 'null']}, {'type': 'null'}]},
                'kind': {'anyOf': [{'type': 'string', 'enum': ['a']}, {'type': 'null'}]},
                'items': {'anyOf': [{'type': 'array'}, {'type': 'null'}]},
            },
        )

        def declare(rb: Runbook) -> None:
            rb.start('count')
            rb.step(
                'count',
                executor='main',
                prompt='prompts/a.md',
                reply={'n': {'type': ['integer', 'null']}, 'items': list},
                next='count',
            )

        rb = self.runbook(declare)
        self.assertEqual(self.check(rb)[0], 0)
        self.start(rb)
        self.reply(rb, 'count', n=None, items=[1])
        out = self.reply(rb, 'count-2', n='two', items=[])
        self.assertIn('did not pass the check (field \'n\' must be integer or null, got "two")', out)
        self.assertEqual(self.state()['sections'][0]['reply'], {'status': 'done', 'n': None, 'items': [1]})

    def test_only_the_type_of_a_field_is_checked(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('review')
            rb.step(
                'review',
                executor='main',
                prompt='prompts/a.md',
                reply={'findings': {'type': 'integer', 'minimum': 0}, 'verdict': {'enum': ['ok', 'not ok']}},
                next=end('ready'),
            )

        rb = self.runbook(declare)
        self.start(rb)
        self.assertTrue(
            self.first_line(self.reply(rb, 'review', findings=-1, verdict='so-so')).startswith('end: ready')
        )

    def test_check_takes_property_names_and_literals_for_what_they_are(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('review')
            rb.step(
                'review',
                executor='main',
                prompt='prompts/a.md',
                next=end('ready'),
                reply={
                    'named': {
                        'type': 'object',
                        'properties': {'$ref': {'type': 'string'}, '$defs': {'type': 'string'}},
                    },
                    'literal': {'const': {'$ref': 'x'}, 'enum': [{'$defs': 1}]},
                },
            )

        self.assertEqual(self.check(self.runbook(declare))[0], 0)

    def test_check_rejects_bad_declarations(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('review')
            rb.step(
                'review',
                executor='main',
                prompt='prompts/a.md',
                reply={
                    'status': str,
                    'findings': 'int',
                    'kind': {'type': 'text'},
                    'odd': {'type': ['string', {}]},
                    'twice': {'type': ['string', 'string']},
                    'none': {'type': []},
                    'ref': {'type': 'array', 'items': {'anyOf': [{'properties': {'v': {'$ref': '#/x'}}}]}},
                },
                next='ask',
            )
            rb.human('ask', question='Go on?', reply={'choice': str}, next=lambda a, s: end('ready'))

        code, out = self.check(self.runbook(declare))
        self.assertEqual(code, 1)
        self.assertEqual(
            out.splitlines(),
            [
                "step review: reply field 'status' is set by the engine",
                "step review: reply field 'findings' is 'int', neither a JSON type (bool, int, float, str, list, dict) "
                'nor a JSON Schema',
                "step review: reply field 'kind' has type 'text', not a JSON type name or a list of different ones",
                "step review: reply field 'odd' has type ['string', {}], not a JSON type name or a list of different ones",
                "step review: reply field 'twice' has type ['string', 'string'], not a JSON type name or a list of "
                'different ones',
                "step review: reply field 'none' has type [], not a JSON type name or a list of different ones",
                "step review: reply field 'ref' uses $ref or $defs; a field's schema stands alone",
                "step ask: reply field 'choice' is set by the engine",
            ],
        )


class LoopTest(RunbookTestCase):
    def test_loop_budget_and_counter_suffixes(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('fix')
            rb.step('fix', executor='main', prompt='prompts/a.md', next='verify')
            rb.step(
                'verify',
                executor='light',
                prompt='prompts/b.md',
                reply={'ok': bool},
                next=lambda r, s: (
                    end('ready')
                    if r.ok
                    else (
                        'fix' if s.done('verify') < s.inputs.rounds else end('needs_attention', 'read <run>/verify.md')
                    )
                ),
            )

        rb = self.runbook(declare, rounds=3)
        self.start(rb)
        self.assertEqual(
            self.first_line(self.reply(rb, 'fix')),
            'launch step `verify` as a new subagent, executor `light`: Light model',
        )
        self.assertEqual(
            self.first_line(self.reply(rb, 'verify', ok=False)),
            'launch step `fix-2` as a new subagent, executor `main`: Main model',
        )
        self.assertEqual(
            self.first_line(self.reply(rb, 'fix-2')),
            'launch step `verify-2` as a new subagent, executor `light`: Light model',
        )
        self.assertEqual(
            self.first_line(self.reply(rb, 'verify-2', ok=False)),
            'launch step `fix-3` as a new subagent, executor `main`: Main model',
        )
        self.reply(rb, 'fix-3')
        out = self.reply(rb, 'verify-3', ok=False)
        self.assertIn('end: needs_attention (after step `verify-3`)', out)
        self.assertEqual(
            [s['id'] for s in self.state()['sections']], ['fix', 'verify', 'fix-2', 'verify-2', 'fix-3', 'verify-3']
        )

    def test_a_loop_of_thousands_of_sections_replays_to_its_end(self) -> None:
        rounds = 5000

        def declare(rb: Runbook) -> None:
            rb.start('work')
            rb.step(
                'work',
                executor='main',
                prompt='prompts/a.md',
                next=lambda r, s: 'work' if s.done('work') < rounds else end('ready'),
            )

        rb = self.runbook(declare)
        self.start(rb)
        state = self.state()
        first = state['sections'][0]
        done = {**first, 'status': 'done', 'reply': {'status': 'done'}}
        state['sections'] = (
            [{**done, 'id': 'work'}]
            + [{**done, 'id': f'work-{i}'} for i in range(2, rounds)]
            + [{**first, 'id': f'work-{rounds}'}]
        )
        with open(os.path.join(self.run_dir, 'state.json'), 'w') as f:
            json.dump(state, f)
        self.assertEqual(self.first_line(self.call(rb)), f'still running: `work-{rounds}`')
        self.assertIn(f'end: ready (after step `work-{rounds}`)', self.reply(rb, f'work-{rounds}'))


class RelaunchTest(RunbookTestCase):
    def test_supersede_refuses_invalid_statuses_without_writing(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('publish')
            rb.step('publish', executor='main', prompt='prompts/a.md', side_effects='publish', next=end('ready'))

        rb = self.runbook(declare)
        self.start(rb)
        state = runbook.RunState.load(self.run_dir)
        section = state.sections[0]
        cases = [('interrupted', status, None) for status in runbook.Status if status is not runbook.Status.RUNNING]
        cases += [
            ('relaunch', status, None)
            for status in (runbook.Status.RUNNING, runbook.Status.WAITING_FOR_HUMAN, runbook.Status.DONE)
        ]
        cases += [
            ('relaunch', status, note)
            for status in (runbook.Status.FAILED, runbook.Status.BLOCKED)
            for note in (runbook.NOTE_INTERRUPTED, runbook.NOTE_RELAUNCHED)
        ]
        for command, status, note in cases:
            with self.subTest(command=command, status=status, note=note):
                section.status, section.note = status, note
                state.save(self.run_dir)
                with open(runbook.RunState.path(self.run_dir), 'rb') as f:
                    before = f.read()
                progress = self.progress()
                err = self.fails(rb, command, 'publish')
                self.assertIn(f'section publish is {status.value}', err)
                self.assertIn('accepts only', err)
                with open(runbook.RunState.path(self.run_dir), 'rb') as f:
                    self.assertEqual(f.read(), before)
                self.assertEqual(self.progress(), progress)

    def test_relaunch_refuses_steps_without_side_effects_and_human_steps(self) -> None:
        rb = self.runbook(linear)
        rb.human('ask', question='Proceed?', next=end('ready'))
        self.start(rb)
        state = runbook.RunState.load(self.run_dir)
        for name in ('first', 'ask'):
            for status in (runbook.Status.FAILED, runbook.Status.BLOCKED):
                with self.subTest(name=name, status=status):
                    state.sections[0] = runbook.Section(id=name, name=name, status=status)
                    state.save(self.run_dir)
                    before, progress = self.state(), self.progress()
                    err = self.fails(rb, 'relaunch', name)
                    self.assertIn(f'section {name} is {status.value}', err)
                    self.assertIn('side_effects', err)
                    self.assertEqual(self.state(), before)
                    self.assertEqual(self.progress(), progress)

    def test_blocked_side_effect_section_can_be_relaunched(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('publish')
            rb.step('publish', executor='main', prompt='prompts/a.md', side_effects='publish', next=end('ready'))

        rb = self.runbook(declare)
        self.start(rb)
        self.call(rb, 'reply', 'publish', '{"status": "blocked", "reason": "offline"}')
        self.assertIn('launch step `publish-2`', self.call(rb, 'relaunch', 'publish'))
        self.assertEqual(self.state()['sections'][0]['note'], runbook.NOTE_RELAUNCHED)

    def test_running_side_effect_section_can_be_interrupted(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('publish')
            rb.step('publish', executor='main', prompt='prompts/a.md', side_effects='publish', next=end('ready'))

        rb = self.runbook(declare)
        self.start(rb)
        self.assertIn('launch step `publish-2`', self.call(rb, 'interrupted', 'publish'))
        self.assertEqual(self.state()['sections'][0]['note'], runbook.NOTE_INTERRUPTED)

    def test_interrupted_opens_a_new_section(self) -> None:
        rb = self.runbook(linear)
        self.start(rb)
        out = self.call(rb, 'interrupted', 'first')
        self.assertEqual(self.first_line(out), 'launch step `first-2` as a new subagent, executor `light`: Light model')
        self.assertIn('The tree may hold a partial earlier attempt.\n--- end of message ---', out)
        self.assertEqual(
            [(s['id'], s['status'], s['note']) for s in self.state()['sections']],
            [('first', 'failed', 'interrupted'), ('first-2', 'running', None)],
        )
        self.assertIn('- first: interrupted\n', self.progress())

    def test_side_effect_failure_asks_the_human(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('commit')
            rb.step('commit', executor='main', prompt='prompts/a.md', side_effects='commit', next=end('ready'))

        rb = self.runbook(declare)
        self.assertEqual(
            self.first_line(self.start(rb)),
            'launch step `commit` as a new subagent, executor `main`: Main model. Side effects: commit',
        )
        out = self.call(rb, 'reply', 'commit', '{"status": "failed", "reason": "hook rejected"}')
        self.assertEqual(
            out.strip(),
            (
                f'ask the human: step `commit` has side effects and ended failed (hook rejected). '
                f'On yes: {self.cmd} {self.run_dir} relaunch commit. '
                f"On no: {self.cmd} {self.run_dir} log '<their decision>' and stop."
            ),
        )
        out = self.call(rb, 'relaunch', 'commit')
        self.assertEqual(
            self.first_line(out),
            'launch step `commit-2` as a new subagent, executor `main`: Main model. Side effects: commit',
        )
        self.assertIn('The tree may hold a partial earlier attempt.', out)
        self.assertEqual(self.state()['sections'][0]['note'], "relaunched on the human's yes")


class CorrectionTest(RunbookTestCase):
    """A reply that does not pass the check goes back to its executor once before the step is failed."""

    def declare(self, rb: Runbook) -> None:
        rb.start('checks')
        rb.step(
            'checks',
            executor='light',
            prompt='prompts/a.md',
            reply={'passed': bool},
            next=lambda r, s: end('ready') if r.passed else end('red'),
            on_failure=end('broken'),
        )

    def test_asks_for_a_correction_and_takes_the_corrected_reply(self) -> None:
        rb = self.runbook(self.declare)
        self.start(rb)
        out = self.call(rb, 'reply', 'checks', '{"status": "done", "passed": "yes"}')
        problem = 'field \'passed\' must be boolean, got "yes"'
        self.assertEqual(
            out.splitlines(),
            [
                f'the reply of step `checks` did not pass the check ({problem}). Send this to the subagent that ran it, '
                f'as a follow-up message in its session, not as a new launch:',
                '--- message ---',
                f'Your final message did not pass the check: {problem}. Do not redo the step and change nothing. '
                f'Reply with one JSON object that fits {self.run_dir}/schemas/checks.json for the work already done, '
                f'and nothing else. If the work is not done, reply failed with a one-line reason.',
                '--- end of message ---',
                f"when it answers: {self.cmd} {self.run_dir} reply checks '<the last JSON object of its message>'",
                f'if your tool cannot send a message to a subagent that has finished: {self.cmd} {self.run_dir} reply checks '
                f'\'{{"status": "failed", "reason": "invalid reply, and its executor could not be asked again"}}\'',
                '',
                'still running: `checks`',
            ],
        )
        section = self.state()['sections'][0]
        self.assertEqual((section['status'], section['reply'], section['ended_at']), ('running', None, None))
        self.assertEqual(
            section['invalid_replies'], [{'reply': '{"status": "done", "passed": "yes"}', 'problem': problem}]
        )
        self.assertEqual(self.call(rb).strip(), 'still running: `checks`')
        out = self.reply(rb, 'checks', passed=True)
        self.assertTrue(out.startswith('end: ready (after step `checks`)'))
        self.assertEqual(self.state()['sections'][0]['reply'], {'status': 'done', 'passed': True})
        self.assertIn(
            f'- checks: invalid reply ({problem}): {{"status": "done", "passed": "yes"}}\n'
            f'- checks: its executor is asked to correct the reply\n'
            f'- checks: {{"status": "done", "passed": true}}\n',
            self.progress(),
        )

    def test_a_second_invalid_reply_fails_the_step_and_keeps_both(self) -> None:
        rb = self.runbook(self.declare)
        self.start(rb)
        out = self.reject(rb, 'checks', '{"status": "done"}')
        self.assertTrue(out.startswith('end: broken (after step `checks`)'))
        section = self.state()['sections'][0]
        self.assertEqual(section['reply'], {'status': 'failed', 'reason': "invalid reply: no field 'passed'"})
        self.assertEqual([r['reply'] for r in section['invalid_replies']], ['{"status": "done"}'] * 2)

    def test_executor_that_cannot_be_asked_again(self) -> None:
        rb = self.runbook(self.declare)
        self.start(rb)
        self.call(rb, 'reply', 'checks', '{}')
        self.call(
            rb,
            'reply',
            'checks',
            '{"status": "failed", "reason": "invalid reply, and its executor could not be asked again"}',
        )
        self.assertEqual(
            self.state()['sections'][0]['reply']['reason'], 'invalid reply, and its executor could not be asked again'
        )

    def test_the_correction_is_printed_once_while_a_parallel_branch_runs(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('fan')
            rb.step('fan', executor='light', prompt='prompts/a.md', next=parallel('left', 'right'))
            rb.step('left', executor='main', prompt='prompts/b.md', reply={'n': int}, next='join')
            rb.step('right', executor='main', prompt='prompts/c.md', next='join')
            rb.step('join', executor='main', prompt='prompts/d.md', after=('left', 'right'), next=end('ready'))

        rb = self.runbook(declare)
        self.start(rb)
        self.reply(rb, 'fan')
        self.assertIn('the reply of step `left` did not pass the check', self.reply(rb, 'left'))
        self.assertEqual(self.reply(rb, 'right').strip(), 'still running: `left`')
        self.assertEqual(
            self.first_line(self.reply(rb, 'left', n=1)),
            'launch step `join` as a new subagent, executor `main`: Main model',
        )

    def test_an_ending_waits_for_a_branch_under_correction_in_either_order(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('fan')
            rb.step('fan', executor='light', prompt='prompts/a.md', next=parallel('left', 'right'))
            rb.step('left', executor='main', prompt='prompts/b.md', next=end('ready'))
            rb.step('right', executor='main', prompt='prompts/c.md', reply={'n': int}, next=end('ready'))

        rb = self.runbook(declare)
        base = self.run_dir
        wait = 'wait: `right`. The run ends ready once they are recorded.'
        for i, ending_first in enumerate((False, True)):
            with self.subTest(ending_first=ending_first):
                self.run_dir = f'{base}-{i}'
                self.start(rb)
                self.reply(rb, 'fan')
                if ending_first:
                    self.assertEqual(self.reply(rb, 'left').strip(), wait)
                out = self.reply(rb, 'right')
                self.assertTrue(out.startswith('the reply of step `right` did not pass the check'))
                if not ending_first:
                    self.assertEqual(self.reply(rb, 'left').strip(), wait)
                else:
                    self.assertEqual(out.splitlines()[-1], wait)
                self.assertTrue(self.reply(rb, 'right', n=1).startswith('end: ready'))

    def test_a_side_effect_step_is_corrected_before_the_human_is_asked(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('commit')
            rb.step(
                'commit',
                executor='main',
                prompt='prompts/a.md',
                reply={'sha': str},
                side_effects='commit',
                next=end('ready'),
            )

        rb = self.runbook(declare)
        self.start(rb)
        self.assertIn('did not pass the check', self.first_line(self.reply(rb, 'commit')))
        self.assertTrue(
            self.reply(rb, 'commit').startswith(
                "ask the human: step `commit` has side effects and ended failed (invalid reply: no field 'sha')."
            )
        )

    def test_the_budget_survives_a_resume_and_a_new_section_gets_its_own(self) -> None:
        rb = self.runbook(self.declare)
        self.start(rb)
        self.call(rb, 'reply', 'checks', 'garbage')
        fresh = self.runbook(self.declare)
        self.assertEqual(self.call(fresh).strip(), 'still running: `checks`')
        self.call(fresh, 'interrupted', 'checks')
        self.assertIn('did not pass the check', self.call(fresh, 'reply', 'checks-2', 'garbage'))


class FailedCountTest(RunbookTestCase):
    """s.failed(step) is the budget of a loop through on_failure, where no section is done."""

    def declare(self, rb: Runbook) -> None:
        rb.start('flaky')
        rb.step(
            'flaky',
            executor='light',
            prompt='prompts/a.md',
            next=end('ready'),
            on_failure=lambda r, s: 'flaky' if s.failed('flaky') < 2 else end('failed', 'read <run>/progress.md'),
        )

    def test_on_failure_loop_ends_on_its_budget(self) -> None:
        rb = self.runbook(self.declare)
        self.start(rb)
        out = self.call(rb, 'reply', 'flaky', '{"status": "failed", "reason": "network"}')
        self.assertEqual(self.first_line(out), 'launch step `flaky-2` as a new subagent, executor `light`: Light model')
        out = self.call(rb, 'reply', 'flaky-2', '{"status": "blocked", "reason": "network"}')
        self.assertTrue(out.startswith('end: failed (after step `flaky-2`)'))

    def test_interrupted_sections_are_not_counted(self) -> None:
        rb = self.runbook(self.declare)
        self.start(rb)
        self.call(rb, 'interrupted', 'flaky')
        out = self.call(rb, 'reply', 'flaky-2', '{"status": "failed", "reason": "network"}')
        self.assertEqual(self.first_line(out), 'launch step `flaky-3` as a new subagent, executor `light`: Light model')

    def test_the_executor_function_sees_it_and_a_relaunch_is_not_counted(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('push')
            rb.step(
                'push',
                executor=lambda s: 'light' if s.failed('push') else 'main',
                prompt='prompts/a.md',
                side_effects='push',
                next=end('ready'),
            )
            rb.step(
                'retry',
                executor=lambda s: 'light' if s.failed('retry') else 'main',
                prompt='prompts/b.md',
                next=end('ready'),
                on_failure='retry',
            )

        rb = self.runbook(declare)
        self.start(rb)
        self.call(rb, 'reply', 'push', '{"status": "failed", "reason": "x"}')
        self.assertIn('executor `main`', self.first_line(self.call(rb, 'relaunch', 'push')))
        self.run_dir += '-2'
        rb.start('retry')
        self.start(rb)
        out = self.call(rb, 'reply', 'retry', '{"status": "failed", "reason": "x"}')
        self.assertEqual(self.first_line(out), 'launch step `retry-2` as a new subagent, executor `light`: Light model')

    def test_counts_per_step_and_none_before_a_failure(self) -> None:
        seen: list[tuple[int, int]] = []

        def declare(rb: Runbook) -> None:
            rb.start('a')
            rb.step(
                'a',
                executor='light',
                prompt='prompts/a.md',
                next='b',
                on_failure=lambda r, s: seen.append((s.failed('a'), s.failed('b'))) or 'b',
            )
            rb.step(
                'b',
                executor='light',
                prompt='prompts/b.md',
                next=lambda r, s: seen.append((s.failed('a'), s.failed('b'))) or end('ready'),
            )

        rb = self.runbook(declare)
        self.start(rb)
        self.call(rb, 'reply', 'a', '{"status": "failed", "reason": "x"}')
        self.reply(rb, 'b')
        self.assertEqual(seen[-1], (1, 0))


class TimingTest(RunbookTestCase):
    """Each section records its executor, when it was opened and when it left an open status."""

    def fields(self) -> list[tuple[str, str | None, str | None, str | None]]:
        return [(s['id'], s['executor'], s['started_at'], s['ended_at']) for s in self.state()['sections']]

    def test_launch_and_reply(self) -> None:
        rb = self.runbook(linear)
        self.start(rb)
        self.assertEqual(self.fields(), [('first', 'light', '2026-10-03T10:00:00Z', None)])
        self.reply(rb, 'first')
        self.assertEqual(
            self.fields(),
            [
                ('first', 'light', '2026-10-03T10:00:00Z', '2026-10-03T10:01:00Z'),
                ('second', 'main', '2026-10-03T10:02:00Z', None),
            ],
        )

    def test_failed_reply_ends_the_section(self) -> None:
        rb = self.runbook(linear)
        self.start(rb)
        self.call(rb, 'reply', 'first', '{"status": "failed", "reason": "x"}')
        self.assertEqual(self.fields(), [('first', 'light', '2026-10-03T10:00:00Z', '2026-10-03T10:01:00Z')])

    def test_executor_function_is_recorded_by_its_resolved_name(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('go')
            rb.step(
                'go',
                executor=lambda s: 'main' if s.inputs.coder == 'big' else 'light',
                prompt='prompts/a.md',
                next=end('ready'),
            )

        rb = self.runbook(declare, coder='big')
        self.start(rb)
        self.assertEqual(self.state()['sections'][0]['executor'], 'main')

    def test_answer(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('ask')
            rb.human(
                'ask',
                choices=['Continue', 'stop'],
                question='Continue?',
                next=lambda choice, s: 'go' if choice == 'Continue' else end('stopped'),
            )
            rb.step('go', executor='main', prompt='prompts/a.md', next=end('ready'))

        rb = self.runbook(declare)
        self.start(rb)
        self.assertEqual(self.fields(), [('ask', None, '2026-10-03T10:00:00Z', None)])
        self.call(rb, 'answer', 'ask', 'Continue')
        self.assertEqual(
            self.fields(),
            [
                ('ask', None, '2026-10-03T10:00:00Z', '2026-10-03T10:01:00Z'),
                ('go', 'main', '2026-10-03T10:02:00Z', None),
            ],
        )

    def test_interrupted(self) -> None:
        rb = self.runbook(linear)
        self.start(rb)
        self.call(rb, 'interrupted', 'first')
        self.assertEqual(
            self.fields(),
            [
                ('first', 'light', '2026-10-03T10:00:00Z', '2026-10-03T10:01:00Z'),
                ('first-2', 'light', '2026-10-03T10:02:00Z', None),
            ],
        )

    def test_relaunch_keeps_the_end_of_the_failed_section(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('commit')
            rb.step('commit', executor='main', prompt='prompts/a.md', side_effects='commit', next=end('ready'))

        rb = self.runbook(declare)
        self.start(rb)
        self.call(rb, 'reply', 'commit', '{"status": "failed", "reason": "hook rejected"}')
        self.call(rb, 'relaunch', 'commit')
        self.assertEqual(
            self.fields(),
            [
                ('commit', 'main', '2026-10-03T10:00:00Z', '2026-10-03T10:01:00Z'),
                ('commit-2', 'main', '2026-10-03T10:02:00Z', None),
            ],
        )

    def test_state_without_the_fields_resumes(self) -> None:
        rb = self.runbook(linear)
        self.start(rb)
        with open(os.path.join(self.run_dir, 'state.json'), encoding='utf-8') as f:
            data = json.load(f)
        for section in data['sections']:
            for name in ('executor', 'started_at', 'ended_at', 'invalid_replies'):
                del section[name]
        with open(os.path.join(self.run_dir, 'state.json'), 'w', encoding='utf-8') as f:
            json.dump(data, f)
        self.assertEqual(self.call(rb).strip(), 'still running: `first`')
        self.assertEqual(
            self.first_line(self.reply(rb, 'first')),
            'launch step `second` as a new subagent, executor `main`: Main model',
        )
        self.assertEqual(
            self.fields(),
            [('first', None, None, '2026-10-03T10:01:00Z'), ('second', 'main', '2026-10-03T10:02:00Z', None)],
        )


class DeclarationTest(RunbookTestCase):
    def declare(self, rb: Runbook, kind: str, name: str, **kwargs: Any) -> None:
        if kind == 'step':
            rb.step(name, executor='main', prompt='prompts/a.md', next=end('ready'), **kwargs)
        else:
            rb.human(name, question='Proceed?', next=end('ready'), **kwargs)

    def test_collection_parameters_refuse_strings_at_declaration(self) -> None:
        for kind, parameters in (('step', ('inputs', 'reads', 'writes', 'after')), ('human', ('choices', 'after'))):
            for parameter in parameters:
                for value in ('file.md', ''):
                    with self.subTest(kind=kind, parameter=parameter, value=value):
                        rb = self.runbook(lambda rb: None)
                        with self.assertRaises(TypeError) as caught:
                            self.declare(rb, kind, 'work', **{parameter: value})
                        self.assertIn('work', str(caught.exception))
                        self.assertIn(parameter, str(caught.exception))
                        self.assertEqual(rb.steps, {})

    def test_duplicate_names_do_not_replace_the_first_declaration(self) -> None:
        for first in ('step', 'human'):
            for second in ('step', 'human'):
                with self.subTest(first=first, second=second):
                    rb = self.runbook(lambda rb: None)
                    self.declare(rb, first, 'work')
                    original = rb.steps['work']
                    with self.assertRaisesRegex(ValueError, 'work.*already declared'):
                        self.declare(rb, second, 'work')
                    self.assertIs(rb.steps['work'], original)

    def test_counter_names_are_refused_in_either_order(self) -> None:
        for first in ('step', 'human'):
            for second in ('step', 'human'):
                for counter in ('0', '1', '2', '02', '123'):
                    for names in (('work', f'work-{counter}'), (f'work-{counter}', 'work')):
                        with self.subTest(first=first, second=second, names=names):
                            rb = self.runbook(lambda rb: None)
                            self.declare(rb, first, names[0])
                            with self.assertRaises(ValueError) as caught:
                                self.declare(rb, second, names[1])
                            self.assertIn(names[0], str(caught.exception))
                            self.assertIn(names[1], str(caught.exception))
                            self.assertEqual(list(rb.steps), [names[0]])

    def test_other_suffixes_and_regex_characters_do_not_collide(self) -> None:
        rb = self.runbook(lambda rb: None)
        for name in ('work', 'work-2x', 'work-', 'w.rk', 'wxrk-2'):
            self.declare(rb, 'step', name)
        self.assertEqual(len(rb.steps), 5)

    def test_iterables_and_human_writes_remain_supported(self) -> None:
        rb = self.runbook(lambda rb: None)
        rb.step(
            'work',
            executor='main',
            prompt='prompts/a.md',
            next='ask',
            inputs=iter(['repo', ('rounds', 2)]),
            reads=('brief.md',),
            writes=iter(['out.md']),
            after=(),
        )
        rb.human(
            'ask',
            question='Proceed?',
            next=end('ready'),
            choices=iter(['yes', 'no']),
            writes='words.md',
            after=('work',),
        )
        rb.start('work')
        self.assertEqual(self.check(rb), (0, 'flow.py is consistent.\n'))
        step = rb.steps['work']
        self.assertIsInstance(step, runbook.Step)
        assert isinstance(step, runbook.Step)
        self.assertEqual(step.inputs, ['repo', ('rounds', 2)])
        self.assertEqual(step.writes, ['out.md'])


class StateSaveTest(RunbookTestCase):
    def test_serialization_error_keeps_the_previous_state_whole(self) -> None:
        rb = self.runbook(linear)
        self.start(rb)
        path = runbook.RunState.path(self.run_dir)
        with open(path, 'rb') as f:
            before = f.read()
        state = runbook.RunState.load(self.run_dir)
        state.inputs['bad'] = object()
        with self.assertRaises(TypeError):
            state.save(self.run_dir)
        with open(path, 'rb') as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(runbook.RunState.load(self.run_dir).inputs, {'repo': '/repo'})

    def test_save_overwrites_a_leftover_temporary_file(self) -> None:
        rb = self.runbook(linear)
        self.start(rb)
        path = runbook.RunState.path(self.run_dir)
        with open(path + '.tmp', 'w') as f:
            f.write('interrupted write')
        state = runbook.RunState.load(self.run_dir)
        state.inputs['repo'] = '/new'
        state.save(self.run_dir)
        self.assertEqual(runbook.RunState.load(self.run_dir).inputs['repo'], '/new')
        self.assertFalse(os.path.exists(path + '.tmp'))


class ShellCommandTest(RunbookTestCase):
    def test_printed_reply_answer_and_resume_commands_preserve_arguments(self) -> None:
        here = self.here + ' with spaces'
        os.rename(self.here, here)
        self.here = here
        self.run_dir += ' with spaces'
        executable = "/tmp/python bin/py'thon"
        work, ask = "work's $task", 'ask me; now'

        def broken(r: Any, s: Any) -> runbook.Target:
            raise ValueError('broken transition')

        def declare(rb: Runbook) -> None:
            rb.start(work)
            rb.step(work, executor='main', prompt='prompts/a.md', next=ask)
            rb.human(ask, question='Proceed?', choices=['yes', 'no'], next=broken)

        with mock.patch.object(sys, 'executable', executable):
            rb = self.runbook(declare)
        prefix = [executable, os.path.join(self.here, 'flow.py'), self.run_dir]
        out = self.start(rb)
        command = next(
            line.removeprefix('when it finishes: ')
            for line in out.splitlines()
            if line.startswith('when it finishes: ')
        )
        self.assertEqual(shlex.split(command), [*prefix, 'reply', work, '<the last JSON object of its message>'])
        out = self.reply(rb, work)
        command = next(line.removeprefix('  then: ') for line in out.splitlines() if line.startswith('  then: '))
        self.assertEqual(
            shlex.split(command),
            [*prefix, 'answer', ask, '<the choice>', '<their words verbatim, or - to read them from stdin>'],
        )
        err = self.fails(rb, 'answer', ask, 'yes')
        command = err.split('Once it is fixed, the run goes on with: ', 1)[1].strip()
        self.assertEqual(shlex.split(command), prefix)

    def test_under_uv_run_commands_go_through_uv(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('a')
            rb.step('a', executor='main', prompt='prompts/a.md', next=end('done'))

        os.environ['UV'] = '/usr/local/bin/uv'
        rb = self.runbook(declare)
        self.assertIn(
            f"when it finishes: uv run {self.here}/flow.py {self.run_dir} reply a '<the last JSON object of its message>'",
            self.start(rb),
        )


class CheckTest(RunbookTestCase):
    def test_consistent(self) -> None:
        self.assertEqual(self.check(self.runbook(linear)), (0, 'flow.py is consistent.\n'))

    def test_finds_problems(self) -> None:
        with mock.patch.object(sys, 'argv', [os.path.join(self.here, 'flow.py')]):
            rb = Runbook()
        rb.inputs(ticket=str)
        rb.executor('main', 'Main model')
        rb.start('ghostly')
        rb.step('ghostly', executor='ghost', prompt='prompts/missing.md', next='nowhere')
        rb.human('ask', question='', next=lambda c, s: end('ready'))
        rb.step('join', executor='main', prompt='prompts/a.md', after=('gone',), next=parallel('ask', 'lost'))
        code, out = self.check(rb)
        self.assertEqual(code, 1)
        self.assertEqual(
            out.splitlines(),
            [
                "inputs: 'repo' is not declared",
                'step ghostly: prompts/missing.md does not exist',
                "step ghostly: executor 'ghost' is not declared",
                "step ghostly: next step 'nowhere' is not declared",
                'step ask: human step without a question',
                "step join: after('gone') is not declared",
                "step join: parallel target 'lost' is not declared",
            ],
        )

    def test_missing_start(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.step('first', executor='light', prompt='prompts/a.md', next=end('ready'))

        rb = self.runbook(declare)
        self.assertEqual(self.check(rb), (1, 'no start step: call rb.start(<name>)\n'))
        self.assertIn('no start step: call rb.start(<name>)', self.fails(rb, 'start', '{"repo": "/repo"}'))

    def test_undeclared_start(self) -> None:
        def declare(rb: Runbook) -> None:
            linear(rb)
            rb.start('ghost')

        rb = self.runbook(declare)
        self.assertEqual(self.check(rb), (1, "start step 'ghost' is not declared\n"))


class SkipTest(RunbookTestCase):
    """A step with `skip` is left out when its function names a target, and `s.reply` sees earlier replies."""

    def declare(self, rb: Runbook) -> None:
        rb.start('left')
        rb.step('left', executor='light', prompt='prompts/a.md', reply={'findings': int}, next='merge')
        rb.step(
            'merge',
            executor='main',
            prompt='prompts/b.md',
            after=('left',),
            reply={'kept': int},
            skip=lambda s: 'last' if s.reply('left').findings == 0 else None,  # pyright: ignore[reportOptionalMemberAccess]
            next=lambda r, s: 'last' if r.kept == 0 else end('needs_attention'),
        )
        rb.step('last', executor='light', prompt='prompts/c.md', next=end('ready'))

    def setUp(self) -> None:
        super().setUp()
        self.rb = self.runbook(self.declare)
        self.start(self.rb)

    def test_skipped_when_nothing_to_merge(self) -> None:
        out = self.reply(self.rb, 'left', findings=0)
        self.assertEqual(self.first_line(out), 'launch step `last` as a new subagent, executor `light`: Light model')
        self.assertEqual([s['id'] for s in self.state()['sections']], ['left', 'last'])
        out = self.reply(self.rb, 'last')
        self.assertTrue(self.first_line(out).startswith('end: ready (after step `last`)'))

    def test_launched_when_there_is_work(self) -> None:
        out = self.reply(self.rb, 'left', findings=3)
        self.assertEqual(self.first_line(out), 'launch step `merge` as a new subagent, executor `main`: Main model')
        out = self.reply(self.rb, 'merge', kept=0)
        self.assertEqual(self.first_line(out), 'launch step `last` as a new subagent, executor `light`: Light model')

    def test_reply_of_unfinished_step_is_none(self) -> None:
        seen: list[tuple[Any, Any]] = []

        def declare(rb: Runbook) -> None:
            rb.start('a')
            rb.step(
                'a',
                executor='light',
                prompt='prompts/a.md',
                next=lambda r, s: seen.append((s.reply('a'), s.reply('b'))) or end('ready'),
            )
            rb.step('b', executor='light', prompt='prompts/b.md', next=end('ready'))

        rb = self.runbook(declare)
        self.run_dir = self.run_dir + '-none'
        self.start(rb)
        self.reply(rb, 'a')
        self.assertEqual(seen[0][0].status, 'done')
        self.assertIsNone(seen[0][1])

    def test_skips_that_go_round_in_a_circle_stop_the_command(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('a')
            rb.step('a', executor='light', prompt='prompts/a.md', skip=lambda s: parallel('b', 'c'), next=end('ready'))
            rb.step('b', executor='light', prompt='prompts/b.md', next=end('ready'))
            rb.step('c', executor='light', prompt='prompts/c.md', skip=lambda s: 'a', next=end('ready'))

        rb = self.runbook(declare)
        self.run_dir = self.run_dir + '-circle'
        err = self.fails(rb, 'start', json.dumps({'repo': '/repo'}))
        self.assertIn('`skip` goes round in a circle with nothing to launch: `a` -> `c` -> `a`', err)

    def test_a_skip_back_through_a_join_made_on_the_way_is_not_a_circle(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('x')
            rb.step('x', executor='light', prompt='prompts/a.md', next='a')
            rb.step(
                'a', executor='light', prompt='prompts/b.md', skip=lambda s: parallel('work', 'c'), next=end('ready')
            )
            rb.step('c', executor='light', prompt='prompts/c.md', after=('x',), skip=lambda s: 'a', next=end('ready'))
            rb.step('work', executor='light', prompt='prompts/d.md', next=end('ready'))

        rb = self.runbook(declare)
        self.run_dir = self.run_dir + '-join'
        self.start(rb)
        self.assertEqual(
            self.first_line(self.reply(rb, 'x')), 'launch step `work` as a new subagent, executor `light`: Light model'
        )

    def test_check_rejects_a_non_callable_skip(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('a')
            rb.step('a', executor='light', prompt='prompts/a.md', skip='b', next=end('ready'))  # pyright: ignore[reportArgumentType]

        rb = self.runbook(declare)
        code, out = self.check(rb)
        self.assertEqual(code, 1)
        self.assertIn("step a: skip is 'b', not a function", out)


if __name__ == '__main__':
    unittest.main()
