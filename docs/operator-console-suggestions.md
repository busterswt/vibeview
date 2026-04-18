# Operator Console Suggestions

## OpenStack-Focused Gaps

The biggest missing capability is cross-service causality. The UI shows useful slices of Nova, Neutron, Octavia, Cinder, and host state, but operators still have to do the mental join themselves. The highest-value additions are views that answer:

- What else is affected by this thing?
- What changed recently that explains this?

Priority OpenStack additions:

1. Change correlation timeline
   - Per host, VM, router, LB, or volume, show recent Nova/Neutron/Cinder/Octavia state transitions and admin actions in one timeline.
2. Service dependency graph
   - From a compute host, show instances, attached volumes, ports, floating IPs, routers, load balancers, and operational blast radius.
3. Placement visibility
   - Resource providers, aggregates, traits, inventories, allocations, and why a build/resize would fail.
4. Live evacuation and drain readiness
   - Preflight for shared storage, local disks, pinned CPUs, SR-IOV, hugepages, anti-affinity, attached volumes, LB membership, and router or network role.
5. API health by service
   - Not just failures, but latency and error-rate trends for Nova, Neutron, Cinder, Octavia, Keystone, and Placement.
6. Scheduler explanation
   - Why an instance did not land or move, with a condensed filter and weigher style explanation from available signals.
7. Better Cinder operational depth
   - Snapshots, backups, replication, backend pool usage, type-to-backend mapping, QoS policy usage, and attachment anomalies.
8. Neutron troubleshooting joins
   - Port -> binding host -> OVS or OVN state -> DHCP, L3, and LB relationship.
9. Quota and capacity pressure view
   - Project quota consumption plus physical backend saturation.
10. Failure-domain view
   - Aggregate, AZ, rack, controller, and network-node concentration risk.

## Hybrid OpenStack and Kubernetes Gaps

The biggest hybrid gap is shared-infrastructure context. The app has pieces of both worlds, but not enough operator-grade joins where one plane explains the other.

Priority hybrid additions:

1. Shared node impact view
   - One host page that shows Nova workloads, Kubernetes workloads, OVN roles, storage replicas, and what operational action would disrupt.
2. Network identity correlation
   - Instance ports, pod IPs, OVN logical objects, provider networks, and external VIPs in one path.
3. Storage consumer correlation
   - Cinder volumes, CSI drivers, PVCs, backing nodes, and replica placement in one drilldown.
4. Load balancer ownership mapping
   - OpenStack LBs, Kubernetes Services and Gateways, VIP ports, floating IPs, and backend consumers.
5. Unified maintenance planner
   - If I drain or reboot this node, what OpenStack and Kubernetes workloads move, fail, or lose redundancy?
6. Cross-plane alert grouping
   - Collapse symptoms from Nova, Kubernetes, OVN, and storage into one incident object.
7. Explicit identity maps where they really exist
   - Router to VPC, OVN LB to Kubernetes LB, CSI to Cinder, provider network to physical network.
8. Operational policy checks
   - Detect unsafe mixes like compute plus control-plane plus storage replica concentration on the same hardware.
9. Tenant or project to namespace mapping
   - Even if partial or convention-based, this would help operators understand ownership quickly.
10. Runbook-grade remediation hints
   - Environment-specific suggestions based on exact object relationships the app already knows.

## Recommended Priority Order

If the goal is to move from inventory UI toward operator console, the strongest next investments are:

1. Unified maintenance impact planner
2. Placement and scheduling explainability
3. Cross-service change timeline
4. Network identity correlation
5. Storage consumer and backing correlation

## OpenStack-Only First Pass

If focusing strictly on the OpenStack side first, start here:

1. Placement visibility and scheduling explainability
2. Cross-service change timeline
3. Evacuation and drain readiness
4. Neutron troubleshooting joins
5. Better Cinder operational depth

## Current Focus

Selected next implementation target:

1. Placement visibility and scheduling explainability

Initial scope:

- expose Placement provider inventory, usages, allocation ratios, traits, and aggregate context
- surface operator-facing constraint reasons per compute host
- ship it as an OpenStack report so it is easy to iterate on
