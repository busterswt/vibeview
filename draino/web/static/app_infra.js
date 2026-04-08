'use strict';

// ════════════════════════════════════════════════════════════════════════════
// § VIEW SWITCHING (top nav)
// ════════════════════════════════════════════════════════════════════════════

function switchView(name) {
  activeView = name;

  // Top nav highlight
  document.querySelectorAll('.top-nav a').forEach(a => {
    a.classList.toggle('active', a.dataset.view === name);
  });

  // Show / hide body views
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById(`view-${name}`).classList.add('active');

  // Breadcrumb root label + actions
  const bcRoot = document.getElementById('bc-root');
  const bcSep  = document.getElementById('bc-sep');
  const bcNode = document.getElementById('bc-node');
  document.getElementById('bc-infra-actions').style.display = name === 'infrastructure' ? '' : 'none';
  document.getElementById('bc-k8s-actions').style.display   = name === 'kubernetes'     ? '' : 'none';
  document.getElementById('bc-net-actions').style.display   = name === 'networking'     ? '' : 'none';
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
        ? 'Networking'
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
    if (name === 'networking' && !netState.data && !netState.loading) loadNetworks();
    if (name === 'storage'    && !volState.data && !volState.loading) loadVolumes();
    // Restore network detail panel visibility when returning
    if (name === 'networking') {
      const det = document.getElementById('net-detail-wrap');
      if (selectedNetwork && netDetailState.data) det.classList.add('open');
    }
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
  showPods      = false;
  lastPodsCache = null;

  // Highlight new item directly (avoids rebuilding whole sidebar)
  const el = document.querySelector(`[data-node="${escAttr(name)}"]`);
  if (el) el.classList.add('selected');

  syncPodsButton();

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
  }
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

async function loadNodeMetrics(name, force = false) {
  nodeMetricsCache[name] = { loading: true, current: null, history: [], error: null };
  if (selectedNode === name && activeTab === 'monitor') renderNodeMonitorTab(nodes[name]);
  try {
    const qs = force ? '?refresh=1' : '';
    const resp = await fetch(`/api/nodes/${encodeURIComponent(name)}/metrics${qs}`);
    const json = await resp.json();
    nodeMetricsCache[name] = {
      loading: false,
      current: json.current || null,
      history: json.history || [],
      error: json.error || null,
    };
  } catch (e) {
    nodeMetricsCache[name] = { loading: false, current: null, history: [], error: String(e) };
  }
  if (selectedNode === name && activeTab === 'monitor') renderNodeMonitorTab(nodes[name]);
}

function shouldLoadNodeMetrics(name) {
  const detail = nodeMetricsCache[name];
  return !detail || detail.error;
}

function refreshSelectedNodeMetrics(force = false) {
  if (activeView !== 'infrastructure' || activeTab !== 'monitor' || !selectedNode || !nodes[selectedNode]) return;
  loadNodeMetrics(selectedNode, force);
}

function enabledNetStatsSet(nodeName) {
  if (!nodeNetStatsEnabled[nodeName]) nodeNetStatsEnabled[nodeName] = new Set();
  return nodeNetStatsEnabled[nodeName];
}

async function loadNodeNetworkStats(name) {
  nodeNetStatsCache[name] = {
    ...(nodeNetStatsCache[name] || {}),
    loading: true,
    error: null,
  };
  if (selectedNode === name && activeTab === 'monitor') renderNodeMonitorTab(nodes[name]);
  try {
    const resp = await fetch(`/api/nodes/${encodeURIComponent(name)}/network-stats`);
    const json = await resp.json();
    nodeNetStatsCache[name] = {
      loading: false,
      interfaces: json.interfaces || [],
      error: json.error || null,
      fetchedAt: new Date(),
    };
  } catch (e) {
    nodeNetStatsCache[name] = {
      loading: false,
      interfaces: [],
      error: String(e),
      fetchedAt: new Date(),
    };
  }
  if (selectedNode === name && activeTab === 'monitor') renderNodeMonitorTab(nodes[name]);
}

function refreshSelectedNodeNetworkStats() {
  if (activeView !== 'infrastructure' || activeTab !== 'monitor' || !selectedNode || !nodes[selectedNode]) return;
  const enabled = enabledNetStatsSet(selectedNode);
  if (!enabled.size) return;
  loadNodeNetworkStats(selectedNode);
}

function toggleNodeInterfaceStats(nodeName, ifaceName, enabled) {
  const set = enabledNetStatsSet(nodeName);
  if (enabled) {
    set.add(ifaceName);
    loadNodeNetworkStats(nodeName);
  } else {
    set.delete(ifaceName);
  }
  if (selectedNode === nodeName && activeTab === 'monitor') renderNodeMonitorTab(nodes[nodeName]);
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
  if (nd.is_etcd) badges.push(`<span class="badge red">etcd</span>`);
  if (nd.hosts_mariadb) badges.push(`<span class="badge magenta">MariaDB</span>`);
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
  ['summary','instances','monitor','configure'].forEach(t => {
    document.getElementById(`tab-${t}`).style.display = t === name ? '' : 'none';
    document.getElementById(`tab-btn-${t}`).className = 'tab' + (t === name ? ' active' : '');
  });
  if (name === 'monitor' && selectedNode && shouldLoadNodeMetrics(selectedNode)) loadNodeMetrics(selectedNode);
  if (name === 'monitor' && selectedNode) _ensureNetworkDataLoaded(selectedNode);
  if (selectedNode && nodes[selectedNode]) renderActiveTab(nodes[selectedNode]);
  if (name === 'configure' && selectedNode) _ensureNetworkDataLoaded(selectedNode);
}

