FROM mcr.microsoft.com/devcontainers/python:1-3.10-jammy

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ansible \
        build-essential \
        iperf3 \
        iproute2 \
        mininet \
        openvswitch-switch \
        python3-dev \
        tcpdump \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*