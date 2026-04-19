'use strict';

// ════════════════════════════════════════════════════════════════════════════
// § STATE
// ════════════════════════════════════════════════════════════════════════════

let nodes        = {};          // nodeName → NodeDict (from server)
let selectedNode = null;
let pendingReboot = null;
let hideSucceeded = true;
let lastPodsCache = null;       // { node, pods[] }
let activeTab    = 'summary';
let activeView   = 'infrastructure';
let activeNetworkingView = 'networking';
let activeStorageView = 'openstack-volumes';
let activeProjectView = 'overview';
let ws           = null;
let authReady    = false;
let authInfo     = null;
let appRuntimeTimer = null;
let nodeMonitorTimer = null;
let nodeNetStatsTimer = null;
let nodeIrqBalanceTimer = null;
let nodeSarTrendsTimer = null;
let instancePortStatsTimer = null;
let stressStatusTimer = null;
let wsStatusMode = 'offline';
let sessionExpired = false;
let logoutInProgress = false;
let monitorEntriesHtml = '';
let sidebarDragging = false;
let tasksDragging = false;
let nodeListRefreshing = false;
const API_ISSUE_COOLDOWN_MS = 120000;
const apiIssueState = {
  open: false,
  current: null,
  recent: [],
  suppressedUntil: {},
  lastSuccessAt: null,
};

// Step timing: records when each step first entered a non-pending state.
// Key format: `${nodeName}:${stepKey}:${status}` → Date
const stepTimes = {};

// Hints toggle
let showHints = false;

// Per-instance migration state (outside full evacuation): instance_id → 'migrating'|'error'
const instanceMigrateStates = {};
// Per-instance migration task records for the Recent Tasks panel
// instance_id → { name, nodeName, status, startTime }
const instanceMigrateTasks  = {};

// Sidebar collapse state
const collapsedGroups = new Set();
let nodeSortDirection = 'asc';

// Node detail cache (summary tab)
const nodeDetailCache = {};   // node_name → { loading, k8s, nova, error }
const nodeMetricsCache = {};  // node_name → { loading, current, history, error }
const nodeNetStatsCache = {}; // node_name → { loading, interfaces, error, fetchedAt }
const nodeIrqBalanceCache = {}; // node_name → { loading, interfaces, error, fetchedAt }
const nodeSarTrendsCache = {}; // node_name → { loading, summary, interfaces, error, fetchedAt }
const nodeSarExpanded = {}; // node_name -> boolean
const nodeNetStatsEnabled = {}; // node_name -> Set(interfaceName)
const nodeInstancePortStatsCache = {}; // node_name -> { loading, portsById, error, fetchedAt }
const instanceDetailCache = {}; // instance_id -> { loading, data, error }
const expandedInstanceIdByNode = {}; // node_name -> instance_id
const expandedPortIdByInstance = {}; // instance_id -> port_id

// Node network config cache (configure tab)
// node_name → { annLoading, annotations, ifacesLoading, ifaces, ifacesError }
const nodeNetworkCache = {};
let appRuntimeState = {
  loading: false,
  current: null,
  history: [],
  requests: {},
  limits: {},
  restart_count: null,
  diagnostics: null,
  diagnosticsAction: '',
  diagnosticsError: null,
};

function hasOpenStackAuth() {
  return Boolean(authInfo?.has_openstack_auth);
}

function hasK8sAuth() {
  return Boolean(authInfo?.has_k8s_auth);
}

function renderAuthModeAlert() {
  const el = document.getElementById('auth-mode-alert');
  if (!el) return;
  if (hasOpenStackAuth()) {
    el.classList.remove('open');
    el.innerHTML = '';
    return;
  }
  el.innerHTML = `
    <strong>Kubernetes-only mode.</strong>
    OpenStack credentials were not provided for this session. Kubernetes views remain available, while OpenStack-backed views and reports are currently unavailable.
  `;
  el.classList.add('open');
}

function renderOpenStackUnavailablePanel(title, detail) {
  return `
    <section class="report-launch-card">
      <div class="report-launch-shell">
        <div class="report-launch-icon">☸️</div>
        <div class="report-launch-copy">
          <div class="report-launch-kicker">Kubernetes-only mode</div>
          <div class="report-launch-title">${esc(title)}</div>
          <div class="report-launch-text">${esc(detail)}</div>
        </div>
      </div>
    </section>
  `;
}

function renderK8sUnavailablePanel(title, detail) {
  return `
    <section class="report-launch-card">
      <div class="report-launch-shell">
        <div class="report-launch-icon">☸️</div>
        <div class="report-launch-copy">
          <div class="report-launch-kicker">Kubernetes auth required</div>
          <div class="report-launch-title">${esc(title)}</div>
          <div class="report-launch-text">${esc(detail)}</div>
        </div>
      </div>
    </section>
  `;
}

// Working edit state for the currently-open Configure tab
const netEdit = {
  node:          null,
  bridges:       [],
  mappings:      [],   // [{physnet, bridge}]
  ports:         [],   // [{bridge, iface}]
  bridgesDirty:  false,
  mappingsDirty: false,
  portsDirty:    false,
};

// Pending annotation save (used by warning modal)
let _annWarnPending = null;  // {key, value, successCb}

