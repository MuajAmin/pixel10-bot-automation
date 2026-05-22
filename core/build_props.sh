#!/bin/bash
# ── Self CRLF Self-Healing Header ───────────────────────────────
# If this script has CRLF line endings (Windows style), it will fix itself and re-run.
if [ -n "$(tail -n 1 "$0" | tr -d '\r')" ] && grep -q $'\r' "$0" 2>/dev/null; then
    echo "⚠️ Windows CRLF endings detected in build_props.sh. Auto-fixing..."
    sed -i 's/\r$//' "$0"
    exec bash "$0" "$@"
fi

# ═══════════════════════════════════════════════════════════════════
#  Dual-Identity Property Engine
#
#  ARCHITECTURE:
#    ┌─────────────────────────────────────────────────────────┐
#    │  GLOBAL LAYER (Pixel 5 / redfin / SDK 30)              │
#    │  • Matches actual Android 11 runtime                    │
#    │  • Play Integrity passes BASIC level                    │
#    │  • All system processes see Pixel 5                     │
#    │                                                         │
#    │  SWAP LAYER (Pixel 10 Pro / franklin / SDK 36)          │
#    │  • Applied for ~5s window during Google One launch      │
#    │  • Build.* class caches values in-memory                │
#    │  • After restore, Google One retains Pixel 10 Pro       │
#    │  • GMS continues seeing Pixel 5 (no attestation break)  │
#    └─────────────────────────────────────────────────────────┘
#
#  NATIVE BRIDGE POLICY:
#    ro.dalvik.vm.native.bridge is NEVER deleted.
#    Only ISA-mapping props (ro.dalvik.vm.isa.arm*) are removed.
#
#  Usage:
#    build_props.sh base   [ADB]  — Full Pixel 5 global identity
#    build_props.sh swap   [ADB]  — Swap to Pixel 10 Pro (for Google One)
#    build_props.sh restore [ADB] — Restore Pixel 5 identity
#    build_props.sh keybox [ADB]  — Push keybox.xml + TrickyStore
#    build_props.sh verify [ADB]  — Verification scorecard
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

ACTION="${1:-base}"
ADB_TARGET="${2:-127.0.0.1:5555}"
ADB="adb -s $ADB_TARGET"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ── Auto-detect SU binary ───────────────────────────────────────
detect_su() {
    for su_path in "/sbin/su" "/system/xbin/su" "/system/bin/su" "su"; do
        if $ADB shell "$su_path -c 'id'" 2>/dev/null | grep -q "uid=0"; then
            echo "$su_path"
            return 0
        fi
    done
    # Fallback: check if already root
    if $ADB shell id 2>/dev/null | grep -q "uid=0"; then
        echo ""
        return 0
    fi
    echo ""
    return 1
}

# ── Auto-detect resetprop ───────────────────────────────────────
detect_resetprop() {
    local su="$1"
    for path in "resetprop" "/sbin/resetprop" "/data/adb/magisk/resetprop"; do
        local test_cmd
        if [ -n "$su" ]; then
            test_cmd="$su -c 'which $path'"
        else
            test_cmd="which $path"
        fi
        if $ADB shell "$test_cmd" 2>/dev/null | grep -q "resetprop"; then
            echo "$path"
            return 0
        fi
    done
    echo "setprop"
}

# ── Connect + wait for boot with robust retries ─────────────────
wait_boot() {
    echo "  Connecting to adb target: $ADB_TARGET..."
    $ADB connect "$ADB_TARGET" 2>/dev/null || true
    sleep 2
    
    # Robust wait with multiple connect retries
    local boot_success=0
    for i in $(seq 1 30); do
        if $ADB wait-for-device 2>/dev/null; then
            boot_success=1
            break
        fi
        echo "  [ADB] Waiting for device... Retrying connection ($i/30)"
        $ADB connect "$ADB_TARGET" 2>/dev/null || true
        sleep 2
    done

    if [ "$boot_success" -ne 1 ]; then
        echo "❌ ADB connection timed out for target: $ADB_TARGET"
        exit 1
    fi

    # Wait for Android OS boot completed flag
    for i in $(seq 1 60); do
        BOOT=$($ADB shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || echo "")
        [ "$BOOT" = "1" ] && return 0
        [ $((i % 10)) -eq 0 ] && echo "  Waiting for boot... ($i/60)"
        sleep 2
    done
    echo "❌ Boot timeout"; exit 1
}

# ── Property setter (resetprop + fallback) ───────────────────────
SU=""
RESETPROP=""

