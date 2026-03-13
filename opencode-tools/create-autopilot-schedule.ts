import { tool } from "@opencode-ai/plugin/tool"

export default tool({
  description:
    "Create a recurring workbench schedule that re-dispatches local autopilot. " +
    "Useful for non-stop local continuation without manual restarts.",
  args: {
    name: tool.schema.string().describe("Human-readable schedule name."),
    cron_expr: tool.schema.string().describe("Standard 5-field cron expression."),
    goal: tool.schema.string().describe("High-level local continuation goal."),
    repo: tool.schema.string().optional().describe("Primary repository."),
    timezone: tool.schema.string().optional().describe("IANA timezone. Default: US/Pacific."),
  },
  async execute(args) {
    const baseUrl = process.env.WORKBENCH_URL || "http://127.0.0.1:8420"
    const payload = {
      type: "prompt",
      source: [
        "# Local Autopilot Orchestrator",
        "",
        "Continue local-safe work until blocked.",
        "Never push or create PRs.",
        "Use backlog and logs to choose the next item.",
        "",
        "Goal:",
        args.goal,
      ].join("\n"),
      role: "orchestrator",
      autonomy: "local",
      timeout: 7200,
      ...(args.repo ? { repo: args.repo } : {}),
    }

    const body = {
      name: args.name,
      cron_expr: args.cron_expr,
      timezone: args.timezone ?? "US/Pacific",
      schedule_type: "task",
      payload,
    }

    try {
      const resp = await fetch(`${baseUrl}/schedules`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
      if (!resp.ok) {
        return `Error creating autopilot schedule (${resp.status}): ${await resp.text()}`
      }
      const data = (await resp.json()) as { id: string; next_run_at?: string }
      return `Autopilot schedule created: ${data.id}${data.next_run_at ? ` next run ${data.next_run_at}` : ""}`
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      return `Failed to connect to workbench at ${baseUrl}: ${msg}`
    }
  },
})
