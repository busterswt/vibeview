'use strict';

let projectSearchTimer = null;

function projectViewLabel(name = activeProjectView) {
  return ({
    instances: 'Instances',
    networks: 'Networks',
    volumes: 'Volumes',
    securitygroups: 'Security Groups',
    floatingips: 'Floating IPs',
    loadbalancers: 'Load Balancers',
    quota: 'Quota + Capacity',
  })[name] || 'Projects';
}

function switchProjectSection(name) {
  const valid = ['instances', 'networks', 'volumes', 'securitygroups', 'floatingips', 'loadbalancers', 'quota'];
  if (!valid.includes(name)) return;
  activeProjectView = name;
  projectInventoryState.filter = '';
  projectDetailState.kind = '';
  projectDetailState.item = null;
  projectDetailState.loading = false;
  projectDetailState.data = null;
  projectDetailState.error = null;
  if (activeView === 'projects') {
    renderProjectsWorkspace();
    if (selectedProjectId) loadProjectInventory(selectedProjectId, name);
  }
}

function projectFilterRows(items, fields, filter) {
  const q = String(filter || '').trim().toLowerCase();
  if (!q) return items;
  return (items || []).filter((item) => fields.some((field) => String(item?.[field] || '').toLowerCase().includes(q)));
}

function projectSelectedRecord() {
  return (projectsState.data || []).find((item) => item.project_id === selectedProjectId) || null;
}

function projectInventory() {
  if (!selectedProjectId) return null;
  const cache = projectInventoryState.sections[selectedProjectId] || {};
  const section = activeProjectView === 'quota' ? 'quota' : activeProjectView;
  return cache[section] || null;
}

function projectItemsForView(view = activeProjectView) {
  const inv = projectInventory();
  if (!inv) return [];
  if (view === 'instances') return inv.instances || [];
  if (view === 'networks') return inv.networks || [];
  if (view === 'volumes') return inv.volumes || [];
  if (view === 'securitygroups') return inv.security_groups || [];
  if (view === 'floatingips') return inv.floating_ips || [];
  if (view === 'loadbalancers') return inv.load_balancers || [];
  return [];
}

function findProjectItem(kind, id) {
  return projectItemsForView(kind).find((item) => item.id === id) || null;
}

function projectSummaryValue(summary, key) {
  return Number(summary?.[key] || 0);
}

function projectSearchChanged(value) {
  projectsState.filter = value;
  if (projectSearchTimer) clearTimeout(projectSearchTimer);
  projectSearchTimer = setTimeout(() => {
    loadProjects(true);
  }, 300);
}

async function loadProjects(force = false) {
  if (projectsState.loading) return;
  if (projectsState.data && !force) {
    renderProjectsWorkspace();
    return;
  }
  projectsState.loading = true;
  renderProjectsWorkspace();
  try {
    const search = String(projectsState.filter || '').trim();
    const url = search ? `/api/projects?search=${encodeURIComponent(search)}` : '/api/projects';
    const resp = await fetch(url);
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('OpenStack');
    projectsState.data = json.projects || [];
    if ((!selectedProjectId || !projectsState.data.some((item) => item.project_id === selectedProjectId)) && projectsState.data.length) {
      selectedProjectId = projectsState.data[0].project_id;
    }
    if (!projectsState.data.length && !search) selectedProjectId = '';
  } catch (err) {
    projectsState.data = [];
    onLog?.({ node: '-', message: `Projects API error: ${err}`, color: 'error' });
  } finally {
    projectsState.loading = false;
    renderProjectsWorkspace();
    if (selectedProjectId) loadProjectInventory(selectedProjectId, activeProjectView);
  }
}

async function loadProjectInventory(projectId, section = activeProjectView, force = false) {
  if (!projectId) return;
  const normalizedSection = section === 'quota' ? 'quota' : section;
  const cache = projectInventoryState.sections[projectId] || {};
  if (projectInventoryState.loading && projectInventoryState.projectId === projectId && projectInventoryState.activeSection === normalizedSection) return;
  if (cache[normalizedSection] && !force) {
    renderProjectsWorkspace();
    return;
  }
  projectInventoryState.loading = true;
  projectInventoryState.projectId = projectId;
  projectInventoryState.activeSection = normalizedSection;
  renderProjectsWorkspace();
  try {
    const resp = await fetch(`/api/projects/${encodeURIComponent(projectId)}/inventory?section=${encodeURIComponent(normalizedSection)}`);
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('OpenStack');
    if (!resp.ok || json.error) throw new Error(json.error || `HTTP ${resp.status}`);
    const nextCache = projectInventoryState.sections[projectId] || {};
    projectInventoryState.sections[projectId] = {
      ...nextCache,
      [normalizedSection]: json.inventory || null,
    };
  } catch (err) {
    const nextCache = projectInventoryState.sections[projectId] || {};
    projectInventoryState.sections[projectId] = {
      ...nextCache,
      [normalizedSection]: null,
    };
    onLog?.({ node: '-', message: `Project inventory API error: ${err}`, color: 'error' });
  } finally {
    projectInventoryState.loading = false;
    renderProjectsWorkspace();
  }
}

