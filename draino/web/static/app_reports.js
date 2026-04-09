'use strict';

const REPORT_META = {
  'maintenance-readiness': {
    label: 'Maintenance Readiness',
    subtitle: 'Node-by-node drain and reboot posture',
    url: '/api/reports/maintenance-readiness',
    csvUrl: '/api/reports/maintenance-readiness.csv',
    icon: '🛠️',
  },
  'capacity-headroom': {
    label: 'Capacity & Headroom',
    subtitle: 'Nova, Kubernetes, and cluster planning view',
    url: '/api/reports/capacity-headroom',
    csvUrl: '/api/reports/capacity-headroom.csv',
    icon: '📊',
  },
};

async function loadActiveReport(force = false) {
  const key = reportState.active;
  if (reportState.loading) return;
  if (reportState.reports[key] && !force) {
    renderReportsView();
    return;
  }
  reportState.loading = true;
  reportState.error = null;
  renderReportsView();
  try {
    const resp = await fetch(REPORT_META[key].url);
    const contentType = (resp.headers.get('content-type') || '').toLowerCase();
    if (!resp.ok) {
      const bodyText = (await resp.text()).trim();
      throw new Error(bodyText || `Report request failed (${resp.status})`);
    }
    if (!contentType.includes('application/json')) {
      const bodyText = (await resp.text()).trim();
      throw new Error(bodyText || 'Report request returned a non-JSON response');
    }
    const json = await resp.json();
    if (json.error) throw new Error(json.error);
    reportState.reports[key] = json.report;
  } catch (e) {
    reportState.error = String(e);
  } finally {
    reportState.loading = false;
    renderReportsView();
  }
}

function refreshActiveReport() {
  loadActiveReport(true);
}

function exportActiveReportCsv() {
  const meta = REPORT_META[reportState.active];
  if (!meta?.csvUrl) return;
  window.location = meta.csvUrl;
}

function renderReportBreakdownBar(label, count, total, cls) {
  const safeTotal = Number(total) > 0 ? Number(total) : 0;
  const safeCount = Number(count) > 0 ? Number(count) : 0;
  const width = safeTotal ? Math.max(0, Math.min(100, (safeCount / safeTotal) * 100)) : 0;
  return `
    <div class="report-bar-row">
      <div class="report-bar-label">${esc(label)}</div>
      <div class="report-bar-track"><div class="report-bar-fill ${escAttr(cls)}" style="width:${width.toFixed(1)}%"></div></div>
      <div class="report-bar-value">${esc(String(safeCount))} / ${esc(String(safeTotal))}</div>
    </div>
  `;
}

function renderPercentValue(value) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '—';
  return `${Math.round(value)}%`;
}

function renderCapacityHero(label, value, footClass, footText) {
  return `
    <div class="report-hero-card">
      <div class="report-hero-label">${esc(label)}</div>
      <div class="report-hero-value">${esc(value)}</div>
      <div class="report-hero-foot ${escAttr(footClass)}">${esc(footText)}</div>
    </div>
  `;
}

function renderFindingsCard(findings, title = 'Highest-Risk Findings') {
  return `
    <div class="card">
      <div class="card-title"><span>${esc(title)}</span></div>
      <div class="card-body report-findings">
        ${findings.length ? findings.map(item => `
          <div class="report-finding-row">
            <div><span class="report-severity ${esc(item.severity || 'medium')}">${esc(item.severity || 'medium')}</span></div>
            <div class="report-finding-text">${item.node ? `<span class="mono">${esc(item.node)}</span> ` : ''}${esc(item.message || '')}</div>
          </div>
        `).join('') : `<div class="card-note">No elevated findings in the current snapshot.</div>`}
      </div>
    </div>
  `;
}

