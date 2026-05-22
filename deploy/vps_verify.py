"""Quick VPS verification — check containers, API, ADB."""
import paramiko, warnings, sys
if sys.platform == "win32":
    try:
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
warnings.filterwarnings("ignore")

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.WarningPolicy())
ssh.connect("203.161.39.181", port=22022, username="root", password="Muaj1324@", timeout=15)

cmds = [
    ("Docker containers", 'docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"'),
    ("Worker API health", "curl -s -m 5 http://127.0.0.1:8800/healthz 2>/dev/null || echo UNREACHABLE"),
    ("ADB boot status", "adb connect localhost:5555 2>/dev/null; adb -s localhost:5555 shell getprop sys.boot_completed 2>/dev/null || echo NOT_READY"),
    ("Bot code in container", "docker exec pixel10-worker ls /app/bot/ 2>/dev/null || echo MISSING"),
    ("Worker API logs (last 10)", "docker logs pixel10-worker --tail=10 2>&1"),
]

for label, cmd in cmds:
    print(f"\n=== {label} ===")
    _, stdout, _ = ssh.exec_command(cmd, timeout=15)
    out = stdout.read().decode("utf-8", errors="replace").strip()
    print(out if out else "(no output)")

ssh.close()
print("\n=== Done ===")
