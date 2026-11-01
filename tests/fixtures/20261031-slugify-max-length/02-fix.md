# Fix: `max_length` for `slugify`

No repository files changed in this step.

## Findings

### 1: `ValueError` on negative `max_length` — left as is, accepted deviation

Kept the guard at `slugger/slugify.py:21`, its docstring sentence at `slugger/slugify.py:16` and `test_negative_limit_raises` at `tests/test_slugify.py:54`.

Reason: the brief defines `max_length` as a character count and says nothing about negative values, so they fall outside its domain either way. Without the guard `slugify('hello', max_length=-1)` runs `slug[:-1]` and `slug[-1]`, returning a silently wrong slice — the review itself calls dropping the guard the worse option. The repository rule asks for explicit, tested behaviour at the edges, and a negative limit is one. The addition is two lines, one docstring sentence and one test. The review lists this finding as fixable either way for the human to pick: if the brief is to be read literally, remove the three places above and the finding closes.

## Checks

- `python3 -m unittest` — exit 0 (16 tests, OK)
