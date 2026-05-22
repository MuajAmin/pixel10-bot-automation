"""Upload a file to the VPS."""
import paramiko, warnings, sys
warnings.filterwarnings("ignore")

if len(sys.argv) < 3:
    print("Usage: python3 vps_upload.py <local_path> <remote_path>")
    sys.exit(1)

local_path = sys.argv[1]
remote_path = sys.argv[2]

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("203.161.39.181", port=22022, username="root", password="Muaj1324@", timeout=15)

sftp = ssh.open_sftp()
print(f"Uploading {local_path} to remote {remote_path}...")
sftp.put(local_path, remote_path)
sftp.close()
ssh.close()
print("Upload completed successfully!")
