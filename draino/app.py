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
    "queued":       "dim",
    "migrating":    "yellow",
    "failing_over": "yellow",
    "complete":     "green",
    "failed":       "bold red",
    "pending":      "dim",
}

# Column keys for the node DataTable
_COL_NODE    = "col_node"
_COL_NOVA    = "col_nova"
_COL_PHASE   = "col_phase"
_COL_AMP     = "col_amp"
_COL_VMS     = "col_vms"


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

    /* ── Left: node list ── */
    #node-panel {
        width: 66;
        min-width: 50;
        layout: vertical;
        border: solid $primary-darken-2;
    }
    #node-panel-title {
        background: $primary-darken-2;
        color: $text;
        text-align: center;
        padding: 0 1;
        text-style: bold;
    }
    #node-table {
        height: 1fr;
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

    # ── Layout ───────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            with Vertical(id="node-panel"):
                yield Static("  Compute Nodes", id="node-panel-title")
                yield DataTable(id="node-table", cursor_type="row")
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

        table = self.query_one("#node-table", DataTable)
        table.add_column("Node",      key=_COL_NODE,  width=22)
        table.add_column("Nova Svc",  key=_COL_NOVA,  width=10)
        table.add_column("Phase",     key=_COL_PHASE, width=10)
        table.add_column("AMP",       key=_COL_AMP,   width=5)
        table.add_column("VMs",       key=_COL_VMS,   width=5)

        self.action_refresh()
        self.set_interval(15, self._auto_refresh)

    # ── Node loading ──────────────────────────────────────────────────────────

    def action_refresh(self) -> None:
        self._global_log("[dim]Refreshing node list…[/dim]")
        threading.Thread(target=self._load_nodes_bg, daemon=True).start()

    def _load_nodes_bg(self) -> None:
        """Load K8s nodes then fetch all OpenStack summaries in a single pass."""
        # Phase 1: K8s node list (fast) — populate the table immediately
        try:
            nodes = k8s_ops.get_nodes()
        except Exception as exc:
            self.call_from_thread(
                self._global_log,
                f"[bold red]Error loading K8s nodes:[/bold red] {exc}",
            )
            return

        self.call_from_thread(self._populate_node_table, nodes)

        # Phase 2: three OpenStack API calls cover every hypervisor at once
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

        self.call_from_thread(self._apply_all_summaries, nodes, summaries)

    def _apply_all_summaries(self, nodes: list[dict], summaries: dict[str, dict]) -> None:
        """Distribute bulk OpenStack summary data to each node's state and table row."""
        for nd in nodes:
            name     = nd["name"]
            hostname = nd.get("hostname", name)
            self._apply_node_summary(name, summaries.get(hostname, {}))

    def _populate_node_table(self, nodes: list[dict]) -> None:
        table = self.query_one("#node-table", DataTable)
        table.clear()

        for nd in nodes:
            name:     str = nd["name"]
            hostname: str = nd.get("hostname", name)

            if name not in self.node_states:
                self.node_states[name] = NodeState(
                    k8s_name=name, hypervisor=hostname
                )

            state = self.node_states[name]
            table.add_row(
                name,
                self._nova_svc_text(state),
                self._phase_text(state, nd),
                self._count_text(state.amphora_count),
                self._count_text(state.vm_count),
                key=name,
            )

        if self.selected_node:
            self._refresh_workflow()

    def _apply_node_summary(self, node_name: str, summary: dict) -> None:
        """Update a node's state and table row with fresh OpenStack summary data."""
        state = self.node_states.get(node_name)
        if not state:
            return

        # Only overwrite counts if we're not mid-evacuation (worker owns them then)
        if state.phase == NodePhase.IDLE:
            state.compute_status = summary.get("compute_status")
            state.amphora_count  = summary.get("amphora_count")
            state.vm_count       = summary.get("vm_count")

        table = self.query_one("#node-table", DataTable)
        try:
            table.update_cell(node_name, _COL_NOVA, self._nova_svc_text(state))
            table.update_cell(node_name, _COL_AMP,  self._count_text(state.amphora_count))
            table.update_cell(node_name, _COL_VMS,  self._count_text(state.vm_count))
        except Exception:
            pass  # row may not exist during concurrent repopulation

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

    def _phase_text(self, state: NodeState, nd: dict) -> Text:
        if state.phase != NodePhase.IDLE:
            color = PHASE_COLOR[state.phase]
            label = PHASE_LABEL[state.phase]
            return Text.from_markup(f"[{color}]{label}[/{color}]")
        if nd.get("cordoned"):
            return Text.from_markup("[dim]CORDONED[/dim]")
        if not nd.get("ready", True):
            return Text.from_markup("[red]NOT READY[/red]")
        return Text.from_markup("[white]IDLE[/white]")

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

        table = self.query_one("#node-table", DataTable)
        try:
            table.update_cell(node_name, _COL_NOVA,  self._nova_svc_text(state))
            table.update_cell(node_name, _COL_PHASE, self._phase_text(state, {}))
            table.update_cell(node_name, _COL_AMP,   self._count_text(state.amphora_count))
            table.update_cell(node_name, _COL_VMS,   self._count_text(state.vm_count))
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

        nova_svc = {
            "up":       "[green]enabled[/green]",
            "disabled": "[yellow]disabled[/yellow]",
            "down":     "[bold red]DOWN[/bold red]",
        }.get(state.compute_status or "", "[dim]unknown[/dim]")

        lines += [
            f"[bold]{state.k8s_name}[/bold]   "
            f"[{phase_color}][ {phase_label} ][/{phase_color}]",
            f"[dim]Hypervisor: {state.hypervisor}   "
            f"Nova compute: {nova_svc}[/dim]",
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
