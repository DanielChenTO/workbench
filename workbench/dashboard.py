"""Self-contained HTML dashboard for workbench.

Serves a single-page app that polls the /tasks API and renders a live
task monitor. All CSS and JS are inlined — no external dependencies.
"""

from __future__ import annotations

DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>workbench dashboard</title>
<style>
  :root {
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #c9d1d9; --muted: #9ea7b0; --accent: #58a6ff;
    --green: #3fb950; --red: #f85149; --yellow: #d29922;
    --orange: #db6d28; --purple: #bc8cff;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'SF Mono', 'Cascadia Code', 'JetBrains Mono', monospace;
    background: var(--bg); color: var(--text);
    padding: 20px; line-height: 1.5; font-size: 13px;
  }
  header {
    display: flex; justify-content: space-between; align-items: center;
    border-bottom: 1px solid var(--border); padding-bottom: 12px; margin-bottom: 20px;
  }
  header h1 { font-size: 18px; font-weight: 600; }
  header h1 span { color: var(--accent); }
  .health { display: flex; gap: 16px; font-size: 12px; color: var(--muted); align-items: center; }
  .nav-link { color: var(--accent); text-decoration: none; font-size: 12px; font-weight: 600; padding: 4px 10px; border: 1px solid var(--accent); border-radius: 6px; }
  .nav-link:hover { background: var(--accent); color: #000; }

  /* Controls bar */
  .controls {
    display: flex; gap: 12px; margin-bottom: 16px; align-items: center; flex-wrap: wrap;
  }
  .controls select, .controls input, .controls button, .controls textarea {
    background: var(--surface); border: 1px solid var(--border); color: var(--text);
    padding: 6px 10px; border-radius: 6px; font-family: inherit; font-size: 12px;
  }
  .controls button {
    cursor: pointer; background: var(--accent); color: #000; border: none;
    font-weight: 600; padding: 6px 14px;
  }
  .controls button:hover { opacity: 0.85; }
  .controls button.secondary { background: var(--surface); color: var(--text); border: 1px solid var(--border); }
  .filter-group { display: flex; gap: 6px; align-items: center; }
  .filter-group label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }

  /* Dispatch form */
  .dispatch-panel {
    background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px; margin-bottom: 16px; display: none;
  }
  .dispatch-panel.open { display: block; }
  .dispatch-panel h3 { font-size: 13px; margin-bottom: 12px; color: var(--accent); }
  .form-row { display: flex; gap: 10px; margin-bottom: 10px; align-items: center; flex-wrap: wrap; }
  .form-row label { width: 80px; font-size: 11px; color: var(--muted); text-transform: uppercase; flex-shrink: 0; }
  .form-row input, .form-row select { flex: 1; min-width: 140px; }
  .form-row textarea { flex: 1; min-height: 80px; resize: vertical; }

  /* Stats bar */
  .stats {
    display: flex; gap: 20px; margin-bottom: 16px; font-size: 12px;
  }
  .stat { padding: 8px 14px; background: var(--surface); border-radius: 6px; border: 1px solid var(--border); }
  .stat .num { font-size: 20px; font-weight: 700; }
  .stat .label { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; }

  /* Task table */
  table { width: 100%; border-collapse: collapse; }
  th {
    text-align: left; font-size: 10px; text-transform: uppercase;
    letter-spacing: 0.5px; color: var(--muted); padding: 8px 10px;
    border-bottom: 1px solid var(--border); position: sticky; top: 0;
    background: var(--bg);
  }
  th.sortable { cursor: pointer; user-select: none; }
  th.sortable:hover { color: var(--text); }
  th.sortable::after { content: ' \\2195'; font-size: 9px; }
  th.sort-asc::after { content: ' \\2191'; color: var(--accent); }
  th.sort-desc::after { content: ' \\2193'; color: var(--accent); }
  td { padding: 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
  tr:hover td { background: rgba(88,166,255,0.04); }

  /* Status badges */
  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 12px;
    font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.3px;
  }
  .badge-queued { background: rgba(139,148,158,0.15); color: var(--muted); }
  .badge-resolving { background: rgba(188,140,255,0.15); color: var(--purple); }
  .badge-running { background: rgba(88,166,255,0.15); color: var(--accent); }
  .badge-creating_pr { background: rgba(210,153,34,0.15); color: var(--yellow); }
  .badge-completed { background: rgba(63,185,80,0.15); color: var(--green); }
  .badge-failed { background: rgba(248,81,73,0.15); color: var(--red); }
  .badge-stuck { background: rgba(219,109,40,0.15); color: var(--orange); }
  .badge-blocked { background: rgba(210,153,34,0.15); color: var(--yellow); }
  .badge-cancelled { background: rgba(139,148,158,0.15); color: var(--muted); }

  .task-id { font-family: inherit; color: var(--accent); cursor: pointer; }
  .task-id:hover { text-decoration: underline; }
  .prompt-cell { max-width: 400px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .prompt-cell:hover { white-space: normal; word-break: break-word; }
  .elapsed { color: var(--muted); font-size: 11px; }
  .actions button {
    background: var(--surface); border: 1px solid var(--border); color: var(--text);
    padding: 3px 8px; border-radius: 4px; cursor: pointer; font-size: 11px;
    font-family: inherit;
  }
  .actions button:hover { border-color: var(--accent); }

  /* Detail panel */
  .detail-overlay {
    position: fixed; top: 0; right: 0; width: 50%; height: 100vh;
    background: var(--surface); border-left: 1px solid var(--border);
    z-index: 100; overflow-y: auto; padding: 20px; display: none;
    box-shadow: -4px 0 20px rgba(0,0,0,0.5);
  }
  .detail-overlay.open { display: block; }
  .detail-overlay h2 { font-size: 14px; margin-bottom: 16px; }
  .detail-overlay .close-btn {
    position: absolute; top: 12px; right: 16px; background: none;
    border: none; color: var(--muted); cursor: pointer; font-size: 20px;
  }
  .detail-section { margin-bottom: 16px; }
  .detail-section h4 { font-size: 11px; text-transform: uppercase; color: var(--muted); margin-bottom: 6px; }
  .detail-section pre {
    background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
    padding: 12px; overflow-x: auto; white-space: pre-wrap; word-break: break-word;
    font-size: 12px; max-height: 400px; overflow-y: auto;
  }
  .log-stream {
    background: #000; border: 1px solid var(--border); border-radius: 6px;
    padding: 12px; font-size: 12px; max-height: 500px; overflow-y: auto;
    white-space: pre-wrap; word-break: break-word; line-height: 1.4;
  }
  .log-stream .log-line { color: var(--text); }
  .log-stream .log-phase { color: var(--purple); font-weight: 600; }
  .log-stream .log-done { color: var(--green); font-weight: 600; }
  .log-stream .log-error { color: var(--red); font-weight: 600; }
  .log-stream .log-spinner {
    display: inline-block; animation: pulse 1.5s infinite; color: var(--accent);
  }
  .log-status { font-size: 11px; color: var(--muted); margin-bottom: 6px; }
  .detail-meta { display: grid; grid-template-columns: auto 1fr; gap: 4px 16px; font-size: 12px; }
  .detail-meta dt { color: var(--muted); }
  .detail-meta dd { color: var(--text); }

  .refresh-indicator {
    font-size: 11px; color: var(--muted); margin-left: auto;
  }
  .refresh-indicator.active { color: var(--accent); }

  .empty-state {
    text-align: center; padding: 60px 20px; color: var(--muted);
  }
  .empty-state p { font-size: 14px; margin-bottom: 8px; }

  /* Backdrop */
  .backdrop {
    position: fixed; top: 0; left: 0; width: 100%; height: 100%;
    background: rgba(0,0,0,0.5); z-index: 90; display: none;
  }
  .backdrop.open { display: block; }

  /* Toast notifications */
  .toast-container {
    position: fixed; bottom: 20px; right: 20px; z-index: 200;
    display: flex; flex-direction: column-reverse; gap: 8px;
  }
  .toast {
    padding: 10px 16px; border-radius: 6px; font-size: 12px;
    max-width: 360px; animation: slideIn 0.3s ease-out;
    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
  }
  .toast-success { background: rgba(63,185,80,0.15); border: 1px solid var(--green); color: var(--green); }
  .toast-error { background: rgba(248,81,73,0.15); border: 1px solid var(--red); color: var(--red); }
  .toast-info { background: rgba(88,166,255,0.15); border: 1px solid var(--accent); color: var(--accent); }
  @keyframes slideIn { from { opacity:0; transform:translateX(40px); } to { opacity:1; transform:translateX(0); } }

  /* Loading shimmer for tables */
  .loading-row td { background: linear-gradient(90deg, var(--surface) 25%, var(--bg) 50%, var(--surface) 75%); background-size: 200% 100%; animation: shimmer 1.5s infinite; }
  @keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }

  @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.5; } }
  .running-indicator { animation: pulse 2s infinite; }

  /* Connection status indicator */
  .conn-status {
    display: inline-flex; align-items: center; gap: 5px;
    padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600;
    transition: all 0.3s ease;
  }
  .conn-status.connected { color: var(--green); background: rgba(63,185,80,0.1); }
  .conn-status.disconnected { color: var(--red); background: rgba(248,81,73,0.1); }
  .conn-status .conn-dot {
    width: 8px; height: 8px; border-radius: 50%; display: inline-block;
    transition: background 0.3s ease;
  }
  .conn-status.connected .conn-dot { background: var(--green); }
  .conn-status.disconnected .conn-dot { background: var(--red); animation: pulse 1.5s infinite; }

  /* Error banner */
  .error-banner {
    background: rgba(248,81,73,0.1); border: 1px solid rgba(248,81,73,0.3);
    border-radius: 6px; padding: 10px 16px; margin-bottom: 16px;
    display: none; align-items: center; gap: 10px; font-size: 12px;
    color: var(--red); animation: slideDown 0.3s ease-out;
  }
  .error-banner.visible { display: flex; }
  .error-banner .banner-icon { font-size: 16px; flex-shrink: 0; }
  .error-banner .banner-msg { flex: 1; }
  .error-banner .banner-dismiss {
    background: none; border: none; color: var(--red); cursor: pointer;
    font-size: 16px; padding: 0 4px; opacity: 0.7; flex-shrink: 0;
  }
  .error-banner .banner-dismiss:hover { opacity: 1; }
  @keyframes slideDown { from { opacity:0; transform:translateY(-10px); } to { opacity:1; transform:translateY(0); } }

  /* Tabs */
  .tab-bar {
    display: flex; gap: 0; margin-bottom: 16px; border-bottom: 1px solid var(--border);
  }
  .tab-bar button {
    background: none; border: none; color: var(--muted); padding: 8px 16px;
    font-family: inherit; font-size: 12px; cursor: pointer; font-weight: 600;
    border-bottom: 2px solid transparent; text-transform: uppercase; letter-spacing: 0.5px;
  }
  .tab-bar button:hover { color: var(--text); }
  .tab-bar button.active { color: var(--accent); border-bottom-color: var(--accent); }
  .tab-content { display: none; }
  .tab-content.active { display: block; }

  /* Pipeline table */
  .pipeline-row { cursor: pointer; }
  .pipeline-row:hover td { background: rgba(88,166,255,0.06); }
  .pipeline-id { font-family: inherit; color: var(--accent); cursor: pointer; }
  .pipeline-id:hover { text-decoration: underline; }

  /* Pipeline stage flow (in pipeline detail overlay) */
  .stage-flow {
    display: flex; align-items: flex-start; gap: 0; overflow-x: auto;
    padding: 12px 0; margin-bottom: 12px;
  }
  .stage-node {
    display: flex; flex-direction: column; align-items: center;
    min-width: 100px; position: relative;
  }
  .stage-box {
    background: var(--surface); border: 2px solid var(--border); border-radius: 8px;
    padding: 8px 12px; text-align: center; min-width: 90px; cursor: pointer;
    transition: border-color 0.2s;
  }
  .stage-box:hover { border-color: var(--accent); }
  .stage-box.stage-active { border-color: var(--accent); box-shadow: 0 0 8px rgba(88,166,255,0.3); }
  .stage-box.stage-completed { border-color: var(--green); }
  .stage-box.stage-failed { border-color: var(--red); }
  .stage-box.stage-pending { border-color: var(--border); opacity: 0.5; }
  .stage-name { font-size: 11px; font-weight: 600; margin-bottom: 2px; }
  .stage-status { font-size: 10px; }
  .stage-task-id { font-size: 9px; color: var(--muted); margin-top: 2px; }
  .stage-arrow {
    display: flex; align-items: center; padding: 0 4px; color: var(--muted);
    font-size: 16px; margin-top: 14px;
  }
  .stage-loop-label {
    font-size: 9px; color: var(--yellow); margin-top: 4px; text-align: center;
  }

  /* Pipeline detail overlay */
  .pipeline-detail-overlay {
    position: fixed; top: 0; right: 0; width: 60%; height: 100vh;
    background: var(--surface); border-left: 1px solid var(--border);
    z-index: 100; overflow-y: auto; padding: 20px; display: none;
    box-shadow: -4px 0 20px rgba(0,0,0,0.5);
  }
  .pipeline-detail-overlay.open { display: block; }
  .pipeline-detail-overlay h2 { font-size: 14px; margin-bottom: 16px; }
  .pipeline-detail-overlay .close-btn {
    position: absolute; top: 12px; right: 16px; background: none;
    border: none; color: var(--muted); cursor: pointer; font-size: 20px;
  }

  /* Stage tasks list inside pipeline detail */
  .stage-tasks { margin-top: 8px; }
  .stage-task-row {
    display: flex; gap: 10px; align-items: center; padding: 6px 8px;
    border: 1px solid var(--border); border-radius: 6px; margin-bottom: 4px;
    font-size: 11px; background: var(--bg); cursor: pointer;
  }
  .stage-task-row:hover { border-color: var(--accent); }

  /* Pipeline timeline in task detail panel */
  .pipeline-timeline {
    background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
    padding: 10px 12px; margin-bottom: 8px;
  }
  .pipeline-timeline-title {
    font-size: 10px; text-transform: uppercase; color: var(--muted);
    letter-spacing: 0.5px; margin-bottom: 8px;
  }
  .pipeline-timeline .stage-flow { padding: 4px 0; margin-bottom: 0; }

  /* Morning report */
  .report-card {
    background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px; margin-bottom: 12px;
  }
  .report-card h3 { font-size: 13px; margin-bottom: 10px; color: var(--accent); }
  .report-counts {
    display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 16px;
  }
  .report-count {
    text-align: center; padding: 12px 20px; background: var(--bg);
    border-radius: 8px; border: 1px solid var(--border); min-width: 80px;
  }
  .report-count .num { font-size: 28px; font-weight: 700; }
  .report-count .label { font-size: 10px; text-transform: uppercase; color: var(--muted); letter-spacing: 0.5px; }
  .report-count.green .num { color: var(--green); }
  .report-count.red .num { color: var(--red); }
  .report-count.blue .num { color: var(--accent); }
  .report-count.yellow .num { color: var(--yellow); }
  .report-count.purple .num { color: var(--purple); }
  .report-pr {
    display: flex; gap: 10px; align-items: center; padding: 8px 10px;
    border: 1px solid var(--border); border-radius: 6px; margin-bottom: 6px;
    background: var(--bg); font-size: 12px;
  }
  .report-pr a { color: var(--accent); }
  .report-task-summary {
    padding: 8px 10px; border: 1px solid var(--border); border-radius: 6px;
    margin-bottom: 6px; background: var(--bg); font-size: 12px;
  }
  .report-task-summary .task-meta { color: var(--muted); font-size: 11px; margin-bottom: 2px; }
  .report-failure {
    padding: 8px 10px; border: 1px solid rgba(248,81,73,0.3); border-radius: 6px;
    margin-bottom: 6px; background: rgba(248,81,73,0.05); font-size: 12px;
  }
  .report-failure .error-text { color: var(--red); font-size: 11px; margin-top: 2px; }

  /* Review inbox */
  .review-header {
    display: flex; gap: 12px; align-items: center; margin-bottom: 14px; flex-wrap: wrap;
  }
  .review-counts { display: flex; gap: 10px; flex-wrap: wrap; }
  .review-count {
    background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
    padding: 8px 12px; min-width: 100px;
  }
  .review-count .num { font-size: 20px; font-weight: 700; }
  .review-count .label { font-size: 10px; text-transform: uppercase; color: var(--muted); }
  .review-count.blocked .num { color: var(--yellow); }
  .review-count.failed .num { color: var(--red); }
  .review-count.todo .num { color: var(--accent); }
  .review-item {
    background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
    padding: 12px; margin-bottom: 10px;
  }
  .review-item-header {
    display: flex; gap: 8px; align-items: center; justify-content: space-between; margin-bottom: 8px;
  }
  .review-title { font-size: 13px; font-weight: 700; }
  .review-why { color: var(--text); margin-bottom: 6px; }
  .review-line { color: var(--muted); font-size: 12px; margin-bottom: 4px; }
  .review-links { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
  .review-link {
    border: 1px solid var(--border); border-radius: 6px; padding: 4px 8px;
    background: var(--bg); font-size: 11px;
  }
  .review-link .task-id, .review-link .pipeline-id { font-size: 11px; }

  /* ===== Kanban Board (embedded tab) ===== */
  #tab-board.active {
    display: flex; flex-direction: column; height: calc(100vh - 200px); overflow: hidden;
  }
  .kb-toolbar {
    display: flex; align-items: center; gap: 12px; padding: 8px 0 12px;
    flex-shrink: 0; flex-wrap: wrap;
  }
  .kb-toolbar .search-input {
    background: var(--surface); border: 1px solid var(--border); color: var(--text);
    padding: 5px 10px; border-radius: 6px; font-size: 13px; width: 200px;
    font-family: inherit;
  }
  .kb-toolbar .search-input:focus { outline: none; border-color: var(--accent); }
  .kb-toolbar .filter-select {
    background: var(--surface); border: 1px solid var(--border); color: var(--text);
    padding: 5px 8px; border-radius: 6px; font-size: 12px; font-family: inherit;
  }
  .kb-toolbar .toolbar-btn {
    background: var(--surface); border: 1px solid var(--border); color: var(--text);
    padding: 5px 10px; border-radius: 6px; font-size: 12px; cursor: pointer;
    font-family: inherit; transition: border-color 0.15s;
  }
  .kb-toolbar .toolbar-btn:hover { border-color: var(--accent); }
  .kb-toolbar .toolbar-btn.active { border-color: var(--green); color: var(--green); }
  .kb-toolbar .toolbar-right { margin-left: auto; display: flex; gap: 8px; align-items: center; }
  .kb-toolbar .refresh-label { font-size: 11px; color: var(--muted); }

  .kb-board {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 12px; flex: 1;
    overflow-x: auto; overflow-y: hidden;
    min-height: 0;
  }
  @media (max-width: 900px) {
    .kb-board { grid-template-columns: repeat(5, minmax(240px, 1fr)); }
  }

  .kb-column {
    background: var(--surface); border-radius: 10px;
    display: flex; flex-direction: column;
    min-height: 0; overflow: hidden;
    border: 1px solid var(--border);
  }
  .kb-column.drag-over { border-color: var(--accent); box-shadow: 0 0 0 2px rgba(88,166,255,0.2); }

  .kb-col-header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 12px 14px 8px; flex-shrink: 0;
  }
  .kb-col-title { font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: var(--muted); }
  .kb-col-count {
    background: var(--border); color: var(--text); font-size: 11px; font-weight: 700;
    padding: 1px 8px; border-radius: 10px; min-width: 22px; text-align: center;
  }
  .kb-col-add-btn {
    background: none; border: 1px dashed var(--border); color: var(--muted);
    width: 28px; height: 28px; border-radius: 6px; cursor: pointer;
    font-size: 18px; display: flex; align-items: center; justify-content: center;
    transition: color 0.15s, border-color 0.15s;
  }
  .kb-col-add-btn:hover { color: var(--accent); border-color: var(--accent); }

  .kb-col-cards {
    flex: 1; overflow-y: auto; padding: 4px 10px 10px;
    min-height: 40px;
  }
  .kb-col-cards::-webkit-scrollbar { width: 4px; }
  .kb-col-cards::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

  .kb-card {
    background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
    padding: 10px 12px; margin-bottom: 8px; cursor: grab;
    box-shadow: 0 1px 3px rgba(0,0,0,0.15);
    transition: box-shadow 0.15s, border-color 0.15s, opacity 0.15s;
    position: relative;
  }
  .kb-card:hover { border-color: var(--accent); box-shadow: 0 2px 8px rgba(0,0,0,0.2); }
  .kb-card.dragging { opacity: 0.4; }
  .kb-card-title { font-size: 13px; font-weight: 600; margin-bottom: 4px; word-break: break-word; }
  .kb-card-desc { font-size: 11px; color: var(--muted); margin-bottom: 6px; word-break: break-word; }
  .kb-card-meta { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
  .kb-card-context {
    margin: 6px 0 7px; font-size: 10px; color: var(--muted);
    display: flex; gap: 6px; flex-wrap: wrap;
  }
  .kb-context-chip {
    border: 1px solid var(--border); border-radius: 10px; padding: 1px 7px;
    background: rgba(255,255,255,0.03);
  }
  .kb-context-chip.gap {
    border-color: rgba(248,81,73,0.45); color: var(--red);
    background: rgba(248,81,73,0.08);
  }
  .kb-coverage-row {
    margin-top: 8px; display: flex; gap: 6px; flex-wrap: wrap;
  }
  .kb-coverage-pill {
    display: inline-block; padding: 1px 6px; border-radius: 10px;
    font-size: 10px; border: 1px solid var(--border); color: var(--muted);
  }
  .kb-coverage-pill.active { border-color: rgba(88,166,255,0.45); color: var(--accent); }
  .kb-coverage-pill.recent { border-color: rgba(63,185,80,0.45); color: var(--green); }
  .kb-coverage-pill.pipeline { border-color: rgba(188,140,255,0.45); color: var(--purple); }
  .kb-coverage-pill.gap { border-color: rgba(248,81,73,0.45); color: var(--red); }

  .kb-pill {
    display: inline-block; padding: 1px 7px; border-radius: 10px;
    font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.3px;
  }
  .kb-pill-high { background: rgba(248,81,73,0.15); color: var(--red); }
  .kb-pill-medium { background: rgba(210,153,34,0.15); color: var(--yellow); }
  .kb-pill-low { background: rgba(63,185,80,0.15); color: var(--green); }
  .kb-pill-tag { background: rgba(255,255,255,0.08); color: var(--muted); }
  .kb-pill-source { background: rgba(188,140,255,0.15); color: var(--purple); }
  .kb-card-jira {
    font-size: 10px; color: var(--accent); text-decoration: none; font-weight: 600;
  }
  .kb-card-jira:hover { text-decoration: underline; }

  .kb-quick-add { padding: 8px 10px; display: none; }
  .kb-quick-add.open { display: block; }
  .kb-quick-add input {
    width: 100%; padding: 6px 8px; border: 1px solid var(--border);
    border-radius: 6px; font-size: 13px; background: var(--bg);
    color: var(--text); font-family: inherit; margin-bottom: 6px;
  }
  .kb-quick-add input:focus { outline: none; border-color: var(--accent); }
  .kb-quick-add-actions { display: flex; gap: 6px; }
  .kb-quick-add-actions select {
    padding: 4px 6px; border: 1px solid var(--border); border-radius: 4px;
    font-size: 11px; background: var(--surface); color: var(--text); font-family: inherit;
  }
  .kb-quick-add-actions button {
    padding: 4px 10px; border: none; border-radius: 4px;
    font-size: 11px; cursor: pointer; font-family: inherit; font-weight: 600;
  }
  .kb-btn-add { background: var(--accent); color: #000; }
  .kb-btn-add:hover { opacity: 0.85; }
  .kb-btn-cancel-add { background: var(--surface); color: var(--muted); border: 1px solid var(--border) !important; }

  /* Kanban detail slide-over */
  .kb-detail-panel {
    position: fixed; top: 0; right: -45%; width: 40%; min-width: 360px; height: 100vh;
    background: var(--surface); border-left: 1px solid var(--border);
    z-index: 100; overflow-y: auto; padding: 24px;
    box-shadow: -4px 0 24px rgba(0,0,0,0.3);
    transition: right 0.25s ease;
  }
  .kb-detail-panel.open { right: 0; }
  .kb-detail-close {
    position: absolute; top: 12px; right: 16px; background: none;
    border: none; color: var(--muted); cursor: pointer; font-size: 22px; line-height: 1;
  }
  .kb-detail-close:hover { color: var(--text); }
  .kb-detail-field { margin-bottom: 16px; }
  .kb-detail-field label {
    display: block; font-size: 11px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.5px; color: var(--muted); margin-bottom: 4px;
  }
  .kb-detail-field input,
  .kb-detail-field textarea,
  .kb-detail-field select {
    width: 100%; padding: 8px 10px; border: 1px solid var(--border);
    border-radius: 6px; font-size: 13px; background: var(--bg);
    color: var(--text); font-family: inherit;
  }
  .kb-detail-field textarea { min-height: 100px; resize: vertical; }
  .kb-detail-field input:focus,
  .kb-detail-field textarea:focus,
  .kb-detail-field select:focus { outline: none; border-color: var(--accent); }

  .kb-detail-actions { display: flex; gap: 8px; margin-top: 20px; padding-top: 16px; border-top: 1px solid var(--border); }
  .kb-btn-save {
    background: var(--accent); color: #000; border: none; padding: 8px 20px;
    border-radius: 6px; font-size: 13px; cursor: pointer; font-weight: 600; font-family: inherit;
  }
  .kb-btn-save:hover { opacity: 0.85; }
  .kb-btn-delete {
    background: none; border: 1px solid var(--red); color: var(--red);
    padding: 8px 16px; border-radius: 6px; font-size: 13px; cursor: pointer;
    font-family: inherit; margin-left: auto;
  }
  .kb-btn-delete:hover { background: var(--red); color: #fff; }

  .kb-detail-meta-row {
    display: flex; gap: 12px; font-size: 11px; color: var(--muted);
    margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--border);
  }
  .kb-detail-meta-row span { display: flex; gap: 4px; }
  .kb-detail-coverage {
    margin-top: 14px; border: 1px solid var(--border); border-radius: 8px;
    padding: 10px; background: rgba(255,255,255,0.02);
  }
  .kb-detail-coverage h4 {
    font-size: 11px; text-transform: uppercase; color: var(--muted); margin-bottom: 8px;
    letter-spacing: 0.5px;
  }
  .kb-detail-links { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
  .kb-detail-link {
    display: flex; gap: 8px; align-items: center; font-size: 11px;
    border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px;
    background: var(--bg);
  }
  .kb-detail-link .task-id, .kb-detail-link .pipeline-id { font-size: 11px; }
</style>
</head>
<body>

<header>
  <h1><span>workbench</span> dashboard</h1>
  <div class="health" id="health">
    <a href="/diagrams" class="nav-link">Diagrams</a>
    <span class="conn-status disconnected" id="connStatus"><span class="conn-dot"></span> <span id="connLabel">connecting...</span></span>
    <span id="workerCount"></span>
    <span id="repoCount"></span>
  </div>
</header>

<div class="error-banner" id="errorBanner">
  <span class="banner-icon">&#x26a0;</span>
  <span class="banner-msg" id="errorBannerMsg">Unable to connect to the API server.</span>
  <button class="banner-dismiss" onclick="dismissErrorBanner()" title="Dismiss">&times;</button>
</div>

<div class="controls">
  <button onclick="toggleDispatch()">+ New Task</button>
  <div class="filter-group" id="searchGroup">
    <label>Search:</label>
    <input id="searchInput" type="text" placeholder="Filter by ID, repo, prompt... (/)" oninput="applySearch()" style="min-width:200px">
  </div>
  <div class="filter-group" id="statusFilterGroup">
    <label>Filter:</label>
    <select id="statusFilter" onchange="fetchTasks()">
      <option value="">All</option>
      <option value="queued">Queued</option>
      <option value="resolving">Resolving</option>
      <option value="running">Running</option>
      <option value="creating_pr">Creating PR</option>
      <option value="completed">Completed</option>
      <option value="failed">Failed</option>
      <option value="stuck">Stuck</option>
      <option value="blocked">Blocked</option>
      <option value="cancelled">Cancelled</option>
    </select>
  </div>
  <div class="filter-group">
    <label>Refresh:</label>
    <select id="refreshInterval" onchange="updateInterval()">
      <option value="3000">3s</option>
      <option value="5000" selected>5s</option>
      <option value="10000">10s</option>
      <option value="30000">30s</option>
      <option value="0">Off</option>
    </select>
  </div>
  <span class="refresh-indicator" id="refreshInd">&#x25cf; auto-refresh</span>
  <span style="font-size:10px;color:var(--muted);margin-left:8px" title="n=new task, /=search, 1-6=tabs, r=refresh, Esc=close">&#x2328; keys</span>
</div>

<div class="dispatch-panel" id="dispatchPanel">
  <h3>Dispatch New Task</h3>
  <div class="form-row">
    <label>Prompt</label>
    <textarea id="dPrompt" placeholder="Describe the task..."></textarea>
  </div>
  <div class="form-row">
    <label>Repo</label>
    <select id="dRepo"><option value="">Auto-detect</option></select>
  </div>
  <div class="form-row">
    <label>Autonomy</label>
    <select id="dAutonomy">
      <option value="local">Local (no push/PR)</option>
      <option value="full">Full (push + draft PR)</option>
      <option value="plan_only">Plan Only</option>
      <option value="research">Research</option>
    </select>
  </div>
  <div class="form-row">
    <label>Model</label>
    <input id="dModel" placeholder="(default)">
  </div>
  <div class="form-row">
    <label></label>
    <button onclick="dispatchTask()">Dispatch</button>
    <button class="secondary" onclick="toggleDispatch()">Cancel</button>
  </div>
</div>

<div class="tab-bar" id="tabBar">
  <button class="active" onclick="switchTab('board')">Board</button>
  <button onclick="switchTab('tasks')">Tasks</button>
  <button onclick="switchTab('pipelines')">Pipelines</button>
  <button onclick="switchTab('schedules')">Schedules</button>
  <button onclick="switchTab('review')">Review Inbox</button>
  <button onclick="switchTab('report')">Morning Report</button>
</div>

<div class="tab-content active" id="tab-board">
  <!-- Kanban toolbar -->
  <div class="kb-toolbar">
    <input type="text" class="search-input" id="kbSearchInput" placeholder="Search cards..." oninput="kbApplyFilters()">
    <select class="filter-select" id="kbPriorityFilter" onchange="kbApplyFilters()">
      <option value="">All priorities</option>
      <option value="high">High</option>
      <option value="medium">Medium</option>
      <option value="low">Low</option>
    </select>
    <select class="filter-select" id="kbSourceFilter" onchange="kbApplyFilters()">
      <option value="">All sources</option>
      <option value="manual">Manual</option>
      <option value="jira">Jira</option>
    </select>
    <div class="toolbar-right">
      <button class="toolbar-btn" onclick="kbFetchTodos()" title="Refresh">&#x21bb; Refresh</button>
      <button class="toolbar-btn" id="kbAutoRefreshBtn" onclick="kbToggleAutoRefresh()" title="Toggle auto-refresh (30s)">Auto</button>
      <span class="refresh-label" id="kbRefreshLabel"></span>
    </div>
  </div>
  <!-- Kanban board -->
  <div class="kb-board" id="kbBoard">
    <!-- Columns injected by JS -->
  </div>
</div>

<!-- Kanban detail panel (outside tab for z-index) -->
<div class="kb-detail-panel" id="kbDetailPanel">
  <button class="kb-detail-close" onclick="kbCloseDetail()">&times;</button>
  <div id="kbDetailContent"></div>
</div>

<div class="tab-content" id="tab-tasks">
  <div class="stats" id="statsBar"></div>
  <table>
    <thead>
      <tr>
        <th class="sortable" onclick="sortTasks('id')">ID</th>
        <th class="sortable" onclick="sortTasks('status')">Status</th>
        <th class="sortable" onclick="sortTasks('repo')">Repo</th>
        <th>Autonomy</th>
        <th>Prompt</th>
        <th>Branch</th>
        <th class="sortable" onclick="sortTasks('created')">Created</th>
        <th class="sortable" onclick="sortTasks('elapsed')">Elapsed</th>
        <th>Actions</th>
      </tr>
    </thead>
    <tbody id="taskBody"></tbody>
  </table>
  <div class="empty-state" id="emptyState" style="display:none">
    <p>No tasks yet</p>
    <span>Click "+ New Task" to dispatch one</span>
  </div>
</div>

<div class="tab-content" id="tab-pipelines">
  <div class="stats" id="pipelineStatsBar"></div>
  <table>
    <thead>
      <tr>
        <th>ID</th>
        <th>Status</th>
        <th>Repo</th>
        <th>Stages</th>
        <th>Current Stage</th>
        <th>Review Iterations</th>
        <th>Created</th>
        <th>Elapsed</th>
        <th>Actions</th>
      </tr>
    </thead>
    <tbody id="pipelineBody"></tbody>
  </table>
  <div class="empty-state" id="pipelineEmptyState" style="display:none">
    <p>No pipelines yet</p>
    <span>Use the API to create a pipeline</span>
  </div>
</div>

<div class="tab-content" id="tab-schedules">
  <div class="stats" id="scheduleStatsBar"></div>
  <table>
    <thead>
      <tr>
        <th>ID</th>
        <th>Name</th>
        <th>Cron</th>
        <th>Timezone</th>
        <th>Type</th>
        <th>Enabled</th>
        <th>Next Run</th>
        <th>Last Run</th>
        <th>Runs</th>
        <th>Last Dispatched</th>
        <th>Error</th>
        <th>Actions</th>
      </tr>
    </thead>
    <tbody id="scheduleBody"></tbody>
  </table>
  <div class="empty-state" id="scheduleEmptyState" style="display:none">
    <p>No schedules yet</p>
    <span>Use the API to create a schedule: POST /schedules</span>
  </div>
</div>

<div class="tab-content" id="tab-review">
  <div class="review-header">
    <div class="filter-group">
      <label>Window:</label>
      <select id="reviewHours" onchange="fetchReviewInbox()">
        <option value="24">Last 24 hours</option>
        <option value="48">Last 48 hours</option>
        <option value="72" selected>Last 72 hours</option>
      </select>
    </div>
    <button class="secondary" onclick="fetchReviewInbox()" style="background:var(--surface);border:1px solid var(--border);color:var(--text);padding:6px 14px;border-radius:6px;cursor:pointer;font-family:inherit;font-size:12px">Refresh Inbox</button>
    <div class="review-counts" id="reviewCounts"></div>
  </div>
  <div id="reviewContent">
    <div class="empty-state"><p>Loading review inbox...</p></div>
  </div>
</div>

<div class="tab-content" id="tab-report">
  <div style="display:flex;gap:12px;align-items:center;margin-bottom:16px">
    <div class="filter-group">
      <label>Window:</label>
      <select id="reportHours" onchange="fetchReport()">
        <option value="6">Last 6 hours</option>
        <option value="12" selected>Last 12 hours</option>
        <option value="24">Last 24 hours</option>
        <option value="48">Last 48 hours</option>
      </select>
    </div>
    <button class="secondary" onclick="fetchReport()" style="background:var(--surface);border:1px solid var(--border);color:var(--text);padding:6px 14px;border-radius:6px;cursor:pointer;font-family:inherit;font-size:12px">Refresh Report</button>
  </div>
  <div id="reportContent">
    <div class="empty-state"><p>Click "Morning Report" or select a time window</p></div>
  </div>
</div>

<div class="toast-container" id="toastContainer"></div>

<div class="backdrop" id="backdrop" onclick="closeAllOverlays()"></div>

<div class="detail-overlay" id="detailPanel">
  <button class="close-btn" onclick="closeDetail()">&times;</button>
  <div id="detailContent"></div>
</div>

<div class="pipeline-detail-overlay" id="pipelineDetailPanel">
  <button class="close-btn" onclick="closePipelineDetail()">&times;</button>
  <div id="pipelineDetailContent"></div>
</div>

<script>
const API = window.location.origin;
let timer = null;
let tasks = [];
let repos = [];
let pipelines = [];
let reviewInbox = { counts: null, items: [] };
let activeTab = 'board';
let searchQuery = '';

function showToast(message, type) {
  type = type || 'info';
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = 'toast toast-' + type;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(function() { toast.style.opacity = '0'; toast.style.transition = 'opacity 0.3s'; setTimeout(function() { toast.remove(); }, 300); }, 4000);
}

// --- Connection state & error banner ---
let apiConnected = false;
let healthCheckTimer = null;
let bannerDismissed = false;
let consecutiveFailures = 0;
let dismissTimer = null;
const BASE_REFRESH_MS = 5000;
const BASE_HEALTH_MS = 30000;
const MAX_BACKOFF_MS = 60000;
let currentRefreshMs = BASE_REFRESH_MS;
let currentHealthMs = BASE_HEALTH_MS;

function setConnected(connected) {
  apiConnected = connected;
  const el = document.getElementById('connStatus');
  const label = document.getElementById('connLabel');
  if (connected) {
    el.className = 'conn-status connected';
    label.textContent = 'connected';
    consecutiveFailures = 0;
    hideErrorBanner();
    // Reset backoff intervals on successful connection
    if (currentRefreshMs !== BASE_REFRESH_MS || currentHealthMs !== BASE_HEALTH_MS) {
      currentRefreshMs = BASE_REFRESH_MS;
      currentHealthMs = BASE_HEALTH_MS;
      updateInterval();
      restartHealthCheck();
    }
  } else {
    el.className = 'conn-status disconnected';
    label.textContent = 'disconnected';
  }
}

function showErrorBanner(msg) {
  if (bannerDismissed) return;
  const banner = document.getElementById('errorBanner');
  document.getElementById('errorBannerMsg').textContent = msg;
  banner.classList.add('visible');
}

function hideErrorBanner() {
  const banner = document.getElementById('errorBanner');
  banner.classList.remove('visible');
  bannerDismissed = false;
}

function dismissErrorBanner() {
  const banner = document.getElementById('errorBanner');
  banner.classList.remove('visible');
  bannerDismissed = true;
  // Clear previous dismiss timer before creating a new one
  if (dismissTimer) { clearTimeout(dismissTimer); dismissTimer = null; }
  // Reset dismiss after 60s so new errors can show
  dismissTimer = setTimeout(function() { bannerDismissed = false; dismissTimer = null; }, 60000);
}

// Wrapper for fetch that checks response status and tracks connectivity
async function apiFetch(url, options) {
  const resp = await fetch(url, options);
  if (!resp.ok) {
    const errText = await resp.text().catch(function() { return resp.statusText; });
    let errMsg;
    try { const errJson = JSON.parse(errText); errMsg = errJson.detail || errJson.error || errText; } catch(e) { errMsg = errText; }
    throw new Error('HTTP ' + resp.status + ': ' + errMsg);
  }
  setConnected(true);
  return resp;
}

function handleFetchError(e, context) {
  consecutiveFailures++;
  const isNetworkError = e.message === 'Failed to fetch' || e.name === 'TypeError';
  if (isNetworkError) {
    setConnected(false);
    showErrorBanner('Unable to reach the API server. Is workbench running?');
  } else if (consecutiveFailures >= 3) {
    showErrorBanner('Repeated API errors — check server health.');
  }
  showToast((context ? context + ': ' : '') + e.message, 'error');
  // Apply exponential backoff on failure
  applyBackoff();
}

function applyBackoff() {
  const newRefresh = Math.min(currentRefreshMs * 2, MAX_BACKOFF_MS);
  const newHealth = Math.min(currentHealthMs * 2, MAX_BACKOFF_MS);
  if (newRefresh !== currentRefreshMs || newHealth !== currentHealthMs) {
    currentRefreshMs = newRefresh;
    currentHealthMs = newHealth;
    updateInterval();
    restartHealthCheck();
  }
}

function restartHealthCheck() {
  if (healthCheckTimer) { clearInterval(healthCheckTimer); healthCheckTimer = null; }
  healthCheckTimer = setInterval(fetchHealth, currentHealthMs);
}

function applySearch() {
  searchQuery = (document.getElementById('searchInput').value || '').toLowerCase();
  if (activeTab === 'tasks') renderTasks();
  else if (activeTab === 'pipelines') renderPipelines();
}

function matchesSearch(fields) {
  if (!searchQuery) return true;
  return fields.some(f => f && f.toLowerCase().indexOf(searchQuery) !== -1);
}

let sortField = 'created';
let sortDir = 'desc';

function sortTasks(field) {
  if (sortField === field) { sortDir = sortDir === 'asc' ? 'desc' : 'asc'; }
  else { sortField = field; sortDir = field === 'created' ? 'desc' : 'asc'; }
  // Update header classes
  document.querySelectorAll('#tab-tasks th.sortable').forEach(th => { th.classList.remove('sort-asc','sort-desc'); });
  const idx = { id:0, status:1, repo:2, created:6, elapsed:7 };
  const headers = document.querySelectorAll('#tab-tasks th.sortable');
  headers.forEach(th => {
    if (th.getAttribute('onclick').indexOf(field) !== -1) {
      th.classList.add(sortDir === 'asc' ? 'sort-asc' : 'sort-desc');
    }
  });
  renderTasks();
}

function getElapsedMs(t) {
  if (t.completed_at && t.started_at) return new Date(t.completed_at) - new Date(t.started_at);
  if (t.started_at) return Date.now() - new Date(t.started_at);
  return 0;
}

function sortedTasks(list) {
  return list.slice().sort(function(a, b) {
    let va, vb;
    if (sortField === 'id') { va = a.id; vb = b.id; }
    else if (sortField === 'status') { va = a.status; vb = b.status; }
    else if (sortField === 'repo') { va = a.input.repo || ''; vb = b.input.repo || ''; }
    else if (sortField === 'created') { va = a.created_at || ''; vb = b.created_at || ''; }
    else if (sortField === 'elapsed') { va = getElapsedMs(a); vb = getElapsedMs(b); return sortDir === 'asc' ? va - vb : vb - va; }
    else { va = ''; vb = ''; }
    if (va < vb) return sortDir === 'asc' ? -1 : 1;
    if (va > vb) return sortDir === 'asc' ? 1 : -1;
    return 0;
  });
}

// --- Tab switching ---
function switchTab(tab) {
  // Stop kanban auto-refresh when leaving board tab
  if (activeTab === 'board' && tab !== 'board') {
    kbStopAutoRefresh();
  }
  activeTab = tab;
  document.querySelectorAll('.tab-bar button').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  document.querySelector('.tab-bar button[onclick*="' + tab + '"]').classList.add('active');
  document.getElementById('tab-' + tab).classList.add('active');
  // Show/hide tab-specific controls
  const taskControls = document.getElementById('statusFilterGroup');
  const searchBox = document.getElementById('searchGroup');
  if (taskControls) taskControls.style.display = (tab === 'tasks') ? '' : 'none';
  if (searchBox) searchBox.style.display = (tab === 'report' || tab === 'board') ? 'none' : '';
  // Close any open detail panels when switching tabs
  closeDetail();
  closePipelineDetail();
  kbCloseDetail();
  // Auto-fetch data when switching to specific tabs
  if (tab === 'board') kbFetchTodos();
  if (tab === 'report') fetchReport();
  if (tab === 'review') fetchReviewInbox();
  if (tab === 'schedules') fetchSchedules();
}

// --- Health ---
async function fetchHealth() {
  try {
    const r = await apiFetch(API + '/health');
    const d = await r.json();
    setConnected(d.status === 'ok');
    document.getElementById('workerCount').textContent = d.workers + ' workers';
    document.getElementById('repoCount').textContent = d.repos.length + ' repos';
    if (repos.length === 0) {
      repos = d.repos;
      const sel = document.getElementById('dRepo');
      repos.forEach(r => { const o = document.createElement('option'); o.value = r; o.textContent = r; sel.appendChild(o); });
    }
  } catch(e) {
    handleFetchError(e, 'Health check failed');
  }
}

// --- Tasks ---
async function fetchTasks() {
  const ind = document.getElementById('refreshInd');
  ind.classList.add('active');
  try {
    const filter = document.getElementById('statusFilter').value;
    const url = API + '/tasks' + (filter ? '?status=' + filter : '') + (filter ? '&' : '?') + 'limit=100';
    const r = await apiFetch(url);
    const d = await r.json();
    tasks = d.tasks || [];
    renderTasks();
    renderStats(d.total);
  } catch(e) {
    handleFetchError(e, 'Failed to fetch tasks');
  }
  setTimeout(() => ind.classList.remove('active'), 300);
}

function renderStats(total) {
  const counts = {};
  tasks.forEach(t => { counts[t.status] = (counts[t.status]||0) + 1; });
  const bar = document.getElementById('statsBar');
  const statuses = ['running','queued','resolving','completed','failed','blocked','stuck','cancelled'];
  bar.innerHTML = '<div class="stat"><div class="num">' + total + '</div><div class="label">Total</div></div>' +
    statuses.filter(s => counts[s]).map(s =>
      '<div class="stat"><div class="num">' + counts[s] + '</div><div class="label">' + s + '</div></div>'
    ).join('');
  // Dynamic page title
  const active = (counts['running']||0) + (counts['resolving']||0) + (counts['creating_pr']||0);
  if (active > 0) {
    document.title = '(' + active + ' active) workbench dashboard';
  } else {
    document.title = 'workbench dashboard';
  }
}

function renderTasks() {
  const tbody = document.getElementById('taskBody');
  const empty = document.getElementById('emptyState');
  const filtered = sortedTasks(tasks.filter(t => matchesSearch([t.id, t.status, t.input.repo, t.input.source, t.branch, t.input.autonomy])));
  if (filtered.length === 0) { tbody.innerHTML = ''; empty.style.display = 'block'; return; }
  empty.style.display = 'none';

  tbody.innerHTML = filtered.map(t => {
    const elapsed = formatElapsed(t);
    const running = ['running','resolving','creating_pr'].includes(t.status);
    return '<tr>' +
      '<td><span class="task-id" onclick="showDetail(\\'' + t.id + '\\')">' + t.id.slice(0,12) + '</span></td>' +
      '<td><span class="badge badge-' + t.status + (running ? ' running-indicator' : '') + '">' + t.status + '</span></td>' +
      '<td>' + (t.input.repo || '<span style="color:var(--muted)">auto</span>') + '</td>' +
      '<td>' + t.input.autonomy + '</td>' +
      '<td class="prompt-cell" title="' + escHtml(t.input.source) + '">' + escHtml(t.input.source.slice(0,100)) + '</td>' +
      '<td style="font-size:11px">' + (t.branch || '—') + '</td>' +
      '<td class="elapsed">' + fmtTimeShort(t.created_at) + '</td>' +
      '<td class="elapsed">' + elapsed + '</td>' +
      '<td class="actions">' + renderActions(t) + '</td>' +
      '</tr>';
  }).join('');
}

function renderActions(t) {
  let btns = '';
  if (['queued','resolving','running','stuck','creating_pr'].includes(t.status)) {
    btns += '<button onclick="cancelTask(\\'' + t.id + '\\')">cancel</button> ';
  }
  if (t.status === 'blocked') {
    btns += '<button onclick="promptUnblock(\\'' + t.id + '\\')">unblock</button> ';
  }
  return btns;
}

function formatElapsed(t) {
  if (t.completed_at && t.started_at) {
    const ms = new Date(t.completed_at) - new Date(t.started_at);
    return formatDuration(ms);
  }
  if (t.started_at) {
    const ms = Date.now() - new Date(t.started_at);
    return formatDuration(ms) + '+';
  }
  return '—';
}

function formatDuration(ms) {
  const s = Math.floor(ms/1000);
  if (s < 60) return s + 's';
  const m = Math.floor(s/60);
  if (m < 60) return m + 'm ' + (s%60) + 's';
  return Math.floor(m/60) + 'h ' + (m%60) + 'm';
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// --- Detail panel with log streaming ---
let activeLogStream = null;  // Current EventSource for SSE

function closeLogStream() {
  if (activeLogStream) {
    activeLogStream.close();
    activeLogStream = null;
  }
}

async function showDetail(id) {
  closeLogStream();
  const panel = document.getElementById('detailPanel');
  const content = document.getElementById('detailContent');
  try {
    const r = await apiFetch(API + '/tasks/' + id);
    const t = await r.json();
    const isActive = ['queued','resolving','running','creating_pr'].includes(t.status);

    // Build pipeline timeline if task belongs to a pipeline
    const timelineHtml = await buildPipelineTimeline(t);

    content.innerHTML =
      '<h2>Task ' + t.id + '</h2>' +
      timelineHtml +
      '<div class="detail-section"><dl class="detail-meta">' +
        '<dt>Status</dt><dd><span class="badge badge-' + t.status + '">' + t.status + '</span>' + (t.phase ? ' (' + t.phase + ')' : '') + '</dd>' +
        '<dt>Repo</dt><dd>' + (t.input.repo || 'auto') + '</dd>' +
        '<dt>Autonomy</dt><dd>' + t.input.autonomy + '</dd>' +
        '<dt>Branch</dt><dd>' + (t.branch || '—') + '</dd>' +
        '<dt>PR</dt><dd>' + (t.pr_url ? '<a href="' + t.pr_url + '" style="color:var(--accent)" target="_blank">' + t.pr_url + '</a>' : '—') + '</dd>' +
        '<dt>Model</dt><dd>' + (t.input.model || 'default') + '</dd>' +
        '<dt>Created</dt><dd>' + fmtTime(t.created_at) + '</dd>' +
        '<dt>Started</dt><dd>' + fmtTime(t.started_at) + '</dd>' +
        '<dt>Completed</dt><dd>' + fmtTime(t.completed_at) + '</dd>' +
        '<dt>Elapsed</dt><dd>' + formatElapsed(t) + '</dd>' +
        '<dt>Retries</dt><dd>' + t.retry_count + '/' + t.max_retries + '</dd>' +
        '<dt>Stale</dt><dd>' + (t.stale ? 'YES' : 'no') + '</dd>' +
        (t.parent_task_id ? '<dt>Parent</dt><dd><span class="task-id" onclick="showDetail(\\'' + t.parent_task_id + '\\')">' + t.parent_task_id + '</span></dd>' : '') +
      '</dl></div>' +
      '<div class="detail-section"><h4>Prompt</h4><pre>' + escHtml(t.input.source) + '</pre></div>' +
      (t.resolved_prompt && t.resolved_prompt !== t.input.source ?
        '<div class="detail-section"><h4>Resolved Prompt</h4><pre>' + escHtml(t.resolved_prompt) + '</pre></div>' : '') +
      '<div class="detail-section">' +
        '<h4>Logs' + (isActive ? ' <span class="log-spinner">&#x25cf;</span> streaming' : '') + '</h4>' +
        '<div class="log-status" id="logStatus">' + (isActive ? 'Connecting to live stream...' : 'Task ' + t.status) + '</div>' +
        '<div class="log-stream" id="logStream"></div>' +
      '</div>' +
      (t.error ? '<div class="detail-section"><h4>Error</h4><pre style="color:var(--red)">' + escHtml(t.error) + '</pre></div>' : '') +
      (t.blocked_reason ? '<div class="detail-section"><h4>Blocked Reason</h4><pre style="color:var(--yellow)">' + escHtml(t.blocked_reason) + '</pre></div>' : '') +
      (t.summary ? '<div class="detail-section"><h4>Summary</h4><pre>' + escHtml(t.summary) + '</pre></div>' : '') +
      (t.extra_instructions ? '<div class="detail-section"><h4>Extra Instructions</h4><pre>' + escHtml(t.extra_instructions) + '</pre></div>' : '');
    panel.classList.add('open');
    document.getElementById('backdrop').classList.add('open');

    // Start SSE log stream
    startLogStream(id);
  } catch(e) {
    handleFetchError(e, 'Failed to load task details');
    content.innerHTML = '<div class="empty-state"><p>Failed to load task details</p><span>' + escHtml(e.message) + '</span></div>';
    panel.classList.add('open');
    document.getElementById('backdrop').classList.add('open');
  }
}

function startLogStream(taskId) {
  const logEl = document.getElementById('logStream');
  const statusEl = document.getElementById('logStatus');
  if (!logEl) return;

  const evtSource = new EventSource(API + '/tasks/' + taskId + '/logs');
  activeLogStream = evtSource;

  evtSource.onmessage = function(event) {
    try {
      const data = JSON.parse(event.data);
      if (data.type === 'log') {
        const span = document.createElement('span');
        span.className = 'log-line';
        span.textContent = data.data;
        logEl.appendChild(span);
        logEl.scrollTop = logEl.scrollHeight;
      } else if (data.type === 'phase') {
        const div = document.createElement('div');
        div.className = 'log-phase';
        div.textContent = '\\n--- ' + data.phase + ' ---\\n';
        logEl.appendChild(div);
        statusEl.textContent = 'Phase: ' + data.phase;
        logEl.scrollTop = logEl.scrollHeight;
      } else if (data.type === 'done') {
        const div = document.createElement('div');
        div.className = 'log-done';
        div.textContent = '\\n=== Task ' + data.status + ' ===';
        logEl.appendChild(div);
        statusEl.textContent = 'Task ' + data.status;
        logEl.scrollTop = logEl.scrollHeight;
        evtSource.close();
        activeLogStream = null;
        // Refresh task list to update status
        fetchTasks();
      } else if (data.type === 'error') {
        const div = document.createElement('div');
        div.className = 'log-error';
        div.textContent = '\\nERROR: ' + data.error;
        logEl.appendChild(div);
        statusEl.textContent = 'Error occurred';
        logEl.scrollTop = logEl.scrollHeight;
      }
    } catch(e) { console.error('SSE parse error', e); }
  };

  evtSource.onerror = function() {
    statusEl.textContent = 'Stream disconnected';
    evtSource.close();
    activeLogStream = null;
  };
}

function closeDetail() {
  closeLogStream();
  document.getElementById('detailPanel').classList.remove('open');
  if (!document.getElementById('pipelineDetailPanel').classList.contains('open')) {
    document.getElementById('backdrop').classList.remove('open');
  }
}

function closeAllOverlays() {
  closeDetail();
  closePipelineDetail();
  kbCloseDetail();
  document.getElementById('backdrop').classList.remove('open');
}

function fmtTime(s) {
  if (!s) return '—';
  const d = new Date(s);
  return d.toLocaleTimeString() + ' ' + d.toLocaleDateString();
}

function fmtTimeShort(s) {
  if (!s) return '—';
  const d = new Date(s);
  const now = new Date();
  const time = d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'});
  // Show date only if not today
  if (d.toDateString() !== now.toDateString()) {
    return d.toLocaleDateString([], {month:'short', day:'numeric'}) + ' ' + time;
  }
  return time;
}

// --- Actions ---
async function cancelTask(id) {
  if (!confirm('Cancel task ' + id + '?')) return;
  try {
    await apiFetch(API + '/tasks/' + id + '/cancel', { method: 'POST' });
    showToast('Task cancelled: ' + id.slice(0,12), 'success');
    fetchTasks();
  } catch(e) { handleFetchError(e, 'Failed to cancel task'); }
}

async function promptUnblock(id) {
  const response = prompt('Enter response to unblock the task:');
  if (!response) return;
  try {
    await apiFetch(API + '/tasks/' + id + '/unblock', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ response })
    });
    showToast('Task unblocked: ' + id.slice(0,12), 'success');
    fetchTasks();
  } catch(e) { handleFetchError(e, 'Failed to unblock task'); }
}

// --- Dispatch ---
function toggleDispatch() {
  document.getElementById('dispatchPanel').classList.toggle('open');
}

async function dispatchTask() {
  const prompt = document.getElementById('dPrompt').value.trim();
  if (!prompt) { alert('Prompt is required'); return; }
  const repo = document.getElementById('dRepo').value || null;
  const autonomy = document.getElementById('dAutonomy').value;
  const model = document.getElementById('dModel').value.trim() || null;

  const payload = { type: 'prompt', source: prompt, repo, autonomy, model };
  try {
    const r = await apiFetch(API + '/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const result = await r.json();
    document.getElementById('dPrompt').value = '';
    toggleDispatch();
    showToast('Task dispatched: ' + (result.task_id || '').slice(0,12), 'success');
    fetchTasks();
  } catch(e) { handleFetchError(e, 'Failed to dispatch task'); }
}

// --- Pipelines ---
async function fetchPipelines() {
  try {
    const r = await apiFetch(API + '/pipelines?limit=100');
    const d = await r.json();
    pipelines = d.pipelines || [];
    renderPipelines();
    renderPipelineStats();
  } catch(e) { handleFetchError(e, 'Failed to fetch pipelines'); }
}

function renderPipelineStats() {
  const counts = {};
  pipelines.forEach(p => { counts[p.status] = (counts[p.status]||0) + 1; });
  const bar = document.getElementById('pipelineStatsBar');
  const statuses = ['running','pending','completed','failed','cancelled'];
  bar.innerHTML = '<div class="stat"><div class="num">' + pipelines.length + '</div><div class="label">Total</div></div>' +
    statuses.filter(s => counts[s]).map(s =>
      '<div class="stat"><div class="num">' + counts[s] + '</div><div class="label">' + s + '</div></div>'
    ).join('');
}

function renderPipelines() {
  const tbody = document.getElementById('pipelineBody');
  const empty = document.getElementById('pipelineEmptyState');
  const filtered = pipelines.filter(p => matchesSearch([p.id, p.status, p.repo, p.stages.map(s => s.name).join(' ')]));
  if (filtered.length === 0) { tbody.innerHTML = ''; empty.style.display = 'block'; return; }
  empty.style.display = 'none';

  tbody.innerHTML = filtered.map(p => {
    const running = p.status === 'running';
    const currentStage = p.stages[p.current_stage_index];
    const elapsed = pipelineElapsed(p);
    return '<tr class="pipeline-row" onclick="showPipelineDetail(\\'' + p.id + '\\')">' +
      '<td><span class="pipeline-id">' + p.id.slice(0,12) + '</span></td>' +
      '<td><span class="badge badge-' + p.status + (running ? ' running-indicator' : '') + '">' + p.status + '</span></td>' +
      '<td>' + (p.repo || '<span style="color:var(--muted)">—</span>') + '</td>' +
      '<td>' + p.stages.length + '</td>' +
      '<td>' + (currentStage ? currentStage.name : '—') + (running ? ' <span class="log-spinner">&#x25cf;</span>' : '') + '</td>' +
      '<td>' + p.review_iteration + '/' + p.max_review_iterations + '</td>' +
      '<td class="elapsed">' + fmtTimeShort(p.created_at) + '</td>' +
      '<td class="elapsed">' + elapsed + '</td>' +
      '<td class="actions">' + pipelineActions(p) + '</td>' +
      '</tr>';
  }).join('');
}

function pipelineElapsed(p) {
  if (p.completed_at && p.created_at) {
    return formatDuration(new Date(p.completed_at) - new Date(p.created_at));
  }
  if (p.status === 'running' && p.created_at) {
    return formatDuration(Date.now() - new Date(p.created_at)) + '+';
  }
  return '—';
}

function pipelineActions(p) {
  if (['running','pending'].includes(p.status)) {
    return '<button onclick="event.stopPropagation(); cancelPipeline(\\'' + p.id + '\\')">cancel</button>';
  }
  return '';
}

async function cancelPipeline(id) {
  if (!confirm('Cancel pipeline ' + id + '?')) return;
  try {
    await apiFetch(API + '/pipelines/' + id + '/cancel', { method: 'POST' });
    showToast('Pipeline cancelled: ' + id.slice(0,12), 'success');
    fetchPipelines();
  } catch(e) { handleFetchError(e, 'Failed to cancel pipeline'); }
}

// --- Pipeline detail overlay ---
function closePipelineDetail() {
  document.getElementById('pipelineDetailPanel').classList.remove('open');
  if (!document.getElementById('detailPanel').classList.contains('open')) {
    document.getElementById('backdrop').classList.remove('open');
  }
}

async function showPipelineDetail(id) {
  const panel = document.getElementById('pipelineDetailPanel');
  const content = document.getElementById('pipelineDetailContent');
  try {
    const r = await apiFetch(API + '/pipelines/' + id);
    const p = await r.json();

    // Fetch tasks for this pipeline to get their statuses
    const taskMap = {};
    for (const tid of (p.task_ids || [])) {
      try {
        const tr = await apiFetch(API + '/tasks/' + tid);
        taskMap[tid] = await tr.json();
      } catch(e) { /* individual task fetch failure is non-critical */ }
    }

    content.innerHTML =
      '<h2>Pipeline ' + p.id + '</h2>' +
      '<div class="detail-section"><dl class="detail-meta">' +
        '<dt>Status</dt><dd><span class="badge badge-' + p.status + '">' + p.status + '</span></dd>' +
        '<dt>Repo</dt><dd>' + (p.repo || '—') + '</dd>' +
        '<dt>Model</dt><dd>' + (p.model || 'default') + '</dd>' +
        '<dt>Stages</dt><dd>' + p.stages.length + '</dd>' +
        '<dt>Current Stage</dt><dd>' + (p.stages[p.current_stage_index] ? p.stages[p.current_stage_index].name : '—') + '</dd>' +
        '<dt>Review Iterations</dt><dd>' + p.review_iteration + ' / ' + p.max_review_iterations + '</dd>' +
        '<dt>Created</dt><dd>' + fmtTime(p.created_at) + '</dd>' +
        '<dt>Completed</dt><dd>' + fmtTime(p.completed_at) + '</dd>' +
        '<dt>Elapsed</dt><dd>' + pipelineElapsed(p) + '</dd>' +
        (p.error ? '<dt>Error</dt><dd style="color:var(--red)">' + escHtml(p.error) + '</dd>' : '') +
      '</dl></div>' +
      '<div class="detail-section"><h4>Stage Flow</h4>' +
        buildStageFlow(p, taskMap) +
      '</div>' +
      '<div class="detail-section"><h4>Stage Details</h4>' +
        buildStageDetails(p, taskMap) +
      '</div>';

    panel.classList.add('open');
    document.getElementById('backdrop').classList.add('open');
  } catch(e) {
    handleFetchError(e, 'Failed to load pipeline details');
    content.innerHTML = '<div class="empty-state"><p>Failed to load pipeline details</p><span>' + escHtml(e.message) + '</span></div>';
    panel.classList.add('open');
    document.getElementById('backdrop').classList.add('open');
  }
}

function buildStageFlow(pipeline, taskMap) {
  const stages = pipeline.stages;
  let html = '<div class="stage-flow">';

  for (let i = 0; i < stages.length; i++) {
    const stage = stages[i];
    const isActive = pipeline.status === 'running' && i === pipeline.current_stage_index;
    const isCompleted = i < pipeline.current_stage_index ||
      (pipeline.status === 'completed' && i === pipeline.current_stage_index);
    const isFailed = pipeline.status === 'failed' && i === pipeline.current_stage_index;

    // Find task for this stage
    const stageTask = findStageTask(pipeline, i, taskMap);
    let statusClass = 'stage-pending';
    let statusText = 'pending';
    if (isActive) { statusClass = 'stage-active'; statusText = stageTask ? stageTask.status : 'active'; }
    else if (isCompleted) { statusClass = 'stage-completed'; statusText = 'done'; }
    else if (isFailed) { statusClass = 'stage-failed'; statusText = 'failed'; }

    html += '<div class="stage-node">' +
      '<div class="stage-box ' + statusClass + '"' +
        (stageTask ? ' onclick="event.stopPropagation(); closePipelineDetail(); showDetail(\\'' + stageTask.id + '\\')"' : '') +
        '>' +
        '<div class="stage-name">' + escHtml(stage.name) + '</div>' +
        '<div class="stage-status"><span class="badge badge-' + statusText + '">' + statusText + '</span></div>' +
        (stageTask ? '<div class="stage-task-id">' + stageTask.id.slice(0,8) + '</div>' : '') +
      '</div>' +
      (stage.review_gate ? '<div class="stage-loop-label">review gate</div>' : '') +
    '</div>';

    if (i < stages.length - 1) {
      html += '<div class="stage-arrow">&rarr;</div>';
    }
  }

  // Show loop-back arrow if there are review iterations
  if (pipeline.review_iteration > 0) {
    html += '<div style="margin-left:12px;color:var(--yellow);font-size:11px;align-self:center">' +
      '&#x21BA; looped ' + pipeline.review_iteration + 'x</div>';
  }

  html += '</div>';
  return html;
}

function findStageTask(pipeline, stageIndex, taskMap) {
  // Look through task_ids to find one matching this stage
  const stageName = pipeline.stages[stageIndex].name;
  for (const tid of pipeline.task_ids) {
    const t = taskMap[tid];
    if (t && t.stage_name === stageName) return t;
  }
  // Fallback: if current_task_id matches current stage
  if (stageIndex === pipeline.current_stage_index && pipeline.current_task_id) {
    return taskMap[pipeline.current_task_id] || null;
  }
  return null;
}

function buildStageDetails(pipeline, taskMap) {
  let html = '';
  for (let i = 0; i < pipeline.stages.length; i++) {
    const stage = pipeline.stages[i];
    const stageTasks = getStageTasks(pipeline, i, taskMap);

    html += '<div style="margin-bottom:12px">' +
      '<div style="font-size:12px;font-weight:600;margin-bottom:4px">' +
        (i + 1) + '. ' + escHtml(stage.name) +
        ' <span style="color:var(--muted);font-weight:400">(' + stage.autonomy + ')</span>' +
        (stage.review_gate ? ' <span style="color:var(--yellow);font-size:10px">&#x2691; review gate</span>' : '') +
      '</div>' +
      '<div style="font-size:11px;color:var(--muted);margin-bottom:6px;max-height:60px;overflow:hidden">' +
        escHtml(stage.prompt.slice(0, 200)) + (stage.prompt.length > 200 ? '...' : '') +
      '</div>';

    if (stageTasks.length > 0) {
      html += '<div class="stage-tasks">';
      for (const t of stageTasks) {
        const running = ['running','resolving','creating_pr'].includes(t.status);
        html += '<div class="stage-task-row" onclick="event.stopPropagation(); closePipelineDetail(); showDetail(\\'' + t.id + '\\')">' +
          '<span class="task-id">' + t.id.slice(0,12) + '</span>' +
          '<span class="badge badge-' + t.status + (running ? ' running-indicator' : '') + '">' + t.status + '</span>' +
          '<span class="elapsed">' + formatElapsed(t) + '</span>' +
          (t.summary ? '<span style="color:var(--muted);font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:300px">' +
            escHtml(t.summary.slice(0, 80)) + '</span>' : '') +
        '</div>';
      }
      html += '</div>';
    } else {
      html += '<div style="font-size:11px;color:var(--muted);font-style:italic">No tasks yet</div>';
    }
    html += '</div>';
  }
  return html;
}

function getStageTasks(pipeline, stageIndex, taskMap) {
  const stageName = pipeline.stages[stageIndex].name;
  const result = [];
  for (const tid of pipeline.task_ids) {
    const t = taskMap[tid];
    if (t && t.stage_name === stageName) result.push(t);
  }
  return result;
}

// --- Pipeline timeline in task detail panel ---
async function buildPipelineTimeline(task) {
  if (!task.pipeline_id) return '';
  try {
    const r = await apiFetch(API + '/pipelines/' + task.pipeline_id);
    const p = await r.json();

    let html = '<div class="pipeline-timeline">' +
      '<div class="pipeline-timeline-title">Pipeline ' +
        '<span class="pipeline-id" onclick="showPipelineDetail(\\'' + p.id + '\\')" style="color:var(--accent);cursor:pointer">' +
          p.id.slice(0,12) +
        '</span>' +
        ' &mdash; ' + p.status +
        (p.review_iteration > 0 ? ' (looped ' + p.review_iteration + 'x)' : '') +
      '</div>';

    html += '<div class="stage-flow">';
    for (let i = 0; i < p.stages.length; i++) {
      const stage = p.stages[i];
      const isActive = p.status === 'running' && i === p.current_stage_index;
      const isCompleted = i < p.current_stage_index ||
        (p.status === 'completed' && i === p.current_stage_index);
      const isFailed = p.status === 'failed' && i === p.current_stage_index;
      const isCurrent = task.stage_name === stage.name;

      let statusClass = 'stage-pending';
      if (isActive) statusClass = 'stage-active';
      else if (isCompleted) statusClass = 'stage-completed';
      else if (isFailed) statusClass = 'stage-failed';

      html += '<div class="stage-node">' +
        '<div class="stage-box ' + statusClass + '"' +
          ' style="min-width:70px;padding:4px 8px' + (isCurrent ? ';box-shadow:0 0 0 2px var(--accent)' : '') + '">' +
          '<div class="stage-name" style="font-size:10px">' + escHtml(stage.name) + '</div>' +
        '</div>' +
      '</div>';

      if (i < p.stages.length - 1) {
        html += '<div class="stage-arrow" style="font-size:12px;margin-top:8px">&rarr;</div>';
      }
    }
    html += '</div></div>';
    return html;
  } catch(e) { return ''; }
}

// --- Schedules ---
let schedules = [];

async function fetchSchedules() {
  try {
    const r = await apiFetch(API + '/schedules?limit=100');
    const d = await r.json();
    schedules = d.schedules || [];
    renderSchedules();
    renderScheduleStats();
  } catch(e) { handleFetchError(e, 'Failed to fetch schedules'); }
}

function renderScheduleStats() {
  const bar = document.getElementById('scheduleStatsBar');
  const enabled = schedules.filter(s => s.enabled).length;
  const disabled = schedules.length - enabled;
  const withErrors = schedules.filter(s => s.error).length;
  const totalRuns = schedules.reduce((sum, s) => sum + s.run_count, 0);
  bar.innerHTML =
    '<div class="stat"><div class="num">' + schedules.length + '</div><div class="label">Total</div></div>' +
    '<div class="stat"><div class="num" style="color:var(--green)">' + enabled + '</div><div class="label">Enabled</div></div>' +
    (disabled ? '<div class="stat"><div class="num" style="color:var(--muted)">' + disabled + '</div><div class="label">Disabled</div></div>' : '') +
    (withErrors ? '<div class="stat"><div class="num" style="color:var(--red)">' + withErrors + '</div><div class="label">Errors</div></div>' : '') +
    '<div class="stat"><div class="num" style="color:var(--accent)">' + totalRuns + '</div><div class="label">Total Runs</div></div>';
}

function renderSchedules() {
  const tbody = document.getElementById('scheduleBody');
  const empty = document.getElementById('scheduleEmptyState');
  const filtered = schedules.filter(s => matchesSearch([s.id, s.name, s.cron_expr, s.schedule_type]));
  if (filtered.length === 0) { tbody.innerHTML = ''; empty.style.display = 'block'; return; }
  empty.style.display = 'none';

  tbody.innerHTML = filtered.map(s => {
    const lastDispatchId = s.schedule_type === 'task' ? s.last_task_id : s.last_pipeline_id;
    return '<tr>' +
      '<td style="font-size:11px;color:var(--muted)">' + s.id.slice(0,12) + '</td>' +
      '<td style="font-weight:600">' + escHtml(s.name) + '</td>' +
      '<td><code style="background:var(--bg);padding:2px 6px;border-radius:4px;font-size:11px">' + escHtml(s.cron_expr) + '</code></td>' +
      '<td style="font-size:11px;color:var(--muted)">' + escHtml(s.timezone) + '</td>' +
      '<td><span class="badge badge-' + (s.schedule_type === 'task' ? 'running' : 'creating_pr') + '">' + s.schedule_type + '</span></td>' +
      '<td>' +
        '<span onclick="toggleScheduleEnabled(\\'' + s.id + '\\', ' + !s.enabled + ')" style="cursor:pointer">' +
          (s.enabled ? '<span style="color:var(--green)">\\u25cf ON</span>' : '<span style="color:var(--muted)">\\u25cb OFF</span>') +
        '</span>' +
      '</td>' +
      '<td class="elapsed">' + fmtTimeShort(s.next_run_at) + '</td>' +
      '<td class="elapsed">' + fmtTimeShort(s.last_run_at) + '</td>' +
      '<td style="font-size:12px">' + s.run_count + '</td>' +
      '<td>' + (lastDispatchId ?
        '<span class="task-id" onclick="' + (s.schedule_type === 'task' ? 'showDetail' : 'showPipelineDetail') + '(\\'' + lastDispatchId + '\\')">' +
          lastDispatchId.slice(0,12) + '</span>' : '\\u2014') +
      '</td>' +
      '<td style="max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--red);font-size:11px" title="' + escHtml(s.error || '') + '">' +
        (s.error ? escHtml(s.error.slice(0, 60)) : '\\u2014') + '</td>' +
      '<td class="actions">' +
        '<button onclick="triggerSchedule(\\'' + s.id + '\\')" title="Trigger now">\\u25B6</button> ' +
        '<button onclick="deleteSchedule(\\'' + s.id + '\\', \\'' + escHtml(s.name) + '\\')" title="Delete" style="color:var(--red)">\\u2715</button>' +
      '</td>' +
      '</tr>';
  }).join('');
}

async function toggleScheduleEnabled(id, enabled) {
  try {
    await apiFetch(API + '/schedules/' + id, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled })
    });
    showToast('Schedule ' + (enabled ? 'enabled' : 'disabled'), 'success');
    fetchSchedules();
  } catch(e) { handleFetchError(e, 'Failed to update schedule'); }
}

