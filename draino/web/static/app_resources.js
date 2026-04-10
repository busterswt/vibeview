'use strict';

// ════════════════════════════════════════════════════════════════════════════
// § NETWORKS VIEW
// ════════════════════════════════════════════════════════════════════════════

function abbreviateRouterPortId(value) {
  const text = String(value || '');
  if (!text) return '—';
  if (text.length <= 18) return text;
  return `${text.slice(0, 8)}…${text.slice(-6)}`;
}

function compactRouterMac(value) {
  const text = String(value || '').trim();
  if (!text) return '—';
  const parts = text.split(':');
  if (parts.length !== 6) return text;
  return parts.slice(3).join(':');
}

async function loadNetworks(force = false) {
  if (netState.loading) return;
  if (netState.data && !force) { renderNetworksView(); return; }
  netState.loading = true;
  netState.data    = null;
  renderNetworksView(); // show spinner
  try {
    const resp = await fetch('/api/networks');
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Neutron');
    netState.data  = json.networks || [];
    netState.page  = 1;
    if (json.error) appendNetError(json.error);
  } catch (e) {
    netState.data = [];
    appendNetError(String(e));
  } finally {
    netState.loading = false;
    renderNetworksView();
  }
}

function renderNetworksView() {
  const wrap = document.getElementById('net-wrap');
  const focusedInput = captureFocusedInput(wrap, '.dv-filter');
  if (netState.loading) {
    wrap.innerHTML = `<div class="data-view-toolbar"><h2>Neutron Networks <span class="hint">Port Groups / vDS</span></h2></div><div style="color:var(--dim);padding:20px 0"><span class="spinner">⟳</span> Loading networks…</div>`;
    return;
  }
  if (!netState.data) { wrap.innerHTML = ''; return; }

  const filtered = applyFilter(netState.data, netState.filter, ['name','status','network_type','project_id']);
  const { page, pageSize } = netState;
  const paged = paginate(filtered, page, pageSize);

  let rows = '';
  for (const n of paged) {
    const stCls = n.status === 'ACTIVE' ? 'st-active' : n.status === 'DOWN' ? 'st-down' : 'st-error';
    const adm   = n.admin_state === 'up'
      ? `<span class="sdot green"></span>up` : `<span class="sdot red"></span>down`;
    const ext    = n.external ? `<span class="tag-amp">ext</span>` : '—';
    const shared = n.shared ? '✓' : '—';
    const rowSel = selectedNetwork === n.id ? ' selected' : '';
    rows += `<tr class="${rowSel}" style="cursor:pointer" data-net-id="${escAttr(n.id)}" onclick="selectNetwork('${escAttr(n.id)}')">
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">${esc(n.name)}</td>
      <td><span class="${stCls}">${esc(n.status)}</span></td>
      <td>${adm}</td>
      <td>${esc(n.network_type) || '<span style="color:var(--dim)">—</span>'}</td>
      <td>${shared}</td>
      <td>${ext}</td>
      <td>${n.subnet_count}</td>
      <td class="uuid-short" title="${esc(n.project_id)}">${n.project_id.slice(0, 8) || '—'}</td>
    </tr>`;
  }

  wrap.innerHTML = `
    <div class="data-view-toolbar">
      <h2>Neutron Networks <span class="hint">Port Groups / vDS</span></h2>
      <input class="dv-filter" type="text" placeholder="Filter networks…"
        value="${esc(netState.filter)}" oninput="netState.filter=this.value;netState.page=1;renderNetworksView()">
      <span style="font-size:11px;color:var(--dim)">${filtered.length} of ${netState.data.length} networks</span>
    </div>
    <table class="data-table">
      <thead><tr>
        <th>Name <span class="hint">Port Group</span></th>
        <th>Status</th>
        <th>Admin State</th>
        <th>Type <span class="hint">vDS Type</span></th>
        <th>Shared</th>
        <th>External <span class="hint">Uplink</span></th>
        <th>Subnets</th>
        <th>Project</th>
      </tr></thead>
      <tbody>${rows || '<tr><td colspan="8" style="text-align:center;color:var(--dim);padding:20px">No networks match the filter.</td></tr>'}</tbody>
    </table>
    ${buildPager(netState, filtered.length, 'netState', 'renderNetworksView')}`;
  restoreFocusedInput(wrap, focusedInput);
}

function appendNetError(msg) {
  onLog({ node: '-', message: `Networks API error: ${msg}`, color: 'error' });
}

async function selectNetwork(id) {
  selectedNetwork = id;
  netDetailState.selectedSubnet = null;
  netDetailState.ovn = { loading: false, data: null, error: null };
  netDetailState.ovnSelectedPort = null;
  netDetailState.ovnPortCache = {};
  netDetailState.metadataRepair = { subnetId: null, loading: false, message: '', error: null };
  // Highlight row in list (immediately, before async fetch)
  document.querySelectorAll('#net-wrap tr[data-net-id]').forEach(r => {
    r.classList.toggle('selected', r.dataset.netId === id);
  });
  // Open detail pane and show spinner
  document.getElementById('net-detail-wrap').classList.add('open');
  netDetailState.loading = true;
  netDetailState.data    = null;
  renderNetworkDetail();
  try {
    const resp = await fetch(`/api/networks/${encodeURIComponent(id)}`);
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Neutron');
    if (json.error) throw new Error(json.error);
    const meta = netState.data?.find(n => n.id === id) || {};
    netDetailState.data = { ...meta, ...json.network };
  } catch (e) {
    netDetailState.data = { error: String(e) };
  } finally {
    netDetailState.loading = false;
    renderNetworkDetail();
  }
  // Load OVN data in parallel (non-blocking — renders separately when done)
  loadNetworkOvn(id);
}

async function refreshSelectedNetworkDetail(options = {}) {
  const { keepSelectedSubnet = true, preserveLoading = false } = options;
  if (!selectedNetwork) return;
  const previousSubnet = keepSelectedSubnet ? netDetailState.selectedSubnet : null;
  if (!preserveLoading) {
    netDetailState.loading = true;
    renderNetworkDetail();
  }
  try {
    const resp = await fetch(`/api/networks/${encodeURIComponent(selectedNetwork)}`);
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Neutron');
    if (json.error) throw new Error(json.error);
    const meta = netState.data?.find(n => n.id === selectedNetwork) || {};
    netDetailState.data = { ...meta, ...json.network };
    netDetailState.selectedSubnet = previousSubnet;
  } catch (e) {
    netDetailState.data = { error: String(e) };
  } finally {
    if (!preserveLoading) {
      netDetailState.loading = false;
    }
    renderNetworkDetail();
  }
}