// Networking view state
const netState       = { data: null, loading: false, page: 1, pageSize: 25, filter: '' };
let   selectedNetwork  = null;
const netDetailState   = {
  loading: false,
  data: null,
  selectedSubnet: null,
  ovn: { loading: false, data: null, error: null },
  ovnSelectedPort: null,
  ovnPortCache: {},
  metadataRepair: { subnetId: null, loading: false, message: '', error: null },
};

// Router view state
const routerState = { data: null, loading: false, page: 1, pageSize: 25, filter: '' };
let   selectedRouter = null;
const routerDetailState = { loading: false, data: null, ovn: { loading: false, data: null, error: null } };

// Load balancer view state
const lbState = { data: null, loading: false, page: 1, pageSize: 25, filter: '' };
let   selectedLoadBalancer = null;
const lbDetailState = { loading: false, data: null, vipOvn: { loading: false, data: null, error: null } };

// Security group view state
const sgState = {
  data: null,
  loading: false,
  page: 1,
  pageSize: 25,
  filter: '',
  auditOnly: true,
  project: '',
  quickFilter: '',
  sort: 'severity',
  projectScopeFilter: '',
  projectScopeExpanded: false,
};
let selectedSecurityGroup = null;
const sgDetailState = { loading: false, data: null };

// Storage view state
const volState = { data: null, loading: false, page: 1, pageSize: 25, filter: '', allProjects: false, retypeMeta: {} };
let selectedVolume = null;
const volumeDetailState = { loading: false, data: null, targetType: '', actionMessage: '', actionTone: 'info', submitting: false };
const volumeSnapshotState = { data: null, loading: false, page: 1, pageSize: 25, filter: '' };
const volumeBackupState = { data: null, loading: false, page: 1, pageSize: 25, filter: '' };
const swiftState = { data: null, loading: false, page: 1, pageSize: 25, filter: '' };

// Projects view state
const projectsState = { data: null, loading: false, filter: '' };
let selectedProjectId = '';
const projectInventoryState = { loading: false, projectId: '', sections: {}, filter: '', activeSection: '', pending: {}, prefetched: {} };
const projectDetailState = { kind: '', item: null, loading: false, data: null, error: null };
const projectQuotaEditState = { section: '', resource: '', value: '', saving: false, error: '' };

// Reports view state
const reportState = {
  active: 'maintenance-readiness',
  loading: false,
  error: null,
  reports: {},
  fetchMeta: {},
};

const stressState = {
  catalogLoading: false,
  catalogError: null,
  catalog: null,
  envLoading: false,
  envError: null,
  env: null,
  statusLoading: false,
  actionLoading: false,
  actionKind: '',
  actionError: null,
  status: null,
  profileKey: '',
  drafts: {},
  detailSections: {
    resources: false,
    servers: false,
  },
  detailsLoading: false,
};

// Compatibility no-ops for older cached networking detail renderers after the
// Kubernetes overlay feature was removed.
if (typeof globalThis.renderNetworkOverlayCard !== 'function') {
  globalThis.renderNetworkOverlayCard = function renderNetworkOverlayCard() { return ''; };
}
if (typeof globalThis.renderRouterOverlayCard !== 'function') {
  globalThis.renderRouterOverlayCard = function renderRouterOverlayCard() { return ''; };
}
if (typeof globalThis.renderLoadBalancerOverlayCard !== 'function') {
  globalThis.renderLoadBalancerOverlayCard = function renderLoadBalancerOverlayCard() { return ''; };
}

const STEP_ICON = { pending:'○', running:'◉', success:'✓', failed:'✗', skipped:'—' };
const OP_COLOR  = {
  queued:'op-queued', migrating:'op-migrating', 'cold-migrating':'op-migrating',
  confirming:'op-migrating', failing_over:'op-migrating',
  complete:'op-complete', failed:'op-failed', pending:'op-queued',
};

// ════════════════════════════════════════════════════════════════════════════
// § HINTS TOGGLE
// ════════════════════════════════════════════════════════════════════════════

function toggleHints() {
  showHints = !showHints;
  document.body.classList.toggle('no-hints', !showHints);
  const btn = document.getElementById('hint-toggle');
  btn.textContent = showHints ? '💡 VMware hints' : '💡 Hints off';
  btn.classList.toggle('on', showHints);
}

function showSessionExpiredOverlay() {
  sessionExpired = true;
  document.getElementById('session-expired-overlay')?.classList.add('open');
}

function hideSessionExpiredOverlay() {
  sessionExpired = false;
  document.getElementById('session-expired-overlay')?.classList.remove('open');
}

function apiIssueFingerprint(issue) {
  return [
    issue?.service || '',
    issue?.operation || '',
    issue?.status != null ? String(issue.status) : '',
    issue?.message || '',
  ].join('|');
}

function recordApiSuccess(service) {
  if (!service || !['Nova', 'Neutron', 'Keystone'].includes(service)) return;
  apiIssueState.lastSuccessAt = new Date().toLocaleTimeString('en-US', { hour12: false });
  apiIssueState.recent = apiIssueState.recent.filter(issue => issue.service !== service);
  if (apiIssueState.current?.service === service) {
    apiIssueState.current = apiIssueState.recent[0] || null;
    apiIssueState.open = Boolean(apiIssueState.current);
  }
  renderApiIssuesOverlay();
}

