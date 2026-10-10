# Triage

## To fix

- a1 = b1: `casefold()` outside the flag. CONFIRMED, `slugify('Straße')` is `'strasse'`.
- a2: `Ё` missing. CONFIRMED.
- b2: hard and soft signs split words. CONFIRMED, `'ob-yom'`.

to_fix: 3
