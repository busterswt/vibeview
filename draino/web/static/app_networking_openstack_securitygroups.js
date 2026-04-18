'use strict';

function sgSeverityClass(value) {
  const text = String(value || '').toLowerCase();
  if (text === 'critical') return 'st-error';
  if (text === 'high' || text === 'medium') return 'st-pending';
  return 'st-active';
}

function sgSeverityBadge(value) {
  const text = String(value || 'clean').toLowerCase();
  const cls = text === 'critical' ? 'badge red' : text === 'high' ? 'badge yellow' : text === 'medium' ? 'badge gray' : 'badge green';
  return `<span class="${cls}" style="font-size:10px">${esc(text.toUpperCase())}</span>`;
}

function sgComplexityBadge(value) {
  const text = String(value || 'low').toLowerCase();
  const cls = text === 'high' ? 'badge red' : text === 'elevated' ? 'badge yellow' : 'badge blue';
  return `<span class="${cls}" style="font-size:10px">${esc(text.toUpperCase())}</span>`;
}

function sgHintLabel(label, hint) {
  return `<span title="${escAttr(hint)}" style="border-bottom:1px dotted var(--border);cursor:help">${esc(label)}</span>`;
}

function sgSummaryPill(cls, label, count, hint) {
  return `<span class="badge ${cls}" title="${escAttr(hint)}" style="cursor:help">${esc(String(count))} ${esc(label)}</span>`;
}

function sgRuleRemoteCell(rule) {
  const remoteGroupId = String(rule.remote_group_id || '').trim();
  if (remoteGroupId) {
    return `<button class="btn" style="padding:2px 8px;font-size:11px" onclick="selectSecurityGroup('${escAttr(remoteGroupId)}')">${esc(remoteGroupId)}</button>`;
  }
  return `<span style="font-family:monospace;font-size:10px">${esc(rule.remote_ip_prefix || '—')}</span>`;
}

function sgProjectOptions(items) {
  const seen = new Set();
  const opts = [];
  for (const item of items || []) {
    const key = String(item.project_name || item.project_id || '');
    if (!key || seen.has(key)) continue;
    seen.add(key);
    opts.push(key);
  }
  opts.sort((a, b) => a.localeCompare(b));
  return opts;
}

function updateSecurityGroupNavBadge() {
  const badge = document.getElementById('networking-cnt-securitygroups');
  if (!badge) return;
  if (!Array.isArray(sgState.data)) {
    badge.textContent = '';
    badge.className = 'tree-badge';
    return;
  }
  const flagged = sgState.data.filter(item => (item.flagged_rule_count || 0) > 0).length;
  badge.textContent = flagged ? `${flagged} flagged` : `${sgState.data.length}`;
  badge.className = `tree-badge${flagged ? ' err' : ''}`;
}

async function loadSecurityGroups(force = false) {
  if (typeof hasOpenStackAuth === 'function' && !hasOpenStackAuth()) {
    const wrap = document.getElementById('sg-wrap');
    if (wrap) wrap.innerHTML = renderOpenStackUnavailablePanel('Security Groups', 'This view currently relies on OpenStack security group inventory. Provide OpenStack credentials to enable it.');
    return;
  }
  if (sgState.loading) return;
  if (sgState.data && !force) {
    renderSecurityGroupsView();
    return;
  }
  sgState.loading = true;
  sgState.data = null;
  renderSecurityGroupsView();
  try {
    const resp = await fetch('/api/security-groups');
    const json = await resp.json();
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Neutron');
    sgState.data = json.security_groups || [];
    sgState.page = 1;
    if (json.error) appendSecurityGroupError(json.error);
  } catch (e) {
    sgState.data = [];
    appendSecurityGroupError(String(e));
  } finally {
    sgState.loading = false;
    updateSecurityGroupNavBadge();
    renderSecurityGroupsView();
  }
}

function appendSecurityGroupError(msg) {
  onLog({ node: '-', message: `Security groups API error: ${msg}`, color: 'error' });
}

function filteredSecurityGroups() {
  const items = Array.isArray(sgState.data) ? sgState.data : [];
  const base = applyFilter(items, sgState.filter, [
    'name',
    'description',
    'project_id',
    'project_name',
  ]);
  return base.filter(item => {
    if (sgState.auditOnly && !(item.flagged_rule_count > 0 || item.audit?.has_unused)) return false;
    if (sgState.project && String(item.project_name || item.project_id || '') !== sgState.project) return false;
    return true;
  });
}

