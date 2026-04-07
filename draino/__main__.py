"""Entry point: parse CLI arguments and launch draino."""
from __future__ import annotations

import argparse

from .logging_utils import configure_stdout_logging


def main() -> None:
    configure_stdout_logging()
    parser = argparse.ArgumentParser(
        prog="draino",
        description="Web UI for draining OpenStack hypervisors and Kubernetes nodes before a reboot.",
    )
    mode = parser.add_mutually_exclusive_group()
    parser.add_argument(
        "--audit-log",
        metavar="PATH",
        default=None,
        help="Path for the compliance audit log (default: ~/.draino/audit.log)",
    )
    mode.add_argument(
        "--web",
        action="store_true",
        help="Launch the web UI (default mode)",
    )
    mode.add_argument(
        "--node-agent",
        action="store_true",
        help="Launch the node-local reboot agent",
    )
    parser.add_argument(
        "--host",
        metavar="HOST",
        default="0.0.0.0",
        help="Bind address for the web server or node agent (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        metavar="PORT",
        type=int,
        default=8000,
        help="Port for the web server or node agent (default: 8000)",
    )
    args = parser.parse_args()

    if args.node_agent:
        from .node_agent import run as node_agent_run
        node_agent_run(host=args.host, port=args.port)
        return

    from .web.server import run as web_run

    web_run(
        audit_log=args.audit_log,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
