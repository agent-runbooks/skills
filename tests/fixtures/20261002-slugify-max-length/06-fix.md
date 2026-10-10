# Fix

## a1: `max_length` cuts inside source words that contain punctuation

Fixed. Cut points are now whitespace-separated source words, not regex tokens.

- `/tmp/rb-demo/textkit/slug.py:21` — in the `max_length` path, split the lowercased ASCII text on whitespace, turn each piece into its hyphen-joined `[a-z0-9]+` tokens; `slug.py:23` drops empty pieces (`filter(None, ...)`) and accumulates whole pieces with the same length check. The `max_length=None` path (`slug.py:18-20`) is unchanged.
- `/tmp/rb-demo/textkit/slug.py:11-12` — docstring states that words are whitespace-separated pieces, so "O'Brien", "3.14" and "e-mail" are kept or dropped whole.
- `/tmp/rb-demo/tests/test_slug.py:36` — `test_max_length_keeps_punctuated_words_whole`: `'Version 3.14 notes', 9 -> 'version'`, `12 -> 'version-3-14'`, `"O'Brien Smith", 3 -> ''`, `'AT&T rocks', 3 -> ''`, `'e-mail me', 1 -> ''`, `'e-mail me', 9 -> 'e-mail-me'`.
- `/tmp/rb-demo/tests/test_slug.py:44` — `test_max_length_skips_words_without_letters`: `'Hello -- big World', 9 -> 'hello-big'` (a punctuation-only piece adds no empty segment).

## Checks

Command: `python3 -m unittest` (from `/tmp/rb-demo`)
Exit code: 0 (Ran 12 tests, OK)
