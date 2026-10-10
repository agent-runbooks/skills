#!/usr/bin/env python3
"""Steps and the flow of runbook-review-loop. Run with --help for the commands."""

from runbook import Runbook, end

rb = Runbook()

rb.inputs(brief=str, repo=str, checks='', maxFixRounds=2)

# Two names for one kind of launch, so either role moves to another model by editing one line.
SUBAGENT = 'general-purpose, on the model of the main session, named explicitly where the tool takes a model'
rb.executor('coder', SUBAGENT)
rb.executor('reviewer', SUBAGENT)

implement = rb.step(
    'implement',
    executor='coder',
    prompt='prompts/01-implement.md',
    inputs=['checks'],
    reads=['brief.md', 'working tree'],
    writes=['implement.md'],
)

review = rb.step(
    'review',
    executor='reviewer',
    prompt='prompts/02-review.md',
    inputs=['checks'],
    reads=['brief.md', 'implement.md', 'fix.md', 'review.md', 'working tree'],
    writes=['review.md'],
    reply={'findings': {'type': 'integer', 'description': 'findings still open, 0 when none'}},
)

fix = rb.step(
    'fix',
    executor='coder',
    prompt='prompts/03-fix.md',
    inputs=['checks'],
    reads=['brief.md', 'review.md', 'rounds.md', 'working tree'],
    writes=['fix.md'],
)

ask_rounds = rb.human(
    'ask-rounds',
    writes='rounds.md',
    choices=['more rounds', 'stop'],
    reply={
        'rounds': {
            'type': 'integer',
            'default': 1,
            'description': 'how many more fix rounds the human grants',
        }
    },
    question='Fix rounds are spent and `<run>/review.md` still lists findings. '
    'More rounds, and how many, or stop here? Anything you add goes to the coder.',
)


@rb.flow
def main(ctx):
    yield implement()
    # The fix rounds of a run: the input, and what the human granted since.
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


if __name__ == '__main__':
    raise SystemExit(rb.main())
