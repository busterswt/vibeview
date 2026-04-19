'use strict';

// ════════════════════════════════════════════════════════════════════════════
// § STORAGE VIEW
// ════════════════════════════════════════════════════════════════════════════

function storageOpenStackWrap() {
  return document.getElementById('storage-openstack-wrap') || document.getElementById('vol-wrap');
}

function storageSwiftWrap() {
  return document.getElementById('storage-swift-wrap');
}

function storageVolumeDetailWrap() {
  return document.getElementById('storage-volume-detail-wrap');
}

function refreshActiveStorageView() {
  if (activeStorageView === 'openstack-volumes') {
    return loadVolumes(true);
  }
  if (activeStorageView === 'openstack-swift') {
    return loadSwiftContainers(true);
  }
  if (isStorageK8sView() && typeof refreshK8sResource === 'function') {
    return refreshK8sResource();
  }
}

async function loadVolumes(force = false) {
  const wrap = storageOpenStackWrap();
  if (typeof hasOpenStackAuth === 'function' && !hasOpenStackAuth()) {
    if (wrap) wrap.innerHTML = renderOpenStackUnavailablePanel('Cinder Volumes', 'This view relies on Cinder inventory. Provide OpenStack credentials to enable it.');
    return;
  }
  if (volState.loading) return;
  if (volState.data && !force) {
    renderStorageView();
    return;
  }
  volState.loading = true;
  volState.data = null;
  renderStorageView();
  try {
    const resp = await fetch('/api/volumes');
    const json = await resp.json();
    volState.data = json.volumes || [];
    volState.allProjects = json.all_projects || false;
    volState.page = 1;
    if (json.error) onLog({ node: '-', message: `Volumes API error: ${json.error}`, color: 'error' });
  } catch (e) {
    volState.data = [];
    onLog({ node: '-', message: `Volumes API error: ${e}`, color: 'error' });
  } finally {
    volState.loading = false;
    renderStorageView();
  }
}

async function loadVolumeSnapshots(force = false) {
  const wrap = document.getElementById('storage-snapshot-wrap');
  if (typeof hasOpenStackAuth === 'function' && !hasOpenStackAuth()) {
    if (wrap) wrap.innerHTML = renderOpenStackUnavailablePanel('Volume Snapshots', 'This view relies on Cinder snapshot inventory. Provide OpenStack credentials to enable it.');
    return;
  }
  if (volumeSnapshotState.loading) return;
  if (volumeSnapshotState.data && !force) {
    renderVolumeSnapshotsView();
    return;
  }
  volumeSnapshotState.loading = true;
  volumeSnapshotState.data = null;
  renderVolumeSnapshotsView();
  try {
    const resp = await fetch('/api/volume-snapshots');
    const json = await resp.json();
    volumeSnapshotState.data = json.snapshots || [];
    volumeSnapshotState.page = 1;
    if (json.error) onLog({ node: '-', message: `Volume snapshots API error: ${json.error}`, color: 'error' });
  } catch (e) {
    volumeSnapshotState.data = [];
    onLog({ node: '-', message: `Volume snapshots API error: ${e}`, color: 'error' });
  } finally {
    volumeSnapshotState.loading = false;
    renderVolumeSnapshotsView();
  }
}

async function loadVolumeBackups(force = false) {
  const wrap = document.getElementById('storage-backup-wrap');
  if (typeof hasOpenStackAuth === 'function' && !hasOpenStackAuth()) {
    if (wrap) wrap.innerHTML = renderOpenStackUnavailablePanel('Volume Backups', 'This view relies on Cinder backup inventory. Provide OpenStack credentials to enable it.');
    return;
  }
  if (volumeBackupState.loading) return;
  if (volumeBackupState.data && !force) {
    renderVolumeBackupsView();
    return;
  }
  volumeBackupState.loading = true;
  volumeBackupState.data = null;
  renderVolumeBackupsView();
  try {
    const resp = await fetch('/api/volume-backups');
    const json = await resp.json();
    volumeBackupState.data = json.backups || [];
    volumeBackupState.page = 1;
    if (json.error) onLog({ node: '-', message: `Volume backups API error: ${json.error}`, color: 'error' });
  } catch (e) {
    volumeBackupState.data = [];
    onLog({ node: '-', message: `Volume backups API error: ${e}`, color: 'error' });
  } finally {
    volumeBackupState.loading = false;
    renderVolumeBackupsView();
  }
}

function volumeStatusClass(status) {
  const key = String(status || '').toLowerCase();
  return { available: 'st-available', 'in-use': 'st-inuse', error: 'st-error', 'error_deleting': 'st-error' }[key] || 'st-pending';
}

function volumeRetypeMeta(volumeId) {
  return (volState.retypeMeta && volState.retypeMeta[volumeId]) || null;
}

