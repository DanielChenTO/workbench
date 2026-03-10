# Workbench Prompt Templates

Reusable JSON prompt templates for common dispatch patterns. Use with `type=prompt_file` and `file_content`.

## Available Templates

### Specialist Templates

Used by both direct dispatch and orchestrators:

| Template | Autonomy | Use When |
|---|---|---|
| `research.json` | `research` | Investigating a topic, finding relevant code, understanding a system |
| `implement.json` | `local` or `full` | Building a new feature or adding functionality |
| `bug-fix.json` | `local` or `full` | Fixing a specific bug with regression test |
| `add-tests.json` | `local` or `full` | Adding test coverage for an existing module |
| `refactor.json` | `local` or `full` | Improving code quality without changing behavior |
| `code-review.json` | `research` | Reviewing branch changes with structured P0/P1/P2 findings |

### Orchestrator Template

| Template | Role | Use When |
|---|---|---|
| `orchestrator.json` | `orchestrator` | Complex multi-step work requiring planning, delegation, and assembly |

## Specialist Registry

Orchestrators dispatch specialists using these configurations:

| Specialist | Autonomy | Template | Description |
|---|---|---|---|
| Researcher | `research` | `research.json` | Codebase investigation, architecture analysis, finding patterns |
| Implementer | `local` | `implement.json` | Code changes, feature work. Branches, codes, tests, commits. |
| Reviewer | `research` | `code-review.json` | Structured P0/P1/P2 code review |
| Tester | `local` | `add-tests.json` | Writes and runs tests, iterates on failures |
| Refactorer | `local` | `refactor.json` | Quality improvements without behavior change |
| Bug fixer | `local` | `bug-fix.json` | Bug fix with regression test |

## How to Use

### Direct dispatch (via dispatch-task tool)

```
dispatch-task type=prompt_file source="Add tests for database.py" file_content=<contents of add-tests.json> repo=workbench autonomy=local
```

### Orchestrator dispatch (via dispatch-orchestrator tool)

```
dispatch-orchestrator goal="Implement user metrics tracking with export to CSV and JSON formats" repo=my-service
```

The orchestrator will autonomously plan the work, dispatch specialists, wait for results, and assemble a summary.

### Combining with context

Inject prior task output or reference docs:

```
dispatch-task type=prompt_file source="Implement auth feature" file_content=<implement.json> context='[{"type":"task_output","task_id":"abc123","label":"Research findings"}]'
```

### In pipelines

Use templates at each stage:

```
Stage 0: research.json (autonomy=research) — understand the problem
Stage 1: implement.json (autonomy=local) — build the solution  
Stage 2: code-review.json (autonomy=research, review_gate=true) — verify quality
```

## JSON Schema

```json
{
  "prompt": "...",                    // Required — the task description
  "repo": "...",                      // Optional — target repo
  "extra_instructions": "...",        // Optional — appended to prompt
  "context": "...",                   // Optional — prepended to prompt
  "steps": ["...", "..."]             // Optional — numbered step list
}
```

The `steps` array is rendered as a numbered list appended to the prompt. This gives the agent a clear execution plan.
