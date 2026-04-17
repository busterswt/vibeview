'use strict';

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
