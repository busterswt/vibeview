'use strict';

async function loadLoadBalancers(force = false) {
  if (typeof hasOpenStackAuth === 'function' && !hasOpenStackAuth()) {
    const wrap = document.getElementById('lb-wrap');
    if (wrap) wrap.innerHTML = renderOpenStackUnavailablePanel('Load Balancers', 'This view currently relies on Octavia inventory. Provide OpenStack credentials to enable it.');
    return;
  }
  if (lbState.loading) return;
  if (lbState.data && !force) { renderLoadBalancersView(); return; }
  lbState.loading = true;
  lbState.data = null;
  renderLoadBalancersView();
  try {
    const resp = await fetch('/api/load-balancers');
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    lbState.data = json.load_balancers || [];
    lbState.page = 1;
    if (json.error) appendLoadBalancerError(json.error);
  } catch (e) {
    lbState.data = [];
    appendLoadBalancerError(String(e));
  } finally {
    lbState.loading = false;
    renderLoadBalancersView();
  }
}

function renderLoadBalancersView() {
  const wrap = document.getElementById('lb-wrap');
  if (!wrap) return;
  const focusedInput = captureFocusedInput(wrap, '.dv-filter');
  if (lbState.loading) {
    wrap.innerHTML = `<div class="data-view-toolbar"><h2>Load Balancers <span class="hint">LBaaS / Octavia</span></h2></div><div style="color:var(--dim);padding:20px 0"><span class="spinner">⟳</span> Loading load balancers…</div>`;
    return;
  }
  if (!lbState.data) { wrap.innerHTML = ''; return; }
  const filtered = applyFilter(lbState.data, lbState.filter, ['name', 'vip_address', 'floating_ip', 'project_id', 'provisioning_status', 'operating_status']);
  const paged = paginate(filtered, lbState.page, lbState.pageSize);
  wrap.innerHTML = `
    <div class="data-view-toolbar">
      <h2>Load Balancers <span class="hint">LBaaS / Octavia</span></h2>
      <input class="dv-filter" type="text" placeholder="Filter load balancers…"
        value="${esc(lbState.filter)}" oninput="lbState.filter=this.value;lbState.page=1;renderLoadBalancersView()">
      <span style="font-size:11px;color:var(--dim)">${filtered.length} of ${lbState.data.length} load balancers</span>
    </div>
    <table class="data-table">
      <thead><tr>
        <th>Name</th>
        <th>Status</th>
        <th>Provisioning</th>
        <th>VIP</th>
        <th>Floating IP</th>
        <th>Project</th>
        <th>Listeners</th>
        <th>Pools</th>
        <th>Amphorae</th>
      </tr></thead>
      <tbody>${paged.map(item => {
        const opCls = item.operating_status === 'ONLINE' ? 'st-active' : item.operating_status === 'ERROR' ? 'st-error' : 'st-pending';
        const provCls = item.provisioning_status === 'ACTIVE' ? 'st-active' : item.provisioning_status === 'ERROR' ? 'st-error' : 'st-pending';
        const rowSel = selectedLoadBalancer === item.id ? ' selected' : '';
        return `<tr class="${rowSel}" style="cursor:pointer" data-lb-id="${escAttr(item.id)}" onclick="selectLoadBalancer('${escAttr(item.id)}')">
          <td><div>${esc(item.name)}</div><div class="uuid-short">${esc(item.id)}</div></td>
          <td><span class="${opCls}">${esc(item.operating_status || 'UNKNOWN')}</span></td>
          <td><span class="${provCls}">${esc(item.provisioning_status || 'UNKNOWN')}</span></td>
          <td>${esc(item.vip_address || '—')}</td>
          <td>${item.floating_ip ? esc(item.floating_ip) : '<span style="color:var(--dim)">—</span>'}</td>
          <td class="uuid-short" title="${esc(item.project_id || '')}">${esc((item.project_id || '').slice(0, 8) || '—')}</td>
          <td>${esc(String(item.listener_count ?? 0))}</td>
          <td>${esc(String(item.pool_count ?? 0))}</td>
          <td>${esc(String(item.amphora_count ?? 0))}</td>
        </tr>`;
      }).join('') || '<tr><td colspan="9" style="text-align:center;color:var(--dim);padding:20px">No load balancers match the filter.</td></tr>'}</tbody>
    </table>
    ${buildPager(lbState, filtered.length, 'lbState', 'renderLoadBalancersView')}`;
  restoreFocusedInput(wrap, focusedInput);
}

