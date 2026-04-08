'use strict';

// ════════════════════════════════════════════════════════════════════════════
// § PAGINATION UTILITY
// ════════════════════════════════════════════════════════════════════════════

/** Filter an array of objects by testing each item against a search string
 *  across the given field names. Case-insensitive substring match. */
function applyFilter(arr, filter, fields) {
  if (!filter) return arr;
  const q = filter.toLowerCase();
  return arr.filter(item => fields.some(f => String(item[f] || '').toLowerCase().includes(q)));
}

/** Return a slice of arr for the given page/pageSize. */
function paginate(arr, page, pageSize) {
  const start = (page - 1) * pageSize;
  return arr.slice(start, start + pageSize);
}

/** Build pager HTML.
 *  stateRef = string name of the state object (e.g. 'netState')
 *  renderFn = string name of the render function to call on change */
function buildPager(state, total, stateRef, renderFn) {
  if (total === 0) return '';
  const pages  = Math.ceil(total / state.pageSize);
  const start  = (state.page - 1) * state.pageSize + 1;
  const end    = Math.min(state.page * state.pageSize, total);

  // Page number buttons: show up to 5 around current page
  let pageBtns = '';
  const lo = Math.max(1, state.page - 2);
  const hi = Math.min(pages, state.page + 2);
  if (lo > 1)    pageBtns += `<button class="page-btn" onclick="${stateRef}.page=1;${renderFn}()">1</button>${lo > 2 ? '<span style="color:var(--dim)">…</span>' : ''}`;
  for (let p = lo; p <= hi; p++)
    pageBtns += `<button class="page-btn ${p === state.page ? 'current' : ''}" onclick="${stateRef}.page=${p};${renderFn}()">${p}</button>`;
  if (hi < pages) pageBtns += `${hi < pages - 1 ? '<span style="color:var(--dim)">…</span>' : ''}<button class="page-btn" onclick="${stateRef}.page=${pages};${renderFn}()">${pages}</button>`;

  return `<div class="pager">
    <button class="page-btn" onclick="${stateRef}.page=${state.page-1};${renderFn}()" ${state.page<=1?'disabled':''}>← Prev</button>
    ${pageBtns}
    <button class="page-btn" onclick="${stateRef}.page=${state.page+1};${renderFn}()" ${state.page>=pages?'disabled':''}>Next →</button>
    <select class="pager-size" onchange="${stateRef}.pageSize=+this.value;${stateRef}.page=1;${renderFn}()">
      ${[25,50,100].map(s => `<option value="${s}" ${s===state.pageSize?'selected':''}>${s} per page</option>`).join('')}
    </select>
    <span class="pager-info">Showing ${start}–${end} of ${total}</span>
  </div>`;
}

// ════════════════════════════════════════════════════════════════════════════
// § RECENT TASKS PANEL
// ════════════════════════════════════════════════════════════════════════════

/** Record the wall-clock time when a step first enters each state. */
function trackStepTimes(nodeName, steps) {
  for (const s of steps) {
    if (s.status === 'pending') continue;
    const key = `${nodeName}:${s.key}:${s.status}`;
    if (!stepTimes[key]) stepTimes[key] = new Date();
  }
}

/** Return the recorded time for a step, or '—'. */
function stepTime(nodeName, stepKey, status) {
  const key = `${nodeName}:${stepKey}:${status}`;
  const t   = stepTimes[key];
  return t ? t.toLocaleTimeString('en-GB', { hour12: false }) : '—';
}

function stepProgress(status) {
  return {
    success: { pct: 100, cls: 'done' },
    failed:  { pct: 100, cls: 'fail' },
    skipped: { pct: 100, cls: 'skip' },
    running: { pct:  55, cls:  ''   },
  }[status] || { pct: 0, cls: '' };
}

