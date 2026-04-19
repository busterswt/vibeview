'use strict';

let projectSearchTimer = null;

function projectViewLabel(name = activeProjectView) {
  return ({
    overview: 'Overview',
    instances: 'Instances',
    networking: 'Networking',
    storage: 'Storage',
    security: 'Security',
    quota: 'Quota + Capacity',
  })[name] || 'Projects';
}

function projectSectionKey(name = activeProjectView) {
  return ({
    overview: 'overview',
    instances: 'instances',
    networking: 'networking',
    storage: 'storage',
    security: 'security',
    quota: 'quota',
  })[name] || 'overview';
}

function projectNetworkingFamilyKey(name = activeProjectNetworkingView) {
  return ({
    networks: 'networks',
    routers: 'routers',
    ports: 'ports',
    floatingips: 'floating_ips',
    loadbalancers: 'load_balancers',
  })[name] || '';
}

function allProjectNetworkingFamilies() {
  return ['networks', 'routers', 'ports', 'floating_ips', 'load_balancers'];
}

function projectInventoryPendingKey(section = projectSectionKey(activeProjectView), family = '') {
  const key = projectSectionKey(section);
  const normalizedFamily = String(family || '').trim();
  return key === 'networking' && normalizedFamily ? `${key}:${normalizedFamily}` : key;
}

function projectRefreshTimestamp(projectId = selectedProjectId, section = activeProjectView, family = '') {
  const key = projectInventoryPendingKey(section, family);
  return projectInventoryState.refreshedAt?.[projectId]?.[key] || 0;
}

function formatProjectRefreshTimestamp(value) {
  const ts = Number(value || 0);
  if (!ts) return 'never';
  try {
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch (_) {
    return 'just now';
  }
}

function projectRefreshMeta(section = activeProjectView, family = '') {
  return `<span class="hint">Last refreshed ${esc(formatProjectRefreshTimestamp(projectRefreshTimestamp(selectedProjectId, section, family)))}</span>`;
}

function resetProjectQuotaEditor() {
  projectQuotaEditState.section = '';
  projectQuotaEditState.resource = '';
  projectQuotaEditState.value = '';
  projectQuotaEditState.saving = false;
  projectQuotaEditState.error = '';
}

function markProjectQuotaSaveSuccess(section, resource) {
  projectQuotaEditState.successKey = `${section}:${resource}`;
}

function switchProjectSection(name) {
  const valid = ['overview', 'instances', 'networking', 'storage', 'security', 'quota'];
  if (!valid.includes(name)) return;
  activeProjectView = name;
  projectInventoryState.filter = '';
  resetProjectQuotaEditor();
  projectDetailState.kind = '';
  projectDetailState.item = null;
  projectDetailState.loading = false;
  projectDetailState.data = null;
  projectDetailState.error = null;
  if (activeView === 'projects') {
    renderProjectsWorkspace();
    if (selectedProjectId) {
      if (name === 'networking') {
        ensureProjectNetworkingFamiliesLoaded(selectedProjectId);
      } else {
        loadProjectInventory(
          selectedProjectId,
          projectSectionKey(name),
          false,
          '',
        );
      }
    }
  }
}

function switchProjectNetworkingView(name) {
  const valid = ['networks', 'routers', 'ports', 'floatingips', 'loadbalancers'];
  if (!valid.includes(name)) return;
  activeProjectNetworkingView = name;
  projectInventoryState.filter = '';
  projectDetailState.kind = '';
  projectDetailState.item = null;
  projectDetailState.loading = false;
  projectDetailState.data = null;
  projectDetailState.error = null;
  renderProjectsWorkspace();
  if (activeView === 'projects' && selectedProjectId) {
    ensureProjectNetworkingFamiliesLoaded(selectedProjectId);
  }
}

function switchProjectStorageView(name) {
  const valid = ['volumes'];
  if (!valid.includes(name)) return;
  activeProjectStorageView = name;
  projectInventoryState.filter = '';
  projectDetailState.kind = '';
  projectDetailState.item = null;
  projectDetailState.loading = false;
  projectDetailState.data = null;
  projectDetailState.error = null;
  renderProjectsWorkspace();
}

function projectFilterRows(items, fields, filter) {
  const q = String(filter || '').trim().toLowerCase();
  if (!q) return items || [];
  return (items || []).filter((item) => fields.some((field) => String(item?.[field] || '').toLowerCase().includes(q)));
}

function projectSelectedRecord() {
  return (projectsState.data || []).find((item) => item.project_id === selectedProjectId) || null;
}

function projectInventory(section = activeProjectView) {
  if (!selectedProjectId) return null;
  const cache = projectInventoryState.sections[selectedProjectId] || {};
  return cache[projectSectionKey(section)] || null;
}

function projectInventoryForView(section = activeProjectView) {
  const inv = projectInventory(section);
  if (inv) return inv;
  if (section === 'quota') {
    const overview = projectInventory('overview');
    if (overview?.overview) {
      return {
        summary: overview.summary || {},
        quotas: overview.overview.quotas || {},
        placement: overview.overview.placement || null,
      };
    }
  }
  return null;
}

function projectSectionCache(projectId = selectedProjectId) {
  return projectInventoryState.sections[projectId] || {};
}

function isProjectSectionLoading(projectId, section) {
  const key = projectInventoryPendingKey(section);
  return Boolean(projectInventoryState.pending?.[projectId]?.[key]);
}

function isProjectNetworkingFamilyLoading(projectId, family = projectNetworkingFamilyKey()) {
  return Boolean(projectInventoryState.pending?.[projectId]?.[projectInventoryPendingKey('networking', family)]);
}

function ensureProjectNetworkingFamiliesLoaded(projectId, force = false) {
  if (!projectId) return;
  allProjectNetworkingFamilies().forEach((family) => {
    loadProjectInventory(projectId, 'networking', force, family);
  });
}

function projectHasBackgroundLoading(projectId, activeSection = activeProjectView) {
  const pending = projectInventoryState.pending?.[projectId] || {};
  const activeKey = projectSectionKey(activeSection);
  const activeNetworkingKey = activeKey === 'networking' ? projectInventoryPendingKey('networking', projectNetworkingFamilyKey()) : '';
  return Object.keys(pending).some((key) => key !== activeKey && key !== activeNetworkingKey && pending[key]);
}

function projectOverviewStats(projectId = selectedProjectId) {
  const cache = projectSectionCache(projectId);
  const overview = cache.overview?.overview || {};
  const instances = cache.instances?.instances || [];
  const networking = cache.networking || {};
  const storage = cache.storage || {};
  const security = cache.security || {};
  const quotas = cache.quota?.quotas || overview.quotas || {};
  const placement = cache.quota?.placement || overview.placement || null;
  const activeInstances = instances.filter((item) => item.status === 'ACTIVE');
  const errorInstances = instances.filter((item) => item.status === 'ERROR');
  const networkingLoaded = ['networks', 'routers', 'ports', 'floating_ips', 'load_balancers'].every((key) => Array.isArray(networking[key]));
  return {
    instancesLoaded: Boolean(cache.instances),
    networkingLoaded,
    storageLoaded: Boolean(cache.storage),
    securityLoaded: Boolean(cache.security),
    quotaLoaded: Boolean(cache.quota || cache.overview),
    instanceCount: instances.length,
    activeInstanceCount: activeInstances.length,
    errorInstanceCount: errorInstances.length,
    cloneCandidateCount: activeInstances.filter((item) => !String(item.name || '').startsWith('amphora-')).length,
    vcpuCount: instances.reduce((sum, item) => sum + Number(item.vcpus || 0), 0),
    ramMb: instances.reduce((sum, item) => sum + Number(item.ram_mb || 0), 0),
    networkCount: (networking.networks || []).length,
    routerCount: (networking.routers || []).length,
    portCount: (networking.ports || []).length,
    floatingIpCount: (networking.floating_ips || []).length,
    loadBalancerCount: (networking.load_balancers || []).length,
    volumeCount: (storage.volumes || []).length,
    securityGroupCount: (security.security_groups || []).length,
    quotas,
    placement,
  };
}

function projectSummary(summary, inv = null) {
  const stats = projectOverviewStats((inv?.summary?.project_id || summary?.project_id || selectedProjectId));
  return {
    ...(projectSelectedRecord() || {}),
    ...(inv?.summary || {}),
    ...(summary || {}),
    ...(stats.instancesLoaded ? {
      instance_count: stats.instanceCount,
      active_instance_count: stats.activeInstanceCount,
      error_instance_count: stats.errorInstanceCount,
      clone_candidate_count: stats.cloneCandidateCount,
      vcpu_count: stats.vcpuCount,
      ram_mb: stats.ramMb,
    } : {}),
    ...(stats.networkingLoaded ? {
      network_count: stats.networkCount,
      router_count: stats.routerCount,
      port_count: stats.portCount,
      floating_ip_count: stats.floatingIpCount,
      load_balancer_count: stats.loadBalancerCount,
    } : {}),
    ...(stats.storageLoaded ? {
      volume_count: stats.volumeCount,
    } : {}),
    ...(stats.securityLoaded ? {
      security_group_count: stats.securityGroupCount,
    } : {}),
    ...(stats.placement ? {
      host_count: stats.placement.host_count,
      top_host: stats.placement.top_host,
      top_host_pct: stats.placement.top_host_pct,
    } : {}),
  };
}

function projectValue(value, loaded, suffix = '') {
  if (!loaded) return '<span class="mv dim"><span class="spinner">⟳</span> Loading…</span>';
  return esc(`${value}${suffix}`);
}

function projectSummaryMetricReady(summary, key) {
  return summary?.[key] != null && summary[key] !== '';
}

function projectNetworkingSummary(summary, stats) {
  const networkQuota = stats?.quotas?.network || {};
  const fromSummary = (key) => projectSummaryMetricReady(summary, key) ? Number(summary[key] || 0) : null;
  const fromQuota = (key) => {
    const entry = networkQuota[key];
    if (!entry || entry.used == null) return null;
    return Number(entry.used || 0);
  };
  const metric = (summaryKey, quotaKey, statsLoaded, statsValue) => {
    const value = stats.networkingLoaded ? statsValue : (fromSummary(summaryKey) ?? fromQuota(quotaKey));
    return {
      ready: stats.networkingLoaded || fromSummary(summaryKey) != null || fromQuota(quotaKey) != null,
      value: value ?? 0,
    };
  };
    return {
    networks: metric('network_count', 'network', stats.networkingLoaded, stats.networkCount),
    routers: metric('router_count', 'router', stats.networkingLoaded, stats.routerCount),
    ports: metric('port_count', 'port', stats.networkingLoaded, stats.portCount),
    floatingIps: metric('floating_ip_count', 'floatingip', stats.networkingLoaded, stats.floatingIpCount),
    loadBalancers: metric('load_balancer_count', 'load_balancer', stats.networkingLoaded, stats.loadBalancerCount),
  };
}

function openProjectQuotaEditor(section, resource, currentLimit) {
  if (!authInfo?.is_admin) return;
  projectQuotaEditState.section = section;
  projectQuotaEditState.resource = resource;
  projectQuotaEditState.value = currentLimit == null ? '' : String(currentLimit);
  projectQuotaEditState.saving = false;
  projectQuotaEditState.error = '';
  renderProjectsWorkspace();
}

function updateProjectQuotaEditorValue(value) {
  projectQuotaEditState.value = value;
}

async function saveProjectQuotaEdit() {
  if (!authInfo?.is_admin || !selectedProjectId || projectQuotaEditState.saving) return;
  const section = projectQuotaEditState.section;
  const resource = projectQuotaEditState.resource;
  if (!section || !resource) return;
  projectQuotaEditState.saving = true;
  projectQuotaEditState.error = '';
  renderProjectsWorkspace();
  try {
    const resp = await fetch(`/api/projects/${encodeURIComponent(selectedProjectId)}/quota`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        section,
        resource,
        limit: projectQuotaEditState.value,
      }),
    });
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else if (section === 'compute') recordApiSuccess('Nova');
    else if (section === 'network') recordApiSuccess('Neutron');
    if (!resp.ok || json.error) throw new Error(json.error || `HTTP ${resp.status}`);
    projectInventoryState.sections[selectedProjectId] = {
      ...(projectInventoryState.sections[selectedProjectId] || {}),
      quota: json.inventory || null,
    };
    markProjectQuotaSaveSuccess(section, resource);
    resetProjectQuotaEditor();
    renderProjectsWorkspace();
    loadProjectInventory(selectedProjectId, 'overview', true);
  } catch (err) {
    projectQuotaEditState.error = String(err);
    projectQuotaEditState.saving = false;
    renderProjectsWorkspace();
  }
}