function renderVolumeRetypeMeta(meta) {
  if (!meta) return '';
  const target = meta.targetType ? ` -> ${esc(meta.targetType)}` : '';
  if (meta.status === 'error') {
    return `<div class="err-block" style="margin-top:6px;font-size:11px;padding:6px 8px">${esc(meta.message || 'Retype request failed.')}</div>`;
  }
  const cls = meta.status === 'requested' ? 'good' : 'warn';
  const mark = meta.status === 'requested' ? '✓' : '!';
  return `<div class="finding ${cls}" style="margin-top:6px;padding:6px 8px;font-size:11px">
    <div class="finding-mark">${mark}</div>
    <div><strong>${esc(meta.label || 'Retype')}</strong>${target}${meta.message ? ` ${esc(meta.message)}` : ''}</div>
  </div>`;
}

function volumePlannerTarget(detail) {
  const currentTypeId = String(detail.volume_type_detail?.id || '');
  const currentTypeName = String(detail.volume_type || '');
  const targetId = String(volumeDetailState.targetType || '');
  const types = Array.isArray(detail.available_volume_types) ? detail.available_volume_types : [];
  if (targetId) return types.find(item => item.id === targetId || item.name === targetId) || null;
  return types.find(item => (item.id || '') !== currentTypeId && (item.name || '') !== currentTypeName) || null;
}

function volumePlannerReadiness(detail, target) {
  if (!target) {
    return {
      state: 'blocked',
      label: 'Select target type',
      summary: 'Choose a target volume type to evaluate readiness.',
      checks: [],
      warnings: [],
    };
  }
  const checks = [];
  const warnings = [];
  const currentBackend = String(detail.volume_type_detail?.backend_name || detail.backend_name || '').trim();
  const targetBackend = String(target.backend_name || '').trim();
  checks.push({
    state: target.name ? 'good' : 'bad',
    title: 'Target type valid',
    detail: target.name
      ? `${target.name} is available for retype planning.`
      : 'Target type metadata is incomplete.',
  });
  checks.push({
    state: targetBackend ? 'good' : 'warn',
    title: 'Backend mapping',
    detail: targetBackend
      ? (currentBackend && currentBackend !== targetBackend
        ? `Retype likely requires migration from ${currentBackend} to ${targetBackend}.`
        : `Target type stays on backend ${targetBackend}.`)
      : 'Target backend is not explicit in the type specs.',
  });
  if (detail.attachments?.length) {
    checks.push({
      state: detail.bootable || detail.multiattach ? 'bad' : 'warn',
      title: 'Attachment state',
      detail: `${detail.attachments.length} active attachment(s) detected.`,
    });
    warnings.push(detail.bootable
      ? 'Attached root disk: do not treat this as a fire-and-forget action.'
      : 'Attached workload: prefer a maintenance window unless driver support is well understood.');
  } else {
    checks.push({
      state: 'good',
      title: 'Not attached',
      detail: 'No active attachments detected.',
    });
  }
  if (detail.bootable) warnings.push('Boot-from-volume workload detected.');
  if (detail.multiattach) warnings.push('Multiattach volume: use explicit storage-admin review.');
  if (!detail.backups?.length && !detail.snapshots?.length) warnings.push('No recent backup or snapshot visible in the detail payload.');
  const hasBlocking = detail.multiattach || (detail.bootable && detail.attachments?.length);
  const hasWindow = !hasBlocking && Boolean(detail.attachments?.length);
  const state = hasBlocking ? 'blocked' : hasWindow ? 'window' : 'safe';
  const label = state === 'safe' ? 'Safe Now' : state === 'window' ? 'Safe In Window' : 'Blocked';
  const summary = state === 'safe'
    ? 'Available volume with a clear target type. Retype is safe to submit.'
    : state === 'window'
      ? 'Retype is reasonable, but attachment state suggests scheduling into a maintenance window.'
      : 'Current workload characteristics make direct submission unsafe without escalation.';
  return { state, label, summary, checks, warnings, targetBackend, currentBackend };
}

