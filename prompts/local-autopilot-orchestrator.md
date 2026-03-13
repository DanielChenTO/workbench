# Local Autopilot Orchestrator

Use this prompt when you want workbench to keep making progress on local-only work without repeated user input.

## Mission

Continue working through the local backlog until there are no clear local-safe tasks left.

## Operating Rules

- Work only on **local-safe** items.
- Allowed: local code/doc changes, tests, builds, lint, migrations, local workbench tasks/pipelines/orchestrators.
- Disallowed: push, PR creation, production changes, destructive git operations, secrets-dependent work.
- Stop and report only when blocked by:
  - product/design decision
  - missing secret/credential/value
  - destructive or irreversible action
  - remote side effect requirement
  - failed validation needing human review

## Sources of Truth

1. Workbench todos with tags like `autopilot`, `backlog`, `review_queue` — these are the primary backlog state.
2. `work-directory/backlog.md` as a durable mirror and human-readable fallback.
3. `work-directory/log.md` for recent work and natural next steps.
4. The shared contract at `workbench/prompts/outcome-contract.md`.

## Loop

1. Read the most relevant backlog/todo items and latest session log entries.
2. Pick the highest-value local-safe item that is not blocked.
3. Build a compact outcome contract for that item.
4. Dispatch the right work:
   - research task for understanding
   - `dispatch-code-change` for normal code changes
   - task/pipeline for narrow special cases
5. Validate the result.
6. If approved:
   - update the workbench todo state first
   - mirror the result into `work-directory/backlog.md`
   - append a concise session/log handoff
   - continue to the next local-safe item
7. If failed and human review is required:
   - create or update a review-queue todo with the failure report
   - mirror that parked item into `work-directory/backlog.md`
   - mark the original item blocked/review
   - continue to the next unblocked item

## Output Contract

Your final output should include:

```text
## Outcome
## Evidence
## Remaining Backlog
## Review Queue
## Why Autopilot Stopped
```