function refreshActiveProjectView() {
  if (projectsState.loading || projectInventoryState.loading) return;
  if (!projectsState.data) return loadProjects(true);
  if (selectedProjectId) return loadProjectInventory(selectedProjectId, activeProjectView, true);
  return loadProjects(true);
}

function selectProject(projectId) {
  if (!projectId || selectedProjectId === projectId) {
    renderProjectsWorkspace();
    return;
  }
  selectedProjectId = projectId;
  projectInventoryState.projectId = projectId;
  projectDetailState.kind = '';
  projectDetailState.item = null;
  projectDetailState.loading = false;
  projectDetailState.data = null;
  projectDetailState.error = null;
  renderProjectsWorkspace();
  loadProjectInventory(projectId, activeProjectView, true);
}

function projectCounts(inv) {
  const summary = inv?.summary || projectSelectedRecord() || {};
  return {
    instances: projectSummaryValue(summary, 'instance_count'),
    networks: projectSummaryValue(summary, 'network_count'),
    volumes: projectSummaryValue(summary, 'volume_count'),
    securitygroups: projectSummaryValue(summary, 'security_group_count'),
    floatingips: projectSummaryValue(summary, 'floating_ip_count'),
    loadbalancers: projectSummaryValue(summary, 'load_balancer_count'),
  };
}

function renderProjectsSidebar() {
  const wrap = document.getElementById('projects-sidebar');
  if (!wrap) return;
  const projects = projectFilterRows(projectsState.data || [], ['project_name', 'project_id', 'description'], projectsState.filter);
  const inv = projectInventory();
  const counts = projectCounts(inv);
  wrap.innerHTML = `
    <div class="sidebar-head">Navigator — Projects</div>
    <div class="project-sidebar-filter">
      <input class="dv-filter" type="text" placeholder="Filter projects…" value="${esc(projectsState.filter || '')}" oninput="projectSearchChanged(this.value)">
    </div>
    <div class="networking-section-label">Project Views</div>
    <div class="project-nav-item ${activeProjectView === 'instances' ? 'selected' : ''}" onclick="switchProjectSection('instances')"><span>🖥️</span> Instances <span class="tree-badge">${counts.instances || ''}</span></div>
    <div class="project-nav-item ${activeProjectView === 'networks' ? 'selected' : ''}" onclick="switchProjectSection('networks')"><span>🌐</span> Networks <span class="tree-badge">${counts.networks || ''}</span></div>
    <div class="project-nav-item ${activeProjectView === 'volumes' ? 'selected' : ''}" onclick="switchProjectSection('volumes')"><span>💿</span> Volumes <span class="tree-badge">${counts.volumes || ''}</span></div>
    <div class="project-nav-item ${activeProjectView === 'securitygroups' ? 'selected' : ''}" onclick="switchProjectSection('securitygroups')"><span>🛡️</span> Security Groups <span class="tree-badge">${counts.securitygroups || ''}</span></div>
    <div class="project-nav-item ${activeProjectView === 'floatingips' ? 'selected' : ''}" onclick="switchProjectSection('floatingips')"><span>📍</span> Floating IPs <span class="tree-badge">${counts.floatingips || ''}</span></div>
    <div class="project-nav-item ${activeProjectView === 'loadbalancers' ? 'selected' : ''}" onclick="switchProjectSection('loadbalancers')"><span>⚖️</span> Load Balancers <span class="tree-badge">${counts.loadbalancers || ''}</span></div>
    <div class="project-nav-item ${activeProjectView === 'quota' ? 'selected' : ''}" onclick="switchProjectSection('quota')"><span>📊</span> Quota + Capacity</div>
    <div class="networking-section-label">Projects</div>
    <div class="project-sidebar-list">
      ${(projectsState.loading && !projectsState.data) ? '<div class="project-sidebar-empty"><span class="spinner">⟳</span> Loading projects…</div>' : ''}
      ${projects.map((item) => `
        <div class="project-record ${selectedProjectId === item.project_id ? 'selected' : ''}" onclick="selectProject('${escAttr(item.project_id)}')">
          <div class="project-record-name">${esc(item.project_name || item.project_id)}</div>
          <div class="project-record-meta">${esc(item.project_id || '')}</div>
          <div class="project-record-pills">
            <span class="tree-badge">${esc(String(item.instance_count || 0))} vm</span>
            <span class="tree-badge">${esc(String(item.network_count || 0))} net</span>
            <span class="tree-badge">${esc(String(item.volume_count || 0))} vol</span>
          </div>
        </div>
      `).join('') || (!projectsState.loading ? '<div class="project-sidebar-empty">No projects match the current filter.</div>' : '')}
    </div>
  `;
}

