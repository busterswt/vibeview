'use strict';


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
