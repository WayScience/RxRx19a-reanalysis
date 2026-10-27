# Triage

Decide what a ticket asks for and report it in the output contract's
shape.

A ticket is the request this run carries: usually a project issue, with
a title, a body, optional labels, and an optional effort estimate.

## Steps

1. Read the ticket.
1. Pick one category: `bug`, `feature`, or `docs`.
1. Write the summary for the stages that come after you. It must
   carry, in two or three sentences: what the ticket asks for, which
   part of the repository it touches, and any constraint the ticket
   states (a deadline, a scale, a must-not-break). Later stages see
   your summary, not the ticket's wording, so nothing they need may
   be lost in your paraphrase.
1. Reply with JSON only.

## Output contract

```json
{
  "type": "object",
  "required": ["category", "summary", "changed_paths"],
  "properties": {
    "category": {"type": "string", "enum": ["bug", "feature", "docs"]},
    "summary": {"type": "string"},
    "changed_paths": {"type": "array", "items": {"type": "string"}}
  },
  "additionalProperties": false
}
```
