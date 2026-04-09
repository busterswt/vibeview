'use strict';

const REPORT_META = {
  'maintenance-readiness': {
    label: 'Maintenance Readiness',
    subtitle: 'Node-by-node drain and reboot posture',
    url: '/api/reports/maintenance-readiness',
    csvUrl: '/api/reports/maintenance-readiness.csv',
    icon: '🛠️',
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
    const summary = report.summary || {};
    const findings = report.findings || [];
    const items = report.items || [];
    content = `
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
        <div class="report-hero-card">
          <div class="report-hero-label">Ready Now</div>
          <div class="report-hero-value">${summary.ready_now ?? 0}</div>
          <div class="report-hero-foot good">No immediate blockers</div>
        </div>
        <div class="report-hero-card">
          <div class="report-hero-label">Blocked</div>
          <div class="report-hero-value">${summary.blocked ?? 0}</div>
          <div class="report-hero-foot bad">Requires operator intervention</div>
        </div>
        <div class="report-hero-card">
          <div class="report-hero-label">Review</div>
          <div class="report-hero-value">${summary.review ?? 0}</div>
          <div class="report-hero-foot warn">Operational caution advised</div>
        </div>
        <div class="report-hero-card">
          <div class="report-hero-label">Reboot Required</div>
          <div class="report-hero-value">${summary.reboot_required ?? 0}</div>
          <div class="report-hero-foot warn">Pending reboot or kernel drift</div>
        </div>
      </section>

      <section class="report-grid-two">
        <div class="card">
          <div class="card-title"><span>Highest-Risk Findings</span><span class="card-note">Live summary</span></div>
          <div class="card-body report-findings">
            ${findings.length ? findings.map(item => `
              <div class="report-finding-row">
                <div><span class="report-severity ${esc(item.severity || 'medium')}">${esc(item.severity || 'medium')}</span></div>
                <div class="report-finding-text"><span class="mono">${esc(item.node || '')}</span> ${esc(item.message || '')}</div>
              </div>
            `).join('') : `<div class="card-note">No elevated findings in the current snapshot.</div>`}
          </div>
        </div>
        <div class="card">
          <div class="card-title"><span>Readiness Breakdown</span><span class="card-note">Current posture</span></div>
          <div class="card-body report-kv-stack">
            <div class="mrow"><span class="ml">Ready nodes</span><span class="mv">${summary.ready_now ?? 0}</span></div>
            <div class="mrow"><span class="ml">Blocked nodes</span><span class="mv">${summary.blocked ?? 0}</span></div>
            <div class="mrow"><span class="ml">Review nodes</span><span class="mv">${summary.review ?? 0}</span></div>
            <div class="mrow"><span class="ml">No-agent nodes</span><span class="mv">${summary.no_agent ?? 0}</span></div>
          </div>
        </div>
      </section>

      <section class="card">
        <div class="card-title"><span>Node Maintenance Grid</span><span class="card-note">Dense, sortable-style layout</span></div>
        <div class="card-body" style="padding:0">
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
