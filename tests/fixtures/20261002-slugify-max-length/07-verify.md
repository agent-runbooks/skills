# Verify

## Resolved

### a1: `max_length` cuts inside source words that contain punctuation
- `/tmp/rb-demo/textkit/slug.py:21`: in the `max_length` path, `source_words` splits the lowercased ASCII text on whitespace (`ascii_text.lower().split()`) and turns each piece into its hyphen-joined `[a-z0-9]+` tokens, so "3.14" -> `3-14` and "O'Brien" -> `o-brien` are single units.
- `/tmp/rb-demo/textkit/slug.py:23-27`: iterates `filter(None, source_words)` (punctuation-only pieces dropped) and appends whole pieces while `len(candidate) <= max_length`.
- `/tmp/rb-demo/textkit/slug.py:17-20`: `max_length=None` path is byte-for-byte the original logic.
- `/tmp/rb-demo/textkit/slug.py:11-12`: docstring states that words are whitespace-separated pieces and names 'O'Brien', '3.14', 'e-mail'.
- `/tmp/rb-demo/tests/test_slug.py:36-42`: `test_max_length_keeps_punctuated_words_whole` covers the triage scenarios; `:44-45` covers punctuation-only pieces.
- Reran the triage scenarios: `slugify('Version 3.14 notes', max_length=9)` -> `'version'`, `slugify("O'Brien Smith", max_length=3)` -> `''`, `slugify('AT&T rocks', max_length=3)` -> `''`.
- Neighbour check (random fuzz, 20000 strings x max_length 0..34, alphabet with letters, digits, space/tab/newline, `.,'&-!`, accented letters, em dash): with a huge `max_length` the result equals `slugify(text)`; for every `max_length` the result is at most `max_length` characters long, is a prefix of `slugify(text)` and ends at a `-` boundary. No failures.

## Unresolved

None.

## Checks

Command: `python3 -m unittest` (from `/tmp/rb-demo`)
Exit code: 0 (Ran 12 tests, OK)
