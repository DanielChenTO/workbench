import { tool } from "@opencode-ai/plugin"

export default tool({
  description:
    "Check the status of a task running in the workbench worker service. " +
    "Use this to monitor dispatched tasks, see their current phase, " +
    "check if they're blocked, or get the PR URL when complete.",
  args: {
    task_id: tool.schema
      .string()
      .optional()
      .describe(
        "Specific task ID to check. If omitted, lists all recent tasks."
      ),
    status_filter: tool.schema
      .enum(["queued", "resolving", "running", "creating_pr", "blocked", "stuck", "completed", "failed", "cancelled"])
      .optional()
      .describe("Filter tasks by status when listing (only used when task_id is omitted)."),
  },
  async execute(args) {
    const baseUrl = process.env.WORKBENCH_URL || "http://127.0.0.1:8420"

    try {
      if (args.task_id) {
        // Single task lookup
        const resp = await fetch(`${baseUrl}/tasks/${args.task_id}`)
        if (!resp.ok) {
          return `Error (${resp.status}): ${await resp.text()}`
        }
        const t = await resp.json() as Record<string, unknown>
        return formatTask(t)
      }

      // List tasks
      let url = `${baseUrl}/tasks?limit=20`
      if (args.status_filter) {
        url += `&status=${args.status_filter}`
      }
      const resp = await fetch(url)
      if (!resp.ok) {
        return `Error (${resp.status}): ${await resp.text()}`
      }
      const data = await resp.json() as { tasks: Record<string, unknown>[]; total: number }

      if (data.tasks.length === 0) {
        return args.status_filter
          ? `No tasks with status '${args.status_filter}'.`
          : "No tasks found."
      }

      const lines = [`${data.total} total task(s). Showing ${data.tasks.length}:`, ""]
      for (const t of data.tasks) {
        lines.push(formatTaskSummary(t))
      }
      return lines.join("\n")
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      return (
        `Failed to connect to workbench at ${baseUrl}. ` +
        `Is the service running? (Error: ${msg})`
      )
    }
  },
})

function formatTask(t: Record<string, unknown>): string {
  const input = t.input as Record<string, unknown> | undefined
  const lines = [
    `Task: ${t.id}`,
    `  Status:   ${t.status}`,
  ]
  if (t.phase) lines.push(`  Phase:    ${t.phase}`)
  if (t.stale) lines.push(`  WARNING:  Task appears stale (no heartbeat)`)
  if (input) {
    lines.push(`  Type:     ${input.type}`)
    lines.push(`  Source:   ${input.source}`)
    if (input.repo) lines.push(`  Repo:     ${input.repo}`)
    if (input.autonomy) lines.push(`  Autonomy: ${input.autonomy}`)
  }
  if (t.branch) lines.push(`  Branch:   ${t.branch}`)
  if (t.pr_url) lines.push(`  PR:       ${t.pr_url}`)
  if (t.blocked_reason) lines.push(`  Blocked:  ${t.blocked_reason}`)
  if (t.retry_count && (t.retry_count as number) > 0) {
    lines.push(`  Retries:  ${t.retry_count}/${t.max_retries}`)
  }
  if (t.error) lines.push(`  Error:    ${t.error}`)
  lines.push(`  Created:  ${t.created_at}`)
  if (t.started_at) lines.push(`  Started:  ${t.started_at}`)
  if (t.completed_at) lines.push(`  Finished: ${t.completed_at}`)
  return lines.join("\n")
}

function formatTaskSummary(t: Record<string, unknown>): string {
  const input = t.input as Record<string, unknown> | undefined
  const source = input?.source ? String(input.source).slice(0, 60) : "?"
  const staleTag = t.stale ? " [STALE]" : ""
  const blockedTag = t.blocked_reason ? ` [BLOCKED: ${String(t.blocked_reason).slice(0, 40)}]` : ""
  const prTag = t.pr_url ? ` -> ${t.pr_url}` : ""
  return `  ${t.id}  ${t.status}${staleTag}${blockedTag}  ${source}${prTag}`
}