function renderProjectToolbar(title, count, placeholder) {
  return `
    <div class="data-view-toolbar">
      <h2>${esc(title)} <span class="hint">Project Scoped</span></h2>
      <input class="dv-filter" type="text" placeholder="${esc(placeholder)}" value="${esc(projectInventoryState.filter || '')}" oninput="projectInventoryState.filter=this.value;renderProjectsWorkspace()">
      <span style="font-size:11px;color:var(--dim)">${esc(String(count || 0))} item(s)</span>
    </div>
  `;
}

function cloneProjectInstance(instanceId) {
  onLog?.({ node: '-', message: `Clone workflow is not implemented yet for instance ${instanceId}.`, color: 'warn' });
}

async function selectProjectDetail(kind, item) {
  projectDetailState.kind = kind;
  projectDetailState.item = item || null;
  projectDetailState.loading = false;
  projectDetailState.data = null;
  projectDetailState.error = null;
  renderProjectsWorkspace();

  if (!item) return;
  const id = item.id || item.project_id || '';
  const detailUrl = ({
    instances: id ? `/api/instances/${encodeURIComponent(id)}` : '',
    volumes: id ? `/api/volumes/${encodeURIComponent(id)}` : '',
    loadbalancers: id ? `/api/load-balancers/${encodeURIComponent(id)}` : '',
    securitygroups: id ? `/api/security-groups/${encodeURIComponent(id)}` : '',
    networks: id ? `/api/networks/${encodeURIComponent(id)}` : '',
  })[kind] || '';
  if (!detailUrl) return;

  projectDetailState.loading = true;
  renderProjectsWorkspace();
  try {
    const resp = await fetch(detailUrl);
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('OpenStack');
    if (!resp.ok || json.error) throw new Error(json.error || `HTTP ${resp.status}`);
    projectDetailState.data = json.instance || json.volume || json.load_balancer || json.security_group || json.network || null;
    if (kind === 'instances' && projectDetailState.data?.compute_host) {
      const nodeName = findNodeNameForHypervisor(projectDetailState.data.compute_host);
      if (nodeName && projectDetailState.data.id) loadNodeInstancePortStats(nodeName, projectDetailState.data.id, true);
    }
  } catch (err) {
    projectDetailState.error = String(err);
  } finally {
    projectDetailState.loading = false;
    renderProjectsWorkspace();
  }
}

function selectProjectDetailById(kind, id) {
  const item = findProjectItem(kind, id);
  if (!item) return;
  selectProjectDetail(kind, item);
}

function toggleProjectPortDetail(instanceId, portId) {
  if (projectDetailState.kind !== 'instances' || projectDetailState.item?.id !== instanceId) return;
  const current = projectDetailState.item?.expandedPortId || '';
  projectDetailState.item = {
    ...projectDetailState.item,
    expandedPortId: current === portId ? '' : portId,
  };
  renderProjectsWorkspace();
}

