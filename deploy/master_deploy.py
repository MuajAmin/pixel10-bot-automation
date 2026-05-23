#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════════════════════╗
║  Gemini Pixel Offer Claim Bot — Zero-Touch Master Deployment Orchestrator     ║
║                                                                               ║
║  Run this from your LOCAL machine. It SSHes into your VPS and                 ║
║  deploys the entire deep-spoofing pipeline automatically.                     ║
║                                                                               ║
║  Usage:                                                                       ║
║    python master_deploy.py                                                    ║
║    python master_deploy.py --config deploy.json                               ║
║    python master_deploy.py --host 1.2.3.4 --user root                         ║
║                                                                               ║
║  Phases:                                                                      ║
║    0. Connect & validate VPS                                                  ║
║    1. Install system dependencies (Docker, kernel modules)                    ║
║    2. Build custom ReDroid image (GApps + Magisk + NDK)                       ║
║    3. Upload infrastructure files via SFTP                                    ║
║    4. Start containers (docker compose up)                                    ║
║    5. Fix VPN/Docker routing (network_fix.sh)                                 ║
║    6. Wait for Android boot + run deep spoofing                               ║
║    7. Interactive GSF ID registration pause                                   ║
║    8. Finalize: restart, re-verify, scorecard                                 ║
╚═══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import getpass
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import paramiko
    from paramiko import SSHClient, WarningPolicy, SFTPClient
except ImportError:
    print("ERROR: paramiko is required. Install it:")
    print("  pip install paramiko")
    sys.exit(1)

# Force UTF-8 output on Windows (cp1252 can't render box-drawing chars)
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
#  Configuration
# ═══════════════════════════════════════════════════════════════════

@dataclass
class DeployConfig:
    """Deployment configuration — loaded from CLI args, JSON, or prompts."""

    # VPS connection
    vps_host: str = ""
    vps_port: int = 22
    vps_user: str = "root"
    vps_password: str = ""
    vps_key_path: str = ""  # Path to SSH private key (optional)

    # Remote paths
    remote_project_dir: str = "/root/pixel10-bot-automation"
    remote_redroid_build_dir: str = "/opt/redroid-setup"

    # ReDroid build options
    redroid_android_version: str = "11.0.0"
    redroid_build_flags: str = "-gmnw"  # g=GApps, m=Magisk, n=NDK, w=Widevine

    # Worker API
    worker_api_key: str = ""

    # WireGuard config (local path to .conf file)
    wireguard_conf_path: str = "infra/wireguard/main.conf"

    # Timeouts
    boot_timeout_sec: int = 300     # Max wait for Android boot
    build_timeout_sec: int = 1200   # Max wait for ReDroid build (20 min)
    cmd_timeout_sec: int = 120      # Default command timeout

    # Skip flags (for partial re-runs)
    skip_system_setup: bool = False
    skip_redroid_build: bool = False
    skip_vpn_setup: bool = False
    skip_magisk_modules: bool = False
    non_interactive: bool = False  # Skip GSF ID pause (for background runs)

    # Derived
    adb_target: str = "localhost:5555"
    container_name: str = "pixel10-android"


# ═══════════════════════════════════════════════════════════════════
#  Console UI helpers
# ═══════════════════════════════════════════════════════════════════

