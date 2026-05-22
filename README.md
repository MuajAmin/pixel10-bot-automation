# BDGemini Pixel Worker

An automated Telegram-bot driven pipeline designed to run on a Linux VPS using a headless ReDroid container (Android 11) to spoof Google Pixel 10 Pro hardware identities and claim the Google One AI Premium (Gemini Advanced) offer on Google accounts.

---

## 📖 How It Works: VPS Gemini Offer Claim Flow

Google restricts the Gemini Advanced/AI Premium promo (typically a 1-year trial) to specific flagship devices like the Pixel 10 Pro. However, spoofing a flagship identity globally on a lower Android version runtime (like ReDroid Android 11) causes critical SDK version mismatches. This breaks Google Play Services (GMS), attestation, and Google Play Store, making account authentication impossible.

To bypass these restrictions, this project implements a **Dual-Identity Property Engine** coordinated by a Python worker and ADB commands:

```
                  ┌────────────────────────────────────────┐
                  │          Telegram Bot Command          │
                  └───────────────────┬────────────────────┘
                                      │
                                      ▼
                  ┌────────────────────────────────────────┐
                  │    Step 0-1: Reset & Purge GMS Data    │
                  │   (Restores device to Pixel 5 Base)    │
                  └───────────────────┬────────────────────┘
                                      │
                                      ▼
                  ┌────────────────────────────────────────┐
                  │    Step 2: Swap to Pixel 10 Pro        │
                  │    & Perform Google Account Login      │
                  └───────────────────┬────────────────────┘
                                      │
                                      ▼
                  ┌────────────────────────────────────────┐
                  │    Step 3: Restore to Pixel 5 Base     │
                  │       (Allows GMS synchronization)     │
                  └───────────────────┬────────────────────┘
                                      │
                                      ▼
                  ┌────────────────────────────────────────┐
                  │    Step 4: Swap to Pixel 10 Pro &      │
                  │    Launch Google One to Trigger Offer  │
                  └───────────────────┬────────────────────┘
                                      │
                                      ▼
                  ┌────────────────────────────────────────┐
                  │    Step 5: Restore to Pixel 5 Base &   │
                  │    Scrape Subscription Status (Finish) │
                  └────────────────────────────────────────┘
```

### The Dual-Identity Property Engine (`build_props.sh`)
* **Base Identity (Pixel 5 / redfin / SDK 30 / Android 11)**:
  Used globally to ensure ReDroid's Android 11 runtime remains fully stable, passes basic Play Integrity checks, and logs into Google Accounts successfully.
* **Swap Layer (Pixel 10 Pro / franklin / SDK 36 / Android 16)**:
  Applied temporarily during critical interaction windows (Google Login initiation and Google One launch).
* **In-Memory Property Caching**:
  Android's `Build` class caches property values in-memory at process initialization. When we swap properties to Pixel 10 Pro, launch the target application (Google One), and immediately restore the properties back to Pixel 5, the Google One process continues to see the Pixel 10 Pro signature in-memory. Meanwhile, GMS and system processes revert safely to Pixel 5 without crashing or failing attestation.

---

## 🛠️ Step-by-Step Claim Pipeline

1. **Step 0: Identity Reset**
   * Reverts any leftover custom properties back to the stable **Pixel 5 Base Identity**.
2. **Step 1: Purge & Initialize**
   * Clear all app data for Google Play Services (GMS), Google Play Store, and Google One.
   * Remove any previously authenticated Google accounts from the system database.
3. **Step 2: Swap & Login**
   * Swaps properties to **Pixel 10 Pro**.
   * Forces a stop of GMS/GSF. To handle the resulting transient `DeadSystemException` in the Android DisplayManager, the worker sleeps for 10 seconds and re-acquires a fresh `uiautomator2` device handle.
   * Dispatches the native `ADD_ACCOUNT_SETTINGS` settings intent.
   * Automates the login flow (typing username/password, handling 2FA delays, and accepting the Google Terms of Service).
4. **Step 3: Restore Base**
   * Immediately restores properties back to the **Pixel 5 Base Identity** to let GMS sync accounts naturally under a stable profile.
5. **Step 4: Swap & Launch Google One**
   * Swaps properties to **Pixel 10 Pro** once more.
   * Launches the Google One app.
   * Detects the presence of "Gemini Advanced", "AI Premium", or "Included with Pixel" trial promotional buttons and automates the activation click.
6. **Step 5: Restore & Scrape Status**
   * Restores properties back to the **Pixel 5 Base Identity**.
   * Opens the Google One subscription/benefits tab and extracts status text to confirm successful subscription activation.

---

## 🚀 Setup & Deployment

### 1. Local Setup
Clone the repository and install the Python dependencies:
```powershell
pip install -r requirements.txt
```
Create a configuration file `api.env`:
```env
WORKER_BACKEND=android
ANDROID_WORKER_URL=http://YOUR_VPS_IP:8800
ANDROID_WORKER_API_KEY=your-secret-key
```

### 2. Deploy to VPS
Use the provided deployment utility to copy the project files to your VPS (replace with your server details):
```powershell
python deploy/vps_upload_folder.py . /root/pixel10-bot-automation --host YOUR_VPS_IP --port YOUR_PORT --user root
```

### 3. VPS Stack Initialization
SSH into your VPS and configure the Redroid Docker container:
```bash
cd /root/pixel10-bot-automation
sudo bash infra/setup_vps.sh

cd /root/pixel10-bot-automation/infra
cp infra.env.example .env
# Edit .env and set REDROID_IMAGE to your custom Google Apps/Magisk image
docker compose up -d
```
*Required files before startup:*
* `/root/pixel10-bot-automation/api.env`
* `/root/pixel10-bot-automation/infra/.env` (with `REDROID_IMAGE` configured)
* `/root/pixel10-bot-automation/infra/wireguard/main.conf` (Wireguard configuration for network routing)
* `/root/pixel10-bot-automation/config/keybox.xml` (Optional for TrickyStore basic integrity verification)

---

## 🔒 Magisk Modules & Device Hardening

After Redroid boots up on the port `5555`:
```bash
# Connect ADB
adb connect 127.0.0.1:5555

# Install Blazer boot-patch modules
bash infra/install_blazer_module.sh 127.0.0.1:5555
docker restart pixel10-android
sleep 120

# Setup Magisk/Zygisk modules
bash infra/setup_magisk_modules.sh 127.0.0.1:5555
docker restart pixel10-android
sleep 120

# Apply final hardening configurations
bash infra/harden_device.sh 127.0.0.1:5555
```

Verify properties are applied correctly:
```bash
bash core/build_props.sh verify 127.0.0.1:5555
adb -s 127.0.0.1:5555 shell getprop ro.product.model
adb -s 127.0.0.1:5555 shell getprop ro.product.device
```
*Expected default global output:*
```text
Pixel 5
redfin
```

---

## 🔍 Validation & Tests

Compile check python files locally:
```powershell
python -m compileall -q main.py bot core deploy
```

Run test suite:
```bash
pytest tests/
```
