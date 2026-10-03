### a1: `casefold()` runs on the whole text, also without `transliterate`
file: /tmp/rb-demo/textkit/slug.py:18
failure_scenario: `slugify('Straße')` returned `'strae'` before the change and returns `'strasse'` now, so existing slugs change without the flag.

The fix for the failing check moved `casefold()` out of the `transliterate` branch.

### a2: `Ё` is missing from the table
file: /tmp/rb-demo/textkit/translit.py:9
failure_scenario: `slugify('Ёлка', transliterate=True)` returns `'lka'`.
