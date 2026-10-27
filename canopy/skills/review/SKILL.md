# Review

Review the change a run is about to finish. Your job is to find problems
that would make this change wrong to accept: a behavior the ticket did
not ask for, a bug the change introduces, a check it breaks, or a risk it
leaves open. Style you would have written differently is not a finding.

## How to review

Your prompt carries, in order: this procedure, the output contract,
the ticket, the validated outputs of every stage that ran before you
(the triage summary, any implement-stage change summary — whatever
earlier stages produced, whatever wrote them), the working directory,
the paths the run claims to have changed, and this round's check
results. Review from all of it: the change summary describes intent;
the check results and your own reading of the code describe reality.
A summary is evidence of what was attempted, never proof it worked.

1. Read the ticket and the change summary.
1. Start with the paths the change claims to touch. If reading them
   shows the change depends on a problem in closely connected code —
   a caller, a helper, or a test the change should have updated —
   review that too, and say why it is in scope.
1. For every finding, name the file and line, and state the consequence:
   what breaks, when, and for whom. This is the evidence. A finding
   without a consequence you can point to is not a finding — drop it.
1. Verdict `fail` when any finding stands; `pass` only when none do.
1. If a finding you reported in an earlier round still applies, report it
   again — unchanged — so the record stays complete. If it was fixed,
   do not re-report it.
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
  "additionalProperties": false,
  "empty_when": [
    {"when_field": "verdict", "equals": "pass", "fields": ["findings"]}
  ]
}
```
