'use strict';

// ════════════════════════════════════════════════════════════════════════════
// § VIEW SWITCHING (top nav)
// ════════════════════════════════════════════════════════════════════════════

function topLevelView(name) {
  if (name === 'networking') return 'networking';
  return name;
}

function switchNetworkingSection(name) {
  const valid = ['networking', 'routers', 'loadbalancers', 'k8s-vpcs', 'k8s-subnets', 'k8s-vlans', 'k8s-providernetworks', 'k8s-providersubnets', 'k8s-ips', 'k8s-clusternetworks', 'k8s-networkdomains', 'k8s-services', 'k8s-lbs', 'k8s-gatewayclasses', 'k8s-gateways', 'k8s-httproutes'];
  if (!valid.includes(name)) return;
  activeNetworkingView = name;
  switchView('networking');
}

function isNetworkingK8sView(name = activeNetworkingView) {
  return String(name || '').startsWith('k8s-');
}

function networkingK8sType(name = activeNetworkingView) {
  return ({
    'k8s-services': 'services',
    'k8s-clusternetworks': 'clusternetworks',
    'k8s-networkdomains': 'networkdomains',
    'k8s-vpcs': 'vpcs',
    'k8s-subnets': 'subnets',
    'k8s-vlans': 'vlans',
    'k8s-providernetworks': 'providernetworks',
    'k8s-providersubnets': 'providersubnets',
    'k8s-ips': 'ips',
    'k8s-lbs': 'lbs',
    'k8s-gatewayclasses': 'gatewayclasses',
    'k8s-gateways': 'gateways',
    'k8s-httproutes': 'httproutes',
  })[name] || null;
}

function networkingViewLabel(name = activeNetworkingView) {
  if (name === 'networking') return 'Networks';
  if (name === 'routers') return 'Routers';
  if (name === 'loadbalancers') return 'Load Balancers';
  const k8sType = networkingK8sType(name);
  return k8sType ? (K8S_RES_META[k8sType]?.label || 'Kubernetes Networking') : 'Networking';
}

function renderNetworkingWorkspace() {
  document.querySelectorAll('.networking-nav-item').forEach(el => {
    el.classList.toggle('selected', el.dataset.networkingView === activeNetworkingView);
  });

  const panes = {
    networking: document.getElementById('net-wrap'),
    routers: document.getElementById('router-wrap'),
    loadbalancers: document.getElementById('lb-wrap'),
    k8s: document.getElementById('networking-k8s-content'),
  };
  Object.values(panes).forEach(pane => pane?.classList.remove('active'));
  if (isNetworkingK8sView()) panes.k8s?.classList.add('active');
  else panes[activeNetworkingView]?.classList.add('active');

  const detailPanes = ['net-detail-wrap', 'router-detail-wrap', 'lb-detail-wrap', 'networking-k8s-detail-wrap'];
  detailPanes.forEach(id => document.getElementById(id)?.classList.remove('open'));

  if (activeNetworkingView === 'networking' && selectedNetwork && netDetailState.data) {
    document.getElementById('net-detail-wrap')?.classList.add('open');
  }
  if (activeNetworkingView === 'routers' && selectedRouter && routerDetailState.data) {
    document.getElementById('router-detail-wrap')?.classList.add('open');
  }
  if (activeNetworkingView === 'loadbalancers' && selectedLoadBalancer && lbDetailState.data) {
    document.getElementById('lb-detail-wrap')?.classList.add('open');
  }
  if (isNetworkingK8sView() && k8sDetailState.type && k8sDetailState.item) {
    document.getElementById('networking-k8s-detail-wrap')?.classList.add('open');
  }

  const detailWrap = document.getElementById('networking-detail-wrap');
  const resizer = document.getElementById('networking-detail-resizer');
  const open = detailPanes.some(id => document.getElementById(id)?.classList.contains('open'));
  detailWrap?.classList.toggle('open', open);
  resizer?.classList.toggle('open', open);
}

