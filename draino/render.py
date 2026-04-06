"""Pure rendering helpers — produce Rich markup strings and Text cells.

Nothing in this module touches the Textual app or any I/O.  Every function
takes plain data (NodeState, dicts) and returns str or rich.text.Text.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from rich.text import Text

from .models import NodePhase, NodeState, StepStatus

# ── Visual constants ──────────────────────────────────────────────────────────

STEP_ICON: dict[StepStatus, str] = {
    StepStatus.PENDING: "○",
    StepStatus.RUNNING: "◉",
    StepStatus.SUCCESS: "✓",
    StepStatus.FAILED:  "✗",
    StepStatus.SKIPPED: "—",
}
STEP_COLOR: dict[StepStatus, str] = {
    StepStatus.PENDING: "dim",
    StepStatus.RUNNING: "bold yellow",
    StepStatus.SUCCESS: "bold green",
    StepStatus.FAILED:  "bold red",
    StepStatus.SKIPPED: "dim",
}
PHASE_COLOR: dict[NodePhase, str] = {
    NodePhase.IDLE:       "white",
    NodePhase.RUNNING:    "yellow",
    NodePhase.COMPLETE:   "bright_green",
    NodePhase.ERROR:      "bright_red",
    NodePhase.UNDRAINING: "cyan",
    NodePhase.REBOOTING:  "bold magenta",
}
PHASE_LABEL: dict[NodePhase, str] = {
    NodePhase.IDLE:       "IDLE",
    NodePhase.RUNNING:    "RUNNING",
    NodePhase.COMPLETE:   "COMPLETE",
    NodePhase.ERROR:      "ERROR",
    NodePhase.UNDRAINING: "UNDRAINING",
    NodePhase.REBOOTING:  "REBOOTING",
}
OP_COLOR: dict[str, str] = {
    "queued":         "dim",
    "migrating":      "yellow",
    "cold-migrating": "yellow",
    "confirming":     "yellow",
    "failing_over":   "yellow",
    "complete":       "green",
    "failed":         "bold red",
    "pending":        "dim",
}

# ── Uptime formatter ──────────────────────────────────────────────────────────

def format_uptime(since) -> str:
    """Return a human-readable age string from a timezone-aware datetime."""
    now   = datetime.now(timezone.utc)
    delta = now - since
    total = int(delta.total_seconds())
    days  = total // 86400
    hours = (total % 86400) // 3600
    mins  = (total % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"

# ── Table cell helpers ────────────────────────────────────────────────────────

def node_name_text(name: str, state: Optional["NodeState"] = None) -> Text:
    t = Text(name)
    if state is None:
        return t
    if state.is_etcd:
        t.append("  etcd", style="bold red")
    if state.reboot_required:
        t.append("  reboot", style="bold yellow")
    if (
        state.latest_kernel_version
        and state.kernel_version
        and state.latest_kernel_version != state.kernel_version
    ):
        t.append("  kernel", style="bold cyan")
    return t


def role_text(state: NodeState) -> Text:
    if state.is_etcd:
        return Text.from_markup("[bold red]etcd[/bold red]")
    return Text("")


def nova_svc_text(state: NodeState) -> Text:
    s = state.compute_status
    if s is None:
        return Text.from_markup("[dim]…[/dim]")
    if s == "up":
        return Text.from_markup("[green]enabled[/green]")
    if s == "disabled":
        return Text.from_markup("[yellow]disabled[/yellow]")
    if s == "down":
        return Text.from_markup("[bold red]DOWN[/bold red]")
    return Text.from_markup(f"[dim]{s}[/dim]")


def phase_text(state: NodeState) -> Text:
    if state.phase != NodePhase.IDLE:
        color = PHASE_COLOR[state.phase]
        label = PHASE_LABEL[state.phase]
        return Text.from_markup(f"[{color}]{label}[/{color}]")
    if state.k8s_cordoned:
        return Text.from_markup("[dim]CORDONED[/dim]")
    if not state.k8s_ready:
        return Text.from_markup("[red]NOT READY[/red]")
    return Text.from_markup("[white]IDLE[/white]")


def k8s_status_text(state: NodeState) -> Text:
    if state.k8s_cordoned:
        return Text.from_markup("[dim]Cordoned[/dim]")
    if not state.k8s_ready:
        return Text.from_markup("[red]Not Ready[/red]")
    return Text.from_markup("[green]Ready[/green]")


def count_text(count: Optional[int]) -> Text:
    if count is None:
        return Text.from_markup("[dim]…[/dim]")
    color = "cyan" if count > 0 else "dim"
    return Text.from_markup(f"[{color}]{count}[/{color}]")


def uptime_text(state: NodeState) -> Text:
    if state.uptime is None:
        return Text.from_markup("[dim]…[/dim]")
    return Text(state.uptime, style="cyan")


def kernel_text(state: NodeState) -> Text:
    if state.kernel_version is None:
        return Text.from_markup("[dim]…[/dim]")
    return Text(state.kernel_version, style="dim")

# ── Workflow panel renderer ───────────────────────────────────────────────────

def render_workflow(state: NodeState, etcd_peers: list[NodeState]) -> str:
    """Return Rich markup string for the workflow detail panel.

    *etcd_peers* should be all NodeState objects that carry the etcd role,
    including *state* itself when applicable.
    """
    lines: list[str] = []
    phase_color = PHASE_COLOR[state.phase]
    phase_label = PHASE_LABEL[state.phase]

    etcd_badge = "  [bold red]etcd[/bold red]" if state.is_etcd else ""
    lines += [
        f"[bold]{state.k8s_name}[/bold]{etcd_badge}   "
        f"[{phase_color}][ {phase_label} ][/{phase_color}]",
    ]

    # ── etcd quorum warning ───────────────────────────────────────────────
    if state.is_etcd:
        etcd_total    = len(etcd_peers)
        quorum_needed = (etcd_total // 2) + 1
        safe_down     = etcd_total - quorum_needed

        checked = [s for s in etcd_peers if s.etcd_healthy is not None]
        if not checked:
            lines += [
                "",
                "[dim yellow]⚠  etcd node — health unknown "
                "(will be checked before reboot)[/dim yellow]",
                "",
            ]
        else:
            healthy   = sum(1 for s in etcd_peers if s.etcd_healthy is True)
            unknown   = sum(1 for s in etcd_peers if s.etcd_healthy is None)
            remaining = healthy - (1 if state.etcd_healthy is True else 0)
            at_risk   = remaining < quorum_needed

            health_parts = [
                f"{s.k8s_name} {'✓' if s.etcd_healthy else ('?' if s.etcd_healthy is None else '✗')}"
                for s in etcd_peers
            ]

            if at_risk:
                lines += [
                    "",
                    f"[bold red]⚠  ETCD QUORUM RISK — {healthy}/{etcd_total} healthy, "
                    f"reboot would leave {remaining} (need {quorum_needed})[/bold red]",
                    f"[dim red]   {' · '.join(health_parts)}[/dim red]",
                    "",
                ]
            else:
                suffix = f"  [dim]({unknown} unchecked)[/dim]" if unknown else ""
                lines += [
                    "",
                    f"[yellow]⚠  etcd — {healthy}/{etcd_total} healthy, "
                    f"safe to work on {safe_down} at a time[/yellow]{suffix}",
                    f"[dim]   {' · '.join(health_parts)}[/dim]",
                    "",
                ]

    # ── Non-compute short path ────────────────────────────────────────────
    if not state.is_compute and state.phase not in (NodePhase.UNDRAINING, NodePhase.REBOOTING):
        k8s_status = (
            "[dim]Cordoned[/dim]"  if state.k8s_cordoned else
            "[red]Not Ready[/red]" if not state.k8s_ready  else
            "[green]Ready[/green]"
        )
        lines += [
            f"[dim]K8s status: {k8s_status}[/dim]",
            "",
            "[dim]Non-compute node — no OpenStack evacuation needed.[/dim]",
        ]
        if state.k8s_cordoned:
            lines += [
                "",
                "[dim]Press [bold]U[/bold] or click "
                "[bold]Undrain Node[/bold] to uncordon.[/dim]",
            ]
        return "\n".join(lines)

    # ── Compute node detail ───────────────────────────────────────────────
    nova_svc = {
        "up":       "[green]enabled[/green]",
        "disabled": "[yellow]disabled[/yellow]",
        "down":     "[bold red]DOWN[/bold red]",
    }.get(state.compute_status or "", "[dim]unknown[/dim]")

    lines += [
        f"[dim]Hypervisor: {state.hypervisor}   Nova compute: {nova_svc}[/dim]",
        "",
    ]

    # ── Idle: preflight instance preview ─────────────────────────────────
    if not state.steps:
        if state.preflight_loading:
            lines.append("[dim]Fetching instances…[/dim]")
        else:
            vms  = [i for i in state.preflight_instances if not i["is_amphora"]]
            amps = [i for i in state.preflight_instances if     i["is_amphora"]]
            if vms:
                W = 34
                lines.append("[bold underline]Instances[/bold underline]")
                lines.append("")
                lines.append(f"[dim]  {'Name':<{W}} {'Status':<12} Storage[/dim]")
                lines.append(f"  [dim]{'─' * (W + 24)}[/dim]")
                for inst in vms:
                    name = (
                        inst["name"][: W - 2] + ".."
                        if len(inst["name"]) > W
                        else inst["name"]
                    )
                    storage = (
                        "[cyan]Volume[/cyan]"
                        if inst["is_volume_backed"]
                        else "[dim]Ephemeral[/dim]"
                    )
                    lines.append(
                        f"  [white]{name:<{W}}[/white]"
                        f"[dim]{inst['status']:<12}[/dim]"
                        f"{storage}"
                    )
                lines.append("")
                if amps:
                    lines.append(f"  [dim]+ {len(amps)} Amphora instance(s)[/dim]")
            elif state.preflight_instances:
                lines.append(
                    f"[dim]{len(state.preflight_instances)} Amphora instance(s), "
                    f"no regular VMs.[/dim]"
                )
            elif state.preflight_instances is not None and not state.preflight_loading:
                lines.append("[dim]No instances on this hypervisor.[/dim]")
        lines.append("")
        lines.append(
            "[dim]Press [bold]S[/bold] or click "
            "[bold]Start Evacuation[/bold] to begin.[/dim]"
        )
        if state.k8s_cordoned or state.compute_status == "disabled":
            lines.append(
                "[dim]Press [bold]U[/bold] or click "
                "[bold]Undrain Node[/bold] to re-enable.[/dim]"
            )
        return "\n".join(lines)

    # ── Live downtime counter (during reboot) ─────────────────────────────
    if state.phase == NodePhase.REBOOTING and state.reboot_start is not None:
        elapsed = int(time.time() - state.reboot_start)
        lines += [
            f"[bold magenta]⏱  Downtime: {elapsed}s[/bold magenta]",
            "",
        ]

    # ── Workflow steps ────────────────────────────────────────────────────
    lines.append("[bold underline]Workflow Steps[/bold underline]")
    lines.append("")
    for step in state.steps:
        icon       = STEP_ICON[step.status]
        color      = STEP_COLOR[step.status]
        detail_str = f"  [dim]{step.detail}[/dim]" if step.detail else ""
        lines.append(f"  [{color}]{icon}  {step.label}[/{color}]{detail_str}")

    if state.phase == NodePhase.IDLE:
        if state.reboot_downtime is not None:
            dt = int(state.reboot_downtime)
            lines += [
                "",
                f"[bold green]✓ Reboot complete — total downtime: {dt}s[/bold green]",
            ]
        else:
            lines.append(
                "[dim]Press [bold]S[/bold] or click "
                "[bold]Start Evacuation[/bold] to begin.[/dim]"
            )
    lines.append("")

    # ── Instance table ────────────────────────────────────────────────────
    if state.instances:
        W = 36
        lines.append("[bold underline]Instances[/bold underline]")
        lines.append("")
        lines.append(
            f"[dim]  {'Name':<{W}} {'Type':<9} {'Nova State':<14} Op Status[/dim]"
        )
        lines.append(f"  [dim]{'─' * (W + 9 + 14 + 12)}[/dim]")
        for inst in state.instances:
            name = (
                inst.name[: W - 2] + ".."
                if len(inst.name) > W
                else inst.name
            )
            itype    = "Amphora" if inst.is_amphora else "VM"
            op       = (
                (inst.failover_status  or "pending") if inst.is_amphora
                else (inst.migration_status or "pending")
            )
            tc       = "cyan" if inst.is_amphora else "white"
            op_color = OP_COLOR.get(op, "white")
            lines.append(
                f"  [{tc}]{name:<{W}}[/{tc}]"
                f"[dim] {itype:<9}{inst.status:<14}[/dim]"
                f"[{op_color}]{op}[/{op_color}]"
            )

    return "\n".join(lines)

# ── Pods panel renderer ───────────────────────────────────────────────────────

def render_pods(
    node_name: str,
    pods: list[dict],
    hide_succeeded: bool = True,
) -> str:
    """Return Rich markup string for the pods detail panel."""
    lines: list[str] = [
        f"[bold]{node_name}[/bold]   [cyan][ PODS ][/cyan]",
        "",
    ]

    succeeded = [p for p in pods if p.get("phase") == "Succeeded"]
    visible   = pods if not hide_succeeded else [p for p in pods if p.get("phase") != "Succeeded"]

    if not visible and not succeeded:
        lines.append("[dim]No pods scheduled on this node.[/dim]")
        return "\n".join(lines)

    NS, NM = 18, 36
    lines.append(
        f"[dim]  {'Namespace':<{NS}} {'Name':<{NM}} "
        f"{'Ready':<6} {'Status':<12} {'Restarts':<10} Age[/dim]"
    )
    lines.append(f"  [dim]{'─' * (NS + NM + 6 + 12 + 10 + 8)}[/dim]")

    for pod in sorted(visible, key=lambda p: (p["namespace"], p["name"])):
        ns  = pod["namespace"]
        nm  = pod["name"]
        if len(ns) > NS:
            ns = ns[: NS - 2] + ".."
        if len(nm) > NM:
            nm = nm[: NM - 2] + ".."
        ready   = f"{pod['ready_count']}/{pod['total_count']}"
        phase   = pod["phase"]
        age     = format_uptime(pod["created_at"]) if pod["created_at"] else "?"
        pc      = "green" if phase == "Running" else "yellow" if phase == "Pending" else "red"
        rc      = "green" if pod["ready_count"] == pod["total_count"] else "yellow"
        lines.append(
            f"  [dim]{ns:<{NS}}[/dim]"
            f"[white]{nm:<{NM}}[/white]"
            f"[{rc}]{ready:<6}[/{rc}]"
            f"[{pc}]{phase:<12}[/{pc}]"
            f"[dim]{pod['restarts']:<10}[/dim]"
            f"[cyan]{age}[/cyan]"
        )

    lines.append("")
    if hide_succeeded and succeeded:
        lines.append(
            f"[dim]{len(visible)} pod(s)   "
            f"{len(succeeded)} Succeeded hidden — press [bold]H[/bold] to show[/dim]"
        )
    elif not hide_succeeded and succeeded:
        lines.append(
            f"[dim]{len(visible)} pod(s) ({len(succeeded)} Succeeded)   "
            f"press [bold]H[/bold] to hide[/dim]"
        )
    else:
        lines.append(f"[dim]{len(visible)} pod(s)   auto-refreshing every 5s[/dim]")

    return "\n".join(lines)
