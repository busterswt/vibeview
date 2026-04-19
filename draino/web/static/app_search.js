'use strict';

const globalSearchState = {
  query: '',
  open: false,
  activeIndex: 0,
  localResults: [],
  remoteResults: [],
  results: [],
  remoteLoading: false,
  remoteError: '',
  remoteSeq: 0,
  debounceTimer: null,
};

const GLOBAL_SEARCH_LIMIT = 18;
const GLOBAL_SEARCH_HISTORY_KEY = 'vibeviewGlobalSearchHistory';
const GLOBAL_SEARCH_HISTORY_LIMIT = 6;

function globalSearchInput() {
  return document.getElementById('global-search-input');
}

function globalSearchDropdown() {
  return document.getElementById('global-search-dropdown');
}

function loadGlobalSearchHistory() {
  try {
    const raw = localStorage.getItem(GLOBAL_SEARCH_HISTORY_KEY);
    const items = raw ? JSON.parse(raw) : [];
    return Array.isArray(items) ? items.filter((item) => typeof item === 'string' && item.trim()).slice(0, GLOBAL_SEARCH_HISTORY_LIMIT) : [];
  } catch (_) {
    return [];
  }
}

function saveGlobalSearchHistory(items) {
  try {
    localStorage.setItem(GLOBAL_SEARCH_HISTORY_KEY, JSON.stringify((items || []).slice(0, GLOBAL_SEARCH_HISTORY_LIMIT)));
  } catch (_) {
    // ignore storage failures
  }
}

function clearGlobalSearchHistory() {
  try {
    localStorage.removeItem(GLOBAL_SEARCH_HISTORY_KEY);
  } catch (_) {
    // ignore storage failures
  }
  renderGlobalSearch();
}

function rememberGlobalSearchQuery(query) {
  const normalized = String(query || '').trim();
  if (!normalized) return;
  const history = loadGlobalSearchHistory().filter((item) => item.toLowerCase() !== normalized.toLowerCase());
  history.unshift(normalized);
  saveGlobalSearchHistory(history);
}

function globalSearchIcon(kind) {
  return ({
    node: '🖥️',
    instance: '🧠',
    project: '📁',
    network: '🌐',
    router: '🛣️',
    port: '🔌',
    floatingip: '📡',
    loadbalancer: '⚖️',
    securitygroup: '🛡️',
    volume: '💿',
    kubernetes: '☸️',
  })[kind] || '•';
}

function globalSearchGroupLabel(kind) {
  return ({
    node: 'Nodes',
    instance: 'Instances',
    project: 'Projects',
    network: 'Networks',
    router: 'Routers',
    port: 'Ports',
    floatingip: 'Floating IPs',
    loadbalancer: 'Load Balancers',
    securitygroup: 'Security Groups',
    volume: 'Volumes',
    kubernetes: 'Kubernetes',
  })[kind] || 'Results';
}

function globalSearchKindRank(kind) {
  return ({
    node: 0,
    instance: 1,
    project: 2,
    network: 3,
    router: 4,
    port: 5,
    floatingip: 6,
    loadbalancer: 7,
    securitygroup: 8,
    volume: 9,
    kubernetes: 10,
  })[kind] ?? 50;
}

function globalSearchScore(query, fields) {
  const q = String(query || '').trim().toLowerCase();
  if (!q) return 0;
  let best = 0;
  const queryLooksLikeIp = /\d/.test(q) && (q.includes('.') || q.includes(':'));
  for (const raw of fields || []) {
    const value = String(raw || '').trim().toLowerCase();
    if (!value) continue;
    if (value === q) best = Math.max(best, queryLooksLikeIp ? 130 : 120);
    else if (queryLooksLikeIp && value.startsWith(q)) best = Math.max(best, 110);
    else if (q.length >= 6 && (value.startsWith(q) || value.includes(q))) best = Math.max(best, value.startsWith(q) ? 100 : 78);
    else if (value.startsWith(q)) best = Math.max(best, 95);
    else if (value.split(/[\s:/._-]+/).some((part) => part.startsWith(q))) best = Math.max(best, 82);
    else if (value.includes(q)) best = Math.max(best, 70);
  }
  return best;
}