function renderTasksPanel() {
  const tbody = document.getElementById('tasks-tbody');
  const nd    = selectedNode ? nodes[selectedNode] : null;

  // Workflow steps: prefer selected node, fall back to any running node
  const source = (nd?.steps?.length) ? nd
    : Object.values(nodes).find(n => n.steps?.length && n.phase !== 'idle');

  // Individual instance migrations
  const migTasks = Object.values(instanceMigrateTasks);

  if (!source?.steps?.length && !migTasks.length) {
    tbody.innerHTML = `<tr><td colspan="5" style="color:var(--dim);text-align:center;padding:10px">No active tasks.</td></tr>`;
    return;
  }

  let html = '';

  // ── Workflow steps ────────────────────────────────────────────────────────
  if (source?.steps?.length) {
    for (const s of source.steps) {
      const prog = stepProgress(s.status);
      const ts   = stepTime(source.k8s_name, s.key, s.status);
      const statusHtml = {
        running: `<span style="display:flex;align-items:center;gap:4px"><span class="spinner">⟳</span> Running…</span>`,
        success: `<span style="color:var(--green)">✓ Done</span>`,
        failed:  `<span style="color:var(--red)">✗ Failed</span>`,
        skipped: `<span style="color:var(--dim)">— Skipped</span>`,
        pending: `<span style="color:var(--dim)">○ Pending</span>`,
      }[s.status] || `<span style="color:var(--dim)">${esc(s.status)}</span>`;
      html += `<tr>
        <td>${esc(s.label)}</td>
        <td>${esc(source.k8s_name)}</td>
        <td>${statusHtml}</td>
        <td><div class="progress-inline">
          <div class="prog-bar"><div class="prog-fill ${prog.cls}" style="width:${prog.pct}%"></div></div>
          <span style="color:var(--dim)">${prog.pct}%</span>
        </div></td>
        <td style="color:var(--dim)">${ts}</td>
      </tr>`;
    }
  }

  // ── Individual instance migrations ────────────────────────────────────────
  for (const t of migTasks) {
    const prog = t.status === 'complete' ? { pct: 100, cls: 'done' }
               : t.status === 'error'    ? { pct: 100, cls: 'fail' }
               : { pct: 55, cls: '' };
    const ts   = t.startTime ? t.startTime.toLocaleTimeString('en-GB', { hour12: false }) : '—';
    const statusHtml = t.status === 'migrating'
      ? `<span style="display:flex;align-items:center;gap:4px"><span class="spinner">⟳</span> Migrating…</span>`
      : t.status === 'complete'
      ? `<span style="color:var(--green)">✓ Done</span>`
      : `<span style="color:var(--red)">✗ Failed</span>`;
    html += `<tr>
      <td>↗ Migrate: ${esc(t.name)}</td>
      <td>${esc(t.nodeName)}</td>
      <td>${statusHtml}</td>
      <td><div class="progress-inline">
        <div class="prog-bar"><div class="prog-fill ${prog.cls}" style="width:${prog.pct}%"></div></div>
        <span style="color:var(--dim)">${prog.pct}%</span>
      </div></td>
      <td style="color:var(--dim)">${ts}</td>
    </tr>`;
  }

  tbody.innerHTML = html;
}

// ════════════════════════════════════════════════════════════════════════════
// § ACTION BUTTONS
// ════════════════════════════════════════════════════════════════════════════

/** Show/hide and update breadcrumb action buttons based on selected node. */
function updateActionButtons(nd) {
  const nodeActions = document.getElementById('bc-node-actions');
  if (!nd) {
    nodeActions.style.display = 'none';
    return;
  }
  nodeActions.style.display = 'flex';

  const phase   = nd.phase;
  const drained = nd.k8s_cordoned || nd.compute_status === 'disabled';
  const rebootReady = nd.is_compute
    ? drained && nd.compute_status === 'disabled' && nd.vm_count === 0 && nd.amphora_count === 0
    : nd.k8s_cordoned;
  const busy    = ['running','rebooting','undraining'].includes(phase);

  const evBtn = document.getElementById('bc-evacuate');
  const drBtn = document.getElementById('bc-drain');
  const rbBtn = document.getElementById('bc-reboot');
  const poBtn = document.getElementById('bc-pods');
  const nsBtn = document.getElementById('bc-noschedule');

  evBtn.textContent = phase === 'running'    ? '▶ Evacuating…' : '▶ Evacuate';
  evBtn.disabled    = busy || !nd.is_compute;
  evBtn.className   = 'btn primary';

  drBtn.textContent = phase === 'undraining' ? '↺ Undraining…' : drained ? '↺ Undrain' : '▽ Drain';
  drBtn.disabled    = busy;
  drBtn.className   = `btn ${drained && !busy ? 'warning' : ''}`;

  rbBtn.textContent = phase === 'rebooting'  ? '⏻ Rebooting…'  : '⏻ Reboot';
  rbBtn.disabled    = busy || !authInfo?.is_admin || !rebootReady;
  rbBtn.title       = !authInfo?.is_admin
    ? "Requires OpenStack admin role"
    : !rebootReady
      ? "Reboot is available only after the node has been cordoned and fully drained"
      : '';

  poBtn.disabled = false;
  const managedNoSchedule = hasManagedNoScheduleTaint(nd);
  nsBtn.textContent = managedNoSchedule ? '↺ Remove NoSchedule' : '＋ Add NoSchedule';
  nsBtn.disabled = busy;
  nsBtn.className = `btn ${managedNoSchedule ? 'warning' : ''}`;
  nsBtn.title = managedNoSchedule
    ? 'Remove VibeView-managed maintenance NoSchedule taint'
    : 'Add VibeView-managed maintenance NoSchedule taint';
}

