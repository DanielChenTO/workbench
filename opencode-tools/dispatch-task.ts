import { tool } from "@opencode-ai/plugin"

export default tool({
  description:
    "Dispatch a task to the workbench autonomous worker service. " +
    "Use this to delegate work (Jira tickets, GitHub issues, plain prompts, or prompt files) " +
    "to a background agent that will execute it headlessly via opencode run. " +
    "Tasks run concurrently in the worker pool and can create branches and draft PRs. " +
    "Returns the task ID and status for tracking.",
  args: {
    type: tool.schema
      .enum(["jira", "github_issue", "prompt", "prompt_file"])
      .describe(
        "Input type: 'jira' (Jira key like PROJ-1234), 'github_issue' (GitHub issue URL), " +
        "'prompt' (plain text), or 'prompt_file' (inline file content)"
      ),
    source: tool.schema
      .string()
      .describe(
        "The task source: Jira key, GitHub issue URL, plain text prompt, " +
        "or description when using prompt_file type. " +
        "For type='prompt', this IS the prompt text — write the full task description here."
      ),
    repo: tool.schema
      .string()
      .optional()
      .describe(
        "Target repository short name (e.g. 'my-service'). " +
        "If omitted, the service tries to infer it from the source."
      ),
    autonomy: tool.schema
      .enum(["full", "local", "plan_only", "research"])
      .optional()
      .describe(
        "Autonomy level: 'full' (code + push + PR), 'local' (code + commit, no push/PR), " +
        "'plan_only' (plan only), 'research' (investigate only). Defaults to 'full'."
      ),
    model: tool.schema
      .string()
      .optional()
      .describe("Override the LLM model for this task."),
    extra_instructions: tool.schema
      .string()
      .optional()
      .describe("Additional instructions appended to the agent prompt."),
    file_content: tool.schema
      .string()
      .optional()
      .describe(
        "Inline content of a .md or .json prompt file. " +
        "Used when type='prompt_file'."
      ),
    context: tool.schema
      .string()
      .optional()
      .describe(
        "JSON array of context items to inject into the agent prompt. " +
        "Each item has a 'type' field: 'task_output' (needs task_id), " +
        "'reference' (needs doc, optional section), 'file' (needs path, optional lines), " +
        "or 'text' (needs content). All items support optional 'label' and 'max_lines'. " +
        "Example: '[{\"type\":\"task_output\",\"task_id\":\"abc123\",\"label\":\"Prior research\"}]'"
      ),
    depends_on: tool.schema
      .string()
      .optional()
      .describe(
        "JSON array of task IDs that must complete before this task starts. " +
        "Example: '[\"abc123\",\"def456\"]'"
      ),
    parent_task_id: tool.schema
      .string()
      .optional()
      .describe(
        "Parent task ID for chaining. The parent's output is auto-injected as context. " +
        "The parent task must already be completed."
      ),
  },
  async execute(args) {
    const baseUrl = process.env.WORKBENCH_URL || "http://127.0.0.1:8420"

    // Validate source field for type=prompt — catch the common mistake of
    // sending a single word or placeholder instead of the actual prompt text.
    if (args.type === "prompt") {
      const wordCount = args.source.trim().split(/\s+/).length
      if (wordCount <= 2) {
        return (
          `Warning: source field for type='prompt' contains only ${wordCount} word(s): "${args.source.trim()}"\n` +
          `For type='prompt', the 'source' field IS the prompt text — it should contain the full task description.\n` +
          `Did you mean to put your prompt text in 'source'?`
        )
      }
    }

    const payload: Record<string, unknown> = {
      type: args.type,
      source: args.source,
    }

    if (args.repo) payload.repo = args.repo
    if (args.autonomy) payload.autonomy = args.autonomy
    if (args.model) payload.model = args.model
    if (args.extra_instructions) payload.extra_instructions = args.extra_instructions
    if (args.file_content) payload.file_content = args.file_content
    if (args.context) {
      try {
        payload.context = JSON.parse(args.context)
      } catch {
        return `Error: 'context' must be valid JSON. Got: ${args.context}`
      }
    }
    if (args.depends_on) {
      try {
        const deps = JSON.parse(args.depends_on)
        if (!Array.isArray(deps)) {
          return "Error: 'depends_on' must be a JSON array of task IDs."
        }
        payload.depends_on = deps
      } catch {
        return `Error: 'depends_on' must be valid JSON. Got: ${args.depends_on}`
      }
    }
    if (args.parent_task_id) payload.parent_task_id = args.parent_task_id

    try {
      const resp = await fetch(`${baseUrl}/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })

      if (!resp.ok) {
        const errorText = await resp.text()
        return `Error dispatching task (${resp.status}): ${errorText}`
      }

      const data = await resp.json() as {
        id: string
        status: string
        input: { type: string; source: string; repo?: string }
      }

      const lines = [
        `Task dispatched successfully.`,
        ``,
        `  Task ID:  ${data.id}`,
        `  Status:   ${data.status}`,
        `  Type:     ${data.input.type}`,
        `  Source:   ${data.input.source}`,
      ]
      if (data.input.repo) {
        lines.push(`  Repo:     ${data.input.repo}`)
      }
      lines.push(
        ``,
        `Track:    curl ${baseUrl}/tasks/${data.id}`,
        `Stream:   curl ${baseUrl}/tasks/${data.id}/logs`,
        `Unblock:  curl -X POST ${baseUrl}/tasks/${data.id}/unblock -d '{"response":"..."}'`,
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
