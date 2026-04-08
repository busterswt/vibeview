'use strict';

// ════════════════════════════════════════════════════════════════════════════
// § PAGINATION UTILITY
// ════════════════════════════════════════════════════════════════════════════

/** Filter an array of objects by testing each item against a search string
 *  across the given field names. Case-insensitive substring match. */
function applyFilter(arr, filter, fields) {
  if (!filter) return arr;
  const q = filter.toLowerCase();
  return arr.filter(item => fields.some(f => String(item[f] || '').toLowerCase().includes(q)));
}

/** Return a slice of arr for the given page/pageSize. */
function paginate(arr, page, pageSize) {
  const start = (page - 1) * pageSize;
  return arr.slice(start, start + pageSize);
}

/** Build pager HTML.
 *  stateRef = string name of the state object (e.g. 'netState')
 *  renderFn = string name of the render function to call on change */
function buildPager(state, total, stateRef, renderFn) {
  if (total === 0) return '';
  const pages  = Math.ceil(total / state.pageSize);
  const start  = (state.page - 1) * state.pageSize + 1;
  const end    = Math.min(state.page * state.pageSize, total);

  // Page number buttons: show up to 5 around current page
  let pageBtns = '';
  const lo = Math.max(1, state.page - 2);
  const hi = Math.min(pages, state.page + 2);
  if (lo > 1)    pageBtns += `<button class="page-btn" onclick="${stateRef}.page=1;${renderFn}()">1</button>${lo > 2 ? '<span style="color:var(--dim)">…</span>' : ''}`;
  for (let p = lo; p <= hi; p++)
    pageBtns += `<button class="page-btn ${p === state.page ? 'current' : ''}" onclick="${stateRef}.page=${p};${renderFn}()">${p}</button>`;
  if (hi < pages) pageBtns += `${hi < pages - 1 ? '<span style="color:var(--dim)">…</span>' : ''}<button class="page-btn" onclick="${stateRef}.page=${pages};${renderFn}()">${pages}</button>`;

  return `<div class="pager">
    <button class="page-btn" onclick="${stateRef}.page=${state.page-1};${renderFn}()" ${state.page<=1?'disabled':''}>← Prev</button>
    ${pageBtns}
    <button class="page-btn" onclick="${stateRef}.page=${state.page+1};${renderFn}()" ${state.page>=pages?'disabled':''}>Next →</button>
    <select class="pager-size" onchange="${stateRef}.pageSize=+this.value;${stateRef}.page=1;${renderFn}()">
      ${[25,50,100].map(s => `<option value="${s}" ${s===state.pageSize?'selected':''}>${s} per page</option>`).join('')}
    </select>
    <span class="pager-info">Showing ${start}–${end} of ${total}</span>
  </div>`;
}

// ════════════════════════════════════════════════════════════════════════════
// § RECENT TASKS PANEL
// ════════════════════════════════════════════════════════════════════════════

/** Record the wall-clock time when a step first enters each state. */
function trackStepTimes(nodeName, steps) {
  for (const s of steps) {
    if (s.status === 'pending') continue;
    const key = `${nodeName}:${s.key}:${s.status}`;
    if (!stepTimes[key]) stepTimes[key] = new Date();
  }
}

/** Return the recorded time for a step, or '—'. */
function stepTime(nodeName, stepKey, status) {
  const key = `${nodeName}:${stepKey}:${status}`;
  const t   = stepTimes[key];
  return t ? t.toLocaleTimeString('en-GB', { hour12: false }) : '—';
}

function stepProgress(status) {
  return {
    success: { pct: 100, cls: 'done' },
    failed:  { pct: 100, cls: 'fail' },
    skipped: { pct: 100, cls: 'skip' },
    running: { pct:  55, cls:  ''   },
  }[status] || { pct: 0, cls: '' };
}

function renderTasksPanel() {
  const tbody = document.getElementById('tasks-tbody');
  const nd    = selectedNode ? nodes[selectedNode] : null;

  // Workflow steps: prefer selected node, fall back to any running node
  const source = (nd?.steps?.length) ? nd
    : Object.values(nodes).find(n => n.steps?.length && n.phase !== 'idle');

  // Individual instance migrations
  const migTasks = Object.values(instanceMigrateTasks);

  if (!source?.steps?.length && !migTasks.length) {
    tbody.innerHTML = `<tr><td colspan="5" style="color:var(--dim);text-align:center;padding:10px">No active tasks.</td></tr>`;
    return;
  }

  let html = '';

  // ── Workflow steps ────────────────────────────────────────────────────────
  if (source?.steps?.length) {
    for (const s of source.steps) {
      const prog = stepProgress(s.status);
      const ts   = stepTime(source.k8s_name, s.key, s.status);
      const statusHtml = {
        running: `<span style="display:flex;align-items:center;gap:4px"><span class="spinner">⟳</span> Running…</span>`,
        success: `<span style="color:var(--green)">✓ Done</span>`,
        failed:  `<span style="color:var(--red)">✗ Failed</span>`,
        skipped: `<span style="color:var(--dim)">— Skipped</span>`,
        pending: `<span style="color:var(--dim)">○ Pending</span>`,
      }[s.status] || `<span style="color:var(--dim)">${esc(s.status)}</span>`;
      html += `<tr>
        <td>${esc(s.label)}</td>
        <td>${esc(source.k8s_name)}</td>
        <td>${statusHtml}</td>
        <td><div class="progress-inline">
          <div class="prog-bar"><div class="prog-fill ${prog.cls}" style="width:${prog.pct}%"></div></div>
          <span style="color:var(--dim)">${prog.pct}%</span>
        </div></td>
        <td style="color:var(--dim)">${ts}</td>
      </tr>`;
    }
  }

  // ── Individual instance migrations ────────────────────────────────────────
  for (const t of migTasks) {
    const prog = t.status === 'complete' ? { pct: 100, cls: 'done' }
               : t.status === 'error'    ? { pct: 100, cls: 'fail' }
               : { pct: 55, cls: '' };
    const ts   = t.startTime ? t.startTime.toLocaleTimeString('en-GB', { hour12: false }) : '—';
    const statusHtml = t.status === 'migrating'
      ? `<span style="display:flex;align-items:center;gap:4px"><span class="spinner">⟳</span> Migrating…</span>`
      : t.status === 'complete'
      ? `<span style="color:var(--green)">✓ Done</span>`
      : `<span style="color:var(--red)">✗ Failed</span>`;
    html += `<tr>
      <td>↗ Migrate: ${esc(t.name)}</td>
      <td>${esc(t.nodeName)}</td>
      <td>${statusHtml}</td>
      <td><div class="progress-inline">
        <div class="prog-bar"><div class="prog-fill ${prog.cls}" style="width:${prog.pct}%"></div></div>
        <span style="color:var(--dim)">${prog.pct}%</span>
      </div></td>
      <td style="color:var(--dim)">${ts}</td>
    </tr>`;
  }

  tbody.innerHTML = html;
}

