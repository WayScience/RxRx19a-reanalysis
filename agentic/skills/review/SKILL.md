# Review

Review the change a run is about to finish. Your job is to find real
problems, not to list everything you notice.

## How to review

1. Read the ticket and the change summary.
1. Look only at what this change claims to touch. Do not re-review code
   the change did not touch.
1. For every finding, name the file and line, and say what breaks.
1. A finding without evidence is not a finding. Drop it.
1. Do not repeat a finding you already reported in an earlier round.
1. Judge the change, not the author's style. Prefer zero comments over
   a comment that would not change the outcome.

## Output contract

```json
{
  "type": "object",
  "required": ["verdict", "findings", "changed_paths"],
  "properties": {
    "verdict": {"type": "string", "enum": ["pass", "fail"]},
    "findings": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["file", "line", "problem"],
        "properties": {
          "file": {"type": "string"},
          "line": {"type": "integer"},
          "problem": {"type": "string"}
        },
        "additionalProperties": false
      }
    },
    "changed_paths": {"type": "array", "items": {"type": "string"}}
  },
  "additionalProperties": false
}
```