"""Run a remote command on the VPS."""
import paramiko, warnings, sys
warnings.filterwarnings("ignore")

if len(sys.argv) < 2:
    print("Usage: python3 vps_exec.py <command>")
    sys.exit(1)

cmd = " ".join(sys.argv[1:])

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("203.161.39.181", port=22022, username="root", password="Muaj1324@", timeout=15)

_, so, se = ssh.exec_command(cmd)
out = so.read().decode("utf-8", errors="replace")
err = se.read().decode("utf-8", errors="replace")

if out:
    print(out)
if err:
    print("--- STDERR ---")
    print(err)

ssh.close()
