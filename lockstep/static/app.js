/**
 * Lockstep Dashboard — app.js
 * Vanilla JS + d3.js (v7), no build step.
 *
 * Responsibilities:
 *  - Load run data from GET /runs/{run_id}, /waves, /conflicts
 *  - Render task list in left pane
 *  - Render d3 force-directed graph in centre pane
 *  - Populate detail panel on node click
 *  - Open WebSocket /ws/{run_id} and handle real-time stage events
 *  - Submit plan JSON via POST /runs
 */

"use strict";

// ── State ──────────────────────────────────────────────────────────────────

let currentRunId = null;
let ws = null;
let graphData = { nodes: [], links: [] };
let simulation = null;

// ── Colour helpers ─────────────────────────────────────────────────────────

const C = {
  bg:     "#0B0E14",
  panel:  "#141822",
  border: "#242B3A",
  text:   "#E8EAED",
  muted:  "#8B93A7",
  safe:   "#2BB3A3",
  medium: "#F5A623",
  high:   "#D64545",
};

function severityColor(score) {
  if (score == null) return C.safe;
  if (score >= 0.7)  return C.high;
  if (score >= 0.4)  return C.medium;
  return C.safe;
}

function severityClass(score) {
  if (score == null) return "safe";
  if (score >= 0.7)  return "high";
  if (score >= 0.4)  return "medium";
  return "safe";
}

// ── DOM helpers ────────────────────────────────────────────────────────────

const $ = id => document.getElementById(id);

function setWsStatus(connected) {
  const el = $("ws-status");
  el.textContent = connected ? "● connected" : "● disconnected";
  el.className = "topbar-status" + (connected ? " connected" : "");
}

function appendEvent(msg, cls = "") {
  const log = $("event-log") || createEventLog();
  const entry = document.createElement("div");
  entry.className = "event-entry" + (cls ? " " + cls : "");
  entry.textContent = msg;
  log.prepend(entry);
}

function createEventLog() {
  const detail = $("detail-content");
  const log = document.createElement("div");
  log.id = "event-log";
  log.className = "event-log";
  const label = document.createElement("div");
  label.className = "detail-label";
  label.textContent = "PIPELINE EVENTS";
  detail.appendChild(label);
  detail.appendChild(log);
  return log;
}

// ── Task list ──────────────────────────────────────────────────────────────

function renderTaskList(waves, conflicts) {
  const container = $("task-list");
  container.innerHTML = "";

  // Build severity map: task_id → max severity across its conflicts
  const severityMap = {};
  (conflicts || []).forEach(c => {
    [c.task_a, c.task_b].forEach(tid => {
      severityMap[tid] = Math.max(severityMap[tid] || 0, c.severity_score || 0);
    });
  });

  (waves || []).forEach((wave, waveIdx) => {
    wave.forEach(task => {
      const tid = task.task_id;
      const sev = severityMap[tid];
      const item = document.createElement("div");
      item.className = "task-item";
      item.dataset.taskId = tid;

      const waveBadge = `<span class="badge badge-wave">W${waveIdx}</span>`;
      const sevBadge = sev != null
        ? `<span class="badge badge-${severityClass(sev)}">${severityClass(sev).toUpperCase()}</span>`
        : "";

      const displayId = tid.includes("::") ? tid.split("::")[1] : tid;
      item.innerHTML = `
        <div class="task-id">${waveBadge}${sevBadge}${escHtml(displayId)}</div>
        <div class="task-desc">${escHtml(task.description || "")}</div>
      `;
      item.addEventListener("click", () => showTaskDetail(task, waveIdx, sev, conflicts));
      container.appendChild(item);
    });
  });
}

// ── Detail panel ───────────────────────────────────────────────────────────

