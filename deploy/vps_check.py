"""Check Android boot status after VPN fix."""
import paramiko, warnings, sys, time
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

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("203.161.39.181", port=22022, username="root", password="Muaj1324@", timeout=15)

def run(cmd, label="", timeout=30):
    if label: print(f"\n=== {label} ===")
    _, so, se = ssh.exec_command(cmd, timeout=timeout)
    out = so.read().decode("utf-8", errors="replace").strip()
    err = se.read().decode("utf-8", errors="replace").strip()
    if out: print(f"    {out[:500]}")
    if err:
        for l in err.split("\n")[-3:]:
            if l.strip(): print(f"    [err] {l}")
    return out

run('docker ps --format "table {{.Names}}\t{{.Status}}"', "Docker Status")
run("adb disconnect 2>/dev/null; adb connect 127.0.0.1:5555 2>&1", "ADB Connect", timeout=15)
time.sleep(3)
run("adb devices -l 2>&1", "ADB Devices")
run("adb -s 127.0.0.1:5555 shell getprop sys.boot_completed 2>&1", "Boot Complete?", timeout=15)
run("adb -s 127.0.0.1:5555 shell getprop ro.product.model 2>&1", "Model", timeout=15)
run("adb -s 127.0.0.1:5555 shell getprop ro.product.brand 2>&1", "Brand", timeout=15)
run("adb -s 127.0.0.1:5555 shell getprop ro.build.version.release 2>&1", "Android Ver", timeout=15)
run("adb -s 127.0.0.1:5555 shell getprop ro.build.fingerprint 2>&1", "Fingerprint", timeout=15)
run("adb -s 127.0.0.1:5555 shell pm list packages 2>&1 | grep -cE 'gms|vending|gsf'", "GApps Count", timeout=15)
run("curl -s -m 3 http://127.0.0.1:8800/healthz", "Worker API")
run("systemctl is-active pixel-bot", "Bot Status")
run("docker logs pixel10-android 2>&1 | tail -5", "ReDroid Logs", timeout=15)

ssh.close()
print("\n[*] Done!")
