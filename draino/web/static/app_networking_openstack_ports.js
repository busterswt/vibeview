'use strict';

async function loadPorts(force = false) {
  if (typeof hasOpenStackAuth === 'function' && !hasOpenStackAuth()) {
    const wrap = document.getElementById('port-wrap');
    if (wrap) wrap.innerHTML = renderOpenStackUnavailablePanel('Ports', 'This view currently relies on OpenStack port inventory. Provide OpenStack credentials to enable it.');
    return;
  }
  if (portState.loading) return;
  if (portState.data && !force) {
    renderPortsView();
    return;
  }
  portState.loading = true;
  portState.data = null;
  renderPortsView();
  try {
    const resp = await fetch('/api/ports');
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Neutron');
    portState.data = json.ports || [];
    portState.page = 1;
    if (json.error) appendPortError(json.error);
  } catch (e) {
    portState.data = [];
    appendPortError(String(e));
  } finally {
    portState.loading = false;
    renderPortsView();
  }
}

function portAttachedLink(item) {
  const label = item.attached_name || item.attached_id || '';
  if (!label) return '<span style="color:var(--dim)">—</span>';
  if (item.attached_kind === 'instance' && item.attached_id && item.compute_host) {
    return renderObjectLink(label, `navigateToInstanceDetail('${escAttr(item.attached_id)}','${escAttr(item.compute_host)}')`);
  }
  if (item.attached_kind === 'router' && item.attached_id) {
    return renderObjectLink(label, `navigateToRouterDetail('${escAttr(item.attached_id)}')`);
  }
  if (item.attached_kind === 'load-balancer' && item.attached_id) {
    return renderObjectLink(label, `navigateToLoadBalancerDetail('${escAttr(item.attached_id)}')`);
  }
  return esc(label);
}

function portRouterLink(item) {
  if (!item.connected_router_id) return '<span style="color:var(--dim)">—</span>';
  return renderObjectLink(item.connected_router_name || item.connected_router_id, `navigateToRouterDetail('${escAttr(item.connected_router_id)}')`);
}

function portProjectCell(item) {
  const projectId = String(item.project_id || '');
  if (!projectId) return '—';
  return `<span class="uuid-short" title="${esc(projectId)}">${esc(projectId.slice(0, 12))}</span>`;
}

function renderPortsView() {
  const wrap = document.getElementById('port-wrap');
  if (!wrap) return;
  const focusedInput = captureFocusedInput(wrap, '.dv-filter');
  if (portState.loading) {
    wrap.innerHTML = `<div class="data-view-toolbar"><h2>Neutron Ports <span class="hint">Interfaces / Attachments</span></h2></div><div style="color:var(--dim);padding:20px 0"><span class="spinner">⟳</span> Loading ports…</div>`;
    return;
  }
  if (!portState.data) {
    wrap.innerHTML = '';
    return;
  }
  const filtered = applyFilter(portState.data, portState.filter, ['name', 'status', 'network_name', 'device_owner', 'attached_name', 'attached_id', 'project_id', 'mac_address', 'connected_router_name']);
  const paged = paginate(filtered, portState.page, portState.pageSize);
  wrap.innerHTML = `
    <div class="data-view-toolbar">
      <h2>Neutron Ports <span class="hint">Interfaces / Attachments</span></h2>
      <input class="dv-filter" type="text" placeholder="Filter ports…"
        value="${esc(portState.filter)}" oninput="portState.filter=this.value;portState.page=1;renderPortsView()">
      <span style="font-size:11px;color:var(--dim)">${filtered.length} of ${portState.data.length} ports</span>
    </div>
    <table class="data-table">
      <thead><tr>
        <th>Name</th>
        <th>Status</th>
        <th>Admin</th>
        <th>Network</th>
        <th>Fixed IPs</th>
        <th>Attached</th>
        <th>Router</th>
        <th>Floating IPs</th>
        <th>Project</th>
      </tr></thead>
      <tbody>${paged.map((item) => {
        const rowSel = selectedPort === item.id ? ' selected' : '';
        const stCls = item.status === 'ACTIVE' ? 'st-active' : item.status === 'DOWN' ? 'st-down' : 'st-pending';
        return `<tr class="${rowSel}" style="cursor:pointer" data-port-id="${escAttr(item.id)}" onclick="selectPort('${escAttr(item.id)}')">
          <td><div>${esc(item.name || item.id)}</div><div class="uuid-short">${esc(item.id || '')}</div></td>
          <td><span class="${stCls}">${esc(item.status || 'UNKNOWN')}</span></td>
          <td>${esc(item.admin_state || '—')}</td>
          <td>${item.network_id ? renderObjectLink(item.network_name || item.network_id, `navigateToNetworkDetail('${escAttr(item.network_id)}')`) : '<span style="color:var(--dim)">—</span>'}</td>
          <td style="font-family:monospace;font-size:10px">${esc((item.fixed_ip_addresses || []).join(', ') || '—')}</td>
          <td>${portAttachedLink(item)}</td>
          <td>${portRouterLink(item)}</td>
          <td style="font-family:monospace;font-size:10px">${esc((item.floating_ips || []).map((ip) => ip.address).join(', ') || '—')}</td>
          <td>${portProjectCell(item)}</td>
        </tr>`;
      }).join('') || '<tr><td colspan="9" style="text-align:center;color:var(--dim);padding:20px">No ports match the filter.</td></tr>'}</tbody>
    </table>
    ${buildPager(portState, filtered.length, 'portState', 'renderPortsView')}`;
  restoreFocusedInput(wrap, focusedInput);
}

