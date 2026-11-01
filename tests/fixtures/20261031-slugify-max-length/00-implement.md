# Implement: `max_length` for `slugify`

## What was done

`slugify` takes a new keyword `max_length: Optional[int] = None`. When set, the slug is built as before and then capped: take the first `max_length` characters; if the character right after the cut is not a hyphen the cut landed inside a word, so retreat to the last hyphen inside the cut; if there is no hyphen the first word alone exceeds the limit and stays truncated at `max_length`. Any trailing hyphen is stripped, so the result never ends with one. `None` means no limit. The docstring names both parameters, as the repository rules require.

Tests cover every edge in a separate method: default has no limit, slug shorter than and exactly at the limit stays unchanged, cut inside a word retreats to the boundary, cut right after a word keeps it, cut landing on the hyphen drops it, first word longer than the limit (with and without further words) is truncated, `max_length=0` gives an empty string, limit on an empty slug, negative limit raises.

CHANGELOG got a line under `## Unreleased`.

## Files

Changed:

- `slugger/slugify.py`
- `tests/test_slugify.py`
- `CHANGELOG.md`

Added: none.

## Decisions

- Negative `max_length` raises `ValueError`. The brief does not say; a silently empty slug or Python's negative-slice behaviour both hide a caller bug. Debatable, easy to drop.
- `max_length=0` returns `''`. Follows literally from "at most `max_length` characters"; the brief's "cut the first word to `max_length`" rule gives the same answer.
- `max_length` stays a positional-or-keyword parameter (not keyword-only), matching the brief's `max_length=None` signature and keeping the existing single-parameter call shape untouched.
- The length check is applied to the finished slug, not the input text, so limits count slug characters as a user would read them in the URL.

## Deviations from the brief

None.

## Checks

- `python3 -m unittest` — exit 0 (16 tests, OK)