function recordApiIssue(issue) {
  if (!issue || !['Nova', 'Neutron', 'Keystone'].includes(issue.service || '')) return;
  const entry = {
    service: issue.service,
    operation: issue.operation || '',
    status: issue.status ?? null,
    request_id: issue.request_id || '',
    message: issue.message || 'Unknown upstream error',
    severity: issue.severity || 'high',
    at: new Date().toLocaleTimeString('en-US', { hour12: false }),
  };
  const fingerprint = apiIssueFingerprint(entry);
  apiIssueState.recent = [entry, ...apiIssueState.recent.filter(item => apiIssueFingerprint(item) !== fingerprint)].slice(0, 8);
  const suppressedUntil = apiIssueState.suppressedUntil[fingerprint] || 0;
  if (!apiIssueState.open && Date.now() >= suppressedUntil) {
    apiIssueState.current = entry;
    apiIssueState.open = true;
  } else if (apiIssueState.open) {
    apiIssueState.current = entry;
  }
  renderApiIssuesOverlay();
}

function dismissApiIssuesOverlay() {
  if (apiIssueState.current) {
    apiIssueState.suppressedUntil[apiIssueFingerprint(apiIssueState.current)] = Date.now() + API_ISSUE_COOLDOWN_MS;
  }
  apiIssueState.open = false;
  renderApiIssuesOverlay();
}

function retryApiIssuesNow() {
  apiIssueState.open = false;
  renderApiIssuesOverlay();
  if (activeView === 'reports') return refreshActiveReport();
  if (activeView === 'networking') {
    if (activeNetworkingView === 'routers') return loadRouters(true);
    if (activeNetworkingView === 'loadbalancers') return loadLoadBalancers(true);
    if (activeNetworkingView === 'securitygroups') return loadSecurityGroups(true);
    return loadNetworks(true);
  }
  if (activeView === 'routers') return loadRouters(true);
  if (activeView === 'storage' && typeof refreshActiveStorageView === 'function') return refreshActiveStorageView();
  if (activeView === 'projects' && typeof refreshActiveProjectView === 'function') return refreshActiveProjectView();
  if (activeView === 'infrastructure' && selectedNode) {
    if (activeTab === 'summary' || activeTab === 'placement') return loadNodeDetail(selectedNode, true);
    if (activeTab === 'instances') {
      const instanceId = expandedInstanceIdByNode[selectedNode];
      if (instanceId) return loadInstanceDetail(selectedNode, instanceId, true);
      if (typeof actionRefreshNode === 'function') return actionRefreshNode();
      return;
    }
    if (activeTab === 'pods') {
      if (typeof actionPodsInline === 'function') return actionPodsInline();
      return;
    }
  }
}

