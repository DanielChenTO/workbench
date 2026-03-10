.PHONY: help db-up db-down db-reset migrate migrate-new lint format test serve serve-bg install clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

db-up: ## Start Postgres via docker compose
	docker compose up -d

db-down: ## Stop Postgres
	docker compose down

db-reset: ## Destroy and recreate the database volume
	docker compose down -v
	docker compose up -d

migrate: ## Run Alembic migrations to head
	alembic upgrade head

migrate-new: ## Create a new migration (usage: make migrate-new msg="add foo column")
	alembic revision --autogenerate -m "$(msg)"

# ---------------------------------------------------------------------------
# Code quality
# ---------------------------------------------------------------------------

lint: ## Run ruff linter
	ruff check workbench/ tests/

format: ## Auto-format with ruff
	ruff format workbench/ tests/
	ruff check --fix workbench/ tests/

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

test: ## Run tests
	python -m pytest tests/ -v

test-quick: ## Run tests (no integration)
	python -m pytest tests/ -v -m "not integration"

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

serve: ## Start the workbench service
	workbench serve

serve-bg: ## Start workbench in background with sleep prevention (for overnight runs)
	@echo "Starting workbench with caffeinate (prevents all sleep modes)..."
	@echo "PID file: /tmp/workbench-serve.pid"
	caffeinate -dims workbench serve &
	@echo $$! > /tmp/workbench-serve.pid
	@echo "Workbench running (PID: $$(cat /tmp/workbench-serve.pid))"
	@echo "Stop with: kill $$(cat /tmp/workbench-serve.pid)"

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

install: ## Install in dev mode with all dependencies
	pip install -e ".[dev]"

clean: ## Remove build artifacts and caches
	rm -rf build/ dist/ *.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
