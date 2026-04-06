# draino

**draino** is an interactive operator tool for safely draining OpenStack hypervisors and
Kubernetes nodes before a maintenance reboot.  It automates the full evacuation workflow —
live-migrating VMs, failing over Octavia load balancers, evicting pods — and then issues
the reboot and waits for the node to return.  Every action is recorded to a compliance
audit log.

It ships with two UIs: a terminal TUI (powered by [Textual](https://textual.textualize.io/))
and a browser-based web UI (powered by FastAPI + WebSockets).

---

## Features

- **Full evacuation workflow** — cordon → disable Nova → live-migrate VMs → fail over
  Amphora LBs → drain K8s pods → reboot → wait for recovery
- **Quick-drain shortcut** — cordon + K8s pod eviction only, skipping OpenStack steps
- **Undrain / re-enable** — uncordon and re-enable Nova compute in one action
- **etcd quorum awareness** — identifies etcd nodes, checks SSH health of all peers,
  and blocks a reboot if it would break quorum
- **Live pod view** — lists all pods on the selected node with status, restarts, and age;
  Succeeded pods hidden by default with a toggle
- **Pre-flight instance preview** — shows VMs and Amphora instances on a compute node
  before evacuation begins
- **Compliance audit log** — every action (started / completed / failed / blocked /
  cancelled) is appended as a structured JSONL entry to `~/.draino/audit.log`
- **Two UIs** — terminal TUI and browser web UI, both driven by the same backend logic

---

## Installation

```bash
# TUI only
pip install .

# TUI + web UI
pip install ".[web]"
```

Requires Python 3.11+.

### Dependencies

| Dependency | Purpose |
|---|---|
| `textual >= 0.52` | Terminal UI framework |
| `kubernetes >= 28` | K8s node/pod operations |
| `openstacksdk >= 2` | Nova compute & Octavia LB operations |
| `fastapi`, `uvicorn` | Web UI server (`[web]` extra) |

---

## Prerequisites

- SSH access from the machine running draino to each hypervisor (used for etcd health
  checks and issuing reboots)

### Terminal UI auth

The TUI still uses local operator credentials from the runtime environment:

- A valid `~/.kube/config` (or `KUBECONFIG`) pointing at the target cluster
- OpenStack credentials in `~/.config/openstack/clouds.yaml` (or `OS_CLOUD` env var)

### Web UI auth

The web UI now requires explicit credentials entered in the browser before the app loads.
It does not rely on ambient Kubernetes or OpenStack auth from the host once a web session
is established.

Required at login:

- Kubernetes bearer token auth
- Kubernetes client certificate auth
- Kubernetes kubeconfig upload/paste
- OpenStack Keystone username / password auth
- OpenStack application credential auth
- OpenStack `clouds.yaml` upload/paste

### Recommended web auth choices

For the web UI, the most operator-friendly combinations are:

- Kubernetes: uploaded or pasted `kubeconfig`
- OpenStack: application credentials

Why:

- `kubeconfig` avoids forcing operators to extract a raw bearer token out-of-band
- application credentials are safer than sharing a full username/password with the UI
- both approaches are easier to scope and rotate than long-lived personal credentials

---

## Usage

### Terminal UI

```bash
draino
draino --cloud mycloud --context staging
```

### Web UI

```bash
draino --web
draino --web --host 127.0.0.1 --port 9000
```

Then open `http://localhost:8000` in a browser and authenticate with both:

- Kubernetes credentials:
  bearer token, client certificate, or `kubeconfig`
- OpenStack credentials:
  password fields, application credential, or `clouds.yaml`

The browser session is stored server-side in memory and all subsequent REST/WebSocket
operations use those supplied credentials.

For the web UI, reboot is additionally restricted to sessions whose OpenStack role
assignments include `admin`. Non-admin sessions can still inspect nodes and run
non-reboot workflows, but the reboot action is disabled in the UI and rejected
server-side.

### Container image

Build the web UI image:

```bash
docker build -t draino:0.1.0 .
```

Run it locally:

```bash
docker run --rm -p 8000:8000 draino:0.1.0
```

Tag and push it to your registry:

```bash
docker tag draino:0.1.0 registry.example.com/operations/draino:0.1.0
docker push registry.example.com/operations/draino:0.1.0
```

Notes:

- The image includes `kubectl`, which is required for drain operations.
- The image includes `ssh`, which is still used for several host-inspection flows and legacy reboot fallback mode.
- The web UI keeps login sessions in-process, so production deployment should start with a single replica unless you add sticky sessions or externalise session storage.
- OVN inspection endpoints rely on `kubectl ko`; if you use those views in-cluster, also provide the `ko` plugin in the image or via a mounted binary.

### Kubernetes / Genestack deployment

Example Helm values for a Genestack / OpenStack-Helm environment are in
`deploy/genestack/`.

The intended deployment pattern is:

- one `Deployment` replica for the web server
- one `ClusterIP` `Service`
- one `HTTPRoute` attached to Envoy Gateway for external user access
- one node-local reboot-agent `DaemonSet` for reboot support

This chart is Gateway API only and is intended for environments that expose applications
through Envoy Gateway.

The default reboot path now uses a node-local HTTPS agent instead of a shared SSH key in
the web pod. Legacy SSH reboot support remains available only as a fallback, and the
documented Secret name for that mode is `draino-ssh`. Using one SSH private key that is
trusted across all nodes is not recommended.

The node-local agent design is documented in
`docs/node-local-reboot-agent.md`.

### Helm chart

A Helm chart for the web UI is in `charts/draino/`.

Example install:

```bash
sudo mkdir -p /etc/genestack/helm-configs/draino
sudo cp deploy/genestack/values.yaml /etc/genestack/helm-configs/draino/draino-helm-overrides.yaml

helm upgrade --install draino ./charts/draino \
  --namespace draino \
  --create-namespace \
  -f /etc/genestack/helm-configs/draino/draino-helm-overrides.yaml
```

By default the chart keeps `replicaCount=1` because authenticated web sessions are stored
in-process.

### GitHub image builds

If you do not build images locally, the repository can build and publish them from GitHub
Actions using [`.github/workflows/build-image.yml`](/Users/james.denton/github/draino-claude/.github/workflows/build-image.yml).

The workflow:

- lints the Helm chart
- builds the container image from `Dockerfile`
- pushes to `ghcr.io/<owner>/<repo>`
- tags branch builds as `:main`
- tags release builds like `v0.1.0` as `:0.1.0` and `:latest`

Typical flow:

1. Push to `main` to publish `ghcr.io/<owner>/<repo>:main`
2. Create a git tag like `v0.1.0` to publish `:0.1.0` and `:latest`

To use GHCR from Kubernetes, set the Helm values:

```bash
--set image.repository=ghcr.io/busterswt/draino-claude \
--set image.tag=main
```

The external hostname should stay deployment-specific. Set it with Helm values rather
than hard-coding it into the chart. For Envoy Gateway:

```bash
--set gateway.hostnames[0]=draino.<environment-domain>
```

### Options

| Flag | Default | Description |
|---|---|---|
| `--cloud NAME` | `$OS_CLOUD` | OpenStack cloud name from `clouds.yaml` for the TUI |
| `--context NAME` | current context | Kubernetes context from `kubeconfig` for the TUI |
| `--audit-log PATH` | `~/.draino/audit.log` | Path for the compliance audit log |
| `--web` | off | Launch the web UI instead of the TUI |
| `--host HOST` | `0.0.0.0` | Bind address for the web server |
| `--port PORT` | `8000` | Port for the web server |

When `--web` is used, `--cloud` and `--context` are no longer required for normal use
because the browser login provides explicit OpenStack and Kubernetes credentials.

---

## Workflows

### Full Evacuation (`S` / Start Evacuation)

Intended for nodes that need a full maintenance window.

1. Cordon the K8s node (mark unschedulable)
2. Disable the Nova compute service (stops new VM scheduling)
3. Enumerate all instances on the hypervisor
4. Live-migrate non-Amphora VMs to other hypervisors
5. Trigger Octavia failover for all Amphora load balancer instances
6. Wait for the hypervisor to report zero instances
7. Drain the K8s node (evict all pods)
8. *(Proceed to reboot)*

### Quick Drain (`D` / Quick Drain)

For nodes where OpenStack evacuation is not needed (e.g. control-plane nodes, or when
VMs have already been migrated manually).

1. Cordon the K8s node
2. *(Compute nodes only)* Disable Nova compute service
3. Drain the K8s node

### Undrain (`U` / Undrain Node)

Reverses a cordon/disable.

1. *(Compute nodes only)* Re-enable Nova compute service
2. Uncordon the K8s node

### Reboot (`R` / Reboot Node)

Issues a reboot after evacuation is complete.  For etcd nodes, SSH health of all etcd
peers is checked first; the reboot is blocked if it would reduce the cluster below quorum.

In the web UI, reboot is only available to authenticated sessions with the OpenStack
`admin` role.

1. Check etcd quorum (etcd nodes only)
2. SSH `reboot` command to the node
3. Wait for the node to go `NotReady` (up to 5 min)
4. Wait for the node to return `Ready` (up to 10 min)
5. Report total downtime

---

## Keyboard shortcuts (TUI)

| Key | Action |
|---|---|
| `S` | Start full evacuation |
| `D` | Quick drain (cordon + pod evict) |
| `U` | Undrain (uncordon + re-enable Nova) |
| `R` | Reboot node |
| `P` | Toggle pods view |
| `H` | Show / hide Succeeded pods |
| `↑ / ↓` | Navigate node list |

---

## Development

Install development tooling:

```bash
pip install -e ".[dev,web]"
```

Run the automated checks:

```bash
python -m pytest
python -m ruff check .
```

The current lint baseline is intentionally scoped to the actively maintained
session-auth and test modules so linting can be adopted incrementally without
forcing a one-shot cleanup of the full legacy codebase.

---

## Audit log

Every significant action is recorded to `~/.draino/audit.log` (JSONL, one entry per line):

```json
{"timestamp":"2026-04-03T14:02:11Z","user":"ops","hostname":"jumpbox","session_id":"a1b2...","action":"evacuation","node":"compute-07","event":"started"}
{"timestamp":"2026-04-03T14:17:43Z","user":"ops","hostname":"jumpbox","session_id":"a1b2...","action":"evacuation","node":"compute-07","event":"completed"}
{"timestamp":"2026-04-03T14:18:01Z","user":"ops","hostname":"jumpbox","session_id":"a1b2...","action":"reboot","node":"compute-07","event":"started"}
{"timestamp":"2026-04-03T14:19:55Z","user":"ops","hostname":"jumpbox","session_id":"a1b2...","action":"reboot","node":"compute-07","event":"completed","detail":"downtime=114s"}
```

Fields: `timestamp`, `user`, `hostname`, `session_id`, `action`, `node`, `event`, `detail` (optional).

A custom path can be set with `--audit-log`.

---

## Project layout

```
draino/
  __main__.py        CLI entry point
  models.py          NodeState, InstanceInfo, WorkflowStep, enums
  worker.py          Background workflow runners (evacuation, drain, reboot)
  app.py             Textual TUI application
  render.py          Pure rendering helpers (Rich markup / Text cells)
  screens.py         Textual modal screens (confirm dialogs)
  audit.py           Compliance audit logger (JSONL)
  operations/
    k8s_ops.py       Kubernetes client helpers
    openstack_ops.py OpenStack / Nova / Octavia helpers
  web/
    server.py        FastAPI + WebSocket backend
    static/
      index.html     Single-page browser UI
      login-mockup.html Standalone login mockup
```

---

## License

MIT