function renderStorageView() {
  const wrap = storageOpenStackWrap();
  if (!wrap) return;
  const focusedInput = captureFocusedInput(wrap, '.dv-filter');
  if (volState.loading) {
    wrap.innerHTML = `<div class="data-view-toolbar"><h2>Cinder Volumes <span class="hint">Datastores</span></h2></div><div style="color:var(--dim);padding:20px 0"><span class="spinner">⟳</span> Loading volumes…</div>`;
    return;
  }
  if (!volState.data) {
    wrap.innerHTML = '';
    return;
  }

  const scopeNote = volState.allProjects
    ? `<span style="color:var(--green);font-size:11px">● All projects (admin)</span>`
    : `<span style="color:var(--yellow);font-size:11px">● Project scope only</span>`;

  const filtered = applyFilter(volState.data, volState.filter, ['name', 'status', 'volume_type', 'project_id', 'backend_name', 'backend_pool']);
  const { page, pageSize } = volState;
  const paged = paginate(filtered, page, pageSize);

  let rows = '';
  for (const v of paged) {
    const stCls = { available: 'st-available', 'in-use': 'st-inuse', error: 'st-error', 'error_deleting': 'st-error' }[v.status] || '';
    const att = v.attached_to.length ? `${v.attached_to.length} server(s)` : '—';
    const risk = v.multiattach ? '<span class="status-tag red">multiattach</span>'
      : v.bootable && v.attached_to.length ? '<span class="status-tag orange">root workload</span>'
      : v.attached_to.length ? '<span class="status-tag orange">attached</span>'
      : '<span class="status-tag green">available</span>';
    const retypeMeta = volumeRetypeMeta(v.id);
    const rowSel = selectedVolume === v.id ? 'selected' : '';
    rows += `<tr class="${rowSel}" style="cursor:pointer" data-vol-id="${escAttr(v.id)}" onclick="selectVolume('${escAttr(v.id)}')">
      <td style="max-width:220px;overflow:hidden;text-overflow:ellipsis">
        <div style="font-weight:600">${esc(v.name)}</div>
        <div class="uuid-short" title="${esc(v.id)}">${esc((v.id || '').slice(0, 12) || '—')}</div>
      </td>
      <td><span class="${stCls}">${esc(v.status)}</span></td>
      <td>${v.size_gb} GB</td>
      <td>${esc(v.volume_type) || '<span style="color:var(--dim)">—</span>'}</td>
      <td>${esc(v.backend_name || '—')}</td>
      <td>${att}</td>
      <td>${risk}${renderVolumeRetypeMeta(retypeMeta)}</td>
    </tr>`;
  }

  wrap.innerHTML = `
    <div class="data-view-toolbar">
      <h2>Cinder Volumes <span class="hint">Datastores</span></h2>
      ${scopeNote}
      <input class="dv-filter" type="text" placeholder="Filter volumes…"
        value="${esc(volState.filter)}" oninput="volState.filter=this.value;volState.page=1;renderStorageView()">
      <span style="font-size:11px;color:var(--dim)">${filtered.length} of ${volState.data.length} volumes</span>
    </div>
    <table class="data-table">
      <thead><tr>
        <th>Name <span class="hint">Datastore</span></th>
        <th>Status</th>
        <th>Size</th>
        <th>Type <span class="hint">Storage Policy</span></th>
        <th>Backend</th>
        <th>Attached <span class="hint">Mounted</span></th>
        <th>Readiness</th>
      </tr></thead>
      <tbody>${rows || '<tr><td colspan="7" style="text-align:center;color:var(--dim);padding:20px">No volumes match the filter.</td></tr>'}</tbody>
    </table>
    ${buildPager(volState, filtered.length, 'volState', 'renderStorageView')}`;
  restoreFocusedInput(wrap, focusedInput);
  if (activeView === 'storage') renderStorageWorkspace();
}

function renderVolumeSnapshotsView() {
  const wrap = document.getElementById('storage-snapshot-wrap');
  if (!wrap) return;
  const focusedInput = captureFocusedInput(wrap, '.dv-filter');
  if (volumeSnapshotState.loading) {
    wrap.innerHTML = `<div class="data-view-toolbar"><h2>Volume Snapshots <span class="hint">Cinder</span></h2></div><div style="color:var(--dim);padding:20px 0"><span class="spinner">⟳</span> Loading snapshots…</div>`;
    return;
  }
  if (!volumeSnapshotState.data) {
    wrap.innerHTML = '';
    return;
  }
  const filtered = applyFilter(volumeSnapshotState.data, volumeSnapshotState.filter, ['name', 'status', 'volume_id', 'project_id']);
  const paged = paginate(filtered, volumeSnapshotState.page, volumeSnapshotState.pageSize);
  wrap.innerHTML = `
    <div class="data-view-toolbar">
      <h2>Volume Snapshots <span class="hint">Cinder</span></h2>
      <input class="dv-filter" type="text" placeholder="Filter snapshots…" value="${esc(volumeSnapshotState.filter)}" oninput="volumeSnapshotState.filter=this.value;volumeSnapshotState.page=1;renderVolumeSnapshotsView()">
      <span style="font-size:11px;color:var(--dim)">${filtered.length} of ${volumeSnapshotState.data.length} snapshots</span>
    </div>
    <table class="data-table">
      <thead><tr><th>Name</th><th>Status</th><th>Size</th><th>Volume</th><th>Created</th><th>Project</th></tr></thead>
      <tbody>${paged.map(item => `<tr>
        <td><div style="font-weight:600">${esc(item.name || '(no name)')}</div><div class="uuid-short" title="${esc(item.id || '')}">${esc((item.id || '').slice(0, 12) || '—')}</div></td>
        <td><span class="${volumeStatusClass(item.status)}">${esc(item.status || 'UNKNOWN')}</span></td>
        <td>${esc(String(item.size_gb || 0))} GB</td>
        <td><span class="uuid-short" title="${esc(item.volume_id || '')}">${esc((item.volume_id || '').slice(0, 12) || '—')}</span></td>
        <td>${esc(item.created_at || '—')}</td>
        <td><span class="uuid-short" title="${esc(item.project_id || '')}">${esc((item.project_id || '').slice(0, 8) || '—')}</span></td>
      </tr>`).join('') || '<tr><td colspan="6" style="text-align:center;color:var(--dim);padding:20px">No snapshots match the filter.</td></tr>'}</tbody>
    </table>
    ${buildPager(volumeSnapshotState, filtered.length, 'volumeSnapshotState', 'renderVolumeSnapshotsView')}`;
  restoreFocusedInput(wrap, focusedInput);
}

