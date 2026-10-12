#!/usr/bin/env python3
"""A flow with a parallel in each item of a foreach, for the viewer's fixtures; make.py drives it. Not a runbook to
install."""

from agent_runbooks import Runbook, end, foreach, parallel

rb = Runbook()

rb.inputs(brief=str, repo=str)

SUBAGENT = 'general-purpose, on the model of the main session'
coder = rb.executor('coder', SUBAGENT)
reviewer = rb.executor('reviewer', SUBAGENT)

plan = rb.step(
    'plan',
    executor=coder,
    prompt='prompts/01-plan.md',
    reads=['brief.md', 'working tree'],
    writes=['modules.json'],
)

port = rb.step(
    'port',
    executor=coder,
    prompt='prompts/02-port.md',
    reads=['brief.md', 'working tree'],
    writes=['port.md'],
)

lint = rb.step(
    'lint',
    executor=reviewer,
    prompt='prompts/03-lint.md',
    reads=['working tree'],
    reply={'warnings': int},
)

test = rb.step(
    'test',
    executor=reviewer,
    prompt='prompts/04-test.md',
    reads=['working tree'],
    writes=['test.md'],
    reply={'passed': bool},
)

fix = rb.step(
    'fix',
    executor=coder,
    prompt='prompts/05-fix.md',
    reads=['test.md', 'working tree'],
    writes=['fix.md'],
)


def tests(ctx):
    r = yield test()
    if not r.passed:
        yield fix()
        r = yield test()
    return r


def port_module(ctx, item):
    yield port()
    checks = yield parallel('checks', lint=lint(), tests=tests)
    return {'warnings': checks.lint.warnings, 'passed': checks.tests.passed}


@rb.flow
def main(ctx):
    yield plan()
    yield foreach('modules', over='modules.json', body=port_module, max_concurrent=2)
    return end('ready', 'read <run>/modules.index')


if __name__ == '__main__':
    raise SystemExit(rb.main())