async function loadNetworkOvn(id) {
  netDetailState.ovn = { loading: true, data: null, error: null };
  renderNetworkDetail();
  try {
    const resp = await fetch(`/api/networks/${encodeURIComponent(id)}/ovn`);
    const json = await resp.json();
    if (json.error) throw new Error(json.error);
    netDetailState.ovn = { loading: false, data: json.ovn, error: null };
  } catch (e) {
    netDetailState.ovn = { loading: false, data: null, error: String(e) };
  }
  renderNetworkDetail();
}

function closeNetworkDetail() {
  selectedNetwork = null;
  netDetailState.data = null;
  netDetailState.selectedSubnet = null;
  netDetailState.ovn = { loading: false, data: null, error: null };
  netDetailState.ovnSelectedPort = null;
  netDetailState.ovnPortCache = {};
  netDetailState.metadataRepair = { subnetId: null, loading: false, message: '', error: null };
  document.getElementById('net-detail-wrap').classList.remove('open');
  document.querySelectorAll('#net-wrap tr[data-net-id]').forEach(r => r.classList.remove('selected'));
}

function renderNetworkDetail() {
  const wrap = document.getElementById('net-detail-wrap');
  if (netDetailState.loading) {
    wrap.innerHTML = `<div class="net-detail-inner"><div style="color:var(--dim);padding:20px 0"><span class="spinner">⟳</span> Loading…</div></div>`;
    return;
  }
  const nd = netDetailState.data;
  if (!nd) { wrap.innerHTML = ''; return; }
  if (nd.error) {
    wrap.innerHTML = `<div class="net-detail-inner"><div class="err-block">${esc(nd.error)}</div></div>`;
    return;
  }

  const stCls = nd.status === 'ACTIVE' ? 'st-active' : nd.status === 'DOWN' ? 'st-down' : 'st-error';
  let h = `<div class="net-detail-inner">
    <div class="net-detail-head">
      <strong style="font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:230px">${esc(nd.name)}</strong>
      <div style="display:flex;gap:6px;align-items:center;flex-shrink:0">
        <span class="${stCls}" style="font-size:11px">${esc(nd.status)}</span>
        <button class="btn" style="padding:1px 7px;font-size:11px" onclick="closeNetworkDetail()">✕</button>
      </div>
    </div>
    <div style="font-family:monospace;font-size:10px;color:var(--dim);word-break:break-all;margin-bottom:10px">${esc(nd.id || '')}</div>

    <div class="card" style="margin-bottom:10px">
      <div class="card-title">Properties</div>
      <div class="card-body">
        <div class="mrow"><span class="ml">Admin state</span><span class="mv ${nd.admin_state === 'up' ? 'green' : 'red'}">${esc(nd.admin_state || '')}</span></div>
        <div class="mrow"><span class="ml">Type</span><span class="mv">${esc(nd.network_type) || '<span style="color:var(--dim)">—</span>'}</span></div>
        <div class="mrow"><span class="ml">Shared</span><span class="mv">${nd.shared ? 'Yes' : 'No'}</span></div>
        <div class="mrow"><span class="ml">External</span><span class="mv">${nd.external ? 'Yes' : 'No'}</span></div>
        ${nd.project_id ? `<div class="mrow"><span class="ml">Project</span><span class="mv uuid-short" title="${esc(nd.project_id)}">${nd.project_id.slice(0, 8)}</span></div>` : ''}
      </div>
    </div>`;

  // Subnets
  const subnets = nd.subnets || [];
  h += `<div class="card" style="margin-bottom:10px">
    <div class="card-title">Subnets (${subnets.length})</div>
    <div class="card-body" style="padding:0">`;
  if (!subnets.length) {
    h += `<div style="color:var(--dim);font-size:12px;padding:8px 10px">No subnets.</div>`;
  } else {
    h += `<table class="data-table" style="font-size:11px">
      <thead><tr><th>Name / CIDR</th><th>DHCP</th><th>Gateway</th></tr></thead>
      <tbody>`;
    for (const s of subnets) {
      const isSel = netDetailState.selectedSubnet === s.id;
      h += `<tr class="${isSel ? 'selected' : ''}" style="cursor:pointer" onclick="selectSubnet('${escAttr(s.id)}')">
        <td>
          <div style="font-weight:${isSel ? 600 : 400}">${esc(s.name || '(unnamed)')}</div>
          <div style="color:var(--dim);font-size:10px;font-family:monospace">${esc(s.cidr)}</div>
        </td>
        <td>${s.enable_dhcp ? '<span class="sdot green"></span>' : '—'}</td>
        <td style="font-family:monospace;font-size:10px;color:var(--dim)">${esc(s.gateway_ip) || '—'}</td>
      </tr>`;
    }
    h += `</tbody></table>`;
  }
  h += `</div></div>`;

  // Subnet detail (shown when a subnet row is clicked)
  if (netDetailState.selectedSubnet) {
    const sub = subnets.find(s => s.id === netDetailState.selectedSubnet);
    if (sub) h += renderSubnetDetail(sub);
  }

  // Segments
  const segments = nd.segments || [];
  if (segments.length) {
    h += `<div class="card" style="margin-bottom:10px">
      <div class="card-title">Segments (${segments.length})</div>
      <div class="card-body" style="padding:0">
        <table class="data-table" style="font-size:11px">
          <thead><tr><th>Type</th><th>Physical net</th><th>Seg ID</th></tr></thead>
          <tbody>`;
    for (const seg of segments) {
      h += `<tr>
        <td>${esc(seg.network_type) || '<span style="color:var(--dim)">—</span>'}</td>
        <td style="color:var(--dim);font-size:10px">${esc(seg.physical_network) || '—'}</td>
        <td style="font-family:monospace">${seg.segmentation_id != null ? seg.segmentation_id : '—'}</td>
      </tr>`;
    }
    h += `</tbody></table></div></div>`;
  }

  // OVN Logical Switch
  const ovn = netDetailState.ovn;
  h += `<div class="card" style="margin-bottom:10px">
    <div class="card-title">OVN Logical Switch</div>`;
  if (ovn.loading) {
    h += `<div class="card-body" style="color:var(--dim);font-size:12px"><span class="spinner">⟳</span> Loading…</div>`;
  } else if (ovn.error) {
    h += `<div class="card-body"><div class="err-block">${esc(ovn.error)}</div></div>`;
  } else if (ovn.data) {
    const ls = ovn.data;
    h += `<div class="card-body">
      <div class="mrow"><span class="ml">Name</span><span class="mv" style="font-family:monospace;font-size:10px">${esc(ls.ls_name)}</span></div>
      <div class="mrow"><span class="ml">UUID</span><span class="mv uuid-short" style="font-family:monospace;font-size:10px;cursor:pointer" title="${escAttr(ls.ls_uuid)}" onclick="navigator.clipboard?.writeText('${escAttr(ls.ls_uuid)}')">${ls.ls_uuid.slice(0,8)}…</span></div>
    </div>`;
    if (ls.ports?.length) {
      h += `<div style="padding:0">
        <table class="data-table" style="font-size:11px">
          <thead><tr><th>Neutron Port ID</th><th>Type</th><th>MAC / IP</th></tr></thead>
          <tbody>`;
      for (const p of ls.ports) {
        const typeLabel = p.type === 'router' ? 'Router' : p.type === 'localnet' ? 'Localnet' : 'VM';
        let mac = '', ipList = [];
        for (const addr of (p.addresses || [])) {
          if (addr === 'unknown') { mac = 'unknown'; break; }
          const parts = addr.trim().split(/\s+/);
          if (!mac && parts[0]) mac = parts[0];
          if (parts.length > 1) ipList.push(...parts.slice(1));
        }
        if (!mac) mac = '—';
        const ipStr = ipList.length ? ipList.join(', ') : '—';
        const isSpecial = p.id.startsWith('provnet-') || p.type === 'localnet';
        const isSel = netDetailState.ovnSelectedPort === p.id;
        const portIdCell = isSpecial
          ? `<span style="color:var(--dim);font-size:10px;font-family:monospace">${esc(p.id)}</span>`
          : `<span style="font-family:monospace;font-size:10px" title="${escAttr(p.id)}">${p.id.slice(0,8)}…</span>`;
        h += `<tr class="${isSel ? 'selected' : ''}" style="cursor:pointer" onclick="selectOvnPort('${escAttr(p.id)}')">
          <td>${portIdCell}</td>
          <td>${esc(typeLabel)}</td>
          <td style="font-family:monospace;font-size:10px">${esc(mac)}<br><span style="color:var(--dim)">${esc(ipStr)}</span></td>
        </tr>`;
      }
      h += `</tbody></table></div>`;
      // Port detail card (shown when a port row is clicked)
      if (netDetailState.ovnSelectedPort) {
        const cached = netDetailState.ovnPortCache[netDetailState.ovnSelectedPort];
        if (cached) h += renderOvnPortDetail(netDetailState.ovnSelectedPort, cached);
      }
    } else {
      h += `<div style="color:var(--dim);font-size:12px;padding:8px 10px">No logical ports found.</div>`;
    }
  } else {
    h += `<div class="card-body" style="color:var(--dim);font-size:12px">Not loaded.</div>`;
  }
  h += `</div>`;

  h += `</div>`;
  wrap.innerHTML = h;
}

