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
    args = parser.parse_args()

    from .app import DrainoApp

    DrainoApp(cloud=args.cloud, context=args.context, audit_log=args.audit_log).run()


if __name__ == "__main__":
    main()
