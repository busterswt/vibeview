'use strict';

// ════════════════════════════════════════════════════════════════════════════
// § NODE SELECTION
// ════════════════════════════════════════════════════════════════════════════

function selectNode(name) {
  const previousNode = selectedNode;
  // Bug fix: clear all existing selected items before setting new one
  document.querySelectorAll('.tree-item.selected').forEach(el => el.classList.remove('selected'));

  if (previousNode && previousNode !== name) {
    delete nodeNetStatsEnabled[previousNode];
  }

  selectedNode  = name;
  lastPodsCache = null;

  // Highlight new item directly (avoids rebuilding whole sidebar)
  const el = document.querySelector(`[data-node="${escAttr(name)}"]`);
  if (el) el.classList.add('selected');

  const nd = nodes[name];
  if (nd) {
    ensureSelectedEtcdHealthCheck();
    if (nd.is_compute && nd.phase === 'idle') wsSend({ action: 'get_preflight', node: name });
  }
  // Load rich node detail if not already cached
  if (shouldLoadNodeDetail(name)) loadNodeDetail(name);
  if (activeTab === 'monitor') {
    if (shouldLoadNodeMetrics(name)) loadNodeMetrics(name);
    _ensureNetworkDataLoaded(name);
    loadNodeIrqBalance(name);
    if (isNodeSarExpanded(name)) loadNodeSarTrends(name);
  }
  if (activeTab === 'pods') actionPodsInline();
  // Load network config data if Configure tab is active
  if (activeTab === 'configure') _ensureNetworkDataLoaded(name);
  renderInfraDetail();
}

function ensureSelectedEtcdHealthCheck() {
  if (!selectedNode) return;
  const nd = nodes[selectedNode];
  if (!nd || !nd.is_etcd) return;
  const peers = Object.values(nodes).filter(n => n.is_etcd);
  if (peers.some(n => n.etcd_healthy === null || n.etcd_healthy === undefined))
    wsSend({ action: 'check_etcd' });
}

async function loadNodeDetail(name, force = false) {
  nodeDetailCache[name] = { loading: true, k8s: null, nova: null, hw: null, error: null };
  if (activeTab === 'summary') renderSummaryTab(nodes[name]);
  try {
    const qs = force ? '?refresh=1' : '';
    const resp = await fetch(`/api/nodes/${encodeURIComponent(name)}/detail${qs}`);
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else if (nodes[name]?.is_compute) recordApiSuccess('Nova');
    nodeDetailCache[name] = { loading: false, k8s: json.k8s || {}, nova: json.nova || {}, hw: json.hw || {}, error: json.error || null };
  } catch (e) {
    nodeDetailCache[name] = { loading: false, k8s: {}, nova: {}, error: String(e) };
  }
  if (selectedNode === name && activeTab === 'summary') renderSummaryTab(nodes[name]);
}

function shouldLoadNodeDetail(name) {
  const detail = nodeDetailCache[name];
  return !detail || detail.error;
}

// ════════════════════════════════════════════════════════════════════════════
// § INFRASTRUCTURE DETAIL PANEL
// ════════════════════════════════════════════════════════════════════════════

function renderInfraDetail() {
  if (!selectedNode || !nodes[selectedNode]) {
    clearObjHeader();
    updateActionButtons(null);
    updateBreadcrumb(null);
    renderTasksPanel();
    return;
  }
  const nd = nodes[selectedNode];
  renderObjHeader(nd);
  updateActionButtons(nd);
  updateBreadcrumb(nd);
  renderTasksPanel();
  renderActiveTab(nd);
}

function clearObjHeader() {
  document.getElementById('oh-icon').textContent   = '🖥️';
  document.getElementById('oh-name').textContent   = 'Select a node';
  document.getElementById('oh-sub').textContent    = 'Choose a node from the inventory to view its status and manage operations.';
  document.getElementById('oh-badges').innerHTML   = '';
}

