# Stress Test Design

This note describes a Heat-based stress testing feature for VibeView that can create disposable infrastructure for scheduler, networking, and control-plane validation.

The goal is to support a strong first version without boxing later extensions into a corner.

## Principles

- one active stress test at a time
- everything created under a single Heat stack
- all resources clearly tagged and easily removable
- no historical persistence required in VibeView
- timing/reporting derived from live Heat and OpenStack state
- extensible to additional profiles later

## First Version Scope

The first version should create:

- one network
- one subnet
- one router
- one router interface
- one security group
- one keypair
- `N` Nova servers

Optional in v1:

- floating IP plumbing
- anti-affinity server group

The whole test must be driven by one Heat stack.

## Naming / Identification

Every test run should have:

- `test_id`
- `stack_name`
- `profile`
- `created_at`

Recommended stack name:

- `vibe-stress-<yyyymmdd>-<hhmmss>`

Tag all resources with:

- `vibeview:stress-test=true`
- `vibeview:test-id=<test_id>`
- `vibeview:stack=<stack_name>`

This makes cleanup and operator inspection straightforward.

## Guardrail

Only one active test may exist at a time.

Before starting a new test:

1. query Heat for stacks with the `vibe-stress-` prefix
2. filter out deleted / delete-complete stacks
3. if any active stack remains, block new test creation

The UI should show:

- active stack name
- current stack status
- explicit delete button

No new test should be allowed until the previous stack is gone.

## Profiles

Design this around profiles so later expansion stays clean.

### `small-distribution`

- small VM count
- one tenant network
- one router
- basic spread test

### `full-host-spread`

- target one VM per compute host
- best-effort spread across hosts
- useful for placement / scheduler validation

### `burst`

- larger VM count on shared plumbing
- stresses Nova + Neutron + scheduler

### `gateway-burst` (later)

- includes floating IPs / external routing
- useful for router/FIP timing

## Heat Template Shape

The template should be parameterized rather than hard-coded.

Suggested parameters:

- `test_id`
- `name_prefix`
- `image`
- `flavor`
- `key_name`
- `network_cidr`
- `vm_count`
- `create_floating_ips`
- `security_group_rules`
- `availability_zone` (optional)

Core resources:

- `OS::Neutron::Net`
- `OS::Neutron::Subnet`
- `OS::Neutron::Router`
- `OS::Neutron::RouterInterface`
- `OS::Neutron::SecurityGroup`
- `OS::Nova::KeyPair`
- `OS::Nova::Server` repeated or a resource group

Useful outputs:

- stack/test identifiers
- network ID
- subnet ID
- router ID
- list of server IDs
- list of server names

## Timing Model

Do not store timing history in VibeView. Derive it live from Heat and OpenStack.

### Layer 1: Heat resource timing

Per resource:

- logical resource name
- resource type
- status
- creation start / end
- elapsed seconds

This gives direct timing for:

- network creation
- subnet creation
- router creation
- interface attachment
- server creation

### Layer 2: grouped report metrics

Derived from Heat resources and current Nova state:

- total stack elapsed time
- plumbing elapsed time
  - network + subnet + router + interface
- average server build time
- p95 server build time
- slowest server build time
- time to all VMs `ACTIVE`

## Reporting Shape

### Summary

- stack name
- stack status
- profile
- requested VM count
- created VM count
- total elapsed time

### Resource Timing Table

Columns:

- type
- logical name
- physical ID
- status
- elapsed
- notes

### VM Timing Table

Columns:

- VM name
- server ID
- host
- status
- build elapsed
- IP

### Distribution Table

Columns:

- host
- VM count
- share %

This can later connect to placement-risk and project-placement logic.

## Data Model

Use a report-oriented payload rather than a hard-wired one-off response.

```json
{
  "test": {
    "test_id": "20260409-141522",
    "stack_name": "vibe-stress-20260409-141522",
    "profile": "full-host-spread",
    "status": "CREATE_COMPLETE",
    "requested_vms": 31,
    "created_vms": 31,
    "active": true
  },
  "summary": {
    "stack_elapsed_s": 434,
    "plumbing_elapsed_s": 18,
    "avg_vm_build_s": 52,
    "p95_vm_build_s": 77,
    "slowest_vm_build_s": 84
  },
  "resources": [],
  "servers": [],
  "distribution": [],
  "error": null
}
```

Keep this generic enough that later:

- multiple profiles
- richer networking
- floating IP timing
- multi-run comparisons

can reuse it without redesign.

## UI Direction

Do not bury this inside generic reports only.

Recommended structure:

- new top-level view later: `Stress`
or
- dedicated report/workflow hybrid under `Reports`

The key point is that this is not purely read-only:

- start test
- monitor progress
- delete test

So it should eventually behave more like a workflow console plus report.

## Extension Path

This design leaves room for:

- multiple named profiles
- anti-affinity / server groups
- floating IP / external routing tests
- volume-backed boot tests
- scheduler policy tests
- host-targeted tests
- per-run comparison reporting

Do not hard-code the first template as the only template shape.

## Recommended First Implementation Order

1. one active-stack guardrail
2. one Heat template profile
3. create/delete stack actions
4. stack summary and status
5. resource timing table
6. VM timing table
7. per-host distribution table

That gets a useful first version without closing off future growth.