function switchView(name) {
  if (activeView === 'stress' && name !== 'stress' && typeof stopStressStatusPolling === 'function') {
    stopStressStatusPolling();
  }
  if (['routers', 'loadbalancers'].includes(name)) name = 'networking';
  activeView = name;

  // Top nav highlight
  document.querySelectorAll('.top-nav a').forEach(a => {
    a.classList.toggle('active', a.dataset.view === topLevelView(name));
  });

  // Show / hide body views
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById(`view-${name}`).classList.add('active');

  // Breadcrumb root label + actions
  const bcRoot = document.getElementById('bc-root');
  const bcSep  = document.getElementById('bc-sep');
  const bcNode = document.getElementById('bc-node');
  document.getElementById('bc-infra-actions').style.display = name === 'infrastructure' ? '' : 'none';
  document.getElementById('bc-k8s-actions').style.display   = (name === 'kubernetes' || (name === 'networking' && isNetworkingK8sView())) ? '' : 'none';
  document.getElementById('bc-net-actions').style.display   = (name === 'networking' && activeNetworkingView === 'networking') ? '' : 'none';
  document.getElementById('bc-router-actions').style.display = (name === 'networking' && activeNetworkingView === 'routers') ? '' : 'none';
  document.getElementById('bc-lb-actions').style.display    = (name === 'networking' && activeNetworkingView === 'loadbalancers') ? '' : 'none';
  document.getElementById('bc-report-actions').style.display = name === 'reports'       ? '' : 'none';
  document.getElementById('bc-stress-actions').style.display = name === 'stress'        ? '' : 'none';
  document.getElementById('bc-vol-actions').style.display   = name === 'storage'        ? '' : 'none';
  document.getElementById('tasks-panel').style.display      = name === 'infrastructure' ? '' : 'none';

  if (name === 'infrastructure') {
    bcRoot.textContent = 'All Nodes';
    // Restore node breadcrumb from selectedNode
    if (selectedNode) {
      bcSep.style.display = '';
      bcNode.textContent  = selectedNode;
    } else {
      bcSep.style.display = 'none';
      bcNode.textContent  = '';
    }
    renderInfraDetail();  // re-render content panel when returning from other views
  } else {
    const label = name === 'monitor'
      ? 'Monitor'
      : name === 'networking'
        ? networkingViewLabel()
        : name === 'stress'
          ? 'Stress'
        : name === 'reports'
          ? 'Reports'
        : name === 'storage'
          ? 'Volumes'
          : name === 'kubernetes'
            ? 'Kubernetes'
            : name;
    bcRoot.textContent  = 'VibeView';
    bcSep.style.display = '';
    bcNode.textContent  = label;
    // Lazy-load on first visit
    if (name === 'kubernetes') {
      bcRoot.textContent = 'VibeView';
      bcSep.style.display = '';
      bcNode.textContent = k8sActiveResource ? K8S_RES_META[k8sActiveResource]?.label || 'Kubernetes' : 'Kubernetes';
    }
    if (name === 'monitor') renderMonitorView();
    if (name === 'storage' && !hasOpenStackAuth()) {
      document.getElementById('vol-wrap').innerHTML = renderOpenStackUnavailablePanel('Volumes', 'This view currently relies on Cinder inventory. Provide OpenStack credentials to enable it.');
      return;
    }
    if (name === 'networking') {
      renderNetworkingWorkspace();
      if (!isNetworkingK8sView() && !hasOpenStackAuth()) {
        if (activeNetworkingView === 'networking') {
          document.getElementById('net-wrap').innerHTML = renderOpenStackUnavailablePanel('Networks', 'This view currently relies on OpenStack networking data. Provide OpenStack credentials to enable it.');
        }
        if (activeNetworkingView === 'routers') {
          document.getElementById('router-wrap').innerHTML = renderOpenStackUnavailablePanel('Routers', 'This view currently relies on OpenStack router inventory. Provide OpenStack credentials to enable it.');
        }
        if (activeNetworkingView === 'loadbalancers') {
          document.getElementById('lb-wrap').innerHTML = renderOpenStackUnavailablePanel('Load Balancers', 'This view currently relies on Octavia inventory. Provide OpenStack credentials to enable it.');
        }
        renderNetworkingWorkspace();
        return;
      }
      if (activeNetworkingView === 'networking' && !netState.data && !netState.loading) loadNetworks();
      if (activeNetworkingView === 'routers' && !routerState.data && !routerState.loading) loadRouters();
      if (activeNetworkingView === 'loadbalancers') loadLoadBalancers();
      if (isNetworkingK8sView()) {
        const k8sType = networkingK8sType();
        if (k8sType) selectK8sResource(k8sType);
      }
    }
    if (name === 'storage'    && !volState.data && !volState.loading) loadVolumes();
    if (name === 'stress') {
      if (!hasOpenStackAuth()) {
        renderStressView();
        return;
      }
      renderStressView();
      if (typeof startStressStatusPolling === 'function') startStressStatusPolling();
    }
    if (name === 'reports') renderReportsView();
  }
}

