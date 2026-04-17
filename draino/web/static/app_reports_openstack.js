'use strict';

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

function renderNovaActivityCapacityReport(activeMeta, report, nowLabel) {
  const summary = report.summary || {};
  const summaryFoot = report.summary_foot || {};
  const findings = report.findings || [];
  const azItems = report.az_items || [];
  const projectItems = report.project_items || [];
  const hypervisorItems = report.hypervisor_items || [];
  const deletedItems = report.deleted_items || [];
  const statusItems = report.status_items || [];
  const recent = report.recent_activity || {};
  const totalInstances = Number(report.scope?.instances ?? summary.active_instances ?? 0) || 0;
  return `
    <section class="report-header-card">
      <div class="report-head-top">
        <div>
          <div class="report-title">${esc(report.title || activeMeta.label)}</div>
          <div class="report-subtitle">${esc(report.subtitle || '')}</div>
        </div>
      </div>
      <div class="report-meta-row">
        <span class="meta-pill">Nova current state</span>
        <span class="meta-pill">Placement live usage</span>
        <span class="meta-pill">Recent change window: ${esc(String(report.scope?.window_hours ?? 24))}h</span>
        <span class="meta-pill">${esc(String(totalInstances))} instances</span>
        <span class="meta-pill">Generated ${esc(nowLabel)}</span>
        ${renderReportActionPills()}
      </div>
    </section>

    <section class="card">
      <div class="card-body">
        <div class="card-note">This report is API-derived only. It does not persist historical snapshots, so deleted visibility and recent activity only reflect what Nova still exposes right now.</div>
      </div>
    </section>

    <section class="report-hero-grid report-hero-grid-five">
      ${renderCapacityHero('Active Instances', String(summary.active_instances ?? 0), 'good', summaryFoot.active_instances || '')}
      ${renderCapacityHero('Deleted Visible', String(summary.deleted_visible ?? 0), (summary.deleted_visible ?? 0) > 0 ? 'warn' : 'good', summaryFoot.deleted_visible || '')}
      ${renderCapacityHero(`Changed Since ${esc(String(report.scope?.window_hours ?? 24))}h`, String(summary.changed_since_window ?? 0), (summary.changed_since_window ?? 0) > 0 ? 'warn' : 'good', summaryFoot.changed_since_window || '')}
      ${renderCapacityHero('vCPU Headroom', renderPercentValue(summary.vcpu_headroom_pct), summary.vcpu_headroom_pct != null && summary.vcpu_headroom_pct < 20 ? 'bad' : 'good', summaryFoot.vcpu_headroom_pct || '')}
      ${renderCapacityHero('RAM Headroom', renderPercentValue(summary.ram_headroom_pct), summary.ram_headroom_pct != null && summary.ram_headroom_pct < 20 ? 'warn' : 'good', summaryFoot.ram_headroom_pct || '')}
    </section>

    <section class="report-grid-two">
      ${renderFindingsCard(findings, 'Highest-Risk Findings')}
      <div class="card">
        <div class="card-title"><span>Current Capacity by AZ</span></div>
        <div class="card-body report-chart-strip">
          ${azItems.flatMap(item => ([
            renderReportBreakdownBar(`${item.availability_zone || 'unknown'} vCPU`, Math.round(item.vcpus_used_pct || 0), 100, (item.vcpus_used_pct || 0) >= 85 ? 'bad' : (item.vcpus_used_pct || 0) >= 70 ? 'warn' : 'good').replace(`${Math.round(item.vcpus_used_pct || 0)} / 100`, `${Math.round(item.vcpus_used_pct || 0)}%`),
            renderReportBreakdownBar(`${item.availability_zone || 'unknown'} RAM`, Math.round(item.memory_mb_used_pct || 0), 100, (item.memory_mb_used_pct || 0) >= 85 ? 'bad' : (item.memory_mb_used_pct || 0) >= 70 ? 'warn' : 'good').replace(`${Math.round(item.memory_mb_used_pct || 0)} / 100`, `${Math.round(item.memory_mb_used_pct || 0)}%`),
          ])).join('')}
        </div>
      </div>
    </section>

    <section class="report-grid-three">
      <div class="card">
        <div class="card-title"><span>Recent Activity Window</span></div>
        <div class="card-body report-chart-strip">
          ${renderReportBreakdownBar('Creates', recent.created ?? 0, Math.max(recent.changed ?? 0, 1), 'good').replace(`${Number(recent.created ?? 0)} / ${Math.max(Number(recent.changed ?? 0), 1)}`, `${Number(recent.created ?? 0)}`)}
          ${renderReportBreakdownBar('Deletes', recent.deleted ?? 0, Math.max(recent.changed ?? 0, 1), 'bad').replace(`${Number(recent.deleted ?? 0)} / ${Math.max(Number(recent.changed ?? 0), 1)}`, `${Number(recent.deleted ?? 0)}`)}
          ${renderReportBreakdownBar('Updates', recent.updated ?? 0, Math.max(recent.changed ?? 0, 1), 'blue').replace(`${Number(recent.updated ?? 0)} / ${Math.max(Number(recent.changed ?? 0), 1)}`, `${Number(recent.updated ?? 0)}`)}
        </div>
      </div>
      <div class="card">
        <div class="card-title"><span>Active Status Mix</span></div>
        <div class="card-body report-chart-strip">
          ${statusItems.map(item => renderReportBreakdownBar(item.status || 'UNKNOWN', item.count ?? 0, Math.max(totalInstances, 1), item.status === 'ACTIVE' ? 'good' : item.status === 'ERROR' ? 'bad' : 'warn')).join('')}
        </div>
      </div>
      <div class="card">
        <div class="card-title"><span>Placement Hotspots</span></div>
        <div class="card-body report-table-wrap">
          <table class="data-table report-table">
            <thead>
              <tr>
                <th>Host</th>
                <th>AZ</th>
                <th>vCPU %</th>
                <th>RAM %</th>
              </tr>
            </thead>
            <tbody>
              ${(report.placement_hotspots || []).map(item => `
                <tr>
                  <td class="mono">${esc(item.hypervisor || '')}</td>
                  <td>${esc(item.availability_zone || '—')}</td>
                  <td>${renderPercentValue(item.vcpus_used_pct)}</td>
                  <td>${renderPercentValue(item.memory_mb_used_pct)}</td>
                </tr>
              `).join('') || `
                <tr>
                  <td colspan="4" class="card-note">No current Placement hotspots detected.</td>
                </tr>
              `}
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <section class="card">
      <div class="card-title"><span>Top Projects by Active Instances</span></div>
      <div class="card-body report-table-wrap">
        <table class="data-table report-table">
          <thead>
            <tr>
              <th>Project</th>
              <th>Project ID</th>
              <th>Active</th>
              <th>Hosts</th>
              <th>24h Changes</th>
              <th>Visible Deletes</th>
              <th>Signal</th>
            </tr>
          </thead>
          <tbody>
            ${projectItems.map(item => `
              <tr>
                <td>${esc(item.project_name || item.project_id || '')}</td>
                <td class="mono">${esc(item.project_id || '')}</td>
                <td>${esc(String(item.active_instances ?? 0))}</td>
                <td>${esc(String(item.host_count ?? 0))}</td>
                <td>${esc(String(item.recent_changes ?? 0))}</td>
                <td>${esc(String(item.deleted_visible ?? 0))}</td>
                <td><span class="report-tag ${item.signal === 'high-churn' ? 'red' : item.signal === 'growing' ? 'yellow' : 'green'}">${esc(item.signal || 'stable')}</span></td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    </section>

    <section class="report-grid-two">
      <div class="card">
        <div class="card-title"><span>Busiest Hypervisors</span></div>
        <div class="card-body report-table-wrap">
          <table class="data-table report-table">
            <thead>
              <tr>
                <th>Host</th>
                <th>AZ</th>
                <th>Instances</th>
                <th>Projects</th>
                <th>vCPU Used %</th>
                <th>RAM Used %</th>
              </tr>
            </thead>
            <tbody>
              ${hypervisorItems.map(item => `
                <tr>
                  <td class="mono">${esc(item.hypervisor || '')}</td>
                  <td>${esc(item.availability_zone || '—')}</td>
                  <td>${esc(String(item.active_instances ?? 0))}</td>
                  <td>${esc(String(item.project_count ?? 0))}</td>
                  <td>${renderPercentValue(item.vcpus_used_pct)}</td>
                  <td>${renderPercentValue(item.memory_mb_used_pct)}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      </div>

      <div class="card">
        <div class="card-title"><span>Deleted Instances Still Visible</span></div>
        <div class="card-body report-table-wrap">
          <table class="data-table report-table">
            <thead>
              <tr>
                <th>Instance</th>
                <th>Project</th>
                <th>Host</th>
                <th>Deleted At</th>
                <th>Flavor</th>
              </tr>
            </thead>
            <tbody>
              ${deletedItems.map(item => `
                <tr>
                  <td class="mono">${esc(item.name || '')}</td>
                  <td>${esc(item.project_name || '')}</td>
                  <td class="mono">${esc(item.host || '—')}</td>
                  <td>${esc(item.deleted_at || '—')}</td>
                  <td>${esc(item.flavor || '—')}</td>
                </tr>
              `).join('') || `
                <tr>
                  <td colspan="5" class="card-note">No deleted instances are currently visible from Nova.</td>
                </tr>
              `}
            </tbody>
          </table>
        </div>
      </div>
    </section>

    ${renderReportDebugCard(report)}
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