async function triggerSchedule(id) {
  try {
    const r = await apiFetch(API + '/schedules/' + id + '/trigger', { method: 'POST' });
    const d = await r.json();
    showToast('Triggered! Dispatched: ' + (d.dispatched_id || 'n/a').slice(0,12), 'success');
    fetchSchedules();
    fetchTasks();
    fetchPipelines();
  } catch(e) { handleFetchError(e, 'Trigger failed'); }
}

async function deleteSchedule(id, name) {
  if (!confirm('Delete schedule "' + name + '" (' + id.slice(0,12) + ')?')) return;
  try {
    await apiFetch(API + '/schedules/' + id, { method: 'DELETE' });
    showToast('Schedule deleted', 'success');
    fetchSchedules();
  } catch(e) { handleFetchError(e, 'Delete failed'); }
}

// --- Morning Report ---
async function fetchReviewInbox() {
  var hours = document.getElementById('reviewHours').value;
  var content = document.getElementById('reviewContent');
  content.innerHTML = '<div class="empty-state"><p>Loading review inbox...</p></div>';
  try {
    var r = await apiFetch(API + '/review-inbox?recent_hours=' + hours);
    var d = await r.json();
    reviewInbox = d || { counts: null, items: [] };
    renderReviewInbox();
  } catch (e) {
    handleFetchError(e, 'Failed to load review inbox');
    content.innerHTML = '<div class="empty-state"><p>Failed to load review inbox</p><span>' + escHtml(e.message) + '</span></div>';
  }
}

