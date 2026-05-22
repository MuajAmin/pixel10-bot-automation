"""Run a remote command on the VPS."""
import paramiko, warnings, sys
warnings.filterwarnings("ignore")

if len(sys.argv) < 2:
    print("Usage: python3 vps_exec.py <command>")
    sys.exit(1)

cmd = " ".join(sys.argv[1:])

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

_, so, se = ssh.exec_command(cmd)
out = so.read().decode("utf-8", errors="replace")
err = se.read().decode("utf-8", errors="replace")

if out:
    print(out)
if err:
    print("--- STDERR ---")
    print(err)

ssh.close()
