#!/bin/bash
set -euo pipefail
/opt/openvpn-panel/compose.sh exec -T nginx nginx -t
/opt/openvpn-panel/compose.sh exec -T nginx nginx -s reload
