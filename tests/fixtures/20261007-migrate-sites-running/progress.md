# Run 20261007-migrate-sites-running

Runbook `runbook-migrate-sites`. State in `state.json`. Inputs:

- brief: brief.md
- repo: /tmp/rb-demo

## Log

- main/plan: launched
- main/plan: {"status": "done"}
- main/sites[auth]/migrate: launched
- main/sites[billing]/migrate: launched
- main/sites[billing]/migrate: {"status": "done", "risky": true}
- main/sites[billing]/approve: asked: `/tmp/rb-demo/.agent-runbooks/runs/20261007-migrate-sites-running/05-migrate.md` changes a shared table. Go on with this site, or skip it?
- main/sites[auth]/migrate: {"status": "done", "risky": false}
- main/sites[auth]/verify: launched
- main/sites[auth]/verify: {"status": "failed", "reason": "users.email loses its NOT NULL constraint"}
- main/sites[billing]/approve: cancelled
- main/reviews/a: launched
- main/reviews/b/review: launched
- main/reviews/a: {"status": "done", "findings": 1}
- main/reviews/b/review: {"status": "blocked", "reason": "no second reviewer is set up"}
- main/reviews/b/review#2: launched
