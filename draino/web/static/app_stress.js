'use strict';

const STRESS_PROFILE_META = {
  'full-host-spread': { icon: '🧭' },
  'burst': { icon: '⚡' },
  'small-distribution': { icon: '🧪' },
};
const STRESS_PROFILE_STORAGE_KEY = 'vibeviewStressProfile';
const STRESS_FALLBACK_PROFILES = [
  { key: 'full-host-spread', label: 'Full Host Spread', description: 'Best-effort one VM per compute host for scheduler and placement validation.', icon: '🧭', default_vm_count: 1 },
  { key: 'burst', label: 'Burst', description: 'High-count VM launch test against shared network plumbing.', icon: '⚡', default_vm_count: 20 },
  { key: 'small-distribution', label: 'Small Distribution', description: 'Quick scheduler sanity test with a small spread set.', icon: '🧪', default_vm_count: 5 },
];

function stressProfiles() {
  return stressState.catalog?.profiles || STRESS_FALLBACK_PROFILES;
}

function stressProfileByKey(key) {
  return stressProfiles().find(profile => profile.key === key) || null;
}

function stressDraft(profileKey = stressState.profileKey) {
  if (!profileKey) return null;
  const profile = stressProfileByKey(profileKey);
  if (!stressState.drafts[profileKey]) {
    stressState.drafts[profileKey] = {
      loaded: false,
      error: null,
      imageId: '',
      flavorId: '',
      externalNetworkId: '',
      keypairMode: 'existing',
      keypairName: '',
      cidrMode: 'auto',
      cidr: '',
      vmCount: profile?.default_vm_count || 1,
    };
  }
  return stressState.drafts[profileKey];
}

function syncStressDraft(profileKey = stressState.profileKey) {
  const draft = stressDraft(profileKey);
  const env = stressState.env;
  const profile = stressProfileByKey(profileKey);
  if (!draft || !env) return draft;
  draft.vmCount = draft.vmCount || profile?.default_vm_count || 1;
  draft.imageId = draft.imageId || env.defaults?.image_id || env.images?.[0]?.id || '';
  draft.externalNetworkId = draft.externalNetworkId || env.defaults?.external_network_id || env.external_networks?.[0]?.id || '';
  draft.keypairMode = draft.keypairMode || env.defaults?.keypair_mode || 'existing';
  draft.keypairName = draft.keypairName || env.defaults?.keypair_name || env.defaults?.generated_keypair_name || '';
  draft.cidrMode = draft.cidrMode || env.defaults?.cidr_mode || 'auto';
  draft.cidr = draft.cidr || env.defaults?.cidr || '';
  const compatible = getCompatibleStressFlavors(profileKey);
  if (!compatible.some(flavor => flavor.id === draft.flavorId)) {
    draft.flavorId = env.defaults?.flavor_id && compatible.some(flavor => flavor.id === env.defaults.flavor_id)
      ? env.defaults.flavor_id
      : (compatible[0]?.id || '');
  }
  return draft;
}

function stressImageById(imageId) {
  return (stressState.env?.images || []).find(image => image.id === imageId) || null;
}

function stressStatusTagClass(status) {
  const text = String(status || '').toUpperCase();
  if (text.includes('COMPLETE') || text === 'ACTIVE') return 'green';
  if (text.includes('FAILED') || text.includes('ERROR')) return 'red';
  if (text.includes('IN_PROGRESS') || text === 'BUILD' || text === 'BUILDING') return 'yellow';
  return 'blue';
}

function getCompatibleStressFlavors(profileKey = stressState.profileKey) {
  const draft = stressDraft(profileKey);
  const image = stressImageById(draft?.imageId);
  const flavors = stressState.env?.flavors || [];
  if (!image) return flavors;
  const compatible = flavors.filter(flavor => flavor.disk_gb >= image.min_disk_gb && flavor.ram_mb >= image.min_ram_mb);
  return compatible.length ? compatible : flavors;
}

function stressFlavorLabel(flavor) {
  return `${flavor.name} - ${flavor.vcpus} vCPU - ${Math.round((flavor.ram_mb || 0) / 1024)} GB - ${flavor.disk_gb} GB`;
}