function renderObjHeader(nd) {
  const det = nodeDetailCache[nd.k8s_name] || null;
  const detailsRefreshing = !det || det.loading;
  document.getElementById('oh-icon').textContent = nd.is_compute ? '🖥️' : nd.is_etcd ? '☣️' : '⚙️';
  document.getElementById('oh-name').textContent = nd.k8s_name;

  const parts = [];
  if (nd.hypervisor && nd.hypervisor !== nd.k8s_name) parts.push(`Hypervisor: ${nd.hypervisor}`);
  if (nd.uptime)         parts.push(`Uptime: ${nd.uptime}`);
  if (nd.kernel_version) parts.push(`Kernel: ${nd.kernel_version}`);
  if (nd.latest_kernel_version && nd.latest_kernel_version !== nd.kernel_version)
    parts.push(`Latest: ${nd.latest_kernel_version}`);
  document.getElementById('oh-sub').textContent = parts.join('  ·  ') || '';

  const phaseColors = { running:'yellow', complete:'green', error:'red', rebooting:'magenta', undraining:'cyan' };
  const phaseLabels = { running:'Workflow Running', complete:'Complete', error:'Error', rebooting:'Rebooting', undraining:'Undraining' };
  const badges = [];
  const noSched = noScheduleTaints(nd);
  if (nd.phase !== 'idle')
    badges.push(`<span class="badge ${phaseColors[nd.phase]||'gray'}">${phaseLabels[nd.phase]||nd.phase.toUpperCase()}</span>`);
  if      (!nd.k8s_ready)     badges.push(`<span class="badge red">● K8s Not Ready</span>`);
  else if (nd.k8s_cordoned)   badges.push(`<span class="badge gray">Cordoned</span>`);
  else if (nd.phase === 'idle') badges.push(`<span class="badge green">● K8s Ready</span>`);
  if (noSched.length)
    badges.push(`<span class="badge red">NoSchedule</span>`);
  if (nd.is_edge)
    badges.push(`<span class="badge green">Edge</span>`);
  if (nd.reboot_required)
    badges.push(`<span class="badge yellow">Reboot Needed</span>`);
  if (nd.latest_kernel_version && nd.latest_kernel_version !== nd.kernel_version)
    badges.push(`<span class="badge blue">New Kernel Available</span>`);
  if (nd.is_compute) {
    const ncMap = { up:'green', disabled:'yellow', down:'red' };
    const nlMap = { up:'enabled', disabled:'disabled', down:'DOWN' };
    if (nd.compute_status)
      badges.push(`<span class="badge ${ncMap[nd.compute_status]||'gray'}">● Nova ${nlMap[nd.compute_status]||nd.compute_status}</span>`);
    else
      badges.push(`<span class="badge gray">Nova …</span>`);
    badges.push(`<span class="badge blue">Compute Node</span>`);
  }
  if (nd.is_etcd) badges.push(`<span class="tree-badge etcd">etcd</span>`);
  if (nd.hosts_mariadb) badges.push(`<span class="tree-badge mariadb">mariadb</span>`);
  if (nd.is_edge) badges.push(`<span class="tree-badge edge">edge</span>`);
  if (detailsRefreshing) badges.push(`<span class="badge blue">⟳ Details Refreshing</span>`);
  document.getElementById('oh-badges').innerHTML = badges.join('');
}

function updateBreadcrumb(nd) {
  if (activeView !== 'infrastructure') return;
  document.getElementById('bc-sep').style.display = nd ? '' : 'none';
  document.getElementById('bc-node').textContent  = nd ? nd.k8s_name : '';
}

function captureFocusedInput(container, selector) {
  const active = document.activeElement;
  if (!container || !active || !container.contains(active) || !active.matches(selector)) return null;
  return {
    selector,
    start: active.selectionStart,
    end: active.selectionEnd,
  };
}

function restoreFocusedInput(container, state) {
  if (!container || !state) return;
  const input = container.querySelector(state.selector);
  if (!input) return;
  input.focus();
  if (typeof state.start === 'number' && typeof state.end === 'number') {
    try { input.setSelectionRange(state.start, state.end); } catch (_) {}
  }
}

// ════════════════════════════════════════════════════════════════════════════
// § TABS (within Infrastructure view)
// ════════════════════════════════════════════════════════════════════════════

function showTab(name) {
  activeTab = name;
  ['summary','instances','pods','monitor','configure'].forEach(t => {
    document.getElementById(`tab-${t}`).style.display = t === name ? '' : 'none';
    document.getElementById(`tab-btn-${t}`).className = 'tab' + (t === name ? ' active' : '');
  });
  if (name === 'pods' && selectedNode && lastPodsCache?.node !== selectedNode) actionPodsInline();
  if (name === 'monitor' && selectedNode && shouldLoadNodeMetrics(selectedNode)) loadNodeMetrics(selectedNode);
  if (name === 'monitor' && selectedNode) _ensureNetworkDataLoaded(selectedNode);
  if (name === 'monitor' && selectedNode) loadNodeIrqBalance(selectedNode);
  if (name === 'monitor' && selectedNode && isNodeSarExpanded(selectedNode)) loadNodeSarTrends(selectedNode);
  if (selectedNode && nodes[selectedNode]) renderActiveTab(nodes[selectedNode]);
  if (name === 'configure' && selectedNode) _ensureNetworkDataLoaded(selectedNode);
}

function renderActiveTab(nd) {
  if (activeTab === 'summary')   renderSummaryTab(nd);
  if (activeTab === 'instances') renderInstancesTab(nd);
  if (activeTab === 'pods')      renderPodsTab(nd);
  if (activeTab === 'monitor')   renderNodeMonitorTab(nd);
  if (activeTab === 'configure') renderConfigureTab(nd);
}

function _ensureNetworkDataLoaded(nodeName) {
  const c = nodeNetworkCache[nodeName] || {};
  if (!c.annotations && !c.annLoading)    loadOvnAnnotations(nodeName);
  if (!c.ifaces     && !c.ifacesLoading)  loadNetworkInterfaces(nodeName);
  // Initialise edit state if annotations just arrived and edit not yet set
  if (c.annotations && netEdit.node !== nodeName) _initNetEdit(nodeName);
}
