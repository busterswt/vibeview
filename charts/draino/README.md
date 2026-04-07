# VibeView Helm Chart

This chart deploys the VibeView web UI.

## Defaults

- `replicaCount=1` because login sessions are stored in-process
- `gateway.enabled=true`
- `service.type=ClusterIP`
- node-local HTTPS reboot agent enabled for host actions and inspection

## Install

```bash
helm upgrade --install draino ./charts/draino \
  --namespace draino \
  --create-namespace \
  --set image.repository=ghcr.io/busterswt/draino-claude \
  --set image.tag=main \
  --set gateway.parentRefs[0].name=shared-gateway \
  --set gateway.parentRefs[0].sectionName=https \
  --set gateway.hostnames[0]=draino.example.com
```

## Envoy Gateway

The chart now prefers Gateway API resources for Genestack-style environments.

Default behavior:

- creates an `HTTPRoute`
- attaches that route to an existing `Gateway`

Typical Genestack deployment:

```bash
sudo mkdir -p /etc/genestack/helm-configs/draino
sudo tee /etc/genestack/helm-configs/draino/draino-helm-overrides.yaml >/dev/null <<'EOF'
image:
  repository: ghcr.io/busterswt/draino-claude
  tag: main
gateway:
  enabled: true
  create: false
  parentRefs:
    - name: envoy-gateway
      namespace: infra-gateway
      sectionName: draino-https
  hostnames:
    - draino.<your-domain>
EOF

helm upgrade --install draino ./charts/draino \
  --namespace draino \
  --create-namespace \
  -f /etc/genestack/helm-configs/draino/draino-helm-overrides.yaml
```

If your environment wants this chart to create its own `Gateway`, set:

```bash
--set gateway.create=true \
--set gateway.name=draino-gateway \
--set gateway.gatewayClassName=envoy-gateway-class
```

That should only be used when the environment expects application teams to manage their
own `Gateway` objects.

## Reboot support

The chart now deploys a node-local reboot agent as a DaemonSet by default. VibeView calls
that agent over HTTPS after the node has already been cordoned and drained.

Default behavior:

- one privileged agent pod per labeled OpenStack infrastructure node
- one headless Service for per-node HTTPS discovery
- one generated Secret containing the agent TLS material and bearer token
- the web pod uses in-cluster RBAC only to find the correct agent pod for the selected node

By default the DaemonSet is scheduled only on nodes labeled with one of:

- `openstack-compute-node=enabled`
- `openstack-network-node=enabled`
- `openstack-control-plane=enabled`
- `openstack-storage-node=enabled`

This intentionally excludes generic worker nodes unless you override `nodeAgent.affinity`.

The DaemonSet rollout strategy is configurable through `nodeAgent.updateStrategy`. The
default allows up to `2` node-agent pods to be unavailable during a rolling update:

```yaml
nodeAgent:
  updateStrategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 2
```

Both the web `Deployment` and the node-agent `DaemonSet` also carry a pod-template
annotation based on the Helm release revision. That means a normal `helm upgrade` will
trigger a rollout even when the image tag and other pod settings are unchanged.

This is still a privileged design because the agent can reboot its host.

Security concerns to understand before deployment:

- the agent is privileged on every managed node
- the web pod can discover and call every agent
- the current implementation uses shared in-cluster trust material for agent access
- this is safer than shared SSH keys, but it is not equivalent to a strongly isolated
  maintenance control plane

Recommended hardening:

- add `NetworkPolicy` so only the VibeView web pod can reach the agent
- keep the VibeView namespace tightly restricted
- restrict access to the generated node-agent Secret
- prefer future migration to mTLS or per-agent credentials
- consider an external maintenance service or out-of-band power control for stricter
  environments

## External hostname

The external hostname is intentionally value-driven because it varies by environment.
For Gateway API, set it with:

```bash
--set gateway.hostnames[0]=draino.<your-domain>
```
or use a values file.
