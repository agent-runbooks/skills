#!/usr/bin/env python3
"""A flow of code steps around an agent step, for the viewer's fixtures; make.py drives it. Not a runbook to install."""

from agent_runbooks import Runbook, StepFailed, end

rb = Runbook()

rb.inputs(brief=str, repo=str)

coder = rb.executor('coder', 'general-purpose, on the model of the main session')


def collect_changes(ctx):
    print('git log v1.4.0..HEAD: 3 commits')
    with open(ctx.writes['changes.md'], 'w', encoding='utf-8') as f:
        f.write('# Changes since v1.4.0\n\n- slugify takes max_length\n- slugify transliterates Cyrillic\n- docs\n')
    return {'status': 'done', 'commits': 3}


def run_lint(ctx):
    print('ruff check src/')
    limits = {'max-line-length': 120}
    return {'status': 'done', 'warnings': limits['max-warnings']}


def build_wheel(ctx):
    print('uv build: dist/textkit-1.5.0-py3-none-any.whl')
    return {'status': 'done', 'wheel': 'dist/textkit-1.5.0-py3-none-any.whl'}


def run_tests(ctx):
    print('python3 -m unittest')
    return {'status': 'done', 'passed': True}


changes = rb.code('changes', run=collect_changes, writes=['changes.md'], reply={'commits': int})
lint = rb.code('lint', run=run_lint, reply={'warnings': int})
build = rb.code('build', run=build_wheel, reply={'wheel': str})
tests = rb.code('tests', run=run_tests, reply={'passed': bool})

notes = rb.step(
    'notes',
    executor=coder,
    prompt='prompts/01-notes.md',
    reads=['brief.md', 'changes.md'],
    writes=['notes.md'],
)


@rb.flow
def main(ctx):
    yield changes()
    report = 'read <run>/notes.md'
    try:
        yield lint()
    except StepFailed as e:
        report += f'; lint did not run: {e.reason}'
    yield build()
    yield notes()
    t = yield tests()
    return end('ready' if t.passed else 'failed', report)


if __name__ == '__main__':
    raise SystemExit(rb.main())
