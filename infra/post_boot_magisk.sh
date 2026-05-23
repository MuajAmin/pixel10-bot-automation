#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# post_boot_magisk.sh — Post-boot Magisk daemon init & module loader
#
# Run AFTER the ReDroid container has booted with the patched binary.
# This script:
#   1. Patches SELinux policy live (the step that was crashing before)
#   2. Starts the Magisk daemon
#   3. Enables Zygisk
#   4. Installs modules from infra/magisk_modules/*.zip
#   5. Restarts zygote to activate Zygisk injection
#
# Usage:   bash infra/post_boot_magisk.sh [ADB_TARGET]
# Example: bash infra/post_boot_magisk.sh 127.0.0.1:5555
#
# Prerequisites:
#   - ReDroid container running with patched magisk64 binary
#   - ADB accessible at the target address
#   - Module zips in infra/magisk_modules/ (e.g. PlayIntegrityFix.zip)
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

ADB_TARGET="${1:-127.0.0.1:5555}"
ADB="adb -s $ADB_TARGET"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODULES_DIR="$SCRIPT_DIR/magisk_modules"

echo "╔══════════════════════════════════════════════════╗"
echo "║  Post-Boot Magisk Initialization                 ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""
echo "  ADB Target:  $ADB_TARGET"
echo "  Modules Dir: $MODULES_DIR"
echo ""

# ── Step 0: Connect and wait for boot ─────────────────────────────
echo "[0/6] Connecting to ADB target..."
$ADB connect "$ADB_TARGET" 2>/dev/null || true
sleep 2

for i in $(seq 1 60); do
    BOOT=$($ADB shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || echo "")
    [ "$BOOT" = "1" ] && break
    [ $((i % 10)) -eq 0 ] && echo "  Waiting for boot... ($i/60)"
    sleep 2
done

BOOT=$($ADB shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || echo "")
if [ "$BOOT" != "1" ]; then
    echo "  ❌ Android boot not completed. Aborting."
    exit 1
fi
echo "  ✅ Android boot complete"

# ── Step 1: Patch SELinux policy ──────────────────────────────────
echo ""
echo "[1/6] Patching SELinux policy (magiskpolicy --live)..."

# Try multiple known paths for the magisk binary
MAGISK_BIN=""
for path in "/data/adb/magisk/magisk64" "/system/etc/init/magisk/magisk64" "/sbin/magisk"; do
    if $ADB shell "[ -f '$path' ] && echo exists" 2>/dev/null | grep -q "exists"; then
        MAGISK_BIN="$path"
        break
    fi
done

if [ -z "$MAGISK_BIN" ]; then
    echo "  ❌ Could not find magisk64 binary in container."
    echo "  Did you run extract_magisk.sh and add the volume overlay to docker-compose.yml?"
    exit 1
fi

echo "  Using binary: $MAGISK_BIN"

# Create the magiskpolicy symlink if it doesn't exist
$ADB shell "su 0 sh -c '
    MAGISK_DIR=\$(dirname $MAGISK_BIN)
    [ ! -f \$MAGISK_DIR/magiskpolicy ] && ln -sf $MAGISK_BIN \$MAGISK_DIR/magiskpolicy
    [ ! -f /sbin/magiskpolicy ] && { mkdir -p /sbin; ln -sf $MAGISK_BIN /sbin/magiskpolicy; }
    [ ! -f /sbin/resetprop ] && ln -sf $MAGISK_BIN /sbin/resetprop
    [ ! -f /sbin/magisk ] && ln -sf $MAGISK_BIN /sbin/magisk
'" 2>/dev/null || true