function renderSecurityGroupsView() {
  const wrap = document.getElementById('sg-wrap');
  if (!wrap) return;
  const focusedInput = captureFocusedInput(wrap, '.dv-filter');
  if (sgState.loading) {
    wrap.innerHTML = `<div class="data-view-toolbar"><h2>Security Groups <span class="hint">Neutron / Project Scoped</span></h2></div><div style="color:var(--dim);padding:20px 0"><span class="spinner">⟳</span> Loading security groups…</div>`;
    return;
  }
  if (!sgState.data) {
    wrap.innerHTML = '';
    return;
  }
  updateSecurityGroupNavBadge();
  const filtered = filteredSecurityGroups();
  const paged = paginate(filtered, sgState.page, sgState.pageSize);
  const projectOptions = sgProjectOptions(sgState.data);
  const criticalCount = sgState.data.filter(item => item.audit?.severity === 'critical').length;
  const highCount = sgState.data.filter(item => item.audit?.severity === 'high').length;
  const mediumCount = sgState.data.filter(item => item.audit?.severity === 'medium').length;
  const lowCount = sgState.data.filter(item => item.audit?.severity === 'clean').length;
  const unusedCount = sgState.data.filter(item => item.audit?.has_unused).length;
  wrap.innerHTML = `
    <div class="data-view-toolbar">
      <h2>Security Groups <span class="hint">Neutron / Project Scoped</span></h2>
      <input class="dv-filter" type="text" placeholder="Filter groups…"
        value="${esc(sgState.filter)}" oninput="sgState.filter=this.value;sgState.page=1;renderSecurityGroupsView()">
      <select class="pager-size" onchange="sgState.project=this.value;sgState.page=1;renderSecurityGroupsView()">
        <option value="">All projects</option>
        ${projectOptions.map(name => `<option value="${escAttr(name)}" ${sgState.project === name ? 'selected' : ''}>${esc(name)}</option>`).join('')}
      </select>
      <button class="btn ${sgState.auditOnly ? 'warning' : ''}" onclick="sgState.auditOnly=!sgState.auditOnly;sgState.page=1;renderSecurityGroupsView()">${sgState.auditOnly ? '⚑ Audit Only' : 'All Groups'}</button>
      <span style="font-size:11px;color:var(--dim)">${filtered.length} of ${sgState.data.length} groups</span>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
      ${sgSummaryPill('red', 'critical', criticalCount, 'Criteria: open-world ingress with any protocol and any port, for example any:any 0.0.0.0/0 or any:any ::/0.')}
      ${sgSummaryPill('yellow', 'high', highCount, 'Criteria: open-world ingress on specific ports or protocols, including publicly exposed administrative ports such as TCP 22 or 3389.')}
      ${sgSummaryPill('gray', 'medium', mediumCount, 'Criteria: currently used for cleanup-oriented findings. In the current model this means the group has zero attachments.')}
      ${sgSummaryPill('green', 'clean', lowCount, 'Criteria: no current audit findings under the present ruleset. This matches the backend severity label of clean.')}
      ${sgSummaryPill('gray', 'unused', unusedCount, 'Criteria: the group has zero current Neutron port attachments. This is a specialized cleanup signal and also maps to medium severity in the current model.')}
    </div>
    <table class="data-table">
      <thead><tr>
        <th>Name</th>
        <th>Project</th>
        <th>Severity</th>
        <th>Findings</th>
        <th>Rules</th>
        <th>Attachments</th>
        <th>Stateful</th>
      </tr></thead>
      <tbody>${paged.map(item => {
        const rowSel = selectedSecurityGroup === item.id ? ' selected' : '';
        const findings = (item.audit?.findings || []).map(finding => {
          const cls = finding.severity === 'critical' ? 'et-error' : finding.severity === 'high' ? 'et-warn' : 'et-info';
          return `<span class="event-tag ${cls}" style="margin-left:0;margin-right:4px">${esc(finding.summary || finding.category || '')}</span>`;
        }).join('') || '<span style="color:var(--dim)">—</span>';
        return `<tr class="${rowSel}" style="cursor:pointer" data-sg-id="${escAttr(item.id)}" onclick="selectSecurityGroup('${escAttr(item.id)}')">
          <td>
            <div style="font-weight:600">${esc(item.name || '(unnamed)')}</div>
            <div class="uuid-short" title="${esc(item.id || '')}">${esc((item.id || '').slice(0, 12) || '—')}</div>
          </td>
          <td>
            <div>${esc(item.project_name || '—')}</div>
            <div class="uuid-short" title="${esc(item.project_id || '')}">${esc((item.project_id || '').slice(0, 8) || '—')}</div>
          </td>
          <td><span class="${sgSeverityClass(item.audit?.severity)}">${esc((item.audit?.severity || 'clean').toUpperCase())}</span></td>
          <td>${findings}</td>
          <td>${esc(String(item.flagged_rule_count || 0))} / ${esc(String(item.rule_count || 0))}</td>
          <td>${esc(String(item.attachment_instance_count || 0))} inst · ${esc(String(item.attachment_port_count || 0))} ports</td>
          <td>${item.stateful ? 'Yes' : 'No'}</td>
        </tr>`;
      }).join('') || '<tr><td colspan="7" style="text-align:center;color:var(--dim);padding:20px">No security groups match the current filters.</td></tr>'}</tbody>
    </table>
    ${buildPager(sgState, filtered.length, 'sgState', 'renderSecurityGroupsView')}`;
  restoreFocusedInput(wrap, focusedInput);
}