function selectSubnet(id) {
  // Toggle: clicking the same subnet again collapses the detail
  netDetailState.selectedSubnet = (netDetailState.selectedSubnet === id) ? null : id;
  renderNetworkDetail();
}

function renderSubnetDetail(sub) {
  const metadataPort = sub.metadata_port || { status: 'missing', port_id: '', ip_address: '' };
  const repairState = netDetailState.metadataRepair || {};
  const repairActive = repairState.subnetId === sub.id && repairState.loading;
  const metadataLabel = metadataPort.status === 'ok'
    ? `<span class="mv green">OK</span>`
    : `<span class="mv red">NotFound</span>`;
  const metadataParts = [];
  if (metadataPort.port_id) {
    const networkId = String(selectedNetwork || '');
    const hoverText = networkId
      ? `${metadataPort.port_id}\novnmeta-${networkId}`
      : metadataPort.port_id;
    metadataParts.push(`<span class="uuid-short" title="${escAttr(hoverText)}">${esc(metadataPort.port_id.slice(0, 8))}</span>`);
  }
  if (metadataPort.ip_address) {
    metadataParts.push(`<span style="font-family:monospace">${esc(metadataPort.ip_address)}</span>`);
  }
  metadataParts.push(metadataLabel);
  if (metadataPort.status !== 'ok') {
    metadataParts.push(
      `<button class="btn" type="button" onclick="repairSubnetMetadataPort('${escAttr(sub.id)}')" ${repairActive ? 'disabled' : ''}>${repairActive ? 'Repairing…' : 'Repair'}</button>`,
    );
  }
  let h = `<div class="card" style="margin-bottom:10px;border-left:3px solid var(--blue)">
    <div class="card-title" style="color:var(--blue)">Subnet: ${esc(sub.name || sub.cidr)}</div>
    <div class="card-body">
      <div class="mrow"><span class="ml">CIDR</span><span class="mv" style="font-family:monospace">${esc(sub.cidr)}</span></div>
      <div class="mrow"><span class="ml">IP version</span><span class="mv">IPv${sub.ip_version}</span></div>
      <div class="mrow"><span class="ml">Gateway</span><span class="mv" style="font-family:monospace">${esc(sub.gateway_ip) || '—'}</span></div>
      <div class="mrow"><span class="ml">DHCP</span><span class="mv ${sub.enable_dhcp ? 'green' : ''}">${sub.enable_dhcp ? 'Enabled' : 'Disabled'}</span></div>`;
  h += `<div class="mrow"><span class="ml">Metadata Port</span><span class="mv">${metadataParts.join(' · ')}</span></div>`;
  if (repairState.subnetId === sub.id && repairState.message) {
    h += `<div class="mrow"><span class="ml">Repair</span><span class="mv">${repairState.loading ? '<span class="spinner">⟳</span> ' : ''}${esc(repairState.message)}</span></div>`;
  }
  if (repairState.subnetId === sub.id && repairState.error) {
    h += `<div class="mrow"><span class="ml">Repair</span><span class="mv red">${esc(repairState.error)}</span></div>`;
  }
  if (sub.allocation_pools?.length) {
    const pools = sub.allocation_pools.map(p => `${p.start}–${p.end}`).join(', ');
    h += `<div class="mrow"><span class="ml">Alloc pools</span><span class="mv" style="font-size:10px;font-family:monospace">${esc(pools)}</span></div>`;
  }
  if (sub.dns_nameservers?.length) {
    h += `<div class="mrow"><span class="ml">DNS</span><span class="mv" style="font-family:monospace;font-size:11px">${esc(sub.dns_nameservers.join(', '))}</span></div>`;
  }
  if (sub.host_routes?.length) {
    const routes = sub.host_routes.map(r => `${r.destination} via ${r.nexthop}`).join('; ');
    h += `<div class="mrow"><span class="ml">Host routes</span><span class="mv" style="font-size:10px;font-family:monospace">${esc(routes)}</span></div>`;
  }
  h += `</div></div>`;
  return h;
}

