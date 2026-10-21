"use strict";

const state = {
  selected: null,        // task id
  openSteps: new Set(),  // expanded step ids
  activeTab: new Map(),  // step id -> tab key ("log" or file path)
  stepEls: new Map(),    // step id -> DOM element (kept across refreshes)
  renderedTask: null,    // task id currently rendered in the detail pane
};

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c != null) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
};

// replaceChildren() would render null as the text "null".
const setKids = (node, ...children) => node.replaceChildren(...children.flat().filter(c => c != null));

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok && res.status !== 409) throw new Error(`${res.status} ${path}`);
  return res.json();
}

function parseTs(ts) { return ts ? new Date(ts) : null; }
function fmtDuration(start, end) {
  const s = parseTs(start), e = parseTs(end) || new Date();
  if (!s) return "";
  let sec = Math.max(0, Math.round((e - s) / 1000));
  const m = Math.floor(sec / 60); sec %= 60;
  return m ? `${m}m ${sec}s` : `${sec}s`;
}
function fmtWhen(ts) {
  const d = parseTs(ts);
  return d ? d.toLocaleString() : "";
}

/* ---------------- task list ---------------- */

async function refreshTasks() {
  const { tasks } = await api("/api/tasks");
  const ul = $("#tasks");
  ul.replaceChildren(...tasks.map(t => el("li", {
      class: t.id === state.selected ? "selected" : "",
      onclick: () => selectTask(t.id),
    },
    el("span", { class: "t-title", title: t.title }, `#${t.id} ${t.title}`),
    el("span", { class: `pill ${t.status}` }, t.status),
    el("span", { class: "t-sub" },
      `${t.filename} · attempts ${t.attempts_used}/${t.max_attempts}` +
      (t.status === "running" ? ` · ${fmtDuration(t.started_at)}` : "")),
  )));
  $("#no-tasks").hidden = tasks.length > 0;
  if (state.selected == null && tasks.length) {
    const fromHash = Number(location.hash.replace("#task-", ""));
    selectTask(tasks.some(t => t.id === fromHash) ? fromHash : tasks[0].id);
  }
}

function selectTask(id) {
  state.selected = id;
  location.hash = `task-${id}`;
  refreshTasks();
  refreshDetail();
}

/* ---------------- task detail ---------------- */

async function refreshDetail() {
  if (state.selected == null) return;
  const { task, steps } = await api(`/api/tasks/${state.selected}`);
  const detail = $("#detail");

  if (state.renderedTask !== task.id) {
    state.renderedTask = task.id;
    state.stepEls.clear();
    detail.replaceChildren(
      el("div", { class: "task-head" },
        el("div", {}, el("h1", { id: "task-title" }), el("div", { class: "meta", id: "task-meta" })),
        el("div", { class: "task-actions", id: "task-actions" })),
      el("div", { id: "task-summary" }),
      el("details", { class: "task-text" }, el("summary", {}, "Task description"), el("pre", { id: "task-text" })),
      el("div", { id: "phases" }),
    );
  }

  setKids($("#task-title"), `#${task.id} ${task.title} `, el("span", { class: `pill ${task.status}` }, task.status));
  setKids($("#task-meta"), 
    el("span", {}, "File ", el("b", {}, task.filename)),
    el("span", {}, "Branch ", el("b", {}, task.branch || "—")),
    el("span", {}, "Base ", el("b", {}, task.base_sha ? `${task.base_ref} @ ${task.base_sha.slice(0, 10)}` : (task.base_ref || "—"))),
    el("span", {}, "Attempts ", el("b", {}, `${task.attempts_used} / ${task.max_attempts}`)),
    el("span", {}, "Created ", el("b", {}, fmtWhen(task.created_at))),
    task.started_at ? el("span", {}, "Duration ", el("b", {}, fmtDuration(task.started_at, task.finished_at))) : null,
  );
  $("#task-text").textContent = task.content;

  const actions = [];
  if (["queued", "running"].includes(task.status)) {
    actions.push(el("button", { class: "btn danger", onclick: () => taskAction(task.id, "cancel") }, "Cancel"));
  } else {
    actions.push(el("button", { class: "btn", onclick: () => taskAction(task.id, "requeue") }, "Requeue"));
  }
  setKids($("#task-actions"), ...actions);

  const summaryText = task.error || task.summary;
  setKids($("#task-summary"), 
    summaryText ? el("div", { class: `summary-box ${task.status}` }, summaryText) : "");

  renderPhases(steps, task);
}

async function taskAction(id, action) {
  await api(`/api/tasks/${id}/${action}`, { method: "POST" });
  refreshTasks();
  refreshDetail();
}

