"""Entry point: parse CLI arguments and launch draino."""
from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="draino",
        description="Drain OpenStack hypervisors and K8s nodes before a reboot.",
    )
    parser.add_argument(
        "--cloud",
        metavar="NAME",
        default=None,
        help="OpenStack cloud name from clouds.yaml (default: OS_CLOUD env var)",
    )
    parser.add_argument(
        "--context",
        metavar="NAME",
        default=None,
        help="Kubernetes context name from kubeconfig (default: current context)",
    )
    parser.add_argument(
        "--audit-log",
        metavar="PATH",
        default=None,
        help="Path for the compliance audit log (default: ~/.draino/audit.log)",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Launch the web UI instead of the TUI (requires draino[web])",
    )
    parser.add_argument(
        "--host",
        metavar="HOST",
        default="0.0.0.0",
        help="Bind address for the web server (default: 0.0.0.0, --web only)",
    )
    parser.add_argument(
        "--port",
        metavar="PORT",
        type=int,
        default=8000,
        help="Port for the web server (default: 8000, --web only)",
    )
    args = parser.parse_args()

    if args.web:
        try:
            from .web.server import run as web_run
        except ImportError:
            print("Web UI requires extra dependencies: pip install 'draino[web]'")
            raise SystemExit(1)
        web_run(
            cloud=args.cloud,
            context=args.context,
            audit_log=args.audit_log,
            host=args.host,
            port=args.port,
        )
        return

    from .app import DrainoApp

    DrainoApp(cloud=args.cloud, context=args.context, audit_log=args.audit_log).run()


if __name__ == "__main__":
    main()