init_tools() {
    SU=$(detect_su)
    RESETPROP=$(detect_resetprop "$SU")
    [ -n "$SU" ] && echo "  SU binary: $SU" || echo "  SU: running as native adb root"
    echo "  Using property tool: $RESETPROP"
}

set_prop() {
    local prop="$1" value="$2"
    if [ "$RESETPROP" = "setprop" ]; then
        $ADB shell "setprop '$prop' '$value'" 2>/dev/null || true
    elif [ -n "$SU" ]; then
        $ADB shell "$SU -c '$RESETPROP \"$prop\" \"$value\"'" 2>/dev/null || \
        $ADB shell "setprop '$prop' '$value'" 2>/dev/null || true
    else
        $ADB shell "$RESETPROP '$prop' '$value'" 2>/dev/null || true
    fi
}

del_prop() {
    if [ "$RESETPROP" != "setprop" ]; then
        if [ -n "$SU" ]; then
            $ADB shell "$SU -c '$RESETPROP --delete \"$1\"'" 2>/dev/null || true
        else
            $ADB shell "$RESETPROP --delete '$1'" 2>/dev/null || true
        fi
    fi
}

su_exec() {
    if [ -n "$SU" ]; then
        $ADB shell "$SU" -c "$@" 2>/dev/null
    else
        $ADB shell "$@" 2>/dev/null
    fi
}

# ═══════════════════════════════════════════════════════════════════
#  PIXEL 5 GLOBAL IDENTITY (base)
#  Matches the actual Android 11 runtime. No SDK mismatch.
# ═══════════════════════════════════════════════════════════════════
apply_pixel5_base() {
    echo ""
    echo "━━━ Applying Pixel 5 (redfin) Global Identity ━━━"

    # Product identity across ALL partitions
    for pfx in "" ".system" ".vendor" ".odm" ".product"; do
        set_prop "ro.product${pfx}.brand"        "google"
        set_prop "ro.product${pfx}.device"       "redfin"
        set_prop "ro.product${pfx}.manufacturer" "Google"
        set_prop "ro.product${pfx}.model"        "Pixel 5"
        set_prop "ro.product${pfx}.name"         "redfin"
    done

    # Build info (SDK 30 = matches Android 11 runtime)
    set_prop "ro.build.fingerprint"            "google/redfin/redfin:11/RQ3A.210805.001.A1/7474174:user/release-keys"
    set_prop "ro.build.display.id"             "RQ3A.210805.001.A1"
    set_prop "ro.build.id"                     "RQ3A.210805.001.A1"
    set_prop "ro.build.description"            "redfin-user 11 RQ3A.210805.001.A1 7474174 release-keys"
    set_prop "ro.build.version.release"        "11"
    set_prop "ro.build.version.sdk"            "30"
    set_prop "ro.build.version.incremental"    "7474174"
    set_prop "ro.build.version.security_patch" "2021-08-05"
    set_prop "ro.build.version.codename"       "REL"
    set_prop "ro.build.type"                   "user"
    set_prop "ro.build.tags"                   "release-keys"
    set_prop "ro.build.flavor"                 "redfin-user"
    set_prop "ro.build.product"                "redfin"

    # All partition build types
    for pfx in bootimage odm odm_dlkm oem product system system_ext vendor vendor_dlkm; do
        set_prop "ro.${pfx}.build.type" "user"
        set_prop "ro.${pfx}.build.tags" "release-keys"
    done

    # Hardware (Snapdragon 765G)
    set_prop "ro.hardware"                     "redfin"
    set_prop "ro.board.platform"               "lito"
    set_prop "ro.soc.manufacturer"             "Qualcomm"
    set_prop "ro.soc.model"                    "SM7250"
    set_prop "ro.hardware.keystore"            "trusty"
    set_prop "ro.hardware.gatekeeper"          "trusty"
    set_prop "ro.boot.hardware"                "redfin"
    set_prop "ro.boot.hardware.sku"            "redfin"
    set_prop "ro.boot.product.hardware.sku"    "redfin"
    set_prop "ro.boot.hardware.revision"       "MP1.0"
    set_prop "ro.boot.slot_suffix"             "_a"
    set_prop "ro.bootloader"                   "c2f2-0.4-7617406"
    set_prop "gsm.version.baseband"            "g7250-00177-210607-B-7455850"

    # Anti-emulator
    set_prop "ro.kernel.qemu"                  "0"
    set_prop "ro.kernel.qemu.gles"             "0"
    set_prop "ro.debuggable"                   "0"
    set_prop "ro.secure"                       "1"
    set_prop "ro.adb.secure"                   "1"
    set_prop "ro.force.debuggable"             "0"
    set_prop "ro.boot.verifiedbootstate"       "green"
    set_prop "ro.boot.flash.locked"            "1"
    set_prop "ro.boot.vbmeta.device_state"     "locked"
    set_prop "ro.boot.veritymode"              "enforcing"
    set_prop "sys.oem_unlock_allowed"          "0"
    set_prop "ro.oem_unlock_supported"         "0"
    set_prop "ro.crypto.state"                 "encrypted"
    set_prop "ro.crypto.type"                  "file"
    set_prop "ro.secureboot.lockstate"         "locked"
    set_prop "ro.secureboot.devicelock"        "1"
    set_prop "ro.build.selinux"                "1"

    # GMS
    set_prop "ro.com.google.gmsversion"        "11_202108"
    set_prop "ro.com.google.clientidbase"      "android-google"
    set_prop "ro.com.google.clientidbase.ms"   "android-google"
    set_prop "ro.com.google.clientidbase.vs"   "android-google"
    set_prop "ro.com.google.clientidbase.am"   "android-google"
    set_prop "ro.opa.eligible_device"          "true"
    set_prop "ro.product.first_api_level"      "30"
    set_prop "persist.sys.device_provisioned"  "1"

    # Native bridge: PRESERVE bridge, delete ISA-mapping only
    del_prop "ro.dalvik.vm.isa.arm"
    del_prop "ro.dalvik.vm.isa.arm64"
    set_prop "ro.product.cpu.abilist"          "x86_64,x86,arm64-v8a,armeabi-v7a,armeabi"
    set_prop "ro.product.cpu.abilist64"        "x86_64,arm64-v8a"
    set_prop "ro.product.cpu.abilist32"        "x86,armeabi-v7a,armeabi"

    # SELinux
    su_exec "setenforce 1" || $ADB shell setenforce 1 2>/dev/null || true

    # Automation-hostile settings
    $ADB shell settings delete global hidden_api_policy 2>/dev/null || true
    $ADB shell settings delete global hidden_api_policy_pre_p_apps 2>/dev/null || true
    $ADB shell settings delete global hidden_api_policy_p_apps 2>/dev/null || true
    for ns in global system secure; do
        $ADB shell settings put "$ns" block_untrusted_touches 0 2>/dev/null || true
    done

    # Clean emulator files
    su_exec "rm -f /system/build.prop.bak" || true
    su_exec "rm -f /data/property/persist.sys.dalvik.vm.lib.2" || true

    echo "  ✅ Pixel 5 global identity applied"
}

