"""Draino — main Textual TUI application."""
from __future__ import annotations

import threading
from typing import Optional

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widgets import Button, DataTable, Footer, Header, RichLog, Static

from .models import NodePhase, NodeState, StepStatus
from .operations import k8s_ops, openstack_ops
from . import worker

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
    NodePhase.IDLE:     "white",
    NodePhase.RUNNING:  "yellow",
    NodePhase.COMPLETE: "bright_green",
    NodePhase.ERROR:    "bright_red",
}
PHASE_LABEL: dict[NodePhase, str] = {
    NodePhase.IDLE:     "IDLE",
    NodePhase.RUNNING:  "RUNNING",
    NodePhase.COMPLETE: "COMPLETE",
    NodePhase.ERROR:    "ERROR",
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

# Column keys — compute table
_COL_NODE  = "col_node"
_COL_NOVA  = "col_nova"
_COL_PHASE = "col_phase"
_COL_AMP   = "col_amp"
_COL_VMS   = "col_vms"

# Column keys — other table (scoped to that table; same string names are fine)
_COL_OTHER_NODE   = "col_node"
_COL_OTHER_STATUS = "col_status"


class DrainoApp(App):
    """Draino — drain OpenStack hypervisors and K8s nodes before a reboot."""

    CSS = """
    Screen {
        layout: vertical;
        background: $background;
    }

    /* ── Top split ── */
    #main {
        layout: horizontal;
        height: 1fr;
    }

    /* ── Left: two stacked node panels ── */
    #node-panel {
        width: 66;
        min-width: 50;
        layout: vertical;
    }

    /* Compute nodes — takes all remaining vertical space */
    #compute-section {
        height: 1fr;
        layout: vertical;
        border: solid $primary-darken-2;
    }
    #compute-section-title {
        background: $primary-darken-2;
        color: $text;
        text-align: center;
        padding: 0 1;
        text-style: bold;
    }
    #compute-table {
        height: 1fr;
        scrollbar-gutter: stable;
    }

    /* Other nodes — auto-sizes to content, capped at 12 rows */
    #other-section {
        height: auto;
        max-height: 14;
        layout: vertical;
        border: solid $primary-darken-2;
    }
    #other-section-title {
        background: $primary-darken-2;
        color: $text;
        text-align: center;
        padding: 0 1;
        text-style: bold;
    }
    #other-table {
        height: auto;
        scrollbar-gutter: stable;
    }

    /* ── Right: workflow + log ── */
    #right-panel {
        width: 1fr;
        layout: vertical;
    }
    #workflow-scroll {
        height: 1fr;
        border: solid $primary-darken-2;
    }
    #workflow-content {
        padding: 1 2;
    }

    /* ── Log panel ── */
    #log-panel {
        height: 14;
        layout: vertical;
        border: solid $primary-darken-2;
    }
    #log-title {
        background: $primary-darken-2;
        color: $text;
        padding: 0 1;
        text-style: bold;
    }
    #log-view {
        height: 1fr;
        padding: 0 1;
    }

    /* ── Action bar ── */
    #action-bar {
        height: auto;
        layout: horizontal;
        align: center middle;
        padding: 0 2;
        background: $surface;
    }
    Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        ("s",  "start",   "Start Evacuation"),
        ("r",  "refresh", "Refresh Nodes"),
        ("q",  "quit",    "Quit"),
        ("f5", "refresh", "Refresh"),
    ]

    def __init__(
        self,
        cloud:   Optional[str] = None,
        context: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.cloud   = cloud
        self.context = context
        self.node_states: dict[str, NodeState] = {}
        self.selected_node: Optional[str] = None
        # Cached K8s node list so _rebuild_tables() can access cordoned/ready
        self._last_k8s_nodes: list[dict] = []

    # ── Layout ───────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            with Vertical(id="node-panel"):
                # ── Compute nodes (full detail) ──
                with Vertical(id="compute-section"):
                    yield Static("  Compute Nodes", id="compute-section-title")
                    yield DataTable(id="compute-table", cursor_type="row")
                # ── Everything else ──
                with Vertical(id="other-section"):
                    yield Static("  Other Nodes", id="other-section-title")
                    yield DataTable(id="other-table", cursor_type="row")
            with Vertical(id="right-panel"):
                with ScrollableContainer(id="workflow-scroll"):
                    yield Static(
                        "[dim]Select a node from the list to view its status.[/dim]",
                        id="workflow-content",
                        markup=True,
                    )
                with Vertical(id="log-panel"):
                    yield Static("  Event Log", id="log-title")
                    yield RichLog(
                        id="log-view",
                        highlight=True,
                        markup=True,
                        wrap=False,
                    )
        with Horizontal(id="action-bar"):
            yield Button("▶  Start Evacuation", id="btn-start",   variant="primary")
            yield Button("⟳  Refresh Nodes",    id="btn-refresh", variant="default")
        yield Footer()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        openstack_ops.configure(cloud=self.cloud)
        k8s_ops.configure(context=self.context)

        ct = self.query_one("#compute-table", DataTable)
        ct.add_column("Node",     key=_COL_NODE,  width=22)
        ct.add_column("Nova Svc", key=_COL_NOVA,  width=10)
        ct.add_column("Phase",    key=_COL_PHASE, width=10)
        ct.add_column("AMP",      key=_COL_AMP,   width=5)
        ct.add_column("VMs",      key=_COL_VMS,   width=5)

        ot = self.query_one("#other-table", DataTable)
        ot.add_column("Node",   key=_COL_OTHER_NODE,   width=40)
        ot.add_column("Status", key=_COL_OTHER_STATUS, width=12)

        self.action_refresh()
        self.set_interval(15, self._auto_refresh)

    # ── Node loading ──────────────────────────────────────────────────────────

    def action_refresh(self) -> None:
        self._global_log("[dim]Refreshing node list…[/dim]")
        threading.Thread(target=self._load_nodes_bg, daemon=True).start()

    def _load_nodes_bg(self) -> None:
        """Load K8s nodes then fetch all OpenStack summaries in a single pass."""
        try:
            nodes = k8s_ops.get_nodes()
        except Exception as exc:
            self.call_from_thread(
                self._global_log,
                f"[bold red]Error loading K8s nodes:[/bold red] {exc}",
            )
            return

        # Show K8s nodes immediately; all go to compute-table until we know better
        self.call_from_thread(self._populate_initial, nodes)

        def _os_log(msg: str) -> None:
            self.call_from_thread(self._global_log, f"[dim]{msg}[/dim]")

        try:
            summaries = openstack_ops.get_all_host_summaries(log_cb=_os_log)
        except Exception as exc:
            self.call_from_thread(
                self._global_log,
                f"[dim red]OpenStack summary failed: {exc}[/dim red]",
            )
            return

        self.call_from_thread(self._apply_summaries_and_rebuild, nodes, summaries)

    def _populate_initial(self, nodes: list[dict]) -> None:
        """First pass: create NodeState entries and show all nodes in compute-table.

        We don't yet know which are compute vs other, so everything lands here
        temporarily.  _apply_summaries_and_rebuild() will split them correctly.
        """
        self._last_k8s_nodes = nodes
        ct = self.query_one("#compute-table", DataTable)
        ct.clear()
        self.query_one("#other-table", DataTable).clear()

        for nd in nodes:
            name:     str  = nd["name"]
            hostname: str  = nd.get("hostname", name)

            if name not in self.node_states:
                self.node_states[name] = NodeState(
                    k8s_name=name, hypervisor=hostname
                )

            state = self.node_states[name]
            state.k8s_ready    = nd.get("ready", True)
            state.k8s_cordoned = nd.get("cordoned", False)

            ct.add_row(
                name,
                self._nova_svc_text(state),
                self._phase_text(state),
                self._count_text(state.amphora_count),
                self._count_text(state.vm_count),
                key=name,
            )

        if self.selected_node:
            self._refresh_workflow()

    def _apply_summaries_and_rebuild(
        self, nodes: list[dict], summaries: dict[str, dict]
    ) -> None:
        """Apply OpenStack data then split nodes across the two tables."""
        # 1. Update every NodeState with its summary
        for nd in nodes:
            name     = nd["name"]
            hostname = nd.get("hostname", name)
            summary  = summaries.get(hostname, {})
            state    = self.node_states.get(name)
            if not state:
                continue
            if state.phase == NodePhase.IDLE:
                state.is_compute     = summary.get("is_compute", False)
                state.compute_status = summary.get("compute_status")
                state.amphora_count  = summary.get("amphora_count")
                state.vm_count       = summary.get("vm_count")

        # 2. Rebuild both tables
        self._rebuild_tables()

    def _rebuild_tables(self) -> None:
        """Clear and repopulate compute-table and other-table from current state."""
        ct = self.query_one("#compute-table", DataTable)
        ot = self.query_one("#other-table",   DataTable)
        ct.clear()
        ot.clear()

        for nd in self._last_k8s_nodes:
            name  = nd["name"]
            state = self.node_states.get(name)
            if not state:
                continue

            if state.is_compute or state.phase != NodePhase.IDLE:
                # Always keep active/complete/error nodes in the compute table
                ct.add_row(
                    name,
                    self._nova_svc_text(state),
                    self._phase_text(state),
                    self._count_text(state.amphora_count),
                    self._count_text(state.vm_count),
                    key=name,
                )
            else:
                ot.add_row(
                    name,
                    self._k8s_status_text(state),
                    key=name,
                )

        if self.selected_node:
            self._refresh_workflow()

    # ── Text helpers ──────────────────────────────────────────────────────────

    def _nova_svc_text(self, state: NodeState) -> Text:
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

    def _phase_text(self, state: NodeState) -> Text:
        if state.phase != NodePhase.IDLE:
            color = PHASE_COLOR[state.phase]
            label = PHASE_LABEL[state.phase]
            return Text.from_markup(f"[{color}]{label}[/{color}]")
        if state.k8s_cordoned:
            return Text.from_markup("[dim]CORDONED[/dim]")
        if not state.k8s_ready:
            return Text.from_markup("[red]NOT READY[/red]")
        return Text.from_markup("[white]IDLE[/white]")

    def _k8s_status_text(self, state: NodeState) -> Text:
        if state.k8s_cordoned:
            return Text.from_markup("[dim]Cordoned[/dim]")
        if not state.k8s_ready:
            return Text.from_markup("[red]Not Ready[/red]")
        return Text.from_markup("[green]Ready[/green]")

    def _count_text(self, count: Optional[int]) -> Text:
        if count is None:
            return Text.from_markup("[dim]…[/dim]")
        color = "cyan" if count > 0 else "dim"
        return Text.from_markup(f"[{color}]{count}[/{color}]")

    # ── Event handlers ────────────────────────────────────────────────────────

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key and event.row_key.value is not None:
            self.selected_node = str(event.row_key.value)
            self._refresh_workflow()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-start":
            self.action_start()
        elif event.button.id == "btn-refresh":
            self.action_refresh()

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_start(self) -> None:
        if not self.selected_node:
            self._global_log("[yellow]No node selected.[/yellow]")
            return

        state = self.node_states.get(self.selected_node)
        if not state:
            return

        if not state.is_compute:
            self._global_log(
                f"[yellow]{self.selected_node} is not a compute node — "
                f"no OpenStack evacuation needed.[/yellow]"
            )
            return

        if state.phase == NodePhase.RUNNING:
            self._global_log(
                f"[yellow]Evacuation already in progress for "
                f"[bold]{self.selected_node}[/bold][/yellow]"
            )
            return

        state.phase      = NodePhase.RUNNING
        state.instances  = []
        state.log_buffer = []
        state.init_steps()

        node_name = self.selected_node  # capture for closures

        def update_cb() -> None:
            self.call_from_thread(self._on_state_changed, node_name)

        def log_cb(msg: str) -> None:
            self.call_from_thread(
                self._global_log,
                f"[dim cyan]{node_name}[/dim cyan]  {msg}",
            )

        threading.Thread(
            target=worker.run_workflow,
            args=(state, update_cb, log_cb),
            daemon=True,
        ).start()

        self._global_log(
            f"[bold]Starting evacuation of [cyan]{node_name}[/cyan]…[/bold]"
        )
        self._refresh_workflow()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _auto_refresh(self) -> None:
        self.action_refresh()

    def _on_state_changed(self, node_name: str) -> None:
        """Called on the main thread whenever the worker mutates node state."""
        state = self.node_states.get(node_name)
        if not state:
            return

        # Compute nodes live in the compute-table
        if state.is_compute:
            ct = self.query_one("#compute-table", DataTable)
            try:
                ct.update_cell(node_name, _COL_NOVA,  self._nova_svc_text(state))
                ct.update_cell(node_name, _COL_PHASE, self._phase_text(state))
                ct.update_cell(node_name, _COL_AMP,   self._count_text(state.amphora_count))
                ct.update_cell(node_name, _COL_VMS,   self._count_text(state.vm_count))
            except Exception:
                pass
        else:
            ot = self.query_one("#other-table", DataTable)
            try:
                ot.update_cell(node_name, _COL_OTHER_STATUS, self._k8s_status_text(state))
            except Exception:
                pass

        if node_name == self.selected_node:
            self._refresh_workflow()

    def _refresh_workflow(self) -> None:
        view = self.query_one("#workflow-content", Static)
        if not self.selected_node:
            view.update("[dim]Select a node from the list to view its status.[/dim]")
            return
        state = self.node_states.get(self.selected_node)
        if not state:
            view.update("[dim]Node state not found.[/dim]")
            return
        view.update(self._render_workflow(state))

    def _render_workflow(self, state: NodeState) -> str:
        lines: list[str] = []
        phase_color = PHASE_COLOR[state.phase]
        phase_label = PHASE_LABEL[state.phase]

        lines += [
            f"[bold]{state.k8s_name}[/bold]   "
            f"[{phase_color}][ {phase_label} ][/{phase_color}]",
        ]

        if not state.is_compute:
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
            return "\n".join(lines)

        nova_svc = {
            "up":       "[green]enabled[/green]",
            "disabled": "[yellow]disabled[/yellow]",
            "down":     "[bold red]DOWN[/bold red]",
        }.get(state.compute_status or "", "[dim]unknown[/dim]")

        lines += [
            f"[dim]Hypervisor: {state.hypervisor}   Nova compute: {nova_svc}[/dim]",
            "",
        ]

        if not state.steps:
            lines.append(
                "[dim]Press [bold]S[/bold] or click "
                "[bold]Start Evacuation[/bold] to begin.[/dim]"
            )
            return "\n".join(lines)

        # ── Workflow steps ──
        lines.append("[bold underline]Workflow Steps[/bold underline]")
        lines.append("")
        for step in state.steps:
            icon       = STEP_ICON[step.status]
            color      = STEP_COLOR[step.status]
            detail_str = f"  [dim]{step.detail}[/dim]" if step.detail else ""
            lines.append(
                f"  [{color}]{icon}  {step.label}[/{color}]{detail_str}"
            )
        lines.append("")

        # ── Instance table ──
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

    def _global_log(self, msg: str) -> None:
        self.query_one("#log-view", RichLog).write(msg)