async function fetchStressJson(url, options) {
  const resp = await fetch(url, options);
  const contentType = resp.headers.get('content-type') || '';
  const text = await resp.text();
  if (!resp.ok) {
    throw new Error(text || `${resp.status} ${resp.statusText}`.trim());
  }
  if (!contentType.includes('application/json')) {
    throw new Error(text || `Expected JSON response from ${url}`);
  }
  try {
    return JSON.parse(text);
  } catch (_) {
    throw new Error(text || `Invalid JSON response from ${url}`);
  }
}

function stressNeedsPolling() {
  return activeView === 'stress' && (
    stressState.actionLoading ||
    stressState.actionKind === 'launch' ||
    stressState.actionKind === 'delete' ||
    Boolean(stressState.catalog?.guardrail?.active) ||
    Boolean(stressState.status?.active)
  );
}

async function pollStressStatusTick() {
  if (activeView !== 'stress') return;
  await loadStressCatalog(true);
  if (stressState.catalog?.guardrail?.active || stressState.status?.active || stressState.actionLoading) {
    await loadStressStatus(true);
  } else if (stressState.status?.active) {
    stressState.status = null;
    renderStressView();
  }
  if (!stressNeedsPolling()) stopStressStatusPolling();
}

function startStressStatusPolling() {
  if (!stressNeedsPolling()) return;
  if (stressStatusTimer) return;
  stressStatusTimer = setInterval(pollStressStatusTick, 5000);
}

function stopStressStatusPolling() {
  if (!stressStatusTimer) return;
  clearInterval(stressStatusTimer);
  stressStatusTimer = null;
}

async function loadStressCatalog(force = false) {
  if (stressState.catalogLoading) return;
  if (stressState.catalog && !force) {
    renderStressView();
    return;
  }
  stressState.catalogLoading = true;
  stressState.catalogError = null;
  if (!stressState.catalog) renderStressView();
  try {
    const json = await fetchStressJson('/api/stress/catalog');
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Nova');
    if (json.error) throw new Error(json.error);
    stressState.catalog = json.catalog;
    if (!stressState.profileKey) {
      const savedProfile = localStorage.getItem(STRESS_PROFILE_STORAGE_KEY) || '';
      if (savedProfile && (json.catalog?.profiles || []).some(profile => profile.key === savedProfile)) {
        stressState.profileKey = savedProfile;
      }
    }
    if (!stressState.profileKey) {
      stressState.profileKey = json.catalog?.profiles?.[0]?.key || '';
    }
    if (stressState.catalog?.guardrail?.active) {
      await loadStressStatus(true);
    }
  } catch (e) {
    stressState.catalogError = String(e);
  } finally {
    stressState.catalogLoading = false;
    if (stressNeedsPolling()) startStressStatusPolling();
    renderStressView();
  }
}

async function loadStressEnvironment(force = false) {
  if (stressState.envLoading) return;
  if (stressState.env && !force) {
    renderStressView();
    return;
  }
  stressState.envLoading = true;
  stressState.envError = null;
  stressState.actionError = null;
  renderStressView();
  try {
    const json = await fetchStressJson('/api/stress/environment');
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Nova');
    if (json.error) throw new Error(json.error);
    stressState.env = json.environment;
    stressState.catalog = {
      profiles: stressState.catalog?.profiles || [],
      guardrail: json.environment?.guardrail || stressState.catalog?.guardrail || null,
      limits: json.environment?.limits || stressState.catalog?.limits || null,
    };
    syncStressDraft();
    if (json.environment?.guardrail?.active) await loadStressStatus(true);
  } catch (e) {
    stressState.envError = String(e);
  } finally {
    stressState.envLoading = false;
    renderStressView();
  }
}

async function loadStressStatus(force = false) {
  if (stressState.statusLoading) return;
  if (stressState.status && !force) {
    renderStressView();
    return;
  }
  stressState.statusLoading = true;
  stressState.actionError = null;
  renderStressView();
  try {
    const json = await fetchStressJson('/api/stress/status');
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Nova');
    if (json.error) throw new Error(json.error);
    stressState.status = json.status;
  } catch (e) {
    stressState.actionError = String(e);
  } finally {
    stressState.statusLoading = false;
    if (stressNeedsPolling()) startStressStatusPolling();
    renderStressView();
  }
}

async function refreshStressView() {
  await loadStressCatalog(true);
  if (stressState.env) await loadStressEnvironment(true);
  if (stressState.catalog?.guardrail?.active || stressState.status?.active) {
    await loadStressStatus(true);
  }
}

