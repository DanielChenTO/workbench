# workbench

Autonomous agent service — accepts work via API, delegates to [OpenCode](https://github.com/opencode-ai/opencode), and manages the full lifecycle of coding tasks: branching, execution, review, and PR creation.

## Features

- **Task management** — Submit coding tasks via REST API, CLI, or MCP server
- **Pipeline workflows** — Multi-stage pipelines (explore → plan → implement → review) with automatic review gating and retry loops
- **Concurrent execution** — Worker pool with git worktree isolation for parallel tasks
- **Live dashboard** — Real-time web UI with task status, logs, and event streaming (SSE)
- **Cron scheduler** — Recurring task and pipeline dispatch on cron schedules
- **Jira sync** — Read-only Jira integration to sync issues as local todo items
- **GitHub integration** — Automatic branch creation, PR submission, and issue resolution
- **MCP server** — Model Context Protocol interface for LLM-driven task dispatch

## Prerequisites

- **Python 3.11+**
- **Docker** (for PostgreSQL)
- **[OpenCode](https://github.com/opencode-ai/opencode)** — the CLI agent that executes tasks
- **Git** and **GitHub CLI** (`gh`) — for branch/PR operations

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/DanielChenTO/workbench.git
cd workbench
python3 -m venv .venv
source .venv/bin/activate
make install
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```env
WORKBENCH_WORKSPACE_ROOT=/path/to/your/workspace   # directory containing your git repos
```

All other settings have sensible defaults. See [.env.example](.env.example) for the full list.

### 3. Start the database

```bash
make db-up       # starts PostgreSQL on port 5433
make migrate     # runs schema migrations
```

### 4. Start the service

```bash
make serve
```

The API server starts at `http://127.0.0.1:8420`. Open the dashboard at [http://127.0.0.1:8420/dashboard](http://127.0.0.1:8420/dashboard).

## Usage

### Submit a task via CLI

```bash
# Run a prompt file
workbench run prompts/implement.json --repo my-service

# Plain text prompt
curl -X POST http://127.0.0.1:8420/tasks \
  -H "Content-Type: application/json" \
  -d '{"type": "prompt", "source": "Add input validation to the signup endpoint", "repo": "my-service"}'
```

### Check task status

```bash
workbench status <task-id>
```

### Stream logs

```bash
curl -N http://127.0.0.1:8420/tasks/<task-id>/logs
```

## API Overview

| Endpoint | Description |
|---|---|
| `GET /health` | Health check |
| `GET /dashboard` | Web UI |
| `POST /tasks` | Create a task |
| `GET /tasks` | List tasks |
| `GET /tasks/{id}` | Task details |
| `GET /tasks/{id}/logs` | Stream logs (SSE) |
| `POST /tasks/{id}/cancel` | Cancel a task |
| `POST /pipelines` | Create a multi-stage pipeline |
| `GET /pipelines` | List pipelines |
| `POST /schedules` | Create a cron schedule |
| `GET /events` | Event stream (SSE) |
| `GET /morning-report` | Daily summary |

## Development

```bash
make test          # run all tests
make test-quick    # skip integration tests
make lint          # ruff linter
make format        # auto-format
```

### Database management

```bash
make db-up         # start postgres
make db-down       # stop postgres
make db-reset      # destroy and recreate database
make migrate       # run migrations
make migrate-new msg="add foo column"   # create new migration
```

## Configuration

All settings use the `WORKBENCH_` prefix and can be set via environment variables or `.env` file.

| Variable | Default | Description |
|---|---|---|
| `WORKSPACE_ROOT` | auto-detected | Directory containing your git repos |
| `OPENCODE_BIN` | `opencode` | Path to opencode binary |
| `OPENCODE_MODEL` | *(opencode default)* | Override LLM model |
| `MAX_WORKERS` | `4` | Concurrent task workers |
| `TASK_TIMEOUT` | `1800` | Per-task timeout in seconds |
| `HOST` | `127.0.0.1` | API bind host |
| `PORT` | `8420` | API bind port |
| `DATABASE_URL` | `postgresql+asyncpg://...localhost:5433/workbench` | Async database URL |
| `BRANCH_PREFIX` | `agent` | Git branch prefix |
| `DEFAULT_BASE_BRANCH` | `main` | Base branch for PRs |
| `GITHUB_TOKEN` | *(falls back to `gh` CLI)* | GitHub API token |
| `JIRA_BASE_URL` | *(disabled)* | Jira instance URL |

## License

[CC0 1.0 Universal](LICENSE)
