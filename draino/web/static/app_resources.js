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

  const filtered = applyFilter(volState.data, volState.filter, ['name', 'status', 'volume_type', 'project_id']);
  const { page, pageSize } = volState;
  const paged = paginate(filtered, page, pageSize);

  let rows = '';
  for (const v of paged) {
    const stCls = { available: 'st-available', 'in-use': 'st-inuse', error: 'st-error', 'error_deleting': 'st-error' }[v.status] || '';
    const att = v.attached_to.length
      ? `<span class="sdot green"></span>${v.attached_to.length} server(s)` : `<span style="color:var(--dim)">—</span>`;
    const boot = v.bootable ? `<span class="tag-vol">boot</span>` : '—';
    const enc = v.encrypted ? `<span class="tag-amp">enc</span>` : '—';
    const rowSel = selectedVolume === v.id ? 'selected' : '';
    rows += `<tr class="${rowSel}" style="cursor:pointer" data-vol-id="${escAttr(v.id)}" onclick="selectVolume('${escAttr(v.id)}')">
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">${esc(v.name)}</td>
      <td><span class="${stCls}">${esc(v.status)}</span></td>
      <td>${v.size_gb} GB</td>
      <td>${esc(v.volume_type) || '<span style="color:var(--dim)">—</span>'}</td>
      <td>${att}</td>
      <td>${boot}</td>
      <td>${enc}</td>
      <td class="uuid-short" title="${esc(v.project_id)}">${v.project_id.slice(0, 8) || '—'}</td>
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
        <th>Attached <span class="hint">Mounted</span></th>
        <th>Bootable</th>
        <th>Encrypted</th>
        <th>Project</th>
      </tr></thead>
      <tbody>${rows || '<tr><td colspan="8" style="text-align:center;color:var(--dim);padding:20px">No volumes match the filter.</td></tr>'}</tbody>
    </table>
    ${buildPager(volState, filtered.length, 'volState', 'renderStorageView')}`;
  restoreFocusedInput(wrap, focusedInput);
  if (activeView === 'storage') renderStorageWorkspace();
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
  storageVolumeDetailWrap()?.classList.remove('open');
  document.querySelectorAll('#vol-wrap tr[data-vol-id]').forEach(r => r.classList.remove('selected'));
  syncStorageDetailShell();
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
          ${vd['os-vol-host-attr:host'] ? `<div class="mrow"><span class="ml">Backend Host</span><span class="mv" style="font-family:monospace;font-size:10px">${esc(vd['os-vol-host-attr:host'])}</span></div>` : ''}
          ${vd.description ? `<div class="mrow"><span class="ml">Description</span><span class="mv">${esc(vd.description)}</span></div>` : ''}
          ${vd.source_volid ? `<div class="mrow"><span class="ml">Source Volume</span><span class="mv" style="font-family:monospace;font-size:10px">${esc(vd.source_volid)}</span></div>` : ''}
          ${vd.snapshot_id ? `<div class="mrow"><span class="ml">Source Snapshot</span><span class="mv" style="font-family:monospace;font-size:10px">${esc(vd.snapshot_id)}</span></div>` : ''}
          ${vd.replication_status ? `<div class="mrow"><span class="ml">Replication</span><span class="mv">${esc(vd.replication_status)}</span></div>` : ''}
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
