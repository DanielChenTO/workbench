"""Unit tests for workbench.resolvers — input type resolvers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import httpx
import pytest

from workbench.config import Settings, settings
from workbench.exceptions import TaskResolutionError
from workbench.models import TaskCreate, TaskInputType
from workbench.resolvers import (
    _detect_format,
    _extract_acceptance_criteria,
    _extract_jira_description,
    _extract_repo_from_markdown,
    _parse_gh_issue_ref,
    _parse_json_prompt,
    resolve_github_issue,
    resolve_jira,
    resolve_prompt,
    resolve_prompt_file,
)

# ---------------------------------------------------------------------------
# _parse_gh_issue_ref
# ---------------------------------------------------------------------------


class TestParseGhIssueRef:
    def test_full_url(self):
        owner, repo, number = _parse_gh_issue_ref(
            "https://github.com/acme/widgets/issues/123"
        )
        assert owner == "acme"
        assert repo == "widgets"
        assert number == 123

    def test_full_url_http(self):
        owner, repo, number = _parse_gh_issue_ref(
            "http://github.com/org/repo/issues/999"
        )
        assert owner == "org"
        assert repo == "repo"
        assert number == 999

    def test_shorthand(self):
        owner, repo, number = _parse_gh_issue_ref("acme/widgets#456")
        assert owner == "acme"
        assert repo == "widgets"
        assert number == 456

    def test_shorthand_with_dashes(self):
        owner, repo, number = _parse_gh_issue_ref("my-org/my-repo#1")
        assert owner == "my-org"
        assert repo == "my-repo"
        assert number == 1

    def test_shorthand_trailing_whitespace(self):
        """Repo name should be stripped of whitespace."""
        owner, repo, number = _parse_gh_issue_ref("owner/repo #10")
        assert owner == "owner"
        assert repo == "repo"
        assert number == 10

    def test_invalid_bare_number(self):
        with pytest.raises(TaskResolutionError, match="Cannot parse"):
            _parse_gh_issue_ref("123")

    def test_invalid_empty(self):
        with pytest.raises(TaskResolutionError, match="Cannot parse"):
            _parse_gh_issue_ref("")

    def test_invalid_no_number(self):
        with pytest.raises(TaskResolutionError, match="Cannot parse"):
            _parse_gh_issue_ref("owner/repo")

    def test_invalid_random_url(self):
        with pytest.raises(TaskResolutionError, match="Cannot parse"):
            _parse_gh_issue_ref("https://example.com/not-github")

    def test_large_issue_number(self):
        owner, repo, number = _parse_gh_issue_ref("org/repo#99999")
        assert number == 99999

    def test_url_with_trailing_slash(self):
        """URL without a proper issue number pattern should fail."""
        with pytest.raises(TaskResolutionError, match="Cannot parse"):
            _parse_gh_issue_ref("https://github.com/owner/repo/issues/")


# ---------------------------------------------------------------------------
# _extract_jira_description
# ---------------------------------------------------------------------------


class TestExtractJiraDescription:
    def test_none(self):
        assert _extract_jira_description(None) == ""

    def test_plain_string(self):
        assert _extract_jira_description("Hello world") == "Hello world"

    def test_adf_simple_paragraph(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "A simple paragraph."}],
                }
            ],
        }
        assert _extract_jira_description(adf) == "A simple paragraph."

    def test_adf_multiple_paragraphs(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "First."}],
                },
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Second."}],
                },
            ],
        }
        result = _extract_jira_description(adf)
        assert "First." in result
        assert "Second." in result

    def test_adf_with_hard_break(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Before"},
                        {"type": "hardBreak"},
                        {"type": "text", "text": "After"},
                    ],
                }
            ],
        }
        result = _extract_jira_description(adf)
        assert "Before" in result
        assert "After" in result
        assert "\n" in result

    def test_adf_empty_content(self):
        adf = {"type": "doc", "content": []}
        assert _extract_jira_description(adf) == ""

    def test_adf_nested_structure(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Item 1"}],
                                }
                            ],
                        },
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Item 2"}],
                                }
                            ],
                        },
                    ],
                }
            ],
        }
        result = _extract_jira_description(adf)
        assert "Item 1" in result
        assert "Item 2" in result


# ---------------------------------------------------------------------------
# _extract_acceptance_criteria
# ---------------------------------------------------------------------------


class TestExtractAcceptanceCriteria:
    def test_with_acceptance_criteria_heading(self):
        text = "Some intro\n\nAcceptance Criteria\n- Must do X\n- Must do Y"
        result = _extract_acceptance_criteria(text)
        assert "Acceptance Criteria" in result
        assert "Must do X" in result

    def test_with_ac_prefix(self):
        result = _extract_acceptance_criteria("Description\nAC: user can login")
        assert "AC: user can login" in result

    def test_with_requirements_prefix(self):
        result = _extract_acceptance_criteria("Intro text\nRequirements: be fast")
        assert "Requirements: be fast" in result

    def test_no_acceptance_criteria(self):
        assert _extract_acceptance_criteria("Just a plain description.") == ""

    def test_none_input(self):
        assert _extract_acceptance_criteria(None) == ""

    def test_adf_with_acceptance_criteria(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Description.\nAcceptance Criteria\n- X"},
                    ],
                }
            ],
        }
        result = _extract_acceptance_criteria(adf)
        assert "Acceptance Criteria" in result


# ---------------------------------------------------------------------------
# resolve_prompt
# ---------------------------------------------------------------------------


class TestResolvePrompt:
    async def test_returns_stripped_source(self):
        text, repo = await resolve_prompt("  Hello world  ")
        assert text == "Hello world"
        assert repo is None

    async def test_empty_string(self):
        text, repo = await resolve_prompt("")
        assert text == ""
        assert repo is None

    async def test_multiline(self):
        source = "line1\nline2\nline3"
        text, repo = await resolve_prompt(source)
        assert text == source
        assert repo is None


# ---------------------------------------------------------------------------
# _detect_format
# ---------------------------------------------------------------------------


class TestDetectFormat:
    def test_explicit_md_hint(self):
        assert _detect_format("{}", None, "md") == "md"

    def test_explicit_markdown_hint(self):
        assert _detect_format("{}", None, "markdown") == "md"

    def test_explicit_json_hint(self):
        assert _detect_format("# heading", None, "json") == "json"

    def test_file_extension_md(self):
        assert _detect_format("content", "task.md", None) == "md"

    def test_file_extension_markdown(self):
        assert _detect_format("content", "task.markdown", None) == "md"

    def test_file_extension_json(self):
        assert _detect_format("content", "task.json", None) == "json"

    def test_content_sniffing_json_object(self):
        assert _detect_format('{"prompt": "test"}', None, None) == "json"

    def test_content_sniffing_json_array(self):
        assert _detect_format('["a", "b"]', None, None) == "json"

    def test_content_sniffing_invalid_json(self):
        assert _detect_format("{not valid json}", None, None) == "md"

    def test_fallback_to_md(self):
        assert _detect_format("plain text", None, None) == "md"

    def test_hint_takes_precedence_over_extension(self):
        assert _detect_format("content", "file.json", "md") == "md"

    def test_extension_takes_precedence_over_sniffing(self):
        assert _detect_format('{"json": true}', "file.md", None) == "md"


# ---------------------------------------------------------------------------
# _parse_json_prompt
# ---------------------------------------------------------------------------


class TestParseJsonPrompt:
    def test_basic_prompt(self):
        content = '{"prompt": "Do the thing"}'
        text, repo, extra = _parse_json_prompt(content)
        assert text == "Do the thing"
        assert repo is None
        assert extra is None

    def test_with_repo(self):
        content = '{"prompt": "Fix the bug", "repo": "my-service"}'
        text, repo, extra = _parse_json_prompt(content)
        assert "Fix the bug" in text
        assert repo == "my-service"

    def test_with_extra_instructions(self):
        content = '{"prompt": "Do X", "extra_instructions": "Be careful"}'
        text, repo, extra = _parse_json_prompt(content)
        assert extra == "Be careful"

    def test_with_context(self):
        content = '{"prompt": "Do X", "context": "Background info here"}'
        text, repo, extra = _parse_json_prompt(content)
        assert "Background info here" in text
        assert "Do X" in text
        # Context should come before the prompt
        assert text.index("Background info here") < text.index("Do X")

    def test_with_steps(self):
        content = '{"prompt": "Do X", "steps": ["Step 1", "Step 2", "Step 3"]}'
        text, repo, extra = _parse_json_prompt(content)
        assert "## Steps" in text
        assert "1. Step 1" in text
        assert "2. Step 2" in text
        assert "3. Step 3" in text

    def test_string_json(self):
        """A JSON string value is treated as the prompt text."""
        text, repo, extra = _parse_json_prompt('"Just a string"')
        assert text == "Just a string"
        assert repo is None
        assert extra is None

    def test_invalid_json(self):
        with pytest.raises(TaskResolutionError, match="Invalid JSON"):
            _parse_json_prompt("not json at all")

    def test_missing_prompt_field(self):
        with pytest.raises(TaskResolutionError, match="must contain a 'prompt' field"):
            _parse_json_prompt('{"repo": "my-service"}')

    def test_non_object_non_string(self):
        with pytest.raises(TaskResolutionError, match="must be an object or string"):
            _parse_json_prompt("[1, 2, 3]")

    def test_full_json_with_all_fields(self):
        content = """{
            "prompt": "Implement the feature",
            "repo": "my-app",
            "extra_instructions": "Follow TDD",
            "context": "This is a React app",
            "steps": ["Write tests", "Implement", "Verify"]
        }"""
        text, repo, extra = _parse_json_prompt(content)
        assert repo == "my-app"
        assert extra == "Follow TDD"
        assert "This is a React app" in text
        assert "Implement the feature" in text
        assert "1. Write tests" in text


# ---------------------------------------------------------------------------
# _extract_repo_from_markdown
# ---------------------------------------------------------------------------


class TestExtractRepoFromMarkdown:
    def test_yaml_frontmatter(self):
        content = "---\nrepo: my-service\n---\n# Task\nDo something."
        assert _extract_repo_from_markdown(content) == "my-service"

    def test_yaml_frontmatter_quoted(self):
        content = '---\nrepo: "my-service"\n---\n# Task'
        assert _extract_repo_from_markdown(content) == "my-service"

    def test_yaml_frontmatter_single_quoted(self):
        content = "---\nrepo: 'my-service'\n---\n# Task"
        assert _extract_repo_from_markdown(content) == "my-service"

    def test_html_comment(self):
        content = "<!-- repo: my-service -->\n# Task\nDo something."
        assert _extract_repo_from_markdown(content) == "my-service"

    def test_html_comment_in_middle(self):
        content = "# Task\nSome text\n<!-- repo: my-service -->\nMore text."
        assert _extract_repo_from_markdown(content) == "my-service"

    def test_no_repo_hint(self):
        content = "# Task\nJust a regular markdown file."
        assert _extract_repo_from_markdown(content) is None

    def test_frontmatter_with_other_fields(self):
        content = "---\ntitle: My Task\nrepo: my-service\nauthor: me\n---\n# Task"
        assert _extract_repo_from_markdown(content) == "my-service"


# ---------------------------------------------------------------------------
# resolve_prompt_file — with file_content (no disk access)
# ---------------------------------------------------------------------------


class TestResolvePromptFile:
    async def test_markdown_content(self):
        task = TaskCreate(
            type=TaskInputType.PROMPT_FILE,
            file_content="# My Task\nDo something interesting.",
        )
        text, repo = await resolve_prompt_file(task)
        assert "# My Task" in text
        assert "Do something interesting." in text
        assert repo is None

    async def test_json_content(self):
        task = TaskCreate(
            type=TaskInputType.PROMPT_FILE,
            file_content='{"prompt": "Implement X", "repo": "my-repo"}',
        )
        text, repo = await resolve_prompt_file(task)
        assert "Implement X" in text
        assert repo == "my-repo"

    async def test_json_with_extra_instructions_no_task_extra(self):
        task = TaskCreate(
            type=TaskInputType.PROMPT_FILE,
            file_content='{"prompt": "Do X", "extra_instructions": "Be careful"}',
        )
        text, repo = await resolve_prompt_file(task)
        assert "## Additional Instructions" in text
        assert "Be careful" in text

    async def test_json_with_extra_instructions_task_has_extra(self):
        """When the task already has extra_instructions, JSON extra is ignored."""
        task = TaskCreate(
            type=TaskInputType.PROMPT_FILE,
            file_content='{"prompt": "Do X", "extra_instructions": "JSON extra"}',
            extra_instructions="Task-level extra",
        )
        text, repo = await resolve_prompt_file(task)
        # JSON extra should NOT be appended since task already has extra_instructions
        assert "JSON extra" not in text

    async def test_format_hint_overrides_detection(self):
        task = TaskCreate(
            type=TaskInputType.PROMPT_FILE,
            file_content='{"prompt": "Do X"}',
            file_format="md",
        )
        text, repo = await resolve_prompt_file(task)
        # With md format hint, the JSON is treated as raw markdown
        assert '{"prompt": "Do X"}' in text

    async def test_empty_content_raises(self):
        task = TaskCreate(
            type=TaskInputType.PROMPT_FILE,
            file_content="   ",
        )
        with pytest.raises(TaskResolutionError, match="empty"):
            await resolve_prompt_file(task)

    async def test_neither_path_nor_content_raises(self):
        task = TaskCreate(
            type=TaskInputType.PROMPT_FILE,
        )
        with pytest.raises(TaskResolutionError, match="requires either file_path or file_content"):
            await resolve_prompt_file(task)

    async def test_markdown_with_repo_hint(self):
        task = TaskCreate(
            type=TaskInputType.PROMPT_FILE,
            file_content="<!-- repo: my-service -->\n# Task\nDo it.",
        )
        text, repo = await resolve_prompt_file(task)
        assert repo == "my-service"

    async def test_file_path_not_found(self, patch_settings):
        task = TaskCreate(
            type=TaskInputType.PROMPT_FILE,
            file_path="/nonexistent/path/task.md",
        )
        with pytest.raises(TaskResolutionError, match="not found"):
            await resolve_prompt_file(task)

    async def test_file_path_reads_from_disk(self, tmp_path, patch_settings):
        """Test that file_path reads file content from disk."""
        prompt_file = tmp_path / "task.md"
        prompt_file.write_text("# Disk Task\nRead from disk.", encoding="utf-8")
        task = TaskCreate(
            type=TaskInputType.PROMPT_FILE,
            file_path=str(prompt_file),
        )
        text, repo = await resolve_prompt_file(task)
        assert "# Disk Task" in text


# ---------------------------------------------------------------------------
# resolve_jira — with mocked httpx
# ---------------------------------------------------------------------------


class TestResolveJira:
    @pytest.fixture(autouse=True)
    def _patch_settings(self, monkeypatch):
        monkeypatch.setattr("workbench.resolvers.settings.jira_base_url", "https://jira.example.com")
        monkeypatch.setattr("workbench.resolvers.settings.jira_api_token", None)
        monkeypatch.setattr("workbench.resolvers.settings.jira_user_email", None)

    @pytest.fixture(autouse=True)
    def _patch_infer_repo(self):
        """Patch _infer_repo_from_jira to return None — Jira resolver tests
        don't focus on repo inference (that has its own test class)."""
        with patch("workbench.resolvers._infer_repo_from_jira", return_value=None):
            yield

    async def test_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "fields": {
                "summary": "Fix the login bug",
                "description": "Users cannot log in with SSO.",
                "labels": ["bug"],
                "components": [{"name": "auth"}],
            }
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.resolvers.httpx.AsyncClient", return_value=mock_client):
            text, repo = await resolve_jira("PROJ-1234")

        assert "PROJ-1234" in text
        assert "Fix the login bug" in text
        assert "Users cannot log in with SSO." in text
        assert "**Components:** auth" in text
        assert "**Labels:** bug" in text

    async def test_invalid_key_format(self):
        with pytest.raises(TaskResolutionError, match="Invalid Jira key format"):
            await resolve_jira("not-a-key")

    async def test_invalid_key_lowercase(self):
        """Keys are uppercased, but must match PROJ-123 pattern after uppercasing."""
        # "abc" uppercased is "ABC" which doesn't have a dash and number
        with pytest.raises(TaskResolutionError, match="Invalid Jira key format"):
            await resolve_jira("abc")

    async def test_http_error(self):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.resolvers.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(TaskResolutionError, match="404"):
                await resolve_jira("PROJ-999")

    async def test_missing_fields(self):
        """Handle a Jira response with minimal fields."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "fields": {
                "summary": "Minimal issue",
            }
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.resolvers.httpx.AsyncClient", return_value=mock_client):
            text, repo = await resolve_jira("PROJ-100")

        assert "PROJ-100" in text
        assert "Minimal issue" in text
        assert "_No description provided._" in text

    async def test_adf_description(self):
        """Handle rich document format (ADF) in the description field."""
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "ADF description text."}],
                }
            ],
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "fields": {
                "summary": "ADF Issue",
                "description": adf,
                "labels": [],
                "components": [],
            }
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.resolvers.httpx.AsyncClient", return_value=mock_client):
            text, repo = await resolve_jira("PROJ-200")

        assert "ADF description text." in text

    async def test_with_auth(self, monkeypatch):
        """When token and email are set, Authorization header is included."""
        monkeypatch.setattr("workbench.resolvers.settings.jira_api_token", "my-token")
        monkeypatch.setattr("workbench.resolvers.settings.jira_user_email", "user@example.com")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "fields": {"summary": "Auth test", "description": None, "labels": [], "components": []}
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.resolvers.httpx.AsyncClient", return_value=mock_client):
            text, repo = await resolve_jira("PROJ-300")

        # Verify Authorization header was passed
        call_kwargs = mock_client.get.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Basic ")


# ---------------------------------------------------------------------------
# resolve_github_issue — with mocked httpx
# ---------------------------------------------------------------------------


class TestResolveGithubIssue:
    @pytest.fixture(autouse=True)
    def _patch_settings(self, monkeypatch):
        monkeypatch.setattr("workbench.resolvers.settings.github_token", "fake-token")

    async def test_success_with_url(self):
        mock_issue_response = MagicMock()
        mock_issue_response.status_code = 200
        mock_issue_response.json.return_value = {
            "title": "Bug in parser",
            "body": "The parser crashes on empty input.",
            "labels": [{"name": "bug"}, {"name": "priority:high"}],
            "html_url": "https://github.com/org/repo/issues/42",
        }

        mock_comments_response = MagicMock()
        mock_comments_response.status_code = 200
        mock_comments_response.json.return_value = []

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_issue_response, mock_comments_response])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.resolvers.httpx.AsyncClient", return_value=mock_client):
            text, repo = await resolve_github_issue(
                "https://github.com/org/repo/issues/42"
            )

        assert "org/repo#42" in text
        assert "Bug in parser" in text
        assert "The parser crashes on empty input." in text
        assert "**Labels:** bug, priority:high" in text
        assert repo == "repo"

    async def test_success_with_shorthand(self):
        mock_issue_response = MagicMock()
        mock_issue_response.status_code = 200
        mock_issue_response.json.return_value = {
            "title": "Feature request",
            "body": "Add dark mode.",
            "labels": [],
            "html_url": "https://github.com/my-org/my-app/issues/7",
        }

        mock_comments_response = MagicMock()
        mock_comments_response.status_code = 200
        mock_comments_response.json.return_value = []

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_issue_response, mock_comments_response])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.resolvers.httpx.AsyncClient", return_value=mock_client):
            text, repo = await resolve_github_issue("my-org/my-app#7")

        assert "my-org/my-app#7" in text
        assert "Feature request" in text
        assert repo == "my-app"

    async def test_http_error(self):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.resolvers.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(TaskResolutionError, match="403"):
                await resolve_github_issue("org/repo#1")

    async def test_no_body(self):
        mock_issue_response = MagicMock()
        mock_issue_response.status_code = 200
        mock_issue_response.json.return_value = {
            "title": "Empty body issue",
            "body": None,
            "labels": [],
            "html_url": "https://github.com/org/repo/issues/5",
        }

        mock_comments_response = MagicMock()
        mock_comments_response.status_code = 200
        mock_comments_response.json.return_value = []

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_issue_response, mock_comments_response])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.resolvers.httpx.AsyncClient", return_value=mock_client):
            text, repo = await resolve_github_issue("org/repo#5")

        assert "_No body provided._" in text

    async def test_with_comments(self):
        mock_issue_response = MagicMock()
        mock_issue_response.status_code = 200
        mock_issue_response.json.return_value = {
            "title": "Issue with discussion",
            "body": "Main issue body.",
            "labels": [],
            "html_url": "https://github.com/org/repo/issues/10",
        }

        mock_comments_response = MagicMock()
        mock_comments_response.status_code = 200
        mock_comments_response.json.return_value = [
            {"user": {"login": "alice"}, "body": "I can reproduce this."},
            {"user": {"login": "bob"}, "body": "Me too, happens on Linux."},
        ]

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_issue_response, mock_comments_response])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.resolvers.httpx.AsyncClient", return_value=mock_client):
            text, repo = await resolve_github_issue("org/repo#10")

        assert "### Discussion (recent comments)" in text
        assert "@alice" in text
        assert "I can reproduce this." in text
        assert "@bob" in text

    async def test_invalid_ref(self):
        with pytest.raises(TaskResolutionError, match="Cannot parse"):
            await resolve_github_issue("just-a-string")

    async def test_comments_fetch_failure_graceful(self):
        """If fetching comments fails, the issue is still returned without comments."""
        mock_issue_response = MagicMock()
        mock_issue_response.status_code = 200
        mock_issue_response.json.return_value = {
            "title": "Issue",
            "body": "Body text.",
            "labels": [],
            "html_url": "https://github.com/org/repo/issues/1",
        }

        # Comments request fails
        mock_comments_response = MagicMock()
        mock_comments_response.status_code = 500

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_issue_response, mock_comments_response])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("workbench.resolvers.httpx.AsyncClient", return_value=mock_client):
            text, repo = await resolve_github_issue("org/repo#1")

        assert "Issue" in text
        assert "Body text." in text
        # No discussion section since comments failed
        assert "### Discussion" not in text


# ---------------------------------------------------------------------------
# _infer_repo_from_jira
# ---------------------------------------------------------------------------


class TestInferRepoFromJira:
    def test_matches_label(self):
        from workbench.resolvers import _infer_repo_from_jira

        with patch.object(
            Settings, "known_repos", new_callable=PropertyMock,
            return_value={"my-service": Path("/path/to/my-service")},
        ):
            result = _infer_repo_from_jira(
                labels=["my-service"], components=[], summary="Something"
            )
        assert result == "my-service"

    def test_matches_component(self):
        from workbench.resolvers import _infer_repo_from_jira

        with patch.object(
            Settings, "known_repos", new_callable=PropertyMock,
            return_value={"my-app": Path("/path/to/my-app")},
        ):
            result = _infer_repo_from_jira(
                labels=[], components=["my-app"], summary="Something"
            )
        assert result == "my-app"

    def test_matches_summary(self):
        from workbench.resolvers import _infer_repo_from_jira

        with patch.object(
            Settings, "known_repos", new_callable=PropertyMock,
            return_value={"my-service": Path("/path/to/my-service")},
        ):
            result = _infer_repo_from_jira(
                labels=[], components=[], summary="Fix bug in my-service auth"
            )
        assert result == "my-service"

    def test_no_match(self):
        from workbench.resolvers import _infer_repo_from_jira

        with patch.object(
            Settings, "known_repos", new_callable=PropertyMock,
            return_value={"my-service": Path("/path/to/my-service")},
        ):
            result = _infer_repo_from_jira(
                labels=["unrelated"], components=["other"], summary="Random task"
            )
        assert result is None


# ---------------------------------------------------------------------------
# resolve() dispatcher
# ---------------------------------------------------------------------------


class TestResolveDispatcher:
    async def test_prompt_type(self):
        from workbench.resolvers import resolve

        task = TaskCreate(type=TaskInputType.PROMPT, source="Do something")
        text, repo = await resolve(task)
        assert text == "Do something"
        assert repo is None

    async def test_prompt_file_type(self):
        from workbench.resolvers import resolve

        task = TaskCreate(
            type=TaskInputType.PROMPT_FILE,
            file_content="# Task\nDo it.",
        )
        text, repo = await resolve(task)
        assert "# Task" in text

    async def test_jira_type_dispatches(self, monkeypatch):
        """Verify Jira type dispatches to resolve_jira."""
        from workbench.resolvers import resolve

        mock_resolve = AsyncMock(return_value=("jira result", "my-repo"))
        monkeypatch.setattr("workbench.resolvers.RESOLVERS", {TaskInputType.JIRA: mock_resolve})
        task = TaskCreate(type=TaskInputType.JIRA, source="PROJ-123")
        text, repo = await resolve(task)
        assert text == "jira result"
        assert repo == "my-repo"
        mock_resolve.assert_awaited_once_with("PROJ-123")

    async def test_github_issue_type_dispatches(self, monkeypatch):
        """Verify GitHub issue type dispatches to resolve_github_issue."""
        from workbench.resolvers import resolve

        mock_resolve = AsyncMock(return_value=("gh result", "repo"))
        monkeypatch.setattr(
            "workbench.resolvers.RESOLVERS", {TaskInputType.GITHUB_ISSUE: mock_resolve}
        )
        task = TaskCreate(type=TaskInputType.GITHUB_ISSUE, source="org/repo#1")
        text, repo = await resolve(task)
        assert text == "gh result"
        mock_resolve.assert_awaited_once_with("org/repo#1")