function highlightGlobalSearchText(text, query) {
  const source = String(text || '');
  const needle = String(query || '').trim();
  if (!needle) return esc(source);
  const lowerSource = source.toLowerCase();
  const lowerNeedle = needle.toLowerCase();
  const start = lowerSource.indexOf(lowerNeedle);
  if (start < 0) return esc(source);
  const end = start + needle.length;
  return `${esc(source.slice(0, start))}<mark>${esc(source.slice(start, end))}</mark>${esc(source.slice(end))}`;
}

function globalSearchAddResult(results, seen, query, entry) {
  const score = globalSearchScore(query, entry.match || []);
  if (!score) return;
  const key = `${entry.kind}:${entry.id || entry.key || entry.label}`;
  if (seen.has(key)) return;
  seen.add(key);
  results.push({ ...entry, score, key });
}

function mergeGlobalSearchResults(localResults, remoteResults) {
  const merged = [];
  const seen = new Set();
  for (const item of [...(localResults || []), ...(remoteResults || [])]) {
    const key = item.key || `${item.kind}:${item.id || item.label}`;
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push({ ...item, key });
  }
  return merged
    .sort((a, b) => (b.score - a.score) || (globalSearchKindRank(a.kind) - globalSearchKindRank(b.kind)) || String(a.label).localeCompare(String(b.label)))
    .slice(0, GLOBAL_SEARCH_LIMIT);
}

function focusGlobalSearchInput(select = true) {
  const input = globalSearchInput();
  if (!input) return;
  input.focus();
  if (select) input.select();
}

function setGlobalSearchQuery(query) {
  const input = globalSearchInput();
  if (input) input.value = query;
  updateGlobalSearch(query);
}

function globalSearchNodeResults(query, results, seen) {
  for (const [nodeName, node] of Object.entries(nodes || {})) {
    globalSearchAddResult(results, seen, query, {
      kind: 'node',
      id: nodeName,
      label: nodeName,
      subtext: `${node?.hypervisor || 'Node'} • ${node?.is_compute ? 'compute' : node?.is_network ? 'network' : node?.is_etcd ? 'etcd' : 'host'}`,
      match: [nodeName, node?.hypervisor, node?.role, node?.k8s_name],
      action: async () => {
        closeGlobalSearch();
        switchView('infrastructure');
        selectNode(nodeName);
      },
    });
    const instances = [...(node?.instances || []), ...(node?.preflight_instances || [])];
    for (const item of instances) {
      globalSearchAddResult(results, seen, query, {
        kind: 'instance',
        id: item.id || `${nodeName}:${item.name}`,
        label: item.name || item.id || 'Instance',
        subtext: `${nodeName} • ${item.status || 'UNKNOWN'}${item.project_name ? ` • ${item.project_name}` : ''}`,
        match: [item.name, item.id, item.status, nodeName, item.project_name, ...(item.fixed_ips || []), ...(item.floating_ips || [])],
        action: async () => {
          closeGlobalSearch();
          if (item.id) {
            const computeHost = node?.hypervisor || nodeName;
            await navigateToInstanceDetail(item.id, computeHost);
            return;
          }
          switchView('infrastructure');
          selectNode(nodeName);
          showTab('instances');
        },
      });
    }
  }
}

function globalSearchProjectResults(query, results, seen) {
  for (const item of projectsState.data || []) {
    globalSearchAddResult(results, seen, query, {
      kind: 'project',
      id: item.project_id,
      label: item.project_name || item.project_id,
      subtext: item.project_id || 'Project',
      match: [item.project_name, item.project_id, item.description],
      action: async () => {
        closeGlobalSearch();
        switchView('projects');
        selectProject(item.project_id);
      },
    });
  }
}