function renderReviewInbox() {
  var content = document.getElementById('reviewContent');
  var countsEl = document.getElementById('reviewCounts');
  var counts = reviewInbox.counts || { total: 0, blocked_tasks: 0, failed_tasks: 0, failed_pipelines: 0, todo_review_items: 0 };
  var items = reviewInbox.items || [];

  countsEl.innerHTML =
    '<div class="review-count"><div class="num">' + counts.total + '</div><div class="label">Total</div></div>' +
    '<div class="review-count blocked"><div class="num">' + counts.blocked_tasks + '</div><div class="label">Blocked</div></div>' +
    '<div class="review-count failed"><div class="num">' + counts.failed_tasks + '</div><div class="label">Failed Tasks</div></div>' +
    '<div class="review-count failed"><div class="num">' + counts.failed_pipelines + '</div><div class="label">Failed Pipelines</div></div>' +
    '<div class="review-count todo"><div class="num">' + counts.todo_review_items + '</div><div class="label">Review Todos</div></div>';

  if (items.length === 0) {
    content.innerHTML = '<div class="empty-state"><p>No review items right now</p><span>New blocked/failed work will appear here.</span></div>';
    return;
  }

  content.innerHTML = items.map(function(item) {
    return '<div class="review-item">' +
      '<div class="review-item-header">' +
        '<div class="review-title">' + escHtml(item.title || item.id) + '</div>' +
        '<span class="badge badge-' + escHtml(item.status || 'queued') + '">' + escHtml(item.status || 'unknown') + '</span>' +
      '</div>' +
      '<div class="review-why">' + escHtml(item.why || '') + '</div>' +
      '<div class="review-line"><strong>Recommendation:</strong> ' + escHtml(item.recommendation || '') + '</div>' +
      (item.summary ? '<div class="review-line"><strong>Summary:</strong> ' + escHtml(item.summary) + '</div>' : '') +
      (item.evidence_summary ? '<div class="review-line"><strong>Evidence:</strong> ' + escHtml(item.evidence_summary) + '</div>' : '') +
      (item.blocking_reason ? '<div class="review-line"><strong>Blocking reason:</strong> ' + escHtml(item.blocking_reason) + '</div>' : '') +
      '<div class="review-line"><strong>Context:</strong> ' +
        (item.repo ? 'repo ' + escHtml(item.repo) + ' \u00b7 ' : '') +
        (item.branch ? 'branch ' + escHtml(item.branch) + ' \u00b7 ' : '') +
        (item.stage_name ? 'stage ' + escHtml(item.stage_name) + ' \u00b7 ' : '') +
        'kind ' + escHtml(item.kind || 'unknown') +
      '</div>' +
      '<div class="review-links">' +
        (item.todo_id ? '<span class="review-link">todo <span class="task-id" onclick="switchTab(\\'board\\'); kbOpenDetail(\\'' + item.todo_id + '\\')">' + item.todo_id.slice(0,12) + '</span></span>' : '') +
        (item.task_id ? '<span class="review-link">task <span class="task-id" onclick="switchTab(\\'tasks\\'); setTimeout(function(){ showDetail(\\'' + item.task_id + '\\'); }, 50)">' + item.task_id.slice(0,12) + '</span></span>' : '') +
        (item.pipeline_id ? '<span class="review-link">pipeline <span class="pipeline-id" onclick="switchTab(\\'pipelines\\'); setTimeout(function(){ showPipelineDetail(\\'' + item.pipeline_id + '\\'); }, 50)">' + item.pipeline_id.slice(0,12) + '</span></span>' : '') +
      '</div>' +
    '</div>';
  }).join('');
}

