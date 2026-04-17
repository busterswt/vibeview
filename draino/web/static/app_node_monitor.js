'use strict';

function shouldLoadNodeMetrics(name) {
  const detail = nodeMetricsCache[name];
  return !detail || detail.error;
}

async function loadNodeMetrics(name, force = false) {
  nodeMetricsCache[name] = { loading: true, current: null, history: [], error: null };
  if (selectedNode === name && activeTab === 'monitor') renderNodeMonitorTab(nodes[name]);
  try {
    const qs = force ? '?refresh=1' : '';
    const resp = await fetch(`/api/nodes/${encodeURIComponent(name)}/metrics${qs}`);
    const json = await resp.json();
    nodeMetricsCache[name] = {
      loading: false,
      current: json.current || null,
      history: json.history || [],
      error: json.error || null,
    };
  } catch (e) {
    nodeMetricsCache[name] = { loading: false, current: null, history: [], error: String(e) };
  }
  if (selectedNode === name && activeTab === 'monitor') renderNodeMonitorTab(nodes[name]);
}

function refreshSelectedNodeMetrics(force = false) {
  if (activeView !== 'infrastructure' || activeTab !== 'monitor' || !selectedNode || !nodes[selectedNode]) return;
  loadNodeMetrics(selectedNode, force);
}

function enabledNetStatsSet(nodeName) {
  if (!nodeNetStatsEnabled[nodeName]) nodeNetStatsEnabled[nodeName] = new Set();
  return nodeNetStatsEnabled[nodeName];
}

function interfaceSupportsLiveStats(iface) {
  return String(iface?.status || '').toLowerCase() === 'up';
}

async function loadNodeNetworkStats(name) {
  nodeNetStatsCache[name] = {
    ...(nodeNetStatsCache[name] || {}),
    loading: true,
    error: null,
  };
  if (selectedNode === name && activeTab === 'monitor') renderNodeMonitorTab(nodes[name]);
  try {
    const resp = await fetch(`/api/nodes/${encodeURIComponent(name)}/network-stats`);
    const json = await resp.json();
    nodeNetStatsCache[name] = {
      loading: false,
      interfaces: json.interfaces || [],
      error: json.error || null,
      fetchedAt: new Date(),
    };
  } catch (e) {
    nodeNetStatsCache[name] = {
      loading: false,
      interfaces: [],
      error: String(e),
      fetchedAt: new Date(),
    };
  }
  if (selectedNode === name && activeTab === 'monitor') renderNodeMonitorTab(nodes[name]);
}

function refreshSelectedNodeNetworkStats() {
  if (activeView !== 'infrastructure' || activeTab !== 'monitor' || !selectedNode || !nodes[selectedNode]) return;
  const enabled = enabledNetStatsSet(selectedNode);
  if (!enabled.size) return;
  loadNodeNetworkStats(selectedNode);
}

async function loadNodeIrqBalance(name, force = false) {
  const cached = nodeIrqBalanceCache[name];
  if (!force && cached?.loading) return;
  nodeIrqBalanceCache[name] = {
    ...(cached || {}),
    loading: true,
    error: null,
  };
  if (selectedNode === name && activeTab === 'monitor') renderNodeMonitorTab(nodes[name]);
  try {
    const resp = await fetch(`/api/nodes/${encodeURIComponent(name)}/irq-balance`);
    const json = await resp.json();
    nodeIrqBalanceCache[name] = {
      loading: false,
      interfaces: json.interfaces || [],
      error: json.error || null,
      fetchedAt: new Date(),
    };
  } catch (e) {
    nodeIrqBalanceCache[name] = {
      loading: false,
      interfaces: [],
      error: String(e),
      fetchedAt: new Date(),
    };
  }
  if (selectedNode === name && activeTab === 'monitor') renderNodeMonitorTab(nodes[name]);
}

function refreshSelectedNodeIrqBalance() {
  if (activeView !== 'infrastructure' || activeTab !== 'monitor' || !selectedNode || !nodes[selectedNode]) return;
  loadNodeIrqBalance(selectedNode);
}

function isNodeSarExpanded(nodeName) {
  return Boolean(nodeSarExpanded[nodeName]);
}

function setNodeSarExpanded(nodeName, expanded) {
  nodeSarExpanded[nodeName] = Boolean(expanded);
}

