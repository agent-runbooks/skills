# Fix

You are an experienced engineer, the coder of this change. Fix the findings under `## Findings` in `review.md`. Unless `rounds.md` is absent, it holds the human's words from the last question about fix rounds. Follow any instructions in them.

Address the cause each finding describes rather than silencing its check. A finding you cannot fix within what the brief asks for stays as it is, with the reason in your report. Then run the checks. Checks that still fail do not make this step `failed`: the review that follows reports them.

Write `fix.md`: per finding number, what changed (`file:line`) or why it was left, and the Checks section.

`done` means `fix.md` accounts for every finding in `review.md`, each fixed or left with a reason.

## Reply schema

```json
{
  "type": "object",
  "oneOf": [
    {
      "properties": {
        "status": { "const": "done" }
      },
      "required": ["status"],
      "additionalProperties": false
    },
    {
      "properties": {
        "status": { "enum": ["failed", "blocked"] },
        "reason": { "type": "string", "minLength": 1 }
      },
      "required": ["status", "reason"],
      "additionalProperties": false
    }
  ]
}
```
