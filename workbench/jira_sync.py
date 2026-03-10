"""READ-ONLY Jira sync. This module NEVER writes to Jira.

Fetches Jira issues matching JQL queries via the REST API and syncs them
to local todo items in the workbench database.  Only HTTP GET requests
are ever made to the Jira API — the module enforces this invariant with
a safety wrapper around the HTTP client.
"""

from __future__ import annotations

import base64
import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .exceptions import WorkbenchError

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class JiraConfigurationError(WorkbenchError):
    """Raised when Jira connectivity is not configured."""

    def __init__(self, message: str) -> None:
        super().__init__(message, operation="jira_sync")


class JiraSafetyError(WorkbenchError):
    """Raised if a non-GET request is attempted against Jira."""

    def __init__(self, message: str) -> None:
        super().__init__(message, operation="jira_sync")


# ---------------------------------------------------------------------------
# Read-only Jira HTTP client
# ---------------------------------------------------------------------------

_ALLOWED_METHODS = frozenset({"GET"})


class _ReadOnlyJiraClient:
    """Thin HTTP wrapper that enforces read-only access to Jira.

    Only GET requests are permitted.  Any other HTTP method raises
    ``JiraSafetyError`` immediately, preventing accidental writes.

    The client reuses a single ``httpx.AsyncClient`` for connection
    pooling and must be used as an async context manager.
    """

    def __init__(self, base_url: str, headers: dict[str, str]) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = headers
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> _ReadOnlyJiraClient:
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers,
            timeout=30,
        )
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get(self, path: str, **kwargs) -> httpx.Response:
        """Perform a GET request.  Only GET is allowed."""
        if self._client is None:
            raise JiraSafetyError(
                "Client not initialised — use as async context manager"
            )
        return await self._client.get(path, **kwargs)

    async def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Safety-gated request.  Rejects anything other than GET."""
        method_upper = method.upper()
        if method_upper not in _ALLOWED_METHODS:
            raise JiraSafetyError(
                f"Refusing to execute {method_upper} against Jira — "
                "only GET is allowed (read-only sync)"
            )
        return await self.get(path, **kwargs)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Default mapping from Jira status category/name to local kanban column.
_DEFAULT_STATUS_MAP: dict[str, str] = {
    "To Do": "todo",
    "In Progress": "in_progress",
    "In Review": "review",
    "Done": "done",
}


def _map_status(
    jira_status: dict | None,
    custom_mapping: dict[str, str] | None,
) -> str:
    """Map a Jira status object to a local kanban column name.

    Tries the status *name* first, then falls back to the status
    *category name*.  If nothing matches, returns ``"backlog"``.
    """
    if jira_status is None:
        return "backlog"

    mapping = custom_mapping if custom_mapping else _DEFAULT_STATUS_MAP
    name = jira_status.get("name", "")
    category = (jira_status.get("statusCategory") or {}).get("name", "")

    if name in mapping:
        return mapping[name]
    if category in mapping:
        return mapping[category]
    return "backlog"


def _map_priority(jira_priority: dict | None) -> str:
    """Map a Jira priority to a local priority string.

    Jira's standard priority names: Highest, High, Medium, Low, Lowest.
    We map these to: high, high, medium, low, low.  Blocker and Critical
    also map to high.
    """
    if jira_priority is None:
        return "medium"

    name = (jira_priority.get("name") or "").strip()
    upper = name.upper()
    if upper in ("HIGHEST", "HIGH", "BLOCKER", "CRITICAL"):
        return "high"
    if upper in ("LOWEST", "LOW"):
        return "low"
    return "medium"


def _build_client(base_url: str) -> _ReadOnlyJiraClient:
    """Build a ``_ReadOnlyJiraClient`` with optional auth.

    Follows the same auth pattern as ``resolvers.py``: if both
    ``jira_api_token`` and ``jira_user_email`` are set, use Basic auth.
    Otherwise proceed without authentication (supports anonymous Jira
    instances).
    """
    if not base_url:
        raise JiraConfigurationError(
            "Jira base URL is not configured. "
            "Set WORKBENCH_JIRA_BASE_URL to enable Jira sync."
        )

    headers: dict[str, str] = {"Accept": "application/json"}

    # Graceful auth — same pattern as resolvers.py:39
    if settings.jira_api_token and settings.jira_user_email:
        creds = base64.b64encode(
            f"{settings.jira_user_email}:{settings.jira_api_token}".encode()
        ).decode()
        headers["Authorization"] = f"Basic {creds}"

    return _ReadOnlyJiraClient(base_url, headers)


# ---------------------------------------------------------------------------
# Fields we request from Jira
# ---------------------------------------------------------------------------

_JIRA_FIELDS = (
    "summary,status,labels,components,description,"
    "priority,assignee,created,updated"
)


# ---------------------------------------------------------------------------
# Core sync function
# ---------------------------------------------------------------------------

async def sync_jira_issues(
    session: AsyncSession,
    *,
    jql: str,
    max_results: int = 50,
    status_mapping: dict[str, str] | None = None,
) -> dict:
    """Fetch Jira issues matching *jql* and sync to local todo items.

    READ-ONLY: This function only reads from Jira, never writes.

    Returns a dict matching the ``JiraSyncResult`` schema with keys:
    ``created``, ``updated``, ``unchanged``, ``errors``, ``synced_at``.

    Implements pagination: if the JQL matches more than *max_results*
    issues, multiple pages are fetched until all results are consumed.
    """
    from .database import get_todo_by_jira_key, create_todo, update_todo

    base_url = settings.jira_base_url
    created = 0
    updated = 0
    unchanged = 0
    errors: list[str] = []

    async with _build_client(base_url) as client:
        start_at = 0
        total: int | None = None

        while True:
            # Fetch a page of results
            try:
                resp = await client.get(
                    "/rest/api/3/search",
                    params={
                        "jql": jql,
                        "maxResults": max_results,
                        "startAt": start_at,
                        "fields": _JIRA_FIELDS,
                    },
                )
            except httpx.TimeoutException:
                errors.append(
                    f"Jira request timed out (startAt={start_at})"
                )
                break
            except httpx.HTTPError as exc:
                errors.append(f"Jira HTTP error: {exc}")
                break

            if resp.status_code == 401 or resp.status_code == 403:
                errors.append(
                    "Jira authentication failed — check "
                    "WORKBENCH_JIRA_USER_EMAIL and WORKBENCH_JIRA_API_TOKEN"
                )
                break

            if resp.status_code == 400:
                # Bad JQL — report and stop
                detail = resp.text[:300]
                errors.append(f"Jira returned 400 (bad JQL?): {detail}")
                break

            if resp.status_code != 200:
                errors.append(
                    f"Jira returned {resp.status_code}: {resp.text[:300]}"
                )
                break

            data = resp.json()
            issues = data.get("issues", [])
            total = data.get("total", 0)

            for issue in issues:
                try:
                    result = await _sync_single_issue(
                        session, issue, base_url, status_mapping
                    )
                    if result == "created":
                        created += 1
                    elif result == "updated":
                        updated += 1
                    else:
                        unchanged += 1
                except Exception as exc:
                    key = issue.get("key", "???")
                    log.warning("Failed to sync Jira issue %s: %s", key, exc)
                    errors.append(f"Failed to sync {key}: {exc}")

            # Advance to next page
            start_at += len(issues)
            if not issues or start_at >= total:
                break

    return {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "errors": errors,
        "synced_at": datetime.now(UTC),
    }


async def _sync_single_issue(
    session: AsyncSession,
    issue: dict,
    base_url: str,
    status_mapping: dict[str, str] | None,
) -> str:
    """Sync a single Jira issue to the local todo table.

    Returns ``"created"``, ``"updated"``, or ``"unchanged"``.
    """
    from .database import get_todo_by_jira_key, create_todo, update_todo

    key = issue["key"]
    fields = issue.get("fields", {})
    summary = fields.get("summary", "")
    jira_status_obj = fields.get("status")
    jira_status_name = (jira_status_obj or {}).get("name", "")
    priority_obj = fields.get("priority")
    browse_url = f"{base_url.rstrip('/')}/browse/{key}"

    local_status = _map_status(jira_status_obj, status_mapping)
    local_priority = _map_priority(priority_obj)

    existing = await get_todo_by_jira_key(session, key)

    if existing is None:
        # Create new todo
        await create_todo(
            session,
            title=summary,
            status=local_status,
            priority=local_priority,
            source="jira",
            jira_key=key,
            jira_url=browse_url,
            jira_status=jira_status_name,
            jira_last_synced=datetime.now(UTC),
        )
        return "created"

    # Check if anything changed
    changed = False
    update_fields: dict = {}

    if existing.title != summary:
        update_fields["title"] = summary
        changed = True

    if existing.jira_status != jira_status_name:
        update_fields["jira_status"] = jira_status_name
        update_fields["status"] = local_status
        changed = True

    if existing.priority != local_priority:
        update_fields["priority"] = local_priority
        changed = True

    if changed:
        update_fields["jira_last_synced"] = datetime.now(UTC)
        await update_todo(session, existing.id, **update_fields)
        return "updated"

    # Touch jira_last_synced even if nothing changed
    await update_todo(
        session, existing.id, jira_last_synced=datetime.now(UTC)
    )
    return "unchanged"
