"""Input resolvers — turn Jira tickets, GitHub issues, prompt files,
or plain text into structured prompts."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import httpx

from .config import settings
from .exceptions import TaskResolutionError
from .models import TaskCreate, TaskInputType

log = logging.getLogger(__name__)

# Backward-compatible alias
ResolveError = TaskResolutionError


# ---------------------------------------------------------------------------
# Jira resolver
# ---------------------------------------------------------------------------

async def resolve_jira(source: str) -> tuple[str, str | None]:
    """Fetch a Jira issue and return (prompt_text, inferred_repo).

    ``source`` should be a Jira issue key like ``PROJ-1234``.
    """
    key = source.strip().upper()
    if not re.match(r"^[A-Z]+-\d+$", key):
        raise ResolveError(f"Invalid Jira key format: {source!r}")

    url = f"{settings.jira_base_url}/rest/api/3/issue/{key}"
    headers: dict[str, str] = {"Accept": "application/json"}

    if settings.jira_api_token and settings.jira_user_email:
        import base64

        creds = base64.b64encode(
            f"{settings.jira_user_email}:{settings.jira_api_token}".encode()
        ).decode()
        headers["Authorization"] = f"Basic {creds}"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            raise ResolveError(
                f"Jira API returned {resp.status_code} for {key}: {resp.text[:300]}"
            )
        data = resp.json()

    fields = data.get("fields", {})
    summary = fields.get("summary", "")
    description = _extract_jira_description(fields.get("description"))
    labels = fields.get("labels", [])
    components = [c.get("name", "") for c in fields.get("components", [])]
    acceptance = _extract_acceptance_criteria(fields.get("description"))

    # Try to infer repo from components or labels
    inferred_repo = _infer_repo_from_jira(labels, components, summary)

    prompt_parts = [
        f"## Jira: {key} — {summary}",
        f"**URL:** {settings.jira_base_url}/browse/{key}",
    ]
    if components:
        prompt_parts.append(f"**Components:** {', '.join(components)}")
    if labels:
        prompt_parts.append(f"**Labels:** {', '.join(labels)}")
    prompt_parts.append("")
    prompt_parts.append("### Description")
    prompt_parts.append(description or "_No description provided._")
    if acceptance:
        prompt_parts.append("")
        prompt_parts.append("### Acceptance Criteria")
        prompt_parts.append(acceptance)

    return "\n".join(prompt_parts), inferred_repo


def _extract_jira_description(desc: dict | str | None) -> str:
    """Best-effort extraction of plain text from Atlassian Document Format or string."""
    if desc is None:
        return ""
    if isinstance(desc, str):
        return desc

    # ADF (Atlassian Document Format) — walk the content tree
    parts: list[str] = []

    def _walk(node: dict | list | str) -> None:
        if isinstance(node, str):
            parts.append(node)
            return
        if isinstance(node, list):
            for item in node:
                _walk(item)
            return
        if isinstance(node, dict):
            if node.get("type") == "text":
                parts.append(node.get("text", ""))
            elif node.get("type") in ("hardBreak", "rule"):
                parts.append("\n")
            for child in node.get("content", []):
                _walk(child)

    _walk(desc)
    return "".join(parts).strip()


def _extract_acceptance_criteria(desc: dict | str | None) -> str:
    """Try to pull out an acceptance criteria section from the description."""
    text = _extract_jira_description(desc)
    # Look for common headings
    for marker in ("acceptance criteria", "ac:", "requirements:"):
        idx = text.lower().find(marker)
        if idx != -1:
            return text[idx:].strip()
    return ""


def _infer_repo_from_jira(labels: list[str], components: list[str], summary: str) -> str | None:
    """Heuristic: map Jira metadata to a repo name.

    Uses the workspace's known repos to find keyword matches in labels,
    components, and summary text.
    """
    all_text = " ".join(labels + components + [summary]).lower()
    # Try matching against known repo names from config
    for repo_name in settings.known_repos:
        if repo_name.lower() in all_text:
            return repo_name
    return None


# ---------------------------------------------------------------------------
# GitHub issue resolver
# ---------------------------------------------------------------------------

async def resolve_github_issue(source: str) -> tuple[str, str | None]:
    """Fetch a GitHub issue and return (prompt_text, inferred_repo).

    ``source`` should be a GitHub issue URL like
    ``https://github.com/org/repo/issues/123``
    or a shorthand like ``org/repo#123``.
    """
    owner, repo, number = _parse_gh_issue_ref(source)

    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
    headers: dict[str, str] = {"Accept": "application/vnd.github.v3+json"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            raise ResolveError(
                f"GitHub API returned {resp.status_code} for {source}: {resp.text[:300]}"
            )
        data = resp.json()

    title = data.get("title", "")
    body = data.get("body", "") or ""
    issue_labels = [lbl.get("name", "") for lbl in data.get("labels", [])]
    html_url = data.get("html_url", source)

    prompt_parts = [
        f"## GitHub Issue: {owner}/{repo}#{number} — {title}",
        f"**URL:** {html_url}",
    ]
    if issue_labels:
        prompt_parts.append(f"**Labels:** {', '.join(issue_labels)}")
    prompt_parts.append("")
    prompt_parts.append("### Body")
    prompt_parts.append(body or "_No body provided._")

    # Also fetch issue comments for context
    comments_text = await _fetch_issue_comments(owner, repo, number, headers)
    if comments_text:
        prompt_parts.append("")
        prompt_parts.append("### Discussion (recent comments)")
        prompt_parts.append(comments_text)

    return "\n".join(prompt_parts), repo


async def _fetch_issue_comments(
    owner: str, repo: str, number: int, headers: dict[str, str]
) -> str:
    """Fetch the last few comments on a GH issue for context."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}/comments"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers, params={"per_page": 5})
            if resp.status_code != 200:
                return ""
            comments = resp.json()
    except Exception:
        return ""

    parts: list[str] = []
    for c in comments[-5:]:
        author = c.get("user", {}).get("login", "unknown")
        body = (c.get("body", "") or "")[:500]
        parts.append(f"**@{author}:** {body}")
    return "\n\n".join(parts)


