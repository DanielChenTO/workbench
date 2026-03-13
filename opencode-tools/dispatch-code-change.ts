import { tool } from "@opencode-ai/plugin/tool"

import { buildDefaultCodeChangeStages } from "./shared-outcome-contract"

export default tool({
  description:
    "Dispatch the default code-change pipeline to workbench. " +
    "Uses the standard explore -> implement -> validate -> review workflow " +
    "with the shared outcome contract and fail-closed validation.",
  args: {
    repo: tool.schema
      .string()
      .describe("Target repository short name (for example: 'terraform-enterprise')."),
    contract: tool.schema
      .string()
      .describe(
        "The full shared outcome contract text. Include goal, acceptance criteria, constraints, required evidence, relevant context, and expected output format."
      ),
    model: tool.schema
      .string()
      .optional()
      .describe("Default LLM model for all stages (optional)."),
    max_review_iterations: tool.schema
      .number()
      .optional()
      .describe("Maximum review rejection loops before the pipeline fails. Default: 3."),
    depends_on: tool.schema
      .string()
      .optional()
      .describe(
        "JSON array of task or pipeline IDs that must complete before this pipeline starts. Example: '[\"abc123\",\"def456\"]'"
      ),
  },
  async execute(args) {
    const baseUrl = process.env.WORKBENCH_URL || "http://127.0.0.1:8420"
    const contract = args.contract.trim()
    const wordCount = contract.split(/\s+/).filter(Boolean).length

    if (wordCount < 12) {
      return (
        `Warning: contract is only ${wordCount} words long.\n` +
        `Provide the full shared outcome contract, not a short goal. Include acceptance criteria and required evidence.`
      )
    }

    const stages = buildDefaultCodeChangeStages(contract)
    const payload: Record<string, unknown> = {
      repo: args.repo,
      stages,
      max_review_iterations: args.max_review_iterations ?? 3,
    }

    if (args.model) payload.model = args.model
    if (args.depends_on) {
      try {
        const deps = JSON.parse(args.depends_on)
        if (!Array.isArray(deps)) {
          return "Error: 'depends_on' must be a JSON array of task or pipeline IDs."
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
        return `Error creating code-change pipeline (${resp.status}): ${await resp.text()}`
      }

      const data = (await resp.json()) as {
        id: string
        status: string
        repo?: string
        stages: { name: string; autonomy: string }[]
      }

      return [
        "Code-change pipeline dispatched successfully.",
        "",
        `  Pipeline ID:   ${data.id}`,
        `  Status:        ${data.status}`,
        `  Repo:          ${data.repo ?? args.repo}`,
        "  Workflow:      explore -> implement -> validate -> review",
        "  Validation:    fail closed on missing evidence",
        "  Failure path:  failed pipelines require human review",
        "",
        `Track:    curl ${baseUrl}/pipelines/${data.id}`,
      ].join("\n")
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
