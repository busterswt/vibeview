'use strict';

const sharedNetworkingOverlayState = globalThis.networkingOverlayState || {
  loading: false,
  loaded: false,
  error: null,
  vpcs: [],
  subnets: [],
  vlans: [],
  providernetworks: [],
  services: [],
  lbs: [],
  gateways: [],
  httproutes: [],
  clusternetworks: [],
  networkdomains: [],
};
globalThis.networkingOverlayState = sharedNetworkingOverlayState;

function overlayIsIpv4(value) {
  return /^\d{1,3}(\.\d{1,3}){3}$/.test(String(value || '').trim());
}

function overlayIpv4ToInt(value) {
  if (!overlayIsIpv4(value)) return null;
  const parts = String(value).trim().split('.').map(Number);
  if (parts.some(part => !Number.isInteger(part) || part < 0 || part > 255)) return null;
  return (((parts[0] << 24) >>> 0) + (parts[1] << 16) + (parts[2] << 8) + parts[3]) >>> 0;
}

function overlayIpInCidr(ip, cidr) {
  const ipInt = overlayIpv4ToInt(ip);
  if (ipInt == null) return false;
  const [base, prefixRaw] = String(cidr || '').split('/');
  const baseInt = overlayIpv4ToInt(base);
  const prefix = Number(prefixRaw);
  if (baseInt == null || !Number.isInteger(prefix) || prefix < 0 || prefix > 32) return false;
  const mask = prefix === 0 ? 0 : (0xffffffff << (32 - prefix)) >>> 0;
  return (ipInt & mask) === (baseInt & mask);
}

function overlayEndpointMatchesCidrs(values, cidrs) {
  return (values || []).some(value => (cidrs || []).some(cidr => overlayIpInCidr(value, cidr)));
}