function renderVolumeBackupsView() {
  const wrap = document.getElementById('storage-backup-wrap');
  if (!wrap) return;
  const focusedInput = captureFocusedInput(wrap, '.dv-filter');
  if (volumeBackupState.loading) {
    wrap.innerHTML = `<div class="data-view-toolbar"><h2>Volume Backups <span class="hint">Cinder</span></h2></div><div style="color:var(--dim);padding:20px 0"><span class="spinner">⟳</span> Loading backups…</div>`;
    return;
  }
  if (!volumeBackupState.data) {
    wrap.innerHTML = '';
    return;
  }
  const filtered = applyFilter(volumeBackupState.data, volumeBackupState.filter, ['name', 'status', 'volume_id', 'project_id', 'container']);
  const paged = paginate(filtered, volumeBackupState.page, volumeBackupState.pageSize);
  wrap.innerHTML = `
    <div class="data-view-toolbar">
      <h2>Volume Backups <span class="hint">Cinder</span></h2>
      <input class="dv-filter" type="text" placeholder="Filter backups…" value="${esc(volumeBackupState.filter)}" oninput="volumeBackupState.filter=this.value;volumeBackupState.page=1;renderVolumeBackupsView()">
      <span style="font-size:11px;color:var(--dim)">${filtered.length} of ${volumeBackupState.data.length} backups</span>
    </div>
    <table class="data-table">
      <thead><tr><th>Name</th><th>Status</th><th>Size</th><th>Volume</th><th>Mode</th><th>Created</th></tr></thead>
      <tbody>${paged.map(item => `<tr>
        <td><div style="font-weight:600">${esc(item.name || '(no name)')}</div><div class="uuid-short" title="${esc(item.id || '')}">${esc((item.id || '').slice(0, 12) || '—')}</div></td>
        <td><span class="${volumeStatusClass(item.status)}">${esc(item.status || 'UNKNOWN')}</span></td>
        <td>${esc(String(item.size_gb || 0))} GB</td>
        <td><span class="uuid-short" title="${esc(item.volume_id || '')}">${esc((item.volume_id || '').slice(0, 12) || '—')}</span></td>
        <td>${item.is_incremental ? 'Incremental' : 'Full'}</td>
        <td>${esc(item.created_at || '—')}</td>
      </tr>`).join('') || '<tr><td colspan="6" style="text-align:center;color:var(--dim);padding:20px">No backups match the filter.</td></tr>'}</tbody>
    </table>
    ${buildPager(volumeBackupState, filtered.length, 'volumeBackupState', 'renderVolumeBackupsView')}`;
  restoreFocusedInput(wrap, focusedInput);
}

async function selectVolume(id) {
  selectedVolume = id;
  document.querySelectorAll('#vol-wrap tr[data-vol-id]').forEach(r => {
    r.classList.toggle('selected', r.dataset.volId === id);
  });
  const detailWrap = storageVolumeDetailWrap();
  if (detailWrap) detailWrap.classList.add('open');
  syncStorageDetailShell();
  volumeDetailState.loading = true;
  volumeDetailState.data = null;
  volumeDetailState.actionMessage = '';
  volumeDetailState.actionTone = 'info';
  volumeDetailState.submitting = false;
  renderVolumeDetail();
  const watchdog = armDetailWatchdog('volume', id, 12000, () => {
    if (selectedVolume !== id || !volumeDetailState.loading) return;
    volumeDetailState.loading = false;
    volumeDetailState.data = { error: 'Timed out after 12s while loading volume details' };
    renderVolumeDetail();
  });
  try {
    const json = await fetchJsonWithTimeout(`/api/volumes/${encodeURIComponent(id)}`, 10000);
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Cinder');
    if (json.error) throw new Error(json.error);
    const meta = volState.data?.find(v => v.id === id) || {};
    volumeDetailState.data = { ...meta, ...(json.volume || {}) };
    volumeDetailState.targetType = volumePlannerTarget(volumeDetailState.data)?.id || volumePlannerTarget(volumeDetailState.data)?.name || '';
    volumeDetailState.actionMessage = '';
    volumeDetailState.actionTone = 'info';
  } catch (e) {
    volumeDetailState.data = { error: String(e) };
  } finally {
    clearTimeout(watchdog);
    volumeDetailState.loading = false;
    renderVolumeDetail();
  }
}

function closeVolumeDetail() {
  selectedVolume = null;
  volumeDetailState.loading = false;
  volumeDetailState.data = null;
  volumeDetailState.targetType = '';
  volumeDetailState.actionMessage = '';
  volumeDetailState.actionTone = 'info';
  volumeDetailState.submitting = false;
  storageVolumeDetailWrap()?.classList.remove('open');
  document.querySelectorAll('#vol-wrap tr[data-vol-id]').forEach(r => r.classList.remove('selected'));
  syncStorageDetailShell();
}

function setVolumePlannerTargetType(value) {
  volumeDetailState.targetType = value || '';
  volumeDetailState.actionMessage = '';
  volumeDetailState.actionTone = 'info';
  renderVolumeDetail();
}

