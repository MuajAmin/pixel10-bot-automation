"""Configuration for the Android worker (ReDroid container)."""

from __future__ import annotations

import os

# ── ADB Connection ───────────────────────────────────────────────
ADB_HOST = os.getenv("ADB_HOST", "localhost")
ADB_PORT = int(os.getenv("ADB_PORT", "5555"))
ADB_CONNECT_TIMEOUT_SEC = int(os.getenv("ADB_CONNECT_TIMEOUT_SEC", "180"))
ADB_RECONNECT_INTERVAL_SEC = int(os.getenv("ADB_RECONNECT_INTERVAL_SEC", "5"))
ADB_COMMAND_TIMEOUT_SEC = int(os.getenv("ADB_COMMAND_TIMEOUT_SEC", "8"))

# ── API Server ───────────────────────────────────────────────────
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8800"))
# Worker reads API_KEY; api.env may set ANDROID_WORKER_API_KEY instead.
# Accept both names for compatibility.
API_KEY = os.getenv("API_KEY") or os.getenv("ANDROID_WORKER_API_KEY", "changeme")

# Fail-fast: refuse to start with the default key
if API_KEY in ("changeme", ""):
    raise RuntimeError(
        "API_KEY is not configured. Set the API_KEY environment variable "
        "in your api.env file to a secure random value."
    )

# ── Proxy ────────────────────────────────────────────────────────
PROXY_URL = os.getenv("PROXY_URL", "")

# ── Screenshots ──────────────────────────────────────────────────
SCREENSHOT_DIR = os.getenv("SCREENSHOT_DIR", "/screenshots")

# ── Timeouts (seconds) ───────────────────────────────────────────
BOOT_WAIT_TIMEOUT = 120          # Wait for Android boot
APP_LAUNCH_TIMEOUT = 30          # Wait for app to launch
LOGIN_STEP_TIMEOUT = 60          # Each login step
OFFER_SCAN_TIMEOUT = 45          # Offer detection scan
TOTAL_JOB_TIMEOUT = 600          # Max total job time (10 min)

# ── Packages ─────────────────────────────────────────────────────
PKG_PLAY_STORE = "com.android.vending"
PKG_GOOGLE_ONE = "com.google.android.apps.subscriptions.red"
PKG_SETTINGS = "com.android.settings"
PKG_GEMINI = "com.google.android.apps.bard"
PKG_PLAY_SERVICES = "com.google.android.gms"
PKG_GSF = "com.google.android.gsf"

# ── 2FA Methods ──────────────────────────────────────────────────
METHOD_DEVICE_PROMPT = "device_prompt"
METHOD_TOTP = "totp"

# ── Play Services Sync ───────────────────────────────────────────
PLAY_SERVICES_SYNC_TIMEOUT = 60   # Max wait for Play Services device sync
PLAY_SERVICES_SYNC_INTERVAL = 5   # Poll interval during sync wait
