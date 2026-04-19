# VibeView UI Consistency Rubric

## Core Model

- Left sidebar selects scope.
- Top tabs switch perspective within that scope.
- Main pane shows the working table or summary.
- Right drawer shows detail for the selected object.
- Breadcrumb bar holds scope-level actions only.

## Scope Rules

- `Infrastructure`: scope is node.
- `Projects`: scope is project.
- `Networking`: scope is networking resource family.
- `Storage`: scope is storage resource family.
- `Kubernetes`: scope is Kubernetes resource family.

A scope should not also be a perspective selector.

## Tab Rules

- Tabs answer “what aspect of this scope am I looking at?”
- Tabs should be stable and reusable across scopes where possible.
- Prefer semantic tabs like `Overview`, `Instances`, `Networking`, `Storage`, `Security`, `Capacity`.
- Avoid tabs that are really just alternate object pickers.

## Drawer Rules

- Every object detail opens in the same right drawer pattern.
- Same close behavior everywhere.
- Same resize behavior everywhere.
- Same header structure:
  - object name
  - state badges
  - object-specific actions
- Do not mix inline detail expansion and drawer detail for the same object class unless there is a strong reason.

## Action Placement

- Breadcrumb actions:
  - refresh current scope
  - export
  - scope-wide reports
- Toolbar actions:
  - filter
  - sort
  - column toggles
  - bulk actions on the current list
- Drawer actions:
  - actions on the selected object

Example:

- `Clone VM` belongs in the instance table toolbar and instance drawer, not the breadcrumb.

## Navigation Semantics

- Links navigate to related objects.
- Pills and badges show status, type, or classification only.
- Row click selects and opens the drawer.
- Explicit buttons are for actions, not navigation.

## Loading and Empty States

- Use one loading style everywhere.
- Use one empty-state style everywhere.
- Use one unavailable or auth-required style everywhere.
- Avoid generic “unavailable” when the real state is “not loaded yet” or “this section failed.”

## Table Rules

- Filters always live in the local toolbar.
- Default sort should be obvious and useful.
- First columns should be name and status.
- Last column should be actions only if actions are truly row-specific.
- Avoid repeating project IDs or UUIDs as primary visible content unless the scope requires them.

## Top-Level Section Intent

Each top-level area should have one sentence of purpose:

- `Infrastructure`: host operations
- `Projects`: tenant and workload operations
- `Networking`: network object operations
- `Storage`: storage object operations
- `Kubernetes`: cluster object operations
- `Reports`: cross-scope analysis

If a view mixes two of those, it will feel muddy.

## What This Means For Projects

Recommended shape:

- Left sidebar: projects only
- Tabs:
  - `Overview`
  - `Instances`
  - `Networking`
  - `Storage`
  - `Security`
  - `Quota + Capacity`

Then inside tabs:

- `Networking`: networks, floating IPs, load balancers
- `Storage`: volumes
- `Security`: security groups

That keeps the left nav clean and makes `Projects` feel parallel to `Infrastructure`.

## Consistency Checklist

Before adding or changing a screen, ask:

1. What is the scope?
2. Are tabs perspectives or just more navigation?
3. Does detail belong in the drawer?
4. Are actions placed at the right level?
5. Are links and pills semantically consistent?
6. Does this match how a neighboring section behaves?