function setVolumeRetypeMeta(volumeId, meta) {
  volState.retypeMeta = { ...(volState.retypeMeta || {}), [volumeId]: meta };
}

async function submitVolumeRetype() {
  const detail = volumeDetailState.data;
  if (!detail || detail.error) return;
  const target = volumePlannerTarget(detail);
  const readiness = volumePlannerReadiness(detail, target);
  const targetName = target?.name || volumeDetailState.targetType || 'selected target';
  if (readiness.state === 'blocked') {
    volumeDetailState.actionTone = 'error';
    volumeDetailState.actionMessage = `Retype is blocked for ${detail.name || detail.id || 'volume'} -> ${targetName}. Resolve the blocking checks before submitting.`;
    setVolumeRetypeMeta(detail.id, {
      status: 'error',
      label: 'Retype blocked',
      targetType: targetName,
      message: 'Resolve blocking checks before submitting.',
      at: Date.now(),
    });
    renderStorageView();
    renderVolumeDetail();
    return;
  }

  volumeDetailState.submitting = true;
  volumeDetailState.actionTone = readiness.state === 'window' ? 'warn' : 'info';
  volumeDetailState.actionMessage = readiness.state === 'window'
    ? `Submitting retype for ${detail.name || detail.id || 'volume'} -> ${targetName}.`
    : `Submitting retype for ${detail.name || detail.id || 'volume'} -> ${targetName}.`;
  setVolumeRetypeMeta(detail.id, {
    status: 'submitting',
    label: 'Retype submitting',
    targetType: targetName,
    message: readiness.state === 'window' ? 'Maintenance window still recommended.' : 'Submitting request.',
    at: Date.now(),
  });
  renderStorageView();
  renderVolumeDetail();

  try {
    const resp = await fetch(`/api/volumes/${encodeURIComponent(detail.id)}/retype`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target_type: target?.id || target?.name || volumeDetailState.targetType || '',
        migration_policy: 'on-demand',
      }),
    });
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Cinder');
    if (json.error) throw new Error(json.error);
    volumeDetailState.actionTone = readiness.state === 'window' ? 'warn' : 'info';
    volumeDetailState.actionMessage = readiness.state === 'window'
      ? `Retype submitted for ${detail.name || detail.id || 'volume'} -> ${targetName}. Maintenance window is still recommended.`
      : `Retype submitted for ${detail.name || detail.id || 'volume'} -> ${targetName}.`;
    setVolumeRetypeMeta(detail.id, {
      status: 'requested',
      label: 'Retype requested',
      targetType: targetName,
      message: readiness.state === 'window' ? 'Window recommended.' : 'Awaiting Cinder status change.',
      at: Date.now(),
    });
    await loadVolumes(true);
    if (selectedVolume === detail.id) await selectVolume(detail.id);
  } catch (e) {
    const message = String(e);
    volumeDetailState.actionTone = 'error';
    volumeDetailState.actionMessage = `Retype failed for ${detail.name || detail.id || 'volume'} -> ${targetName}: ${message}`;
    setVolumeRetypeMeta(detail.id, {
      status: 'error',
      label: 'Retype failed',
      targetType: targetName,
      message,
      at: Date.now(),
    });
  } finally {
    volumeDetailState.submitting = false;
    renderStorageView();
    renderVolumeDetail();
  }
}

