"""Short test to inspect ReDroid state."""
import paramiko, warnings, json
warnings.filterwarnings("ignore")

import json
from pathlib import Path

def load_creds():
    path = Path(__file__).resolve().parent.parent / "deploy.json"
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg["vps_host"], int(cfg["vps_port"]), cfg["vps_user"], cfg["vps_password"]

host, port, user, password = load_creds()
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.WarningPolicy())
ssh.connect(host, port=port, username=user, password=password, timeout=15)

cmds = [
    ("Docker PS -a", "docker ps -a"),
    ("Android State", "docker inspect pixel10-android --format='{{json .State}}'"),
    ("Android Health", "docker inspect pixel10-android --format='{{json .State.Health}}'"),
]

for label, cmd in cmds:
    print(f"\n=== {label} ===")
    _, stdout, _ = ssh.exec_command(cmd, timeout=15)
    print(stdout.read().decode("utf-8", errors="replace").strip())

ssh.close()
