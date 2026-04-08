'use strict';

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
