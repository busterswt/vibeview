'use strict';

const STRESS_PROFILE_META = {
  'full-host-spread': { icon: '🧭' },
  'burst': { icon: '⚡' },
  'small-distribution': { icon: '🧪' },
};

function stressProfileByKey(key) {
  return (stressState.options?.profiles || []).find(profile => profile.key === key) || null;
}

function stressImageById(imageId) {
  return (stressState.options?.images || []).find(image => image.id === imageId) || null;
}

function stressStatusTagClass(status) {
  const text = String(status || '').toUpperCase();
  if (text.includes('COMPLETE') || text === 'ACTIVE') return 'green';
  if (text.includes('FAILED') || text.includes('ERROR')) return 'red';
  if (text.includes('IN_PROGRESS') || text === 'BUILD' || text === 'BUILDING') return 'yellow';
  return 'blue';
}

function getCompatibleStressFlavors() {
  const image = stressImageById(stressState.imageId);
  const flavors = stressState.options?.flavors || [];
  if (!image) return flavors;
  const compatible = flavors.filter(flavor => flavor.disk_gb >= image.min_disk_gb && flavor.ram_mb >= image.min_ram_mb);
  return compatible.length ? compatible : flavors;
}

function stressFlavorLabel(flavor) {
  return `${flavor.name} - ${flavor.vcpus} vCPU - ${Math.round((flavor.ram_mb || 0) / 1024)} GB - ${flavor.disk_gb} GB`;
}

function syncStressDefaults(options) {
  const defaults = options.defaults || {};
  stressState.profileKey = stressState.profileKey || defaults.profile || options.profiles?.[0]?.key || '';
  stressState.imageId = stressState.imageId || defaults.image_id || options.images?.[0]?.id || '';
  stressState.externalNetworkId = stressState.externalNetworkId || defaults.external_network_id || options.external_networks?.[0]?.id || '';
  const compatible = getCompatibleStressFlavors();
  if (!compatible.some(flavor => flavor.id === stressState.flavorId)) {
    stressState.flavorId = defaults.flavor_id && compatible.some(flavor => flavor.id === defaults.flavor_id)
      ? defaults.flavor_id
      : (compatible[0]?.id || '');
  }
  stressState.keypairMode = stressState.keypairMode || defaults.keypair_mode || 'existing';
  stressState.keypairName = stressState.keypairName || defaults.keypair_name || defaults.generated_keypair_name || '';
  stressState.cidrMode = stressState.cidrMode || defaults.cidr_mode || 'auto';
  stressState.cidr = stressState.cidr || defaults.cidr || '';
  if (!stressState.vmCount || stressState.vmCount < 1) {
    stressState.vmCount = defaults.vm_count || 1;
  }
}

async function loadStressOptions(force = false) {
  if (stressState.loading) return;
  if (stressState.options && !force) {
    renderStressView();
    return;
  }
  stressState.loading = true;
  stressState.error = null;
  stressState.actionError = null;
  renderStressView();
  try {
    const resp = await fetch('/api/stress/options');
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Nova');
    if (json.error) throw new Error(json.error);
    stressState.options = json.options;
    syncStressDefaults(json.options || {});
    if (json.options?.guardrail?.active) await loadStressStatus(true);
  } catch (e) {
    stressState.error = String(e);
  } finally {
    stressState.loading = false;
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
    const resp = await fetch('/api/stress/status');
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Nova');
    if (json.error) throw new Error(json.error);
    stressState.status = json.status;
  } catch (e) {
    stressState.actionError = String(e);
  } finally {
    stressState.statusLoading = false;
    renderStressView();
  }
}

async function refreshStressView() {
  await loadStressOptions(true);
  if (stressState.options?.guardrail?.active || stressState.status?.active) {
    await loadStressStatus(true);
  }
}