function toggleNodeSarTrends(nodeName) {
  if (!nodeName || !nodes[nodeName]) return;
  const expanded = !isNodeSarExpanded(nodeName);
  setNodeSarExpanded(nodeName, expanded);
  if (expanded) loadNodeSarTrends(nodeName);
  if (selectedNode === nodeName && activeTab === 'monitor') renderNodeMonitorTab(nodes[nodeName]);
}

async function loadNodeSarTrends(name, force = false) {
  if (!force && !isNodeSarExpanded(name)) return;
  const cached = nodeSarTrendsCache[name];
  if (!force && cached?.loading) return;
  nodeSarTrendsCache[name] = {
    ...(cached || {}),
    loading: true,
    error: null,
  };
  if (selectedNode === name && activeTab === 'monitor') renderNodeMonitorTab(nodes[name]);
  try {
    const resp = await fetch(`/api/nodes/${encodeURIComponent(name)}/sar-trends`);
    const json = await resp.json();
    nodeSarTrendsCache[name] = {
      loading: false,
      summary: json.summary || null,
      interfaces: json.interfaces || [],
      error: json.error || null,
      fetchedAt: new Date(),
    };
  } catch (e) {
    nodeSarTrendsCache[name] = {
      loading: false,
      summary: null,
      interfaces: [],
      error: String(e),
      fetchedAt: new Date(),
    };
  }
  if (selectedNode === name && activeTab === 'monitor') renderNodeMonitorTab(nodes[name]);
}

function refreshSelectedNodeSarTrends() {
  if (activeView !== 'infrastructure' || activeTab !== 'monitor' || !selectedNode || !nodes[selectedNode]) return;
  if (!isNodeSarExpanded(selectedNode)) return;
  loadNodeSarTrends(selectedNode);
}

function toggleNodeInterfaceStats(nodeName, ifaceName, enabled) {
  const set = enabledNetStatsSet(nodeName);
  if (enabled) {
    set.add(ifaceName);
    loadNodeNetworkStats(nodeName);
  } else {
    set.delete(ifaceName);
  }
  if (selectedNode === nodeName && activeTab === 'monitor') renderNodeMonitorTab(nodes[nodeName]);
}

