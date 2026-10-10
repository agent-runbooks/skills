"""Tests of agent_runbooks.py: run with `python3 -m unittest test_runbook -v` from this directory."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import re
import shlex
import sys
import tempfile
import time
import unittest
from collections.abc import Callable
from typing import Any
from unittest import mock

import agent_runbooks
from agent_runbooks import Executor, Runbook, StepFailed, address, end, files, foreach, parallel


class RunbookTestCase(unittest.TestCase):
    """A temporary runbook directory with prompts, a run directory inside a temporary root, and fake executors:
    a launch's message is kept by its label, and a reply first writes the files the message names."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.realpath(tmp.name)
        self.here = os.path.join(self.root, 'runbook-test')
        os.makedirs(os.path.join(self.here, 'prompts'))
        for name in ('common.md', 'a.md', 'b.md', 'c.md', 'd.md'):
            with open(os.path.join(self.here, 'prompts', name), 'w') as f:
                f.write(name)
        self.run_dir = os.path.join(self.root, 'run')
        self.cmd = f'{sys.executable} {self.here}/flow.py'
        self.messages: dict[str, list[str]] = {}
        environ = mock.patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        os.environ.pop('UV', None)
        self.minute = 0
        clock = mock.patch.object(agent_runbooks, 'utc_now', self.tick)
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
        rb.executor('strong', 'Strong model')
        rb.executor('light', 'Light model')
        declare(rb)
        return rb

    def call(self, rb: Runbook, *args: str) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = rb.main(['flow.py', self.run_dir, *args])
        self.assertEqual(code, 0)
        label = None
        for line in out.getvalue().splitlines():
            launch = re.match(r'launch step `([^`]+)`', line)
            if launch:
                label = launch.group(1)
                self.messages[label] = []
            elif line == '--- end of message ---':
                label = None
            elif label is not None and line != '--- message ---':
                self.messages[label].append(line)
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

    def lines(self, label: str, prefix: str) -> dict[str, str]:
        """The `<prefix> <name>: <value>` lines of a launch's message, by name."""
        found = {}
        for line in self.messages[label]:
            if line.startswith(prefix + ' '):
                name, _, value = line[len(prefix) + 1 :].partition(': ')
                found[name] = value
        return found

    def reads(self, label: str) -> dict[str, str]:
        return self.lines(label, 'read')

    def reply(self, rb: Runbook, label: str, contents: dict[str, str] | None = None, **fields: object) -> str:
        """The executor of the launch writes each file its message names, then replies done with these fields."""
        for name, path in self.lines(label, 'write').items():
            with open(path, 'w', encoding='utf-8') as f:
                f.write((contents or {}).get(name, f'{label} wrote {name}\n'))
        return self.call(rb, 'reply', label, json.dumps({'status': 'done', **fields}))

    def fail_step(self, rb: Runbook, label: str, reason: str = 'broken', status: str = 'failed') -> str:
        return self.call(rb, 'reply', label, json.dumps({'status': status, 'reason': reason}))

    def reject(self, rb: Runbook, label: str, raw: str) -> str:
        """Passes an invalid reply, and again after the correction it asks for; returns the second output."""
        self.assertTrue(
            self.first_line(self.call(rb, 'reply', label, raw)).startswith(f'the reply of step `{label}` did not')
        )
        return self.call(rb, 'reply', label, raw)

    @staticmethod
    def launched(out: str) -> list[str]:
        return re.findall(r'^launch step `([^`]+)`', out, re.MULTILINE)

    @staticmethod
    def asked(out: str) -> list[str]:
        return re.findall(r'^ask the human \(`([^`]+)`\)', out, re.MULTILINE)

    def state(self) -> dict:
        with open(os.path.join(self.run_dir, 'state.json')) as f:
            return json.load(f)

    def record(self, label: str) -> dict:
        address, _, attempt = label.partition('@')
        return next(c for c in self.state()['calls'] if c['id'] == address and c['attempt'] == int(attempt or 1))

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

    def write_input(self, name: str, value: object) -> None:
        with open(os.path.join(self.run_dir, name), 'w', encoding='utf-8') as f:
            json.dump(value, f)


def executors(rb: Runbook) -> tuple[Executor, Executor]:
    """The executors RunbookTestCase.runbook declares."""
    return rb.executors['strong'], rb.executors['light']


def linear(rb: Runbook) -> None:
    strong, light = executors(rb)
    first = rb.step('first', executor=light, prompt='prompts/a.md')
    second = rb.step('second', executor=strong, prompt='prompts/b.md', writes=['b.md'])

    @rb.flow
    def main(ctx):
        yield first()
        yield second()
        return end('ready', 'read <run>/b.md')


def review_loop(rb: Runbook) -> None:
    """The flow of runbook-review-loop."""
    strong, light = executors(rb)
    implement = rb.step(
        'implement', executor=strong, prompt='prompts/a.md', reads=['brief.md', 'working tree'], writes=['implement.md']
    )
    review = rb.step(
        'review',
        executor=light,
        prompt='prompts/b.md',
        reads=['brief.md', 'implement.md', 'fix.md', 'review.md', 'working tree'],
        writes=['review.md'],
        reply={'findings': int},
    )
    fix = rb.step('fix', executor=strong, prompt='prompts/c.md', reads=['review.md', 'rounds.md'], writes=['fix.md'])
    ask_rounds = rb.human(
        'ask-rounds',
        writes='rounds.md',
        choices=['more rounds', 'stop'],
        reply={'rounds': {'type': 'integer', 'default': 1, 'description': 'how many more'}},
        question='Fix rounds are spent and `<run>/review.md` still lists findings. More, or stop?',
    )

    @rb.flow
    def main(ctx):
        yield implement()
        budget, used = ctx.inputs.maxFixRounds, 0
        while True:
            r = yield review()
            if r.findings == 0:
                return end('ready', 'read <run>/review.md')
            if used >= budget:
                a = yield ask_rounds()
                if a.choice == 'stop':
                    return end('needs_attention', 'read <run>/review.md')
                budget += a.rounds
            yield fix()
            used += 1


# ---------- whole flows ----------


class ReviewLoopTest(RunbookTestCase):
    def test_end_to_end_with_the_human_extending_the_budget(self) -> None:
        rb = self.runbook(review_loop, brief=str, maxFixRounds=1)
        self.assertEqual(self.check(rb), (0, 'flow.py is consistent.\n'))
        out = self.start(rb, brief='brief.md')
        self.assertEqual(self.launched(out), ['main/implement'])
        self.assertEqual(self.reads('main/implement'), {'brief.md': f'{self.run_dir}/brief.md'})
        self.assertEqual(self.launched(self.reply(rb, 'main/implement')), ['main/review'])
        self.assertEqual(
            self.reads('main/review'),
            {
                'brief.md': f'{self.run_dir}/brief.md',
                'implement.md': f'{self.run_dir}/00-implement.md',
                'fix.md': 'absent, no earlier step wrote it',
                'review.md': 'absent, no earlier step wrote it',
            },
        )
        self.assertEqual(self.launched(self.reply(rb, 'main/review', findings=2)), ['main/fix'])
        self.assertEqual(self.launched(self.reply(rb, 'main/fix')), ['main/review#2'])
        reads = self.reads('main/review#2')
        self.assertEqual(
            (reads['fix.md'], reads['review.md']), (f'{self.run_dir}/02-fix.md', f'{self.run_dir}/01-review.md')
        )
        out = self.reply(rb, 'main/review#2', findings=1)
        self.assertEqual(self.asked(out), ['main/ask-rounds'])
        self.assertIn(f'`{self.run_dir}/03-review.md` still lists findings', out)
        self.assertEqual(self.state()['status'], 'waiting_for_human')
        out = self.call(rb, 'answer', 'main/ask-rounds', '{"choice": "More Rounds", "rounds": 2}', 'focus on tests')
        self.assertEqual(self.launched(out), ['main/fix#2'])
        self.assertEqual(self.reads('main/fix#2')['rounds.md'], f'{self.run_dir}/04-rounds.md')
        with open(f'{self.run_dir}/04-rounds.md') as f:
            self.assertEqual(f.read(), 'focus on tests\n')
        self.assertEqual(self.launched(self.reply(rb, 'main/fix#2')), ['main/review#3'])
        self.assertEqual(self.launched(self.reply(rb, 'main/review#3', findings=1)), ['main/fix#3'])
        self.reply(rb, 'main/fix#3')
        out = self.reply(rb, 'main/review#4', findings=0)
        self.assertEqual(
            out.strip(),
            f'end: ready (after `main/review#4`). The run is over. Report to the human: status ready, '
            f'run directory {self.run_dir}, read {self.run_dir}/08-review.md.',
        )
        self.assertEqual(self.state()['status'], 'ready')
        log = self.progress()
        self.assertEqual(log.count(': launched\n'), 8)
        self.assertIn(
            '- main/ask-rounds: answered: more rounds {"rounds": 2}\n- main/ask-rounds: said: focus on tests\n', log
        )
        self.assertTrue(
            log.endswith('- main/review#4: {"status": "done", "findings": 0}\n- end: ready (after `main/review#4`)\n')
        )

    def test_the_human_stops(self) -> None:
        rb = self.runbook(review_loop, brief=str, maxFixRounds=0)
        self.start(rb, brief='brief.md')
        self.reply(rb, 'main/implement')
        self.reply(rb, 'main/review', findings=1)
        out = self.call(rb, 'answer', 'main/ask-rounds', '{"choice": "stop"}', 'stop')
        self.assertTrue(out.startswith('end: needs_attention (after `main/ask-rounds`)'))
        self.assertTrue(out.strip().endswith(f'read {self.run_dir}/01-review.md.'))


def task_cycle(rb: Runbook) -> None:
    """A task cycle like runbook-implement-task's: preflight, implement, two reviews, triage, fix rounds, polish."""
    strong, light = executors(rb)
    preflight = rb.step(
        'preflight', executor=light, prompt='prompts/a.md', writes=['preflight.md'], reply={'clean': bool}
    )
    implement = rb.step(
        'implement', executor=strong, prompt='prompts/b.md', reads=['brief.md'], writes=['implement.md']
    )
    review_a = rb.step(
        'review-a',
        executor=strong,
        prompt='prompts/c.md',
        reads=['implement.md'],
        writes=['review-a.md'],
        reply={'findings': int},
    )
    review_b = rb.step(
        'review-b',
        executor=light,
        prompt='prompts/c.md',
        reads=['implement.md'],
        writes=['review-b.md'],
        reply={'findings': int},
    )
    triage = rb.step('triage', executor=strong, prompt='prompts/d.md', writes=['triage.md'], reply={'to_fix': int})
    fix = rb.step(
        'fix', executor=strong, prompt='prompts/b.md', reads=['triage.md', 'verify.md', 'rounds.md'], writes=['fix.md']
    )
    verify = rb.step(
        'verify',
        executor=strong,
        prompt='prompts/c.md',
        reads=['fix.md'],
        writes=['verify.md'],
        reply={'unresolved': int},
    )
    polish = rb.step('polish', executor=strong, prompt='prompts/d.md', writes=['polish.md'], reply={'passed': bool})
    ask_rounds = rb.human(
        'ask-rounds',
        writes='rounds.md',
        choices=['one more round', 'stop'],
        question='Fix rounds are spent, see `<run>/verify.md`. One more round, or stop?',
    )
    ask_dirty = rb.human(
        'ask-dirty', choices=['continue', 'stop'], question='The tree is dirty, see `<run>/preflight.md`. Continue?'
    )

    @rb.flow
    def main(ctx):
        p = yield preflight()
        if not p.clean:
            a = yield ask_dirty()
            if a.choice == 'stop':
                return end('failed', 'read <run>/preflight.md')
        yield implement()
        reviews = yield parallel('reviews', a=review_a(), b=review_b())
        if reviews.a.findings or reviews.b.findings:
            t = yield triage(reviews=reviews)
            rounds = 0
            while t.to_fix:
                yield fix()
                rounds += 1
                v = yield verify()
                if v.unresolved == 0:
                    break
                if rounds >= ctx.inputs.maxFixRounds:
                    a = yield ask_rounds()
                    if a.choice == 'stop':
                        return end('needs_attention', 'read <run>/verify.md')
        p = yield polish()
        return end('ready' if p.passed else 'needs_attention', 'read <run>/polish.md')