function renderMaintenanceReport(activeMeta, report, nowLabel) {
  const summary = report.summary || {};
  const findings = report.findings || [];
  const items = report.items || [];
  const totalNodes = Number(report.scope?.nodes ?? items.length) || 0;
  return `
    <section class="report-header-card">
      <div class="report-head-top">
        <div>
          <div class="report-title">${esc(report.title || activeMeta.label)}</div>
          <div class="report-subtitle">${esc(report.subtitle || '')}</div>
        </div>
        <div class="report-head-actions">
          <button class="btn" onclick="refreshActiveReport()">↻ Refresh</button>
          <button class="btn" onclick="exportActiveReportCsv()">CSV</button>
          <button class="btn primary" onclick="window.print()">PDF</button>
        </div>
      </div>
      <div class="report-meta-row">
        <span class="meta-pill">Generated: ${esc(nowLabel)}</span>
        <span class="meta-pill">Source: ${esc(report.source || 'Live environment')}</span>
        <span class="meta-pill">Scope: ${esc(String(report.scope?.nodes ?? items.length))} nodes</span>
        <span class="meta-pill">Mode: Live snapshot only</span>
      </div>
    </section>

    <section class="report-hero-grid">
      ${renderCapacityHero('Ready Now', String(summary.ready_now ?? 0), 'good', 'No immediate blockers')}
      ${renderCapacityHero('Blocked', String(summary.blocked ?? 0), 'bad', 'Requires operator intervention')}
      ${renderCapacityHero('Review', String(summary.review ?? 0), 'warn', 'Operational caution advised')}
      ${renderCapacityHero('Reboot Required', String(summary.reboot_required ?? 0), 'warn', 'Pending reboot or kernel drift')}
    </section>

    <section class="report-grid-two">
      ${renderFindingsCard(findings)}
      <div class="card">
        <div class="card-title"><span>Readiness Breakdown</span></div>
        <div class="card-body report-chart-strip">
          ${renderReportBreakdownBar('Ready nodes', summary.ready_now ?? 0, totalNodes, 'good')}
          ${renderReportBreakdownBar('Blocked nodes', summary.blocked ?? 0, totalNodes, 'bad')}
          ${renderReportBreakdownBar('Review nodes', summary.review ?? 0, totalNodes, 'warn')}
          ${renderReportBreakdownBar('No-agent nodes', summary.no_agent ?? 0, totalNodes, 'bad')}
        </div>
      </div>
    </section>

    <section class="card">
      <div class="card-title"><span>Node Maintenance Grid</span></div>
      <div class="card-body report-table-wrap">
        <table class="data-table report-table">
          <thead>
            <tr>
              <th>Node</th>
              <th>AZ</th>
              <th>Role</th>
              <th>Nova</th>
              <th>K8s</th>
              <th>VMs</th>
              <th>Pods</th>
              <th>Reboot</th>
              <th>Agent</th>
              <th>Verdict</th>
              <th>Blocking Reason</th>
            </tr>
          </thead>
          <tbody>
            ${items.map(item => `
              <tr>
                <td class="mono">${esc(item.node || '')}</td>
                <td>${esc(item.availability_zone || '—')}</td>
                <td>${(item.roles || []).map(role => `<span class="report-tag ${reportRoleTagClass(role)}">${esc(role)}</span>`).join('')}</td>
                <td>${renderReportStatus(item.nova_status)}</td>
                <td>${renderReportStatus(item.k8s_status)}</td>
                <td>${esc(String(item.vm_count ?? 0))}</td>
                <td>${item.pod_count != null ? esc(String(item.pod_count)) : '—'}</td>
                <td>${item.reboot_required ? '<span class="report-tag yellow">needed</span>' : '<span class="report-tag blue">clear</span>'}</td>
                <td>${item.node_agent_ready ? '<span class="report-status good" title="Node agent ready"><span class="report-dot good"></span></span>' : '<span class="report-status bad" title="Node agent unavailable"><span class="report-dot bad"></span></span>'}</td>
                <td><span class="report-tag ${reportVerdictTagClass(item.verdict)}">${esc(item.verdict || '')}</span></td>
                <td>${esc(item.blocking_reason || '')}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function renderCapacityReport(activeMeta, report, nowLabel) {
  const summary = report.summary || {};
  const summaryFoot = report.summary_foot || {};
  const findings = report.findings || [];
  const items = report.items || [];
  const azHeadroom = report.az_headroom || [];
  return `
    <section class="report-header-card">
      <div class="report-head-top">
        <div>
          <div class="report-title">${esc(report.title || activeMeta.label)}</div>
          <div class="report-subtitle">${esc(report.subtitle || '')}</div>
        </div>
        <div class="report-head-actions">
          <button class="btn" onclick="refreshActiveReport()">↻ Refresh</button>
          <button class="btn" onclick="exportActiveReportCsv()">CSV</button>
          <button class="btn primary" onclick="window.print()">PDF</button>
        </div>
      </div>
      <div class="report-meta-row">
        <span class="meta-pill">Generated: ${esc(nowLabel)}</span>
        <span class="meta-pill">Scope: ${esc(String(report.scope?.computes ?? items.length))} computes / ${esc(String(report.scope?.instances ?? 0))} instances / ${esc(String(report.scope?.pods ?? 0))} pods</span>
        <span class="meta-pill">Source: ${esc(report.source || 'Live environment')}</span>
        <span class="meta-pill">No stored history</span>
      </div>
    </section>

    <section class="report-hero-grid">
      ${renderCapacityHero('Free vCPU Headroom', renderPercentValue(summary.free_vcpu_pct), summary.free_vcpu_pct != null && summary.free_vcpu_pct < 20 ? 'warn' : 'good', summaryFoot.free_vcpu_pct || '')}
      ${renderCapacityHero('Free RAM Headroom', renderPercentValue(summary.free_ram_pct), summary.free_ram_pct != null && summary.free_ram_pct < 20 ? 'warn' : 'good', summaryFoot.free_ram_pct || '')}
      ${renderCapacityHero('Pod Headroom', renderPercentValue(summary.free_pod_pct), summary.free_pod_pct != null && summary.free_pod_pct < 20 ? 'warn' : 'good', summaryFoot.free_pod_pct || '')}
      ${renderCapacityHero('Drain-Safe Hosts', String(summary.drain_safe_hosts ?? 0), (summary.drain_safe_hosts ?? 0) < 3 ? 'bad' : 'good', summaryFoot.drain_safe_hosts || '')}
    </section>

    <section class="report-grid-two">
      <div class="card">
        <div class="card-title"><span>AZ Headroom</span></div>
        <div class="card-body report-chart-strip">
          ${azHeadroom.map(item => renderReportBreakdownBar(
            item.availability_zone || 'unknown',
            Math.round(item.vcpus_percent_used || 0),
            100,
            item.severity === 'high' ? 'bad' : item.severity === 'medium' ? 'warn' : 'good',
          ).replace(`${Math.round(item.vcpus_percent_used || 0)} / 100`, `${Math.round(item.vcpus_percent_used || 0)}% vCPU`)).join('')}
        </div>
      </div>
      ${renderFindingsCard(findings, 'Top Constraints')}
    </section>

    <section class="card">
      <div class="card-title"><span>Per-Host Headroom Grid</span></div>
      <div class="card-body report-table-wrap">
        <table class="data-table report-table">
          <thead>
            <tr>
              <th>Host</th>
              <th>AZ</th>
              <th>Aggregates</th>
              <th>VMs</th>
              <th>Amphora</th>
              <th>vCPU</th>
              <th>RAM</th>
              <th>Pods</th>
              <th>Maintenance</th>
            </tr>
          </thead>
          <tbody>
            ${items.map(item => `
              <tr>
                <td class="mono">${esc(item.host || '')}</td>
                <td>${esc(item.availability_zone || '—')}</td>
                <td>${esc((item.aggregates || []).join(', ') || '—')}</td>
                <td>${esc(String(item.vm_count ?? 0))}</td>
                <td>${esc(String(item.amphora_count ?? 0))}</td>
                <td>${item.vcpus_used != null && item.vcpus != null ? `${esc(String(item.vcpus_used))} / ${esc(String(item.vcpus))}` : '—'}</td>
                <td>${item.memory_mb_used != null && item.memory_mb != null ? `${esc(formatReportMemory(item.memory_mb_used))} / ${esc(formatReportMemory(item.memory_mb))}` : '—'}</td>
                <td>${item.pod_count != null && item.pods_allocatable != null ? `${esc(String(item.pod_count))} / ${esc(String(item.pods_allocatable))}` : '—'}</td>
                <td><span class="report-tag ${reportCapacityMaintenanceClass(item.maintenance_status)}">${esc(item.maintenance_status || '')}</span></td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function renderReportsView() {
  const wrap = document.getElementById('reports-wrap');
  if (!wrap) return;
  const activeMeta = REPORT_META[reportState.active];
  const report = reportState.reports[reportState.active];
  const nowLabel = new Date().toLocaleString('en-US', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });

  let content = '';
  if (reportState.loading && !report) {
    content = `<div class="report-empty"><span class="spinner">⟳</span> Loading live report…</div>`;
  } else if (reportState.error) {
    content = `<div class="err-block" style="margin:0">${esc(reportState.error)}</div>`;
  } else if (report) {
    content = reportState.active === 'capacity-headroom'
      ? renderCapacityReport(activeMeta, report, nowLabel)
      : renderMaintenanceReport(activeMeta, report, nowLabel);
  }

  wrap.innerHTML = `
    <div class="reports-shell">
      <aside class="reports-nav">
        <div class="reports-nav-head">Report Navigator</div>
        <div class="reports-nav-group">Operations</div>
        ${Object.entries(REPORT_META).map(([key, meta]) => `
          <div class="reports-nav-item${key === reportState.active ? ' active' : ''}" onclick="selectReport('${escAttr(key)}')">
            <span class="ico">${meta.icon}</span>
            <div class="meta">
              <div class="name">${esc(meta.label)}</div>
              <div class="sub">${esc(meta.subtitle)}</div>
            </div>
          </div>
        `).join('')}
      </aside>
      <main class="reports-content">${content}</main>
    </div>
  `;
}

function selectReport(key) {
  if (!REPORT_META[key]) return;
  reportState.active = key;
  renderReportsView();
  if (!reportState.reports[key] && !reportState.loading) loadActiveReport();
}

function renderReportStatus(value) {
  const normalized = String(value || '').toLowerCase();
  if (normalized === '-') {
    return '<span class="report-status na">-</span>';
  }
  const cls = normalized.includes('down') || normalized.includes('not-ready')
    ? 'bad'
    : normalized.includes('cordon') || normalized.includes('disabled') || normalized.includes('unknown')
      ? 'warn'
      : 'good';
  return `<span class="report-status ${cls}"><span class="report-dot ${cls}"></span>${esc(value || '—')}</span>`;
}

function reportRoleTagClass(role) {
  if (role === 'compute') return 'blue';
  if (role === 'etcd') return 'red';
  if (role === 'mariadb') return 'purple';
  if (role === 'edge') return 'yellow';
  return 'blue';
}

function reportVerdictTagClass(verdict) {
  if (verdict === 'ready') return 'green';
  if (verdict === 'blocked') return 'red';
  return 'yellow';
}

function reportCapacityMaintenanceClass(status) {
  if (status === 'drain-safe') return 'green';
  if (status === 'blocked') return 'red';
  return 'yellow';
}

function formatReportMemory(memoryMb) {
  const value = Number(memoryMb);
  if (!Number.isFinite(value)) return '—';
  const gb = value / 1024;
  return `${Math.round(gb)} GB`;
}
