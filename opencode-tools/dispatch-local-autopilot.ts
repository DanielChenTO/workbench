import { tool } from "@opencode-ai/plugin/tool"

import { buildLocalAutopilotPrompt } from "./shared-outcome-contract"

export default tool({
  description:
    "Dispatch a local-only continuation orchestrator that keeps working through backlog items " +
    "until no clear local-safe task remains. Never pushes or creates PRs.",
  args: {
    goal: tool.schema
      .string()
      .describe("High-level local continuation goal. Example: 'Continue the workbench workflow hardening roadmap until blocked'."),
    repo: tool.schema
      .string()
      .optional()
      .describe("Primary repository for the continuation loop."),
    backlog_context: tool.schema
      .string()
      .optional()
      .describe("Optional JSON array of context items pointing to backlog files, todos, or prior outputs."),
    timeout: tool.schema
      .number()
      .optional()
      .describe("Timeout in seconds. Default: 7200."),
  },
  async execute(args) {
    const baseUrl = process.env.WORKBENCH_URL || "http://127.0.0.1:8420"
    const goal = args.goal.trim()
    if (goal.split(/\s+/).filter(Boolean).length < 6) {
      return "Please provide a fuller goal for local autopilot continuation."
    }

    const source = buildLocalAutopilotPrompt(goal, "full")
    const payload: Record<string, unknown> = {
      type: "prompt",
      source,
      role: "orchestrator",
      autonomy: "local",
      timeout: args.timeout ?? 7200,
    }

    if (args.repo) payload.repo = args.repo
    if (args.backlog_context) {
      try {
        payload.context = JSON.parse(args.backlog_context)
      } catch {
        return `Error: 'backlog_context' must be valid JSON. Got: ${args.backlog_context}`
      }
    }

    try {
      const resp = await fetch(`${baseUrl}/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })

      if (!resp.ok) {
        return `Error dispatching local autopilot (${resp.status}): ${await resp.text()}`
      }

      const data = (await resp.json()) as { id: string; status: string }
      return [
        "Local autopilot orchestrator dispatched.",
        "",
        `  Task ID: ${data.id}`,
        `  Status:  ${data.status}`,
        "  Mode:    local-only continuation",
        "",
        `Track: curl ${baseUrl}/tasks/${data.id}`,
      ].join("\n")
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      return `Failed to connect to workbench at ${baseUrl}: ${msg}`
    }
  },
})
