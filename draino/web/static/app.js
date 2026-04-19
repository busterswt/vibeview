'use strict';

const POLLING_COOKIE = 'draino_polling_interval';
const DEFAULT_POLLING_INTERVAL_SECONDS = 15;
let inventoryRefreshTimer = null;
let preflightRefreshTimer = null;
let pollingIntervalSeconds = DEFAULT_POLLING_INTERVAL_SECONDS;

function setCookie(name, value, maxAgeSeconds) {
  document.cookie = `${name}=${encodeURIComponent(value)}; path=/; max-age=${maxAgeSeconds}; samesite=lax`;
}

function getCookie(name) {
  const prefix = `${name}=`;
  const parts = document.cookie ? document.cookie.split('; ') : [];
  for (const part of parts) {
    if (part.startsWith(prefix)) return decodeURIComponent(part.slice(prefix.length));
  }
  return '';
}

function normalisePollingInterval(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return DEFAULT_POLLING_INTERVAL_SECONDS;
  if ([0, 15, 30, 60].includes(numeric)) return numeric;
  return DEFAULT_POLLING_INTERVAL_SECONDS;
}

function inventoryRefreshTick() {
  if (pollingIntervalSeconds <= 0) return;
  wsSend({ action: 'refresh_silent' });
}

function preflightRefreshTick() {
  if (pollingIntervalSeconds <= 0) return;
  if (activeTab !== 'instances') return;
  if (!selectedNode) return;
  const nd = nodes[selectedNode];
  if (!nd || !nd.is_compute || nd.phase !== 'idle') return;
  wsSend({ action: 'refresh_preflight', node: selectedNode });
}

function restartPollingTimers() {
  if (inventoryRefreshTimer) clearInterval(inventoryRefreshTimer);
  if (preflightRefreshTimer) clearInterval(preflightRefreshTimer);
  inventoryRefreshTimer = null;
  preflightRefreshTimer = null;
  if (pollingIntervalSeconds <= 0) return;
  const intervalMs = pollingIntervalSeconds * 1000;
  inventoryRefreshTimer = setInterval(inventoryRefreshTick, intervalMs);
  preflightRefreshTimer = setInterval(preflightRefreshTick, intervalMs);
}

function setPollingInterval(value) {
  pollingIntervalSeconds = normalisePollingInterval(value);
  const select = document.getElementById('polling-select');
  if (select) select.value = String(pollingIntervalSeconds);
  setCookie(POLLING_COOKIE, String(pollingIntervalSeconds), 60 * 60 * 24 * 365);
  restartPollingTimers();
}

function initPollingInterval() {
  pollingIntervalSeconds = normalisePollingInterval(getCookie(POLLING_COOKIE) || DEFAULT_POLLING_INTERVAL_SECONDS);
  const select = document.getElementById('polling-select');
  if (select) select.value = String(pollingIntervalSeconds);
  restartPollingTimers();
}


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

function applyK8sDetailWidth(width) {
  const panel = document.getElementById('k8s-detail-wrap');
  if (!panel) return;
  const px = Math.max(560, Math.min(1120, Math.round(width)));
  panel.style.width = `${px}px`;
  localStorage.setItem('vibeviewK8sDetailWidth', String(px));
}

function applyNetworkingDetailWidth(width) {
  const panel = document.getElementById('networking-detail-wrap');
  if (!panel) return;
  const px = Math.max(560, Math.min(1120, Math.round(width)));
  panel.style.width = `${px}px`;
  localStorage.setItem('vibeviewNetworkingDetailWidth', String(px));
}

function applyStorageDetailWidth(width) {
  const panel = document.getElementById('storage-detail-wrap');
  if (!panel) return;
  const px = Math.max(560, Math.min(1120, Math.round(width)));
  panel.style.width = `${px}px`;
  localStorage.setItem('vibeviewStorageDetailWidth', String(px));
}