async function repairSubnetMetadataPort(subnetId) {
  if (!selectedNetwork || !subnetId || netDetailState.metadataRepair.loading) return;
  netDetailState.metadataRepair = {
    subnetId,
    loading: true,
    message: 'Creating metadata port…',
    error: null,
  };
  renderNetworkDetail();
  try {
    const resp = await fetch(
      `/api/networks/${encodeURIComponent(selectedNetwork)}/subnets/${encodeURIComponent(subnetId)}/repair-metadata-port`,
      { method: 'POST' },
    );
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Neutron');
    if (json.error) throw new Error(json.error);
    netDetailState.metadataRepair.message = 'Metadata port created. Refreshing subnet details…';
    renderNetworkDetail();
    for (let attempt = 0; attempt < 8; attempt += 1) {
      await refreshSelectedNetworkDetail({ keepSelectedSubnet: true, preserveLoading: true });
      const subnets = netDetailState.data?.subnets || [];
      const refreshed = subnets.find(item => item.id === subnetId);
      if (refreshed?.metadata_port?.status === 'ok') {
        netDetailState.metadataRepair = {
          subnetId,
          loading: false,
          message: 'Metadata port repaired.',
          error: null,
        };
        renderNetworkDetail();
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 1000));
      netDetailState.metadataRepair.message = 'Waiting for metadata port to appear…';
      renderNetworkDetail();
    }
    netDetailState.metadataRepair = {
      subnetId,
      loading: false,
      message: 'Repair requested. Metadata port is not visible yet.',
      error: null,
    };
  } catch (e) {
    netDetailState.metadataRepair = {
      subnetId,
      loading: false,
      message: '',
      error: String(e),
    };
  }
  renderNetworkDetail();
}

async function selectOvnPort(portId) {
  // Toggle: same port deselects
  if (netDetailState.ovnSelectedPort === portId) {
    netDetailState.ovnSelectedPort = null;
    renderNetworkDetail();
    return;
  }
  netDetailState.ovnSelectedPort = portId;
  // If already cached, just re-render
  if (netDetailState.ovnPortCache[portId]) {
    renderNetworkDetail();
    return;
  }
  // Show spinner in detail slot while fetching
  netDetailState.ovnPortCache[portId] = { loading: true, data: null, error: null };
  renderNetworkDetail();
  try {
    const resp = await fetch(`/api/ovn/lsp/${encodeURIComponent(portId)}`);
    const json = await resp.json();
    if (json.error) throw new Error(json.error);
    netDetailState.ovnPortCache[portId] = { loading: false, data: json.port, error: null };
  } catch (e) {
    netDetailState.ovnPortCache[portId] = { loading: false, data: null, error: String(e) };
  }
  renderNetworkDetail();
}

function renderOvnPortDetail(portId, cached) {
  let h = `<div class="card" style="margin-bottom:10px;border-left:3px solid var(--blue)">
    <div class="card-title" style="color:var(--blue)">LSP: ${portId.slice(0,8)}…</div>`;
  if (cached.loading) {
    h += `<div class="card-body" style="color:var(--dim);font-size:12px"><span class="spinner">⟳</span> Loading…</div>`;
  } else if (cached.error) {
    h += `<div class="card-body"><div class="err-block">${esc(cached.error)}</div></div>`;
  } else if (cached.data) {
    const d = cached.data;
    const ext = d.external_ids || {};
    const opts = d.options || {};
    h += `<div class="card-body">`;
    // Full UUID (copyable)
    h += `<div class="mrow"><span class="ml">UUID</span><span class="mv" style="font-family:monospace;font-size:10px;cursor:pointer;word-break:break-all" title="Click to copy" onclick="navigator.clipboard?.writeText('${escAttr(portId)}')">${esc(portId)}</span></div>`;
    // Status
    if (d.up !== null)      h += `<div class="mrow"><span class="ml">Up</span><span class="mv ${d.up ? 'green' : 'red'}">${d.up ? 'true' : 'false'}</span></div>`;
    if (d.enabled !== null) h += `<div class="mrow"><span class="ml">Enabled</span><span class="mv ${d.enabled ? 'green' : 'red'}">${d.enabled ? 'true' : 'false'}</span></div>`;
    if (d.tag !== null)     h += `<div class="mrow"><span class="ml">Tag (VLAN)</span><span class="mv" style="font-family:monospace">${d.tag}</span></div>`;
    // Chassis binding
    if (opts['requested-chassis']) h += `<div class="mrow"><span class="ml">Chassis</span><span class="mv" style="font-family:monospace;font-size:10px">${esc(opts['requested-chassis'])}</span></div>`;
    // Port security
    if (d.port_security?.length) {
      h += `<div class="mrow"><span class="ml">Port security</span><span class="mv" style="font-family:monospace;font-size:10px">${esc(d.port_security.join(', '))}</span></div>`;
    }
    // Dynamic addresses
    if (d.dynamic_addresses) h += `<div class="mrow"><span class="ml">Dynamic addr</span><span class="mv" style="font-family:monospace;font-size:10px">${esc(d.dynamic_addresses)}</span></div>`;
    // Neutron external_ids
    const neutronKeys = ['neutron:port_name','neutron:device_owner','neutron:device_id',
                         'neutron:project_id','neutron:network_id','neutron:subnet_id',
                         'neutron:revision_number'];
    const labelMap = {
      'neutron:port_name':       'Port name',
      'neutron:device_owner':    'Device owner',
      'neutron:device_id':       'Device ID',
      'neutron:project_id':      'Project ID',
      'neutron:network_id':      'Network ID',
      'neutron:subnet_id':       'Subnet ID',
      'neutron:revision_number': 'Revision',
    };
    const isUuidKey = new Set(['neutron:device_id','neutron:project_id','neutron:network_id','neutron:subnet_id']);
    for (const k of neutronKeys) {
      if (!ext[k]) continue;
      const val = ext[k];
      const mono = isUuidKey.has(k) ? `style="font-family:monospace;font-size:10px;cursor:pointer;word-break:break-all" title="Click to copy" onclick="navigator.clipboard?.writeText('${escAttr(val)}')"` : `style="font-size:11px"`;
      h += `<div class="mrow"><span class="ml">${labelMap[k]}</span><span class="mv" ${mono}>${esc(val)}</span></div>`;
    }
    // Any remaining external_ids not in the known list
    for (const [k, v] of Object.entries(ext)) {
      if (neutronKeys.includes(k)) continue;
      h += `<div class="mrow"><span class="ml" style="font-size:10px">${esc(k)}</span><span class="mv" style="font-size:10px;font-family:monospace">${esc(v)}</span></div>`;
    }
    h += `</div>`;
  }
  h += `</div>`;
  return h;
}

