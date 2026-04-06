# Node-Local Reboot Agent Design

## Purpose

This document describes a safer replacement for direct SSH-based reboot support in
Draino's web UI.

The current SSH model allows the Draino pod to hold a long-lived private key that may be
trusted by many or all nodes. That is operationally simple, but it creates a large blast
radius if the pod, Secret, or an authenticated session is compromised.

The proposed replacement is a node-local reboot agent deployed as a DaemonSet. Each
agent is responsible only for the node it runs on and exposes a minimal control surface
for reboot-related actions.

## Goals

- remove the need for a shared SSH private key in the Draino pod
- reduce credential blast radius from "all nodes" to "this node"
- preserve Draino's existing drain, evacuation, and recovery workflow
- keep the reboot interface narrow and auditable
- fit naturally into Kubernetes and Genestack operational patterns

## Non-Goals

- provide a general remote execution service
- expose arbitrary shell or host command execution
- replace Kubernetes drain or OpenStack evacuation logic already handled by Draino
- solve bare-metal power management or out-of-band BMC workflows

## High-Level Architecture

The design introduces a small reboot agent running as a DaemonSet:

- one pod per node
- each pod knows the Kubernetes node it is bound to
- each pod exposes a minimal authenticated API
- each pod can reboot only its own host

Draino continues to own the orchestration workflow:

1. authenticate the operator
2. validate maintenance preconditions
3. cordon/drain or evacuate as required
4. identify the target node
5. call the reboot agent on that node
6. monitor recovery and complete the workflow

## Trust Model

The main trust boundary is between the Draino web application and the node-local reboot
agent.

The agent must not trust a caller just because it is inside the cluster. It should
require explicit authenticated requests and should accept only the smallest possible set
of operations.

The node-local agent must also enforce node locality:

- it should know its own node identity from the downward API or pod scheduling metadata
- it should refuse requests that specify any other node
- ideally, the request should not need to supply a node identifier at all

## Recommended Transport

Preferred options, in order:

1. mTLS between Draino and the reboot agent
2. a tightly scoped Kubernetes ServiceAccount token model with server-side validation
3. a temporary bootstrap token model only for lab use

mTLS is the cleanest long-term approach because it gives strong mutual identity and fits
well with an internal service-to-service control plane.

## API Surface

The agent API should stay intentionally small.

Recommended endpoints:

- `GET /health`
- `GET /status`
- `POST /reboot`

Optional response fields:

- agent version
- node name
- current uptime or boot ID
- last reboot request metadata

The reboot endpoint should not accept arbitrary commands or scripts. It should accept a
small structured request, for example:

- requested-by identity
- request ID / correlation ID
- optional reason string

The actual node target should be inferred from the agent instance, not caller input.

## Agent Deployment Model

Recommended deployment shape:

- Kubernetes `DaemonSet`
- one replica per eligible node
- optional node selector or tolerations if only certain node classes should support
  in-band reboot
- dedicated namespace such as `draino-system` or `draino-agent`

The pod will likely need elevated privileges to initiate a host reboot. That should be
treated as sensitive and constrained as much as possible.

Possible host integration patterns:

- call a tightly scoped reboot binary from inside the container with host privileges
- use host PID / namespaces only if strictly required
- rely on a system facility exposed to the container for reboot operations

The exact mechanism can vary by environment, but the API and trust model should not.

## Security Requirements

The agent should be designed assuming the web UI is internet-reachable and may
eventually face hostile traffic.

Minimum controls:

- no shell access and no arbitrary command execution
- no endpoint that proxies arbitrary host operations
- authenticated and authorized callers only
- request logging with user, timestamp, node, and result
- restrictive `NetworkPolicy` allowing access only from Draino
- restrictive RBAC for any Kubernetes API access
- read-only root filesystem where practical
- seccomp, capability, and privilege minimization where practical

If the implementation needs elevated Linux privileges to trigger a reboot, keep them
limited to the agent pod, not the Draino web pod.

## Authorization Model

Draino should remain the user-facing policy enforcement point. The reboot agent should
trust only requests made by Draino's service identity, not by end users directly.

Recommended flow:

1. Draino authenticates the operator and authorizes the reboot action.
2. Draino performs prechecks and workflow gating.
3. Draino sends a signed or mutually authenticated reboot request to the target agent.
4. The agent validates the caller identity and logs the request.
5. The agent reboots its own node.

This keeps user authorization centralized while still preventing direct node control from
arbitrary cluster workloads.

## Service Discovery

The agent must be reachable on a per-node basis. Practical options:

- headless Service plus pod DNS
- direct pod IP lookup from Kubernetes API
- one Service per pod, created by a controller

The simplest first implementation is usually:

- a headless Service
- one DaemonSet pod per node
- Draino resolves the correct pod for the target node

The mapping from Kubernetes node name to agent pod should be explicit and observable.

## Failure Handling

The design must expect partial failures.

Important cases:

- Draino cannot reach the target agent
- the agent receives the request but the node reboots before a success response returns
- the node never comes back
- the node comes back but the agent is not yet healthy
- duplicate reboot requests for the same node

The reboot API should therefore be treated as an idempotent maintenance action from
Draino's perspective. Draino should continue to own retry policy, backoff, and post-boot
verification.

## Auditing

Every reboot request should produce an auditable record that includes:

- timestamp
- authenticated Draino caller identity
- authenticated end-user identity as forwarded by Draino
- node name
- request ID
- action result

Draino should also continue writing its own higher-level workflow audit events so the two
records can be correlated.

## Rollout Plan

Recommended phased approach:

1. design the agent API and trust model
2. prototype the DaemonSet in a lab cluster
3. add a pluggable reboot backend in Draino:
   - `ssh`
   - `node-agent`
4. validate failure handling and recovery behavior
5. deploy the node-agent backend in one environment
6. deprecate shared-key SSH for environments that can adopt the agent

This keeps the migration low-risk and allows environments that still need SSH to retain
it temporarily.

## Open Questions

- what is the narrowest host privilege set required to perform a reboot in the target
  Genestack environment?
- should the agent listen on HTTPS directly, or should it rely on a sidecar or service
  mesh for mTLS?
- should reboot authorization remain entirely in Draino, or should the agent apply its
  own coarse policy checks as a second layer?
- how should the system surface "request accepted but node rebooted before response" to
  the operator?
- is there an existing Genestack maintenance component that could own this function
  instead of introducing a new agent?

## Recommendation

The preferred long-term design is:

- Draino remains the orchestration and authorization layer
- a DaemonSet-based node-local reboot agent performs only local reboot actions
- service-to-service authentication uses mTLS
- shared SSH keys in Kubernetes Secrets are treated as temporary legacy support only

This gives a materially better security model than placing a long-lived SSH private key
with broad node access inside the Draino web application pod.
