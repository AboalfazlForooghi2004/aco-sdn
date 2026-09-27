"""Six-switch multipath Mininet topology for the MVP lab."""


def run() -> None:
    try:
        from mininet.cli import CLI
        from mininet.link import TCLink
        from mininet.net import Mininet
        from mininet.node import OVSSwitch, RemoteController
    except ImportError as exc:
        raise SystemExit(
            "Mininet is required; run this module on the Ubuntu lab host"
        ) from exc

    net = Mininet(
        controller=None,
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=True,
    )
    net.addController(
        "c0",
        controller=RemoteController,
        ip="127.0.0.1",
        port=6653,
    )
    h1, h2 = net.addHost("h1"), net.addHost("h2")
    switches = {
        f"s{i}": net.addSwitch(
            f"s{i}", protocols="OpenFlow13"
        )
        for i in range(1, 7)
    }
    net.addLink(h1, switches["s1"])
    net.addLink(switches["s1"], switches["s2"], bw=100, delay="2ms")
    net.addLink(switches["s2"], switches["s4"], bw=100, delay="2ms")
    net.addLink(switches["s4"], switches["s6"], bw=100, delay="2ms")
    net.addLink(switches["s1"], switches["s3"], bw=100, delay="2ms")
    net.addLink(switches["s3"], switches["s5"], bw=100, delay="2ms")
    net.addLink(switches["s5"], switches["s6"], bw=100, delay="2ms")
    net.addLink(h2, switches["s6"])
    net.start()
    CLI(net)
    net.stop()


if __name__ == "__main__":
    run()