function handleBcRoot() {
  if (activeView !== 'infrastructure') switchView('infrastructure');
  else { selectedNode = null; renderInfraDetail(); }
}

// ════════════════════════════════════════════════════════════════════════════
// § SIDEBAR
// ════════════════════════════════════════════════════════════════════════════

function phaseColor(nd) {
  if (nd.phase === 'error')      return 'red';
  if (nd.phase === 'rebooting')  return 'magenta';
  if (nd.phase === 'undraining') return 'cyan';
  if (nd.phase === 'running')    return 'yellow';
  if (nd.phase === 'complete')   return 'green';
  if (!nd.k8s_ready)             return 'red';
  if (nd.k8s_cordoned)           return 'gray';
  if (nd.compute_status === 'down')     return 'red';
  if (nd.compute_status === 'disabled') return 'yellow';
  return 'green';
}

function noScheduleTaints(nd) {
  return (nd.k8s_taints || []).filter(t => t.effect === 'NoSchedule');
}

function hasManagedNoScheduleTaint(nd) {
  return noScheduleTaints(nd).some(t => t.key === 'draino.openstack.org/maintenance');
}

function taintLabel(t) {
  const value = t.value ? `=${t.value}` : '';
  return `${t.key}${value}`;
}

function treeItemHtml(name, nd) {
  const sel     = name === selectedNode ? ' selected' : '';
  const ico     = nd.is_compute ? '🖥️' : nd.is_etcd ? '☣️' : '⚙️';
  const hintTxt = nd.is_compute ? 'ESXi' : nd.is_etcd ? 'Mgmt' : 'Host';
  const dot     = phaseColor(nd);
  const etcBdg  = nd.is_etcd ? `<span class="tree-badge etcd">etcd</span>` : '';
  const mariadbBdg = nd.hosts_mariadb ? `<span class="tree-badge mariadb">mariadb</span>` : '';
  const agentBdg = nd.node_agent_ready === false
    ? `<span class="tree-badge noagent" title="No ready node-agent pod on this node">NoAgent</span>`
    : '';
  const edgeBdg = nd.is_edge ? `<span class="tree-badge edge">edge</span>` : '';
  const noSched = noScheduleTaints(nd);
  const noSchedBdg = noSched.length
    ? `<span class="tree-badge nosched" title="${escAttr(noSched.map(taintLabel).join(', '))}">NoSchedule</span>`
    : '';
  const rebootBdg = nd.reboot_required ? `<span class="tree-badge reboot">reboot</span>` : '';
  const kernelBdg = nd.latest_kernel_version && nd.kernel_version && nd.latest_kernel_version !== nd.kernel_version
    ? `<span class="tree-badge kernel">kernel</span>`
    : '';

  let vmBadge = '';
  if (nd.is_compute && nd.vm_count != null && nd.vm_count > 0)
    vmBadge = `<span class="tree-badge warn">${nd.vm_count} vm</span>`;

  // Aggregate membership badges (up to 2, then "+N more")
  let aggHtml = '';
  if (nd.aggregates?.length) {
    const shown = nd.aggregates.slice(0, 2);
    const extra = nd.aggregates.length - shown.length;
    aggHtml = shown.map(a => `<span class="tree-badge agg" title="${esc(a)}">${esc(a)}</span>`).join('');
    if (extra > 0) aggHtml += `<span class="tree-badge agg">+${extra}</span>`;
  }

  return `<div class="tree-item${sel}" onclick="selectNode('${escAttr(name)}')" data-node="${escAttr(name)}">
    <span class="ti-ico">${ico}</span>
    <span class="tree-dot ${dot}"></span>
    <span class="ti-name">${esc(name)}</span>
    <span class="hint">${hintTxt}</span>
    ${etcBdg}${mariadbBdg}${agentBdg}${edgeBdg}${noSchedBdg}${rebootBdg}${kernelBdg}${vmBadge}${aggHtml}
  </div>`;
}

function compareNodeNames(a, b) {
  const dir = nodeSortDirection === 'desc' ? -1 : 1;
  return dir * a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' });
}