function appendPortError(msg) {
  onLog({ node: '-', message: `Ports API error: ${msg}`, color: 'error' });
}

async function selectPort(id) {
  selectedPort = id;
  document.querySelectorAll('#port-wrap tr[data-port-id]').forEach((row) => {
    row.classList.toggle('selected', row.dataset.portId === id);
  });
  document.getElementById('port-detail-wrap')?.classList.add('open');
  syncNetworkingDetailShell();
  portDetailState.loading = true;
  portDetailState.data = null;
  renderPortDetail();
  const watchdog = armDetailWatchdog('port', id, 12000, () => {
    if (selectedPort !== id || !portDetailState.loading) return;
    portDetailState.loading = false;
    portDetailState.data = { error: 'Timed out after 12s while loading port details' };
    renderPortDetail();
  });
  try {
    const json = await fetchJsonWithTimeout(`/api/ports/${encodeURIComponent(id)}`, 10000);
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Neutron');
    if (json.error) throw new Error(json.error);
    const meta = portState.data?.find((item) => item.id === id) || {};
    portDetailState.data = { ...meta, ...(json.port || {}) };
  } catch (e) {
    portDetailState.data = { error: String(e) };
  } finally {
    clearTimeout(watchdog);
    portDetailState.loading = false;
    renderPortDetail();
  }
}

function closePortDetail() {
  selectedPort = null;
  portDetailState.data = null;
  document.getElementById('port-detail-wrap')?.classList.remove('open');
  document.querySelectorAll('#port-wrap tr[data-port-id]').forEach((row) => row.classList.remove('selected'));
  syncNetworkingDetailShell();
}

