from __future__ import annotations

import os
import platform
import shutil
import sys


REQUIRED_COMMANDS = (
    "mn",
    "ovs-vsctl",
    "ovs-ofctl",
    "ip",
    "tc",
    "iperf3",
    "ryu-manager",
)


def main() -> int:
    missing = [
        command
        for command in REQUIRED_COMMANDS
        if shutil.which(command) is None
    ]
    print(f"system: {platform.platform()}")
    print(f"python: {sys.version.split()[0]}")
    print(f"effective_uid: {os.geteuid()}")
    if missing:
        print("missing commands: " + ", ".join(missing))
    if os.geteuid() != 0:
        print("live runner must be invoked with sudo/root")
    if missing or os.geteuid() != 0:
        return 1
    print("lab preflight passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())