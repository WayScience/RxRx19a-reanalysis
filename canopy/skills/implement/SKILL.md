# Implement

Make the planned change in the working directory.

## Steps

1. Read the plan and the ticket.
1. Apply the change with the tools available to you.
1. Reply with a JSON summary of what changed and which paths it touched.

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
