"""Interactive six-switch multipath Mininet topology."""

from topology.lab import build_network


def run() -> None:
    try:
        from mininet.cli import CLI
    except ImportError as exc:
        raise SystemExit(
            "Mininet is required; run this module on Ubuntu"
        ) from exc
    net = build_network()
    try:
        net.start()
        CLI(net)
    finally:
        net.stop()


if __name__ == "__main__":
    run()