function globalSearchOpenStackResults(query, results, seen) {
  for (const item of netState.data || []) {
    globalSearchAddResult(results, seen, query, {
      kind: 'network',
      id: item.id,
      label: item.name || item.id,
      subtext: `${item.network_type || 'Network'} • ${item.status || 'UNKNOWN'}`,
      match: [item.name, item.id, item.status, item.network_type, item.project_id],
      action: async () => {
        closeGlobalSearch();
        await navigateToNetworkDetail(item.id);
      },
    });
  }
  for (const item of routerState.data || []) {
    globalSearchAddResult(results, seen, query, {
      kind: 'router',
      id: item.id,
      label: item.name || item.id,
      subtext: `${item.status || 'UNKNOWN'} • ${item.external_network_name || item.external_network_id || 'no external network'}`,
      match: [item.name, item.id, item.status, item.external_network_name, item.external_network_id, item.project_id],
      action: async () => {
        closeGlobalSearch();
        await navigateToRouterDetail(item.id);
      },
    });
  }
  for (const item of portState.data || []) {
    globalSearchAddResult(results, seen, query, {
      kind: 'port',
      id: item.id,
      label: item.name || item.id,
      subtext: `${item.network_name || item.network_id || 'Port'} • ${item.attached_name || item.device_owner || item.status || 'UNKNOWN'}`,
      match: [item.name, item.id, item.network_name, item.network_id, item.attached_name, item.attached_id, item.device_owner, item.mac_address, ...(item.fixed_ip_addresses || []), ...(item.floating_ips || []).map((ip) => ip.address)],
      action: async () => {
        closeGlobalSearch();
        await navigateToPortDetail(item.id);
      },
    });
  }
  for (const item of lbState.data || []) {
    globalSearchAddResult(results, seen, query, {
      kind: 'loadbalancer',
      id: item.id,
      label: item.name || item.id,
      subtext: `${item.vip_address || 'no VIP'} • ${item.operating_status || 'UNKNOWN'}`,
      match: [item.name, item.id, item.vip_address, item.floating_ip, item.project_id, item.operating_status, item.provisioning_status],
      action: async () => {
        closeGlobalSearch();
        await navigateToLoadBalancerDetail(item.id);
      },
    });
  }
  for (const item of sgState.data || []) {
    globalSearchAddResult(results, seen, query, {
      kind: 'securitygroup',
      id: item.id,
      label: item.name || item.id,
      subtext: `${item.project_name || item.project_id || 'Security Group'} • ${item.audit?.severity || 'unknown'}`,
      match: [item.name, item.id, item.project_name, item.project_id, item.description, item.audit?.severity],
      action: async () => {
        closeGlobalSearch();
        switchNetworkingSection('securitygroups');
        await loadSecurityGroups();
        await selectSecurityGroup(item.id);
      },
    });
  }
  for (const item of volState.data || []) {
    globalSearchAddResult(results, seen, query, {
      kind: 'volume',
      id: item.id,
      label: item.name || item.id,
      subtext: `${item.volume_type || 'Volume'} • ${item.status || 'UNKNOWN'} • ${item.size_gb || 0} GB`,
      match: [item.name, item.id, item.volume_type, item.status, item.backend_name, item.project_id],
      action: async () => {
        closeGlobalSearch();
        switchView('storage');
        switchStorageSection('openstack-volumes');
        await loadVolumes();
        await selectVolume(item.id);
      },
    });
  }
}

function globalSearchProjectInventoryResults(query, results, seen) {
  for (const [projectId, sections] of Object.entries(projectInventoryState.sections || {})) {
    const project = (projectsState.data || []).find((item) => item.project_id === projectId);
    const projectName = project?.project_name || sections?.overview?.summary?.project_name || projectId;
    for (const item of sections?.instances?.instances || []) {
      globalSearchAddResult(results, seen, query, {
        kind: 'instance',
        id: item.id,
        label: item.name || item.id,
        subtext: `${projectName} • ${item.compute_host || item.status || 'UNKNOWN'}`,
        match: [item.name, item.id, item.compute_host, item.project_name, ...(item.fixed_ips || []), ...(item.floating_ips || [])],
        action: async () => {
          closeGlobalSearch();
          if (item.id && item.compute_host) {
            await navigateToInstanceDetail(item.id, item.compute_host);
            return;
          }
          switchView('projects');
          selectProject(projectId);
          switchProjectSection('instances');
          selectProjectDetailById('instances', item.id);
        },
      });
    }
    for (const item of sections?.networking?.floating_ips || []) {
      globalSearchAddResult(results, seen, query, {
        kind: 'floatingip',
        id: item.id,
        label: item.floating_ip_address || item.id,
        subtext: `${projectName} • ${item.instance_name || item.fixed_ip_address || item.status || 'Floating IP'}`,
        match: [item.id, item.floating_ip_address, item.fixed_ip_address, item.instance_name, item.instance_id, item.port_id, item.status],
        action: async () => {
          closeGlobalSearch();
          switchView('projects');
          selectProject(projectId);
          switchProjectSection('networking');
          switchProjectNetworkingView('floatingips');
          selectProjectDetailById('floatingips', item.id);
        },
      });
    }
  }
}