class UI:
    """Colored console output."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    DIM = "\033[2m"

    @staticmethod
    def banner():
        print(f"""
{UI.CYAN}{UI.BOLD}╔══════════════════════════════════════════════════════════════╗
║  Gemini Pixel Offer Claim Bot — Zero-Touch Master Deployment                 ║
║  Deep Spoofing • Magisk • PIF • VPN Routing                  ║
╚══════════════════════════════════════════════════════════════╝{UI.RESET}
""")

    @staticmethod
    def phase(num: int, title: str):
        print(f"\n{UI.BOLD}{UI.BLUE}{'━' * 60}")
        print(f"  Phase {num}: {title}")
        print(f"{'━' * 60}{UI.RESET}\n")

    @staticmethod
    def step(msg: str):
        print(f"  {UI.CYAN}▸{UI.RESET} {msg}")

    @staticmethod
    def ok(msg: str):
        print(f"  {UI.GREEN}✅ {msg}{UI.RESET}")

    @staticmethod
    def warn(msg: str):
        print(f"  {UI.YELLOW}⚠️  {msg}{UI.RESET}")

    @staticmethod
    def error(msg: str):
        print(f"  {UI.RED}❌ {msg}{UI.RESET}")

    @staticmethod
    def info(msg: str):
        print(f"  {UI.DIM}ℹ️  {msg}{UI.RESET}")

    @staticmethod
    def cmd_output(line: str):
        print(f"      {UI.DIM}{line.rstrip()}{UI.RESET}")


# ═══════════════════════════════════════════════════════════════════
#  SSH/SFTP Transport Layer
# ═══════════════════════════════════════════════════════════════════

class VPSConnection:
    """Manages SSH + SFTP connection to the VPS."""

    def __init__(self, config: DeployConfig):
        self.config = config
        self.ssh: Optional[SSHClient] = None
        self.sftp: Optional[SFTPClient] = None

    def connect(self) -> None:
        """Establish SSH connection to VPS."""
        UI.step(f"Connecting to {self.config.vps_host}:{self.config.vps_port} as {self.config.vps_user}...")

        self.ssh = SSHClient()
        self.ssh.set_missing_host_key_policy(WarningPolicy())

        connect_kwargs: dict = {
            "hostname": self.config.vps_host,
            "port": self.config.vps_port,
            "username": self.config.vps_user,
            "timeout": 30,
            "banner_timeout": 30,
            "auth_timeout": 30,
        }

        # Auth: key file > password > agent
        if self.config.vps_key_path and os.path.isfile(self.config.vps_key_path):
            connect_kwargs["key_filename"] = self.config.vps_key_path
            UI.info(f"Using SSH key: {self.config.vps_key_path}")
        elif self.config.vps_password:
            connect_kwargs["password"] = self.config.vps_password
            UI.info("Using password authentication")
        else:
            connect_kwargs["allow_agent"] = True
            connect_kwargs["look_for_keys"] = True
            UI.info("Using SSH agent / default keys")

        try:
            self.ssh.connect(**connect_kwargs)
            UI.ok(f"Connected to {self.config.vps_host}")
        except paramiko.AuthenticationException:
            UI.error("Authentication failed. Check credentials.")
            raise
        except socket.timeout:
            UI.error(f"Connection timed out. Is {self.config.vps_host}:{self.config.vps_port} reachable?")
            raise
        except Exception as e:
            UI.error(f"SSH connection failed: {e}")
            raise

        # Open SFTP
        self.sftp = self.ssh.open_sftp()

    def close(self) -> None:
        """Clean up connections."""
        if self.sftp:
            self.sftp.close()
        if self.ssh:
            self.ssh.close()

    def exec(
        self,
        cmd: str,
        timeout: int = 0,
        stream: bool = True,
        sudo: bool = False,
        check: bool = True,
    ) -> tuple[int, str, str]:
        """Execute a command over SSH.

        Args:
            cmd: Shell command to execute.
            timeout: Timeout in seconds (0 = use config default).
            stream: If True, print stdout/stderr in real-time.
            sudo: If True, prepend sudo (with password if configured).
            check: If True, raise on non-zero exit code.

        Returns:
            (exit_code, stdout, stderr)
        """
        if not self.ssh:
            raise RuntimeError("Not connected")

        if timeout <= 0:
            timeout = self.config.cmd_timeout_sec

        if sudo and self.config.vps_user != "root":
            if self.config.vps_password:
                cmd = f"echo '{self.config.vps_password}' | sudo -S bash -c '{cmd}'"
                # NOTE: Password appears in cmd string but is NOT logged
                # thanks to the truncation on line below.
            else:
                cmd = f"sudo bash -c '{cmd}'"

        UI.info(f"$ {cmd[:120]}{'...' if len(cmd) > 120 else ''}")

        stdin_ch, stdout_ch, stderr_ch = self.ssh.exec_command(
            cmd, timeout=timeout, get_pty=True
        )

        # Stream output
        stdout_lines = []
        stderr_lines = []

        stdout_ch.channel.setblocking(0)
        stderr_ch.channel.setblocking(0)

        start = time.time()
        while not stdout_ch.channel.exit_status_ready():
            if time.time() - start > timeout:
                stdout_ch.channel.close()
                UI.warn(f"Command timed out after {timeout}s")
                break

            # Read available stdout
            while stdout_ch.channel.recv_ready():
                chunk = stdout_ch.channel.recv(4096).decode("utf-8", errors="replace")
                for line in chunk.splitlines():
                    stdout_lines.append(line)
                    if stream:
                        UI.cmd_output(line)

            # Read available stderr
            while stdout_ch.channel.recv_stderr_ready():
                chunk = stdout_ch.channel.recv_stderr(4096).decode("utf-8", errors="replace")
                for line in chunk.splitlines():
                    stderr_lines.append(line)
                    if stream:
                        UI.cmd_output(f"[stderr] {line}")

            time.sleep(0.1)

        # Drain remaining output
        for line in stdout_ch.read().decode("utf-8", errors="replace").splitlines():
            stdout_lines.append(line)
            if stream:
                UI.cmd_output(line)
        for line in stderr_ch.read().decode("utf-8", errors="replace").splitlines():
            stderr_lines.append(line)

        exit_code = stdout_ch.channel.recv_exit_status()
        stdout_str = "\n".join(stdout_lines)
        stderr_str = "\n".join(stderr_lines)

        if check and exit_code != 0:
            UI.warn(f"Command exited with code {exit_code}")

        return exit_code, stdout_str, stderr_str

    def exec_quiet(self, cmd: str, timeout: int = 30) -> tuple[int, str]:
        """Execute without streaming. Returns (exit_code, stdout)."""
        code, out, _ = self.exec(cmd, timeout=timeout, stream=False, check=False)
        return code, out.strip()

    def upload_file(self, local_path: str, remote_path: str) -> None:
        """Upload a single file via SFTP."""
        if not self.sftp:
            raise RuntimeError("SFTP not connected")

        # Ensure remote directory exists
        remote_dir = os.path.dirname(remote_path)
        self._mkdir_p(remote_dir)

        self.sftp.put(local_path, remote_path)

    def upload_dir(self, local_dir: str, remote_dir: str, exclude: set | None = None) -> int:
        """Recursively upload a directory. Returns file count."""
        if not self.sftp:
            raise RuntimeError("SFTP not connected")

        exclude = exclude or set()
        count = 0

        for root, dirs, files in os.walk(local_dir):
            # Skip excluded dirs
            dirs[:] = [d for d in dirs if d not in exclude]

            for fname in files:
                if fname in exclude:
                    continue
                # Skip .sha256 files, __pycache__, .git
                if fname.endswith(".sha256") or fname.endswith(".pyc"):
                    continue

                local_path = os.path.join(root, fname)
                rel_path = os.path.relpath(local_path, local_dir)
                remote_path = f"{remote_dir}/{rel_path}".replace("\\", "/")

                self.upload_file(local_path, remote_path)
                count += 1

        return count

    def _mkdir_p(self, remote_dir: str) -> None:
        """Create remote directory tree (like mkdir -p)."""
        if not self.sftp:
            return
        dirs_to_create = []
        current = remote_dir
        while current and current != "/":
            try:
                self.sftp.stat(current)
                break
            except FileNotFoundError:
                dirs_to_create.append(current)
                current = os.path.dirname(current)

        for d in reversed(dirs_to_create):
            try:
                self.sftp.mkdir(d)
            except Exception:
                pass  # May already exist from parallel creates

    def file_exists(self, remote_path: str) -> bool:
        """Check if a remote file exists."""
        try:
            if self.sftp:
                self.sftp.stat(remote_path)
            return True
        except FileNotFoundError:
            return False


# ═══════════════════════════════════════════════════════════════════
#  Deployment Phases
# ═══════════════════════════════════════════════════════════════════

class MasterDeployer:
    """Orchestrates the full deployment pipeline."""

    def __init__(self, config: DeployConfig):
        self.config = config
        self.conn = VPSConnection(config)
        self.project_root = Path(__file__).parent.parent.resolve()  # deploy/ → project root
        self.infra_dir = self.project_root / "infra"
        self.remote_dir = config.remote_project_dir
        self.redroid_image = ""  # Set during build phase

    def run(self) -> None:
        """Execute all deployment phases."""
        UI.banner()
        try:
            self.phase_0_connect()
            self.phase_1_system_setup()
            self.phase_2_build_redroid()
            self.phase_3_upload_files()
            self.phase_4_start_containers()
            self.phase_5_vpn_routing()
            self.phase_6_spoofing_setup()
            self.phase_7_gsf_registration()
            self.phase_8_finalize()
        except KeyboardInterrupt:
            print(f"\n{UI.YELLOW}Deployment interrupted by user.{UI.RESET}")
        except Exception as e:
            UI.error(f"Deployment failed: {e}")
            raise
        finally:
            self.conn.close()

    # ── Phase 0: Connect & Validate ────────────────────────────────
    def phase_0_connect(self) -> None:
        UI.phase(0, "Connect & Validate VPS")
        self.conn.connect()

        # Check OS
        _, os_info = self.conn.exec_quiet("cat /etc/os-release | head -3")
        UI.info(f"OS: {os_info.splitlines()[0] if os_info else 'unknown'}")

        # Check available resources
        _, mem = self.conn.exec_quiet("free -h | grep Mem | awk '{print $2}'")
        _, disk = self.conn.exec_quiet("df -h / | tail -1 | awk '{print $4}'")
        _, cpu_count = self.conn.exec_quiet("nproc")
        UI.info(f"Resources: {cpu_count} CPUs, {mem} RAM, {disk} disk free")

        # Check kernel version (binder needs 5.4+)
        _, kernel = self.conn.exec_quiet("uname -r")
        UI.info(f"Kernel: {kernel}")

        UI.ok("VPS validation complete")

    # ── Phase 1: System Dependencies ───────────────────────────────
    def phase_1_system_setup(self) -> None:
        UI.phase(1, "System Dependencies (Docker, Kernel Modules, Tools)")

        if self.config.skip_system_setup:
            UI.warn("Skipped (--skip-system-setup)")
            return

        # Check if Docker is already installed
        code, _ = self.conn.exec_quiet("docker --version")
        if code == 0:
            UI.ok("Docker already installed")
        else:
            UI.step("Installing Docker...")
            self.conn.exec(
                "apt-get update -qq && curl -fsSL https://get.docker.com | sh",
                timeout=300, sudo=True,
            )
            self.conn.exec(
                "systemctl enable docker && systemctl start docker",
                sudo=True,
            )
            UI.ok("Docker installed")

        # Docker Compose plugin
        code, _ = self.conn.exec_quiet("docker compose version")
        if code == 0:
            UI.ok("Docker Compose plugin available")
        else:
            UI.step("Installing Docker Compose plugin...")
            self.conn.exec(
                "apt-get install -y -qq docker-compose-plugin",
                sudo=True, timeout=120,
            )

        # Kernel modules
        UI.step("Loading kernel modules (binder, ashmem)...")
        self.conn.exec(
            "apt-get install -y -qq linux-modules-extra-$(uname -r) 2>/dev/null || true",
            sudo=True, timeout=120, check=False,
        )
        self.conn.exec(
            "modprobe binder_linux devices='binder,hwbinder,vndbinder' 2>/dev/null || true"
            " && modprobe ashmem_linux 2>/dev/null || true",
            sudo=True, check=False,
        )

        # Persist modules
        self.conn.exec(
            "echo 'binder_linux' > /etc/modules-load.d/redroid.conf"
            " && echo 'ashmem_linux' >> /etc/modules-load.d/redroid.conf"
            " && echo 'options binder_linux devices=\"binder,hwbinder,vndbinder\"' > /etc/modprobe.d/redroid.conf",
            sudo=True, check=False,
        )

        # Verify binder
        code, _ = self.conn.exec_quiet("lsmod | grep binder_linux")
        if code == 0:
            UI.ok("binder_linux loaded")
        else:
            UI.warn("binder_linux not loaded — may need reboot or kernel upgrade")

        # Install tools
        UI.step("Installing ADB, Python, Git, tools...")
        self.conn.exec(
            "apt-get install -y -qq adb python3 python3-pip python3-venv git lzip unzip wget curl sqlite3",
            sudo=True, timeout=180,
        )

        # Install WireGuard
        code, _ = self.conn.exec_quiet("which wg")
        if code == 0:
            UI.ok("WireGuard already installed")
        else:
            UI.step("Installing WireGuard...")
            self.conn.exec(
                "apt-get install -y -qq wireguard wireguard-tools",
                sudo=True, timeout=120,
            )
            UI.ok("WireGuard installed")

        UI.ok("System dependencies ready")

    # ── Phase 2: Build ReDroid Image ───────────────────────────────
    def phase_2_build_redroid(self) -> None:
        UI.phase(2, "Build Custom ReDroid Image (GApps + Magisk + NDK)")

        if self.config.skip_redroid_build:
            UI.warn("Skipped (--skip-redroid-build)")
            # Try to detect existing image
            code, images = self.conn.exec_quiet(
                "docker images --format '{{.Repository}}:{{.Tag}}' | grep redroid | head -1"
            )
            if code == 0 and images.strip():
                self.redroid_image = images.strip()
                UI.ok(f"Using existing image: {self.redroid_image}")
            else:
                UI.error("No ReDroid image found and build was skipped!")
                UI.info("Remove --skip-redroid-build or set REDROID_IMAGE in .env")
                raise RuntimeError("No ReDroid image available")
            return

        build_dir = self.config.remote_redroid_build_dir

        # Clone redroid-script if needed
        code, _ = self.conn.exec_quiet(f"test -d {build_dir}/redroid-script")
        if code != 0:
            UI.step("Cloning redroid-script...")
            self.conn.exec(
                f"mkdir -p {build_dir} && cd {build_dir}"
                " && git clone https://github.com/ayasa520/redroid-script.git",
                sudo=True, timeout=120,
            )

        # Install redroid-script dependencies
        UI.step("Installing build dependencies...")
        self.conn.exec(
            f"cd {build_dir}/redroid-script"
            " && python3 -m venv venv 2>/dev/null || true"
            " && . venv/bin/activate"
            " && pip install -r requirements.txt 2>/dev/null || pip3 install -r requirements.txt",
            sudo=True, timeout=120,
        )

        # Build the image
        ver = self.config.redroid_android_version
        flags = self.config.redroid_build_flags
        UI.step(f"Building ReDroid {ver} with flags: {flags}")
        UI.info("This takes 5-15 minutes on first run...")

        code, stdout, _ = self.conn.exec(
            f"cd {build_dir}/redroid-script"
            f" && . venv/bin/activate"
            f" && python3 redroid.py -a {ver} {flags}",
            sudo=True, timeout=self.config.build_timeout_sec,
            check=False,
        )

        if code != 0:
            UI.warn("redroid-script build returned non-zero. Checking for image anyway...")

        # Find the built image
        code, images = self.conn.exec_quiet(
            "docker images --format '{{.Repository}}:{{.Tag}}' | grep redroid | head -1"
        )
        if code == 0 and images.strip():
            self.redroid_image = images.strip()
            UI.ok(f"ReDroid image ready: {self.redroid_image}")
        else:
            UI.error("No ReDroid image found after build!")
            raise RuntimeError("ReDroid build failed — no image produced")

    # ── Phase 3: Upload Files ──────────────────────────────────────
    def phase_3_upload_files(self) -> None:
        UI.phase(3, "Upload Infrastructure Files")

        remote = self.remote_dir
        infra = self.infra_dir

        # Create remote directory structure
        UI.step("Creating remote directory structure...")
        self.conn.exec(
            f"mkdir -p {remote}/infra/scripts {remote}/infra/identity {remote}/infra/wireguard {remote}/infra/.module_cache"
            f" {remote}/magisk_module {remote}/bot {remote}/screenshots {remote}/logs",
            sudo=True,
        )

        # ── Upload infra files ──
        upload_map = {
            # Infra root files
            str(infra / "docker-compose.yml"): f"{remote}/infra/docker-compose.yml",
            str(infra / "Dockerfile"): f"{remote}/infra/Dockerfile",
            str(infra / "requirements-android.txt"): f"{remote}/infra/requirements-android.txt",
            str(infra / "infra.env.example"): f"{remote}/infra/infra.env.example",
            # Infra scripts
            str(infra / "scripts/network_fix.sh"): f"{remote}/infra/scripts/network_fix.sh",
            str(infra / "scripts/post_boot_setup.sh"): f"{remote}/infra/scripts/post_boot_setup.sh",
            str(infra / "scripts/setup_protonvpn.sh"): f"{remote}/infra/scripts/setup_protonvpn.sh",
            str(infra / "scripts/setup_vps.sh"): f"{remote}/infra/scripts/setup_vps.sh",
            str(infra / "scripts/harden_device.sh"): f"{remote}/infra/scripts/harden_device.sh",
            str(infra / "scripts/setup_magisk_modules.sh"): f"{remote}/infra/scripts/setup_magisk_modules.sh",
            str(infra / "scripts/install_blazer_module.sh"): f"{remote}/infra/scripts/install_blazer_module.sh",
            # Identity and guides
            str(infra / "identity/pif.json"): f"{remote}/infra/identity/pif.json",
            str(infra / "identity/pixel_props.txt"): f"{remote}/infra/identity/pixel_props.txt",
        }

        # Post-boot props (if exists)
        pbs = infra / "scripts/post_boot_props.sh"
        if pbs.exists():
            upload_map[str(pbs)] = f"{remote}/infra/scripts/post_boot_props.sh"

        count = 0
        for local_path, remote_path in upload_map.items():
            if os.path.isfile(local_path):
                self.conn.upload_file(local_path, remote_path)
                count += 1
            else:
                UI.warn(f"Skipping (not found): {os.path.basename(local_path)}")

        UI.ok(f"Uploaded {count} infra files")

        # ── Upload WireGuard config ──
        wg_local = str(self.project_root / self.config.wireguard_conf_path)
        if os.path.isfile(wg_local):
            self.conn.upload_file(wg_local, f"{remote}/infra/wireguard/wg0.conf")
            UI.ok(f"WireGuard config uploaded: {self.config.wireguard_conf_path}")
        else:
            UI.warn(f"WireGuard config not found: {wg_local}")
            UI.info("VPN routing will not be configured")

        # ── Upload magisk_module directory ──
        mm_dir = str(self.project_root / "magisk_module")
        if os.path.isdir(mm_dir):
            mm_count = self.conn.upload_dir(mm_dir, f"{remote}/magisk_module")
            UI.ok(f"Uploaded {mm_count} magisk_module files")
        else:
            UI.warn("magisk_module/ directory not found")

        # ── Upload bot code (for worker-api Docker build) ──
        bot_dir = str(self.project_root / "bot")
        if os.path.isdir(bot_dir):
            bot_count = self.conn.upload_dir(
                bot_dir, f"{remote}/bot",
                exclude={"__pycache__", ".pyc", "screenshots"},
            )
            UI.ok(f"Uploaded {bot_count} bot files")

        # ── Generate .env file ──
        UI.step("Generating .env file...")
        image_name = self.redroid_image or "CHANGE_ME"
        api_key = self.config.worker_api_key or self._generate_api_key()

        env_content = (
            f"REDROID_IMAGE={image_name}\n"
            f"ANDROID_WORKER_API_KEY={api_key}\n"
            f"WORKER_API_BIND=127.0.0.1\n"
        )

        # Write .env to a temp file, then upload
        env_tmp = str(self.project_root / ".env.deploy.tmp")
        with open(env_tmp, "w") as f:
            f.write(env_content)
        self.conn.upload_file(env_tmp, f"{remote}/infra/.env")
        os.remove(env_tmp)
        UI.ok(f".env generated (image={image_name})")

        # Make scripts executable
        self.conn.exec(
            f"chmod +x {remote}/infra/scripts/*.sh",
            sudo=True,
        )

        UI.ok("All files uploaded and configured")

    # ── Phase 4: Start Containers ──────────────────────────────────
    def phase_4_start_containers(self) -> None:
        UI.phase(4, "Start Docker Containers")

        remote_infra = f"{self.remote_dir}/infra"

        # Stop existing containers
        UI.step("Stopping any existing containers...")
        self.conn.exec(
            f"cd {remote_infra} && docker compose down 2>/dev/null || true",
            sudo=True, check=False,
        )

        # Start containers
        UI.step("Starting containers (docker compose up -d)...")
        code, stdout, stderr = self.conn.exec(
            f"cd {remote_infra} && docker compose up -d",
            sudo=True, timeout=180,
            check=False,
        )

        if code != 0:
            UI.error("docker compose up failed!")
            UI.info("Check .env file and docker-compose.yml")
            # Show logs
            self.conn.exec(
                f"cd {remote_infra} && docker compose logs --tail=20",
                sudo=True, check=False,
            )
            raise RuntimeError("Container startup failed")

        UI.ok("Containers started")

        # Wait for ReDroid to boot
        UI.step(f"Waiting for Android boot (timeout: {self.config.boot_timeout_sec}s)...")
        booted = self._wait_for_boot()
        if booted:
            UI.ok("Android booted successfully")
        else:
            UI.error(f"Android did not boot within {self.config.boot_timeout_sec}s")
            UI.info("Check: docker logs pixel10-android --tail=30")
            raise RuntimeError("Android boot timeout")

    # ── Phase 5: VPN Routing ───────────────────────────────────────
    def phase_5_vpn_routing(self) -> None:
        UI.phase(5, "VPN/Docker Network Routing")

        if self.config.skip_vpn_setup:
            UI.warn("Skipped (--skip-vpn-setup)")
            return

        remote_infra = f"{self.remote_dir}/infra"

        # Check if WireGuard config exists on VPS
        wg_exists = self.conn.file_exists(f"{remote_infra}/wireguard/wg0.conf")
        if not wg_exists:
            UI.warn("No WireGuard config found on VPS — skipping VPN setup")
            UI.info("Upload a .conf file to infra/wireguard/wg0.conf")
            return

        # Run ProtonVPN setup (installs WG, configures split-tunnel)
        UI.step("Configuring ProtonVPN WireGuard...")
        self.conn.exec(
            f"cd {remote_infra} && bash scripts/setup_protonvpn.sh ./wireguard/wg0.conf",
            sudo=True, timeout=120,
            check=False,
        )

        # Run network fix (policy routing + DNS guardrails)
        UI.step("Applying VPN/Docker policy routing and DNS guardrails...")
        self.conn.exec(
            f"cd {self.remote_dir} && bash infra/fix_vpn_routing.sh",
            sudo=True, timeout=60,
        )

        # Verify routing without disclosing the VPS public IP to an external service.
        UI.step("Verifying container DNS and route boundary...")
        _, dns1 = self.conn.exec_quiet(f"adb -s {self.config.adb_target} shell getprop net.dns1")
        _, dns_route = self.conn.exec_quiet(f"adb -s {self.config.adb_target} shell ip route get 10.2.0.1")
        _, default_route = self.conn.exec_quiet(f"adb -s {self.config.adb_target} shell ip route get 203.0.113.10")
        UI.info(f"DNS:           {dns1.strip() or 'UNKNOWN'}")
        UI.info(f"DNS route:     {dns_route.strip() or 'UNKNOWN'}")
        UI.info(f"Default route: {default_route.strip() or 'UNKNOWN'}")
        route_text = default_route.strip()
        route_uses_vpn = re.search(r"\bdev\s+(tun|wg)[A-Za-z0-9_.:-]*\b", route_text)
        route_uses_eth = re.search(r"\bdev\s+eth[0-9_.:-]*\b", route_text)
        route_blocked = "unreachable" in route_text.lower() or "prohibit" in route_text.lower()
        if dns1.strip() == "10.2.0.1" and route_uses_vpn and not route_uses_eth and not route_blocked:
            UI.ok("VPN routing guardrails active")
        else:
            UI.warn("VPN routing guardrails need review")

    # ── Phase 6: Deep Spoofing Setup ───────────────────────────────
    def phase_6_spoofing_setup(self) -> None:
        UI.phase(6, "Deep Spoofing (Magisk + PIF + resetprop)")

        remote_infra = f"{self.remote_dir}/infra"

        # Ensure ADB is connected
        UI.step("Connecting ADB...")
        self.conn.exec(
            f"adb connect {self.config.adb_target}",
            check=False, timeout=30,
        )
        time.sleep(3)

        # Install Blazer_Props Magisk module first
        UI.step("Installing Blazer_Props Magisk module...")
        self.conn.exec(
            f"cd {remote_infra} && bash scripts/install_blazer_module.sh {self.config.adb_target}",
            sudo=True, timeout=120,
            check=False,
        )

        # Run the unified post-boot setup
        UI.step("Running unified post_boot_setup.sh...")
        UI.info("This installs PIF, Shamiko, applies resetprop overrides, and verifies...")
        self.conn.exec(
            f"cd {remote_infra} && bash scripts/post_boot_setup.sh {self.config.adb_target}",
            sudo=True, timeout=300,
        )

        UI.ok("Deep spoofing setup complete")

    # ── Phase 7: GSF ID Registration (Interactive) ─────────────────
    def phase_7_gsf_registration(self) -> None:
        UI.phase(7, "GSF ID Registration (Interactive)")

        # Extract GSF ID from container
        UI.step("Extracting GSF ID from container...")
        _, gsf_raw = self.conn.exec_quiet(
            f"adb -s {self.config.adb_target} shell /sbin/su -c"
            " \"sqlite3 /data/data/com.google.android.gsf/databases/gservices.db"
            " 'select value from main where name=\\\"android_id\\\";'\""
        )

        gsf_id = gsf_raw.strip()
        gsf_hex = ""

        if gsf_id and gsf_id.isdigit():
            gsf_hex = hex(int(gsf_id))[2:]  # Remove "0x" prefix
            print(f"""
{UI.BOLD}{UI.CYAN}╔══════════════════════════════════════════════════════════════╗
║  GSF ID EXTRACTED — MANUAL STEP REQUIRED                     ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  GSF ID (hex):  {gsf_hex:<44}║
║                                                              ║
║  Steps:                                                      ║
║    1. Open in your browser:                                  ║
║       https://www.google.com/android/uncertified              ║
║    2. Log in with your Google account                        ║
║    3. Paste the GSF ID above and register                    ║
║    4. Wait 5-10 minutes for propagation                      ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝{UI.RESET}
""")
        else:
            print(f"""
{UI.YELLOW}╔══════════════════════════════════════════════════════════════╗
║  GSF ID NOT AVAILABLE                                        ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  GApps may need more boot time, or GSF is not installed.     ║
║  You can extract it later:                                   ║
║    adb shell su -c "sqlite3 /data/.../gservices.db ..."      ║
║                                                              ║
║  Or skip this step and continue.                             ║
╚══════════════════════════════════════════════════════════════╝{UI.RESET}
""")

        # Interactive pause — skip in non-interactive mode or non-TTY
        if self.config.non_interactive or not sys.stdin.isatty():
            UI.info("Non-interactive mode — skipping GSF ID pause")
            UI.info("Register the GSF ID manually later if needed")
        else:
            print(f"{UI.BOLD}")
            input("  Press Enter after you have registered the GSF ID (or to skip)...")
            print(f"{UI.RESET}")

    # ── Phase 8: Finalize ──────────────────────────────────────────
    def phase_8_finalize(self) -> None:
        UI.phase(8, "Finalize — Restart & Verify")

        # Restart container to activate all Magisk modules
        UI.step("Restarting container to activate modules...")
        self.conn.exec(
            f"docker restart {self.config.container_name}",
            sudo=True, timeout=60,
        )

        # Wait for reboot
        UI.step(f"Waiting for Android reboot (timeout: {self.config.boot_timeout_sec}s)...")
        booted = self._wait_for_boot()
        if not booted:
            UI.error("Android did not reboot in time")
            raise RuntimeError("Reboot timeout")
        UI.ok("Android rebooted successfully")

        # Reconnect ADB
        time.sleep(5)
        self.conn.exec(
            f"adb connect {self.config.adb_target}",
            check=False, timeout=30,
        )
        time.sleep(3)

        # Re-run post_boot_setup for verification
        UI.step("Running post-boot verification...")
        self.conn.exec(
            f"cd {self.remote_dir}/infra && bash scripts/post_boot_setup.sh {self.config.adb_target}",
            sudo=True, timeout=300,
        )

        # Final VPN check without public IP probes.
        UI.step("Final VPN routing check...")
        _, dns1 = self.conn.exec_quiet(f"adb -s {self.config.adb_target} shell getprop net.dns1")
        _, dns_route = self.conn.exec_quiet(f"adb -s {self.config.adb_target} shell ip route get 10.2.0.1")
        _, default_route = self.conn.exec_quiet(f"adb -s {self.config.adb_target} shell ip route get 203.0.113.10")
        dns1 = dns1.strip() or "UNKNOWN"
        dns_route = dns_route.strip() or "UNKNOWN"
        default_route = default_route.strip() or "UNKNOWN"
        route_uses_vpn = re.search(r"\bdev\s+(tun|wg)[A-Za-z0-9_.:-]*\b", default_route)
        route_uses_eth = re.search(r"\bdev\s+eth[0-9_.:-]*\b", default_route)
        route_blocked = "unreachable" in default_route.lower() or "prohibit" in default_route.lower()
        vpn_active = dns1 == "10.2.0.1" and bool(route_uses_vpn) and not route_uses_eth and not route_blocked

        # Print final summary
        _, model = self.conn.exec_quiet(
            f"adb -s {self.config.adb_target} shell getprop ro.product.model"
        )
        _, fingerprint = self.conn.exec_quiet(
            f"adb -s {self.config.adb_target} shell getprop ro.build.fingerprint"
        )
        _, boot_state = self.conn.exec_quiet(
            f"adb -s {self.config.adb_target} shell getprop ro.boot.verifiedbootstate"
        )
        _, magisk_ver = self.conn.exec_quiet(
            f"adb -s {self.config.adb_target} shell /sbin/su -c 'magisk -v'"
        )

        print(f"""
{UI.BOLD}{UI.GREEN}╔══════════════════════════════════════════════════════════════╗
║  🎯 DEPLOYMENT COMPLETE                                      ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  Device:       {model.strip():<46}║
║  Fingerprint:  {fingerprint.strip()[:46]:<46}║
║  Boot State:   {boot_state.strip():<46}║
║  Magisk:       {magisk_ver.strip():<46}║
║  DNS:          {dns1:<46}║
║  DNS Route:    {dns_route[:46]:<46}║
║  VPN Active:   {'YES ✅' if vpn_active else 'REVIEW ❌':<46}║
║                                                              ║
║  ADB:  adb connect {self.config.vps_host}:5555               ║
║  API:  ssh -L 8800:127.0.0.1:8800 {self.config.vps_user}@{self.config.vps_host}║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝{UI.RESET}
""")

    # ── Internal helpers ───────────────────────────────────────────

    def _wait_for_boot(self) -> bool:
        """Poll container until sys.boot_completed=1."""
        deadline = time.time() + self.config.boot_timeout_sec
        attempt = 0

        while time.time() < deadline:
            attempt += 1
            code, out = self.conn.exec_quiet(
                f"adb -s {self.config.adb_target} shell getprop sys.boot_completed"
            )
            if out.strip() == "1":
                return True

            # Also try docker exec in case ADB isn't ready
            code2, out2 = self.conn.exec_quiet(
                f"docker exec {self.config.container_name} getprop sys.boot_completed"
            )
            if out2.strip() == "1":
                # Ensure ADB is connected
                self.conn.exec_quiet(f"adb connect {self.config.adb_target}")
                return True

            if attempt % 10 == 0:
                elapsed = int(time.time() - (deadline - self.config.boot_timeout_sec))
                UI.info(f"Still waiting... ({elapsed}s elapsed)")

            time.sleep(3)

        return False

    @staticmethod
    def _generate_api_key() -> str:
        """Generate a random API key."""
        import secrets
        return secrets.token_hex(32)


# ═══════════════════════════════════════════════════════════════════
#  Configuration Loading
# ═══════════════════════════════════════════════════════════════════

def load_config_from_args() -> DeployConfig:
    """Parse CLI arguments and/or JSON config file."""

    parser = argparse.ArgumentParser(
        description="Gemini Pixel Offer Claim Bot — Zero-Touch Master Deployment",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python master_deploy.py --host 1.2.3.4 --user root --password secret
  python master_deploy.py --config deploy.json
  python master_deploy.py --host 1.2.3.4 --key ~/.ssh/id_rsa
  python master_deploy.py --host 1.2.3.4 --skip-redroid-build
        """,
    )

    parser.add_argument("--config", type=str, help="Path to JSON config file")
    parser.add_argument("--host", type=str, help="VPS IP address")
    parser.add_argument("--port", type=int, default=22, help="SSH port (default: 22)")
    parser.add_argument("--user", type=str, default="root", help="SSH username (default: root)")
    parser.add_argument("--password", type=str, help="SSH password")
    parser.add_argument("--key", type=str, help="Path to SSH private key")
    parser.add_argument("--wg-conf", type=str, default="infra/wireguard/main.conf", help="WireGuard config file (default: infra/wireguard/main.conf)")
    parser.add_argument("--api-key", type=str, help="Worker API key")
    parser.add_argument("--android-version", type=str, default="14.0.0", help="ReDroid Android version")
    parser.add_argument("--skip-system-setup", action="store_true", help="Skip system package installation")
    parser.add_argument("--skip-redroid-build", action="store_true", help="Skip ReDroid image build")
    parser.add_argument("--skip-vpn-setup", action="store_true", help="Skip VPN/WireGuard setup")
    parser.add_argument("--skip-magisk-modules", action="store_true", help="Skip Magisk module installation")
    parser.add_argument("--non-interactive", action="store_true", help="Skip GSF ID pause (for background/automated runs)")

    args = parser.parse_args()
    config = DeployConfig()

    # Load from JSON if provided
    if args.config:
        config_path = Path(args.config)
        if not config_path.exists():
            fallback_path = Path(__file__).resolve().parent.parent / args.config
            if fallback_path.exists():
                config_path = fallback_path
        if config_path.exists():
            with open(config_path) as f:
                data = json.load(f)
            for key, value in data.items():
                if hasattr(config, key):
                    setattr(config, key, value)
            UI.info(f"Loaded config from {config_path}")
        else:
            UI.error(f"Config file not found: {args.config}")
            sys.exit(1)

    # CLI args override JSON
    if args.host:
        config.vps_host = args.host
    if args.port:
        config.vps_port = args.port
    if args.user:
        config.vps_user = args.user
    if args.password:
        config.vps_password = args.password
    if args.key:
        config.vps_key_path = args.key
    if args.wg_conf:
        config.wireguard_conf_path = args.wg_conf
    if args.api_key:
        config.worker_api_key = args.api_key
    if args.android_version:
        config.redroid_android_version = args.android_version

    config.skip_system_setup = args.skip_system_setup
    config.skip_redroid_build = args.skip_redroid_build
    config.skip_vpn_setup = args.skip_vpn_setup
    config.skip_magisk_modules = args.skip_magisk_modules
    config.non_interactive = args.non_interactive

    # Interactive prompts for missing required fields
    if not config.vps_host:
        config.vps_host = input(f"  {UI.CYAN}VPS IP address: {UI.RESET}").strip()
    if not config.vps_password and not config.vps_key_path:
        key_or_pass = input(
            f"  {UI.CYAN}Auth method — (1) Password  (2) SSH Key  [1]: {UI.RESET}"
        ).strip()
        if key_or_pass == "2":
            default_key = os.path.expanduser("~/.ssh/id_rsa")
            config.vps_key_path = input(
                f"  {UI.CYAN}SSH key path [{default_key}]: {UI.RESET}"
            ).strip() or default_key
        else:
            config.vps_password = getpass.getpass(f"  {UI.CYAN}SSH password: {UI.RESET}")

    return config


def create_sample_config() -> None:
    """Create a sample deploy.json for reference."""
    sample = {
        "vps_host": "YOUR_VPS_IP",
        "vps_port": 22,
        "vps_user": "root",
        "vps_password": "",
        "vps_key_path": "~/.ssh/id_rsa",
        "wireguard_conf_path": "infra/wireguard/main.conf",
        "worker_api_key": "",
        "redroid_android_version": "14.0.0",
        "skip_system_setup": False,
        "skip_redroid_build": False,
        "skip_vpn_setup": False,
    }
    sample_path = Path(__file__).resolve().parent.parent / "deploy.json.example"
    with open(sample_path, "w") as f:
        json.dump(sample, f, indent=4)
    print(f"Sample config written to: {sample_path}")


# ═══════════════════════════════════════════════════════════════════
#  Entry Point
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Create sample config if requested
    if "--create-sample-config" in sys.argv:
        create_sample_config()
        sys.exit(0)

    config = load_config_from_args()
    deployer = MasterDeployer(config)
    deployer.run()
