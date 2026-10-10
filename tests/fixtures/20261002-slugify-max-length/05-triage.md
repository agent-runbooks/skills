# Triage

## To fix

### a1: `max_length` cuts inside source words that contain punctuation
file: /tmp/rb-demo/textkit/slug.py:20
failure_scenario: `slugify('Version 3.14 notes', max_length=9)` returns `'version-3'` and `slugify("O'Brien Smith", max_length=3)` returns `'o'`, cutting the words "3.14" and "O'Brien" in the middle and, in the first case, producing a slug that reads as a different version.

Verdict: CONFIRMED. Ran in `/tmp/rb-demo`:
- `slugify('Version 3.14 notes', max_length=9)` -> `'version-3'`
- `slugify("O'Brien Smith", max_length=3)` -> `'o'`
- `slugify('AT&T rocks', max_length=3)` -> `'at'`

Cause: line 16 `words = re.findall(r'[a-z0-9]+', ascii_text.lower())` splits on every non-alphanumeric char, and the loop at lines 20-24 (`for word in words: candidate = f'{slug}-{word}' if slug else word`) treats each token as a cut point. The brief requires the slug to be cut "on a word boundary, never in the middle of a word"; "3.14", "O'Brien", "AT&T" are single words in the source text. Existing tests use only plain alphabetic words, so they pass.

Weighing: fix is ~5 lines inside one function (split the normalized text on whitespace, turn each piece into its hyphen-joined tokens, drop empty pieces, accumulate whole pieces with the same length check); the `max_length=None` path is untouched, so no risk to existing behaviour. Leaving it produces misleading slugs (`version-3` for "Version 3.14") and violates the brief directly. Worth fixing.

Description: decide cut boundaries on whitespace-separated source words, not on regex tokens. Add tests such as `slugify('Version 3.14 notes', max_length=9) == 'version'` and `slugify("O'Brien Smith", max_length=3) == ''`. Hyphenated source words ("e-mail") become one unit under this scheme (currently `slugify('e-mail me', max_length=1)` -> `'e'`); state that choice in the docstring.

## Rejected

None.
