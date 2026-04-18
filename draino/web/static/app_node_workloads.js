'use strict';

async function loadNodeInstancePortStats(nodeName, instanceId, force = false) {
  if (!instanceId) return;
  const detail = instanceDetailCache[instanceId]?.data;
  const portIds = Array.isArray(detail?.ports) ? detail.ports.map((p) => p.id).filter(Boolean) : [];
  if (!portIds.length) return;
  const cacheKey = `${nodeName}:${instanceId}`;
  const cached = nodeInstancePortStatsCache[cacheKey];
  if (!force && cached?.loading) return;
  nodeInstancePortStatsCache[cacheKey] = {
    ...(cached || {}),
    loading: true,
    error: null,
    unsupported: false,
    message: '',
  };
  if (selectedNode === nodeName && activeTab === 'instances') renderInstancesTab(nodes[nodeName]);
  try {
    const resp = await fetch(`/api/nodes/${encodeURIComponent(nodeName)}/instance-port-stats`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ port_ids: portIds }),
    });
    const json = await resp.json();
    const ports = json.ports || [];
    nodeInstancePortStatsCache[cacheKey] = {
      loading: false,
      portsById: Object.fromEntries(ports.map(item => [item.port_id, item])),
      error: json.error || null,
      unsupported: !!json.unsupported,
      message: json.message || '',
      fetchedAt: new Date(),
    };
  } catch (e) {
    nodeInstancePortStatsCache[cacheKey] = {
      loading: false,
      portsById: {},
      error: String(e),
      unsupported: false,
      message: '',
      fetchedAt: new Date(),
    };
  }
  if (selectedNode === nodeName && activeTab === 'instances') renderInstancesTab(nodes[nodeName]);
}

function refreshSelectedInstancePortStats() {
  if (activeView !== 'infrastructure' || activeTab !== 'instances' || !selectedNode || !nodes[selectedNode]) return;
  const instanceId = expandedInstanceIdByNode[selectedNode];
  if (!instanceId) return;
  loadNodeInstancePortStats(selectedNode, instanceId, true);
}

