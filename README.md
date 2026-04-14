# VibeView

**VibeView** is an interactive operator tool for safely draining OpenStack hypervisors and
Kubernetes nodes before a maintenance reboot. It automates the full evacuation workflow —
live-migrating VMs, failing over Octavia load balancers, evicting pods — and then issues
the reboot and waits for the node to return. It also includes live infrastructure,
Kubernetes, networking, reporting, and stress-testing views for day-two operator work.
Every action is recorded to a compliance audit log.

It ships as a browser-based web UI powered by FastAPI and WebSockets, with a
node-local agent for reboot and host-inspection operations.

---

## Features

- **Full evacuation workflow** — cordon → disable Nova → live-migrate VMs → fail over
  Amphora LBs → drain K8s pods → reboot → wait for recovery
- **Quick-drain shortcut** — cordon + K8s pod eviction only, skipping OpenStack steps
- **Undrain / re-enable** — uncordon and re-enable Nova compute in one action
- **etcd quorum awareness** — identifies etcd nodes, checks peer health before reboot,
  and blocks a reboot if it would break quorum. This requires etcd nodes to be labeled
  with `node-role.kubernetes.io/etcd`
- **Live pod view** — lists all pods on the selected node with status, restarts, and age;
  Succeeded pods hidden by default with a toggle
- **Pre-flight instance preview** — shows VMs and Amphora instances on a compute node
  before evacuation begins
- **Compliance audit log** — every action (started / completed / failed / blocked /
  cancelled) is appended as a structured JSONL entry to `~/.draino/audit.log`
- **Browser-based operations UI** — web UI for infrastructure, Kubernetes, networking,
  reports, stress testing, and storage views
- **Network operations views** — list/detail drawers for networks, routers, and load
  balancers, including VIP ports, OVN logical port data, and subnet metadata-port repair
- **Kubernetes inventory** — browse namespaces, pods, services, Gateway API resources,
  PVCs/PVs, CRDs, and operators with right-side detail drawers
- **Live operator reports** — capacity and headroom, placement risk, project placement,
  node health and density, and PVC placement/workload reports
- **Heat-backed stress workspace** — disposable stack templates for capacity spread and
  end-to-end load balancer testing with action trace and timing summaries
- **Flexible node-agent profile** — deploy the node-local agent in unprivileged mode by
  default, or switch to privileged/root mode when host reboot support is required

---

## Installation

For local development or operator use from a checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

For a plain runtime install without development tooling:

```bash
pip install .
```

Requires Python 3.11+.

### Local test tooling

To run the local validation commands used in this repo, install:

| Tool | Purpose |
|---|---|
| Python 3.11+ | runtime, unit tests, helper scripts |
| `pytest` | Python test suite |
| `helm` | render and validate the Helm chart locally |
| `node` | JavaScript syntax checks via `node --check` |

Typical local checks:

```bash
PYTHONPATH=. pytest -q tests/web
pytest -q tests/web
pytest -q
helm template test charts/draino
node --check draino/web/static/app_resources.js
```

### Dependencies

| Dependency | Purpose |
|---|---|
| `kubernetes >= 28` | K8s node/pod operations |
| `openstacksdk >= 2` | Nova compute & Octavia LB operations |
| `fastapi`, `uvicorn` | Web UI server and node-local agent |

---

## Prerequisites

- etcd nodes must be labeled with `node-role.kubernetes.io/etcd` or VibeView cannot
  identify them for quorum-aware reboot protection

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

### Web UI

```bash
draino
draino --host 127.0.0.1 --port 9000
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
docker build -t vibeview:0.2.0 .
```

Run it locally:

```bash
docker run --rm -p 8000:8000 vibeview:0.2.0
```

Tag and push it to your registry:

```bash
docker tag vibeview:0.2.0 registry.example.com/operations/vibeview:0.2.0
docker push registry.example.com/operations/vibeview:0.2.0
```

Notes:

- The image includes `kubectl`, which is required for drain operations.
- The image includes `kubectl-ko`, so OVN inspection views work without an extra plugin mount.
- The web UI keeps login sessions in-process, so production deployment should start with a single replica unless you add sticky sessions or externalise session storage.

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

Reboot and host-inspection flows now rely on the node-local HTTPS agent rather than SSH
from the web pod.

The node-local agent design is documented in
`docs/node-local-reboot-agent.md`.

Security note:

