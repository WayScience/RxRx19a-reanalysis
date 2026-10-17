# Plan

Plan the change a ticket asks for, concretely and minimally.

## Steps

1. Read the ticket and the repository layout.
1. Name the exact files the change will touch.
1. Write a one-paragraph plan a reviewer can follow.
1. Reply with JSON only.

## Output contract

```json
{
  "type": "object",
  "required": ["summary", "changed_paths"],
  "properties": {
    "summary": {"type": "string"},
    "changed_paths": {"type": "array", "items": {"type": "string"}}
  },
  "additionalProperties": false
}
```