def _parse_gh_issue_ref(source: str) -> tuple[str, str, int]:
    """Parse a GitHub issue URL or shorthand into (owner, repo, number)."""
    # Full URL: https://github.com/owner/repo/issues/123
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/issues/(\d+)", source)
    if m:
        return m.group(1), m.group(2), int(m.group(3))

    # Shorthand: owner/repo#123
    m = re.match(r"([^/]+)/([^#]+)#(\d+)", source)
    if m:
        return m.group(1), m.group(2).strip(), int(m.group(3))

    raise ResolveError(
        f"Cannot parse GitHub issue reference: {source!r}. "
        "Expected URL (https://github.com/owner/repo/issues/N) or shorthand (owner/repo#N)."
    )


# ---------------------------------------------------------------------------
# Plain text resolver
# ---------------------------------------------------------------------------

async def resolve_prompt(source: str) -> tuple[str, str | None]:
    """Pass-through resolver for free-form text prompts.

    Returns (prompt_text, None) since we can't infer a repo from plain text.
    """
    return source.strip(), None


# ---------------------------------------------------------------------------
# Prompt file resolver
# ---------------------------------------------------------------------------

def _detect_format(content: str, file_path: str | None, format_hint: str | None) -> str:
    """Auto-detect whether content is markdown or JSON.

    Priority: explicit hint > file extension > content sniffing.
    Returns 'md' or 'json'.
    """
    if format_hint and format_hint.lower() in ("md", "markdown"):
        return "md"
    if format_hint and format_hint.lower() == "json":
        return "json"

    if file_path:
        ext = Path(file_path).suffix.lower()
        if ext in (".md", ".markdown"):
            return "md"
        if ext == ".json":
            return "json"

    # Content sniffing: try JSON parse
    stripped = content.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            json.loads(stripped)
            return "json"
        except (json.JSONDecodeError, ValueError):
            pass
    return "md"