function setNodeSort(direction) {
  if (direction !== 'asc' && direction !== 'desc') return;
  nodeSortDirection = direction;
  document.getElementById('sort-asc-btn')?.classList.toggle('active', direction === 'asc');
  document.getElementById('sort-desc-btn')?.classList.toggle('active', direction === 'desc');
  rebuildSidebar();
}

function rebuildSidebar() {
  const compute = Object.entries(nodes).filter(([,nd]) => nd.is_compute);
  const other   = Object.entries(nodes).filter(([,nd]) => !nd.is_compute);

  // Group compute nodes by AZ; fall back to single group if no AZ data at all
  const byAZ = {};
  for (const [name, nd] of compute) {
    const az = nd.availability_zone || 'nova';
    (byAZ[az] = byAZ[az] || []).push([name, nd]);
  }
  const azKeys = Object.keys(byAZ).sort((a, b) => compareNodeNames(a, b));

  let html = '';

  for (const az of azKeys) {
    const entries = [...byAZ[az]].sort(([nameA], [nameB]) => compareNodeNames(nameA, nameB));
    const gid = `az:${az}`;
    const col = collapsedGroups.has(gid);
    html += `<div class="tree-group" onclick="toggleGroup('${escAttr(gid)}')">
      <span class="tree-expand">${col ? '▶' : '▼'}</span>
      🌐 ${esc(az)}
      <span class="tree-count">${entries.length}</span>
    </div>`;
    if (!col) for (const [name, nd] of entries) html += treeItemHtml(name, nd);
  }

  if (other.length) {
    const sortedOther = [...other].sort(([nameA], [nameB]) => compareNodeNames(nameA, nameB));
    const col = collapsedGroups.has('other');
    html += `<div class="tree-group" onclick="toggleGroup('other')">
      <span class="tree-expand">${col ? '▶' : '▼'}</span> ⚙️ Other Nodes
      <span class="tree-count">${sortedOther.length}</span>
    </div>`;
    if (!col) for (const [name, nd] of sortedOther) html += treeItemHtml(name, nd);
  }

  if (!compute.length && !other.length)
    html = `<div class="tree-group" style="border-top:none;cursor:default">No nodes loaded</div>`;

  document.getElementById('tree').innerHTML = html;
}

function toggleGroup(groupId) {
  if (collapsedGroups.has(groupId)) collapsedGroups.delete(groupId);
  else collapsedGroups.add(groupId);
  rebuildSidebar();
}

function updateSidebarRow(name) {
  const nd = nodes[name]; if (!nd) return;
  const el = document.querySelector(`[data-node="${escAttr(name)}"]`);
  if (!el) { rebuildSidebar(); return; }
  const tmp = document.createElement('div');
  tmp.innerHTML = treeItemHtml(name, nd);
  el.replaceWith(tmp.firstElementChild);
}

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

// ════════════════════════════════════════════════════════════════════════════
// § SUMMARY TAB
// ════════════════════════════════════════════════════════════════════════════

