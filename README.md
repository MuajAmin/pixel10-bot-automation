# 🤖 BDGemini Pixel Worker

An automated Telegram-bot driven pipeline designed to run on a Linux VPS using a headless ReDroid container (Android 11) to spoof Google Pixel 10 Pro hardware identities and claim the Google One AI Premium (Gemini Advanced) offer on Google accounts.

---

## 🏗️ System Architecture

The following diagram illustrates the network model, containers, and data flows between the user, Telegram servers, the local controller bot, and the remote Android worker VPS:

```mermaid
graph TD
    User([Telegram User]) <-->|Commands & Alerts| TelegramAPI[Telegram API Servers]
    
    subgraph LocalController [Local Controller Machine]
        TelegramBot[Telegram Bot Client] <-->|Poll / Webhooks| TelegramAPI
        BotWorker[Bot Worker / Concurrency Registry] -->|Dispatches Jobs| ClientAPI[Worker API Client client.py]
        LocalDB[(MongoDB / accounts.json)] <-->|Save Accounts & Jobs| TelegramBot
    end
    
    subgraph VPSHost [Linux VPS Host]
        subgraph DockerStack [Docker Compose Stack]
            Gluetun[Gluetun VPN Gateway] <-->|WireGuard VPN Tunnel| VPNProvider([ProtonVPN Server])
            ReDroid[ReDroid Container Android 11] ---|Shares Network Namespace| Gluetun
            WorkerAPI[Worker API Server api_server.py]
        end
        
        ClientAPI -->|HTTP POST /jobs :8800| WorkerAPI
        WorkerAPI -->|Spawns Subprocess| AutomationScript[automation.py]
        AutomationScript -->|ADB loopback :5555| Gluetun
        Gluetun -->|Forward ADB| ReDroid
        AutomationScript -->|UiAutomator2 Controls| ReDroid
        AutomationScript -->|build_props.sh Swap commands| ReDroid
    end
    
    style LocalController fill:#1f2937,stroke:#3b82f6,stroke-width:2px,color:#fff
    style VPSHost fill:#111827,stroke:#10b981,stroke-width:2px,color:#fff
    style DockerStack fill:#374151,stroke:#6366f1,stroke-width:1px,color:#fff
```

---

## 📖 How It Works: VPS Gemini Offer Claim Flow

Google restricts the Gemini Advanced/AI Premium promo (typically a 1-year trial) to specific flagship devices like the Pixel 10 Pro. However, spoofing a flagship identity globally on a lower Android version runtime (like ReDroid Android 11) causes critical SDK version mismatches. This breaks Google Play Services (GMS), attestation, and Google Play Store, making account authentication impossible.

To bypass these restrictions, this project implements a **Dual-Identity Property Engine** coordinated by a Python worker and ADB commands:

### The Dual-Identity Property Engine ([build_props.sh](file:///root/pixel10-bot-automation/core/build_props.sh))
* **Base Identity (Pixel 5 / redfin / SDK 30 / Android 11)**:
  Used globally to ensure ReDroid's Android 11 runtime remains fully stable, passes basic Play Integrity checks, and logs into Google Accounts successfully.
* **Swap Layer (Pixel 10 Pro / franklin / SDK 36 / Android 16)**:
  Applied temporarily during critical interaction windows (Google Login initiation and Google One launch).
* **In-Memory Property Caching**:
  Android's `Build` class caches property values in-memory at process initialization. When we swap properties to Pixel 10 Pro, launch the target application (Google One), and immediately restore the properties back to Pixel 5, the Google One process continues to see the Pixel 10 Pro signature in-memory. Meanwhile, GMS and system processes revert safely to Pixel 5 without crashing or failing attestation.

### 🕒 Dual-Identity Prop-Swap Timeline

