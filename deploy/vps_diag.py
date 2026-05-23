"""Passive VPS stability monitor for host WireGuard, Docker routing, and ADB.

By default this script only observes the remote stack. Use --restart when you
explicitly want it to upload docker-compose.yml and recreate the containers.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import paramiko

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


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REMOTE_DIR = "/root/pixel10-bot-automation"
DOCKER_NETWORK = "container:gluetun"


@dataclass(frozen=True)
class SshConfig:
    host: str
    port: int
    user: str
    password: str = ""
    key_path: str = ""
    remote_dir: str = DEFAULT_REMOTE_DIR


@dataclass(frozen=True)
class CommandResult:
    code: int
    stdout: str
    stderr: str
    timed_out: bool = False


def load_ssh_config() -> SshConfig:
    deploy_json = ROOT / "deploy.json"
    data: dict[str, object] = {}
    if deploy_json.exists():
        with deploy_json.open("r", encoding="utf-8") as fh:
            data = json.load(fh)

    def get(name: str, json_key: str = "", default: str = "") -> str:
        return os.getenv(name) or str(data.get(json_key or name.lower(), default) or "")

    host = get("VPS_HOST", "vps_host")
    user = get("VPS_USER", "vps_user", "root")
    password = get("VPS_PASS", "vps_password")
    key_path = get("VPS_KEY_PATH", "vps_key_path")
    if key_path and not Path(os.path.expanduser(key_path)).exists():
        key_path = ""
    remote_dir = get("REMOTE_DIR", "remote_project_dir", DEFAULT_REMOTE_DIR)
    port_raw = get("VPS_PORT", "vps_port", "22")

    if not host:
        raise SystemExit("Missing VPS_HOST. Set env vars or create deploy.json from deploy.json.example.")
    if not password and not key_path:
        raise SystemExit("Missing VPS_PASS or VPS_KEY_PATH. Set env vars or deploy.json before running.")

    return SshConfig(host, int(port_raw), user, password, key_path, remote_dir)


def connect(config: SshConfig) -> paramiko.SSHClient:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs: dict = {
        "hostname": config.host,
        "port": config.port,
        "username": config.user,
        "timeout": 20,
        "banner_timeout": 20,
        "auth_timeout": 20,
    }
    if config.key_path:
        kwargs["key_filename"] = os.path.expanduser(config.key_path)
    else:
        kwargs["password"] = config.password
    ssh.connect(**kwargs)
    transport = ssh.get_transport()
    if transport is not None:
        transport.set_keepalive(10)
    return ssh


def ssh_run(ssh: paramiko.SSHClient, cmd: str, timeout: int = 10) -> CommandResult:
    transport = ssh.get_transport()
    if transport is None or not transport.is_active():
        return CommandResult(255, "", "SSH transport is not active")

    channel = transport.open_session(timeout=10)
    channel.settimeout(1.0)
    channel.exec_command(cmd)

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    deadline = time.monotonic() + timeout
    timed_out = False

    while True:
        try:
            if channel.recv_ready():
                stdout_chunks.append(channel.recv(65535))
            if channel.recv_stderr_ready():
                stderr_chunks.append(channel.recv_stderr(65535))
        except socket.timeout:
            pass

        if channel.exit_status_ready():
            break
        if time.monotonic() >= deadline:
            timed_out = True
            channel.close()
            break
        time.sleep(0.05)

    try:
        while channel.recv_ready():
            stdout_chunks.append(channel.recv(65535))
        while channel.recv_stderr_ready():
            stderr_chunks.append(channel.recv_stderr(65535))
    except (OSError, socket.timeout):
        pass

    code = 124 if timed_out else channel.recv_exit_status()
    stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace").strip()
    stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
    return CommandResult(code, stdout, stderr, timed_out)


def bounded(cmd: str, seconds: int) -> str:
    return f"timeout {seconds}s bash -lc {shlex.quote(cmd)}"


def print_result(label: str, result: CommandResult, max_chars: int = 1200) -> None:
    print(f"\n=== {label} ===")
    if result.stdout:
        print(result.stdout[:max_chars])
    if result.stderr:
        print(f"[err] {result.stderr[:max_chars]}")
    if result.timed_out:
        print("[timeout] command exceeded client timeout")


def upload_compose_and_restart(ssh: paramiko.SSHClient, remote_dir: str) -> None:
    compose = ROOT / "infra" / "docker-compose.yml"
    remote_compose = f"{remote_dir}/infra/docker-compose.yml"

    print("\n=== Restart requested ===")
    sftp = ssh.open_sftp()
    try:
        sftp.put(str(compose), remote_compose)
    finally:
        sftp.close()

    commands = [
        ("Fix CRLF", f"sed -i 's/\\r$//' {shlex.quote(remote_compose)}"),
        ("Clean host WG", "systemctl disable --now wg-quick@wg0 2>/dev/null || true; ip link delete dev wg0 2>/dev/null || true"),
        ("Clean host routing", f"bash {shlex.quote(remote_dir)}/infra/scripts/network_fix.sh down 2>/dev/null || true"),
        ("ADB kill", "adb kill-server 2>&1 || true"),
        ("Compose down", f"cd {shlex.quote(remote_dir)}/infra && docker compose down 2>&1"),
        ("Binderfs", "mountpoint -q /dev/binderfs || mount -t binder binder /dev/binderfs 2>&1"),
        ("Compose up", f"cd {shlex.quote(remote_dir)}/infra && docker compose up -d 2>&1"),
        ("Restore Gluetun routing", f"cd {shlex.quote(remote_dir)} && bash infra/fix_vpn_routing.sh"),
    ]
    for label, cmd in commands:
        timeout = 180 if label in {"Compose up", "Restore Gluetun routing"} else 60
        print_result(label, ssh_run(ssh, cmd, timeout=timeout))


def adb_status(ssh: paramiko.SSHClient) -> tuple[str, str]:
    connect_out = ssh_run(ssh, bounded("adb connect 127.0.0.1:5555 2>&1", 8), timeout=10).stdout
    devices = ssh_run(ssh, bounded("adb devices 2>&1 | awk '/5555/{print}'", 5), timeout=8).stdout
    boot = ssh_run(
        ssh,
        bounded("adb -s 127.0.0.1:5555 shell getprop sys.boot_completed 2>/dev/null | tr -d '\\r'", 5),
        timeout=8,
    ).stdout
    ok = "device" in devices and "offline" not in devices and boot == "1"
    detail = f"{devices or connect_out or 'no-adb'} boot={boot or 'unknown'}"
    return ("OK" if ok else "FAIL", detail)


def docker_network_probe(ssh: paramiko.SSHClient) -> str:
    cmd = (
        "docker exec gluetun sh -c "
        "'getent hosts google.com >/dev/null 2>&1; "
        "printf \"dns=%s route=%s\" "
        "\"$(cat /etc/resolv.conf | awk \"/^nameserver/{print \\$2}\" | paste -sd, -)\" "
        "\"$(ip route get 10.2.0.1 2>/dev/null | head -1)\"' 2>/dev/null || true"
    )
    return ssh_run(ssh, bounded(cmd, 20), timeout=25).stdout[:80]


def monitor_once(ssh: paramiko.SSHClient, remote_dir: str) -> bool:
    vpn_status = ssh_run(ssh, "docker inspect --format='{{.State.Health.Status}}' gluetun 2>/dev/null || echo 'down'", timeout=8).stdout.strip()
    android_status = ssh_run(ssh, "docker inspect --format='{{.State.Status}}' pixel10-android 2>/dev/null || echo 'down'", timeout=8).stdout.strip()
    docker_ip = docker_network_probe(ssh)
    adb_ok, adb_detail = adb_status(ssh)

    print(f"Gluetun={vpn_status}")
    print(f"ReDroid={android_status}")
    print(f"Docker DNS route={docker_ip or 'timeout/empty'}")
    print(f"ADB={adb_ok} ({adb_detail})")

    ok = vpn_status == "healthy" and android_status == "running" and bool(docker_ip) and adb_ok == "OK"
    return ok


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, default=8.0, help="monitoring duration")
    parser.add_argument("--interval", type=float, default=30.0, help="seconds between checks")
    parser.add_argument("--restart", action="store_true", help="upload compose and recreate containers before monitoring")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_ssh_config()
    ssh = connect(config)
    checks = max(1, int((args.minutes * 60) // args.interval))
    stable = 0

    try:
        if args.restart:
            upload_compose_and_restart(ssh, config.remote_dir)
            time.sleep(20)

        print(f"=== Host WG Stability Monitor ({args.minutes:g} min) ===")
        for idx in range(checks):
            if idx:
                time.sleep(args.interval)
            elapsed = (idx + 1) * args.interval
            print(f"\n--- check {idx + 1}/{checks} at {elapsed:.0f}s ---")
            if monitor_once(ssh, config.remote_dir):
                stable += 1

        print(f"\n=== Done: {stable}/{checks} checks fully healthy ===")
        return 0 if stable == checks else 1
    finally:
        ssh.close()


if __name__ == "__main__":
    raise SystemExit(main())
