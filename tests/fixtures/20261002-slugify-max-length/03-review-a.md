### a1: `max_length` cuts inside source words that contain punctuation
file: /tmp/rb-demo/textkit/slug.py:20
failure_scenario: `slugify('Version 3.14 notes', max_length=9)` returns `'version-3'` and `slugify("O'Brien Smith", max_length=3)` returns `'o'`, cutting the words "3.14" and "O'Brien" in the middle and, in the first case, producing a slug that reads as a different version.

The cut loop accumulates the regex tokens `[a-z0-9]+`, so any punctuation inside a word (`.`, `'`, `&`, `-`) becomes a cut point: "3.14" is two tokens `3`, `14`; "O'Brien" is `o`, `brien`; "AT&T" is `at`, `t`. The brief says the slug is cut on a word boundary, "never in the middle of a word"; for the user, "3.14" and "O'Brien" are single words. Existing tests only use plain alphabetic words, so they do not catch it.

Check: run the two calls above in `/tmp/rb-demo`.

Fix: decide boundaries on the source text, not on the tokens. Split the normalized text on whitespace, slugify each piece into its hyphen-joined tokens (dropping empty pieces), and accumulate whole pieces with the same length check. Add a test such as `slugify('Version 3.14 notes', max_length=9) == 'version'`. If hyphens in the source ("e-mail") should also count as boundaries, say so in the docstring; either way, the choice should be explicit.