function prefetchProjectDetails(projectId) {
  if (!projectId) return;
  if (projectInventoryState.prefetched?.[projectId]) return;
  projectInventoryState.prefetched = { ...(projectInventoryState.prefetched || {}), [projectId]: true };
  ['instances', 'storage', 'quota'].forEach((section) => {
    if (!projectSectionCache(projectId)[section]) loadProjectInventory(projectId, section);
  });
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
    if (selectedProjectId) {
      if (activeProjectView === 'networking') ensureProjectNetworkingFamiliesLoaded(selectedProjectId);
      else loadProjectInventory(
        selectedProjectId,
        projectSectionKey(activeProjectView),
        false,
        '',
      );
      prefetchProjectDetails(selectedProjectId);
    }
  }
}

async function loadProjectInventory(projectId, section = projectSectionKey(activeProjectView), force = false, family = '') {
  if (!projectId) return;
  const key = projectSectionKey(section);
  const normalizedFamily = key === 'networking' ? String(family || '').trim() : '';
  const pendingKey = projectInventoryPendingKey(key, normalizedFamily);
  const cache = projectInventoryState.sections[projectId] || {};
  if (Boolean(projectInventoryState.pending?.[projectId]?.[pendingKey])) return;
  if (key === 'networking' && normalizedFamily) {
    if (cache[key]?.[normalizedFamily] && !force) {
      renderProjectsWorkspace();
      return;
    }
  } else if (cache[key] && !force) {
    renderProjectsWorkspace();
    return;
  }
  projectInventoryState.projectId = projectId;
  projectInventoryState.activeSection = pendingKey;
  projectInventoryState.pending = {
    ...(projectInventoryState.pending || {}),
    [projectId]: {
      ...(projectInventoryState.pending?.[projectId] || {}),
      [pendingKey]: true,
    },
  };
  projectInventoryState.loading = true;
  renderProjectsWorkspace();
  try {
    const params = new URLSearchParams({ section: key });
    if (normalizedFamily) params.set('family', normalizedFamily);
    const resp = await fetch(`/api/projects/${encodeURIComponent(projectId)}/inventory?${params.toString()}`);
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('OpenStack');
    if (!resp.ok || json.error) throw new Error(json.error || `HTTP ${resp.status}`);
    if (key === 'networking' && normalizedFamily) {
      const existingSections = projectInventoryState.sections[projectId] || {};
      const existingNetworking = existingSections.networking || {};
      projectInventoryState.sections[projectId] = {
        ...existingSections,
        networking: {
          ...(existingNetworking || {}),
          summary: json.inventory?.summary || existingNetworking.summary || {},
          [normalizedFamily]: json.inventory?.[normalizedFamily] || [],
        },
      };
    } else {
      projectInventoryState.sections[projectId] = {
        ...(projectInventoryState.sections[projectId] || {}),
        [key]: json.inventory || null,
      };
    }
    projectInventoryState.refreshedAt = {
      ...(projectInventoryState.refreshedAt || {}),
      [projectId]: {
        ...(projectInventoryState.refreshedAt?.[projectId] || {}),
        [pendingKey]: Date.now(),
      },
    };
    if (key === 'quota' && projectQuotaEditState.successKey) {
      projectQuotaEditState.successKey = '';
    }
  } catch (err) {
    if (key !== 'networking' || !normalizedFamily) {
      projectInventoryState.sections[projectId] = {
        ...(projectInventoryState.sections[projectId] || {}),
        [key]: null,
      };
    }
    onLog?.({ node: '-', message: `Project inventory API error: ${err}`, color: 'error' });
  } finally {
    if (projectInventoryState.pending?.[projectId]) {
      const nextPending = { ...(projectInventoryState.pending[projectId] || {}) };
      delete nextPending[pendingKey];
      projectInventoryState.pending = {
        ...(projectInventoryState.pending || {}),
        [projectId]: nextPending,
      };
    }
    projectInventoryState.loading = Object.values(projectInventoryState.pending || {}).some((items) => Object.values(items || {}).some(Boolean));
    renderProjectsWorkspace();
  }
}

