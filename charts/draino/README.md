# Draino Helm Chart

This chart deploys the Draino web UI.

## Defaults

- `replicaCount=1` because login sessions are stored in-process
- `ingress.enabled=true`
- `service.type=ClusterIP`
- optional SSH secret mount for reboot support

## Install

```bash
helm upgrade --install draino ./charts/draino \
  --namespace draino \
  --create-namespace \
  --set image.repository=ghcr.io/busterswt/draino-claude \
  --set image.tag=main \
  --set ingress.hosts[0].host=draino.example.com \
  --set ingress.tls[0].hosts[0]=draino.example.com \
  --set ingress.tls[0].secretName=draino-tls
```

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

## Ingress host

The ingress hostname is intentionally value-driven because it varies by environment.
Set it per deployment with either:

```bash
--set ingress.hosts[0].host=draino.<your-domain>
--set ingress.tls[0].hosts[0]=draino.<your-domain>
```

or a values file.
