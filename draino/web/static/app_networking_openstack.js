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

function syncNetworkingDetailShell() {
  if (activeView === 'networking' && typeof renderNetworkingWorkspace === 'function') {
    renderNetworkingWorkspace();
  }
}

async function fetchJsonWithTimeout(url, timeoutMs = 8000) {
  const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
  try {
    const resp = await fetch(url, controller ? { signal: controller.signal } : undefined);
    return await resp.json();
  } catch (e) {
    if (e?.name === 'AbortError') {
      throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)}s`);
    }
    throw e;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

function armDetailWatchdog(kind, id, timeoutMs, onTimeout) {
  return setTimeout(() => {
    try {
      onTimeout();
    } catch (_) {
      // ignore watchdog callback errors
    }
  }, timeoutMs);
}

async function loadNetworks(force = false) {
  if (typeof hasOpenStackAuth === 'function' && !hasOpenStackAuth()) {
    const wrap = document.getElementById('net-wrap');
    if (wrap) wrap.innerHTML = renderOpenStackUnavailablePanel('Networks', 'This view currently relies on OpenStack networking data. Provide OpenStack credentials to enable it.');
    return;
  }
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
    const routerPill = n.router_connected && n.router_id
      ? `<button class="btn sm" style="padding:1px 8px;font-size:11px" onclick="event.stopPropagation();navigateToRouterFromNetwork('${escAttr(n.router_id)}')">Connected</button>`
      : '<span style="color:var(--dim)">—</span>';
    const rowSel = selectedNetwork === n.id ? ' selected' : '';
    rows += `<tr class="${rowSel}" style="cursor:pointer" data-net-id="${escAttr(n.id)}" onclick="selectNetwork('${escAttr(n.id)}')">
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">${esc(n.name)}</td>
      <td><span class="${stCls}">${esc(n.status)}</span></td>
      <td>${adm}</td>
      <td>${esc(n.network_type) || '<span style="color:var(--dim)">—</span>'}</td>
      <td>${shared}</td>
      <td>${ext}</td>
      <td>${routerPill}</td>
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
        <th>Router</th>
        <th>Subnets</th>
        <th>Project</th>
      </tr></thead>
      <tbody>${rows || '<tr><td colspan="9" style="text-align:center;color:var(--dim);padding:20px">No networks match the filter.</td></tr>'}</tbody>
    </table>
    ${buildPager(netState, filtered.length, 'netState', 'renderNetworksView')}`;
  restoreFocusedInput(wrap, focusedInput);
}

async function navigateToRouterFromNetwork(routerId) {
  if (!routerId) return;
  switchNetworkingSection('routers');
  await loadRouters();
  await selectRouter(routerId);
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
  syncNetworkingDetailShell();
  netDetailState.loading = true;
  netDetailState.data    = null;
  renderNetworkDetail();
  const watchdog = armDetailWatchdog('network', id, 12000, () => {
    if (selectedNetwork !== id || !netDetailState.loading) return;
    netDetailState.loading = false;
    netDetailState.data = { error: 'Timed out after 12s while loading network details' };
    renderNetworkDetail();
  });
  try {
    const json = await fetchJsonWithTimeout(`/api/networks/${encodeURIComponent(id)}`, 10000);
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Neutron');
    if (json.error) throw new Error(json.error);
    const meta = netState.data?.find(n => n.id === id) || {};
    netDetailState.data = { ...meta, ...json.network };
  } catch (e) {
    netDetailState.data = { error: String(e) };
  } finally {
    clearTimeout(watchdog);
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
    const json = await fetchJsonWithTimeout(`/api/networks/${encodeURIComponent(selectedNetwork)}`, 10000);
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
    const json = await fetchJsonWithTimeout(`/api/networks/${encodeURIComponent(id)}/ovn`, 8000);
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
  syncNetworkingDetailShell();
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
  try {

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
  h += renderNetworkOverlayCard(nd);

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
    const lsUuid = String(ls.ls_uuid || '');
    h += `<div class="card-body">
      <div class="mrow"><span class="ml">Name</span><span class="mv" style="font-family:monospace;font-size:10px">${esc(ls.ls_name)}</span></div>
      <div class="mrow"><span class="ml">UUID</span><span class="mv uuid-short" style="font-family:monospace;font-size:10px;cursor:pointer" title="${escAttr(lsUuid)}" onclick="navigator.clipboard?.writeText('${escAttr(lsUuid)}')">${lsUuid ? `${lsUuid.slice(0,8)}…` : '—'}</span></div>
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
        const portId = String(p.id || '');
        const isSpecial = portId.startsWith('provnet-') || p.type === 'localnet';
        const isSel = netDetailState.ovnSelectedPort === portId;
        const portIdCell = isSpecial
          ? `<span style="color:var(--dim);font-size:10px;font-family:monospace">${esc(portId || '—')}</span>`
          : `<span style="font-family:monospace;font-size:10px" title="${escAttr(portId)}">${portId ? `${portId.slice(0,8)}…` : '—'}</span>`;
        h += `<tr class="${isSel ? 'selected' : ''}" style="cursor:pointer" onclick="selectOvnPort('${escAttr(portId)}')">
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
  syncNetworkingDetailShell();
  } catch (e) {
    wrap.innerHTML = `<div class="net-detail-inner"><div class="err-block">Detail render failed: ${esc(String(e))}</div></div>`;
  }
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
    const json = await fetchJsonWithTimeout(`/api/ovn/lsp/${encodeURIComponent(portId)}`, 8000);
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