// ════════════════════════════════════════════════════════════════════════════
// § ROUTERS VIEW
// ════════════════════════════════════════════════════════════════════════════

async function loadRouters(force = false) {
  if (routerState.loading) return;
  if (routerState.data && !force) { renderRoutersView(); return; }
  routerState.loading = true;
  routerState.data = null;
  renderRoutersView();
  try {
    const resp = await fetch('/api/routers');
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Neutron');
    routerState.data = json.routers || [];
    routerState.page = 1;
    if (json.error) appendRouterError(json.error);
  } catch (e) {
    routerState.data = [];
    appendRouterError(String(e));
  } finally {
    routerState.loading = false;
    renderRoutersView();
  }
}

function renderRoutersView() {
  const wrap = document.getElementById('router-wrap');
  const focusedInput = captureFocusedInput(wrap, '.dv-filter');
  if (routerState.loading) {
    wrap.innerHTML = `<div class="data-view-toolbar"><h2>Neutron Routers <span class="hint">Gateways</span></h2></div><div style="color:var(--dim);padding:20px 0"><span class="spinner">⟳</span> Loading routers…</div>`;
    return;
  }
  if (!routerState.data) { wrap.innerHTML = ''; return; }

  const filtered = applyFilter(routerState.data, routerState.filter, ['name', 'status', 'external_network_name', 'project_id']);
  const { page, pageSize } = routerState;
  const paged = paginate(filtered, page, pageSize);

  let rows = '';
  for (const r of paged) {
    const stCls = r.status === 'ACTIVE' ? 'st-active' : r.status === 'DOWN' ? 'st-down' : 'st-error';
    const adm = r.admin_state === 'up'
      ? `<span class="sdot green"></span>up` : `<span class="sdot red"></span>down`;
    const ext = r.external_network_name || '<span style="color:var(--dim)">—</span>';
    const ha = r.ha ? '✓' : '—';
    const dist = r.distributed ? '✓' : '—';
    const rowSel = selectedRouter === r.id ? ' selected' : '';
    rows += `<tr class="${rowSel}" style="cursor:pointer" data-router-id="${escAttr(r.id)}" onclick="selectRouter('${escAttr(r.id)}')">
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">${esc(r.name)}</td>
      <td><span class="${stCls}">${esc(r.status)}</span></td>
      <td>${adm}</td>
      <td>${ha}</td>
      <td>${dist}</td>
      <td>${ext}</td>
      <td>${r.interface_count}</td>
      <td>${r.route_count}</td>
      <td class="uuid-short" title="${esc(r.project_id)}">${r.project_id.slice(0, 8) || '—'}</td>
    </tr>`;
  }

  wrap.innerHTML = `
    <div class="data-view-toolbar">
      <h2>Neutron Routers <span class="hint">Gateways</span></h2>
      <input class="dv-filter" type="text" placeholder="Filter routers…"
        value="${esc(routerState.filter)}" oninput="routerState.filter=this.value;routerState.page=1;renderRoutersView()">
      <span style="font-size:11px;color:var(--dim)">${filtered.length} of ${routerState.data.length} routers</span>
    </div>
    <table class="data-table">
      <thead><tr>
        <th>Name</th>
        <th>Status</th>
        <th>Admin State</th>
        <th>HA</th>
        <th>Distributed</th>
        <th>External Network</th>
        <th>Interfaces</th>
        <th>Routes</th>
        <th>Project</th>
      </tr></thead>
      <tbody>${rows || '<tr><td colspan="9" style="text-align:center;color:var(--dim);padding:20px">No routers match the filter.</td></tr>'}</tbody>
    </table>
    ${buildPager(routerState, filtered.length, 'routerState', 'renderRoutersView')}`;
  restoreFocusedInput(wrap, focusedInput);
}

function appendRouterError(msg) {
  onLog({ node: '-', message: `Routers API error: ${msg}`, color: 'error' });
}

async function selectRouter(id) {
  selectedRouter = id;
  routerDetailState.ovn = { loading: false, data: null, error: null };
  document.querySelectorAll('#router-wrap tr[data-router-id]').forEach(r => {
    r.classList.toggle('selected', r.dataset.routerId === id);
  });
  document.getElementById('router-detail-wrap').classList.add('open');
  routerDetailState.loading = true;
  routerDetailState.data = null;
  renderRouterDetail();
  try {
    const resp = await fetch(`/api/routers/${encodeURIComponent(id)}`);
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Neutron');
    if (json.error) throw new Error(json.error);
    const meta = routerState.data?.find(r => r.id === id) || {};
    routerDetailState.data = { ...meta, ...json.router };
  } catch (e) {
    routerDetailState.data = { error: String(e) };
  } finally {
    routerDetailState.loading = false;
    renderRouterDetail();
  }
  loadRouterOvn(id);
}