# ═══════════════════════════════════════════════════════════════════
#  PIXEL 10 PRO SWAP (for Google One only)
# ═══════════════════════════════════════════════════════════════════
swap_pixel10_pro() {
    echo ""
    echo "━━━ PROP SWAP: Pixel 10 Pro (franklin) ━━━"

    local CODENAME="${PIXEL10_CODENAME:-franklin}"
    local BUILD_ID="BP1A.251205.010"
    local INCREMENTAL="12847291"
    local FP="google/${CODENAME}/${CODENAME}:16/${BUILD_ID}/${INCREMENTAL}:user/release-keys"
    echo "  Codename: $CODENAME"
    echo "  Build ID: $BUILD_ID"

    # Kill Google One first (ensures fresh process start)
    $ADB shell am force-stop com.google.android.apps.subscriptions.red 2>/dev/null || true
    sleep 1

    echo "  Swapping to Pixel 10 Pro..."

    # Product identity → Pixel 10 Pro (all partitions)
    for pfx in "" ".system" ".vendor" ".odm" ".product"; do
        set_prop "ro.product${pfx}.brand"        "google"
        set_prop "ro.product${pfx}.device"       "$CODENAME"
        set_prop "ro.product${pfx}.manufacturer" "Google"
        set_prop "ro.product${pfx}.model"        "Pixel 10 Pro"
        set_prop "ro.product${pfx}.name"         "$CODENAME"
    done

    # Build → Android 16 / SDK 36
    set_prop "ro.build.fingerprint"            "$FP"
    set_prop "ro.bootimage.build.fingerprint"  "$FP"
    set_prop "ro.system.build.fingerprint"     "$FP"
    set_prop "ro.vendor.build.fingerprint"     "$FP"
    set_prop "ro.build.display.id"             "$BUILD_ID"
    set_prop "ro.build.id"                     "$BUILD_ID"
    set_prop "ro.build.description"            "${CODENAME}-user 16 ${BUILD_ID} ${INCREMENTAL} release-keys"
    set_prop "ro.build.version.release"        "16"
    set_prop "ro.build.version.sdk"            "36"
    set_prop "ro.build.version.incremental"    "$INCREMENTAL"
    set_prop "ro.build.version.security_patch" "2025-12-05"
    set_prop "ro.build.flavor"                 "${CODENAME}-user"
    set_prop "ro.build.product"                "$CODENAME"

    # Hardware → Tensor G5
    set_prop "ro.hardware"                     "tensor"
    set_prop "ro.board.platform"               "gs601"
    set_prop "ro.soc.manufacturer"             "Google"
    set_prop "ro.soc.model"                    "Tensor G5"
    set_prop "ro.boot.hardware"                "tensor"
    set_prop "ro.boot.hardware.sku"            "$CODENAME"
    set_prop "ro.boot.product.hardware.sku"    "$CODENAME"

    # GMS version for Android 16
    set_prop "ro.com.google.gmsversion"        "16_202605"
    set_prop "ro.product.first_api_level"      "36"

    # Gemini offer eligibility
    set_prop "ro.opa.eligible_device"          "true"
    set_prop "ro.com.google.clientidbase"      "android-google"

    echo "  ✅ Props swapped to Pixel 10 Pro ($CODENAME)"

    # Restart framework is disabled as it causes DeadSystemException on Android 11 with SDK 36.
    # Instead, we force-stop GMS/GSF/Vending/Google One to ensure they read the new properties on process initialization.
    echo "  Force-stopping Google services..."
    for pkg in com.google.android.gms \
               com.android.vending \
               com.google.android.apps.subscriptions.red \
               com.google.android.gsf \
               com.google.android.gsf.login; do
        $ADB shell am force-stop "$pkg" 2>/dev/null || true
    done
    sleep 2

    # Launch Google One — it will read Pixel 10 Pro from Build.*
    echo "  Launching Google One under Pixel 10 Pro identity..."
    $ADB shell am start -n com.google.android.apps.subscriptions.red/.MainActivity 2>/dev/null || true

    # Wait for Build.* class initialization in Google One's process
    echo "  Waiting 4s for Build.* class init..."
    sleep 4

    echo "  ✅ Google One loaded with Pixel 10 Pro identity"
}

