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
helm upgrade --install draino ./charts/draino \
  --namespace draino \
  --create-namespace \
  -f deploy/genestack/values.yaml
```

Update these fields first:

- image repository if different from `ghcr.io/busterswt/draino-claude`
- image tag
- ingress hostname
- TLS secret name
- optional SSH secret name

If you prefer raw manifests, update these fields in [draino.yaml](/Users/james.denton/github/draino-claude/deploy/genestack/draino.yaml):

- image reference
- ingress hostname
- TLS secret name
- optional SSH secret mount

Then apply:

```bash
kubectl apply -f deploy/genestack/draino.yaml
```

## Genestack notes

- In OpenStack-Helm environments, the normal user-facing pattern is an Ingress fronting a ClusterIP service.
- If your environment does not expose ingress for custom apps, switch the service to `NodePort` or use `hostNetwork: true` on a tightly controlled node pool.
- The app needs `kubectl` for drain operations. The Docker image includes it.
- OVN inspection endpoints call `kubectl ko nbctl ...`. If your Genestack operators use the `ko` plugin, mount or bake that plugin into the image too. Core drain/evacuation workflows do not depend on it.
- For reboot support, mount an SSH private key into `/home/draino/.ssh` and ensure the remote host accepts `sudo reboot` for that account.