function renderNodeMonitorTab(nd) {
  const wrap = document.getElementById('node-monitor-content');
  const tabBody = document.getElementById('tab-body');
  const priorScrollTop = tabBody ? tabBody.scrollTop : 0;
  const data = nodeMetricsCache[nd.k8s_name] || { loading: true, current: null, history: [], error: null };
  const current = data.current || {};
  const filesystems = current.filesystems || [];
  const rootFs = filesystems.find(fs => fs.mount === '/') || null;
  const ifaceCache = nodeNetworkCache[nd.k8s_name] || {};
  const ifaces = (ifaceCache.ifaces || []).filter(i => i.type === 'physical' || i.type === 'bond');
  const enabledStats = enabledNetStatsSet(nd.k8s_name);
  const netStats = nodeNetStatsCache[nd.k8s_name] || { loading: false, interfaces: [], error: null, fetchedAt: null };
  const netStatsByName = Object.fromEntries((netStats.interfaces || []).map(item => [item.name, item]));
  const irqBalance = nodeIrqBalanceCache[nd.k8s_name] || { loading: false, interfaces: [], error: null, fetchedAt: null };
  const sarTrends = nodeSarTrendsCache[nd.k8s_name] || { loading: false, summary: null, interfaces: [], error: null, fetchedAt: null };
  const bondByMember = {};
  for (const iface of ifaces) {
    if (iface.type !== 'bond') continue;
    for (const member of (iface.members || [])) bondByMember[member] = iface.name;
  }

  const fsRows = filesystems.length
    ? filesystems.map((fs) => `
      <tr>
        <td>${esc(fs.mount)}</td>
        <td>${fmtKiB(fs.total_kb)}</td>
        <td>${fmtKiB(fs.available_kb)}</td>
        <td>${fs.used_percent != null ? `${fs.used_percent}%` : '—'}</td>
      </tr>
    `).join('')
    : `<tr><td colspan="4" style="color:var(--dim)">No filesystem data reported.</td></tr>`;

  let h = `<div class="tab-section-title" style="margin-bottom:10px"><span>Node Metrics</span></div>`;
  if (data.error) {
    h += `<div class="etcd-alert danger">Host metrics error: ${esc(data.error)}</div>`;
  }
  h += `<div class="summary-grid">
    <div class="card">
      <div class="card-title">Host Load</div>
      <div class="card-body">
        <div class="mrow"><span class="ml">Load average</span><span class="mv">${
          current.load1 != null && current.load5 != null && current.load15 != null
            ? `${current.load1.toFixed(2)} · ${current.load5.toFixed(2)} · ${current.load15.toFixed(2)}`
            : '—'
        }</span></div>
        <div class="mrow"><span class="ml">CPU count</span><span class="mv">${current.cpu_count ?? '—'}</span></div>
        <div class="mrow"><span class="ml">Uptime</span><span class="mv">${fmtSeconds(current.uptime_seconds)}</span></div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">Memory</div>
      <div class="card-body">
        <div class="mrow"><span class="ml">Used</span><span class="mv">${fmtKiB(current.memory_used_kb)}</span></div>
        <div class="mrow"><span class="ml">Available</span><span class="mv">${fmtKiB(current.memory_available_kb)}</span></div>
        <div class="mrow"><span class="ml">Total</span><span class="mv">${fmtKiB(current.memory_total_kb)}</span></div>
        <div class="mrow"><span class="ml">Pressure</span><span class="mv">${
          current.memory_used_percent != null ? `${current.memory_used_percent.toFixed(1)}%` : '—'
        }</span></div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">Local Disk</div>
      <div class="card-body">
        <div class="mrow"><span class="ml">Root free</span><span class="mv">${rootFs ? fmtKiB(rootFs.available_kb) : '—'}</span></div>
        <div class="mrow"><span class="ml">Root used</span><span class="mv">${rootFs && rootFs.used_percent != null ? `${rootFs.used_percent}%` : '—'}</span></div>
        <div class="mrow"><span class="ml">Tracked filesystems</span><span class="mv">${filesystems.length || '—'}</span></div>
      </div>
    </div>
  </div>`;

  h += `<div class="card">
    <div class="card-title">Filesystem Free Space</div>
    <div class="card-body">
      <table class="data-table">
        <thead>
          <tr><th>Mount</th><th>Total</th><th>Free</th><th>Used</th></tr>
        </thead>
        <tbody>${fsRows}</tbody>
      </table>
    </div>
  </div>`;

  h += `<div class="card" style="margin-top:12px">
    <div class="card-title">Network Interfaces</div>
    <div class="card-body">
      <div style="font-size:11px;color:var(--dim);margin-bottom:8px">
        Toggle live throughput per interface. Rates are sampled from kernel byte counters and shown only for enabled interfaces.
      </div>
      ${ifaceCache.ifacesLoading ? `<div class="runtime-note"><span class="spinner">⟳</span> Loading interface inventory…</div>` : ''}
      ${ifaceCache.ifacesError ? `<div class="etcd-alert danger">Interface inventory error: ${esc(ifaceCache.ifacesError)}</div>` : ''}
      ${netStats.error ? `<div class="etcd-alert danger">Live stats error: ${esc(netStats.error)}</div>` : ''}
      ${ifaces.length ? `
        <table class="data-table">
          <thead>
            <tr><th>Interface</th><th>Type</th><th>Status</th><th>Speed</th><th>Duplex</th><th>Bond Member</th><th>IPs</th><th>Live Stats</th><th>RX</th><th>TX</th></tr>
          </thead>
          <tbody>
            ${ifaces.map((iface) => {
              const supportsLiveStats = interfaceSupportsLiveStats(iface);
              if (!supportsLiveStats) enabledStats.delete(iface.name);
              const checked = supportsLiveStats && enabledStats.has(iface.name);
              const live = netStatsByName[iface.name] || {};
              const ipSummary = [...(iface.ipv4 || []), ...(iface.ipv6 || [])].join(', ');
              const bondName = iface.type === 'physical' ? (bondByMember[iface.name] || '') : '';
              const bondCell = bondName
                ? `<span class="tree-badge" style="margin-right:3px;font-family:monospace">${esc(bondName)}</span>`
                : '<span style="color:var(--dim)">—</span>';
              return `<tr>
                <td><strong>${esc(iface.name)}</strong></td>
                <td><span class="nic-type ${escAttr(iface.type || 'physical')}">${esc(iface.type || 'physical')}</span></td>
                <td>${esc(iface.status || 'unknown')}</td>
                <td>${esc(iface.speed || '—')}</td>
                <td>${esc(iface.duplex || '—')}</td>
                <td>${bondCell}</td>
                <td style="font-size:11px;color:var(--dim)">${esc(ipSummary || '—')}</td>
                <td>
                  <label style="display:inline-flex;align-items:center;gap:6px;cursor:pointer">
                    <input type="checkbox" ${checked ? 'checked' : ''} ${supportsLiveStats ? '' : 'disabled'} onchange="toggleNodeInterfaceStats('${escAttr(nd.k8s_name)}','${escAttr(iface.name)}', this.checked)">
                    <span>${supportsLiveStats ? (checked ? 'on' : 'off') : 'down'}</span>
                  </label>
                </td>
                <td>${checked ? esc(fmtNetRate(live.rx_bytes_per_second)) : `<span style="color:var(--dim)">${supportsLiveStats ? 'off' : 'down'}</span>`}</td>
                <td>${checked ? esc(fmtNetRate(live.tx_bytes_per_second)) : `<span style="color:var(--dim)">${supportsLiveStats ? 'off' : 'down'}</span>`}</td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      ` : (!ifaceCache.ifacesLoading ? `<div style="color:var(--dim)">No physical or bond interfaces found.</div>` : '')}
      ${netStats.fetchedAt && enabledStats.size ? `<div class="runtime-note">Updated ${_fmtTime(netStats.fetchedAt)}</div>` : ''}
    </div>
  </div>`;

  const irqRows = (irqBalance.interfaces || []).length
    ? irqBalance.interfaces.map((item) => {
      const riskClass = item.risk === 'high' ? 'mv red' : item.risk === 'medium' ? 'mv yellow' : 'mv green';
      const topShare = item.top_cpu_share_pct != null ? `${item.top_cpu_share_pct}%` : '—';
      const topCpu = item.top_cpu || '—';
      const queues = `${item.rx_queues ?? 0}/${item.tx_queues ?? 0}`;
      return `<tr>
        <td><strong>${esc(item.name)}</strong></td>
        <td>${esc(item.irq_total ?? '—')}</td>
        <td>${esc(item.active_cpus ?? 0)} / ${esc(item.cpu_count ?? '—')}</td>
        <td>${esc(topCpu)}</td>
        <td>${esc(topShare)}</td>
        <td>${esc(queues)}</td>
        <td>${item.rps_enabled || item.xps_enabled ? `<span class="tree-badge">${item.rps_enabled ? 'RPS' : ''}${item.rps_enabled && item.xps_enabled ? ' · ' : ''}${item.xps_enabled ? 'XPS' : ''}</span>` : '<span style="color:var(--dim)">—</span>'}</td>
        <td><span class="${riskClass}">${esc(item.risk || 'low')}</span></td>
        <td style="color:var(--dim)">${esc(item.reason || '—')}</td>
      </tr>`;
    }).join('')
    : `<tr><td colspan="9" style="color:var(--dim)">No IRQ balance data reported.</td></tr>`;

  h += `<div class="card" style="margin-top:12px">
    <div class="card-title">NIC IRQ Balance</div>
    <div class="card-body">
      <div style="font-size:11px;color:var(--dim);margin-bottom:8px">
        Highlights interfaces where interrupt handling appears concentrated on too few CPUs under active traffic.
      </div>
      ${irqBalance.loading ? `<div class="runtime-note"><span class="spinner">⟳</span> Loading IRQ balance…</div>` : ''}
      ${irqBalance.error ? `<div class="etcd-alert danger">IRQ balance error: ${esc(irqBalance.error)}</div>` : ''}
      <table class="data-table">
        <thead>
          <tr><th>Interface</th><th>IRQs</th><th>Active CPUs</th><th>Top CPU</th><th>Top Share</th><th>Queues</th><th>RPS/XPS</th><th>Risk</th><th>Reason</th></tr>
        </thead>
        <tbody>${irqRows}</tbody>
      </table>
      ${irqBalance.fetchedAt ? `<div class="runtime-note">Updated ${_fmtTime(irqBalance.fetchedAt)}</div>` : ''}
    </div>
  </div>`;

  const sarExpanded = isNodeSarExpanded(nd.k8s_name);
  const sarSummary = sarTrends.summary || {};
  const sarIfaceRows = (sarTrends.interfaces || []).length
    ? sarTrends.interfaces.map((item) => `<tr>
        <td><strong>${esc(item.name)}</strong></td>
        <td>${esc(item.rxdrop ?? '—')}</td>
        <td>${esc(item.txdrop ?? '—')}</td>
        <td>${esc(item.rxerr ?? '—')}</td>
        <td>${esc(item.txerr ?? '—')}</td>
      </tr>`).join('')
    : `<tr><td colspan="5" style="color:var(--dim)">No NIC drops or errors reported in the last ${esc(String(sarSummary.window_minutes || 15))} minutes.</td></tr>`;

  h += `<div class="card" style="margin-top:12px">
    <div class="card-title" style="display:flex;align-items:center;justify-content:space-between;gap:10px">
      <span>Recent Trends (SAR)</span>
      <button class="btn secondary" type="button" onclick="toggleNodeSarTrends('${escAttr(nd.k8s_name)}')">${sarExpanded ? 'Hide' : 'Show'}</button>
    </div>
    <div class="card-body">
      <div style="font-size:11px;color:var(--dim);margin-bottom:8px">
        Short historical context from sysstat when available.
      </div>
      ${!sarExpanded ? `<div class="runtime-note">SAR is loaded on demand and refreshed infrequently while this section is open.</div>` : ''}
      ${sarExpanded && sarTrends.loading ? `<div class="runtime-note"><span class="spinner">⟳</span> Loading SAR trends…</div>` : ''}
      ${sarExpanded && sarTrends.error ? `<div class="runtime-note">${esc(sarTrends.error)}</div>` : sarExpanded && sarSummary ? `
        <div class="summary-grid">
          <div class="card">
            <div class="card-title">CPU Busy Avg</div>
            <div class="card-body"><div class="mrow"><span class="ml">Last ${esc(String(sarSummary.window_minutes || 15))}m</span><span class="mv">${sarSummary.cpu_busy_avg != null ? `${sarSummary.cpu_busy_avg}%` : '—'}</span></div></div>
          </div>
          <div class="card">
            <div class="card-title">Run Queue Peak</div>
            <div class="card-body"><div class="mrow"><span class="ml">Last ${esc(String(sarSummary.window_minutes || 15))}m</span><span class="mv">${sarSummary.run_queue_peak != null ? sarSummary.run_queue_peak : '—'}</span></div></div>
          </div>
          <div class="card">
            <div class="card-title">Ctx Switches Avg</div>
            <div class="card-body"><div class="mrow"><span class="ml">Per second</span><span class="mv">${sarSummary.ctx_switches_avg != null ? sarSummary.ctx_switches_avg : '—'}</span></div></div>
          </div>
          <div class="card">
            <div class="card-title">NIC Error Standouts</div>
            <div class="card-body"><div class="mrow"><span class="ml">Interfaces</span><span class="mv">${sarSummary.nic_issue_count != null ? sarSummary.nic_issue_count : '—'}</span></div></div>
          </div>
        </div>
        <div style="font-size:11px;font-weight:600;color:var(--dim);margin:12px 0 6px">NIC Errors &amp; Drops</div>
        <table class="data-table">
          <thead>
            <tr><th>Interface</th><th>rxdrop/s</th><th>txdrop/s</th><th>rxerr/s</th><th>txerr/s</th></tr>
          </thead>
          <tbody>${sarIfaceRows}</tbody>
        </table>
      ` : sarExpanded ? `<div class="runtime-note">No SAR summary is available yet.</div>` : ''}
      ${sarExpanded && sarTrends.fetchedAt ? `<div class="runtime-note">Updated ${_fmtTime(sarTrends.fetchedAt)}</div>` : ''}
    </div>
  </div>`;

  wrap.innerHTML = h;
  if (tabBody) tabBody.scrollTop = priorScrollTop;
}
