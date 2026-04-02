"""Draino — main Textual TUI application."""
from __future__ import annotations

import threading
from typing import Optional

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, RichLog, Static

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

# Column keys — compute table
_COL_NODE   = "col_node"
_COL_NOVA   = "col_nova"
_COL_PHASE  = "col_phase"
_COL_AMP    = "col_amp"
_COL_VMS    = "col_vms"
_COL_UPTIME = "col_uptime"
_COL_KERNEL = "col_kernel"

# Column keys — other table (scoped to that table; same string names are fine)
_COL_OTHER_NODE   = "col_node"
_COL_OTHER_STATUS = "col_status"
_COL_OTHER_UPTIME = "col_uptime"
_COL_OTHER_KERNEL = "col_kernel"


class ConfirmRebootScreen(ModalScreen):
    """Modal that requires the user to type YES before rebooting a node."""

    CSS = """
    ConfirmRebootScreen {
        align: center middle;
    }
    #reboot-dialog {
        padding: 1 2;
        background: $surface;
        border: thick $error;
        width: 54;
        height: auto;
    }
    #reboot-dialog Label {
        margin: 0 0 1 0;
    }
    #reboot-input {
        width: 100%;
    }
    """

    def __init__(self, node_name: str) -> None:
        super().__init__()
        self._node_name = node_name

    def compose(self) -> ComposeResult:
        with Vertical(id="reboot-dialog"):
            yield Label(f"[bold red]⚠  REBOOT NODE[/bold red]")
            yield Label(f"[bold]{self._node_name}[/bold]")
            yield Label("")
            yield Label(
                "This will SSH into the node and issue [bold]sudo reboot[/bold].\n"
                "Downtime will be measured until K8s reports the node Ready again."
            )
            yield Label("")
            yield Label("Type [bold]YES[/bold] and press Enter to confirm:")
            yield Input(placeholder="YES", id="reboot-input")

    def on_mount(self) -> None:
        self.query_one("#reboot-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() == "YES")

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(False)


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
        width: 96;
        min-width: 70;
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
        ("s",       "start",   "Start Evacuation"),
        ("u",       "drain_or_undrain", "Drain / Undrain"),
        ("p",       "pods",    "Pods"),
        ("r",       "refresh", "Refresh Nodes"),
        ("ctrl+r",  "reboot",  "Reboot Node"),
        ("q",       "quit",    "Quit"),
        ("f5",      "refresh", "Refresh"),
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
        self._show_pods: bool = False

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
            yield Button("↺  Undrain Node",      id="btn-undrain", variant="warning")
            yield Button("⬡  Pods",              id="btn-pods",    variant="default")
            yield Button("⟳  Refresh Nodes",    id="btn-refresh", variant="default")
            yield Button("⏻  Reboot Node",       id="btn-reboot",  variant="error")
        yield Footer()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        openstack_ops.configure(cloud=self.cloud)
        k8s_ops.configure(context=self.context)

        ct = self.query_one("#compute-table", DataTable)
        ct.add_column("Node",     key=_COL_NODE,   width=22)
        ct.add_column("Nova Svc", key=_COL_NOVA,   width=10)
        ct.add_column("Phase",    key=_COL_PHASE,  width=10)
        ct.add_column("AMP",      key=_COL_AMP,    width=5)
        ct.add_column("VMs",      key=_COL_VMS,    width=5)
        ct.add_column("Uptime",   key=_COL_UPTIME, width=9)
        ct.add_column("Kernel",   key=_COL_KERNEL, width=22)

        ot = self.query_one("#other-table", DataTable)
        ot.add_column("Node",   key=_COL_OTHER_NODE,   width=28)
        ot.add_column("Status", key=_COL_OTHER_STATUS, width=12)
        ot.add_column("Uptime", key=_COL_OTHER_UPTIME, width=9)
        ot.add_column("Kernel", key=_COL_OTHER_KERNEL, width=22)

        self.action_refresh()
        self.set_interval(15, self._auto_refresh)
        self.set_interval(5,  self._auto_refresh_pods)
        self.set_interval(1,  self._tick_rebooting)

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
            state.kernel_version = nd.get("kernel_version")
            ready_since = nd.get("ready_since")
            if ready_since is not None:
                state.uptime = self._format_uptime(ready_since)

            ct.add_row(
                name,
                self._nova_svc_text(state),
                self._phase_text(state),
                self._count_text(state.amphora_count),
                self._count_text(state.vm_count),
                self._uptime_text(state),
                self._kernel_text(state),
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

        # 3. If the selected node just became a known compute node, load preflight
        if self.selected_node:
            self._trigger_preflight(self.selected_node)

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
                    self._uptime_text(state),
                    self._kernel_text(state),
                    key=name,
                )
            else:
                ot.add_row(
                    name,
                    self._k8s_status_text(state),
                    self._uptime_text(state),
                    self._kernel_text(state),
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

    def _uptime_text(self, state: NodeState) -> Text:
        if state.uptime is None:
            return Text.from_markup("[dim]…[/dim]")
        return Text(state.uptime, style="cyan")

    def _kernel_text(self, state: NodeState) -> Text:
        if state.kernel_version is None:
            return Text.from_markup("[dim]…[/dim]")
        return Text(state.kernel_version, style="dim")

    @staticmethod
    def _format_uptime(since) -> str:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
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

    # ── Event handlers ────────────────────────────────────────────────────────

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key and event.row_key.value is not None:
            self.selected_node = str(event.row_key.value)
            self._update_buttons()
            if self._show_pods:
                self._start_pods_fetch(self.selected_node)
            else:
                self._refresh_workflow()
                self._trigger_preflight(self.selected_node)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-start":
            self.action_start()
        elif event.button.id == "btn-undrain":
            self.action_drain_or_undrain()
        elif event.button.id == "btn-pods":
            self.action_pods()
        elif event.button.id == "btn-refresh":
            self.action_refresh()
        elif event.button.id == "btn-reboot":
            self.action_reboot()

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

        state.phase               = NodePhase.RUNNING
        state.instances           = []
        state.log_buffer          = []
        state.preflight_instances = []
        state.preflight_loading   = False
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

    def action_drain_or_undrain(self) -> None:
        """Route to drain_quick or undrain depending on current node state."""
        state = self.node_states.get(self.selected_node) if self.selected_node else None
        node_is_drained = state and (
            state.k8s_cordoned or state.compute_status == "disabled"
        )
        if node_is_drained:
            self.action_undrain()
        else:
            self.action_drain_quick()

    def action_drain_quick(self) -> None:
        """Cordon node and disable Nova compute service (no VM migration)."""
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

        state.phase = NodePhase.RUNNING
        state.init_quick_drain_steps(state.is_compute)
        self._on_state_changed(self.selected_node)

        node_name = self.selected_node

        def update_cb() -> None:
            self.call_from_thread(self._on_state_changed, node_name)

        def log(msg: str) -> None:
            self.call_from_thread(
                self._global_log,
                f"[dim cyan]{node_name}[/dim cyan]  {msg}",
            )

        def step_set(key: str, status: StepStatus, detail: str = "") -> None:
            step = state.get_step(key)
            if step:
                step.status = status
                if detail:
                    step.detail = detail
            update_cb()

        def _run() -> None:
            step_set("cordon", StepStatus.RUNNING)
            try:
                k8s_ops.cordon_node(state.k8s_name, log)
                state.k8s_cordoned = True
                step_set("cordon", StepStatus.SUCCESS)
            except Exception as exc:
                step_set("cordon", StepStatus.FAILED, str(exc))
                state.phase = NodePhase.ERROR
                update_cb()
                return

            if state.is_compute:
                step_set("disable_nova", StepStatus.RUNNING)
                try:
                    openstack_ops.disable_compute_service(state.hypervisor, log)
                    state.compute_status = "disabled"
                    step_set("disable_nova", StepStatus.SUCCESS)
                except Exception as exc:
                    step_set("disable_nova", StepStatus.FAILED, str(exc))
                    state.phase = NodePhase.ERROR
                    update_cb()
                    return

            step_set("drain_k8s", StepStatus.RUNNING)
            try:
                k8s_ops.drain_node(state.k8s_name, log)
                step_set("drain_k8s", StepStatus.SUCCESS)
            except Exception as exc:
                step_set("drain_k8s", StepStatus.FAILED, str(exc))
                state.phase = NodePhase.ERROR
                update_cb()
                return

            state.phase = NodePhase.IDLE
            self.call_from_thread(self._on_state_changed, node_name)
            self.call_from_thread(
                self._global_log,
                f"[bold green]✓ '{node_name}' drained — cordoned, nova disabled, pods evicted.[/bold green]",
            )

        self._global_log(f"[bold]Draining [cyan]{node_name}[/cyan]…[/bold]")
        self._refresh_workflow()
        threading.Thread(target=_run, daemon=True).start()

    def action_undrain(self) -> None:
        if not self.selected_node:
            self._global_log("[yellow]No node selected.[/yellow]")
            return

        state = self.node_states.get(self.selected_node)
        if not state:
            return

        if state.phase == NodePhase.RUNNING:
            self._global_log(
                f"[yellow]Evacuation in progress for "
                f"[bold]{self.selected_node}[/bold] — cannot undrain now.[/yellow]"
            )
            return

        state.phase = NodePhase.UNDRAINING
        state.init_undrain_steps(state.is_compute)
        self._on_state_changed(self.selected_node)

        node_name = self.selected_node

        def update_cb() -> None:
            self.call_from_thread(self._on_state_changed, node_name)

        def log(msg: str) -> None:
            self.call_from_thread(
                self._global_log,
                f"[dim cyan]{node_name}[/dim cyan]  {msg}",
            )

        def step_set(key: str, status: StepStatus, detail: str = "") -> None:
            step = state.get_step(key)
            if step:
                step.status = status
                if detail:
                    step.detail = detail
            update_cb()

        def _run() -> None:
            if state.is_compute:
                step_set("enable_nova", StepStatus.RUNNING)
                try:
                    openstack_ops.enable_compute_service(state.hypervisor, log)
                    state.compute_status = "up"
                    step_set("enable_nova", StepStatus.SUCCESS)
                except Exception as exc:
                    step_set("enable_nova", StepStatus.FAILED, str(exc))
                    state.phase = NodePhase.ERROR
                    update_cb()
                    return

            step_set("uncordon", StepStatus.RUNNING)
            try:
                k8s_ops.uncordon_node(state.k8s_name, log)
                state.k8s_cordoned = False
                step_set("uncordon", StepStatus.SUCCESS)
            except Exception as exc:
                step_set("uncordon", StepStatus.FAILED, str(exc))
                state.phase = NodePhase.ERROR
                update_cb()
                return

            state.phase = NodePhase.IDLE
            self.call_from_thread(self._on_state_changed, node_name)
            self.call_from_thread(
                self._global_log,
                f"[bold green]✓ '{node_name}' undrained — "
                f"nova enabled and node uncordoned.[/bold green]",
            )

        self._global_log(f"[bold]Undraining [cyan]{node_name}[/cyan]…[/bold]")
        self._refresh_workflow()
        threading.Thread(target=_run, daemon=True).start()

    def action_pods(self) -> None:
        self._show_pods = not self._show_pods
        btn = self.query_one("#btn-pods", Button)
        if self._show_pods:
            btn.label = "✕  Close Pods"
            if self.selected_node:
                self._start_pods_fetch(self.selected_node)
            else:
                self.query_one("#workflow-content", Static).update(
                    "[dim]Select a node to view its pods.[/dim]"
                )
        else:
            btn.label = "⬡  Pods"
            self._refresh_workflow()

    def action_reboot(self) -> None:
        if not self.selected_node:
            self._global_log("[yellow]No node selected.[/yellow]")
            return

        state = self.node_states.get(self.selected_node)
        if not state:
            return

        if state.phase == NodePhase.REBOOTING:
            self._global_log(
                f"[yellow]Reboot already in progress for "
                f"[bold]{self.selected_node}[/bold][/yellow]"
            )
            return

        if state.phase == NodePhase.RUNNING:
            self._global_log(
                "[yellow]Evacuation in progress — complete it before rebooting.[/yellow]"
            )
            return

        node_name = self.selected_node

        def _on_confirmed(confirmed: bool) -> None:
            if not confirmed:
                self._global_log("[dim]Reboot cancelled.[/dim]")
                return
            self._do_reboot(node_name)

        self.push_screen(ConfirmRebootScreen(node_name), _on_confirmed)

    def _do_reboot(self, node_name: str) -> None:
        state = self.node_states.get(node_name)
        if not state:
            return

        def update_cb() -> None:
            self.call_from_thread(self._on_state_changed, node_name)

        def log_cb(msg: str) -> None:
            self.call_from_thread(
                self._global_log,
                f"[dim magenta]{node_name}[/dim magenta]  {msg}",
            )

        self._global_log(
            f"[bold magenta]Rebooting [cyan]{node_name}[/cyan]…[/bold magenta]"
        )
        threading.Thread(
            target=worker.run_reboot,
            args=(state, update_cb, log_cb),
            daemon=True,
        ).start()
        self._refresh_workflow()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _update_buttons(self) -> None:
        """Update action bar button labels and state to reflect the selected node."""
        state = self.node_states.get(self.selected_node) if self.selected_node else None
        phase = state.phase if state else None

        start_btn   = self.query_one("#btn-start",   Button)
        undrain_btn = self.query_one("#btn-undrain", Button)
        reboot_btn  = self.query_one("#btn-reboot",  Button)

        # ── Start / Evacuation button ─────────────────────────────────────
        if phase == NodePhase.RUNNING:
            start_btn.label    = "▶  Evacuating…"
            start_btn.disabled = True
        else:
            start_btn.label    = "▶  Start Evacuation"
            start_btn.disabled = False

        # ── Drain / Undrain button ────────────────────────────────────────
        # Show "Undrain Node" when the node is cordoned or nova-disabled,
        # otherwise show "Drain Node" (quick prep: cordon + disable nova).
        node_is_drained = state and (
            state.k8s_cordoned or state.compute_status == "disabled"
        )
        if phase == NodePhase.UNDRAINING:
            undrain_btn.label    = "↺  Undraining…"
            undrain_btn.disabled = True
        elif phase in (NodePhase.RUNNING, NodePhase.REBOOTING):
            undrain_btn.label    = "↺  Undrain Node" if node_is_drained else "▽  Drain Node"
            undrain_btn.disabled = True
        elif node_is_drained:
            undrain_btn.label    = "↺  Undrain Node"
            undrain_btn.disabled = False
        else:
            undrain_btn.label    = "▽  Drain Node"
            undrain_btn.disabled = False

        # ── Reboot button ─────────────────────────────────────────────────
        if phase == NodePhase.REBOOTING:
            reboot_btn.label    = "⏻  Rebooting…"
            reboot_btn.disabled = True
        elif phase in (NodePhase.RUNNING, NodePhase.UNDRAINING):
            reboot_btn.label    = "⏻  Reboot Node"
            reboot_btn.disabled = True
        else:
            reboot_btn.label    = "⏻  Reboot Node"
            reboot_btn.disabled = False

    def _tick_rebooting(self) -> None:
        """Refresh the workflow view every second while any node is rebooting."""
        if (
            self.selected_node
            and self.node_states.get(self.selected_node, None) is not None
            and self.node_states[self.selected_node].phase == NodePhase.REBOOTING
        ):
            self._refresh_workflow()

    def _auto_refresh(self) -> None:
        self.action_refresh()

    def _auto_refresh_pods(self) -> None:
        if self._show_pods and self.selected_node:
            threading.Thread(
                target=self._load_pods_bg,
                args=(self.selected_node,),
                daemon=True,
            ).start()

    def _start_pods_fetch(self, node_name: str) -> None:
        self.query_one("#workflow-content", Static).update(
            f"[bold]{node_name}[/bold]   [cyan][ PODS ][/cyan]\n\n[dim]Fetching pods…[/dim]"
        )
        threading.Thread(
            target=self._load_pods_bg, args=(node_name,), daemon=True
        ).start()

    def _load_pods_bg(self, node_name: str) -> None:
        try:
            pods = k8s_ops.get_pods_on_node(node_name)
        except Exception as exc:
            self.call_from_thread(
                self._global_log,
                f"[dim red]Pod fetch failed for {node_name}: {exc}[/dim red]",
            )
            return
        self.call_from_thread(self._update_pods_view, node_name, pods)

    def _update_pods_view(self, node_name: str, pods: list[dict]) -> None:
        if not self._show_pods or self.selected_node != node_name:
            return
        self.query_one("#workflow-content", Static).update(
            self._render_pods(node_name, pods)
        )

    def _render_pods(self, node_name: str, pods: list[dict]) -> str:
        lines: list[str] = [
            f"[bold]{node_name}[/bold]   [cyan][ PODS ][/cyan]",
            "",
        ]
        if not pods:
            lines.append("[dim]No pods scheduled on this node.[/dim]")
            return "\n".join(lines)

        NS, NM = 18, 36
        lines.append(
            f"[dim]  {'Namespace':<{NS}} {'Name':<{NM}} "
            f"{'Ready':<6} {'Status':<12} {'Restarts':<10} Age[/dim]"
        )
        lines.append(f"  [dim]{'─' * (NS + NM + 6 + 12 + 10 + 8)}[/dim]")

        for pod in sorted(pods, key=lambda p: (p["namespace"], p["name"])):
            ns    = pod["namespace"]
            nm    = pod["name"]
            if len(ns) > NS:
                ns = ns[: NS - 2] + ".."
            if len(nm) > NM:
                nm = nm[: NM - 2] + ".."
            ready   = f"{pod['ready_count']}/{pod['total_count']}"
            phase   = pod["phase"]
            age     = self._format_uptime(pod["created_at"]) if pod["created_at"] else "?"
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

        lines += ["", f"[dim]{len(pods)} pod(s)   auto-refreshing every 5s[/dim]"]
        return "\n".join(lines)

    def _trigger_preflight(self, node_name: str) -> None:
        """Start a background preflight fetch for *node_name* if conditions allow."""
        state = self.node_states.get(node_name)
        if not state or not state.is_compute or state.phase != NodePhase.IDLE:
            return
        if state.preflight_loading:
            return
        state.preflight_loading   = True
        state.preflight_instances = []
        self._refresh_workflow()
        threading.Thread(
            target=self._load_preflight_bg, args=(node_name,), daemon=True
        ).start()

    def _load_preflight_bg(self, node_name: str) -> None:
        state = self.node_states.get(node_name)
        if not state:
            return
        try:
            state.preflight_instances = openstack_ops.get_instances_preflight(
                state.hypervisor
            )
        except Exception as exc:
            state.preflight_instances = []
            self.call_from_thread(
                self._global_log,
                f"[dim red]Preflight fetch failed for {node_name}: {exc}[/dim red]",
            )
        finally:
            state.preflight_loading = False
        self.call_from_thread(self._refresh_workflow)

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
            self._update_buttons()
            self._refresh_workflow()

    def _refresh_workflow(self) -> None:
        if self._show_pods:
            return
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
            if state.preflight_loading:
                lines.append("[dim]Fetching instances…[/dim]")
            else:
                vms  = [i for i in state.preflight_instances if not i["is_amphora"]]
                amps = [i for i in state.preflight_instances if     i["is_amphora"]]
                if vms:
                    W = 34
                    lines.append("[bold underline]Instances[/bold underline]")
                    lines.append("")
                    lines.append(
                        f"[dim]  {'Name':<{W}} {'Status':<12} Storage[/dim]"
                    )
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
                        lines.append(
                            f"  [dim]+ {len(amps)} Amphora instance(s)[/dim]"
                        )
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

        # ── Live downtime counter (during reboot) ──
        if state.phase == NodePhase.REBOOTING and state.reboot_start is not None:
            import time as _time
            elapsed = int(_time.time() - state.reboot_start)
            lines += [
                f"[bold magenta]⏱  Downtime: {elapsed}s[/bold magenta]",
                "",
            ]

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
