"""Upload a local folder to the VPS with recursive SFTP.

Examples:
    python vps_upload_folder.py "E:\\Game\\Coding\\AutoLoginBOT\\pixel10-bot-automation" /root/pixel10-bot-automation
    python vps_upload_folder.py . /root/pixel10-bot-automation --config deploy.json
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path, PurePosixPath

import paramiko


SKIP_DIRS = {
    ".git",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".vscode",
    "__pycache__",
    "build",
    "dist",
    "env",
    "venv",
    ".venv",
}

SKIP_FILES = {".DS_Store", "Thumbs.db"}


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def mkdir_p(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    current = PurePosixPath("/")
    for part in PurePosixPath(remote_dir).parts:
        if part == "/":
            continue
        current /= part
        try:
            sftp.stat(str(current))
        except (FileNotFoundError, IOError):
            try:
                sftp.mkdir(str(current))
            except IOError:
                pass


def should_skip(path: Path) -> bool:
    return path.name in SKIP_DIRS or path.name in SKIP_FILES


def upload_folder(sftp: paramiko.SFTPClient, local_dir: Path, remote_dir: str) -> tuple[int, int]:
    uploaded = 0
    skipped = 0
    mkdir_p(sftp, remote_dir)

    for root, dirs, files in os.walk(local_dir):
        root_path = Path(root)
        dirs[:] = [item for item in dirs if item not in SKIP_DIRS]

        relative_root = root_path.relative_to(local_dir)
        remote_root = str(PurePosixPath(remote_dir) / relative_root.as_posix())
        mkdir_p(sftp, remote_root)

        for filename in files:
            local_file = root_path / filename
            if should_skip(local_file):
                skipped += 1
                continue

            remote_file = str(PurePosixPath(remote_root) / filename)
            print(f"Uploading {local_file} -> {remote_file}")
            sftp.put(str(local_file), remote_file)
            uploaded += 1

    return uploaded, skipped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload a folder to a VPS over SFTP.")
    parser.add_argument("local_dir", help="Local folder path to upload.")
    parser.add_argument("remote_dir", help="Remote VPS folder path.")
    parser.add_argument("--config", default="deploy.json", help="Config JSON path. Default: deploy.json")
    parser.add_argument("--host", help="VPS host/IP. Overrides config.")
    parser.add_argument("--port", type=int, help="SSH port. Overrides config.")
    parser.add_argument("--user", help="SSH username. Overrides config.")
    parser.add_argument("--password", help="SSH password. If omitted, config or prompt is used.")
    parser.add_argument("--key", help="SSH private key path. Overrides config.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    if not config_path.exists():
        fallback_path = Path(__file__).resolve().parent.parent / args.config
        if fallback_path.exists():
            config_path = fallback_path
    config = load_config(config_path)

    local_dir = Path(args.local_dir).expanduser().resolve()
    if not local_dir.is_dir():
        print(f"Local folder not found: {local_dir}")
        return 1

    host = args.host or config.get("vps_host")
    port = args.port or int(config.get("vps_port", 22))
    user = args.user or config.get("vps_user")
    password = args.password or config.get("vps_password")
    key_path = args.key or config.get("vps_key_path") or None

    if not host or not user:
        print("Missing VPS host/user. Pass --host and --user or set them in deploy.json.")
        return 1

    if not key_path and not password:
        password = getpass.getpass(f"Password for {user}@{host}: ")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs = {
        "hostname": host,
        "port": port,
        "username": user,
        "timeout": 20,
    }
    if key_path:
        connect_kwargs["key_filename"] = str(Path(key_path).expanduser())
    else:
        connect_kwargs["password"] = password

    try:
        ssh.connect(**connect_kwargs)
        sftp = ssh.open_sftp()
        try:
            uploaded, skipped = upload_folder(sftp, local_dir, args.remote_dir)
        finally:
            sftp.close()
    finally:
        ssh.close()

    print(f"Done. Uploaded {uploaded} file(s), skipped {skipped} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