function renderProjectInstances(inv) {
  const items = projectFilterRows(inv.instances || [], ['name', 'status', 'compute_host', 'project_name'], projectInventoryState.filter);
  return `
    ${renderProjectToolbar('Instances', items.length, 'Filter instances…')}
    <table class="data-table">
      <thead><tr><th>Name</th><th>Status</th><th>Flavor</th><th>Host</th><th>Networks</th><th>Volumes</th><th>Action</th></tr></thead>
      <tbody>
        ${items.map((item) => `
          <tr class="${projectDetailState.kind === 'instances' && projectDetailState.item?.id === item.id ? 'selected' : ''}" style="cursor:pointer" onclick="selectProjectDetailById('instances','${escAttr(item.id)}')">
            <td><div style="font-weight:600">${esc(item.name || item.id)}</div><div class="uuid-short" title="${esc(item.id)}">${esc((item.id || '').slice(0, 12))}</div></td>
            <td>${esc(item.status || 'UNKNOWN')}</td>
            <td>${esc(item.flavor?.name || item.flavor?.id || '—')}</td>
            <td>${esc(item.compute_host || '—')}</td>
            <td>${esc((item.networks || []).map((net) => net.name).join(', ') || '—')}</td>
            <td>${item.is_volume_backed ? 'Boot from volume' : 'Image / ephemeral'}</td>
            <td><button class="btn" style="font-size:11px" onclick="event.stopPropagation();cloneProjectInstance('${escAttr(item.id)}')">Clone</button></td>
          </tr>
        `).join('') || '<tr><td colspan="7" style="text-align:center;color:var(--dim);padding:20px">No instances match the current filter.</td></tr>'}
      </tbody>
    </table>
  `;
}

function renderProjectNetworks(inv) {
  const items = projectFilterRows(inv.networks || [], ['name', 'status', 'network_type'], projectInventoryState.filter);
  return `
    ${renderProjectToolbar('Networks', items.length, 'Filter networks…')}
    <table class="data-table">
      <thead><tr><th>Name</th><th>Status</th><th>Type</th><th>Subnets</th><th>Router</th></tr></thead>
      <tbody>
        ${items.map((item) => `
          <tr class="${projectDetailState.kind === 'networks' && projectDetailState.item?.id === item.id ? 'selected' : ''}" style="cursor:pointer" onclick="selectProjectDetailById('networks','${escAttr(item.id)}')">
            <td>${esc(item.name || item.id)}</td>
            <td>${esc(item.status || 'UNKNOWN')}</td>
            <td>${esc(item.network_type || '—')}</td>
            <td>${esc(String(item.subnet_count ?? 0))}</td>
            <td>${item.router_id ? renderObjectLink(item.router_id, `navigateToRouterDetail('${escAttr(item.router_id)}')`) : '<span style="color:var(--dim)">—</span>'}</td>
          </tr>
        `).join('') || '<tr><td colspan="5" style="text-align:center;color:var(--dim);padding:20px">No networks match the current filter.</td></tr>'}
      </tbody>
    </table>
  `;
}

function renderProjectVolumes(inv) {
  const items = projectFilterRows(inv.volumes || [], ['name', 'status', 'volume_type', 'backend_name'], projectInventoryState.filter);
  return `
    ${renderProjectToolbar('Volumes', items.length, 'Filter volumes…')}
    <table class="data-table">
      <thead><tr><th>Name</th><th>Status</th><th>Size</th><th>Type</th><th>Backend</th><th>Attached</th></tr></thead>
      <tbody>
        ${items.map((item) => `
          <tr class="${projectDetailState.kind === 'volumes' && projectDetailState.item?.id === item.id ? 'selected' : ''}" style="cursor:pointer" onclick="selectProjectDetailById('volumes','${escAttr(item.id)}')">
            <td>${esc(item.name || item.id)}</td>
            <td>${esc(item.status || 'UNKNOWN')}</td>
            <td>${esc(String(item.size_gb || 0))} GB</td>
            <td>${esc(item.volume_type || '—')}</td>
            <td>${esc(item.backend_name || '—')}</td>
            <td>${esc(String((item.attached_to || []).length || 0))}</td>
          </tr>
        `).join('') || '<tr><td colspan="6" style="text-align:center;color:var(--dim);padding:20px">No volumes match the current filter.</td></tr>'}
      </tbody>
    </table>
  `;
}

function renderProjectSecurityGroups(inv) {
  const items = projectFilterRows(inv.security_groups || [], ['name', 'project_name'], projectInventoryState.filter);
  return `
    ${renderProjectToolbar('Security Groups', items.length, 'Filter security groups…')}
    <table class="data-table">
      <thead><tr><th>Name</th><th>Severity</th><th>Rules</th><th>Attached Ports</th><th>Instances</th></tr></thead>
      <tbody>
        ${items.map((item) => `
          <tr class="${projectDetailState.kind === 'securitygroups' && projectDetailState.item?.id === item.id ? 'selected' : ''}" style="cursor:pointer" onclick="selectProjectDetailById('securitygroups','${escAttr(item.id)}')">
            <td>${esc(item.name || item.id)}</td>
            <td>${esc(item.audit?.severity || 'unknown')}</td>
            <td>${esc(String(item.rule_count || 0))}</td>
            <td>${esc(String(item.attachment_port_count || 0))}</td>
            <td>${esc(String(item.attachment_instance_count || 0))}</td>
          </tr>
        `).join('') || '<tr><td colspan="5" style="text-align:center;color:var(--dim);padding:20px">No security groups match the current filter.</td></tr>'}
      </tbody>
    </table>
  `;
}

