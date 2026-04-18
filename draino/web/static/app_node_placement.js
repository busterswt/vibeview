'use strict';

function placementWrap() {
  return document.getElementById('placement-content');
}

function formatPlacementCount(value, digits = 0) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '—';
  return digits > 0 ? numeric.toFixed(digits) : Math.round(numeric).toString();
}

function formatPlacementMemory(memoryMb) {
  const numeric = Number(memoryMb);
  if (!Number.isFinite(numeric)) return '—';
  return `${Math.round(numeric / 1024)} GB`;
}

function placementConstraintBadge(level) {
  if (level === 'critical') return 'red';
  if (level === 'warning') return 'yellow';
  return 'blue';
}

function placementFitBadge(status) {
  if (status === 'unavailable' || status === 'administratively blocked') return 'red';
  if (status === 'cpu-constrained' || status === 'ram-constrained') return 'red';
  if (status === 'cpu-tight' || status === 'ram-tight' || status === 'trait-restricted') return 'yellow';
  return 'green';
}

function renderPlacementTab(nd) {
  const wrap = placementWrap();
  if (!wrap) return;
  if (!nd?.is_compute) {
    wrap.innerHTML = `<div class="card"><div class="card-title">Placement</div><div class="card-body" style="color:var(--dim)">Placement detail applies to Nova compute nodes only.</div></div>`;
    return;
  }

  const det = nodeDetailCache[nd.k8s_name] || {};
  if (det.loading) {
    wrap.innerHTML = `<div class="card"><div class="card-title">Placement</div><div class="card-body" style="color:var(--dim)"><span class="spinner">⟳</span> Loading Placement detail…</div></div>`;
    return;
  }
  if (det.error) {
    wrap.innerHTML = `<div class="card"><div class="card-title">Placement</div><div class="card-body"><div class="err-block">${esc(det.error)}</div></div></div>`;
    return;
  }

  const placement = det.placement || {};
  const provider = placement.provider || {};
  const inventories = Array.isArray(placement.inventories) ? placement.inventories : [];
  const constraints = Array.isArray(placement.constraints) ? placement.constraints : [];
  const traits = Array.isArray(placement.traits) ? placement.traits : [];
  const customTraits = Array.isArray(placement.custom_traits) ? placement.custom_traits : [];
  const service = placement.service || {};
  const fit = placement.fit || {};
  const providerName = provider.name || nd.hypervisor || nd.k8s_name;
  const hasPlacementData = Boolean(provider.id || inventories.length || traits.length || (placement.aggregates || []).length);

  if (!hasPlacementData) {
    wrap.innerHTML = `<div class="card"><div class="card-title">Placement</div><div class="card-body" style="color:var(--dim)">No Placement provider detail was returned for this compute host.</div></div>`;
    return;
  }

  const inventoryRows = inventories.map((item) => {
    const effective = item.effective_total;
    const used = item.used;
    const pct = effective && used != null ? Math.round((Number(used) / Number(effective)) * 100) : null;
    const pctCls = pct >= 95 ? 'crit' : pct >= 80 ? 'warn' : '';
    const isMemory = item.resource_class === 'MEMORY_MB';
    const totalLabel = isMemory ? formatPlacementMemory(item.total) : formatPlacementCount(item.total);
    const usedLabel = isMemory ? formatPlacementMemory(item.used) : formatPlacementCount(item.used);
    const freeLabel = isMemory ? formatPlacementMemory(item.free) : formatPlacementCount(item.free);
    const effectiveLabel = isMemory ? formatPlacementMemory(item.effective_total) : formatPlacementCount(item.effective_total, item.resource_class === 'VCPU' ? 1 : 0);
    const reservedLabel = isMemory ? formatPlacementMemory(item.reserved) : formatPlacementCount(item.reserved);
    return `<div class="card" style="margin-bottom:10px">
      <div class="card-title">${esc(item.resource_class)}</div>
      <div class="card-body">
        <div class="mrow"><span class="ml">Used / effective</span><span class="mv">${usedLabel} / ${effectiveLabel}</span></div>
        <div class="mrow"><span class="ml">Raw total</span><span class="mv">${totalLabel}</span></div>
        <div class="mrow"><span class="ml">Reserved</span><span class="mv">${reservedLabel}</span></div>
        <div class="mrow"><span class="ml">Allocation ratio</span><span class="mv">${formatPlacementCount(item.allocation_ratio, 2)}</span></div>
        <div class="mrow"><span class="ml">Free headroom</span><span class="mv">${freeLabel}</span></div>
        ${pct != null ? `<div class="pbw"><div class="pb-label"><span>Effective saturation</span><span>${pct}%</span></div><div class="pb-track"><div class="pb-fill ${pctCls}" style="width:${Math.max(0, Math.min(100, pct))}%"></div></div></div>` : ''}
        ${(item.min_unit || item.max_unit || item.step_size) ? `<div class="mrow"><span class="ml">Request shape</span><span class="mv">min ${esc(String(item.min_unit ?? '—'))} · max ${esc(String(item.max_unit ?? '—'))} · step ${esc(String(item.step_size ?? '—'))}</span></div>` : ''}
      </div>
    </div>`;
  }).join('');

  const constraintRows = constraints.length
    ? constraints.map((item) => `<div class="mrow"><span class="ml"><span class="badge ${placementConstraintBadge(item.level)}">${esc(item.title)}</span></span><span class="mv" style="white-space:normal;text-align:right;max-width:70%">${esc(item.detail || '')}</span></div>`).join('')
    : `<div style="color:var(--dim)">No obvious scheduling constraints were detected from current Placement and Nova service signals.</div>`;

  const traitRows = traits.length
    ? `<div style="display:flex;flex-wrap:wrap;gap:4px">${traits.map((trait) => `<span class="tree-badge ${trait.startsWith('CUSTOM_') ? 'agg' : ''}" style="font-family:monospace">${esc(trait)}</span>`).join('')}</div>`
    : `<div style="color:var(--dim)">No Placement traits reported for this provider.</div>`;

  wrap.innerHTML = `
    <div class="summary-grid">
      <div class="card">
        <div class="card-title">Provider Identity</div>
        <div class="card-body">
          <div class="mrow"><span class="ml">Provider</span><span class="mv">${esc(providerName)}</span></div>
          <div class="mrow"><span class="ml">Resource provider ID</span><span class="mv dim" style="font-size:10px">${esc(provider.id || '—')}</span></div>
          <div class="mrow"><span class="ml">Availability Zone</span><span class="mv">${esc(placement.availability_zone || nd.availability_zone || '—')}</span></div>
          <div class="mrow"><span class="ml">Aggregates</span><span class="mv" style="display:flex;flex-wrap:wrap;gap:3px;justify-content:flex-end">${(placement.aggregates || nd.aggregates || []).length ? (placement.aggregates || nd.aggregates || []).map((item) => `<span class="tree-badge agg">${esc(item)}</span>`).join('') : '<span style="color:var(--dim)">—</span>'}</span></div>
          <div class="mrow"><span class="ml">Nova service</span><span class="mv">${esc(service.state || 'unknown')} / ${esc(service.status || 'unknown')}</span></div>
          ${service.disabled_reason ? `<div class="mrow"><span class="ml">Disabled reason</span><span class="mv dim" style="white-space:normal;text-align:right;max-width:70%">${esc(service.disabled_reason)}</span></div>` : ''}
        </div>
      </div>

      <div class="card">
        <div class="card-title">Workload Fit</div>
        <div class="card-body">
          <div class="mrow"><span class="ml">Current fit</span><span class="mv"><span class="badge ${placementFitBadge(fit.status)}">${esc(fit.status || 'unknown')}</span></span></div>
          <div style="color:var(--dim);line-height:1.5">${esc(fit.reason || 'No placement fit summary available.')}</div>
          <div class="mrow" style="margin-top:10px"><span class="ml">Custom traits</span><span class="mv">${customTraits.length ? customTraits.length : '0'}</span></div>
          <div class="mrow"><span class="ml">Inventory classes</span><span class="mv">${inventories.length}</span></div>
        </div>
      </div>
    </div>

    <div class="card" style="margin-top:10px">
      <div class="card-title">Scheduling Constraints</div>
      <div class="card-body">${constraintRows}</div>
    </div>

    <div class="card" style="margin-top:10px">
      <div class="card-title">Traits</div>
      <div class="card-body">${traitRows}</div>
    </div>

    <div style="margin-top:10px">
      ${inventoryRows || `<div class="card"><div class="card-title">Inventories</div><div class="card-body" style="color:var(--dim)">No Placement inventories were returned for this provider.</div></div>`}
    </div>
  `;
}