async function loadSelectedStressTemplate() {
  if (stressState.envLoading || stressState.catalogLoading) return;
  await loadStressCatalog(true);
  await loadStressEnvironment(true);
  const draft = syncStressDraft();
  if (draft) {
    draft.loaded = true;
    draft.error = null;
  }
  if (stressState.catalog?.guardrail?.active || stressState.status?.active) {
    await loadStressStatus(true);
  }
  renderStressView();
}

function setStressProfile(profileKey) {
  stressState.profileKey = profileKey;
  localStorage.setItem(STRESS_PROFILE_STORAGE_KEY, profileKey);
  const profile = stressProfileByKey(profileKey);
  const draft = stressDraft(profileKey);
  if (profile && draft && !draft.vmCount) draft.vmCount = profile.default_vm_count || 1;
  renderStressView();
}

function setStressImage(imageId) {
  const draft = stressDraft();
  if (!draft) return;
  draft.imageId = imageId;
  const compatible = getCompatibleStressFlavors();
  if (!compatible.some(flavor => flavor.id === draft.flavorId)) {
    draft.flavorId = compatible[0]?.id || '';
  }
  renderStressView();
}

function setStressFlavor(flavorId) {
  const draft = stressDraft();
  if (!draft) return;
  draft.flavorId = flavorId;
  renderStressView();
}

function setStressKeypairMode(mode) {
  const draft = stressDraft();
  if (!draft) return;
  draft.keypairMode = mode;
  if (mode === 'auto') {
    draft.keypairName = stressState.env?.defaults?.generated_keypair_name || 'vibe-stress-key';
  } else if (!draft.keypairName) {
    draft.keypairName = stressState.env?.keypairs?.[0]?.name || '';
  }
  renderStressView();
}

function setStressKeypairName(name) {
  const draft = stressDraft();
  if (draft) draft.keypairName = name;
}

function setStressCidrMode(mode) {
  const draft = stressDraft();
  if (!draft) return;
  draft.cidrMode = mode;
  if (mode === 'auto') {
    draft.cidr = stressState.env?.defaults?.cidr || '';
  }
  renderStressView();
}

function setStressCidr(value) {
  const draft = stressDraft();
  if (draft) draft.cidr = value;
}

function setStressVmCount(value) {
  const draft = stressDraft();
  if (!draft) return;
  const numeric = Number(value);
  if (Number.isFinite(numeric) && numeric >= 1) draft.vmCount = Math.round(numeric);
}

function setStressExternalNetwork(value) {
  const draft = stressDraft();
  if (draft) draft.externalNetworkId = value;
}

async function launchStressTest() {
  if (stressState.actionLoading) return;
  const draft = syncStressDraft();
  if (!draft) return;
  stressState.actionLoading = true;
  stressState.actionKind = 'launch';
  stressState.actionError = null;
  renderStressView();
  try {
    const json = await fetchStressJson('/api/stress/launch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        profile: stressState.profileKey,
        vm_count: draft.vmCount,
        image_id: draft.imageId,
        flavor_id: draft.flavorId,
        keypair_mode: draft.keypairMode,
        keypair_name: draft.keypairName,
        cidr_mode: draft.cidrMode,
        cidr: draft.cidr,
        external_network_id: draft.externalNetworkId,
      }),
    });
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Nova');
    if (json.error) throw new Error(json.error);
    stressState.status = json.status;
    await loadStressCatalog(true);
    await loadStressStatus(true);
    startStressStatusPolling();
  } catch (e) {
    stressState.actionError = String(e);
    await loadStressCatalog(true);
    if (stressState.catalog?.guardrail?.active) {
      await loadStressStatus(true);
      startStressStatusPolling();
    }
  } finally {
    stressState.actionLoading = false;
    stressState.actionKind = '';
    if (stressNeedsPolling()) startStressStatusPolling();
    renderStressView();
  }
}

async function deleteStressTest() {
  if (stressState.actionLoading) return;
  stressState.actionLoading = true;
  stressState.actionKind = 'delete';
  stressState.actionError = null;
  renderStressView();
  try {
    const json = await fetchStressJson('/api/stress/delete', { method: 'POST' });
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Nova');
    if (json.error) throw new Error(json.error);
    stressState.status = null;
    await loadStressCatalog(true);
    startStressStatusPolling();
  } catch (e) {
    stressState.actionError = String(e);
  } finally {
    stressState.actionLoading = false;
    stressState.actionKind = '';
    if (stressNeedsPolling()) startStressStatusPolling();
    else stopStressStatusPolling();
    renderStressView();
  }
}