async function overlayFetchJsonWithTimeout(url, timeoutMs = 8000) {
  const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
  try {
    const resp = await fetch(url, controller ? { signal: controller.signal } : undefined);
    return await resp.json();
  } catch (e) {
    if (e?.name === 'AbortError') {
      throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)}s`);
    }
    throw e;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function ensureNetworkingOverlayData() {
  if (sharedNetworkingOverlayState.loaded || sharedNetworkingOverlayState.loading) return;
  sharedNetworkingOverlayState.loading = true;
  sharedNetworkingOverlayState.error = null;
  try {
    const loaders = [
      ['vpcs', '/api/k8s/vpcs'],
      ['subnets', '/api/k8s/subnets'],
      ['vlans', '/api/k8s/vlans'],
      ['providernetworks', '/api/k8s/provider-networks'],
      ['services', '/api/k8s/services'],
      ['gateways', '/api/k8s/gateways'],
      ['httproutes', '/api/k8s/httproutes'],
      ['clusternetworks', '/api/k8s/cluster-networks'],
      ['networkdomains', '/api/k8s/network-domains'],
    ];
    const results = await Promise.all(loaders.map(([, url]) => overlayFetchJsonWithTimeout(url, 8000)));
    for (const [index, [key]] of loaders.entries()) {
      const json = results[index] || {};
      if (json.error) throw new Error(json.error);
      sharedNetworkingOverlayState[key] = json.items || [];
      if (key === 'services') {
        sharedNetworkingOverlayState.lbs = (json.items || []).filter(item => item.type === 'LoadBalancer');
      }
    }
    sharedNetworkingOverlayState.loaded = true;
  } catch (e) {
    sharedNetworkingOverlayState.error = String(e);
  } finally {
    sharedNetworkingOverlayState.loading = false;
    if (typeof selectedNetwork !== 'undefined' && selectedNetwork && typeof renderNetworkDetail === 'function') renderNetworkDetail();
    if (typeof selectedRouter !== 'undefined' && selectedRouter && typeof renderRouterDetail === 'function') renderRouterDetail();
    if (typeof selectedLoadBalancer !== 'undefined' && selectedLoadBalancer && typeof renderLoadBalancerDetail === 'function') renderLoadBalancerDetail();
  }
}

function gatewayRoutesForGateway(gateway) {
  if (!gateway) return [];
  return (sharedNetworkingOverlayState.httproutes || []).filter(route =>
    (route.parent_refs || []).some(parent => {
      const [parentName] = String(parent || '').split('/');
      return parentName === gateway.name && route.namespace === gateway.namespace;
    }),
  );
}

function renderOverlayCard(title, body) {
  return `<div class="card" style="margin-bottom:10px">
    <div class="card-title">${title}</div>
    <div class="card-body">${body}</div>
  </div>`;
}

function renderNetworkOverlayCard(network) {
  ensureNetworkingOverlayData();
  if (sharedNetworkingOverlayState.loading && !sharedNetworkingOverlayState.loaded) {
    return renderOverlayCard('Kubernetes Overlay', '<div style="color:var(--dim);font-size:12px"><span class="spinner">⟳</span> Loading Kubernetes overlay relationships…</div>');
  }
  if (sharedNetworkingOverlayState.error) {
    return renderOverlayCard('Kubernetes Overlay', `<div class="err-block">${esc(sharedNetworkingOverlayState.error)}</div>`);
  }
  const cidrs = (network.subnets || []).map(item => item.cidr).filter(Boolean);
  const matchingServices = (sharedNetworkingOverlayState.lbs || []).filter(item => overlayEndpointMatchesCidrs(item.external_ips, cidrs));
  const matchingGateways = (sharedNetworkingOverlayState.gateways || []).filter(item => overlayEndpointMatchesCidrs(item.addresses, cidrs));
  const matchingSubnets = (sharedNetworkingOverlayState.subnets || []).filter(item => (cidrs || []).includes(item.cidr));
  const subnetNames = new Set(matchingSubnets.map(item => item.name));
  const matchingVpcs = (sharedNetworkingOverlayState.vpcs || []).filter(item => (item.subnets || []).some(name => subnetNames.has(name)));
  const routeMap = new Map();
  for (const gateway of matchingGateways) {
    for (const route of gatewayRoutesForGateway(gateway)) routeMap.set(`${route.namespace}/${route.name}`, route);
  }
  const matchingDomains = (sharedNetworkingOverlayState.networkdomains || []).filter(item =>
    overlayEndpointMatchesCidrs(item.external_endpoints, cidrs),
  );
  const hasMatches = matchingServices.length || matchingGateways.length || routeMap.size || matchingDomains.length || matchingSubnets.length || matchingVpcs.length;
  return renderOverlayCard('Kubernetes Overlay', `
    <div class="mrow"><span class="ml">Kube-OVN VPCs</span><span class="mv">${matchingVpcs.length || '—'}</span></div>
    <div class="mrow"><span class="ml">Kube-OVN subnets</span><span class="mv">${matchingSubnets.length || '—'}</span></div>
    <div class="mrow"><span class="ml">Network domains</span><span class="mv">${matchingDomains.length || '—'}</span></div>
    <div class="mrow"><span class="ml">LoadBalancer services</span><span class="mv">${matchingServices.length || '—'}</span></div>
    <div class="mrow"><span class="ml">Gateways</span><span class="mv">${matchingGateways.length || '—'}</span></div>
    <div class="mrow"><span class="ml">HTTPRoutes</span><span class="mv">${routeMap.size || '—'}</span></div>
    ${!hasMatches ? `<div style="margin-top:8px;color:var(--dim);font-size:12px">No overlay relationships found for this network.</div>` : ''}
    ${matchingVpcs.length ? `<div class="mrow"><span class="ml">VPC names</span><span class="mv">${esc(matchingVpcs.map(item => item.name).join(', '))}</span></div>` : ''}
    ${matchingSubnets.length ? `<div class="mrow"><span class="ml">Subnet names</span><span class="mv">${esc(matchingSubnets.map(item => item.name).join(', '))}</span></div>` : ''}
    ${matchingDomains.length ? `<div class="mrow"><span class="ml">Namespaces</span><span class="mv">${esc(matchingDomains.map(item => item.namespace).join(', '))}</span></div>` : ''}
    ${matchingServices.length ? `<div class="mrow"><span class="ml">Service VIPs</span><span class="mv" style="font-size:10px">${esc(matchingServices.map(item => `${item.namespace}/${item.name} → ${(item.external_ips || []).join(', ')}`).join(' | '))}</span></div>` : ''}
    ${matchingGateways.length ? `<div class="mrow"><span class="ml">Gateway addrs</span><span class="mv" style="font-size:10px">${esc(matchingGateways.map(item => `${item.namespace}/${item.name} → ${(item.addresses || []).join(', ')}`).join(' | '))}</span></div>` : ''}
  `);
}

function renderRouterOverlayCard(router) {
  ensureNetworkingOverlayData();
  if (sharedNetworkingOverlayState.loading && !sharedNetworkingOverlayState.loaded) {
    return renderOverlayCard('Kubernetes Overlay', '<div style="color:var(--dim);font-size:12px"><span class="spinner">⟳</span> Loading Kubernetes overlay relationships…</div>');
  }
  if (sharedNetworkingOverlayState.error) {
    return renderOverlayCard('Kubernetes Overlay', `<div class="err-block">${esc(sharedNetworkingOverlayState.error)}</div>`);
  }
  const cidrs = (router.connected_subnets || []).map(item => item.cidr).filter(Boolean);
  const matchingServices = (sharedNetworkingOverlayState.lbs || []).filter(item => overlayEndpointMatchesCidrs(item.external_ips, cidrs));
  const matchingGateways = (sharedNetworkingOverlayState.gateways || []).filter(item => overlayEndpointMatchesCidrs(item.addresses, cidrs));
  const matchingDomains = (sharedNetworkingOverlayState.networkdomains || []).filter(item => overlayEndpointMatchesCidrs(item.external_endpoints, cidrs));
  const matchingSubnets = (sharedNetworkingOverlayState.subnets || []).filter(item => (cidrs || []).includes(item.cidr));
  const subnetNames = new Set(matchingSubnets.map(item => item.name));
  const matchingVpcs = (sharedNetworkingOverlayState.vpcs || []).filter(item => (item.subnets || []).some(name => subnetNames.has(name)));
  const hasMatches = matchingServices.length || matchingGateways.length || matchingDomains.length || matchingSubnets.length || matchingVpcs.length;
  return renderOverlayCard('Kubernetes Overlay', `
    <div class="mrow"><span class="ml">Connected subnets</span><span class="mv">${cidrs.length || '—'}</span></div>
    <div class="mrow"><span class="ml">Kube-OVN VPCs</span><span class="mv">${matchingVpcs.length || '—'}</span></div>
    <div class="mrow"><span class="ml">Kube-OVN subnets</span><span class="mv">${matchingSubnets.length || '—'}</span></div>
    <div class="mrow"><span class="ml">Network domains</span><span class="mv">${matchingDomains.length || '—'}</span></div>
    <div class="mrow"><span class="ml">LoadBalancer services</span><span class="mv">${matchingServices.length || '—'}</span></div>
    <div class="mrow"><span class="ml">Gateways</span><span class="mv">${matchingGateways.length || '—'}</span></div>
    ${!hasMatches ? `<div style="margin-top:8px;color:var(--dim);font-size:12px">No overlay relationships found for this router.</div>` : ''}
    ${matchingVpcs.length ? `<div class="mrow"><span class="ml">VPC names</span><span class="mv">${esc(matchingVpcs.map(item => item.name).join(', '))}</span></div>` : ''}
    ${matchingServices.length ? `<div class="mrow"><span class="ml">Service VIPs</span><span class="mv" style="font-size:10px">${esc(matchingServices.map(item => `${item.namespace}/${item.name}`).join(', '))}</span></div>` : ''}
    ${matchingGateways.length ? `<div class="mrow"><span class="ml">Gateway namespaces</span><span class="mv">${esc(matchingGateways.map(item => item.namespace).join(', '))}</span></div>` : ''}
  `);
}

function renderLoadBalancerOverlayCard(lb) {
  ensureNetworkingOverlayData();
  if (sharedNetworkingOverlayState.loading && !sharedNetworkingOverlayState.loaded) {
    return renderOverlayCard('Kubernetes Overlay', '<div style="color:var(--dim);font-size:12px"><span class="spinner">⟳</span> Loading Kubernetes overlay relationships…</div>');
  }
  if (sharedNetworkingOverlayState.error) {
    return renderOverlayCard('Kubernetes Overlay', `<div class="err-block">${esc(sharedNetworkingOverlayState.error)}</div>`);
  }
  const ips = [lb.vip_address, lb.floating_ip].filter(Boolean);
  const matchingServices = (sharedNetworkingOverlayState.lbs || []).filter(item =>
    (item.external_ips || []).some(value => ips.includes(value)),
  );
  const matchingGateways = (sharedNetworkingOverlayState.gateways || []).filter(item =>
    (item.addresses || []).some(value => ips.includes(value)),
  );
  const matchingSubnets = (sharedNetworkingOverlayState.subnets || []).filter(item =>
    ips.some(value => overlayIpInCidr(value, item.cidr)),
  );
  const subnetNames = new Set(matchingSubnets.map(item => item.name));
  const matchingVpcs = (sharedNetworkingOverlayState.vpcs || []).filter(item => (item.subnets || []).some(name => subnetNames.has(name)));
  const routeMap = new Map();
  for (const gateway of matchingGateways) {
    for (const route of gatewayRoutesForGateway(gateway)) routeMap.set(`${route.namespace}/${route.name}`, route);
  }
  const hasMatches = matchingServices.length || matchingGateways.length || routeMap.size || matchingSubnets.length || matchingVpcs.length;
  return renderOverlayCard('Kubernetes Overlay', `
    <div class="mrow"><span class="ml">Matched addresses</span><span class="mv" style="font-family:monospace">${esc(ips.join(', ') || '—')}</span></div>
    <div class="mrow"><span class="ml">Kube-OVN VPCs</span><span class="mv">${matchingVpcs.length || '—'}</span></div>
    <div class="mrow"><span class="ml">Kube-OVN subnets</span><span class="mv">${matchingSubnets.length || '—'}</span></div>
    <div class="mrow"><span class="ml">LoadBalancer services</span><span class="mv">${matchingServices.length || '—'}</span></div>
    <div class="mrow"><span class="ml">Gateways</span><span class="mv">${matchingGateways.length || '—'}</span></div>
    <div class="mrow"><span class="ml">HTTPRoutes</span><span class="mv">${routeMap.size || '—'}</span></div>
    ${!hasMatches ? `<div style="margin-top:8px;color:var(--dim);font-size:12px">No overlay relationships found for this load balancer.</div>` : ''}
    ${matchingVpcs.length ? `<div class="mrow"><span class="ml">VPC names</span><span class="mv" style="font-size:10px">${esc(matchingVpcs.map(item => item.name).join(', '))}</span></div>` : ''}
    ${matchingServices.length ? `<div class="mrow"><span class="ml">Services</span><span class="mv" style="font-size:10px">${esc(matchingServices.map(item => `${item.namespace}/${item.name}`).join(', '))}</span></div>` : ''}
    ${matchingGateways.length ? `<div class="mrow"><span class="ml">Gateways</span><span class="mv" style="font-size:10px">${esc(matchingGateways.map(item => `${item.namespace}/${item.name}`).join(', '))}</span></div>` : ''}
    ${routeMap.size ? `<div class="mrow"><span class="ml">HTTPRoutes</span><span class="mv" style="font-size:10px">${esc(Array.from(routeMap.values()).map(item => `${item.namespace}/${item.name}`).join(', '))}</span></div>` : ''}
  `);
}
