# Implement

## What was done

Added an optional keyword `max_length: int | None = None` to `textkit.slugify`. Without it (or with `None`) the result is unchanged. With it, the slug keeps as many leading words as fit in `max_length` characters, joined by hyphens; a word is never truncated. Docstring updated. Added tests to `tests/test_slug.py` per `AGENTS.md`.

## Changed files

- `textkit/slug.py`: `max_length` parameter, word-by-word accumulation, validation, docstring.
- `tests/test_slug.py`: 7 new tests (word-boundary cut, exact fit, limit above slug length, first word too long, zero, negative, explicit `None`).

No added files.

## Decisions

- First word longer than `max_length` -> `''`. The brief forbids cutting inside a word and caps length, so an empty slug is the only result satisfying both. Alternative (return the truncated first word, or the whole first word) would violate one of the two requirements.
- `max_length=0` -> `''` (falls out of the same rule).
- Negative `max_length` -> `ValueError`. The brief is silent; treating it as 0 would hide caller bugs.
- No trailing hyphen possible: the slug is built from whole words, so the length cap is checked against the joined string including separators.

## Deviations from the brief

None.

## Checks

Command: `python3 -m unittest`
Exit code: 0 (10 tests, OK)
