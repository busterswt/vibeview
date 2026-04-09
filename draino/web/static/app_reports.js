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
  'project-placement': {
    label: 'Project Placement',
    subtitle: 'Tenant VM distribution across compute hosts',
    url: '/api/reports/project-placement',
    csvUrl: '/api/reports/project-placement.csv',
    icon: '🏢',
  },
  'placement-risk': {
    label: 'Placement Risk',
    subtitle: 'Concentration and maintenance blast radius',
    url: '/api/reports/placement-risk',
    csvUrl: '/api/reports/placement-risk.csv',
    icon: '⚠️',
  },
};

function guessReportApiIssue(message, status) {
  const text = String(message || '').toLowerCase();
  let service = null;
  if (text.includes('neutron')) service = 'Neutron';
  else if (text.includes('nova')) service = 'Nova';
  else if (text.includes('keystone') || (text.includes('auth') && text.includes('token'))) service = 'Keystone';
  else if ((text.includes('timeout') || text.includes('upstream')) && reportState.active === 'capacity-headroom') service = 'Nova';
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
    else if (reportState.active === 'capacity-headroom') recordApiSuccess('Nova');
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
      </div>
      <div class="report-meta-row">
        <span class="meta-pill">Generated: ${esc(nowLabel)}</span>
        <span class="meta-pill">Source: ${esc(report.source || 'Live environment')}</span>
        <span class="meta-pill">Scope: ${esc(String(report.scope?.nodes ?? items.length))} nodes</span>
        <span class="meta-pill">Mode: Live snapshot only</span>
        ${renderReportActionPills()}
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

    ${renderReportDebugCard(report)}
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
      </div>
      <div class="report-meta-row">
        <span class="meta-pill">Generated: ${esc(nowLabel)}</span>
        <span class="meta-pill">Scope: ${esc(String(report.scope?.computes ?? items.length))} computes / ${esc(String(report.scope?.instances ?? 0))} instances / ${esc(String(report.scope?.pods ?? 0))} pods</span>
        <span class="meta-pill">Source: ${esc(report.source || 'Live environment')}</span>
        <span class="meta-pill">No stored history</span>
        ${renderReportActionPills()}
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

    ${renderReportDebugCard(report)}
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

function renderProjectPlacementReport(activeMeta, report, nowLabel) {
  const summary = report.summary || {};
  const summaryFoot = report.summary_foot || {};
  const findings = report.findings || [];
  const items = report.items || [];
  return `
    <section class="report-header-card">
      <div class="report-head-top">
        <div>
          <div class="report-title">${esc(report.title || activeMeta.label)}</div>
          <div class="report-subtitle">${esc(report.subtitle || '')}</div>
        </div>
      </div>
      <div class="report-meta-row">
        <span class="meta-pill">Generated: ${esc(nowLabel)}</span>
        <span class="meta-pill">Scope: ${esc(String(report.scope?.projects ?? items.length))} projects / ${esc(String(report.scope?.instances ?? 0))} instances</span>
        <span class="meta-pill">Source: ${esc(report.source || 'Live environment')}</span>
        <span class="meta-pill">No stored history</span>
        ${renderReportActionPills()}
      </div>
    </section>

    <section class="report-hero-grid">
      ${renderCapacityHero('Projects At Risk', String(summary.projects_at_risk ?? 0), (summary.projects_at_risk ?? 0) > 0 ? 'warn' : 'good', summaryFoot.projects_at_risk || '')}
      ${renderCapacityHero('High-Risk Projects', String(summary.high_risk_projects ?? 0), (summary.high_risk_projects ?? 0) > 0 ? 'bad' : 'good', summaryFoot.high_risk_projects || '')}
      ${renderCapacityHero('Single-Host Projects', String(summary.single_host_projects ?? 0), (summary.single_host_projects ?? 0) > 0 ? 'warn' : 'good', summaryFoot.single_host_projects || '')}
      ${renderCapacityHero('Largest Project', String(summary.largest_project_vms ?? 0), 'good', summaryFoot.largest_project_vms || '')}
    </section>

    <section class="report-grid-two">
      ${renderFindingsCard(findings, 'Highest-Risk Findings')}
      <div class="card">
        <div class="card-title"><span>Concentration Breakdown</span></div>
        <div class="card-body report-chart-strip">
          ${renderReportBreakdownBar('High risk', summary.high_risk_projects ?? 0, report.scope?.projects ?? items.length, 'bad')}
          ${renderReportBreakdownBar('At risk', summary.projects_at_risk ?? 0, report.scope?.projects ?? items.length, 'warn')}
          ${renderReportBreakdownBar('Single-host', summary.single_host_projects ?? 0, report.scope?.projects ?? items.length, 'warn')}
        </div>
      </div>
    </section>

    <section class="card">
      <div class="card-title"><span>Project VM Distribution</span></div>
      <div class="card-body report-table-wrap">
        <table class="data-table report-table">
          <thead>
            <tr>
              <th>Project</th>
              <th>Project ID</th>
              <th>VMs</th>
              <th>Hosts</th>
              <th>Top Host</th>
              <th>Top Share</th>
              <th>Top Hosts</th>
              <th>Risk</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            ${items.map(item => `
              <tr>
                <td>${esc(item.project_name || item.project_id || '')}</td>
                <td class="mono">${esc(item.project_id || '')}</td>
                <td>${esc(String(item.vm_count ?? 0))}</td>
                <td>${esc(String(item.host_count ?? 0))}</td>
                <td class="mono">${esc(item.top_host_label || item.top_host || '—')}</td>
                <td>${esc(item.has_dominant_host ? `${Math.round(item.top_host_pct || 0)}%` : '—')}</td>
                <td>${esc(item.top_hosts_label || '—')}</td>
                <td><span class="report-tag ${item.risk === 'high' ? 'red' : item.risk === 'medium' ? 'yellow' : 'green'}">${esc(item.risk || '')}</span></td>
                <td>${esc(item.reason || '')}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    </section>

    ${renderReportDebugCard(report)}
  `;
}

function renderPlacementRiskReport(activeMeta, report, nowLabel) {
  const summary = report.summary || {};
  const summaryFoot = report.summary_foot || {};
  const findings = report.findings || [];
  const controlItems = report.control_plane_items || [];
  const edgeItems = report.edge_items || [];
  const densityItems = report.density_items || [];
  const totalCritical = report.scope?.critical_nodes ?? controlItems.length;
  const totalEdge = report.scope?.edge_hosts ?? edgeItems.length;
  const totalCompute = report.scope?.computes ?? densityItems.length;
  return `
    <section class="report-header-card">
      <div class="report-head-top">
        <div>
          <div class="report-title">${esc(report.title || activeMeta.label)}</div>
          <div class="report-subtitle">${esc(report.subtitle || '')}</div>
        </div>
      </div>
      <div class="report-meta-row">
        <span class="meta-pill">Generated: ${esc(nowLabel)}</span>
        <span class="meta-pill">Scope: ${esc(String(report.scope?.nodes ?? 0))} nodes / ${esc(String(report.scope?.computes ?? 0))} computes</span>
        <span class="meta-pill">Source: ${esc(report.source || 'Live environment')}</span>
        <span class="meta-pill">No stored history</span>
        ${renderReportActionPills()}
      </div>
    </section>

    <section class="report-hero-grid">
      ${renderCapacityHero('etcd Risk', String(summary.etcd_risk || 'low').toUpperCase(), summary.etcd_risk === 'high' ? 'bad' : summary.etcd_risk === 'medium' ? 'warn' : 'good', summaryFoot.etcd_risk || '')}
      ${renderCapacityHero('MariaDB Spread', String(summary.mariadb_hosts ?? 0), (summary.mariadb_hosts ?? 0) > 0 ? 'warn' : 'good', summaryFoot.mariadb_hosts || '')}
      ${renderCapacityHero('Gateway Concentration', String(summary.gateway_hosts ?? 0), (summary.gateway_hosts ?? 0) < 3 ? 'warn' : 'good', summaryFoot.gateway_hosts || '')}
      ${renderCapacityHero('High-Density Hosts', String(summary.density_hotspots ?? 0), (summary.density_hotspots ?? 0) > 0 ? 'bad' : 'good', summaryFoot.density_hotspots || '')}
    </section>

    <section class="report-grid-two">
      ${renderFindingsCard(findings, 'Highest-Risk Findings')}
      <div class="card">
        <div class="card-title"><span>Risk Breakdown</span></div>
        <div class="card-body report-chart-strip">
          ${renderReportBreakdownBar('Critical role hosts', controlItems.length, totalCritical || controlItems.length || 1, 'bad')}
          ${renderReportBreakdownBar('Edge / gateway hosts', edgeItems.length, totalEdge || edgeItems.length || 1, edgeItems.some(item => item.risk === 'high') ? 'warn' : 'good')}
          ${renderReportBreakdownBar('High-density hosts', densityItems.length, totalCompute || densityItems.length || 1, densityItems.some(item => item.risk === 'high') ? 'bad' : 'warn')}
          ${renderReportBreakdownBar('Drain-safe criticals', controlItems.filter(item => item.maintenance === 'ready').length, totalCritical || controlItems.length || 1, 'good')}
        </div>
      </div>
    </section>

    <section class="card">
      <div class="card-title"><span>Control Plane Placement</span></div>
      <div class="card-body report-table-wrap">
        <table class="data-table report-table">
          <thead>
            <tr>
              <th>Node</th>
              <th>Role</th>
              <th>AZ</th>
              <th>Aggregate</th>
              <th>K8s</th>
              <th>Nova</th>
              <th>Maintenance</th>
              <th>Risk</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            ${controlItems.map(item => `
              <tr>
                <td class="mono">${esc(item.node || '')}</td>
                <td><span class="report-tag ${item.role === 'etcd' ? 'red' : 'purple'}">${esc(item.role || '')}</span></td>
                <td>${esc(item.availability_zone || '—')}</td>
                <td>${esc(item.aggregate || '—')}</td>
                <td>${renderReportStatus(item.k8s_status)}</td>
                <td>${renderReportStatus(item.nova_status)}</td>
                <td><span class="report-tag ${item.maintenance === 'ready' ? 'green' : 'yellow'}">${esc(item.maintenance || '')}</span></td>
                <td><span class="report-tag ${item.risk === 'high' ? 'red' : item.risk === 'medium' ? 'yellow' : 'green'}">${esc(item.risk || '')}</span></td>
                <td>${esc(item.reason || '')}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    </section>

    <section class="card">
      <div class="card-title"><span>Gateway / Edge Placement</span></div>
      <div class="card-body report-table-wrap">
        <table class="data-table report-table">
          <thead>
            <tr>
              <th>Node</th>
              <th>AZ</th>
              <th>Aggregate</th>
              <th>Amphorae</th>
              <th>VMs</th>
              <th>Maintenance</th>
              <th>Risk</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            ${edgeItems.map(item => `
              <tr>
                <td class="mono">${esc(item.node || '')}</td>
                <td>${esc(item.availability_zone || '—')}</td>
                <td>${esc(item.aggregate || '—')}</td>
                <td>${esc(String(item.amphora_count ?? 0))}</td>
                <td>${esc(String(item.vm_count ?? 0))}</td>
                <td><span class="report-tag ${item.maintenance === 'ready' ? 'green' : 'yellow'}">${esc(item.maintenance || '')}</span></td>
                <td><span class="report-tag ${item.risk === 'high' ? 'red' : item.risk === 'medium' ? 'yellow' : 'green'}">${esc(item.risk || '')}</span></td>
                <td>${esc(item.reason || '')}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    </section>

    <section class="card">
      <div class="card-title"><span>High-Density Compute Hosts</span></div>
      <div class="card-body report-table-wrap">
        <table class="data-table report-table">
          <thead>
            <tr>
              <th>Node</th>
              <th>AZ</th>
              <th>VMs</th>
              <th>Pods</th>
              <th>Amphorae</th>
              <th>Maintenance</th>
              <th>Risk</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            ${densityItems.map(item => `
              <tr>
                <td class="mono">${esc(item.node || '')}</td>
                <td>${esc(item.availability_zone || '—')}</td>
                <td>${esc(String(item.vm_count ?? 0))}</td>
                <td>${esc(String(item.pod_count ?? 0))}</td>
                <td>${esc(String(item.amphora_count ?? 0))}</td>
                <td><span class="report-tag ${item.maintenance === 'ready' ? 'green' : 'yellow'}">${esc(item.maintenance || '')}</span></td>
                <td><span class="report-tag ${item.risk === 'high' ? 'red' : item.risk === 'medium' ? 'yellow' : 'green'}">${esc(item.risk || '')}</span></td>
                <td>${esc(item.reason || '')}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    </section>

    ${renderReportDebugCard(report)}
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
    content = renderReportError(activeMeta);
  } else if (report) {
    content = reportState.active === 'capacity-headroom'
      ? renderCapacityReport(activeMeta, report, nowLabel)
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

function formatReportMemory(memoryMb) {
  const value = Number(memoryMb);
  if (!Number.isFinite(value)) return '—';
  const gb = value / 1024;
  return `${Math.round(gb)} GB`;
}