// --- Morning Report ---
async function fetchReport() {
  const hours = document.getElementById('reportHours').value;
  const container = document.getElementById('reportContent');
  container.innerHTML = '<div class="empty-state"><p>Loading report...</p></div>';
  try {
    const r = await apiFetch(API + '/morning-report?hours=' + hours);
    const d = await r.json();
    renderReport(d);
  } catch(e) {
    handleFetchError(e, 'Failed to load report');
    container.innerHTML = '<div class="empty-state"><p>Failed to load report</p><span>' + escHtml(e.message) + '</span></div>';
  }
}

function renderReport(data) {
  const container = document.getElementById('reportContent');
  const c = data.counts;
  let html = '';

  // Counts overview
  html += '<div class="report-counts">' +
    '<div class="report-count"><div class="num">' + c.total + '</div><div class="label">Dispatched</div></div>' +
    '<div class="report-count green"><div class="num">' + c.completed + '</div><div class="label">Completed</div></div>' +
    '<div class="report-count red"><div class="num">' + c.failed + '</div><div class="label">Failed</div></div>' +
    '<div class="report-count blue"><div class="num">' + c.running + '</div><div class="label">Running</div></div>' +
    '<div class="report-count purple"><div class="num">' + c.pipelines + '</div><div class="label">Pipelines</div></div>' +
    '<div class="report-count yellow"><div class="num">' + c.prs_created + '</div><div class="label">PRs Created</div></div>' +
  '</div>';

  // PRs created
  if (data.prs && data.prs.length > 0) {
    html += '<div class="report-card"><h3>Draft PRs Created</h3>';
    for (const pr of data.prs) {
      html += '<div class="report-pr">' +
        '<span style="color:var(--muted)">' + (pr.repo || '—') + '</span>' +
        '<a href="' + escHtml(pr.pr_url) + '" target="_blank">' + escHtml(pr.pr_url) + '</a>' +
        (pr.summary ? '<span style="color:var(--text);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + escHtml(pr.summary.slice(0,120)) + '</span>' : '') +
      '</div>';
    }
    html += '</div>';
  }

  // Pipeline summaries
  if (data.pipelines && data.pipelines.length > 0) {
    html += '<div class="report-card"><h3>Pipelines</h3>';
    for (const p of data.pipelines) {
      html += '<div class="report-task-summary">' +
        '<div class="task-meta">' +
          '<span class="pipeline-id" onclick="switchTab(\\'pipelines\\'); setTimeout(()=>showPipelineDetail(\\'' + p.id + '\\'),100)">' + p.id.slice(0,12) + '</span>' +
          ' &mdash; <span class="badge badge-' + p.status + '">' + p.status + '</span>' +
          ' &mdash; ' + (p.repo || '—') +
          ' &mdash; ' + p.stages_completed + '/' + p.stages_total + ' stages' +
          (p.review_iterations > 0 ? ' &mdash; ' + p.review_iterations + ' review loops' : '') +
        '</div>' +
        (p.error ? '<div class="error-text">' + escHtml(p.error) + '</div>' : '') +
      '</div>';
    }
    html += '</div>';
  }

  // Completed tasks
  if (data.completed_tasks && data.completed_tasks.length > 0) {
    html += '<div class="report-card"><h3>Completed Tasks (' + data.completed_tasks.length + ')</h3>';
    for (const t of data.completed_tasks) {
      const elapsed = t.elapsed_seconds ? formatDuration(t.elapsed_seconds * 1000) : '—';
      html += '<div class="report-task-summary">' +
        '<div class="task-meta">' +
          '<span class="task-id" onclick="switchTab(\\'tasks\\'); setTimeout(()=>showDetail(\\'' + t.id + '\\'),100)">' + t.id.slice(0,12) + '</span>' +
          ' &mdash; ' + (t.repo || '—') +
          ' &mdash; ' + t.autonomy +
          ' &mdash; ' + elapsed +
          (t.branch ? ' &mdash; branch: ' + t.branch : '') +
          (t.pr_url ? ' &mdash; <a href="' + escHtml(t.pr_url) + '" target="_blank" style="color:var(--accent)">PR</a>' : '') +
          (t.pipeline_id ? ' &mdash; pipeline stage: ' + (t.stage_name || '?') : '') +
        '</div>' +
        (t.summary ? '<div style="margin-top:4px;color:var(--text)">' + escHtml(t.summary.slice(0, 200)) + '</div>' : '') +
      '</div>';
    }
    html += '</div>';
  }

  // Failed tasks
  if (data.failed_tasks && data.failed_tasks.length > 0) {
    html += '<div class="report-card"><h3 style="color:var(--red)">Failed Tasks (' + data.failed_tasks.length + ')</h3>';
    for (const t of data.failed_tasks) {
      html += '<div class="report-failure">' +
        '<div class="task-meta">' +
          '<span class="task-id" onclick="switchTab(\\'tasks\\'); setTimeout(()=>showDetail(\\'' + t.id + '\\'),100)">' + t.id.slice(0,12) + '</span>' +
          ' &mdash; ' + (t.repo || '—') +
          (t.pipeline_id ? ' &mdash; pipeline stage: ' + (t.stage_name || '?') : '') +
        '</div>' +
        (t.error ? '<div class="error-text">' + escHtml(t.error.slice(0, 300)) + '</div>' : '') +
      '</div>';
    }
    html += '</div>';
  }

  // No activity
  if (c.total === 0) {
    html += '<div class="empty-state"><p>No activity in the last ' + data.hours + ' hours</p></div>';
  }

  // Generated at
  html += '<div style="text-align:right;font-size:10px;color:var(--muted);margin-top:12px">Generated: ' + fmtTime(data.generated_at) + '</div>';

  container.innerHTML = html;
}

// --- Keyboard shortcuts ---
document.addEventListener('keydown', function(e) {
  // Escape: close overlays, then dispatch panel
  if (e.key === 'Escape') {
    const detailOpen = document.getElementById('detailPanel').classList.contains('open');
    const pipelineOpen = document.getElementById('pipelineDetailPanel').classList.contains('open');
    const kbDetailOpen = document.getElementById('kbDetailPanel').classList.contains('open');
    if (kbDetailOpen) {
      kbCloseDetail();
    } else if (detailOpen || pipelineOpen) {
      closeAllOverlays();
    } else if (document.getElementById('dispatchPanel').classList.contains('open')) {
      toggleDispatch();
    }
    return;
  }
  // Don't trigger shortcuts when typing in inputs
  const tag = document.activeElement.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
  // / or Ctrl+K: focus search box
  if (e.key === '/' || (e.key === 'k' && (e.metaKey || e.ctrlKey))) {
    e.preventDefault();
    const searchBox = document.getElementById('searchInput');
    if (searchBox) searchBox.focus();
    return;
  }
  // n: new task
  if (e.key === 'n') {
    if (!document.getElementById('dispatchPanel').classList.contains('open')) {
      toggleDispatch();
    }
    return;
  }
  // 1/2/3/4/5/6: switch tabs
  if (e.key === '1') { switchTab('board'); return; }
  if (e.key === '2') { switchTab('tasks'); return; }
  if (e.key === '3') { switchTab('pipelines'); return; }
  if (e.key === '4') { switchTab('schedules'); return; }
  if (e.key === '5') { switchTab('review'); return; }
  if (e.key === '6') { switchTab('report'); return; }
  // r: manual refresh
  if (e.key === 'r') {
    fetchTasks(); fetchPipelines(); fetchSchedules(); fetchHealth();
    if (activeTab === 'board') kbFetchTodos();
    if (activeTab === 'review') fetchReviewInbox();
    return;
  }
});

// ===== Kanban Board JS =====
const KB_COLUMNS = [
  { key: 'backlog', label: 'Backlog' },
  { key: 'todo', label: 'Todo' },
  { key: 'in_progress', label: 'In Progress' },
  { key: 'review', label: 'Review' },
  { key: 'done', label: 'Done' },
];

let kbTodos = [];
let kbCoverageByTodoId = {};
let kbCoverageSummary = null;
let kbCoverageLoadError = false;
let kbAutoRefreshTimer = null;
let kbAutoRefreshOn = false;
let kbDraggedCardId = null;
let kbDetailTodoId = null;
let kbInitialized = false;

function kbInit() {
  if (kbInitialized) return;
  kbInitialized = true;
  kbRenderBoard();
  kbFetchTodos();
}

function kbStopAutoRefresh() {
  if (kbAutoRefreshTimer) { clearInterval(kbAutoRefreshTimer); kbAutoRefreshTimer = null; }
}

// --- Kanban API ---
async function kbFetchTodos() {
  try {
    const r = await apiFetch(API + '/todos?limit=500');
    const data = await r.json();

    kbTodos = Array.isArray(data) ? data : (data.todos || []);

    await kbFetchCoverageBestEffort();

    kbApplyFilters();
  } catch (e) {
    showToast('Failed to load todos: ' + e.message, 'error');
  }
}

async function kbFetchCoverageBestEffort() {
  const COVERAGE_TIMEOUT_MS = 4000;
  const abortController = new AbortController();
  const timeoutId = setTimeout(function() {
    abortController.abort();
  }, COVERAGE_TIMEOUT_MS);
  try {
    const coverageResponse = await apiFetch(
      API + '/todos/coverage?recent_hours=72',
      { signal: abortController.signal }
    );
    const coverageData = await coverageResponse.json();
    kbCoverageByTodoId = {};
    (coverageData.coverages || []).forEach(function(c) { kbCoverageByTodoId[c.todo_id] = c; });
    kbCoverageSummary = coverageData.summary || null;
    kbCoverageLoadError = false;
    kbUpdateRefreshLabel();
  } catch (e) {
    if (e && e.name === 'AbortError') {
      e = new Error('coverage request timed out');
    }
    kbCoverageByTodoId = {};
    kbCoverageSummary = null;
    kbCoverageLoadError = true;
    kbUpdateRefreshLabel();
    console.warn('Coverage unavailable; rendering todos without coverage details', e);
  } finally {
    clearTimeout(timeoutId);
  }
}

function kbUpdateRefreshLabel() {
  var label = document.getElementById('kbRefreshLabel');
  if (!label) return;

  var parts = [];
  if (kbAutoRefreshOn) {
    parts.push('every 30s');
  }
  if (kbCoverageSummary) {
    parts.push(kbCoverageSummary.uncovered_todos + ' needs task');
  } else if (kbCoverageLoadError) {
    parts.push('coverage unavailable');
  }
  label.textContent = parts.join(' · ');
}

// --- Kanban Render ---
function kbRenderBoard() {
  var board = document.getElementById('kbBoard');
  board.innerHTML = KB_COLUMNS.map(function(col) {
    return '<div class="kb-column" data-status="' + col.key + '"' +
      ' ondragover="kbOnDragOver(event)" ondragleave="kbOnDragLeave(event)" ondrop="kbOnDrop(event)">' +
      '<div class="kb-col-header">' +
        '<div style="display:flex;align-items:center;gap:8px">' +
          '<span class="kb-col-title">' + col.label + '</span>' +
          '<span class="kb-col-count" id="kb-count-' + col.key + '">0</span>' +
        '</div>' +
        '<button class="kb-col-add-btn" onclick="kbOpenQuickAdd(\\'' + col.key + '\\')" title="Add card">+</button>' +
      '</div>' +
      '<div class="kb-quick-add" id="kb-qa-' + col.key + '">' +
        '<input type="text" id="kb-qa-title-' + col.key + '" placeholder="Card title..." ' +
          'onkeydown="if(event.key===\\'Enter\\')kbSubmitQuickAdd(\\'' + col.key + '\\')">' +
        '<div class="kb-quick-add-actions">' +
          '<select id="kb-qa-priority-' + col.key + '">' +
            '<option value="medium">Medium</option>' +
            '<option value="high">High</option>' +
            '<option value="low">Low</option>' +
          '</select>' +
          '<button class="kb-btn-add" onclick="kbSubmitQuickAdd(\\'' + col.key + '\\')">Add</button>' +
          '<button class="kb-btn-cancel-add" onclick="kbCloseQuickAdd(\\'' + col.key + '\\')">Cancel</button>' +
        '</div>' +
      '</div>' +
      '<div class="kb-col-cards" id="kb-cards-' + col.key + '"></div>' +
    '</div>';
  }).join('');
}

function kbRenderCards(filtered) {
  KB_COLUMNS.forEach(function(col) {
    var colTodos = (filtered || kbTodos).filter(function(t) { return t.status === col.key; });
    colTodos.sort(function(a, b) { return (a.column_order || 0) - (b.column_order || 0); });
    document.getElementById('kb-count-' + col.key).textContent = colTodos.length;
    var container = document.getElementById('kb-cards-' + col.key);
    container.innerHTML = colTodos.map(function(t) { return kbCardHtml(t); }).join('');
  });
}

function kbCardHtml(t) {
  var desc = (t.description || '');
  if (desc.length > 100) desc = desc.slice(0, 100) + '...';
  var tags = t.tags || [];
  var coverage = kbCoverageByTodoId[t.id] || null;
  var initiativeTag = '';
  if (coverage && coverage.initiative_tags && coverage.initiative_tags.length > 0) {
    initiativeTag = coverage.initiative_tags[0];
  }
  var repoHint = '';
  if (coverage && coverage.repo_hints && coverage.repo_hints.length > 0) {
    repoHint = coverage.repo_hints[0];
  }
  var priorityCls = 'kb-pill-' + (t.priority || 'medium');

  var html = '<div class="kb-card" draggable="true" data-id="' + t.id + '"' +
    ' ondragstart="kbOnDragStart(event)" ondragend="kbOnDragEnd(event)"' +
    ' onclick="kbOpenDetail(\\'' + t.id + '\\')">' +
    '<div class="kb-card-title">' + escHtml(t.title) + '</div>';
  if (desc) {
    html += '<div class="kb-card-desc">' + escHtml(desc) + '</div>';
  }
  if (initiativeTag || t.jira_key || repoHint || (coverage && coverage.needs_task)) {
    html += '<div class="kb-card-context">';
    if (initiativeTag) {
      html += '<span class="kb-context-chip">' + escHtml(initiativeTag) + '</span>';
    }
    if (t.jira_key) {
      html += '<span class="kb-context-chip">' + escHtml(t.jira_key) + '</span>';
    }
    if (repoHint) {
      html += '<span class="kb-context-chip">repo ' + escHtml(repoHint) + '</span>';
    }
    if (coverage && coverage.needs_task) {
      html += '<span class="kb-context-chip gap">Needs task</span>';
    }
    html += '</div>';
  }

  if (coverage) {
    html += '<div class="kb-coverage-row">';
    if (coverage.needs_task) {
      html += '<span class="kb-coverage-pill gap">no linked work</span>';
    } else {
      html += '<span class="kb-coverage-pill active">active ' + coverage.related_active_task_count + '</span>';
      html += '<span class="kb-coverage-pill recent">recent ' + coverage.related_recent_task_count + '</span>';
      html += '<span class="kb-coverage-pill pipeline">pipelines ' + coverage.related_pipeline_count + '</span>';
    }
    html += '</div>';
  }

  html += '<div class="kb-card-meta">' +
    '<span class="kb-pill ' + priorityCls + '">' + (t.priority || 'medium') + '</span>';

  tags.forEach(function(tag) {
    html += '<span class="kb-pill kb-pill-tag">' + escHtml(tag) + '</span>';
  });

  if (t.source && t.source !== 'manual') {
    html += '<span class="kb-pill kb-pill-source">' + escHtml(t.source) + '</span>';
  }
  if (t.jira_key && t.jira_url) {
    html += '<a class="kb-card-jira" href="' + escHtml(t.jira_url) + '" target="_blank" onclick="event.stopPropagation()">' + escHtml(t.jira_key) + '</a>';
  }
  html += '</div></div>';
  return html;
}

// --- Kanban Filters ---
function kbApplyFilters() {
  var search = (document.getElementById('kbSearchInput').value || '').toLowerCase();
  var priority = document.getElementById('kbPriorityFilter').value;
  var source = document.getElementById('kbSourceFilter').value;

  var filtered = kbTodos.filter(function(t) {
    if (priority && t.priority !== priority) return false;
    if (source && t.source !== source) return false;
    if (search) {
      var haystack = ((t.title || '') + ' ' + (t.description || '') + ' ' + (t.jira_key || '') + ' ' + ((t.tags || []).join(' '))).toLowerCase();
      if (haystack.indexOf(search) === -1) return false;
    }
    return true;
  });
  kbRenderCards(filtered);
}

// --- Kanban Drag and Drop ---
function kbOnDragStart(e) {
  kbDraggedCardId = e.target.getAttribute('data-id');
  e.target.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', kbDraggedCardId);
}

function kbOnDragEnd(e) {
  e.target.classList.remove('dragging');
  kbDraggedCardId = null;
  document.querySelectorAll('.kb-column').forEach(function(c) { c.classList.remove('drag-over'); });
}

function kbOnDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  var col = e.target.closest('.kb-column');
  if (col) col.classList.add('drag-over');
}

