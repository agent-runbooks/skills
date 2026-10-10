# Fix checks

Moved the table lookup after `casefold()`, which also turns `ß` into `ss` on its own; the `ß` entry is gone from the table. `python3 -m unittest`: 14 tests, OK.
