type Stage = {
  name: string
  autonomy: "research" | "local"
  prompt: string
  review_gate?: boolean
  reject_action?: "fail"
  loop_to?: number
}

export function composeSharedOutcomeContract(contract: string): string {
  return `## Shared Outcome Contract\n\n${contract}`
}

export function buildDefaultCodeChangeStages(contract: string): Stage[] {
  const shared = composeSharedOutcomeContract(contract)

  return [
    {
      name: "explore",
      autonomy: "research",
      prompt:
        `Investigate the codebase needed for this change. ` +
        `Use the shared outcome contract to focus the research. ` +
        `Map the relevant files, data flow, constraints, and risks. ` +
        `Return a structured handoff for implementation.\n\n${shared}`,
    },
    {
      name: "implement",
      autonomy: "local",
      prompt:
        `Implement the requested code change using the shared outcome contract. ` +
        `Make the minimal correct change set, run the required checks, and return a structured handoff with concrete evidence.\n\n${shared}`,
    },
    {
      name: "validate",
      autonomy: "research",
      prompt:
        `Validate the implementation against the shared outcome contract. ` +
        `Reject if any acceptance criterion is unmet or any required evidence is missing. ` +
        `This stage fails closed and requires human review on rejection.\n\n${shared}`,
      review_gate: true,
      reject_action: "fail",
    },
    {
      name: "review",
      autonomy: "research",
      prompt:
        `Review the implementation for correctness, completeness, edge cases, tests, error handling, and code quality. ` +
        `Use the shared outcome contract as the scope boundary. Output APPROVE or REJECT with specific findings.\n\n${shared}`,
      review_gate: true,
      loop_to: 1,
    },
  ]
}

export function buildLocalAutopilotPrompt(goal: string, variant: "full" | "compact" = "full"): string {
  if (variant === "compact") {
    return [
      "# Local Autopilot Orchestrator",
      "",
      "Continue local-safe work until blocked.",
      "Never push or create PRs.",
      "Use backlog and logs to choose the next item.",
      "",
      "Goal:",
      goal,
    ].join("\n")
  }

  return [
    "# Local Autopilot Orchestrator",
    "",
    "You are a local-only orchestrator. Keep making progress without asking for more input unless truly blocked.",
    "",
    "Rules:",
    "- Only local-safe work is allowed.",
    "- Never push or create PRs.",
    "- Use workbench todos tagged with 'autopilot' as the primary backlog.",
    "- Mirror todo state into work-directory/backlog.md so humans can inspect the queue easily.",
    "- Use the backlog, todos, and recent log entries to choose the next task.",
    "- For normal code changes, use the default workflow: explore -> implement -> validate -> review.",
    "- Failed validation requires a human-review report and the item should be parked in a review queue.",
    "- Continue to the next local-safe item until no such item remains.",
    "",
    "Goal:",
    goal,
  ].join("\n")
}