function kbOnDragLeave(e) {
  var col = e.target.closest('.kb-column');
  if (col && !col.contains(e.relatedTarget)) col.classList.remove('drag-over');
}

function kbOnDrop(e) {
  e.preventDefault();
  var col = e.target.closest('.kb-column');
  if (!col) return;
  col.classList.remove('drag-over');

  var todoId = e.dataTransfer.getData('text/plain');
  var newStatus = col.getAttribute('data-status');
  if (!todoId || !newStatus) return;

  var colTodos = kbTodos.filter(function(t) { return t.status === newStatus && t.id !== todoId; });
  var newOrder = colTodos.length;

  var todo = kbTodos.find(function(t) { return t.id === todoId; });
  if (!todo) return;
  var oldStatus = todo.status;
  var oldOrder = todo.column_order;
  todo.status = newStatus;
  todo.column_order = newOrder;
  kbApplyFilters();

  apiFetch(API + '/todos/' + todoId + '/reorder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status: newStatus, order: newOrder })
  }).then(function(r) { return r.json(); }).then(function(updated) {
    var idx = kbTodos.findIndex(function(t) { return t.id === todoId; });
    if (idx !== -1) kbTodos[idx] = updated;
    kbApplyFilters();
  }).catch(function(err) {
    showToast('Failed to move card: ' + err.message, 'error');
    todo.status = oldStatus;
    todo.column_order = oldOrder;
    kbApplyFilters();
  });
}