function renderVolumeDetail() {
  const wrap = storageVolumeDetailWrap();
  if (!wrap) return;
  if (volumeDetailState.loading) {
    wrap.innerHTML = `<div class="net-detail-inner"><div style="color:var(--dim);padding:20px 0"><span class="spinner">⟳</span> Loading…</div></div>`;
    return;
  }
  const vd = volumeDetailState.data;
  if (!vd) {
    wrap.innerHTML = '';
    return;
  }
  if (vd.error) {
    wrap.innerHTML = `<div class="net-detail-inner"><div class="err-block">${esc(vd.error)}</div></div>`;
    return;
  }

  const statusCls = vd.status === 'available' ? 'st-available'
    : vd.status === 'in-use' ? 'st-inuse'
    : vd.status === 'error' || vd.status === 'error_deleting' ? 'st-error'
    : 'st-pending';
  const typeDetail = vd.volume_type_detail || {};
  const extraSpecs = Object.entries(typeDetail.extra_specs || {});
  const qosEntries = Object.entries(typeDetail.qos_policy || {});
  const attachments = vd.attachments || [];
  const metadata = Object.entries(vd.metadata || {});
  const target = volumePlannerTarget(vd);
  const readiness = volumePlannerReadiness(vd, target);
  const targetOptions = (vd.available_volume_types || []).filter(item => item.name !== vd.volume_type);
  const readinessCls = readiness.state === 'safe' ? 'st-available' : readiness.state === 'window' ? 'st-pending' : 'st-error';
  const actionLabel = readiness.state === 'blocked' ? 'Blocked Pending Review' : 'Retype Volume';
  const actionClass = readiness.state === 'safe'
    ? 'primary'
    : readiness.state === 'window'
      ? 'warning'
      : 'danger';
  const actionDisabled = !target || volumeDetailState.submitting;
  const retypeMeta = vd.id ? volumeRetypeMeta(vd.id) : null;
  wrap.innerHTML = `
    <div class="net-detail-inner">
      <div class="net-detail-head">
        <div>
          <div style="font-size:16px;font-weight:700">${esc(vd.name || '(no name)')}</div>
          <div style="font-size:11px;color:var(--dim);margin-top:2px">Cinder Volume</div>
        </div>
        <div style="display:flex;gap:6px;align-items:center;flex-shrink:0">
          <span class="${statusCls}" style="font-size:11px">${esc(vd.status || 'UNKNOWN')}</span>
          <button class="btn btn-outline small-btn" onclick="closeVolumeDetail()">Close</button>
        </div>
      </div>
      <div style="font-family:monospace;font-size:10px;color:var(--dim);word-break:break-all;margin-bottom:10px">${esc(vd.id || '')}</div>

      <div class="card" style="margin-bottom:10px">
        <div class="card-title">
          <span class="card-title-label">Retype Readiness</span>
          <span class="card-title-actions">
            <button class="btn ${actionClass}" onclick="submitVolumeRetype()" ${actionDisabled ? 'disabled' : ''}>${volumeDetailState.submitting ? 'Submitting...' : actionLabel}</button>
          </span>
        </div>
        <div class="card-body">
          <div class="mrow"><span class="ml">Recommended path</span><span class="mv"><span class="${readinessCls}">${esc(readiness.label)}</span></span></div>
          <div class="mrow"><span class="ml">Current backend</span><span class="mv">${esc(readiness.currentBackend || vd.backend_name || '—')}</span></div>
          <div class="mrow"><span class="ml">Target type</span><span class="mv">
            <select class="pager-size" onchange="setVolumePlannerTargetType(this.value)" style="max-width:180px">
              ${targetOptions.length ? targetOptions.map(item => `<option value="${escAttr(item.id || item.name)}" ${String(volumeDetailState.targetType || '') === String(item.id || item.name) ? 'selected' : ''}>${esc(item.name || item.id || 'unknown')}</option>`).join('') : '<option value="">No alternate types</option>'}
            </select>
          </span></div>
          <div class="mrow"><span class="ml">Target backend</span><span class="mv">${esc(readiness.targetBackend || target?.backend_name || '—')}</span></div>
          <div style="margin-top:8px;padding:8px 10px;border-radius:4px;border:1px solid var(--border);background:${readiness.state === 'safe' ? '#f3fbf6' : readiness.state === 'window' ? '#fff8e1' : '#fff5f5'};font-size:12px;line-height:1.4">${esc(readiness.summary)}</div>
          ${retypeMeta ? renderVolumeRetypeMeta(retypeMeta) : ''}
          ${volumeDetailState.actionMessage ? (volumeDetailState.actionTone === 'error'
            ? `<div class="err-block" style="margin-top:8px">${esc(volumeDetailState.actionMessage)}</div>`
            : `<div class="finding warn" style="margin-top:8px">
              <div class="finding-mark">!</div>
              <div><strong>Planner Action</strong>${esc(volumeDetailState.actionMessage)}</div>
            </div>`) : ''}
        </div>
      </div>

      <div class="card" style="margin-bottom:10px">
        <div class="card-title">Properties</div>
        <div class="card-body">
          <div class="mrow"><span class="ml">Size</span><span class="mv">${esc(String(vd.size_gb ?? 0))} GB</span></div>
          <div class="mrow"><span class="ml">Type</span><span class="mv">${esc(vd.volume_type || '—')}</span></div>
          <div class="mrow"><span class="ml">Bootable</span><span class="mv">${vd.bootable ? 'Yes' : 'No'}</span></div>
          <div class="mrow"><span class="ml">Encrypted</span><span class="mv">${vd.encrypted ? 'Yes' : 'No'}</span></div>
          <div class="mrow"><span class="ml">Multiattach</span><span class="mv">${vd.multiattach ? 'Yes' : 'No'}</span></div>
          <div class="mrow"><span class="ml">Availability Zone</span><span class="mv">${esc(vd.availability_zone || '—')}</span></div>
          <div class="mrow"><span class="ml">Created</span><span class="mv">${esc(vd.created_at || '—')}</span></div>
          <div class="mrow"><span class="ml">Updated</span><span class="mv">${esc(vd.updated_at || '—')}</span></div>
          <div class="mrow"><span class="ml">Snapshots</span><span class="mv">${esc(String(vd.snapshot_count ?? 0))}</span></div>
          <div class="mrow"><span class="ml">Backups</span><span class="mv">${esc(String(vd.backup_count ?? 0))}</span></div>
          ${vd.project_id ? `<div class="mrow"><span class="ml">Project</span><span class="mv uuid-short" title="${esc(vd.project_id)}">${vd.project_id.slice(0, 8)}</span></div>` : ''}
          ${vd.backend_host ? `<div class="mrow"><span class="ml">Backend Host</span><span class="mv">${esc(vd.backend_host)}</span></div>` : ''}
          ${vd.backend_pool ? `<div class="mrow"><span class="ml">Pool</span><span class="mv">${esc(vd.backend_pool)}</span></div>` : ''}
          ${vd['os-vol-host-attr:host'] ? `<div class="mrow"><span class="ml">Host String</span><span class="mv" style="font-family:monospace;font-size:10px">${esc(vd['os-vol-host-attr:host'])}</span></div>` : ''}
          ${vd.description ? `<div class="mrow"><span class="ml">Description</span><span class="mv">${esc(vd.description)}</span></div>` : ''}
          ${vd.source_volid ? `<div class="mrow"><span class="ml">Source Volume</span><span class="mv" style="font-family:monospace;font-size:10px">${esc(vd.source_volid)}</span></div>` : ''}
          ${vd.snapshot_id ? `<div class="mrow"><span class="ml">Source Snapshot</span><span class="mv" style="font-family:monospace;font-size:10px">${esc(vd.snapshot_id)}</span></div>` : ''}
          ${vd.replication_status ? `<div class="mrow"><span class="ml">Replication</span><span class="mv">${esc(vd.replication_status)}</span></div>` : ''}
        </div>
      </div>

      <div class="card" style="margin-bottom:10px">
        <div class="card-title">Checks</div>
        <div class="card-body">
          ${(readiness.checks || []).length
            ? readiness.checks.map(item => {
              const cls = item.state === 'good' ? 'good' : item.state === 'warn' ? 'warn' : 'bad';
              const mark = item.state === 'good' ? '✓' : item.state === 'warn' ? '!' : '×';
              return `<div class="finding ${cls}" style="margin-bottom:8px">
                <div class="finding-mark">${mark}</div>
                <div><strong>${esc(item.title)}</strong>${esc(item.detail)}</div>
              </div>`;
            }).join('')
            : '<div style="color:var(--dim);font-size:12px">No planner checks available.</div>'}
          ${(readiness.warnings || []).length
            ? readiness.warnings.map(item => `<div class="finding warn" style="margin-bottom:8px">
              <div class="finding-mark">!</div>
              <div><strong>Operator Warning</strong>${esc(item)}</div>
            </div>`).join('')
            : ''}
        </div>
      </div>

      <div class="card" style="margin-bottom:10px">
        <div class="card-title">Attachments (${attachments.length})</div>
        <div class="card-body">
          ${attachments.length ? attachments.map(item => `
            <div style="padding:8px 0;border-bottom:1px solid #f0f2f5">
              <div style="font-weight:600">${esc(item.server_name || item.server_id || '(unknown server)')}</div>
              <div style="font-size:10px;color:var(--dim);font-family:monospace;word-break:break-all">${esc(item.server_id || '—')}</div>
              <div style="margin-top:4px;font-size:11px;color:var(--dim)">
                Device ${esc(item.device || '—')} · Host ${esc(item.host_name || '—')} · Attached ${esc(item.attached_at || '—')}
              </div>
            </div>
          `).join('') : '<div style="color:var(--dim);font-size:12px">Not attached.</div>'}
        </div>
      </div>

      <div class="card" style="margin-bottom:10px">
        <div class="card-title">Snapshots (${vd.snapshot_count || 0})</div>
        <div class="card-body">
          ${(vd.snapshots || []).length ? (vd.snapshots || []).slice(0, 5).map(item => `
            <div style="padding:8px 0;border-bottom:1px solid #f0f2f5">
              <div style="font-weight:600">${esc(item.name || '(no name)')}</div>
              <div style="font-size:10px;color:var(--dim);font-family:monospace;word-break:break-all">${esc(item.id || '—')}</div>
              <div style="margin-top:4px;font-size:11px;color:var(--dim)">${esc(item.status || 'UNKNOWN')} · ${esc(String(item.size_gb || 0))} GB · ${esc(item.created_at || '—')}</div>
            </div>`).join('') : '<div style="color:var(--dim);font-size:12px">No snapshots for this volume.</div>'}
        </div>
      </div>

      <div class="card" style="margin-bottom:10px">
        <div class="card-title">Backups (${vd.backup_count || 0})</div>
        <div class="card-body">
          ${(vd.backups || []).length ? (vd.backups || []).slice(0, 5).map(item => `
            <div style="padding:8px 0;border-bottom:1px solid #f0f2f5">
              <div style="font-weight:600">${esc(item.name || '(no name)')}</div>
              <div style="font-size:10px;color:var(--dim);font-family:monospace;word-break:break-all">${esc(item.id || '—')}</div>
              <div style="margin-top:4px;font-size:11px;color:var(--dim)">${esc(item.status || 'UNKNOWN')} · ${item.is_incremental ? 'Incremental' : 'Full'} · ${esc(item.created_at || '—')}</div>
            </div>`).join('') : '<div style="color:var(--dim);font-size:12px">No backups for this volume.</div>'}
        </div>
      </div>

      <div class="card" style="margin-bottom:10px">
        <div class="card-title">Volume Type</div>
        <div class="card-body">
          <div class="mrow"><span class="ml">Name</span><span class="mv">${esc(typeDetail.name || vd.volume_type || '—')}</span></div>
          <div class="mrow"><span class="ml">Type ID</span><span class="mv" style="font-family:monospace;font-size:10px;word-break:break-all">${esc(typeDetail.id || '—')}</span></div>
          <div class="mrow"><span class="ml">Public</span><span class="mv">${typeDetail.is_public ? 'Yes' : 'No'}</span></div>
          ${typeDetail.description ? `<div class="mrow"><span class="ml">Description</span><span class="mv">${esc(typeDetail.description)}</span></div>` : ''}
        </div>
      </div>

      <div class="card" style="margin-bottom:10px">
        <div class="card-title">QoS Policy</div>
        <div class="card-body">
          ${qosEntries.length ? qosEntries.map(([key, value]) => `<div class="mrow"><span class="ml">${esc(key)}</span><span class="mv">${esc(String(value))}</span></div>`).join('') : '<div style="color:var(--dim);font-size:12px">No QoS policy attached.</div>'}
        </div>
      </div>

      <div class="card" style="margin-bottom:10px">
        <div class="card-title">Extra Specs</div>
        <div class="card-body">
          ${extraSpecs.length ? extraSpecs.map(([key, value]) => `<div class="mrow"><span class="ml">${esc(key)}</span><span class="mv">${esc(String(value))}</span></div>`).join('') : '<div style="color:var(--dim);font-size:12px">No extra specs.</div>'}
        </div>
      </div>

      <div class="card">
        <div class="card-title">Metadata</div>
        <div class="card-body">
          ${metadata.length ? metadata.map(([key, value]) => `<div class="mrow"><span class="ml">${esc(key)}</span><span class="mv">${esc(String(value))}</span></div>`).join('') : '<div style="color:var(--dim);font-size:12px">No metadata.</div>'}
        </div>
      </div>
    </div>`;
}

