"""Tests for workbench.jira_sync — read-only Jira sync to local todos."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from workbench.jira_sync import (
    JiraConfigurationError,
    JiraSafetyError,
    _ReadOnlyJiraClient,
    _map_priority,
    _map_status,
    sync_jira_issues,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_jira_issue(
    key: str = "PROJ-1",
    summary: str = "Test issue",
    status_name: str = "To Do",
    status_category: str = "To Do",
    priority_name: str = "Medium",
) -> dict:
    """Build a fake Jira issue dict matching the REST API shape."""
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "status": {
                "name": status_name,
                "statusCategory": {"name": status_category},
            },
            "priority": {"name": priority_name},
            "labels": ["backend"],
            "components": [{"name": "api"}],
            "description": "A test description",
            "assignee": {"displayName": "Test User"},
            "created": "2025-01-01T00:00:00.000+0000",
            "updated": "2025-01-02T00:00:00.000+0000",
        },
    }


def _make_search_response(
    issues: list[dict],
    total: int | None = None,
    start_at: int = 0,
) -> dict:
    """Build a fake Jira search response."""
    if total is None:
        total = len(issues)
    return {
        "issues": issues,
        "total": total,
        "startAt": start_at,
        "maxResults": 50,
    }


class FakeTodoRow:
    """Minimal stand-in for database.TodoRow used in unit tests."""

    def __init__(
        self,
        id: str = "abc123",
        title: str = "Test issue",
        status: str = "todo",
        priority: str = "medium",
        jira_key: str | None = None,
        jira_status: str | None = None,
        jira_last_synced: datetime | None = None,
    ):
        self.id = id
        self.title = title
        self.status = status
        self.priority = priority
        self.jira_key = jira_key
        self.jira_status = jira_status
        self.jira_last_synced = jira_last_synced


# ---------------------------------------------------------------------------
# _map_status tests
# ---------------------------------------------------------------------------

class TestMapStatus:
    def test_default_mapping_to_do(self):
        status = {"name": "To Do", "statusCategory": {"name": "To Do"}}
        assert _map_status(status, None) == "todo"

    def test_default_mapping_in_progress(self):
        status = {"name": "In Progress", "statusCategory": {"name": "In Progress"}}
        assert _map_status(status, None) == "in_progress"

    def test_default_mapping_done(self):
        status = {"name": "Done", "statusCategory": {"name": "Done"}}
        assert _map_status(status, None) == "done"

    def test_default_mapping_in_review(self):
        status = {"name": "In Review", "statusCategory": {"name": "In Progress"}}
        assert _map_status(status, None) == "review"

    def test_unmapped_status_returns_backlog(self):
        status = {"name": "Waiting for QA", "statusCategory": {"name": "Unknown"}}
        assert _map_status(status, None) == "backlog"

    def test_falls_back_to_category(self):
        """If the status name is not in the mapping, try the category."""
        status = {"name": "Custom Name", "statusCategory": {"name": "Done"}}
        assert _map_status(status, None) == "done"

    def test_none_status_returns_backlog(self):
        assert _map_status(None, None) == "backlog"

    def test_custom_mapping(self):
        custom = {"Awaiting Deploy": "review"}
        status = {"name": "Awaiting Deploy", "statusCategory": {"name": "In Progress"}}
        assert _map_status(status, custom) == "review"

    def test_custom_mapping_overrides_default(self):
        custom = {"Done": "deployed"}
        status = {"name": "Done", "statusCategory": {"name": "Done"}}
        assert _map_status(status, custom) == "deployed"


# ---------------------------------------------------------------------------
# _map_priority tests
# ---------------------------------------------------------------------------

class TestMapPriority:
    def test_highest_maps_to_high(self):
        assert _map_priority({"name": "Highest"}) == "high"

    def test_high_maps_to_high(self):
        """P1 fix: 'High' should map to 'high', not 'medium'."""
        assert _map_priority({"name": "High"}) == "high"

    def test_blocker_maps_to_high(self):
        assert _map_priority({"name": "Blocker"}) == "high"

    def test_critical_maps_to_high(self):
        assert _map_priority({"name": "Critical"}) == "high"

    def test_medium_maps_to_medium(self):
        assert _map_priority({"name": "Medium"}) == "medium"

    def test_low_maps_to_low(self):
        assert _map_priority({"name": "Low"}) == "low"

    def test_lowest_maps_to_low(self):
        assert _map_priority({"name": "Lowest"}) == "low"

    def test_none_priority_defaults_to_medium(self):
        assert _map_priority(None) == "medium"

    def test_unknown_priority_defaults_to_medium(self):
        assert _map_priority({"name": "Custom"}) == "medium"


# ---------------------------------------------------------------------------
# _ReadOnlyJiraClient safety tests
# ---------------------------------------------------------------------------

class TestReadOnlyJiraClient:
    @pytest.mark.asyncio
    async def test_get_request_is_allowed(self):
        """GET requests should succeed through the safety wrapper."""
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json={"ok": True})
        )
        async with httpx.AsyncClient(transport=transport, base_url="https://jira.test") as real_client:
            client = _ReadOnlyJiraClient("https://jira.test", {})
            client._client = real_client
            resp = await client.get("/rest/api/3/search")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_post_request_is_blocked(self):
        """POST requests should raise JiraSafetyError."""
        client = _ReadOnlyJiraClient("https://jira.test", {})
        client._client = MagicMock()  # Won't actually be used
        with pytest.raises(JiraSafetyError, match="Refusing to execute POST"):
            await client.request("POST", "/rest/api/3/issue")

    @pytest.mark.asyncio
    async def test_put_request_is_blocked(self):
        """PUT requests should raise JiraSafetyError."""
        client = _ReadOnlyJiraClient("https://jira.test", {})
        client._client = MagicMock()
        with pytest.raises(JiraSafetyError, match="Refusing to execute PUT"):
            await client.request("PUT", "/rest/api/3/issue/PROJ-1")

    @pytest.mark.asyncio
    async def test_delete_request_is_blocked(self):
        """DELETE requests should raise JiraSafetyError."""
        client = _ReadOnlyJiraClient("https://jira.test", {})
        client._client = MagicMock()
        with pytest.raises(JiraSafetyError, match="Refusing to execute DELETE"):
            await client.request("DELETE", "/rest/api/3/issue/PROJ-1")

    @pytest.mark.asyncio
    async def test_patch_request_is_blocked(self):
        """PATCH requests should raise JiraSafetyError."""
        client = _ReadOnlyJiraClient("https://jira.test", {})
        client._client = MagicMock()
        with pytest.raises(JiraSafetyError, match="Refusing to execute PATCH"):
            await client.request("PATCH", "/rest/api/3/issue/PROJ-1")

    @pytest.mark.asyncio
    async def test_get_via_request_method_is_allowed(self):
        """GET through request() should be allowed."""
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json={"ok": True})
        )
        async with httpx.AsyncClient(transport=transport, base_url="https://jira.test") as real_client:
            client = _ReadOnlyJiraClient("https://jira.test", {})
            client._client = real_client
            resp = await client.request("GET", "/rest/api/3/search")
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# sync_jira_issues tests (with mocked HTTP and database)
# ---------------------------------------------------------------------------

class TestSyncJiraIssues:
    """Tests for the core sync function with fully mocked dependencies."""

    @pytest.mark.asyncio
    async def test_creates_new_todos(self):
        """New Jira issues should create new local todos."""
        issues = [
            _make_jira_issue("PROJ-1", "First issue"),
            _make_jira_issue("PROJ-2", "Second issue", status_name="In Progress"),
        ]
        response = _make_search_response(issues)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = response

        mock_session = AsyncMock()

        with (
            patch("workbench.jira_sync._build_client") as mock_build,
            patch("workbench.jira_sync._sync_single_issue") as mock_sync,
        ):
            # Make _build_client return an async context manager
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_build.return_value = _make_async_cm(mock_client)

            # Each issue is "created"
            mock_sync.side_effect = ["created", "created"]

            result = await sync_jira_issues(
                mock_session, jql="project = PROJ"
            )

        assert result["created"] == 2
        assert result["updated"] == 0
        assert result["unchanged"] == 0
        assert result["errors"] == []
        assert isinstance(result["synced_at"], datetime)

    @pytest.mark.asyncio
    async def test_updates_existing_todos(self):
        """Existing Jira issues with changes should be updated."""
        issues = [_make_jira_issue("PROJ-1", "Updated summary")]
        response = _make_search_response(issues)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = response

        mock_session = AsyncMock()

        with (
            patch("workbench.jira_sync._build_client") as mock_build,
            patch("workbench.jira_sync._sync_single_issue") as mock_sync,
        ):
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_build.return_value = _make_async_cm(mock_client)

            mock_sync.return_value = "updated"

            result = await sync_jira_issues(
                mock_session, jql="project = PROJ"
            )

        assert result["created"] == 0
        assert result["updated"] == 1
        assert result["unchanged"] == 0

    @pytest.mark.asyncio
    async def test_unchanged_todos(self):
        """Issues that haven't changed should be counted as unchanged."""
        issues = [_make_jira_issue("PROJ-1")]
        response = _make_search_response(issues)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = response

        mock_session = AsyncMock()

        with (
            patch("workbench.jira_sync._build_client") as mock_build,
            patch("workbench.jira_sync._sync_single_issue") as mock_sync,
        ):
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_build.return_value = _make_async_cm(mock_client)

            mock_sync.return_value = "unchanged"

            result = await sync_jira_issues(
                mock_session, jql="project = PROJ"
            )

        assert result["unchanged"] == 1

    @pytest.mark.asyncio
    async def test_empty_jira_results(self):
        """Empty Jira results should return zeros with no errors."""
        response = _make_search_response([])

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = response

        mock_session = AsyncMock()

        with patch("workbench.jira_sync._build_client") as mock_build:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_build.return_value = _make_async_cm(mock_client)

            result = await sync_jira_issues(
                mock_session, jql="project = EMPTY"
            )

        assert result["created"] == 0
        assert result["updated"] == 0
        assert result["unchanged"] == 0
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_auth_failure(self):
        """401/403 from Jira should produce an error, not raise."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"

        mock_session = AsyncMock()

        with patch("workbench.jira_sync._build_client") as mock_build:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_build.return_value = _make_async_cm(mock_client)

            result = await sync_jira_issues(
                mock_session, jql="project = PROJ"
            )

        assert len(result["errors"]) == 1
        assert "authentication failed" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_bad_jql(self):
        """400 from Jira (bad JQL) should produce an error, not raise."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Error in JQL query"

        mock_session = AsyncMock()

        with patch("workbench.jira_sync._build_client") as mock_build:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_build.return_value = _make_async_cm(mock_client)

            result = await sync_jira_issues(
                mock_session, jql="invalid jql!!!"
            )

        assert len(result["errors"]) == 1
        assert "400" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_network_timeout(self):
        """Network timeout should produce an error, not raise."""
        mock_session = AsyncMock()

        with patch("workbench.jira_sync._build_client") as mock_build:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.TimeoutException("timed out")
            mock_build.return_value = _make_async_cm(mock_client)

            result = await sync_jira_issues(
                mock_session, jql="project = PROJ"
            )

        assert len(result["errors"]) == 1
        assert "timed out" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_individual_issue_failure_continues(self):
        """If one issue fails to sync, others should still proceed."""
        issues = [
            _make_jira_issue("PROJ-1", "Good issue"),
            _make_jira_issue("PROJ-2", "Bad issue"),
            _make_jira_issue("PROJ-3", "Good issue 2"),
        ]
        response = _make_search_response(issues)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = response

        mock_session = AsyncMock()

        with (
            patch("workbench.jira_sync._build_client") as mock_build,
            patch("workbench.jira_sync._sync_single_issue") as mock_sync,
        ):
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_build.return_value = _make_async_cm(mock_client)

            # Second issue raises, others succeed
            mock_sync.side_effect = [
                "created",
                Exception("DB write failed"),
                "created",
            ]

            result = await sync_jira_issues(
                mock_session, jql="project = PROJ"
            )

        assert result["created"] == 2
        assert len(result["errors"]) == 1
        assert "PROJ-2" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_only_get_requests_made(self):
        """Verify that only GET requests are made to Jira during sync."""
        issues = [_make_jira_issue("PROJ-1")]
        response = _make_search_response(issues)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = response

        mock_session = AsyncMock()

        with (
            patch("workbench.jira_sync._build_client") as mock_build,
            patch("workbench.jira_sync._sync_single_issue") as mock_sync,
        ):
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_build.return_value = _make_async_cm(mock_client)

            mock_sync.return_value = "created"

            await sync_jira_issues(
                mock_session, jql="project = PROJ"
            )

        # Verify ONLY get was called (no post, put, patch, delete)
        mock_client.get.assert_called()
        # These methods should not exist or not be called
        assert not getattr(mock_client, "post", AsyncMock()).called
        assert not getattr(mock_client, "put", AsyncMock()).called
        assert not getattr(mock_client, "patch", AsyncMock()).called
        assert not getattr(mock_client, "delete", AsyncMock()).called

    @pytest.mark.asyncio
    async def test_pagination_fetches_all_pages(self):
        """When total > max_results, multiple pages should be fetched."""
        page1_issues = [_make_jira_issue(f"PROJ-{i}") for i in range(1, 3)]
        page2_issues = [_make_jira_issue(f"PROJ-{i}") for i in range(3, 5)]

        page1_resp = MagicMock()
        page1_resp.status_code = 200
        page1_resp.json.return_value = _make_search_response(
            page1_issues, total=4, start_at=0
        )

        page2_resp = MagicMock()
        page2_resp.status_code = 200
        page2_resp.json.return_value = _make_search_response(
            page2_issues, total=4, start_at=2
        )

        mock_session = AsyncMock()

        with (
            patch("workbench.jira_sync._build_client") as mock_build,
            patch("workbench.jira_sync._sync_single_issue") as mock_sync,
        ):
            mock_client = AsyncMock()
            mock_client.get.side_effect = [page1_resp, page2_resp]
            mock_build.return_value = _make_async_cm(mock_client)

            mock_sync.return_value = "created"

            result = await sync_jira_issues(
                mock_session, jql="project = PROJ", max_results=2
            )

        assert result["created"] == 4
        assert mock_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_custom_status_mapping(self):
        """Custom status mapping should be passed through to _sync_single_issue."""
        issues = [_make_jira_issue("PROJ-1")]
        response = _make_search_response(issues)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = response

        mock_session = AsyncMock()
        custom_map = {"To Do": "pending", "Done": "shipped"}

        with (
            patch("workbench.jira_sync._build_client") as mock_build,
            patch("workbench.jira_sync._sync_single_issue") as mock_sync,
        ):
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_build.return_value = _make_async_cm(mock_client)

            mock_sync.return_value = "created"

            await sync_jira_issues(
                mock_session,
                jql="project = PROJ",
                status_mapping=custom_map,
            )

        # Verify custom mapping was passed to _sync_single_issue
        call_args = mock_sync.call_args
        assert call_args[0][3] == custom_map  # 4th positional arg is status_mapping