function globalSearchK8sResults(query, results, seen) {
  for (const [type, cached] of Object.entries(k8sResCache || {})) {
    for (const item of cached?.data || []) {
      const key = typeof k8sItemKey === 'function' ? k8sItemKey(type, item) : (item.name || JSON.stringify(item));
      const label = item.name || item.driver || item.namespace || K8S_RES_META[type]?.label || type;
      const subtext = item.namespace ? `${item.namespace} • ${K8S_RES_META[type]?.label || type}` : (K8S_RES_META[type]?.label || type);
      globalSearchAddResult(results, seen, query, {
        kind: 'kubernetes',
        id: `${type}:${key}`,
        label,
        subtext,
        match: [label, key, item.namespace, item.name, item.driver, item.cluster_ip, item.type, item.pod_name, item.v4_ip, item.v6_ip, type, K8S_RES_META[type]?.label],
        action: async () => {
          closeGlobalSearch();
          if (['services', 'lbs', 'vpcs', 'subnets', 'vlans', 'providernetworks', 'providersubnets', 'ips', 'clusternetworks', 'networkdomains', 'gatewayclasses', 'gateways', 'httproutes'].includes(type)) {
            switchView('networking');
            switchNetworkingSection(type === 'services' ? 'k8s-services'
              : type === 'lbs' ? 'k8s-lbs'
              : type === 'vpcs' ? 'k8s-vpcs'
              : type === 'subnets' ? 'k8s-subnets'
              : type === 'vlans' ? 'k8s-vlans'
              : type === 'providernetworks' ? 'k8s-providernetworks'
              : type === 'providersubnets' ? 'k8s-providersubnets'
              : type === 'ips' ? 'k8s-ips'
              : type === 'clusternetworks' ? 'k8s-clusternetworks'
              : type === 'networkdomains' ? 'k8s-networkdomains'
              : type === 'gatewayclasses' ? 'k8s-gatewayclasses'
              : type === 'gateways' ? 'k8s-gateways'
              : 'k8s-httproutes');
          } else if (['pvcs', 'pvs', 'storagecsis'].includes(type)) {
            switchView('storage');
            switchStorageSection(type === 'pvcs' ? 'k8s-pvcs' : type === 'pvs' ? 'k8s-pvs' : 'k8s-csi');
          } else {
            switchView('kubernetes');
          }
          await selectK8sResource(type);
          selectK8sObject(type, key);
        },
      });
    }
  }
}

function computeGlobalSearchResults(query) {
  const results = [];
  const seen = new Set();
  globalSearchNodeResults(query, results, seen);
  globalSearchProjectResults(query, results, seen);
  globalSearchOpenStackResults(query, results, seen);
  globalSearchProjectInventoryResults(query, results, seen);
  globalSearchK8sResults(query, results, seen);
  return results
    .sort((a, b) => (b.score - a.score) || (globalSearchKindRank(a.kind) - globalSearchKindRank(b.kind)) || String(a.label).localeCompare(String(b.label)))
    .slice(0, GLOBAL_SEARCH_LIMIT);
}

async function navigateToProjectScopedSearchResult(projectId, section, kind, id, options = {}) {
  if (!projectId) return false;
  const projectSection = section || 'overview';
  closeGlobalSearch();
  switchView('projects');
  selectProject(projectId);
  switchProjectSection(projectSection);
  if (projectSection === 'networking' && options.networkingView) {
    switchProjectNetworkingView(options.networkingView);
  }
  if (projectSection === 'storage' && options.storageView) {
    switchProjectStorageView(options.storageView);
  }
  await loadProjectInventory(projectId, projectSectionKey(projectSection), false);
  if (kind && id) selectProjectDetailById(kind, id);
  return true;
}

