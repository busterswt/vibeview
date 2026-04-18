# Operator Feature Roadmap

Generated from a design session on 2026-04-18. Captures gaps identified for pure OpenStack operators and hybrid OpenStack/Kubernetes operators.

---

## For OpenStack Operators

### Ironic (Bare Metal)
The app tracks hypervisors and VMs but has no visibility into Ironic nodes — provisioning state, power state, last deploy, introspection data. Operators running mixed bare-metal/VM environments are flying blind on half their infrastructure.

### Certificate & Endpoint Expiry Tracking
Barbican secrets and service endpoint TLS certs expire silently. A dedicated view showing cert expiry timelines (especially for Octavia TLS termination, Keystone endpoints) would prevent outages.

### Project Quota Utilization
No view showing per-project quota consumption vs. limits. Operators constantly context-switch to the CLI or Horizon to answer "which projects are near quota?" A quota heatmap with trend lines would be immediately useful.

### Nova Scheduler Explainability
When a VM lands on an unexpected host or fails to schedule, operators have no in-app way to see why. A "why did this instance land here?" explainer using placement data already collected would save significant debugging time.

### Volume Backup & Snapshot Status
Cinder volumes are tracked but backup jobs and snapshots are not. Operators need to know which volumes have recent backups and which are unprotected.

### Security Group Audit View ← IN PROGRESS
No cross-project view of security group rules. A flattened "what is actually exposed where" view — especially rules allowing `0.0.0.0/0` — would be high-value for operators who care about posture. Planned under OpenStack Networking as a dedicated tab with risk scoring and an audit mode.

### Unified Event/Notification Stream
OpenStack has rich event data (Nova notifications, Neutron port events, Cinder state changes) but none of it surfaces in the app. A real-time event feed would let operators watch what's happening across the cloud without SSHing into RabbitMQ/Kafka.

---

## For Hybrid OpenStack/Kubernetes Operators

### VM-to-Node Lineage
Both sides of the picture exist — Nova servers and K8s nodes — but no explicit mapping showing "this K8s node is this Nova instance on this hypervisor." A topology view tracing the full chain (hypervisor → VM → K8s node → pods) would be uniquely powerful and nothing in the market does it well.

### Cross-Environment Capacity Planning
K8s resource requests (CPU/memory) vs. Nova placement capacity are tracked separately. A combined view showing "if I drain this hypervisor, how many K8s nodes lose capacity and what pods are at risk?" would directly support maintenance planning — the app's core use case.

### Magnum Cluster Management
If operators are using Magnum to provision K8s clusters on OpenStack, there is no visibility into cluster state, node group health, or upgrade status. This is the missing bridge between the two worlds.

### Namespace-to-Project Mapping
OpenStack projects and K8s namespaces are often 1:1 for tenants. Surfacing that mapping explicitly (with cross-env quota, resource counts, and cost attribution) would help platform teams with chargeback and multi-tenancy governance.

### Cross-Stack Dependency Graph
Which K8s services are backed by OpenStack load balancers? Which PVCs bind to which Cinder volumes on which hypervisor? Most of the data already exists — a dependency graph view connecting the dots would let operators understand blast radius before taking action.

### Unified Alerting / Webhooks
The app has no outbound alerting. Operators want to know when a node goes degraded, a volume hits capacity, or a cert expires — without watching a dashboard. Webhook/Slack integration with configurable thresholds would make the app sticky.

---

## Priority Ranking

| Priority | Feature | Rationale |
|----------|---------|-----------|
| 1 | Security Group Audit | Uses existing Neutron data; immediate operator value |
| 2 | VM-to-Node Lineage | Unique, uses existing data from both sides |
| 3 | Project Quota Utilization | Operators ask this constantly |
| 4 | Unified Event Stream | Turns app from read-only to live operations console |
| 5 | Cross-Environment Capacity Planning | Directly extends existing maintenance workflow |
| 6 | Certificate Expiry Tracking | Prevents silent outages |
| 7 | Volume Backup Status | Fills gap in existing storage view |
| 8 | Nova Scheduler Explainability | Advanced but high-value for placement debugging |
| 9 | Magnum Cluster Management | Requires Magnum deployment |
| 10 | Namespace-to-Project Mapping | Enables chargeback workflows |
| 11 | Cross-Stack Dependency Graph | Complex but powerful |
| 12 | Unified Alerting / Webhooks | Infrastructure for many other features |
| 13 | Ironic Bare Metal | Niche but important for specific deployments |
