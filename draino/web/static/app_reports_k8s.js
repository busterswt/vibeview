'use strict';

function renderK8sNodeHealthDensityReport(activeMeta, report, nowLabel) {
  const summary = report.summary || {};
  const summaryFoot = report.summary_foot || {};
  const findings = report.findings || [];
  const items = report.items || [];
  const versionItems = report.version_items || [];
  const pvcItems = report.pvc_items || [];
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
        <span class="meta-pill">Kubernetes live view</span>
        <span class="meta-pill">No stored history</span>
        <span class="meta-pill">${esc(String(totalNodes))} nodes</span>
        <span class="meta-pill">Generated ${esc(nowLabel)}</span>
        ${renderReportActionPills()}
      </div>
    </section>

    <section class="report-hero-grid">
      ${renderCapacityHero('Ready Nodes', String(summary.ready_nodes ?? 0), 'good', summaryFoot.ready_nodes || '')}
      ${renderCapacityHero('Version Drift', String(summary.version_drift ?? 0), (summary.version_drift ?? 0) > 0 ? 'warn' : 'good', summaryFoot.version_drift || '')}
      ${renderCapacityHero('High Pod Density', String(summary.high_pod_density ?? 0), (summary.high_pod_density ?? 0) > 0 ? 'warn' : 'good', summaryFoot.high_pod_density || '')}
      ${renderCapacityHero('PVC Hotspots', String(summary.pvc_hotspots ?? 0), (summary.pvc_hotspots ?? 0) > 0 ? 'bad' : 'good', summaryFoot.pvc_hotspots || '')}
    </section>

    <section class="report-grid-two">
      ${renderFindingsCard(findings, 'Highest-Risk Findings')}
      <div class="card">
        <div class="card-title"><span>Version / Density Breakdown</span></div>
        <div class="card-body report-chart-strip">
          ${renderReportBreakdownBar('Ready', summary.ready_nodes ?? 0, totalNodes, 'good')}
          ${renderReportBreakdownBar('Version Drift', summary.version_drift ?? 0, totalNodes, 'warn')}
          ${renderReportBreakdownBar('High Pod Density', summary.high_pod_density ?? 0, totalNodes, 'bad')}
          ${renderReportBreakdownBar('PVC Hotspots', summary.pvc_hotspots ?? 0, totalNodes, 'blue')}
        </div>
      </div>
    </section>

    <section class="card">
      <div class="card-title"><span>Node Health Grid</span></div>
      <div class="card-body report-table-wrap">
        <table class="data-table report-table">
          <thead>
            <tr>
              <th>Node</th>
              <th>Ready</th>
              <th>Kubelet</th>
              <th>Runtime</th>
              <th>Pods</th>
              <th>Pods %</th>
              <th>PVC Pods</th>
              <th>Namespaces</th>
              <th>CPU Req %</th>
              <th>Mem Req %</th>
              <th>Conditions</th>
              <th>Risk</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            ${items.map(item => `
              <tr>
                <td class="mono">${esc(item.node || '')}</td>
                <td>${renderNodeReadyTag(item.ready)}</td>
                <td>${esc(item.kubelet_version || 'unknown')}</td>
                <td>${esc(item.runtime || 'unknown')}</td>
                <td>${esc(String(item.pod_count ?? 0))}</td>
                <td>${renderPercentValue(item.pods_pct)}</td>
                <td>${esc(String(item.pvc_pod_count ?? 0))}</td>
                <td>${esc(String(item.namespace_count ?? 0))}</td>
                <td>${renderPercentValue(item.cpu_req_pct)}</td>
                <td>${renderPercentValue(item.mem_req_pct)}</td>
                <td>${esc(item.conditions?.length ? item.conditions.join(', ') : 'Healthy')}</td>
                <td><span class="report-tag ${item.risk === 'high' ? 'red' : item.risk === 'medium' ? 'yellow' : 'green'}">${esc(item.risk || 'low')}</span></td>
                <td>${esc(item.reason || '')}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    </section>

    <section class="report-grid-two">
      <div class="card">
        <div class="card-title"><span>Version Drift Breakdown</span></div>
        <div class="card-body report-table-wrap">
          <table class="data-table report-table">
            <thead>
              <tr>
                <th>Kubelet Version</th>
                <th>Node Count</th>
                <th>Nodes</th>
                <th>Majority</th>
              </tr>
            </thead>
            <tbody>
              ${versionItems.map(item => `
                <tr>
                  <td>${esc(item.kubelet_version || 'unknown')}</td>
                  <td>${esc(String(item.node_count ?? 0))}</td>
                  <td class="mono">${esc(item.nodes || '—')}</td>
                  <td><span class="report-tag ${item.is_majority ? 'green' : 'yellow'}">${item.is_majority ? 'Yes' : 'No'}</span></td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      </div>

      <div class="card">
        <div class="card-title"><span>PVC Density</span></div>
        <div class="card-body report-table-wrap">
          <table class="data-table report-table">
            <thead>
              <tr>
                <th>Node</th>
                <th>PVC Pods</th>
                <th>PVC Claims</th>
                <th>Namespaces</th>
              </tr>
            </thead>
            <tbody>
              ${pvcItems.map(item => `
                <tr>
                  <td class="mono">${esc(item.node || '')}</td>
                  <td>${esc(String(item.pvc_pod_count ?? 0))}</td>
                  <td>${esc(String(item.pvc_claim_count ?? 0))}</td>
                  <td>${esc(String(item.namespace_count ?? 0))}</td>
                </tr>
              `).join('') || `
                <tr>
                  <td colspan="4" class="card-note">No PVC-backed pod concentration detected in the current snapshot.</td>
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

function renderK8sPvcWorkloadReport(activeMeta, report, nowLabel) {
  const summary = report.summary || {};
  const summaryFoot = report.summary_foot || {};
  const findings = report.findings || [];
  const items = report.items || [];
  const storageItems = report.storage_items || [];
  const replicaNodeItems = report.replica_node_items || [];
  const totalPvcs = Number(report.scope?.pvcs ?? items.length) || 0;
  return `
    <section class="report-header-card">
      <div class="report-head-top">
        <div>
          <div class="report-title">${esc(report.title || activeMeta.label)}</div>
          <div class="report-subtitle">${esc(report.subtitle || '')}</div>
        </div>
      </div>
      <div class="report-meta-row">
        <span class="meta-pill">Kubernetes live view</span>
        <span class="meta-pill">CSI / PVC topology</span>
        <span class="meta-pill">No stored history</span>
        <span class="meta-pill">${esc(String(totalPvcs))} PVCs</span>
        <span class="meta-pill">Generated ${esc(nowLabel)}</span>
        ${renderReportActionPills()}
      </div>
    </section>

    <section class="report-hero-grid">
      ${renderCapacityHero('Bound PVCs', String(summary.bound_pvcs ?? 0), 'good', summaryFoot.bound_pvcs || '')}
      ${renderCapacityHero('Replica Skew', String(summary.replica_skew ?? 0), (summary.replica_skew ?? 0) > 0 ? 'warn' : 'good', summaryFoot.replica_skew || '')}
      ${renderCapacityHero('Single-Node Attachments', String(summary.single_node_use ?? 0), (summary.single_node_use ?? 0) > 0 ? 'warn' : 'good', summaryFoot.single_node_use || '')}
      ${renderCapacityHero('Orphan / Unbound', String(summary.orphan_unbound ?? 0), (summary.orphan_unbound ?? 0) > 0 ? 'bad' : 'good', summaryFoot.orphan_unbound || '')}
    </section>

    <section class="report-grid-two">
      ${renderFindingsCard(findings, 'Highest-Risk Findings')}
      <div class="card">
        <div class="card-title"><span>Replica / Consumer Breakdown</span></div>
        <div class="card-body report-chart-strip">
          ${renderReportBreakdownBar('Replica Skew', summary.replica_skew ?? 0, totalPvcs, 'bad')}
          ${renderReportBreakdownBar('Single-Node Use', summary.single_node_use ?? 0, totalPvcs, 'warn')}
          ${renderReportBreakdownBar('Unbound / Orphan', summary.orphan_unbound ?? 0, totalPvcs, 'blue')}
          ${renderReportBreakdownBar('Healthy Spread', Math.max(0, totalPvcs - ((summary.replica_skew ?? 0) + (summary.orphan_unbound ?? 0))), totalPvcs, 'good')}
        </div>
      </div>
    </section>

    <section class="card">
      <div class="card-title"><span>PVC Workload Grid</span></div>
      <div class="card-body report-table-wrap">
        <table class="data-table report-table">
          <thead>
            <tr>
              <th>Namespace / PVC</th>
              <th>Storage Class</th>
              <th>Size</th>
              <th>Access</th>
              <th>Replicas</th>
              <th>Replica Nodes</th>
              <th>Consumer Pod</th>
              <th>Consumer Node</th>
              <th>Status</th>
              <th>Risk</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            ${items.map(item => `
              <tr>
                <td class="mono">${esc(item.namespace || '')} / ${esc(item.name || '')}</td>
                <td>${esc(item.storageclass || '—')}</td>
                <td>${esc(item.capacity || '—')}</td>
                <td>${esc(item.access_modes || '—')}</td>
                <td>${item.replica_count != null ? esc(String(item.replica_count)) : '—'}</td>
                <td class="mono report-wrap-cell">${esc(item.replica_nodes_label || '—')}</td>
                <td class="mono report-wrap-cell">${esc(item.consumer_pod_label || '—')}</td>
                <td class="mono report-wrap-cell">${esc(item.consumer_node_label || '—')}</td>
                <td><span class="report-tag ${String(item.status || '').toLowerCase() === 'bound' ? 'green' : 'yellow'}">${esc(item.status || 'unknown')}</span></td>
                <td><span class="report-tag ${item.risk === 'high' ? 'red' : item.risk === 'medium' ? 'yellow' : 'green'}">${esc(item.risk || 'low')}</span></td>
                <td class="report-wrap-cell">${esc(item.reason || '')}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    </section>

    <section class="report-grid-two">
      <div class="card">
        <div class="card-title"><span>Storage Class Breakdown</span></div>
        <div class="card-body report-table-wrap">
          <table class="data-table report-table">
            <thead>
              <tr>
                <th>Storage Class</th>
                <th>PVCs</th>
                <th>Typical Replicas</th>
                <th>Top Consumer Nodes</th>
              </tr>
            </thead>
            <tbody>
              ${storageItems.map(item => `
                <tr>
                  <td>${esc(item.storageclass || '—')}</td>
                  <td>${esc(String(item.pvc_count ?? 0))}</td>
                  <td>${item.typical_replicas != null ? esc(String(item.typical_replicas)) : '—'}</td>
                  <td class="mono report-wrap-cell">${esc(item.top_consumer_nodes || '—')}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      </div>

      <div class="card">
        <div class="card-title"><span>Replica Placement Risk</span></div>
        <div class="card-body report-table-wrap">
          <table class="data-table report-table">
            <thead>
              <tr>
                <th>Node</th>
                <th>PVC Replicas</th>
                <th>Active Consumers</th>
                <th>Namespaces</th>
              </tr>
            </thead>
            <tbody>
              ${replicaNodeItems.map(item => `
                <tr>
                  <td class="mono">${esc(item.node || '')}</td>
                  <td>${esc(String(item.pvc_count ?? 0))}</td>
                  <td>${esc(String(item.consumer_count ?? 0))}</td>
                  <td>${esc(String(item.namespace_count ?? 0))}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      </div>
    </section>

    ${renderReportDebugCard(report)}
  `;
}

function renderK8sRolloutHealthReport(activeMeta, report, nowLabel) {
  const summary = report.summary || {};
  const summaryFoot = report.summary_foot || {};
  const findings = report.findings || [];
  const workloadItems = report.workload_items || [];
  const restartItems = report.restart_items || [];
  const fatalItems = report.fatal_items || [];
  const totalWorkloads = Number(report.scope?.workloads ?? workloadItems.length) || 0;
  const podsWithSignals = Number(report.scope?.pods_with_restart_signals ?? restartItems.length) || 0;
  return `
    <section class="report-header-card">
      <div class="report-head-top">
        <div>
          <div class="report-title">${esc(report.title || activeMeta.label)}</div>
          <div class="report-subtitle">${esc(report.subtitle || '')}</div>
        </div>
      </div>
      <div class="report-meta-row">
        <span class="meta-pill">Kubernetes live view</span>
        <span class="meta-pill">Stateless restart signals</span>
        <span class="meta-pill">${esc(String(totalWorkloads))} workloads</span>
        <span class="meta-pill">${esc(String(podsWithSignals))} pods with restart signal</span>
        <span class="meta-pill">Generated ${esc(nowLabel)}</span>
        ${renderReportActionPills()}
      </div>
    </section>

    <section class="report-hero-grid">
      ${renderCapacityHero('Broken Rollouts', String(summary.broken_rollouts ?? 0), (summary.broken_rollouts ?? 0) > 0 ? 'bad' : 'good', summaryFoot.broken_rollouts || '')}
      ${renderCapacityHero('Recent Restarts', String(summary.recent_restarts ?? 0), (summary.recent_restarts ?? 0) > 0 ? 'warn' : 'good', summaryFoot.recent_restarts || '')}
      ${renderCapacityHero('Fatal Signals', String(summary.fatal_signals ?? 0), (summary.fatal_signals ?? 0) > 0 ? 'bad' : 'good', summaryFoot.fatal_signals || '')}
      ${renderCapacityHero('Coverage Gaps', String(summary.misscheduled_coverage ?? 0), (summary.misscheduled_coverage ?? 0) > 0 ? 'warn' : 'good', summaryFoot.misscheduled_coverage || '')}
    </section>

    <section class="report-grid-two">
      ${renderFindingsCard(findings, 'Highest-Risk Findings')}
      <div class="card">
        <div class="card-title"><span>Rollout / Restart Breakdown</span></div>
        <div class="card-body report-chart-strip">
          ${renderReportBreakdownBar('At-risk workloads', summary.broken_rollouts ?? 0, totalWorkloads, 'bad')}
          ${renderReportBreakdownBar('Recent restarts', summary.recent_restarts ?? 0, Math.max(podsWithSignals, summary.recent_restarts ?? 0), 'warn')}
          ${renderReportBreakdownBar('Fatal signals', summary.fatal_signals ?? 0, Math.max(podsWithSignals, summary.fatal_signals ?? 0), 'bad')}
          ${renderReportBreakdownBar('Coverage gaps', summary.misscheduled_coverage ?? 0, totalWorkloads, 'blue')}
        </div>
      </div>
    </section>

    <section class="card">
      <div class="card-title"><span>Rollout Standouts</span></div>
      <div class="card-body report-table-wrap">
        <table class="data-table report-table">
          <thead>
            <tr>
              <th>Namespace</th>
              <th>Workload</th>
              <th>Kind</th>
              <th>Ready</th>
              <th>Updated</th>
              <th>Available</th>
              <th>Unavailable</th>
              <th>Revision Drift</th>
              <th>Risk</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            ${workloadItems.map(item => `
              <tr>
                <td class="dim">${esc(item.namespace || '')}</td>
                <td>${esc(item.name || '')}</td>
                <td><span class="report-tag blue">${esc(item.kind || '')}</span></td>
                <td class="mono">${esc(`${item.ready ?? 0}/${item.desired ?? 0}`)}</td>
                <td>${esc(String(item.updated ?? 0))}</td>
                <td>${esc(String(item.available ?? 0))}</td>
                <td${(Number(item.unavailable ?? 0) > 0) ? ' class="bad"' : ''}>${esc(String(item.unavailable ?? 0))}</td>
                <td class="mono report-wrap-cell">${esc(item.revision_drift || '—')}</td>
                <td><span class="report-tag ${item.risk === 'high' ? 'red' : item.risk === 'medium' ? 'yellow' : 'green'}">${esc(item.risk || 'low')}</span></td>
                <td class="report-wrap-cell">${esc(item.reason || '')}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    </section>

    <section class="card">
      <div class="card-title"><span>Recent Pod Restarts</span></div>
      <div class="card-body report-table-wrap">
        <table class="data-table report-table">
          <thead>
            <tr>
              <th>Namespace</th>
              <th>Pod</th>
              <th>Owner</th>
              <th>Node</th>
              <th>Restart Count</th>
              <th>Window</th>
              <th>Last Reason</th>
              <th>Last Exit</th>
              <th>Risk</th>
            </tr>
          </thead>
          <tbody>
            ${restartItems.map(item => `
              <tr>
                <td class="dim">${esc(item.namespace || '')}</td>
                <td>${esc(item.pod || '')}</td>
                <td>${esc(`${item.owner_name || ''} / ${item.owner_kind || ''}`)}</td>
                <td class="mono">${esc(item.node || '—')}</td>
                <td>${esc(String(item.restart_count ?? 0))}</td>
                <td><span class="report-tag ${item.window === '5m' ? 'red' : item.window === '15m' ? 'yellow' : 'blue'}">${esc(item.window || '—')}</span></td>
                <td>${esc(item.last_reason || 'Unknown')}</td>
                <td class="mono">${esc(item.last_exit_code || '—')}</td>
                <td><span class="report-tag ${item.risk === 'high' ? 'red' : item.risk === 'medium' ? 'yellow' : 'blue'}">${esc(item.risk || 'info')}</span></td>
              </tr>
            `).join('') || `
              <tr>
                <td colspan="9" class="card-note">No recent restart signals detected from current pod status.</td>
              </tr>
            `}
          </tbody>
        </table>
      </div>
    </section>

    <section class="report-grid-two">
      <div class="card">
        <div class="card-title"><span>Fatal Reason Breakdown</span></div>
        <div class="card-body report-table-wrap">
          <table class="data-table report-table">
            <thead>
              <tr>
                <th>Reason</th>
                <th>Count</th>
              </tr>
            </thead>
            <tbody>
              ${fatalItems.map(item => `
                <tr>
                  <td>${esc(item.reason || '')}</td>
                  <td>${esc(String(item.count ?? 0))}</td>
                </tr>
              `).join('') || `
                <tr>
                  <td colspan="2" class="card-note">No fatal restart reasons in the current snapshot.</td>
                </tr>
              `}
            </tbody>
          </table>
        </div>
      </div>

      <div class="card">
        <div class="card-title"><span>Collection Notes</span></div>
        <div class="card-body">
          <div class="card-note">Recent restart windows are derived from current container status only. This stays stateless and low-cost, but older restart events may no longer be visible if kubelet status has rotated.</div>
        </div>
      </div>
    </section>

    ${renderReportDebugCard(report)}
  `;
}
