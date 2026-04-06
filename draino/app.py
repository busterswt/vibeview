"""Draino — main Textual TUI application."""
from __future__ import annotations

import threading
from typing import Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widgets import Button, DataTable, Footer, Header, RichLog, Static

from . import render, worker
from .audit import AuditLogger
from .models import NodePhase, NodeState
from .operations import k8s_ops, openstack_ops
from .reboot import is_ready_for_reboot
from .screens import ConfirmRebootScreen

# ── Column keys — compute table ───────────────────────────────────────────────
_COL_NODE   = "col_node"
_COL_NOVA   = "col_nova"
_COL_PHASE  = "col_phase"
_COL_AMP    = "col_amp"
_COL_VMS    = "col_vms"
_COL_UPTIME = "col_uptime"
_COL_KERNEL = "col_kernel"

# ── Column keys — other table ─────────────────────────────────────────────────
_COL_OTHER_NODE   = "col_node"
_COL_OTHER_ROLE   = "col_role"
_COL_OTHER_STATUS = "col_status"
_COL_OTHER_UPTIME = "col_uptime"
_COL_OTHER_KERNEL = "col_kernel"


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
        ("s",       "start",              "Start Evacuation"),
        ("u",       "drain_or_undrain",   "Drain / Undrain"),
        ("p",       "pods",               "Pods"),
        ("h",       "toggle_succeeded",   "Show/Hide Succeeded"),
        ("r",       "refresh",            "Refresh Nodes"),
        ("ctrl+r",  "reboot",             "Reboot Node"),
        ("q",       "quit",               "Quit"),
        ("f5",      "refresh",            "Refresh"),
    ]

    def __init__(
        self,
        cloud:     Optional[str] = None,
        context:   Optional[str] = None,
        audit_log: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.cloud   = cloud
        self.context = context
        self.node_states: dict[str, NodeState] = {}
        self.selected_node: Optional[str] = None
        self._last_k8s_nodes: list[dict] = []
        self._show_pods: bool = False
        self._hide_succeeded: bool = True
        self._cached_pods: list[dict] = []
        self._etcd_node_names: set[str] = set()
        self._audit = AuditLogger(path=audit_log)

    # ── Audit helper ──────────────────────────────────────────────────────────

    def _make_audit_cb(self, action: str, node_name: str):
        """Return a (event, detail) -> None callback bound to action+node."""
        def cb(event: str, detail: str = "") -> None:
            self._audit.log(action, node_name, event, detail)
        return cb

    # ── Layout ────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            with Vertical(id="node-panel"):
                with Vertical(id="compute-section"):
                    yield Static("  Compute Nodes", id="compute-section-title")
                    yield DataTable(id="compute-table", cursor_type="row")
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
        self._audit.log("session", "-", "started", f"audit_log={self._audit.path}")

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
        ot.add_column("Role",   key=_COL_OTHER_ROLE,   width=6)
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

        etcd_names = k8s_ops.get_etcd_node_names()
        self.call_from_thread(self._apply_summaries_and_rebuild, nodes, summaries, etcd_names)

    def _populate_initial(self, nodes: list[dict]) -> None:
        """First pass: create NodeState entries and show all nodes in compute-table."""
        self._last_k8s_nodes = nodes
        ct = self.query_one("#compute-table", DataTable)
        ct.clear()
        self.query_one("#other-table", DataTable).clear()

        for nd in nodes:
            name:     str = nd["name"]
            hostname: str = nd.get("hostname", name)

            if name not in self.node_states:
                self.node_states[name] = NodeState(k8s_name=name, hypervisor=hostname)

            state = self.node_states[name]
            state.k8s_ready      = nd.get("ready", True)
            state.k8s_cordoned   = nd.get("cordoned", False)
            state.k8s_taints     = list(nd.get("taints", []))
            state.kernel_version = nd.get("kernel_version")
            ready_since = nd.get("ready_since")
            if ready_since is not None:
                state.uptime = render.format_uptime(ready_since)
            signals = k8s_ops.get_node_host_signals(name, hostname)
            if signals.get("kernel_version"):
                state.kernel_version = signals.get("kernel_version")
            state.latest_kernel_version = signals.get("latest_kernel_version")
            state.reboot_required = bool(signals.get("reboot_required", False))

            ct.add_row(
                render.node_name_text(name, state),
                render.nova_svc_text(state),
                render.phase_text(state),
                render.count_text(state.amphora_count),
                render.count_text(state.vm_count),
                render.uptime_text(state),
                render.kernel_text(state),
                key=name,
            )

        if self.selected_node:
            self._refresh_workflow()

    def _apply_summaries_and_rebuild(
        self,
        nodes:      list[dict],
        summaries:  dict[str, dict],
        etcd_names: set[str] | None = None,
    ) -> None:
        """Apply OpenStack data then split nodes across the two tables."""
        if etcd_names is not None:
            self._etcd_node_names = etcd_names

        for nd in nodes:
            name     = nd["name"]
            hostname = nd.get("hostname", name)
            summary  = summaries.get(hostname, {})
            state    = self.node_states.get(name)
            if not state:
                continue
            state.is_etcd = name in self._etcd_node_names
            if state.phase == NodePhase.IDLE:
                state.is_compute     = summary.get("is_compute", False)
                state.compute_status = summary.get("compute_status")
                state.amphora_count  = summary.get("amphora_count")
                state.vm_count       = summary.get("vm_count")

        self._rebuild_tables()

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

            if state.is_compute:
                ct.add_row(
                    render.node_name_text(name, state),
                    render.nova_svc_text(state),
                    render.phase_text(state),
                    render.count_text(state.amphora_count),
                    render.count_text(state.vm_count),
                    render.uptime_text(state),
                    render.kernel_text(state),
                    key=name,
                )
            else:
                ot.add_row(
                    render.node_name_text(name, state),
                    render.role_text(state),
                    render.k8s_status_text(state),
                    render.uptime_text(state),
                    render.kernel_text(state),
                    key=name,
                )

        if self.selected_node:
            self._refresh_workflow()

    # ── Event handlers ────────────────────────────────────────────────────────

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key and event.row_key.value is not None:
            self.selected_node = str(event.row_key.value)
            self._update_buttons()
            state = self.node_states.get(self.selected_node)
            if state and state.is_etcd:
                threading.Thread(
                    target=self._check_etcd_health_bg, daemon=True
                ).start()
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

        node_name = self.selected_node

        def update_cb() -> None:
            self.call_from_thread(self._on_state_changed, node_name)

        def log_cb(msg: str) -> None:
            self.call_from_thread(
                self._global_log,
                f"[dim cyan]{node_name}[/dim cyan]  {msg}",
            )

        audit_cb = self._make_audit_cb("evacuation", node_name)
        audit_cb("started")
        threading.Thread(
            target=worker.run_workflow,
            args=(state, update_cb, log_cb, audit_cb),
            daemon=True,
        ).start()

        self._global_log(f"[bold]Starting evacuation of [cyan]{node_name}[/cyan]…[/bold]")
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
        """Cordon, disable Nova, and drain pods (no VM migration)."""
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
        audit_cb  = self._make_audit_cb("drain_quick", node_name)
        audit_cb("started")

        def update_cb() -> None:
            self.call_from_thread(self._on_state_changed, node_name)

        def log_cb(msg: str) -> None:
            self.call_from_thread(
                self._global_log,
                f"[dim cyan]{node_name}[/dim cyan]  {msg}",
            )

        self._global_log(f"[bold]Draining [cyan]{node_name}[/cyan]…[/bold]")
        self._refresh_workflow()
        threading.Thread(
            target=worker.run_drain_quick,
            args=(state, update_cb, log_cb, audit_cb),
            daemon=True,
        ).start()

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
        audit_cb  = self._make_audit_cb("undrain", node_name)
        audit_cb("started")

        def update_cb() -> None:
            self.call_from_thread(self._on_state_changed, node_name)

        def log_cb(msg: str) -> None:
            self.call_from_thread(
                self._global_log,
                f"[dim cyan]{node_name}[/dim cyan]  {msg}",
            )

        self._global_log(f"[bold]Undraining [cyan]{node_name}[/cyan]…[/bold]")
        self._refresh_workflow()
        threading.Thread(
            target=worker.run_undrain,
            args=(state, update_cb, log_cb, audit_cb),
            daemon=True,
        ).start()

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
            self._cached_pods = []
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

        if state.phase in (NodePhase.RUNNING, NodePhase.UNDRAINING):
            self._global_log(
                "[yellow]Cannot reboot while an operation is in progress.[/yellow]"
            )
            return

        reboot_ready, detail = is_ready_for_reboot(state)
        if not reboot_ready:
            self._global_log(f"[yellow]{detail}[/yellow]")
            return

        node_name = self.selected_node

        if state.is_etcd:
            self._global_log("[dim]Checking etcd quorum before reboot…[/dim]")
            threading.Thread(
                target=self._etcd_reboot_preflight,
                args=(node_name,),
                daemon=True,
            ).start()
            return

        def _on_confirmed(confirmed: bool) -> None:
            if not confirmed:
                self._global_log("[dim]Reboot cancelled.[/dim]")
                self._audit.log("reboot", node_name, "cancelled")
                return
            self._do_reboot(node_name)

        self.push_screen(ConfirmRebootScreen(node_name), _on_confirmed)

    # ── etcd helpers ──────────────────────────────────────────────────────────

    def _check_etcd_health_bg(self) -> None:
        """SSH-check etcd service on all etcd nodes and refresh the workflow view."""
        for state in list(self.node_states.values()):
            if state.is_etcd:
                state.etcd_healthy = k8s_ops.check_etcd_service(state.k8s_name, state.hypervisor)
        self.call_from_thread(self._refresh_workflow)

    def _etcd_reboot_preflight(self, node_name: str) -> None:
        """Check etcd quorum health via SSH; push confirm dialog or block."""
        etcd_states   = [s for s in self.node_states.values() if s.is_etcd]
        etcd_total    = len(etcd_states)
        quorum_needed = (etcd_total // 2) + 1

        for s in etcd_states:
            s.etcd_healthy = k8s_ops.check_etcd_service(s.k8s_name, s.hypervisor)

        self.call_from_thread(self._refresh_workflow)

        healthy_count = sum(1 for s in etcd_states if s.etcd_healthy is True)
        this_state    = self.node_states.get(node_name)
        this_healthy  = this_state is not None and this_state.etcd_healthy is True
        remaining     = healthy_count - (1 if this_healthy else 0)

        if remaining < quorum_needed:
            detail = (
                f"{healthy_count}/{etcd_total} healthy, "
                f"rebooting would leave {remaining} (quorum={quorum_needed})"
            )
            self._audit.log("reboot", node_name, "blocked", detail)
            self.call_from_thread(
                self._global_log,
                f"[bold red]✗ Reboot blocked — {detail}[/bold red]",
            )
            return

        def _on_confirmed(confirmed: bool) -> None:
            if not confirmed:
                self._global_log("[dim]Reboot cancelled.[/dim]")
                self._audit.log("reboot", node_name, "cancelled")
                return
            self._do_reboot(node_name)

        self.call_from_thread(self.push_screen, ConfirmRebootScreen(node_name), _on_confirmed)

    def _do_reboot(self, node_name: str) -> None:
        state = self.node_states.get(node_name)
        if not state:
            return

        audit_cb = self._make_audit_cb("reboot", node_name)
        audit_cb("started", f"hypervisor={state.hypervisor}")

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
            args=(state, update_cb, log_cb, audit_cb),
            daemon=True,
        ).start()
        self._refresh_workflow()

    # ── UI state helpers ──────────────────────────────────────────────────────

    def _update_buttons(self) -> None:
        """Update action bar button labels and disabled state for the selected node."""
        state = self.node_states.get(self.selected_node) if self.selected_node else None
        phase = state.phase if state else None

        start_btn   = self.query_one("#btn-start",   Button)
        undrain_btn = self.query_one("#btn-undrain", Button)
        reboot_btn  = self.query_one("#btn-reboot",  Button)

        if phase == NodePhase.RUNNING:
            start_btn.label    = "▶  Evacuating…"
            start_btn.disabled = True
        else:
            start_btn.label    = "▶  Start Evacuation"
            start_btn.disabled = False

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

        if phase == NodePhase.REBOOTING:
            reboot_btn.label    = "⏻  Rebooting…"
            reboot_btn.disabled = True
        elif phase in (NodePhase.RUNNING, NodePhase.UNDRAINING):
            reboot_btn.label    = "⏻  Reboot Node"
            reboot_btn.disabled = True
        else:
            reboot_ready = bool(state and is_ready_for_reboot(state)[0])
            reboot_btn.label    = "⏻  Reboot Node"
            reboot_btn.disabled = not reboot_ready

    def _tick_rebooting(self) -> None:
        """Refresh the workflow view every second while any node is rebooting."""
        if (
            self.selected_node
            and self.node_states.get(self.selected_node) is not None
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

    # ── Pods view ─────────────────────────────────────────────────────────────

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
        self._cached_pods = pods
        self.query_one("#workflow-content", Static).update(
            render.render_pods(node_name, pods, hide_succeeded=self._hide_succeeded)
        )

    def action_toggle_succeeded(self) -> None:
        """Toggle visibility of Succeeded pods (H key, only active in pods view)."""
        if not self._show_pods:
            return
        self._hide_succeeded = not self._hide_succeeded
        if self.selected_node and self._cached_pods:
            self.query_one("#workflow-content", Static).update(
                render.render_pods(
                    self.selected_node,
                    self._cached_pods,
                    hide_succeeded=self._hide_succeeded,
                )
            )

    # ── Preflight ─────────────────────────────────────────────────────────────

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

    # ── State change / workflow display ───────────────────────────────────────

    def _on_state_changed(self, node_name: str) -> None:
        """Called on the main thread whenever a worker mutates node state."""
        state = self.node_states.get(node_name)
        if not state:
            return

        if state.is_compute:
            ct = self.query_one("#compute-table", DataTable)
            try:
                ct.update_cell(node_name, _COL_NOVA,  render.nova_svc_text(state))
                ct.update_cell(node_name, _COL_PHASE, render.phase_text(state))
                ct.update_cell(node_name, _COL_AMP,   render.count_text(state.amphora_count))
                ct.update_cell(node_name, _COL_VMS,   render.count_text(state.vm_count))
            except Exception:
                pass
        else:
            ot = self.query_one("#other-table", DataTable)
            try:
                ot.update_cell(node_name, _COL_OTHER_STATUS, render.k8s_status_text(state))
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
        etcd_peers = [s for s in self.node_states.values() if s.is_etcd]
        view.update(render.render_workflow(state, etcd_peers))

    def _global_log(self, msg: str) -> None:
        self.query_one("#log-view", RichLog).write(msg)
