# Polish

- `textkit/slug.py`: rewrote the `slugify` docstring for `max_length`, cutting it from four sentences to three. It now says words are split on whitespace and never cut, folds the "first word too long gives ''" case into the 'O'Brien'/'3.14'/'e-mail' example and keeps the ValueError line. Behaviour is unchanged.
- `tests/test_slug.py`: renamed `test_max_length_skips_words_without_letters` to `test_max_length_skips_punctuation_only_words`. The skipped piece is `--`, and the old name was inexact because a piece of digits alone is kept.

## Checks

Command: `python3 -m unittest`

Exit code: 0