restore_pixel5() {
    echo ""
    echo "━━━ RESTORING: Pixel 5 (redfin) ━━━"

    # Product identity → Pixel 5
    for pfx in "" ".system" ".vendor" ".odm" ".product"; do
        set_prop "ro.product${pfx}.brand"        "google"
        set_prop "ro.product${pfx}.device"       "redfin"
        set_prop "ro.product${pfx}.manufacturer" "Google"
        set_prop "ro.product${pfx}.model"        "Pixel 5"
        set_prop "ro.product${pfx}.name"         "redfin"
    done

    # Build → Android 11 / SDK 30
    set_prop "ro.build.fingerprint"            "google/redfin/redfin:11/RQ3A.210805.001.A1/7474174:user/release-keys"
    set_prop "ro.build.display.id"             "RQ3A.210805.001.A1"
    set_prop "ro.build.id"                     "RQ3A.210805.001.A1"
    set_prop "ro.build.description"            "redfin-user 11 RQ3A.210805.001.A1 7474174 release-keys"
    set_prop "ro.build.version.release"        "11"
    set_prop "ro.build.version.sdk"            "30"
    set_prop "ro.build.version.incremental"    "7474174"
    set_prop "ro.build.version.security_patch" "2021-08-05"
    set_prop "ro.build.flavor"                 "redfin-user"
    set_prop "ro.build.product"                "redfin"

    # Hardware → Snapdragon 765G
    set_prop "ro.hardware"                     "redfin"
    set_prop "ro.board.platform"               "lito"
    set_prop "ro.soc.manufacturer"             "Qualcomm"
    set_prop "ro.soc.model"                    "SM7250"
    set_prop "ro.boot.hardware"                "redfin"
    set_prop "ro.boot.hardware.sku"            "redfin"
    set_prop "ro.boot.product.hardware.sku"    "redfin"

    # GMS version
    set_prop "ro.com.google.gmsversion"        "11_202108"
    set_prop "ro.product.first_api_level"      "30"

    echo "  ✅ Global identity restored to Pixel 5"
    echo "  ℹ️  Google One retains Pixel 10 Pro in JVM memory"
}