function refreshActiveProjectView() {
  if (projectsState.loading) return;
  if (!projectsState.data) return loadProjects(true);
  if (selectedProjectId) {
    if (activeProjectView === 'networking') {
      ensureProjectNetworkingFamiliesLoaded(selectedProjectId, true);
      return;
    }
    return loadProjectInventory(
      selectedProjectId,
      projectSectionKey(activeProjectView),
      true,
      '',
    );
  }
  return loadProjects(true);
}

function selectProject(projectId) {
  if (!projectId || selectedProjectId === projectId) {
    renderProjectsWorkspace();
    return;
  }
  selectedProjectId = projectId;
  resetProjectQuotaEditor();
  projectInventoryState.projectId = projectId;
  projectDetailState.kind = '';
  projectDetailState.item = null;
  projectDetailState.loading = false;
  projectDetailState.data = null;
  projectDetailState.error = null;
  renderProjectsWorkspace();
  if (activeProjectView === 'networking') ensureProjectNetworkingFamiliesLoaded(projectId, true);
  else loadProjectInventory(
    projectId,
    projectSectionKey(activeProjectView),
    true,
    '',
  );
  prefetchProjectDetails(projectId);
}

function renderProjectsSidebar() {
  const wrap = document.getElementById('projects-sidebar');
  if (!wrap) return;
  const projects = projectsState.data || [];
  wrap.innerHTML = `
    <div class="sidebar-head">Navigator — Projects</div>
    <div class="project-sidebar-filter">
      <input class="dv-filter" type="text" placeholder="Filter projects…" value="${esc(projectsState.filter || '')}" oninput="projectSearchChanged(this.value)">
    </div>
    <div class="project-sidebar-list">
      ${(projectsState.loading && !projects.length) ? '<div class="project-sidebar-empty"><span class="spinner">⟳</span> Loading projects…</div>' : ''}
      ${projects.map((item) => `
        <div class="project-record ${selectedProjectId === item.project_id ? 'selected' : ''}" onclick="selectProject('${escAttr(item.project_id)}')">
          <div class="project-record-name">${esc(item.project_name || item.project_id)}</div>
          <div class="project-record-meta">${esc(item.project_id || '')}</div>
          ${item.description ? `<div class="project-record-meta">${esc(item.description)}</div>` : ''}
        </div>
      `).join('') || (!projectsState.loading ? '<div class="project-sidebar-empty">No projects match the current filter.</div>' : '')}
    </div>
  `;
}

function renderProjectToolbar(title, count, placeholder, extra = '') {
  const section = activeProjectView;
  const family = section === 'networking' ? projectNetworkingFamilyKey() : '';
  return `
    <div class="data-view-toolbar">
      <h2>${esc(title)} <span class="hint">Project Scoped</span></h2>
      ${extra}
      ${projectRefreshMeta(section, family)}
      <button class="btn" onclick="refreshActiveProjectView()">⟳ Refresh</button>
      <input class="dv-filter" type="text" placeholder="${esc(placeholder)}" value="${esc(projectInventoryState.filter || '')}" oninput="projectInventoryState.filter=this.value;renderProjectsWorkspace()">
      <span style="font-size:11px;color:var(--dim)">${esc(String(count || 0))} item(s)</span>
    </div>
  `;
}

function renderProjectSectionToolbar(title, extra = '') {
  const section = activeProjectView;
  const family = section === 'networking' ? projectNetworkingFamilyKey() : '';
  return `
    <div class="data-view-toolbar">
      <h2>${esc(title)} <span class="hint">Project Scoped</span></h2>
      ${extra}
      ${projectRefreshMeta(section, family)}
      <button class="btn" onclick="refreshActiveProjectView()">⟳ Refresh</button>
    </div>
  `;
}

function cloneProjectInstance(instanceId) {
  onLog?.({ node: '-', message: `Clone workflow is not implemented yet for instance ${instanceId}.`, color: 'warn' });
}

function findProjectItem(kind, id) {
  const inv = projectInventoryForView(activeProjectView);
  if (!inv) return null;
  if (kind === 'instances') return (inv.instances || []).find((item) => item.id === id) || null;
  if (kind === 'networks') return (inv.networks || []).find((item) => item.id === id) || null;
  if (kind === 'routers') return (inv.routers || []).find((item) => item.id === id) || null;
  if (kind === 'ports') return (inv.ports || []).find((item) => item.id === id) || null;
  if (kind === 'volumes') return (inv.volumes || []).find((item) => item.id === id) || null;
  if (kind === 'securitygroups') return (inv.security_groups || []).find((item) => item.id === id) || null;
  if (kind === 'floatingips') return (inv.floating_ips || []).find((item) => item.id === id) || null;
  if (kind === 'loadbalancers') return (inv.load_balancers || []).find((item) => item.id === id) || null;
  return null;
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
    routers: id ? `/api/routers/${encodeURIComponent(id)}` : '',
    ports: id ? `/api/ports/${encodeURIComponent(id)}` : '',
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
    projectDetailState.data = json.instance || json.volume || json.load_balancer || json.security_group || json.network || json.router || json.port || null;
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
  projectDetailState.item = { ...projectDetailState.item, expandedPortId: current === portId ? '' : portId };
  renderProjectsWorkspace();
}

