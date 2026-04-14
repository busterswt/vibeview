# Progressive Node Loading Plan

This note outlines how to make the node list feel more incremental and progressively hydrated, while keeping the current websocket/state model intact.

## Current Behavior

Today the node list loads in stages, but it still tends to feel batch-oriented:

1. Kubernetes node membership is loaded with one `list_node()` call.
2. The backend creates or updates `NodeState` objects for all nodes.
3. The backend pushes either:
   - one `full_state` when membership changes, or
   - many `state_update` messages after enrichment
4. OpenStack summaries, host signals, MariaDB placement, edge roles, etc. are then layered onto those node states.

This is already asynchronous in the backend, but the user usually perceives it as “all at once” because the first visible inventory appears as a full list and later enrichment often lands quickly.

## Goal

Make the inventory feel progressively loaded:

- node names appear as soon as possible
- each node visibly hydrates as data arrives
- the sidebar does not wait for secondary enrichment
- the UI makes it obvious which nodes are still incomplete

Without:

- redesigning the websocket protocol from scratch
- introducing persistent state
- breaking the current `NodeState`/`state_update` model

## Recommended Changes

### 1. Push membership immediately and mark nodes as loading

When Kubernetes node membership is known:

- create/update `NodeState` for each node
- set a lightweight loading marker on each node, for example:
  - `inventory_loading = true`
  - `details_loading = true`
- push the initial inventory state immediately

This makes the sidebar render names and base readiness/cordon data right away, even before OpenStack and host-level enrichment complete.

### 2. Split enrichment from membership clearly

Treat inventory loading in two phases:

#### Phase 1: membership
- node names
- ready / cordoned
- taints
- kernel version if already available from K8s node object

#### Phase 2: enrichment
- OpenStack summary
  - compute role
  - compute status
  - VM count
  - amphora count
  - AZ
  - aggregates
- MariaDB / etcd / edge role signals
- host signals
  - reboot required
  - latest kernel
  - node-agent status

The important change is to stop treating those two phases as one user-facing event.

### 3. Parallelize per-node enrichment

After membership is pushed:

- fan out node enrichment using a bounded worker pool
- each worker updates exactly one node’s derived state
- push a `state_update` as each node completes

This gives the user visible progressive hydration and reduces wall-clock time.

Recommended concurrency:

- small bounded pool, e.g. `4` to `8`
- do not blast OpenStack/Kubernetes/node-agent indiscriminately

### 4. Add per-node loading affordance in the sidebar

Render nodes with a visual loading state until enrichment completes:

- muted “loading” dot
- subtle shimmer or dimmed status line
- optional `Refreshing` pill or spinner on the selected node

This matters because progressive loading only feels intentional if the user can tell incomplete nodes are still being enriched.

### 5. Add a backend completion marker per node

Each node should clearly transition from:

- base inventory loaded
to
- enrichment complete

Suggested state field:

- `inventory_complete: bool`

This is better than inferring completion indirectly from missing fields.

### 6. Avoid overusing `full_state`

Right now the UI often receives a full inventory snapshot when membership changes.

Keep that for the membership skeleton only, then prefer:

- per-node `state_update`

This preserves simplicity while making the hydration path feel incremental.

## Minimal Implementation Plan

### Backend

Files:

- [`draino/web/inventory_refresh.py`](/Users/james.denton/github/vibeview/draino/web/inventory_refresh.py)
- [`draino/models.py`](/Users/james.denton/github/vibeview/draino/models.py)
- [`draino/web/serialise.py`](/Users/james.denton/github/vibeview/draino/web/serialise.py)

Changes:

1. Add a node-level loading/completion field to `NodeState`
   - `inventory_complete: bool = False`

2. In `_apply_k8s_nodes(...)`
   - populate base fields only
   - set `inventory_complete = False`

3. After membership is known
   - push the base inventory immediately

4. Replace serial enrichment with bounded parallel enrichment
   - one node at a time
   - update node state in-place
   - set `inventory_complete = True`
   - push `state_update` for that node immediately

### Frontend

Files:

- [`draino/web/static/app_core.js`](/Users/james.denton/github/vibeview/draino/web/static/app_core.js)
- [`draino/web/static/app_infra.js`](/Users/james.denton/github/vibeview/draino/web/static/app_infra.js)
- [`draino/web/static/app.css`](/Users/james.denton/github/vibeview/draino/web/static/app.css)

Changes:

1. Respect `inventory_complete`
   - loading state in tree rows
   - loading state in selected node header/details

2. Preserve partial data when later updates are incomplete
   - similar to the existing instance-list stabilization work

3. Avoid a large “all nodes rerendered as complete” effect
   - only rerender changed rows if practical
   - if not, at least keep the visual diff subtle

## Why This Is Worth Doing

This does not improve absolute time-to-first-byte for the initial node membership call. That call is still a single Kubernetes API request.

What it improves:

- time to first visible inventory
- perceived responsiveness
- operator confidence that the app is still working
- clarity about which node details are still loading

## What This Does Not Solve

This will not fix:

- slow Kubernetes `list_node()` calls
- slow OpenStack summary generation itself
- heavy node-agent latency

It is primarily a perceived responsiveness and progressive hydration improvement.

## Recommended Priority

If implemented later, do it in this order:

1. Add `inventory_complete` and push membership immediately
2. Add sidebar/selected-node loading affordances
3. Parallelize per-node enrichment
4. Fine-tune rerender behavior only if needed

## Recommendation

This is worthwhile if the goal is:

- “make the app feel alive while data is still arriving”

It is less worthwhile if the goal is:

- “dramatically reduce total refresh latency”

For raw latency reduction, report/query optimization gives bigger returns. For inventory UX, progressive hydration is the right next step.
