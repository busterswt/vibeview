'use strict';

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
