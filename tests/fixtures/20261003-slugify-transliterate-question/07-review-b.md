### b1: behaviour without the flag changed for `ß`
file: /tmp/rb-demo/textkit/slug.py:18
failure_scenario: `slugify('Straße')` gives `'strasse'`, it gave `'strae'`; the brief says nothing changes without the flag.

### b2: `ъ` and `ь` become `'`, which then splits words
file: /tmp/rb-demo/textkit/translit.py:31
failure_scenario: `slugify('Объём', transliterate=True)` returns `'ob-yom'`.
