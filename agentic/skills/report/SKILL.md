# Report

Write the summary a person reads before deciding what to do with the
finished work. A person owns the outcome, so give them the evidence.

## Steps

1. Read the ticket and the change summary.
1. Write a short plain-language report covering:
   - What changed, in one or two sentences.
   - How it was tested: the commands that ran and what they returned.
   - How to revert: the one step that undoes this change.
   - What is left for a person to review, if anything.
1. Reply with JSON only.

## Output contract

```json
{
  "type": "object",
  "required": ["report"],
  "properties": {
    "report": {"type": "string"}
  },
  "additionalProperties": false
}
```