async function loadRouterOvn(id) {
  routerDetailState.ovn = { loading: true, data: null, error: null };
  renderRouterDetail();
  try {
    const resp = await fetch(`/api/routers/${encodeURIComponent(id)}/ovn`);
    const json = await resp.json();
    if (json.error) throw new Error(json.error);
    routerDetailState.ovn = { loading: false, data: json.ovn, error: null };
  } catch (e) {
    routerDetailState.ovn = { loading: false, data: null, error: String(e) };
  }
  renderRouterDetail();
}

function closeRouterDetail() {
  selectedRouter = null;
  routerDetailState.data = null;
  routerDetailState.ovn = { loading: false, data: null, error: null };
  document.getElementById('router-detail-wrap').classList.remove('open');
  document.querySelectorAll('#router-wrap tr[data-router-id]').forEach(r => r.classList.remove('selected'));
}

function renderRouterDetail() {
  const wrap = document.getElementById('router-detail-wrap');
  if (routerDetailState.loading) {
    wrap.innerHTML = `<div class="net-detail-inner"><div style="color:var(--dim);padding:20px 0"><span class="spinner">⟳</span> Loading…</div></div>`;
    return;
  }
  const rd = routerDetailState.data;
  if (!rd) { wrap.innerHTML = ''; return; }
  if (rd.error) {
    wrap.innerHTML = `<div class="net-detail-inner"><div class="err-block">${esc(rd.error)}</div></div>`;
    return;
  }

  const stCls = rd.status === 'ACTIVE' ? 'st-active' : rd.status === 'DOWN' ? 'st-down' : 'st-error';
  const gateway = rd.external_gateway || {};
  let h = `<div class="net-detail-inner">
    <div class="net-detail-head">
      <strong style="font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:230px">${esc(rd.name)}</strong>
      <div style="display:flex;gap:6px;align-items:center;flex-shrink:0">
        <span class="${stCls}" style="font-size:11px">${esc(rd.status)}</span>
        <button class="btn" style="padding:1px 7px;font-size:11px" onclick="closeRouterDetail()">✕</button>
      </div>
    </div>
    <div style="font-family:monospace;font-size:10px;color:var(--dim);word-break:break-all;margin-bottom:10px">${esc(rd.id || '')}</div>

    <div class="card" style="margin-bottom:10px">
      <div class="card-title">Properties</div>
      <div class="card-body">
        <div class="mrow"><span class="ml">Admin state</span><span class="mv ${rd.admin_state === 'up' ? 'green' : 'red'}">${esc(rd.admin_state || '')}</span></div>
        <div class="mrow"><span class="ml">HA</span><span class="mv">${rd.ha ? 'Yes' : 'No'}</span></div>
        <div class="mrow"><span class="ml">Distributed</span><span class="mv">${rd.distributed ? 'Yes' : 'No'}</span></div>
        <div class="mrow"><span class="ml">Interfaces</span><span class="mv">${rd.interface_count ?? 0}</span></div>
        <div class="mrow"><span class="ml">Routes</span><span class="mv">${rd.route_count ?? 0}</span></div>
        ${rd.project_id ? `<div class="mrow"><span class="ml">Project</span><span class="mv uuid-short" title="${esc(rd.project_id)}">${rd.project_id.slice(0, 8)}</span></div>` : ''}
      </div>
    </div>

    <div class="card" style="margin-bottom:10px">
      <div class="card-title">External Gateway</div>
      <div class="card-body">
        <div class="mrow"><span class="ml">Network</span><span class="mv">${esc(gateway.network_name || '') || '<span style="color:var(--dim)">—</span>'}</span></div>
        <div class="mrow"><span class="ml">SNAT</span><span class="mv">${gateway.enable_snat ? 'Enabled' : 'Disabled'}</span></div>
        <div class="mrow"><span class="ml">External IPs</span><span class="mv">${gateway.external_fixed_ips?.length ? gateway.external_fixed_ips.map(item => esc(item.ip_address)).join(', ') : '<span style="color:var(--dim)">—</span>'}</span></div>
      </div>
    </div>`;

  const subnets = rd.connected_subnets || [];
  h += `<div class="card" style="margin-bottom:10px">
    <div class="card-title">Connected Subnets (${subnets.length})</div>
    <div class="card-body" style="padding:0">`;
  if (!subnets.length) {
    h += `<div style="color:var(--dim);font-size:12px;padding:8px 10px">No connected subnets.</div>`;
  } else {
    h += `<table class="data-table" style="font-size:11px">
      <thead><tr><th>Subnet</th><th>Network</th><th>Router IP</th><th>Gateway</th><th>DHCP</th></tr></thead>
      <tbody>`;
    for (const subnet of subnets) {
      h += `<tr>
        <td>
          <div>${esc(subnet.subnet_name || '(unnamed)')}</div>
          <div style="color:var(--dim);font-size:10px;font-family:monospace">${esc(subnet.cidr || '') || '—'}</div>
        </td>
        <td>${esc(subnet.network_name || '') || '<span style="color:var(--dim)">—</span>'}</td>
        <td style="font-family:monospace;font-size:10px">${esc(subnet.ip_address || '') || '—'}</td>
        <td style="font-family:monospace;font-size:10px">${esc(subnet.gateway_ip || '') || '—'}</td>
        <td>${subnet.enable_dhcp ? '<span class="sdot green"></span>' : '—'}</td>
      </tr>`;
    }
    h += `</tbody></table>`;
  }
  h += `</div></div>`;

  const routes = rd.routes || [];
  h += `<div class="card" style="margin-bottom:10px">
    <div class="card-title">Static Routes (${routes.length})</div>
    <div class="card-body" style="padding:0">`;
  if (!routes.length) {
    h += `<div style="color:var(--dim);font-size:12px;padding:8px 10px">No static routes.</div>`;
  } else {
    h += `<table class="data-table" style="font-size:11px">
      <thead><tr><th>Destination</th><th>Next hop</th></tr></thead>
      <tbody>${routes.map(route => `<tr>
        <td style="font-family:monospace;font-size:10px">${esc(route.destination || '')}</td>
        <td style="font-family:monospace;font-size:10px">${esc(route.nexthop || '')}</td>
      </tr>`).join('')}</tbody></table>`;
  }
  h += `</div></div>`;

  const ovn = routerDetailState.ovn;
  h += `<div class="card" style="margin-bottom:10px">
    <div class="card-title">OVN Logical Router</div>`;
  if (ovn.loading) {
    h += `<div class="card-body" style="color:var(--dim);font-size:12px"><span class="spinner">⟳</span> Loading OVN data…</div>`;
  } else if (ovn.error) {
    h += `<div class="card-body"><div class="err-block">${esc(ovn.error)}</div></div>`;
  } else if (ovn.data) {
    const lr = ovn.data;
    h += `<div class="card-body">
      <div class="mrow"><span class="ml">Logical router</span><span class="mv" style="font-family:monospace;font-size:10px">${esc(lr.lr_name || '')}</span></div>
      <div class="mrow"><span class="ml">UUID</span><span class="mv uuid-short" title="${escAttr(lr.lr_uuid || '')}">${(lr.lr_uuid || '').slice(0, 8) || '—'}</span></div>
    </div>`;
    if (lr.ports?.length) {
      h += `<div style="padding:0">
        <table class="data-table" style="font-size:11px">
          <thead><tr><th>Port</th><th>MAC</th><th>Networks</th><th>Chassis</th></tr></thead>
          <tbody>${lr.ports.map(port => `<tr>
            <td style="font-family:monospace;font-size:10px" title="${escAttr(port.id || '')}">${esc(abbreviateRouterPortId(port.id || ''))}</td>
            <td style="font-family:monospace;font-size:10px" title="${escAttr(port.mac || '')}">${esc(compactRouterMac(port.mac || ''))}</td>
            <td style="font-family:monospace;font-size:10px">${esc((port.networks || []).join(', ')) || '—'}</td>
            <td style="font-family:monospace;font-size:10px">${esc(port.peer || (port.gateway_hosts || []).join(', ') || '') || '—'}</td>
          </tr>`).join('')}</tbody>
        </table>
      </div>`;
    } else {
      h += `<div class="card-body" style="color:var(--dim);font-size:12px">No logical router ports found.</div>`;
    }
  } else {
    h += `<div class="card-body" style="color:var(--dim);font-size:12px">Not loaded.</div>`;
  }
  h += `</div></div>`;

  wrap.innerHTML = h;
}