function renderOverviewCard(title, rows, extraClass = '') {
  return `
    <div class="card ${extraClass}">
      <div class="card-title">${esc(title)}</div>
      <div class="card-body">
        ${rows.map((row) => `<div class="mrow"><span class="ml">${row.label}</span><span class="mv">${row.value}</span></div>`).join('')}
      </div>
    </div>
  `;
}

function isAmphoraInstance(item) {
  return String(item?.name || '').startsWith('amphora-');
}

function renderProjectInstanceRows(items, cloneable, emptyMessage) {
  return items.map((item) => `
    <tr class="${projectDetailState.kind === 'instances' && projectDetailState.item?.id === item.id ? 'selected' : ''}" style="cursor:pointer" onclick="selectProjectDetailById('instances','${escAttr(item.id)}')">
      <td><div style="font-weight:600">${esc(item.name || item.id)}</div><div class="uuid-short" title="${esc(item.id)}">${esc((item.id || '').slice(0, 12))}</div></td>
      <td>${esc(item.status || 'UNKNOWN')}</td>
      <td>${esc(item.flavor?.name || item.flavor?.id || '—')}</td>
      <td>${esc(item.compute_host || '—')}</td>
      <td>${esc((item.networks || []).map((net) => net.name).join(', ') || '—')}</td>
      <td>${item.is_volume_backed ? 'Volume' : 'Image / Ephemeral'}</td>
      <td>${cloneable ? `<button class="btn" style="font-size:11px" onclick="event.stopPropagation();cloneProjectInstance('${escAttr(item.id)}')">Clone</button>` : '<span class="hint">Not cloneable</span>'}</td>
    </tr>
  `).join('') || `<tr><td colspan="7" style="text-align:center;color:var(--dim);padding:20px">${esc(emptyMessage)}</td></tr>`;
}

function renderProjectOverview(inv) {
  const summary = projectSummary(inv.summary, inv);
  const stats = projectOverviewStats(summary.project_id || selectedProjectId);
  const placement = stats.placement || {};
  const quotas = stats.quotas || {};
  const computeQuota = quotas.compute || {};
  const networkQuota = quotas.network || {};
  const storageQuota = quotas.block_storage || {};
  const hostPct = Number(placement.top_host_pct || summary.top_host_pct || 0);
  const networkingSummary = projectNetworkingSummary(summary, stats);
  return `
    ${renderProjectSectionToolbar('Overview')}
    <div class="summary-grid" style="margin-bottom:10px">
      ${renderOverviewCard('Workloads', [
        { label: 'Instances', value: projectValue(summary.instance_count || 0, stats.instancesLoaded) },
        { label: 'Clone Candidates', value: projectValue(summary.clone_candidate_count || 0, stats.instancesLoaded) },
        { label: 'Active / Error', value: stats.instancesLoaded ? esc(`${summary.active_instance_count || 0} / ${summary.error_instance_count || 0}`) : '<span class="mv dim"><span class="spinner">⟳</span> Loading…</span>' },
      ])}
      ${renderOverviewCard('Networking', [
        { label: 'Networks', value: projectValue(networkingSummary.networks.value, networkingSummary.networks.ready) },
        { label: 'Routers', value: projectValue(networkingSummary.routers.value, networkingSummary.routers.ready) },
        { label: 'Ports', value: projectValue(networkingSummary.ports.value, networkingSummary.ports.ready) },
        { label: 'Floating IPs', value: projectValue(networkingSummary.floatingIps.value, networkingSummary.floatingIps.ready) },
        { label: 'Load Balancers', value: projectValue(networkingSummary.loadBalancers.value, networkingSummary.loadBalancers.ready) }
      ])}
      ${renderOverviewCard('Capacity', [
        { label: 'vCPU', value: projectValue(summary.vcpu_count || 0, stats.instancesLoaded) },
        { label: 'RAM', value: projectValue(Math.round((summary.ram_mb || 0) / 1024), stats.instancesLoaded, ' GB') },
        { label: 'Volumes', value: projectValue(summary.volume_count || 0, stats.storageLoaded) },
      ])}
      ${renderOverviewCard('Placement', [
        { label: 'Hosts', value: projectValue(placement.host_count || summary.host_count || 0, stats.quotaLoaded) },
        { label: 'Top Host', value: stats.quotaLoaded ? esc(placement.top_host || summary.top_host || '—') : '<span class="mv dim"><span class="spinner">⟳</span> Loading…</span>' },
        { label: 'Concentration', value: stats.quotaLoaded ? (hostPct ? esc(`${hostPct.toFixed(0)}%`) : '—') : '<span class="mv dim"><span class="spinner">⟳</span> Loading…</span>' },
      ])}
    </div>
    <div class="summary-grid">
      <div class="card">
        <div class="card-title">Quota Snapshot</div>
        <div class="card-body">
          <div class="mrow"><span class="ml">Instances</span><span class="mv">${stats.quotaLoaded ? esc(`${computeQuota.instances?.used ?? '—'} / ${computeQuota.instances?.limit ?? '—'}`) : '<span class="mv dim"><span class="spinner">⟳</span> Loading…</span>'}</span></div>
          <div class="mrow"><span class="ml">Ports</span><span class="mv">${stats.quotaLoaded ? esc(`${networkQuota.port?.used ?? '—'} / ${networkQuota.port?.limit ?? '—'}`) : '<span class="mv dim"><span class="spinner">⟳</span> Loading…</span>'}</span></div>
          <div class="mrow"><span class="ml">Volumes</span><span class="mv">${stats.quotaLoaded ? esc(`${storageQuota.volumes?.used ?? '—'} / ${storageQuota.volumes?.limit ?? '—'}`) : '<span class="mv dim"><span class="spinner">⟳</span> Loading…</span>'}</span></div>
          <div class="mrow"><span class="ml">Gigabytes</span><span class="mv">${stats.quotaLoaded ? esc(`${storageQuota.gigabytes?.used ?? '—'} / ${storageQuota.gigabytes?.limit ?? '—'}`) : '<span class="mv dim"><span class="spinner">⟳</span> Loading…</span>'}</span></div>
        </div>
      </div>
      <div class="card">
        <div class="card-title">Next Steps</div>
        <div class="card-body">
          <div class="mrow"><span class="ml">Security Review</span><span class="mv"><a class="obj-link" href="#" onclick="event.preventDefault();switchProjectSection('security')">Open Security Tab</a></span></div>
          <div class="mrow"><span class="ml">Network Review</span><span class="mv"><a class="obj-link" href="#" onclick="event.preventDefault();switchProjectSection('networking')">Open Networking Tab</a></span></div>
          <div class="mrow"><span class="ml">Storage Review</span><span class="mv"><a class="obj-link" href="#" onclick="event.preventDefault();switchProjectSection('storage')">Open Storage Tab</a></span></div>
        </div>
      </div>
    </div>
  `;
}

