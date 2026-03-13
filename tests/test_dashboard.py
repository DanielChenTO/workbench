"""Unit tests for workbench.dashboard — HTML structure, JS syntax, and UI correctness.

These tests validate the dashboard HTML/JS without a browser by parsing the
rendered HTML string and checking for common issues:
- Unescaped quotes in onclick handlers (causes SyntaxError in browser)
- Missing function definitions that JS references
- Structural HTML correctness (matching tags, required elements)
- CSS class consistency (JS references classes that exist in <style>)
- Tab infrastructure completeness
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest

from workbench.dashboard import DASHBOARD_HTML


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_script(html: str) -> str:
    """Extract the contents of the last <script> block."""
    match = re.search(r"<script>(.*?)</script>", html, re.DOTALL)
    assert match, "No <script> block found in dashboard HTML"
    return match.group(1)


def _extract_style(html: str) -> str:
    """Extract the contents of the <style> block."""
    match = re.search(r"<style>(.*?)</style>", html, re.DOTALL)
    assert match, "No <style> block found in dashboard HTML"
    return match.group(1)


def _find_all_onclick_in_static_html(html: str) -> list[str]:
    """Find onclick attributes in the static HTML (outside <script>)."""
    # Remove script content first
    no_script = re.sub(r"<script>.*?</script>", "", html, flags=re.DOTALL)
    return re.findall(r'onclick="([^"]*)"', no_script)


def _find_all_onclick_in_js(html: str) -> list[str]:
    """Find onclick=\\"...\\" patterns inside JS string concatenation.

    In the Python source these are ``onclick=\\\\"...\\\\"`` which render to
    ``onclick=\\"...\\"`` in the HTML output (inside a <script> tag, the
    backslash-escaped double quotes delimit the attribute value in the
    dynamically-built HTML string).
    """
    script = _extract_script(html)
    # Match onclick=\"...\" patterns in JS (escaped double quotes)
    return re.findall(r'onclick=\\"([^"]*?)\\"', script)


class TagCounter(HTMLParser):
    """Count opening/closing tags to detect mismatches."""

    def __init__(self):
        super().__init__()
        self.open_tags: dict[str, int] = {}
        self.close_tags: dict[str, int] = {}
        # Void elements that don't need closing tags
        self.void_elements = {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag not in self.void_elements:
            self.open_tags[tag] = self.open_tags.get(tag, 0) + 1

    def handle_endtag(self, tag: str) -> None:
        if tag not in self.void_elements:
            self.close_tags[tag] = self.close_tags.get(tag, 0) + 1


# ---------------------------------------------------------------------------
# Basic structure tests
# ---------------------------------------------------------------------------


class TestDashboardStructure:
    """Tests for the basic HTML structure of the dashboard."""

    def test_is_valid_html_document(self):
        assert DASHBOARD_HTML.strip().startswith("<!DOCTYPE html>")
        assert "</html>" in DASHBOARD_HTML

    def test_has_required_meta_tags(self):
        assert '<meta charset="utf-8">' in DASHBOARD_HTML
        assert '<meta name="viewport"' in DASHBOARD_HTML

    def test_has_title(self):
        assert "<title>" in DASHBOARD_HTML

    def test_has_style_block(self):
        assert "<style>" in DASHBOARD_HTML
        assert "</style>" in DASHBOARD_HTML

    def test_has_script_block(self):
        assert "<script>" in DASHBOARD_HTML
        assert "</script>" in DASHBOARD_HTML

    def test_matching_div_tags(self):
        """Open and close <div> counts should match."""
        counter = TagCounter()
        counter.feed(DASHBOARD_HTML)
        opens = counter.open_tags.get("div", 0)
        closes = counter.close_tags.get("div", 0)
        assert opens == closes, f"Mismatched <div> tags: {opens} opens vs {closes} closes"

    def test_matching_button_tags(self):
        counter = TagCounter()
        counter.feed(DASHBOARD_HTML)
        opens = counter.open_tags.get("button", 0)
        closes = counter.close_tags.get("button", 0)
        assert opens == closes, f"Mismatched <button> tags: {opens} opens vs {closes} closes"


# ---------------------------------------------------------------------------
# Tab system tests
# ---------------------------------------------------------------------------


class TestTabSystem:
    """Tests for the tab navigation system."""

    EXPECTED_TABS = ["board", "tasks", "pipelines", "schedules", "report"]

    def test_tab_bar_exists(self):
        assert 'id="tabBar"' in DASHBOARD_HTML

    def test_all_tab_buttons_exist(self):
        """Each expected tab should have a button with onclick=switchTab(...)."""
        for tab in self.EXPECTED_TABS:
            pattern = f"switchTab('{tab}')"
            assert pattern in DASHBOARD_HTML, f"Missing tab button for '{tab}'"

    def test_all_tab_content_divs_exist(self):
        """Each tab should have a matching content div."""
        for tab in self.EXPECTED_TABS:
            tab_id = f'id="tab-{tab}"'
            assert tab_id in DASHBOARD_HTML, f"Missing tab content div for '{tab}'"

    def test_switchTab_function_defined(self):
        script = _extract_script(DASHBOARD_HTML)
        assert "function switchTab(" in script

    def test_keyboard_shortcuts_for_tabs(self):
        """Keys 1-5 should map to the five tabs."""
        script = _extract_script(DASHBOARD_HTML)
        for i, tab in enumerate(self.EXPECTED_TABS, 1):
            pattern = f"e.key === '{i}'"
            assert pattern in script, f"Missing keyboard shortcut {i} for tab '{tab}'"


# ---------------------------------------------------------------------------
# JavaScript function definition tests
# ---------------------------------------------------------------------------


class TestJSFunctionDefinitions:
    """Ensure all JS functions referenced in the dashboard are defined."""

    # Functions called from static HTML onclick attributes
    STATIC_ONCLICK_FUNCTIONS = [
        "switchTab",
        "toggleDispatch",
        "fetchTasks",
        "fetchPipelines",
        "fetchSchedules",
        "fetchReport",
    ]

    # Functions called from JS code
    JS_FUNCTIONS = [
        "renderReport",
        "escHtml",
        "formatDuration",
        "fmtTime",
        "showDetail",
        "showPipelineDetail",
        "closeAllOverlays",
        "showToast",
        "handleFetchError",
        "apiFetch",
        # Work-stream helper functions
        "toggleWsBody",
        "renderOrchestrator",
        "toggleSpecialists",
        "renderStandaloneTask",
        "renderFailedTask",
    ]

    @pytest.mark.parametrize("func", STATIC_ONCLICK_FUNCTIONS + JS_FUNCTIONS)
    def test_function_is_defined(self, func: str):
        script = _extract_script(DASHBOARD_HTML)
        # Match either "function name(" or "async function name(" or "name = function"
        patterns = [
            f"function {func}(",
            f"async function {func}(",
            f"{func} = function",
            f"const {func} = ",
            f"let {func} = ",
            f"var {func} = ",
        ]
        found = any(p in script for p in patterns)
        assert found, f"Function '{func}' is referenced but not defined in <script>"


# ---------------------------------------------------------------------------
# onclick attribute escaping tests (the bug class that triggered this file)
# ---------------------------------------------------------------------------


class TestOnclickEscaping:
    """Validate that onclick attributes in JS-built HTML have proper escaping.

    The dashboard builds HTML strings in JS via concatenation. When those
    strings contain onclick attributes with function calls that take string
    arguments (e.g. switchTab('tasks')), the quotes MUST be escaped as \\'
    in the JS string so they render as \\' in the HTML onclick attribute.

    A common bug is using \\' (single backslash) in the Python source, which
    Python consumes as a quote escape, producing a bare ' in the HTML output.
    This breaks the onclick attribute boundary.
    """

    def test_static_onclick_attributes_are_valid(self):
        """onclick attrs in static HTML should have balanced quotes."""
        onclicks = _find_all_onclick_in_static_html(DASHBOARD_HTML)
        for oc in onclicks:
            # Single quotes inside double-quoted attr must be balanced
            assert oc.count("'") % 2 == 0, f"Unbalanced quotes in static onclick: {oc}"

    def test_js_onclick_switchTab_properly_escaped(self):
        """switchTab calls in JS-built onclick attrs must use escaped quotes.

        In the rendered HTML (inside <script>), a correct pattern looks like:
            onclick=\\"switchTab(\\'tasks\\'); ....\\"
        A broken pattern would render as:
            onclick=\\"switchTab('tasks'); ....\\"
        because the bare ' terminates the attribute value in the browser.
        """
        onclicks = _find_all_onclick_in_js(DASHBOARD_HTML)
        for oc in onclicks:
            if "switchTab" in oc:
                # The onclick value should use \\' for quotes, not bare '
                # In the extracted string, escaped quotes appear as \'
                assert "switchTab(\\'" in oc, (
                    f"switchTab has unescaped quotes in JS onclick: {oc[:100]}"
                )

    def test_js_onclick_showDetail_properly_escaped(self):
        """showDetail calls in JS-built onclick attrs must use escaped quotes."""
        onclicks = _find_all_onclick_in_js(DASHBOARD_HTML)
        for oc in onclicks:
            if "showDetail" in oc:
                assert "showDetail(\\'" in oc, (
                    f"showDetail has unescaped quotes in JS onclick: {oc[:100]}"
                )

    def test_js_onclick_showPipelineDetail_properly_escaped(self):
        """showPipelineDetail calls in JS-built onclicks must use escaped quotes."""
        onclicks = _find_all_onclick_in_js(DASHBOARD_HTML)
        for oc in onclicks:
            if "showPipelineDetail" in oc:
                assert "showPipelineDetail(\\'" in oc, (
                    f"showPipelineDetail has unescaped quotes in JS onclick: {oc[:100]}"
                )

    def test_no_bare_single_quotes_in_js_onclick_attrs(self):
        """No JS-built onclick should have bare (unescaped) single quotes.

        Every single quote in a JS-built onclick attr string should be
        preceded by a backslash (i.e. appear as \\' in the rendered HTML).
        """
        onclicks = _find_all_onclick_in_js(DASHBOARD_HTML)
        for oc in onclicks:
            # Replace all properly escaped quotes, then check for leftovers
            stripped = oc.replace("\\'", "")
            assert "'" not in stripped, f"Bare single quote found in JS onclick attr: {oc[:100]}"


# ---------------------------------------------------------------------------
# CSS class consistency tests
# ---------------------------------------------------------------------------


class TestCSSClassConsistency:
    """Ensure CSS classes used in JS are defined in <style>."""

    # CSS classes referenced in JS that should be defined in the stylesheet
    JS_REFERENCED_CLASSES = [
        "orch-card",
        "orch-header",
        "orch-icon",
        "orch-id",
        "orch-goal",
        "orch-summary",
        "orch-specialists",
        "orch-specialist",
        "spec-header",
        "spec-prompt",
        "spec-summary",
        "orch-meta",
        "report-card",
        "report-counts",
        "report-count",
        "report-task-summary",
        "report-failure",
        "badge",
        "task-id",
        "pipeline-id",
        # Work-stream classes
        "ws-card",
        "ws-header",
        "ws-body",
        "ws-task",
        "ws-repo",
        "ws-counts",
        "ws-chevron",
        "ws-asked",
        "ws-result",
        "ws-section-label",
        "ws-task-header",
    ]

    @pytest.mark.parametrize("cls", JS_REFERENCED_CLASSES)
    def test_css_class_defined_in_style(self, cls: str):
        style = _extract_style(DASHBOARD_HTML)
        # Class should appear as .classname in the CSS
        assert f".{cls}" in style, f"CSS class '.{cls}' is used in JS but not defined in <style>"


# ---------------------------------------------------------------------------
# Report rendering tests
# ---------------------------------------------------------------------------


class TestReportRendering:
    """Tests for the morning report / Reports tab rendering."""

    def test_renderReport_function_exists(self):
        script = _extract_script(DASHBOARD_HTML)
        assert "function renderReport(" in script

    def test_fetchReport_function_exists(self):
        script = _extract_script(DASHBOARD_HTML)
        assert "function fetchReport(" in script or "async function fetchReport(" in script

    def test_report_renders_orchestrator_section(self):
        """renderReport should handle data.orchestrators array."""
        script = _extract_script(DASHBOARD_HTML)
        assert "data.orchestrators" in script, "renderReport does not reference data.orchestrators"

    def test_report_renders_orchestrator_count(self):
        """The counts bar should include orchestrators."""
        script = _extract_script(DASHBOARD_HTML)
        assert "c.orchestrators" in script, "Counts bar does not include orchestrators count"

    def test_report_renders_pipeline_section(self):
        script = _extract_script(DASHBOARD_HTML)
        assert "data.pipelines" in script

    def test_report_renders_pipelines_requiring_review_count(self):
        script = _extract_script(DASHBOARD_HTML)
        assert "c.pipelines_requiring_review" in script

    def test_report_renders_pipeline_failure_report(self):
        script = _extract_script(DASHBOARD_HTML)
        assert "p.failure_report" in script

    def test_report_renders_pipeline_human_review_required(self):
        script = _extract_script(DASHBOARD_HTML)
        assert "p.human_review_required" in script

    def test_report_renders_completed_tasks(self):
        script = _extract_script(DASHBOARD_HTML)
        assert "data.completed_tasks" in script

    def test_report_renders_failed_tasks(self):
        script = _extract_script(DASHBOARD_HTML)
        assert "data.failed_tasks" in script

    def test_dashboard_pipeline_stats_include_review_required(self):
        script = _extract_script(DASHBOARD_HTML)
        assert "reviewRequired" in script

    def test_pipeline_detail_renders_failure_report(self):
        script = _extract_script(DASHBOARD_HTML)
        assert "Failure Report" in script

    def test_pipeline_detail_renders_human_review_flag(self):
        script = _extract_script(DASHBOARD_HTML)
        assert "Human Review" in script

    def test_report_renders_pr_section(self):
        script = _extract_script(DASHBOARD_HTML)
        assert "data.prs" in script

    def test_orchestrator_card_has_specialist_rendering(self):
        """Orchestrator cards should render nested specialists."""
        script = _extract_script(DASHBOARD_HTML)
        assert "o.specialists" in script, "Orchestrator rendering doesn't iterate specialists"

    def test_orchestrator_renders_goal(self):
        script = _extract_script(DASHBOARD_HTML)
        assert "o.goal" in script

    def test_orchestrator_renders_status_badge(self):
        script = _extract_script(DASHBOARD_HTML)
        assert "o.status" in script

    def test_specialist_renders_branch(self):
        script = _extract_script(DASHBOARD_HTML)
        assert "sp.branch" in script

    def test_specialist_renders_pr_url(self):
        script = _extract_script(DASHBOARD_HTML)
        assert "sp.pr_url" in script


# ---------------------------------------------------------------------------
# Work-stream (repo-grouped) rendering tests
# ---------------------------------------------------------------------------


class TestWorkStreamRendering:
    """Tests for the repo-grouped work-stream report layout."""

    def test_report_groups_by_repo(self):
        """renderReport should group tasks into repo-based work streams."""
        script = _extract_script(DASHBOARD_HTML)
        assert "repoGroups" in script, "renderReport does not group tasks by repo"

    def test_specialist_filtering(self):
        """Orchestrator specialists should be filtered from standalone task list."""
        script = _extract_script(DASHBOARD_HTML)
        assert "specialistIds" in script, "No specialist ID filtering set"
        assert "parent_task_id" in script, "Does not filter by parent_task_id"

    def test_ws_card_css_defined(self):
        """Work-stream card CSS classes must exist."""
        style = _extract_style(DASHBOARD_HTML)
        assert ".ws-card" in style
        assert ".ws-header" in style
        assert ".ws-body" in style
        assert ".ws-task" in style

    def test_toggleWsBody_defined(self):
        """The toggle function for collapsing work-stream bodies must exist."""
        script = _extract_script(DASHBOARD_HTML)
        assert "function toggleWsBody(" in script

    def test_renderOrchestrator_helper_defined(self):
        """Orchestrator rendering is extracted into a helper function."""
        script = _extract_script(DASHBOARD_HTML)
        assert "function renderOrchestrator(" in script

    def test_renderStandaloneTask_helper_defined(self):
        script = _extract_script(DASHBOARD_HTML)
        assert "function renderStandaloneTask(" in script

    def test_renderFailedTask_helper_defined(self):
        script = _extract_script(DASHBOARD_HTML)
        assert "function renderFailedTask(" in script

    def test_task_source_displayed(self):
        """Task source (what was asked) should be rendered."""
        script = _extract_script(DASHBOARD_HTML)
        assert "t.source" in script, "Task source/prompt is not displayed"

    def test_ws_collapse_chevron(self):
        """Work-stream headers should have a chevron for collapse."""
        script = _extract_script(DASHBOARD_HTML)
        assert "ws-chevron" in script

    def test_specialist_collapse_for_large_groups(self):
        """Orchestrators with many specialists should collapse them by default."""
        script = _extract_script(DASHBOARD_HTML)
        assert "toggleSpecialists" in script

    def test_ws_section_labels(self):
        """Work streams should have section labels for orchestrators/tasks/failed."""
        script = _extract_script(DASHBOARD_HTML)
        assert "ws-section-label" in script

    def test_onclick_toggleWsBody_properly_escaped(self):
        """toggleWsBody onclick attrs must use escaped quotes."""
        onclicks = _find_all_onclick_in_js(DASHBOARD_HTML)
        for oc in onclicks:
            if "toggleWsBody" in oc:
                assert "toggleWsBody(\\'" in oc, (
                    f"toggleWsBody has unescaped quotes in JS onclick: {oc[:100]}"
                )

    def test_onclick_toggleSpecialists_properly_escaped(self):
        """toggleSpecialists onclick attrs must use escaped quotes."""
        onclicks = _find_all_onclick_in_js(DASHBOARD_HTML)
        for oc in onclicks:
            if "toggleSpecialists" in oc:
                assert "toggleSpecialists(\\'" in oc, (
                    f"toggleSpecialists has unescaped quotes in JS onclick: {oc[:100]}"
                )


# ---------------------------------------------------------------------------
# Kanban board tests
# ---------------------------------------------------------------------------


class TestKanbanBoard:
    """Tests for the kanban board tab."""

    def test_board_tab_uses_active_class_for_display(self):
        """The #tab-board should only display when .active is present (CSS fix)."""
        style = _extract_style(DASHBOARD_HTML)
        # Should be #tab-board.active, NOT just #tab-board
        assert "#tab-board.active" in style, "#tab-board display rule should require .active class"
        # Should NOT have a bare #tab-board { display: flex } without .active
        bare_pattern = re.search(r"#tab-board\s*\{[^}]*display:\s*flex", style)
        assert bare_pattern is None, (
            "Found bare #tab-board { display: flex } without .active qualifier"
        )


class TestKanbanCoverageResilience:
    """Board coverage fetch should be best-effort, never all-or-nothing."""

    def test_todos_and_coverage_not_loaded_via_promise_all(self):
        """A coverage failure must not block /todos rendering."""
        script = _extract_script(DASHBOARD_HTML)
        assert "Promise.all([todosReq, coverageReq])" not in script

    def test_best_effort_coverage_helper_exists(self):
        """Coverage fetch should be isolated behind a best-effort helper."""
        script = _extract_script(DASHBOARD_HTML)
        assert "async function kbFetchCoverageBestEffort(" in script

    def test_coverage_timeout_guard_exists(self):
        """Coverage requests should time out rather than hanging board refresh."""
        script = _extract_script(DASHBOARD_HTML)
        assert "new AbortController()" in script
        assert "abortController.abort()" in script
        assert "coverage request timed out" in script

    def test_degraded_coverage_label_exists(self):
        """UI should clearly show when coverage is unavailable."""
        script = _extract_script(DASHBOARD_HTML)
        assert "coverage unavailable" in script


# ---------------------------------------------------------------------------
# Security / XSS prevention tests
# ---------------------------------------------------------------------------


class TestSecurity:
    """Tests for XSS prevention in the dashboard."""

    def test_escHtml_function_exists(self):
        """The escHtml function must exist for HTML entity encoding."""
        script = _extract_script(DASHBOARD_HTML)
        assert "function escHtml(" in script

    def test_user_content_uses_escHtml(self):
        """User-controlled content (summaries, errors, prompts) should be escaped."""
        script = _extract_script(DASHBOARD_HTML)
        # These fields contain user content and should be passed through escHtml
        user_fields = [
            "o.goal",
            "o.summary",
            "o.error",
            "sp.prompt",
            "sp.summary",
            "sp.error",
            "sp.branch",
            "sp.pr_url",
            "t.summary",
            "t.error",
            "t.source",
            "t.branch",
        ]
        for field in user_fields:
            # Look for escHtml(field) pattern
            pattern = f"escHtml({field}"
            assert pattern in script, f"User content field '{field}' is not escaped with escHtml()"


# ---------------------------------------------------------------------------
# Dispatch panel tests
# ---------------------------------------------------------------------------


class TestDispatchPanel:
    """Tests for the task dispatch panel."""

    def test_dispatch_panel_exists(self):
        assert 'id="dispatchPanel"' in DASHBOARD_HTML

    def test_toggleDispatch_defined(self):
        script = _extract_script(DASHBOARD_HTML)
        assert "function toggleDispatch(" in script


# ---------------------------------------------------------------------------
# Refresh and polling tests
# ---------------------------------------------------------------------------


class TestPolling:
    """Tests for the auto-refresh / polling infrastructure."""

    def test_refresh_interval_setting_exists(self):
        assert 'id="refreshInterval"' in DASHBOARD_HTML

    def test_api_base_url_configured(self):
        """The API variable should be set (even if relative)."""
        script = _extract_script(DASHBOARD_HTML)
        assert re.search(r"(const|var|let)\s+API\s*=", script), "API base URL variable not defined"