// ════════════════════════════════════════════════════════════════════════════
// § KUBERNETES VIEW
// ════════════════════════════════════════════════════════════════════════════

const K8S_RES_META = {
  namespaces: { label: 'Namespaces',        icon: '📦', url: '/api/k8s/namespaces' },
  pods:       { label: 'Pods',              icon: '⬡',  url: '/api/k8s/pods'       },
  services:   { label: 'Services',          icon: '🔗', url: '/api/k8s/services'   },
  lbs:        { label: 'LoadBalancers',     icon: '⚡', url: '/api/k8s/services'   }, // filtered client-side
  pvcs:       { label: 'PV Claims',         icon: '📋', url: '/api/k8s/pvcs'       },
  pvs:        { label: 'Persistent Vols',   icon: '💾', url: '/api/k8s/pvs'        },
  crds:       { label: 'Custom Resources',  icon: '🔧', url: '/api/k8s/crds'       },
};

const k8sResCache = {}; // type → { loading, data, error }
let k8sActiveResource = null;
const k8sPageState = {}; // type → { page, filter }
const K8S_PAGE_SIZE = 50;

function getK8sPage(type) {
  if (!k8sPageState[type]) k8sPageState[type] = { page: 1, filter: '' };
  return k8sPageState[type];
}

function k8sAge(isoStr) {
  if (!isoStr) return '—';
  const diff = Math.floor((Date.now() - new Date(isoStr)) / 1000);
  if (diff < 60)         return `${diff}s`;
  if (diff < 3600)       return `${Math.floor(diff/60)}m`;
  if (diff < 86400)      return `${Math.floor(diff/3600)}h`;
  if (diff < 86400 * 30) return `${Math.floor(diff/86400)}d`;
  if (diff < 86400 * 365)return `${Math.floor(diff/86400/30)}mo`;
  return `${Math.floor(diff/86400/365)}y`;
}

async function selectK8sResource(type) {
  k8sActiveResource = type;
  // Highlight sidebar
  document.querySelectorAll('.k8s-res-item').forEach(el =>
    el.classList.toggle('selected', el.dataset.res === type));
  // Update breadcrumb node label
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
    // LoadBalancers is a client-side filter of services
    if (type === 'lbs') data = data.filter(s => s.type === 'LoadBalancer');
    k8sResCache[type] = { loading: false, data, error: null };
    // Also update services cache if we fetched services for lbs
    if (type === 'lbs' && !k8sResCache['services']) {
      k8sResCache['services'] = { loading: false, data: json.items || [], error: null };
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
    const el = document.getElementById(`k8s-cnt-${type}`);
    if (el && cached.data) el.textContent = cached.data.length;
  }
}

function renderK8sContent() {
  const wrap = document.getElementById('k8s-content-inner');
  const focusedInput = captureFocusedInput(wrap, '.dv-filter');
  if (!k8sActiveResource) {
    wrap.innerHTML = `<div style="color:var(--dim);text-align:center;padding:40px 16px">Select a resource type from the navigator.</div>`;
    return;
  }
  const type   = k8sActiveResource;
  const cached = k8sResCache[type];
  const meta   = K8S_RES_META[type];
  const ps     = getK8sPage(type);

  if (!cached || cached.loading) {
    wrap.innerHTML = `<div class="data-view-toolbar"><h2>${meta.icon} ${meta.label}</h2></div>
      <div style="color:var(--dim);padding:20px 0"><span class="spinner">⟳</span> Loading…</div>`;
    return;
  }
  if (cached.error) {
    wrap.innerHTML = `<div class="data-view-toolbar"><h2>${meta.icon} ${meta.label}</h2></div>
      <div class="err-block" style="margin:8px 0">${esc(cached.error)}</div>`;
    return;
  }

  const all      = cached.data || [];
  const filter   = ps.filter.toLowerCase();
  const filtered = filter ? all.filter(r => JSON.stringify(r).toLowerCase().includes(filter)) : all;
  const total    = filtered.length;
  const pages    = Math.max(1, Math.ceil(total / K8S_PAGE_SIZE));
  ps.page        = Math.min(ps.page, pages);
  const slice    = filtered.slice((ps.page-1)*K8S_PAGE_SIZE, ps.page*K8S_PAGE_SIZE);

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
}

