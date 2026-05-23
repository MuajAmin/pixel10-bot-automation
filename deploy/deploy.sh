#!/bin/bash
# Master deployment script for the VPS.
#
# Usage:
#   sudo bash deploy.sh [up|down|rebuild|harden|keybox|status|verify]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ACTION="${1:-up}"

echo "=== Master Deploy: $ACTION ==="

echo ""
echo "=== Phase 1: CRLF cleanup ==="
FIXED=0
while IFS= read -r -d '' f; do
    if grep -qP '\r' "$f" 2>/dev/null; then
        sed -i "s/\r$//" "$f"
        FIXED=$((FIXED + 1))
    fi
done < <(find "$SCRIPT_DIR" -name "*.sh" -print0)

while IFS= read -r -d '' f; do
    if grep -qP '\r' "$f" 2>/dev/null; then
        sed -i "s/\r$//" "$f"
    fi
done < <(find "$SCRIPT_DIR" -name "*.py" -print0)
echo "  Fixed $FIXED shell files"

echo ""
echo "=== Phase 2: Directory validation ==="
for dir in config core bot infra logs; do
    mkdir -p "$SCRIPT_DIR/$dir"
done

if [ ! -f "$SCRIPT_DIR/config/proton.conf" ]; then
    echo "  WARNING: config/proton.conf not found"
    echo "  Copy your Proton VPN WireGuard config there before running up/rebuild."
fi

if [ -f "$SCRIPT_DIR/config/keybox.xml" ]; then
    echo "  keybox.xml found; TrickyStore can be configured"
else
    echo "  No keybox.xml; BASIC integrity only"
fi

ensure_host_vpn() {
    echo "  Disabling host-level WireGuard to avoid conflict with Gluetun..."
    systemctl disable --now wg-quick@wg0 2>/dev/null || true
    if ip link show wg0 >/dev/null 2>&1; then
        ip link delete dev wg0 2>/dev/null || true
    fi
    echo "  Deactivating host policy routing..."
    bash "$SCRIPT_DIR/infra/scripts/network_fix.sh" down 2>/dev/null || true

    echo "  Ensuring Gluetun uses latest VPN config from config/proton.conf..."
    mkdir -p "$SCRIPT_DIR/infra/wireguard"
    cp "$SCRIPT_DIR/config/proton.conf" "$SCRIPT_DIR/infra/wireguard/main.conf"
    cp "$SCRIPT_DIR/config/proton.conf" "$SCRIPT_DIR/infra/wireguard/proton.conf"
    cd "$SCRIPT_DIR/infra"
}

wait_for_android() {
    echo "  Waiting for Android boot..."
    timeout 8s adb connect 127.0.0.1:5555 2>/dev/null || true
    sleep 3

    BOOT=""
    for i in $(seq 1 60); do
        timeout 8s adb connect 127.0.0.1:5555 2>/dev/null || true
        BOOT=$(timeout 8s adb -s 127.0.0.1:5555 shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || echo "")
        if [ "$BOOT" = "1" ]; then
            echo "  Android booted ($((i * 3))s)"
            return 0
        fi
        [ $((i % 10)) -eq 0 ] && echo "  Waiting ($((i * 3))s/180s)..."
        sleep 3
    done

    echo "  ERROR: Android did not boot after 180s"
    docker logs pixel10-android --tail=40 2>&1 || true
    exit 1
}

restore_gluetun_routing() {
    echo "  Restoring Gluetun routing and DNS guardrails..."
    bash "$SCRIPT_DIR/infra/fix_vpn_routing.sh" || {
        echo "    ERROR: failed to restore Gluetun routing guardrails"
        exit 1
    }
}

echo ""
echo "=== Phase 3: Docker Compose ($ACTION) ==="
cd "$SCRIPT_DIR/infra"

case "$ACTION" in
    up)
        ensure_host_vpn
        docker compose up -d --build
        wait_for_android

        restore_gluetun_routing

        echo ""
        echo "=== Post-boot hardening ==="
        cd "$SCRIPT_DIR"
        if [ -f "$SCRIPT_DIR/config/keybox.xml" ]; then
            bash core/build_props.sh full 127.0.0.1:5555
        else
            bash core/build_props.sh base 127.0.0.1:5555
        fi
        ;;

    down)
        docker compose down
        echo "  Stack stopped"
        ;;

    rebuild)
        ensure_host_vpn
        docker compose down -v
        docker compose up -d --build --force-recreate
        wait_for_android

        restore_gluetun_routing

        echo "  Stack rebuilt"
        echo "  Run: sudo bash deploy.sh harden"
        ;;

    harden)
        cd "$SCRIPT_DIR"
        bash core/build_props.sh base 127.0.0.1:5555
        ;;

    keybox)
        cd "$SCRIPT_DIR"
        bash core/build_props.sh keybox 127.0.0.1:5555
        ;;

    status)
        echo ""
        echo "  Docker containers:"
        docker compose ps 2>/dev/null || docker ps --filter "name=pixel10"
        echo ""
        echo "  Host WireGuard:"
        wg show wg0 2>/dev/null || echo "  (wg0 not running)"
        echo ""
        echo "  Docker VPN routing:"
        cd "$SCRIPT_DIR"
        bash infra/scripts/network_fix.sh status || true
        cd "$SCRIPT_DIR/infra"
        echo ""
        echo "  ADB:"
        timeout 8s adb connect 127.0.0.1:5555 2>/dev/null || true
        timeout 8s adb -s 127.0.0.1:5555 shell getprop ro.product.model 2>/dev/null || echo "  (offline)"
        ;;

    verify)
        cd "$SCRIPT_DIR"
        bash core/build_props.sh verify 127.0.0.1:5555
        ;;

    *)
        echo "Usage: $0 {up|down|rebuild|harden|keybox|status|verify}"
        exit 1
        ;;
esac

echo ""
echo "=== Deploy complete: $ACTION ==="
