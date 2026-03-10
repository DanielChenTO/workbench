import { tool } from "@opencode-ai/plugin"

export default tool({
  description:
    "Dispatch a multi-stage pipeline to the workbench service. " +
    "Pipelines run staged workflows like explore → plan → implement → review " +
    "with automatic review gating and retry loops. " +
    "Returns the pipeline ID and status for tracking.",
  args: {
    repo: tool.schema
      .string()
      .optional()
      .describe(
        "Target repository short name (e.g. 'workbench', 'my-service'). " +
        "Applied to all stages unless overridden per-stage."
      ),
    stages: tool.schema
      .string()
      .describe(
        "JSON array of stage objects. Each stage requires: " +
        "'name' (string), 'autonomy' (full|local|plan_only|research), 'prompt' (string). " +
        "Optional: 'review_gate' (bool, parse output for APPROVE/REJECT), " +
        "'loop_to' (int, stage index to retry on rejection), " +
        "'model' (string, override model), 'extra_instructions' (string). " +
        "Example: '[{\"name\":\"explore\",\"autonomy\":\"research\",\"prompt\":\"Find all usages of X\"}," +
        "{\"name\":\"implement\",\"autonomy\":\"local\",\"prompt\":\"Refactor X to Y\"}," +
        "{\"name\":\"review\",\"autonomy\":\"research\",\"prompt\":\"Review the changes\",\"review_gate\":true}]'"
      ),
    max_review_iterations: tool.schema
      .number()
      .optional()
      .describe(
        "Maximum times a review-gated stage can reject before failing the pipeline. Default: 3."
      ),
    model: tool.schema
      .string()
      .optional()
      .describe("Default LLM model for all stages (can be overridden per-stage)."),
    depends_on: tool.schema
      .string()
      .optional()
      .describe(
        "JSON array of pipeline IDs that must complete before this pipeline starts. " +
        "If any dependency fails or is cancelled, this pipeline auto-fails. " +
        "Example: '[\"abc123\",\"def456\"]'"
      ),
  },
  async execute(args) {
    const baseUrl = process.env.WORKBENCH_URL || "http://127.0.0.1:8420"

    // Parse stages JSON
    let stages: unknown[]
    try {
      stages = JSON.parse(args.stages)
      if (!Array.isArray(stages) || stages.length === 0) {
        return "Error: 'stages' must be a non-empty JSON array."
      }
    } catch {
      return `Error: 'stages' must be valid JSON. Got: ${args.stages.slice(0, 200)}`
    }

    const payload: Record<string, unknown> = { stages }

    if (args.repo) payload.repo = args.repo
    if (args.max_review_iterations != null) payload.max_review_iterations = args.max_review_iterations
    if (args.model) payload.model = args.model

    if (args.depends_on) {
      try {
        const deps = JSON.parse(args.depends_on)
        if (!Array.isArray(deps)) {
          return "Error: 'depends_on' must be a JSON array of pipeline IDs."
        }
        payload.depends_on = deps
      } catch {
        return `Error: 'depends_on' must be valid JSON. Got: ${args.depends_on}`
      }
    }

    try {
      const resp = await fetch(`${baseUrl}/pipelines`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })

      if (!resp.ok) {
        const errorText = await resp.text()
        return `Error creating pipeline (${resp.status}): ${errorText}`
      }

      const data = await resp.json() as {
        id: string
        status: string
        repo?: string
        stages: { name: string; autonomy: string }[]
        current_stage_index: number
        current_task_id?: string
        max_review_iterations: number
        depends_on?: string[]
        dependencies_met?: boolean
      }

      const stageList = data.stages
        .map((s, i) => `    ${i}: ${s.name} (${s.autonomy})${i === data.current_stage_index ? " ← current" : ""}`)
        .join("\n")

      const lines = [
        `Pipeline dispatched successfully.`,
        ``,
        `  Pipeline ID:   ${data.id}`,
        `  Status:        ${data.status}`,
      ]
      if (data.repo) lines.push(`  Repo:          ${data.repo}`)
      lines.push(
        `  Stages:`,
        stageList,
        `  Review limit:  ${data.max_review_iterations} iterations`,
      )
      if (data.current_task_id) {
        lines.push(`  Current task:  ${data.current_task_id}`)
      }
      if (data.depends_on && data.depends_on.length > 0) {
        lines.push(`  Depends on:    ${data.depends_on.join(", ")}`)
        lines.push(`  Deps met:      ${data.dependencies_met}`)
      }
      lines.push(
        ``,
        `Track:    curl ${baseUrl}/pipelines/${data.id}`,
        `Cancel:   curl -X POST ${baseUrl}/pipelines/${data.id}/cancel`,
      )

      return lines.join("\n")
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      return (
        `Failed to connect to workbench at ${baseUrl}. ` +
        `Is the service running? (Error: ${msg})\n\n` +
        `Start it with: WORKBENCH_WORKSPACE_ROOT=/path/to/workspace workbench serve`
      )
    }
  },
})