function downloadApiIssueSnapshot() {
  const payload = {
    generated_at: new Date().toISOString(),
    current: apiIssueState.current,
    recent: apiIssueState.recent,
    last_success_at: apiIssueState.lastSuccessAt,
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = 'vibeview-api-issues.json';
  link.click();
  URL.revokeObjectURL(url);
}

function renderApiIssuesOverlay() {
  const wrap = document.getElementById('api-issues-overlay');
  const content = document.getElementById('api-issues-overlay-content');
  if (!wrap || !content) return;
  if (!apiIssueState.open || !apiIssueState.current) {
    wrap.classList.remove('open');
    content.innerHTML = '';
    return;
  }
  const current = apiIssueState.current;
  const recent = apiIssueState.recent;
  const impacted = [...new Set(recent.map(issue => issue.service))];
  content.innerHTML = `
    <section class="api-issues-panel">
      <div class="api-issues-head">
        <div class="api-issues-alert">API Issues Detected</div>
        <div class="api-issues-title">OpenStack API responses are failing</div>
        <div class="api-issues-sub">VibeView can keep showing the last successful page state, but live data may be incomplete until Nova, Neutron, or Keystone recover.</div>
      </div>
      <div class="api-issues-body">
        <div class="api-issues-summary">
          <div class="api-issues-summary-card critical">
            <div class="api-issues-summary-label">Services Impacted</div>
            <div class="api-issues-summary-value">${esc(impacted.join(', ') || current.service)}</div>
          </div>
          <div class="api-issues-summary-card critical">
            <div class="api-issues-summary-label">Last Failure</div>
            <div class="api-issues-summary-value">${esc(current.status != null ? `HTTP ${current.status}` : 'Error')}</div>
          </div>
          <div class="api-issues-summary-card">
            <div class="api-issues-summary-label">Occurred</div>
            <div class="api-issues-summary-value">${esc(current.at || '—')}</div>
          </div>
          <div class="api-issues-summary-card">
            <div class="api-issues-summary-label">Last Good Refresh</div>
            <div class="api-issues-summary-value">${esc(apiIssueState.lastSuccessAt || '—')}</div>
          </div>
        </div>
        <div class="api-issues-grid">
          <div class="card">
            <div class="card-title"><span>Active API Failures</span></div>
            <div class="card-body report-findings">
              ${recent.map(issue => `
                <div class="report-finding-row">
                  <div><span class="report-severity ${esc(issue.severity || 'high')}">${esc(issue.severity || 'high')}</span></div>
                  <div class="report-finding-text"><span class="mono">${esc(issue.service)}</span> ${esc(issue.message)}</div>
                </div>
              `).join('')}
            </div>
          </div>
          <div class="card">
            <div class="card-title"><span>Last Error Detail</span></div>
            <div class="card-body">
              <div class="mrow"><span class="ml">Service</span><span class="mv">${esc(current.service)}</span></div>
              <div class="mrow"><span class="ml">Operation</span><span class="mv api-issue-mono">${esc(current.operation || '—')}</span></div>
              <div class="mrow"><span class="ml">Status</span><span class="mv">${esc(current.status != null ? `${current.status}` : '—')}</span></div>
              <div class="mrow"><span class="ml">Request ID</span><span class="mv api-issue-mono">${esc(current.request_id || '—')}</span></div>
              <div class="mrow"><span class="ml">Message</span><span class="mv api-issue-mono">${esc(current.message || '—')}</span></div>
            </div>
          </div>
        </div>
        <div class="card">
          <div class="card-title"><span>Recent API Error Log</span></div>
          <div class="card-body api-issue-table-wrap">
            <table class="data-table api-issue-table">
              <thead>
                <tr><th></th><th>Time</th><th>Service</th><th>Operation</th><th>Status</th><th>Message</th></tr>
              </thead>
              <tbody>
                ${recent.map(issue => `
                  <tr>
                    <td class="api-issue-severity-cell"><span class="api-issue-severity-dot ${esc(issue.severity || 'high')}" title="${esc((issue.severity || 'high').toUpperCase())}"></span></td>
                    <td class="mono">${esc(issue.at || '—')}</td>
                    <td>${esc(issue.service)}</td>
                    <td class="api-issue-mono">${esc(issue.operation || '—')}</td>
                    <td>${esc(issue.status != null ? String(issue.status) : '—')}</td>
                    <td class="api-issue-mono">${esc(issue.message || '—')}</td>
                  </tr>
                `).join('')}
              </tbody>
            </table>
          </div>
        </div>
      </div>
      <div class="api-issues-actions">
        <div class="api-issues-note">Dismissed issues stay quiet for ${Math.round(API_ISSUE_COOLDOWN_MS / 1000)} seconds unless a fresh failure appears.</div>
        <div class="api-issues-btn-row">
          <button class="btn" onclick="dismissApiIssuesOverlay()">Dismiss And Keep Working</button>
          <button class="btn" onclick="downloadApiIssueSnapshot()">Download Debug Snapshot</button>
          <button class="btn danger" onclick="retryApiIssuesNow()">Retry Now</button>
        </div>
      </div>
    </section>
  `;
  wrap.classList.add('open');
}

function returnToLogin() {
  window.location = '/';
}

function setAuthenticatedUI(info) {
  authReady = true;
  logoutInProgress = false;
  authInfo = info;
  hideSessionExpiredOverlay();
  document.getElementById('logout-btn').classList.add('visible');
  const label = !info?.has_openstack_auth
    ? 'Kubernetes-only session'
    : info?.username && info?.project_name
    ? `${info.username} @ ${info.project_name}`
    : (info?.username || 'Authenticated');
  document.getElementById('user-label').textContent = label;
  document.getElementById('user-dot').textContent = label.slice(0, 1).toUpperCase();
  document.getElementById('bc-reboot').title = !info?.has_openstack_auth
    ? 'Unavailable without OpenStack credentials'
    : info?.is_admin ? '' : "Requires OpenStack admin role";
  renderAuthModeAlert();
  wsSetStatus('connecting');
  if (!ws || ws.readyState === WebSocket.CLOSED) wsConnect();
  refreshAppRuntime();
  if (!appRuntimeTimer) appRuntimeTimer = setInterval(refreshAppRuntime, 15000);
  if (!nodeMonitorTimer) nodeMonitorTimer = setInterval(refreshSelectedNodeMetrics, 30000);
  if (!nodeNetStatsTimer) nodeNetStatsTimer = setInterval(refreshSelectedNodeNetworkStats, 3000);
  if (!nodeIrqBalanceTimer) nodeIrqBalanceTimer = setInterval(refreshSelectedNodeIrqBalance, 10000);
  if (!nodeSarTrendsTimer) nodeSarTrendsTimer = setInterval(refreshSelectedNodeSarTrends, 60000);
  if (!instancePortStatsTimer) instancePortStatsTimer = setInterval(refreshSelectedInstancePortStats, 3000);
}

function fmtBytes(bytes) {
  if (bytes == null) return '—';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let value = bytes;
  let unit = units[0];
  for (const next of units) {
    unit = next;
    if (Math.abs(value) < 1024 || next === units[units.length - 1]) break;
    value /= 1024;
  }
  return `${value >= 10 ? value.toFixed(0) : value.toFixed(1)} ${unit}`;
}

function fmtKiB(kib) {
  if (kib == null) return '—';
  return fmtBytes(Number(kib) * 1024);
}

function fmtSeconds(seconds) {
  if (seconds == null) return '—';
  let remaining = Math.max(0, Math.floor(Number(seconds)));
  const days = Math.floor(remaining / 86400);
  remaining %= 86400;
  const hours = Math.floor(remaining / 3600);
  remaining %= 3600;
  const minutes = Math.floor(remaining / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function fmtLatencyMs(value) {
  if (value == null) return '—';
  const ms = Number(value);
  if (!Number.isFinite(ms)) return '—';
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)} s`;
  return `${ms.toFixed(0)} ms`;
}

function fmtCount(value) {
  if (value == null) return '—';
  return String(value);
}

function fmtNetRate(bytesPerSecond) {
  if (bytesPerSecond == null) return 'warming up';
  const bitsPerSecond = Number(bytesPerSecond) * 8;
  if (!Number.isFinite(bitsPerSecond)) return '—';
  if (bitsPerSecond >= 1_000_000_000) return `${(bitsPerSecond / 1_000_000_000).toFixed(2)} Gb/s`;
  if (bitsPerSecond >= 1_000_000) return `${(bitsPerSecond / 1_000_000).toFixed(1)} Mb/s`;
  if (bitsPerSecond >= 1_000) return `${(bitsPerSecond / 1_000).toFixed(1)} Kb/s`;
  return `${bitsPerSecond.toFixed(0)} b/s`;
}

function metricValue(metrics, key) {
  return metrics && metrics[key] ? metrics[key] : '—';
}

function buildRuntimeChart(history, key, color, formatter, maxLabel) {
  if (!history?.length) return `<div class="runtime-note">No samples yet.</div>`;
  const width = 320;
  const height = 120;
  const leftPad = 42;
  const rightPad = 8;
  const topPad = 8;
  const bottomPad = 22;
  const plotWidth = width - leftPad - rightPad;
  const plotHeight = height - topPad - bottomPad;
  const values = history.map(p => Number(p[key] || 0));
  const max = Math.max(...values, 1);
  const points = values.map((value, idx) => {
    const x = leftPad + (values.length === 1 ? 0 : (idx / (values.length - 1)) * plotWidth);
    const y = topPad + (plotHeight - ((value / max) * plotHeight));
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const latest = values[values.length - 1];
  const baselineY = topPad + plotHeight;
  return `
    <svg class="runtime-chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet">
      <line x1="${leftPad}" y1="${topPad}" x2="${leftPad}" y2="${baselineY}" stroke="#cfd8dc" stroke-width="1"></line>
      <line x1="${leftPad}" y1="${baselineY}" x2="${width - rightPad}" y2="${baselineY}" stroke="#cfd8dc" stroke-width="1"></line>
      <text x="4" y="${topPad + 10}" fill="#78909c" font-size="10">${esc(maxLabel(max))}</text>
      <text x="18" y="${baselineY - 2}" fill="#78909c" font-size="10">0</text>
      <text x="${leftPad}" y="${height - 6}" fill="#78909c" font-size="10">oldest</text>
      <text x="${width - rightPad - 28}" y="${height - 6}" fill="#78909c" font-size="10">now</text>
      <polyline fill="none" stroke="${color}" stroke-width="2.5" points="${points}"></polyline>
    </svg>
    <div class="runtime-note">Current: ${formatter(latest)} · Peak: ${formatter(max)}</div>
  `;
}

function renderMonitorView() {
  const tab = document.getElementById('monitor-wrap');
  const state = appRuntimeState || {};
  const current = state.current || {};
  const requests = state.requests || {};
  const limits = state.limits || {};
  const diagnostics = state.diagnostics || {};
  const sessions = diagnostics.sessions || {};
  const currentSession = diagnostics.current_session || {};
  const allSessions = diagnostics.all_sessions || {};
  const globalCaches = diagnostics.global_caches || {};
  const latencyLabels = {
    node_list_refresh: 'Node refresh',
    node_detail: 'Node detail',
    node_metrics: 'Node metrics',
    node_network_stats: 'Node net stats',
    instance_preflight: 'VM list',
    vm_detail: 'VM detail',
    pods_list: 'Pod list',
    app_meta: 'Update check',
    app_runtime: 'Monitor data',
  };
  const latencyEntries = Object.entries(state.latencies || {})
    .sort((a, b) => (latencyLabels[a[0]] || a[0]).localeCompare(latencyLabels[b[0]] || b[0]));
  tab.innerHTML = `
    <div class="tab-section-title" style="margin-bottom:10px"><span>VibeView Runtime</span></div>
    <div class="app-runtime-grid">
      <div class="card">
        <div class="card-title">Process Usage</div>
        <div class="card-body">
          <div class="mrow"><span class="ml">CPU</span><span class="mv">${current.cpu_percent != null ? `${current.cpu_percent.toFixed(1)}%` : '—'}</span></div>
          <div class="mrow"><span class="ml">Memory RSS</span><span class="mv">${fmtBytes(current.rss_bytes)}</span></div>
          <div class="mrow"><span class="ml">Restarts</span><span class="mv">${state.restart_count ?? '—'}</span></div>
        </div>
      </div>
      <div class="card">
        <div class="card-title">Pod Limits / Requests</div>
        <div class="card-body">
          <div class="mrow"><span class="ml">CPU request</span><span class="mv">${metricValue(requests, 'cpu')}</span></div>
          <div class="mrow"><span class="ml">CPU limit</span><span class="mv">${metricValue(limits, 'cpu')}</span></div>
          <div class="mrow"><span class="ml">Memory request</span><span class="mv">${metricValue(requests, 'memory')}</span></div>
          <div class="mrow"><span class="ml">Memory limit</span><span class="mv">${metricValue(limits, 'memory')}</span></div>
        </div>
      </div>
      <div class="card">
        <div class="card-title">CPU Usage History</div>
        <div class="card-body">
          ${buildRuntimeChart(state.history, 'cpu_percent', '#1565c0', value => `${value.toFixed(1)}%`, value => `${value.toFixed(0)}%`)}
        </div>
      </div>
      <div class="card">
        <div class="card-title">Memory Usage History</div>
        <div class="card-body">
          ${buildRuntimeChart(state.history, 'rss_bytes', '#2e7d32', fmtBytes, fmtBytes)}
        </div>
      </div>
      <div class="card">
        <div class="card-title">API Latency</div>
        <div class="card-body">
          ${latencyEntries.length ? `
            <table class="data-table" style="margin-bottom:0">
              <thead><tr><th>Operation</th><th>Last</th><th>Avg</th><th>P95</th><th>Count</th></tr></thead>
              <tbody>
                ${latencyEntries.map(([key, value]) => `
                  <tr>
                    <td>${esc(latencyLabels[key] || key)}</td>
                    <td>${esc(fmtLatencyMs(value.last_ms))}</td>
                    <td>${esc(fmtLatencyMs(value.avg_ms))}</td>
                    <td>${esc(fmtLatencyMs(value.p95_ms))}</td>
                    <td>${esc(String(value.count ?? 0))}</td>
                  </tr>
                `).join('')}
              </tbody>
            </table>
          ` : `<div class="runtime-note">No latency samples yet.</div>`}
        </div>
      </div>
    </div>
    <div class="tab-section-title" style="margin-bottom:10px"><span>Memory &amp; Cache Diagnostics</span></div>
    ${state.diagnosticsError ? `<div class="etcd-alert danger">${esc(state.diagnosticsError)}</div>` : ''}
    <div class="app-runtime-grid">
      <div class="card">
        <div class="card-title">Sessions</div>
        <div class="card-body">
          <div class="mrow"><span class="ml">TTL</span><span class="mv">${fmtSeconds(sessions.ttl_seconds)}</span></div>
          <div class="mrow"><span class="ml">Active sessions</span><span class="mv">${fmtCount(sessions.active_count)}</span></div>
          <div class="mrow"><span class="ml">Stored records</span><span class="mv">${fmtCount(sessions.stored_count)}</span></div>
          <div class="mrow"><span class="ml">Expired still stored</span><span class="mv">${fmtCount(sessions.expired_count)}</span></div>
        </div>
      </div>
      <div class="card">
        <div class="card-title">Current Session Cache</div>
        <div class="card-body">
          <div class="mrow"><span class="ml">Tracked nodes</span><span class="mv">${fmtCount(currentSession.node_count)}</span></div>
          <div class="mrow"><span class="ml">Node detail cache</span><span class="mv">${fmtCount(currentSession.node_detail_entries)}</span></div>
          <div class="mrow"><span class="ml">Node metrics cache</span><span class="mv">${fmtCount(currentSession.node_metrics_entries)}</span></div>
          <div class="mrow"><span class="ml">Host signal cache</span><span class="mv">${fmtCount(currentSession.host_signal_entries)}</span></div>
        </div>
      </div>
      <div class="card">
        <div class="card-title">All Session Caches</div>
        <div class="card-body">
          <div class="mrow"><span class="ml">Tracked nodes</span><span class="mv">${fmtCount(allSessions.node_count)}</span></div>
          <div class="mrow"><span class="ml">Node detail entries</span><span class="mv">${fmtCount(allSessions.node_detail_entries)}</span></div>
          <div class="mrow"><span class="ml">Node metrics entries</span><span class="mv">${fmtCount(allSessions.node_metrics_entries)}</span></div>
          <div class="mrow"><span class="ml">Connected clients</span><span class="mv">${fmtCount(allSessions.client_count)}</span></div>
        </div>
      </div>
      <div class="card">
        <div class="card-title">Shared Runtime Caches</div>
        <div class="card-body">
          <div class="mrow"><span class="ml">Node-agent endpoints</span><span class="mv">${fmtCount(globalCaches.node_agent_endpoint_cache)}</span></div>
          <div class="mrow"><span class="ml">Flavor cache</span><span class="mv">${fmtCount(globalCaches.openstack_flavor_cache)}</span></div>
          <div class="mrow"><span class="ml">Runtime samples</span><span class="mv">${fmtCount(globalCaches.runtime_history_samples)}</span></div>
        </div>
      </div>
      <div class="card">
        <div class="card-title">Clear Actions</div>
        <div class="card-body">
          <div class="runtime-note" style="margin-bottom:10px">Use these to reduce retained runtime state without restarting the pod.</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn" ${state.diagnosticsAction ? 'disabled' : ''} onclick="clearRuntimeDiagnostics('clear_current_session_caches')">${state.diagnosticsAction === 'clear_current_session_caches' ? 'Clearing…' : 'Clear Current Session Caches'}</button>
            <button class="btn" ${state.diagnosticsAction ? 'disabled' : ''} onclick="clearRuntimeDiagnostics('sweep_expired_sessions')">${state.diagnosticsAction === 'sweep_expired_sessions' ? 'Sweeping…' : 'Sweep Expired Sessions'}</button>
            <button class="btn" ${state.diagnosticsAction ? 'disabled' : ''} onclick="clearRuntimeDiagnostics('clear_global_runtime_caches')">${state.diagnosticsAction === 'clear_global_runtime_caches' ? 'Clearing…' : 'Clear Shared Caches'}</button>
          </div>
        </div>
      </div>
    </div>
    <div class="tab-section-title" style="margin-bottom:10px"><span>Events &amp; Alarms</span></div>
    <div class="event-list" id="monitor-log"></div>
  `;
  const ml = document.getElementById('monitor-log');
  if (ml && monitorEntriesHtml) ml.innerHTML = monitorEntriesHtml;
}

async function refreshAppRuntime() {
  try {
    const resp = await fetch('/api/app-runtime');
    if (!resp.ok) return;
    appRuntimeState = {
      ...appRuntimeState,
      ...(await resp.json()),
      diagnosticsAction: '',
      diagnosticsError: null,
    };
    if (activeView === 'monitor') renderMonitorView();
  } catch (_) {}
}

async function clearRuntimeDiagnostics(action) {
  appRuntimeState = { ...appRuntimeState, diagnosticsAction: action, diagnosticsError: null };
  if (activeView === 'monitor') renderMonitorView();
  try {
    const resp = await fetch('/api/app-runtime/clear', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    });
    const payload = await resp.json();
    if (!resp.ok || payload.ok === false) {
      throw new Error(payload.detail || payload.error || 'Failed to clear runtime diagnostics');
    }
    appRuntimeState = {
      ...appRuntimeState,
      diagnostics: payload.diagnostics || appRuntimeState.diagnostics,
      diagnosticsAction: '',
      diagnosticsError: null,
    };
    if (activeView === 'monitor') renderMonitorView();
    refreshAppRuntime();
  } catch (e) {
    appRuntimeState = {
      ...appRuntimeState,
      diagnosticsAction: '',
      diagnosticsError: String(e),
    };
    if (activeView === 'monitor') renderMonitorView();
  }
}

async function bootstrapSession() {
  try {
    const resp = await fetch('/api/session');
    const json = await resp.json();
    if (json.authenticated) setAuthenticatedUI(json);
    else window.location = '/';
  } catch (e) {
    window.location = '/';
  }
}

async function logout() {
  logoutInProgress = true;
  try {
    await fetch('/api/session', { method: 'DELETE' });
  } catch (_) {}
  window.location = '/';
}

// ════════════════════════════════════════════════════════════════════════════
// § WEBSOCKET
// ════════════════════════════════════════════════════════════════════════════

function wsConnect() {
  if (!authReady || sessionExpired) return;
  wsSetStatus('connecting');
  setNodeListRefreshing(true);
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen  = () => { wsSetStatus('loading'); };
  ws.onclose = () => {
    if (logoutInProgress) {
      wsSetStatus('offline');
      return;
    }
    wsSetStatus('offline');
    if (authReady) showSessionExpiredOverlay();
  };
  ws.onerror = () => ws.close();
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    const dispatch = {
      full_state:            () => onFullState(msg),
      state_update:          () => onStateUpdate(msg),
      log:                   () => onLog(msg),
      reboot_confirm_needed:    () => onRebootConfirmNeeded(msg),
      reboot_blocked:           () => onRebootBlocked(msg),
      pods:                     () => onPods(msg),
      instance_migrate_status:  () => onInstanceMigrateStatus(msg),
    };
    dispatch[msg.type]?.();
  };
}

function wsSend(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}

function wsSetStatus(mode) {
  wsStatusMode = mode;
  const dot = document.getElementById('ws-dot');
  const label = document.getElementById('ws-label');
  const pill = document.getElementById('ws-pill');
  dot.classList.toggle('live', mode === 'live');
  if (mode === 'live') {
    label.textContent = 'Live';
    pill.title = 'Live WebSocket connection to the VibeView web pod.';
  } else if (mode === 'loading') {
    label.textContent = 'Loading inventory…';
    pill.title = 'Connected. Inventory is still loading.';
  } else if (mode === 'connecting') {
    label.textContent = 'Connecting…';
    pill.title = 'Connecting to the VibeView web pod.';
  } else if (mode === 'reconnecting') {
    label.textContent = 'Reconnecting…';
    pill.title = 'Connection to the VibeView web pod was lost. If a Helm upgrade or rollout just ran, the pod may have been replaced and you may need to sign in again to recreate the session.';
  } else {
    label.textContent = 'Offline';
    pill.title = 'WebSocket connection is offline.';
  }
}

function setNodeListRefreshing(active) {
  nodeListRefreshing = !!active;
  const indicator = document.getElementById('node-refresh-indicator');
  if (!indicator) return;
  indicator.classList.toggle('active', nodeListRefreshing);
}

function normaliseIncomingNode(nextNode, prevNode) {
  const normalisedNode = { ...nextNode };
  const prevPreflight = Array.isArray(prevNode?.preflight_instances) ? prevNode.preflight_instances : [];
  const nextPreflight = Array.isArray(normalisedNode.preflight_instances) ? normalisedNode.preflight_instances : [];
  const shouldKeepPreflightPreview =
    prevPreflight.length > 0 &&
    nextPreflight.length === 0 &&
    normalisedNode.phase === 'idle' &&
    (normalisedNode.preflight_loading === true || normalisedNode.vm_count == null || Number(normalisedNode.vm_count) > 0);
  if (shouldKeepPreflightPreview) {
    normalisedNode.preflight_instances = prevPreflight;
  }
  return normalisedNode;
}

// ════════════════════════════════════════════════════════════════════════════
// § MESSAGE HANDLERS
// ════════════════════════════════════════════════════════════════════════════

function onFullState(msg) {
  const shouldAutoSelect = !selectedNode || !msg.nodes[selectedNode];
  const nextNodes = {};
  for (const [nodeName, nodeData] of Object.entries(msg.nodes || {})) {
    nextNodes[nodeName] = normaliseIncomingNode(nodeData, nodes[nodeName]);
  }
  nodes = nextNodes;
  setNodeListRefreshing(false);
  if (wsStatusMode !== 'live') wsSetStatus('live');
  rebuildSidebar();
  if (shouldAutoSelect) {
    const firstNode = document.querySelector('.tree-item');
    if (firstNode?.dataset.node) {
      selectNode(firstNode.dataset.node);
    } else {
      renderInfraDetail();
    }
  } else {
    renderInfraDetail();
  }
  ensureSelectedEtcdHealthCheck();
}

function onStateUpdate(msg) {
  const prevNode = nodes[msg.node] || null;
  const prevPhase = prevNode?.phase;
  const prevIsEtcd = prevNode?.is_etcd;
  const nextNode = normaliseIncomingNode(msg.data, prevNode);
  nodes[msg.node] = nextNode;
  // Clear individual migrate state when a full workflow kicks off
  if (nextNode.phase === 'running' && prevPhase !== 'running') {
    Object.keys(instanceMigrateStates).forEach(k => delete instanceMigrateStates[k]);
    Object.keys(instanceMigrateTasks).forEach(k => delete instanceMigrateTasks[k]);
  }
  trackStepTimes(msg.node, nextNode.steps || []);
  updateSidebarRow(msg.node);
  if (msg.node === selectedNode) {
    if (nextNode.is_etcd && !prevIsEtcd) ensureSelectedEtcdHealthCheck();
    renderInfraDetail();
  } else if (nextNode.is_etcd && selectedNode && nodes[selectedNode]?.is_etcd && activeTab === 'summary') {
    // A peer etcd node's health changed — re-render so the etcd quorum block updates
    renderSummaryTab(nodes[selectedNode]);
  }
  renderTasksPanel();
}

function onLog(msg) {
  if (nodeListRefreshing && msg.node === '-') {
    if (
      msg.message?.startsWith('Node list refreshed') ||
      msg.message?.startsWith('Error loading K8s nodes:') ||
      msg.message?.startsWith('OpenStack summary failed:')
    ) {
      setNodeListRefreshing(false);
    }
  }
  const ts = new Date().toLocaleTimeString('en-GB', { hour12: false });
  const iconMap  = { success:'✅', error:'❌', warn:'⚠️', magenta:'🔄', important:'ℹ️', dim:'ℹ️', cyan:'ℹ️' };
  const tagMap   = {
    success:   `<span class="event-tag et-ok">ok</span>`,
    error:     `<span class="event-tag et-error">error</span>`,
    warn:      `<span class="event-tag et-warn">warning</span>`,
    magenta:   `<span class="event-tag et-task">reboot</span>`,
    important: `<span class="event-tag et-task">task</span>`,
  };
  const ico      = iconMap[msg.color] || 'ℹ️';
  const tag      = tagMap[msg.color]  || '';
  const nodeTag  = (msg.node && msg.node !== '-') ? `<strong>${esc(msg.node)}</strong> — ` : '';
  const itemHtml = `<div class="event-item"><div class="event-ico">${ico}</div>
    <div class="event-body">
      <div class="event-ts">${ts}</div>
      <div class="event-msg">${nodeTag}${esc(msg.message)}${tag}</div>
    </div></div>`;
  monitorEntriesHtml += itemHtml;

  const ml = document.getElementById('monitor-log');
  if (ml) {
    ml.insertAdjacentHTML('beforeend', itemHtml);
    while (ml.children.length > 300) ml.removeChild(ml.firstChild);
    monitorEntriesHtml = ml.innerHTML;
  }
  if (activeView === 'monitor') ml.scrollTop = ml.scrollHeight;
}

function onRebootConfirmNeeded(msg) {
  pendingReboot = msg.node;
  document.getElementById('modal-node-name').textContent = msg.node;
  document.getElementById('modal-input').value = '';
  document.getElementById('modal-input').classList.remove('shake');
  document.getElementById('modal-overlay').classList.add('open');
  setTimeout(() => document.getElementById('modal-input').focus(), 50);
}

function onRebootBlocked(msg) {
  onLog({ node: msg.node, message: `Reboot blocked — ${msg.detail}`, color: 'error' });
  document.getElementById('blocked-detail').textContent = msg.detail;
  document.getElementById('blocked-overlay').classList.add('open');
}

function onPods(msg) {
  if (selectedNode !== msg.node) return;
  lastPodsCache = { node: msg.node, pods: msg.pods || [] };
  if (activeTab === 'pods') {
    const sec = document.getElementById('pods-section');
    if (sec) sec.innerHTML = buildPodsTableHtml(lastPodsCache.pods);
  }
}

function onInstanceMigrateStatus(msg) {
  // Update button/row state (complete means the instance has moved — remove from states)
  if (msg.status === 'complete') {
    delete instanceMigrateStates[msg.instance_id];
  } else {
    instanceMigrateStates[msg.instance_id] = msg.status;
  }
  // Update the task record for the Recent Tasks panel
  if (instanceMigrateTasks[msg.instance_id])
    instanceMigrateTasks[msg.instance_id].status = msg.status;
  renderTasksPanel();
  if (activeTab === 'instances' && selectedNode === msg.node && nodes[selectedNode])
    renderInstancesTab(nodes[selectedNode]);
}
