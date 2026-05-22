# LSPosed Per-App Spoofing Guide — Google One → Pixel 10 Pro

## Dual-Layer Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  ReDroid Container (Actual Runtime: Android 11 / API 30)     │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  LAYER 1 — Global System Identity                      │  │
│  │  ─────────────────────────────────────────────         │  │
│  │  Model:   Pixel 5             Device:  redfin          │  │
│  │  SDK:     30                  Release: 11              │  │
│  │  SoC:     SM7250              Platform: lito           │  │
│  │                                                        │  │
│  │  WHY: Matches the actual Android 11 runtime.           │  │
│  │       No API mismatch → Play Integrity passes.         │  │
│  │  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  LAYER 2 — Per-App Override (LSPosed Xposed Module)    │  │
│  │  ─────────────────────────────────────────────         │  │
│  │  Scope:   ONLY com.google.android.apps.subscriptions.  │  │
│  │           red (Google One)                              │  │
│  │                                                        │  │
│  │  Model:   Pixel 10 Pro        Device:  franklin/blazer │  │
│  │  SDK:     36                  Release: 16              │  │
│  │  SoC:     Tensor G5           Platform: gs601          │  │
│  │                                                        │  │
│  │  WHY: Google One checks Build.MODEL to determine       │  │
│  │       eligibility for Pixel-exclusive promotions.      │  │
│  │       LSPosed hooks the Build class inside Google      │  │
│  │       One's process to return Pixel 10 Pro values.     │  │
│  │  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

## Prerequisites

1. **Magisk** with Zygisk enabled ✅ (already in your build)
2. **LSPosed (Zygisk variant)** installed as a Magisk module
3. **An Xposed spoofing module** — one of:
   - **Device Faker** (simplest for per-app Build field spoofing)
   - **XPrivacyLua** (broader tool, works well)
   - **Pixelify GApps** (has built-in device override)

## Codename Toggle

The Pixel 10 Pro spoof uses `franklin` by default to match `core/build_props.sh`.

| Codename | Status | Usage |
|----------|--------|-------|
| `franklin` | **Default** | Matches `core/build_props.sh` |
| `blazer` | **Fallback** | Optional override if Google One rejects `franklin` |

Switch between them:
```bash
# Default (franklin):
bash scripts/setup_lsposed_spoof.sh localhost:5555

# Switch to blazer:
PIXEL10_CODENAME=blazer bash scripts/setup_lsposed_spoof.sh localhost:5555
```

## Properties to Inject (Per-App)

These are injected ONLY into `com.google.android.apps.subscriptions.red`:

### Build Class Fields
| Field | Value |
|-------|-------|
| `Build.MODEL` | `Pixel 10 Pro` |
| `Build.BRAND` | `google` |
| `Build.DEVICE` | `franklin` ← (or `blazer`) |
| `Build.PRODUCT` | `franklin` ← (or `blazer`) |
| `Build.MANUFACTURER` | `Google` |
| `Build.BOARD` | `franklin` ← (or `blazer`) |
| `Build.HARDWARE` | `tensor` |
| `Build.FINGERPRINT` | `google/franklin/franklin:16/BP1A.251205.010/12847291:user/release-keys` |
| `Build.DISPLAY` | `BP1A.251205.010` |
| `Build.ID` | `BP1A.251205.010` |
| `Build.TYPE` | `user` |
| `Build.TAGS` | `release-keys` |

### Build.VERSION Fields
| Field | Value |
|-------|-------|
| `Build.VERSION.RELEASE` | `16` |
| `Build.VERSION.SDK_INT` | `36` |
| `Build.VERSION.INCREMENTAL` | `12847291` |
| `Build.VERSION.SECURITY_PATCH` | `2025-12-05` |
| `Build.VERSION.CODENAME` | `REL` |

### System Properties (getprop)
| Property | Value |
|----------|-------|
| `ro.product.model` | `Pixel 10 Pro` |
| `ro.product.device` | `franklin` |
| `ro.product.board` | `franklin` |
| `ro.build.version.release` | `16` |
| `ro.build.version.sdk` | `36` |
| `ro.build.fingerprint` | `google/franklin/franklin:16/BP1A.251205.010/12847291:user/release-keys` |
| `ro.soc.model` | `Tensor G5` |
| `ro.board.platform` | `gs601` |
| `ro.product.first_api_level` | `36` |

## Setup Steps

### Step 1: Run the setup script
```bash
bash scripts/setup_lsposed_spoof.sh localhost:5555
```
This writes the config JSON to `/data/adb/lsposed_spoof/google_one_config.json`.

### Step 2: Configure LSPosed module scope

**Via ADB (headless):**
```bash
# Open LSPosed Manager
adb shell am start -a android.intent.action.MAIN \
    -c org.lsposed.manager.LAUNCH_MANAGER
```

Then use `scrcpy` or UI automation to:
1. Navigate to **Modules** tab
2. Enable your spoofing module
3. Set scope to **only** `com.google.android.apps.subscriptions.red`
4. Configure the module with values from the config JSON

**Via scrcpy (if available):**
```bash
scrcpy --serial localhost:5555 --window-title "LSPosed Setup"
```

### Step 3: Verify hooks are active
```bash
# Check if Google One sees Pixel 10 Pro
adb logcat -s LSPosed,DeviceFaker 2>/dev/null | grep -i "pixel\|model\|build"

# Check Build.MODEL from Google One's perspective
adb shell "dumpsys package com.google.android.apps.subscriptions.red" | grep -i model
```

### Step 4: Test Google One
```bash
# Force stop and relaunch
adb shell am force-stop com.google.android.apps.subscriptions.red
adb shell am start -n com.google.android.apps.subscriptions.red/.MainActivity
```

## Troubleshooting

### Google One doesn't show the promo
1. Try switching codename: `PIXEL10_CODENAME=blazer bash scripts/setup_lsposed_spoof.sh`
2. Clear Google One data: `adb shell pm clear com.google.android.apps.subscriptions.red`
3. Ensure LSPosed module scope is correct (ONLY Google One, not system-wide)
4. Check that Play Integrity passes (BASIC at minimum)
5. Verify VPN is routing (IP should not be datacenter)

### LSPosed module not hooking
1. Reboot container: `docker restart pixel10-android`
2. Check Zygisk status: `adb shell su -c 'magisk --zygisk status'`
3. Check LSPosed logs: `adb shell su -c 'cat /data/adb/lspd/log/modules.log'`

### Play Integrity fails after changes
1. Verify Layer 1 is consistent: `bash scripts/harden_device.sh`
2. The global identity must be Pixel 5 / Android 11 / SDK 30
3. Ensure PIF config matches: `cat /data/adb/pif/pif.json`
4. Clear GMS: `adb shell pm clear com.google.android.gms`
