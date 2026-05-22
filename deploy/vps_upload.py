"""Upload a file to the VPS."""
import paramiko, warnings, sys
warnings.filterwarnings("ignore")

if len(sys.argv) < 3:
    print("Usage: python3 vps_upload.py <local_path> <remote_path>")
    sys.exit(1)

local_path = sys.argv[1]
remote_path = sys.argv[2]

import json
from pathlib import Path

def load_creds():
    path = Path(__file__).resolve().parent.parent / "deploy.json"
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg["vps_host"], int(cfg["vps_port"]), cfg["vps_user"], cfg["vps_password"]

host, port, user, password = load_creds()
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, port=port, username=user, password=password, timeout=15)

sftp = ssh.open_sftp()
print(f"Uploading {local_path} to remote {remote_path}...")
sftp.put(local_path, remote_path)
sftp.close()
ssh.close()
print("Upload completed successfully!")
