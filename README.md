# BDGemini Pixel Worker

Android worker stack for the Pixel offer automation flow.

## Local Setup

```powershell
pip install -r requirements.txt
```

Set the Android backend in `api.env`:

```env
WORKER_BACKEND=android
ANDROID_WORKER_URL=http://YOUR_VPS_IP:8800
ANDROID_WORKER_API_KEY=your-secret-key
```

## Upload To VPS

Upload the full repository, not only `infra/`. The Docker Compose file builds from the repository root and reads `../api.env`.

```powershell
python deploy/vps_upload_folder.py . /root/pixel10-bot-automation --host 203.161.39.181 --port 22022 --user root
```

## VPS Setup

On the VPS:

```bash
cd /root/pixel10-bot-automation
sudo bash infra/setup_vps.sh
cd /root/pixel10-bot-automation/infra
cp infra.env.example .env
# Edit .env and set REDROID_IMAGE to your custom GApps/Magisk/NDK image.
docker compose up -d
```

Required files before starting:

- `/root/pixel10-bot-automation/api.env`
- `/root/pixel10-bot-automation/infra/.env` with `REDROID_IMAGE` set
- `/root/pixel10-bot-automation/infra/wireguard/main.conf`

Optional for TrickyStore:

- `/root/pixel10-bot-automation/config/keybox.xml`

## Device Identity

The global ReDroid identity is intentionally Pixel 5 / redfin / Android 11. This matches the default ReDroid Android 11 runtime and avoids SDK/build mismatches.

Do not set Pixel 9 or Pixel 10 as the global system identity unless the container image actually runs the matching Android version. For Google One-specific testing, use the per-app LSPosed path instead of changing global props.

## Magisk And Hardening

After Android boots:

```bash
adb connect 127.0.0.1:5555
bash infra/install_blazer_module.sh 127.0.0.1:5555
docker restart pixel10-android
sleep 120
bash infra/setup_magisk_modules.sh 127.0.0.1:5555
docker restart pixel10-android
sleep 120
bash infra/harden_device.sh 127.0.0.1:5555
```

Verify the final props:

```bash
bash core/build_props.sh verify 127.0.0.1:5555
adb -s 127.0.0.1:5555 shell getprop ro.product.model
adb -s 127.0.0.1:5555 shell getprop ro.product.device
adb -s 127.0.0.1:5555 shell getprop ro.build.version.sdk
```

Expected:

```text
Pixel 5
redfin
30
```

## Common Fixes

If the model does not change, confirm Magisk/resetprop is available:

```bash
adb -s 127.0.0.1:5555 shell su -c 'which resetprop || ls /sbin/resetprop /data/adb/magisk/resetprop'
```

If Docker commands fail, use the actual container names:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
docker restart pixel10-android
docker logs pixel10-android --tail=50
```

The worker API binds to `127.0.0.1:8800` by default. Use an SSH tunnel from your bot host, or explicitly set `WORKER_API_BIND=0.0.0.0` in `infra/.env` only when you also enforce firewall/API-key controls.

Run a quick Python compile check locally:

```powershell
python -m compileall -q main.py bot core deploy
```
