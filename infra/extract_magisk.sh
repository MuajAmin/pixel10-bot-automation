#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# extract_magisk.sh — Download and extract Magisk x86_64 binaries
#
# Downloads the specified Magisk release APK, extracts the native
# binaries, and places them in infra/patches/magisk/ for use as
# Docker volume overlays in docker-compose.yml.
#
# Usage:   bash infra/extract_magisk.sh [VERSION]
# Example: bash infra/extract_magisk.sh v28.1
#
# After running, the following files will exist:
#   infra/patches/magisk/magisk64   (64-bit daemon + multi-call binary)
#   infra/patches/magisk/magisk32   (32-bit companion)
#
# These are mounted over /system/etc/init/magisk/ inside the ReDroid
# container via docker-compose.yml volume binds.
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

MAGISK_VERSION="${1:-v28.1}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PATCHES_DIR="$SCRIPT_DIR/patches/magisk"
WORK_DIR="$(mktemp -d)"

trap "rm -rf '$WORK_DIR'" EXIT

echo "═══ Magisk Binary Extraction ═══"
echo "  Version:    $MAGISK_VERSION"
echo "  Output dir: $PATCHES_DIR"
echo ""

# Step 1: Download
DOWNLOAD_URL="https://github.com/topjohnwu/Magisk/releases/download/${MAGISK_VERSION}/Magisk-${MAGISK_VERSION}.apk"
APK_PATH="$WORK_DIR/Magisk.apk"

echo "[1/3] Downloading Magisk ${MAGISK_VERSION}..."
if command -v curl &>/dev/null; then
    curl -sfL -o "$APK_PATH" "$DOWNLOAD_URL"
elif command -v wget &>/dev/null; then
    wget -q -O "$APK_PATH" "$DOWNLOAD_URL"
else
    echo "ERROR: Neither curl nor wget found. Install one and retry."
    exit 1
fi

if [ ! -s "$APK_PATH" ]; then
    echo "ERROR: Download failed or file is empty."
    echo "  URL: $DOWNLOAD_URL"
    exit 1
fi

echo "  ✅ Downloaded $(du -h "$APK_PATH" | cut -f1)"

# Step 2: Extract native libraries
echo "[2/3] Extracting x86_64 and x86 native binaries..."
cd "$WORK_DIR"
unzip -o Magisk.apk "lib/x86_64/*" "lib/x86/*" 2>/dev/null || {
    echo "ERROR: Failed to extract native libraries from APK."
    echo "  The APK may be corrupted or the version string may be wrong."
    exit 1
}

# Magisk bundles binaries as .so files inside the APK:
#   lib/x86_64/libmagisk64.so    → magisk64 (main daemon, multi-call)
#   lib/x86/libmagisk32.so       → magisk32 (32-bit companion)
#
# In Magisk v26+, magiskpolicy is NOT a separate binary.
# magisk64 is a multi-call binary: when invoked as "magiskpolicy"
# (via argv[0] from a symlink), it runs SELinux policy patching.

MAGISK64="$WORK_DIR/lib/x86_64/libmagisk64.so"
MAGISK32="$WORK_DIR/lib/x86/libmagisk32.so"

if [ ! -f "$MAGISK64" ]; then
    echo "ERROR: libmagisk64.so not found in APK."
    echo "  Contents of lib/:"
    find "$WORK_DIR/lib/" -type f 2>/dev/null || echo "  (empty)"
    exit 1
fi

# Step 3: Install to patches directory
echo "[3/3] Installing to $PATCHES_DIR..."
mkdir -p "$PATCHES_DIR"

cp "$MAGISK64" "$PATCHES_DIR/magisk64"
chmod 755 "$PATCHES_DIR/magisk64"
echo "  ✅ magisk64 ($(du -h "$PATCHES_DIR/magisk64" | cut -f1))"

if [ -f "$MAGISK32" ]; then
    cp "$MAGISK32" "$PATCHES_DIR/magisk32"
    chmod 755 "$PATCHES_DIR/magisk32"
    echo "  ✅ magisk32 ($(du -h "$PATCHES_DIR/magisk32" | cut -f1))"
else
    echo "  ℹ️  No 32-bit binary found (x86 support may not be needed)"
fi

echo ""
echo "═══ Extraction Complete ═══"
echo ""
echo "Files ready for docker-compose volume overlay:"
echo "  $PATCHES_DIR/magisk64"
[ -f "$PATCHES_DIR/magisk32" ] && echo "  $PATCHES_DIR/magisk32"
echo ""
echo "Next steps:"
echo "  1. Run: sudo bash infra/fix_cpuset.sh          (host cpuset init)"
echo "  2. Run: docker compose -f infra/docker-compose.yml up -d"
echo "  3. Run: bash infra/post_boot_magisk.sh          (load modules)"
