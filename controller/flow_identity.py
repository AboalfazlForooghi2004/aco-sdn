from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class FlowSelector:
    """Normalized L2/L3/L4 flow identity with reverse direction."""

    source_mac: str
    destination_mac: str
    eth_type: int | None = None
    ipv4_source: str | None = None
    ipv4_destination: str | None = None
    ip_protocol: int | None = None
    source_port: int | None = None
    destination_port: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_mac", self.source_mac.lower()
        )
        object.__setattr__(
            self,
            "destination_mac",
            self.destination_mac.lower(),
        )

    @property
    def key(self) -> tuple:
        left = (
            self.source_mac,
            self.ipv4_source,
            self.source_port,
        )
        right = (
            self.destination_mac,
            self.ipv4_destination,
            self.destination_port,
        )
        endpoints = tuple(
            sorted(
                (left, right),
                key=lambda item: tuple(
                    "" if value is None else str(value)
                    for value in item
                ),
            )
        )
        return (
            "flow",
            self.eth_type,
            self.ip_protocol,
            endpoints,
        )

    def reverse(self) -> "FlowSelector":
        return FlowSelector(
            source_mac=self.destination_mac,
            destination_mac=self.source_mac,
            eth_type=self.eth_type,
            ipv4_source=self.ipv4_destination,
            ipv4_destination=self.ipv4_source,
            ip_protocol=self.ip_protocol,
            source_port=self.destination_port,
            destination_port=self.source_port,
        )

    def openflow_match(self) -> dict[str, object]:
        values: dict[str, object] = {
            "eth_src": self.source_mac,
            "eth_dst": self.destination_mac,
        }
        if self.eth_type is not None:
            values["eth_type"] = self.eth_type
        if (
            self.ipv4_source is not None
            and self.ipv4_destination is not None
        ):
            values["ipv4_src"] = self.ipv4_source
            values["ipv4_dst"] = self.ipv4_destination
        if self.ip_protocol is not None:
            values["ip_proto"] = self.ip_protocol
        if (
            self.source_port is not None
            and self.destination_port is not None
        ):
            prefix = (
                "tcp"
                if self.ip_protocol == 6
                else "udp"
                if self.ip_protocol == 17
                else None
            )
            if prefix is not None:
                values[f"{prefix}_src"] = self.source_port
                values[f"{prefix}_dst"] = self.destination_port
        return values

    def matches(self, match: Mapping[str, object]) -> bool:
        return all(
            str(match.get(name)).lower()
            == str(value).lower()
            for name, value in self.openflow_match().items()
        )

    @classmethod
    def from_match(
        cls, match: Mapping[str, object]
    ) -> "FlowSelector | None":
        source_mac = match.get("eth_src")
        destination_mac = match.get("eth_dst")
        if source_mac is None or destination_mac is None:
            return None
        protocol_value = match.get("ip_proto")
        protocol = (
            int(protocol_value)
            if protocol_value is not None
            else None
        )
        prefix = (
            "tcp"
            if protocol == 6
            else "udp"
            if protocol == 17
            else None
        )
        return cls(
            source_mac=str(source_mac),
            destination_mac=str(destination_mac),
            eth_type=(
                int(match["eth_type"])
                if match.get("eth_type") is not None
                else None
            ),
            ipv4_source=(
                str(match["ipv4_src"])
                if match.get("ipv4_src") is not None
                else None
            ),
            ipv4_destination=(
                str(match["ipv4_dst"])
                if match.get("ipv4_dst") is not None
                else None
            ),
            ip_protocol=protocol,
            source_port=(
                int(match[f"{prefix}_src"])
                if prefix
                and match.get(f"{prefix}_src") is not None
                else None
            ),
            destination_port=(
                int(match[f"{prefix}_dst"])
                if prefix
                and match.get(f"{prefix}_dst") is not None
                else None
            ),
        )