function renderProjectFloatingIps(inv) {
  const items = projectFilterRows(inv.floating_ips || [], ['floating_ip_address', 'fixed_ip_address', 'status', 'description'], projectInventoryState.filter);
  return `
    ${renderProjectToolbar('Floating IPs', items.length, 'Filter floating IPs…')}
    <table class="data-table">
      <thead><tr><th>Floating IP</th><th>Fixed IP</th><th>Status</th><th>Network</th><th>Port</th></tr></thead>
      <tbody>
        ${items.map((item) => `
          <tr class="${projectDetailState.kind === 'floatingips' && projectDetailState.item?.id === item.id ? 'selected' : ''}" style="cursor:pointer" onclick="selectProjectDetailById('floatingips','${escAttr(item.id)}')">
            <td>${esc(item.floating_ip_address || '—')}</td>
            <td>${esc(item.fixed_ip_address || '—')}</td>
            <td>${esc(item.status || 'UNKNOWN')}</td>
            <td>${esc(item.floating_network_name || item.floating_network_id || '—')}</td>
            <td><span class="uuid-short" title="${esc(item.port_id || '')}">${esc((item.port_id || '').slice(0, 12) || '—')}</span></td>
          </tr>
        `).join('') || '<tr><td colspan="5" style="text-align:center;color:var(--dim);padding:20px">No floating IPs match the current filter.</td></tr>'}
      </tbody>
    </table>
  `;
}

function renderProjectLoadBalancers(inv) {
  const items = projectFilterRows(inv.load_balancers || [], ['name', 'vip_address', 'floating_ip', 'operating_status'], projectInventoryState.filter);
  return `
    ${renderProjectToolbar('Load Balancers', items.length, 'Filter load balancers…')}
    <table class="data-table">
      <thead><tr><th>Name</th><th>VIP</th><th>Floating IP</th><th>Status</th><th>Pools</th></tr></thead>
      <tbody>
        ${items.map((item) => `
          <tr class="${projectDetailState.kind === 'loadbalancers' && projectDetailState.item?.id === item.id ? 'selected' : ''}" style="cursor:pointer" onclick="selectProjectDetailById('loadbalancers','${escAttr(item.id)}')">
            <td>${esc(item.name || item.id)}</td>
            <td>${esc(item.vip_address || '—')}</td>
            <td>${esc(item.floating_ip || '—')}</td>
            <td>${esc(item.operating_status || 'UNKNOWN')} / ${esc(item.provisioning_status || 'UNKNOWN')}</td>
            <td>${esc(String(item.pool_count || 0))}</td>
          </tr>
        `).join('') || '<tr><td colspan="5" style="text-align:center;color:var(--dim);padding:20px">No load balancers match the current filter.</td></tr>'}
      </tbody>
    </table>
  `;
}