function renderProjectInstances(inv) {
  const items = projectFilterRows(inv.instances || [], ['name', 'status', 'compute_host', 'project_name'], projectInventoryState.filter);
  const projectInstances = items.filter((item) => !isAmphoraInstance(item));
  const amphoraInstances = items.filter((item) => isAmphoraInstance(item));
  const cloneButton = projectDetailState.kind === 'instances' && projectDetailState.item?.id && !isAmphoraInstance(projectDetailState.item)
    ? `<button class="btn primary" onclick="cloneProjectInstance('${escAttr(projectDetailState.item.id)}')">Clone VM</button>`
    : `<button class="btn primary" onclick="onLog?.({ node: '-', message: 'Select a non-amphora instance first to start clone.', color: 'warn' })">Clone VM</button>`;
  return `
    ${renderProjectToolbar('Instances', items.length, 'Filter instances…', cloneButton)}
    <div class="card" style="margin-bottom:10px">
      <div class="card-title">Project Instances</div>
      <div class="card-body" style="padding:0">
        <table class="data-table" style="border:none;margin-bottom:0">
          <thead><tr><th>Name</th><th>Status</th><th>Flavor</th><th>Host</th><th>Networks</th><th>Boot</th><th>Action</th></tr></thead>
          <tbody>
            ${renderProjectInstanceRows(projectInstances, true, 'No cloneable instances match the current filter.')}
          </tbody>
        </table>
      </div>
    </div>
    <div class="card">
      <div class="card-title">Amphora</div>
      <div class="card-body" style="padding:0">
        <table class="data-table" style="border:none;margin-bottom:0">
          <thead><tr><th>Name</th><th>Status</th><th>Flavor</th><th>Host</th><th>Networks</th><th>Boot</th><th>Action</th></tr></thead>
          <tbody>
            ${renderProjectInstanceRows(amphoraInstances, false, 'No amphora instances match the current filter.')}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function renderProjectNetworking(inv) {
  const networks = projectFilterRows(inv.networks || [], ['name', 'status', 'network_type'], projectInventoryState.filter);
  const routers = projectFilterRows(inv.routers || [], ['name', 'status', 'external_network_name', 'admin_state'], projectInventoryState.filter);
  const ports = projectFilterRows(inv.ports || [], ['name', 'status', 'network_name', 'device_owner', 'attached_name', 'attached_id', 'connected_router_name', 'mac_address'], projectInventoryState.filter);
  const floatingIps = projectFilterRows(inv.floating_ips || [], ['floating_ip_address', 'fixed_ip_address', 'status', 'description', 'instance_name', 'instance_id'], projectInventoryState.filter);
  const loadBalancers = projectFilterRows(inv.load_balancers || [], ['name', 'vip_address', 'floating_ip', 'operating_status'], projectInventoryState.filter);
  const counts = {
    networks: networks.length,
    routers: routers.length,
    ports: ports.length,
    floatingips: floatingIps.length,
    loadbalancers: loadBalancers.length,
  };
  const activeCount = counts[activeProjectNetworkingView] || 0;
  const loadingStates = {
    networks: isProjectNetworkingFamilyLoading(selectedProjectId, 'networks'),
    routers: isProjectNetworkingFamilyLoading(selectedProjectId, 'routers'),
    ports: isProjectNetworkingFamilyLoading(selectedProjectId, 'ports'),
    floatingips: isProjectNetworkingFamilyLoading(selectedProjectId, 'floating_ips'),
    loadbalancers: isProjectNetworkingFamilyLoading(selectedProjectId, 'load_balancers'),
  };
  const activeFamily = projectNetworkingFamilyKey();
  const loadingFamily = isProjectNetworkingFamilyLoading(selectedProjectId, activeFamily);
  const toolbarExtra = loadingFamily ? '<span class="hint"><span class="spinner">⟳</span> Loading current view…</span>' : '';
  const navLabel = (icon, label, key, count) => `${icon} ${label} <span>${loadingStates[key] ? '<span class="spinner">⟳</span>' : count}</span>`;
  const filterActive = Boolean(String(projectInventoryState.filter || '').trim());
  const familyEmptyState = (colspan, loadedItems, label) => {
    if (loadingFamily && !loadedItems.length) {
      return `<tr><td colspan="${colspan}" style="text-align:center;color:var(--dim);padding:16px"><span class="spinner">⟳</span> Loading ${label}…</td></tr>`;
    }
    if (filterActive) {
      return `<tr><td colspan="${colspan}" style="text-align:center;color:var(--dim);padding:16px">No ${label} match the current filter.</td></tr>`;
    }
    return `<tr><td colspan="${colspan}" style="text-align:center;color:var(--dim);padding:16px">No ${label} found for this project.</td></tr>`;
  };
  const familyNav = `
    <div class="project-subnav">
      <button class="project-subnav-item ${activeProjectNetworkingView === 'networks' ? 'active' : ''}" onclick="switchProjectNetworkingView('networks')">${navLabel('🌐', 'Networks', 'networks', counts.networks)}</button>
      <button class="project-subnav-item ${activeProjectNetworkingView === 'routers' ? 'active' : ''}" onclick="switchProjectNetworkingView('routers')">${navLabel('🛣️', 'Routers', 'routers', counts.routers)}</button>
      <button class="project-subnav-item ${activeProjectNetworkingView === 'ports' ? 'active' : ''}" onclick="switchProjectNetworkingView('ports')">${navLabel('🔌', 'Ports', 'ports', counts.ports)}</button>
      <button class="project-subnav-item ${activeProjectNetworkingView === 'floatingips' ? 'active' : ''}" onclick="switchProjectNetworkingView('floatingips')">${navLabel('📡', 'Floating IPs', 'floatingips', counts.floatingips)}</button>
      <button class="project-subnav-item ${activeProjectNetworkingView === 'loadbalancers' ? 'active' : ''}" onclick="switchProjectNetworkingView('loadbalancers')">${navLabel('⚖️', 'Load Balancers', 'loadbalancers', counts.loadbalancers)}</button>
    </div>`;
  let table = '';
  if (activeProjectNetworkingView === 'networks') {
    table = `
      <div class="card">
        <div class="card-title">Networks</div>
        <div class="card-body" style="padding:0">
          <table class="data-table" style="border:none;margin-bottom:0">
            <thead><tr><th>Name</th><th>Status</th><th>Type</th><th>Subnets</th><th>Router</th></tr></thead>
            <tbody>
              ${networks.map((item) => `<tr class="${projectDetailState.kind === 'networks' && projectDetailState.item?.id === item.id ? 'selected' : ''}" style="cursor:pointer" onclick="selectProjectDetailById('networks','${escAttr(item.id)}')">
                <td>${esc(item.name || item.id)}</td><td>${esc(item.status || 'UNKNOWN')}</td><td>${esc(item.network_type || '—')}</td><td>${esc(String(item.subnet_count ?? 0))}</td><td>${item.router_id ? renderObjectLink(item.router_id, `navigateToRouterDetail('${escAttr(item.router_id)}')`) : '<span style="color:var(--dim)">—</span>'}</td>
              </tr>`).join('') || familyEmptyState(5, inv.networks || [], 'networks')}
            </tbody>
          </table>
        </div>
      </div>`;
  } else if (activeProjectNetworkingView === 'routers') {
    table = `
      <div class="card">
        <div class="card-title">Routers</div>
        <div class="card-body" style="padding:0">
          <table class="data-table" style="border:none;margin-bottom:0">
            <thead><tr><th>Name</th><th>Status</th><th>Admin</th><th>External Network</th><th>Interfaces</th><th>Routes</th></tr></thead>
            <tbody>
              ${routers.map((item) => `<tr class="${projectDetailState.kind === 'routers' && projectDetailState.item?.id === item.id ? 'selected' : ''}" style="cursor:pointer" onclick="selectProjectDetailById('routers','${escAttr(item.id)}')">
                <td>${renderObjectLink(item.name || item.id, `selectProjectDetailById('routers','${escAttr(item.id)}')`)}</td>
                <td>${esc(item.status || 'UNKNOWN')}</td>
                <td>${esc(item.admin_state || '—')}</td>
                <td>${esc(item.external_network_name || item.external_network_id || '—')}</td>
                <td>${esc(String(item.interface_count || 0))}</td>
                <td>${esc(String(item.route_count || 0))}</td>
              </tr>`).join('') || familyEmptyState(6, inv.routers || [], 'routers')}
            </tbody>
          </table>
        </div>
      </div>`;
  } else if (activeProjectNetworkingView === 'ports') {
    table = `
      <div class="card">
        <div class="card-title">Ports</div>
        <div class="card-body" style="padding:0">
          <table class="data-table" style="border:none;margin-bottom:0">
            <thead><tr><th>Name</th><th>Status</th><th>Network</th><th>Fixed IPs</th><th>Attached</th><th>Router</th><th>Floating IPs</th></tr></thead>
            <tbody>
              ${ports.map((item) => `<tr class="${projectDetailState.kind === 'ports' && projectDetailState.item?.id === item.id ? 'selected' : ''}" style="cursor:pointer" onclick="selectProjectDetailById('ports','${escAttr(item.id)}')">
                <td><div>${esc(item.name || item.id)}</div><div class="uuid-short">${esc(item.id || '')}</div></td>
                <td>${esc(item.status || 'UNKNOWN')}</td>
                <td>${item.network_id ? renderObjectLink(item.network_name || item.network_id, `navigateToNetworkDetail('${escAttr(item.network_id)}')`) : '<span style="color:var(--dim)">—</span>'}</td>
                <td style="font-family:monospace;font-size:10px">${esc((item.fixed_ip_addresses || []).join(', ') || '—')}</td>
                <td>${item.attached_kind === 'instance' && item.attached_id && item.compute_host
                  ? renderObjectLink(item.attached_name || item.attached_id, `navigateToInstanceDetail('${escAttr(item.attached_id)}','${escAttr(item.compute_host)}')`)
                  : item.attached_kind === 'router' && item.attached_id
                    ? renderObjectLink(item.attached_name || item.attached_id, `navigateToRouterDetail('${escAttr(item.attached_id)}')`)
                    : item.attached_kind === 'load-balancer' && item.attached_id
                      ? renderObjectLink(item.attached_name || item.attached_id, `navigateToLoadBalancerDetail('${escAttr(item.attached_id)}')`)
                      : esc(item.attached_name || item.attached_id || '—')}</td>
                <td>${item.connected_router_id ? renderObjectLink(item.connected_router_name || item.connected_router_id, `navigateToRouterDetail('${escAttr(item.connected_router_id)}')`) : '<span style="color:var(--dim)">—</span>'}</td>
                <td style="font-family:monospace;font-size:10px">${esc((item.floating_ips || []).map((ip) => ip.address).join(', ') || '—')}</td>
              </tr>`).join('') || familyEmptyState(7, inv.ports || [], 'ports')}
            </tbody>
          </table>
        </div>
      </div>`;
  } else if (activeProjectNetworkingView === 'floatingips') {
    table = `
      <div class="card">
        <div class="card-title">Floating IPs</div>
        <div class="card-body" style="padding:0">
          <table class="data-table" style="border:none;margin-bottom:0">
            <thead><tr><th>Floating IP</th><th>Fixed IP</th><th>Status</th><th>Network</th><th>Instance</th><th>Port</th></tr></thead>
            <tbody>
              ${floatingIps.map((item) => `<tr class="${projectDetailState.kind === 'floatingips' && projectDetailState.item?.id === item.id ? 'selected' : ''}" style="cursor:pointer" onclick="selectProjectDetailById('floatingips','${escAttr(item.id)}')">
                <td>${esc(item.floating_ip_address || '—')}</td><td>${esc(item.fixed_ip_address || '—')}</td><td>${esc(item.status || 'UNKNOWN')}</td><td>${esc(item.floating_network_name || item.floating_network_id || '—')}</td><td>${item.instance_id && item.compute_host ? renderObjectLink(item.instance_name || item.instance_id, `navigateToInstanceDetail('${escAttr(item.instance_id)}','${escAttr(item.compute_host)}')`) : esc(item.instance_name || '—')}</td><td>${item.port_id ? renderObjectLink((item.port_id || '').slice(0, 12), `navigateToPortDetail('${escAttr(item.port_id)}')`) : '<span style="color:var(--dim)">—</span>'}</td>
              </tr>`).join('') || familyEmptyState(6, inv.floating_ips || [], 'floating IPs')}
            </tbody>
          </table>
        </div>
      </div>`;
  } else {
    table = `
      <div class="card">
        <div class="card-title">Load Balancers</div>
        <div class="card-body" style="padding:0">
          <table class="data-table" style="border:none;margin-bottom:0">
            <thead><tr><th>Name</th><th>VIP</th><th>Floating IP</th><th>Status</th><th>Pools</th></tr></thead>
            <tbody>
              ${loadBalancers.map((item) => `<tr class="${projectDetailState.kind === 'loadbalancers' && projectDetailState.item?.id === item.id ? 'selected' : ''}" style="cursor:pointer" onclick="selectProjectDetailById('loadbalancers','${escAttr(item.id)}')">
                <td>${esc(item.name || item.id)}</td><td>${esc(item.vip_address || '—')}</td><td>${esc(item.floating_ip || '—')}</td><td>${esc(item.operating_status || 'UNKNOWN')} / ${esc(item.provisioning_status || 'UNKNOWN')}</td><td>${esc(String(item.pool_count || 0))}</td>
              </tr>`).join('') || familyEmptyState(5, inv.load_balancers || [], 'load balancers')}
            </tbody>
          </table>
        </div>
      </div>`;
  }
  return `
    ${renderProjectToolbar('Networking', activeCount, 'Filter networking…', toolbarExtra)}
    ${familyNav}
    ${table}
  `;
}

function renderProjectStorage(inv) {
  const items = projectFilterRows(inv.volumes || [], ['name', 'status', 'volume_type', 'backend_name'], projectInventoryState.filter);
  return `
    ${renderProjectToolbar('Storage', items.length, 'Filter volumes…')}
    <div class="project-subnav">
      <button class="project-subnav-item ${activeProjectStorageView === 'volumes' ? 'active' : ''}" onclick="switchProjectStorageView('volumes')">💿 Volumes <span>${items.length}</span></button>
      <button class="project-subnav-item future" disabled>📸 Snapshots <span>Later</span></button>
      <button class="project-subnav-item future" disabled>🛟 Backups <span>Later</span></button>
    </div>
    <table class="data-table">
      <thead><tr><th>Name</th><th>Status</th><th>Size</th><th>Type</th><th>Backend</th><th>Attached</th></tr></thead>
      <tbody>
        ${items.map((item) => `<tr class="${projectDetailState.kind === 'volumes' && projectDetailState.item?.id === item.id ? 'selected' : ''}" style="cursor:pointer" onclick="selectProjectDetailById('volumes','${escAttr(item.id)}')">
          <td>${esc(item.name || item.id)}</td><td>${esc(item.status || 'UNKNOWN')}</td><td>${esc(String(item.size_gb || 0))} GB</td><td>${esc(item.volume_type || '—')}</td><td>${esc(item.backend_name || '—')}</td><td>${esc(String((item.attached_to || []).length || 0))}</td>
        </tr>`).join('') || '<tr><td colspan="6" style="text-align:center;color:var(--dim);padding:20px">No volumes match the current filter.</td></tr>'}
      </tbody>
    </table>
  `;
}

function renderProjectSecurity(inv) {
  const items = projectFilterRows(inv.security_groups || [], ['name', 'project_name'], projectInventoryState.filter);
  return `
    ${renderProjectToolbar('Security', items.length, 'Filter security groups…')}
    <table class="data-table">
      <thead><tr><th>Name</th><th>Severity</th><th>Rules</th><th>Attached Ports</th><th>Instances</th></tr></thead>
      <tbody>
        ${items.map((item) => `<tr class="${projectDetailState.kind === 'securitygroups' && projectDetailState.item?.id === item.id ? 'selected' : ''}" style="cursor:pointer" onclick="selectProjectDetailById('securitygroups','${escAttr(item.id)}')">
          <td>${esc(item.name || item.id)}</td><td>${esc(item.audit?.severity || 'unknown')}</td><td>${esc(String(item.rule_count || 0))}</td><td>${esc(String(item.attachment_port_count || 0))}</td><td>${esc(String(item.attachment_instance_count || 0))}</td>
        </tr>`).join('') || '<tr><td colspan="5" style="text-align:center;color:var(--dim);padding:20px">No security groups match the current filter.</td></tr>'}
      </tbody>
    </table>
  `;
}

function renderQuotaSection(title, entries) {
  const rows = Object.entries(entries || {});
  const editable = Boolean(authInfo?.is_admin);
  return `
    <div class="card" style="margin-bottom:10px">
      <div class="card-title">${esc(title)}</div>
      <div class="card-body" style="padding:0">
        <table class="data-table" style="border:none;margin-bottom:0">
          <thead><tr><th>Resource</th><th>Used</th><th>Limit</th>${editable ? '<th>Action</th>' : ''}</tr></thead>
          <tbody>
            ${rows.map(([key, value]) => {
              const section = title === 'Compute Quotas' ? 'compute' : title === 'Network Quotas' ? 'network' : 'block_storage';
              const editing = editable && projectQuotaEditState.section === section && projectQuotaEditState.resource === key;
              const wasUpdated = projectQuotaEditState.successKey === `${section}:${key}`;
              const limitCell = editing
                ? `<div class="project-quota-editor"><input class="project-quota-input" type="text" value="${escAttr(projectQuotaEditState.value || '')}" oninput="updateProjectQuotaEditorValue(this.value)" placeholder="Enter new limit">${projectQuotaEditState.error ? `<div class="project-quota-error">${esc(projectQuotaEditState.error)}</div>` : ''}</div>`
                : `<span class="${wasUpdated ? 'project-quota-value project-quota-value-updated' : 'project-quota-value'}">${esc(String(value.limit ?? '—'))}</span>`;
              const actionCell = editable
                ? editing
                  ? `<div class="project-quota-actions"><button class="btn primary" ${projectQuotaEditState.saving ? 'disabled' : ''} onclick="saveProjectQuotaEdit()">${projectQuotaEditState.saving ? 'Saving…' : 'Save'}</button><button class="btn" ${projectQuotaEditState.saving ? 'disabled' : ''} onclick="resetProjectQuotaEditor();renderProjectsWorkspace()">Cancel</button></div>`
                  : `<div class="project-quota-actions"><button class="btn" onclick="openProjectQuotaEditor('${escAttr(section)}','${escAttr(key)}','${escAttr(value.limit ?? '')}')">Modify</button>${wasUpdated ? '<span class="project-quota-saved">Saved</span>' : ''}</div>`
                : '';
              return `<tr><td>${esc(key)}</td><td>${esc(String(value.used ?? '—'))}</td><td>${limitCell}</td>${editable ? `<td>${actionCell}</td>` : ''}</tr>`;
            }).join('') || `<tr><td colspan="${editable ? '4' : '3'}" style="text-align:center;color:var(--dim);padding:16px">No quota data available.</td></tr>`}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function renderProjectQuota(inv) {
  const summary = projectSummary(inv.summary, inv);
  const stats = projectOverviewStats(summary.project_id || selectedProjectId);
  const quotas = inv.quotas || stats.quotas || {};
  const placement = inv.placement || stats.placement || {};
  const hostPct = Number(placement.top_host_pct || summary.top_host_pct || 0);
  const networkingSummary = projectNetworkingSummary(summary, stats);
  return `
    ${renderProjectSectionToolbar('Quota + Capacity')}
    <div class="summary-grid" style="margin-bottom:10px">
      ${renderOverviewCard('Instance Footprint', [
        { label: 'Instances', value: projectValue(summary.instance_count || 0, stats.instancesLoaded) },
        { label: 'vCPU', value: projectValue(summary.vcpu_count || 0, stats.instancesLoaded) },
        { label: 'RAM', value: projectValue(Math.round((summary.ram_mb || 0) / 1024), stats.instancesLoaded, ' GB') },
      ])}
      ${renderOverviewCard('Placement Spread', [
        { label: 'Hosts', value: projectValue(placement.host_count || summary.host_count || 0, stats.quotaLoaded) },
        { label: 'Top Host', value: stats.quotaLoaded ? esc(placement.top_host || summary.top_host || '—') : '<span class="mv dim"><span class="spinner">⟳</span> Loading…</span>' },
        { label: 'Concentration', value: stats.quotaLoaded ? (hostPct ? esc(`${hostPct.toFixed(0)}%`) : '—') : '<span class="mv dim"><span class="spinner">⟳</span> Loading…</span>' },
      ])}
      ${renderOverviewCard('Project Networking', [
        { label: 'Networks', value: projectValue(networkingSummary.networks.value, networkingSummary.networks.ready) },
        { label: 'Routers', value: projectValue(networkingSummary.routers.value, networkingSummary.routers.ready) },
        { label: 'Ports', value: projectValue(networkingSummary.ports.value, networkingSummary.ports.ready) },
        { label: 'Floating IPs', value: projectValue(networkingSummary.floatingIps.value, networkingSummary.floatingIps.ready) },
        { label: 'Load Balancers', value: projectValue(networkingSummary.loadBalancers.value, networkingSummary.loadBalancers.ready) }
      ])}
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
  if (!selected) {
    if (projectsState.loading && !(projectsState.data || []).length) {
      wrap.innerHTML = `<div style="color:var(--dim);padding:20px"><span class="spinner">⟳</span> Loading projects…</div>`;
      return;
    }
    wrap.innerHTML = `<div style="color:var(--dim);padding:20px">Select a project from the navigator.</div>`;
    return;
  }
  const inv = projectInventoryForView(activeProjectView);
  const activeSection = projectSectionKey(activeProjectView);
  const activeLoading = activeSection === 'networking'
    ? isProjectNetworkingFamilyLoading(selected.project_id, projectNetworkingFamilyKey())
    : isProjectSectionLoading(selected.project_id, activeSection);
  if (activeLoading && !inv) {
    wrap.innerHTML = `<div style="color:var(--dim);padding:20px"><span class="spinner">⟳</span> Loading project data…</div>`;
    return;
  }
  if (!inv) {
    wrap.innerHTML = `<div style="color:var(--dim);padding:20px">Project data is unavailable. Try refreshing once.</div>`;
    return;
  }

  const summary = projectSummary(inv.summary, inv);
  const detailsLoading = projectHasBackgroundLoading(selected.project_id, activeSection);
  const body = activeProjectView === 'overview'
    ? renderProjectOverview(inv)
    : activeProjectView === 'instances'
      ? renderProjectInstances(inv)
      : activeProjectView === 'networking'
        ? renderProjectNetworking(inv)
        : activeProjectView === 'storage'
          ? renderProjectStorage(inv)
          : activeProjectView === 'security'
            ? renderProjectSecurity(inv)
            : renderProjectQuota(inv);

  wrap.innerHTML = `
    <div id="obj-header">
      <div class="obj-icon">▣</div>
      <div class="obj-meta">
        <h2>${esc(summary.project_name || summary.project_id)}</h2>
        <div class="obj-sub">${esc(summary.project_id || '')}</div>
        ${summary.description ? `<div class="obj-sub">${esc(summary.description)}</div>` : ''}
      </div>
    </div>
    <div id="tabs-bar">
      <div class="tab ${activeProjectView === 'overview' ? 'active' : ''}" onclick="switchProjectSection('overview')">Overview</div>
      <div class="tab ${activeProjectView === 'instances' ? 'active' : ''}" onclick="switchProjectSection('instances')">Instances</div>
      <div class="tab ${activeProjectView === 'networking' ? 'active' : ''}" onclick="switchProjectSection('networking')">Networking</div>
      <div class="tab ${activeProjectView === 'storage' ? 'active' : ''}" onclick="switchProjectSection('storage')">Storage</div>
      <div class="tab ${activeProjectView === 'security' ? 'active' : ''}" onclick="switchProjectSection('security')">Security</div>
      <div class="tab ${activeProjectView === 'quota' ? 'active' : ''}" onclick="switchProjectSection('quota')">Quota + Capacity</div>
    </div>
    <div class="project-body-shell">
      <div class="project-body-pane">${body}</div>
      ${detailsLoading ? '<div class="project-loading-overlay"><span class="spinner">⟳</span> Loading details…</div>' : ''}
    </div>
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
  } else if (projectDetailState.kind === 'routers') {
    body = `<div class="card"><div class="card-title">Router</div><div class="card-body">
      <div class="mrow"><span class="ml">Name</span><span class="mv">${esc(detail.name || detail.id || '—')}</span></div>
      <div class="mrow"><span class="ml">Status</span><span class="mv">${esc(detail.status || 'UNKNOWN')}</span></div>
      <div class="mrow"><span class="ml">Admin</span><span class="mv">${esc(detail.admin_state || '—')}</span></div>
      <div class="mrow"><span class="ml">External Network</span><span class="mv">${esc(detail.external_network_name || detail.external_network_id || '—')}</span></div>
      <div class="mrow"><span class="ml">Interfaces</span><span class="mv">${esc(String(detail.interface_count || 0))}</span></div>
      <div class="mrow"><span class="ml">Routes</span><span class="mv">${esc(String(detail.route_count || 0))}</span></div>
    </div></div>`;
  } else if (projectDetailState.kind === 'ports') {
    body = `<div class="card"><div class="card-title">Port</div><div class="card-body">
      <div class="mrow"><span class="ml">Name</span><span class="mv">${esc(detail.name || detail.id || '—')}</span></div>
      <div class="mrow"><span class="ml">Status</span><span class="mv">${esc(detail.status || 'UNKNOWN')}</span></div>
      <div class="mrow"><span class="ml">Network</span><span class="mv">${detail.network_id ? renderObjectLink(detail.network_name || detail.network_id, `navigateToNetworkDetail('${escAttr(detail.network_id)}')`) : '—'}</span></div>
      <div class="mrow"><span class="ml">Attached</span><span class="mv">${detail.attached_kind === 'instance' && detail.attached_id && detail.compute_host
        ? renderObjectLink(detail.attached_name || detail.attached_id, `navigateToInstanceDetail('${escAttr(detail.attached_id)}','${escAttr(detail.compute_host)}')`)
        : detail.attached_kind === 'router' && detail.attached_id
          ? renderObjectLink(detail.attached_name || detail.attached_id, `navigateToRouterDetail('${escAttr(detail.attached_id)}')`)
          : detail.attached_kind === 'load-balancer' && detail.attached_id
            ? renderObjectLink(detail.attached_name || detail.attached_id, `navigateToLoadBalancerDetail('${escAttr(detail.attached_id)}')`)
            : esc(detail.attached_name || detail.attached_id || '—')}</span></div>
      <div class="mrow"><span class="ml">Router</span><span class="mv">${detail.connected_router_id ? renderObjectLink(detail.connected_router_name || detail.connected_router_id, `navigateToRouterDetail('${escAttr(detail.connected_router_id)}')`) : '—'}</span></div>
      <div class="mrow"><span class="ml">Fixed IPs</span><span class="mv mono">${esc((detail.fixed_ip_addresses || []).join(', ') || '—')}</span></div>
      <div class="mrow"><span class="ml">Floating IPs</span><span class="mv mono">${esc((detail.floating_ips || []).map((ip) => ip.address).join(', ') || '—')}</span></div>
      <div class="mrow"><span class="ml">Security Groups</span><span class="mv">${(detail.security_groups || []).length ? (detail.security_groups || []).map((group) => renderObjectLink(group.name || group.id, `switchNetworkingSection('securitygroups');selectSecurityGroup('${escAttr(group.id)}')`)).join(', ') : '—'}</span></div>
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
      <div class="mrow"><span class="ml">Instance</span><span class="mv">${detail.instance_id && detail.compute_host ? renderObjectLink(detail.instance_name || detail.instance_id, `navigateToInstanceDetail('${escAttr(detail.instance_id)}','${escAttr(detail.compute_host)}')`) : esc(detail.instance_name || '—')}</span></div>
      <div class="mrow"><span class="ml">Port</span><span class="mv">${detail.port_id ? renderObjectLink(detail.port_id, `navigateToPortDetail('${escAttr(detail.port_id)}')`) : '—'}</span></div>
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
        <strong style="font-size:13px">${esc(projectDetailState.kind === 'securitygroups' ? 'Security Group' : projectDetailState.kind === 'floatingips' ? 'Floating IP' : projectDetailState.kind === 'loadbalancers' ? 'Load Balancer' : projectDetailState.kind === 'networks' ? 'Network' : projectDetailState.kind === 'routers' ? 'Router' : projectDetailState.kind === 'ports' ? 'Port' : projectDetailState.kind === 'volumes' ? 'Volume' : 'Instance')} Detail</strong>
        <div style="display:flex;gap:6px;align-items:center">
          ${projectDetailState.kind === 'instances' && !isAmphoraInstance(detail || item) ? `<button class="btn" style="padding:1px 7px;font-size:11px" onclick="cloneProjectInstance('${escAttr(detail.id || item.id || '')}')">Clone</button>` : ''}
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
