# Review: `max_length` for `slugify`

## Summary

`slugify` gains `max_length: Optional[int] = None`. The finished slug is cut to `max_length` characters, the cut retreats to the last hyphen when it lands inside a word, a first word longer than the limit is truncated to it, and a trailing hyphen is stripped. Negative values raise `ValueError`, which the brief does not ask for. Docstring, CHANGELOG line and eleven edge-case tests (one method each) follow the repository rules. The logic is correct on every case I traced: limits 0 and 1, cut on, before and after a hyphen, single and first long word, empty slug.

## Findings

### 1: `ValueError` on negative `max_length` is behaviour the brief did not ask for

file: slugger/slugify.py:21
consequence: `slugify('hello', max_length=-1)` raises `ValueError`, while the brief defines only `None` and a non-negative cap — the review axis "everything it asks for, nothing it does not" is not met.

The coder flagged this as debatable in `implement.md`. Without the check, a negative value falls into `slug[:max_length]` and `slug[max_length]` and returns a garbage slice, so silently dropping the guard is worse than keeping it. Fix either way for the human to pick: drop the check, its docstring sentence and `test_negative_limit_raises` to match the brief literally, or keep the guard and let this finding stand as an accepted deviation listed in `fix.md`.

## Checks

- `python3 -m unittest` — exit 0 (16 tests, OK)