function setStressProfile(profileKey) {
  if (!stressState.options) return;
  stressState.profileKey = profileKey;
  const profile = stressProfileByKey(profileKey);
  if (profile) stressState.vmCount = profile.default_vm_count || 1;
  renderStressView();
}

function setStressImage(imageId) {
  stressState.imageId = imageId;
  const compatible = getCompatibleStressFlavors();
  if (!compatible.some(flavor => flavor.id === stressState.flavorId)) {
    stressState.flavorId = compatible[0]?.id || '';
  }
  renderStressView();
}

function setStressFlavor(flavorId) {
  stressState.flavorId = flavorId;
  renderStressView();
}

function setStressKeypairMode(mode) {
  stressState.keypairMode = mode;
  if (mode === 'auto') {
    stressState.keypairName = stressState.options?.defaults?.generated_keypair_name || 'vibe-stress-key';
  } else if (!stressState.keypairName) {
    stressState.keypairName = stressState.options?.keypairs?.[0]?.name || '';
  }
  renderStressView();
}

function setStressKeypairName(name) {
  stressState.keypairName = name;
}

function setStressCidrMode(mode) {
  stressState.cidrMode = mode;
  if (mode === 'auto') {
    stressState.cidr = stressState.options?.defaults?.cidr || '';
  }
  renderStressView();
}

function setStressCidr(value) {
  stressState.cidr = value;
}

function setStressVmCount(value) {
  const numeric = Number(value);
  if (Number.isFinite(numeric) && numeric >= 1) stressState.vmCount = Math.round(numeric);
}

function setStressExternalNetwork(value) {
  stressState.externalNetworkId = value;
}

async function launchStressTest() {
  if (stressState.actionLoading) return;
  stressState.actionLoading = true;
  stressState.actionError = null;
  renderStressView();
  try {
    const resp = await fetch('/api/stress/launch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        profile: stressState.profileKey,
        vm_count: stressState.vmCount,
        image_id: stressState.imageId,
        flavor_id: stressState.flavorId,
        keypair_mode: stressState.keypairMode,
        keypair_name: stressState.keypairName,
        cidr_mode: stressState.cidrMode,
        cidr: stressState.cidr,
        external_network_id: stressState.externalNetworkId,
      }),
    });
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Nova');
    if (json.error) throw new Error(json.error);
    stressState.status = json.status;
    await loadStressOptions(true);
    await loadStressStatus(true);
  } catch (e) {
    stressState.actionError = String(e);
  } finally {
    stressState.actionLoading = false;
    renderStressView();
  }
}

async function deleteStressTest() {
  if (stressState.actionLoading) return;
  stressState.actionLoading = true;
  stressState.actionError = null;
  renderStressView();
  try {
    const resp = await fetch('/api/stress/delete', { method: 'POST' });
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Nova');
    if (json.error) throw new Error(json.error);
    stressState.status = null;
    await loadStressOptions(true);
  } catch (e) {
    stressState.actionError = String(e);
  } finally {
    stressState.actionLoading = false;
    renderStressView();
  }
}