function renderActiveTab(nd) {
  if (activeTab === 'summary')   renderSummaryTab(nd);
  if (activeTab === 'instances') renderInstancesTab(nd);
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
  const det  = nodeDetailCache[nd.k8s_name] || {};
  const k8sd = det.k8s  || {};
  const novd = det.nova || {};
  const hwd  = det.hw   || {};
  const detLoading = det.loading;

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
    h += `<div class="idle-hint">Click <strong>Evacuate</strong> to begin the full evacuation workflow. See the <strong>Instances &amp; Pods</strong> tab for workloads on this hypervisor.</div>`;
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

// ════════════════════════════════════════════════════════════════════════════
// § NODE MONITOR TAB
// ════════════════════════════════════════════════════════════════════════════

function renderNodeMonitorTab(nd) {
  const wrap = document.getElementById('node-monitor-content');
  const tabBody = document.getElementById('tab-body');
  const priorScrollTop = tabBody ? tabBody.scrollTop : 0;
  const data = nodeMetricsCache[nd.k8s_name] || { loading: true, current: null, history: [], error: null };
  const current = data.current || {};
  const filesystems = current.filesystems || [];
  const rootFs = filesystems.find(fs => fs.mount === '/') || null;
  const ifaceCache = nodeNetworkCache[nd.k8s_name] || {};
  const ifaces = (ifaceCache.ifaces || []).filter(i => i.type === 'physical' || i.type === 'bond');
  const enabledStats = enabledNetStatsSet(nd.k8s_name);
  const netStats = nodeNetStatsCache[nd.k8s_name] || { loading: false, interfaces: [], error: null, fetchedAt: null };
  const netStatsByName = Object.fromEntries((netStats.interfaces || []).map(item => [item.name, item]));
  const bondByMember = {};
  for (const iface of ifaces) {
    if (iface.type !== 'bond') continue;
    for (const member of (iface.members || [])) bondByMember[member] = iface.name;
  }

  const fsRows = filesystems.length
    ? filesystems.map((fs) => `
      <tr>
        <td>${esc(fs.mount)}</td>
        <td>${fmtKiB(fs.total_kb)}</td>
        <td>${fmtKiB(fs.available_kb)}</td>
        <td>${fs.used_percent != null ? `${fs.used_percent}%` : '—'}</td>
      </tr>
    `).join('')
    : `<tr><td colspan="4" style="color:var(--dim)">No filesystem data reported.</td></tr>`;

  let h = `<div class="tab-section-title" style="margin-bottom:10px"><span>Node Metrics</span></div>`;
  if (data.error) {
    h += `<div class="etcd-alert danger">Host metrics error: ${esc(data.error)}</div>`;
  }
  h += `<div class="summary-grid">
    <div class="card">
      <div class="card-title">Host Load</div>
      <div class="card-body">
        <div class="mrow"><span class="ml">Load average</span><span class="mv">${
          current.load1 != null && current.load5 != null && current.load15 != null
            ? `${current.load1.toFixed(2)} · ${current.load5.toFixed(2)} · ${current.load15.toFixed(2)}`
            : '—'
        }</span></div>
        <div class="mrow"><span class="ml">CPU count</span><span class="mv">${current.cpu_count ?? '—'}</span></div>
        <div class="mrow"><span class="ml">Uptime</span><span class="mv">${fmtSeconds(current.uptime_seconds)}</span></div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">Memory</div>
      <div class="card-body">
        <div class="mrow"><span class="ml">Used</span><span class="mv">${fmtKiB(current.memory_used_kb)}</span></div>
        <div class="mrow"><span class="ml">Available</span><span class="mv">${fmtKiB(current.memory_available_kb)}</span></div>
        <div class="mrow"><span class="ml">Total</span><span class="mv">${fmtKiB(current.memory_total_kb)}</span></div>
        <div class="mrow"><span class="ml">Pressure</span><span class="mv">${
          current.memory_used_percent != null ? `${current.memory_used_percent.toFixed(1)}%` : '—'
        }</span></div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">Local Disk</div>
      <div class="card-body">
        <div class="mrow"><span class="ml">Root free</span><span class="mv">${rootFs ? fmtKiB(rootFs.available_kb) : '—'}</span></div>
        <div class="mrow"><span class="ml">Root used</span><span class="mv">${rootFs && rootFs.used_percent != null ? `${rootFs.used_percent}%` : '—'}</span></div>
        <div class="mrow"><span class="ml">Tracked filesystems</span><span class="mv">${filesystems.length || '—'}</span></div>
      </div>
    </div>
  </div>`;

  h += `<div class="card">
    <div class="card-title">Filesystem Free Space</div>
    <div class="card-body">
      <table class="data-table">
        <thead>
          <tr><th>Mount</th><th>Total</th><th>Free</th><th>Used</th></tr>
        </thead>
        <tbody>${fsRows}</tbody>
      </table>
    </div>
  </div>`;

  h += `<div class="card" style="margin-top:12px">
    <div class="card-title">Network Interfaces</div>
    <div class="card-body">
      <div style="font-size:11px;color:var(--dim);margin-bottom:8px">
        Toggle live throughput per interface. Rates are sampled from kernel byte counters and shown only for enabled interfaces.
      </div>
      ${ifaceCache.ifacesLoading ? `<div class="runtime-note"><span class="spinner">⟳</span> Loading interface inventory…</div>` : ''}
      ${ifaceCache.ifacesError ? `<div class="etcd-alert danger">Interface inventory error: ${esc(ifaceCache.ifacesError)}</div>` : ''}
      ${netStats.error ? `<div class="etcd-alert danger">Live stats error: ${esc(netStats.error)}</div>` : ''}
      ${ifaces.length ? `
        <table class="data-table">
          <thead>
            <tr><th>Interface</th><th>Type</th><th>Status</th><th>Speed</th><th>Duplex</th><th>Bond Member</th><th>IPs</th><th>Live Stats</th><th>RX</th><th>TX</th></tr>
          </thead>
          <tbody>
            ${ifaces.map((iface) => {
              const checked = enabledStats.has(iface.name);
              const live = netStatsByName[iface.name] || {};
              const ipSummary = [...(iface.ipv4 || []), ...(iface.ipv6 || [])].join(', ');
              const bondName = iface.type === 'physical' ? (bondByMember[iface.name] || '') : '';
              const bondCell = bondName
                ? `<span class="tree-badge" style="margin-right:3px;font-family:monospace">${esc(bondName)}</span>`
                : '<span style="color:var(--dim)">—</span>';
              return `<tr>
                <td><strong>${esc(iface.name)}</strong></td>
                <td><span class="nic-type ${escAttr(iface.type || 'physical')}">${esc(iface.type || 'physical')}</span></td>
                <td>${esc(iface.status || 'unknown')}</td>
                <td>${esc(iface.speed || '—')}</td>
                <td>${esc(iface.duplex || '—')}</td>
                <td>${bondCell}</td>
                <td style="font-size:11px;color:var(--dim)">${esc(ipSummary || '—')}</td>
                <td>
                  <label style="display:inline-flex;align-items:center;gap:6px;cursor:pointer">
                    <input type="checkbox" ${checked ? 'checked' : ''} onchange="toggleNodeInterfaceStats('${escAttr(nd.k8s_name)}','${escAttr(iface.name)}', this.checked)">
                    <span>${checked ? 'on' : 'off'}</span>
                  </label>
                </td>
                <td>${checked ? esc(fmtNetRate(live.rx_bytes_per_second)) : '<span style="color:var(--dim)">off</span>'}</td>
                <td>${checked ? esc(fmtNetRate(live.tx_bytes_per_second)) : '<span style="color:var(--dim)">off</span>'}</td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      ` : (!ifaceCache.ifacesLoading ? `<div style="color:var(--dim)">No physical or bond interfaces found.</div>` : '')}
      ${netStats.fetchedAt && enabledStats.size ? `<div class="runtime-note">Updated ${_fmtTime(netStats.fetchedAt)}</div>` : ''}
    </div>
  </div>`;

  wrap.innerHTML = h;
  if (tabBody) tabBody.scrollTop = priorScrollTop;
}

// ════════════════════════════════════════════════════════════════════════════
// § INSTANCES & PODS TAB
// ════════════════════════════════════════════════════════════════════════════

function renderInstancesTab(nd) {
  const drained = nd.k8s_cordoned || nd.compute_status === 'disabled';
  const expandedId = expandedInstanceIdByNode[nd.k8s_name] || '';
  const instanceRefreshPill = nd.preflight_loading
    ? `<span class="node-refresh-indicator active" title="Refreshing VM list">
        <span class="spinner">⟳</span>
        <span>Refreshing</span>
      </span>`
    : '<span class="node-refresh-indicator instances-refresh-slot" aria-hidden="true"></span>';
  let h = `<div class="inst-toolbar">
    <button class="btn primary" onclick="actionEvacuate()">▶ Evacuate <span class="hint">Enter Maintenance</span></button>
    <button class="btn ${drained?'warning':''}" onclick="actionDrainOrUndrain()">${drained ? '↺ Undrain' : '▽ Drain'}</button>
    <button class="btn" id="inst-pods-btn" onclick="actionPodsInline()">${podsButtonText()}</button>
    <span style="flex:1"></span>
    <input class="toolbar-filter" type="text" placeholder="Filter instances…" id="inst-filter" oninput="filterInstTable()">
  </div>`;

  // Live migration instances
  if (nd.instances?.length) {
    h += `<div class="tab-section-title"><span>Nova Instances — Migration Status <span class="hint">vMotion Progress</span></span>${instanceRefreshPill}</div>
    <table class="data-table" id="inst-data-table">
      <thead><tr>
        <th>Name <span class="hint">VM Name</span></th><th>Type</th>
        <th>Nova State <span class="hint">Power State</span></th>
        <th>Operation <span class="hint">vMotion</span></th>
      </tr></thead><tbody>`;
    for (const inst of nd.instances) {
      const tp   = inst.is_amphora ? `<span class="tag-amp">Amphora LB</span>` : 'VM';
      const op   = inst.is_amphora ? (inst.failover_status || 'pending') : (inst.migration_status || 'pending');
      const phc  = { complete:'ph-complete', failed:'ph-failed', migrating:'ph-migrate', 'cold-migrating':'ph-migrate', confirming:'ph-migrate', failing_over:'ph-migrate' }[op] || 'ph-queued';
      const dotc = { ACTIVE:'green', ERROR:'red' }[inst.status] || 'gray';
      h += `<tr><td>${esc(inst.name)}</td><td>${tp}</td>
        <td><span class="sdot ${dotc}"></span>${esc(inst.status)}</td>
        <td><span class="ph-badge ${phc}">${esc(op)}</span></td></tr>`;
    }
      h += `</tbody></table>`;
  } else if (nd.is_compute) {
    h += `<div class="tab-section-title"><span>Nova Instances <span class="hint">VMs &amp; Templates</span></span>${instanceRefreshPill}</div>`;
    if (nd.preflight_loading && !nd.preflight_instances?.length) {
      h += `<div style="height:8px"></div>`;
    } else if (nd.preflight_instances?.length) {
      h += `<table class="data-table" id="inst-data-table">
        <thead><tr>
          <th>Name</th><th>Status</th><th>Type</th><th>vCPU</th><th>Memory</th><th>Storage <span class="hint">Datastore</span></th><th>Action</th>
        </tr></thead><tbody>`;
      for (const i of nd.preflight_instances) {
        const tp = i.is_amphora ? `<span class="tag-amp">Amphora LB</span>` : 'VM';
        const st = i.is_volume_backed ? `<span class="tag-vol">Volume</span>` : `<span class="tag-eph">Ephemeral</span>`;
        const vcpu = i.vcpus != null ? esc(String(i.vcpus)) : '—';
        const memory = i.ram_mb != null ? esc(`${Math.round(i.ram_mb / 1024)} GB`) : '—';
        const ms = instanceMigrateStates[i.id];
        const detailsLabel = expandedId === i.id ? '▾ Details' : '▸ Details';
        let action = '';
        const actions = [`<button class="btn" style="font-size:11px" onclick="toggleInstanceDetail('${escAttr(i.id)}')">${detailsLabel}</button>`];
        if (!i.is_amphora) {
          if (ms === 'migrating')
            actions.push(`<button class="btn" disabled style="font-size:11px"><span class="spinner">⟳</span> Migrating…</button>`);
          else if (ms === 'error')
            actions.push(`<button class="btn danger" style="font-size:11px" onclick="migrateInstance('${escAttr(i.id)}')">↺ Retry</button>`);
          else
            actions.push(`<button class="btn" style="font-size:11px" onclick="migrateInstance('${escAttr(i.id)}')">↗ Migrate</button>`);
        }
        action = `<div style="display:flex;gap:6px;flex-wrap:wrap">${actions.join('')}</div>`;
        h += `<tr><td>${esc(i.name)}</td><td><span class="sdot green"></span>${esc(i.status)}</td><td>${tp}</td><td>${vcpu}</td><td>${memory}</td><td>${st}</td><td>${action}</td></tr>`;
      }
      h += `</tbody></table>`;
      if (expandedId) h += renderInstanceDetailPanel(nd.k8s_name, expandedId);
    } else {
      h += `<div style="color:var(--dim);font-size:12px;padding:4px 0">No instances on this hypervisor.</div>`;
    }
  } else {
    h += `<div class="tab-section-title"><span>Nova Instances</span></div>
      <div style="color:var(--dim);font-size:12px;padding:4px 0">Non-compute node.</div>`;
  }

  // Pods section
  h += `<div class="tab-section-title" style="margin-top:10px">
    <span>Kubernetes Pods <span class="hint">Containerised Workloads</span></span>
  </div>
  <div id="pods-section">`;
  if (showPods && lastPodsCache?.node === nd.k8s_name) {
    h += buildPodsTableHtml(lastPodsCache.pods);
  } else if (showPods) {
    h += `<div style="color:var(--dim);font-size:12px"><span class="spinner">⟳</span> Fetching pods…</div>`;
  } else {
    h += `<div style="color:var(--dim);font-size:12px">Click <strong>Load Pods</strong> above to fetch current pods on this node.</div>`;
  }
  h += `</div>`;

  document.getElementById('instances-content').innerHTML = h;
}

function renderInstanceDetailPanel(nodeName, instanceId) {
  const cached = instanceDetailCache[instanceId];
  if (!cached || cached.loading) {
    return `<div class="card" style="margin-top:10px"><div class="card-title">Instance Detail</div><div class="card-body" style="color:var(--dim)"><span class="spinner">⟳</span> Loading instance detail…</div></div>`;
  }
  if (cached.error || !cached.data) {
    return `<div class="card" style="margin-top:10px"><div class="card-title">Instance Detail</div><div class="card-body"><div class="err-block">${esc(cached.error || 'Unknown error')}</div></div></div>`;
  }

  const inst = cached.data;
  const flavor = inst.flavor || {};
  const ports = inst.ports || [];
  let h = `<div class="card" style="margin-top:10px">
    <div class="card-title">Instance Detail</div>
    <div class="card-body">
      <div class="summary-grid">
        <div class="card">
          <div class="card-title">Nova</div>
          <div class="card-body">
            <div class="mrow"><span class="ml">Name</span><span class="mv">${esc(inst.name || instanceId)}</span></div>
            <div class="mrow"><span class="ml">UUID</span><span class="mv" style="font-family:monospace;font-size:11px">${esc(inst.id || instanceId)}</span></div>
            <div class="mrow"><span class="ml">Status</span><span class="mv">${esc(inst.status || 'UNKNOWN')}</span></div>
            <div class="mrow"><span class="ml">Host</span><span class="mv dim">${esc(inst.compute_host || '—')}</span></div>
            <div class="mrow"><span class="ml">AZ</span><span class="mv dim">${esc(inst.availability_zone || '—')}</span></div>
            <div class="mrow"><span class="ml">Task state</span><span class="mv dim">${esc(inst.task_state || '—')}</span></div>
            <div class="mrow"><span class="ml">Boot source</span><span class="mv">${inst.is_volume_backed ? 'Volume-backed' : 'Image / Ephemeral'}</span></div>
          </div>
        </div>
        <div class="card">
          <div class="card-title">Flavor</div>
          <div class="card-body">
            <div class="mrow"><span class="ml">Flavor</span><span class="mv">${esc(flavor.name || flavor.id || '—')}</span></div>
            <div class="mrow"><span class="ml">vCPU</span><span class="mv">${esc(flavor.vcpus ?? '—')}</span></div>
            <div class="mrow"><span class="ml">RAM</span><span class="mv">${flavor.ram_mb != null ? esc(`${flavor.ram_mb} MB`) : '—'}</span></div>
            <div class="mrow"><span class="ml">Disk</span><span class="mv">${flavor.disk_gb != null ? esc(`${flavor.disk_gb} GB`) : '—'}</span></div>
            <div class="mrow"><span class="ml">Ephemeral</span><span class="mv">${flavor.ephemeral_gb != null ? esc(`${flavor.ephemeral_gb} GB`) : '—'}</span></div>
            <div class="mrow"><span class="ml">Swap</span><span class="mv">${flavor.swap_mb != null ? esc(`${flavor.swap_mb} MB`) : '—'}</span></div>
          </div>
        </div>
      </div>`;
  if (inst.node_mismatch) {
    h += `<div class="err-block" style="margin-top:10px">Instance no longer appears to be on ${esc(nodeName)}. Current hypervisor: ${esc(inst.node_mismatch.actual_hypervisor || 'unknown')}.</div>`;
  }
  h += `<div class="tab-section-title" style="margin-top:10px"><span>Neutron Ports &amp; OVN</span></div>`;
  if (!ports.length) {
    h += `<div style="color:var(--dim);font-size:12px">No Neutron ports found for this instance.</div>`;
  } else {
    for (const port of ports) {
      const ovn = port.ovn || {};
      const ovnPort = ovn.port || {};
      h += `<div class="card" style="margin-top:10px">
        <div class="card-title">${esc(port.name || port.id || 'Port')}</div>
        <div class="card-body">
          <div class="summary-grid">
            <div class="card">
              <div class="card-title">Neutron Port</div>
              <div class="card-body">
                <div class="mrow"><span class="ml">Port ID</span><span class="mv" style="font-family:monospace;font-size:11px">${esc(port.id || '—')}</span></div>
                <div class="mrow"><span class="ml">Network</span><span class="mv">${esc(port.network_name || port.network_id || '—')}</span></div>
                <div class="mrow"><span class="ml">MAC</span><span class="mv" style="font-family:monospace">${esc(port.mac_address || '—')}</span></div>
                <div class="mrow"><span class="ml">Fixed IPs</span><span class="mv">${esc((port.fixed_ips || []).join(', ') || '—')}</span></div>
                <div class="mrow"><span class="ml">Floating IPs</span><span class="mv">${esc((port.floating_ips || []).join(', ') || '—')}</span></div>
                <div class="mrow"><span class="ml">Security Groups</span><span class="mv">${esc((port.security_groups || []).join(', ') || '—')}</span></div>
                <div class="mrow"><span class="ml">Device owner</span><span class="mv dim">${esc(port.device_owner || '—')}</span></div>
                <div class="mrow"><span class="ml">vNIC type</span><span class="mv dim">${esc(port.binding_vnic_type || '—')}</span></div>
              </div>
            </div>
            <div class="card">
              <div class="card-title">OVN Attachment</div>
              <div class="card-body">
                ${port.ovn_error ? `<div class="err-block">${esc(port.ovn_error)}</div>` : `
                  <div class="mrow"><span class="ml">Logical switch</span><span class="mv">${esc(ovn.ls_name || '—')}</span></div>
                  <div class="mrow"><span class="ml">Switch UUID</span><span class="mv" style="font-family:monospace;font-size:11px">${esc(ovn.ls_uuid || '—')}</span></div>
                  <div class="mrow"><span class="ml">Port type</span><span class="mv">${esc(ovnPort.type || 'normal')}</span></div>
                  <div class="mrow"><span class="ml">Router port</span><span class="mv dim">${esc(ovnPort.router_port || '—')}</span></div>
                  <div class="mrow"><span class="ml">Up</span><span class="mv">${ovnPort.up == null ? '—' : (ovnPort.up ? 'true' : 'false')}</span></div>
                  <div class="mrow"><span class="ml">Enabled</span><span class="mv">${ovnPort.enabled == null ? '—' : (ovnPort.enabled ? 'true' : 'false')}</span></div>
                  <div class="mrow"><span class="ml">Addresses</span><span class="mv" style="font-family:monospace;font-size:11px">${esc((ovnPort.addresses || []).join(', ') || '—')}</span></div>
                `}
              </div>
            </div>
          </div>
        </div>
      </div>`;
    }
  }
  h += `</div></div>`;
  return h;
}

function toggleInstanceDetail(instanceId) {
  if (!selectedNode) return;
  const nodeName = selectedNode;
  if (expandedInstanceIdByNode[nodeName] === instanceId) {
    delete expandedInstanceIdByNode[nodeName];
    renderInstancesTab(nodes[nodeName]);
    return;
  }
  expandedInstanceIdByNode[nodeName] = instanceId;
  renderInstancesTab(nodes[nodeName]);
  loadInstanceDetail(nodeName, instanceId);
}

async function loadInstanceDetail(nodeName, instanceId, force = false) {
  const cached = instanceDetailCache[instanceId];
  if (!force && (cached?.loading || cached?.data)) {
    if (selectedNode === nodeName && activeTab === 'instances' && nodes[nodeName]) renderInstancesTab(nodes[nodeName]);
    return;
  }
  instanceDetailCache[instanceId] = { loading: true, data: null, error: null };
  if (selectedNode === nodeName && activeTab === 'instances' && nodes[nodeName]) renderInstancesTab(nodes[nodeName]);
  try {
    const resp = await fetch(`/api/nodes/${encodeURIComponent(nodeName)}/instances/${encodeURIComponent(instanceId)}`);
    const json = await resp.json();
    if (!resp.ok || json.error) throw new Error(json.error || `HTTP ${resp.status}`);
    instanceDetailCache[instanceId] = { loading: false, data: json.instance, error: null };
  } catch (err) {
    instanceDetailCache[instanceId] = { loading: false, data: null, error: String(err) };
  }
  if (selectedNode === nodeName && activeTab === 'instances' && nodes[nodeName]) renderInstancesTab(nodes[nodeName]);
}

function filterInstTable() {
  const q   = (document.getElementById('inst-filter')?.value || '').toLowerCase();
  const tbl = document.getElementById('inst-data-table');
  if (!tbl) return;
  for (const row of tbl.tBodies[0].rows)
    row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
}

function buildPodsTableHtml(allPods) {
  if (allPods.length === 1 && allPods[0].error)
    return `<div class="err-block">${esc(allPods[0].error)}</div>`;
  if (!allPods.length)
    return `<div style="color:var(--dim);font-size:12px">No pods on this node.</div>`;

  const succeeded = allPods.filter(p => p.phase === 'Succeeded');
  const visible   = hideSucceeded ? allPods.filter(p => p.phase !== 'Succeeded') : allPods;
  const sorted    = [...visible].sort((a, b) => (a.namespace + a.name).localeCompare(b.namespace + b.name));

  let rows = '';
  for (const p of sorted) {
    const ready = `${p.ready_count}/${p.total_count}`;
    const phase = p.phase || 'Unknown';
    const age   = p.created_at ? podAge(p.created_at) : '?';
    const rCls  = p.ready_count === p.total_count ? 'green' : 'yellow';
    const pCls  = { Running:'green', Pending:'yellow', Succeeded:'gray' }[phase] || 'red';
    rows += `<tr>
      <td style="color:var(--dim)">${esc(p.namespace)}</td>
      <td>${esc(p.name)}</td>
      <td><span class="sdot ${rCls}"></span>${esc(ready)}</td>
      <td><span class="sdot ${pCls}"></span>${esc(phase)}</td>
      <td>${p.restarts}</td>
      <td style="color:var(--dim)">${esc(age)}</td>
    </tr>`;
  }

  let footer = `${visible.length} pod(s)`;
  let toggle = '';
  if (succeeded.length) {
    footer += hideSucceeded ? ` · ${succeeded.length} Succeeded hidden` : ` (${succeeded.length} Succeeded)`;
    toggle = ` <a href="#" onclick="toggleSucceeded();return false" style="color:var(--blue)">${hideSucceeded ? 'show' : 'hide'}</a>`;
  }
  return `<table class="data-table">
    <thead><tr><th>Namespace</th><th>Name</th><th>Ready</th><th>Status</th><th>Restarts</th><th>Age</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>
  <div style="font-size:11px;color:var(--dim);margin-top:4px">${footer}${toggle}</div>`;
}

function toggleSucceeded() {
  hideSucceeded = !hideSucceeded;
  if (lastPodsCache && selectedNode === lastPodsCache.node) {
    const sec = document.getElementById('pods-section');
    if (sec) sec.innerHTML = buildPodsTableHtml(lastPodsCache.pods);
  }
}

function podAge(iso) {
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d > 0 ? `${d}d ${h}h` : h > 0 ? `${h}h ${m}m` : `${m}m`;
}

// ════════════════════════════════════════════════════════════════════════════
// § CONFIGURE TAB
// ════════════════════════════════════════════════════════════════════════════

function renderConfigureTab(nd) {
  // If annotations are loaded but edit state isn't initialised for this node yet, init now
  const _nc = nodeNetworkCache[nd.k8s_name];
  if (_nc?.annotations && netEdit.node !== nd.k8s_name) _initNetEdit(nd.k8s_name);

  const managedNoSchedule = hasManagedNoScheduleTaint(nd);
  const noSchedule = noScheduleTaints(nd);
  let h = `<div class="summary-grid" style="margin-bottom:16px">
    <div class="card">
      <div class="card-title">Node Details</div>
      <div class="card-body">
        <div class="mrow"><span class="ml">K8s node name</span><span class="mv">${esc(nd.k8s_name)}</span></div>
        <div class="mrow"><span class="ml">Nova hypervisor <span class="hint">ESXi FQDN</span></span><span class="mv dim">${esc(nd.hypervisor)}</span></div>
        ${nd.kernel_version ? `<div class="mrow"><span class="ml">Kernel</span><span class="mv dim">${esc(nd.kernel_version)}</span></div>` : ''}
        ${nd.latest_kernel_version ? `<div class="mrow"><span class="ml">Latest installed kernel</span><span class="mv dim">${esc(nd.latest_kernel_version)}</span></div>` : ''}
        ${nd.uptime         ? `<div class="mrow"><span class="ml">Uptime</span><span class="mv">${esc(nd.uptime)}</span></div>` : ''}
        <div class="mrow"><span class="ml">Reboot needed</span><span class="mv ${nd.reboot_required ? 'yellow' : ''}">${nd.reboot_required ? 'Yes' : 'No'}</span></div>
        <div class="mrow"><span class="ml">NoSchedule taint</span><span class="mv ${noSchedule.length ? 'red' : ''}">${noSchedule.length ? 'Yes' : 'No'}</span></div>
        <div class="mrow"><span class="ml">VibeView-managed NoSchedule</span><span class="mv ${managedNoSchedule ? 'yellow' : ''}">${managedNoSchedule ? 'Yes' : 'No'}</span></div>
        ${noSchedule.length ? `<div class="mrow"><span class="ml">Taint details</span><span class="mv mono" style="font-size:10px">${esc(noSchedule.map(taintLabel).join(', '))}</span></div>` : ''}
        <div class="mrow"><span class="ml">Maintenance taint action</span><span class="mv"><button class="btn ${managedNoSchedule ? 'warning' : ''}" onclick="actionToggleNoSchedule()">${managedNoSchedule ? '↺ Remove NoSchedule' : '＋ Add NoSchedule'}</button></span></div>
        <div class="mrow"><span class="ml">Is compute node</span><span class="mv">${nd.is_compute ? 'Yes' : 'No'}</span></div>
        ${nd.availability_zone ? `<div class="mrow"><span class="ml">Availability zone</span><span class="mv blue">${esc(nd.availability_zone)}</span></div>` : ''}
        ${nd.aggregates?.length ? `<div class="mrow"><span class="ml">Host aggregates</span><span class="mv" style="display:flex;flex-wrap:wrap;gap:3px">${nd.aggregates.map(a => `<span class="tree-badge agg" title="${esc(a)}">${esc(a)}</span>`).join('')}</span></div>` : ''}
        <div class="mrow"><span class="ml">Carries etcd role</span><span class="mv ${nd.is_etcd ? 'red' : ''}">${nd.is_etcd ? 'Yes' : 'No'}</span></div>
      </div>
    </div>`;
  if (nd.is_etcd) {
    const peers = Object.values(nodes).filter(n => n.is_etcd);
    const hc    = peers.filter(n => n.etcd_healthy === true).length;
    h += `<div class="card">
      <div class="card-title">etcd Cluster <span class="hint">vCenter HA</span></div>
      <div class="card-body">
        <div class="mrow"><span class="ml">Total members</span><span class="mv">${peers.length}</span></div>
        <div class="mrow"><span class="ml">Healthy</span><span class="mv green">${hc}</span></div>
        <div class="mrow"><span class="ml">Quorum needed</span><span class="mv">${Math.floor(peers.length / 2) + 1}</span></div>
        <div class="mrow"><span class="ml">This node</span><span class="mv ${nd.etcd_healthy === true ? 'green' : nd.etcd_healthy === false ? 'red' : 'gray'}">${nd.etcd_healthy === true ? '✓ Healthy' : nd.etcd_healthy === false ? '✗ Unhealthy' : 'Unknown'}</span></div>
      </div>
    </div>`;
  }
  h += `</div>`;

  // ── OVN Networking section ────────────────────────────────────────────────
  h += renderOvnSection(nd);

  // ── Physical interfaces section ───────────────────────────────────────────
  h += renderNicSection(nd);

  // ── VMware reference (hidden when hints off) ──────────────────────────────
  h += `<div class="card vmware-ref-card" style="margin-top:16px">
    <div class="card-title">💡 VMware vSphere → OpenStack / Kubernetes Reference</div>
    <div class="card-body" style="padding:0">
      <table class="concept-table">
        <thead><tr><th>VMware vSphere</th><th>OpenStack / Kubernetes equivalent</th></tr></thead>
        <tbody>
          <tr><td>Datacenter</td><td>Region</td></tr>
          <tr><td>vSphere Cluster / DRS</td><td>Availability Zone + Nova Scheduler</td></tr>
          <tr><td>ESXi Host</td><td>Nova Hypervisor + K8s Node (same physical machine)</td></tr>
          <tr><td>VM</td><td>Nova Instance (QEMU/KVM) or K8s Pod</td></tr>
          <tr><td>Enter Maintenance Mode</td><td>Disable Nova service + Cordon K8s + Drain pods</td></tr>
          <tr><td>vMotion (live migration)</td><td>Nova live-migrate</td></tr>
          <tr><td>vApp</td><td>Helm Chart / K8s Deployment</td></tr>
          <tr><td>VM Template</td><td>Glance Image</td></tr>
          <tr><td>Datastore</td><td>Ceph / Cinder Volume (RBD-backed)</td></tr>
          <tr><td>vDS Port Group</td><td>Neutron Network / OVN logical port</td></tr>
          <tr><td>NSX-T</td><td>Neutron + OVN (or Calico for K8s)</td></tr>
          <tr><td>Resource Pool / Quota</td><td>K8s Namespace + ResourceQuota</td></tr>
          <tr><td>VM HA Restart</td><td>K8s Pod restart policy / Nova evacuate</td></tr>
          <tr><td>Load Balancer (NSX LB)</td><td>Octavia (Amphora)</td></tr>
          <tr><td>Snapshot</td><td>Cinder Volume Snapshot / etcd backup</td></tr>
        </tbody>
      </table>
    </div>
  </div>`;

  document.getElementById('configure-content').innerHTML = h;

  // After DOM is set, bind interactive elements
  _bindConfigureTab(nd.k8s_name);
}

// ════════════════════════════════════════════════════════════════════════════
// § OVN NETWORKING SECTION (Configure tab)
// ════════════════════════════════════════════════════════════════════════════

async function loadOvnAnnotations(nodeName, force = false) {
  const c = nodeNetworkCache[nodeName] || {};
  if (c.annLoading) return;
  nodeNetworkCache[nodeName] = { ...c, annLoading: true, annFetchedAt: null };
  if (selectedNode === nodeName && activeTab === 'configure') renderConfigureTab(nodes[nodeName]);
  try {
    const resp = await fetch(`/api/nodes/${encodeURIComponent(nodeName)}/ovn-annotations`);
    const json = await resp.json();
    nodeNetworkCache[nodeName] = {
      ...nodeNetworkCache[nodeName],
      annLoading: false,
      annotations: json,
      annFetchedAt: new Date(),
      annError: json.error || null,
    };
    if (netEdit.node === nodeName || force) _initNetEdit(nodeName);
  } catch (e) {
    nodeNetworkCache[nodeName] = {
      ...nodeNetworkCache[nodeName],
      annLoading: false,
      annotations: null,
      annFetchedAt: new Date(),
      annError: String(e),
    };
  }
  if (selectedNode === nodeName && activeTab === 'configure') renderConfigureTab(nodes[nodeName]);
}

async function loadNetworkInterfaces(nodeName, force = false) {
  const c = nodeNetworkCache[nodeName] || {};
  if (c.ifacesLoading) return;
  nodeNetworkCache[nodeName] = { ...c, ifacesLoading: true, ifaces: null, ifacesError: null, ifacesFetchedAt: null };
  if (selectedNode === nodeName && activeTab === 'configure') renderConfigureTab(nodes[nodeName]);
  try {
    const resp = await fetch(`/api/nodes/${encodeURIComponent(nodeName)}/network-interfaces`);
    const json = await resp.json();
    nodeNetworkCache[nodeName] = {
      ...nodeNetworkCache[nodeName],
      ifacesLoading:   false,
      ifaces:          json.interfaces || [],
      ifacesError:     json.error || null,
      ifacesFetchedAt: new Date(),
    };
  } catch (e) {
    nodeNetworkCache[nodeName] = {
      ...nodeNetworkCache[nodeName],
      ifacesLoading: false,
      ifaces: [],
      ifacesError: String(e),
      ifacesFetchedAt: new Date(),
    };
  }
  if (selectedNode === nodeName && activeTab === 'configure') renderConfigureTab(nodes[nodeName]);
}

function _initNetEdit(nodeName) {
  const ann = (nodeNetworkCache[nodeName] || {}).annotations || {};
  netEdit.node          = nodeName;
  netEdit.bridges       = ann['ovn.openstack.org/bridges'] ? ann['ovn.openstack.org/bridges'].split(',').map(s => s.trim()).filter(Boolean) : [];
  netEdit.mappings      = ann['ovn.openstack.org/mappings'] ? ann['ovn.openstack.org/mappings'].split(',').map(s => { const [p, b] = s.trim().split(':'); return { physnet: p || '', bridge: b || '' }; }).filter(m => m.physnet || m.bridge) : [];
  netEdit.ports         = ann['ovn.openstack.org/ports'] ? ann['ovn.openstack.org/ports'].split(',').map(s => { const [b, i] = s.trim().split(':'); return { bridge: b || '', iface: i || '' }; }).filter(p => p.bridge || p.iface) : [];
  netEdit.bridgesDirty  = false;
  netEdit.mappingsDirty = false;
  netEdit.portsDirty    = false;
}

function _fmtTime(d) {
  if (!d) return '';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function renderOvnSection(nd) {
  const name  = nd.k8s_name;
  const cache = nodeNetworkCache[name] || {};
  const ann   = cache.annotations || {};
  const loading = cache.annLoading;
  const hasUnsaved = netEdit.node === name && (netEdit.bridgesDirty || netEdit.mappingsDirty || netEdit.portsDirty);

  let h = `<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.4px;color:var(--dim);padding-bottom:6px;margin:16px 0 10px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px">
    OVN Node Networking
    ${hasUnsaved ? `<span style="font-size:10px;font-weight:400;color:#f57f17;text-transform:none">● Unsaved changes</span>` : ''}
    <span style="margin-left:auto;display:flex;gap:5px">
      ${hasUnsaved ? `<button class="btn sm" onclick="cfgRevertAll()" title="Discard all pending changes">✕ Revert</button>` : ''}
      <button class="btn sm" onclick="loadOvnAnnotations('${escAttr(name)}', true)" ${loading ? 'disabled' : ''} title="Refresh annotations from cluster">↺ Refresh</button>
    </span>
  </div>`;

  // Status bar
  if (loading) {
    h += `<div class="fetch-bar loading"><span class="spinner">⟳</span><span class="fb-msg">Reading OVN annotations from Kubernetes API…</span></div>`;
    return h;
  }
  if (cache.annError) {
    h += `<div class="fetch-bar error"><span class="fb-msg">⚠ ${esc(cache.annError)}</span>
      <button class="fb-btn" onclick="loadOvnAnnotations('${escAttr(name)}', true)">Retry</button></div>`;
    return h;
  }
  if (!cache.annotations && !loading) {
    h += `<div class="fetch-bar loading"><span class="spinner">⟳</span><span class="fb-msg">Reading OVN annotations from Kubernetes API…</span></div>`;
    return h;
  }
  if (cache.annFetchedAt) {
    h += `<div class="fetch-bar done">✓ <span class="fb-msg">Annotations loaded at ${_fmtTime(cache.annFetchedAt)}</span></div>`;
  }

  const bridges     = netEdit.node === name ? netEdit.bridges : [];
  const mappings    = netEdit.node === name ? netEdit.mappings : [];
  const ports       = netEdit.node === name ? netEdit.ports : [];
  const ifaces      = (cache.ifaces || []).filter(i => i.type === 'physical' || i.type === 'bond');
  const ifaceNames  = ifaces.map(i => i.name);
  const ifaceLoading = cache.ifacesLoading;

  // ── Tunnel + int_bridge (read-only) ───────────────────────────────────────
  h += `<div class="summary-grid" style="margin-bottom:12px">
    <div class="card">
      <div class="card-title"><span class="card-title-label">Tunnel &amp; Integration Bridge</span> <span class="hint">Read-only</span></div>
      <div class="card-body">
        <div class="mrow">
          <span class="ml" style="font-family:monospace;font-size:11px;font-weight:400">ovn.kubernetes.io/tunnel-interface</span>
          <span class="mv mono">${esc(ann['ovn.kubernetes.io/tunnel-interface'] || '—')}</span>
        </div>
        <div class="mrow">
          <span class="ml" style="font-family:monospace;font-size:11px;font-weight:400">ovn.openstack.org/int_bridge</span>
          <span class="mv mono">${esc(ann['ovn.openstack.org/int_bridge'] || '—')}</span>
        </div>
      </div>
    </div>

    <!-- Bridges (editable chips) -->
    <div class="card">
      <div class="card-title">
        <span class="card-title-label" style="font-family:monospace;font-weight:400;text-transform:none;letter-spacing:0">ovn.openstack.org/bridges</span>
        <div class="card-title-actions">
          ${netEdit.bridgesDirty ? `<span class="unsaved-dot" title="Unsaved changes"></span>` : ''}
          <button class="btn sm primary" id="cfg-save-bridges" onclick="cfgSaveBridges()" ${netEdit.bridgesDirty ? '' : 'disabled'}>Save</button>
        </div>
      </div>
      <div class="card-body">
        <div style="font-size:11px;color:var(--dim);margin-bottom:6px">OVS bridges managed by OVN on this node.</div>
        <div class="bridge-list" id="cfg-bridge-list">
          ${bridges.map((b, i) => `<span class="bridge-chip">${esc(b)}<span class="bx" onclick="cfgRemoveBridge(${i})" title="Remove">✕</span></span>`).join('')}
          ${bridges.length === 0 ? `<span style="color:var(--dim);font-size:11px">No bridges configured</span>` : ''}
        </div>
        <div class="add-chip-row">
          <input type="text" id="cfg-bridge-input" placeholder="br-phys" maxlength="40">
          <button class="btn sm primary" onclick="cfgAddBridge()">+ Add</button>
        </div>
      </div>
    </div>
  </div>`;

  // ── Mappings ──────────────────────────────────────────────────────────────
  h += `<div class="card" style="margin-bottom:12px">
    <div class="card-title">
      <span class="card-title-label" style="font-family:monospace;font-weight:400;text-transform:none;letter-spacing:0">ovn.openstack.org/mappings</span>
      <span class="hint">physnet → bridge</span>
      <div class="card-title-actions">
        ${netEdit.mappingsDirty ? `<span class="unsaved-dot" title="Unsaved changes"></span>` : ''}
        <button class="btn sm primary" id="cfg-save-mappings" onclick="cfgSaveMappings()" ${netEdit.mappingsDirty ? '' : 'disabled'}>Save</button>
      </div>
    </div>
    <div style="padding:5px 12px 2px;font-size:11px;color:var(--dim)">
      Maps a physical network name to an OVS bridge.
      The bridge must exist in <code style="background:var(--gray-bg);padding:1px 4px;border-radius:2px">ovn.openstack.org/bridges</code>.
    </div>
    <table class="ann-table">
      <thead><tr>
        <th style="width:32px">#</th>
        <th>Physical Network Name</th>
        <th style="width:16px"></th>
        <th>OVS Bridge</th>
        <th style="width:32px"></th>
      </tr></thead>
      <tbody id="cfg-mappings-body">
        ${mappings.map((m, i) => `<tr>
          <td style="color:var(--dim);font-size:11px">${i+1}</td>
          <td><input type="text" value="${esc(m.physnet)}" style="width:180px"
              oninput="netEdit.mappings[${i}].physnet=this.value;netEdit.mappingsDirty=true;_cfgMarkDirty()"
              placeholder="physnet1"></td>
          <td style="color:var(--dim);text-align:center;padding:0">:</td>
          <td>
            <select onchange="netEdit.mappings[${i}].bridge=this.value;netEdit.mappingsDirty=true;_cfgMarkDirty()">
              ${bridges.length ? bridges.map(b => `<option value="${esc(b)}" ${b===m.bridge?'selected':''}>${esc(b)}</option>`).join('') : `<option value="${esc(m.bridge)}">${esc(m.bridge)||'(no bridges)'}</option>`}
            </select>
            ${m.bridge && !bridges.includes(m.bridge) ? `<span class="err-chip" style="margin-left:4px" title="Bridge not in bridges list">⚠ not in bridges</span>` : ''}
          </td>
          <td><button class="del-btn" onclick="cfgRemoveMapping(${i})" title="Remove">✕</button></td>
        </tr>`).join('')}
      </tbody>
    </table>
    <div class="add-row-btn" onclick="cfgAddMapping()">+ Add mapping</div>
  </div>`;

  // ── Ports ─────────────────────────────────────────────────────────────────
  // Build interface <option> list: disabled+grayed if the interface has an IP configured
  const buildIfaceOptions = (selectedIface) => {
    if (!ifaces.length) return `<option value="${esc(selectedIface)}">${esc(selectedIface) || '—'}</option>`;
    return ifaces.map(n => {
      const ips = [...(n.ipv4 || []), ...(n.ipv6 || [])];
      const hasIp = ips.length > 0;
      const reason = hasIp ? ` — has IP: ${ips.join(', ')}` : '';
      return `<option value="${esc(n.name)}" ${n.name === selectedIface ? 'selected' : ''} ${hasIp ? 'disabled style="color:#bdbdbd"' : ''}>${esc(n.name)}${esc(reason)}</option>`;
    }).join('');
  };

  h += `<div class="card" style="margin-bottom:16px">
    <div class="card-title">
      <span class="card-title-label" style="font-family:monospace;font-weight:400;text-transform:none;letter-spacing:0">ovn.openstack.org/ports</span>
      <span class="hint">bridge → physical interface</span>
      <div class="card-title-actions">
        ${netEdit.portsDirty ? `<span class="unsaved-dot" title="Unsaved changes"></span>` : ''}
        <button class="btn sm primary" id="cfg-save-ports" onclick="cfgSavePorts()" ${netEdit.portsDirty ? '' : 'disabled'}>Save</button>
      </div>
    </div>
    <div style="padding:5px 12px 2px;font-size:11px;color:var(--dim)">
      Maps an OVS bridge to a physical or bond interface.
      The bridge must be in <code style="background:var(--gray-bg);padding:1px 4px;border-radius:2px">ovn.openstack.org/bridges</code>.
      The interface is selected from interfaces discovered on this node.
    </div>
    <table class="ann-table">
      <thead><tr>
        <th style="width:32px">#</th>
        <th>OVS Bridge</th>
        <th style="width:16px"></th>
        <th>Physical / Bond Interface ${ifaceLoading ? `<span class="spinner" style="font-size:10px">⟳</span>` : ''}</th>
        <th style="width:32px"></th>
      </tr></thead>
      <tbody id="cfg-ports-body">
        ${ports.map((p, i) => {
          // Bridges already used by OTHER rows
          const usedByOthers = new Set(ports.filter((_, j) => j !== i).map(x => x.bridge));
          const isDupeBridge = p.bridge && usedByOthers.has(p.bridge);
          // For this row's select: show all bridges but mark duplicates disabled
          const bridgeOpts = bridges.length
            ? bridges.map(b => {
                const taken = usedByOthers.has(b);
                return `<option value="${esc(b)}" ${b===p.bridge?'selected':''} ${taken?'disabled':''}>
                  ${esc(b)}${taken?' (in use)':''}
                </option>`;
              }).join('')
            : `<option value="${esc(p.bridge)}">${esc(p.bridge)||'(no bridges)'}</option>`;
          return `<tr ${isDupeBridge ? 'style="background:#fff8f8"' : ''}>
            <td style="color:var(--dim);font-size:11px">${i+1}</td>
            <td>
              <select onchange="netEdit.ports[${i}].bridge=this.value;netEdit.portsDirty=true;_cfgMarkDirty()">
                ${bridgeOpts}
              </select>
              ${isDupeBridge ? `<span class="err-chip" style="margin-left:4px" title="Each bridge can only have one port">⚠ duplicate bridge</span>` : ''}
              ${p.bridge && !bridges.includes(p.bridge) ? `<span class="err-chip" style="margin-left:4px" title="Bridge not in bridges list">⚠ not in bridges</span>` : ''}
            </td>
            <td style="color:var(--dim);text-align:center;padding:0">:</td>
            <td>
              ${ifaceLoading
                ? `<span style="color:var(--dim);font-size:11px"><span class="spinner">⟳</span> Loading…</span>`
                : ifaces.length > 0
                  ? `<select onchange="netEdit.ports[${i}].iface=this.value;netEdit.portsDirty=true;_cfgMarkDirty()">${buildIfaceOptions(p.iface)}</select>`
                  : `<input type="text" value="${esc(p.iface)}" style="width:160px;font-family:monospace" oninput="netEdit.ports[${i}].iface=this.value;netEdit.portsDirty=true;_cfgMarkDirty()">`
              }
            </td>
            <td><button class="del-btn" onclick="cfgRemovePort(${i})" title="Remove">✕</button></td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>
    ${bridges.length > ports.length
      ? `<div class="add-row-btn" onclick="cfgAddPort()">+ Add port mapping</div>`
      : `<div style="padding:6px 10px 5px;font-size:11px;color:var(--dim);border-top:1px solid #f0f2f5">All bridges already have a port assigned.</div>`
    }
  </div>`;

  return h;
}

function renderNicSection(nd) {
  const cache = nodeNetworkCache[nd.k8s_name] || {};
  const name  = nd.k8s_name;

  let h = `<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.4px;color:var(--dim);padding-bottom:6px;margin:16px 0 10px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px">
    Physical Interfaces &amp; Bonds
    <span style="margin-left:auto">
      <button class="btn sm" onclick="loadNetworkInterfaces('${escAttr(name)}', true)" ${cache.ifacesLoading ? 'disabled' : ''} title="Refresh interface list">↺ Refresh</button>
    </span>
  </div>`;

  if (cache.ifacesLoading) {
    h += `<div class="fetch-bar loading"><span class="spinner">⟳</span>
      <span class="fb-msg">Discovering interfaces from the node agent — this may take up to 20 seconds…</span></div>`;
    return h;
  }
  if (cache.ifacesError) {
    h += `<div class="fetch-bar error"><span class="fb-msg">⚠ Host detail error: ${esc(cache.ifacesError)}</span>
      <button class="fb-btn" onclick="loadNetworkInterfaces('${escAttr(name)}', true)">Retry</button></div>`;
    return h;
  }
  if (!cache.ifaces) {
    h += `<div class="fetch-bar loading"><span class="spinner">⟳</span>
      <span class="fb-msg">Discovering interfaces from the node agent — this may take up to 20 seconds…</span></div>`;
    return h;
  }
  if (cache.ifacesFetchedAt) {
    h += `<div class="fetch-bar done">✓ <span class="fb-msg">Interface data fetched at ${_fmtTime(cache.ifacesFetchedAt)}</span></div>`;
  }

  const physicals = cache.ifaces.filter(n => n.type === 'physical');
  const bonds     = cache.ifaces.filter(n => n.type === 'bond');

  function speedCls(s) {
    if (!s) return 'gunk';
    if (s.startsWith('100')) return 'g100';
    if (s.startsWith('40'))  return 'g40';
    if (s.startsWith('25'))  return 'g25';
    if (s.startsWith('10G')) return 'g10';
    if (s.startsWith('1G'))  return 'g1';
    return 'gunk';
  }
  function statusDot(st) {
    const c = st === 'up' ? 'green' : 'gray';
    return `<span class="tree-dot ${c}" style="display:inline-block;margin-right:4px;flex-shrink:0"></span>${esc(st)}`;
  }

  if (physicals.length) {
    h += `<div style="font-size:11px;font-weight:600;color:var(--dim);margin-bottom:6px">Physical Interfaces</div>
    <table class="data-table" style="margin-bottom:14px">
      <thead><tr>
        <th>Interface</th><th>Status</th><th>Speed</th><th>Duplex</th>
        <th>Driver</th><th>Model</th><th>MAC</th>
      </tr></thead>
      <tbody>
        ${physicals.map(n => `<tr>
          <td style="font-family:monospace;font-weight:600">${esc(n.name)}</td>
          <td>${statusDot(n.status)}</td>
          <td>${n.speed ? `<span class="nic-speed ${speedCls(n.speed)}">${esc(n.speed)}</span>` : '<span style="color:var(--dim)">—</span>'}</td>
          <td style="color:var(--dim);font-size:11px">${esc(n.duplex || '—')}</td>
          <td style="font-family:monospace;font-size:11px;color:var(--dim)">${esc(n.driver || '—')}</td>
          <td style="font-size:11px;color:var(--dim)">${esc(n.model || (n.vendor ? n.vendor : '—'))}</td>
          <td style="font-family:monospace;font-size:11px;color:var(--dim)">${esc(n.mac || '—')}</td>
        </tr>`).join('')}
      </tbody>
    </table>`;
  }

  if (bonds.length) {
    h += `<div style="font-size:11px;font-weight:600;color:var(--dim);margin-bottom:6px">Bond Interfaces</div>
    <table class="data-table" style="margin-bottom:14px">
      <thead><tr>
        <th>Bond</th><th>Status</th><th>Speed</th><th>Mode</th><th>MAC</th><th>Members</th>
      </tr></thead>
      <tbody>
        ${bonds.map(n => `<tr>
          <td style="font-family:monospace;font-weight:600">${esc(n.name)}</td>
          <td>${statusDot(n.status)}</td>
          <td>${n.speed ? `<span class="nic-speed ${speedCls(n.speed)}">${esc(n.speed)}</span>` : '<span style="color:var(--dim)">—</span>'}</td>
          <td style="font-size:11px;color:var(--dim)">${esc(n.mode || '—')}</td>
          <td style="font-family:monospace;font-size:11px;color:var(--dim)">${esc(n.mac || '—')}</td>
          <td>${n.members.map(m => `<span class="tree-badge" style="margin-right:3px;font-family:monospace">${esc(m)}</span>`).join('')}</td>
        </tr>`).join('')}
      </tbody>
    </table>`;
  }

  if (!physicals.length && !bonds.length) {
    h += `<div style="color:var(--dim);font-size:12px;padding:6px 0">No physical or bond interfaces found.</div>`;
  }
  return h;
}

// ── Configure tab binding (called after DOM render) ──────────────────────────

function _bindConfigureTab(nodeName) {
  const inp = document.getElementById('cfg-bridge-input');
  if (inp) {
    inp.onkeydown = null;
  }
}

function _cfgMarkDirty() {
  // Re-render just the title unsaved indicators without full re-render
  const saveB  = document.getElementById('cfg-save-bridges');
  const saveM  = document.getElementById('cfg-save-mappings');
  const saveP  = document.getElementById('cfg-save-ports');
  if (saveB) saveB.disabled = !netEdit.bridgesDirty;
  if (saveM) saveM.disabled = !netEdit.mappingsDirty;
  if (saveP) saveP.disabled = !netEdit.portsDirty;
}

// ── Revert all pending changes ───────────────────────────────────────────────

function cfgRevertAll() {
  if (!confirm('Discard all pending changes to bridges, mappings, and ports?\n\nThis will restore the values currently stored in Kubernetes.')) return;
  _initNetEdit(netEdit.node);
  renderConfigureTab(nodes[netEdit.node]);
}

// ── Bridge operations ────────────────────────────────────────────────────────

function cfgAddBridge() {
  const inp = document.getElementById('cfg-bridge-input');
  const val = (inp?.value || '').trim();
  if (!val) return;
  if (!/^[a-zA-Z0-9_-]+$/.test(val)) { alert('Bridge name must contain only letters, numbers, hyphens, or underscores.'); return; }
  if (netEdit.bridges.includes(val))  { alert(`Bridge "${val}" already exists.`); return; }
  netEdit.bridges.push(val);
  netEdit.bridgesDirty = true;
  renderConfigureTab(nodes[netEdit.node]);
}

function cfgRemoveBridge(i) {
  const name = netEdit.bridges[i];
  const usedM = netEdit.mappings.some(m => m.bridge === name);
  const usedP = netEdit.ports.some(p => p.bridge === name);
  const isLast = netEdit.bridges.length === 1;
  const refs = [usedM && 'Mappings', usedP && 'Ports'].filter(Boolean).join(' and ');
  let msg = null;
  if (isLast && (usedM || usedP)) {
    msg = `"${name}" is the last bridge and is referenced in ${refs}.\n\nRemoving it will leave Mappings and Ports with no valid bridge, which will break data plane connectivity.\n\nRemove anyway?`;
  } else if (isLast) {
    msg = `"${name}" is the last bridge.\n\nRemoving all bridges will break data plane connectivity on this node.\n\nRemove anyway?`;
  } else if (usedM || usedP) {
    msg = `Bridge "${name}" is referenced in ${refs}. Remove anyway?`;
  }
  if (msg && !confirm(msg)) return;
  netEdit.bridges.splice(i, 1);
  netEdit.bridgesDirty = true;
  renderConfigureTab(nodes[netEdit.node]);
}

function cfgSaveBridges() {
  const val = netEdit.bridges.join(',');
  _annWarnAndSave('ovn.openstack.org/bridges', val, () => {
    netEdit.bridgesDirty = false;
    _cfgMarkDirty();
  });
}

// ── Mapping operations ───────────────────────────────────────────────────────

function cfgAddMapping() {
  netEdit.mappings.push({ physnet: '', bridge: netEdit.bridges[0] || '' });
  netEdit.mappingsDirty = true;
  renderConfigureTab(nodes[netEdit.node]);
  // Focus last physnet input
  const inputs = document.querySelectorAll('#cfg-mappings-body input[type=text]');
  if (inputs.length) inputs[inputs.length - 1].focus();
}

function cfgRemoveMapping(i) {
  if (netEdit.mappings.length === 1) {
    if (!confirm('This is the last mapping entry.\n\nRemoving it will leave no physical network to bridge mappings, which will break data plane connectivity.\n\nRemove anyway?')) return;
  }
  netEdit.mappings.splice(i, 1);
  netEdit.mappingsDirty = true;
  renderConfigureTab(nodes[netEdit.node]);
}

function cfgSaveMappings() {
  const val = netEdit.mappings.filter(m => m.physnet && m.bridge).map(m => `${m.physnet}:${m.bridge}`).join(',');
  _annWarnAndSave('ovn.openstack.org/mappings', val, () => {
    netEdit.mappingsDirty = false;
    _cfgMarkDirty();
  });
}

// ── Port operations ──────────────────────────────────────────────────────────

function cfgAddPort() {
  const usedBridges = new Set(netEdit.ports.map(p => p.bridge));
  const freeBridge  = netEdit.bridges.find(b => !usedBridges.has(b));
  if (!freeBridge) return; // button should be hidden, but guard anyway
  const ifaces = ((nodeNetworkCache[netEdit.node] || {}).ifaces || []).filter(i => i.type === 'physical' || i.type === 'bond');
  // Prefer an interface with no IP configured as the default
  const defaultIface = ifaces.find(i => !i.ipv4?.length && !i.ipv6?.length) || ifaces[0];
  netEdit.ports.push({ bridge: freeBridge, iface: defaultIface?.name || '' });
  netEdit.portsDirty = true;
  renderConfigureTab(nodes[netEdit.node]);
}

function cfgRemovePort(i) {
  if (netEdit.ports.length === 1) {
    if (!confirm('This is the last port mapping entry.\n\nRemoving it will leave no bridge to physical interface assignments, which will break data plane connectivity.\n\nRemove anyway?')) return;
  }
  netEdit.ports.splice(i, 1);
  netEdit.portsDirty = true;
  renderConfigureTab(nodes[netEdit.node]);
}

function cfgSavePorts() {
  const valid = netEdit.ports.filter(p => p.bridge && p.iface);
  const bridgeCounts = valid.reduce((m, p) => { m[p.bridge] = (m[p.bridge] || 0) + 1; return m; }, {});
  const dupes = Object.entries(bridgeCounts).filter(([, n]) => n > 1).map(([b]) => b);
  if (dupes.length) {
    alert(`Cannot save: bridge(s) ${dupes.map(b => `"${b}"`).join(', ')} appear more than once. Each bridge may only have one port.`);
    return;
  }
  const val = valid.map(p => `${p.bridge}:${p.iface}`).join(',');
  _annWarnAndSave('ovn.openstack.org/ports', val, () => {
    netEdit.portsDirty = false;
    _cfgMarkDirty();
  });
}

// ── Warning modal + API call ─────────────────────────────────────────────────

function _annWarnAndSave(key, value, successCb) {
  _annWarnPending = { key, value, successCb };
  document.getElementById('awm-key').textContent = key;
  document.getElementById('awm-val').textContent = value || '(empty — will clear annotation)';
  document.getElementById('ann-warn-modal').classList.add('open');
}

function annWarnCancel() {
  document.getElementById('ann-warn-modal').classList.remove('open');
  _annWarnPending = null;
}

async function annWarnConfirm() {
  if (!_annWarnPending) return;
  const { key, value, successCb } = _annWarnPending;
  _annWarnPending = null;
  document.getElementById('ann-warn-modal').classList.remove('open');

  const nodeName = netEdit.node;
  try {
    const resp = await fetch(`/api/nodes/${encodeURIComponent(nodeName)}/ovn-annotations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, value }),
    });
    const json = await resp.json();
    if (!json.ok) throw new Error(json.error || 'API error');
    // Update cache
    if (nodeNetworkCache[nodeName]?.annotations) {
      nodeNetworkCache[nodeName].annotations[key] = value;
    }
    successCb?.();
    _showToast(`✓ Annotation saved`, `${key} = ${value || '(cleared)'}`, 'green');
  } catch (e) {
    _showToast(`✗ Failed to save annotation`, String(e), 'red');
  }
  if (selectedNode === nodeName && activeTab === 'configure') renderConfigureTab(nodes[nodeName]);
}

function _showToast(title, body, type) {
  const t = document.createElement('div');
  const bc = type === 'green' ? 'var(--green-lt)' : type === 'red' ? 'var(--red-lt)' : '#f57f17';
  t.style.cssText = `position:fixed;bottom:20px;right:20px;z-index:9999;background:#1a2332;color:white;
    padding:10px 16px;border-radius:6px;font-size:12px;max-width:440px;
    box-shadow:0 4px 12px rgba(0,0,0,0.25);border-left:3px solid ${bc};`;
  t.innerHTML = `<strong>${esc(title)}</strong><br>
    <span style="font-family:monospace;font-size:11px;color:#cfd8dc;word-break:break-all">${esc(body)}</span>`;
  document.body.appendChild(t);
  setTimeout(() => { t.style.transition = 'opacity .4s'; t.style.opacity = '0'; setTimeout(() => t.remove(), 400); }, 4000);
}
