# Global Search Plan

## Goal

Make the top search box in the app header actually useful across nodes, projects, OpenStack resources, and Kubernetes resources.

The current input in `draino/web/static/index.html` is visual only. It has no event handling, no search state, no results UI, and no backend support for cross-resource search.

## Recommended Approach

Implement global search as a hybrid:

1. Client-side instant search over already-loaded data.
2. Server-side federated search for resources not currently loaded.

This gives:

- fast perceived response
- broad coverage
- lower backend cost than searching everything on every keystroke
- better UX than server-only search

## Why Hybrid Fits This App

This app is mostly I/O-bound on upstream systems:

- OpenStack APIs
- Kubernetes APIs
- node-agent calls

Client-side search is good when the data is already in memory.
Server-side search is needed when the user has not yet opened the relevant view, or when loading all resources into the browser would be too expensive.

## Search Coverage

### Phase 1: Client-side local search

Search already-loaded browser state:

- nodes
- projects
- networks
- routers
- load balancers
- security groups
- volumes
- loaded Kubernetes resource caches

### Phase 2: Server-side federated search

Add a backend endpoint to search resources that are not already loaded:

- instances across all hypervisors
- projects not loaded into current browser state
- floating IPs
- additional OpenStack resources
- Kubernetes resources not already cached locally

## Result Model

Normalize all search hits into a single result shape:

```json
{
  "kind": "instance",
  "id": "vm-1",
  "label": "prod-api-01",
  "subtext": "Project production • cmp-a12 • 10.0.0.5",
  "view": "projects",
  "project_id": "proj-1",
  "score": 97
}
```

Recommended common fields:

- `kind`
- `id`
- `label`
- `subtext`
- `view`
- `score`

Optional fields by resource type:

- `project_id`
- `node_name`
- `network_id`
- `resource_id`
- `ip_address`

## Resource Types To Support First

Start with the highest-value objects:

- Nodes
- Instances
- Projects
- Networks
- Routers
- Floating IPs
- Load balancers
- Security groups
- Volumes
- Kubernetes namespaces
- Kubernetes pods
- Kubernetes services

## UX Behavior

The top search should:

- open a dropdown as the user types
- group results by resource type
- rank exact matches first
- support keyboard navigation
- open the top result on Enter
- navigate directly to the relevant view and object when clicked

Examples:

- `cmp-a01` should find a node
- `10.0.0.5` should find an instance, port, or floating IP
- `prod-api` should find a project, network, or VM
- `vol-...` should find a volume
- `router-...` should find a router

## Ranking

Use simple deterministic ranking first:

1. exact ID match
2. exact name match
3. prefix match
4. substring match

Add boosts for:

- IP address matches
- currently visible scope
- recently selected objects

## Frontend Implementation Plan

### Phase 1

Add wiring for the existing top search box:

- attach `oninput`
- maintain global search state
- render a dropdown
- search local in-memory state
- support keyboard navigation
- support click-to-navigate

Suggested files:

- `draino/web/static/index.html`
- `draino/web/static/app_core.js`
- new file: `draino/web/static/app_search.js`

### Navigation behavior

Each result should know how to open itself:

- node result: switch to Infrastructure and select node
- project result: switch to Projects and select project
- network result: switch to Networking and open network detail
- volume result: switch to Storage and open volume detail
- instance result: route to the most appropriate context, likely Projects or Infrastructure depending on available metadata

## Backend Implementation Plan

### Phase 2

Add:

- `GET /api/search?q=...&limit=...`

Suggested files:

- `draino/web/api/resources.py`
- `draino/web/resource_helpers.py`

The backend should:

- fan out only across needed resource helpers
- normalize all results into one shared schema
- deduplicate results by kind/id
- return a capped result set

## Suggested Rollout

### Phase 1

Client-side search only:

- immediate value
- low risk
- no backend cost

### Phase 2

Hybrid search:

- add `/api/search`
- merge server results with local results
- show loading state such as `Searching more…`

### Phase 3

Improve quality:

- better ranking
- alias handling
- recent selection boosts
- broader resource coverage

## Recommendation

Do not build this as server-only search.
Do not leave it as client-only search.

Hybrid search is the right fit for this app.

## Next Revisit Scope

When this work is resumed, the best first deliverable is:

- dropdown UI
- local search over loaded node/project/network/storage data
- direct navigation on result selection
- optional backend search for instances and other unloaded resources