function renderStressProfileNav() {
  const sourceProfiles = stressProfiles();
  const currentKey = stressState.profileKey || sourceProfiles[0]?.key || '';
  return sourceProfiles.map(profile => {
    const selected = profile.key === currentKey ? ' active' : '';
    const icon = profile.icon || STRESS_PROFILE_META[profile.key]?.icon || '🧪';
    return `
      <button class="stress-profile-item${selected}" type="button" onclick="setStressProfile('${escAttr(profile.key)}')">
        <div class="stress-profile-ico">${esc(icon)}</div>
        <div>
          <div class="stress-profile-name">${esc(profile.label)}</div>
          <div class="stress-profile-sub">${esc(profile.description)}</div>
        </div>
      </button>
    `;
  }).join('');
}

function renderStressGuardrail() {
  const guardrail = stressState.catalog?.guardrail || stressState.env?.guardrail || { active: false, stack: null, message: 'No active stress stack detected.' };
  const stack = guardrail.stack;
  return `
    <section class="card">
      <div class="card-title"><span>Guardrail</span></div>
      <div class="card-body">
        <div class="stress-guard${guardrail.active ? ' active' : ''}">
          <div class="stress-guard-head">${guardrail.active ? 'Existing Active Test Detected' : 'No Active Stress Test'}</div>
          ${stack ? `
            <div class="guard-row"><span>Stack</span><span class="mono">${esc(stack.stack_name)}</span></div>
            <div class="guard-row"><span>Status</span><span>${esc(stack.status || 'UNKNOWN')}</span></div>
            <div class="guard-row"><span>Created</span><span>${esc(stack.created_at || '—')}</span></div>
            <div class="guard-row"><span>Updated</span><span>${esc(stack.updated_at || '—')}</span></div>
          ` : ''}
          <div class="guard-row"><span>Message</span><span>${esc(guardrail.message || '')}</span></div>
          ${guardrail.active ? `
            <div class="stress-guard-actions">
              <button class="btn danger" type="button" onclick="deleteStressTest()" ${stressState.actionLoading ? 'disabled' : ''}>${stressState.actionLoading && stressState.actionKind === 'delete' ? 'Deleting…' : 'Delete Existing Test'}</button>
              <button class="btn" type="button" onclick="loadStressStatus(true)" ${stressState.statusLoading ? 'disabled' : ''}>${stressState.statusLoading ? 'Refreshing…' : 'Load Stack Details'}</button>
            </div>
          ` : ''}
        </div>
      </div>
    </section>
  `;
}

