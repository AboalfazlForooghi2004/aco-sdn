from __future__ import annotations


def build_network(
    controller_ip: str = "127.0.0.1",
    controller_port: int = 6653,
):
    try:
        from mininet.link import TCLink
        from mininet.net import Mininet
        from mininet.node import OVSSwitch, RemoteController
    except ImportError as exc:
        raise RuntimeError(
            "Mininet is required on the Ubuntu lab host"
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
        ip=controller_ip,
        port=controller_port,
    )
    hosts = {
        f"h{i}": net.addHost(
            f"h{i}", ip=f"10.0.0.{i}/24"
        )
        for i in range(1, 5)
    }
    switches = {
        f"s{i}": net.addSwitch(
            f"s{i}", protocols="OpenFlow13"
        )
        for i in range(1, 7)
    }
    net.addLink(hosts["h1"], switches["s1"])
    for left, right in (
        ("s1", "s2"),
        ("s2", "s4"),
        ("s4", "s6"),
        ("s1", "s3"),
        ("s3", "s5"),
        ("s5", "s6"),
    ):
        net.addLink(
            switches[left],
            switches[right],
            bw=100,
            delay="2ms",
        )
    net.addLink(hosts["h2"], switches["s6"])
    net.addLink(hosts["h3"], switches["s2"])
    net.addLink(hosts["h4"], switches["s4"])
    return net


def switch_link(net, left: str, right: str):
    links = net.linksBetween(net[left], net[right])
    if len(links) != 1:
        raise RuntimeError(
            f"expected one link between {left} and {right}"
        )
    return links[0]