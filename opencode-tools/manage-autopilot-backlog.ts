import { promises as fs } from "node:fs"
import path from "node:path"

import { tool } from "@opencode-ai/plugin/tool"

type Action = "list" | "add_active" | "park_review" | "complete" | "sync_from_todos"

type BacklogState = {
  active: string[]
  review: string[]
  notes: string[]
}

type TodoRecord = {
  id: string
  title: string
  description?: string | null
  status?: string
  priority?: string
  tags?: string[] | null
}

export type { BacklogState, TodoRecord }

export {
  parseBacklog,
  normalizeItem,
  addUnique,
  removeMatching,
  titleOf,
  backlogFromTodos,
}

export default tool({
  description:
    "Manage the persistent local autopilot backlog and keep it aligned with workbench todos. " +
    "Useful for adding active items, parking review-required failures, marking work complete, and syncing from todos.",
  args: {
    action: tool.schema
      .enum(["list", "add_active", "park_review", "complete", "sync_from_todos"] satisfies Action[])
      .describe("Backlog operation to perform."),
    title: tool.schema.string().optional().describe("Backlog item title for add/park/complete operations."),
    details: tool.schema.string().optional().describe("Optional details or failure report snippet."),
    priority: tool.schema.string().optional().describe("Todo priority to use when creating or updating a mirrored workbench todo."),
  },
  async execute(args) {
    const backlogPath = path.resolve(process.cwd(), "work-directory/backlog.md")
    const state = await loadBacklog(backlogPath)

    if (args.action === "sync_from_todos") {
      const todos = await loadAutopilotTodos()
      const synced = backlogFromTodos(todos, state.notes)
      await saveBacklog(backlogPath, synced)
      return renderBacklog(`Synced backlog from ${todos.length} autopilot todo(s).`, synced)
    }

    if (args.action === "list") {
      return renderBacklog("Current local autopilot backlog.", state)
    }

    const title = args.title?.trim()
    if (!title) {
      return `Error: 'title' is required for action '${args.action}'.`
    }

    const item = normalizeItem(title, args.details)
    let message = ""

    if (args.action === "add_active") {
      state.active = addUnique(state.active, item)
      state.review = removeMatching(state.review, title)
      await upsertTodo(title, args.details, "todo", args.priority ?? "medium", ["autopilot", "backlog"])
      message = `Added active backlog item: ${title}`
    } else if (args.action === "park_review") {
      state.active = removeMatching(state.active, title)
      state.review = addUnique(state.review, item)
      await upsertTodo(title, args.details, "review", args.priority ?? "high", ["autopilot", "review_queue"])
      message = `Parked item in review queue: ${title}`
    } else if (args.action === "complete") {
      state.active = removeMatching(state.active, title)
      state.review = removeMatching(state.review, title)
      await upsertTodo(title, args.details, "done", args.priority ?? "medium", ["autopilot", "done"])
      message = `Marked item complete: ${title}`
    }

    await saveBacklog(backlogPath, state)
    return renderBacklog(message, state)
  },
})

async function loadBacklog(filePath: string): Promise<BacklogState> {
  try {
    const raw = await fs.readFile(filePath, "utf8")
    return parseBacklog(raw)
  } catch {
    return {
      active: [],
      review: [],
      notes: [
        "- Local autopilot should only work on items that can be completed with local changes.",
        "- Failed validation should move the item into `Review Queue` with the failure report.",
        "- When an item is completed, move it into the session log instead of keeping long history here.",
      ],
    }
  }
}

