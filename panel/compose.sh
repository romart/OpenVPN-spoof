#!/bin/bash
set -euo pipefail
cd /opt/openvpn-panel
if docker compose version >/dev/null 2>&1; then
    exec docker compose -p openvpn-panel -f compose.yaml "$@"
else
    exec docker-compose -p openvpn-panel -f compose.yaml "$@"
fi
