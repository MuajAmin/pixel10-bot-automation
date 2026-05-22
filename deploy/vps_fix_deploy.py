"""Fix deploy v5: Full compose restart with DNS fix."""
import paramiko, warnings, os, sys, time
warnings.filterwarnings("ignore")

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

VPS = ("203.161.39.181", 22022, "root", "Muaj1324@")
REMOTE = "/root/pixel10-bot-automation"
LOCAL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
print("[*] Connecting...")
ssh.connect(VPS[0], port=VPS[1], username=VPS[2], password=VPS[3], timeout=15)
sftp = ssh.open_sftp()

def run(cmd, label=""):
    if label: print(f"\n=== {label} ===")
    print(f"  $ {cmd[:120]}")
    _, so, se = ssh.exec_command(cmd, timeout=300)
    out = so.read().decode("utf-8", errors="replace").strip()
    err = se.read().decode("utf-8", errors="replace").strip()
    for l in out.split("\n")[-10:]:
        if l.strip(): print(f"    {l}")
    for l in err.split("\n")[-5:]:
        if l.strip(): print(f"    [err] {l}")
    return out

def upload(local_rel, remote_path):
    local_path = os.path.join(LOCAL, local_rel)
    if not os.path.exists(local_path):
        print(f"  SKIP: {local_rel}")
        return
    rd = os.path.dirname(remote_path)
    try: sftp.stat(rd)
    except: ssh.exec_command(f"mkdir -p {rd}"); time.sleep(0.3)
    sftp.put(local_path, remote_path)
    print(f"  OK: {local_rel}")

# ---- Upload ----
print("\n=== Upload ===")
upload("infra/docker-compose.yml", f"{REMOTE}/infra/docker-compose.yml")
upload("config/proton.conf", f"{REMOTE}/config/proton.conf")
run(f'sed -i "s/\\r$//" {REMOTE}/infra/docker-compose.yml {REMOTE}/config/proton.conf', "CRLF")

# ---- Full restart ----
run(f"cd {REMOTE}/infra && docker compose down -v --remove-orphans 2>&1 | tail -5", "Docker Down")
time.sleep(3)

# Clear stale ADB
run("adb kill-server 2>/dev/null; sleep 1; adb start-server 2>/dev/null || true", "ADB Reset")

run(f"cd {REMOTE}/infra && docker compose up -d --build 2>&1 | tail -15", "Docker Up")

# ---- Wait for Gluetun health (120s max) ----
print("\n=== Waiting for Gluetun VPN (120s max) ===")
for i in range(24):
    time.sleep(5)
    _, so, _ = ssh.exec_command(
        "docker inspect --format='{{.State.Health.Status}}' gluetun 2>/dev/null", timeout=10)
    status = so.read().decode().strip()
    elapsed = (i+1)*5
    print(f"  [{elapsed}s] VPN: {status}")
    if status == "healthy":
        print("  VPN HEALTHY!")
        break
    if elapsed % 20 == 0:
        # Check Gluetun logs periodically
        _, so2, _ = ssh.exec_command(
            "docker logs gluetun 2>&1 | grep -v '@@' | tail -3", timeout=10)
        log = so2.read().decode("utf-8", errors="replace").strip()
        if log:
            for l in log.split("\n"):
                print(f"    >> {l.strip()}")
else:
    print("  VPN still not healthy after 120s")
    run("docker logs gluetun 2>&1 | grep -v '@@' | tail -10", "Gluetun Logs")

# ---- Wait for ReDroid boot (90s) ----
print("\n=== Waiting for ReDroid boot (90s max) ===")
time.sleep(10)  # Initial delay
for i in range(16):
    time.sleep(5)
    _, so, _ = ssh.exec_command(
        "adb connect 127.0.0.1:5555 2>&1; adb -s 127.0.0.1:5555 shell getprop sys.boot_completed 2>/dev/null",
        timeout=10)
    result = so.read().decode().strip()
    elapsed = 10 + (i+1)*5
    print(f"  [{elapsed}s] {result}")
    if "1" in result:
        print("  Android BOOTED!")
        break
else:
    print("  Android not booted after 90s")

# ---- Final status ----
run('docker ps --format "table {{.Names}}\t{{.Status}}"', "Final Docker Status")
run("adb -s 127.0.0.1:5555 shell getprop ro.product.model 2>&1", "Device Model")
run("adb -s 127.0.0.1:5555 shell getprop ro.build.version.release 2>&1", "Android Version")
run("adb -s 127.0.0.1:5555 shell pm list packages 2>&1 | grep -E 'gms|vending' || echo 'NO GAPPS'", "GApps Check")
run("curl -s -m 3 http://127.0.0.1:8800/healthz 2>/dev/null || echo 'API: fail'", "Worker API")
run("systemctl is-active pixel-bot", "Bot Status")

sftp.close()
ssh.close()
print("\n[*] Done!")