function renderPhases(steps, task) {
  // Group steps: setup (attempt 0, before any attempt), attempt N, finalize.
  const groups = [];
  const byKey = new Map();
  for (const s of steps) {
    const key = s.kind === "finalize" ? `finalize-${s.id}` : s.attempt === 0 ? `setup-${s.id}` : `attempt-${s.attempt}`;
    if (!byKey.has(key)) {
      const g = { key, attempt: s.attempt, kind: s.kind, steps: [] };
      byKey.set(key, g);
      groups.push(g);
    }
    byKey.get(key).steps.push(s);
  }

  const phasesEl = $("#phases");
  const verdicts = steps.map(s => `${s.id}:${s.verdict || ""}:${s.status === "running"}`).join(",");
  const signature = `${task.status}|${verdicts}`;
  if (phasesEl.dataset.signature === signature) {
    // Same structure: update steps in place so scroll positions and open panels survive.
    steps.forEach(s => stepElement(s, state.stepEls.get(s.id)?._index));
    return;
  }
  phasesEl.dataset.signature = signature;
  const children = groups.map((g, gi) => {
    let title, badge = null;
    if (g.attempt === 0) {
      title = g.kind === "finalize" ? "Finalize" : "Setup";
    } else {
      const n = groups.slice(0, gi + 1).filter(x => x.attempt !== 0).length;
      title = `Attempt ${n}`;
      const review = g.steps.find(s => s.kind === "review");
      if (review && review.verdict) badge = el("span", { class: `pill ${review.verdict}` }, `verdict: ${review.verdict}`);
      else if (g.steps.some(s => s.status === "running")) badge = el("span", { class: "pill running" }, "in progress");
    }
    const phase = el("div", { class: "phase" },
      el("div", { class: "phase-head" }, el("span", {}, title), badge));
    g.steps.forEach((s, i) => phase.append(stepElement(s, i + 1)));
    return phase;
  });
  if (!steps.length) {
    children.push(el("p", { class: "muted" }, task.status === "queued" ? "Waiting in queue…" : "No steps yet."));
  }
  phasesEl.replaceChildren(...children);
}

function stepElement(step, index) {
  let node = state.stepEls.get(step.id);
  if (!node) {
    node = el("div", { class: "step" },
      el("div", { class: "step-row", onclick: () => toggleStep(step.id) },
        el("span", { class: "step-idx" }),
        el("span", { class: "step-title" }),
        el("span", { class: "step-stats" }),
        el("div", { class: "step-summary" })),
      el("div", { class: "step-body" }, el("div", { class: "tabs" }), el("div", { class: "tab-content" })));
    state.stepEls.set(step.id, node);
  }
  const wasRunning = node.dataset.status === "running";
  node.dataset.status = step.status;
  node._step = step;
  node._index = index;
  node.classList.toggle("open", state.openSteps.has(step.id));

  const idx = $(".step-idx", node);
  idx.className = `step-idx ${step.status}`;
  idx.textContent = step.status === "succeeded" ? "✓" : step.status === "failed" ? "✕" : step.status === "skipped" ? "–" : index;

  setKids($(".step-title", node), step.title, step.agent ? el("span", { class: "muted" }, `agent: ${step.agent}`) : null);
  setKids($(".step-stats", node), 
    step.verdict ? el("span", { class: `pill ${step.verdict}` }, step.verdict) : null,
    el("span", { class: `pill ${step.status}` }, step.status),
    el("span", {}, fmtDuration(step.started_at, step.finished_at)),
    step.cost_usd != null ? el("span", {}, `$${step.cost_usd.toFixed(3)}`) : null,
  );
  $(".step-summary", node).textContent = step.summary || "";

  if (state.openSteps.has(step.id)) {
    renderTabs(node, step);
    // Keep refreshing the live log while the step runs, and once more when it finishes.
    if (step.status === "running" || wasRunning) loadTab(node, step);
  }
  return node;
}

function toggleStep(id) {
  const node = state.stepEls.get(id);
  if (state.openSteps.has(id)) {
    state.openSteps.delete(id);
    node.classList.remove("open");
  } else {
    state.openSteps.add(id);
    node.classList.add("open");
    renderTabs(node, node._step);
    loadTab(node, node._step);
  }
}

function tabKeys(step) {
  // Output first, then produced files (artifact first), then prompts.
  const order = f => /artifact\.md$|review\.json$/.test(f) ? 0 : /prompt\.md$/.test(f) ? 2 : 1;
  const files = [...step.files].sort((a, b) => order(a) - order(b));
  return [{ key: "log", label: "Output" }, ...files.map(f => ({ key: f, label: f.split("/").pop() }))];
}