function applyProjectsDetailWidth(width) {
  const panel = document.getElementById('projects-detail-wrap');
  if (!panel) return;
  const px = Math.max(560, Math.min(1120, Math.round(width)));
  panel.style.width = `${px}px`;
  localStorage.setItem('vibeviewProjectsDetailWidth', String(px));
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

function initK8sDetailResizer() {
  const panel = document.getElementById('k8s-detail-wrap');
  const resizer = document.getElementById('k8s-detail-resizer');
  if (!panel || !resizer) return;

  const saved = Number(localStorage.getItem('vibeviewK8sDetailWidth'));
  if (Number.isFinite(saved) && saved >= 560) applyK8sDetailWidth(saved);

  resizer.addEventListener('mousedown', (event) => {
    tasksDragging = false;
    sidebarDragging = false;
    resizer.classList.add('dragging');
    document.body.dataset.k8sDetailDragging = 'true';
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    event.preventDefault();
  });

  window.addEventListener('mousemove', (event) => {
    if (document.body.dataset.k8sDetailDragging !== 'true') return;
    const view = document.getElementById('view-kubernetes');
    const rect = view?.getBoundingClientRect();
    if (!rect) return;
    const width = rect.right - event.clientX;
    const maxWidth = Math.max(560, rect.width - 260);
    applyK8sDetailWidth(Math.max(560, Math.min(maxWidth, width)));
  });

  window.addEventListener('mouseup', () => {
    if (document.body.dataset.k8sDetailDragging !== 'true') return;
    delete document.body.dataset.k8sDetailDragging;
    resizer.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  });
}

function initNetworkingDetailResizer() {
  const panel = document.getElementById('networking-detail-wrap');
  const resizer = document.getElementById('networking-detail-resizer');
  if (!panel || !resizer) return;

  const saved = Number(localStorage.getItem('vibeviewNetworkingDetailWidth'));
  if (Number.isFinite(saved) && saved >= 560) applyNetworkingDetailWidth(saved);

  resizer.addEventListener('mousedown', (event) => {
    tasksDragging = false;
    sidebarDragging = false;
    resizer.classList.add('dragging');
    document.body.dataset.networkingDetailDragging = 'true';
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    event.preventDefault();
  });

  window.addEventListener('mousemove', (event) => {
    if (document.body.dataset.networkingDetailDragging !== 'true') return;
    const view = document.getElementById('view-networking');
    const rect = view?.getBoundingClientRect();
    if (!rect) return;
    const width = rect.right - event.clientX;
    const maxWidth = Math.max(560, rect.width - 260);
    applyNetworkingDetailWidth(Math.max(560, Math.min(maxWidth, width)));
  });

  window.addEventListener('mouseup', () => {
    if (document.body.dataset.networkingDetailDragging !== 'true') return;
    delete document.body.dataset.networkingDetailDragging;
    resizer.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  });
}

function initStorageDetailResizer() {
  const panel = document.getElementById('storage-detail-wrap');
  const resizer = document.getElementById('storage-detail-resizer');
  if (!panel || !resizer) return;

  const saved = Number(localStorage.getItem('vibeviewStorageDetailWidth'));
  if (Number.isFinite(saved) && saved >= 560) applyStorageDetailWidth(saved);

  resizer.addEventListener('mousedown', (event) => {
    tasksDragging = false;
    sidebarDragging = false;
    resizer.classList.add('dragging');
    document.body.dataset.storageDetailDragging = 'true';
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    event.preventDefault();
  });

  window.addEventListener('mousemove', (event) => {
    if (document.body.dataset.storageDetailDragging !== 'true') return;
    const view = document.getElementById('view-storage');
    const rect = view?.getBoundingClientRect();
    if (!rect) return;
    const width = rect.right - event.clientX;
    const maxWidth = Math.max(560, rect.width - 260);
    applyStorageDetailWidth(Math.max(560, Math.min(maxWidth, width)));
  });

  window.addEventListener('mouseup', () => {
    if (document.body.dataset.storageDetailDragging !== 'true') return;
    delete document.body.dataset.storageDetailDragging;
    resizer.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  });
}

function initProjectsDetailResizer() {
  const panel = document.getElementById('projects-detail-wrap');
  const resizer = document.getElementById('projects-detail-resizer');
  if (!panel || !resizer) return;

  const saved = Number(localStorage.getItem('vibeviewProjectsDetailWidth'));
  if (Number.isFinite(saved) && saved >= 560) applyProjectsDetailWidth(saved);

  resizer.addEventListener('mousedown', (event) => {
    tasksDragging = false;
    sidebarDragging = false;
    resizer.classList.add('dragging');
    document.body.dataset.projectsDetailDragging = 'true';
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    event.preventDefault();
  });

  window.addEventListener('mousemove', (event) => {
    if (document.body.dataset.projectsDetailDragging !== 'true') return;
    const view = document.getElementById('view-projects');
    const rect = view?.getBoundingClientRect();
    if (!rect) return;
    const width = rect.right - event.clientX;
    const maxWidth = Math.max(560, rect.width - 320);
    applyProjectsDetailWidth(Math.max(560, Math.min(maxWidth, width)));
  });

  window.addEventListener('mouseup', () => {
    if (document.body.dataset.projectsDetailDragging !== 'true') return;
    delete document.body.dataset.projectsDetailDragging;
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
initK8sDetailResizer();
initNetworkingDetailResizer();
initStorageDetailResizer();
initProjectsDetailResizer();
initPollingInterval();
if (typeof initGlobalSearch === 'function') initGlobalSearch();
bootstrapSession();
