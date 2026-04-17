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

  document.querySelectorAll('.top-nav a').forEach(a => {
    a.classList.toggle('active', a.dataset.view === topLevelView(name));
  });

  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById(`view-${name}`).classList.add('active');

  const bcRoot = document.getElementById('bc-root');
  const bcSep = document.getElementById('bc-sep');
  const bcNode = document.getElementById('bc-node');
  document.getElementById('bc-infra-actions').style.display = name === 'infrastructure' ? '' : 'none';
  document.getElementById('bc-k8s-actions').style.display = (name === 'kubernetes' || (name === 'networking' && isNetworkingK8sView())) ? '' : 'none';
  document.getElementById('bc-net-actions').style.display = (name === 'networking' && activeNetworkingView === 'networking') ? '' : 'none';
  document.getElementById('bc-router-actions').style.display = (name === 'networking' && activeNetworkingView === 'routers') ? '' : 'none';
  document.getElementById('bc-lb-actions').style.display = (name === 'networking' && activeNetworkingView === 'loadbalancers') ? '' : 'none';
  document.getElementById('bc-report-actions').style.display = name === 'reports' ? '' : 'none';
  document.getElementById('bc-stress-actions').style.display = name === 'stress' ? '' : 'none';
  document.getElementById('bc-vol-actions').style.display = name === 'storage' ? '' : 'none';
  document.getElementById('tasks-panel').style.display = name === 'infrastructure' ? '' : 'none';

  if (name === 'infrastructure') {
    bcRoot.textContent = 'All Nodes';
    if (selectedNode) {
      bcSep.style.display = '';
      bcNode.textContent = selectedNode;
    } else {
      bcSep.style.display = 'none';
      bcNode.textContent = '';
    }
    renderInfraDetail();
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
    bcRoot.textContent = 'VibeView';
    bcSep.style.display = '';
    bcNode.textContent = label;
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
    if (name === 'storage' && !volState.data && !volState.loading) loadVolumes();
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
  if (nd.phase === 'error') return 'red';
  if (nd.phase === 'rebooting') return 'magenta';
  if (nd.phase === 'undraining') return 'cyan';
  if (nd.phase === 'running') return 'yellow';
  if (nd.phase === 'complete') return 'green';
  if (!nd.k8s_ready) return 'red';
  if (nd.k8s_cordoned) return 'gray';
  if (nd.compute_status === 'down') return 'red';
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
  const sel = name === selectedNode ? ' selected' : '';
  const ico = nd.is_compute ? '🖥️' : nd.is_etcd ? '☣️' : '⚙️';
  const hintTxt = nd.is_compute ? 'ESXi' : nd.is_etcd ? 'Mgmt' : 'Host';
  const dot = phaseColor(nd);
  const etcBdg = nd.is_etcd ? `<span class="tree-badge etcd">etcd</span>` : '';
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
  if (nd.is_compute && nd.vm_count != null && nd.vm_count > 0) vmBadge = `<span class="tree-badge warn">${nd.vm_count} vm</span>`;

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
  const compute = Object.entries(nodes).filter(([, nd]) => nd.is_compute);
  const other = Object.entries(nodes).filter(([, nd]) => !nd.is_compute);

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

  if (!compute.length && !other.length) html = `<div class="tree-group" style="border-top:none;cursor:default">No nodes loaded</div>`;

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
