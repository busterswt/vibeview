'use strict';

function renderConfigureTab(nd) {
  const cache = nodeNetworkCache[nd.k8s_name];
  if (cache?.annotations && netEdit.node !== nd.k8s_name) _initNetEdit(nd.k8s_name);

  const managedNoSchedule = hasManagedNoScheduleTaint(nd);
  const noSchedule = noScheduleTaints(nd);
  let h = `<div class="summary-grid" style="margin-bottom:16px">
    <div class="card">
      <div class="card-title">Node Details</div>
      <div class="card-body">
        <div class="mrow"><span class="ml">K8s node name</span><span class="mv">${esc(nd.k8s_name)}</span></div>
        <div class="mrow"><span class="ml">Nova hypervisor <span class="hint">ESXi FQDN</span></span><span class="mv dim">${esc(nd.hypervisor)}</span></div>
        ${nd.kernel_version ? `<div class="mrow"><span class="ml">Kernel</span><span class="mv dim">${esc(nd.kernel_version)}</span></div>` : ''}
        ${nd.latest_kernel_version ? `<div class="mrow"><span class="ml">Latest installed kernel</span><span class="mv dim">${esc(nd.latest_kernel_version)}</span></div>` : ''}
        ${nd.uptime ? `<div class="mrow"><span class="ml">Uptime</span><span class="mv">${esc(nd.uptime)}</span></div>` : ''}
        <div class="mrow"><span class="ml">Reboot needed</span><span class="mv ${nd.reboot_required ? 'yellow' : ''}">${nd.reboot_required ? 'Yes' : 'No'}</span></div>
        <div class="mrow"><span class="ml">NoSchedule taint</span><span class="mv ${noSchedule.length ? 'red' : ''}">${noSchedule.length ? 'Yes' : 'No'}</span></div>
        <div class="mrow"><span class="ml">VibeView-managed NoSchedule</span><span class="mv ${managedNoSchedule ? 'yellow' : ''}">${managedNoSchedule ? 'Yes' : 'No'}</span></div>
        ${noSchedule.length ? `<div class="mrow"><span class="ml">Taint details</span><span class="mv mono" style="font-size:10px">${esc(noSchedule.map(taintLabel).join(', '))}</span></div>` : ''}
        <div class="mrow"><span class="ml">Maintenance taint action</span><span class="mv"><button class="btn ${managedNoSchedule ? 'warning' : ''}" onclick="actionToggleNoSchedule()">${managedNoSchedule ? '↺ Remove NoSchedule' : '＋ Add NoSchedule'}</button></span></div>
        <div class="mrow"><span class="ml">Is compute node</span><span class="mv">${nd.is_compute ? 'Yes' : 'No'}</span></div>
        ${nd.availability_zone ? `<div class="mrow"><span class="ml">Availability zone</span><span class="mv blue">${esc(nd.availability_zone)}</span></div>` : ''}
        ${nd.aggregates?.length ? `<div class="mrow"><span class="ml">Host aggregates</span><span class="mv" style="display:flex;flex-wrap:wrap;gap:3px">${nd.aggregates.map(a => `<span class="tree-badge agg" title="${esc(a)}">${esc(a)}</span>`).join('')}</span></div>` : ''}
        <div class="mrow"><span class="ml">Carries etcd role</span><span class="mv ${nd.is_etcd ? 'red' : ''}">${nd.is_etcd ? 'Yes' : 'No'}</span></div>
      </div>
    </div>`;
  if (nd.is_etcd) {
    const peers = Object.values(nodes).filter(n => n.is_etcd);
    const healthy = peers.filter(n => n.etcd_healthy === true).length;
    h += `<div class="card">
      <div class="card-title">etcd Cluster <span class="hint">vCenter HA</span></div>
      <div class="card-body">
        <div class="mrow"><span class="ml">Total members</span><span class="mv">${peers.length}</span></div>
        <div class="mrow"><span class="ml">Healthy</span><span class="mv green">${healthy}</span></div>
        <div class="mrow"><span class="ml">Quorum needed</span><span class="mv">${Math.floor(peers.length / 2) + 1}</span></div>
        <div class="mrow"><span class="ml">This node</span><span class="mv ${nd.etcd_healthy === true ? 'green' : nd.etcd_healthy === false ? 'red' : 'gray'}">${nd.etcd_healthy === true ? '✓ Healthy' : nd.etcd_healthy === false ? '✗ Unhealthy' : 'Unknown'}</span></div>
      </div>
    </div>`;
  }
  h += `</div>`;

  h += renderOvnSection(nd);
  h += renderNicSection(nd);

  h += `<div class="card vmware-ref-card" style="margin-top:16px">
    <div class="card-title">💡 VMware vSphere → OpenStack / Kubernetes Reference</div>
    <div class="card-body" style="padding:0">
      <table class="concept-table">
        <thead><tr><th>VMware vSphere</th><th>OpenStack / Kubernetes equivalent</th></tr></thead>
        <tbody>
          <tr><td>Datacenter</td><td>Region</td></tr>
          <tr><td>vSphere Cluster / DRS</td><td>Availability Zone + Nova Scheduler</td></tr>
          <tr><td>ESXi Host</td><td>Nova Hypervisor + K8s Node (same physical machine)</td></tr>
          <tr><td>VM</td><td>Nova Instance (QEMU/KVM) or K8s Pod</td></tr>
          <tr><td>Enter Maintenance Mode</td><td>Disable Nova service + Cordon K8s + Drain pods</td></tr>
          <tr><td>vMotion (live migration)</td><td>Nova live-migrate</td></tr>
          <tr><td>vApp</td><td>Helm Chart / K8s Deployment</td></tr>
          <tr><td>VM Template</td><td>Glance Image</td></tr>
          <tr><td>Datastore</td><td>Ceph / Cinder Volume (RBD-backed)</td></tr>
          <tr><td>vDS Port Group</td><td>Neutron Network / OVN logical port</td></tr>
          <tr><td>NSX-T</td><td>Neutron + OVN (or Calico for K8s)</td></tr>
          <tr><td>Resource Pool / Quota</td><td>K8s Namespace + ResourceQuota</td></tr>
          <tr><td>VM HA Restart</td><td>K8s Pod restart policy / Nova evacuate</td></tr>
          <tr><td>Load Balancer (NSX LB)</td><td>Octavia (Amphora)</td></tr>
          <tr><td>Snapshot</td><td>Cinder Volume Snapshot / etcd backup</td></tr>
        </tbody>
      </table>
    </div>
  </div>`;

  document.getElementById('configure-content').innerHTML = h;
  _bindConfigureTab(nd.k8s_name);
}