// --- Kanban Quick Add ---
function kbOpenQuickAdd(status) {
  document.getElementById('kb-qa-' + status).classList.add('open');
  var input = document.getElementById('kb-qa-title-' + status);
  input.value = '';
  input.focus();
}

function kbCloseQuickAdd(status) {
  document.getElementById('kb-qa-' + status).classList.remove('open');
}

function kbSubmitQuickAdd(status) {
  var title = document.getElementById('kb-qa-title-' + status).value.trim();
  if (!title) return;
  var priority = document.getElementById('kb-qa-priority-' + status).value;
  kbCloseQuickAdd(status);

  apiFetch(API + '/todos', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: title, status: status, priority: priority })
  }).then(function(r) { return r.json(); }).then(function(newTodo) {
    kbTodos.push(newTodo);
    kbApplyFilters();
    showToast('Card created', 'success');
  }).catch(function(err) {
    showToast('Failed to create card: ' + err.message, 'error');
  });
}

// --- Kanban Detail panel ---
function kbOpenDetail(id) {
  kbDetailTodoId = id;
  var t = kbTodos.find(function(x) { return x.id === id; });
  if (!t) return;

  var tagsStr = (t.tags || []).join(', ');
  var coverage = kbCoverageByTodoId[id] || null;

  var html = '<h2 style="font-size:16px;margin-bottom:20px">Edit Card</h2>' +
    '<div class="kb-detail-field">' +
      '<label>Title</label>' +
      '<input type="text" id="kb-det-title" value="' + escHtml(t.title) + '">' +
    '</div>' +
    '<div class="kb-detail-field">' +
      '<label>Description</label>' +
      '<textarea id="kb-det-desc">' + escHtml(t.description || '') + '</textarea>' +
    '</div>' +
    '<div style="display:flex;gap:12px">' +
      '<div class="kb-detail-field" style="flex:1">' +
        '<label>Status</label>' +
        '<select id="kb-det-status">' +
          KB_COLUMNS.map(function(c) { return '<option value="' + c.key + '"' + (t.status === c.key ? ' selected' : '') + '>' + c.label + '</option>'; }).join('') +
        '</select>' +
      '</div>' +
      '<div class="kb-detail-field" style="flex:1">' +
        '<label>Priority</label>' +
        '<select id="kb-det-priority">' +
          '<option value="high"' + (t.priority === 'high' ? ' selected' : '') + '>High</option>' +
          '<option value="medium"' + (t.priority === 'medium' ? ' selected' : '') + '>Medium</option>' +
          '<option value="low"' + (t.priority === 'low' ? ' selected' : '') + '>Low</option>' +
        '</select>' +
      '</div>' +
    '</div>' +
    '<div class="kb-detail-field">' +
      '<label>Tags (comma separated)</label>' +
      '<input type="text" id="kb-det-tags" value="' + escHtml(tagsStr) + '">' +
    '</div>';

  if (t.jira_key) {
    html += '<div class="kb-detail-field">' +
      '<label>Jira</label>' +
      '<div style="font-size:13px"><a href="' + escHtml(t.jira_url || '') + '" target="_blank" style="color:var(--accent)">' + escHtml(t.jira_key) + '</a>' +
        (t.jira_status ? ' <span class="kb-pill kb-pill-tag">' + escHtml(t.jira_status) + '</span>' : '') +
      '</div>' +
    '</div>';
  }

  html += kbCoverageDetailHtml(coverage);

  html += '<div class="kb-detail-actions">' +
    '<button class="kb-btn-save" onclick="kbSaveDetail()">Save</button>' +
    '<button class="kb-btn-delete" onclick="kbDeleteDetail()">Delete</button>' +
  '</div>';

  html += '<div class="kb-detail-meta-row">' +
    '<span>ID: ' + t.id + '</span>' +
    '<span>Source: ' + (t.source || 'manual') + '</span>' +
    '<span>Created: ' + fmtTime(t.created_at) + '</span>' +
  '</div>';

  document.getElementById('kbDetailContent').innerHTML = html;
  document.getElementById('kbDetailPanel').classList.add('open');
  document.getElementById('backdrop').classList.add('open');
}