- the current node-agent model is still privileged and high-trust
- the web pod can reach node agents and request host actions through in-cluster trust
- this should be treated as safer than shared SSH, not as a strong isolation boundary
- review `docs/node-local-reboot-agent.md` before using this in a high-sensitivity
  environment

### Helm chart

A Helm chart for the web UI is in `charts/draino/`.
The chart path and Python package still use `draino` internally, but deployment-facing
examples below use the `vibeview` product name.

Example install:

```bash
sudo mkdir -p /etc/genestack/helm-configs/vibeview
sudo cp deploy/genestack/values.yaml /etc/genestack/helm-configs/vibeview/vibeview-helm-overrides.yaml

helm upgrade --install vibeview ./charts/draino \
  --namespace vibeview \
  --create-namespace \
  -f /etc/genestack/helm-configs/vibeview/vibeview-helm-overrides.yaml
```

By default the chart keeps `replicaCount=1` because authenticated web sessions are stored
in-process.

### GitHub image builds

If you do not build images locally, the repository can build and publish them from GitHub
Actions using [`.github/workflows/build-image.yml`](/Users/james.denton/github/vibeview/.github/workflows/build-image.yml).

The workflow:

- lints the Helm chart
- builds the container image from `Dockerfile`
- pushes to `ghcr.io/<owner>/<repo>`
- tags branch builds as `:main`
- tags release builds like `v0.2.0` as `:0.2.0` and `:latest`

Typical flow:

1. Push to `main` to publish `ghcr.io/<owner>/<repo>:main`
2. Create a git tag like `v0.2.0` to publish `:0.2.0` and `:latest`

To use GHCR from Kubernetes, set the Helm values:

```bash
--set image.repository=ghcr.io/busterswt/vibeview \
--set image.tag=main
```

The external hostname should stay deployment-specific. Set it with Helm values rather
than hard-coding it into the chart. For Envoy Gateway:

```bash
--set gateway.hostnames[0]=vibeview.<environment-domain>
```

### Options

| Flag | Default | Description |
|---|---|---|
| `--audit-log PATH` | `~/.draino/audit.log` | Path for the compliance audit log |
| `--web` | off | Launch the web UI explicitly (same behavior as the default mode) |
| `--node-agent` | off | Launch the node-local reboot agent |
| `--host HOST` | `0.0.0.0` | Bind address for the web server or node agent |
| `--port PORT` | `8000` | Port for the web server or node agent |

---

## Workflows

### Full Evacuation

Intended for nodes that need a full maintenance window.

1. Cordon the K8s node (mark unschedulable)
2. Disable the Nova compute service (stops new VM scheduling)
3. Enumerate all instances on the hypervisor
4. Live-migrate non-Amphora VMs to other hypervisors
5. Trigger Octavia failover for all Amphora load balancer instances
6. Wait for the hypervisor to report zero instances
7. Drain the K8s node (evict all pods)
8. *(Proceed to reboot)*

### Quick Drain

For nodes where OpenStack evacuation is not needed (e.g. control-plane nodes, or when
VMs have already been migrated manually).

1. Cordon the K8s node
2. *(Compute nodes only)* Disable Nova compute service
3. Drain the K8s node

### Undrain

Reverses a cordon/disable.

1. *(Compute nodes only)* Re-enable Nova compute service
2. Uncordon the K8s node

### Reboot

Issues a reboot after evacuation is complete.  For etcd nodes, peer health of all etcd
peers is checked first; the reboot is blocked if it would reduce the cluster below quorum.
This protection depends on the node carrying the `node-role.kubernetes.io/etcd` label.

In the web UI, reboot is only available to authenticated sessions with the OpenStack
`admin` role.

1. Check etcd quorum (etcd nodes only)
2. Send a reboot request to the node-local agent
3. Wait for the node to go `NotReady` (up to 5 min)
4. Wait for the node to return `Ready` (up to 10 min)
5. Automatically uncordon the node when it comes back
5. Report total downtime

---

## Development

Install development tooling:

```bash
pip install -e ".[dev]"
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
  time_utils.py      Shared time-formatting helpers
  audit.py           Compliance audit logger (JSONL)
  operations/
    k8s_ops.py       Kubernetes client helpers
    openstack_ops.py OpenStack / Nova / Octavia helpers
  node_agent.py      Node-local FastAPI reboot/inspection agent
  web/
    server.py        FastAPI + WebSocket backend
    static/
      index.html     Single-page browser UI
      login.html     Standalone login page
      login-isolated-mockup.html Standalone login mockup
```

---

## License

MIT