async function selectSecurityGroup(id) {
  selectedSecurityGroup = id;
  document.querySelectorAll('#sg-wrap tr[data-sg-id]').forEach(r => {
    r.classList.toggle('selected', r.dataset.sgId === id);
  });
  document.getElementById('sg-detail-wrap').classList.add('open');
  syncNetworkingDetailShell();
  sgDetailState.loading = true;
  sgDetailState.data = null;
  renderSecurityGroupDetail();
  const watchdog = armDetailWatchdog('securitygroup', id, 12000, () => {
    if (selectedSecurityGroup !== id || !sgDetailState.loading) return;
    sgDetailState.loading = false;
    sgDetailState.data = { error: 'Timed out after 12s while loading security group details' };
    renderSecurityGroupDetail();
  });
  try {
    const json = await fetchJsonWithTimeout(`/api/security-groups/${encodeURIComponent(id)}`, 10000);
    if (json.api_issue) recordApiIssue(json.api_issue);
    else recordApiSuccess('Neutron');
    if (json.error) throw new Error(json.error);
    const meta = sgState.data?.find(item => item.id === id) || {};
    sgDetailState.data = { ...meta, ...json.security_group };
  } catch (e) {
    sgDetailState.data = { error: String(e) };
  } finally {
    clearTimeout(watchdog);
    sgDetailState.loading = false;
    renderSecurityGroupDetail();
  }
}

function closeSecurityGroupDetail() {
  selectedSecurityGroup = null;
  sgDetailState.data = null;
  document.getElementById('sg-detail-wrap').classList.remove('open');
  document.querySelectorAll('#sg-wrap tr[data-sg-id]').forEach(r => r.classList.remove('selected'));
  syncNetworkingDetailShell();
}

function renderSecurityGroupProperties(detail) {
  return `<div class="card" style="margin-bottom:10px">
    <div class="card-title">Properties</div>
    <div class="card-body">
      <div class="mrow"><span class="ml">Project</span><span class="mv">${esc(detail.project_name || detail.project_id || '—')}</span></div>
      <div class="mrow"><span class="ml">Revision</span><span class="mv">${esc(String(detail.revision_number ?? '—'))}</span></div>
      <div class="mrow"><span class="ml">Stateful</span><span class="mv">${detail.stateful ? 'Yes' : 'No'}</span></div>
      <div class="mrow"><span class="ml">Rules</span><span class="mv">${esc(String(detail.rule_count ?? 0))}</span></div>
      <div class="mrow"><span class="ml">Flagged rules</span><span class="mv ${detail.flagged_rule_count ? 'red' : 'green'}">${esc(String(detail.flagged_rule_count ?? 0))}</span></div>
      <div class="mrow"><span class="ml">Attached instances</span><span class="mv">${esc(String(detail.attachment_instance_count ?? 0))}</span></div>
      <div class="mrow"><span class="ml">Attached ports</span><span class="mv">${esc(String(detail.attachment_port_count ?? 0))}</span></div>
    </div>
  </div>`;
}