function renderK8sTable(type, rows) {
  if (!rows.length) return `<div style="color:var(--dim);padding:12px 0;font-size:12px">No items match the filter.</div>`;

  const badge = (text, cls) => `<span class="k8s-badge ${cls}">${esc(text)}</span>`;

  switch (type) {
    case 'namespaces':
      return `<table class="data-table"><thead><tr>
        <th>Name</th><th>Status</th><th>Age</th>
        </tr></thead><tbody>` +
        rows.map(r => `<tr>
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
          return `<tr>
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
          return `<tr>
            <td style="color:var(--dim)">${esc(r.namespace)}</td>
            <td>${esc(r.name)}</td>
            <td>${badge(r.type, tCls)}</td>
            <td style="font-family:monospace;font-size:10px">${esc(r.cluster_ip)}</td>
            <td style="font-family:monospace;font-size:10px${r.external_ips?.length ? '' : ';color:var(--dim)'}">${esc(extIp)}</td>
            <td style="font-family:monospace;font-size:10px">${esc(r.ports)}</td>
            <td style="color:var(--dim)">${k8sAge(r.created)}</td>
          </tr>`;
        }).join('') + `</tbody></table>`;

    case 'pvs':
      return `<table class="data-table"><thead><tr>
        <th>Name</th><th>Capacity</th><th>Access</th><th>Reclaim</th><th>Status</th><th>Claim</th><th>StorageClass</th><th>Age</th>
        </tr></thead><tbody>` +
        rows.map(r => {
          const stCls = r.status === 'Bound' ? 'bound' : r.status === 'Released' ? 'released' : 'pending';
          return `<tr>
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
          return `<tr>
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
        rows.map(r => `<tr>
          <td style="font-size:10px;font-family:monospace">${esc(r.name)}</td>
          <td style="font-size:10px;color:var(--dim)">${esc(r.group)}</td>
          <td>${esc(r.kind)}</td>
          <td><span class="k8s-badge ${r.scope === 'Namespaced' ? 'clusterip' : 'lb'}">${esc(r.scope)}</span></td>
          <td style="font-size:10px;color:var(--dim)">${(r.versions || []).join(', ')}</td>
          <td style="color:var(--dim)">${k8sAge(r.created)}</td>
        </tr>`).join('') + `</tbody></table>`;

    default:
      return `<div style="color:var(--dim)">Unknown resource type.</div>`;
  }
}

// ════════════════════════════════════════════════════════════════════════════
// § STORAGE VIEW
// ════════════════════════════════════════════════════════════════════════════

async function loadVolumes(force = false) {
  if (volState.loading) return;
  if (volState.data && !force) { renderStorageView(); return; }
  volState.loading = true;
  volState.data    = null;
  renderStorageView();
  try {
    const resp = await fetch('/api/volumes');
    const json = await resp.json();
    volState.data       = json.volumes || [];
    volState.allProjects = json.all_projects || false;
    volState.page       = 1;
    if (json.error) onLog({ node: '-', message: `Volumes API error: ${json.error}`, color: 'error' });
  } catch (e) {
    volState.data = [];
    onLog({ node: '-', message: `Volumes API error: ${e}`, color: 'error' });
  } finally {
    volState.loading = false;
    renderStorageView();
  }
}

function renderStorageView() {
  const wrap = document.getElementById('vol-wrap');
  const focusedInput = captureFocusedInput(wrap, '.dv-filter');
  if (volState.loading) {
    wrap.innerHTML = `<div class="data-view-toolbar"><h2>Cinder Volumes <span class="hint">Datastores</span></h2></div><div style="color:var(--dim);padding:20px 0"><span class="spinner">⟳</span> Loading volumes…</div>`;
    return;
  }
  if (!volState.data) { wrap.innerHTML = ''; return; }

  const scopeNote = volState.allProjects
    ? `<span style="color:var(--green);font-size:11px">● All projects (admin)</span>`
    : `<span style="color:var(--yellow);font-size:11px">● Project scope only</span>`;

  const filtered = applyFilter(volState.data, volState.filter, ['name','status','volume_type','project_id']);
  const { page, pageSize } = volState;
  const paged = paginate(filtered, page, pageSize);

  let rows = '';
  for (const v of paged) {
    const stCls = { available:'st-available', 'in-use':'st-inuse', error:'st-error', 'error_deleting':'st-error' }[v.status] || '';
    const att   = v.attached_to.length
      ? `<span class="sdot green"></span>${v.attached_to.length} server(s)` : `<span style="color:var(--dim)">—</span>`;
    const boot  = v.bootable  ? `<span class="tag-vol">boot</span>` : '—';
    const enc   = v.encrypted ? `<span class="tag-amp">enc</span>`  : '—';
    rows += `<tr>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">${esc(v.name)}</td>
      <td><span class="${stCls}">${esc(v.status)}</span></td>
      <td>${v.size_gb} GB</td>
      <td>${esc(v.volume_type) || '<span style="color:var(--dim)">—</span>'}</td>
      <td>${att}</td>
      <td>${boot}</td>
      <td>${enc}</td>
      <td class="uuid-short" title="${esc(v.project_id)}">${v.project_id.slice(0, 8) || '—'}</td>
    </tr>`;
  }

  wrap.innerHTML = `
    <div class="data-view-toolbar">
      <h2>Cinder Volumes <span class="hint">Datastores</span></h2>
      ${scopeNote}
      <input class="dv-filter" type="text" placeholder="Filter volumes…"
        value="${esc(volState.filter)}" oninput="volState.filter=this.value;volState.page=1;renderStorageView()">
      <span style="font-size:11px;color:var(--dim)">${filtered.length} of ${volState.data.length} volumes</span>
    </div>
    <table class="data-table">
      <thead><tr>
        <th>Name <span class="hint">Datastore</span></th>
        <th>Status</th>
        <th>Size</th>
        <th>Type <span class="hint">Storage Policy</span></th>
        <th>Attached <span class="hint">Mounted</span></th>
        <th>Bootable</th>
        <th>Encrypted</th>
        <th>Project</th>
      </tr></thead>
      <tbody>${rows || '<tr><td colspan="8" style="text-align:center;color:var(--dim);padding:20px">No volumes match the filter.</td></tr>'}</tbody>
    </table>
    ${buildPager(volState, filtered.length, 'volState', 'renderStorageView')}`;
  restoreFocusedInput(wrap, focusedInput);
}
