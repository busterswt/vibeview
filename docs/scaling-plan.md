# Scaling Plan

This document outlines a concrete scaling plan for Draino as the environment grows from a handful of nodes to 100+ nodes.

The focus is on functional scaling, not just CPU and memory sizing. The main concerns are:

- repeated full-environment refreshes
- eager per-node detail fetching
- repeated node-agent discovery work
- duplicate backend polling across user sessions
- full-state WebSocket broadcasts

## Current Pressure Points

The current implementation behaves well at small scale, but several patterns will become expensive as node count and user count rise:

- The web server refresh path performs broad cluster-wide work on a regular basis.
- The browser eagerly prefetches detail for all nodes after inventory loads.
- Node-agent requests rediscover the target pod through the Kubernetes API every time.
- Expensive host-detail data is fetched too frequently for data that changes rarely.
- The WebSocket layer broadcasts full state snapshots more often than necessary.
- Each authenticated session owns its own inventory refresh work.

## Phase 1: Stop Eager Detail Prefetch

Goal:
- remove the largest avoidable fanout in the system

Actions:
- stop prefetching `/api/nodes/{node}/detail` for every node after each full-state load
- fetch node detail only for the selected node
- optionally prefetch only the currently visible or recently selected node
- keep explicit per-node refresh behavior

Expected impact:
- large reduction in request volume against the web server
- fewer K8s, Nova, and node-agent calls during page load
- faster initial UI load for large inventories

## Phase 2: Split Refresh Tiers by Cost

Goal:
- keep frequently changing state fresh without repeatedly scanning expensive host data

Actions:
- define a fast refresh tier for:
  - K8s readiness
  - cordon state
  - taints
  - compute service status
  - VM and Amphora counts
- define a slow refresh tier for:
  - reboot-required
  - latest installed kernel
  - other lightweight host signals
- treat hardware detail, interface enumeration, and OVN detail as on-demand
- apply separate TTLs to each tier

Expected impact:
- lower steady-state background load
- less churn on node-agent subprocess execution
- more predictable refresh behavior as node count increases

## Phase 3: Move from Full-State Pushes to Deltas

Goal:
- reduce WebSocket payload size and browser-side redraw cost

Actions:
- keep one initial `full_state` when the browser connects
- send only changed node records after that
- batch changed nodes into delta payloads when useful
- rebuild the full sidebar only when node membership changes

Expected impact:
- lower network overhead
- less DOM churn in the browser
- improved responsiveness with large inventories and multiple users

## Phase 4: Cache Node-Agent Resolution and Static Facts

Goal:
- reduce repeated control-plane lookups and repeated expensive host fact collection

Actions:
- cache `node -> node-agent pod IP` in the web process with a short TTL
- invalidate that cache on connection failure or non-ready responses
- cache static host facts with a longer TTL:
  - vendor
  - product
  - BIOS
  - CPU model and topology
  - RAM inventory
- keep dynamic host facts on shorter TTLs:
  - uptime
  - running kernel
  - latest kernel
  - interface state

Expected impact:
- fewer Kubernetes API calls for node-agent discovery
- fewer repeated `nsenter` + shell executions inside the node agent
- faster selected-node detail rendering

## Phase 5: Introduce Shared Inventory State

Goal:
- stop multiplying cluster-wide refresh work by the number of logged-in users

Actions:
- move cluster-wide inventory refresh into one shared worker in the web pod
- let sessions read from shared cached inventory
- keep user auth attached to actions and user-scoped reads only where necessary
- avoid one full Nova/K8s refresh loop per session

Expected impact:
- major reduction in backend API load for multi-user usage
- more consistent UI state between users
- cleaner separation between inventory collection and user interaction

## Phase 6: Prepare for Horizontal Scaling

Goal:
- make multiple web replicas practical without duplicating work

Actions:
- externalize session state
- externalize or centralize shared inventory state
- ensure only one active refresher performs cluster-wide polling, or coordinate ownership cleanly

Expected impact:
- enables higher availability for the web tier
- avoids refresh storms caused by each replica polling the whole environment independently

## Recommended Data Freshness Model

At around 100 nodes, the desired behavior should be:

- node list state refreshes frequently, but only with cheap data
- selected-node detail loads on demand
- hardware and interface data stays cached with TTLs
- OpenStack-wide scans are shared and amortized
- node-agent endpoints are not rediscovered on every request

## Recommended Priority Order

Highest value first:

1. Remove eager node-detail prefetch.
2. Split refresh tiers by data cost.
3. Introduce shared inventory refresh and caching.
4. Replace repeated full-state broadcasts with deltas.
5. Cache node-agent resolution and static host facts.
6. Externalize session and shared state for multi-replica deployments.

## Summary

The biggest immediate wins are reducing broad detail fanout and separating cheap refresh data from expensive refresh data. Once those are in place, the next major scaling gain comes from shared inventory state so that more users do not linearly increase backend polling cost.
