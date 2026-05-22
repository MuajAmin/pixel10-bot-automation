#!/bin/bash
# fix_vpn_routing.sh — Fix VPN routing after ReDroid pollutes Gluetun's network namespace
#
# Problem: ReDroid shares Gluetun's network namespace (network_mode: container:gluetun).
# Android's netd daemon injects ip rules (priority 10000-32000) with a catch-all
# "unreachable" rule at priority 32000 that blocks WireGuard traffic.
#
# Solution: Add a high-priority ip rule (50) that routes all traffic through
# WireGuard's table 51820, with an exception route for the WireGuard endpoint
# so tunnel UDP packets can still reach the server via eth0.

set -euo pipefail

CONTAINER="gluetun"
WG_TABLE="51820"
RULE_PRIO="50"

# Extract WireGuard endpoint from config
WG_CONF="/root/pixel10-bot-automation/infra/wireguard/main.conf"
ENDPOINT_IP=$(grep -oP 'Endpoint\s*=\s*\K[^:]+' "$WG_CONF" || true)
GATEWAY=$(docker exec "$CONTAINER" ip route show dev eth0 2>/dev/null | grep -oP 'via \K[\d.]+' | head -1 || true)

if [ -z "$ENDPOINT_IP" ]; then
    echo "ERROR: Could not extract WireGuard endpoint IP from $WG_CONF"
    exit 1
fi

if [ -z "$GATEWAY" ]; then
    # Fallback: get gateway from default network
    GATEWAY=$(docker exec "$CONTAINER" ip route list table 1002 2>/dev/null | grep -oP 'via \K[\d.]+' | head -1 || true)
fi

if [ -z "$GATEWAY" ]; then
    # Fallback 2: Inspect docker container networks
    GATEWAY=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}' "$CONTAINER" | head -n1 || true)
fi

echo "Gateway: $GATEWAY"

# Add default gateway to main table for Android's ipconfigstore to discover it on boot
if [ -n "$GATEWAY" ]; then
    if ! docker exec "$CONTAINER" ip route show | grep -q "default via $GATEWAY"; then
        docker exec "$CONTAINER" ip route add default via "$GATEWAY" dev eth0 || true
        echo "✅ Added default gateway to main table: default via $GATEWAY"
    else
        echo "ℹ️  Default gateway already exists in main table"
    fi
fi

# Add bypass rule for local Docker subnet so host/container communications (e.g. ADB, API) don't get routed over VPN.
SUBNET=$(docker exec "$CONTAINER" ip route show dev eth0 2>/dev/null | grep proto | awk '{print $1}' || true)
if [ -n "$SUBNET" ]; then
    echo "Docker Subnet: $SUBNET"
    BYPASS_PRIO=$((RULE_PRIO - 10))
    if ! docker exec "$CONTAINER" ip rule show 2>/dev/null | grep -q "to $SUBNET lookup main"; then
        docker exec "$CONTAINER" ip rule add to "$SUBNET" table main prio "$BYPASS_PRIO"
        echo "✅ Added bypass rule: to $SUBNET -> table main (prio $BYPASS_PRIO)"
    else
        echo "ℹ️  Bypass rule for $SUBNET already exists"
    fi
else
    echo "⚠️  Could not determine Docker subnet of eth0"
fi

# Add high-priority rule to route via WireGuard table (idempotent)
if ! docker exec "$CONTAINER" ip rule show 2>/dev/null | grep -q "lookup $WG_TABLE"; then
    docker exec "$CONTAINER" ip rule add from all table "$WG_TABLE" prio "$RULE_PRIO"
    echo "✅ Added ip rule: prio $RULE_PRIO -> table $WG_TABLE"
else
    echo "ℹ️  ip rule already exists for table $WG_TABLE"
fi

# Add endpoint exception route (idempotent)
if ! docker exec "$CONTAINER" ip route show table "$WG_TABLE" 2>/dev/null | grep -q "$ENDPOINT_IP"; then
    docker exec "$CONTAINER" ip route add "$ENDPOINT_IP/32" via "$GATEWAY" dev eth0 table "$WG_TABLE"
    echo "✅ Added endpoint route: $ENDPOINT_IP via $GATEWAY (table $WG_TABLE)"
else
    echo "ℹ️  Endpoint route already exists"
fi

# Verify only if --verify flag is provided
if [ "${1:-}" = "--verify" ]; then
    echo ""
    echo "=== Verification ==="
    echo "Ping test:"
    docker exec "$CONTAINER" ping -c 2 -W 3 8.8.8.8 2>&1 || echo "❌ Ping failed"
    echo ""
    echo "Public IP:"
    docker exec "$CONTAINER" wget -qO- --timeout=5 http://ifconfig.me/ip 2>&1 || echo "❌ wget failed"
    echo ""
    echo "Health: $(docker inspect "$CONTAINER" --format '{{.State.Health.Status}}')"
fi