function showTaskDetail(task, waveIdx, severity, conflicts) {
  // Deselect all, select this
  document.querySelectorAll(".task-item").forEach(el => el.classList.remove("selected"));
  const el = document.querySelector(`[data-task-id="${task.task_id}"]`);
  if (el) el.classList.add("selected");

  const content = $("detail-content");
  const displayId = task.task_id.includes("::") ? task.task_id.split("::")[1] : task.task_id;
  const myConflicts = (conflicts || []).filter(c =>
    c.task_a === task.task_id || c.task_b === task.task_id
  );

  let html = `
    <div class="detail-section">
      <div class="detail-label">TASK ID</div>
      <div class="detail-value">${escHtml(displayId)}</div>
    </div>
    <div class="detail-section">
      <div class="detail-label">WAVE</div>
      <div class="detail-value">${waveIdx}</div>
    </div>
    <div class="detail-section">
      <div class="detail-label">DESCRIPTION</div>
      <div class="detail-value">${escHtml(task.description || "")}</div>
    </div>
    <div class="detail-section">
      <div class="detail-label">SEVERITY</div>
      <div class="detail-value ${severityClass(severity)}">${severity != null ? severity.toFixed(4) : "none"}</div>
    </div>
  `;

  if (myConflicts.length) {
    html += `<div class="detail-section"><div class="detail-label">CONFLICTS (${myConflicts.length})</div>`;
    myConflicts.forEach(c => {
      const peer = c.task_a === task.task_id ? c.task_b : c.task_a;
      const peerDisplay = peer.includes("::") ? peer.split("::")[1] : peer;
      html += `
        <div style="margin-bottom:8px; padding:8px; border:1px solid var(--border);">
          <div class="detail-value ${severityClass(c.severity_score)}">${c.symbol}</div>
          <div style="color:var(--muted);font-size:11px;font-family:var(--font-code)">
            vs ${escHtml(peerDisplay)} · ${c.resolution} · score ${(c.severity_score||0).toFixed(3)}
          </div>
        </div>`;
    });
    html += `</div>`;
  }

  content.innerHTML = html;
}

// ── D3 force graph ─────────────────────────────────────────────────────────

