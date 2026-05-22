#!/bin/bash
# vpn_keeper.sh — Dynamic routing daemon to keep VPN rules active and override netd/gluetun resets
#
# Runs in a continuous loop to guarantee high availability of local ADB/API and VPN routes.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROUTING_SCRIPT="${SCRIPT_DIR}/fix_vpn_routing.sh"

echo "=== Starting VPN Routing Keeper Daemon ==="
echo "Monitoring script: ${ROUTING_SCRIPT}"

while true; do
    # Run the routing fix script and suppress stdout to avoid log bloat
    if ! "${ROUTING_SCRIPT}" >/dev/null 2>&1; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') - WARNING: routing fix script execution returned error status" >&2
    fi
    sleep 3
done
