# Fix (round 3): `max_length` for `slugify`

`rounds.md`: keep the `ValueError` on a negative `max_length`, mention the error in the CHANGELOG line.

## Findings

### 1: `ValueError` on negative `max_length` is behaviour the brief did not ask for

Kept, per the human's instruction in `rounds.md`. The guard at `slugger/slugify.py:21-22`, the docstring sentence at `slugger/slugify.py:16` and `test_negative_limit_raises` at `tests/test_slugify.py:54-56` are unchanged.

Changed: `CHANGELOG.md:5` — the `## Unreleased` line now ends with "A negative `max_length` raises `ValueError`.", so the behaviour is in the release notes, as the review's "to accept it" option and the human asked.

## Checks

- `python3 -m unittest` — exit 0 (16 tests, OK)
