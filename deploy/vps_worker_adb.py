"""Check ADB connection inside the worker-api container."""
import paramiko, warnings
warnings.filterwarnings("ignore")

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.WarningPolicy())
ssh.connect("203.161.39.181", port=22022, username="root", password="Muaj1324@", timeout=15)

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
