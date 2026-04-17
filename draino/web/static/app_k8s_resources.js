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

function renderK8sDetail() {
  const wrap = document.getElementById(activeK8sDetailWrapId());
  if (!wrap) return;
  const { type, item } = k8sDetailState;
  if (!type || !item) {
    wrap.classList.remove('open');
    wrap.innerHTML = '';
    document.getElementById(activeK8sDetailResizerId())?.classList.remove('open');
    syncActiveK8sShell();
    return;
  }

  const title = item.name || K8S_RES_META[type]?.label || 'Kubernetes Object';
  const subtitle = item.namespace ? `${item.namespace} / ${K8S_RES_META[type]?.label || type}` : (K8S_RES_META[type]?.label || type);
  let body = '';

  switch (type) {
    case 'vpcs':
      body += `<div class="card"><div class="card-body">${k8sDetailRows([
        ['Name', esc(item.name || '—')],
        ['Default', item.default ? 'Yes' : 'No'],
        ['Namespaces', esc(String(item.namespace_count ?? 0))],
        ['Namespace Names', k8sListHtml(item.namespaces || [])],
        ['Subnets', esc(String(item.subnet_count ?? 0))],
        ['Subnet Names', k8sListHtml(item.subnets || [])],
        ['Static Routes', esc(String(item.static_route_count ?? 0))],
        ['Policy Routes', esc(String(item.policy_route_count ?? 0))],
        ['Standby', item.standby ? 'Yes' : 'No'],
        ['Age', esc(k8sAge(item.created))],
      ])}</div></div>`;
      break;
    case 'subnets':
      body += `<div class="card"><div class="card-body">${k8sDetailRows([
        ['Name', esc(item.name || '—')],
        ['CIDR', `<span style="font-family:monospace">${esc(item.cidr || '—')}</span>`],
        ['Gateway', `<span style="font-family:monospace">${esc(item.gateway || '—')}</span>`],
        ['Protocol', esc(item.protocol || '—')],
        ['VPC', esc(item.vpc || '—')],
        ['Provider', esc(item.provider || '—')],
        ['Namespaces', esc(String(item.namespace_count ?? 0))],
        ['Namespace Names', k8sListHtml(item.namespaces || [])],
        ['NAT Outgoing', item.nat_outgoing ? 'Yes' : 'No'],
        ['Private', item.private ? 'Yes' : 'No'],
        ['Default', item.default ? 'Yes' : 'No'],
        ['Exclude IPs', esc(String(item.exclude_ip_count ?? 0))],
        ['Available IPs', esc(String(item.available_ips || '—'))],
        ['Used IPs', esc(String(item.used_ips || '—'))],
        ['Age', esc(k8sAge(item.created))],
      ])}</div></div>`;
      break;
    case 'vlans':
      body += `<div class="card"><div class="card-body">${k8sDetailRows([
        ['Name', esc(item.name || '—')],
        ['Provider', esc(item.provider || '—')],
        ['VLAN ID', `<span style="font-family:monospace">${esc(String(item.vlan_id || '—'))}</span>`],
        ['Subnets', esc(String(item.subnet_count ?? 0))],
        ['Subnet Names', k8sListHtml(item.subnets || [])],
        ['Age', esc(k8sAge(item.created))],
      ])}</div></div>`;
      break;
    case 'providernetworks':
      body += `<div class="card"><div class="card-body">${k8sDetailRows([
        ['Name', esc(item.name || '—')],
        ['Default Interface', esc(item.default_interface || '—')],
        ['Custom NICs', esc(String(item.nic_count ?? 0))],
        ['Ready Nodes', esc(String(item.ready_node_count ?? 0))],
        ['Excluded Nodes', esc(String(item.exclude_node_count ?? 0))],
        ['Ready Node Names', k8sListHtml(item.ready_nodes || [])],
        ['Excluded Node Names', k8sListHtml(item.exclude_nodes || [])],
        ['Age', esc(k8sAge(item.created))],
      ])}</div></div>`;
      break;
    case 'providersubnets':
      body += `<div class="card"><div class="card-body">${k8sDetailRows([
        ['Name', esc(item.name || '—')],
        ['CIDR', `<span style="font-family:monospace">${esc(item.cidr || '—')}</span>`],
        ['Gateway', `<span style="font-family:monospace">${esc(item.gateway || '—')}</span>`],
        ['Protocol', esc(item.protocol || '—')],
        ['VPC', esc(item.vpc || '—')],
        ['Provider', esc(item.provider || '—')],
        ['Namespaces', esc(String(item.namespace_count ?? 0))],
        ['Namespace Names', k8sListHtml(item.namespaces || [])],
        ['Available IPs', esc(String(item.available_ips || '—'))],
        ['Used IPs', esc(String(item.used_ips || '—'))],
        ['Age', esc(k8sAge(item.created))],
      ])}</div></div>`;
      break;
    case 'ips':
      body += `<div class="card"><div class="card-body">${k8sDetailRows([
        ['Name', esc(item.name || '—')],
        ['Namespace', esc(item.namespace || '—')],
        ['Pod', esc(item.pod_name || '—')],
        ['Node', esc(item.node_name || '—')],
        ['Subnet', esc(item.subnet || '—')],
        ['IPv4', `<span style="font-family:monospace">${esc(item.v4_ip || '—')}</span>`],
        ['IPv6', `<span style="font-family:monospace">${esc(item.v6_ip || '—')}</span>`],
        ['MAC', `<span style="font-family:monospace">${esc(item.mac_address || '—')}</span>`],
        ['Attach Subnets', k8sListHtml(item.attach_subnets || [])],
        ['Attach IPs', k8sListHtml(item.attach_ips || [])],
        ['Age', esc(k8sAge(item.created))],
      ])}</div></div>`;
      break;
    case 'clusternetworks':
      body += `<div class="card"><div class="card-body">${k8sDetailRows([
        ['Type', esc(item.network_type || '—')],
        ['CIDR', `<span style="font-family:monospace">${esc(item.cidr || '—')}</span>`],
        ['Nodes', esc(String(item.node_count ?? 0))],
        ['Node Names', k8sListHtml(item.nodes || [])],
        ['LB IPs In Range', esc(String(item.load_balancer_ips ?? 0))],
      ])}</div></div>`;
      break;
    case 'networkdomains':
      body += `<div class="card"><div class="card-body">${k8sDetailRows([
        ['Namespace', esc(item.namespace || '—')],
        ['Services', esc(String(item.service_count ?? 0))],
        ['LoadBalancers', esc(String(item.lb_count ?? 0))],
        ['Gateways', esc(String(item.gateway_count ?? 0))],
        ['HTTPRoutes', esc(String(item.route_count ?? 0))],
        ['External Endpoints', k8sListHtml(item.external_endpoints || [])],
        ['Services', k8sListHtml(item.service_names || [])],
        ['Gateways', k8sListHtml(item.gateway_names || [])],
        ['HTTPRoutes', k8sListHtml(item.route_names || [])],
      ])}</div></div>`;
      break;
    case 'namespaces':
      body += `<div class="card"><div class="card-body">${k8sDetailRows([
        ['Name', esc(item.name || '—')],
        ['Status', `<span class="k8s-badge ${item.status === 'Active' ? 'active' : 'failed'}">${esc(item.status || '—')}</span>`],
        ['Age', esc(k8sAge(item.created))],
      ])}</div></div>`;
      break;
    case 'pods':
      body += `<div class="card"><div class="card-body">${k8sDetailRows([
        ['Namespace', esc(item.namespace || '—')],
        ['Phase', `<span class="k8s-badge ${item.phase === 'Running' ? 'running' : item.phase === 'Pending' ? 'pending' : 'failed'}">${esc(item.phase || '—')}</span>`],
        ['Ready', `<span style="font-family:monospace">${esc(item.ready || '—')}</span>`],
        ['Restarts', esc(String(item.restarts ?? '—'))],
        ['Node', esc(item.node || '—')],
        ['Age', esc(k8sAge(item.created))],
      ])}</div></div>`;
      break;
    case 'services':
    case 'lbs':
      body += `<div class="card"><div class="card-body">${k8sDetailRows([
        ['Namespace', esc(item.namespace || '—')],
        ['Type', `<span class="k8s-badge ${item.type === 'LoadBalancer' ? 'lb' : item.type === 'NodePort' ? 'nodeport' : 'clusterip'}">${esc(item.type || '—')}</span>`],
        ['Cluster IP', `<span style="font-family:monospace">${esc(item.cluster_ip || '—')}</span>`],
        ['External IPs', k8sListHtml(item.external_ips || [])],
        ['Ports', `<span style="font-family:monospace">${esc(item.ports || '—')}</span>`],
        ['Age', esc(k8sAge(item.created))],
      ])}</div></div>`;
      break;
    case 'deployments':
      body += `<div class="card"><div class="card-body">${k8sDetailRows([
        ['Namespace', esc(item.namespace || '—')],
        ['Ready', `<span style="font-family:monospace">${esc(`${item.ready ?? 0}/${item.desired ?? 0}`)}</span>`],
        ['Updated', esc(String(item.updated ?? 0))],
        ['Available', esc(String(item.available ?? 0))],
        ['Unavailable', esc(String(item.unavailable ?? 0))],
        ['Strategy', esc(item.strategy || '—')],
        ['Max Unavailable', esc(item.max_unavailable || '—')],
        ['Max Surge', esc(item.max_surge || '—')],
        ['Selector', `<span style="font-family:monospace">${esc(item.selector || '—')}</span>`],
        ['Images', k8sListHtml(item.images || [])],
        ['Age', esc(k8sAge(item.created))],
      ])}</div></div>`;
      break;
    case 'statefulsets':
      body += `<div class="card"><div class="card-body">${k8sDetailRows([
        ['Namespace', esc(item.namespace || '—')],
        ['Ready', `<span style="font-family:monospace">${esc(`${item.ready ?? 0}/${item.desired ?? 0}`)}</span>`],
        ['Current', esc(String(item.current ?? 0))],
        ['Updated', esc(String(item.updated ?? 0))],
        ['Service Name', esc(item.service_name || '—')],
        ['Update Strategy', esc(item.update_strategy || '—')],
        ['Current Revision', `<span style="font-family:monospace">${esc(item.current_revision || '—')}</span>`],
        ['Update Revision', `<span style="font-family:monospace">${esc(item.update_revision || '—')}</span>`],
        ['PVC Templates', k8sListHtml(item.pvc_templates || [])],
        ['Selector', `<span style="font-family:monospace">${esc(item.selector || '—')}</span>`],
        ['Images', k8sListHtml(item.images || [])],
        ['Age', esc(k8sAge(item.created))],
      ])}</div></div>`;
      break;
    case 'daemonsets':
      body += `<div class="card"><div class="card-body">${k8sDetailRows([
        ['Namespace', esc(item.namespace || '—')],
        ['Ready', `<span style="font-family:monospace">${esc(`${item.ready ?? 0}/${item.desired ?? 0}`)}</span>`],
        ['Current', esc(String(item.current ?? 0))],
        ['Available', esc(String(item.available ?? 0))],
        ['Unavailable', esc(String(item.unavailable ?? 0))],
        ['Misscheduled', esc(String(item.misscheduled ?? 0))],
        ['Update Strategy', esc(item.update_strategy || '—')],
        ['Selector', `<span style="font-family:monospace">${esc(item.selector || '—')}</span>`],
        ['Node Selector', `<span style="font-family:monospace">${esc(item.node_selector || '—')}</span>`],
        ['Tolerations', esc(String(item.tolerations ?? 0))],
        ['Images', k8sListHtml(item.images || [])],
        ['Age', esc(k8sAge(item.created))],
      ])}</div></div>`;
      break;
    case 'gatewayclasses':
      body += `<div class="card"><div class="card-body">${k8sDetailRows([
        ['Name', esc(item.name || '—')],
        ['Controller', `<span style="font-family:monospace">${esc(item.controller || '—')}</span>`],
        ['Accepted', `<span class="k8s-badge ${item.accepted === 'True' ? 'running' : item.accepted === 'False' ? 'failed' : 'pending'}">${esc(item.accepted || 'Unknown')}</span>`],
        ['Age', esc(k8sAge(item.created))],
      ])}</div></div>`;
      break;
    case 'gateways':
      body += `<div class="card"><div class="card-body">${k8sDetailRows([
        ['Namespace', esc(item.namespace || '—')],
        ['GatewayClass', esc(item.gateway_class || '—')],
        ['Addresses', k8sListHtml(item.addresses || [])],
        ['Listeners', esc(String(item.listener_count ?? 0))],
        ['Listener Names', k8sListHtml(item.listener_names || [])],
        ['Attached Routes', esc(String(item.attached_routes ?? 0))],
        ['Accepted', `<span class="k8s-badge ${item.accepted === 'True' ? 'running' : item.accepted === 'False' ? 'failed' : 'pending'}">${esc(item.accepted || 'Unknown')}</span>`],
        ['Programmed', `<span class="k8s-badge ${item.programmed === 'True' ? 'running' : item.programmed === 'False' ? 'failed' : 'pending'}">${esc(item.programmed || 'Unknown')}</span>`],
        ['Age', esc(k8sAge(item.created))],
      ])}</div></div>`;
      break;
    case 'httproutes':
      body += `<div class="card"><div class="card-body">${k8sDetailRows([
        ['Namespace', esc(item.namespace || '—')],
        ['Hostnames', k8sListHtml(item.hostnames || [])],
        ['Parent Refs', k8sListHtml(item.parent_refs || [])],
        ['Rules', esc(String(item.rules ?? 0))],
        ['Backend Refs', k8sListHtml(item.backend_refs || [])],
        ['Accepted', `<span class="k8s-badge ${item.accepted === 'True' ? 'running' : item.accepted === 'False' ? 'failed' : 'pending'}">${esc(item.accepted || 'Unknown')}</span>`],
        ['ResolvedRefs', `<span class="k8s-badge ${item.resolved_refs === 'True' ? 'running' : item.resolved_refs === 'False' ? 'failed' : 'pending'}">${esc(item.resolved_refs || 'Unknown')}</span>`],
        ['Age', esc(k8sAge(item.created))],
      ])}</div></div>`;
      break;
    case 'pvs':
      body += `<div class="card"><div class="card-body">${k8sDetailRows([
        ['Capacity', `<span style="font-family:monospace">${esc(item.capacity || '—')}</span>`],
        ['Access Modes', esc(item.access_modes || '—')],
        ['Reclaim Policy', esc(item.reclaim_policy || '—')],
        ['Status', `<span class="k8s-badge ${item.status === 'Bound' ? 'bound' : item.status === 'Released' ? 'released' : 'pending'}">${esc(item.status || '—')}</span>`],
        ['Claim', esc(item.claim || '—')],
        ['StorageClass', esc(item.storageclass || '—')],
        ['Age', esc(k8sAge(item.created))],
      ])}</div></div>`;
      break;
    case 'pvcs':
      body += `<div class="card"><div class="card-body">${k8sDetailRows([
        ['Namespace', esc(item.namespace || '—')],
        ['Status', `<span class="k8s-badge ${item.status === 'Bound' ? 'bound' : item.status === 'Pending' ? 'pending' : 'failed'}">${esc(item.status || '—')}</span>`],
        ['Volume', esc(item.volume || '—')],
        ['Capacity', `<span style="font-family:monospace">${esc(item.capacity || '—')}</span>`],
        ['Access Modes', esc(item.access_modes || '—')],
        ['StorageClass', esc(item.storageclass || '—')],
        ['Replicas', esc(String(item.replica_count ?? '—'))],
        ['Replica Nodes', k8sListHtml(item.replica_nodes || [])],
        ['Consumer Pods', k8sListHtml(item.consumer_pods || [])],
        ['Consumer Nodes', k8sListHtml(item.consumer_nodes || [])],
        ['Age', esc(k8sAge(item.created))],
      ])}</div></div>`;
      break;
    case 'crds':
      body += `<div class="card"><div class="card-body">${k8sDetailRows([
        ['Name', `<span style="font-family:monospace">${esc(item.name || '—')}</span>`],
        ['Group', esc(item.group || '—')],
        ['Kind', esc(item.kind || '—')],
        ['Scope', `<span class="k8s-badge ${item.scope === 'Namespaced' ? 'clusterip' : 'lb'}">${esc(item.scope || '—')}</span>`],
        ['Versions', k8sListHtml(item.versions || [])],
        ['Age', esc(k8sAge(item.created))],
      ])}</div></div>`;
      break;
    case 'operators':
      body += `<div class="card"><div class="card-body">${k8sDetailRows([
        ['Namespace', esc(item.namespace || '—')],
        ['Kind', `<span class="k8s-badge ${item.kind === 'DaemonSet' ? 'nodeport' : item.kind === 'StatefulSet' ? 'lb' : 'clusterip'}">${esc(item.kind || '—')}</span>`],
        ['Ready', `<span style="font-family:monospace">${esc(item.ready || '—')}</span>`],
        ['Version', `<span style="font-family:monospace">${esc(item.version || 'unknown')}</span>`],
        ['Managed CRDs', esc(String(item.managed_crds ?? 0))],
        ['Images', k8sListHtml(item.images || [])],
        ['Age', esc(k8sAge(item.created))],
      ])}</div></div>`;
      break;
    default:
      body = `<div class="card"><div class="card-body"><div class="mrow"><span class="ml">Raw</span><span class="mv"><pre style="margin:0;white-space:pre-wrap">${esc(JSON.stringify(item, null, 2))}</pre></span></div></div></div>`;
      break;
  }

  wrap.innerHTML = `
    <div class="net-detail-inner">
      <div class="net-detail-head">
        <div>
          <div style="font-size:16px;font-weight:700">${esc(title)}</div>
          <div style="font-size:11px;color:var(--dim);margin-top:2px">${esc(subtitle)}</div>
        </div>
        <button class="btn btn-outline small-btn" onclick="closeK8sDetail()">Close</button>
      </div>
      ${body}
    </div>`;
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

function renderK8sTable(type, rows) {
  if (!rows.length) return `<div style="color:var(--dim);padding:12px 0;font-size:12px">No items match the filter.</div>`;

  const badge = (text, cls) => `<span class="k8s-badge ${cls}">${esc(text)}</span>`;
  const rowOpen = (r) => {
    const key = k8sItemKey(type, r);
    const sel = key === k8sSelectedItemKey ? ' class="selected"' : '';
    return `<tr${sel} style="cursor:pointer" onclick="selectK8sObject('${type}','${esc(key)}')">`;
  };

  switch (type) {
    case 'vpcs':
      return `<table class="data-table"><thead><tr>
        <th>Name</th><th>Default</th><th>Namespaces</th><th>Subnets</th><th>Static Routes</th><th>Policy Routes</th><th>Standby</th>
        </tr></thead><tbody>` +
        rows.map(r => `${rowOpen(r)}
          <td>${esc(r.name)}</td>
          <td>${r.default ? '✓' : '—'}</td>
          <td>${esc(String(r.namespace_count ?? 0))}</td>
          <td>${esc(String(r.subnet_count ?? 0))}</td>
          <td>${esc(String(r.static_route_count ?? 0))}</td>
          <td>${esc(String(r.policy_route_count ?? 0))}</td>
          <td>${r.standby ? '✓' : '—'}</td>
        </tr>`).join('') + `</tbody></table>`;
    case 'subnets':
      return `<table class="data-table"><thead><tr>
        <th>Name</th><th>CIDR</th><th>Gateway</th><th>VPC</th><th>Namespaces</th><th>NAT</th><th>Private</th><th>Used IPs</th>
        </tr></thead><tbody>` +
        rows.map(r => `${rowOpen(r)}
          <td>${esc(r.name)}</td>
          <td style="font-family:monospace;font-size:10px">${esc(r.cidr || '—')}</td>
          <td style="font-family:monospace;font-size:10px">${esc(r.gateway || '—')}</td>
          <td>${esc(r.vpc || '—')}</td>
          <td>${esc(String(r.namespace_count ?? 0))}</td>
          <td>${r.nat_outgoing ? '✓' : '—'}</td>
          <td>${r.private ? '✓' : '—'}</td>
          <td>${esc(String(r.used_ips || '—'))}</td>
        </tr>`).join('') + `</tbody></table>`;
    case 'vlans':
      return `<table class="data-table"><thead><tr>
        <th>Name</th><th>Provider</th><th>VLAN ID</th><th>Subnets</th><th>Age</th>
        </tr></thead><tbody>` +
        rows.map(r => `${rowOpen(r)}
          <td>${esc(r.name)}</td>
          <td>${esc(r.provider || '—')}</td>
          <td style="font-family:monospace">${esc(String(r.vlan_id || '—'))}</td>
          <td>${esc(String(r.subnet_count ?? 0))}</td>
          <td style="color:var(--dim)">${k8sAge(r.created)}</td>
        </tr>`).join('') + `</tbody></table>`;
    case 'providernetworks':
      return `<table class="data-table"><thead><tr>
        <th>Name</th><th>Default Interface</th><th>NICs</th><th>Ready Nodes</th><th>Excluded Nodes</th><th>Age</th>
        </tr></thead><tbody>` +
        rows.map(r => `${rowOpen(r)}
          <td>${esc(r.name)}</td>
          <td>${esc(r.default_interface || '—')}</td>
          <td>${esc(String(r.nic_count ?? 0))}</td>
          <td>${esc(String(r.ready_node_count ?? 0))}</td>
          <td>${esc(String(r.exclude_node_count ?? 0))}</td>
          <td style="color:var(--dim)">${k8sAge(r.created)}</td>
        </tr>`).join('') + `</tbody></table>`;
    case 'providersubnets':
      return `<table class="data-table"><thead><tr>
        <th>Name</th><th>CIDR</th><th>Gateway</th><th>VPC</th><th>Provider</th><th>Used IPs</th><th>Age</th>
        </tr></thead><tbody>` +
        rows.map(r => `${rowOpen(r)}
          <td>${esc(r.name)}</td>
          <td style="font-family:monospace;font-size:10px">${esc(r.cidr || '—')}</td>
          <td style="font-family:monospace;font-size:10px">${esc(r.gateway || '—')}</td>
          <td>${esc(r.vpc || '—')}</td>
          <td>${esc(r.provider || '—')}</td>
          <td>${esc(String(r.used_ips || '—'))}</td>
          <td style="color:var(--dim)">${k8sAge(r.created)}</td>
        </tr>`).join('') + `</tbody></table>`;
    case 'ips':
      return `<table class="data-table"><thead><tr>
        <th>Name</th><th>Namespace</th><th>Pod</th><th>Node</th><th>Subnet</th><th>IPv4</th><th>MAC</th>
        </tr></thead><tbody>` +
        rows.map(r => `${rowOpen(r)}
          <td>${esc(r.name)}</td>
          <td>${esc(r.namespace || '—')}</td>
          <td>${esc(r.pod_name || '—')}</td>
          <td>${esc(r.node_name || '—')}</td>
          <td>${esc(r.subnet || '—')}</td>
          <td style="font-family:monospace;font-size:10px">${esc(r.v4_ip || r.v6_ip || '—')}</td>
          <td style="font-family:monospace;font-size:10px">${esc(r.mac_address || '—')}</td>
        </tr>`).join('') + `</tbody></table>`;
    case 'clusternetworks':
      return `<table class="data-table"><thead><tr>
        <th>Name</th><th>Type</th><th>CIDR</th><th>Nodes</th><th>LB IPs In Range</th>
        </tr></thead><tbody>` +
        rows.map(r => `${rowOpen(r)}
          <td>${esc(r.name)}</td>
          <td>${esc(r.network_type || '—')}</td>
          <td style="font-family:monospace;font-size:10px">${esc(r.cidr || '—')}</td>
          <td>${esc(String(r.node_count ?? 0))}</td>
          <td>${esc(String(r.load_balancer_ips ?? 0))}</td>
        </tr>`).join('') + `</tbody></table>`;
    case 'networkdomains':
      return `<table class="data-table"><thead><tr>
        <th>Namespace</th><th>Services</th><th>LoadBalancers</th><th>Gateways</th><th>HTTPRoutes</th><th>External Endpoints</th>
        </tr></thead><tbody>` +
        rows.map(r => `${rowOpen(r)}
          <td>${esc(r.namespace || r.name || '—')}</td>
          <td>${esc(String(r.service_count ?? 0))}</td>
          <td>${esc(String(r.lb_count ?? 0))}</td>
          <td>${esc(String(r.gateway_count ?? 0))}</td>
          <td>${esc(String(r.route_count ?? 0))}</td>
          <td style="font-family:monospace;font-size:10px;color:var(--dim)">${esc((r.external_endpoints || []).join(', ') || '—')}</td>
        </tr>`).join('') + `</tbody></table>`;
    case 'namespaces':
      return `<table class="data-table"><thead><tr>
        <th>Name</th><th>Status</th><th>Age</th>
        </tr></thead><tbody>` +
        rows.map(r => `${rowOpen(r)}
          <td><strong>${esc(r.name)}</strong></td>
          <td>${badge(r.status, r.status === 'Active' ? 'active' : 'failed')}</td>
          <td style="color:var(--dim)">${k8sAge(r.created)}</td>
        </tr>`).join('') + `</tbody></table>`;
    case 'pods':
      return `<table class="data-table"><thead><tr>
        <th>Namespace</th><th>Name</th><th>Phase</th><th>Ready</th><th>Restarts</th><th>Node</th><th>Age</th>
        </tr></thead><tbody>` +
        rows.map(r => {
          const phCls = r.phase === 'Running' ? 'running' : r.phase === 'Pending' ? 'pending' : 'failed';
          return `${rowOpen(r)}
            <td style="color:var(--dim)">${esc(r.namespace)}</td>
            <td>${esc(r.name)}</td>
            <td>${badge(r.phase, phCls)}</td>
            <td style="font-family:monospace">${esc(r.ready)}</td>
            <td style="text-align:center${r.restarts > 10 ? ';color:var(--red)' : ''}">${r.restarts}</td>
            <td style="color:var(--dim);font-size:10px">${esc(r.node)}</td>
            <td style="color:var(--dim)">${k8sAge(r.created)}</td>
          </tr>`;
        }).join('') + `</tbody></table>`;
    case 'services':
    case 'lbs':
      return `<table class="data-table"><thead><tr>
        <th>Namespace</th><th>Name</th><th>Type</th><th>Cluster IP</th><th>External IP</th><th>Ports</th><th>Age</th>
        </tr></thead><tbody>` +
        rows.map(r => {
          const tCls = r.type === 'LoadBalancer' ? 'lb' : r.type === 'NodePort' ? 'nodeport' : 'clusterip';
          const extIp = r.external_ips?.length ? r.external_ips.join(', ') : '—';
          return `${rowOpen(r)}
            <td style="color:var(--dim)">${esc(r.namespace)}</td>
            <td>${esc(r.name)}</td>
            <td>${badge(r.type, tCls)}</td>
            <td style="font-family:monospace;font-size:10px">${esc(r.cluster_ip)}</td>
            <td style="font-family:monospace;font-size:10px${r.external_ips?.length ? '' : ';color:var(--dim)'}">${esc(extIp)}</td>
            <td style="font-family:monospace;font-size:10px">${esc(r.ports)}</td>
            <td style="color:var(--dim)">${k8sAge(r.created)}</td>
          </tr>`;
        }).join('') + `</tbody></table>`;
    case 'deployments':
      return `<table class="data-table"><thead><tr>
        <th>Namespace</th><th>Name</th><th>Ready</th><th>Updated</th><th>Available</th><th>Unavailable</th><th>Strategy</th><th>Images</th><th>Age</th>
        </tr></thead><tbody>` +
        rows.map(r => `${rowOpen(r)}
          <td style="color:var(--dim)">${esc(r.namespace)}</td>
          <td>${esc(r.name)}</td>
          <td style="font-family:monospace">${esc(`${r.ready ?? 0}/${r.desired ?? 0}`)}</td>
          <td>${esc(String(r.updated ?? 0))}</td>
          <td>${esc(String(r.available ?? 0))}</td>
          <td${(r.unavailable ?? 0) > 0 ? ' style="color:var(--red)"' : ''}>${esc(String(r.unavailable ?? 0))}</td>
          <td>${esc(r.strategy || '—')}</td>
          <td style="font-size:10px;color:var(--dim)">${esc((r.images || []).join(', ') || '—')}</td>
          <td style="color:var(--dim)">${k8sAge(r.created)}</td>
        </tr>`).join('') + `</tbody></table>`;
    case 'statefulsets':
      return `<table class="data-table"><thead><tr>
        <th>Namespace</th><th>Name</th><th>Ready</th><th>Current</th><th>Updated</th><th>Service</th><th>PVC Templates</th><th>Age</th>
        </tr></thead><tbody>` +
        rows.map(r => `${rowOpen(r)}
          <td style="color:var(--dim)">${esc(r.namespace)}</td>
          <td>${esc(r.name)}</td>
          <td style="font-family:monospace">${esc(`${r.ready ?? 0}/${r.desired ?? 0}`)}</td>
          <td>${esc(String(r.current ?? 0))}</td>
          <td>${esc(String(r.updated ?? 0))}</td>
          <td style="font-size:10px;color:var(--dim)">${esc(r.service_name || '—')}</td>
          <td style="font-size:10px;color:var(--dim)">${esc((r.pvc_templates || []).join(', ') || '—')}</td>
          <td style="color:var(--dim)">${k8sAge(r.created)}</td>
        </tr>`).join('') + `</tbody></table>`;
    case 'daemonsets':
      return `<table class="data-table"><thead><tr>
        <th>Namespace</th><th>Name</th><th>Ready</th><th>Current</th><th>Available</th><th>Unavailable</th><th>Misscheduled</th><th>Node Selector</th><th>Age</th>
        </tr></thead><tbody>` +
        rows.map(r => `${rowOpen(r)}
          <td style="color:var(--dim)">${esc(r.namespace)}</td>
          <td>${esc(r.name)}</td>
          <td style="font-family:monospace">${esc(`${r.ready ?? 0}/${r.desired ?? 0}`)}</td>
          <td>${esc(String(r.current ?? 0))}</td>
          <td>${esc(String(r.available ?? 0))}</td>
          <td${(r.unavailable ?? 0) > 0 ? ' style="color:var(--red)"' : ''}>${esc(String(r.unavailable ?? 0))}</td>
          <td${(r.misscheduled ?? 0) > 0 ? ' style="color:var(--red)"' : ''}>${esc(String(r.misscheduled ?? 0))}</td>
          <td style="font-size:10px;color:var(--dim)">${esc(r.node_selector || '—')}</td>
          <td style="color:var(--dim)">${k8sAge(r.created)}</td>
        </tr>`).join('') + `</tbody></table>`;
    case 'gatewayclasses':
      return `<table class="data-table"><thead><tr>
        <th>Name</th><th>Controller</th><th>Accepted</th><th>Age</th>
        </tr></thead><tbody>` +
        rows.map(r => `${rowOpen(r)}
          <td>${esc(r.name)}</td>
          <td style="font-size:10px;color:var(--dim);font-family:monospace">${esc(r.controller || '—')}</td>
          <td>${badge(r.accepted || 'Unknown', r.accepted === 'True' ? 'running' : r.accepted === 'False' ? 'failed' : 'pending')}</td>
          <td style="color:var(--dim)">${k8sAge(r.created)}</td>
        </tr>`).join('') + `</tbody></table>`;
    case 'gateways':
      return `<table class="data-table"><thead><tr>
        <th>Namespace</th><th>Name</th><th>Class</th><th>Addresses</th><th>Listeners</th><th>Attached Routes</th><th>Accepted</th><th>Programmed</th><th>Age</th>
        </tr></thead><tbody>` +
        rows.map(r => `${rowOpen(r)}
          <td style="color:var(--dim)">${esc(r.namespace)}</td>
          <td>${esc(r.name)}</td>
          <td>${esc(r.gateway_class || '—')}</td>
          <td style="font-family:monospace;font-size:10px">${esc((r.addresses || []).join(', ') || '—')}</td>
          <td title="${esc((r.listener_names || []).join(', '))}">${esc(String(r.listener_count ?? 0))}</td>
          <td>${esc(String(r.attached_routes ?? 0))}</td>
          <td>${badge(r.accepted || 'Unknown', r.accepted === 'True' ? 'running' : r.accepted === 'False' ? 'failed' : 'pending')}</td>
          <td>${badge(r.programmed || 'Unknown', r.programmed === 'True' ? 'running' : r.programmed === 'False' ? 'failed' : 'pending')}</td>
          <td style="color:var(--dim)">${k8sAge(r.created)}</td>
        </tr>`).join('') + `</tbody></table>`;
    case 'httproutes':
      return `<table class="data-table"><thead><tr>
        <th>Namespace</th><th>Name</th><th>Hostnames</th><th>Parents</th><th>Rules</th><th>Backends</th><th>Accepted</th><th>ResolvedRefs</th><th>Age</th>
        </tr></thead><tbody>` +
        rows.map(r => `${rowOpen(r)}
          <td style="color:var(--dim)">${esc(r.namespace)}</td>
          <td>${esc(r.name)}</td>
          <td style="font-size:10px">${esc((r.hostnames || []).join(', ') || '—')}</td>
          <td style="font-size:10px;color:var(--dim)">${esc((r.parent_refs || []).join(', ') || '—')}</td>
          <td>${esc(String(r.rules ?? 0))}</td>
          <td style="font-size:10px;color:var(--dim)">${esc((r.backend_refs || []).join(', ') || '—')}</td>
          <td>${badge(r.accepted || 'Unknown', r.accepted === 'True' ? 'running' : r.accepted === 'False' ? 'failed' : 'pending')}</td>
          <td>${badge(r.resolved_refs || 'Unknown', r.resolved_refs === 'True' ? 'running' : r.resolved_refs === 'False' ? 'failed' : 'pending')}</td>
          <td style="color:var(--dim)">${k8sAge(r.created)}</td>
        </tr>`).join('') + `</tbody></table>`;
    case 'pvs':
      return `<table class="data-table"><thead><tr>
        <th>Name</th><th>Capacity</th><th>Access</th><th>Reclaim</th><th>Status</th><th>Claim</th><th>StorageClass</th><th>Age</th>
        </tr></thead><tbody>` +
        rows.map(r => {
          const stCls = r.status === 'Bound' ? 'bound' : r.status === 'Released' ? 'released' : 'pending';
          return `${rowOpen(r)}
            <td>${esc(r.name)}</td>
            <td style="font-family:monospace">${esc(r.capacity)}</td>
            <td style="font-size:10px;color:var(--dim)">${esc(r.access_modes)}</td>
            <td style="font-size:10px">${esc(r.reclaim_policy)}</td>
            <td>${badge(r.status, stCls)}</td>
            <td style="font-size:10px;color:var(--dim)">${esc(r.claim) || '—'}</td>
            <td style="font-size:10px">${esc(r.storageclass) || '—'}</td>
            <td style="color:var(--dim)">${k8sAge(r.created)}</td>
          </tr>`;
        }).join('') + `</tbody></table>`;
    case 'pvcs':
      return `<table class="data-table"><thead><tr>
        <th>Namespace</th><th>Name</th><th>Status</th><th>Volume</th><th>Capacity</th><th>Access</th><th>StorageClass</th><th>Age</th>
        </tr></thead><tbody>` +
        rows.map(r => {
          const stCls = r.status === 'Bound' ? 'bound' : r.status === 'Pending' ? 'pending' : 'failed';
          return `${rowOpen(r)}
            <td style="color:var(--dim)">${esc(r.namespace)}</td>
            <td>${esc(r.name)}</td>
            <td>${badge(r.status, stCls)}</td>
            <td style="font-size:10px;color:var(--dim)">${esc(r.volume) || '—'}</td>
            <td style="font-family:monospace">${esc(r.capacity) || '—'}</td>
            <td style="font-size:10px;color:var(--dim)">${esc(r.access_modes)}</td>
            <td style="font-size:10px">${esc(r.storageclass) || '—'}</td>
            <td style="color:var(--dim)">${k8sAge(r.created)}</td>
          </tr>`;
        }).join('') + `</tbody></table>`;
    case 'crds':
      return `<table class="data-table"><thead><tr>
        <th>Name</th><th>Group</th><th>Kind</th><th>Scope</th><th>Versions</th><th>Age</th>
        </tr></thead><tbody>` +
        rows.map(r => `${rowOpen(r)}
          <td style="font-size:10px;font-family:monospace">${esc(r.name)}</td>
          <td style="font-size:10px;color:var(--dim)">${esc(r.group)}</td>
          <td>${esc(r.kind)}</td>
          <td><span class="k8s-badge ${r.scope === 'Namespaced' ? 'clusterip' : 'lb'}">${esc(r.scope)}</span></td>
          <td style="font-size:10px;color:var(--dim)">${(r.versions || []).join(', ')}</td>
          <td style="color:var(--dim)">${k8sAge(r.created)}</td>
        </tr>`).join('') + `</tbody></table>`;
    case 'operators':
      return `<table class="data-table"><thead><tr>
        <th>Namespace</th><th>Name</th><th>Kind</th><th>Ready</th><th>Version</th><th>Managed CRDs</th><th>Images</th><th>Age</th>
        </tr></thead><tbody>` +
        rows.map(r => `${rowOpen(r)}
          <td style="color:var(--dim)">${esc(r.namespace)}</td>
          <td>${esc(r.name)}</td>
          <td>${badge(r.kind, r.kind === 'DaemonSet' ? 'nodeport' : r.kind === 'StatefulSet' ? 'lb' : 'clusterip')}</td>
          <td style="font-family:monospace">${esc(r.ready || '—')}</td>
          <td style="font-family:monospace;font-size:10px">${esc(r.version || 'unknown')}</td>
          <td>${esc(String(r.managed_crds ?? 0))}</td>
          <td style="font-size:10px;color:var(--dim)">${esc((r.images || []).join(', ') || '—')}</td>
          <td style="color:var(--dim)">${k8sAge(r.created)}</td>
        </tr>`).join('') + `</tbody></table>`;
    default:
      return `<div style="color:var(--dim)">Unknown resource type.</div>`;
  }
}