function renderSecurityGroupAuditFindings(findings) {
  return `<div class="card" style="margin-bottom:10px">
    <div class="card-title">Audit Findings</div>
    <div class="card-body">${!findings.length
      ? `<div style="color:var(--dim);font-size:12px">No current audit findings for this group.</div>`
      : findings.map(finding => {
        const cls = finding.severity === 'critical' ? 'bad' : finding.severity === 'high' ? 'warn' : 'good';
        const mark = finding.severity === 'critical' ? '!' : finding.severity === 'high' ? '!' : 'i';
        return `<div class="finding ${cls}" style="margin-bottom:8px">
          <div class="finding-mark">${mark}</div>
          <div><strong>${esc((finding.severity || 'info').toUpperCase())}</strong>${esc(finding.summary || finding.category || '')}${finding.count ? ` · ${esc(String(finding.count))}` : ''}</div>
        </div>`;
      }).join('')}</div>
  </div>`;
}

function renderSecurityGroupReferenceGraph(detail) {
  const fanout = detail.remote_group_fanout || {};
  const complexity = detail.control_plane_complexity || {};
  const referencedBy = detail.referenced_by || [];
  return `<div class="card" style="margin-bottom:10px">
    <div class="card-title">Reference Graph</div>
    <div class="card-body">
      <div class="mrow"><span class="ml">${sgHintLabel('Remote Group Fanout', 'How many distinct security groups this group directly references through remote-group rules.')}</span><span class="mv">${esc(String(fanout.direct_group_count ?? 0))}</span></div>
      <div class="mrow"><span class="ml">${sgHintLabel('Reference Graph Depth', 'The longest chain of remote-group references starting from this group. Depth 0 means no remote-group references.')}</span><span class="mv">${esc(String(detail.reference_graph_depth ?? 0))}</span></div>
      <div class="mrow"><span class="ml">${sgHintLabel('Referenced By', 'How many other security groups directly reference this group through remote-group rules. These entries are clickable below.')}</span><span class="mv">${esc(String(referencedBy.length))}</span></div>
      <div class="mrow"><span class="ml">${sgHintLabel('Control Plane Complexity', 'A lightweight estimate of rule-graph complexity based on fanout, depth, reverse references, and cycles.')}</span><span class="mv">${sgComplexityBadge(complexity.level)}</span></div>
      ${(fanout.groups || []).length ? `<div style="margin-top:10px;font-size:11px;color:var(--dim)">Direct references</div>
        <div style="margin-top:6px;display:flex;flex-wrap:wrap;gap:6px">${fanout.groups.map(item => `<button class="btn" style="padding:2px 8px;font-size:11px" onclick="selectSecurityGroup('${escAttr(item.id)}')">${esc(item.name)}</button>`).join('')}</div>` : ''}
      ${referencedBy.length ? `<div style="margin-top:10px;font-size:11px;color:var(--dim)">Referenced by</div>
        <div style="margin-top:6px;display:flex;flex-wrap:wrap;gap:6px">${referencedBy.map(item => `<button class="btn" style="padding:2px 8px;font-size:11px" onclick="selectSecurityGroup('${escAttr(item.id)}')">${esc(item.name)}</button>`).join('')}</div>` : ''}
      ${(complexity.reasons || []).length ? `<div style="margin-top:10px;font-size:11px;color:var(--dim)">Complexity notes</div>
        <div style="margin-top:6px">${complexity.reasons.map(reason => `<div style="font-size:12px;color:var(--text);padding:2px 0">${esc(reason)}</div>`).join('')}</div>` : ''}
    </div>
  </div>`;
}