function renderStressProfileNav() {
  const profiles = stressState.options?.profiles || [];
  return profiles.map(profile => {
    const selected = profile.key === stressState.profileKey ? ' active' : '';
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
  const guardrail = stressState.options?.guardrail || { active: false, stack: null, message: 'No active stress stack detected.' };
  const stack = guardrail.stack;
  return `
    <div class="card">
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
        </div>
      </div>
    </div>
  `;
}

function renderStressLaunchCard() {
  const options = stressState.options;
  const profile = stressProfileByKey(stressState.profileKey);
  const compatibleFlavors = getCompatibleStressFlavors();
  const image = stressImageById(stressState.imageId);
  const externalNetworks = options?.external_networks || [];
  const flavorHint = image
    ? `Showing flavors with at least ${image.min_disk_gb} GB disk and ${Math.round((image.min_ram_mb || 0) / 1024)} GB RAM.`
    : 'Select an image to filter flavors.';
  const autoKeyName = options?.defaults?.generated_keypair_name || 'vibe-stress-key';
  const launchBlocked = Boolean(options?.guardrail?.active) || stressState.actionLoading;
  return `
    <div class="card">
      <div class="card-title"><span>Launch Parameters</span></div>
      <div class="card-body">
        <div class="stress-form-grid">
          <div class="field">
            <label>VM Count</label>
            <input type="number" min="1" value="${esc(String(stressState.vmCount || profile?.default_vm_count || 1))}" oninput="setStressVmCount(this.value)">
          </div>
          <div class="field">
            <label>Image</label>
            <select onchange="setStressImage(this.value)">
              ${(options?.images || []).map(item => `
                <option value="${escAttr(item.id)}"${item.id === stressState.imageId ? ' selected' : ''}>${esc(item.name)}</option>
              `).join('')}
            </select>
          </div>
          <div class="field">
            <label>Flavor</label>
            <select onchange="setStressFlavor(this.value)">
              ${compatibleFlavors.map(item => `
                <option value="${escAttr(item.id)}"${item.id === stressState.flavorId ? ' selected' : ''}>${esc(stressFlavorLabel(item))}</option>
              `).join('')}
            </select>
            <div class="stress-field-note">${esc(flavorHint)}</div>
          </div>
          <div class="field">
            <label>External Network</label>
            <select onchange="setStressExternalNetwork(this.value)">
              ${externalNetworks.map(item => `
                <option value="${escAttr(item.id)}"${item.id === stressState.externalNetworkId ? ' selected' : ''}>${esc(item.name)}</option>
              `).join('')}
            </select>
          </div>
          <div class="field">
            <label>Keypair</label>
            <div class="stress-inline-toggle">
              <button type="button" class="${stressState.keypairMode === 'existing' ? 'active' : ''}" onclick="setStressKeypairMode('existing')">Existing</button>
              <button type="button" class="${stressState.keypairMode === 'auto' ? 'active' : ''}" onclick="setStressKeypairMode('auto')">Auto-generate</button>
            </div>
            ${stressState.keypairMode === 'existing' ? `
              <select onchange="setStressKeypairName(this.value)">
                ${(options?.keypairs || []).map(item => `
                  <option value="${escAttr(item.name)}"${item.name === stressState.keypairName ? ' selected' : ''}>${esc(item.name)}</option>
                `).join('')}
              </select>
            ` : `
              <input type="text" value="${esc(autoKeyName)}" readonly>
            `}
          </div>
          <div class="field">
            <label>CIDR</label>
            <div class="stress-inline-toggle">
              <button type="button" class="${stressState.cidrMode === 'auto' ? 'active' : ''}" onclick="setStressCidrMode('auto')">Auto</button>
              <button type="button" class="${stressState.cidrMode === 'manual' ? 'active' : ''}" onclick="setStressCidrMode('manual')">Custom</button>
            </div>
            <input type="text" value="${esc(stressState.cidr || '')}" ${stressState.cidrMode === 'auto' ? 'readonly' : ''} oninput="setStressCidr(this.value)">
          </div>
        </div>
        <div class="stress-launch-actions">
          <button class="btn primary" type="button" onclick="launchStressTest()" ${launchBlocked ? 'disabled' : ''}>${stressState.actionLoading ? 'Launching…' : 'Launch Test Stack'}</button>
          <button class="btn" type="button" onclick="refreshStressView()" ${stressState.actionLoading ? 'disabled' : ''}>Refresh Status</button>
        </div>
        ${stressState.actionError ? `<div class="stress-action-error">${esc(stressState.actionError)}</div>` : ''}
      </div>
    </div>
  `;
}

function renderStressEmptyState() {
  return `
    <section class="report-launch-card">
      <div class="report-launch-shell">
        <div class="report-launch-icon">🧪</div>
        <div class="report-launch-copy">
          <div class="report-launch-kicker">Stress Console</div>
          <div class="report-launch-title">Heat Stress Test Console</div>
          <div class="report-launch-subtitle">Disposable Heat-driven infrastructure tests for scheduler, networking, and control-plane timing.</div>
          <div class="report-launch-text">Load live OpenStack options to configure a test profile, choose an image/flavor/keypair, and review the one-stack guardrail.</div>
          <div class="report-launch-actions">
            <button class="report-launch-btn" type="button" onclick="loadStressOptions(true)">Load Stress Options</button>
          </div>
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

    <section class="two-col">
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
            <button class="btn danger" type="button" onclick="deleteStressTest()" ${stressState.actionLoading ? 'disabled' : ''}>Delete Existing Test</button>
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
  if (!stressState.options && !stressState.loading && !stressState.error) {
    wrap.innerHTML = renderStressEmptyState();
    return;
  }
  if (stressState.loading && !stressState.options) {
    wrap.innerHTML = `<div class="data-view-wrap"><div style="text-align:center;padding:40px 0;color:var(--dim)">Loading stress options…</div></div>`;
    return;
  }
  if (stressState.error && !stressState.options) {
    wrap.innerHTML = `
      <section class="report-launch-card">
        <div class="report-launch-shell">
          <div class="report-launch-icon">⛔</div>
          <div class="report-launch-copy">
            <div class="report-launch-kicker">Stress Console</div>
            <div class="report-launch-title">Unable to load stress options</div>
            <div class="report-launch-text">${esc(stressState.error)}</div>
            <div class="report-launch-actions">
              <button class="report-launch-btn" type="button" onclick="loadStressOptions(true)">Retry</button>
            </div>
          </div>
        </div>
      </section>
    `;
    return;
  }
  const options = stressState.options || {};
  const profile = stressProfileByKey(stressState.profileKey) || options.profiles?.[0] || {};
  const status = stressState.status;
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
            <span class="meta-pill">Stack prefix: ${esc(options.guardrail?.stack_prefix || 'vibe-stress-')}</span>
            <span class="meta-pill">Guardrail: one active test</span>
            <span class="meta-pill">Cleanup: Heat stack delete</span>
            <span class="report-action-pills">
              <button class="report-action-pill${stressState.loading || stressState.statusLoading ? ' active' : ''}" type="button" onclick="refreshStressView()" title="Refresh stress view">
                <span class="report-refresh-icon${stressState.loading || stressState.statusLoading ? ' active' : ''}">↻</span>
              </button>
            </span>
          </div>
        </section>
        <section class="stress-launch-grid">
          ${renderStressLaunchCard()}
          ${renderStressGuardrail()}
        </section>
        <section class="two-col">
          <div class="card">
            <div class="card-title"><span>Selected Profile</span></div>
            <div class="card-body report-kv-stack">
              <div class="mrow"><span class="ml">Profile</span><span class="mv">${esc(profile.label || '—')}</span></div>
              <div class="mrow"><span class="ml">Description</span><span class="mv">${esc(profile.description || '—')}</span></div>
              <div class="mrow"><span class="ml">Target VMs</span><span class="mv">${esc(String(stressState.vmCount || 0))}</span></div>
              <div class="mrow"><span class="ml">Compute Hosts</span><span class="mv">${esc(String(options.limits?.compute_count || 0))}</span></div>
            </div>
          </div>
          <div class="card">
            <div class="card-title"><span>Notes</span></div>
            <div class="card-body note">
              Launches are orchestrated through a single Heat stack with clear naming and one-active-test guardrails.
              Timing and distribution are derived live from Heat resources and Nova server state.
            </div>
          </div>
        </section>
        ${status?.active ? renderStressSummarySection(status) : ''}
        ${status?.active ? renderStressResourceTable(status.resources || []) : ''}
        ${status?.active ? renderStressServerTable(status.servers || []) : ''}
        ${status?.active ? renderStressDistributionTable(status.distribution || []) : ''}
      </div>
    </div>
  `;
}