function parseBacklog(raw: string): BacklogState {
  const lines = raw.split(/\r?\n/)
  const state: BacklogState = { active: [], review: [], notes: [] }
  let section: "active" | "review" | "notes" | null = null
  for (const line of lines) {
    if (line.startsWith("## Active")) {
      section = "active"
      continue
    }
    if (line.startsWith("## Review Queue")) {
      section = "review"
      continue
    }
    if (line.startsWith("## Notes")) {
      section = "notes"
      continue
    }
    if (!line.trim()) continue
    if (section === "active" && line.startsWith("- [ ] ")) state.active.push(line.slice(6))
    if (section === "review" && line.startsWith("- [ ] ")) state.review.push(line.slice(6))
    if (section === "notes" && line.startsWith("- ")) state.notes.push(line)
  }
  return state
}

async function saveBacklog(filePath: string, state: BacklogState): Promise<void> {
  const text = [
    "# Local Backlog",
    "",
    "Use this file as the persistent queue for local autopilot work.",
    "",
    "## Active",
    "",
    ...renderItems(state.active),
    "",
    "## Review Queue",
    "",
    ...renderItems(state.review),
    "",
    "## Notes",
    "",
    ...(state.notes.length ? state.notes : ["- No notes yet"]),
    "",
  ].join("\n")
  await fs.mkdir(path.dirname(filePath), { recursive: true })
  await fs.writeFile(filePath, text, "utf8")
}

function renderItems(items: string[]): string[] {
  return items.length ? items.map((item) => `- [ ] ${item}`) : ["- [ ] No items"]
}

function normalizeItem(title: string, details?: string): string {
  return details?.trim() ? `${title} — ${details.trim()}` : title
}

function addUnique(items: string[], item: string): string[] {
  const title = titleOf(item)
  return items.some((existing) => titleOf(existing) === title) ? items.map((existing) => titleOf(existing) === title ? item : existing) : [...items, item]
}

function removeMatching(items: string[], title: string): string[] {
  return items.filter((item) => titleOf(item) !== title)
}

function titleOf(item: string): string {
  return item.split(" — ")[0]?.trim() ?? item.trim()
}

function renderBacklog(prefix: string, state: BacklogState): string {
  return [
    prefix,
    "",
    `Active: ${state.active.length}`,
    ...state.active.map((item) => `  - ${item}`),
    "",
    `Review Queue: ${state.review.length}`,
    ...state.review.map((item) => `  - ${item}`),
  ].join("\n")
}

async function loadAutopilotTodos(): Promise<TodoRecord[]> {
  const baseUrl = process.env.WORKBENCH_URL || "http://127.0.0.1:8420"
  const resp = await fetch(`${baseUrl}/todos?limit=500`)
  if (!resp.ok) {
    throw new Error(`Failed to load todos (${resp.status}): ${await resp.text()}`)
  }
  const data = (await resp.json()) as { todos?: TodoRecord[] }
  return (data.todos ?? []).filter((todo) => (todo.tags ?? []).includes("autopilot"))
}

function backlogFromTodos(todos: TodoRecord[], notes: string[]): BacklogState {
  const autopilotTodos = todos.filter((todo) => (todo.tags ?? []).includes("autopilot"))
  const active = autopilotTodos
    .filter((todo) => ["backlog", "todo", "in_progress"].includes(todo.status ?? ""))
    .map((todo) => normalizeItem(todo.title, todo.description ?? undefined))
  const review = autopilotTodos
    .filter((todo) => todo.status === "review" || (todo.tags ?? []).includes("review_queue"))
    .map((todo) => normalizeItem(todo.title, todo.description ?? undefined))
  return { active, review, notes }
}

async function upsertTodo(
  title: string,
  details: string | undefined,
  status: string,
  priority: string,
  tags: string[],
): Promise<void> {
  const baseUrl = process.env.WORKBENCH_URL || "http://127.0.0.1:8420"
  const todos = await loadAutopilotTodos().catch(() => [])
  const existing = todos.find((todo) => todo.title === title)
  const body = {
    title,
    description: details,
    status,
    priority,
    tags,
  }

  if (existing) {
    await fetch(`${baseUrl}/todos/${existing.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
    return
  }

  await fetch(`${baseUrl}/todos`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
}
