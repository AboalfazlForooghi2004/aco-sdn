#!/usr/bin/env bash
set -euo pipefail

python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install -r requirements-lab.txt

make test
make offline

sudo service openvswitch-switch start || true
python scripts/lab_preflight.py || true