function renderPortDetail() {
  const wrap = document.getElementById('port-detail-wrap');
  if (!wrap) return;
  if (portDetailState.loading) {
    wrap.innerHTML = `<div class="net-detail-inner"><div style="color:var(--dim);padding:20px 0"><span class="spinner">⟳</span> Loading…</div></div>`;
    return;
  }
  const detail = portDetailState.data;
  if (!detail) {
    wrap.innerHTML = '';
    return;
  }
  if (detail.error) {
    wrap.innerHTML = `<div class="net-detail-inner"><div class="err-block">${esc(detail.error)}</div></div>`;
    return;
  }
  wrap.innerHTML = `
    <div class="net-detail-inner">
      <div class="net-detail-head">
        <strong style="font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:230px">${esc(detail.name || detail.id || 'Port')}</strong>
        <div style="display:flex;gap:6px;align-items:center;flex-shrink:0">
          <span class="${detail.status === 'ACTIVE' ? 'st-active' : detail.status === 'DOWN' ? 'st-down' : 'st-pending'}" style="font-size:11px">${esc(detail.status || 'UNKNOWN')}</span>
          <button class="btn" style="padding:1px 7px;font-size:11px" onclick="closePortDetail()">✕</button>
        </div>
      </div>
      <div style="font-family:monospace;font-size:10px;color:var(--dim);word-break:break-all;margin-bottom:10px">${esc(detail.id || '')}</div>

      <div class="card" style="margin-bottom:10px">
        <div class="card-title">Attachment</div>
        <div class="card-body">
          <div class="mrow"><span class="ml">Attached resource</span><span class="mv">${portAttachedLink(detail)}</span></div>
          <div class="mrow"><span class="ml">Device owner</span><span class="mv">${esc(detail.device_owner || '—')}</span></div>
          <div class="mrow"><span class="ml">Device ID</span><span class="mv mono">${esc(detail.device_id || '—')}</span></div>
          <div class="mrow"><span class="ml">Router</span><span class="mv">${portRouterLink(detail)}</span></div>
          <div class="mrow"><span class="ml">Admin state</span><span class="mv">${esc(detail.admin_state || '—')}</span></div>
        </div>
      </div>

      <div class="card" style="margin-bottom:10px">
        <div class="card-title">Network Path</div>
        <div class="card-body">
          <div class="mrow"><span class="ml">Network</span><span class="mv">${detail.network_id ? renderObjectLink(detail.network_name || detail.network_id, `navigateToNetworkDetail('${escAttr(detail.network_id)}')`) : '—'}</span></div>
          <div class="mrow"><span class="ml">MAC</span><span class="mv mono">${esc(detail.mac_address || '—')}</span></div>
          <div class="mrow"><span class="ml">Project</span><span class="mv mono">${esc(detail.project_id || '—')}</span></div>
          <div class="mrow"><span class="ml">Fixed IPs</span><span class="mv mono">${esc((detail.fixed_ip_addresses || []).join(', ') || '—')}</span></div>
        </div>
      </div>

      <div class="card" style="margin-bottom:10px">
        <div class="card-title">Subnets</div>
        <div class="card-body" style="padding:0">
          ${(detail.subnets || []).length ? `<table class="data-table" style="border:none;margin-bottom:0;font-size:11px">
            <thead><tr><th>Subnet</th><th>CIDR</th><th>IP</th></tr></thead>
            <tbody>${(detail.subnets || []).map((subnet) => `<tr>
              <td>${esc(subnet.name || subnet.id || '—')}</td>
              <td style="font-family:monospace;font-size:10px">${esc(subnet.cidr || '—')}</td>
              <td style="font-family:monospace;font-size:10px">${esc(subnet.ip_address || '—')}</td>
            </tr>`).join('')}</tbody>
          </table>` : '<div style="padding:10px;color:var(--dim);font-size:12px">No subnet records were returned for this port.</div>'}
        </div>
      </div>

      <div class="card" style="margin-bottom:10px">
        <div class="card-title">Security Groups</div>
        <div class="card-body">
          ${(detail.security_groups || []).length
            ? (detail.security_groups || []).map((group) => `<div class="mrow"><span class="ml">${group.id ? renderObjectLink(group.name || group.id, `switchNetworkingSection('securitygroups');selectSecurityGroup('${escAttr(group.id)}')`) : esc(group.name || '—')}</span><span class="mv mono">${esc(group.id || '')}</span></div>`).join('')
            : '<div style="color:var(--dim);font-size:12px">No security groups attached.</div>'}
        </div>
      </div>

      <div class="card">
        <div class="card-title">Floating IPs</div>
        <div class="card-body">
          ${(detail.floating_ips || []).length
            ? (detail.floating_ips || []).map((ip) => `<div class="mrow"><span class="ml mono">${esc(ip.address || '—')}</span><span class="mv">${esc(ip.status || 'UNKNOWN')}</span></div>`).join('')
            : '<div style="color:var(--dim);font-size:12px">No floating IP is associated with this port.</div>'}
        </div>
      </div>
    </div>`;
}