def _parse_json_prompt(content: str) -> tuple[str, str | None, str | None]:
    """Parse a JSON prompt file into (prompt_text, repo, extra_instructions).

    Supported JSON schema:
    {
        "prompt": "...",           # required — the task description
        "repo": "...",             # optional — target repo
        "extra_instructions": "...",  # optional
        "context": "...",          # optional — prepended to prompt
        "steps": ["...", "..."]    # optional — appended as numbered list
    }
    """
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError) as e:
        raise ResolveError(f"Invalid JSON in prompt file: {e}")

    if isinstance(data, str):
        return data, None, None

    if not isinstance(data, dict):
        raise ResolveError(
            f"JSON prompt file must be an object or string, "
            f"got {type(data).__name__}"
        )

    prompt = data.get("prompt", "")
    if not prompt:
        raise ResolveError("JSON prompt file must contain a 'prompt' field")

    parts: list[str] = []
    if data.get("context"):
        parts.append(str(data["context"]))
        parts.append("")
    parts.append(prompt)

    steps = data.get("steps")
    if steps and isinstance(steps, list):
        parts.append("")
        parts.append("## Steps")
        for i, step in enumerate(steps, 1):
            parts.append(f"{i}. {step}")

    return "\n".join(parts), data.get("repo"), data.get("extra_instructions")


async def resolve_prompt_file(task_input: TaskCreate) -> tuple[str, str | None]:
    """Resolve a prompt file (from file_path or file_content) into prompt text.

    Returns (prompt_text, inferred_repo).
    """
    if task_input.file_content:
        content = task_input.file_content
    elif task_input.file_path:
        path = Path(task_input.file_path)
        if not path.is_absolute():
            # Resolve relative to workspace root
            path = settings.workspace_root / path
        if not path.is_file():
            raise ResolveError(f"Prompt file not found: {path}")
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            raise ResolveError(f"Cannot read prompt file {path}: {e}")
    else:
        raise ResolveError(
            "prompt_file type requires either file_path or file_content"
        )

    if not content.strip():
        raise ResolveError("Prompt file is empty")

    fmt = _detect_format(content, task_input.file_path, task_input.file_format)
    log.info("Prompt file format detected as: %s", fmt)

    if fmt == "json":
        prompt_text, json_repo, json_extra = _parse_json_prompt(content)
        # JSON can provide repo/extra_instructions that override if not set on the task
        inferred_repo = json_repo
        if json_extra and not task_input.extra_instructions:
            # We'll include it in the prompt text since we can't mutate the task_input
            prompt_text = f"{prompt_text}\n\n## Additional Instructions\n{json_extra}"
        return prompt_text, inferred_repo
    else:
        # Markdown: use content as-is
        # Try to extract repo hint from frontmatter-style comment
        inferred_repo = _extract_repo_from_markdown(content)
        return content.strip(), inferred_repo


def _extract_repo_from_markdown(content: str) -> str | None:
    """Try to extract a repo hint from markdown content.

    Looks for patterns like:
    - ``repo: my-service`` in YAML frontmatter
    - ``<!-- repo: my-service -->`` as HTML comment
    """
    # YAML frontmatter
    m = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if m:
        for line in m.group(1).splitlines():
            km = re.match(r"repo\s*:\s*(.+)", line.strip())
            if km:
                return km.group(1).strip().strip("\"'")

    # HTML comment
    m = re.search(r"<!--\s*repo\s*:\s*(\S+)\s*-->", content)
    if m:
        return m.group(1).strip()

    return None


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

RESOLVERS = {
    TaskInputType.JIRA: resolve_jira,
    TaskInputType.GITHUB_ISSUE: resolve_github_issue,
    TaskInputType.PROMPT: resolve_prompt,
}


async def resolve(task_input: TaskCreate) -> tuple[str, str | None]:
    """Resolve any input type into (prompt_text, inferred_repo_or_none)."""
    # prompt_file is special — it needs the full task_input, not just source
    if task_input.type == TaskInputType.PROMPT_FILE:
        return await resolve_prompt_file(task_input)

    resolver = RESOLVERS.get(task_input.type)
    if resolver is None:
        raise ResolveError(f"Unknown input type: {task_input.type}")
    return await resolver(task_input.source)