async function loadOvnAnnotations(nodeName, force = false) {
  const cache = nodeNetworkCache[nodeName] || {};
  if (cache.annLoading) return;
  nodeNetworkCache[nodeName] = { ...cache, annLoading: true, annFetchedAt: null };
  if (selectedNode === nodeName && activeTab === 'configure') renderConfigureTab(nodes[nodeName]);
  try {
    const resp = await fetch(`/api/nodes/${encodeURIComponent(nodeName)}/ovn-annotations`);
    const json = await resp.json();
    nodeNetworkCache[nodeName] = {
      ...nodeNetworkCache[nodeName],
      annLoading: false,
      annotations: json,
      annFetchedAt: new Date(),
      annError: json.error || null,
    };
    if (netEdit.node === nodeName || force) _initNetEdit(nodeName);
  } catch (e) {
    nodeNetworkCache[nodeName] = {
      ...nodeNetworkCache[nodeName],
      annLoading: false,
      annotations: null,
      annFetchedAt: new Date(),
      annError: String(e),
    };
  }
  if (selectedNode === nodeName && activeTab === 'configure') renderConfigureTab(nodes[nodeName]);
}

async function loadNetworkInterfaces(nodeName, force = false) {
  const cache = nodeNetworkCache[nodeName] || {};
  if (cache.ifacesLoading) return;
  nodeNetworkCache[nodeName] = { ...cache, ifacesLoading: true, ifaces: null, ifacesError: null, ifacesFetchedAt: null };
  if (selectedNode === nodeName && activeTab === 'configure') renderConfigureTab(nodes[nodeName]);
  try {
    const resp = await fetch(`/api/nodes/${encodeURIComponent(nodeName)}/network-interfaces`);
    const json = await resp.json();
    nodeNetworkCache[nodeName] = {
      ...nodeNetworkCache[nodeName],
      ifacesLoading: false,
      ifaces: json.interfaces || [],
      ifacesError: json.error || null,
      ifacesFetchedAt: new Date(),
    };
  } catch (e) {
    nodeNetworkCache[nodeName] = {
      ...nodeNetworkCache[nodeName],
      ifacesLoading: false,
      ifaces: [],
      ifacesError: String(e),
      ifacesFetchedAt: new Date(),
    };
  }
  if (selectedNode === nodeName && activeTab === 'configure') renderConfigureTab(nodes[nodeName]);
}