class TaskCycleTest(RunbookTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.rb = self.runbook(task_cycle, maxFixRounds=1)

    def test_clean_reviews_skip_triage(self) -> None:
        self.start(self.rb)
        self.assertEqual(self.launched(self.reply(self.rb, 'main/preflight', clean=True)), ['main/implement'])
        out = self.reply(self.rb, 'main/implement')
        self.assertEqual(self.launched(out), ['main/reviews/a', 'main/reviews/b'])
        self.assertIn('2 steps to launch together, in one turn:', out)
        self.assertEqual(self.reads('main/reviews/b'), {'implement.md': f'{self.run_dir}/01-implement.md'})
        self.assertEqual(self.reply(self.rb, 'main/reviews/b', findings=0).strip(), 'still running: `main/reviews/a`')
        self.assertEqual(self.launched(self.reply(self.rb, 'main/reviews/a', findings=0)), ['main/polish'])
        self.assertEqual(self.record('main/reviews')['status'], 'done')
        out = self.reply(self.rb, 'main/polish', passed=True)
        self.assertTrue(out.startswith('end: ready (after `main/polish`)'))
        self.assertNotIn('triage', json.dumps(self.state()))

    def test_findings_a_fix_round_and_the_human_stops(self) -> None:
        self.start(self.rb)
        self.reply(self.rb, 'main/preflight', clean=True)
        self.reply(self.rb, 'main/implement')
        self.reply(self.rb, 'main/reviews/a', findings=2)
        self.assertEqual(self.launched(self.reply(self.rb, 'main/reviews/b', findings=1)), ['main/triage'])
        self.assertEqual(
            self.messages['main/triage'][3:],
            [
                'reviews.a.findings: 2',
                'reviews.b.findings: 1',
                f'write triage.md: {self.run_dir}/05-triage.md',
                f'read reviews.a/review-a.md: {self.run_dir}/03-review-a.md',
                f'read reviews.b/review-b.md: {self.run_dir}/04-review-b.md',
                f'reply schema: {self.run_dir}/schemas/05-triage.json',
            ],
        )
        self.assertEqual(self.launched(self.reply(self.rb, 'main/triage', to_fix=2)), ['main/fix'])
        self.assertEqual(self.reads('main/fix')['triage.md'], f'{self.run_dir}/05-triage.md')
        self.reply(self.rb, 'main/fix')
        out = self.reply(self.rb, 'main/verify', unresolved=1)
        self.assertEqual(self.asked(out), ['main/ask-rounds'])
        self.assertIn(f'see `{self.run_dir}/07-verify.md`', out)
        out = self.call(self.rb, 'answer', 'main/ask-rounds', 'stop')
        self.assertTrue(out.startswith('end: needs_attention (after `main/ask-rounds`)'))
        self.assertTrue(out.strip().endswith(f'read {self.run_dir}/07-verify.md.'))

    def test_dirty_tree_and_the_human_stops(self) -> None:
        self.start(self.rb)
        out = self.reply(self.rb, 'main/preflight', clean=False)
        self.assertEqual(self.asked(out), ['main/ask-dirty'])
        self.assertIn(f'see `{self.run_dir}/00-preflight.md`', out)
        out = self.call(self.rb, 'answer', 'main/ask-dirty', 'stop')
        self.assertTrue(out.startswith('end: failed (after `main/ask-dirty`)'))
        self.assertEqual(self.state()['status'], 'failed')

    def test_the_files_a_join_is_told_do_not_depend_on_the_order_of_the_replies(self) -> None:
        told = []
        for order in (('a', 'b'), ('b', 'a')):
            self.run_dir = os.path.join(self.root, 'run-' + ''.join(order))
            self.start(self.rb)
            self.reply(self.rb, 'main/preflight', clean=True)
            self.reply(self.rb, 'main/implement')
            for branch in order:
                self.reply(self.rb, f'main/reviews/{branch}', findings=1)
            told.append([line.replace(self.run_dir, '<run>') for line in self.messages['main/triage']])
            self.assertEqual(
                self.record('main/triage')['files_in']['reviews.a/review-a.md'], f'{self.run_dir}/03-review-a.md'
            )
        self.assertEqual(told[0], told[1])


class ParallelTest(RunbookTestCase):
    def declare(self, rb: Runbook) -> None:
        strong, light = executors(rb)
        implement = rb.step('implement', executor=strong, prompt='prompts/a.md', writes=['implement.md'])
        review_a = rb.step(
            'review-a',
            executor=strong,
            prompt='prompts/b.md',
            reads=['implement.md'],
            writes=['review-a.md'],
            reply={'findings': int},
        )
        review_b = rb.step(
            'review-b',
            executor=light,
            prompt='prompts/b.md',
            reads=['implement.md'],
            writes=['review-b.md'],
            reply={'findings': int},
        )
        lint = rb.step('lint', executor=light, prompt='prompts/c.md', writes=['lint.md'], reply={'ok': bool})
        triage = rb.step(
            'triage',
            executor=strong,
            prompt='prompts/d.md',
            reads=['implement.md', 'review-a.md'],
            writes=['triage.md'],
        )
        commit = rb.step('commit', executor=strong, prompt='prompts/a.md', side_effects='pushes a commit')

        def chain(ctx):
            r = yield review_b()
            try:
                lint_result = yield lint()
            except StepFailed as e:
                return {'review': r, 'lint': e.reason}
            return {'review': r, 'lint': lint_result.ok}

        @rb.flow
        def main(ctx):
            yield implement()
            reviews = yield parallel('reviews', a=review_a(), b=chain)
            yield triage(reviews=reviews)
            try:
                yield commit()
            except StepFailed:
                return end('wrong', 'a side-effect failure never reaches the flow')
            return end('ready', f'a has {reviews.a.findings}, lint says {reviews.b["lint"]}')

    def test_a_chain_handles_its_failure_the_join_gets_an_explicit_pass_and_a_side_effect_step_is_relaunched(
        self,
    ) -> None:
        rb = self.runbook(self.declare)
        self.start(rb)
        out = self.reply(rb, 'main/implement')
        self.assertEqual(self.launched(out), ['main/reviews/a', 'main/reviews/b/review-b'])
        self.assertEqual(self.reads('main/reviews/b/review-b'), {'implement.md': f'{self.run_dir}/00-implement.md'})
        self.assertEqual(self.launched(self.reply(rb, 'main/reviews/b/review-b', findings=1)), ['main/reviews/b/lint'])
        self.assertEqual(
            self.fail_step(rb, 'main/reviews/b/lint', 'eslint crashed').strip(), 'still running: `main/reviews/a`'
        )
        out = self.reply(rb, 'main/reviews/a', findings=3)
        self.assertEqual(self.launched(out), ['main/triage'])
        self.assertEqual(self.record('main/reviews')['branches'], {'a': 'done', 'b': 'done'})
        self.assertEqual(
            self.messages['main/triage'][3:],
            [
                'reviews.a.findings: 3',
                'reviews.b.review.findings: 1',
                'reviews.b.lint: eslint crashed',
                f'write triage.md: {self.run_dir}/05-triage.md',
                f'read implement.md: {self.run_dir}/00-implement.md',
                'read review-a.md: absent, no earlier step wrote it',
                f'read reviews.a/review-a.md: {self.run_dir}/02-review-a.md',
                f'read reviews.b.review/review-b.md: {self.run_dir}/03-review-b.md',
                f'reply schema: {self.run_dir}/schemas/05-triage.json',
            ],
        )
        self.assertEqual(
            self.record('main/triage')['inputs'],
            {'reviews.a.findings': 3, 'reviews.b.review.findings': 1, 'reviews.b.lint': 'eslint crashed'},
        )
        out = self.reply(rb, 'main/triage')
        self.assertEqual(
            self.first_line(out),
            'launch step `main/commit` as a new subagent, executor `strong`: Strong model. Side effects: pushes a commit',
        )
        out = self.fail_step(rb, 'main/commit', 'push rejected')
        self.assertEqual(
            out.strip(),
            f'ask the human: step `main/commit` has side effects and ended failed (push rejected). '
            f'On yes: {self.cmd} {self.run_dir} relaunch main/commit. '
            f"On no: {self.cmd} {self.run_dir} log '<their decision>' and stop.",
        )
        self.assertEqual(self.call(rb).strip(), out.strip())
        out = self.call(rb, 'relaunch', 'main/commit')
        self.assertEqual(self.launched(out), ['main/commit@2'])
        self.assertEqual(self.messages['main/commit@2'][-1], 'The tree may hold a partial earlier attempt.')
        self.assertIn("reply main/commit@2 '<the last JSON object of its message>'", out)
        self.assertEqual(self.record('main/commit')['note'], "relaunched on the human's yes")
        self.assertIn("- main/commit: relaunched on the human's yes\n- main/commit@2: launched\n", self.progress())
        out = self.reply(rb, 'main/commit@2')
        self.assertTrue(out.startswith('end: ready (after `main/commit`). '), out)
        self.assertIn('a has 3, lint says eslint crashed', out)

    def test_a_failed_branch_waits_for_a_running_sibling_and_launches_nothing_new(self) -> None:
        def declare(rb: Runbook) -> None:
            strong, _ = executors(rb)
            a = rb.step('a', executor=strong, prompt='prompts/a.md')
            b1 = rb.step('b1', executor=strong, prompt='prompts/b.md')
            b2 = rb.step('b2', executor=strong, prompt='prompts/c.md')
            after = rb.step('after', executor=strong, prompt='prompts/d.md')
            ask = rb.human('ask', question='Anything?')

            def chain(ctx):
                yield b1()
                yield b2()

            def asking(ctx):
                return (yield ask())

            @rb.flow
            def main(ctx):
                try:
                    yield parallel('group', a=a(), b=chain, c=asking)
                except StepFailed as e:
                    yield after(note=f'{e.id} {e.reason}')
                    return end('failed', 'read <run>/progress.md')
                return end('ready')

        rb = self.runbook(declare)
        out = self.start(rb)
        self.assertEqual(self.launched(out), ['main/group/a', 'main/group/b/b1'])
        self.assertEqual(self.asked(out), ['main/group/c/ask'])
        out = self.fail_step(rb, 'main/group/a', 'disk full')
        self.assertEqual(
            out.splitlines(),
            [
                'withdraw the question `main/group/c/ask`: its group failed; tell the human no answer is needed',
                '',
                'still running: `main/group/b/b1`',
            ],
        )
        self.assertEqual(self.record('main/group/c/ask')['status'], 'cancelled')
        self.assertTrue(self.progress().endswith('- main/group/c/ask: cancelled\n'), self.progress())
        self.assertIn('call main/group/c/ask is cancelled', self.fails(rb, 'answer', 'main/group/c/ask', 'yes'))
        out = self.reply(rb, 'main/group/b/b1')
        self.assertEqual(self.launched(out), ['main/after'])
        self.assertIn('note: main/group/a disk full', self.messages['main/after'])
        self.assertNotIn('main/group/b/b2', json.dumps(self.state()))
        group = self.record('main/group')
        self.assertEqual(
            (group['status'], group['branches']), ('failed', {'a': 'failed', 'b': 'cancelled', 'c': 'cancelled'})
        )

    def test_a_group_that_fails_once_driven_launches_and_asks_nothing_beside_it(self) -> None:
        def declare(rb: Runbook) -> None:
            strong, _ = executors(rb)
            work = rb.step('work', executor=strong, prompt='prompts/a.md')
            push = rb.step('push', executor=strong, prompt='prompts/b.md', side_effects='pushes')
            ask = rb.human('ask', question='ok?')

            def missing_over(ctx):
                yield foreach('f', over='missing.json', body=work())

            def failing_inner(ctx):
                def boom(ctx):
                    raise StepFailed('no')
                    yield work()

                yield parallel('inner', a=boom)

            def asking(ctx):
                yield ask()

            @rb.flow
            def main(ctx):
                yield parallel(
                    'g', x=missing_over if ctx.inputs.kind == 'foreach' else failing_inner, y=push(), h=asking
                )
                return end('ready')

        for kind in ('foreach', 'parallel'):
            with self.subTest(kind=kind):
                self.run_dir = os.path.join(self.root, f'run-{kind}')
                why = {
                    'foreach': f'`main/g/x/f` failed: {self.run_dir}/missing.json: No such file or directory',
                    'parallel': '`main/g/x/inner/a` failed: no',
                }[kind]
                out = self.start(self.runbook(declare, kind=str), kind=kind)
                self.assertEqual((self.launched(out), self.asked(out)), ([], []))
                self.assertTrue(out.startswith(f'end: failed ({why})'), out)
                self.assertEqual([c['id'] for c in self.state()['calls'] if c['kind'] in ('step', 'human')], [])

    def test_an_unhandled_failure_in_a_branch_fails_the_run(self) -> None:
        def declare(rb: Runbook) -> None:
            strong, _ = executors(rb)
            a = rb.step('a', executor=strong, prompt='prompts/a.md')
            b = rb.step('b', executor=strong, prompt='prompts/b.md')

            @rb.flow
            def main(ctx):
                yield parallel('group', a=a(), b=b())
                return end('ready')

        rb = self.runbook(declare)
        self.start(rb)
        self.reply(rb, 'main/group/b')
        out = self.fail_step(rb, 'main/group/a', 'disk full', status='blocked')
        self.assertEqual(
            out.strip(),
            f'end: failed (`main/group/a` blocked: disk full). The run is over. Report to the human: '
            f'status failed, run directory {self.run_dir}, read {self.run_dir}/progress.md.',
        )

    def test_a_human_step_in_a_branch_takes_free_text_and_names_the_branch_s_own_files(self) -> None:
        def declare(rb: Runbook) -> None:
            strong, _ = executors(rb)
            draft = rb.step('draft', executor=strong, prompt='prompts/a.md', writes=['note.md'])
            ask = rb.human('ask', writes='words.md', question='Is `<run>/note.md` fine?')
            summary = rb.step('summary', executor=strong, prompt='prompts/b.md', reads=['note.md', 'words.md'])

            def branch(ctx):
                yield draft()
                return (yield ask())

            @rb.flow
            def main(ctx):
                yield draft()
                answers = yield parallel('notes', x=branch, y=branch)
                yield summary(answers=answers)
                return end('ready')

        rb = self.runbook(declare)
        self.start(rb)
        out = self.reply(rb, 'main/draft')
        self.assertEqual(self.launched(out), ['main/notes/x/draft', 'main/notes/y/draft'])
        self.reply(rb, 'main/notes/x/draft')
        out = self.reply(rb, 'main/notes/y/draft')
        self.assertEqual(self.asked(out), ['main/notes/y/ask'])
        self.assertIn(f'Is `{self.run_dir}/03-note.md` fine?', out)
        self.assertIn('  free text: pass their words as they are.', out)
        self.assertIn('waiting for the human on `main/notes/x/ask`. Free text.', out)
        self.call(rb, 'answer', 'main/notes/x/ask', 'fine as it is')
        out = self.call(rb, 'answer', 'main/notes/y/ask', 'shorten it')
        self.assertEqual(self.launched(out), ['main/summary'])
        self.assertEqual(
            self.reads('main/summary'),
            {
                'note.md': f'{self.run_dir}/00-note.md',
                'words.md': 'absent, no earlier step wrote it',
                'answers.x/words.md': f'{self.run_dir}/04-words.md',
                'answers.y/words.md': f'{self.run_dir}/05-words.md',
            },
        )
        self.assertIn('answers.y.choice: shorten it', self.messages['main/summary'])
        with open(f'{self.run_dir}/05-words.md') as f:
            self.assertEqual(f.read(), 'shorten it\n')


# ---------- foreach ----------


def sites(rb: Runbook, on_item_failure: agent_runbooks.OnItemFailure = 'fail', max_concurrent: int = 2) -> None:
    strong, light = executors(rb)
    plan = rb.step('plan', executor=light, prompt='prompts/a.md', writes=['sites.json'])
    migrate = rb.step(
        'migrate',
        executor=strong,
        prompt='prompts/b.md',
        reads=['plan.md'],
        writes=['migrated.md'],
        reply={'changed': int},
    )
    verify = rb.step('verify', executor=light, prompt='prompts/c.md', reads=['migrated.md'], writes=['verify.md'])
    summary = rb.step('summary', executor=strong, prompt='prompts/d.md', reads=['sites.index'])

    def body(ctx, item):
        m = yield migrate()
        yield verify()
        return {'changed': m.changed, 'url': item.url}

    @rb.flow
    def main(ctx):
        yield plan()
        done = yield foreach(
            'sites', over='sites.json', body=body, max_concurrent=max_concurrent, on_item_failure=on_item_failure
        )
        yield summary(done=done)
        return end('ready', 'read <run>/sites.index')


SITES = json.dumps([{'key': k, 'url': f'https://{k}.example'} for k in ('a', 'b', 'c')])


class ForeachTest(RunbookTestCase):
    def index(self, label: str) -> list[dict]:
        with open(self.record(label)['index']) as f:
            return json.load(f)

    def test_fail_stops_new_items_waits_for_running_ones_and_fails_the_run(self) -> None:
        rb = self.runbook(sites)
        self.start(rb)
        out = self.reply(rb, 'main/plan', contents={'sites.json': SITES})
        self.assertEqual(self.launched(out), ['main/sites[a]/migrate', 'main/sites[b]/migrate'])
        self.assertIn("reply 'main/sites[a]/migrate' '<the last JSON object of its message>'", out)
        self.assertEqual(
            self.messages['main/sites[b]/migrate'][3:6],
            ['key: b', 'url: https://b.example', f'write migrated.md: {self.run_dir}/05-migrated.md'],
        )
        out = self.fail_step(rb, 'main/sites[a]/migrate', 'timeout')
        self.assertEqual(out.strip(), 'still running: `main/sites[b]/migrate`')
        self.assertEqual(self.record('main/sites[a]')['reason'], 'timeout')
        self.assertEqual(self.record('main/sites')['status'], 'running')
        out = self.reply(rb, 'main/sites[b]/migrate', changed=1)
        self.assertTrue(out.startswith('end: failed (`main/sites[a]/migrate` failed: timeout)'), out)
        self.assertEqual(
            [(row['key'], row['status'], row['reason']) for row in self.index('main/sites')],
            [('a', 'failed', 'timeout'), ('b', 'cancelled', None), ('c', 'not_started', None)],
        )
        self.assertEqual(self.index('main/sites')[1]['files'], {'migrated.md': f'{self.run_dir}/05-migrated.md'})
        self.assertEqual(self.record('main/sites')['index'], f'{self.run_dir}/01-sites.index.json')
        self.assertEqual(
            [(c['id'], c['status']) for c in self.state()['calls']],
            [
                ('main/plan', 'done'),
                ('main/sites', 'failed'),
                ('main/sites[a]', 'failed'),
                ('main/sites[b]', 'cancelled'),
                ('main/sites[a]/migrate', 'failed'),
                ('main/sites[b]/migrate', 'done'),
            ],
        )

    def test_item_results_pass_their_fields_and_files(self) -> None:
        def declare(rb: Runbook) -> None:
            strong, light = executors(rb)
            plan = rb.step('plan', executor=light, prompt='prompts/a.md', writes=['sites.json'])
            migrate = rb.step(
                'migrate', executor=strong, prompt='prompts/b.md', writes=['migrated.md'], reply={'changed': int}
            )
            summary = rb.step('summary', executor=strong, prompt='prompts/d.md')

            @rb.flow
            def main(ctx):
                yield plan()
                done = yield foreach('sites', over='sites.json', body=migrate())
                yield summary(first=done.items[0], all=done.items)
                return end('ready')

        rb = self.runbook(declare)
        self.start(rb)
        self.reply(rb, 'main/plan', contents={'sites.json': json.dumps([{'key': 'a'}, {'key': 'b'}])})
        self.reply(rb, 'main/sites[a]/migrate', changed=1)
        out = self.reply(rb, 'main/sites[b]/migrate', changed=2)
        self.assertEqual(self.launched(out), ['main/summary'])
        a = self.record('main/sites[a]/migrate')['files']['migrated.md']
        b = self.record('main/sites[b]/migrate')['files']['migrated.md']
        self.assertEqual(
            self.reads('main/summary'), {'first/migrated.md': a, 'all.0/migrated.md': a, 'all.1/migrated.md': b}
        )
        inputs = self.record('main/summary')['inputs']
        self.assertEqual(
            (inputs['first.key'], inputs['first.status'], inputs['first.reply']), ('a', 'done', {'changed': 1})
        )
        self.assertEqual(
            (inputs['all.1.key'], inputs['all.1.reply'], inputs['all.1.reason']), ('b', {'changed': 2}, None)
        )
        self.assertIn('first.reply: {"changed": 1}', self.messages['main/summary'])

    def test_skip_goes_on_and_the_join_reads_the_index(self) -> None:
        rb = self.runbook(lambda rb: sites(rb, on_item_failure='skip'))
        self.start(rb)
        self.reply(rb, 'main/plan', contents={'sites.json': SITES})
        out = self.fail_step(rb, 'main/sites[a]/migrate', 'timeout')
        self.assertEqual(self.launched(out), ['main/sites[c]/migrate'])
        self.assertEqual(self.record('main/sites[a]')['status'], 'failed')
        self.assertEqual(self.launched(self.reply(rb, 'main/sites[b]/migrate', changed=1)), ['main/sites[b]/verify'])
        self.assertEqual(self.reads('main/sites[b]/verify'), {'migrated.md': f'{self.run_dir}/05-migrated.md'})
        self.reply(rb, 'main/sites[b]/verify')
        self.reply(rb, 'main/sites[c]/migrate', changed=0)
        out = self.reply(rb, 'main/sites[c]/verify')
        self.assertEqual(self.launched(out), ['main/summary'])
        index = f'{self.run_dir}/01-sites.index.json'
        self.assertEqual(self.reads('main/summary'), {'sites.index': index, 'done/sites.index': index})
        rows = self.index('main/sites')
        self.assertEqual([(r['key'], r['status']) for r in rows], [('a', 'failed'), ('b', 'done'), ('c', 'done')])
        self.assertEqual(rows[1]['reply'], {'changed': 1, 'url': 'https://b.example'})
        self.assertEqual(
            rows[1]['files'],
            {'migrated.md': f'{self.run_dir}/05-migrated.md', 'verify.md': f'{self.run_dir}/08-verify.md'},
        )
        self.assertEqual(self.record('main/sites[b]')['reply'], {'changed': 1, 'url': 'https://b.example'})
        out = self.reply(rb, 'main/summary')
        self.assertTrue(out.strip().endswith(f'read {index}.'), out)

    def test_bad_over_files_fail_the_foreach(self) -> None:
        cases = {
            'not a list': ({'key': 'a'}, 'is not a JSON array of objects'),
            'repeated key': ([{'key': 'a'}, {'key': 'a'}], "item 1 of {path} repeats key 'a'"),
            'bad key': ([{'key': 'a b'}], 'item 0 of {path} has key "a b"'),
            'too many': ([{'key': str(i)} for i in range(3)], 'has 3 items, more than max_items=2'),
            'absent input': (None, 'No such file or directory'),
        }
        for case, (content, problem) in cases.items():
            with self.subTest(case=case):

                def declare(rb: Runbook) -> None:
                    strong, _ = executors(rb)
                    work = rb.step('work', executor=strong, prompt='prompts/a.md')

                    @rb.flow
                    def main(ctx):
                        try:
                            yield foreach('items', over='items.json', body=work(), max_items=2)
                        except StepFailed as e:
                            return end('needs_attention', e.reason)
                        return end('ready')

                self.run_dir = os.path.join(self.root, case.replace(' ', '-'))
                os.makedirs(self.run_dir)
                if content is not None:
                    self.write_input('items.json', content)
                out = self.start(self.runbook(declare))
                path = f'{self.run_dir}/items.json'
                self.assertTrue(out.startswith('end: needs_attention (after `main/items`)'), out)
                self.assertIn(problem.format(path=path), out)
                self.assertEqual(self.record('main/items')['status'], 'failed')
                self.assertEqual(self.index('main/items'), [])

    def test_an_over_file_no_step_wrote_yet_is_absent(self) -> None:
        def declare(rb: Runbook) -> None:
            strong, light = executors(rb)
            rb.step('plan', executor=light, prompt='prompts/a.md', writes=['sites.json'])
            work = rb.step('work', executor=strong, prompt='prompts/a.md')

            @rb.flow
            def main(ctx):
                yield foreach('sites', over='sites.json', body=work())
                return end('ready')

        out = self.start(self.runbook(declare))
        self.assertTrue(
            out.startswith('end: failed (`main/sites` failed: sites.json is absent, no earlier step wrote it)'), out
        )

    def test_a_loop_reaches_the_foreach_twice_and_a_body_of_one_call_replies_for_the_item(self) -> None:
        def declare(rb: Runbook) -> None:
            strong, _ = executors(rb)
            work = rb.step('work', executor=strong, prompt='prompts/a.md', writes=['work.md'], reply={'n': int})

            @rb.flow
            def main(ctx):
                total = 0
                for _ in range(2):
                    done = yield foreach('items', over='items.json', body=work(round=1), max_concurrent=5)
                    total += sum(item.reply['n'] for item in done.items)
                return end('ready', f'total {total}')

        os.makedirs(self.run_dir)
        self.write_input('items.json', [{'key': 'x', 'size': 3}, {'key': 'y', 'size': 4}])
        rb = self.runbook(declare)
        out = self.start(rb)
        self.assertEqual(self.launched(out), ['main/items[x]/work', 'main/items[y]/work'])
        self.assertEqual(self.messages['main/items[y]/work'][3:6], ['round: 1', 'key: y', 'size: 4'])
        self.reply(rb, 'main/items[x]/work', n=1)
        out = self.reply(rb, 'main/items[y]/work', n=2)
        self.assertEqual(self.launched(out), ['main/items#2[x]/work', 'main/items#2[y]/work'])
        self.assertEqual(self.record('main/items[y]')['reply'], {'n': 2})
        self.reply(rb, 'main/items#2[x]/work', n=3)
        out = self.reply(rb, 'main/items#2[y]/work', n=4)
        self.assertIn('Report to the human: status ready, run directory', out)
        self.assertTrue(out.strip().endswith('total 10.'))

    def test_a_parallel_and_a_foreach_nest_inside_an_item(self) -> None:
        def declare(rb: Runbook) -> None:
            strong, light = executors(rb)
            check_x = rb.step('check-x', executor=strong, prompt='prompts/a.md', writes=['x.md'], reply={'ok': bool})
            check_y = rb.step('check-y', executor=light, prompt='prompts/b.md', writes=['y.md'])
            page = rb.step('page', executor=light, prompt='prompts/c.md', reads=['x.md'], writes=['page.md'])

            def body(ctx, item):
                checks = yield parallel('checks', x=check_x(), y=check_y())
                pages = yield foreach('pages', over='pages.json', body=page(site=item.key))
                return {'ok': checks.x.ok, 'pages': [p.status for p in pages.items]}

            @rb.flow
            def main(ctx):
                done = yield foreach('sites', over='sites.json', body=body)
                return end('ready', json.dumps([item.reply for item in done.items]))

        os.makedirs(self.run_dir)
        self.write_input('sites.json', [{'key': 'a'}, {'key': 'b'}])
        self.write_input('pages.json', [{'key': 'p1'}, {'key': 'p2'}])
        rb = self.runbook(declare)
        out = self.start(rb)
        self.assertEqual(
            self.launched(out),
            ['main/sites[a]/checks/x', 'main/sites[a]/checks/y', 'main/sites[b]/checks/x', 'main/sites[b]/checks/y'],
        )
        for site in 'ab':
            self.reply(rb, f'main/sites[{site}]/checks/x', ok=site == 'a')
            out = self.reply(rb, f'main/sites[{site}]/checks/y')
            self.assertEqual(
                self.launched(out), [f'main/sites[{site}]/pages[p1]/page', f'main/sites[{site}]/pages[p2]/page']
            )
        self.assertEqual(
            self.messages['main/sites[b]/pages[p2]/page'][3:5],
            ['site: b', 'key: p2'],
            'the innermost item names the fields',
        )
        self.assertEqual(self.reads('main/sites[b]/pages[p2]/page'), {'x.md': 'absent, no earlier step wrote it'})
        for site in 'ab':
            for p in ('p1', 'p2'):
                out = self.reply(rb, f'main/sites[{site}]/pages[{p}]/page')
        self.assertIn('[{"ok": true, "pages": ["done", "done"]}, {"ok": false, "pages": ["done", "done"]}]', out)
        rows = self.index('main/sites')
        self.assertEqual(sorted(rows[0]['files']), ['page.md', 'x.md', 'y.md'])

    def test_an_item_whose_nested_group_fails_once_driven_leaves_the_other_items_unlaunched(self) -> None:
        def declare(rb: Runbook) -> None:
            strong, light = executors(rb)
            plan = rb.step('plan', executor=light, prompt='prompts/a.md', writes=['items.json'])
            work = rb.step('work', executor=strong, prompt='prompts/b.md')

            def body(ctx, item):
                if item.key == 'p':
                    yield foreach('inner', over='missing.json', body=work())
                else:
                    yield work()

            @rb.flow
            def main(ctx):
                yield plan()
                yield foreach('f', over='items.json', body=body, max_concurrent=2)
                return end('ready')

        rb = self.runbook(declare)
        self.start(rb)
        out = self.reply(rb, 'main/plan', {'items.json': '[{"key": "p"}, {"key": "q"}]'})
        self.assertEqual(self.launched(out), [])
        self.assertTrue(out.startswith('end: failed (`main/f[p]/inner` failed: '), out)
        self.assertEqual(self.record('main/f[q]')['status'], 'cancelled')
        self.assertNotIn('main/f[q]/work', json.dumps(self.state()))

    def test_a_foreach_over_the_index_of_the_one_closed_in_the_same_pass_reads_its_rows(self) -> None:
        def declare(rb: Runbook) -> None:
            _, light = executors(rb)
            plan = rb.step('plan', executor=light, prompt='prompts/a.md', writes=['items.json'])

            def body(ctx, item):
                yield from ()
                return {'from': getattr(item, 'status', 'items.json')}

            @rb.flow
            def main(ctx):
                yield plan()
                first = yield foreach('first', over='items.json', body=body)
                second = yield foreach('second', over=files(first)['first.index'], body=body)
                return end('ready', f'{second.items[0].reply["from"]}')

        rb = self.runbook(declare)
        self.start(rb)
        with mock.patch.object(
            agent_runbooks.Replay, 'run', autospec=True, side_effect=agent_runbooks.Replay.run
        ) as replays:
            out = self.reply(rb, 'main/plan', contents={'items.json': json.dumps([{'key': 'a'}])})
        self.assertTrue(out.startswith('end: ready (after `main/second`)'), out)
        self.assertTrue(out.rstrip().endswith(', done.'), out)
        self.assertLessEqual(replays.call_count, 2)
        self.assertEqual(self.record('main/first')['status'], 'done')
        self.assertEqual(self.record('main/second')['status'], 'done')
        self.assertEqual(self.record('main/second')['over'], self.record('main/first')['index'])
        self.assertEqual([row['key'] for row in self.index('main/second')], ['a'])
        self.assertEqual(self.call(rb), out)

    def test_a_skipped_failure_of_a_nested_foreach_does_not_stop_the_outer_one(self) -> None:
        def declare(rb: Runbook) -> None:
            _, light = executors(rb)
            page = rb.step('page', executor=light, prompt='prompts/c.md')

            def body(ctx, item):
                pages = yield foreach('pages', over='pages.json', body=page(), on_item_failure='skip')
                return [p.status for p in pages.items]

            @rb.flow
            def main(ctx):
                done = yield foreach('sites', over='sites.json', body=body, on_item_failure='fail')
                return end('ready', json.dumps([item.reply for item in done.items]))

        os.makedirs(self.run_dir)
        self.write_input('sites.json', [{'key': 'a'}])
        self.write_input('pages.json', [{'key': 'p1'}, {'key': 'p2'}])
        rb = self.runbook(declare)
        self.start(rb)
        self.fail_step(rb, 'main/sites[a]/pages[p1]/page')
        out = self.reply(rb, 'main/sites[a]/pages[p2]/page')
        self.assertTrue(out.startswith('end: ready'), out)
        self.assertIn('[["failed", "done"]]', out)

    def test_an_item_s_step_interrupted_at_the_concurrency_limit_relaunches_in_its_item(self) -> None:
        rb = self.runbook(lambda rb: sites(rb, max_concurrent=1))
        self.start(rb)
        out = self.reply(rb, 'main/plan', contents={'sites.json': SITES})
        self.assertEqual(self.launched(out), ['main/sites[a]/migrate'])
        out = self.call(rb, 'interrupted', 'main/sites[a]/migrate')
        self.assertEqual(self.launched(out), ['main/sites[a]/migrate@2'])
        self.assertEqual(self.messages['main/sites[a]/migrate@2'][-1], 'The tree may hold a partial earlier attempt.')
        self.assertEqual(self.record('main/sites[a]/migrate')['status'], 'interrupted')
        self.assertEqual(self.launched(self.reply(rb, 'main/sites[a]/migrate@2', changed=0)), ['main/sites[a]/verify'])
        self.assertEqual(self.launched(self.reply(rb, 'main/sites[a]/verify')), ['main/sites[b]/migrate'])

    def test_a_body_that_returns_a_non_json_value_is_refused_before_state_is_written(self) -> None:
        def declare(rb: Runbook) -> None:
            strong, _ = executors(rb)
            work = rb.step('work', executor=strong, prompt='prompts/a.md')

            def body(ctx, item):
                yield work()
                return object()

            @rb.flow
            def main(ctx):
                yield foreach('items', over='items.json', body=body)
                return end('ready')

        os.makedirs(self.run_dir)
        self.write_input('items.json', [{'key': 'x'}])
        rb = self.runbook(declare)
        self.start(rb)
        err = self.fails(rb, 'reply', 'main/items[x]/work', '{"status": "done"}')
        self.assertIn('`main/items[x]`: the body returned <object object', err)
        self.assertIn('This is a defect in flow.py', err)
        self.assertEqual(self.record('main/items[x]/work')['status'], 'done')
        self.assertEqual(self.record('main/items[x]')['status'], 'running')
        self.assertFalse(any(name.endswith('.index.json') for name in os.listdir(self.run_dir)))

    def test_foreach_parameters_are_checked_when_it_is_reached(self) -> None:
        cases = {
            'max_concurrent is 0, not a whole number from 1': dict(max_concurrent=0),
            'max_items is True, not a whole number from 1': dict(max_items=True),
            "on_item_failure is 'retry', not one of fail, skip": dict(on_item_failure='retry'),
            "over is '', not a file name": dict(over=''),
        }
        for problem, kwargs in cases.items():
            with self.subTest(problem=problem):

                def declare(rb: Runbook, kwargs: dict[str, Any] = kwargs) -> None:
                    strong, _ = executors(rb)
                    work = rb.step('work', executor=strong, prompt='prompts/a.md')

                    @rb.flow
                    def main(ctx):
                        yield foreach('items', **{'over': 'items.json', 'body': work(), **kwargs})
                        return end('ready')

                self.run_dir = os.path.join(self.root, f'run-{len(problem)}')
                err = self.fails(self.runbook(declare), 'start', '{"repo": "/repo"}')
                self.assertIn(f"foreach('items'): {problem}", err)


# ---------- replies, attempts, commands ----------


class StartTest(RunbookTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.rb = self.runbook(linear, rounds=2)

    def test_creates_run_and_prints_first_launch(self) -> None:
        out = self.start(self.rb)
        self.assertEqual(
            self.state(),
            {
                'format': 2,
                'runbook': 'runbook-test',
                'status': 'running',
                'inputs': {'repo': '/repo', 'rounds': 2},
                'calls': [
                    {
                        'id': 'main/first',
                        'attempt': 1,
                        'kind': 'step',
                        'step': 'first',
                        'status': 'running',
                        'executor': 'light',
                        'reply': None,
                        'note': None,
                        'answer': None,
                        'inputs': {},
                        'files_in': {},
                        'files': {},
                        'schema': agent_runbooks.reply_schema({}),
                        'writes': {},
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
                '- repo: /repo\n- rounds: 2\n\n## Log\n\n- main/first: launched\n'
            ),
        )
        self.assertEqual(
            out.splitlines(),
            [
                'launch step `main/first` as a new subagent, executor `light`: Light model',
                '--- message ---',
                f'Read {self.here}/prompts/common.md, then {self.here}/prompts/a.md, and do what they say.',
                'repo: /repo',
                f'run: {self.run_dir}',
                f'reply schema: {self.run_dir}/schemas/00-first.json',
                '--- end of message ---',
                f"when it finishes: {self.cmd} {self.run_dir} reply main/first '<the last JSON object of its message>'",
            ],
        )

    def test_refuses_a_directory_that_holds_a_run(self) -> None:
        self.start(self.rb)
        before = self.state()
        self.assertIn(f'{self.run_dir} holds a run', self.fails(self.rb, 'start', '{"repo": "/repo"}'))
        self.assertEqual(self.state(), before)

    def test_starts_in_an_existing_directory_without_a_run(self) -> None:
        os.makedirs(self.run_dir)
        self.write_input('brief.md', 'brief')
        self.assertEqual(self.launched(self.start(self.rb)), ['main/first'])

    def test_linear_flow_and_end_report(self) -> None:
        self.start(self.rb)
        self.assertEqual(
            self.first_line(self.reply(self.rb, 'main/first')),
            'launch step `main/second` as a new subagent, executor `strong`: Strong model',
        )
        out = self.reply(self.rb, 'main/second')
        self.assertEqual(
            out.strip(),
            f'end: ready (after `main/second`). The run is over. Report to the human: '
            f'status ready, run directory {self.run_dir}, read {self.run_dir}/01-b.md.',
        )
        self.assertEqual(self.state()['status'], 'ready')
        self.assertTrue(
            self.progress().endswith('- main/second: {"status": "done"}\n- end: ready (after `main/second`)\n')
        )
        self.assertEqual(self.call(self.rb).strip(), out.strip())
        self.assertEqual(self.progress().count('- end: ready'), 1)

    def test_log_and_status(self) -> None:
        self.start(self.rb)
        before = self.state()
        self.assertEqual(self.call(self.rb).strip(), 'still running: `main/first`')
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
        self.assertIn("usage: flow.py <run> reply <call> '<reply JSON>'", self.fails(self.rb, 'reply', 'main/first'))
        self.assertIn('no call nope in state.json', self.fails(self.rb, 'reply', 'nope', '{}'))
        self.assertIn('no call main/first@2 in state.json', self.fails(self.rb, 'reply', 'main/first@2', '{}'))
        self.reply(self.rb, 'main/first')
        self.assertIn(
            'call main/first is done, not running', self.fails(self.rb, 'reply', 'main/first', '{"status": "done"}')
        )

    def test_a_run_of_another_format_does_not_load(self) -> None:
        os.makedirs(self.run_dir)
        with open(os.path.join(self.run_dir, 'state.json'), 'w') as f:
            json.dump({'runbook': 'x', 'status': 'running', 'inputs': {}, 'sections': []}, f)
        self.assertIn('is format 1, and agent_runbooks.py 2.0.0 reads format 2', self.fails(self.rb))


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

    def test_the_flow_reads_them(self) -> None:
        def declare(rb: Runbook) -> None:
            strong, _ = executors(rb)
            work = rb.step('work', executor=strong, prompt='prompts/a.md', inputs=['ticket', ('mode', 'fast')])

            @rb.flow
            def main(ctx):
                yield work(
                    executor='light' if ctx.inputs.rounds > 2 else 'strong', rounds=ctx.inputs.rounds, tags=['x']
                )
                return end('ready')

        rb = self.runbook(declare, ticket=str, rounds=2)
        out = self.start(rb, ticket='T-1', rounds=3)
        self.assertEqual(
            self.first_line(out), 'launch step `main/work` as a new subagent, executor `light`: Light model'
        )
        self.assertEqual(self.messages['main/work'][3:7], ['ticket: T-1', 'mode: fast', 'rounds: 3', 'tags: ["x"]'])
        self.assertEqual(self.record('main/work')['inputs'], {'rounds': 3, 'tags': ['x']})

    def test_a_call_takes_an_executor_or_the_name_of_a_declared_one(self) -> None:
        def declare(rb: Runbook) -> None:
            strong, light = executors(rb)
            work = rb.step('work', executor=strong, prompt='prompts/a.md')

            @rb.flow
            def main(ctx):
                yield parallel('picks', a=work(executor=light), b=work(executor=ctx.inputs.coder), c=work())
                return end('ready')

        rb = self.runbook(declare, coder=str)
        self.start(rb, coder='light')
        self.assertEqual(
            [(c['id'], c['executor']) for c in self.state()['calls'] if c['kind'] == 'step'],
            [('main/picks/a', 'light'), ('main/picks/b', 'light'), ('main/picks/c', 'strong')],
        )

    def test_declared_pairs_are_one_line_each_as_a_call_s_inputs_are(self) -> None:
        def declare(rb: Runbook) -> None:
            strong, _ = executors(rb)
            work = rb.step(
                'work', executor=strong, prompt='prompts/a.md', inputs=[('tags', ['a']), ('note', 'two\nlines')]
            )

            @rb.flow
            def main(ctx):
                yield work()
                return end('ready')

        rb = self.runbook(declare)
        self.start(rb)
        self.assertEqual(self.messages['main/work'][3:5], ['tags: ["a"]', 'note: "two\\nlines"'])


class AttemptTest(RunbookTestCase):
    def test_interrupted_opens_the_next_attempt_and_a_late_reply_to_the_closed_one_is_refused(self) -> None:
        rb = self.runbook(linear)
        self.start(rb)
        out = self.call(rb, 'interrupted', 'main/first')
        self.assertEqual(
            self.first_line(out), 'launch step `main/first@2` as a new subagent, executor `light`: Light model'
        )
        self.assertIn('The tree may hold a partial earlier attempt.\n--- end of message ---', out)
        self.assertEqual(
            [(c['id'], c['attempt'], c['status']) for c in self.state()['calls']],
            [('main/first', 1, 'interrupted'), ('main/first', 2, 'running')],
        )
        self.assertIn('- main/first: interrupted\n- main/first@2: launched\n', self.progress())
        before = self.state()
        err = self.fails(rb, 'reply', 'main/first', '{"status": "done"}')
        self.assertIn('call main/first is a closed attempt (interrupted); the latest attempt is main/first@2', err)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.launched(self.reply(rb, 'main/first@2')), ['main/second'])

    def test_commands_log_the_canonical_label_however_the_attempt_is_typed(self) -> None:
        rb = self.runbook(linear)
        self.start(rb)
        self.call(rb, 'interrupted', 'main/first@01')
        self.call(rb, 'reply', 'main/first@02', '{"status": "done"}')
        self.assertIn(
            '- main/first: interrupted\n- main/first@2: launched\n- main/first@2: {"status": "done"}\n', self.progress()
        )

    def test_interrupted_and_relaunch_refuse_what_they_do_not_take_without_writing(self) -> None:
        def declare(rb: Runbook) -> None:
            strong, _ = executors(rb)
            publish = rb.step('publish', executor=strong, prompt='prompts/a.md', side_effects='publish')
            work = rb.step('work', executor=strong, prompt='prompts/b.md')
            ask = rb.human('ask', question='Proceed?')

            @rb.flow
            def main(ctx):
                yield parallel('all', p=publish(), w=work(), a=ask())
                return end('ready')

        rb = self.runbook(declare)
        self.start(rb)
        self.reply(rb, 'main/all/w')
        cases = [
            ('relaunch', 'main/all/p', 'is running'),
            ('relaunch', 'main/all/w', 'is done'),
            ('relaunch', 'main/all/a', 'is waiting'),
            ('interrupted', 'main/all/w', 'is done'),
            ('interrupted', 'main/all/a', 'is waiting'),
        ]
        for command, label, problem in cases:
            with self.subTest(command=command, label=label):
                before, progress = self.state(), self.progress()
                err = self.fails(rb, command, label)
                self.assertIn(f'call {label} {problem}', err)
                self.assertIn('accepts only', err)
                self.assertEqual(self.state(), before)
                self.assertEqual(self.progress(), progress)

    def test_a_side_effect_step_interrupted_asks_the_human(self) -> None:
        def declare(rb: Runbook) -> None:
            strong, _ = executors(rb)
            publish = rb.step('publish', executor=strong, prompt='prompts/a.md', side_effects='publish')

            @rb.flow
            def main(ctx):
                yield publish()
                return end('ready')

        rb = self.runbook(declare)
        self.start(rb)
        out = self.call(rb, 'interrupted', 'main/publish')
        self.assertTrue(
            out.startswith('ask the human: step `main/publish` has side effects and ended interrupted. '), out
        )
        self.assertEqual(self.launched(self.call(rb, 'relaunch', 'main/publish')), ['main/publish@2'])
        self.assertIn('call main/publish@2 is running', self.fails(rb, 'relaunch', 'main/publish@2'))

    def test_a_failed_group_withdraws_the_side_effect_question_and_refuses_the_relaunch(self) -> None:
        def declare(rb: Runbook) -> None:
            strong, _ = executors(rb)
            publish = rb.step('publish', executor=strong, prompt='prompts/a.md', side_effects='publish')
            work = rb.step('work', executor=strong, prompt='prompts/b.md')

            @rb.flow
            def main(ctx):
                yield parallel('g', p=publish(), w=work())
                return end('ready')

        rb = self.runbook(declare)
        self.start(rb)
        self.assertIn('ask the human: step `main/g/p`', self.fail_step(rb, 'main/g/p', 'push rejected'))
        out = self.fail_step(rb, 'main/g/w', 'tests red')
        self.assertEqual(
            out.splitlines()[:2],
            [
                'withdraw the question whether to relaunch step `main/g/p`: its group failed, and it is not '
                'relaunched; tell the human no answer is needed',
                '',
            ],
        )
        self.assertTrue(out.splitlines()[2].startswith('end: failed (`main/g/w` failed: tests red)'), out)
        self.assertIn('- main/g/p: cancelled with its group\n', self.progress())
        self.assertEqual(self.record('main/g/p')['note'], 'cancelled with its group')
        self.assertIn('accepts only', self.fails(rb, 'relaunch', 'main/g/p'))
        self.assertTrue(self.call(rb).startswith('end: failed'), 'the withdrawal is printed once')

    def test_a_side_effect_step_that_ends_in_a_failed_group_is_neither_asked_about_nor_withdrawn(self) -> None:
        def declare(rb: Runbook) -> None:
            strong, _ = executors(rb)
            publish = rb.step('publish', executor=strong, prompt='prompts/a.md', side_effects='publish')
            work = rb.step('work', executor=strong, prompt='prompts/b.md')

            @rb.flow
            def main(ctx):
                yield parallel('g', p=publish(), w=work())
                return end('ready')

        rb = self.runbook(declare)
        self.start(rb)
        self.assertEqual(self.fail_step(rb, 'main/g/w', 'tests red').strip(), 'still running: `main/g/p`')
        out = self.fail_step(rb, 'main/g/p', 'push rejected')
        self.assertTrue(out.startswith('end: failed (`main/g/w` failed: tests red)'), out)
        self.assertIn('- main/g/p: cancelled with its group\n', self.progress())
        self.assertIn('accepts only', self.fails(rb, 'relaunch', 'main/g/p'))


def produce_and_ask(rb: Runbook) -> None:
    strong, _ = executors(rb)
    produce = rb.step('produce', executor=strong, prompt='prompts/a.md', writes=['x.md'])
    ask = rb.human(
        'ask', choices=['Continue', 'stop'], writes='answer.md', question='Read `<run>/x.md`. Continue or stop?'
    )
    go = rb.step('go', executor=strong, prompt='prompts/b.md', reads=['answer.md'])

    @rb.flow
    def main(ctx):
        yield produce()
        a = yield ask()
        if a.choice == 'stop':
            return end('stopped', 'read <run>/x.md')
        yield go()
        return end('ready')


class HumanTest(RunbookTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.rb = self.runbook(produce_and_ask)
        self.start(self.rb)
        self.out = self.reply(self.rb, 'main/produce')

    def test_prints_question_and_choices(self) -> None:
        self.assertEqual(
            self.out.splitlines(),
            [
                f'ask the human (`main/ask`): Read `{self.run_dir}/00-x.md`. Continue or stop?',
                '  choices: Continue | stop. Map the answer to one of them; ask again if none fits.',
                f"  then: {self.cmd} {self.run_dir} answer main/ask '<the choice>' '<their words verbatim, or - to read them from stdin>'",
            ],
        )
        self.assertEqual(self.state()['status'], 'waiting_for_human')
        self.assertEqual(self.record('main/ask')['status'], 'waiting')
        self.assertIn(f'- main/ask: asked: Read `{self.run_dir}/00-x.md`. Continue or stop?\n', self.progress())

    def test_status_repeats_the_wait(self) -> None:
        before = self.state()
        out = self.call(self.rb)
        self.assertEqual(
            out.strip(),
            f'waiting for the human on `main/ask`. Choices: Continue | stop. '
            f"When they answer: {self.cmd} {self.run_dir} answer main/ask '<the choice>' '<their words verbatim, or - to read them from stdin>'.",
        )
        self.assertEqual(self.state(), before)

    def test_answer_ignores_case(self) -> None:
        out = self.call(self.rb, 'answer', 'main/ask', ' CONTINUE ')
        self.assertEqual(
            self.first_line(out), 'launch step `main/go` as a new subagent, executor `strong`: Strong model'
        )
        self.assertEqual(self.record('main/ask')['reply'], {'choice': 'Continue'})
        self.assertIn('- main/ask: answered: Continue\n', self.progress())

    def test_answer_refuses_unknown_choice(self) -> None:
        self.assertIn('answer must be one of: Continue | stop', self.fails(self.rb, 'answer', 'main/ask', 'maybe'))
        self.assertEqual(self.record('main/ask')['status'], 'waiting')
        self.assertIn('call main/ask is waiting, not running', self.fails(self.rb, 'reply', 'main/ask', '{}'))

    def test_verbatim_words_are_kept_and_written(self) -> None:
        self.call(self.rb, 'answer', 'main/ask', 'continue', 'continue, but only touch a3')
        record = self.record('main/ask')
        self.assertEqual((record['reply'], record['answer']), ({'choice': 'Continue'}, 'continue, but only touch a3'))
        with open(os.path.join(self.run_dir, '01-answer.md'), encoding='utf-8') as f:
            self.assertEqual(f.read(), 'continue, but only touch a3\n')
        self.assertEqual(self.reads('main/go'), {'answer.md': f'{self.run_dir}/01-answer.md'})
        self.assertIn('- main/ask: said: continue, but only touch a3\n', self.progress())

    def test_stop_ends_with_the_report(self) -> None:
        out = self.call(self.rb, 'answer', 'main/ask', 'stop')
        self.assertTrue(
            out.strip().endswith(f'status stopped, run directory {self.run_dir}, read {self.run_dir}/00-x.md.')
        )

    def test_reply_fields_are_taken_as_json(self) -> None:
        def declare(rb: Runbook) -> None:
            strong, _ = executors(rb)
            fix = rb.step('fix', executor=strong, prompt='prompts/a.md', reply={'left': int})
            ask = rb.human(
                'ask',
                choices=['more', 'stop'],
                question='More rounds?',
                reply={
                    'rounds': {'type': 'integer', 'minimum': 1, 'default': 1, 'description': 'how many more rounds'}
                },
            )

            @rb.flow
            def main(ctx):
                budget = 1
                while True:
                    r = yield fix()
                    if r.left == 0:
                        return end('ready')
                    budget -= 1
                    if budget == 0:
                        a = yield ask()
                        if a.choice == 'stop':
                            return end('stopped')
                        budget = a.rounds

        rb = self.runbook(declare)
        self.run_dir = self.run_dir + '-fields'
        self.start(rb)
        out = self.reply(rb, 'main/fix', left=2)
        answer = (
            f'{self.cmd} {self.run_dir} answer main/ask \'{{"choice": "<the choice>", '
            f"\"rounds\": <value, if given>}}' '<their words verbatim, or - to read them from stdin>'"
        )
        self.assertEqual(
            out.splitlines(),
            [
                'ask the human (`main/ask`): More rounds?',
                '  choices: more | stop. Map the answer to one of them; ask again if none fits.',
                '  fields: rounds (integer): how many more rounds. Take them from their words; '
                'leave out a field they did not give.',
                f'  then: {answer}',
            ],
        )
        self.assertEqual(
            self.call(rb).strip(),
            'waiting for the human on `main/ask`. Choices: more | stop. '
            f'Fields: rounds (integer): how many more rounds. When they answer: {answer}.',
        )
        self.assertIn('this step takes one JSON object', self.fails(rb, 'answer', 'main/ask', 'more'))
        self.assertIn(
            'field \'rounds\' must be integer, got "two"',
            self.fails(rb, 'answer', 'main/ask', '{"choice": "more", "rounds": "two"}'),
        )
        self.assertIn(
            "field 'round' is not declared", self.fails(rb, 'answer', 'main/ask', '{"choice": "more", "round": 2}')
        )
        self.assertIn('answer must be one of: more | stop', self.fails(rb, 'answer', 'main/ask', '{"choice": "maybe"}'))
        self.assertIn('only one argument can be `-`', self.fails(rb, 'answer', 'main/ask', '-', '-'))
        self.assertEqual(self.record('main/ask')['status'], 'waiting')
        out = self.call(rb, 'answer', 'main/ask', '{"choice": "More", "rounds": 2}', 'two more, then leave me alone')
        self.assertEqual(self.launched(out), ['main/fix#2'])
        record = self.record('main/ask')
        self.assertEqual(
            (record['answer'], record['reply']), ('two more, then leave me alone', {'choice': 'more', 'rounds': 2})
        )
        self.assertIn('- main/ask: answered: more {"rounds": 2}\n', self.progress())
        self.assertEqual(self.launched(self.reply(rb, 'main/fix#2', left=1)), ['main/fix#3'])
        self.assertEqual(self.asked(self.reply(rb, 'main/fix#3', left=1)), ['main/ask#2'])
        out = self.call(rb, 'answer', 'main/ask#2', '{"choice": "more"}')
        self.assertEqual(self.record('main/ask#2')['reply'], {'choice': 'more', 'rounds': 1})
        self.assertEqual(self.launched(out), ['main/fix#4'])


class ReplySchemaTest(RunbookTestCase):
    """Each launch leaves the JSON Schema of the step's reply in <run>/schemas for its executor."""

    def one(self, rb: Runbook, **reply: Any) -> None:
        strong, _ = executors(rb)
        review = rb.step('review', executor=strong, prompt='prompts/a.md', reply=reply)

        @rb.flow
        def main(ctx):
            while True:
                with contextlib.suppress(StepFailed):
                    yield review()

    def test_schema_is_written_from_the_declared_fields(self) -> None:
        rb = self.runbook(
            lambda rb: self.one(
                rb, passed=bool, findings={'type': 'integer', 'minimum': 0, 'description': 'open findings'}
            )
        )
        self.start(rb)
        with open(os.path.join(self.run_dir, 'schemas', '00-review.json'), encoding='utf-8') as f:
            schema = json.load(f)
        self.assertEqual(
            schema,
            {
                'type': 'object',
                'description': agent_runbooks.SCHEMA_ABOUT,
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
        self.assertEqual(self.record('main/review')['schema'], schema)
        out = self.reply(rb, 'main/review', passed=True, findings='none')
        self.assertIn('did not pass the check (field \'findings\' must be integer, got "none")', out)

    def test_nulls_of_the_schema_are_not_recorded(self) -> None:
        rb = self.runbook(lambda rb: self.one(rb, findings=int))
        self.start(rb)
        self.call(rb, 'reply', 'main/review', '{"status": "done", "reason": null, "findings": 2}')
        self.call(rb, 'reply', 'main/review#2', '{"status": "blocked", "reason": "no repo", "findings": null}')
        self.reject(rb, 'main/review#3', '{"status": "done", "reason": null, "findings": null}')
        self.call(rb, 'reply', 'main/review#4', '{"status": "failed", "reason": null, "findings": null}')
        self.assertEqual(
            [(c['status'], c['reply']) for c in self.state()['calls'][:4]],
            [
                ('done', {'findings': 2}),
                ('blocked', {'reason': 'no repo'}),
                ('failed', {'reason': "invalid reply: field 'findings' must be integer, got null"}),
                ('failed', {'reason': 'no reason given'}),
            ],
        )

    def test_field_schemas_without_a_type_and_with_several(self) -> None:
        self.assertEqual(
            agent_runbooks.reply_schema(
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
        rb = self.runbook(lambda rb: self.one(rb, n={'type': ['integer', 'null']}, items=list))
        self.assertEqual(self.check(rb)[0], 0)
        self.start(rb)
        self.reply(rb, 'main/review', n=None, items=[1])
        out = self.reply(rb, 'main/review#2', n='two', items=[])
        self.assertIn('did not pass the check (field \'n\' must be integer or null, got "two")', out)
        self.assertEqual(self.state()['calls'][0]['reply'], {'n': None, 'items': [1]})

    def test_only_the_type_of_a_field_is_checked(self) -> None:
        rb = self.runbook(lambda rb: self.one(rb, findings={'type': 'integer', 'minimum': 0}, verdict={'enum': ['ok']}))
        self.start(rb)
        self.assertEqual(self.launched(self.reply(rb, 'main/review', findings=-1, verdict='so-so')), ['main/review#2'])

    def test_the_check_reads_the_contract_of_the_launch_not_the_current_declaration(self) -> None:
        self.start(self.runbook(lambda rb: self.one(rb, findings=int)))
        changed = self.runbook(lambda rb: self.one(rb, passed=bool))
        self.assertIn(
            "no field 'findings'", self.call(changed, 'reply', 'main/review', '{"status": "done", "passed": true}')
        )
        self.assertEqual(self.launched(self.reply(changed, 'main/review', findings=0)), ['main/review#2'])
        self.assertEqual(self.record('main/review#2')['schema'], agent_runbooks.reply_schema({'passed': bool}))

    def test_a_running_attempt_keeps_its_schema_file_when_a_later_launch_of_its_step_declares_another(self) -> None:
        def declare(fields: dict[str, Any]) -> Callable[[Runbook], None]:
            def declare(rb: Runbook) -> None:
                strong, _ = executors(rb)
                s = rb.step('s', executor=strong, prompt='prompts/a.md', reply=fields)

                def chain(ctx):
                    yield s()
                    yield s()

                @rb.flow
                def main(ctx):
                    yield parallel('g', a=chain, b=s())
                    return end('ready')

            return declare

        self.start(self.runbook(declare({'n': int})))
        changed = self.runbook(declare({'m': int}))
        self.assertEqual(self.launched(self.reply(changed, 'main/g/a/s', n=1)), ['main/g/a/s#2'])
        paths = {label: self.lines(label, 'reply')['schema'] for label in ('main/g/b', 'main/g/a/s#2')}
        self.assertEqual(
            paths,
            {'main/g/b': f'{self.run_dir}/schemas/02-s.json', 'main/g/a/s#2': f'{self.run_dir}/schemas/03-s.json'},
        )
        for label, fields in (('main/g/b', {'n': int}), ('main/g/a/s#2', {'m': int})):
            with open(paths[label]) as f:
                self.assertEqual(json.load(f), agent_runbooks.reply_schema(fields))
        self.reply(changed, 'main/g/b', n=2)
        self.assertEqual(self.record('main/g/b')['reply'], {'n': 2})

    def test_check_takes_property_names_and_literals_for_what_they_are(self) -> None:
        rb = self.runbook(
            lambda rb: self.one(
                rb,
                named={'type': 'object', 'properties': {'$ref': {'type': 'string'}, '$defs': {'type': 'string'}}},
                literal={'const': {'$ref': 'x'}, 'enum': [{'$defs': 1}]},
            )
        )
        self.assertEqual(self.check(rb)[0], 0)

    def test_bad_reply_fields_are_refused_at_declaration(self) -> None:
        cases = {
            "reply field 'findings' is 'int', neither a JSON type (bool, int, float, str, list, dict) nor a JSON Schema": {
                'findings': 'int'
            },
            "reply field 'n' is <class 'object'>, neither a JSON type (bool, int, float, str, list, dict) nor a JSON "
            'Schema': {'n': object},
            "reply field 'kind' has type 'text', not a JSON type name or a list of different ones": {
                'kind': {'type': 'text'}
            },
            "reply field 'odd' has type ['string', {}], not a JSON type name or a list of different ones": {
                'odd': {'type': ['string', {}]}
            },
            "reply field 'twice' has type ['string', 'string'], not a JSON type name or a list of different ones": {
                'twice': {'type': ['string', 'string']}
            },
            "reply field 'none' has type [], not a JSON type name or a list of different ones": {'none': {'type': []}},
            "reply field 'ref' uses $ref or $defs; a field's schema stands alone": {
                'ref': {'type': 'array', 'items': {'anyOf': [{'properties': {'v': {'$ref': '#/x'}}}]}}
            },
        }
        for kind in ('step', 'human'):
            for problem, reply in cases.items():
                with self.subTest(kind=kind, problem=problem):
                    rb = self.runbook(lambda rb: None)
                    with self.assertRaises(TypeError) as caught:
                        if kind == 'step':
                            rb.step('review', executor=executors(rb)[0], prompt='prompts/a.md', reply=reply)
                        else:
                            rb.human('review', question='Proceed?', reply=reply)
                    self.assertEqual(str(caught.exception), f'step review: {problem}')
                    self.assertEqual(rb.steps, {})
        self.assertFalse(os.path.exists(self.run_dir))


class CorrectionTest(RunbookTestCase):
    """A reply that does not pass the check goes back to its executor once before the step is failed."""

    def declare(self, rb: Runbook) -> None:
        _, light = executors(rb)
        checks = rb.step('checks', executor=light, prompt='prompts/a.md', reply={'passed': bool})

        @rb.flow
        def main(ctx):
            try:
                r = yield checks()
            except StepFailed:
                return end('broken')
            return end('ready' if r.passed else 'red')

    def test_asks_for_a_correction_and_takes_the_corrected_reply(self) -> None:
        rb = self.runbook(self.declare)
        self.start(rb)
        out = self.call(rb, 'reply', 'main/checks', '{"status": "done", "passed": "yes"}')
        problem = 'field \'passed\' must be boolean, got "yes"'
        self.assertEqual(
            out.splitlines(),
            [
                f'the reply of step `main/checks` did not pass the check ({problem}). Send this to the subagent that ran it, '
                f'as a follow-up message in its session, not as a new launch:',
                '--- message ---',
                f'Your final message did not pass the check: {problem}. Do not redo the step and change nothing. '
                f'Reply with one JSON object that fits {self.run_dir}/schemas/00-checks.json for the work already done, '
                f'and nothing else. If the work is not done, reply failed with a one-line reason.',
                '--- end of message ---',
                f"when it answers: {self.cmd} {self.run_dir} reply main/checks '<the last JSON object of its message>'",
                f'if your tool cannot send a message to a subagent that has finished: {self.cmd} {self.run_dir} reply '
                f'main/checks \'{{"status": "failed", "reason": "invalid reply, and its executor could not be asked again"}}\'',
                '',
                'still running: `main/checks`',
            ],
        )
        record = self.record('main/checks')
        self.assertEqual((record['status'], record['reply'], record['ended_at']), ('running', None, None))
        self.assertEqual(
            record['invalid_replies'], [{'reply': '{"status": "done", "passed": "yes"}', 'problem': problem}]
        )
        self.assertEqual(self.call(rb).strip(), 'still running: `main/checks`')
        out = self.reply(rb, 'main/checks', passed=True)
        self.assertTrue(out.startswith('end: ready (after `main/checks`)'))
        self.assertEqual(self.record('main/checks')['reply'], {'passed': True})
        self.assertIn(
            f'- main/checks: invalid reply ({problem}): {{"status": "done", "passed": "yes"}}\n'
            f'- main/checks: its executor is asked to correct the reply\n'
            f'- main/checks: {{"status": "done", "passed": true}}\n',
            self.progress(),
        )

    def test_a_second_invalid_reply_fails_the_step_and_keeps_both(self) -> None:
        rb = self.runbook(self.declare)
        self.start(rb)
        out = self.reject(rb, 'main/checks', '{"status": "done"}')
        self.assertTrue(out.startswith('end: broken (after `main/checks`)'))
        record = self.record('main/checks')
        self.assertEqual(
            (record['status'], record['reply']), ('failed', {'reason': "invalid reply: no field 'passed'"})
        )
        self.assertEqual([r['reply'] for r in record['invalid_replies']], ['{"status": "done"}'] * 2)
        with open(os.path.join(self.run_dir, 'schemas', '00-checks.json')) as f:
            self.assertEqual(json.load(f), agent_runbooks.reply_schema({'passed': bool}))

    def test_executor_that_cannot_be_asked_again(self) -> None:
        rb = self.runbook(self.declare)
        self.start(rb)
        self.call(rb, 'reply', 'main/checks', '{}')
        self.call(
            rb,
            'reply',
            'main/checks',
            '{"status": "failed", "reason": "invalid reply, and its executor could not be asked again"}',
        )
        self.assertEqual(
            self.record('main/checks')['reply']['reason'], 'invalid reply, and its executor could not be asked again'
        )

    def test_a_side_effect_step_is_corrected_before_the_human_is_asked(self) -> None:
        def declare(rb: Runbook) -> None:
            strong, _ = executors(rb)
            commit = rb.step(
                'commit', executor=strong, prompt='prompts/a.md', reply={'sha': str}, side_effects='commit'
            )

            @rb.flow
            def main(ctx):
                yield commit()
                return end('ready')

        rb = self.runbook(declare)
        self.start(rb)
        self.assertIn('did not pass the check', self.first_line(self.reply(rb, 'main/commit')))
        self.assertTrue(
            self.reply(rb, 'main/commit').startswith(
                "ask the human: step `main/commit` has side effects and ended failed (invalid reply: no field 'sha')."
            )
        )

    def test_the_budget_survives_a_resume_and_a_new_attempt_gets_its_own(self) -> None:
        rb = self.runbook(self.declare)
        self.start(rb)
        self.call(rb, 'reply', 'main/checks', 'garbage')
        fresh = self.runbook(self.declare)
        self.assertEqual(self.call(fresh).strip(), 'still running: `main/checks`')
        self.call(fresh, 'interrupted', 'main/checks')
        self.assertIn('did not pass the check', self.call(fresh, 'reply', 'main/checks@2', 'garbage'))


class TimingTest(RunbookTestCase):
    """Each record notes its executor, when it was opened and when it left an open status."""

    def fields(self) -> list[tuple[str, int, str | None, str | None, str | None]]:
        return [
            (c['id'], c['attempt'], c.get('executor'), c['started_at'], c['ended_at']) for c in self.state()['calls']
        ]

    def test_launch_reply_and_interruption(self) -> None:
        rb = self.runbook(linear)
        self.start(rb)
        self.assertEqual(self.fields(), [('main/first', 1, 'light', '2026-10-03T10:00:00Z', None)])
        self.call(rb, 'interrupted', 'main/first')
        self.reply(rb, 'main/first@2')
        self.assertEqual(
            self.fields(),
            [
                ('main/first', 1, 'light', '2026-10-03T10:00:00Z', '2026-10-03T10:01:00Z'),
                ('main/first', 2, 'light', '2026-10-03T10:02:00Z', '2026-10-03T10:03:00Z'),
                ('main/second', 1, 'strong', '2026-10-03T10:04:00Z', None),
            ],
        )

    def test_answer(self) -> None:
        rb = self.runbook(produce_and_ask)
        self.start(rb)
        self.reply(rb, 'main/produce')
        self.call(rb, 'answer', 'main/ask', 'Continue')
        self.assertEqual(
            self.fields()[1:],
            [
                ('main/ask', 1, None, '2026-10-03T10:02:00Z', '2026-10-03T10:03:00Z'),
                ('main/go', 1, 'strong', '2026-10-03T10:04:00Z', None),
            ],
        )


# ---------- flow.py ----------


class FlowTest(RunbookTestCase):
    def test_a_flow_changed_under_a_run_is_refused(self) -> None:
        self.start(self.runbook(linear))
        before = self.state()

        def declare(rb: Runbook) -> None:
            strong, _ = executors(rb)
            other = rb.step('other', executor=strong, prompt='prompts/a.md')
            rb.step('first', executor=strong, prompt='prompts/a.md')

            @rb.flow
            def main(ctx):
                yield parallel('first', x=other())
                return end('ready')

        err = self.fails(self.runbook(declare))
        self.assertIn(
            '`main/first` is recorded as step `first`, and the flow yields parallel `first` there: '
            'flow.py changed under this run. Start a new run.',
            err,
        )
        self.assertEqual(self.state(), before)

    def test_an_author_s_bug_keeps_the_reply_and_the_run_resumes_after_the_fix(self) -> None:
        def declare(rb: Runbook, broken: bool) -> None:
            strong, light = executors(rb)
            first = rb.step('first', executor=light, prompt='prompts/a.md', reply={'n': int})
            second = rb.step('second', executor=strong, prompt='prompts/b.md')

            @rb.flow
            def main(ctx):
                r = yield first()
                yield second(share=1 / r.n if broken else 0)
                return end('ready')

        broken = self.runbook(lambda rb: declare(rb, True))
        self.start(broken)
        err = self.fails(broken, 'reply', 'main/first', '{"status": "done", "n": 0}')
        self.assertIn('flow.py: the flow raised ZeroDivisionError: division by zero at `main` (line ', err)
        self.assertIn(
            f'What you passed is recorded. This is a defect in flow.py: report it to the human and stop. '
            f'Once it is fixed, the run goes on with: {self.cmd} {self.run_dir}',
            err,
        )
        self.assertEqual(self.record('main/first')['status'], 'done')
        self.assertEqual(len(self.state()['calls']), 1)
        fixed = self.runbook(lambda rb: declare(rb, False))
        self.assertEqual(self.launched(self.call(fixed)), ['main/second'])

    def test_a_copy_of_a_result_passed_to_a_call_is_a_defect_of_flow_py(self) -> None:
        def declare(rb: Runbook) -> None:
            strong, _ = executors(rb)
            work = rb.step('work', executor=strong, prompt='prompts/a.md', writes=['out.md'])
            use = rb.step('use', executor=strong, prompt='prompts/b.md')

            @rb.flow
            def main(ctx):
                r = yield work()
                yield use(previous=copy.copy(r))
                return end('ready')

        rb = self.runbook(declare)
        self.start(rb)
        for name, path in self.lines('main/work', 'write').items():
            with open(path, 'w', encoding='utf-8') as f:
                f.write(name)
        err = self.fails(rb, 'reply', 'main/work', '{"status": "done"}')
        self.assertIn("`main/use`: input 'previous' is StepResult(), not a result a yield returned", err)
        self.assertIn('What you passed is recorded. This is a defect in flow.py', err)
        self.assertNotIn('Traceback', err)
        self.assertEqual(self.record('main/work')['status'], 'done')
        self.assertEqual(len(self.state()['calls']), 1)

    def test_what_the_flow_yields_passes_and_returns_is_checked(self) -> None:
        def flows(rb: Runbook) -> dict[str, Callable[..., Any]]:
            strong, _ = executors(rb)
            work = rb.step('work', executor=strong, prompt='prompts/a.md')
            foreign = Runbook().executor('strong', 'Strong model')

            def forgot_the_call(ctx):
                yield work

            def yields_a_value(ctx):
                yield 5

            def returns_a_value(ctx):
                yield from ()
                return 'ready'

            def passes_an_object(ctx):
                yield work(thing=object())

            def an_undeclared_executor(ctx):
                yield work(executor='ghost')

            def a_foreign_executor(ctx):
                yield work(executor=foreign)

            def a_bad_branch(ctx):
                yield parallel('group', a='work')  # type: ignore[arg-type]

            def a_branch_key_with_a_slash(ctx):
                yield parallel('group', **{'a/work': work(), 'a': work()})

            def a_bad_group_name(ctx):
                yield parallel('a/b', a=work())

            def a_plain_function_body(ctx):
                yield foreach('items', over='items.json', body=lambda ctx, item: None)  # type: ignore[arg-type]

            return {
                'yields step `work` without calling it: yield work()': forgot_the_call,
                'yields 5: a flow yields a step call, parallel(...) or foreach(...)': yields_a_value,
                "the flow returned 'ready'; it ends with `return end(<status>, <report>)`": returns_a_value,
                "`main/work`: input 'thing' is <object object": passes_an_object,
                "`main/work`: executor 'ghost' is not declared in this flow.py": an_undeclared_executor,
                "`main/work`: executor Executor(name='strong', description='Strong model') is not declared in this "
                'flow.py': a_foreign_executor,
                "parallel('group'): branch 'a' is 'work', neither a step call nor a generator function": a_bad_branch,
                "parallel('group'): branch 'a/work': the name must be letters, digits, _ . -": a_branch_key_with_a_slash,
                "parallel('a/b'): the name must be letters, digits, _ . -": a_bad_group_name,
                "foreach('items'): body is <function": a_plain_function_body,
            }

        for problem in flows(self.runbook(lambda rb: None)):
            with self.subTest(problem=problem):

                def declare(rb: Runbook, problem: str = problem) -> None:
                    rb.flow(flows(rb)[problem])

                self.run_dir = os.path.join(self.root, f'run-{len(os.listdir(self.root))}')
                err = self.fails(self.runbook(declare), 'start', '{"repo": "/repo"}')
                self.assertIn(problem, err)
                self.assertIn('This is a defect in flow.py', err)
                self.assertEqual(self.state()['calls'], [])

    def test_a_declared_file_the_launch_did_not_write_is_not_recorded_as_written(self) -> None:
        def declare(rb: Runbook) -> None:
            strong, _ = executors(rb)
            work = rb.step(
                'work', executor=strong, prompt='prompts/a.md', reads=['notes.md'], writes=['notes.md', 'log.md']
            )
            report = rb.step('report', executor=strong, prompt='prompts/b.md')

            @rb.flow
            def main(ctx):
                first = yield work()
                second = yield work()
                yield work()
                yield report(first=first, second=second)
                return end('ready')

        rb = self.runbook(declare)
        self.start(rb)
        self.reply(rb, 'main/work')
        log_path = self.lines('main/work#2', 'write')['log.md']
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write('log\n')
        self.call(rb, 'reply', 'main/work#2', '{"status": "done"}')
        self.assertEqual(self.record('main/work#2')['files'], {'log.md': log_path})
        self.assertIn('- main/work#2: {"status": "done"} (did not write notes.md)\n', self.progress())
        self.assertEqual(self.reads('main/work#3')['notes.md'], f'{self.run_dir}/00-notes.md')
        self.reply(rb, 'main/work#3')
        self.assertEqual(
            self.reads('main/report'),
            {
                'first/notes.md': f'{self.run_dir}/00-notes.md',
                'first/log.md': f'{self.run_dir}/00-log.md',
                'second/log.md': log_path,
            },
        )

    def test_a_replay_of_600_records_takes_well_under_a_second(self) -> None:
        rb = self.runbook(review_loop, brief=str, maxFixRounds=1000)
        self.start(rb, brief='brief.md')
        state = agent_runbooks.RunState.load(self.run_dir)
        state.calls = []
        names = ['implement'] + ['review', 'fix'] * 300
        counts: dict[str, int] = {}
        for name in names:
            counts[name] = counts.get(name, 0) + 1
            address = f'main/{name}' + (f'#{counts[name]}' if counts[name] > 1 else '')
            record = agent_runbooks.CallRecord(id=address, kind='step', step=name, status='done', executor='strong')
            record.reply = {'findings': 1} if name == 'review' else {}
            state.append(record)
            record.files = record.writes = {
                f'{name}.md': os.path.join(self.run_dir, f'{record.position:02d}-{name}.md')
            }
        state.save(self.run_dir)
        began = time.perf_counter()
        out = self.call(rb)
        elapsed = time.perf_counter() - began
        self.assertEqual(self.launched(out), ['main/review#301'])
        self.assertEqual(self.reads('main/review#301')['review.md'], f'{self.run_dir}/599-review.md')
        self.assertLess(elapsed, 0.5)

    def test_a_command_is_one_replay_however_many_groups_it_opens_and_closes(self) -> None:
        def declare(rb: Runbook) -> None:
            _, light = executors(rb)
            plan = rb.step('plan', executor=light, prompt='prompts/a.md', writes=['items.json'])

            def body(ctx, item):
                yield from ()
                return {'ok': True}

            def trivial(ctx):
                yield from ()
                return 'leaf'

            def nest(depth: int) -> Callable[[Any], Any]:
                def chain(ctx):
                    if depth == 0:
                        return 'leaf'
                    r = yield parallel('p', a=nest(depth - 1), b=trivial)
                    return r.b

                return chain

            @rb.flow
            def main(ctx):
                if ctx.inputs.kind == 'parallel':
                    yield from nest(300)(ctx)
                    return end('ready')
                yield plan()
                done = yield foreach('items', over='items.json', body=body, max_items=600)
                return end('ready', f'{sum(item.reply["ok"] for item in done.items)} done, <run>/items.index')

        def timed(rb: Runbook, *args: str) -> tuple[str, int, float]:
            with mock.patch.object(
                agent_runbooks.Replay, 'run', autospec=True, side_effect=agent_runbooks.Replay.run
            ) as replays:
                began = time.perf_counter()
                out = self.call(rb, *args)
                return out, replays.call_count, time.perf_counter() - began

        self.run_dir = os.path.join(self.root, 'run-foreach')
        rb = self.runbook(declare, kind=str)
        self.start(rb, kind='foreach')
        items = json.dumps([{'key': f'i{i}'} for i in range(600)])
        for path in self.lines('main/plan', 'write').values():
            with open(path, 'w') as f:
                f.write(items)
        out, replays, elapsed = timed(rb, 'reply', 'main/plan', '{"status": "done"}')
        self.assertTrue(out.startswith('end: ready'), out)
        self.assertIn(f'600 done, {self.run_dir}/01-items.index.json', out)
        with open(f'{self.run_dir}/01-items.index.json') as f:
            self.assertEqual(len(json.load(f)), 600)
        self.assertLessEqual(replays, 3)
        self.assertLess(elapsed, 1)

        self.run_dir = os.path.join(self.root, 'run-parallel')
        out, replays, elapsed = timed(self.runbook(declare, kind=str), 'start', '{"repo": "/repo", "kind": "parallel"}')
        self.assertTrue(out.startswith('end: ready'), out)
        self.assertEqual(sum(c['kind'] == 'parallel' and c['status'] == 'done' for c in self.state()['calls']), 300)
        self.assertLessEqual(replays, 3)
        self.assertLess(elapsed, 1)


class ResultTest(RunbookTestCase):
    def test_files_and_address_read_every_kind_of_result_and_nothing_else_is_on_it(self) -> None:
        seen: dict[str, Any] = {}

        def declare(rb: Runbook) -> None:
            strong, light = executors(rb)
            plan = rb.step('plan', executor=light, prompt='prompts/a.md', writes=['sites.json'], reply={'n': int})
            ask = rb.human('ask', choices=['go'], writes='answer.md', question='Go?')
            review = rb.step('review', executor=strong, prompt='prompts/b.md', writes=['review.md'])
            migrate = rb.step('migrate', executor=strong, prompt='prompts/c.md', writes=['migrated.md'])

            def chain(ctx):
                yield from ()
                return 'chained'

            @rb.flow
            def main(ctx):
                seen['step'] = yield plan()
                seen['human'] = yield ask()
                seen['parallel'] = yield parallel('reviews', a=review(), b=chain)
                seen['foreach'] = yield foreach('sites', over='sites.json', body=migrate())
                seen['item'] = seen['foreach'].items[0]
                return end('ready')

        rb = self.runbook(declare)
        self.start(rb)
        self.reply(rb, 'main/plan', {'sites.json': '[{"key": "a"}]'}, n=1)
        self.call(rb, 'answer', 'main/ask', 'go')
        self.reply(rb, 'main/reviews/a')
        out = self.reply(rb, 'main/sites[a]/migrate')
        self.assertTrue(out.startswith('end: ready'), out)
        run = self.run_dir
        expected = {
            'step': ('main/plan', {'sites.json': f'{run}/00-sites.json'}, {'n': 1}),
            'human': ('main/ask', {'answer.md': f'{run}/01-answer.md'}, {'choice': 'go'}),
            'parallel': ('main/reviews', {}, {'a', 'b'}),
            'foreach': ('main/sites', {'sites.index': f'{run}/04-sites.index.json'}, {'items'}),
            'item': ('main/sites[a]', {'migrated.md': f'{run}/06-migrated.md'}, {'key', 'status', 'reply', 'reason'}),
        }
        for kind, (where, written, attributes) in expected.items():
            with self.subTest(kind=kind):
                result = seen[kind]
                self.assertEqual(address(result), where)
                self.assertEqual(files(result), written)
                if isinstance(attributes, dict):
                    self.assertEqual(vars(result), attributes)
                else:
                    self.assertEqual(set(vars(result)), attributes)
        self.assertEqual(seen['parallel'].b, 'chained')
        self.assertEqual(address(seen['parallel'].a), 'main/reviews/a')
        files(seen['step'])['sites.json'] = 'changed'
        self.assertEqual(files(seen['step']), {'sites.json': f'{run}/00-sites.json'})

    def test_anything_but_a_result_is_refused(self) -> None:
        for value in (5, None, {'id': 'main/plan', 'files': {}}, StepFailed('x'), agent_runbooks.StepResult(n=1)):
            for function in (files, address):
                with self.subTest(value=value, function=function.__name__):
                    with self.assertRaises(TypeError) as caught:
                        function(value)  # type: ignore[arg-type]
                    self.assertEqual(
                        str(caught.exception),
                        f'{function.__name__}() takes what a yield returned or an item of a foreach, got {value!r}',
                    )

    def test_id_files_and_a_leading_underscore_work_as_reply_fields_and_branch_keys(self) -> None:
        seen: dict[str, Any] = {}

        def declare(rb: Runbook) -> None:
            strong, _ = executors(rb)
            names = {'id': str, 'files': list, '_x': int}
            work = rb.step('work', executor=strong, prompt='prompts/a.md', writes=['out.md'], reply=names)
            ask = rb.human('ask', choices=['go'], question='Go?', reply=names)
            use = rb.step('use', executor=strong, prompt='prompts/b.md')

            @rb.flow
            def main(ctx):
                r = yield work()
                a = yield ask()
                g = yield parallel('group', id=work(), files=work(), _x=work())
                seen.update(r=r, a=a, g=g)
                yield use(r=r, g=g)
                return end('ready', f'{r.id} {r.files} {r._x} {a.id} {g.id.id} {g.files._x} {g._x.files}')

        rb = self.runbook(declare)
        self.start(rb)
        self.reply(rb, 'main/work', id='w', files=['f'], _x=1)
        answer = json.dumps({'choice': 'go', 'id': 'h', 'files': [], '_x': 2})
        out = self.call(rb, 'answer', 'main/ask', answer, 'go')
        self.assertEqual(self.launched(out), ['main/group/id', 'main/group/files', 'main/group/_x'])
        for key, n in (('id', 3), ('files', 4), ('_x', 5)):
            out = self.reply(rb, f'main/group/{key}', id=key, files=[key], _x=n)
        self.assertEqual(self.launched(out), ['main/use'])
        self.assertEqual(address(seen['r']), 'main/work')
        self.assertEqual(files(seen['r']), {'out.md': f'{self.run_dir}/00-out.md'})
        self.assertEqual(vars(seen['a']), {'choice': 'go', 'id': 'h', 'files': [], '_x': 2})
        self.assertEqual(address(seen['g']), 'main/group')
        self.assertEqual(address(seen['g'].files), 'main/group/files')
        self.assertEqual(
            self.record('main/use')['inputs'],
            {
                'r.id': 'w',
                'r.files': ['f'],
                'r._x': 1,
                **{
                    f'g.{key}.{name}': value
                    for key, n in (('id', 3), ('files', 4), ('_x', 5))
                    for name, value in (('id', key), ('files', [key]), ('_x', n))
                },
            },
        )
        self.assertEqual(
            self.reads('main/use'),
            {
                'r/out.md': f'{self.run_dir}/00-out.md',
                'g.id/out.md': f'{self.run_dir}/03-out.md',
                'g.files/out.md': f'{self.run_dir}/04-out.md',
                'g._x/out.md': f'{self.run_dir}/05-out.md',
            },
        )
        self.assertIn('r.files: ["f"]', self.messages['main/use'])
        out = self.reply(rb, 'main/use')
        self.assertTrue(out.strip().endswith(", w ['f'] 1 h id 4 ['_x']."), out)


class DeclarationTest(RunbookTestCase):
    def declare(self, rb: Runbook, kind: str, name: str, **kwargs: Any) -> None:
        strong, _ = executors(rb)
        if kind == 'step':
            rb.step(name, executor=strong, prompt='prompts/a.md', **kwargs)
        else:
            rb.human(name, question='Proceed?', **kwargs)

    def test_collection_parameters_refuse_strings_at_declaration(self) -> None:
        for kind, parameters in (('step', ('inputs', 'reads', 'writes')), ('human', ('choices',))):
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

    def test_mistakes_are_refused_naming_the_step_and_the_parameter(self) -> None:
        cases = [
            ('step', 'a/b', {}, ValueError, "step 'a/b': the name must be letters, digits, _ . -"),
            ('step', 'w#2', {}, ValueError, "step 'w#2': the name must be"),
            ('step', 'work', {'reply': {'status': str}}, ValueError, "step 'work': reply field 'status' is reserved"),
            ('step', 'work', {'reply': {'reason': str}}, ValueError, "step 'work': reply field 'reason' is reserved"),
            ('human', 'ask', {'reply': {'status': str}}, ValueError, "step 'ask': reply field 'status' is reserved"),
            ('human', 'ask', {'reply': {'reason': str}}, ValueError, "step 'ask': reply field 'reason' is reserved"),
            ('human', 'ask', {'reply': {'choice': str}}, ValueError, "step 'ask': reply field 'choice' is reserved"),
            (
                'step',
                'work',
                {'inputs': [3]},
                TypeError,
                "step 'work': inputs item 3 is neither a name nor a (key, value) pair",
            ),
            ('step', 'work', {'reads': [5]}, TypeError, "step 'work': reads takes strings, got 5"),
            ('step', 'work', {'writes': ['']}, TypeError, "step 'work': writes takes strings, got ''"),
            ('step', 'work', {'side_effects': 1}, TypeError, "step 'work': side_effects takes strings, got 1"),
            ('human', 'ask', {'choices': [1]}, TypeError, "step 'ask': choices takes strings, got 1"),
            ('human', 'ask', {'writes': 2}, TypeError, "step 'ask': writes takes strings, got 2"),
        ]
        for kind, name, kwargs, error, message in cases:
            with self.subTest(message=message):
                rb = self.runbook(lambda rb: None)
                with self.assertRaises(error) as caught:
                    self.declare(rb, kind, name, **kwargs)
                self.assertIn(message, str(caught.exception))
                self.assertEqual(rb.steps, {})
        rb = self.runbook(lambda rb: None)
        with self.assertRaisesRegex(TypeError, "step 'work': prompt takes strings, got 3"):
            rb.step('work', executor=executors(rb)[0], prompt=3)  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "step 'ask': question takes strings, got None"):
            rb.human('ask', question=None)  # type: ignore[arg-type]
        self.assertEqual(rb.steps, {})

    def test_a_step_takes_an_executor_this_runbook_returned(self) -> None:
        rb = self.runbook(lambda rb: None)
        foreign = Runbook().executor('strong', 'Strong model')
        for executor in ('strong', foreign, None):
            with self.subTest(executor=executor):
                with self.assertRaises(TypeError) as caught:
                    rb.step('work', executor=executor, prompt='prompts/a.md')  # type: ignore[arg-type]
                self.assertEqual(
                    str(caught.exception),
                    f"step 'work': executor is {executor!r}, not what executor() of this flow.py returned",
                )
                self.assertEqual(rb.steps, {})

    def test_executor_names_are_refused_as_step_names_are(self) -> None:
        rb = self.runbook(lambda rb: None)
        strong, _ = executors(rb)
        self.assertEqual(strong, agent_runbooks.Executor('strong', 'Strong model'))
        cases = [
            ('strong', 'Other model', ValueError, "executor 'strong' is already declared"),
            ('a/b', 'Other model', ValueError, "executor 'a/b': the name must be letters, digits, _ . -"),
            ('', 'Other model', ValueError, "executor '': the name must be"),
            ('other', None, TypeError, "executor 'other': description takes a string, got None"),
        ]
        for name, description, error, message in cases:
            with self.subTest(message=message):
                with self.assertRaises(error) as caught:
                    rb.executor(name, description)  # type: ignore[arg-type]
                self.assertIn(message, str(caught.exception))
        self.assertEqual(list(rb.executors), ['strong', 'light'])
        self.assertIs(rb.executors['strong'], strong)

    def test_the_flow_is_one_generator_function(self) -> None:
        rb = self.runbook(lambda rb: None)
        with self.assertRaisesRegex(TypeError, '@rb.flow: plain is not a generator function'):

            @rb.flow  # type: ignore[arg-type]
            def plain(ctx):
                return end('ready')

        @rb.flow
        def main(ctx):
            yield from ()
            return end('ready')

        with self.assertRaisesRegex(ValueError, '@rb.flow: main is already the flow'):

            @rb.flow
            def other(ctx):
                yield from ()
                return end('ready')

    def test_iterables_and_human_writes_remain_supported(self) -> None:
        rb = self.runbook(lambda rb: None)
        strong, _ = executors(rb)
        work = rb.step(
            'work',
            executor=strong,
            prompt='prompts/a.md',
            inputs=iter(['repo', ('rounds', 2)]),
            reads=('brief.md',),
            writes=iter(['out.md']),
        )
        rb.human('ask', question='Proceed?', choices=iter(['yes', 'no']), writes='words.md')

        @rb.flow
        def main(ctx):
            yield work()
            return end('ready')

        self.assertEqual(self.check(rb), (0, 'flow.py is consistent.\n'))
        self.assertEqual(work.inputs, ['repo', ('rounds', 2)])
        self.assertEqual(work.writes, ['out.md'])


class CheckTest(RunbookTestCase):
    def test_consistent(self) -> None:
        self.assertEqual(self.check(self.runbook(linear)), (0, 'flow.py is consistent.\n'))

    def test_finds_problems(self) -> None:
        with mock.patch.object(sys, 'argv', [os.path.join(self.here, 'flow.py')]):
            rb = Runbook()
        rb.inputs(ticket=str)
        strong = rb.executor('strong', 'Strong model')
        rb.step('ghostly', executor=strong, prompt='prompts/missing.md', inputs=['ticket', 'nope'])
        rb.human('ask', question='')
        os.remove(os.path.join(self.here, 'prompts', 'common.md'))
        code, out = self.check(rb)
        self.assertEqual(code, 1)
        self.assertEqual(
            out.splitlines(),
            [
                "inputs: 'repo' is not declared",
                'no flow: decorate the generator function of the flow with @rb.flow',
                'step ghostly: prompts/missing.md does not exist',
                "step ghostly: input 'nope' is not a declared run input",
                'step ask: human step without a question',
                'prompts/common.md does not exist',
            ],
        )
        self.assertIn('no flow: decorate the generator function', self.fails(rb, 'start', '{"repo": "/repo"}'))


class StateSaveTest(RunbookTestCase):
    def test_serialization_error_keeps_the_previous_state_whole(self) -> None:
        rb = self.runbook(linear)
        self.start(rb)
        path = agent_runbooks.RunState.path(self.run_dir)
        with open(path, 'rb') as f:
            before = f.read()
        state = agent_runbooks.RunState.load(self.run_dir)
        state.inputs['bad'] = object()
        with self.assertRaisesRegex(agent_runbooks.FlowError, 'state.json cannot hold what the flow gave'):
            state.save(self.run_dir)
        with open(path, 'rb') as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(agent_runbooks.RunState.load(self.run_dir).inputs, {'repo': '/repo'})

    def test_save_overwrites_a_leftover_temporary_file(self) -> None:
        rb = self.runbook(linear)
        self.start(rb)
        path = agent_runbooks.RunState.path(self.run_dir)
        with open(path + '.tmp', 'w') as f:
            f.write('interrupted write')
        state = agent_runbooks.RunState.load(self.run_dir)
        state.inputs['repo'] = '/new'
        state.save(self.run_dir)
        self.assertEqual(agent_runbooks.RunState.load(self.run_dir).inputs['repo'], '/new')
        self.assertFalse(os.path.exists(path + '.tmp'))


class ShellCommandTest(RunbookTestCase):
    def test_printed_commands_preserve_addresses_and_paths(self) -> None:
        here = self.here + ' with spaces'
        os.rename(self.here, here)
        self.here = here
        self.run_dir += " with spaces and a '"
        executable = "/tmp/python bin/py'thon"

        def declare(rb: Runbook) -> None:
            strong, _ = executors(rb)
            work = rb.step('work', executor=strong, prompt='prompts/a.md')
            ask = rb.human('ask', question='Proceed?', choices=['yes', 'no'])

            @rb.flow
            def main(ctx):
                yield work()
                yield foreach('items', over='items.json', body=ask())
                yield work()
                return end('ready', str(1 / 0))

        with mock.patch.object(sys, 'executable', executable):
            rb = self.runbook(declare)
        os.makedirs(self.run_dir)
        self.write_input('items.json', [{'key': 'x'}])
        prefix = [executable, os.path.join(self.here, 'flow.py'), self.run_dir]
        out = self.start(rb)
        command = next(
            line.removeprefix('when it finishes: ') for line in out.splitlines() if line.startswith('when it')
        )
        self.assertEqual(shlex.split(command), [*prefix, 'reply', 'main/work', '<the last JSON object of its message>'])
        out = self.reply(rb, 'main/work')
        command = next(line.removeprefix('  then: ') for line in out.splitlines() if line.startswith('  then: '))
        self.assertEqual(
            shlex.split(command),
            [
                *prefix,
                'answer',
                'main/items[x]/ask',
                '<the choice>',
                '<their words verbatim, or - to read them from stdin>',
            ],
        )
        self.call(rb, 'answer', 'main/items[x]/ask', 'yes')
        out = self.call(rb, 'interrupted', 'main/work#2')
        command = next(
            line.removeprefix('when it finishes: ') for line in out.splitlines() if line.startswith('when it')
        )
        self.assertEqual(
            shlex.split(command), [*prefix, 'reply', 'main/work#2@2', '<the last JSON object of its message>']
        )
        err = self.fails(rb, 'reply', 'main/work#2@2', '{"status": "done"}')
        command = err.split('Once it is fixed, the run goes on with: ', 1)[1].strip()
        self.assertEqual(shlex.split(command), prefix)

    def test_under_uv_run_commands_go_through_uv(self) -> None:
        def declare(rb: Runbook) -> None:
            strong, _ = executors(rb)
            work = rb.step('work', executor=strong, prompt='prompts/a.md')

            @rb.flow
            def main(ctx):
                yield work()
                return end('done')

        os.environ['UV'] = '/usr/local/bin/uv'
        rb = self.runbook(declare)
        self.assertIn(
            f"when it finishes: uv run {self.here}/flow.py {self.run_dir} reply main/work '<the last JSON object of its message>'",
            self.start(rb),
        )


if __name__ == '__main__':
    unittest.main()
