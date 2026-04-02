"""Modal screens used by the Draino TUI."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label


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
        width: 72;
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
            yield Label("[bold red]⚠  REBOOT NODE[/bold red]")
            yield Label(f"[bold]{self._node_name}[/bold]")
            yield Label("")
            yield Label("This will SSH into the node and issue [bold]sudo reboot[/bold].")
            yield Label("Downtime will be measured until K8s reports the node Ready again.")
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
