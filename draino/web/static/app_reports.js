'use strict';

const REPORT_META = {
  'maintenance-readiness': {
    label: 'Maintenance Readiness',
    subtitle: 'Node-by-node drain and reboot posture',
    url: '/api/reports/maintenance-readiness',
    csvUrl: '/api/reports/maintenance-readiness.csv',
    icon: '🛠️',
    requiresOpenStack: true,
  },
  'capacity-headroom': {
    label: 'Capacity & Headroom',
    subtitle: 'Nova, Kubernetes, and cluster planning view',
    url: '/api/reports/capacity-headroom',
    csvUrl: '/api/reports/capacity-headroom.csv',
    icon: '📊',
    requiresOpenStack: true,
  },
  'nova-activity-capacity': {
    label: 'Nova Activity & Capacity',
    subtitle: 'Current estate, recent queryable change window, and Placement headroom',
    url: '/api/reports/nova-activity-capacity',
    csvUrl: '/api/reports/nova-activity-capacity.csv',
    icon: '🧭',
    requiresOpenStack: true,
  },
  'k8s-node-health-density': {
    label: 'Kubernetes Node Health & Density',
    subtitle: 'Kubelet drift, pod density, PVC hotspots, and standout nodes',
    url: '/api/reports/k8s-node-health-density',
    csvUrl: '/api/reports/k8s-node-health-density.csv',
    icon: '☸️',
    requiresOpenStack: false,
  },
  'k8s-pvc-workload': {
    label: 'Kubernetes PVC Placement & Workload',
    subtitle: 'PVCs, storage classes, replica placement, and consumer locality',
    url: '/api/reports/k8s-pvc-workload',
    csvUrl: '/api/reports/k8s-pvc-workload.csv',
    icon: '🗄️',
    requiresOpenStack: false,
  },
  'k8s-rollout-health': {
    label: 'Kubernetes Rollout Health',
    subtitle: 'Broken rollouts, recent restarts, fatal signals, and daemonset coverage',
    url: '/api/reports/k8s-rollout-health',
    csvUrl: '/api/reports/k8s-rollout-health.csv',
    icon: '🚦',
    requiresOpenStack: false,
  },
  'project-placement': {
    label: 'Project Placement',
    subtitle: 'Tenant VM distribution across compute hosts',
    url: '/api/reports/project-placement',
    csvUrl: '/api/reports/project-placement.csv',
    icon: '🏢',
    requiresOpenStack: true,
  },
  'placement-risk': {
    label: 'Placement Risk',
    subtitle: 'Concentration and maintenance blast radius',
    url: '/api/reports/placement-risk',
    csvUrl: '/api/reports/placement-risk.csv',
    icon: '⚠️',
    requiresOpenStack: true,
  },
};

function reportRequiresOpenStack(key) {
  return Boolean(REPORT_META[key]?.requiresOpenStack);
}

function guessReportApiIssue(message, status) {
  const text = String(message || '').toLowerCase();
  let service = null;
  if (text.includes('neutron')) service = 'Neutron';
  else if (text.includes('nova')) service = 'Nova';
  else if (text.includes('keystone') || (text.includes('auth') && text.includes('token'))) service = 'Keystone';
  else if ((text.includes('timeout') || text.includes('upstream')) && (reportState.active === 'capacity-headroom' || reportState.active === 'nova-activity-capacity')) service = 'Nova';
  if (!service) return null;
  return {
    service,
    operation: `GET ${REPORT_META[reportState.active]?.url || '/api/reports'}`,
    status: status ?? null,
    message: String(message || ''),
    severity: 'high',
  };
}