# ═══════════════════════════════════════════════════════════════════
#  TRICKYSTORE / KEYBOX INTEGRATION
# ═══════════════════════════════════════════════════════════════════
push_keybox() {
    echo ""
    echo "━━━ TrickyStore + Keybox Integration ━━━"

    local KEYBOX="${PROJECT_DIR}/config/keybox.xml"
    if [ ! -f "$KEYBOX" ]; then
        # Check alternative path (if run inside containers)
        KEYBOX="/app/config/keybox.xml"
    fi

    if [ ! -f "$KEYBOX" ]; then
        echo "  ⚠️  No keybox.xml found at config/keybox.xml"
        echo "  Place a valid keybox.xml in config/ for DEVICE_INTEGRITY"
        return 1
    fi

    echo "  Found keybox.xml, normalizing CRLF endings..."
    # Inline strip CRLF from keybox on host first to prevent XML parsing crash
    sed -i "s/\r$//" "$KEYBOX" 2>/dev/null || true

    # Install TrickyStore module if not present
    local TS_INSTALLED
    TS_INSTALLED=$(su_exec "[ -f /data/adb/modules/tricky_store/module.prop ] && echo yes || echo no" | tr -d '\r')

    if [ "$TS_INSTALLED" != "yes" ]; then
        echo "  TrickyStore Magisk module not found. Installing..."
        local TS_ZIP="/tmp/TrickyStore.zip"
        if curl -sfL -o "$TS_ZIP" "https://github.com/5ec1cff/TrickyStore/releases/latest/download/TrickyStore.zip" 2>/dev/null || \
           wget -q -O "$TS_ZIP" "https://github.com/5ec1cff/TrickyStore/releases/latest/download/TrickyStore.zip" 2>/dev/null; then
            $ADB push "$TS_ZIP" /data/local/tmp/TrickyStore.zip >/dev/null 2>&1
            su_exec "magisk --install-module /data/local/tmp/TrickyStore.zip" && \
                echo "  ✅ TrickyStore module installed successfully" || \
                echo "  ❌ TrickyStore Magisk module install failed"
            $ADB shell rm -f /data/local/tmp/TrickyStore.zip 2>/dev/null || true
            rm -f "$TS_ZIP"
        else
            echo "  ❌ Cannot download TrickyStore module from GitHub"
        fi
    else
        echo "  ✅ TrickyStore module already registered in Magisk"
    fi

    # Push and set absolute strict permissions on target keybox.xml
    echo "  Pushing keybox.xml to target secure storage path..."
    $ADB push "$KEYBOX" /data/local/tmp/keybox.xml >/dev/null 2>&1
    su_exec "mkdir -p /data/adb/tricky_store"
    su_exec "cp /data/local/tmp/keybox.xml /data/adb/tricky_store/keybox.xml"
    
    # ── Strict Authorization & Ownership Setup ──
    su_exec "chown -R root:root /data/adb/tricky_store"
    su_exec "chmod 755 /data/adb/tricky_store"
    su_exec "chmod 600 /data/adb/tricky_store/keybox.xml"
    su_exec "sed -i 's/\r$//' /data/adb/tricky_store/keybox.xml"  # Double check CRLF in container
    
    $ADB shell rm -f /data/local/tmp/keybox.xml 2>/dev/null || true

    echo "  ✅ keybox.xml successfully locked at /data/adb/tricky_store/keybox.xml (chmod 600)"
    echo "  ℹ️  Reboot Android (or restart stack) to load device keybox certificates"
}

# ═══════════════════════════════════════════════════════════════════
#  GSF ID EXTRACTION
# ═══════════════════════════════════════════════════════════════════
extract_gsf() {
    echo ""
    echo "━━━ GSF ID Extraction ━━━"

    local GSF_ID
    GSF_ID=$(su_exec "sqlite3 /data/data/com.google.android.gsf/databases/gservices.db 'select value from main where name=\"android_id\";'" 2>/dev/null | tr -d '\r' || echo "")

    if [ -n "$GSF_ID" ] && echo "$GSF_ID" | grep -qE '^[0-9]+$'; then
        local GSF_HEX
        GSF_HEX=$(printf "%x" "$GSF_ID" 2>/dev/null || echo "$GSF_ID")
        echo "  GSF ID: $GSF_ID (hex: $GSF_HEX)"
        echo ""
        echo "  Register: https://www.google.com/android/uncertified"
        echo "  Enter:    $GSF_HEX"
    else
        echo "  ℹ️  GSF ID not registered yet. Google Services will populate this after login completes."
    fi
}

