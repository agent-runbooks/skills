"""Tests of runbook.py: run with `python3 -m unittest test_runbook -v` from this directory."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from collections.abc import Callable
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
        """Runs a command that must exit with status 2; returns its stderr."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                rb.main(['flow.py', self.run_dir, *args])
        self.assertEqual(caught.exception.code, 2)
        return err.getvalue()

    def start(self, rb: Runbook, **given: object) -> str:
        return self.call(rb, 'start', json.dumps({'repo': '/repo', **given}))

    def reply(self, rb: Runbook, sid: str, **reply: object) -> str:
        return self.call(rb, 'reply', sid, json.dumps({'status': 'done', **reply}))

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
        self.assertIn("input 'strict' must be bool, got \"yes\"", err)

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
        self.assertEqual(self.state(), {
            'runbook': 'runbook-test', 'status': 'running', 'inputs': {'repo': '/repo', 'rounds': 2},
            'sections': [{'id': 'first', 'name': 'first', 'status': 'running', 'reply': None, 'note': None,
                          'answer': None, 'executor': 'light', 'started_at': '2026-10-03T10:00:00Z', 'ended_at': None}],
        })
        self.assertEqual(self.progress(), (
            '# Run run\n\nRunbook `runbook-test`. State in `state.json`. Inputs:\n\n'
            '- repo: /repo\n- rounds: 2\n\n## Log\n\n- first: launched\n'))
        self.assertEqual(out.splitlines(), [
            'launch first with executor light: Light model',
            '--- message ---',
            f'Read {self.here}/prompts/common.md, then {self.here}/prompts/a.md, and do what they say.',
            'repo: /repo',
            f'run: {self.run_dir}',
            '--- end of message ---',
            f"when it finishes: {self.cmd} {self.run_dir} reply first '<the last JSON object of its message>'",
        ])

    def test_refuses_existing_directory(self) -> None:
        self.start(self.rb)
        self.assertIn('exists', self.fails(self.rb, 'start', '{"repo": "/repo"}'))

    def test_linear_transition_and_end_report(self) -> None:
        self.start(self.rb)
        self.assertEqual(self.first_line(self.reply(self.rb, 'first')), 'launch second with executor main: Main model')
        out = self.reply(self.rb, 'second')
        self.assertEqual(out.strip(), f'end: ready (after step second). The run is over. Report to the human: '
                                      f'status ready, run directory {self.run_dir}, read {self.run_dir}/b.md.')
        self.assertEqual(self.state()['status'], 'ready')
        self.assertTrue(self.progress().endswith('- second: {"status": "done"}\n- end: ready (after step second)\n'))

    def test_log_and_status(self) -> None:
        self.start(self.rb)
        before = self.state()
        self.assertEqual(self.call(self.rb).strip(), 'still running: first')
        self.assertEqual(self.state(), before)
        self.call(self.rb, 'log', 'went off script')
        self.assertTrue(self.progress().endswith('- orchestrator: went off script\n'))

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
            rb.step('probe', executor='light', prompt='prompts/a.md', reply={'clean': bool},
                    next=lambda r, s: 'clean' if r.clean else 'dirty')
            rb.step('clean', executor='main', prompt='prompts/b.md', next=end('ready'))
            rb.step('dirty', executor='main', prompt='prompts/c.md', next=end('ready'))
        rb = self.runbook(declare)
        self.start(rb)
        self.assertEqual(self.first_line(self.reply(rb, 'probe', clean=False)), 'launch dirty with executor main: Main model')

    def test_invalid_reply_fails_the_run(self) -> None:
        rb = self.runbook(linear)
        base = self.run_dir
        for i, raw in enumerate(('garbage', '{"status": "ok"}', '[1]')):
            with self.subTest(raw=raw):
                self.run_dir = f'{base}-{i}'
                self.start(rb)
                out = self.call(rb, 'reply', 'first', raw)
                self.assertIn('end: failed (step first failed: invalid reply)', out)
                self.assertEqual(self.state()['sections'][0]['reply'], {'status': 'failed', 'reason': 'invalid reply'})

    def test_done_reply_without_declared_field_fails_the_run(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('checks')
            rb.step('checks', executor='light', prompt='prompts/a.md', reply={'passed': bool, 'count': int},
                    next=lambda r, s: end('ready') if r.passed else end('red'))
        rb = self.runbook(declare)
        base = self.run_dir
        cases = (({}, "no field 'passed'"),
                 ({'passed': 'yes', 'count': 1}, "field 'passed' must be bool, got \"yes\""),
                 ({'passed': True, 'count': True}, "field 'count' must be int, got true"))
        for i, (fields, problem) in enumerate(cases):
            with self.subTest(fields=fields):
                self.run_dir = f'{base}-{i}'
                self.start(rb)
                out = self.reply(rb, 'checks', **fields)
                self.assertIn(f'end: failed (step checks failed: invalid reply: {problem})', out)

    def test_blocked_without_on_failure_fails_with_reason(self) -> None:
        rb = self.runbook(linear)
        self.start(rb)
        out = self.call(rb, 'reply', 'first', '{"status": "blocked", "reason": "brief contradicts itself"}')
        self.assertIn('end: failed (step first blocked: brief contradicts itself)', out)
        self.assertEqual(self.state()['status'], 'failed')

    def test_on_failure_routes(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('try')
            rb.step('try', executor='light', prompt='prompts/a.md', next=end('ready'), on_failure='recover')
            rb.step('recover', executor='main', prompt='prompts/b.md', next=end('recovered'))
        rb = self.runbook(declare)
        self.start(rb)
        out = self.call(rb, 'reply', 'try', '{"status": "failed", "reason": "x"}')
        self.assertEqual(self.first_line(out), 'launch recover with executor main: Main model')

    def test_executor_function_and_inputs_in_message(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('go')
            rb.step('go', executor=lambda s: 'main' if s.inputs.coder == 'big' else 'light',
                    prompt='prompts/a.md', inputs=['coder', ('file', 'out.md'), 'missing'], next=end('ready'))
        rb = self.runbook(declare, coder='big')
        out = self.start(rb)
        self.assertEqual(self.first_line(out), 'launch go with executor main: Main model')
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
        self.assertEqual(self.launches(out), ['launch left with executor main: Main model',
                                              'launch right with executor light: Light model'])
        self.assertEqual(self.reply(self.rb, 'left').strip(), 'still running: right')
        self.assertEqual(self.launches(self.reply(self.rb, 'right')), ['launch join with executor main: Main model'])

    def test_failed_branch_before_join_ends_failed(self) -> None:
        self.reply(self.rb, 'fan')
        self.reply(self.rb, 'left')
        out = self.call(self.rb, 'reply', 'right', '{"status": "failed", "reason": "x"}')
        self.assertIn('end: failed (step right failed before the join at join)', out)

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
        out = self.call(rb, 'reply', 'right', 'garbage')
        self.assertEqual(out.strip(), 'wait: left. The run ends failed once they are recorded.')
        self.assertEqual(self.state()['status'], 'running')

    def test_end_waits_for_running_branch_visited_later(self) -> None:
        self.reply(self.rb, 'fan')
        out = self.call(self.rb, 'reply', 'left', '{"status": "failed", "reason": "x"}')
        self.assertEqual(out.strip(), 'wait: right. The run ends failed once they are recorded.')
        out = self.reply(self.rb, 'right')
        self.assertIn(f'end: failed (step left failed: x). The run is over. Report to the human: status failed, '
                      f'run directory {self.run_dir}, read {self.run_dir}/progress.md.', out)


class RunFilesTest(RunbookTestCase):
    """Each launch writes <run>/<NN>-<name>; readers get the latest done file of a name, or absent."""

    def declare(self, rb: Runbook) -> None:
        rb.start('check')
        rb.step('check', executor='light', prompt='prompts/a.md', reads=['brief.md', 'working tree'],
                writes=['check.md'], reply={'passed': bool},
                next=lambda r, s: end('ready', 'read <run>/check.md') if r.passed else 'fix')
        rb.step('fix', executor='main', prompt='prompts/b.md', reads=['check.md', 'fix.md'], writes=['fix.md'],
                next='check')

    def setUp(self) -> None:
        super().setUp()
        self.rb = self.runbook(self.declare)

    def message(self, out: str) -> list[str]:
        lines = out.splitlines()
        return lines[lines.index('--- message ---') + 1:lines.index('--- end of message ---')]

    def test_outputs_are_numbered_by_launch_and_readers_get_the_latest(self) -> None:
        run = self.run_dir
        out = self.start(self.rb)
        self.assertEqual(self.message(out)[3:], [f'write check.md: {run}/00-check.md', f'read brief.md: {run}/brief.md'])
        out = self.reply(self.rb, 'check', passed=False)
        self.assertEqual(self.message(out)[3:], [f'write fix.md: {run}/01-fix.md', f'read check.md: {run}/00-check.md',
                                                 'read fix.md: absent, no earlier step wrote it'])
        out = self.reply(self.rb, 'fix')
        self.assertIn(f'write check.md: {run}/02-check.md', self.message(out))
        out = self.reply(self.rb, 'check-2', passed=False)
        self.assertEqual(self.message(out)[3:], [f'write fix.md: {run}/03-fix.md', f'read check.md: {run}/02-check.md',
                                                 f'read fix.md: {run}/01-fix.md'])
        self.reply(self.rb, 'fix-2')
        out = self.reply(self.rb, 'check-3', passed=True)
        self.assertIn(f'read {run}/04-check.md.', out)


class HumanTest(RunbookTestCase):
    def declare(self, rb: Runbook) -> None:
        rb.start('ask')
        rb.human('ask', choices=['Continue', 'stop'], writes='answer.md',
                 question='Read `<run>/x.md`. Continue or stop?',
                 next=lambda choice, s: 'go' if choice == 'Continue' else end('stopped', 'read <run>/x.md'))
        rb.step('go', executor='main', prompt='prompts/a.md', next=end('ready'))

    def setUp(self) -> None:
        super().setUp()
        self.rb = self.runbook(self.declare)
        self.out = self.start(self.rb)

    def test_prints_question_and_choices(self) -> None:
        self.assertEqual(self.out.splitlines(), [
            f'ask the human (ask): Read `{self.run_dir}/x.md`. Continue or stop?',
            '  choices: Continue | stop. Map the answer to one of them; ask again if none fits.',
            f"  then: {self.cmd} {self.run_dir} answer ask '<the choice>' '<their words verbatim, or - to read them from stdin>'",
        ])
        self.assertEqual(self.state()['status'], 'waiting_for_human')

    def test_status_repeats_the_wait(self) -> None:
        before = self.state()
        out = self.call(self.rb)
        self.assertEqual(out.strip(), f"waiting for the human on ask. Choices: Continue | stop. "
                                      f"When they answer: {self.cmd} {self.run_dir} answer ask '<the choice>' '<their words verbatim, or - to read them from stdin>'.")
        self.assertEqual(self.state(), before)

    def test_answer_ignores_case(self) -> None:
        out = self.call(self.rb, 'answer', 'ask', ' CONTINUE ')
        self.assertEqual(self.first_line(out), 'launch go with executor main: Main model')
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
            rb.human('ask', question='What next?', writes='words.md',
                     next=lambda words, s: 'go' if 'go' in words else end('stopped'))
            rb.step('go', executor='main', prompt='prompts/a.md', next=end('done'))
        rb = self.runbook(declare)
        self.run_dir = self.run_dir + '-free'
        out = self.start(rb)
        self.assertIn('  free text: pass their words as they are.', out)
        self.assertIn("answer ask '<their words verbatim, or - to read them from stdin>'", out)
        out = self.call(rb, 'answer', 'ask', 'please go on, carefully')
        self.assertEqual(self.first_line(out), 'launch go with executor main: Main model')
        with open(os.path.join(self.run_dir, '00-words.md'), encoding='utf-8') as f:
            self.assertEqual(f.read(), 'please go on, carefully\n')

class LoopTest(RunbookTestCase):
    def test_loop_budget_and_counter_suffixes(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('fix')
            rb.step('fix', executor='main', prompt='prompts/a.md', next='verify')
            rb.step('verify', executor='light', prompt='prompts/b.md', reply={'ok': bool},
                    next=lambda r, s: end('ready') if r.ok
                    else ('fix' if s.done('verify') < s.inputs.rounds else end('needs_attention', 'read <run>/verify.md')))
        rb = self.runbook(declare, rounds=3)
        self.start(rb)
        self.assertEqual(self.first_line(self.reply(rb, 'fix')), 'launch verify with executor light: Light model')
        self.assertEqual(self.first_line(self.reply(rb, 'verify', ok=False)), 'launch fix-2 with executor main: Main model')
        self.assertEqual(self.first_line(self.reply(rb, 'fix-2')), 'launch verify-2 with executor light: Light model')
        self.assertEqual(self.first_line(self.reply(rb, 'verify-2', ok=False)), 'launch fix-3 with executor main: Main model')
        self.reply(rb, 'fix-3')
        out = self.reply(rb, 'verify-3', ok=False)
        self.assertIn('end: needs_attention (after step verify-3)', out)
        self.assertEqual([s['id'] for s in self.state()['sections']],
                         ['fix', 'verify', 'fix-2', 'verify-2', 'fix-3', 'verify-3'])


class RelaunchTest(RunbookTestCase):
    def test_interrupted_opens_a_new_section(self) -> None:
        rb = self.runbook(linear)
        self.start(rb)
        out = self.call(rb, 'interrupted', 'first')
        self.assertEqual(self.first_line(out), 'launch first-2 with executor light: Light model')
        self.assertIn('The tree may hold a partial earlier attempt.\n--- end of message ---', out)
        self.assertEqual([(s['id'], s['status'], s['note']) for s in self.state()['sections']],
                         [('first', 'failed', 'interrupted'), ('first-2', 'running', None)])
        self.assertIn('- first: interrupted\n', self.progress())

    def test_side_effect_failure_asks_the_human(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('commit')
            rb.step('commit', executor='main', prompt='prompts/a.md', side_effects='commit', next=end('ready'))
        rb = self.runbook(declare)
        self.assertEqual(self.first_line(self.start(rb)),
                         'launch commit with executor main: Main model. Side effects: commit')
        out = self.call(rb, 'reply', 'commit', '{"status": "failed", "reason": "hook rejected"}')
        self.assertEqual(out.strip(), (
            f'ask the human: step commit has side effects and ended failed (hook rejected). '
            f'On yes: {self.cmd} {self.run_dir} relaunch commit. '
            f"On no: {self.cmd} {self.run_dir} log '<their decision>' and stop."))
        out = self.call(rb, 'relaunch', 'commit')
        self.assertEqual(self.first_line(out), 'launch commit-2 with executor main: Main model. Side effects: commit')
        self.assertIn('The tree may hold a partial earlier attempt.', out)
        self.assertEqual(self.state()['sections'][0]['note'], "relaunched on the human's yes")


class TimingTest(RunbookTestCase):
    """Each section records its executor, when it was opened and when it left an open status."""

    def fields(self) -> list[tuple[str, str | None, str | None, str | None]]:
        return [(s['id'], s['executor'], s['started_at'], s['ended_at']) for s in self.state()['sections']]

    def test_launch_and_reply(self) -> None:
        rb = self.runbook(linear)
        self.start(rb)
        self.assertEqual(self.fields(), [('first', 'light', '2026-10-03T10:00:00Z', None)])
        self.reply(rb, 'first')
        self.assertEqual(self.fields(), [('first', 'light', '2026-10-03T10:00:00Z', '2026-10-03T10:01:00Z'),
                                         ('second', 'main', '2026-10-03T10:02:00Z', None)])

    def test_failed_reply_ends_the_section(self) -> None:
        rb = self.runbook(linear)
        self.start(rb)
        self.call(rb, 'reply', 'first', '{"status": "failed", "reason": "x"}')
        self.assertEqual(self.fields(), [('first', 'light', '2026-10-03T10:00:00Z', '2026-10-03T10:01:00Z')])

    def test_executor_function_is_recorded_by_its_resolved_name(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('go')
            rb.step('go', executor=lambda s: 'main' if s.inputs.coder == 'big' else 'light',
                    prompt='prompts/a.md', next=end('ready'))
        rb = self.runbook(declare, coder='big')
        self.start(rb)
        self.assertEqual(self.state()['sections'][0]['executor'], 'main')

    def test_answer(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('ask')
            rb.human('ask', choices=['Continue', 'stop'], question='Continue?',
                     next=lambda choice, s: 'go' if choice == 'Continue' else end('stopped'))
            rb.step('go', executor='main', prompt='prompts/a.md', next=end('ready'))
        rb = self.runbook(declare)
        self.start(rb)
        self.assertEqual(self.fields(), [('ask', None, '2026-10-03T10:00:00Z', None)])
        self.call(rb, 'answer', 'ask', 'Continue')
        self.assertEqual(self.fields(), [('ask', None, '2026-10-03T10:00:00Z', '2026-10-03T10:01:00Z'),
                                         ('go', 'main', '2026-10-03T10:02:00Z', None)])

    def test_interrupted(self) -> None:
        rb = self.runbook(linear)
        self.start(rb)
        self.call(rb, 'interrupted', 'first')
        self.assertEqual(self.fields(), [('first', 'light', '2026-10-03T10:00:00Z', '2026-10-03T10:01:00Z'),
                                         ('first-2', 'light', '2026-10-03T10:02:00Z', None)])

    def test_relaunch_keeps_the_end_of_the_failed_section(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('commit')
            rb.step('commit', executor='main', prompt='prompts/a.md', side_effects='commit', next=end('ready'))
        rb = self.runbook(declare)
        self.start(rb)
        self.call(rb, 'reply', 'commit', '{"status": "failed", "reason": "hook rejected"}')
        self.call(rb, 'relaunch', 'commit')
        self.assertEqual(self.fields(), [('commit', 'main', '2026-10-03T10:00:00Z', '2026-10-03T10:01:00Z'),
                                         ('commit-2', 'main', '2026-10-03T10:02:00Z', None)])

    def test_state_without_the_fields_resumes(self) -> None:
        rb = self.runbook(linear)
        self.start(rb)
        with open(os.path.join(self.run_dir, 'state.json'), encoding='utf-8') as f:
            data = json.load(f)
        for section in data['sections']:
            for name in ('executor', 'started_at', 'ended_at'):
                del section[name]
        with open(os.path.join(self.run_dir, 'state.json'), 'w', encoding='utf-8') as f:
            json.dump(data, f)
        self.assertEqual(self.call(rb).strip(), 'still running: first')
        self.assertEqual(self.first_line(self.reply(rb, 'first')), 'launch second with executor main: Main model')
        self.assertEqual(self.fields(), [('first', None, None, '2026-10-03T10:01:00Z'),
                                         ('second', 'main', '2026-10-03T10:02:00Z', None)])


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
        self.assertEqual(out.splitlines(), [
            "inputs: 'repo' is not declared",
            'step ghostly: prompts/missing.md does not exist',
            "step ghostly: executor 'ghost' is not declared",
            "step ghostly: next step 'nowhere' is not declared",
            'step ask: human step without a question',
            "step join: after('gone') is not declared",
            "step join: parallel target 'lost' is not declared",
        ])

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


if __name__ == '__main__':
    unittest.main()


class SkipTest(RunbookTestCase):
    """A step with `skip` is left out when its function names a target, and `s.reply` sees earlier replies."""

    def declare(self, rb: Runbook) -> None:
        rb.start('left')
        rb.step('left', executor='light', prompt='prompts/a.md', reply={'findings': int}, next='merge')
        rb.step('merge', executor='main', prompt='prompts/b.md', after=('left',), reply={'kept': int},
                skip=lambda s: 'last' if s.reply('left').findings == 0 else None,
                next=lambda r, s: 'last' if r.kept == 0 else end('needs_attention'))
        rb.step('last', executor='light', prompt='prompts/c.md', next=end('ready'))

    def setUp(self) -> None:
        super().setUp()
        self.rb = self.runbook(self.declare)
        self.start(self.rb)

    def test_skipped_when_nothing_to_merge(self) -> None:
        out = self.reply(self.rb, 'left', findings=0)
        self.assertEqual(self.first_line(out), 'launch last with executor light: Light model')
        self.assertEqual([s['id'] for s in self.state()['sections']], ['left', 'last'])
        out = self.reply(self.rb, 'last')
        self.assertTrue(self.first_line(out).startswith('end: ready (after step last)'))

    def test_launched_when_there_is_work(self) -> None:
        out = self.reply(self.rb, 'left', findings=3)
        self.assertEqual(self.first_line(out), 'launch merge with executor main: Main model')
        out = self.reply(self.rb, 'merge', kept=0)
        self.assertEqual(self.first_line(out), 'launch last with executor light: Light model')

    def test_reply_of_unfinished_step_is_none(self) -> None:
        seen: list[object] = []

        def declare(rb: Runbook) -> None:
            rb.start('a')
            rb.step('a', executor='light', prompt='prompts/a.md',
                    next=lambda r, s: seen.append((s.reply('a'), s.reply('b'))) or end('ready'))
            rb.step('b', executor='light', prompt='prompts/b.md', next=end('ready'))
        rb = self.runbook(declare)
        self.run_dir = self.run_dir + '-none'
        self.start(rb)
        self.reply(rb, 'a')
        self.assertEqual(seen[0][0].status, 'done')
        self.assertIsNone(seen[0][1])

    def test_check_rejects_a_non_callable_skip(self) -> None:
        def declare(rb: Runbook) -> None:
            rb.start('a')
            rb.step('a', executor='light', prompt='prompts/a.md', skip='b', next=end('ready'))
        rb = self.runbook(declare)
        code, out = self.check(rb)
        self.assertEqual(code, 1)
        self.assertIn("step a: skip is 'b', not a function", out)