function renderSecurityGroupRulesCard(title, rules, includeSeverity = false) {
  return `<div class="card" style="margin-bottom:10px">
    <div class="card-title">${esc(title)} (${rules.length})</div>
    <div class="card-body" style="padding:0">${!rules.length
      ? `<div style="color:var(--dim);font-size:12px;padding:10px 12px">No ${includeSeverity ? 'flagged ' : 'additional '}rules.</div>`
      : `<table class="data-table" style="font-size:11px;margin-bottom:0;border:none">
        <thead><tr><th>Dir</th><th>Protocol</th><th>Ports</th><th>Remote</th>${includeSeverity ? '<th>Severity</th>' : ''}</tr></thead>
        <tbody>${rules.map(rule => `<tr>
          <td>${esc(rule.direction || '—')}</td>
          <td>${esc(rule.protocol || '—')}</td>
          <td>${esc(rule.port_range || '—')}</td>
          <td>${sgRuleRemoteCell(rule)}</td>
          ${includeSeverity ? `<td><span class="${sgSeverityClass(rule.audit?.severity)}">${esc((rule.audit?.severity || 'clean').toUpperCase())}</span></td>` : ''}
        </tr>`).join('')}</tbody></table>`}
    </div>
  </div>`;
}

function renderSecurityGroupAttachments(attachments) {
  return `<div class="card" style="margin-bottom:10px">
    <div class="card-title">Attachments (${attachments.length})</div>
    <div class="card-body" style="padding:0">${!attachments.length
      ? `<div style="color:var(--dim);font-size:12px;padding:10px 12px">This group is not currently attached to any Neutron ports.</div>`
      : `<table class="data-table" style="font-size:11px;margin-bottom:0;border:none">
        <thead><tr><th>Port</th><th>Owner</th><th>Device</th><th>Network</th><th>IPs</th></tr></thead>
        <tbody>${attachments.map(item => `<tr>
          <td class="uuid-short" title="${esc(item.port_id || '')}">${esc((item.port_id || '').slice(0, 10) || '—')}</td>
          <td>${esc(item.device_owner || '—')}</td>
          <td class="uuid-short" title="${esc(item.device_id || '')}">${esc(item.device_id || '—')}</td>
          <td class="uuid-short" title="${esc(item.network_id || '')}">${esc((item.network_id || '').slice(0, 10) || '—')}</td>
          <td style="font-family:monospace;font-size:10px">${esc((item.fixed_ips || []).map(ip => ip.ip_address).filter(Boolean).join(', ') || '—')}</td>
        </tr>`).join('')}</tbody></table>`}
    </div>
  </div>`;
}

function renderSecurityGroupDetailContent(detail) {
  const findings = detail.audit?.findings || [];
  const rules = detail.rules || [];
  const attachments = detail.attachments || [];
  const flaggedRules = rules.filter(item => item.audit?.flagged);
  const safeRules = rules.filter(item => !item.audit?.flagged);
  return `<div class="net-detail-inner">
    <div class="net-detail-head">
      <strong style="font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:230px">${esc(detail.name || '(unnamed)')}</strong>
      <div style="display:flex;gap:6px;align-items:center;flex-shrink:0">
        ${sgSeverityBadge(detail.audit?.severity)}
        <button class="btn" style="padding:1px 7px;font-size:11px" onclick="closeSecurityGroupDetail()">✕</button>
      </div>
    </div>
    <div style="font-family:monospace;font-size:10px;color:var(--dim);word-break:break-all;margin-bottom:10px">${esc(detail.id || '')}</div>
    ${renderSecurityGroupProperties(detail)}
    ${renderSecurityGroupAuditFindings(findings)}
    ${renderSecurityGroupReferenceGraph(detail)}
    ${renderSecurityGroupRulesCard('Flagged Rules', flaggedRules, true)}
    ${renderSecurityGroupRulesCard('Other Rules', safeRules, false)}
    ${renderSecurityGroupAttachments(attachments)}
  </div>`;
}

function renderSecurityGroupDetail() {
  const wrap = document.getElementById('sg-detail-wrap');
  if (!wrap) return;
  if (sgDetailState.loading) {
    wrap.innerHTML = `<div class="net-detail-inner"><div style="color:var(--dim);padding:20px 0"><span class="spinner">⟳</span> Loading…</div></div>`;
    return;
  }
  const detail = sgDetailState.data;
  if (!detail) {
    wrap.innerHTML = '';
    return;
  }
  if (detail.error) {
    wrap.innerHTML = `<div class="net-detail-inner"><div class="err-block">${esc(detail.error)}</div></div>`;
    return;
  }
  try {
    wrap.innerHTML = renderSecurityGroupDetailContent(detail);
  } catch (e) {
    wrap.innerHTML = `<div class="net-detail-inner"><div class="err-block">Detail render failed: ${esc(String(e))}</div></div>`;
  }
}