// ════════════════════════════════════════════════════════════════════════════
// § ACTIONS → WEBSOCKET
// ════════════════════════════════════════════════════════════════════════════

function actionRefreshNode() {
  if (!selectedNode) return;
  delete nodeDetailCache[selectedNode];
  delete nodeMetricsCache[selectedNode];
  loadNodeDetail(selectedNode, true);
  if (activeTab === 'monitor') loadNodeMetrics(selectedNode, true);
}

function actionRefreshAll()  {
  Object.keys(nodeDetailCache).forEach((name) => delete nodeDetailCache[name]);
  Object.keys(nodeMetricsCache).forEach((name) => delete nodeMetricsCache[name]);
  if (selectedNode) loadNodeDetail(selectedNode, true);
  if (selectedNode && activeTab === 'monitor') loadNodeMetrics(selectedNode, true);
  wsSend({ action: 'refresh' });
}
function actionEvacuate() { if (selectedNode) wsSend({ action: 'evacuate',       node: selectedNode }); }
function actionReboot()   { if (selectedNode) wsSend({ action: 'reboot_request', node: selectedNode }); }
async function actionToggleNoSchedule() {
  if (!selectedNode) return;
  const nd = nodes[selectedNode];
  if (!nd) return;
  const enabled = !hasManagedNoScheduleTaint(nd);
  try {
    const resp = await fetch(`/api/nodes/${encodeURIComponent(selectedNode)}/taints/noschedule`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    const data = await resp.json();
    if (!resp.ok || data.ok === false) throw new Error(data.error || `HTTP ${resp.status}`);
    actionRefreshNode();
    wsSend({ action: 'refresh_silent' });
  } catch (err) {
    addLog(selectedNode, `NoSchedule taint error: ${err.message || err}`, 'red');
  }
}

function podsButtonText() {
  if (!showPods)           return '⬡ Pods';
  if (!lastPodsCache)      return '⟳ Pods…';
  return '⟳ Refresh Pods';
}

/** Keep both pod buttons (breadcrumb bar + instances tab toolbar) in sync. */
function syncPodsButton() {
  const txt = podsButtonText();
  const bc  = document.getElementById('bc-pods');
  const tb  = document.getElementById('inst-pods-btn');
  if (bc) bc.textContent = txt;
  if (tb) tb.textContent = txt;
}

function migrateInstance(instanceId) {
  if (!selectedNode) return;
  const nd   = nodes[selectedNode];
  const inst = nd?.preflight_instances?.find(i => i.id === instanceId);
  instanceMigrateStates[instanceId] = 'migrating';
  instanceMigrateTasks[instanceId]  = {
    name:      inst?.name || instanceId.slice(0, 8),
    nodeName:  selectedNode,
    status:    'migrating',
    startTime: new Date(),
  };
  wsSend({ action: 'migrate_instance', node: selectedNode, instance_id: instanceId });
  if (nd) renderInstancesTab(nd);
  renderTasksPanel();
}

function actionDrainOrUndrain() {
  if (!selectedNode) return;
  const nd = nodes[selectedNode]; if (!nd) return;
  const action = (nd.k8s_cordoned || nd.compute_status === 'disabled') ? 'undrain' : 'drain_quick';
  wsSend({ action, node: selectedNode });
}

function actionPods() {
  showTab('instances');
  actionPodsInline();
}

function actionPodsInline() {
  if (!selectedNode) return;
  showPods      = true;
  lastPodsCache = null;
  wsSend({ action: 'get_pods', node: selectedNode });
  syncPodsButton();
  const sec = document.getElementById('pods-section');
  if (sec) sec.innerHTML = `<div style="color:var(--dim);font-size:12px"><span class="spinner">⟳</span> Fetching pods…</div>`;
}

// ════════════════════════════════════════════════════════════════════════════
// § MODALS
// ════════════════════════════════════════════════════════════════════════════

function cancelReboot() {
  document.getElementById('modal-overlay').classList.remove('open');
  if (pendingReboot) { wsSend({ action: 'reboot_cancel', node: pendingReboot }); pendingReboot = null; }
}

function confirmReboot() {
  if (document.getElementById('modal-input').value.trim() !== 'YES') {
    const inp = document.getElementById('modal-input');
    inp.classList.remove('shake'); void inp.offsetWidth; inp.classList.add('shake'); inp.focus();
    return;
  }
  document.getElementById('modal-overlay').classList.remove('open');
  if (pendingReboot) { wsSend({ action: 'reboot_confirm', node: pendingReboot }); pendingReboot = null; }
}

function closeBlocked() { document.getElementById('blocked-overlay').classList.remove('open'); }

// ════════════════════════════════════════════════════════════════════════════
// § CLOCK & LIVE TIMERS
// ════════════════════════════════════════════════════════════════════════════

setInterval(() => {
  document.getElementById('clock').textContent = new Date().toLocaleTimeString('en-GB', { hour12: false });
  // Refresh summary while rebooting (live downtime counter)
  if (selectedNode && nodes[selectedNode]) {
    const nd = nodes[selectedNode];
    if (nd.phase === 'rebooting') {
      if (activeTab === 'summary') renderSummaryTab(nd);
      renderTasksPanel();
    }
  }
}, 1000);

// Periodic silent refresh of instance list every 10 s while Instances tab is open
setInterval(() => {
  if (activeTab !== 'instances') return;
  if (!selectedNode) return;
  const nd = nodes[selectedNode];
  if (!nd || !nd.is_compute || nd.phase !== 'idle') return;
  wsSend({ action: 'refresh_preflight', node: selectedNode });
}, 10000);

// Periodic silent inventory refresh so external node changes like taints
// show up without manual intervention.
setInterval(() => {
  wsSend({ action: 'refresh_silent' });
}, 15000);

// ════════════════════════════════════════════════════════════════════════════
// § UTILS
// ════════════════════════════════════════════════════════════════════════════

function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function escAttr(s) { return String(s ?? '').replace(/'/g, "\\'"); }

function applySidebarWidth(width) {
  const sidebar = document.getElementById('sidebar');
  if (!sidebar) return;
  const px = Math.max(300, Math.min(720, Math.round(width)));
  sidebar.style.width = `${px}px`;
  localStorage.setItem('drainoSidebarWidth', String(px));
}

function applyTasksHeight(height) {
  const panel = document.getElementById('tasks-panel');
  if (!panel) return;
  const px = Math.max(110, Math.min(190, Math.round(height)));
  panel.style.height = `${px}px`;
  localStorage.setItem('drainoTasksHeight', String(px));
}

function initSidebarResizer() {
  const sidebar = document.getElementById('sidebar');
  const resizer = document.getElementById('sidebar-resizer');
  if (!sidebar || !resizer) return;

  const saved = Number(localStorage.getItem('drainoSidebarWidth'));
  if (Number.isFinite(saved) && saved >= 300) applySidebarWidth(saved);

  resizer.addEventListener('mousedown', (event) => {
    sidebarDragging = true;
    resizer.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    event.preventDefault();
  });

  window.addEventListener('mousemove', (event) => {
    if (!sidebarDragging) return;
    const rect = document.getElementById('view-infrastructure')?.getBoundingClientRect();
    const maxWidth = rect ? Math.min(720, rect.width - 420) : 720;
    const px = Math.max(300, Math.min(maxWidth, event.clientX));
    applySidebarWidth(px);
  });

  window.addEventListener('mouseup', () => {
    if (!sidebarDragging) return;
    sidebarDragging = false;
    resizer.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  });
}

function initTasksResizer() {
  const panel = document.getElementById('tasks-panel');
  const resizer = document.getElementById('tasks-resizer');
  if (!panel || !resizer) return;

  const saved = Number(localStorage.getItem('drainoTasksHeight'));
  if (Number.isFinite(saved) && saved >= 110) applyTasksHeight(saved);

  resizer.addEventListener('mousedown', (event) => {
    tasksDragging = true;
    resizer.classList.add('dragging');
    document.body.style.cursor = 'ns-resize';
    document.body.style.userSelect = 'none';
    event.preventDefault();
  });

  window.addEventListener('mousemove', (event) => {
    if (!tasksDragging) return;
    const bodyRect = document.body.getBoundingClientRect();
    const px = bodyRect.bottom - event.clientY;
    applyTasksHeight(px);
  });

  window.addEventListener('mouseup', () => {
    if (!tasksDragging) return;
    tasksDragging = false;
    resizer.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  });
}

// ════════════════════════════════════════════════════════════════════════════
// § INIT
// ════════════════════════════════════════════════════════════════════════════

initSidebarResizer();
initTasksResizer();
bootstrapSession();
