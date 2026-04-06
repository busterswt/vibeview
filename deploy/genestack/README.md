# Draino on Genestack

This repo can run cleanly as a single web pod in a Genestack / OpenStack-Helm environment.

If you use Helm, prefer the chart in `charts/draino/`. The manifest in this folder is a
plain-YAML reference equivalent.

## Why this shape

- `draino --web` already exposes the browser UI over HTTP/WebSockets.
- Browser users provide Kubernetes and OpenStack credentials at login, so the pod does not need broad ambient cluster or OpenStack credentials.
- Session state is stored in-process, so start with `replicas: 1`. If you later scale out, you will need sticky sessions or an external session store.
- Reboot and several hardware inspection paths use `ssh` from the container to the target node, so the pod must be able to reach node management addresses and have a suitable SSH key/config.

## Build and push

```bash
docker build -t registry.example.com/operations/draino:0.1.0 .
docker push registry.example.com/operations/draino:0.1.0
```

## Deploy

If you use Helm, start with [values.yaml](/Users/james.denton/github/draino-claude/deploy/genestack/values.yaml):

```bash
sudo mkdir -p /etc/genestack/helm-configs/draino
sudo cp deploy/genestack/values.yaml /etc/genestack/helm-configs/draino/draino-helm-overrides.yaml
```

Update these fields first:

- image repository if different from `ghcr.io/busterswt/draino-claude`
- image tag
- Gateway parent reference name and namespace
- external hostname
- optional SSH secret name

Then deploy with Helm using the Genestack override file:

```bash
helm upgrade --install draino ./charts/draino \
  --namespace draino \
  --create-namespace \
  -f /etc/genestack/helm-configs/draino/draino-helm-overrides.yaml
```

## New Gateway listener

Genestack’s Envoy Gateway flow patches listeners onto the shared `Gateway` from
`/etc/genestack/gateway-api/listeners/`. The Rackspace Genestack docs show this patching
model explicitly: listener fragments are stored in that directory and then applied with
`kubectl patch` against the shared `Gateway`. Source:
https://docs.rackspacecloud.com/infrastructure-envoy-gateway-api/

Example listener fragment for a dedicated Draino hostname:

```json
[
  {
    "op": "add",
    "path": "/spec/listeners/-",
    "value": {
      "name": "draino-https",
      "protocol": "HTTPS",
      "port": 443,
      "hostname": "draino.example.com",
      "tls": {
        "mode": "Terminate",
        "certificateRefs": [
          {
            "kind": "Secret",
            "name": "draino-tls"
          }
        ]
      },
      "allowedRoutes": {
        "namespaces": {
          "from": "All"
        }
      }
    }
  }
]
```

Save that as:

```bash
/etc/genestack/gateway-api/listeners/draino-https.json
```

Then patch the shared gateway, for example:

```bash
kubectl patch -n envoy-gateway gateway flex-gateway \
  --type='json' \
  --patch="$(cat /etc/genestack/gateway-api/listeners/draino-https.json)"
```

Adjust `envoy-gateway`, `flex-gateway`, the listener name, hostname, and TLS secret to
match your deployment.

## New Route

Draino’s Helm chart already creates the `HTTPRoute`, so in most cases the route is just
the chart install. In Genestack terms, that is the route resource that binds to the
listener via `parentRefs.sectionName`.

Your override file at
`/etc/genestack/helm-configs/draino/draino-helm-overrides.yaml`
should line up with the listener:

```yaml
gateway:
  enabled: true
  create: false
  parentRefs:
    - name: flex-gateway
      namespace: envoy-gateway
      sectionName: draino-https
  hostnames:
    - draino.example.com
```

That causes the chart to render an `HTTPRoute` similar to:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: draino
spec:
  parentRefs:
    - name: flex-gateway
      namespace: envoy-gateway
      sectionName: draino-https
  hostnames:
    - draino.example.com
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: draino
          port: 80
```

If you ever need to manage the route outside Helm, Genestack’s documented route path is:

```bash
/etc/genestack/gateway-api/routes/
```

and those route manifests are applied with:

```bash
kubectl apply -f /etc/genestack/gateway-api/routes
```

## SSH secret for reboot support

If you want the web UI to issue reboots, create a Secret named `draino-ssh` in the
`draino` namespace:

```bash
kubectl -n draino create secret generic draino-ssh \
  --from-file=id_rsa=/path/to/private_key \
  --from-file=known_hosts=/path/to/known_hosts
```

Then enable it in
`/etc/genestack/helm-configs/draino/draino-helm-overrides.yaml`:

```yaml
ssh:
  enabled: true
  secretName: draino-ssh
```

The secret is mounted at `/home/draino/.ssh` inside the pod. The remote SSH account
must be able to reach all target nodes and must be permitted to run `sudo reboot`.

This is insecure if the same private key is trusted on every node. It creates a single
credential with broad node-level access and high blast radius. Use it only as a
temporary operational shortcut and treat the Secret as highly sensitive.

Better approaches:

- use a node-local reboot agent or privileged DaemonSet instead of direct SSH from the web pod
- use short-lived SSH certificates or other ephemeral credentials instead of one shared private key
- scope credentials per node or per host group with restricted `sudoers`
- move reboot execution into an external automation service and let Draino call that service

## Genestack notes

- In Genestack environments that use Envoy Gateway, prefer Gateway API resources over a classic `Ingress`.
- The chart supports this by creating an `HTTPRoute` and attaching it to an existing shared `Gateway`.
- The app needs `kubectl` for drain operations. The Docker image includes it.
- OVN inspection endpoints call `kubectl ko nbctl ...`. If your Genestack operators use the `ko` plugin, mount or bake that plugin into the image too. Core drain/evacuation workflows do not depend on it.
- Reboot support uses the `draino-ssh` Secret mounted at `/home/draino/.ssh` when `ssh.enabled=true`.
