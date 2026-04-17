'use strict';

const K8S_RES_META = {
  vpcs: { label: 'VPCs', icon: '🧩', url: '/api/k8s/vpcs' },
  subnets: { label: 'Subnets', icon: '📐', url: '/api/k8s/subnets' },
  vlans: { label: 'VLANs', icon: '🏷️', url: '/api/k8s/vlans' },
  providernetworks: { label: 'Provider Networks', icon: '🛜', url: '/api/k8s/provider-networks' },
  providersubnets: { label: 'Provider Subnets', icon: '🧱', url: '/api/k8s/provider-subnets' },
  ips: { label: 'IPs', icon: '🧷', url: '/api/k8s/ips' },
  clusternetworks: { label: 'Cluster Networks', icon: '🧭', url: '/api/k8s/cluster-networks' },
  networkdomains: { label: 'Network Domains', icon: '🪪', url: '/api/k8s/network-domains' },
  namespaces: { label: 'Namespaces', icon: '📦', url: '/api/k8s/namespaces' },
  pods: { label: 'Pods', icon: '⬡', url: '/api/k8s/pods' },
  services: { label: 'Services', icon: '🔗', url: '/api/k8s/services' },
  deployments: { label: 'Deployments', icon: '🚀', url: '/api/k8s/deployments' },
  statefulsets: { label: 'StatefulSets', icon: '🗄️', url: '/api/k8s/statefulsets' },
  daemonsets: { label: 'DaemonSets', icon: '🛰️', url: '/api/k8s/daemonsets' },
  gatewayclasses: { label: 'GatewayClasses', icon: '🏛️', url: '/api/k8s/gatewayclasses' },
  gateways: { label: 'Gateways', icon: '🚪', url: '/api/k8s/gateways' },
  httproutes: { label: 'HTTPRoutes', icon: '🛣️', url: '/api/k8s/httproutes' },
  lbs: { label: 'LoadBalancers', icon: '⚡', url: '/api/k8s/services' },
  pvcs: { label: 'PV Claims', icon: '📋', url: '/api/k8s/pvcs' },
  pvs: { label: 'Persistent Vols', icon: '💾', url: '/api/k8s/pvs' },
  crds: { label: 'Custom Resources', icon: '🔧', url: '/api/k8s/crds' },
  operators: { label: 'Operators', icon: '🧰', url: '/api/k8s/operators' },
};

const k8sResCache = {};
let k8sActiveResource = null;
let k8sSelectedItemKey = null;
const k8sDetailState = { type: null, item: null };
const k8sPageState = {};
const K8S_PAGE_SIZE = 50;

function isNetworkingK8sContext() {
  return activeView === 'networking' && typeof isNetworkingK8sView === 'function' && isNetworkingK8sView();
}

function activeK8sContentInnerId() {
  return isNetworkingK8sContext() ? 'networking-k8s-content-inner' : 'k8s-content-inner';
}

function activeK8sDetailWrapId() {
  return isNetworkingK8sContext() ? 'networking-k8s-detail-wrap' : 'k8s-detail-wrap';
}

function activeK8sDetailResizerId() {
  return isNetworkingK8sContext() ? 'networking-detail-resizer' : 'k8s-detail-resizer';
}

function syncActiveK8sShell() {
  if (isNetworkingK8sContext() && typeof syncNetworkingDetailShell === 'function') {
    syncNetworkingDetailShell();
  }
}

function getK8sPage(type) {
  if (!k8sPageState[type]) k8sPageState[type] = { page: 1, filter: '' };
  return k8sPageState[type];
}