function globalSearchActionForResult(item) {
  if (typeof item.action === 'function') return item.action;
  if (item.kind === 'project') {
    return async () => {
      closeGlobalSearch();
      switchView('projects');
      selectProject(item.project_id || item.id);
    };
  }
  if (item.kind === 'instance') {
    return async () => {
      if (item.project_id && (!item.compute_host || activeView === 'projects')) {
        if (await navigateToProjectScopedSearchResult(item.project_id, 'instances', 'instances', item.id)) return;
      }
      closeGlobalSearch();
      if (item.id && item.compute_host) {
        await navigateToInstanceDetail(item.id, item.compute_host);
        return;
      }
      if (item.project_id) await navigateToProjectScopedSearchResult(item.project_id, 'instances', 'instances', item.id);
    };
  }
  if (item.kind === 'network') return async () => {
    if (await navigateToProjectScopedSearchResult(item.project_id, 'networking', 'networks', item.id, { networkingView: 'networks' })) return;
    closeGlobalSearch();
    await navigateToNetworkDetail(item.id);
  };
  if (item.kind === 'router') return async () => {
    if (await navigateToProjectScopedSearchResult(item.project_id, 'networking', 'routers', item.id, { networkingView: 'routers' })) return;
    closeGlobalSearch();
    await navigateToRouterDetail(item.id);
  };
  if (item.kind === 'port') return async () => {
    if (await navigateToProjectScopedSearchResult(item.project_id, 'networking', 'ports', item.id, { networkingView: 'ports' })) return;
    closeGlobalSearch();
    await navigateToPortDetail(item.id);
  };
  if (item.kind === 'loadbalancer') return async () => {
    if (await navigateToProjectScopedSearchResult(item.project_id, 'networking', 'loadbalancers', item.id, { networkingView: 'loadbalancers' })) return;
    closeGlobalSearch();
    await navigateToLoadBalancerDetail(item.id);
  };
  if (item.kind === 'securitygroup') {
    return async () => {
      if (await navigateToProjectScopedSearchResult(item.project_id, 'security', 'securitygroups', item.id)) return;
      closeGlobalSearch();
      switchNetworkingSection('securitygroups');
      await loadSecurityGroups();
      await selectSecurityGroup(item.id);
    };
  }
  if (item.kind === 'volume') {
    return async () => {
      if (await navigateToProjectScopedSearchResult(item.project_id, 'storage', 'volumes', item.id, { storageView: 'volumes' })) return;
      closeGlobalSearch();
      switchView('storage');
      switchStorageSection('openstack-volumes');
      await loadVolumes();
      await selectVolume(item.id);
    };
  }
  if (item.kind === 'floatingip') {
    return async () => {
      if (item.instance_id && item.compute_host) {
        closeGlobalSearch();
        await navigateToInstanceDetail(item.instance_id, item.compute_host);
        return;
      }
      if (await navigateToProjectScopedSearchResult(item.project_id, 'networking', 'floatingips', item.id, { networkingView: 'floatingips' })) return;
      closeGlobalSearch();
      if (item.port_id) await navigateToPortDetail(item.port_id);
    };
  }
  if (item.kind === 'kubernetes') {
    return async () => {
      closeGlobalSearch();
      const type = item.k8s_type;
      const key = item.k8s_key;
      if (!type || !key) return;
      if (['services', 'lbs', 'vpcs', 'subnets', 'vlans', 'providernetworks', 'providersubnets', 'ips', 'clusternetworks', 'networkdomains', 'gatewayclasses', 'gateways', 'httproutes'].includes(type)) {
        switchView('networking');
        switchNetworkingSection(type === 'services' ? 'k8s-services'
          : type === 'lbs' ? 'k8s-lbs'
          : type === 'vpcs' ? 'k8s-vpcs'
          : type === 'subnets' ? 'k8s-subnets'
          : type === 'vlans' ? 'k8s-vlans'
          : type === 'providernetworks' ? 'k8s-providernetworks'
          : type === 'providersubnets' ? 'k8s-providersubnets'
          : type === 'ips' ? 'k8s-ips'
          : type === 'clusternetworks' ? 'k8s-clusternetworks'
          : type === 'networkdomains' ? 'k8s-networkdomains'
          : type === 'gatewayclasses' ? 'k8s-gatewayclasses'
          : type === 'gateways' ? 'k8s-gateways'
          : 'k8s-httproutes');
      } else if (['pvcs', 'pvs', 'storagecsis'].includes(type)) {
        switchView('storage');
        switchStorageSection(type === 'pvcs' ? 'k8s-pvcs' : type === 'pvs' ? 'k8s-pvs' : 'k8s-csi');
      } else {
        switchView('kubernetes');
      }
      await selectK8sResource(type);
      selectK8sObject(type, key);
    };
  }
  return async () => {};
}

