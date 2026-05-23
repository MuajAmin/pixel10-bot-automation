#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  VPS Full Deploy — Nuke Old Containers + Deploy Backend + Bot  ║
║                                                                 ║
║  Usage (from Windows):                                          ║
║    & "C:/Program Files/Python313/python.exe" vps_deploy.py      ║
║                                                                 ║
║  Actions:                                                       ║
║    1. SSH into VPS                                              ║
║    2. Stop & remove ALL old Docker containers + volumes          ║
║    3. Upload entire project via SFTP                            ║
║    4. docker compose up (ReDroid + worker-api)                  ║
║    5. Install pip deps + start Telegram bot as systemd service  ║
║    6. Verify everything is running                              ║
╚══════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

try:
    import paramiko
except ImportError:
    print("ERROR: paramiko is required. Run:")
    print('  pip install paramiko')
    sys.exit(1)

# Force UTF-8 on Windows
if sys.platform == "win32":
    try:
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")
        reconfigure_err = getattr(sys.stderr, "reconfigure", None)
        if reconfigure_err:
            reconfigure_err(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════

# Remote project directory on VPS
REMOTE_DIR = "/root/pixel10-bot-automation"

# Local project root (this script's parent of deploy/)
LOCAL_DIR = Path(__file__).resolve().parent.parent


def load_deploy_settings() -> dict[str, object]:
    config_path = LOCAL_DIR / "deploy.json"
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


DEPLOY_SETTINGS = load_deploy_settings()


def setting(env_name: str, json_key: str, default: str = "") -> str:
    return os.getenv(env_name) or str(DEPLOY_SETTINGS.get(json_key, default) or "")


VPS_HOST = setting("VPS_HOST", "vps_host")
VPS_PORT = int(setting("VPS_PORT", "vps_port", "22"))
VPS_USER = setting("VPS_USER", "vps_user", "root")
VPS_PASS = setting("VPS_PASS", "vps_password")
VPS_KEY_PATH = setting("VPS_KEY_PATH", "vps_key_path")
if VPS_KEY_PATH and not Path(os.path.expanduser(VPS_KEY_PATH)).exists():
    VPS_KEY_PATH = ""

# Files/dirs to EXCLUDE from upload
EXCLUDE = {
    ".git", "__pycache__", ".mypy_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", "archive",
    ".gemini", "screenshots", "pyrightconfig.json",
    "requirements-dev.txt", "deploy.json.example",
}

# Exclude large/binary files
EXCLUDE_EXTENSIONS = {".pyc", ".pyo", ".sha256", ".exe", ".dll"}


# ═══════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════

def pr(icon: str, msg: str):
    print(f"  {icon} {msg}")

def header(title: str):
    print(f"\n{'━' * 60}")
    print(f"  {title}")
    print(f"{'━' * 60}\n")


def ssh_exec(ssh: paramiko.SSHClient, cmd: str, timeout: int = 120,
             stream: bool = True, check: bool = True) -> tuple[int, str]:
    """Execute command over SSH. Returns (exit_code, stdout)."""
    print(f"  💻 $ {cmd[:120]}{'...' if len(cmd) > 120 else ''}")

    _, stdout_ch, stderr_ch = ssh.exec_command(cmd, timeout=timeout)
    out_lines = []

    for line in stdout_ch:
        line = line.rstrip()
        out_lines.append(line)
        if stream:
            print(f"      {line}")

    exit_code = stdout_ch.channel.recv_exit_status()
    stderr_text = stderr_ch.read().decode("utf-8", errors="replace").strip()

    if stderr_text and stream:
        for err_line in stderr_text.split("\n")[-3:]:
            print(f"      [err] {err_line}")

    if check and exit_code != 0:
        pr("⚠️", f"Command exited with code {exit_code}")

    return exit_code, "\n".join(out_lines)


def sftp_mkdir_p(sftp: paramiko.SFTPClient, remote_dir: str):
    """Create remote directory tree (like mkdir -p)."""
    dirs_to_create = []
    current = remote_dir
    while current and current != "/":
        try:
            sftp.stat(current)
            break
        except FileNotFoundError:
            dirs_to_create.append(current)
            current = os.path.dirname(current)

    for d in reversed(dirs_to_create):
        try:
            sftp.mkdir(d)
        except Exception:
            pass


def upload_project(sftp: paramiko.SFTPClient, local_root: Path,
                   remote_root: str) -> int:
    """Upload entire project directory via SFTP. Returns file count."""
    count = 0
    for root, dirs, files in os.walk(local_root):
        # Filter excluded directories
        dirs[:] = [d for d in dirs if d not in EXCLUDE]

        for fname in files:
            if fname in EXCLUDE:
                continue
            if any(fname.endswith(ext) for ext in EXCLUDE_EXTENSIONS):
                continue

            local_path = os.path.join(root, fname)
            rel_path = os.path.relpath(local_path, local_root)
            remote_path = f"{remote_root}/{rel_path}".replace("\\", "/")

            # Create remote directory
            remote_dir = os.path.dirname(remote_path).replace("\\", "/")
            sftp_mkdir_p(sftp, remote_dir)

            try:
                sftp.put(local_path, remote_path)
                count += 1
            except Exception as e:
                pr("⚠️", f"Upload failed: {rel_path} — {e}")

    return count


# ═══════════════════════════════════════════════════════════════════
#  MAIN DEPLOYMENT
# ═══════════════════════════════════════════════════════════════════

def main():
    print("""
╔══════════════════════════════════════════════════════════════╗
║  VPS Full Deploy — Nuke + Backend + Telegram Bot            ║
╚══════════════════════════════════════════════════════════════╝
    """)

    # ── Phase 0: Connect ──────────────────────────────────────
    header("Phase 0: Connect to VPS")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    if not VPS_HOST:
        raise SystemExit("ERROR: VPS_HOST missing. Set env vars or create deploy.json from deploy.json.example.")
    if not VPS_PASS and not VPS_KEY_PATH:
        raise SystemExit("ERROR: VPS_PASS or VPS_KEY_PATH missing. Set env vars or deploy.json before running.")

    pr("🔗", f"Connecting to {VPS_HOST}:{VPS_PORT}...")
    connect_kwargs = {
        "hostname": VPS_HOST,
        "port": VPS_PORT,
        "username": VPS_USER,
        "timeout": 30,
    }
    if VPS_KEY_PATH:
        connect_kwargs["key_filename"] = os.path.expanduser(VPS_KEY_PATH)
    else:
        connect_kwargs["password"] = VPS_PASS
    ssh.connect(**connect_kwargs)
    pr("✅", "SSH connected")

    sftp = ssh.open_sftp()

    # ── Phase 1: Nuke ALL old containers ──────────────────────
    header("Phase 1: Remove ALL Old Docker Containers")

    pr("🗑️", "Stopping all running containers...")
    ssh_exec(ssh, "docker stop $(docker ps -aq) 2>/dev/null || true",
             check=False, stream=False)

    pr("🗑️", "Removing all containers...")
    ssh_exec(ssh, "docker rm -f $(docker ps -aq) 2>/dev/null || true",
             check=False, stream=False)

    pr("🗑️", "Pruning unused images/volumes/networks...")
    ssh_exec(ssh, "docker system prune -af --volumes 2>/dev/null || true",
             check=False, stream=False)

    # Verify clean state
    _, ps_out = ssh_exec(ssh, "docker ps -a --format '{{.Names}}'",
                         stream=False, check=False)
    if ps_out.strip():
        pr("⚠️", f"Remaining containers: {ps_out.strip()}")
    else:
        pr("✅", "All containers removed — clean slate")

    # ── Phase 2: Upload project code ──────────────────────────
    header("Phase 2: Upload Project Files")

    # Host-level WireGuard cleanup (avoid conflict with Gluetun)
    pr("🌐", "Ensuring host WireGuard is disabled to avoid conflicts...")
    ssh_exec(ssh, "systemctl disable --now wg-quick@wg0 2>/dev/null || true; ip link delete dev wg0 2>/dev/null || true", check=False)
    ssh_exec(ssh, f"bash {REMOTE_DIR}/infra/scripts/network_fix.sh down 2>/dev/null || true", check=False)
    pr("🗑️", f"Removing old remote directory: {REMOTE_DIR}")
    ssh_exec(ssh, f"rm -rf {REMOTE_DIR}", stream=False)

    pr("📁", f"Creating remote directory: {REMOTE_DIR}")
    ssh_exec(ssh, f"mkdir -p {REMOTE_DIR}", stream=False)

    pr("📤", f"Uploading from {LOCAL_DIR}...")
    count = upload_project(sftp, LOCAL_DIR, REMOTE_DIR)
    pr("✅", f"Uploaded {count} files")

    # Ensure Gluetun VPN configuration is set up
    pr("🌐", "Copying proton.conf to Gluetun WireGuard runtime configs...")
    ssh_exec(
        ssh,
        f"mkdir -p {REMOTE_DIR}/infra/wireguard "
        f"&& cp {REMOTE_DIR}/config/proton.conf {REMOTE_DIR}/infra/wireguard/main.conf "
        f"&& cp {REMOTE_DIR}/config/proton.conf {REMOTE_DIR}/infra/wireguard/proton.conf "
        r"&& sed -i -E 's/(Address = [^,]+),.*/\1/; s/(DNS = [^,]+),.*/\1/' "
        f"{REMOTE_DIR}/infra/wireguard/main.conf {REMOTE_DIR}/infra/wireguard/proton.conf",
        check=True,
    )

    # Fix CRLF + permissions for all shell scripts
    pr("🔧", "Fixing CRLF line endings...")
    ssh_exec(ssh,
             f'find {REMOTE_DIR} -name "*.sh" -exec sed -i "s/\\r$//" {{}} \\; '
             f'&& find {REMOTE_DIR} -name "*.py" -exec sed -i "s/\\r$//" {{}} \\; '
             f'&& find {REMOTE_DIR} -name "*.sh" -exec chmod +x {{}} \\;',
             stream=False)
    pr("✅", "CRLF fixed + scripts executable")

    # ── Phase 3: Docker Compose — Backend ─────────────────────
    header("Phase 3: Start Backend (Host WireGuard + ReDroid + Worker API)")

    # Check if proton.conf exists
    _, proton_check = ssh_exec(ssh,
        f"test -f {REMOTE_DIR}/config/proton.conf && echo 'YES' || echo 'NO'",
        stream=False, check=False)

    if "NO" in proton_check:
        pr("⚠️", "config/proton.conf not found!")
        pr("ℹ️", "Copy your Proton VPN WireGuard config there.")
        pr("ℹ️", f"Template: {REMOTE_DIR}/config/proton.conf.example")
        pr("ℹ️", "Host WireGuard setup cannot continue without it.")

        raise SystemExit(1)

    # Create .env for docker-compose if needed
    pr("🔧", "Setting up environment...")
    ssh_exec(ssh, f"""
cat > {REMOTE_DIR}/infra/.env << 'ENVEOF'
REDROID_IMAGE=redroid/redroid:11.0.0_gapps_ndk_magisk_widevine
ENVEOF
""", stream=False)


    # Start docker compose
    pr("🐳", "Starting docker compose...")
    code, _ = ssh_exec(ssh,
        f"cd {REMOTE_DIR}/infra && docker compose up -d --build",
        timeout=300, check=False)

    if code != 0:
        pr("❌", "docker compose failed!")
        pr("ℹ️", "Checking logs...")
        ssh_exec(ssh, f"cd {REMOTE_DIR}/infra && docker compose logs --tail=20",
                 check=False)
    else:
        pr("✅", "Docker compose started")

    # Wait for containers
    pr("⏳", "Waiting for containers to stabilize (30s)...")
    code, _ = ssh_exec(ssh, """
for i in $(seq 1 60); do
  timeout 8s adb connect 127.0.0.1:5555 >/dev/null 2>&1 || true
  BOOT=$(timeout 8s adb -s 127.0.0.1:5555 shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || echo "")
  if [ "$BOOT" = "1" ]; then
    echo "Android booted ($((i * 3))s)"
    exit 0
  fi
  [ $((i % 10)) -eq 0 ] && echo "Waiting ($((i * 3))s/180s)..."
  sleep 3
done
docker logs pixel10-android --tail=40 2>&1 || true
exit 1
""", timeout=220, check=False)
    if code != 0:
        pr("ERROR", "Android did not boot or ADB did not respond")
        raise SystemExit(1)

    # Restore Gluetun routing and DNS guardrails inside the shared namespace.
    pr("🌐", "Restoring Gluetun routing and DNS guardrails...")
    ssh_exec(ssh, f"cd {REMOTE_DIR} && bash infra/fix_vpn_routing.sh", check=True)

    # Check container status
    ssh_exec(ssh, "docker ps --format 'table {{.Names}}\\t{{.Status}}\\t{{.Ports}}'")

    # ── Phase 4: Install Python deps + Start Telegram Bot ─────
    header("Phase 4: Deploy Telegram Bot")

    # Install Python deps for bot
    pr("📦", "Installing Python dependencies...")
    ssh_exec(ssh,
        f"cd {REMOTE_DIR} && pip3 install --break-system-packages -r requirements.txt 2>&1 | tail -5",
        timeout=180, check=False)

    # Create systemd service for Telegram bot
    pr("🤖", "Creating systemd service for Telegram bot...")
    ssh_exec(ssh, f"""
cat > /etc/systemd/system/pixel-bot.service << 'SVCEOF'
[Unit]
Description=Gemini Pixel Offer Claim — Telegram Bot
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
User=root
WorkingDirectory={REMOTE_DIR}
EnvironmentFile={REMOTE_DIR}/api.env
ExecStart=/usr/bin/python3 {REMOTE_DIR}/main.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=pixel-bot

# Graceful shutdown (15s for active jobs)
TimeoutStopSec=20
KillSignal=SIGINT

[Install]
WantedBy=multi-user.target
SVCEOF
""", stream=False)

    # Reload systemd + start bot
    pr("🚀", "Starting Telegram bot service...")
    ssh_exec(ssh, "systemctl daemon-reload", stream=False)
    ssh_exec(ssh, "systemctl enable pixel-bot", stream=False)
    ssh_exec(ssh, "systemctl restart pixel-bot", stream=False, check=False)

    # Wait a moment for startup
    time.sleep(5)

    # Check status
    code, status_out = ssh_exec(ssh,
        "systemctl is-active pixel-bot && systemctl status pixel-bot --no-pager -l | head -15",
        check=False)

    if code == 0:
        pr("✅", "Telegram bot is RUNNING")
    else:
        pr("⚠️", "Telegram bot may have issues")
        pr("ℹ️", "Check logs: journalctl -u pixel-bot -f")

    # ── Phase 5: Final Verification ───────────────────────────
    header("Phase 5: Final Verification")

    pr("🐳", "Docker containers:")
    ssh_exec(ssh, "docker ps --format 'table {{.Names}}\\t{{.Status}}'")

    pr("🤖", "Telegram bot:")
    ssh_exec(ssh, "systemctl is-active pixel-bot && echo 'BOT: RUNNING' || echo 'BOT: STOPPED'",
             check=False)

    pr("🌐", "Worker API health:")
    ssh_exec(ssh, "curl -s -m 5 http://127.0.0.1:8800/healthz 2>/dev/null || echo 'API: unreachable'",
             check=False)

    # Check if ADB is reachable through the direct host port
    pr("📱", "ADB status:")
    ssh_exec(ssh,
        "timeout 8s adb connect 127.0.0.1:5555 2>/dev/null; "
        "timeout 8s adb -s 127.0.0.1:5555 shell getprop ro.product.model 2>/dev/null || echo 'ADB: offline'",
        check=False, stream=True)

    # ── Done ──────────────────────────────────────────────────
    header("Deployment Complete")

    print(f"""
  📋 Summary:
     VPS:         {VPS_HOST}:{VPS_PORT}
     Project:     {REMOTE_DIR}
     Backend:     host WireGuard + docker compose (ReDroid + worker-api)
     Bot:         systemd service (pixel-bot)
     Worker API:  http://127.0.0.1:8800

  🔧 Useful commands (SSH into VPS):
     docker ps                          # Container status
     journalctl -u pixel-bot -f         # Bot logs (live)
     docker logs pixel10-worker -f      # Worker API logs
     docker logs pixel10-android -f     # ReDroid logs
     systemctl restart pixel-bot        # Restart bot
     cd {REMOTE_DIR}/infra && docker compose restart  # Restart backend

  ⚠️  Don't forget:
     1. Place config/proton.conf (VPN config) if not done
     2. Place config/keybox.xml (optional, for DEVICE_INTEGRITY)
     3. Test bot: Send /start to @BDGeminBot on Telegram
""")

    sftp.close()
    ssh.close()
    pr("✅", "SSH connection closed")


if __name__ == "__main__":
    main()