# ---------------------------------------------------------------------------
# Configuration error tests
# ---------------------------------------------------------------------------

class TestJiraConfiguration:
    @pytest.mark.asyncio
    async def test_missing_base_url_raises(self):
        """sync_jira_issues should raise JiraConfigurationError if base URL is empty."""
        mock_session = AsyncMock()

        with patch("workbench.jira_sync.settings") as mock_settings:
            mock_settings.jira_base_url = ""
            mock_settings.jira_api_token = "token"
            mock_settings.jira_user_email = "test@example.com"

            with pytest.raises(JiraConfigurationError, match="base URL"):
                await sync_jira_issues(
                    mock_session, jql="project = PROJ"
                )

    @pytest.mark.asyncio
    async def test_no_credentials_proceeds_unauthenticated(self):
        """Missing credentials should NOT raise — proceeds unauthenticated.

        This matches the auth pattern from resolvers.py.
        """
        issues = []
        response = _make_search_response(issues)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = response

        mock_session = AsyncMock()

        with patch("workbench.jira_sync.settings") as mock_settings:
            mock_settings.jira_base_url = "https://jira.test"
            mock_settings.jira_api_token = None
            mock_settings.jira_user_email = None

            # Use a real transport mock for the client
            with patch("workbench.jira_sync._build_client") as mock_build:
                mock_client = AsyncMock()
                mock_client.get.return_value = mock_resp
                mock_build.return_value = _make_async_cm(mock_client)

                # Should NOT raise
                result = await sync_jira_issues(
                    mock_session, jql="project = PROJ"
                )

        assert result["errors"] == []