function renderQuotaSection(title, entries) {
  const rows = Object.entries(entries || {});
  return `
    <div class="card" style="margin-bottom:10px">
      <div class="card-title">${esc(title)}</div>
      <div class="card-body" style="padding:0">
        <table class="data-table" style="border:none;margin-bottom:0">
          <thead><tr><th>Resource</th><th>Used</th><th>Limit</th></tr></thead>
          <tbody>
            ${rows.map(([key, value]) => `<tr><td>${esc(key)}</td><td>${esc(String(value.used ?? '—'))}</td><td>${esc(String(value.limit ?? '—'))}</td></tr>`).join('') || '<tr><td colspan="3" style="text-align:center;color:var(--dim);padding:16px">No quota data available.</td></tr>'}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function renderProjectQuota(inv) {
  const summary = { ...(projectSelectedRecord() || {}), ...(inv.summary || {}) };
  const quotas = inv.quotas || {};
  const placement = inv.placement || {};
  const hostPct = Number(placement.top_host_pct || summary.top_host_pct || 0);
  return `
    <div class="summary-grid" style="margin-bottom:10px">
      <div class="card"><div class="card-title">Instance Footprint</div><div class="card-body">
        <div class="mrow"><span class="ml">Instances</span><span class="mv">${esc(String(summary.instance_count || 0))}</span></div>
        <div class="mrow"><span class="ml">vCPU</span><span class="mv">${esc(String(summary.vcpu_count || 0))}</span></div>
        <div class="mrow"><span class="ml">RAM</span><span class="mv">${esc(String(Math.round((summary.ram_mb || 0) / 1024)))} GB</span></div>
      </div></div>
      <div class="card"><div class="card-title">Placement Spread</div><div class="card-body">
        <div class="mrow"><span class="ml">Hosts</span><span class="mv">${esc(String(placement.host_count || summary.host_count || 0))}</span></div>
        <div class="mrow"><span class="ml">Top Host</span><span class="mv">${esc(placement.top_host || summary.top_host || '—')}</span></div>
        <div class="mrow"><span class="ml">Concentration</span><span class="mv">${hostPct ? esc(`${hostPct.toFixed(0)}%`) : '—'}</span></div>
      </div></div>
      <div class="card"><div class="card-title">Network Usage</div><div class="card-body">
        <div class="mrow"><span class="ml">Networks</span><span class="mv">${esc(String(summary.network_count || 0))}</span></div>
        <div class="mrow"><span class="ml">Floating IPs</span><span class="mv">${esc(String(summary.floating_ip_count || 0))}</span></div>
        <div class="mrow"><span class="ml">Load Balancers</span><span class="mv">${esc(String(summary.load_balancer_count || 0))}</span></div>
      </div></div>
    </div>
    ${renderQuotaSection('Compute Quotas', quotas.compute || {})}
    ${renderQuotaSection('Network Quotas', quotas.network || {})}
    ${renderQuotaSection('Block Storage Quotas', quotas.block_storage || {})}
  `;
}

function renderProjectMainContent() {
  const wrap = document.getElementById('projects-content-inner');
  if (!wrap) return;
  if (!hasOpenStackAuth()) {
    wrap.innerHTML = renderOpenStackUnavailablePanel('Projects', 'This view relies on OpenStack project-scoped inventory. Provide OpenStack credentials to enable it.');
    return;
  }
  const selected = projectSelectedRecord();
  const inv = projectInventory();
  const activeSection = activeProjectView === 'quota' ? 'quota' : activeProjectView;
  if (projectsState.loading && !projectsState.data) {
    wrap.innerHTML = `<div style="color:var(--dim);padding:20px"><span class="spinner">⟳</span> Loading projects…</div>`;
    return;
  }
  if (!selected) {
    wrap.innerHTML = `<div style="color:var(--dim);padding:20px">Select a project from the navigator.</div>`;
    return;
  }
  if (projectInventoryState.loading && projectInventoryState.projectId === selected.project_id && projectInventoryState.activeSection === activeSection && !inv) {
    wrap.innerHTML = `<div style="color:var(--dim);padding:20px"><span class="spinner">⟳</span> Loading project inventory…</div>`;
    return;
  }
  if (!inv) {
    wrap.innerHTML = `<div style="color:var(--dim);padding:20px">Project inventory is unavailable.${projectInventoryState.projectId === selected.project_id ? ' Try refreshing once.' : ''}</div>`;
    return;
  }
  const summary = inv.summary || selected;
  const body = activeProjectView === 'instances'
    ? renderProjectInstances(inv)
    : activeProjectView === 'networks'
      ? renderProjectNetworks(inv)
      : activeProjectView === 'volumes'
        ? renderProjectVolumes(inv)
        : activeProjectView === 'securitygroups'
          ? renderProjectSecurityGroups(inv)
          : activeProjectView === 'floatingips'
            ? renderProjectFloatingIps(inv)
            : activeProjectView === 'loadbalancers'
              ? renderProjectLoadBalancers(inv)
              : renderProjectQuota(inv);

  wrap.innerHTML = `
    <div id="obj-header">
      <div class="obj-icon">▣</div>
      <div class="obj-meta">
        <h2>${esc(summary.project_name || summary.project_id)}</h2>
        <div class="obj-sub">${esc(summary.project_id || '')}</div>
        <div class="obj-badges">
          <span class="tree-badge">${esc(String(summary.instance_count || 0))} vm</span>
          <span class="tree-badge">${esc(String(summary.network_count || 0))} net</span>
          <span class="tree-badge">${esc(String(summary.volume_count || 0))} vol</span>
          <span class="tree-badge">${esc(String(summary.security_group_count || 0))} sg</span>
          <span class="tree-badge">${esc(String(summary.floating_ip_count || 0))} fip</span>
          <span class="tree-badge">${esc(String(summary.load_balancer_count || 0))} lb</span>
        </div>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        ${activeProjectView === 'instances' ? `<button class="btn primary" onclick="${projectDetailState.kind === 'instances' && projectDetailState.item?.id ? `cloneProjectInstance('${escAttr(projectDetailState.item.id)}')` : `onLog?.({ node: '-', message: 'Select an instance first to start clone.', color: 'warn' })`}">Clone VM</button>` : ''}
      </div>
    </div>
    <div id="tabs-bar">
      <div class="tab ${activeProjectView === 'instances' ? 'active' : ''}" onclick="switchProjectSection('instances')">Instances</div>
      <div class="tab ${activeProjectView === 'networks' ? 'active' : ''}" onclick="switchProjectSection('networks')">Networks</div>
      <div class="tab ${activeProjectView === 'volumes' ? 'active' : ''}" onclick="switchProjectSection('volumes')">Volumes</div>
      <div class="tab ${activeProjectView === 'securitygroups' ? 'active' : ''}" onclick="switchProjectSection('securitygroups')">Security Groups</div>
      <div class="tab ${activeProjectView === 'floatingips' ? 'active' : ''}" onclick="switchProjectSection('floatingips')">Floating IPs</div>
      <div class="tab ${activeProjectView === 'loadbalancers' ? 'active' : ''}" onclick="switchProjectSection('loadbalancers')">Load Balancers</div>
      <div class="tab ${activeProjectView === 'quota' ? 'active' : ''}" onclick="switchProjectSection('quota')">Quota + Capacity</div>
    </div>
    <div class="project-body-pane">${body}</div>
  `;
}

function renderProjectDetailDrawer() {
  const wrap = document.getElementById('projects-detail-wrap');
  const resizer = document.getElementById('projects-detail-resizer');
  if (!wrap) return;
  if (!projectDetailState.kind || !projectDetailState.item) {
    wrap.classList.remove('open');
    resizer?.classList.remove('open');
    wrap.innerHTML = '';
    return;
  }
  wrap.classList.add('open');
  resizer?.classList.add('open');
  const item = projectDetailState.item;
  if (projectDetailState.loading) {
    wrap.innerHTML = `<div class="net-detail-inner"><div class="net-detail-head"><strong>Detail</strong><button class="btn" style="padding:1px 7px;font-size:11px" onclick="projectDetailState.kind='';projectDetailState.item=null;renderProjectsWorkspace()">✕</button></div><div style="padding:12px;color:var(--dim)"><span class="spinner">⟳</span> Loading detail…</div></div>`;
    return;
  }
  if (projectDetailState.error) {
    wrap.innerHTML = `<div class="net-detail-inner"><div class="net-detail-head"><strong>Detail</strong><button class="btn" style="padding:1px 7px;font-size:11px" onclick="projectDetailState.kind='';projectDetailState.item=null;renderProjectsWorkspace()">✕</button></div><div class="err-block" style="margin:12px">${esc(projectDetailState.error)}</div></div>`;
    return;
  }
  const detail = projectDetailState.data || item;
  let body = '';
  if (projectDetailState.kind === 'instances' && detail) {
    const nodeName = findNodeNameForHypervisor(detail.compute_host);
    body = renderInstanceDetailContent(detail, {
      nodeName,
      instanceId: detail.id,
      expandedPortId: item.expandedPortId || '',
      portToggleJs: (portId) => `toggleProjectPortDetail('${escAttr(detail.id)}','${escAttr(portId)}')`,
    });
  } else if (projectDetailState.kind === 'volumes') {
    body = `<div class="card"><div class="card-title">Volume</div><div class="card-body">
      <div class="mrow"><span class="ml">Name</span><span class="mv">${esc(detail.name || detail.id || '—')}</span></div>
      <div class="mrow"><span class="ml">Status</span><span class="mv">${esc(detail.status || 'UNKNOWN')}</span></div>
      <div class="mrow"><span class="ml">Size</span><span class="mv">${esc(String(detail.size_gb || 0))} GB</span></div>
      <div class="mrow"><span class="ml">Type</span><span class="mv">${esc(detail.volume_type || detail.volume_type_detail?.name || '—')}</span></div>
      <div class="mrow"><span class="ml">QoS</span><span class="mv">${esc(detail.qos_policy?.name || detail.volume_type_detail?.qos_specs?.name || '—')}</span></div>
      <div class="mrow"><span class="ml">Backend</span><span class="mv">${esc(detail.backend_name || detail.volume_type_detail?.backend_name || '—')}</span></div>
    </div></div>`;
  } else if (projectDetailState.kind === 'networks') {
    body = `<div class="card"><div class="card-title">Network</div><div class="card-body">
      <div class="mrow"><span class="ml">Name</span><span class="mv">${esc(item.name || item.id || '—')}</span></div>
      <div class="mrow"><span class="ml">Status</span><span class="mv">${esc(item.status || 'UNKNOWN')}</span></div>
      <div class="mrow"><span class="ml">Type</span><span class="mv">${esc(item.network_type || '—')}</span></div>
      <div class="mrow"><span class="ml">Subnets</span><span class="mv">${esc(String((detail.subnets || []).length || item.subnet_count || 0))}</span></div>
    </div></div>`;
  } else if (projectDetailState.kind === 'securitygroups') {
    body = `<div class="card"><div class="card-title">Security Group</div><div class="card-body">
      <div class="mrow"><span class="ml">Name</span><span class="mv">${esc(detail.name || detail.id || '—')}</span></div>
      <div class="mrow"><span class="ml">Severity</span><span class="mv">${esc(detail.audit?.severity || 'unknown')}</span></div>
      <div class="mrow"><span class="ml">Rules</span><span class="mv">${esc(String(detail.rule_count || 0))}</span></div>
      <div class="mrow"><span class="ml">Instances</span><span class="mv">${esc(String(detail.attachment_instance_count || 0))}</span></div>
    </div></div>`;
  } else if (projectDetailState.kind === 'floatingips') {
    body = `<div class="card"><div class="card-title">Floating IP</div><div class="card-body">
      <div class="mrow"><span class="ml">Floating IP</span><span class="mv">${esc(detail.floating_ip_address || '—')}</span></div>
      <div class="mrow"><span class="ml">Fixed IP</span><span class="mv">${esc(detail.fixed_ip_address || '—')}</span></div>
      <div class="mrow"><span class="ml">Status</span><span class="mv">${esc(detail.status || 'UNKNOWN')}</span></div>
      <div class="mrow"><span class="ml">Network</span><span class="mv">${esc(detail.floating_network_name || detail.floating_network_id || '—')}</span></div>
      <div class="mrow"><span class="ml">Port</span><span class="mv mono">${esc(detail.port_id || '—')}</span></div>
    </div></div>`;
  } else if (projectDetailState.kind === 'loadbalancers') {
    body = `<div class="card"><div class="card-title">Load Balancer</div><div class="card-body">
      <div class="mrow"><span class="ml">Name</span><span class="mv">${esc(detail.name || detail.id || '—')}</span></div>
      <div class="mrow"><span class="ml">VIP</span><span class="mv">${esc(detail.vip_address || '—')}</span></div>
      <div class="mrow"><span class="ml">Floating IP</span><span class="mv">${esc(detail.floating_ip || '—')}</span></div>
      <div class="mrow"><span class="ml">Status</span><span class="mv">${esc(detail.operating_status || 'UNKNOWN')} / ${esc(detail.provisioning_status || 'UNKNOWN')}</span></div>
      <div class="mrow"><span class="ml">Pools</span><span class="mv">${esc(String((detail.pools || []).length || detail.pool_count || 0))}</span></div>
    </div></div>`;
  }
  wrap.innerHTML = `
    <div class="net-detail-inner">
      <div class="net-detail-head">
        <strong style="font-size:13px">${esc(projectViewLabel(projectDetailState.kind))} Detail</strong>
        <div style="display:flex;gap:6px;align-items:center">
          ${projectDetailState.kind === 'instances' ? `<button class="btn" style="padding:1px 7px;font-size:11px" onclick="cloneProjectInstance('${escAttr(detail.id || item.id || '')}')">Clone</button>` : ''}
          <button class="btn" style="padding:1px 7px;font-size:11px" onclick="projectDetailState.kind='';projectDetailState.item=null;projectDetailState.data=null;projectDetailState.error=null;renderProjectsWorkspace()">✕</button>
        </div>
      </div>
      ${body}
    </div>
  `;
}

function renderProjectsWorkspace() {
  renderProjectsSidebar();
  renderProjectMainContent();
  renderProjectDetailDrawer();
}
