#!/usr/bin/env bash
# Unified ReDroid environment patcher.
#
# Usage:
#   bash infra/patch_env.sh [ADB_TARGET]
#
# Expected local inputs:
#   config/keybox.xml
#   infra/magisk_modules/ZygiskNext*.zip       optional; downloaded if absent
#   infra/magisk_modules/TrickyStore*.zip      optional; downloaded if absent

set -euo pipefail

ADB_TARGET="${1:-127.0.0.1:5555}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MODULES_DIR="$SCRIPT_DIR/magisk_modules"
KEYBOX_FILE="$PROJECT_DIR/config/keybox.xml"
WORK_DIR="$(mktemp -d)"
MAGISK_MODULE_ID_RE='^[A-Za-z][A-Za-z0-9._-]+$'
TRICKY_STORE_DIR="/data/adb/tricky_store"

cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

adb_cmd() {
  adb -s "$ADB_TARGET" "$@"
}

adb_root_sh() {
  local script="$1"
  printf '%s\n' "set -e" "$script" | adb_cmd shell su 0 sh -s
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

wait_for_boot() {
  echo "[0/5] Connecting to $ADB_TARGET and waiting for Android boot..."
  adb connect "$ADB_TARGET" >/dev/null 2>&1 || true

  local boot=""
  for i in $(seq 1 90); do
    if [ $((i % 5)) -eq 1 ]; then
      adb connect "$ADB_TARGET" >/dev/null 2>&1 || true
    fi

    if ! adb devices | awk -v target="$ADB_TARGET" '$1 == target && $2 == "device" {found = 1} END {exit !found}'; then
      [ $((i % 15)) -eq 0 ] && echo "  adb pending ($i/90)"
      sleep 2
      continue
    fi

    boot="$(adb_cmd shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)"
    [ "$boot" = "1" ] && break
    [ $((i % 15)) -eq 0 ] && echo "  boot pending ($i/90)"
    sleep 2
  done

  boot="$(adb_cmd shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)"
  [ "$boot" = "1" ] || die "Android did not report sys.boot_completed=1"
}

latest_release_asset() {
  local repo="$1"
  local pattern="$2"

  python3 - "$repo" "$pattern" <<'PY'
import json
import re
import sys
import urllib.request

repo, pattern = sys.argv[1], re.compile(sys.argv[2], re.I)
req = urllib.request.Request(
    f"https://api.github.com/repos/{repo}/releases/latest",
    headers={"User-Agent": "pixel10-bot-automation"},
)
with urllib.request.urlopen(req, timeout=30) as response:
    data = json.loads(response.read().decode("utf-8"))
for asset in data.get("assets", []):
    name = asset.get("name", "")
    if name.endswith(".zip") and pattern.search(name):
        print(asset["browser_download_url"])
        break
PY
}

download_if_missing() {
  local label="$1"
  local glob="$2"
  local repo="$3"
  local asset_pattern="$4"
  local fallback_url="$5"

  local existing=""
  existing="$(find "$MODULES_DIR" -maxdepth 1 -type f -iname "$glob" | sort | tail -n 1 || true)"
  if [ -n "$existing" ]; then
    echo "$existing"
    return
  fi

  mkdir -p "$MODULES_DIR"
  local url=""
  url="$(latest_release_asset "$repo" "$asset_pattern" 2>/dev/null || true)"
  [ -n "$url" ] || url="$fallback_url"

  local out="$MODULES_DIR/${label}.zip"
  echo "  downloading $label from $url" >&2
  curl -fsSL -o "$out" "$url"
  [ -s "$out" ] || die "Downloaded empty $label payload"
  echo "$out"
}

module_id_from_zip() {
  local zip="$1"
  unzip -p "$zip" module.prop 2>/dev/null \
    | tr -d '\r' \
    | awk -F= '$1 == "id" {print $2; exit}'
}

validate_module_id() {
  local id="$1"
  [[ "$id" =~ $MAGISK_MODULE_ID_RE ]] || die "Invalid Magisk module id in payload: $id"
}

validate_zip_entries() {
  local label="$1"
  local zip="$2"

  unzip -Z1 "$zip" | while IFS= read -r entry; do
    if [[ "$entry" == *$'\r'* || "$entry" == *$'\n'* ]]; then
      die "$label zip contains unsafe entry: $entry"
    fi

    case "$entry" in
      ""|/*|../*|*/../*|*"/.."|*"/../"*)
        die "$label zip contains unsafe entry: $entry"
        ;;
    esac
  done
}

install_module_payload() {
  local label="$1"
  local zip="$2"
  local id=""
  local extract_dir=""

  [ -f "$zip" ] || die "$label zip not found: $zip"
  id="$(module_id_from_zip "$zip")"
  [ -n "$id" ] || die "Could not read module id from $zip"
  validate_module_id "$id"
  validate_zip_entries "$label" "$zip"

  extract_dir="$WORK_DIR/$id"
  mkdir -p "$extract_dir"
  unzip -q -o "$zip" -d "$extract_dir"
  [ -f "$extract_dir/module.prop" ] || die "$label payload has no module.prop after extraction"
  [ -z "$(find "$extract_dir" -type l -print -quit)" ] || die "$label payload contains symlinks"

  echo "  installing $label as /data/adb/modules/$id"
  adb_root_sh "rm -rf /data/adb/modules/$id && mkdir -p /data/adb/modules/$id"
  adb_cmd push "$extract_dir/." "/data/adb/modules/$id/" >/dev/null
  adb_root_sh "chown -R root:root /data/adb/modules/$id && chmod -R 755 /data/adb/modules/$id && find /data/adb/modules/$id -type d -exec chmod 755 {} + && touch /data/adb/modules/$id/auto_mount"
}

patch_magiskpolicy_crash() {
  echo "[1/5] Installing guarded magiskpolicy wrapper for modern kernels..."

  adb_root_sh "mkdir -p /data/adb/magisk /data/adb/modules /data/adb/post-fs-data.d /data/adb/service.d"

  local magisk_bin=""
  for path in /data/adb/magisk/magisk64 /system/etc/init/magisk/magisk64 /sbin/magisk; do
    if adb_cmd shell "[ -f '$path' ] && echo yes" 2>/dev/null | grep -q yes; then
      magisk_bin="$path"
      break
    fi
  done

  if [ -z "$magisk_bin" ]; then
    echo "  magisk64 was not found; module ingestion will continue, but no policy wrapper was installed"
    return
  fi

  adb_root_sh "cp $magisk_bin /data/adb/magisk/magisk64 2>/dev/null || true; chmod 755 /data/adb/magisk/magisk64"
  adb_root_sh "if [ ! -f /data/adb/magisk/magiskpolicy.real ]; then cp /data/adb/magisk/magisk64 /data/adb/magisk/magiskpolicy.real; chmod 755 /data/adb/magisk/magiskpolicy.real; fi"

  cat > "$WORK_DIR/magiskpolicy" <<'SH'
#!/system/bin/sh
REAL=/data/adb/magisk/magiskpolicy.real
LOG=/data/adb/magisk/magiskpolicy-wrapper.log

case " $* " in
  *" --live "*|*" --magisk "*)
    if [ ! -r /sys/fs/selinux/policy ]; then
      echo "$(date) skipped live policy patch: /sys/fs/selinux/policy unreadable" >> "$LOG"
      exit 0
    fi
    ;;
esac

exec "$REAL" "$@"
SH

  adb_cmd push "$WORK_DIR/magiskpolicy" /data/local/tmp/magiskpolicy >/dev/null
  adb_root_sh "cp /data/local/tmp/magiskpolicy /data/adb/magisk/magiskpolicy && chmod 755 /data/adb/magisk/magiskpolicy && chown root:root /data/adb/magisk/magiskpolicy && ln -sf /data/adb/magisk/magisk64 /data/adb/magisk/magisk && ln -sf /data/adb/magisk/magisk64 /data/adb/magisk/resetprop && ln -sf /data/adb/magisk/magisk64 /data/adb/magisk/su && rm -f /data/local/tmp/magiskpolicy"

  adb_root_sh "/data/adb/magisk/magiskpolicy --live --magisk >/dev/null 2>&1 || true"
}

deploy_modules() {
  echo "[2/5] Ingesting Zygisk Next and TrickyStore into /data/adb/modules..."

  local zygisk_zip=""
  local tricky_zip=""

  zygisk_zip="$(download_if_missing \
    "ZygiskNext-latest" \
    "*zygisk*.zip" \
    "Dr-TSNG/ZygiskNext" \
    "zygisk.*release|zygisk.*next" \
    "https://github.com/Dr-TSNG/ZygiskNext/releases/latest/download/Zygisk-Next-1.3.4-746-d1b76b3-release.zip")"

  tricky_zip="$(download_if_missing \
    "TrickyStore-latest" \
    "*tricky*.zip" \
    "5ec1cff/TrickyStore" \
    "tricky" \
    "https://github.com/5ec1cff/TrickyStore/releases/latest/download/TrickyStore.zip")"

  install_module_payload "Zygisk Next" "$zygisk_zip"
  install_module_payload "TrickyStore" "$tricky_zip"
}

lock_module_permissions() {
  echo "[3/5] Applying tight module directory permissions..."
  adb_root_sh "mkdir -p /data/adb/modules && chown root:root /data/adb/modules && chmod 755 /data/adb/modules && for module in /data/adb/modules/*; do [ -d \"\$module\" ] || continue; chown -R root:root \"\$module\"; chmod -R 755 \"\$module\"; find \"\$module\" -type d -exec chmod 755 {} +; done"
}

deploy_keybox() {
  echo "[4/5] Deploying local keybox.xml to TrickyStore runtime path..."

  [ -f "$KEYBOX_FILE" ] || die "Missing required local asset: $KEYBOX_FILE"
  [ -s "$KEYBOX_FILE" ] || die "keybox.xml exists but is empty: $KEYBOX_FILE"

  sed -i 's/\r$//' "$KEYBOX_FILE"
  adb_cmd push "$KEYBOX_FILE" /data/local/tmp/keybox.xml >/dev/null
  adb_root_sh "mkdir -p $TRICKY_STORE_DIR && cp /data/local/tmp/keybox.xml $TRICKY_STORE_DIR/keybox.xml && chown root:root $TRICKY_STORE_DIR $TRICKY_STORE_DIR/keybox.xml && chmod 755 $TRICKY_STORE_DIR && chmod 600 $TRICKY_STORE_DIR/keybox.xml && rm -f /data/local/tmp/keybox.xml"

  local keybox_perm=""
  keybox_perm="$(adb_root_sh "stat -c '%a' $TRICKY_STORE_DIR/keybox.xml" | tr -d '\r' || true)"
  [ "$keybox_perm" = "600" ] || die "keybox.xml permission mismatch at $TRICKY_STORE_DIR/keybox.xml: ${keybox_perm:-missing}"
}

restart_framework() {
  echo "[5/5] Restarting Android framework to pick up module changes..."
  if ! adb_root_sh "setprop ctl.restart zygote" >/dev/null 2>&1; then
    adb_root_sh "stop; sleep 2; start" >/dev/null
  fi

  echo "  waiting for framework to return..."
  for i in $(seq 1 45); do
    local boot=""
    boot="$(adb_cmd shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)"
    [ "$boot" = "1" ] && return
    [ $((i % 10)) -eq 0 ] && echo "  framework pending ($i/45)"
    sleep 2
  done

  die "Android framework did not report sys.boot_completed=1 after reload"
}

verify_install() {
  echo
  echo "Verification:"
  adb_root_sh "ls -ld /data/adb/modules /data/adb/modules/* 2>/dev/null || true"
  adb_root_sh "ls -ld $TRICKY_STORE_DIR 2>/dev/null || true"
  adb_root_sh "ls -l $TRICKY_STORE_DIR/keybox.xml 2>/dev/null || true"
}

need_cmd adb
need_cmd curl
need_cmd python3
need_cmd unzip

echo "ReDroid environment patch"
echo "  target:    $ADB_TARGET"
echo "  workspace: $PROJECT_DIR"

wait_for_boot
patch_magiskpolicy_crash
deploy_modules
lock_module_permissions
deploy_keybox
restart_framework
verify_install

echo "Done."