async function loadSwiftContainers(force = false) {
  const wrap = storageSwiftWrap();
  if (typeof hasOpenStackAuth === 'function' && !hasOpenStackAuth()) {
    if (wrap) wrap.innerHTML = renderOpenStackUnavailablePanel('Swift Containers', 'This view relies on Swift inventory. Provide OpenStack credentials to enable it.');
    return;
  }
  if (swiftState.loading) return;
  if (swiftState.data && !force) {
    renderSwiftStorageView();
    return;
  }
  swiftState.loading = true;
  swiftState.data = null;
  renderSwiftStorageView();
  try {
    const resp = await fetch('/api/swift-containers');
    const json = await resp.json();
    swiftState.data = json.containers || [];
    swiftState.page = 1;
    if (json.error) onLog({ node: '-', message: `Swift API error: ${json.error}`, color: 'error' });
  } catch (e) {
    swiftState.data = [];
    onLog({ node: '-', message: `Swift API error: ${e}`, color: 'error' });
  } finally {
    swiftState.loading = false;
    renderSwiftStorageView();
  }
}

function renderSwiftStorageView() {
  const wrap = storageSwiftWrap();
  if (!wrap) return;
  const focusedInput = captureFocusedInput(wrap, '.dv-filter');
  if (swiftState.loading) {
    wrap.innerHTML = `<div class="data-view-toolbar"><h2>Swift Containers <span class="hint">Object Storage</span></h2></div><div style="color:var(--dim);padding:20px 0"><span class="spinner">⟳</span> Loading containers…</div>`;
    return;
  }
  if (!swiftState.data) {
    wrap.innerHTML = '';
    return;
  }

  const filtered = applyFilter(swiftState.data, swiftState.filter, ['name']);
  const { page, pageSize } = swiftState;
  const paged = paginate(filtered, page, pageSize);
  let rows = '';
  for (const c of paged) {
    rows += `<tr>
      <td>${esc(c.name)}</td>
      <td>${esc(String(c.object_count ?? 0))}</td>
      <td>${fmtBytes(Number(c.bytes_used || 0))}</td>
      <td>${c.is_public ? '<span class="k8s-badge lb">public</span>' : '<span style="color:var(--dim)">private</span>'}</td>
    </tr>`;
  }

  wrap.innerHTML = `
    <div class="data-view-toolbar">
      <h2>Swift Containers <span class="hint">Object Storage</span></h2>
      <input class="dv-filter" type="text" placeholder="Filter containers…"
        value="${esc(swiftState.filter)}" oninput="swiftState.filter=this.value;swiftState.page=1;renderSwiftStorageView()">
      <span style="font-size:11px;color:var(--dim)">${filtered.length} of ${swiftState.data.length} containers</span>
    </div>
    <table class="data-table">
      <thead><tr>
        <th>Name</th>
        <th>Objects</th>
        <th>Bytes Used</th>
        <th>ACL</th>
      </tr></thead>
      <tbody>${rows || '<tr><td colspan="4" style="text-align:center;color:var(--dim);padding:20px">No containers match the filter.</td></tr>'}</tbody>
    </table>
    ${buildPager(swiftState, filtered.length, 'swiftState', 'renderSwiftStorageView')}`;
  restoreFocusedInput(wrap, focusedInput);
  if (activeView === 'storage') renderStorageWorkspace();
}