function k8sAge(isoStr) {
  if (!isoStr) return '—';
  const diff = Math.floor((Date.now() - new Date(isoStr)) / 1000);
  if (diff < 60) return `${diff}s`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)}d`;
  if (diff < 86400 * 365) return `${Math.floor(diff / 86400 / 30)}mo`;
  return `${Math.floor(diff / 86400 / 365)}y`;
}

function k8sListHtml(values, empty = '—') {
  if (!values || !values.length) return `<span class="mv">${esc(empty)}</span>`;
  return values.map(v => `<div class="mv">${esc(v)}</div>`).join('');
}

function k8sDetailRows(rows) {
  return rows.map(([label, value]) => `<div class="mrow"><span class="ml">${esc(label)}</span><span class="mv">${value}</span></div>`).join('');
}

function k8sItemKey(type, row) {
  switch (type) {
    case 'vpcs':
    case 'subnets':
    case 'vlans':
    case 'providernetworks':
    case 'providersubnets':
    case 'clusternetworks':
      return row.name || '';
    case 'ips':
      return row.name || `${row.namespace || ''}/${row.pod_name || ''}/${row.v4_ip || row.v6_ip || ''}`;
    case 'networkdomains':
      return row.namespace || row.name || '';
    case 'namespaces':
    case 'gatewayclasses':
    case 'pvs':
    case 'crds':
      return row.name || '';
    case 'pods':
    case 'services':
    case 'lbs':
    case 'pvcs':
    case 'deployments':
    case 'statefulsets':
    case 'daemonsets':
    case 'gateways':
    case 'httproutes':
      return `${row.namespace || ''}/${row.name || ''}`;
    case 'operators':
      return `${row.namespace || ''}/${row.kind || ''}/${row.name || ''}`;
    default:
      return JSON.stringify(row);
  }
}

function closeK8sDetail() {
  k8sSelectedItemKey = null;
  k8sDetailState.type = null;
  k8sDetailState.item = null;
  const wrap = document.getElementById(activeK8sDetailWrapId());
  const resizer = document.getElementById(activeK8sDetailResizerId());
  if (wrap) {
    wrap.classList.remove('open');
    wrap.innerHTML = '';
  }
  if (resizer) resizer.classList.remove('open');
  syncActiveK8sShell();
}

function selectK8sObject(type, key) {
  const cached = k8sResCache[type];
  const rows = cached?.data || [];
  const item = rows.find(r => k8sItemKey(type, r) === key);
  if (!item) return;
  k8sSelectedItemKey = key;
  k8sDetailState.type = type;
  k8sDetailState.item = item;
  const wrap = document.getElementById(activeK8sDetailWrapId());
  const resizer = document.getElementById(activeK8sDetailResizerId());
  if (wrap) wrap.classList.add('open');
  if (resizer) resizer.classList.add('open');
  renderK8sContent();
  renderK8sDetail();
  syncActiveK8sShell();
}

async function selectK8sResource(type) {
  if (k8sActiveResource !== type) closeK8sDetail();
  k8sActiveResource = type;
  document.querySelectorAll('.k8s-res-item').forEach(el =>
    el.classList.toggle('selected', el.dataset.res === type));
  document.querySelectorAll('.networking-nav-item[data-k8s-res]').forEach(el =>
    el.classList.toggle('selected', el.dataset.k8sRes === type));
  const bcNode = document.getElementById('bc-node');
  if (bcNode) bcNode.textContent = K8S_RES_META[type]?.label || type;
  renderK8sContent();
  if (!k8sResCache[type]) await loadK8sResource(type);
  renderK8sContent();
}

async function loadK8sResource(type, force = false) {
  if (!force && k8sResCache[type]?.loading) return;
  const meta = K8S_RES_META[type];
  if (!meta) return;
  k8sResCache[type] = { loading: true, data: null, error: null };
  renderK8sContent();
  try {
    const resp = await fetch(meta.url);
    const json = await resp.json();
    if (json.error) throw new Error(json.error);
    let data = json.items || [];
    if (type === 'lbs') data = data.filter(s => s.type === 'LoadBalancer');
    k8sResCache[type] = { loading: false, data, error: null };
    if (type === 'lbs' && !k8sResCache.services) {
      k8sResCache.services = { loading: false, data: json.items || [], error: null };
    }
    updateK8sCountBadges();
  } catch (e) {
    k8sResCache[type] = { loading: false, data: null, error: String(e) };
  }
  if (k8sActiveResource === type) renderK8sContent();
}

async function refreshK8sResource() {
  if (!k8sActiveResource) return;
  delete k8sResCache[k8sActiveResource];
  await loadK8sResource(k8sActiveResource);
}

function updateK8sCountBadges() {
  for (const [type, cached] of Object.entries(k8sResCache)) {
    const ids = [`k8s-cnt-${type}`, `networking-cnt-${type}`];
    for (const id of ids) {
      const el = document.getElementById(id);
      if (el && cached.data) el.textContent = cached.data.length;
    }
  }
}

function renderK8sContent() {
  const wrap = document.getElementById(activeK8sContentInnerId());
  const focusedInput = captureFocusedInput(wrap, '.dv-filter');
  if (!k8sActiveResource) {
    wrap.innerHTML = `<div style="color:var(--dim);text-align:center;padding:40px 16px">Select a resource type from the navigator.</div>`;
    renderK8sDetail();
    return;
  }
  const type = k8sActiveResource;
  const cached = k8sResCache[type];
  const meta = K8S_RES_META[type];
  const ps = getK8sPage(type);

  if (!cached || cached.loading) {
    wrap.innerHTML = `<div class="data-view-toolbar"><h2>${meta.icon} ${meta.label}</h2></div>
      <div style="color:var(--dim);padding:20px 0"><span class="spinner">⟳</span> Loading…</div>`;
    renderK8sDetail();
    return;
  }
  if (cached.error) {
    wrap.innerHTML = `<div class="data-view-toolbar"><h2>${meta.icon} ${meta.label}</h2></div>
      <div class="err-block" style="margin:8px 0">${esc(cached.error)}</div>`;
    renderK8sDetail();
    return;
  }

  const all = cached.data || [];
  const filter = ps.filter.toLowerCase();
  const filtered = filter ? all.filter(r => JSON.stringify(r).toLowerCase().includes(filter)) : all;
  const total = filtered.length;
  const pages = Math.max(1, Math.ceil(total / K8S_PAGE_SIZE));
  ps.page = Math.min(ps.page, pages);
  const slice = filtered.slice((ps.page - 1) * K8S_PAGE_SIZE, ps.page * K8S_PAGE_SIZE);

  let h = `<div class="data-view-toolbar">
    <h2>${meta.icon} ${meta.label}</h2>
    <input class="dv-filter" type="text" placeholder="Filter…"
      value="${esc(ps.filter)}" oninput="getK8sPage('${type}').filter=this.value;getK8sPage('${type}').page=1;renderK8sContent()">
    <span style="font-size:11px;color:var(--dim)">${total} of ${all.length}</span>
  </div>`;

  h += renderK8sTable(type, slice);
  h += buildPager({ page: ps.page, pageSize: K8S_PAGE_SIZE }, total,
    `getK8sPage('${type}')`, 'renderK8sContent');

  wrap.innerHTML = h;
  restoreFocusedInput(wrap, focusedInput);
  const stillExists = (cached.data || []).some(r => k8sItemKey(type, r) === k8sSelectedItemKey);
  if (type !== k8sDetailState.type || !stillExists) closeK8sDetail();
  renderK8sDetail();
}

