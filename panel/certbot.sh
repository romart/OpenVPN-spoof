#!/bin/bash
set -euo pipefail
exec /opt/openvpn-panel/certbot-venv/bin/certbot \
    --config-dir /var/lib/openvpn-panel/letsencrypt \
    --work-dir /var/lib/openvpn-panel/certbot-work \
    --logs-dir /var/lib/openvpn-panel/certbot-logs "$@"
