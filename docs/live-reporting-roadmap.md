# Live Reporting Roadmap

This document outlines a reporting roadmap for VibeView that respects one hard constraint:

- do not store reporting state or historical data inside the app

All reports should be derived directly from the running environment at request time using:

- Kubernetes
- OpenStack
- OVN
- node-agent

This is a live-synthesis model, not a warehouse or analytics model.

## Operating Principle

The reporting model should be:

- no database
- no persistent snapshot store
- no background collectors keeping history
- no report-specific state retained between requests

That means VibeView can provide:

- current-state operational reports
- synthetic current-state risk and readiness summaries
- live inventory exports

That also means VibeView should not try to provide:

- long-term trends
- week-over-week comparisons
- historical SLA reporting
- capacity growth over time

Those need an external time-series or log system if they are ever required.

## High-Value Live Reports

### 1. Capacity Snapshot

Goal:
- show current compute, Kubernetes, and storage headroom

Inputs:
- Nova hypervisor summaries
- Kubernetes node detail
- Cinder volume inventory

Outputs:
- vCPU and RAM usage by compute host
- allocatable vs requested pod capacity
- drain-safe or overloaded hosts
- storage footprint by project or backend

Difficulty:
- low

### 2. Maintenance Readiness

Goal:
- show which nodes are ready for maintenance right now

Inputs:
- current node inventory state
- Nova compute service status
- Kubernetes readiness, taints, cordon state
- reboot-required and node-agent availability

Outputs:
- ready vs blocked nodes
- cordoned or NoSchedule nodes
- disabled or down Nova services
- reboot-required nodes
- missing node-agent coverage

Difficulty:
- low

### 3. Placement Risk

Goal:
- show concentration or anti-affinity risk across core services

Inputs:
- etcd placement
- MariaDB cluster placement
- amphora placement
- VM placement by host and AZ
- pod placement by node

Outputs:
- etcd concentration warnings
- MariaDB cluster co-location risk
- amphora imbalance
- tenant VM concentration by host or AZ

Difficulty:
- low to medium

### 4. Network and Router Topology

Goal:
- present a live snapshot of Neutron and OVN topology

Inputs:
- Neutron networks
- Neutron routers
- router-connected subnets
- OVN logical switches
- OVN logical routers
- gateway chassis hostnames

Outputs:
- router inventory
- connected subnet inventory
- external networks and SNAT state
- OVN switch and router linkage
- gateway host placement

Difficulty:
- low

### 5. Tenant Consumption

Goal:
- summarize how projects are using the environment

Inputs:
- Nova servers
- Cinder volumes
- Neutron routers, networks, and floating IPs

Outputs:
- instances, vCPUs, RAM, volumes by project
- routers and networks by project
- public exposure by project

Difficulty:
- medium

### 6. Cleanup Candidates

Goal:
- identify obvious live resource cleanup opportunities

Inputs:
- Cinder volumes
- Neutron routers
- Neutron floating IPs
- Neutron networks and ports

Outputs:
- unattached volumes
- unused floating IPs
- routers with no interfaces
- networks with no ports or no subnets

Difficulty:
- medium

## What Does Not Fit a Stateless Model

These should not be built into VibeView without an external history source:

- reboot duration trends
- migration success rates over time
- API latency over days or weeks
- capacity growth trends
- uptime percentages
- MTTR and MTBF
- weekly or monthly comparisons

If those are ever needed, use an external source such as:

- Prometheus
- OpenStack telemetry
- Loki / ELK / other log systems

VibeView should remain a current-state reporting surface unless that constraint changes.

## Recommended Build Order

### Phase 1: Snapshot Reports Using Existing Data

Build first:

1. Maintenance readiness report
2. Capacity and headroom report
3. Placement risk report
4. Network and router topology report

Why first:
- highest operator value
- closest to existing APIs and helpers
- no special export or trend model required

### Phase 2: Live Inventory Summaries

Build next:

1. Tenant resource summary
2. Cleanup candidates report
3. Public exposure report

Why next:
- still stateless
- mostly aggregation across existing OpenStack and Kubernetes inventory
- useful for planning and hygiene

### Phase 3: Export-Oriented Reports

Add optional exports for:

- tenant inventory
- VM placement matrix
- subnet and router inventory
- orphaned resource list

Recommended export formats:

- CSV
- JSON

These should still be generated live from the environment, not from retained snapshots.

## Recommended UI vs Export Split

Best suited for the UI:

- maintenance readiness
- capacity snapshot
- placement risk
- network and router topology
- public exposure summary

Best suited for export:

- tenant inventory
- cleanup candidates
- VM placement matrix
- network and router inventory
- floating IP and volume inventory

## Recommended File Layout

To keep the current code style and module layout coherent, reporting should be added as a focused slice instead of spreading report logic across unrelated files.

Recommended backend additions:

- `draino/web/report_helpers.py`
  - report assembly and aggregation helpers
- `draino/web/api/reports.py`
  - report endpoints only

Recommended frontend additions:

- `draino/web/static/app_reports.js`
  - report view rendering and live fetch logic

Minimal supporting touch points:

- `draino/web/app.py`
  - register the reports router
- `draino/web/static/index.html`
  - add a Reports view shell
- `draino/web/static/app_infra.js`
  - wire top-level view switching

This keeps:

- resource inventory helpers in `resource_helpers.py`
- node and workflow logic in existing modules
- reporting-specific synthesis isolated in one place

## Suggested Endpoint Shape

Keep report endpoints small and explicit.

Examples:

- `GET /api/reports/maintenance-readiness`
- `GET /api/reports/capacity`
- `GET /api/reports/placement-risk`
- `GET /api/reports/network-topology`
- `GET /api/reports/tenant-summary`
- `GET /api/reports/cleanup`

Exports can use parallel endpoints:

- `GET /api/reports/tenant-summary.csv`
- `GET /api/reports/cleanup.csv`

## Recommended First Milestone

The best first implementation slice is:

1. Add a Reports top-level view
2. Add `report_helpers.py`
3. Add `reports.py` API routes
4. Implement `maintenance-readiness`
5. Implement `capacity`

Why this is the right first slice:

- strongest operational value
- easiest to validate
- uses data you already collect live
- does not require changing the existing data model

## Concrete First Reports

### Maintenance Readiness

One row per node:

- node name
- availability zone
- compute host
- k8s ready
- cordoned
- NoSchedule
- Nova service state
- reboot required
- node-agent ready
- VM count
- pod count
- etcd / MariaDB / edge flags
- readiness verdict
- blocking reason

### Capacity and Headroom

One row per compute host:

- node name
- AZ
- aggregate membership
- VM count
- amphora count
- vCPU used / total
- RAM used / total
- pod count / allocatable
- readiness
- maintenance candidate flag

### Placement Risk

Environment summary:

- etcd placement by node
- MariaDB cluster placement by node
- amphora distribution
- overloaded hosts
- single-AZ or single-host concentrations

### Network and Router Topology

Router summary:

- router name
- status
- external network
- interface count
- route count
- connected subnets
- OVN logical router ports
- gateway hostnames

## Summary

The correct reporting strategy for VibeView is live current-state reporting, not retained analytics.

The best next work is:

1. maintenance readiness
2. capacity and headroom
3. placement risk
4. network and router topology

And the cleanest implementation path is:

- add a dedicated reports API module
- add a dedicated report helper module
- add a dedicated reports frontend module

That preserves the current coding style and keeps the file layout logical.
