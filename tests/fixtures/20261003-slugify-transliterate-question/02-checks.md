## Checks

**Command:** `python3 -m unittest`

**Exit code:** 1

```
FAIL: test_transliterate_german (tests.test_slug.SlugifyTest)
AssertionError: 'strae' != 'strasse'

Ran 14 tests in 0.004s
FAILED (failures=1)
```

`ß` is lowercased after the table lookup, so `ẞ`/`ß` never match the entry.