```
               ┌── Base Identity ──┐ (P5 / redfin / SDK 30 / Android 11)
               │                   │
               │   ┌───────────────┴───────────────┐
               │   │ Swap Layer (P10 Pro)          │
               │   │                               │
               ▼   ▼                               ▼
PROP LEVEL:   Base ───── Swap ───── Base ───── Swap ───── Base
PHASE:        Boot ───► Login ───► Sync ───► Launch ───► Scrape
              (P5)    (P10 Pro)     (P5)    (P10 Pro)     (P5)
GMS State:   Stable   Transient    Stable   Transient    Stable
```

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
   * Automates the login flow (typing username/password, handling 2FA delays, and accepting the Google Terms of Service) utilizing randomized touch events and natural keyboard delay simulation in [humanize.py](file:///root/pixel10-bot-automation/bot/android_worker/humanize.py) to mimic human behavior.
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

## 🚀 Setup & Zero-Touch Deployment

We provide a local zero-touch orchestrator script ([master_deploy.py](file:///root/pixel10-bot-automation/deploy/master_deploy.py)) that deploys the entire stack onto your remote Linux VPS from your local machine.

### Prerequisites

#### VPS Host Requirements
*   **OS**: Linux Ubuntu 20.04 LTS or newer (recommended).
*   **Virtualization**: Nested virtualization enabled (must support `/dev/kvm`).
*   **Kernel Modules**: `binder_linux` and `ashmem_linux` enabled.
*   **Root Access**: Required to install Docker and manage network configurations.

#### Configuration Files (Local)
1. **WireGuard VPN Profile**: Obtain a WireGuard configuration file (`.conf`) from your VPN provider (e.g. ProtonVPN, Surfshark, or custom proxy) and place it at `config/proton.conf`. This is used to route ReDroid container traffic and hide bot footprints.
2. **Keybox File (Optional)**: If you have a keybox file for TrickyStore basic integrity verification, place it at `config/keybox.xml`.

---

### Step 1: Local Setup & Configuration

1. Clone this repository locally.
2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Initialize the environment configuration file [api.env](file:///root/pixel10-bot-automation/api.env):
   ```bash
   cp api.env.example api.env
   ```
   Edit `api.env` and populate:
   * `TELEGRAM_BOT_TOKEN`: Your Telegram Bot API token.
   * `ADMIN_USER_IDS`: Comma-separated Telegram User IDs allowed to access admin functions.
   * `ANDROID_WORKER_API_KEY`: A strong secret API key of your choice to secure communication between your bot controller and the VPS worker API.

---

### Step 2: Run Zero-Touch Master Deployer

Run the deployment orchestrator from your local machine. It will automatically SSH into your VPS, transfer the source files, configure Docker, set up dependencies, compile/pull ReDroid, configure the VPN network, and deploy the entire stack:

```bash
python deploy/master_deploy.py --host YOUR_VPS_IP --port 22 --user root
```

> [!TIP]
> Use `--help` to view all available parameters. If your SSH environment uses private keys, pass `--key-path /path/to/your/key`.

#### 9-Phase Master Deployment Lifecycle
```
[Phase 0] ➔ Verify VPS SSH connectivity
[Phase 1] ➔ Install system dependencies & kernel modules (Docker, WireGuard, ADB)
[Phase 2] ➔ Build custom ReDroid image (injects GApps + Magisk + NDK translators)
[Phase 3] ➔ Upload project code & config via SFTP
[Phase 4] ➔ Initialize Docker Compose stack (ReDroid + Gluetun VPN + Worker API)
[Phase 5] ➔ Configure policy routing to route Android via VPN while keeping SSH open
[Phase 6] ➔ Wait for ReDroid container boot sequence
[Phase 7] ➔ Interactive GSF ID Registration pause (displays ID with Google registration link)
[Phase 8] ➔ Post-boot hardening & verification check
```

---

### Step 3: Run the Telegram Bot

Once the VPS stack is deployed and verification passes, run the Telegram bot on your local controller machine:

```bash
python main.py
```

Open Telegram, search for your bot username, and send `/start` to begin using the interface.

---

## ⚙️ Configuration Reference

### 1. Bot & Client Configuration (`api.env`)

| Variable Name | Description | Default / Example | Required |
| :--- | :--- | :--- | :--- |
| `TELEGRAM_BOT_TOKEN` | API token generated from BotFather. | `123456789:ABC...` | **Yes** |
| `ADMIN_USER_IDS` | Comma-separated list of Telegram User IDs with admin access. | `987654321,123456789` | **Yes** |
| `BOT_TITLE` | Name shown in Telegram menus. | `BDGeminBot` | No |
| `BOT_USERNAME` | Username of your Telegram bot. | `BDGeminBot` | No |
| `WORKER_BACKEND` | Execution driver. Currently supports `android`. | `android` | No |
| `ANDROID_WORKER_URL` | Endpoint of the FastAPI worker API on the VPS. | `http://YOUR_VPS_IP:8800` | **Yes** |
| `ANDROID_WORKER_API_KEY` | API authentication key. Must match the VPS server key. | `your-secret-api-key` | **Yes** |
| `ADB_CONNECT_TIMEOUT_SEC`| Max seconds to wait for ADB connection to ReDroid. | `180` | No |
| `PROXY_URL` | Optional residential proxy (e.g. BrightData) for GMS logins. | `http://user:pass@host:port` | No |
| `DEVICE_PROMPT_TIMEOUT` | Seconds to wait for 2FA Device Prompt authorization. | `90` | No |

### 2. VPS Infrastructure Configuration (`infra/.env`)

| Variable Name | Description | Default / Example | Required |
| :--- | :--- | :--- | :--- |
| `REDROID_IMAGE` | The custom ReDroid Docker image (with GApps + Magisk + NDK). | `redroid/redroid:11.0.0_...` | **Yes** |
| `ANDROID_WORKER_API_KEY` | Must match the client key to authenticate FastAPI HTTP requests. | `your-secret-api-key` | **Yes** |
| `WORKER_API_BIND` | Bind host for FastAPI. Use `127.0.0.1` + SSH tunnel or `0.0.0.0`. | `127.0.0.1` | No |
| `PROTONVPN_ENABLED` | Enables routing of ReDroid traffic through wireguard container. | `true` | No |

---

## 🛠️ CLI Utilities & Scripts Directory

The `deploy/` and `core/` directories contain several script utilities for maintenance and diagnostics:

*   **[deploy/vps_diag.py](file:///root/pixel10-bot-automation/deploy/vps_diag.py)**: Performs diagnostics on VPS health (Docker status, ADB status, memory, network routing).
*   **[deploy/vps_fix.py](file:///root/pixel10-bot-automation/deploy/vps_fix.py)**: Automated script to resolve common stack failures (restarts services, rebuilds network bridges, cleans logs).
*   **[deploy/find_stuck_jobs.py](file:///root/pixel10-bot-automation/deploy/find_stuck_jobs.py)**: Scans the database to identify and report stuck/unresponsive jobs.
*   **[deploy/vps_refund_stuck.py](file:///root/pixel10-bot-automation/deploy/vps_refund_stuck.py)**: Automatically cancels stuck jobs and refunds spent credits to users.
*   **[deploy/vps_state.py](file:///root/pixel10-bot-automation/deploy/vps_state.py)**: Prints the state/scorecard of the active ReDroid virtual device.
*   **[core/build_props.sh](file:///root/pixel10-bot-automation/core/build_props.sh)**: Low-level shell script that swaps build properties on the device (`base`, `swap`, `restore`, `verify`).
*   **[core/automation.py](file:///root/pixel10-bot-automation/core/automation.py)**: Standalone automation pipeline script run inside the worker container to execute ADB UI events.

---

## 🔒 Magisk Modules & Device Hardening

To ensure Google Account logins pass anti-bot and device attestation checks, ReDroid needs root-level modifications. The deployment orchestrator configures these automatically, but you can manage them via ADB:

1. **Magisk & Zygisk**: Used to inject hooks into the Zygote process to mask emulator fingerprints.
2. **Play Integrity Fix**: Patches GMS to pass device attestation.
3. **TrickyStore**: Spoofs high-level hardware keys using your custom `keybox.xml`.
4. **Harden configurations**:
   ```bash
   bash infra/harden_device.sh 127.0.0.1:5555
   ```
   This disables remote tracking logs, diagnostic ports, adjusts settings provider values, and locks hardware props.

Verify identity state:
```bash
bash core/build_props.sh verify 127.0.0.1:5555
```

---

## 🩺 Troubleshooting & Diagnostics

### 1. Android / ADB shows Offline or Unreachable
If the bot reports that it cannot connect to the VPS Android instance, verify the ADB connection on the VPS host:
```bash
# Check if container is running
docker ps | grep redroid

# Disconnect ghost serials & reconnect loopback
adb disconnect 127.0.0.1:5555
adb kill-server
adb connect 127.0.0.1:5555
adb -s 127.0.0.1:5555 shell getprop sys.boot_completed
```

### 2. uvloop Subprocess & ProcessLookupError Stability Fix
When running FastAPI with `uvloop` as the event loop, standard `asyncio` subprocess operations like `proc.kill()` can raise an uncaught `ProcessLookupError` or `OSError` if the process has already terminated.
We resolved this by wrapping the subprocess termination handlers in robust `try/except (ProcessLookupError, OSError)` blocks in both:
- `_adb_shell()` in [runner.py](file:///root/pixel10-bot-automation/bot/android_worker/runner.py)
- `adb_connect()` and `run_adb()` in [device.py](file:///root/pixel10-bot-automation/bot/android_worker/device.py)

This guarantees that the original `TimeoutError` is correctly propagated to the caller, preventing jobs from silently failing or logging empty state strings.

### 3. CPU Set Governor Issues (`fix_cpuset.sh`)
On some virtual private servers, Docker compose starts ReDroid with restricted cgroup resources, leading to extremely slow execution or freezing. Run the alignment script:
```bash
sudo bash infra/fix_cpuset.sh
```

### 4. Fixing VPN Network Routing & Policy Leaks
If Android traffic leaks or fails to resolve DNS under VPN routing, force routing tables rules on Gluetun:
```bash
bash infra/fix_vpn_routing.sh
```

---

## 🔍 Validation & Tests

Ensure all Python files are correctly formatted and compile:
```bash
python -m compileall -q main.py bot core deploy
```

Run unit tests:
```bash
pytest tests/
```