# ---------------------------------------------------------------------------
# Exception hierarchy tests
# ---------------------------------------------------------------------------

class TestExceptionHierarchy:
    def test_jira_configuration_error_has_operation(self):
        """JiraConfigurationError should carry operation='jira_sync'."""
        exc = JiraConfigurationError("test message")
        assert exc.operation == "jira_sync"
        assert "jira_sync" in str(exc)

    def test_jira_safety_error_has_operation(self):
        """JiraSafetyError should carry operation='jira_sync'."""
        exc = JiraSafetyError("test message")
        assert exc.operation == "jira_sync"
        assert "jira_sync" in str(exc)

    def test_exceptions_inherit_from_workbench_error(self):
        """Both exceptions should inherit from WorkbenchError."""
        from workbench.exceptions import WorkbenchError

        assert issubclass(JiraConfigurationError, WorkbenchError)
        assert issubclass(JiraSafetyError, WorkbenchError)


# ---------------------------------------------------------------------------
# Helper: async context manager wrapper
# ---------------------------------------------------------------------------

class _AsyncCM:
    """Wraps an object to act as an async context manager."""

    def __init__(self, obj):
        self._obj = obj

    async def __aenter__(self):
        return self._obj

    async def __aexit__(self, *exc_info):
        pass


def _make_async_cm(obj):
    """Create an async context manager that yields *obj*."""
    return _AsyncCM(obj)
