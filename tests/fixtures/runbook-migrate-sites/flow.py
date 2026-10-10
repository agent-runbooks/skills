#!/usr/bin/env python3
"""A flow with a foreach and a parallel, for the viewer's fixtures; make.py drives it. Not a runbook to install."""

import contextlib

from runbook import Runbook, StepFailed, end, foreach, parallel

rb = Runbook()

rb.inputs(brief=str, repo=str)

SUBAGENT = 'general-purpose, on the model of the main session'
rb.executor('coder', SUBAGENT)
rb.executor('reviewer', SUBAGENT)

plan = rb.step(
    'plan',
    executor='coder',
    prompt='prompts/01-plan.md',
    reads=['brief.md', 'working tree'],
    writes=['sites.json'],
)

migrate = rb.step(
    'migrate',
    executor='coder',
    prompt='prompts/02-migrate.md',
    reads=['brief.md', 'working tree'],
    writes=['migrate.md'],
    reply={'risky': bool},
)

approve = rb.human(
    'approve',
    writes='approve.md',
    choices=['go', 'skip'],
    question='`<run>/migrate.md` changes a shared table. Go on with this site, or skip it?',
)

verify = rb.step(
    'verify',
    executor='reviewer',
    prompt='prompts/03-verify.md',
    reads=['migrate.md', 'working tree'],
    writes=['verify.md'],
)

review = rb.step(
    'review',
    executor='reviewer',
    prompt='prompts/04-review.md',
    reads=['brief.md', 'sites.index', 'working tree'],
    writes=['review.md'],
    reply={'findings': int},
)


def migrate_site(ctx, item):
    r = yield migrate()
    if r.risky:
        a = yield approve()
        if a.choice == 'skip':
            return 'skipped'
    yield verify()
    return 'migrated'


def second_review(ctx):
    try:
        return (yield review())
    except StepFailed:
        return (yield review(executor='coder'))


@rb.flow
def main(ctx):
    yield plan()
    # A failed site does not stop the run: the reviews read the index, failed items included.
    with contextlib.suppress(StepFailed):
        yield foreach('sites', over='sites.json', body=migrate_site, max_concurrent=2)
    reviews = yield parallel('reviews', a=review(), b=second_review)
    status = 'needs_attention' if reviews.a.findings or reviews.b.findings else 'ready'
    return end(status, 'read <run>/sites.index and the reviews')


if __name__ == '__main__':
    raise SystemExit(rb.main())