async function loadActiveReport(force = false) {
  const key = reportState.active;
  if (typeof hasOpenStackAuth === 'function' && !hasOpenStackAuth() && reportRequiresOpenStack(key)) {
    reportState.loading = false;
    reportState.error = null;
    renderReportsView();
    return;
  }
  if (reportState.loading) return;
  if (reportState.reports[key] && !force) {
    renderReportsView();
    return;
  }
  reportState.loading = true;
  reportState.error = null;
  const started = performance.now();
  reportState.fetchMeta[key] = {
    status: null,
    durationMs: null,
    contentType: null,
    errorText: null,
    fetchedAt: null,
  };
  renderReportsView();
  try {
    const resp = await fetch(REPORT_META[key].url);
    const contentType = (resp.headers.get('content-type') || '').toLowerCase();
    reportState.fetchMeta[key].status = resp.status;
    reportState.fetchMeta[key].contentType = contentType || null;
    if (!resp.ok) {
      const bodyText = (await resp.text()).trim();
      const issue = guessReportApiIssue(bodyText, resp.status);
      if (issue) recordApiIssue(issue);
      reportState.fetchMeta[key].errorText = bodyText || `Report request failed (${resp.status})`;
      throw new Error(bodyText || `Report request failed (${resp.status})`);
    }
    if (!contentType.includes('application/json')) {
      const bodyText = (await resp.text()).trim();
      const issue = guessReportApiIssue(bodyText, resp.status);
      if (issue) recordApiIssue(issue);
      reportState.fetchMeta[key].errorText = bodyText || 'Report request returned a non-JSON response';
      throw new Error(bodyText || 'Report request returned a non-JSON response');
    }
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else if (reportState.active === 'capacity-headroom' || reportState.active === 'nova-activity-capacity') recordApiSuccess('Nova');
    if (json.error) throw new Error(json.error);
    reportState.reports[key] = json.report;
  } catch (e) {
    reportState.error = String(e);
    reportState.fetchMeta[key].errorText = String(e);
  } finally {
    reportState.fetchMeta[key].durationMs = Math.round(performance.now() - started);
    reportState.fetchMeta[key].fetchedAt = new Date().toLocaleTimeString('en-US', { hour12: false });
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

function renderReportActionPills() {
  const loadingClass = reportState.loading ? ' active' : '';
  return `
    <span class="report-action-pills">
      <button class="report-action-pill" type="button" onclick="refreshActiveReport()" title="Refresh report">
        <span class="report-refresh-icon${loadingClass}">↻</span>
      </button>
      <button class="report-action-pill" type="button" onclick="exportActiveReportCsv()">CSV</button>
      <button class="report-action-pill primary" type="button" onclick="window.print()">PDF</button>
    </span>
  `;
}

function renderReportLaunchState(activeMeta) {
  return `
    <section class="report-launch-card">
      <div class="report-launch-shell">
        <div class="report-launch-icon">${esc(activeMeta.icon || '📄')}</div>
        <div class="report-launch-copy">
          <div class="report-launch-kicker">Live Report</div>
          <div class="report-launch-title">${esc(activeMeta.label)}</div>
          <div class="report-launch-subtitle">${esc(activeMeta.subtitle)}</div>
          <div class="report-launch-text">
            This report runs live against the current environment and can be expensive for large clouds.
            It will not auto-run or auto-refresh until you explicitly request it.
          </div>
          <div class="report-launch-pills">
            <span class="meta-pill">Live synthesis</span>
            <span class="meta-pill">No stored data</span>
            <span class="meta-pill">Manual execution only</span>
          </div>
          <div class="report-launch-actions">
            <button class="report-launch-btn" type="button" onclick="loadActiveReport(true)">Run ${esc(activeMeta.label)}</button>
          </div>
        </div>
      </div>
    </section>
  `;
}

function renderReportUnavailableState(activeMeta) {
  return `
    <section class="report-launch-card">
      <div class="report-launch-shell">
        <div class="report-launch-icon">${esc(activeMeta.icon || '📄')}</div>
        <div class="report-launch-copy">
          <div class="report-launch-kicker">Kubernetes-only mode</div>
          <div class="report-launch-title">${esc(activeMeta.label)}</div>
          <div class="report-launch-subtitle">${esc(activeMeta.subtitle)}</div>
          <div class="report-launch-text">
            OpenStack credentials were not provided for this session, so this OpenStack-backed report is unavailable.
            Kubernetes-only reports remain available.
          </div>
          <div class="report-launch-pills">
            <span class="meta-pill">OpenStack required</span>
            <span class="meta-pill">Session is Kubernetes-only</span>
          </div>
        </div>
      </div>
    </section>
  `;
}

function renderReportDebugCard(report) {
  const meta = reportState.fetchMeta[reportState.active] || {};
  const debug = report?.debug || {};
  const timing = debug.timing_ms || {};
  const counts = debug.counts || {};
  const statusLabel = meta.status != null ? String(meta.status) : '—';
  const durationLabel = meta.durationMs != null ? `${meta.durationMs} ms` : '—';
  const fetchedAtLabel = meta.fetchedAt || '—';
  const errorLabel = meta.errorText || 'none';
  return `
    <section class="card">
      <div class="card-title"><span>Debug</span></div>
      <div class="card-body report-debug-grid">
        <div class="report-debug-block">
          <div class="report-debug-head">Last Fetch</div>
          <div class="mrow"><span class="ml">Status</span><span class="mv">${esc(statusLabel)}</span></div>
          <div class="mrow"><span class="ml">Duration</span><span class="mv">${esc(durationLabel)}</span></div>
          <div class="mrow"><span class="ml">Fetched</span><span class="mv">${esc(fetchedAtLabel)}</span></div>
          <div class="mrow"><span class="ml">Content-Type</span><span class="mv">${esc(meta.contentType || '—')}</span></div>
          <div class="mrow"><span class="ml">Error</span><span class="mv report-debug-error">${esc(errorLabel)}</span></div>
        </div>
        <div class="report-debug-block">
          <div class="report-debug-head">Backend Timing</div>
          ${Object.keys(timing).length ? Object.entries(timing).map(([key, value]) => `
            <div class="mrow"><span class="ml">${esc(key.replaceAll('_', ' '))}</span><span class="mv">${esc(String(value))} ms</span></div>
          `).join('') : '<div class="card-note">No timing data available.</div>'}
        </div>
        <div class="report-debug-block">
          <div class="report-debug-head">Collection Counts</div>
          ${Object.keys(counts).length ? Object.entries(counts).map(([key, value]) => `
            <div class="mrow"><span class="ml">${esc(key.replaceAll('_', ' '))}</span><span class="mv">${esc(String(value))}</span></div>
          `).join('') : '<div class="card-note">No collection counts available.</div>'}
        </div>
      </div>
    </section>
  `;
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

function renderReportError(activeMeta) {
  const meta = reportState.fetchMeta[reportState.active] || {};
  const issue = guessReportApiIssue(meta.errorText || reportState.error || '', meta.status);
  const timeoutHint = String(meta.errorText || reportState.error || '').toLowerCase().includes('timeout')
    || String(meta.errorText || reportState.error || '').toLowerCase().includes('upstream')
    ? 'This usually means the gateway or upstream API timed out while the report was gathering live data.'
    : null;
  return `
    <section class="report-header-card">
      <div class="report-head-top">
        <div>
          <div class="report-title">${esc(activeMeta.label)}</div>
          <div class="report-subtitle">${esc(activeMeta.subtitle)}</div>
        </div>
      </div>
      <div class="report-meta-row">
        <span class="meta-pill">Status: ${esc(meta.status != null ? String(meta.status) : 'error')}</span>
        <span class="meta-pill">Fetched: ${esc(meta.fetchedAt || '—')}</span>
        <span class="meta-pill">Duration: ${esc(meta.durationMs != null ? `${meta.durationMs} ms` : '—')}</span>
        ${issue ? `<span class="meta-pill">${esc(issue.service)} issue suspected</span>` : ''}
        ${renderReportActionPills()}
      </div>
    </section>

    <section class="card">
      <div class="card-title"><span>Report Request Failed</span></div>
      <div class="card-body">
        <div class="mrow"><span class="ml">Report</span><span class="mv">${esc(activeMeta.label)}</span></div>
        <div class="mrow"><span class="ml">Likely Upstream</span><span class="mv">${esc(issue?.service || 'Unknown')}</span></div>
        <div class="mrow"><span class="ml">HTTP Status</span><span class="mv">${esc(meta.status != null ? String(meta.status) : '—')}</span></div>
        <div class="mrow"><span class="ml">Content-Type</span><span class="mv">${esc(meta.contentType || '—')}</span></div>
        <div class="mrow"><span class="ml">Failure Detail</span><span class="mv report-debug-error">${esc(meta.errorText || reportState.error || 'Unknown error')}</span></div>
        ${timeoutHint ? `<div class="err-block" style="margin:14px 0 0">${esc(timeoutHint)}</div>` : ''}
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
  if (typeof hasOpenStackAuth === 'function' && !hasOpenStackAuth() && reportRequiresOpenStack(reportState.active)) {
    content = renderReportUnavailableState(activeMeta);
  } else if (reportState.loading && !report) {
    content = `<div class="report-empty"><span class="spinner">⟳</span> Loading live report…</div>`;
  } else if (reportState.error) {
    content = renderReportError(activeMeta);
  } else if (report) {
    content = reportState.active === 'capacity-headroom'
      ? renderCapacityReport(activeMeta, report, nowLabel)
      : reportState.active === 'nova-activity-capacity'
        ? renderNovaActivityCapacityReport(activeMeta, report, nowLabel)
      : reportState.active === 'k8s-node-health-density'
        ? renderK8sNodeHealthDensityReport(activeMeta, report, nowLabel)
      : reportState.active === 'k8s-pvc-workload'
        ? renderK8sPvcWorkloadReport(activeMeta, report, nowLabel)
      : reportState.active === 'k8s-rollout-health'
        ? renderK8sRolloutHealthReport(activeMeta, report, nowLabel)
      : reportState.active === 'placement-risk'
        ? renderPlacementRiskReport(activeMeta, report, nowLabel)
      : reportState.active === 'project-placement'
        ? renderProjectPlacementReport(activeMeta, report, nowLabel)
        : renderMaintenanceReport(activeMeta, report, nowLabel);
  } else {
    content = renderReportLaunchState(activeMeta);
  }

  wrap.innerHTML = `
    <div class="reports-shell">
      <aside class="reports-nav">
        <div class="reports-nav-head">Report Navigator</div>
        <div class="reports-nav-group">Operations</div>
        ${Object.entries(REPORT_META).map(([key, meta]) => `
          <div class="reports-nav-item${key === reportState.active ? ' active' : ''}" onclick="selectReport('${escAttr(key)}')" title="${!hasOpenStackAuth() && meta.requiresOpenStack ? 'OpenStack credentials required' : ''}">
            <span class="ico">${meta.icon}</span>
            <div class="meta">
              <div class="name">${esc(meta.label)}</div>
              <div class="sub">${esc(meta.subtitle)}${!hasOpenStackAuth() && meta.requiresOpenStack ? ' · OpenStack required' : ''}</div>
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
  reportState.error = null;
  renderReportsView();
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

function renderNodeReadyTag(ready) {
  return `<span class="report-tag ${ready ? 'green' : 'red'}">${ready ? 'Ready' : 'NotReady'}</span>`;
}

function formatReportMemory(memoryMb) {
  const value = Number(memoryMb);
  if (!Number.isFinite(value)) return '—';
  const gb = value / 1024;
  return `${Math.round(gb)} GB`;
}
