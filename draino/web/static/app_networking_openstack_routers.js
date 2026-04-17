'use strict';

async function loadRouters(force = false) {
  if (typeof hasOpenStackAuth === 'function' && !hasOpenStackAuth()) {
    const wrap = document.getElementById('router-wrap');
    if (wrap) wrap.innerHTML = renderOpenStackUnavailablePanel('Routers', 'This view currently relies on OpenStack router inventory. Provide OpenStack credentials to enable it.');
    return;
  }
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
  syncNetworkingDetailShell();
  routerDetailState.loading = true;
  routerDetailState.data = null;
  renderRouterDetail();
  const watchdog = armDetailWatchdog('router', id, 12000, () => {
    if (selectedRouter !== id || !routerDetailState.loading) return;
    routerDetailState.loading = false;
    routerDetailState.data = { error: 'Timed out after 12s while loading router details' };
    renderRouterDetail();
  });
  try {
    const json = await fetchJsonWithTimeout(`/api/routers/${encodeURIComponent(id)}`, 10000);
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Neutron');
    if (json.error) throw new Error(json.error);
    const meta = routerState.data?.find(r => r.id === id) || {};
    routerDetailState.data = { ...meta, ...json.router };
  } catch (e) {
    routerDetailState.data = { error: String(e) };
  } finally {
    clearTimeout(watchdog);
    routerDetailState.loading = false;
    renderRouterDetail();
  }
  loadRouterOvn(id);
}

async function loadRouterOvn(id) {
  routerDetailState.ovn = { loading: true, data: null, error: null };
  renderRouterDetail();
  try {
    const json = await fetchJsonWithTimeout(`/api/routers/${encodeURIComponent(id)}/ovn`, 8000);
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
  syncNetworkingDetailShell();
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
  try {

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
  h += renderRouterOverlayCard(rd);

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
  syncNetworkingDetailShell();
  } catch (e) {
    wrap.innerHTML = `<div class="net-detail-inner"><div class="err-block">Detail render failed: ${esc(String(e))}</div></div>`;
  }
}
