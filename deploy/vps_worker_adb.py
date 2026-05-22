"""Check ADB connection inside the worker-api container."""
import paramiko, warnings
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
    ("ADB Devices inside worker before connect", "docker exec pixel10-worker adb devices"),
    ("Connect inside worker", "docker exec pixel10-worker adb connect 172.20.0.10:5555"),
    ("ADB Devices inside worker after connect", "docker exec pixel10-worker adb devices"),
    ("Check getprop inside worker", "docker exec pixel10-worker adb -s 172.20.0.10:5555 shell getprop sys.boot_completed 2>&1 || echo failed"),
]

for label, cmd in cmds:
    print(f"\n=== {label} ===")
    _, stdout, _ = ssh.exec_command(cmd, timeout=15)
    print(stdout.read().decode("utf-8", errors="replace").strip())

ssh.close()