async function fetchRemoteGlobalSearch(query) {
  if (!hasOpenStackAuth() || String(query || '').trim().length < 2) {
    globalSearchState.remoteLoading = false;
    globalSearchState.remoteResults = [];
    globalSearchState.remoteError = '';
    globalSearchState.results = mergeGlobalSearchResults(globalSearchState.localResults, []);
    renderGlobalSearch();
    return;
  }
  const seq = ++globalSearchState.remoteSeq;
  globalSearchState.remoteLoading = true;
  globalSearchState.remoteError = '';
  renderGlobalSearch();
  try {
    const resp = await fetch(`/api/search?q=${encodeURIComponent(query)}&limit=${GLOBAL_SEARCH_LIMIT}`);
    const json = await resp.json();
    if (seq !== globalSearchState.remoteSeq) return;
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('OpenStack');
    if (!resp.ok || json.error) throw new Error(json.error || `HTTP ${resp.status}`);
    globalSearchState.remoteResults = (json.results || []).map((item) => ({
      ...item,
      key: `${item.kind}:${item.id || item.label}`,
      action: globalSearchActionForResult(item),
    }));
    globalSearchState.remoteError = '';
  } catch (err) {
    if (seq !== globalSearchState.remoteSeq) return;
    globalSearchState.remoteResults = [];
    globalSearchState.remoteError = String(err);
  } finally {
    if (seq !== globalSearchState.remoteSeq) return;
    globalSearchState.remoteLoading = false;
    globalSearchState.results = mergeGlobalSearchResults(globalSearchState.localResults, globalSearchState.remoteResults);
    renderGlobalSearch();
  }
}

function closeGlobalSearch(clearQuery = false) {
  globalSearchState.open = false;
  globalSearchState.activeIndex = 0;
  globalSearchState.localResults = [];
  globalSearchState.remoteResults = [];
  globalSearchState.results = [];
  globalSearchState.remoteLoading = false;
  globalSearchState.remoteError = '';
  const dropdown = globalSearchDropdown();
  if (dropdown) {
    dropdown.style.display = 'none';
    dropdown.innerHTML = '';
  }
  if (clearQuery) {
    globalSearchState.query = '';
    const input = globalSearchInput();
    if (input) input.value = '';
  }
}

function renderGlobalSearch() {
  const dropdown = globalSearchDropdown();
  if (!dropdown) return;
  const query = String(globalSearchState.query || '').trim();
  if (!globalSearchState.open) {
    dropdown.style.display = 'none';
    dropdown.innerHTML = '';
    return;
  }
  if (!query) {
    const history = loadGlobalSearchHistory();
    if (!history.length) {
      dropdown.style.display = 'none';
      dropdown.innerHTML = '';
      return;
    }
    dropdown.innerHTML = `<div class="global-search-section">
      <div class="global-search-section-title">Recent Searches <button class="global-search-clear" onclick="event.stopPropagation();clearGlobalSearchHistory()">Clear</button></div>
      ${history.map((item) => `<button class="global-search-item" onclick="setGlobalSearchQuery('${escAttr(item)}')">
        <span class="global-search-item-icon">⏱️</span>
        <span class="global-search-item-copy">
          <div class="global-search-item-label">${esc(item)}</div>
          <div class="global-search-item-subtext">Search again</div>
        </span>
      </button>`).join('')}
    </div><div class="global-search-meta">Press <strong>Cmd/Ctrl+K</strong> to jump here anytime.</div>`;
    dropdown.style.display = 'block';
    return;
  }
  const sections = {};
  for (const item of globalSearchState.results) {
    if (!sections[item.kind]) sections[item.kind] = [];
    sections[item.kind].push(item);
  }
  if (!globalSearchState.results.length) {
    dropdown.innerHTML = `<div class="global-search-empty">No results match <strong>${esc(query)}</strong>.</div><div class="global-search-meta">${globalSearchState.remoteLoading ? 'Searching more resources…' : globalSearchState.remoteError ? `Backend search failed: ${esc(globalSearchState.remoteError)}` : 'No local or backend results matched.'}</div>`;
    dropdown.style.display = 'block';
    return;
  }
  let activeCursor = 0;
  dropdown.innerHTML = Object.entries(sections).map(([kind, items]) => {
    return `<div class="global-search-section">
      <div class="global-search-section-title">${esc(globalSearchGroupLabel(kind))}</div>
      ${items.map((item) => {
        const index = activeCursor++;
        return `<button class="global-search-item ${index === globalSearchState.activeIndex ? 'active' : ''}" data-result-index="${index}" onclick="activateGlobalSearchResult(${index})">
          <span class="global-search-item-icon">${esc(globalSearchIcon(item.kind))}</span>
          <span class="global-search-item-copy">
            <div class="global-search-item-label">${highlightGlobalSearchText(item.label, query)}</div>
            <div class="global-search-item-subtext">${highlightGlobalSearchText(item.subtext || '', query)}</div>
          </span>
        </button>`;
      }).join('')}
    </div>`;
  }).join('') + `<div class="global-search-meta">${globalSearchState.remoteLoading ? 'Searching more resources…' : globalSearchState.remoteError ? `Backend search failed: ${esc(globalSearchState.remoteError)}` : hasOpenStackAuth() ? 'Showing local and server search results.' : 'Showing local results only.'}</div>`;
  dropdown.style.display = 'block';
  requestAnimationFrame(() => {
    dropdown.querySelector('.global-search-item.active')?.scrollIntoView({ block: 'nearest' });
  });
}

