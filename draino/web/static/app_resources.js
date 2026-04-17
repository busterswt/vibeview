'use strict';

// ════════════════════════════════════════════════════════════════════════════
// § STORAGE VIEW
// ════════════════════════════════════════════════════════════════════════════

async function loadVolumes(force = false) {
  if (typeof hasOpenStackAuth === 'function' && !hasOpenStackAuth()) {
    const wrap = document.getElementById('vol-wrap');
    if (wrap) wrap.innerHTML = renderOpenStackUnavailablePanel('Volumes', 'This view currently relies on Cinder inventory. Provide OpenStack credentials to enable it.');
    return;
  }
  if (volState.loading) return;
  if (volState.data && !force) { renderStorageView(); return; }
  volState.loading = true;
  volState.data    = null;
  renderStorageView();
  try {
    const resp = await fetch('/api/volumes');
    const json = await resp.json();
    volState.data       = json.volumes || [];
    volState.allProjects = json.all_projects || false;
    volState.page       = 1;
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
  const wrap = document.getElementById('vol-wrap');
  const focusedInput = captureFocusedInput(wrap, '.dv-filter');
  if (volState.loading) {
    wrap.innerHTML = `<div class="data-view-toolbar"><h2>Cinder Volumes <span class="hint">Datastores</span></h2></div><div style="color:var(--dim);padding:20px 0"><span class="spinner">⟳</span> Loading volumes…</div>`;
    return;
  }
  if (!volState.data) { wrap.innerHTML = ''; return; }

  const scopeNote = volState.allProjects
    ? `<span style="color:var(--green);font-size:11px">● All projects (admin)</span>`
    : `<span style="color:var(--yellow);font-size:11px">● Project scope only</span>`;

  const filtered = applyFilter(volState.data, volState.filter, ['name','status','volume_type','project_id']);
  const { page, pageSize } = volState;
  const paged = paginate(filtered, page, pageSize);

  let rows = '';
  for (const v of paged) {
    const stCls = { available:'st-available', 'in-use':'st-inuse', error:'st-error', 'error_deleting':'st-error' }[v.status] || '';
    const att   = v.attached_to.length
      ? `<span class="sdot green"></span>${v.attached_to.length} server(s)` : `<span style="color:var(--dim)">—</span>`;
    const boot  = v.bootable  ? `<span class="tag-vol">boot</span>` : '—';
    const enc   = v.encrypted ? `<span class="tag-amp">enc</span>`  : '—';
    rows += `<tr>
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
}