function renderStressTrace() {
  const trace = stressState.catalog?.trace || [];
  return `
    <section class="card">
      <div class="card-title"><span>Action Trace</span></div>
      <div class="card-body report-table-wrap">
        <table class="report-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Action</th>
              <th>Stage</th>
              <th>Status</th>
              <th>Message</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            ${trace.length ? trace.map(item => `
              <tr>
                <td class="mono">${esc(item.at || '—')}</td>
                <td>${esc(item.action || '—')}</td>
                <td>${esc(item.stage || '—')}</td>
                <td><span class="report-tag ${item.status === 'bad' ? 'red' : item.status === 'warn' ? 'yellow' : item.status === 'good' ? 'green' : 'blue'}">${esc(item.status || 'info')}</span></td>
                <td>${esc(item.message || '—')}</td>
                <td class="mono">${esc(item.detail || '—')}</td>
              </tr>
            `).join('') : `<tr><td colspan="6" style="text-align:center;color:var(--dim)">No stress action trace yet.</td></tr>`}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function renderStressLaunchCard() {
  const env = stressState.env || {};
  const draft = syncStressDraft();
  const profile = stressProfileByKey(stressState.profileKey);
  const compatibleFlavors = getCompatibleStressFlavors();
  const image = stressImageById(draft?.imageId);
  const externalNetworks = env.external_networks || [];
  const flavorHint = image
    ? `Showing flavors with at least ${image.min_disk_gb} GB disk and ${Math.round((image.min_ram_mb || 0) / 1024)} GB RAM.`
    : 'Select an image to filter flavors.';
  const autoKeyName = env.defaults?.generated_keypair_name || 'vibe-stress-key';
  const launchBlocked = Boolean(env.guardrail?.active) || stressState.actionLoading;
  return `
    <div class="card">
      <div class="card-title"><span>Launch Parameters</span></div>
      <div class="card-body">
        <div class="stress-launch-note">
          ${esc(profile?.label || 'Selected template')} targets ${esc(String(draft?.vmCount || profile?.default_vm_count || 1))} VMs across ${esc(String(env.limits?.compute_count || 0))} visible compute hosts.
        </div>
        <div class="stress-form-grid">
          <div class="field">
            <label>VM Count</label>
            <input type="number" min="1" value="${esc(String(draft?.vmCount || profile?.default_vm_count || 1))}" oninput="setStressVmCount(this.value)">
          </div>
          <div class="field">
            <label>Image</label>
            <select onchange="setStressImage(this.value)">
              ${(env.images || []).map(item => `
                <option value="${escAttr(item.id)}"${item.id === draft?.imageId ? ' selected' : ''}>${esc(item.name)}</option>
              `).join('')}
            </select>
          </div>
          <div class="field">
            <label>Flavor</label>
            <select onchange="setStressFlavor(this.value)">
              ${compatibleFlavors.map(item => `
                <option value="${escAttr(item.id)}"${item.id === draft?.flavorId ? ' selected' : ''}>${esc(stressFlavorLabel(item))}</option>
              `).join('')}
            </select>
            <div class="stress-field-note">${esc(flavorHint)}</div>
          </div>
          <div class="field">
            <label>External Network</label>
            <select onchange="setStressExternalNetwork(this.value)">
              ${externalNetworks.map(item => `
                <option value="${escAttr(item.id)}"${item.id === draft?.externalNetworkId ? ' selected' : ''}>${esc(item.name)}</option>
              `).join('')}
            </select>
          </div>
          <div class="field">
            <label>Keypair</label>
            <div class="stress-inline-toggle">
              <button type="button" class="${draft?.keypairMode === 'existing' ? 'active' : ''}" onclick="setStressKeypairMode('existing')">Existing</button>
              <button type="button" class="${draft?.keypairMode === 'auto' ? 'active' : ''}" onclick="setStressKeypairMode('auto')">Auto-generate</button>
            </div>
            ${draft?.keypairMode === 'existing' ? `
              <select onchange="setStressKeypairName(this.value)">
                ${(env.keypairs || []).map(item => `
                  <option value="${escAttr(item.name)}"${item.name === draft?.keypairName ? ' selected' : ''}>${esc(item.name)}</option>
                `).join('')}
              </select>
            ` : `
              <input type="text" value="${esc(autoKeyName)}" readonly>
            `}
          </div>
          <div class="field">
            <label>CIDR</label>
            <div class="stress-inline-toggle">
              <button type="button" class="${draft?.cidrMode === 'auto' ? 'active' : ''}" onclick="setStressCidrMode('auto')">Auto</button>
              <button type="button" class="${draft?.cidrMode === 'manual' ? 'active' : ''}" onclick="setStressCidrMode('manual')">Custom</button>
            </div>
            <input type="text" value="${esc(draft?.cidr || '')}" ${draft?.cidrMode === 'auto' ? 'readonly' : ''} oninput="setStressCidr(this.value)">
          </div>
        </div>
        <div class="stress-launch-actions">
          <button class="btn primary" type="button" onclick="launchStressTest()" ${launchBlocked ? 'disabled' : ''}>${stressState.actionLoading && stressState.actionKind === 'launch' ? 'Launching…' : 'Launch Test Stack'}</button>
          <button class="btn" type="button" onclick="refreshStressView()" ${stressState.actionLoading ? 'disabled' : ''}>Refresh Status</button>
        </div>
        ${stressState.actionError ? `<div class="stress-action-error">${esc(stressState.actionError)}</div>` : ''}
      </div>
    </div>
  `;
}

function renderStressEmptyState() {
  const profile = stressProfileByKey(stressState.profileKey) || STRESS_FALLBACK_PROFILES[0];
  return `
    <div class="stress-shell">
      <aside class="stress-nav">
        <div class="sidebar-head">Stress Profiles</div>
        <div class="nav-group">Templates</div>
        <div class="stress-nav-items">${renderStressProfileNav()}</div>
      </aside>
      <div class="stress-content">
        <section class="report-header-card">
          <div class="report-head-top">
            <div>
              <div class="report-title">Heat Stress Test Console</div>
              <div class="report-subtitle">Disposable Heat-driven infrastructure tests for scheduler, networking, and control-plane timing. One active test stack is allowed at a time.</div>
            </div>
          </div>
          <div class="report-meta-row">
            <span class="meta-pill">Mode: live orchestration</span>
            <span class="meta-pill">Stack prefix: vibe-stress-</span>
            <span class="meta-pill">Guardrail: one active test</span>
            <span class="meta-pill">Cleanup: Heat stack delete</span>
          </div>
        </section>
        <section class="stress-launch-grid">
          <section class="report-launch-card stress-landing-card">
            <div class="report-launch-shell">
              <div class="report-launch-icon">${esc(profile.icon || '🧪')}</div>
              <div class="report-launch-copy">
                <div class="report-launch-kicker">Stress Template</div>
                <div class="report-launch-title">${esc(profile.label)}</div>
                <div class="report-launch-subtitle">${esc(profile.description)}</div>
                <div class="report-launch-text">
                  OpenStack discovery for images, flavors, keypairs, and networks is intentionally manual here so the page opens instantly.
                  Default target: ${esc(String(profile.default_vm_count || 1))} VMs. Full Host Spread expands to your visible compute count after details load.
                </div>
                <div class="report-launch-pills">
                  <span class="meta-pill">Template-first landing</span>
                  <span class="meta-pill">Manual discovery</span>
                  <span class="meta-pill">No stored data</span>
                </div>
                <div class="report-launch-actions">
                  <button class="report-launch-btn" type="button" onclick="loadSelectedStressTemplate()" ${stressState.envLoading || stressState.catalogLoading ? 'disabled' : ''}>${stressState.envLoading || stressState.catalogLoading ? 'Loading Template Details…' : `Load ${esc(profile.label)}`}</button>
                </div>
              </div>
            </div>
          </section>
        </section>
      </div>
    </div>
  `;
}

function renderStressTemplateLaunchState() {
  const profile = stressProfileByKey(stressState.profileKey) || stressState.catalog?.profiles?.[0] || null;
  const guardrail = stressState.catalog?.guardrail || stressState.env?.guardrail || { active: false };
  return `
    <section class="report-launch-card">
      <div class="report-launch-shell">
        <div class="report-launch-icon">${esc(profile?.icon || '🧪')}</div>
        <div class="report-launch-copy">
          <div class="report-launch-kicker">Stress Template</div>
          <div class="report-launch-title">${esc(profile?.label || 'Heat Stress Test Console')}</div>
          <div class="report-launch-subtitle">${esc(profile?.description || 'Disposable Heat-driven infrastructure tests for scheduler, networking, and control-plane timing.')}</div>
          <div class="report-launch-text">
            Template selection is lightweight. Full launch parameters are loaded only when you explicitly open this template,
            since image, flavor, keypair, and network discovery can be expensive. Default target:
            ${esc(String(profile?.default_vm_count || 1))} VMs across ${esc(String(stressState.catalog?.limits?.compute_count || 0))} visible compute hosts.
          </div>
          <div class="report-launch-pills">
            <span class="meta-pill">Manual details load</span>
            <span class="meta-pill">No stored data</span>
            <span class="meta-pill">${guardrail.active ? 'Guardrail active' : 'Ready to configure'}</span>
          </div>
          <div class="report-launch-actions">
            <button class="report-launch-btn" type="button" onclick="loadSelectedStressTemplate()" ${stressState.envLoading || stressState.catalogLoading ? 'disabled' : ''}>${stressState.envLoading || stressState.catalogLoading ? 'Loading Template Details…' : `Load ${esc(profile?.label || 'Template')}`}</button>
          </div>
          ${stressState.envLoading || stressState.catalogLoading ? `
            <div class="stress-template-note" aria-live="polite">
              <span class="stress-action-spinner">↻</span>
              <span>Loading shared cloud options and guardrail details for the selected template.</span>
            </div>
          ` : ''}
          ${guardrail.active ? `
            <div class="stress-template-note">
              An active Heat stack already exists. Stack details and delete controls can be loaded from the live environment without re-running discovery.
            </div>
          ` : ''}
          ${stressState.envError ? `<div class="stress-action-error">${esc(stressState.envError)}</div>` : ''}
        </div>
      </div>
    </section>
  `;
}

function renderStressSummarySection(status) {
  const test = status.test || {};
  const summary = status.summary || {};
  return `
    <section class="report-hero-grid">
      ${renderCapacityHero('Stack Status', test.status || 'unknown', stressStatusTagClass(test.status) === 'red' ? 'bad' : stressStatusTagClass(test.status) === 'yellow' ? 'warn' : 'good', 'Heat stack lifecycle state')}
      ${renderCapacityHero('Plumbing Time', summary.plumbing_elapsed || '—', 'good', 'Network, subnet, router, and interface attachment')}
      ${renderCapacityHero('Avg VM Build', summary.avg_vm_build || '—', 'good', 'Server create requested to ACTIVE')}
      ${renderCapacityHero('P95 VM Build', summary.p95_vm_build || '—', 'warn', 'Tail latency for slower builds')}
    </section>

    <section class="stress-meta-grid">
      <div class="card">
        <div class="card-title"><span>Summary</span></div>
        <div class="card-body">
          <table class="summary-table">
            <tr><td>Stack Name</td><td class="mono">${esc(test.stack_name || '—')}</td></tr>
            <tr><td>Profile</td><td>${esc(test.profile || '—')}</td></tr>
            <tr><td>Requested VMs</td><td>${esc(String(test.requested_vms ?? '—'))}</td></tr>
            <tr><td>Created VMs</td><td>${esc(String(test.created_vms ?? '—'))}</td></tr>
            <tr><td>Total Stack Time</td><td>${esc(summary.stack_elapsed || '—')}</td></tr>
            <tr><td>Slowest VM Build</td><td>${esc(summary.slowest_vm_build || '—')}</td></tr>
          </table>
        </div>
      </div>
      <div class="card">
        <div class="card-title"><span>Actions</span></div>
        <div class="card-body note">
          This stack is live and tracked directly from Heat and Nova. Delete it before launching a new test run.
          <div class="stress-stack-actions">
            <button class="btn danger" type="button" onclick="deleteStressTest()" ${stressState.actionLoading ? 'disabled' : ''}>${stressState.actionLoading && stressState.actionKind === 'delete' ? 'Deleting…' : 'Delete Existing Test'}</button>
            <button class="btn" type="button" onclick="loadStressStatus(true)" ${stressState.statusLoading ? 'disabled' : ''}>${stressState.statusLoading ? 'Refreshing…' : 'Refresh Status'}</button>
          </div>
        </div>
      </div>
    </section>
  `;
}

function renderStressResourceTable(resources) {
  return `
    <section class="card">
      <div class="card-title"><span>Resource Timing</span></div>
      <div class="card-body report-table-wrap">
        <table class="report-table">
          <thead>
            <tr>
              <th>Type</th>
              <th>Logical Name</th>
              <th>Physical ID</th>
              <th>Status</th>
              <th>Elapsed</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            ${resources.length ? resources.map(item => `
              <tr>
                <td>${esc(item.type || '—')}</td>
                <td class="mono">${esc(item.logical_name || '—')}</td>
                <td class="mono">${esc(item.physical_id || '—')}</td>
                <td><span class="report-tag ${stressStatusTagClass(item.status)}">${esc(item.status || 'UNKNOWN')}</span></td>
                <td>${esc(item.elapsed || '—')}</td>
                <td>${esc(item.notes || '—')}</td>
              </tr>
            `).join('') : `<tr><td colspan="6" style="text-align:center;color:var(--dim)">No stack resources available.</td></tr>`}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function renderStressServerTable(servers) {
  return `
    <section class="card">
      <div class="card-title"><span>VM Timing</span></div>
      <div class="card-body report-table-wrap">
        <table class="report-table">
          <thead>
            <tr>
              <th>VM</th>
              <th>Server ID</th>
              <th>Host</th>
              <th>Status</th>
              <th>Elapsed</th>
              <th>IP</th>
            </tr>
          </thead>
          <tbody>
            ${servers.length ? servers.map(item => `
              <tr>
                <td class="mono">${esc(item.name || '—')}</td>
                <td class="mono">${esc(item.server_id || '—')}</td>
                <td class="mono">${esc(item.host || '—')}</td>
                <td><span class="report-tag ${stressStatusTagClass(item.status)}">${esc(item.status || 'UNKNOWN')}</span></td>
                <td>${esc(item.elapsed || '—')}</td>
                <td>${esc(item.ip || '—')}</td>
              </tr>
            `).join('') : `<tr><td colspan="6" style="text-align:center;color:var(--dim)">No VM rows available.</td></tr>`}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function renderStressDistributionTable(distribution) {
  return `
    <section class="card">
      <div class="card-title"><span>VM Distribution</span></div>
      <div class="card-body report-table-wrap">
        <table class="report-table">
          <thead>
            <tr>
              <th>Host</th>
              <th>VM Count</th>
              <th>Share</th>
            </tr>
          </thead>
          <tbody>
            ${distribution.length ? distribution.map(item => `
              <tr>
                <td class="mono">${esc(item.host || '—')}</td>
                <td>${esc(String(item.vm_count || 0))}</td>
                <td>${renderPercentValue(item.share_pct)}</td>
              </tr>
            `).join('') : `<tr><td colspan="3" style="text-align:center;color:var(--dim)">No host distribution available yet.</td></tr>`}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function renderStressView() {
  const wrap = document.getElementById('stress-wrap');
  if (!wrap) return;
  if (!stressState.catalog && !stressState.catalogLoading && !stressState.catalogError) {
    wrap.innerHTML = renderStressEmptyState();
    return;
  }
  if (stressState.catalogError && !stressState.catalog) {
    wrap.innerHTML = `
      <section class="report-launch-card">
        <div class="report-launch-shell">
          <div class="report-launch-icon">⛔</div>
          <div class="report-launch-copy">
            <div class="report-launch-kicker">Stress Console</div>
            <div class="report-launch-title">Unable to load stress templates</div>
            <div class="report-launch-text">${esc(stressState.catalogError)}</div>
            <div class="report-launch-actions">
              <button class="report-launch-btn" type="button" onclick="loadStressCatalog(true)">Retry</button>
            </div>
          </div>
        </div>
      </section>
    `;
    return;
  }
  const env = stressState.env || {};
  const profile = stressProfileByKey(stressState.profileKey) || stressState.catalog?.profiles?.[0] || STRESS_FALLBACK_PROFILES[0];
  const draft = stressDraft();
  const status = stressState.status;
  const showLoadedGrids = Boolean(draft?.loaded || status?.active);
  wrap.innerHTML = `
    <div class="stress-shell">
      <aside class="stress-nav">
        <div class="sidebar-head">Stress Profiles</div>
        <div class="nav-group">Templates</div>
        <div class="stress-nav-items">${renderStressProfileNav()}</div>
      </aside>
      <div class="stress-content">
        <section class="report-header-card">
          <div class="report-head-top">
            <div>
              <div class="report-title">Heat Stress Test Console</div>
              <div class="report-subtitle">Disposable Heat-driven infrastructure tests for scheduler, networking, and control-plane timing. One active test stack is allowed at a time.</div>
            </div>
          </div>
          <div class="report-meta-row">
            <span class="meta-pill">Mode: live orchestration</span>
            <span class="meta-pill">Stack prefix: ${esc(stressState.catalog?.guardrail?.stack_prefix || env.guardrail?.stack_prefix || 'vibe-stress-')}</span>
            <span class="meta-pill">Guardrail: one active test</span>
            <span class="meta-pill">Cleanup: Heat stack delete</span>
            <span class="report-action-pills">
              <button class="report-action-pill${stressState.catalogLoading || stressState.envLoading || stressState.statusLoading ? ' active' : ''}" type="button" onclick="refreshStressView()" title="Refresh stress view">
                <span class="report-refresh-icon${stressState.catalogLoading || stressState.envLoading || stressState.statusLoading ? ' active' : ''}">↻</span>
              </button>
            </span>
          </div>
        </section>
        ${showLoadedGrids ? `
          <section class="stress-launch-grid">
            ${draft?.loaded ? renderStressLaunchCard() : renderStressTemplateLaunchState()}
            ${renderStressGuardrail()}
          </section>
          ${renderStressTrace()}
          <section class="card">
            <div class="card-title"><span>Notes</span></div>
            <div class="card-body note">
              Launches are orchestrated through a single Heat stack with clear naming and one-active-test guardrails.
              Timing and distribution are derived live from Heat resources and Nova server state.
            </div>
          </section>
        ` : `
          <section class="stress-launch-grid">
            ${renderStressTemplateLaunchState()}
            ${renderStressGuardrail()}
          </section>
          ${renderStressTrace()}
        `}
        ${status?.active ? renderStressSummarySection(status) : ''}
        ${status?.active ? renderStressResourceTable(status.resources || []) : ''}
        ${status?.active ? renderStressServerTable(status.servers || []) : ''}
        ${status?.active ? renderStressDistributionTable(status.distribution || []) : ''}
      </div>
    </div>
  `;
}