function kbCloseDetail() {
  document.getElementById('kbDetailPanel').classList.remove('open');
  // Only remove backdrop if other panels are not open
  if (!document.getElementById('detailPanel').classList.contains('open') &&
      !document.getElementById('pipelineDetailPanel').classList.contains('open')) {
    document.getElementById('backdrop').classList.remove('open');
  }
  kbDetailTodoId = null;
}

function kbSaveDetail() {
  if (!kbDetailTodoId) return;

  var tagsRaw = document.getElementById('kb-det-tags').value.trim();
  var tags = tagsRaw ? tagsRaw.split(',').map(function(s) { return s.trim(); }).filter(Boolean) : [];

  var payload = {
    title: document.getElementById('kb-det-title').value.trim(),
    description: document.getElementById('kb-det-desc').value,
    status: document.getElementById('kb-det-status').value,
    priority: document.getElementById('kb-det-priority').value,
    tags: tags.length > 0 ? tags : []
  };

  if (!payload.title) { showToast('Title is required', 'error'); return; }

  apiFetch(API + '/todos/' + kbDetailTodoId, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }).then(function(r) { return r.json(); }).then(function(updated) {
    var idx = kbTodos.findIndex(function(t) { return t.id === kbDetailTodoId; });
    if (idx !== -1) kbTodos[idx] = updated;
    kbApplyFilters();
    kbFetchTodos();
    kbCloseDetail();
    showToast('Card updated', 'success');
  }).catch(function(err) {
    showToast('Failed to save: ' + err.message, 'error');
  });
}