# ═══════════════════════════════════════════════════════════════════
#  VERIFICATION SCORECARD
# ═══════════════════════════════════════════════════════════════════
verify_props() {
    echo ""
    echo "━━━ Verification Scorecard ━━━"
    echo ""

    local SCORE=0 TOTAL=0

    _check() {
        local name="$1" expected="$2" actual="$3"
        TOTAL=$((TOTAL + 1))
        if [ "$actual" = "$expected" ]; then
            echo "  ✅ $name = $actual"
            SCORE=$((SCORE + 1))
        else
            echo "  ❌ $name = $actual (expected: $expected)"
        fi
    }

    _check "ro.product.model"          "Pixel 5"       "$($ADB shell getprop ro.product.model | tr -d '\r')"
    _check "ro.product.device"         "redfin"         "$($ADB shell getprop ro.product.device | tr -d '\r')"
    _check "ro.product.system.device"  "redfin"         "$($ADB shell getprop ro.product.system.device | tr -d '\r')"
    _check "ro.build.version.sdk"      "30"             "$($ADB shell getprop ro.build.version.sdk | tr -d '\r')"
    _check "ro.build.type"             "user"           "$($ADB shell getprop ro.build.type | tr -d '\r')"
    _check "ro.kernel.qemu"            "0"              "$($ADB shell getprop ro.kernel.qemu | tr -d '\r')"
    _check "ro.debuggable"             "0"              "$($ADB shell getprop ro.debuggable | tr -d '\r')"
    _check "ro.boot.verifiedbootstate" "green"          "$($ADB shell getprop ro.boot.verifiedbootstate | tr -d '\r')"
    _check "ro.hardware"               "redfin"         "$($ADB shell getprop ro.hardware | tr -d '\r')"
    _check "ro.board.platform"         "lito"           "$($ADB shell getprop ro.board.platform | tr -d '\r')"

    # Native bridge must be PRESENT
    local BRIDGE
    BRIDGE=$($ADB shell getprop ro.dalvik.vm.native.bridge | tr -d '\r')
    TOTAL=$((TOTAL + 1))
    if [ -n "$BRIDGE" ] && [ "$BRIDGE" != "0" ]; then
        echo "  ✅ native.bridge = $BRIDGE (preserved)"
        SCORE=$((SCORE + 1))
    else
        echo "  ❌ native.bridge missing"
    fi

    # ISA must be DELETED
    local ISA
    ISA=$($ADB shell getprop ro.dalvik.vm.isa.arm | tr -d '\r')
    TOTAL=$((TOTAL + 1))
    if [ -z "$ISA" ]; then
        echo "  ✅ isa.arm = (deleted)"
        SCORE=$((SCORE + 1))
    else
        echo "  ❌ isa.arm = $ISA (should be deleted)"
    fi

    # TrickyStore Keybox permission check (optional — only scored if keybox is deployed)
    local KB_EXISTS
    KB_EXISTS=$(su_exec "[ -f /data/adb/tricky_store/keybox.xml ] && echo yes || echo no" 2>/dev/null | tr -d '\r' || echo "no")
    if [ "$KB_EXISTS" = "yes" ]; then
        local KB_PERM
        KB_PERM=$(su_exec "stat -c '%a' /data/adb/tricky_store/keybox.xml" 2>/dev/null | tr -d '\r' || echo "")
        TOTAL=$((TOTAL + 1))
        if [ "$KB_PERM" = "600" ]; then
            echo "  ✅ keybox.xml permissions = 600 (restricted)"
            SCORE=$((SCORE + 1))
        else
            echo "  ❌ keybox.xml permissions = ${KB_PERM:-none} (must be 600)"
        fi
    else
        echo "  ℹ️  keybox.xml not deployed (optional — place in config/keybox.xml and run 'keybox' command)"
    fi

    echo ""
    echo "  Score: $SCORE / $TOTAL"
    [ "$SCORE" -eq "$TOTAL" ] && echo "  🎯 PERFECT STATUS" || echo "  ⚠️  Fix issues above"
}

