"""Architecture and workflow diagrams page for workbench.

Serves a standalone HTML page using Mermaid.js to render interactive
diagrams of the system architecture, FSM states, pipeline flow, and more.
All CSS and JS are inlined; Mermaid is loaded from CDN.
"""

from __future__ import annotations

DIAGRAMS_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>workbench diagrams</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<style>
  :root {
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #c9d1d9; --muted: #9ea7b0; --accent: #58a6ff;
    --green: #3fb950; --red: #f85149; --yellow: #d29922;
    --orange: #db6d28; --purple: #bc8cff;
    --sidebar-w: 240px;
  }
  @media (prefers-color-scheme: light) {
    :root {
      --bg: #ffffff; --surface: #f6f8fa; --border: #d0d7de;
      --text: #1f2328; --muted: #636c76; --accent: #0969da;
      --green: #1a7f37; --red: #cf222e; --yellow: #9a6700;
      --orange: #bc4c00; --purple: #8250df;
    }
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'SF Mono', 'Cascadia Code', 'JetBrains Mono', monospace;
    background: var(--bg); color: var(--text);
    line-height: 1.5; font-size: 13px;
    display: flex; min-height: 100vh;
  }

  /* Sidebar */
  .sidebar {
    width: var(--sidebar-w); min-width: var(--sidebar-w);
    background: var(--surface); border-right: 1px solid var(--border);
    padding: 20px 0; position: fixed; top: 0; left: 0; height: 100vh;
    overflow-y: auto; z-index: 10;
  }
  .sidebar h2 {
    font-size: 14px; font-weight: 700; padding: 0 16px 12px;
    border-bottom: 1px solid var(--border); margin-bottom: 8px;
  }
  .sidebar h2 span { color: var(--accent); }
  .sidebar-nav { list-style: none; }
  .sidebar-nav li { margin: 0; }
  .sidebar-nav a {
    display: block; padding: 8px 16px; color: var(--muted);
    text-decoration: none; font-size: 12px; border-left: 3px solid transparent;
    transition: all 0.15s;
  }
  .sidebar-nav a:hover { color: var(--text); background: rgba(88,166,255,0.06); }
  .sidebar-nav a.active {
    color: var(--accent); border-left-color: var(--accent);
    background: rgba(88,166,255,0.08); font-weight: 600;
  }
  .sidebar-links {
    padding: 12px 16px; border-top: 1px solid var(--border);
    margin-top: 12px;
  }
  .sidebar-links a {
    display: block; padding: 6px 0; color: var(--muted);
    text-decoration: none; font-size: 11px;
  }
  .sidebar-links a:hover { color: var(--accent); }

  /* Main content */
  .main {
    margin-left: var(--sidebar-w); flex: 1; padding: 24px 32px;
    max-width: 1200px;
  }

  /* Diagram sections */
  .diagram-section {
    margin-bottom: 48px; scroll-margin-top: 24px;
  }
  .diagram-section h3 {
    font-size: 16px; font-weight: 700; margin-bottom: 8px;
    padding-bottom: 8px; border-bottom: 1px solid var(--border);
  }
  .diagram-section h3 .section-num {
    color: var(--accent); margin-right: 8px;
  }
  .diagram-section .desc {
    color: var(--muted); font-size: 12px; margin-bottom: 16px;
    max-width: 720px; line-height: 1.6;
  }
  .diagram-container {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 24px; overflow-x: auto;
  }
  .diagram-container .mermaid {
    display: flex; justify-content: center;
  }

  /* State count annotations */
  .state-counts {
    display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px;
  }
  .state-count-badge {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 3px 10px; border-radius: 12px; font-size: 11px;
    font-weight: 600; background: var(--surface); border: 1px solid var(--border);
  }
  .state-count-badge .count {
    background: var(--accent); color: #000; border-radius: 50%;
    min-width: 18px; height: 18px; display: inline-flex;
    align-items: center; justify-content: center; font-size: 10px;
  }

  /* Data stats bar */
  .data-stats {
    display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap;
  }
  .data-stat {
    padding: 10px 16px; background: var(--surface); border-radius: 8px;
    border: 1px solid var(--border); text-align: center;
  }
  .data-stat .num { font-size: 22px; font-weight: 700; }
  .data-stat .label {
    color: var(--muted); font-size: 10px; text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  /* Responsive */
  @media (max-width: 768px) {
    .sidebar { width: 100%; position: relative; height: auto; }
    .main { margin-left: 0; padding: 16px; }
    body { flex-direction: column; }
  }
</style>
</head>
<body>

<nav class="sidebar">
  <h2><span>workbench</span> diagrams</h2>
  <ul class="sidebar-nav" id="sidebarNav">
    <li><a href="#fsm" class="active" data-target="fsm">Task State Machine</a></li>
    <li><a href="#pipeline" data-target="pipeline">Pipeline Flow</a></li>
    <li><a href="#architecture" data-target="architecture">System Architecture</a></li>
    <li><a href="#worktree" data-target="worktree">Git Worktree Lifecycle</a></li>
    <li><a href="#dataflow" data-target="dataflow">Task Dataflow</a></li>
    <li><a href="#scheduler" data-target="scheduler">Scheduler Flow</a></li>
  </ul>
  <div class="sidebar-links">
    <a href="/dashboard">&larr; Dashboard</a>
    <a href="/health">Health Check</a>
  </div>
</nav>

<div class="main">

  <div class="data-stats" id="dataStats">
    <div class="data-stat"><div class="num" id="statTotalTasks">-</div><div class="label">Total Tasks</div></div>
    <div class="data-stat"><div class="num" id="statActiveTasks" style="color:var(--accent)">-</div><div class="label">Active</div></div>
    <div class="data-stat"><div class="num" id="statPipelines" style="color:var(--purple)">-</div><div class="label">Pipelines</div></div>
    <div class="data-stat"><div class="num" id="statWorktrees" style="color:var(--yellow)">-</div><div class="label">Worktrees</div></div>
  </div>

  <!-- 1. Task FSM State Diagram -->
  <div class="diagram-section" id="fsm">
    <h3><span class="section-num">01</span>Task State Machine (FSM)</h3>
    <p class="desc">
      Every task follows a finite state machine with strict transition rules.
      States are color-coded: <span style="color:var(--muted)">gray</span>=queued,
      <span style="color:var(--accent)">blue</span>=active (resolving/running/creating_pr),
      <span style="color:var(--green)">green</span>=completed,
      <span style="color:var(--red)">red</span>=failed/cancelled,
      <span style="color:var(--orange)">orange</span>=blocked/stuck.
      Guards enforce retry limits and require reasons for blocked transitions.
    </p>
    <div class="diagram-container">
      <div class="mermaid">
stateDiagram-v2
    [*] --> queued

    queued --> resolving
    queued --> cancelled
    queued --> failed : validation failure

    resolving --> running
    resolving --> failed
    resolving --> stuck
    resolving --> cancelled

    running --> creating_pr
    running --> completed : plan_only / research
    running --> failed
    running --> blocked : needs human input
    running --> stuck
    running --> cancelled

    creating_pr --> completed
    creating_pr --> failed
    creating_pr --> stuck
    creating_pr --> cancelled

    blocked --> running : unblocked by human
    blocked --> cancelled
    blocked --> failed : manually failed
    blocked --> stuck : watchdog timeout

    stuck --> queued : auto-retry (retries remaining)
    stuck --> failed : max retries exceeded
    stuck --> cancelled

    classDef green fill:#238636,stroke:#2ea043,color:#fff
    classDef red fill:#da3633,stroke:#f85149,color:#fff
    classDef blue fill:#1f6feb,stroke:#58a6ff,color:#fff
    classDef gray fill:#30363d,stroke:#484f58,color:#c9d1d9
    classDef orange fill:#9e6a03,stroke:#d29922,color:#fff

    class queued gray
    class resolving,running,creating_pr blue
    class completed green
    class failed,cancelled red
    class blocked,stuck orange
      </div>
    </div>
    <div class="state-counts" id="fsmCounts"></div>
  </div>

  <!-- 2. Pipeline Flow -->
  <div class="diagram-section" id="pipeline">
    <h3><span class="section-num">02</span>Pipeline Flow</h3>
    <p class="desc">
      Pipelines execute multiple stages sequentially. Stages with a
      <code>review_gate</code> trigger an automated review after implementation.
      If the review rejects, the pipeline loops back to the <code>loop_to</code>
      stage (default: the implement stage) for another iteration, up to
      <code>max_review_iterations</code> times. Approval advances to the next
      stage or completes the pipeline.
    </p>
    <div class="diagram-container">
      <div class="mermaid">
flowchart LR
    A["explore\n<i>research autonomy</i>"] --> B["plan\n<i>plan_only autonomy</i>"]
    B --> C["implement\n<i>full autonomy</i>"]
    C --> D{"review_gate?"}
    D -->|No gate| F["next stage /\ncomplete"]
    D -->|Has gate| E["review\n<i>research autonomy</i>"]
    E -->|APPROVE| F
    E -->|REJECT| G{"iterations <\nmax_review_iterations?"}
    G -->|Yes| H["loop back to\n<b>loop_to</b> stage"]
    H --> C
    G -->|No| I["pipeline\nFAILED"]

    style A fill:#1f6feb,stroke:#58a6ff,color:#fff
    style B fill:#8b5cf6,stroke:#bc8cff,color:#fff
    style C fill:#1f6feb,stroke:#58a6ff,color:#fff
    style D fill:#9e6a03,stroke:#d29922,color:#fff
    style E fill:#8b5cf6,stroke:#bc8cff,color:#fff
    style F fill:#238636,stroke:#2ea043,color:#fff
    style G fill:#9e6a03,stroke:#d29922,color:#fff
    style H fill:#30363d,stroke:#484f58,color:#c9d1d9
    style I fill:#da3633,stroke:#f85149,color:#fff
      </div>
    </div>
    <div class="state-counts" id="pipelineCounts"></div>
  </div>

  <!-- 3. System Architecture -->
  <div class="diagram-section" id="architecture">
    <h3><span class="section-num">03</span>System Architecture</h3>
    <p class="desc">
      Workbench is a FastAPI service organized into four layers: the API/UI
      layer handles HTTP routes and the dashboard; the execution layer runs
      tasks via OpenCode subprocesses in isolated git worktrees; the
      orchestration layer manages pipelines, scheduling, and review gating;
      and the data layer persists state to PostgreSQL.
    </p>
    <div class="diagram-container">
      <div class="mermaid">
flowchart TD
    subgraph API["API &amp; UI Layer"]
        main["main.py\n<i>FastAPI app, REST endpoints, CLI</i>"]
        dashboard["dashboard.py\n<i>HTML dashboard UI</i>"]
        diagrams_mod["diagrams.py\n<i>Architecture diagrams</i>"]
        models["models.py\n<i>Pydantic request/response</i>"]
    end

    subgraph EXEC["Execution Layer"]
        worker["worker.py\n<i>Worker pool, task loop</i>"]
        executor["executor.py\n<i>OpenCode subprocess runner</i>"]
        git_ops["git_ops.py\n<i>Worktree create/remove/prune</i>"]
        resolvers["resolvers.py\n<i>Jira, GitHub, prompt file</i>"]
        context["context.py\n<i>Context item resolution</i>"]
    end

    subgraph ORCH["Orchestration Layer"]
        pipeline["pipeline.py\n<i>Stage dispatch, review gating</i>"]
        scheduler["scheduler.py\n<i>Cron-based job dispatch</i>"]
        review["review.py\n<i>Review prompt building &amp; parsing</i>"]
        fsm_mod["fsm.py\n<i>State machine, transitions</i>"]
    end

    subgraph DATA["Data &amp; Config Layer"]
        database["database.py\n<i>SQLAlchemy models, async CRUD</i>"]
        config["config.py\n<i>Settings management</i>"]
        events["events.py\n<i>Event logging</i>"]
        exceptions["exceptions.py\n<i>Error types</i>"]
    end

    main --> worker
    main --> scheduler
    main --> database
    main --> models
    main --> config
    dashboard -.-> main
    diagrams_mod -.-> main

    worker --> executor
    worker --> git_ops
    worker --> resolvers
    worker --> context
    worker --> fsm_mod
    worker --> database
    worker --> config
    worker --> exceptions

    executor --> config
    executor --> exceptions
    git_ops --> config
    git_ops --> exceptions
    resolvers --> config
    resolvers --> exceptions
    context --> config
    context --> exceptions

    pipeline --> database
    pipeline --> config
    pipeline --> events
    pipeline --> review
    scheduler --> database
    scheduler --> events
    fsm_mod --> exceptions

    database --> config
    events --> config

    style API fill:#1f3a5f,stroke:#58a6ff,color:#c9d1d9
    style EXEC fill:#1a3a2a,stroke:#3fb950,color:#c9d1d9
    style ORCH fill:#3a2a1a,stroke:#d29922,color:#c9d1d9
    style DATA fill:#2a1a3a,stroke:#bc8cff,color:#c9d1d9
      </div>
    </div>
  </div>

  <!-- 4. Git Worktree Lifecycle -->
  <div class="diagram-section" id="worktree">
    <h3><span class="section-num">04</span>Git Worktree Lifecycle</h3>
    <p class="desc">
      Each task with full or local autonomy gets its own git worktree &mdash;
      an isolated checkout that shares the repository's .git database. This
      allows multiple agents to work on the same repo concurrently without
      conflicts. Worktrees are cleaned up after the task completes.
    </p>
    <div class="diagram-container">
      <div class="mermaid">
sequenceDiagram
    participant W as Worker
    participant G as GitOps
    participant FS as Filesystem
    participant OC as OpenCode
    participant P as Pipeline

    W->>W: Pick up task from queue
    W->>G: create_worktree(repo, branch)
    G->>FS: git worktree add path -b branch
    FS-->>G: Worktree created at /worktrees/id
    G-->>W: worktree_path

    W->>OC: run_opencode(prompt, workdir=worktree_path)
    Note over OC: Agent reads, edits, and commits in isolated worktree
    OC->>FS: git add + git commit
    OC-->>W: output + exit_code

    alt Full autonomy
        W->>G: push branch to remote
        W->>W: Create draft PR
    end

    W->>G: remove_worktree(path)
    G->>FS: git worktree remove path
    FS-->>G: Worktree removed

    alt Pipeline task
        W->>P: on_task_completed(task_id)
        P->>P: Evaluate next stage / review gate
        Note over P: May merge branch or dispatch next stage
    end
      </div>
    </div>
  </div>

  <!-- 5. Task Dataflow -->
  <div class="diagram-section" id="dataflow">
    <h3><span class="section-num">05</span>Task Dataflow</h3>
    <p class="desc">
      Data flows from an API request through validation, resolution, context
      building, prompt construction, and execution. Results are captured,
      persisted, and surface through SSE notifications to the dashboard.
    </p>
    <div class="diagram-container">
      <div class="mermaid">
flowchart TD
    A["API Request\nPOST /tasks"] --> B["TaskCreate Model\nPydantic validation"]
    B --> C["DB Insert\nTaskRow created, status=queued"]
    C --> D["Worker Pickup\nDequeued by worker pool"]
    D --> E["Resolver\nJira / GitHub / prompt file"]
    E --> F["Context Builder\ntext, file, task_output, reference"]
    F --> G["Prompt Builder\nAssemble final prompt + instructions"]
    G --> H["Executor\nopencode run subprocess"]
    H --> I["Output Capture\nstdout/stderr streaming"]
    I --> J["DB Update\nstatus, output, summary, branch"]
    J --> K{"Pipeline task?"}
    K -->|Yes| L["Pipeline Hook\nAdvance / loop / complete"]
    K -->|No| M["SSE Notification\nReal-time event stream"]
    L --> M
    M --> N["Dashboard\nLive task monitor"]

    style A fill:#1f6feb,stroke:#58a6ff,color:#fff
    style B fill:#1f6feb,stroke:#58a6ff,color:#fff
    style C fill:#6e40c9,stroke:#bc8cff,color:#fff
    style D fill:#1a3a2a,stroke:#3fb950,color:#c9d1d9
    style E fill:#9e6a03,stroke:#d29922,color:#fff
    style F fill:#9e6a03,stroke:#d29922,color:#fff
    style G fill:#9e6a03,stroke:#d29922,color:#fff
    style H fill:#238636,stroke:#2ea043,color:#fff
    style I fill:#238636,stroke:#2ea043,color:#fff
    style J fill:#6e40c9,stroke:#bc8cff,color:#fff
    style K fill:#30363d,stroke:#484f58,color:#c9d1d9
    style L fill:#9e6a03,stroke:#d29922,color:#fff
    style M fill:#1f6feb,stroke:#58a6ff,color:#fff
    style N fill:#1f6feb,stroke:#58a6ff,color:#fff
      </div>
    </div>
  </div>

  <!-- 6. Scheduler Flow -->
  <div class="diagram-section" id="scheduler">
    <h3><span class="section-num">06</span>Scheduler Flow</h3>
    <p class="desc">
      The scheduler runs a background loop that checks for due schedules every
      tick. When a schedule's <code>next_run_at</code> is in the past, it
      dispatches the configured task or pipeline and computes the next run time
      from the cron expression. Failed dispatches are recorded on the schedule
      row for visibility.
    </p>
    <div class="diagram-container">
      <div class="mermaid">
flowchart TD
    A["Scheduler Tick\nBackground async loop"] --> B{"Any schedules due?"}
    B -->|No| A
    B -->|Yes| C["Load due schedules\nnext_run_at < now AND enabled"]
    C --> D{"schedule_type?"}
    D -->|task| E["Dispatch Task\nPOST /tasks equivalent"]
    D -->|pipeline| F["Dispatch Pipeline\nPOST /pipelines equivalent"]
    E --> G["Update Schedule Row"]
    F --> G
    G --> H["Set last_run_at = now"]
    H --> I["Compute next_run_at\nfrom cron expression + timezone"]
    I --> J["Increment run_count"]
    J --> K["Record last_task_id /\nlast_pipeline_id"]
    K --> A

    E -.->|Error| L["Record error\non schedule row"]
    F -.->|Error| L
    L --> I

    style A fill:#1f6feb,stroke:#58a6ff,color:#fff
    style B fill:#30363d,stroke:#484f58,color:#c9d1d9
    style C fill:#1f6feb,stroke:#58a6ff,color:#fff
    style D fill:#9e6a03,stroke:#d29922,color:#fff
    style E fill:#238636,stroke:#2ea043,color:#fff
    style F fill:#238636,stroke:#2ea043,color:#fff
    style G fill:#6e40c9,stroke:#bc8cff,color:#fff
    style H fill:#6e40c9,stroke:#bc8cff,color:#fff
    style I fill:#6e40c9,stroke:#bc8cff,color:#fff
    style J fill:#6e40c9,stroke:#bc8cff,color:#fff
    style K fill:#6e40c9,stroke:#bc8cff,color:#fff
    style L fill:#da3633,stroke:#f85149,color:#fff
      </div>
    </div>
  </div>

</div><!-- .main -->

<script>
const API = window.location.origin;
const isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;

// Initialize Mermaid
mermaid.initialize({
  startOnLoad: true,
  theme: isDark ? 'dark' : 'default',
  securityLevel: 'loose',
  flowchart: { useMaxWidth: true, htmlLabels: true, curve: 'basis' },
  stateDiagram: { useMaxWidth: true },
  sequence: { useMaxWidth: true, showSequenceNumbers: false, actorMargin: 80 },
});

// --- Sidebar scroll spy & click ---
const navLinks = document.querySelectorAll('.sidebar-nav a');
const sections = document.querySelectorAll('.diagram-section');

function updateActiveNav() {
  let current = '';
  sections.forEach(s => {
    const rect = s.getBoundingClientRect();
    if (rect.top <= 100) current = s.id;
  });
  navLinks.forEach(a => {
    a.classList.toggle('active', a.getAttribute('data-target') === current);
  });
}

window.addEventListener('scroll', updateActiveNav);

navLinks.forEach(a => {
  a.addEventListener('click', function(e) {
    e.preventDefault();
    const target = document.getElementById(this.getAttribute('data-target'));
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
});

// --- Fetch live data ---
async function fetchDiagramData() {
  try {
    const r = await fetch(API + '/diagrams/data');
    if (!r.ok) return;
    const d = await r.json();

    // Update stats bar
    const taskTotal = Object.values(d.task_state_counts || {}).reduce((a, b) => a + b, 0);
    const active = (d.task_state_counts.running || 0) +
                   (d.task_state_counts.resolving || 0) +
                   (d.task_state_counts.creating_pr || 0);
    document.getElementById('statTotalTasks').textContent = taskTotal;
    document.getElementById('statActiveTasks').textContent = active;

    const pipeTotal = Object.values(d.pipeline_status_counts || {}).reduce((a, b) => a + b, 0);
    document.getElementById('statPipelines').textContent = pipeTotal;
    document.getElementById('statWorktrees').textContent = d.active_worktrees || 0;

    // FSM state count badges
    const fsmEl = document.getElementById('fsmCounts');
    const stateColors = {
      queued: 'var(--muted)', resolving: 'var(--purple)', running: 'var(--accent)',
      creating_pr: 'var(--yellow)', completed: 'var(--green)', failed: 'var(--red)',
      blocked: 'var(--orange)', stuck: 'var(--orange)', cancelled: 'var(--muted)',
    };
    let fsmHtml = '';
    for (const [state, count] of Object.entries(d.task_state_counts || {})) {
      if (count > 0) {
        const color = stateColors[state] || 'var(--muted)';
        fsmHtml += '<span class="state-count-badge" style="color:' + color + '">' +
          state + ' <span class="count" style="background:' + color + '">' + count + '</span></span>';
      }
    }
    fsmEl.innerHTML = fsmHtml;

    // Pipeline count badges
    const pipeEl = document.getElementById('pipelineCounts');
    let pipeHtml = '';
    for (const [status, count] of Object.entries(d.pipeline_status_counts || {})) {
      if (count > 0) {
        const color = stateColors[status] || 'var(--muted)';
        pipeHtml += '<span class="state-count-badge" style="color:' + color + '">' +
          status + ' <span class="count" style="background:' + color + '">' + count + '</span></span>';
      }
    }
    pipeEl.innerHTML = pipeHtml;
  } catch(e) {
    console.warn('Failed to fetch diagram data:', e);
  }
}

fetchDiagramData();
// Refresh every 30 seconds
setInterval(fetchDiagramData, 30000);
</script>
</body>
</html>
"""
