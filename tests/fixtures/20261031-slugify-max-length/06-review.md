# Review (pass 3): `max_length` for `slugify`

## Summary

`slugify` takes `max_length: Optional[int] = None`. When set, the finished slug is cut to `max_length` characters: a cut inside a word retreats to the last hyphen before it, a cut right after a word keeps the word, a first word longer than the limit is truncated to the limit, and a trailing hyphen is stripped. A negative `max_length` raises `ValueError`, which the human asked to keep in `04-rounds.md`, and the `## Unreleased` line in `CHANGELOG.md` now names that error, as the same round asked. The previous pass's only finding was that guard as an addition beyond the brief: the brief's owner has now accepted it, so it is closed, not re-reported. Re-traced the cap on limits 0, 1 and `True`, cuts on, before and after a hyphen, first and single long word, repeated separators, accented input, and empty slug: all match the brief. Docstring with every parameter, type hints, CHANGELOG line and one test method per edge follow `AGENTS.md`.

## Findings

None.

## Checks

- `python3 -m unittest` — exit 0 (16 tests, OK)