function kbCoverageDetailHtml(coverage) {
  if (!coverage) {
    return '<div class="kb-detail-coverage"><h4>Work Coverage</h4><div style="font-size:11px;color:var(--muted)">Coverage unavailable.</div></div>';
  }

  var html = '<div class="kb-detail-coverage"><h4>Work Coverage</h4>' +
    '<div style="display:flex;gap:8px;flex-wrap:wrap">' +
      '<span class="kb-coverage-pill active">active ' + coverage.related_active_task_count + '</span>' +
      '<span class="kb-coverage-pill recent">recent ' + coverage.related_recent_task_count + '</span>' +
      '<span class="kb-coverage-pill pipeline">pipelines ' + coverage.related_pipeline_count + '</span>' +
      (coverage.needs_task ? '<span class="kb-coverage-pill gap">no linked work</span>' : '') +
    '</div>';

  if (coverage.active_tasks && coverage.active_tasks.length > 0) {
    html += '<div class="kb-detail-links">';
    coverage.active_tasks.forEach(function(taskRef) {
      html += '<div class="kb-detail-link">' +
        '<span class="task-id" onclick="event.stopPropagation(); switchTab(\'tasks\'); kbCloseDetail(); setTimeout(function(){ showDetail(\'' + taskRef.id + '\'); }, 50)">' + taskRef.id.slice(0, 12) + '</span>' +
        '<span class="badge badge-' + taskRef.status + '">' + taskRef.status + '</span>' +
        (taskRef.stage_name ? '<span style="color:var(--muted)">' + escHtml(taskRef.stage_name) + '</span>' : '') +
      '</div>';
    });
    html += '</div>';
  } else if (coverage.recent_tasks && coverage.recent_tasks.length > 0) {
    html += '<div class="kb-detail-links">';
    coverage.recent_tasks.slice(0, 5).forEach(function(taskRef) {
      html += '<div class="kb-detail-link">' +
        '<span class="task-id" onclick="event.stopPropagation(); switchTab(\'tasks\'); kbCloseDetail(); setTimeout(function(){ showDetail(\'' + taskRef.id + '\'); }, 50)">' + taskRef.id.slice(0, 12) + '</span>' +
        '<span class="badge badge-' + taskRef.status + '">' + taskRef.status + '</span>' +
      '</div>';
    });
    html += '</div>';
  }

  if (coverage.related_pipeline_ids && coverage.related_pipeline_ids.length > 0) {
    html += '<div class="kb-detail-links">';
    coverage.related_pipeline_ids.slice(0, 3).forEach(function(pid) {
      html += '<div class="kb-detail-link">' +
        '<span class="pipeline-id" onclick="event.stopPropagation(); switchTab(\'pipelines\'); kbCloseDetail(); setTimeout(function(){ showPipelineDetail(\'' + pid + '\'); }, 50)">' + pid.slice(0, 12) + '</span>' +
      '</div>';
    });
    html += '</div>';
  }

  html += '</div>';
  return html;
}

function kbDeleteDetail() {
  if (!kbDetailTodoId) return;
  if (!confirm('Delete this card? This cannot be undone.')) return;

  apiFetch(API + '/todos/' + kbDetailTodoId, { method: 'DELETE' })
  .then(function() {
    kbTodos = kbTodos.filter(function(t) { return t.id !== kbDetailTodoId; });
    kbApplyFilters();
    kbCloseDetail();
    showToast('Card deleted', 'success');
  }).catch(function(err) {
    showToast('Failed to delete: ' + err.message, 'error');
  });
}

// --- Kanban Auto-refresh ---
function kbToggleAutoRefresh() {
  kbAutoRefreshOn = !kbAutoRefreshOn;
  var btn = document.getElementById('kbAutoRefreshBtn');
  if (kbAutoRefreshOn) {
    btn.classList.add('active');
    kbAutoRefreshTimer = setInterval(kbFetchTodos, 30000);
  } else {
    btn.classList.remove('active');
    kbStopAutoRefresh();
  }
  kbUpdateRefreshLabel();
}

// --- Refresh ---
function updateInterval() {
  if (timer) { clearInterval(timer); timer = null; }
  // Also clear health check timer to prevent overlapping timers
  if (healthCheckTimer) { clearInterval(healthCheckTimer); healthCheckTimer = null; }
  const ms = parseInt(document.getElementById('refreshInterval').value);
  const ind = document.getElementById('refreshInd');
  if (ms > 0) {
    // Use the larger of the user-selected interval and backoff interval
    const effectiveMs = Math.max(ms, currentRefreshMs > BASE_REFRESH_MS ? currentRefreshMs : 0);
    timer = setInterval(() => { fetchTasks(); fetchPipelines(); fetchSchedules(); fetchHealth(); }, effectiveMs || ms);
    ind.textContent = '\\u25cf auto-refresh';
  } else {
    ind.textContent = '\\u25cb paused';
  }
  // Restart dedicated health check timer
  healthCheckTimer = setInterval(fetchHealth, currentHealthMs);
}

// --- Init ---
fetchHealth();
fetchTasks();
fetchPipelines();
fetchSchedules();
updateInterval();
// Initialize kanban board (default tab)
kbInit();
// Hide task-specific controls initially (board is default)
(function() {
  var sf = document.getElementById('statusFilterGroup');
  var sb = document.getElementById('searchGroup');
  if (sf) sf.style.display = 'none';
  if (sb) sb.style.display = 'none';
})();
// Health check timer is now managed by updateInterval() and restartHealthCheck()
</script>
</body>
</html>
"""