function _initNetEdit(nodeName) {
  const ann = (nodeNetworkCache[nodeName] || {}).annotations || {};
  netEdit.node = nodeName;
  netEdit.bridges = ann['ovn.openstack.org/bridges'] ? ann['ovn.openstack.org/bridges'].split(',').map(s => s.trim()).filter(Boolean) : [];
  netEdit.mappings = ann['ovn.openstack.org/mappings'] ? ann['ovn.openstack.org/mappings'].split(',').map(s => {
    const [p, b] = s.trim().split(':');
    return { physnet: p || '', bridge: b || '' };
  }).filter(m => m.physnet || m.bridge) : [];
  netEdit.ports = ann['ovn.openstack.org/ports'] ? ann['ovn.openstack.org/ports'].split(',').map(s => {
    const [b, i] = s.trim().split(':');
    return { bridge: b || '', iface: i || '' };
  }).filter(p => p.bridge || p.iface) : [];
  netEdit.bridgesDirty = false;
  netEdit.mappingsDirty = false;
  netEdit.portsDirty = false;
}

function _fmtTime(d) {
  if (!d) return '';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function renderOvnSection(nd) {
  const name = nd.k8s_name;
  const cache = nodeNetworkCache[name] || {};
  const ann = cache.annotations || {};
  const loading = cache.annLoading;
  const hasUnsaved = netEdit.node === name && (netEdit.bridgesDirty || netEdit.mappingsDirty || netEdit.portsDirty);

  let h = `<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.4px;color:var(--dim);padding-bottom:6px;margin:16px 0 10px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px">
    OVN Node Networking
    ${hasUnsaved ? `<span style="font-size:10px;font-weight:400;color:#f57f17;text-transform:none">● Unsaved changes</span>` : ''}
    <span style="margin-left:auto;display:flex;gap:5px">
      ${hasUnsaved ? `<button class="btn sm" onclick="cfgRevertAll()" title="Discard all pending changes">✕ Revert</button>` : ''}
      <button class="btn sm" onclick="loadOvnAnnotations('${escAttr(name)}', true)" ${loading ? 'disabled' : ''} title="Refresh annotations from cluster">↺ Refresh</button>
    </span>
  </div>`;

  if (loading) {
    h += `<div class="fetch-bar loading"><span class="spinner">⟳</span><span class="fb-msg">Reading OVN annotations from Kubernetes API…</span></div>`;
    return h;
  }
  if (cache.annError) {
    h += `<div class="fetch-bar error"><span class="fb-msg">⚠ ${esc(cache.annError)}</span>
      <button class="fb-btn" onclick="loadOvnAnnotations('${escAttr(name)}', true)">Retry</button></div>`;
    return h;
  }
  if (!cache.annotations && !loading) {
    h += `<div class="fetch-bar loading"><span class="spinner">⟳</span><span class="fb-msg">Reading OVN annotations from Kubernetes API…</span></div>`;
    return h;
  }
  if (cache.annFetchedAt) {
    h += `<div class="fetch-bar done">✓ <span class="fb-msg">Annotations loaded at ${_fmtTime(cache.annFetchedAt)}</span></div>`;
  }

  const bridges = netEdit.node === name ? netEdit.bridges : [];
  const mappings = netEdit.node === name ? netEdit.mappings : [];
  const ports = netEdit.node === name ? netEdit.ports : [];
  const ifaces = (cache.ifaces || []).filter(i => i.type === 'physical' || i.type === 'bond');
  const ifaceLoading = cache.ifacesLoading;

  h += `<div class="summary-grid" style="margin-bottom:12px">
    <div class="card">
      <div class="card-title"><span class="card-title-label">Tunnel &amp; Integration Bridge</span> <span class="hint">Read-only</span></div>
      <div class="card-body">
        <div class="mrow">
          <span class="ml" style="font-family:monospace;font-size:11px;font-weight:400">ovn.kubernetes.io/tunnel-interface</span>
          <span class="mv mono">${esc(ann['ovn.kubernetes.io/tunnel-interface'] || '—')}</span>
        </div>
        <div class="mrow">
          <span class="ml" style="font-family:monospace;font-size:11px;font-weight:400">ovn.openstack.org/int_bridge</span>
          <span class="mv mono">${esc(ann['ovn.openstack.org/int_bridge'] || '—')}</span>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">
        <span class="card-title-label" style="font-family:monospace;font-weight:400;text-transform:none;letter-spacing:0">ovn.openstack.org/bridges</span>
        <div class="card-title-actions">
          ${netEdit.bridgesDirty ? `<span class="unsaved-dot" title="Unsaved changes"></span>` : ''}
          <button class="btn sm primary" id="cfg-save-bridges" onclick="cfgSaveBridges()" ${netEdit.bridgesDirty ? '' : 'disabled'}>Save</button>
        </div>
      </div>
      <div class="card-body">
        <div style="font-size:11px;color:var(--dim);margin-bottom:6px">OVS bridges managed by OVN on this node.</div>
        <div class="bridge-list" id="cfg-bridge-list">
          ${bridges.map((b, i) => `<span class="bridge-chip">${esc(b)}<span class="bx" onclick="cfgRemoveBridge(${i})" title="Remove">✕</span></span>`).join('')}
          ${bridges.length === 0 ? `<span style="color:var(--dim);font-size:11px">No bridges configured</span>` : ''}
        </div>
        <div class="add-chip-row">
          <input type="text" id="cfg-bridge-input" placeholder="br-phys" maxlength="40">
          <button class="btn sm primary" onclick="cfgAddBridge()">+ Add</button>
        </div>
      </div>
    </div>
  </div>`;

  h += `<div class="card" style="margin-bottom:12px">
    <div class="card-title">
      <span class="card-title-label" style="font-family:monospace;font-weight:400;text-transform:none;letter-spacing:0">ovn.openstack.org/mappings</span>
      <span class="hint">physnet → bridge</span>
      <div class="card-title-actions">
        ${netEdit.mappingsDirty ? `<span class="unsaved-dot" title="Unsaved changes"></span>` : ''}
        <button class="btn sm primary" id="cfg-save-mappings" onclick="cfgSaveMappings()" ${netEdit.mappingsDirty ? '' : 'disabled'}>Save</button>
      </div>
    </div>
    <div style="padding:5px 12px 2px;font-size:11px;color:var(--dim)">
      Maps a physical network name to an OVS bridge.
      The bridge must exist in <code style="background:var(--gray-bg);padding:1px 4px;border-radius:2px">ovn.openstack.org/bridges</code>.
    </div>
    <table class="ann-table">
      <thead><tr>
        <th style="width:32px">#</th>
        <th>Physical Network Name</th>
        <th style="width:16px"></th>
        <th>OVS Bridge</th>
        <th style="width:32px"></th>
      </tr></thead>
      <tbody id="cfg-mappings-body">
        ${mappings.map((m, i) => `<tr>
          <td style="color:var(--dim);font-size:11px">${i + 1}</td>
          <td><input type="text" value="${esc(m.physnet)}" style="width:180px"
              oninput="netEdit.mappings[${i}].physnet=this.value;netEdit.mappingsDirty=true;_cfgMarkDirty()"
              placeholder="physnet1"></td>
          <td style="color:var(--dim);text-align:center;padding:0">:</td>
          <td>
            <select onchange="netEdit.mappings[${i}].bridge=this.value;netEdit.mappingsDirty=true;_cfgMarkDirty()">
              ${bridges.length ? bridges.map(b => `<option value="${esc(b)}" ${b === m.bridge ? 'selected' : ''}>${esc(b)}</option>`).join('') : `<option value="${esc(m.bridge)}">${esc(m.bridge) || '(no bridges)'}</option>`}
            </select>
            ${m.bridge && !bridges.includes(m.bridge) ? `<span class="err-chip" style="margin-left:4px" title="Bridge not in bridges list">⚠ not in bridges</span>` : ''}
          </td>
          <td><button class="del-btn" onclick="cfgRemoveMapping(${i})" title="Remove">✕</button></td>
        </tr>`).join('')}
      </tbody>
    </table>
    <div class="add-row-btn" onclick="cfgAddMapping()">+ Add mapping</div>
  </div>`;

  const buildIfaceOptions = (selectedIface) => {
    if (!ifaces.length) return `<option value="${esc(selectedIface)}">${esc(selectedIface) || '—'}</option>`;
    return ifaces.map(n => {
      const ips = [...(n.ipv4 || []), ...(n.ipv6 || [])];
      const hasIp = ips.length > 0;
      const reason = hasIp ? ` — has IP: ${ips.join(', ')}` : '';
      return `<option value="${esc(n.name)}" ${n.name === selectedIface ? 'selected' : ''} ${hasIp ? 'disabled style="color:#bdbdbd"' : ''}>${esc(n.name)}${esc(reason)}</option>`;
    }).join('');
  };

  h += `<div class="card" style="margin-bottom:16px">
    <div class="card-title">
      <span class="card-title-label" style="font-family:monospace;font-weight:400;text-transform:none;letter-spacing:0">ovn.openstack.org/ports</span>
      <span class="hint">bridge → physical interface</span>
      <div class="card-title-actions">
        ${netEdit.portsDirty ? `<span class="unsaved-dot" title="Unsaved changes"></span>` : ''}
        <button class="btn sm primary" id="cfg-save-ports" onclick="cfgSavePorts()" ${netEdit.portsDirty ? '' : 'disabled'}>Save</button>
      </div>
    </div>
    <div style="padding:5px 12px 2px;font-size:11px;color:var(--dim)">
      Maps an OVS bridge to a physical or bond interface.
      The bridge must be in <code style="background:var(--gray-bg);padding:1px 4px;border-radius:2px">ovn.openstack.org/bridges</code>.
      The interface is selected from interfaces discovered on this node.
    </div>
    <table class="ann-table">
      <thead><tr>
        <th style="width:32px">#</th>
        <th>OVS Bridge</th>
        <th style="width:16px"></th>
        <th>Physical / Bond Interface ${ifaceLoading ? `<span class="spinner" style="font-size:10px">⟳</span>` : ''}</th>
        <th style="width:32px"></th>
      </tr></thead>
      <tbody id="cfg-ports-body">
        ${ports.map((p, i) => {
          const usedByOthers = new Set(ports.filter((_, j) => j !== i).map(x => x.bridge));
          const isDupeBridge = p.bridge && usedByOthers.has(p.bridge);
          const bridgeOpts = bridges.length
            ? bridges.map(b => {
              const taken = usedByOthers.has(b);
              return `<option value="${esc(b)}" ${b === p.bridge ? 'selected' : ''} ${taken ? 'disabled' : ''}>${esc(b)}${taken ? ' (in use)' : ''}</option>`;
            }).join('')
            : `<option value="${esc(p.bridge)}">${esc(p.bridge) || '(no bridges)'}</option>`;
          return `<tr ${isDupeBridge ? 'style="background:#fff8f8"' : ''}>
            <td style="color:var(--dim);font-size:11px">${i + 1}</td>
            <td>
              <select onchange="netEdit.ports[${i}].bridge=this.value;netEdit.portsDirty=true;_cfgMarkDirty()">
                ${bridgeOpts}
              </select>
              ${isDupeBridge ? `<span class="err-chip" style="margin-left:4px" title="Each bridge can only have one port">⚠ duplicate bridge</span>` : ''}
              ${p.bridge && !bridges.includes(p.bridge) ? `<span class="err-chip" style="margin-left:4px" title="Bridge not in bridges list">⚠ not in bridges</span>` : ''}
            </td>
            <td style="color:var(--dim);text-align:center;padding:0">:</td>
            <td>
              ${ifaceLoading
                ? `<span style="color:var(--dim);font-size:11px"><span class="spinner">⟳</span> Loading…</span>`
                : ifaces.length > 0
                  ? `<select onchange="netEdit.ports[${i}].iface=this.value;netEdit.portsDirty=true;_cfgMarkDirty()">${buildIfaceOptions(p.iface)}</select>`
                  : `<input type="text" value="${esc(p.iface)}" style="width:160px;font-family:monospace" oninput="netEdit.ports[${i}].iface=this.value;netEdit.portsDirty=true;_cfgMarkDirty()">`
              }
            </td>
            <td><button class="del-btn" onclick="cfgRemovePort(${i})" title="Remove">✕</button></td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>
    ${bridges.length > ports.length
      ? `<div class="add-row-btn" onclick="cfgAddPort()">+ Add port mapping</div>`
      : `<div style="padding:6px 10px 5px;font-size:11px;color:var(--dim);border-top:1px solid #f0f2f5">All bridges already have a port assigned.</div>`
    }
  </div>`;

  return h;
}

function renderNicSection(nd) {
  const cache = nodeNetworkCache[nd.k8s_name] || {};
  const name = nd.k8s_name;

  let h = `<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.4px;color:var(--dim);padding-bottom:6px;margin:16px 0 10px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px">
    Physical Interfaces &amp; Bonds
    <span style="margin-left:auto">
      <button class="btn sm" onclick="loadNetworkInterfaces('${escAttr(name)}', true)" ${cache.ifacesLoading ? 'disabled' : ''} title="Refresh interface list">↺ Refresh</button>
    </span>
  </div>`;

  if (cache.ifacesLoading) {
    h += `<div class="fetch-bar loading"><span class="spinner">⟳</span>
      <span class="fb-msg">Discovering interfaces from the node agent — this may take up to 20 seconds…</span></div>`;
    return h;
  }
  if (cache.ifacesError) {
    h += `<div class="fetch-bar error"><span class="fb-msg">⚠ Host detail error: ${esc(cache.ifacesError)}</span>
      <button class="fb-btn" onclick="loadNetworkInterfaces('${escAttr(name)}', true)">Retry</button></div>`;
    return h;
  }
  if (!cache.ifaces) {
    h += `<div class="fetch-bar loading"><span class="spinner">⟳</span>
      <span class="fb-msg">Discovering interfaces from the node agent — this may take up to 20 seconds…</span></div>`;
    return h;
  }
  if (cache.ifacesFetchedAt) {
    h += `<div class="fetch-bar done">✓ <span class="fb-msg">Interface data fetched at ${_fmtTime(cache.ifacesFetchedAt)}</span></div>`;
  }

  const physicals = cache.ifaces.filter(n => n.type === 'physical');
  const bonds = cache.ifaces.filter(n => n.type === 'bond');

  function speedCls(s) {
    if (!s) return 'gunk';
    if (s.startsWith('100')) return 'g100';
    if (s.startsWith('40')) return 'g40';
    if (s.startsWith('25')) return 'g25';
    if (s.startsWith('10G')) return 'g10';
    if (s.startsWith('1G')) return 'g1';
    return 'gunk';
  }
  function statusDot(st) {
    const c = st === 'up' ? 'green' : 'gray';
    return `<span class="tree-dot ${c}" style="display:inline-block;margin-right:4px;flex-shrink:0"></span>${esc(st)}`;
  }

  if (physicals.length) {
    h += `<div style="font-size:11px;font-weight:600;color:var(--dim);margin-bottom:6px">Physical Interfaces</div>
    <table class="data-table" style="margin-bottom:14px">
      <thead><tr>
        <th>Interface</th><th>Status</th><th>Speed</th><th>Duplex</th>
        <th>Driver</th><th>Model</th><th>MAC</th>
      </tr></thead>
      <tbody>
        ${physicals.map(n => `<tr>
          <td style="font-family:monospace;font-weight:600">${esc(n.name)}</td>
          <td>${statusDot(n.status)}</td>
          <td>${n.speed ? `<span class="nic-speed ${speedCls(n.speed)}">${esc(n.speed)}</span>` : '<span style="color:var(--dim)">—</span>'}</td>
          <td style="color:var(--dim);font-size:11px">${esc(n.duplex || '—')}</td>
          <td style="font-family:monospace;font-size:11px;color:var(--dim)">${esc(n.driver || '—')}</td>
          <td style="font-size:11px;color:var(--dim)">${esc(n.model || (n.vendor ? n.vendor : '—'))}</td>
          <td style="font-family:monospace;font-size:11px;color:var(--dim)">${esc(n.mac || '—')}</td>
        </tr>`).join('')}
      </tbody>
    </table>`;
  }

  if (bonds.length) {
    h += `<div style="font-size:11px;font-weight:600;color:var(--dim);margin-bottom:6px">Bond Interfaces</div>
    <table class="data-table" style="margin-bottom:14px">
      <thead><tr>
        <th>Bond</th><th>Status</th><th>Speed</th><th>Mode</th><th>MAC</th><th>Members</th>
      </tr></thead>
      <tbody>
        ${bonds.map(n => `<tr>
          <td style="font-family:monospace;font-weight:600">${esc(n.name)}</td>
          <td>${statusDot(n.status)}</td>
          <td>${n.speed ? `<span class="nic-speed ${speedCls(n.speed)}">${esc(n.speed)}</span>` : '<span style="color:var(--dim)">—</span>'}</td>
          <td style="font-size:11px;color:var(--dim)">${esc(n.mode || '—')}</td>
          <td style="font-family:monospace;font-size:11px;color:var(--dim)">${esc(n.mac || '—')}</td>
          <td>${n.members.map(m => `<span class="tree-badge" style="margin-right:3px;font-family:monospace">${esc(m)}</span>`).join('')}</td>
        </tr>`).join('')}
      </tbody>
    </table>`;
  }

  if (!physicals.length && !bonds.length) {
    h += `<div style="color:var(--dim);font-size:12px;padding:6px 0">No physical or bond interfaces found.</div>`;
  }
  return h;
}

function _bindConfigureTab(nodeName) {
  const inp = document.getElementById('cfg-bridge-input');
  if (inp) inp.onkeydown = null;
}

function _cfgMarkDirty() {
  const saveB = document.getElementById('cfg-save-bridges');
  const saveM = document.getElementById('cfg-save-mappings');
  const saveP = document.getElementById('cfg-save-ports');
  if (saveB) saveB.disabled = !netEdit.bridgesDirty;
  if (saveM) saveM.disabled = !netEdit.mappingsDirty;
  if (saveP) saveP.disabled = !netEdit.portsDirty;
}

function cfgRevertAll() {
  if (!confirm('Discard all pending changes to bridges, mappings, and ports?\n\nThis will restore the values currently stored in Kubernetes.')) return;
  _initNetEdit(netEdit.node);
  renderConfigureTab(nodes[netEdit.node]);
}

function cfgAddBridge() {
  const inp = document.getElementById('cfg-bridge-input');
  const val = (inp?.value || '').trim();
  if (!val) return;
  if (!/^[a-zA-Z0-9_-]+$/.test(val)) {
    alert('Bridge name must contain only letters, numbers, hyphens, or underscores.');
    return;
  }
  if (netEdit.bridges.includes(val)) {
    alert(`Bridge "${val}" already exists.`);
    return;
  }
  netEdit.bridges.push(val);
  netEdit.bridgesDirty = true;
  renderConfigureTab(nodes[netEdit.node]);
}

function cfgRemoveBridge(i) {
  const name = netEdit.bridges[i];
  const usedM = netEdit.mappings.some(m => m.bridge === name);
  const usedP = netEdit.ports.some(p => p.bridge === name);
  const isLast = netEdit.bridges.length === 1;
  const refs = [usedM && 'Mappings', usedP && 'Ports'].filter(Boolean).join(' and ');
  let msg = null;
  if (isLast && (usedM || usedP)) {
    msg = `"${name}" is the last bridge and is referenced in ${refs}.\n\nRemoving it will leave Mappings and Ports with no valid bridge, which will break data plane connectivity.\n\nRemove anyway?`;
  } else if (isLast) {
    msg = `"${name}" is the last bridge.\n\nRemoving all bridges will break data plane connectivity on this node.\n\nRemove anyway?`;
  } else if (usedM || usedP) {
    msg = `Bridge "${name}" is referenced in ${refs}. Remove anyway?`;
  }
  if (msg && !confirm(msg)) return;
  netEdit.bridges.splice(i, 1);
  netEdit.bridgesDirty = true;
  renderConfigureTab(nodes[netEdit.node]);
}

function cfgSaveBridges() {
  const val = netEdit.bridges.join(',');
  _annWarnAndSave('ovn.openstack.org/bridges', val, () => {
    netEdit.bridgesDirty = false;
    _cfgMarkDirty();
  });
}

function cfgAddMapping() {
  netEdit.mappings.push({ physnet: '', bridge: netEdit.bridges[0] || '' });
  netEdit.mappingsDirty = true;
  renderConfigureTab(nodes[netEdit.node]);
  const inputs = document.querySelectorAll('#cfg-mappings-body input[type=text]');
  if (inputs.length) inputs[inputs.length - 1].focus();
}

function cfgRemoveMapping(i) {
  if (netEdit.mappings.length === 1) {
    if (!confirm('This is the last mapping entry.\n\nRemoving it will leave no physical network to bridge mappings, which will break data plane connectivity.\n\nRemove anyway?')) return;
  }
  netEdit.mappings.splice(i, 1);
  netEdit.mappingsDirty = true;
  renderConfigureTab(nodes[netEdit.node]);
}

function cfgSaveMappings() {
  const val = netEdit.mappings.filter(m => m.physnet && m.bridge).map(m => `${m.physnet}:${m.bridge}`).join(',');
  _annWarnAndSave('ovn.openstack.org/mappings', val, () => {
    netEdit.mappingsDirty = false;
    _cfgMarkDirty();
  });
}

function cfgAddPort() {
  const usedBridges = new Set(netEdit.ports.map(p => p.bridge));
  const freeBridge = netEdit.bridges.find(b => !usedBridges.has(b));
  if (!freeBridge) return;
  const ifaces = ((nodeNetworkCache[netEdit.node] || {}).ifaces || []).filter(i => i.type === 'physical' || i.type === 'bond');
  const defaultIface = ifaces.find(i => !i.ipv4?.length && !i.ipv6?.length) || ifaces[0];
  netEdit.ports.push({ bridge: freeBridge, iface: defaultIface?.name || '' });
  netEdit.portsDirty = true;
  renderConfigureTab(nodes[netEdit.node]);
}

function cfgRemovePort(i) {
  if (netEdit.ports.length === 1) {
    if (!confirm('This is the last port mapping entry.\n\nRemoving it will leave no bridge to physical interface assignments, which will break data plane connectivity.\n\nRemove anyway?')) return;
  }
  netEdit.ports.splice(i, 1);
  netEdit.portsDirty = true;
  renderConfigureTab(nodes[netEdit.node]);
}

function cfgSavePorts() {
  const valid = netEdit.ports.filter(p => p.bridge && p.iface);
  const bridgeCounts = valid.reduce((m, p) => {
    m[p.bridge] = (m[p.bridge] || 0) + 1;
    return m;
  }, {});
  const dupes = Object.entries(bridgeCounts).filter(([, n]) => n > 1).map(([b]) => b);
  if (dupes.length) {
    alert(`Cannot save: bridge(s) ${dupes.map(b => `"${b}"`).join(', ')} appear more than once. Each bridge may only have one port.`);
    return;
  }
  const val = valid.map(p => `${p.bridge}:${p.iface}`).join(',');
  _annWarnAndSave('ovn.openstack.org/ports', val, () => {
    netEdit.portsDirty = false;
    _cfgMarkDirty();
  });
}

function _annWarnAndSave(key, value, successCb) {
  _annWarnPending = { key, value, successCb };
  document.getElementById('awm-key').textContent = key;
  document.getElementById('awm-val').textContent = value || '(empty — will clear annotation)';
  document.getElementById('ann-warn-modal').classList.add('open');
}

function annWarnCancel() {
  document.getElementById('ann-warn-modal').classList.remove('open');
  _annWarnPending = null;
}

async function annWarnConfirm() {
  if (!_annWarnPending) return;
  const { key, value, successCb } = _annWarnPending;
  _annWarnPending = null;
  document.getElementById('ann-warn-modal').classList.remove('open');

  const nodeName = netEdit.node;
  try {
    const resp = await fetch(`/api/nodes/${encodeURIComponent(nodeName)}/ovn-annotations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, value }),
    });
    const json = await resp.json();
    if (!json.ok) throw new Error(json.error || 'API error');
    if (nodeNetworkCache[nodeName]?.annotations) {
      nodeNetworkCache[nodeName].annotations[key] = value;
    }
    successCb?.();
    _showToast('✓ Annotation saved', `${key} = ${value || '(cleared)'}`, 'green');
  } catch (e) {
    _showToast('✗ Failed to save annotation', String(e), 'red');
  }
  if (selectedNode === nodeName && activeTab === 'configure') renderConfigureTab(nodes[nodeName]);
}

function _showToast(title, body, type) {
  const t = document.createElement('div');
  const bc = type === 'green' ? 'var(--green-lt)' : type === 'red' ? 'var(--red-lt)' : '#f57f17';
  t.style.cssText = `position:fixed;bottom:20px;right:20px;z-index:9999;background:#1a2332;color:white;
    padding:10px 16px;border-radius:6px;font-size:12px;max-width:440px;
    box-shadow:0 4px 12px rgba(0,0,0,0.25);border-left:3px solid ${bc};`;
  t.innerHTML = `<strong>${esc(title)}</strong><br>
    <span style="font-family:monospace;font-size:11px;color:#cfd8dc;word-break:break-all">${esc(body)}</span>`;
  document.body.appendChild(t);
  setTimeout(() => {
    t.style.transition = 'opacity .4s';
    t.style.opacity = '0';
    setTimeout(() => t.remove(), 400);
  }, 4000);
}
