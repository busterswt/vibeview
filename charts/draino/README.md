# Draino Helm Chart

This chart deploys the Draino web UI.

## Defaults

- `replicaCount=1` because login sessions are stored in-process
- `gateway.enabled=true`
- `service.type=ClusterIP`
- optional SSH secret mount for reboot support

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

If you want in-app reboot support, mount an SSH secret:

```bash
helm upgrade --install draino ./charts/draino \
  --namespace draino \
  --create-namespace \
  --set image.repository=ghcr.io/busterswt/draino-claude \
  --set image.tag=main \
  --set ssh.enabled=true \
  --set ssh.secretName=draino-ssh
```

The mounted key must allow SSH from the pod to the target nodes and the remote account
must be allowed to run `sudo reboot`.

## External hostname

The external hostname is intentionally value-driven because it varies by environment.
For Gateway API, set it with:

```bash
--set gateway.hostnames[0]=draino.<your-domain>
```
or use a values file.