function appendLoadBalancerError(msg) {
  onLog({ node: '-', message: `Load balancers API error: ${msg}`, color: 'error' });
}

async function selectLoadBalancer(id) {
  selectedLoadBalancer = id;
  document.querySelectorAll('#lb-wrap tr[data-lb-id]').forEach(r => {
    r.classList.toggle('selected', r.dataset.lbId === id);
  });
  document.getElementById('lb-detail-wrap').classList.add('open');
  syncNetworkingDetailShell();
  lbDetailState.loading = true;
  lbDetailState.data = null;
  lbDetailState.vipOvn = { loading: false, data: null, error: null };
  renderLoadBalancerDetail();
  const watchdog = armDetailWatchdog('loadbalancer', id, 12000, () => {
    if (selectedLoadBalancer !== id || !lbDetailState.loading) return;
    lbDetailState.loading = false;
    lbDetailState.data = { error: 'Timed out after 12s while loading load balancer details' };
    renderLoadBalancerDetail();
  });
  try {
    const json = await fetchJsonWithTimeout(`/api/load-balancers/${encodeURIComponent(id)}`, 10000);
    if (json.api_issue) recordApiIssue(json.api_issue);
    if (json.error) throw new Error(json.error);
    const meta = lbState.data?.find(item => item.id === id) || {};
    lbDetailState.data = { ...meta, ...json.load_balancer };
  } catch (e) {
    lbDetailState.data = { error: String(e) };
  } finally {
    clearTimeout(watchdog);
    lbDetailState.loading = false;
    renderLoadBalancerDetail();
  }
  const vipPortId = lbDetailState.data?.vip_port?.id || lbDetailState.data?.vip_port_id || '';
  if (vipPortId) {
    lbDetailState.vipOvn = { loading: true, data: null, error: null };
    renderLoadBalancerDetail();
    try {
      const json = await fetchJsonWithTimeout(`/api/ovn/lsp/${encodeURIComponent(vipPortId)}`, 8000);
      if (json.error) throw new Error(json.error);
      lbDetailState.vipOvn = { loading: false, data: json.port, error: null };
    } catch (e) {
      lbDetailState.vipOvn = { loading: false, data: null, error: String(e) };
    }
    renderLoadBalancerDetail();
  }
}

function closeLoadBalancerDetail() {
  selectedLoadBalancer = null;
  lbDetailState.data = null;
  lbDetailState.vipOvn = { loading: false, data: null, error: null };
  document.getElementById('lb-detail-wrap').classList.remove('open');
  document.querySelectorAll('#lb-wrap tr[data-lb-id]').forEach(r => r.classList.remove('selected'));
  syncNetworkingDetailShell();
}

