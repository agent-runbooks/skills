# Verify

## Resolved

- a1/b1, a2.

## Unresolved

### b2: hard and soft signs
`slugify('Объём', transliterate=True)` now returns `'obyom'`, but GOST 7.79-B wants `'ob``yom'`, and the brief's own example list is silent on it. The reviewer and the fixer read the standard differently.

unresolved: 1, checks passed
