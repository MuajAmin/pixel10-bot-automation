"""Restart ReDroid after Gluetun fix to restore ADB connectivity."""
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
    if label: print(f"\n{'='*60}\n  {label}\n{'='*60}")
    _, so, se = ssh.exec_command(cmd, timeout=timeout)
    out = so.read().decode("utf-8", errors="replace").strip()
    err = se.read().decode("utf-8", errors="replace").strip()
    if out: print(out)
    if err:
        for l in err.split("\n")[-5:]:
            if l.strip(): print(f"[err] {l}")
    return out

print("=" * 60)
print("  Restarting ReDroid + Restoring ADB")
print("=" * 60)

# ── Step 1: Kill stale ADB server state ──
run("adb kill-server 2>&1 || true", "1. Kill ADB server")

# ── Step 2: Restart ReDroid container ──
print("\n[*] Restarting ReDroid container...")
run("docker restart pixel10-android", "2. Restart ReDroid", timeout=60)

# ── Step 3: Wait for Android boot ──
print("\n[*] Waiting for Android to boot (up to 120s)...")
booted = False
for i in range(24):
    time.sleep(5)
    _, so, _ = ssh.exec_command("docker exec pixel10-android getprop sys.boot_completed", timeout=10)
    val = so.read().decode("utf-8", errors="replace").strip()
    elapsed = (i + 1) * 5
    print(f"  [{elapsed}s] boot_completed = '{val}'")
    if val == "1":
        booted = True
        break

if not booted:
    print("\n  ❌ ReDroid did not boot in 120s!")
    run("docker logs pixel10-android --tail=20 2>&1", "ReDroid logs")
    ssh.close()
    sys.exit(1)

print("\n  ✅ Android booted!")

# ── Step 3.5: Restore Gluetun routing policy rules ──
print("\n[*] Restoring Gluetun routing policy rules...")
run("docker exec -u 0 gluetun sh -c '"
    "ETH_IP=\$(ip -o -4 addr show dev eth0 | awk \"{print \$4}\" | cut -d/ -f1) && "
    "ETH_NET=\$(ip route | grep \"dev eth0\" | grep \"proto kernel\" | awk \"{print \$1}\") && "
    "if [ -n \"\$ETH_IP\" ] && [ -n \"\$ETH_NET\" ]; then "
    "  ip rule add from all to \"\$ETH_NET\" lookup main pref 98 2>/dev/null || true; "
    "  ip rule add from \"\$ETH_IP\" lookup 1002 pref 100 2>/dev/null || true; "
    "  ip rule add not from all fwmark 0xca6c lookup 51820 pref 101 2>/dev/null || true; "
    "fi'", "Restore Gluetun Routing Rules")

# ── Step 4: Connect ADB ──
time.sleep(5)  # Give adbd a moment to fully initialize
run("adb start-server 2>&1", "4a. Start ADB server")
time.sleep(2)
run("adb connect 127.0.0.1:5555 2>&1", "4b. ADB Connect")
time.sleep(3)
run("adb devices -l 2>&1", "4c. ADB Devices")

boot = run("adb -s 127.0.0.1:5555 shell getprop sys.boot_completed 2>&1", "4d. Boot Complete?")

if boot.strip() == "1":
    print("\n" + "=" * 60)
    print("  ✅ FULLY OPERATIONAL — VPN + Android + ADB all working!")
    print("=" * 60)
    run("adb -s 127.0.0.1:5555 shell getprop ro.product.model 2>&1", "Model")
    run("adb -s 127.0.0.1:5555 shell getprop ro.product.brand 2>&1", "Brand")
    run("adb -s 127.0.0.1:5555 shell getprop ro.build.version.release 2>&1", "Android Version")
    run("adb -s 127.0.0.1:5555 shell getprop ro.build.fingerprint 2>&1", "Fingerprint")
    
    # Verify VPN from inside ReDroid
    print("\n[*] Checking VPN routing from ReDroid...")
    vpn_ip = run("adb -s 127.0.0.1:5555 shell \"/system/etc/init/magisk/busybox wget -q -O - http://api.ipify.org 2>/dev/null || curl -s -m 10 http://api.ipify.org 2>/dev/null || echo 'N/A'\"", "ReDroid public IP (VPN)")
    host_ip = run("curl -s -m 5 http://api.ipify.org 2>&1", "VPS public IP")
    
    if vpn_ip and host_ip and vpn_ip.strip() != host_ip.strip() and "N/A" not in vpn_ip:
        print(f"\n  ✅ VPN ACTIVE — ReDroid IP ({vpn_ip.strip()}) ≠ VPS IP ({host_ip.strip()})")
    elif "N/A" in vpn_ip:
        print("\n  ⚠️  Could not check VPN IP (neither busybox wget nor curl is functional in ReDroid)")
    else:
        print(f"\n  ⚠️  VPN may not be routing — IPs match ({vpn_ip.strip()} vs {host_ip.strip()})")
    
    run("curl -s -m 3 http://127.0.0.1:8800/healthz", "Worker API health")
    
    # Final container status
    run('docker ps --format "table {{.Names}}\t{{.Status}}"', "Container Status")
else:
    print("\n  ❌ ADB still not working properly")
    run("docker logs pixel10-android --tail=10 2>&1", "ReDroid logs")

ssh.close()
print("\n[*] Done!")
