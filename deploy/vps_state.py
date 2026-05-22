"""Short test to inspect ReDroid state."""
import paramiko, warnings, json
warnings.filterwarnings("ignore")

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.WarningPolicy())
ssh.connect("203.161.39.181", port=22022, username="root", password="Muaj1324@", timeout=15)

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
