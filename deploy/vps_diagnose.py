"""Diagnose Gluetun VPN + ReDroid ADB failure."""
import paramiko, warnings, sys
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

# 1. Gluetun logs — look for VPN failure reason
run("docker logs gluetun --tail=60 2>&1", "1. Gluetun VPN Logs (last 60)")

# 2. Check WireGuard interface inside Gluetun
run("docker exec gluetun ip addr show 2>&1 || echo 'EXEC_FAILED'", "2. Gluetun Network Interfaces")

# 3. Check if WireGuard tunnel is up
run("docker exec gluetun wg show 2>&1 || echo 'NO_WG'", "3. WireGuard Tunnel Status")

# 4. Check Gluetun health
run("docker inspect --format='{{.State.Health.Status}}' gluetun 2>&1", "4. Gluetun Health Status")

# 5. Port 5555 listening on host?
run("ss -tlnp | grep 5555 || echo 'PORT_5555_NOT_LISTENING'", "5. Port 5555 Binding")

# 6. Can we reach inside ReDroid via docker exec?
run("docker exec pixel10-android getprop sys.boot_completed 2>&1 || echo 'EXEC_FAILED'", "6. ReDroid Boot Status (docker exec)")

# 7. ReDroid logs
run("docker logs pixel10-android --tail=30 2>&1", "7. ReDroid Logs (last 30)")

# 8. Can Gluetun reach the Proton endpoint?
run("docker exec gluetun ping -c 2 -W 3 138.199.50.149 2>&1 || echo 'PING_FAILED'", "8. Ping Proton VPN Endpoint")

# 9. DNS resolution inside Gluetun
run("docker exec gluetun nslookup google.com 2>&1 || echo 'DNS_FAILED'", "9. DNS from Gluetun")

# 10. iptables/firewall on host
run("iptables -L -n --line-numbers 2>&1 | head -30", "10. Host iptables (first 30)")

ssh.close()
print("\n[*] Diagnosis complete!")