function renderSummaryTab(nd) {
  let h = '';
  const det = nodeDetailCache[nd.k8s_name] || {};
  const detLoading = det.loading;

  // Concept map banner
  h += `<div class="concept-map">
    <strong>💡 VMware → OpenStack/K8s quick reference:</strong>
    <div class="cm-grid">
      <span class="cm-from">ESXi Host</span><span class="cm-arrow">→</span><span class="cm-to">Nova Hypervisor + K8s Node</span>
      <span class="cm-from">Enter Maintenance Mode</span><span class="cm-arrow">→</span><span class="cm-to">Disable Nova + Cordon + Drain pods</span>
      <span class="cm-from">vMotion</span><span class="cm-arrow">→</span><span class="cm-to">Nova Live Migration</span>
      <span class="cm-from">VM</span><span class="cm-arrow">→</span><span class="cm-to">Nova Instance (QEMU/KVM) or K8s Pod</span>
    </div>
    <div style="margin-top:5px;font-size:10px">
      <a href="#" onclick="showTab('configure');return false" style="color:var(--blue)">Full VMware reference →</a>
    </div>
  </div>`;

  if (detLoading) {
    h += `<div class="runtime-note" style="margin-bottom:10px"><span class="spinner">⟳</span> Node details are still refreshing. Inventory status is loaded, but hardware, Nova, and Kubernetes detail may still be filling in.</div>`;
  }

  // etcd quorum block
  if (nd.is_etcd) {
    const peers   = Object.values(nodes).filter(n => n.is_etcd);
    const total   = peers.length;
    const quorum  = Math.floor(total / 2) + 1;
    if (peers.some(n => n.etcd_checking)) {
      h += `<div class="etcd-alert checking"><span class="spinner">⟳</span> Checking etcd health on all ${total} etcd nodes…</div>`;
    } else {
      const checked  = peers.filter(n => n.etcd_healthy !== null && n.etcd_healthy !== undefined);
      if (!checked.length) {
        h += `<div class="etcd-alert">⚠ etcd node — health unknown (checked before reboot)</div>`;
      } else {
        const healthy   = peers.filter(n => n.etcd_healthy === true).length;
        const remaining = healthy - (nd.etcd_healthy === true ? 1 : 0);
        const atRisk    = remaining < quorum;
        const peerList  = peers.map(p =>
          `${esc(p.k8s_name)} ${p.etcd_healthy===true?'✓':p.etcd_healthy===false?'✗':'?'}`
        ).join('  ·  ');
        h += `<div class="etcd-alert ${atRisk?'danger':''}">
          ${atRisk
            ? `⚠ ETCD QUORUM RISK — ${healthy}/${total} healthy, reboot would leave ${remaining} (need ${quorum})`
            : `⚠ etcd — ${healthy}/${total} healthy · safe to work on ${total - quorum} at a time`}
          <div class="etcd-peers">${peerList}</div>
        </div>`;
      }
    }
  }

  // Live downtime counter (still shown in summary alongside the tasks panel)
  if (nd.phase === 'rebooting' && nd.reboot_start) {
    const elapsed = Math.floor(Date.now() / 1000 - nd.reboot_start);
    h += `<div class="downtime-counter">⏱ Downtime: ${elapsed}s</div>`;
  }

  // Post-workflow hints (steps themselves are in the Tasks panel)
  if (nd.phase === 'idle' && nd.steps?.length) {
    if (nd.reboot_downtime != null)
      h += `<div class="reboot-complete">✓ Reboot complete — total downtime: ${Math.round(nd.reboot_downtime)}s</div>`;
    if (nd.k8s_cordoned || nd.compute_status === 'disabled')
      h += `<div class="idle-hint">Node is drained — click <strong>Drain (Undrain)</strong> to re-enable.</div>`;
  }

  // ── Status / resource cards ───────────────────────────────────────────────
  const k8sd = det.k8s  || {};
  const novd = det.nova || {};
  const hwd  = det.hw   || {};

  function pb(label, used, total, unit, warnPct = 70, critPct = 90) {
    if (used == null || total == null || total === 0) return '';
    const pct = Math.round(used / total * 100);
    const cls = pct >= critPct ? 'crit' : pct >= warnPct ? 'warn' : '';
    const usedFmt  = unit === 'GB' ? `${(used/1024).toFixed(0)} GB`  : unit === 'MB' ? `${(used/1024).toFixed(0)} GB`  : used;
    const totalFmt = unit === 'GB' ? `${(total/1024).toFixed(0)} GB` : unit === 'MB' ? `${(total/1024).toFixed(0)} GB` : total;
    return `<div class="pbw"><div class="pb-label"><span>${esc(label)}</span><span>${usedFmt} / ${totalFmt} (${pct}%)</span></div>
      <div class="pb-track"><div class="pb-fill ${cls}" style="width:${pct}%"></div></div></div>`;
  }
  function pbPct(label, pct, extra = '') {
    if (pct == null) return '';
    const cls = pct >= 90 ? 'crit' : pct >= 70 ? 'warn' : '';
    return `<div class="pbw"><div class="pb-label"><span>${esc(label)}</span><span>${pct}%${extra ? ' · ' + extra : ''}</span></div>
      <div class="pb-track"><div class="pb-fill ${cls}" style="width:${pct}%"></div></div></div>`;
  }

  h += `<div class="summary-grid">`;

  // ── Nova Compute card ────────────────────────────────────────────────────
  if (nd.is_compute) {
    const nc = { up:'green', disabled:'yellow', down:'red' }[nd.compute_status] || 'gray';
    const nl = { up:'up · enabled', disabled:'disabled', down:'DOWN' }[nd.compute_status] || '…';
    const v  = nd.vm_count      != null ? nd.vm_count      : '…';
    const a  = nd.amphora_count != null ? nd.amphora_count : '…';
    const vcpuUsed  = novd.vcpus_used;
    const vcpuTotal = novd.vcpus;
    const ramUsed   = novd.memory_mb_used;
    const ramTotal  = novd.memory_mb;
    const vcpuPct   = vcpuTotal ? Math.round(vcpuUsed / vcpuTotal * 100) : null;
    const ramPct    = ramTotal  ? Math.round(ramUsed  / ramTotal  * 100) : null;
    const vcpuCls   = vcpuPct >= 90 ? 'crit' : vcpuPct >= 70 ? 'warn' : '';
    const ramCls    = ramPct  >= 90 ? 'crit' : ramPct  >= 70 ? 'warn' : '';
    h += `<div class="card">
      <div class="card-title">Nova Compute <span class="hint">VMware Cluster</span></div>
      <div class="card-body">
        <div class="mrow"><span class="ml">Service state</span><span class="mv ${nc}">${esc(nl)}</span></div>
        <div class="mrow"><span class="ml">Instances <span class="hint">VMs</span></span><span class="mv">${v}</span></div>
        <div class="mrow"><span class="ml">Amphora LBs <span class="hint">NSX LB</span></span><span class="mv ${a > 0 ? 'yellow' : ''}">${a}</span></div>
        ${vcpuTotal != null ? `<div class="mrow"><span class="ml">vCPUs</span><span class="mv">${vcpuUsed ?? '…'} / ${vcpuTotal}</span></div>` : detLoading ? `<div class="mrow"><span class="ml">vCPUs</span><span class="mv dim"><span class="spinner">⟳</span></span></div>` : ''}
        ${ramTotal  != null ? `<div class="mrow"><span class="ml">RAM</span><span class="mv">${ramUsed != null ? Math.round(ramUsed/1024) : '…'} / ${Math.round(ramTotal/1024)} GB</span></div>` : ''}
        ${vcpuTotal != null && vcpuPct != null ? `<div class="pbw"><div class="pb-label"><span>vCPU allocation</span><span>${vcpuPct}%</span></div><div class="pb-track"><div class="pb-fill ${vcpuCls}" style="width:${vcpuPct}%"></div></div></div>` : ''}
        ${ramTotal  != null && ramPct  != null ? `<div class="pbw"><div class="pb-label"><span>RAM allocation</span><span>${ramPct}%</span></div><div class="pb-track"><div class="pb-fill ${ramCls}"  style="width:${ramPct}%"></div></div></div>` : ''}
      </div>
    </div>`;
  }

  // ── Kubernetes Node card ─────────────────────────────────────────────────
  {
    const rc = nd.k8s_cordoned ? 'gray' : nd.k8s_ready ? 'green' : 'red';
    const rl = nd.k8s_cordoned ? 'Cordoned' : nd.k8s_ready ? 'Ready' : 'Not Ready';
    const podCount = k8sd.pod_count;
    const podCap   = k8sd.pods_allocatable ? parseInt(k8sd.pods_allocatable) : null;
    const podPct   = podCount != null && podCap ? Math.round(podCount / podCap * 100) : null;
    const podCls   = podPct >= 90 ? 'crit' : podPct >= 70 ? 'warn' : '';
    const roles    = (k8sd.roles || []).join(', ') || '—';
    h += `<div class="card">
      <div class="card-title">Kubernetes Node <span class="hint">ESXi Host</span></div>
      <div class="card-body">
        <div class="mrow"><span class="ml">Status</span><span class="mv ${rc}">${rl}</span></div>
        <div class="mrow"><span class="ml">Cordoned <span class="hint">In Maintenance</span></span><span class="mv ${nd.k8s_cordoned?'yellow':'green'}">${nd.k8s_cordoned?'Yes':'No'}</span></div>
        <div class="mrow"><span class="ml">Role</span><span class="mv">${esc(roles)}</span></div>
        ${nd.is_etcd ? `<div class="mrow"><span class="ml">etcd</span><span class="mv red">member</span></div>` : ''}
        ${podCount != null ? `<div class="mrow"><span class="ml">Running pods</span><span class="mv">${podCount}${podCap ? ' / ' + podCap : ''}</span></div>` : detLoading ? `<div class="mrow"><span class="ml">Pods</span><span class="mv dim"><span class="spinner">⟳</span></span></div>` : ''}
        ${k8sd.kubelet_version   ? `<div class="mrow"><span class="ml">Kubelet</span><span class="mv dim">${esc(k8sd.kubelet_version)}</span></div>` : ''}
        ${k8sd.container_runtime ? `<div class="mrow"><span class="ml">Runtime</span><span class="mv dim">${esc(k8sd.container_runtime)}</span></div>` : ''}
        ${podPct != null ? `<div class="pbw"><div class="pb-label"><span>Pod capacity</span><span>${podPct}%</span></div><div class="pb-track"><div class="pb-fill ${podCls}" style="width:${podPct}%"></div></div></div>` : ''}
        ${k8sd.error ? `<div class="err-chip" title="${esc(k8sd.error)}">⚠ K8s API: ${esc(k8sd.error.length > 60 ? k8sd.error.slice(0,60)+'…' : k8sd.error)}</div>` : ''}
      </div>
    </div>`;
  }

  // ── Hardware / Host Info card ────────────────────────────────────────────
  {
    // Chassis
    const vendor  = hwd.vendor  || null;
    const product = hwd.product || null;
    const chassis = (vendor && product) ? `${vendor} ${product}` : (vendor || product || null);

    // CPU — prefer host-agent data, fall back to Nova cpu_info
    const cpuInfo   = novd.cpu_info || {};
    const novaTopo  = cpuInfo.topology || {};
    const cpuModel  = hwd.cpu_model || cpuInfo.model || cpuInfo.vendor || null;
    const sockets   = hwd.cpu_sockets         || novaTopo.sockets || null;
    const coresPerS = hwd.cpu_cores_per_socket || novaTopo.cores   || null;
    const threadsPerC = hwd.cpu_threads_per_core || (novaTopo.threads > 1 ? novaTopo.threads : null);
    const totalCores   = sockets && coresPerS ? sockets * coresPerS : null;
    const totalThreads = totalCores && threadsPerC ? totalCores * threadsPerC : null;

    // RAM
    const ramGb   = hwd.ram_total_gb || (k8sd.memory_capacity_kb ? Math.round(k8sd.memory_capacity_kb / 1024 / 1024) : null);
    const ramType = hwd.ram_type     || null;
    const ramSpeed = hwd.ram_speed   || null;
    const ramMfr  = hwd.ram_manufacturer || null;
    const ramSlots = hwd.ram_slots_used  || null;
    let ramStr = ramGb ? `${ramGb} GB` : null;
    if (ramStr && ramType)  ramStr += ` ${ramType}`;
    if (ramStr && ramSpeed) ramStr += ` @ ${ramSpeed}`;

    h += `<div class="card">
      <div class="card-title">Hardware / Host Info</div>
      <div class="card-body">
        ${chassis     ? `<div class="mrow"><span class="ml">System</span><span class="mv" style="font-size:11px">${esc(chassis)}</span></div>` : detLoading ? `<div class="mrow"><span class="ml">System</span><span class="mv dim"><span class="spinner">⟳</span></span></div>` : ''}
        ${hwd.bios_version ? `<div class="mrow"><span class="ml">BIOS</span><span class="mv dim" style="font-size:10px">${esc(hwd.bios_version)}</span></div>` : ''}
        ${cpuModel    ? `<div class="mrow"><span class="ml">CPU</span><span class="mv" style="font-size:11px">${esc(cpuModel)}</span></div>` : detLoading && !chassis ? '' : ''}
        ${totalCores  ? `<div class="mrow"><span class="ml">CPU topology</span><span class="mv">${sockets}s × ${coresPerS}c${threadsPerC > 1 ? ' × ' + threadsPerC + 't' : ''} = ${totalThreads || totalCores} logical CPUs</span></div>` : coresPerS ? `<div class="mrow"><span class="ml">CPU cores</span><span class="mv">${coresPerS}</span></div>` : ''}
        ${novd.vcpus  ? `<div class="mrow"><span class="ml">vCPUs (Nova)</span><span class="mv">${novd.vcpus}</span></div>` : ''}
        ${ramStr      ? `<div class="mrow"><span class="ml">RAM</span><span class="mv">${esc(ramStr)}${ramSlots ? `<span style="color:var(--dim);font-size:10px"> (${ramSlots} DIMMs${ramMfr ? ', ' + ramMfr : ''})</span>` : ''}</span></div>` : detLoading ? `<div class="mrow"><span class="ml">RAM</span><span class="mv dim"><span class="spinner">⟳</span></span></div>` : ''}
        ${k8sd.os_image ? `<div class="mrow"><span class="ml">OS</span><span class="mv dim" style="font-size:10px">${esc(k8sd.os_image)}</span></div>` : ''}
        ${hwd.kernel_version || nd.kernel_version || k8sd.kernel_version ? `<div class="mrow"><span class="ml">Kernel</span><span class="mv dim" style="font-size:10px">${esc(hwd.kernel_version || nd.kernel_version || k8sd.kernel_version)}</span></div>` : ''}
        ${hwd.architecture || k8sd.architecture ? `<div class="mrow"><span class="ml">Architecture</span><span class="mv">${esc(hwd.architecture || k8sd.architecture)}</span></div>` : ''}
        ${hwd.uptime || nd.uptime ? `<div class="mrow"><span class="ml">Uptime</span><span class="mv">${esc(hwd.uptime || nd.uptime)}</span></div>` : ''}
        ${hwd.error ? `<div class="err-chip" title="${esc(hwd.error)}">⚠ Host detail: ${esc(hwd.error.length > 60 ? hwd.error.slice(0,60)+'…' : hwd.error)}</div>` : ''}
      </div>
    </div>`;
  }

  // ── Availability Zone / Aggregates card ─────────────────────────────────
  if (nd.is_compute && (nd.availability_zone || nd.aggregates?.length)) {
    h += `<div class="card">
      <div class="card-title">Placement</div>
      <div class="card-body">
        ${nd.availability_zone ? `<div class="mrow"><span class="ml">Availability Zone</span><span class="mv">${esc(nd.availability_zone)}</span></div>` : ''}
        ${nd.hypervisor        ? `<div class="mrow"><span class="ml">Hypervisor</span><span class="mv dim" style="font-size:10px">${esc(nd.hypervisor)}</span></div>` : ''}
        ${nd.aggregates?.length ? `<div class="mrow"><span class="ml">Aggregates</span><span class="mv" style="font-size:10px">${nd.aggregates.map(a => `<span class="tree-badge agg">${esc(a)}</span>`).join(' ')}</span></div>` : ''}
      </div>
    </div>`;
  }

  h += `</div>`;

  const labels = Object.entries(k8sd.labels || {});
  const annotations = Object.entries(k8sd.annotations || {});
  const renderKvRows = (items, emptyLabel) => {
    if (!items.length) {
      return `<div style="color:var(--dim);font-size:12px">${emptyLabel}</div>`;
    }
    return `<div style="max-height:240px;overflow:auto;border:1px solid #eef2f5;border-radius:3px">
      <table class="data-table" style="margin:0">
        <tbody>
          ${items.map(([key, value]) => `<tr>
            <td style="width:42%;font-family:monospace;font-size:10px;vertical-align:top">${esc(key)}</td>
            <td style="font-size:10px;vertical-align:top;word-break:break-all">${esc(value)}</td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
  };

  h += `<div class="summary-grid">`;
  h += `<div class="card">
    <div class="card-title">Node Labels</div>
    <div class="card-body">
      ${renderKvRows(labels, 'No labels reported.')}
    </div>
  </div>`;
  h += `<div class="card">
    <div class="card-title">Node Annotations</div>
    <div class="card-body">
      ${renderKvRows(annotations, 'No annotations reported.')}
    </div>
  </div>`;
  h += `</div>`;

  if (nd.is_compute && nd.phase === 'idle' && (!nd.steps || !nd.steps.length)) {
    h += hasOpenStackAuth()
      ? `<div class="idle-hint">Click <strong>Evacuate</strong> to begin the full evacuation workflow. Use the <strong>Instances</strong> and <strong>Pods</strong> tabs to inspect workloads on this hypervisor.</div>`
      : `<div class="idle-hint">OpenStack credentials are required for instance evacuation workflows. Use the <strong>Instances</strong> and <strong>Pods</strong> tabs to inspect workloads on this hypervisor.</div>`;
    if (nd.k8s_cordoned || nd.compute_status === 'disabled')
      h += `<div class="idle-hint">Node is partially drained — click <strong>Drain (Undrain)</strong> to re-enable.</div>`;
  }

  if (!nd.is_compute && nd.phase === 'idle' && (!nd.steps || !nd.steps.length)) {
    h += `<div style="color:var(--dim);font-size:12px;padding:4px 0">Non-compute node — no OpenStack evacuation required.</div>`;
    if (nd.k8s_cordoned)
      h += `<div class="idle-hint">Node is cordoned — click <strong>Drain (Undrain)</strong> to uncordon.</div>`;
  }

  document.getElementById('summary-content').innerHTML = h;
}