function renderLoadBalancerDetail() {
  const wrap = document.getElementById('lb-detail-wrap');
  if (!wrap) return;
  if (lbDetailState.loading) {
    wrap.innerHTML = `<div class="net-detail-inner"><div style="color:var(--dim);padding:20px 0"><span class="spinner">⟳</span> Loading…</div></div>`;
    return;
  }
  const ld = lbDetailState.data;
  if (!ld) { wrap.innerHTML = ''; return; }
  if (ld.error) {
    wrap.innerHTML = `<div class="net-detail-inner"><div class="err-block">${esc(ld.error)}</div></div>`;
    return;
  }
  try {
  const provCls = ld.provisioning_status === 'ACTIVE' ? 'st-active' : ld.provisioning_status === 'ERROR' ? 'st-error' : 'st-pending';
  let h = `<div class="net-detail-inner">
    <div class="net-detail-head">
      <strong style="font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:230px">${esc(ld.name)}</strong>
      <div style="display:flex;gap:6px;align-items:center;flex-shrink:0">
        <span class="${provCls}" style="font-size:11px">${esc(ld.provisioning_status || 'UNKNOWN')}</span>
        <button class="btn" style="padding:1px 7px;font-size:11px" onclick="closeLoadBalancerDetail()">✕</button>
      </div>
    </div>
    <div style="font-family:monospace;font-size:10px;color:var(--dim);word-break:break-all;margin-bottom:10px">${esc(ld.id || '')}</div>

    <div class="card" style="margin-bottom:10px">
      <div class="card-title">Properties</div>
      <div class="card-body">
        <div class="mrow"><span class="ml">Operating status</span><span class="mv">${esc(ld.operating_status || 'UNKNOWN')}</span></div>
        <div class="mrow"><span class="ml">Provisioning status</span><span class="mv">${esc(ld.provisioning_status || 'UNKNOWN')}</span></div>
        <div class="mrow"><span class="ml">VIP address</span><span class="mv">${esc(ld.vip_address || '—')}</span></div>
        <div class="mrow"><span class="ml">Floating IP</span><span class="mv">${esc(ld.floating_ip || '—')}</span></div>
        <div class="mrow"><span class="ml">VIP subnet</span><span class="mv" style="font-family:monospace;font-size:10px">${esc(ld.vip_subnet_id || '—')}</span></div>
        <div class="mrow"><span class="ml">Project</span><span class="mv" style="font-family:monospace;font-size:10px">${esc(ld.project_id || '—')}</span></div>
        <div class="mrow"><span class="ml">Flavor</span><span class="mv">${esc(ld.flavor_id || '—')}</span></div>
      </div>
    </div>`;
  h += renderLoadBalancerOverlayCard(ld);

  const vipPort = ld.vip_port || null;
  h += `<div class="card" style="margin-bottom:10px">
    <div class="card-title">VIP Port</div>
    <div class="card-body">`;
  if (!vipPort || !vipPort.id) {
    h += `<div style="color:var(--dim);font-size:12px">VIP port details are not available.</div>`;
  } else {
    h += `
      <div class="mrow"><span class="ml">Port ID</span><span class="mv" style="font-family:monospace;font-size:10px;word-break:break-all">${esc(vipPort.id || '—')}</span></div>
      <div class="mrow"><span class="ml">Name</span><span class="mv">${esc(vipPort.name || '—')}</span></div>
      <div class="mrow"><span class="ml">Status</span><span class="mv">${esc(vipPort.status || '—')}</span></div>
      <div class="mrow"><span class="ml">IP address</span><span class="mv" style="font-family:monospace">${esc(vipPort.ip_address || '—')}</span></div>
      <div class="mrow"><span class="ml">MAC</span><span class="mv" style="font-family:monospace">${esc(vipPort.mac_address || '—')}</span></div>
      <div class="mrow"><span class="ml">Network ID</span><span class="mv" style="font-family:monospace;font-size:10px;word-break:break-all">${esc(vipPort.network_id || '—')}</span></div>
      <div class="mrow"><span class="ml">Subnet ID</span><span class="mv" style="font-family:monospace;font-size:10px;word-break:break-all">${esc(vipPort.subnet_id || '—')}</span></div>
      <div class="mrow"><span class="ml">Device owner</span><span class="mv">${esc(vipPort.device_owner || '—')}</span></div>
      <div class="mrow"><span class="ml">Device ID</span><span class="mv" style="font-family:monospace;font-size:10px;word-break:break-all">${esc(vipPort.device_id || '—')}</span></div>
      <div class="mrow"><span class="ml">Admin state</span><span class="mv">${vipPort.admin_state_up ? 'UP' : 'DOWN'}</span></div>
      <div class="mrow"><span class="ml">Project</span><span class="mv" style="font-family:monospace;font-size:10px;word-break:break-all">${esc(vipPort.project_id || '—')}</span></div>`;
  }
  h += `</div></div>`;

  h += `<div class="card" style="margin-bottom:10px">
    <div class="card-title">OVN Logical Port</div>`;
  if (!vipPort || !vipPort.id) {
    h += `<div class="card-body" style="color:var(--dim);font-size:12px">VIP port ID is not available.</div>`;
  } else if (lbDetailState.vipOvn.loading) {
    h += `<div class="card-body" style="color:var(--dim);font-size:12px"><span class="spinner">⟳</span> Loading OVN logical port details…</div>`;
  } else if (lbDetailState.vipOvn.error) {
    h += `<div class="card-body"><div class="err-block">${esc(lbDetailState.vipOvn.error)}</div></div>`;
  } else if (lbDetailState.vipOvn.data) {
    const ovn = lbDetailState.vipOvn.data;
    const ext = ovn.external_ids || {};
    const opts = ovn.options || {};
    h += `<div class="card-body">
      <div class="mrow"><span class="ml">UUID</span><span class="mv" style="font-family:monospace;font-size:10px;word-break:break-all">${esc(vipPort.id)}</span></div>
      ${ovn.up !== null ? `<div class="mrow"><span class="ml">Up</span><span class="mv ${ovn.up ? 'green' : 'red'}">${ovn.up ? 'true' : 'false'}</span></div>` : ''}
      ${ovn.enabled !== null ? `<div class="mrow"><span class="ml">Enabled</span><span class="mv ${ovn.enabled ? 'green' : 'red'}">${ovn.enabled ? 'true' : 'false'}</span></div>` : ''}
      ${ovn.tag !== null ? `<div class="mrow"><span class="ml">Tag (VLAN)</span><span class="mv" style="font-family:monospace">${esc(String(ovn.tag))}</span></div>` : ''}
      ${opts['requested-chassis'] ? `<div class="mrow"><span class="ml">Chassis</span><span class="mv" style="font-family:monospace;font-size:10px;word-break:break-all">${esc(opts['requested-chassis'])}</span></div>` : ''}
      ${ovn.dynamic_addresses ? `<div class="mrow"><span class="ml">Dynamic addr</span><span class="mv" style="font-family:monospace;font-size:10px;word-break:break-all">${esc(ovn.dynamic_addresses)}</span></div>` : ''}
      ${ovn.port_security?.length ? `<div class="mrow"><span class="ml">Port security</span><span class="mv" style="font-family:monospace;font-size:10px;word-break:break-all">${esc(ovn.port_security.join(', '))}</span></div>` : ''}
      ${ext['neutron:port_name'] ? `<div class="mrow"><span class="ml">Port name</span><span class="mv">${esc(ext['neutron:port_name'])}</span></div>` : ''}
      ${ext['neutron:device_owner'] ? `<div class="mrow"><span class="ml">Device owner</span><span class="mv">${esc(ext['neutron:device_owner'])}</span></div>` : ''}
      ${ext['neutron:device_id'] ? `<div class="mrow"><span class="ml">Device ID</span><span class="mv" style="font-family:monospace;font-size:10px;word-break:break-all">${esc(ext['neutron:device_id'])}</span></div>` : ''}
      ${ext['neutron:network_id'] ? `<div class="mrow"><span class="ml">Network ID</span><span class="mv" style="font-family:monospace;font-size:10px;word-break:break-all">${esc(ext['neutron:network_id'])}</span></div>` : ''}
      ${ext['neutron:project_id'] ? `<div class="mrow"><span class="ml">Project ID</span><span class="mv" style="font-family:monospace;font-size:10px;word-break:break-all">${esc(ext['neutron:project_id'])}</span></div>` : ''}
    </div>`;
  } else {
    h += `<div class="card-body" style="color:var(--dim);font-size:12px">OVN logical port details are not available.</div>`;
  }
  h += `</div>`;

  const listeners = ld.listeners || [];
  h += `<div class="card" style="margin-bottom:10px">
    <div class="card-title">Listeners</div>
    <div class="card-body">`;
  if (!listeners.length) {
    h += `<div style="color:var(--dim);font-size:12px">No listeners found.</div>`;
  } else {
    h += listeners.map(listener => `<div style="padding:7px 0;border-bottom:1px solid #f0f2f5">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:10px">
        <div>
          <div style="font-weight:600">${esc(listener.name || '(unnamed)')}</div>
          <div style="font-family:monospace;font-size:10px;color:var(--dim)">${esc(listener.protocol || '')}${listener.protocol_port != null ? ` / ${esc(String(listener.protocol_port))}` : ''}</div>
          <div style="font-family:monospace;font-size:10px;color:var(--dim);word-break:break-all">${esc(listener.id || '')}</div>
        </div>
        <span class="tree-badge ${listener.default_pool_id ? 'good' : 'warn'}">${listener.default_pool_id ? 'pool attached' : 'no pool'}</span>
      </div>
    </div>`).join('');
  }
  h += `</div></div>`;

  const pools = ld.pools || [];
  h += `<div class="card" style="margin-bottom:10px">
    <div class="card-title">Pools</div>
    <div class="card-body">`;
  if (!pools.length) {
    h += `<div style="color:var(--dim);font-size:12px">No pools found.</div>`;
  } else {
    h += pools.map(pool => `
      <div style="padding:7px 0;border-bottom:1px solid #f0f2f5">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:6px">
          <div>
            <div style="font-weight:600">${esc(pool.name || '(unnamed)')}</div>
            <div style="font-family:monospace;font-size:10px;color:var(--dim);word-break:break-all">${esc(pool.id || '')}</div>
          </div>
          <span class="tree-badge ${pool.operating_status === 'ACTIVE' ? 'good' : pool.operating_status === 'ERROR' ? 'err' : 'warn'}">${esc(pool.operating_status || 'UNKNOWN')}</span>
        </div>
        <div class="mrow"><span class="ml">Protocol</span><span class="mv">${esc(pool.protocol || '—')}</span></div>
        <div class="mrow"><span class="ml">Algorithm</span><span class="mv">${esc(pool.lb_algorithm || '—')}</span></div>
        <div class="mrow"><span class="ml">Members</span><span class="mv">${esc(String(pool.member_count ?? 0))}</span></div>
        <div class="mrow"><span class="ml">Admin state</span><span class="mv">${pool.admin_state_up ? 'UP' : 'DOWN'}</span></div>
        <div class="mrow"><span class="ml">Health monitor</span><span class="mv" style="display:grid;gap:2px;text-align:right;white-space:pre-line">${esc(pool.healthmonitor || '—')}</span></div>
        <div class="mrow"><span class="ml">Session persistence</span><span class="mv">${esc(pool.session_persistence || 'None')}</span></div>
        <div class="mrow"><span class="ml">TLS enabled</span><span class="mv">${pool.tls_enabled ? 'Yes' : 'No'}</span></div>
      </div>`).join('');
  }
  h += `</div></div>`;

  const amphorae = ld.amphorae || [];
  h += `<div class="card" style="margin-bottom:10px">
    <div class="card-title">Amphorae</div>
    <div class="card-body">`;
  if (!amphorae.length) {
    h += `<div style="color:var(--dim);font-size:12px">No amphorae found.</div>`;
  } else {
    h += amphorae.map(amp => `
      <div style="border:1px solid var(--border);border-radius:4px;background:#fbfcfd;padding:10px 11px;margin-bottom:10px">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:6px">
          <div>
            <div style="font-weight:600">${esc(amp.id || '(unnamed)')}</div>
            <div style="font-size:11px;color:var(--dim)">${esc(amp.role || 'UNKNOWN')}</div>
          </div>
          <span class="tree-badge ${amp.role === 'MASTER' ? 'good' : 'purple'}">${esc(amp.role || 'UNKNOWN')}</span>
        </div>
        <div style="display:grid;gap:5px;font-size:11px;color:var(--dim)">
          <div>Compute host: <strong style="color:var(--text)">${esc(amp.compute_host || '—')}</strong></div>
          <div>Compute ID: <span style="font-family:monospace">${esc(amp.compute_id || '—')}</span></div>
          <div>LB network IP: ${esc(amp.lb_network_ip || '—')}</div>
          <div>HA IP: ${esc(amp.ha_ip || '—')}</div>
          <div>VRRP IP: ${esc(amp.vrrp_ip || '—')}</div>
          <div>Status: ${esc(amp.status || '—')}</div>
          <div>Image ID: <span style="font-family:monospace">${esc(amp.image_id || '—')}</span></div>
        </div>
      </div>`).join('');
  }
  h += `</div></div>
    <div class="card" style="margin-bottom:10px">
      <div class="card-title">Placement</div>
      <div class="card-body">
        <div class="mrow"><span class="ml">Distinct hosts</span><span class="mv">${esc(String(ld.distinct_host_count ?? 0))}</span></div>
        <div class="mrow"><span class="ml">Failover posture</span><span class="mv">${esc(ld.ha_summary || 'Unknown')}</span></div>
      </div>
    </div>
  </div>`;
  wrap.innerHTML = h;
  syncNetworkingDetailShell();
  } catch (e) {
    wrap.innerHTML = `<div class="net-detail-inner"><div class="err-block">Detail render failed: ${esc(String(e))}</div></div>`;
  }
}