# Patch SELinux — this is the operation that was SIGABRT-ing before the fix
POLICY_RESULT=$($ADB shell "su 0 sh -c '
    MAGISK_DIR=\$(dirname $MAGISK_BIN)
    \$MAGISK_DIR/magiskpolicy --live --magisk 2>&1
'" 2>/dev/null || echo "FAILED")

if echo "$POLICY_RESULT" | grep -qi "fail\|abort\|error\|signal"; then
    echo "  ⚠️  magiskpolicy returned warnings: $POLICY_RESULT"
    echo "  Continuing anyway (some warnings are non-fatal)..."
else
    echo "  ✅ SELinux policy patched successfully"
fi

# ── Step 2: Start Magisk daemon ───────────────────────────────────
echo ""
echo "[2/6] Starting Magisk daemon..."

$ADB shell "su 0 sh -c '
    # Set up /data/adb directory structure
    mkdir -p /data/adb/magisk
    mkdir -p /data/adb/modules
    mkdir -p /data/adb/post-fs-data.d
    mkdir -p /data/adb/service.d

    # Copy binary to data partition if not there
    if [ ! -f /data/adb/magisk/magisk64 ]; then
        cp $MAGISK_BIN /data/adb/magisk/magisk64
        chmod 755 /data/adb/magisk/magisk64
        ln -sf /data/adb/magisk/magisk64 /data/adb/magisk/magisk
        ln -sf /data/adb/magisk/magisk64 /data/adb/magisk/magiskpolicy
        ln -sf /data/adb/magisk/magisk64 /data/adb/magisk/resetprop
        ln -sf /data/adb/magisk/magisk64 /data/adb/magisk/su
    fi

    # Start the daemon
    /data/adb/magisk/magisk64 --daemon
'" 2>/dev/null || true

sleep 2

# Verify daemon is running
MAGISK_VER=$($ADB shell "su 0 sh -c '/data/adb/magisk/magisk64 -v'" 2>/dev/null | tr -d '\r' || echo "")
if [ -n "$MAGISK_VER" ]; then
    echo "  ✅ Magisk daemon running: $MAGISK_VER"
else
    echo "  ⚠️  Magisk version query returned empty (daemon may still be starting)"
fi

# ── Step 3: Enable Zygisk ────────────────────────────────────────
echo ""
echo "[3/6] Enabling Zygisk..."

$ADB shell "su 0 sh -c '
    /data/adb/magisk/magisk64 --sqlite \"REPLACE INTO settings (key,value) VALUES(\\\"zygisk\\\",1);\" 2>/dev/null
'" 2>/dev/null && echo "  ✅ Zygisk enabled in Magisk settings" || echo "  ⚠️  Could not set Zygisk flag (will try manual method)"

# Fallback: write the denylist config directly
$ADB shell "su 0 sh -c '
    mkdir -p /data/adb
    echo "ZYGISK=true" >> /data/adb/magisk.db 2>/dev/null || true
'" 2>/dev/null || true

# ── Step 4: Install modules ──────────────────────────────────────
echo ""
echo "[4/6] Installing Magisk modules..."

if [ ! -d "$MODULES_DIR" ] || [ -z "$(ls "$MODULES_DIR"/*.zip 2>/dev/null)" ]; then
    echo "  ℹ️  No module zips found in $MODULES_DIR — skipping"
else
    for zip in "$MODULES_DIR"/*.zip; do
        [ -f "$zip" ] || continue
        MOD_NAME=$(basename "$zip")
        echo "  Installing: $MOD_NAME"

        $ADB push "$zip" "/data/local/tmp/$MOD_NAME" >/dev/null 2>&1

        INSTALL_RESULT=$($ADB shell "su 0 sh -c '
            /data/adb/magisk/magisk64 --install-module /data/local/tmp/$MOD_NAME 2>&1
        '" 2>/dev/null || echo "FAILED")

        if echo "$INSTALL_RESULT" | grep -qi "success\|done"; then
            echo "    ✅ $MOD_NAME installed"
        else
            echo "    ⚠️  $MOD_NAME install result: $INSTALL_RESULT"
            echo "    Attempting manual installation..."

            # Manual fallback: extract to modules directory
            $ADB shell "su 0 sh -c '
                MOD_ID=\$(unzip -p /data/local/tmp/$MOD_NAME module.prop 2>/dev/null | grep \"^id=\" | cut -d= -f2 | tr -d \"\\r\")
                if [ -n \"\$MOD_ID\" ]; then
                    mkdir -p /data/adb/modules/\$MOD_ID
                    cd /data/adb/modules/\$MOD_ID
                    unzip -o /data/local/tmp/$MOD_NAME 2>/dev/null
                    chmod -R 755 /data/adb/modules/\$MOD_ID
                    echo \"Manual install to /data/adb/modules/\$MOD_ID\"
                else
                    echo \"Could not determine module ID\"
                fi
            '" 2>/dev/null || true
        fi

        $ADB shell "rm -f '/data/local/tmp/$MOD_NAME'" 2>/dev/null || true
    done
fi

# ── Step 5: Push PIF config ──────────────────────────────────────
echo ""
echo "[5/6] Deploying Play Integrity Fix configuration..."

PIF_JSON="$SCRIPT_DIR/identity/pif.json"
if [ -f "$PIF_JSON" ]; then
    # PIF reads config from /data/adb/pif.json
    $ADB push "$PIF_JSON" /data/local/tmp/pif.json >/dev/null 2>&1
    $ADB shell "su 0 sh -c '
        cp /data/local/tmp/pif.json /data/adb/pif.json
        chmod 644 /data/adb/pif.json
        chown root:root /data/adb/pif.json
        rm -f /data/local/tmp/pif.json
    '" 2>/dev/null
    echo "  ✅ pif.json deployed to /data/adb/pif.json"
else
    echo "  ℹ️  No pif.json found at $PIF_JSON — PIF will use defaults"
fi

# ── Step 6: Restart zygote to activate Zygisk ────────────────────
echo ""
echo "[6/6] Restarting zygote to activate Zygisk injection..."

$ADB shell "su 0 sh -c 'setprop ctl.restart zygote'" 2>/dev/null || \
    $ADB shell "su 0 sh -c 'stop; sleep 2; start'" 2>/dev/null || true

echo "  Waiting for framework restart..."
sleep 5

for i in $(seq 1 30); do
    BOOT=$($ADB shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || echo "")
    [ "$BOOT" = "1" ] && break
    sleep 2
done

BOOT=$($ADB shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || echo "")
if [ "$BOOT" = "1" ]; then
    echo "  ✅ Framework restarted successfully"
else
    echo "  ⚠️  Boot not yet complete — framework may still be starting"
fi

# ── Final verification ────────────────────────────────────────────
echo ""
echo "═══ Verification ═══"

MAGISK_VER=$($ADB shell "su 0 sh -c '/data/adb/magisk/magisk64 -v'" 2>/dev/null | tr -d '\r' || echo "not found")
echo "  Magisk version:  $MAGISK_VER"

MODULE_LIST=$($ADB shell "su 0 ls /data/adb/modules/" 2>/dev/null | tr -d '\r' || echo "(none)")
echo "  Installed modules:"
echo "$MODULE_LIST" | while read -r mod; do
    [ -z "$mod" ] && continue
    echo "    • $mod"
done

RESETPROP_TEST=$($ADB shell "su 0 sh -c 'which resetprop 2>/dev/null || echo missing'" 2>/dev/null | tr -d '\r')
echo "  resetprop:       $RESETPROP_TEST"

echo ""
echo "═══ Post-Boot Initialization Complete ═══"
echo ""
echo "Next steps:"
echo "  1. Run: bash core/build_props.sh base    (apply Pixel identity)"
echo "  2. Run: bash core/build_props.sh verify   (check property scorecard)"
echo "  3. Run: bash core/build_props.sh keybox   (push keybox if available)"
