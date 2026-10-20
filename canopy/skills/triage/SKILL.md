# Triage

Decide what a ticket asks for and report it in the output contract's
shape.

A ticket is the request this run carries: usually a project issue, with
a title, a body, optional labels, and an optional effort estimate.

## Steps

1. Read the ticket.
1. Pick one category: `bug`, `feature`, or `docs`.
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
