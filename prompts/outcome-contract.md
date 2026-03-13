# Outcome Contract

Use this contract when dispatching non-trivial work through workbench. Pass it as focused `text` context or embed it directly in the task/pipeline prompt.

```text
Goal
<What must be accomplished>

Acceptance Criteria
- <Concrete done condition>
- <Concrete done condition>

Constraints
- <Repo, safety, scope, time, or autonomy constraints>

Required Evidence
- <Tests that must run>
- <Lint/build/runtime verification that must run>
- <Artifacts or citations that must be included>

Out of Scope
- <Things the agent must not change>

Relevant Context
- <Prior task output, file references, design notes, or decisions>

Expected Output Format
## Outcome
## Evidence
## Risks / Unknowns
## Handoff Notes
```

Validation tasks should use this verdict shape instead:

```text
## Outcome
## Evidence
## Findings
## Risks / Unknowns
## Verdict
```

Rules:

- Missing required evidence must cause rejection.
- If the workflow fails, generate a human-review report instead of guessing.
- Pass contracts, not transcripts.
- Agents do not talk directly to each other; coordinators pass structured handoffs.