function renderTabs(node, step) {
  const active = state.activeTab.get(step.id) || "log";
  setKids($(".tabs", node), ...tabKeys(step).map(t => el("button", {
    class: `tab ${t.key === active ? "active" : ""}`,
    onclick: ev => {
      ev.stopPropagation();
      state.activeTab.set(step.id, t.key);
      renderTabs(node, node._step);
      loadTab(node, node._step);
    },
  }, t.label)));
}

async function loadTab(node, step) {
  const key = state.activeTab.get(step.id) || "log";
  const content = $(".tab-content", node);
  const wasAtBottom = content.scrollHeight - content.scrollTop - content.clientHeight < 40;
  let rendered;
  if (key === "log") {
    const data = await api(`/api/steps/${step.id}/log`);
    rendered = data.format === "events" ? renderEvents(data.events, step) : el("pre", {}, data.text || "(empty)");
  } else {
    const data = await api(`/api/steps/${step.id}/file?path=${encodeURIComponent(key)}`);
    rendered = renderFile(data);
  }
  if ((state.activeTab.get(step.id) || "log") !== key) return; // tab switched meanwhile
  content.replaceChildren(rendered);
  if (step.status === "running" && wasAtBottom) content.scrollTop = content.scrollHeight;
}

function renderEvents(events, step) {
  if (!events.length) return el("p", { class: "muted" }, step.status === "running" ? "Waiting for agent output…" : "(no output)");
  const toolNames = new Map();
  return el("div", {}, events.map(ev => {
    switch (ev.type) {
      case "system": return el("div", { class: "ev ev-system" }, ev.text);
      case "text": return el("div", { class: "ev ev-text" }, ev.text);
      case "tool_use":
        toolNames.set(ev.id, ev.name);
        return el("div", { class: "ev" }, el("details", {},
          el("summary", {}, `${ev.name} ${toolPreview(ev)}`), el("pre", {}, ev.input)));
      case "tool_result":
        return el("div", { class: `ev tool-result ${ev.is_error ? "error" : ""}` }, el("details", {},
          el("summary", {}, `${ev.is_error ? "✕" : "↳"} ${toolNames.get(ev.id) || "result"} (${ev.text.split("\n").length} lines)`),
          el("pre", {}, ev.text)));
      case "result":
        return el("div", { class: `ev ev-result ${ev.is_error ? "error" : ""}` }, ev.text || "(no final message)",
          el("span", { class: "muted" },
            [ev.num_turns != null ? `${ev.num_turns} turns` : null,
             ev.duration_ms != null ? `${Math.round(ev.duration_ms / 1000)}s` : null,
             ev.cost_usd != null ? `$${ev.cost_usd.toFixed(3)}` : null].filter(Boolean).join(" · ")));
      default: return el("div", { class: "ev ev-raw" }, ev.text);
    }
  }));
}

function toolPreview(ev) {
  try {
    const input = JSON.parse(ev.input);
    const v = input.command || input.file_path || input.pattern || input.path || input.description || "";
    return String(v).split("\n")[0].slice(0, 120);
  } catch { return ""; }
}

function renderFile(data) {
  const note = data.truncated ? el("p", { class: "muted" }, "(truncated)") : null;
  if (data.name.endsWith(".json")) {
    try { return el("div", {}, note, el("pre", {}, JSON.stringify(JSON.parse(data.content), null, 2))); } catch { /* fall through */ }
  }
  if (data.name.endsWith(".patch") || data.name.endsWith(".diff")) {
    const pre = el("pre", { class: "diff" });
    for (const line of data.content.split("\n")) {
      const cls = line.startsWith("diff --git") ? "file" : line.startsWith("@@") ? "hunk"
        : line.startsWith("+") && !line.startsWith("+++") ? "add"
        : line.startsWith("-") && !line.startsWith("---") ? "del" : "";
      pre.append(el("span", { class: cls }, line + "\n"));
    }
    return el("div", {}, note, data.content.trim() ? pre : el("p", { class: "muted" }, "(empty diff)"));
  }
  return el("div", {}, note, el("pre", {}, data.content || "(empty)"));
}

/* ---------------- boot ---------------- */

async function boot() {
  const cfg = await api("/api/config");
  $("#repo").textContent = cfg.repo_root;
  $("#config").textContent = `base ${cfg.base_ref} · max ${cfg.max_retries} retries · ${cfg.test_cmd}`;
  $("#scan").addEventListener("click", async () => { await api("/api/scan", { method: "POST" }); refreshTasks(); });
  const fromHash = Number(location.hash.replace("#task-", ""));
  if (fromHash) state.selected = fromHash;
  await refreshTasks();
  await refreshDetail();
  setInterval(() => refreshTasks().catch(console.error), 3000);
  setInterval(() => refreshDetail().catch(console.error), 2000);
}

boot().catch(err => { $("#detail").textContent = `Failed to load: ${err}`; });