# ═══════════════════════════════════════════════════════════════════
#  DEVICE IDENTITY RESET (for multi-account claiming)
# ═══════════════════════════════════════════════════════════════════
generate_imei() {
    local TACS=("35326511" "35161413" "35870111" "35397710" "35256211"
                "35260411" "35836810" "35738811" "35260611" "35904511")
    local TAC="${TACS[$((RANDOM % ${#TACS[@]}))]}"
    local SERIAL=""
    for i in $(seq 1 6); do
        SERIAL="${SERIAL}$((RANDOM % 10))"
    done
    local PARTIAL="${TAC}${SERIAL}"
    local SUM=0
    local LEN=${#PARTIAL}
    for i in $(seq 0 $((LEN - 1))); do
        local DIGIT="${PARTIAL:$i:1}"
        if [ $(( (LEN - i) % 2 )) -eq 0 ]; then
            DIGIT=$((DIGIT * 2))
            [ "$DIGIT" -gt 9 ] && DIGIT=$((DIGIT - 9))
        fi
        SUM=$((SUM + DIGIT))
    done
    local CHECK=$(( (10 - (SUM % 10)) % 10 ))
    echo "${PARTIAL}${CHECK}"
}

random_hex() {
    local LEN="${1:-16}"
    cat /dev/urandom 2>/dev/null | tr -dc 'a-f0-9' | head -c "$LEN" || \
    od -An -tx1 -N"$((LEN/2 + 1))" /dev/urandom 2>/dev/null | tr -d ' \n' | head -c "$LEN"
}

random_alnum() {
    local LEN="${1:-12}"
    cat /dev/urandom 2>/dev/null | tr -dc 'A-Z0-9' | head -c "$LEN" || \
    od -An -tx1 -N"$((LEN/2 + 1))" /dev/urandom 2>/dev/null | tr -d ' \n' | head -c "$LEN" | tr 'a-f' 'A-F'
}

reset_device_identity() {
    echo ""
    echo "━━━ DEVICE IDENTITY RESET ━━━"
    echo ""

    local NEW_IMEI=$(generate_imei)
    local NEW_IMEI2=$(generate_imei)
    local NEW_ANDROID_ID=$(random_hex 16)
    local NEW_SERIAL=$(random_alnum 12)
    local NEW_MAC="02:$(random_hex 2):$(random_hex 2):$(random_hex 2):$(random_hex 2):$(random_hex 2)"
    local NEW_BOOT_ID=$(random_hex 8)-$(random_hex 4)-$(random_hex 4)-$(random_hex 4)-$(random_hex 12)

    echo "  New Identifiers Generated:"
    echo "    IMEI 1:     $NEW_IMEI"
    echo "    IMEI 2:     $NEW_IMEI2"
    echo "    Android ID: $NEW_ANDROID_ID"
    echo "    Serial:     $NEW_SERIAL"
    echo "    MAC:        $NEW_MAC"
    echo "    Boot ID:    $NEW_BOOT_ID"
    echo ""

    echo "  [1/6] Stopping Android Java runtime framework..."
    su_exec "stop"
    echo "  Waiting for system_server and zygote to terminate..."
    for i in $(seq 1 30); do
        local server_pid=""
        local zygote_pid=""
        server_pid=$(su_exec "pidof system_server" || echo "")
        zygote_pid=$(su_exec "pidof zygote zygote64" || echo "")
        if [ -z "$server_pid" ] && [ -z "$zygote_pid" ]; then
            echo "    Framework terminated successfully."
            break
        fi
        sleep 1
    done

    echo "  [2/6] Clearing Google app data storage & wiping databases..."
    # Safe database removal while zygote/system_server are stopped
    su_exec "rm -rf /data/system/users/0/accounts_ce.db*" || true
    su_exec "rm -rf /data/system/users/0/accounts_de.db*" || true
    su_exec "rm -rf /data/system_ce/0/accounts_ce.db*" || true
    su_exec "rm -rf /data/system_de/0/accounts_de.db*" || true

    su_exec "rm -rf /data/data/com.google.android.gsf/databases/*" || true
    su_exec "rm -rf /data/data/com.google.android.gsf/shared_prefs/*" || true
    su_exec "rm -rf /data/data/com.google.android.gms/databases/phenotype*" || true
    su_exec "rm -rf /data/data/com.google.android.gms/databases/config*" || true
    su_exec "rm -rf /data/data/com.google.android.gms/shared_prefs/adid_settings.xml" || true

    # Clear cache directories to remove any remaining local state
    for pkg in com.google.android.gms \
               com.android.vending \
               com.google.android.apps.subscriptions.red \
               com.google.android.gsf \
               com.google.android.gsf.login; do
        su_exec "rm -rf /data/data/$pkg/cache/*" || true
        su_exec "rm -rf /data/data/$pkg/code_cache/*" || true
    done

    echo "  [3/6] Writing fresh identifiers to properties..."
    set_prop "persist.radio.imei"          "$NEW_IMEI"
    set_prop "persist.radio.imei1"         "$NEW_IMEI"
    set_prop "persist.radio.imei2"         "$NEW_IMEI2"
    set_prop "ro.ril.oem.imei"             "$NEW_IMEI"
    set_prop "ro.ril.oem.imei1"            "$NEW_IMEI"
    set_prop "ro.ril.oem.imei2"            "$NEW_IMEI2"
    set_prop "gsm.imei.sv"                 "09"

    set_prop "ro.serialno"                 "$NEW_SERIAL"
    set_prop "ro.boot.serialno"            "$NEW_SERIAL"
    set_prop "persist.sys.serialno"        "$NEW_SERIAL"

    set_prop "ro.boot.wifi_macaddr"        "$NEW_MAC"
    set_prop "persist.wifi.mac"            "$NEW_MAC"
    set_prop "ro.boot.btmacaddr"           "$(echo "$NEW_MAC" | awk -F: '{printf "%s:%s:%s:%s:%s:%s",$1,$2,$3,strftime("%X"),$5,$6}' 2>/dev/null || echo "$NEW_MAC")"

    set_prop "ro.boot.bootreason"          "reboot"
    set_prop "ro.runtime.firstboot"        "$(date +%s)000"

    echo "  [4/6] Starting Android Java runtime framework..."
    su_exec "start"

    echo "  [5/6] Waiting for Android runtime to boot..."
    sleep 5
    local booted=0
    for i in $(seq 1 45); do
        BOOT=$($ADB shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || echo "")
        if [ "$BOOT" = "1" ]; then
            booted=1
            break
        fi
        sleep 2
    done
    if [ "$booted" -eq 1 ]; then
        echo "    ✅ Android boot complete!"
        $ADB shell settings put secure android_id "$NEW_ANDROID_ID" 2>/dev/null || true
    else
        echo "    ⚠️ Android boot completed flag still pending."
    fi

    echo "  [6/6] Broadcasting boot completion signal..."
    $ADB shell am broadcast -a android.intent.action.BOOT_COMPLETED 2>/dev/null || true
    sleep 2

    # Verification
    echo ""
    echo "  ═══ Identity Verification ═══"
    local VERIFY_SERIAL=$($ADB shell getprop ro.serialno 2>/dev/null | tr -d '\r')
    local VERIFY_ANDROID_ID=$($ADB shell settings get secure android_id 2>/dev/null | tr -d '\r')
    local VERIFY_IMEI=$($ADB shell getprop persist.radio.imei 2>/dev/null | tr -d '\r')
    echo "    Serial:     ${VERIFY_SERIAL:-?} (expected: $NEW_SERIAL)"
    echo "    Android ID: ${VERIFY_ANDROID_ID:-?} (expected: $NEW_ANDROID_ID)"
    echo "    IMEI:       ${VERIFY_IMEI:-?} (expected: $NEW_IMEI)"

    [ "$VERIFY_SERIAL" = "$NEW_SERIAL" ] && \
    [ "$VERIFY_ANDROID_ID" = "$NEW_ANDROID_ID" ] && \
        echo "    🎯 Identity reset PERFECT" || \
        echo "    ⚠️ Properties mismatch"

    echo ""
    echo "  ✅ Device identity reset complete"
}

# ═══════════════════════════════════════════════════════════════════
#  MAIN DISPATCH
# ═══════════════════════════════════════════════════════════════════
echo "╔══════════════════════════════════════════════════╗"
echo "║  Build Props Dual-Identity Engine — $ACTION"
echo "╚══════════════════════════════════════════════════╝"

wait_boot
init_tools

case "$ACTION" in
    base)
        apply_pixel5_base
        extract_gsf
        verify_props
        ;;
    swap|swap-p10)
        swap_pixel10_pro
        ;;
    restore)
        restore_pixel5
        ;;
    reset-device)
        reset_device_identity
        ;;
    keybox)
        push_keybox
        ;;
    verify)
        verify_props
        ;;
    full)
        apply_pixel5_base
        push_keybox
        extract_gsf
        verify_props
        ;;
    fresh)
        reset_device_identity
        apply_pixel5_base
        push_keybox
        extract_gsf
        verify_props
        ;;
    *)
        echo "Usage: $0 {base|swap|restore|reset-device|keybox|verify|full|fresh} [ADB_TARGET]"
        exit 1
        ;;
esac
