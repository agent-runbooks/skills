# Review (pass 2): `max_length` for `slugify`

## Summary

`slugify` takes `max_length: Optional[int] = None`. When set, the finished slug is cut to `max_length` characters; a cut that lands inside a word retreats to the last hyphen before it, a first word longer than the limit is truncated to the limit, and a trailing hyphen is stripped. A negative `max_length` raises `ValueError`, which the brief does not define. The fix round changed no repository files and kept the guard as an accepted deviation. Re-traced the cap logic on limits 0 and 1, cuts on, before and after a hyphen, single and first long word, and empty slug: all correct. Docstring, type hints, CHANGELOG line and one-method-per-edge tests follow `AGENTS.md`.

## Findings

### 1: `ValueError` on negative `max_length` is behaviour the brief did not ask for

file: slugger/slugify.py:21
consequence: `slugify('hello', max_length=-1)` raises `ValueError`, while the brief defines only `None` and a character cap, so the axis "everything it asks for, nothing it does not" is not met.

Unchanged since the previous pass. The coder kept it on purpose (`fix.md`): without the guard a negative value runs `slug[:-1]` and `slug[-1]` and returns a silently wrong slice, and the repository rule asks for explicit, tested edge behaviour. I agree the guard is the better code, but it is still an addition beyond the brief, so the human decides. To close it literally: drop the check at `slugger/slugify.py:21-22`, the docstring sentence at `slugger/slugify.py:16` and `test_negative_limit_raises` at `tests/test_slugify.py:54-56`. To accept it: no change, the CHANGELOG line could mention the error so the behaviour is user-visible in the release notes.

## Checks

- `python3 -m unittest` — exit 0 (16 tests, OK)
