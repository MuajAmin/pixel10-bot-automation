#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# fix_cpuset.sh — Initialize cpuset cgroup groups for Android init
#
# Problem: Ubuntu 24.04 defaults to cgroup v2, but Android's init
#          expects cgroup v1 cpuset paths with CPUs pre-assigned.
#          Without this, init fails with:
#            "couldn't write PID to /dev/cpuset/system-background/tasks:
#             No space left on device"
#
# Usage:   sudo bash infra/fix_cpuset.sh [container_name_or_id]
#          Run on the VPS host. Can be run in a loop or right after
#          starting the Docker container.
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

CONTAINER_NAME="${1:-pixel10-android}"

echo "═══ cpuset Initialization for ReDroid ($CONTAINER_NAME) ═══"

# 1. Ensure host cpuset is mounted (hybrid/v1)
if ! mountpoint -q /dev/cpuset 2>/dev/null; then
    echo "  Mounting host /dev/cpuset..."
    mkdir -p /dev/cpuset
    mount -t cpuset cpuset /dev/cpuset 2>/dev/null || {
        echo "  ℹ️  Standard mount to /dev/cpuset failed. Checking alternative cgroup mount..."
    }
fi

# Determine host system root cpuset configurations
HOST_CPUS=$(cat /sys/fs/cgroup/cpuset/cpuset.cpus 2>/dev/null || cat /sys/devices/system/cpu/online)
HOST_MEMS=$(cat /sys/fs/cgroup/cpuset/cpuset.mems 2>/dev/null || cat /sys/devices/system/node/online 2>/dev/null || echo "0")

echo "  Host CPUs: $HOST_CPUS"
echo "  Host Memory Nodes: $HOST_MEMS"

# 2. Locate container cgroup path
CONTAINER_ID=$(docker inspect --format '{{.Id}}' "$CONTAINER_NAME" 2>/dev/null || true)
if [ -z "$CONTAINER_ID" ]; then
    echo "  ⚠️  Container $CONTAINER_NAME not found or not running yet."
    echo "  Waiting for container to start..."
    for i in $(seq 1 30); do
        CONTAINER_ID=$(docker inspect --format '{{.Id}}' "$CONTAINER_NAME" 2>/dev/null || true)
        [ -n "$CONTAINER_ID" ] && break
        sleep 1
    done
fi

if [ -z "$CONTAINER_ID" ]; then
    echo "  ❌ Container $CONTAINER_NAME failed to start. Aborting."
    exit 1
fi

echo "  Container ID: $CONTAINER_ID"

# Locate the cpuset controller directory for this container
CONTAINER_CGROUP_PATH=""
for path in \
    "/sys/fs/cgroup/cpuset/docker/$CONTAINER_ID" \
    "/sys/fs/cgroup/cpuset/docker.slice/docker-$CONTAINER_ID.scope" \
    "/sys/fs/cgroup/docker/$CONTAINER_ID" \
    "/sys/fs/cgroup/docker.slice/docker-$CONTAINER_ID.scope" \
    "/sys/fs/cgroup/devices/docker/$CONTAINER_ID" \
    "/dev/cpuset/docker/$CONTAINER_ID"; do
    if [ -d "$path" ]; then
        CONTAINER_CGROUP_PATH="$path"
        break
    fi
done

if [ -z "$CONTAINER_CGROUP_PATH" ]; then
    # Fallback to searching the filesystem
    echo "  Searching for cgroup folder..."
    FOUND=$(find /sys/fs/cgroup/ -name "$CONTAINER_ID" -type d -print -quit 2>/dev/null || true)
    if [ -n "$FOUND" ]; then
        CONTAINER_CGROUP_PATH="$FOUND"
    fi
fi

if [ -z "$CONTAINER_CGROUP_PATH" ]; then
    echo "  ❌ Could not locate cgroup path for container $CONTAINER_ID on the host."
    exit 1
fi

echo "  Found Container Cgroup Path: $CONTAINER_CGROUP_PATH"

# Write cpuset limits to the container cgroup itself if not set
cat "$CONTAINER_CGROUP_PATH/cpuset.cpus" 2>/dev/null | grep -q "[0-9]" || {
    echo "$HOST_CPUS" > "$CONTAINER_CGROUP_PATH/cpuset.cpus" 2>/dev/null || true
}
cat "$CONTAINER_CGROUP_PATH/cpuset.mems" 2>/dev/null | grep -q "[0-9]" || {
    echo "$HOST_MEMS" > "$CONTAINER_CGROUP_PATH/cpuset.mems" 2>/dev/null || true
}

PARENT_CPUS=$(cat "$CONTAINER_CGROUP_PATH/cpuset.cpus" 2>/dev/null || echo "$HOST_CPUS")
PARENT_MEMS=$(cat "$CONTAINER_CGROUP_PATH/cpuset.mems" 2>/dev/null || echo "$HOST_MEMS")

[ -z "$PARENT_CPUS" ] && PARENT_CPUS="$HOST_CPUS"
[ -z "$PARENT_MEMS" ] && PARENT_MEMS="$HOST_MEMS"

echo "  Cgroup Parent CPUs: $PARENT_CPUS"
echo "  Cgroup Parent Mems: $PARENT_MEMS"

# 3. Create the cpuset groups inside the container's cgroup directory
CPUSET_GROUPS=("foreground" "background" "system-background" "top-app" "restricted" "camera-daemon")

for group in "${CPUSET_GROUPS[@]}"; do
    TARGET="$CONTAINER_CGROUP_PATH/$group"
    if [ ! -d "$TARGET" ]; then
        mkdir -p "$TARGET"
        echo "  Created sub-cgroup: $group"
    fi

    # Assign parent's CPUs
    echo "$PARENT_CPUS" > "$TARGET/cpuset.cpus" 2>/dev/null || echo "  ⚠️  Could not set cpuset.cpus for $group"
    # Assign parent's Mem nodes
    echo "$PARENT_MEMS" > "$TARGET/cpuset.mems" 2>/dev/null || echo "  ⚠️  Could not set cpuset.mems for $group"
done

echo "  ✅ Container cpuset groups successfully initialized and populated."
echo "  ReDroid container should boot without cpuset allocation errors."