function renderGraph(waves, conflicts) {
  const svg = d3.select("#graph-svg");
  svg.selectAll("*").remove();

  const rect = document.getElementById("graph-svg").getBoundingClientRect();
  const W = rect.width || 600;
  const H = rect.height || 500;

  // Build nodes: task nodes only (symbols kept implicit via edges)
  const nodeMap = {};
  const nodes = [];
  const links = [];

  (waves || []).forEach((wave, wi) => {
    wave.forEach(task => {
      const id = task.task_id;
      if (!nodeMap[id]) {
        const display = id.includes("::") ? id.split("::")[1] : id;
        const node = { id, display, wave: wi, kind: "task" };
        nodeMap[id] = node;
        nodes.push(node);
      }
    });
  });

  // Conflict edges between task nodes
  (conflicts || []).forEach(c => {
    if (nodeMap[c.task_a] && nodeMap[c.task_b]) {
      links.push({
        source: c.task_a,
        target: c.task_b,
        severity: c.severity_score,
        symbol: c.symbol,
      });
    }
  });

  graphData = { nodes, links };

  const g = svg.append("g");

  // Zoom
  svg.call(d3.zoom().scaleExtent([0.3, 3]).on("zoom", e => g.attr("transform", e.transform)));

  // Arrow markers
  svg.append("defs").append("marker")
    .attr("id", "arrow")
    .attr("viewBox", "0 -4 8 8")
    .attr("refX", 18).attr("refY", 0)
    .attr("markerWidth", 6).attr("markerHeight", 6)
    .attr("orient", "auto")
    .append("path")
    .attr("d", "M0,-4L8,0L0,4")
    .attr("fill", C.muted);

  // Links
  const link = g.append("g").selectAll("line")
    .data(links).join("line")
    .attr("class", "link")
    .attr("stroke", d => severityColor(d.severity))
    .attr("stroke-width", d => 1 + (d.severity || 0) * 2)
    .attr("marker-end", "url(#arrow)");

  // Nodes
  const node = g.append("g").selectAll("g")
    .data(nodes).join("g")
    .attr("class", "node")
    .call(d3.drag()
      .on("start", (e, d) => { if (!e.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
      .on("drag",  (e, d) => { d.fx = e.x; d.fy = e.y; })
      .on("end",   (e, d) => { if (!e.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; })
    )
    .on("click", (e, d) => {
      // Find matching task in waves for detail
      const allTasks = (waves || []).flat();
      const task = allTasks.find(t => t.task_id === d.id);
      if (task) {
        const waveIdx = (waves || []).findIndex(w => w.some(t => t.task_id === d.id));
        const taskConflicts = (conflicts || []).filter(c => c.task_a === d.id || c.task_b === d.id);
        const sev = taskConflicts.length ? Math.max(...taskConflicts.map(c => c.severity_score)) : null;
        showTaskDetail(task, waveIdx, sev, conflicts);
      }
    });

  node.append("circle")
    .attr("r", 16)
    .attr("fill", d => {
      const taskConflicts = (conflicts || []).filter(c => c.task_a === d.id || c.task_b === d.id);
      if (!taskConflicts.length) return C.safe;
      const maxSev = Math.max(...taskConflicts.map(c => c.severity_score || 0));
      return severityColor(maxSev);
    })
    .attr("stroke", C.border)
    .attr("fill-opacity", 0.25)
    .attr("stroke-width", 2);

  node.append("text")
    .attr("text-anchor", "middle")
    .attr("dy", "0.35em")
    .attr("y", 26)
    .text(d => {
      const short = d.display.length > 16 ? d.display.slice(0, 14) + "…" : d.display;
      return short;
    });

  node.append("text")
    .attr("text-anchor", "middle")
    .attr("dy", "0.35em")
    .attr("font-size", "10px")
    .attr("fill", d => {
      const taskConflicts = (conflicts || []).filter(c => c.task_a === d.id || c.task_b === d.id);
      if (!taskConflicts.length) return C.safe;
      const maxSev = Math.max(...taskConflicts.map(c => c.severity_score || 0));
      return severityColor(maxSev);
    })
    .text(d => `W${d.wave}`);

  // Simulation
  simulation = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(links).id(d => d.id).distance(120))
    .force("charge", d3.forceManyBody().strength(-300))
    .force("center", d3.forceCenter(W / 2, H / 2))
    .force("x", d3.forceX(d => (d.wave + 1) * (W / ((waves ? waves.length : 1) + 1))).strength(0.4))
    .on("tick", () => {
      link.attr("x1", d => d.source.x).attr("y1", d => d.source.y)
          .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
      node.attr("transform", d => `translate(${d.x},${d.y})`);
    });
}

// ── WebSocket ──────────────────────────────────────────────────────────────

function connectWs(runId) {
  if (ws) ws.close();
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/${runId}`);

  ws.onopen = () => {
    setWsStatus(true);
    appendEvent("WebSocket connected", "done");
  };

  ws.onmessage = e => {
    let data;
    try { data = JSON.parse(e.data); } catch { return; }
    const event = data.event || "";
    appendEvent(event + (data.conflict_count != null ? ` (${data.conflict_count} conflicts)` : ""), "done");

    // Reload run data on key events
    if (["detector_done", "scheduler_done", "report_done"].includes(event)) {
      loadRun(runId);
    }
  };

  ws.onclose = () => setWsStatus(false);
  ws.onerror = () => setWsStatus(false);
}

// ── Data loading ───────────────────────────────────────────────────────────

async function loadRun(runId) {
  currentRunId = runId;
  try {
    const [wavesResp, conflictsResp] = await Promise.all([
      fetch(`/runs/${runId}/waves`),
      fetch(`/runs/${runId}/conflicts`),
    ]);
    if (!wavesResp.ok || !conflictsResp.ok) {
      $("task-list").innerHTML = `<div class="empty-state">Run not found.</div>`;
      return;
    }
    const wavesData    = await wavesResp.json();
    const conflictsData = await conflictsResp.json();

    renderTaskList(wavesData.waves, conflictsData.conflicts);
    renderGraph(wavesData.waves, conflictsData.conflicts);
    connectWs(runId);
  } catch (err) {
    $("task-list").innerHTML = `<div class="empty-state">Error: ${escHtml(String(err))}</div>`;
  }
}

// ── Event handlers ─────────────────────────────────────────────────────────

$("load-btn").addEventListener("click", () => {
  const runId = $("run-id-input").value.trim();
  if (runId) loadRun(runId);
});

$("run-id-input").addEventListener("keydown", e => {
  if (e.key === "Enter") $("load-btn").click();
});

$("post-btn").addEventListener("click", () => $("plan-file").click());

$("plan-file").addEventListener("change", async e => {
  const file = e.target.files[0];
  if (!file) return;
  const text = await file.text();
  let plan;
  try { plan = JSON.parse(text); } catch { alert("Invalid JSON"); return; }
  try {
    const resp = await fetch("/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(plan),
    });
    const body = await resp.json();
    if (!resp.ok) { alert("Error: " + JSON.stringify(body)); return; }
    $("run-id-input").value = body.run_id;
    loadRun(body.run_id);
  } catch (err) {
    alert("Submit failed: " + err);
  }
  e.target.value = "";
});

// ── Utility ────────────────────────────────────────────────────────────────

function escHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── Init ───────────────────────────────────────────────────────────────────

// Auto-load run_id from URL query ?run=xxx
const urlParams = new URLSearchParams(location.search);
const urlRun = urlParams.get("run");
if (urlRun) {
  $("run-id-input").value = urlRun;
  loadRun(urlRun);
}
