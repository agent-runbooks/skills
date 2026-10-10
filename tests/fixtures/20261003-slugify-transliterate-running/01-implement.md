# Implement

## What was done

Added `transliterate: bool = False` to `textkit.slugify`. With it, the text goes through a new table in `textkit/translit.py` (Cyrillic per GOST 7.79-B, German and Nordic letters, ligatures) before the ASCII fold; letters without an entry fall through to `unicodedata` decomposition as before.

## Changed files

- `textkit/translit.py`: new, the table and `transliterate(text)`.
- `textkit/slug.py`: the flag, docstring.
- `tests/test_slug.py`: 6 new tests.