function renderInstancesTab(nd) {
  const showEvacuate = hasOpenStackAuth();
  const expandedId = expandedInstanceIdByNode[nd.k8s_name] || '';
  const instanceRefreshPill = nd.preflight_loading
    ? `<span class="node-refresh-indicator active" title="Refreshing VM list">
        <span class="spinner">⟳</span>
        <span>Refreshing</span>
      </span>`
    : '<span class="node-refresh-indicator instances-refresh-slot" aria-hidden="true"></span>';
  let h = `<div class="inst-toolbar">
    ${showEvacuate ? `<button class="btn primary" onclick="actionEvacuate()">▶ Evacuate <span class="hint">Enter Maintenance</span></button>` : ''}
    <span style="flex:1"></span>
    <input class="toolbar-filter" type="text" placeholder="Filter instances…" id="inst-filter" oninput="filterInstTable()">
  </div>`;

  if (nd.instances?.length) {
    h += `<div class="tab-section-title"><span>Nova Instances — Migration Status <span class="hint">vMotion Progress</span></span>${instanceRefreshPill}</div>
    <table class="data-table" id="inst-data-table">
      <thead><tr>
        <th>Name <span class="hint">VM Name</span></th><th>Type</th>
        <th>Nova State <span class="hint">Power State</span></th>
        <th>Operation <span class="hint">vMotion</span></th>
      </tr></thead><tbody>`;
    for (const inst of nd.instances) {
      const tp = inst.is_amphora ? `<span class="tag-amp">Amphora LB</span>` : 'VM';
      const op = inst.is_amphora ? (inst.failover_status || 'pending') : (inst.migration_status || 'pending');
      const phc = { complete: 'ph-complete', failed: 'ph-failed', migrating: 'ph-migrate', 'cold-migrating': 'ph-migrate', confirming: 'ph-migrate', failing_over: 'ph-migrate' }[op] || 'ph-queued';
      const dotc = { ACTIVE: 'green', ERROR: 'red' }[inst.status] || 'gray';
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
        const actions = [`<button class="btn" style="font-size:11px" onclick="toggleInstanceDetail('${escAttr(i.id)}')">${detailsLabel}</button>`];
        if (!i.is_amphora) {
          if (ms === 'migrating') {
            actions.push(`<button class="btn" disabled style="font-size:11px"><span class="spinner">⟳</span> Migrating…</button>`);
          } else if (ms === 'error') {
            actions.push(`<button class="btn danger" style="font-size:11px" onclick="migrateInstance('${escAttr(i.id)}')">↺ Retry</button>`);
          } else {
            actions.push(`<button class="btn" style="font-size:11px" onclick="migrateInstance('${escAttr(i.id)}')">↗ Migrate</button>`);
          }
        }
        h += `<tr><td>${esc(i.name)}</td><td><span class="sdot green"></span>${esc(i.status)}</td><td>${tp}</td><td>${vcpu}</td><td>${memory}</td><td>${st}</td><td><div style="display:flex;gap:6px;flex-wrap:wrap">${actions.join('')}</div></td></tr>`;
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

  document.getElementById('instances-content').innerHTML = h;
}

function renderPodsTab(nd) {
  const drained = nd.k8s_cordoned || nd.compute_status === 'disabled';
  let h = `<div class="inst-toolbar">
    <button class="btn ${drained ? 'warning' : ''}" onclick="actionDrainOrUndrain()">${drained ? '↺ Undrain' : '▽ Drain'}</button>
    <span style="flex:1"></span>
    <span style="color:var(--dim);font-size:11px">Succeeded pods can be hidden below.</span>
  </div>`;

  h += `<div class="tab-section-title" style="margin-top:10px">
    <span>Kubernetes Pods <span class="hint">Containerised Workloads</span></span>
  </div>
  <div id="pods-section">`;
  if (lastPodsCache?.node === nd.k8s_name) {
    h += buildPodsTableHtml(lastPodsCache.pods);
  } else {
    h += `<div style="color:var(--dim);font-size:12px"><span class="spinner">⟳</span> Fetching pods…</div>`;
  }
  h += `</div>`;

  document.getElementById('pods-content').innerHTML = h;
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
  const expandedPortId = expandedPortIdByInstance[instanceId] || '';
  const expandedPort = ports.find((port) => port.id === expandedPortId) || null;
  const firstPort = ports[0] || null;
  const firstSubnet = firstPort?.subnets?.[0] || {};
  const firstRouter = firstSubnet?.router || {};
  const firstFloatingIp = (firstPort?.floating_ips || [])[0] || '';
  const sgNames = [...new Set(ports.flatMap((port) => port.security_group_names || []))].filter(Boolean);
  const findings = [];
  if (firstPort) {
    if ((firstPort.status || '').toUpperCase() === 'ACTIVE') findings.push({ cls: 'good', title: 'Port is active', detail: 'Neutron reports the primary interface as ACTIVE.' });
    else findings.push({ cls: 'bad', title: 'Port is not active', detail: `Neutron reports port state ${firstPort.status || 'UNKNOWN'}.` });
    if (firstPort.binding_host && inst.compute_host && firstPort.binding_host === inst.compute_host) findings.push({ cls: 'good', title: 'Binding host matches compute host', detail: `${firstPort.binding_host} matches the current Nova host.` });
    else if (firstPort.binding_host || inst.compute_host) findings.push({ cls: 'warn', title: 'Binding host mismatch', detail: `Port binding is ${firstPort.binding_host || 'unknown'}, Nova host is ${inst.compute_host || 'unknown'}.` });
    if (firstFloatingIp && firstRouter?.name) findings.push({ cls: 'good', title: 'North-south path exists', detail: `Floating IP ${firstFloatingIp} is associated through router ${firstRouter.name}.` });
    else if (firstRouter?.name) findings.push({ cls: 'warn', title: 'Primary interface is not externally exposed', detail: `Router ${firstRouter.name} is present, but there is no floating IP on the primary port.` });
  }
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
  h += `<div class="summary-grid" style="margin-top:10px">
        <div class="card">
          <div class="card-title">Network Path</div>
          <div class="card-body">
            <div class="mrow"><span class="ml">Primary port</span><span class="mv" style="font-family:monospace;font-size:11px">${esc(firstPort?.id || '—')}</span></div>
            <div class="mrow"><span class="ml">Tenant network</span><span class="mv">${esc(firstPort?.network_name || firstPort?.network_id || '—')}</span></div>
            <div class="mrow"><span class="ml">Subnet</span><span class="mv">${esc(firstSubnet?.name || firstSubnet?.cidr || '—')}</span></div>
            <div class="mrow"><span class="ml">Router</span><span class="mv">${esc(firstRouter?.name || '—')}</span></div>
            <div class="mrow"><span class="ml">Floating IP</span><span class="mv">${esc(firstFloatingIp || '—')}</span></div>
            <div class="mrow"><span class="ml">Security groups</span><span class="mv">${esc(sgNames.join(', ') || '—')}</span></div>
          </div>
        </div>
        <div class="card">
          <div class="card-title">Troubleshooting</div>
          <div class="card-body">
            ${findings.length ? findings.map((item) => `<div class="finding ${item.cls}" style="margin-bottom:8px">
                <div class="finding-mark">${item.cls === 'good' ? '✓' : item.cls === 'bad' ? '×' : '!'}</div>
                <div><strong>${esc(item.title)}</strong>${esc(item.detail)}</div>
              </div>`).join('') : '<div style="color:var(--dim);font-size:12px">No joined networking findings available.</div>'}
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
    h += `<table class="data-table" style="margin-top:10px">
      <thead><tr>
        <th>Port ID</th><th>MAC</th><th>Fixed IP</th><th>DHCP</th><th>Floating IP</th><th>Gateway</th><th>Action</th>
      </tr></thead><tbody>`;
    for (const port of ports) {
      const firstFixedIp = (port.fixed_ips || [])[0] || '—';
      const firstFloatingIp = (port.floating_ips || [])[0] || '—';
      const gatewayTarget = port.gateway_target || '—';
      const dhcpEnabled = port.dhcp_enabled == null ? '—' : (port.dhcp_enabled ? 'true' : 'false');
      const detailsLabel = expandedPortId === port.id ? '▾ Details' : '▸ Details';
      h += `<tr>
        <td style="font-family:monospace;font-size:11px">${esc(port.id || '—')}</td>
        <td style="font-family:monospace">${esc(port.mac_address || '—')}</td>
        <td>${esc(firstFixedIp)}</td>
        <td>${esc(dhcpEnabled)}</td>
        <td>${esc(firstFloatingIp)}</td>
        <td>${esc(gatewayTarget)}</td>
        <td><button class="btn" style="font-size:11px" onclick="togglePortDetail('${escAttr(instanceId)}','${escAttr(port.id)}')">${detailsLabel}</button></td>
      </tr>`;
    }
    h += `</tbody></table>`;
    if (expandedPort) h += renderPortDetailPanel(expandedPort);
  }
  h += `</div></div>`;
  return h;
}

function renderPortDetailPanel(port) {
  const ovn = port.ovn || {};
  const ovnPort = ovn.port || {};
  const expandedInstanceId = expandedInstanceIdByNode[selectedNode];
  const portStatsCache = nodeInstancePortStatsCache[`${selectedNode}:${expandedInstanceId}`] || {
    loading: false,
    portsById: {},
    error: null,
    unsupported: false,
    message: '',
    fetchedAt: null,
  };
  const portStats = (port.id && portStatsCache.portsById && portStatsCache.portsById[port.id]) || null;
  const allowedAddressPairs = (port.allowed_address_pairs || [])
    .map((pair) => [pair.ip_address, pair.mac_address].filter(Boolean).join(' '))
    .filter(Boolean);
  return `<div class="card" style="margin-top:10px">
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
            <div class="mrow"><span class="ml">Allowed address pairs</span><span class="mv">${esc(allowedAddressPairs.join(', ') || '—')}</span></div>
            <div class="mrow"><span class="ml">Security Groups</span><span class="mv">${esc((port.security_group_names || port.security_groups || []).join(', ') || '—')}</span></div>
            <div class="mrow"><span class="ml">Device owner</span><span class="mv dim">${esc(port.device_owner || '—')}</span></div>
            <div class="mrow"><span class="ml">vNIC type</span><span class="mv dim">${esc(port.binding_vnic_type || '—')}</span></div>
            <div class="mrow"><span class="ml">Binding host</span><span class="mv dim">${esc(port.binding_host || '—')}</span></div>
          </div>
        </div>
        <div class="card">
          <div class="card-title">Host Interface</div>
          <div class="card-body">
            ${portStatsCache.error ? `<div class="err-block">${esc(portStatsCache.error)}</div>` : portStatsCache.unsupported ? `
              <div class="subtle" style="line-height:1.45">${esc(portStatsCache.message || 'VM interface metrics are not supported by the current node-agent build.')}</div>
            ` : `
              <div class="mrow"><span class="ml">OVS Interface</span><span class="mv mono">${esc(portStats?.interface_name || '—')}</span></div>
              <div class="mrow"><span class="ml">Operstate</span><span class="mv">${esc(portStats?.operstate || (portStatsCache.loading ? 'loading…' : '—'))}</span></div>
              <div class="mrow"><span class="ml">RX</span><span class="mv mono">${portStats ? esc(fmtNetRate(portStats.rx_bytes_per_second)) : (portStatsCache.loading ? '<span class="spinner">⟳</span>' : '—')}</span></div>
              <div class="mrow"><span class="ml">TX</span><span class="mv mono">${portStats ? esc(fmtNetRate(portStats.tx_bytes_per_second)) : (portStatsCache.loading ? '<span class="spinner">⟳</span>' : '—')}</span></div>
            `}
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
        <div class="card">
          <div class="card-title">Joined Path</div>
          <div class="card-body">
            <div class="mrow"><span class="ml">Network</span><span class="mv">${esc(port.network_name || port.network_id || '—')}</span></div>
            <div class="mrow"><span class="ml">Subnets</span><span class="mv">${esc((port.subnets || []).map(item => item.name || item.cidr || item.id).join(', ') || '—')}</span></div>
            <div class="mrow"><span class="ml">Router</span><span class="mv">${esc((port.subnets || []).map(item => item.router?.name).filter(Boolean)[0] || port.gateway_target || '—')}</span></div>
            <div class="mrow"><span class="ml">Floating IPs</span><span class="mv">${esc((port.floating_ips || []).join(', ') || '—')}</span></div>
          </div>
        </div>
      </div>
    </div>
  </div>`;
}

function toggleInstanceDetail(instanceId) {
  if (!selectedNode) return;
  const nodeName = selectedNode;
  if (expandedInstanceIdByNode[nodeName] === instanceId) {
    delete expandedPortIdByInstance[instanceId];
    delete expandedInstanceIdByNode[nodeName];
    renderInstancesTab(nodes[nodeName]);
    return;
  }
  expandedInstanceIdByNode[nodeName] = instanceId;
  renderInstancesTab(nodes[nodeName]);
  loadInstanceDetail(nodeName, instanceId);
}

function togglePortDetail(instanceId, portId) {
  if (!selectedNode || expandedInstanceIdByNode[selectedNode] !== instanceId) return;
  if (expandedPortIdByInstance[instanceId] === portId) delete expandedPortIdByInstance[instanceId];
  else expandedPortIdByInstance[instanceId] = portId;
  if (nodes[selectedNode] && activeTab === 'instances') renderInstancesTab(nodes[selectedNode]);
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
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Nova');
    if (!resp.ok || json.error) throw new Error(json.error || `HTTP ${resp.status}`);
    instanceDetailCache[instanceId] = { loading: false, data: json.instance, error: null };
    if (json.instance?.ports?.length) loadNodeInstancePortStats(nodeName, instanceId, true);
  } catch (err) {
    instanceDetailCache[instanceId] = { loading: false, data: null, error: String(err) };
  }
  if (selectedNode === nodeName && activeTab === 'instances' && nodes[nodeName]) renderInstancesTab(nodes[nodeName]);
}

function filterInstTable() {
  const q = (document.getElementById('inst-filter')?.value || '').toLowerCase();
  const tbl = document.getElementById('inst-data-table');
  if (!tbl) return;
  for (const row of tbl.tBodies[0].rows) {
    row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
  }
}

function buildPodsTableHtml(allPods) {
  if (allPods.length === 1 && allPods[0].error) {
    return `<div class="err-block">${esc(allPods[0].error)}</div>`;
  }
  if (!allPods.length) {
    return `<div style="color:var(--dim);font-size:12px">No pods on this node.</div>`;
  }

  const succeeded = allPods.filter(p => p.phase === 'Succeeded');
  const visible = hideSucceeded ? allPods.filter(p => p.phase !== 'Succeeded') : allPods;
  const sorted = [...visible].sort((a, b) => (a.namespace + a.name).localeCompare(b.namespace + b.name));

  let rows = '';
  for (const p of sorted) {
    const ready = `${p.ready_count}/${p.total_count}`;
    const phase = p.phase || 'Unknown';
    const age = p.created_at ? podAge(p.created_at) : '?';
    const rCls = p.ready_count === p.total_count ? 'green' : 'yellow';
    const pCls = { Running: 'green', Pending: 'yellow', Succeeded: 'gray' }[phase] || 'red';
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
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return d > 0 ? `${d}d ${h}h` : h > 0 ? `${h}h ${m}m` : `${m}m`;
}