function updateGlobalSearch(query) {
  globalSearchState.query = query;
  const trimmed = String(query || '').trim();
  if (!trimmed) {
    closeGlobalSearch(false);
    return;
  }
  globalSearchState.remoteResults = [];
  globalSearchState.remoteError = '';
  globalSearchState.localResults = computeGlobalSearchResults(trimmed);
  globalSearchState.results = mergeGlobalSearchResults(globalSearchState.localResults, []);
  globalSearchState.activeIndex = 0;
  globalSearchState.open = true;
  renderGlobalSearch();
  if (globalSearchState.debounceTimer) clearTimeout(globalSearchState.debounceTimer);
  globalSearchState.debounceTimer = setTimeout(() => {
    fetchRemoteGlobalSearch(trimmed);
  }, 180);
}

async function activateGlobalSearchResult(index) {
  const item = globalSearchState.results[index];
  if (!item) return;
  rememberGlobalSearchQuery(globalSearchState.query);
  globalSearchState.activeIndex = index;
  renderGlobalSearch();
  await item.action();
}

function initGlobalSearch() {
  const input = globalSearchInput();
  const dropdown = globalSearchDropdown();
  if (!input || !dropdown) return;

  input.addEventListener('input', () => updateGlobalSearch(input.value));
  input.addEventListener('focus', () => {
    globalSearchState.open = true;
    if (String(input.value || '').trim()) {
      globalSearchState.open = true;
      globalSearchState.results = computeGlobalSearchResults(input.value);
    }
    renderGlobalSearch();
  });
  input.addEventListener('blur', () => {
    if (!String(input.value || '').trim()) {
      globalSearchState.query = '';
    }
  });
  input.addEventListener('keydown', async (event) => {
    if (!globalSearchState.open || !globalSearchState.results.length) {
      if (event.key === 'Escape') closeGlobalSearch(true);
      return;
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      globalSearchState.activeIndex = Math.min(globalSearchState.results.length - 1, globalSearchState.activeIndex + 1);
      renderGlobalSearch();
      return;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      globalSearchState.activeIndex = Math.max(0, globalSearchState.activeIndex - 1);
      renderGlobalSearch();
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      await activateGlobalSearchResult(globalSearchState.activeIndex);
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      closeGlobalSearch(true);
    }
  });

  document.addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && String(event.key || '').toLowerCase() === 'k') {
      event.preventDefault();
      focusGlobalSearchInput(true);
      return;
    }
    if (event.key === '/' && document.activeElement !== input) {
      const target = event.target;
      const tag = target?.tagName;
      const isEditable = target?.isContentEditable || tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
      if (!isEditable) {
        event.preventDefault();
        focusGlobalSearchInput(false);
      }
    }
  });

  document.addEventListener('mousedown', (event) => {
    const shell = input.closest('.top-search-shell');
    if (shell && shell.contains(event.target)) return;
    closeGlobalSearch(false);
  });
}
