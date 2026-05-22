#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════
 Gemini Pixel Offer Claim Bot — Core Automation Pipeline v4
 
 Dual-Identity Swapping Network Namespace Stack with Android 11 Fixes
 
 EXECUTION FLOW:
   ┌──────────────────────────────────────────────────────────┐
   │  STEP 0: Network & DNS pre-flight leak audit             │
   │  STEP 1: build_props.sh base → Nuclear cache clear      │
   │  STEP 2: build_props.sh swap → Login as Pixel 10 Pro    │
   │  STEP 3: build_props.sh restore → Stabilize GMS         │
   │  STEP 4: build_props.sh swap → Launch Google One        │
   │  STEP 5: build_props.sh restore → Scrape offer URL      │
   └──────────────────────────────────────────────────────────┘
 
 PROP-SWAP TIMING DIAGRAM:
   ─── base ───┐
               swap ──── LOGIN ────┐
                                  restore ───┐
                                             swap ── G1 LAUNCH ──┐
                                                                restore ── SCRAPE
   GMS sees:   P5    P10Pro(login)    P5        P10Pro(5s)         P5
   Google One:  —         —           —        Caches P10Pro    Still P10Pro
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# ── Self-Healing CRLF Converter ────────────────────────────────
# Converts itself to Unix line endings if loaded with Windows \r\n
try:
    with open(__file__, "rb") as f:
        content = f.read()
    if b"\r\n" in content:
        cleaned = content.replace(b"\r\n", b"\n")
        with open(__file__, "wb") as f:
            f.write(cleaned)
        # Re-execute script under clean line endings
        os.execv(sys.executable, [sys.executable] + sys.argv)
except Exception:
    pass

import uiautomator2 as u2

# ── Logging Setup ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)d) - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("automation")

# ── Config Constants ───────────────────────────────────────────
ADB_CONNECT_TIMEOUT_SEC = int(os.getenv("ADB_CONNECT_TIMEOUT_SEC", "180"))
ADB_RECONNECT_INTERVAL_SEC = int(os.getenv("ADB_RECONNECT_INTERVAL_SEC", "5"))
ADB_COMMAND_TIMEOUT_SEC = int(os.getenv("ADB_COMMAND_TIMEOUT_SEC", "10"))
ADB_STALE_RESET_INTERVAL_SEC = int(os.getenv("ADB_STALE_RESET_INTERVAL_SEC", "30"))

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
BUILD_PROPS = SCRIPT_DIR / "build_props.sh"
SCREENSHOTS_DIR = PROJECT_DIR / "screenshots"

PKG_GMS = "com.google.android.gms"
PKG_PLAY_STORE = "com.android.vending"
PKG_GOOGLE_ONE = "com.google.android.apps.subscriptions.red"
PKG_GSF = "com.google.android.gsf"


# ═══════════════════════════════════════════════════════════════════
#  ADB CONNECTION & TIMEOUT ROBUST RETRY
# ═══════════════════════════════════════════════════════════════════

def normalize_adb_target(adb_target: str) -> str:
    """Use one stable ADB serial so localhost and 127.0.0.1 do not split state."""
    if adb_target.startswith("localhost:"):
        return "127.0.0.1:" + adb_target.rsplit(":", 1)[1]
    return adb_target


def adb_cmd(args: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["adb", *args],
        capture_output=True,
        text=True,
        timeout=timeout or ADB_COMMAND_TIMEOUT_SEC,
        check=False,
    )


def adb_transport_state(adb_target: str) -> str:
    try:
        res = adb_cmd(["devices"], timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"

    for line in res.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[0] == adb_target:
            return parts[1]
    return "missing"


def adb_shell_prop(adb_target: str, prop: str, timeout: int = 5) -> str:
    try:
        res = adb_cmd(["-s", adb_target, "shell", "getprop", prop], timeout=timeout)
        if res.returncode == 0:
            return res.stdout.strip().replace("\r", "")
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def reset_stale_adb_transport(adb_target: str) -> None:
    for target in {adb_target, adb_target.replace("127.0.0.1:", "localhost:")}:
        try:
            adb_cmd(["disconnect", target], timeout=5)
        except Exception:
            pass
    try:
        adb_cmd(["kill-server"], timeout=5)
    except Exception:
        pass


def adb_connect(adb_target: str) -> bool:
    """Run a bounded adb connect to link the VPS host to the container network namespace."""
    adb_target = normalize_adb_target(adb_target)
    try:
        # Kill server and restart to clean broken sockets if needed
        subprocess.run(["adb", "start-server"], capture_output=True, timeout=5)
        
        result = subprocess.run(
            ["adb", "connect", adb_target],
            capture_output=True,
            text=True,
            timeout=ADB_COMMAND_TIMEOUT_SEC,
            check=False,
        )
        output = f"{result.stdout}\n{result.stderr}".strip()
        log.info("ADB connection target result: %s", output)
        
        return adb_transport_state(adb_target) == "device"
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.error("adb_connect subprocess exception occurred: %s", exc)
        return False


def wait_for_framework_restart(adb_target: str, timeout_sec: int = 60) -> bool:
    """Wait for the Android framework to boot completely after a stop/start restart."""
    adb_target = normalize_adb_target(adb_target)
    log.info("Waiting for Android framework boot completed post-restart...")
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            res = subprocess.run(
                ["adb", "-s", adb_target, "shell", "getprop", "sys.boot_completed"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if res.stdout.strip() == "1":
                log.info("✅ Android framework boot completed successfully!")
                # Give uiautomator2 helper processes 2 seconds to settle
                time.sleep(2)
                return True
        except Exception:
            pass
        time.sleep(2)
    log.warning("⚠️ Android framework did not signal boot completion in %ds", timeout_sec)
    return False


def get_robust_device(adb_target: str, timeout_sec: int = 120) -> u2.Device:
    """Acquires a uiautomator2 connection with strict reconnect loops for VPS environments."""
    adb_target = normalize_adb_target(adb_target)
    log.info("Acquiring robust uiautomator2 handle for: %s", adb_target)
    deadline = time.time() + timeout_sec
    attempt = 0
    last_reset = 0.0
    last_state = ""
    
    while time.time() < deadline:
        attempt += 1
        try:
            if not adb_connect(adb_target):
                state = adb_transport_state(adb_target)
                if state != last_state:
                    log.warning("ADB transport for %s is %s", adb_target, state)
                    last_state = state
                if state in ("offline", "unauthorized") or time.time() - last_reset > ADB_STALE_RESET_INTERVAL_SEC:
                    reset_stale_adb_transport(adb_target)
                    last_reset = time.time()
                raise ConnectionError(f"adb transport is {state}")

            boot = adb_shell_prop(adb_target, "sys.boot_completed", timeout=5)
            if boot != "1":
                raise ConnectionError(f"Android boot not complete yet (sys.boot_completed={boot or 'empty'})")

            device = u2.connect(adb_target)
            # Test RPC interface
            device.info
            log.info("✅ uiautomator2 handle successfully bound to ADB TCP.")
            return device
        except Exception as exc:
            if attempt % 6 == 0:
                reset_stale_adb_transport(adb_target)
                last_reset = time.time()
            log.warning("Connection attempt #%d failed: %s. Retrying in %ds...", 
                        attempt, exc, ADB_RECONNECT_INTERVAL_SEC)
            time.sleep(ADB_RECONNECT_INTERVAL_SEC)
            
    raise TimeoutError(f"Failed to establish robust ADB link to {adb_target} after {timeout_sec}s")


# ═══════════════════════════════════════════════════════════════════
#  DNS & NETWORK LEAK PRE-FLIGHT AUDIT
# ═══════════════════════════════════════════════════════════════════

def audit_network_and_dns(device: u2.Device) -> bool:
    """Verify ReDroid's routing and DNS to prevent datacenter IP leak bans, with a retry loop to handle startup stabilization."""
    log.info("━━━ NETWORK & DNS LEAK AUDIT ━━━")
    
    max_attempts = 12
    retry_interval = 5
    
    for attempt in range(1, max_attempts + 1):
        log.info("Running network & DNS audit (Attempt %d/%d)...", attempt, max_attempts)
        
        # 1. DNS Resolution Properties Check
        dns1 = device.shell("getprop net.dns1").output.strip()
        dns2 = device.shell("getprop net.dns2").output.strip()
        log.info("  Primary DNS server property: %s", dns1)
        log.info("  Secondary DNS server property: %s", dns2)

        # 2. Check if Proton secure DNS (10.2.0.1) is active
        ping_res = device.shell("ping -c 1 -W 3 10.2.0.1").output
        if "1 received" in ping_res or "1 packets transmitted, 1 received" in ping_res:
            log.info("  ✅ Proton VPN Secure DNS (10.2.0.1) is pingable inside ReDroid namespace")
        else:
            log.warning("  ⚠️ Proton DNS (10.2.0.1) is not directly pingable (check Gluetun routing)")

        # 3. Retrieve container's public IP inside Android container
        android_public_ip = ""
        providers = ["http://api.ipify.org", "http://ifconfig.me", "http://ipinfo.io/ip"]
        commands = [
            "/system/etc/init/magisk/busybox wget -q -O - {provider}",
            "wget -q -O - {provider}",
            "curl -s {provider}",
            "/system/bin/curl -s {provider}"
        ]
        for cmd_template in commands:
            for provider in providers:
                cmd = cmd_template.format(provider=provider)
                try:
                    res = device.shell(cmd).output.strip()
                    # Clean non-ip junk
                    match = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', res)
                    if match:
                        android_public_ip = match.group(1)
                        log.info("  Successfully retrieved IP using: %s", cmd)
                        break
                except Exception as e:
                    log.debug("IP check failed via cmd '%s': %s", cmd, e)
            if android_public_ip:
                break
                
        if not android_public_ip:
            log.warning("  ⚠️ Android container has NO internet connectivity or can't fetch external IP yet.")
            if attempt < max_attempts:
                log.info("  Waiting %ds before retry...", retry_interval)
                time.sleep(retry_interval)
                continue
            else:
                log.error("  ❌ Android container has NO internet connectivity or can't fetch external IP after %d attempts!", max_attempts)
                return False
            
        log.info("  Android Container Public IP: %s", android_public_ip)
        
        # 4. Prevent leak by comparing with worker host's public IP
        try:
            import urllib.request
            req = urllib.request.Request(
                "http://api.ipify.org", 
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            with urllib.request.urlopen(req, timeout=8) as response:
                host_ip = response.read().decode('utf-8').strip()
                
            log.info("  Worker Host Public IP:        %s", host_ip)
            
            if android_public_ip == host_ip:
                log.warning("  ⚠️ WARNING: Android container public IP matches the VPS host IP (Leaking host IP)!")
                if attempt < max_attempts:
                    log.info("  VPN may still be establishing connection. Waiting %ds before retry...", retry_interval)
                    time.sleep(retry_interval)
                    continue
                else:
                    log.critical("  ❌ CRITICAL SECURITY BREACH: Android container public IP matches the VPS host IP after %d attempts!", max_attempts)
                    log.critical("  ReDroid is LEAKING datacenter IP! Halting automation instantly to prevent bans.")
                    return False
            else:
                log.info("  ✅ Geolocation Isolation Confirmed: ReDroid IP is distinct from Datacenter Host IP")
        except Exception as exc:
            log.warning("  ⚠️ Could not acquire host public IP for leakage comparison: %s. Continuing with caution.", exc)
            
        log.info("  ✅ Pre-flight network audit passed.")
        return True

    return False


# ═══════════════════════════════════════════════════════════════════
#  ANDROID 11 WEBVIEW / KEYBOARD FREEZE BYPASS
# ═══════════════════════════════════════════════════════════════════

def hide_keyboard(device: u2.Device) -> None:
    """Force dismiss any software keyboard using dual keyevents to prevent button blocking."""
    try:
        device.shell("input keyevent 111")  # Escape key
        time.sleep(0.3)
        device.shell("input keyevent 4")    # Back key
        time.sleep(0.3)
    except Exception as exc:
        log.debug("Failed to hide keyboard: %s", exc)


def robust_type(device: u2.Device, selector, text: str, field_desc: str = "Input Field") -> bool:
    """Input text bypassing Android 11 keyboard freezes.
    
    Uses uiautomator2 native typing, but immediately recovers via direct ADB keyevent injections
    if the keyboard focus freezes or input fields fail to update.
    """
    log.info("Typing into %s...", field_desc)
    
    try:
        # Disable InputIME inside uiautomator2 to prevent WebView focus locking bugs
        device.set_input_ime(False)
    except Exception:
        pass
        
    # Attempt click to focus
    try:
        if selector.exists(timeout=5):
            selector.click()
            time.sleep(0.5)
    except Exception as e:
        log.debug("Focus click failed for %s: %s", field_desc, e)

    # Perform character input
    success = False
    try:
        selector.clear_text()
        time.sleep(0.2)
        
        # Human-like keyboard simulation
        for char in text:
            # Escape space character for ADB
            if char == " ":
                device.shell("input keyevent 62") # KEYCODE_SPACE
            else:
                # Direct key injection is immune to uiautomator2 IME server crashes
                device.shell(f"input text '{char}'")
            time.sleep(random.uniform(0.04, 0.1))
        success = True
    except Exception as exc:
        log.warning("Native IME typing failed, attempting brute-force ADB keystroke recovery: %s", exc)
        try:
            # Delete potential garbage
            for _ in range(50):
                device.shell("input keyevent 67") # KEYCODE_DEL
            
            # Simple direct text stream injection
            escaped = text.replace(" ", "%s")
            device.shell(f"input text '{escaped}'")
            success = True
        except Exception as adb_e:
            log.error("Brute-force ADB typing fallback crashed: %s", adb_e)

    # Force hide the keyboard layout so it doesn't block the 'Next' button
    hide_keyboard(device)
    return success


def robust_click(device: u2.Device, selector, field_desc: str = "Button", timeout: int = 5) -> bool:
    """Performs click actions with coordinate-based backups if WebView selectors freeze."""
    try:
        if selector.exists(timeout=timeout):
            # Fetch coordinates for backup before clicking
            info = selector.info
            bounds = info.get("bounds", {})
            
            selector.click()
            time.sleep(1)
            return True
    except Exception as exc:
        log.warning("uiautomator2 click on %s crashed. Applying coordinate-tap fallback: %s", field_desc, exc)
        try:
            if bounds:
                x = (bounds.get("left", 0) + bounds.get("right", 0)) // 2
                y = (bounds.get("top", 0) + bounds.get("bottom", 0)) // 2
                if x > 0 and y > 0:
                    device.shell(f"input tap {x} {y}")
                    time.sleep(1)
                    return True
        except Exception as e:
            log.error("Coordinate fallback tap failed: %s", e)
            
    return False


# ═══════════════════════════════════════════════════════════════════
#  DOCKER STACK / SHELL RUNNER HELPERS
# ═══════════════════════════════════════════════════════════════════

def human_delay(min_s: float = 1.0, max_s: float = 4.0) -> None:
    """Sleep for a random duration inside [min_s, max_s] to emulate real human interactions."""
    time.sleep(random.uniform(min_s, max_s))


def run_build_props(action: str, adb_target: str = "localhost:5555") -> bool:
    """Execute core/build_props.sh with automatic self-healing and error output diagnostics."""
    script = str(BUILD_PROPS)
    if not BUILD_PROPS.exists():
        log.error("build_props.sh not found at path: %s", script)
        return False

    log.info("═══ Running build_props.sh %s ═══", action)
    try:
        # Pre-execution CRLF clean of the shell script to avoid immediate execution drop
        subprocess.run(["sed", "-i", "s/\\r$//", script], capture_output=True, timeout=5)
        
        result = subprocess.run(
            ["bash", script, action, adb_target],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(PROJECT_DIR),
        )

        if result.stdout:
            for line in result.stdout.strip().split("\n")[-12:]:
                log.info("  [props] %s", line)
        if result.stderr:
            for line in result.stderr.strip().split("\n")[-5:]:
                log.warning("  [props:err] %s", line)

        if result.returncode != 0:
            log.error("build_props.sh %s failed with exit code: %d", action, result.returncode)
            return False

        log.info("═══ build_props.sh %s execution successful ═══", action)
        return True
    except subprocess.TimeoutExpired:
        log.error("build_props.sh %s timed out (180s deadline)", action)
        return False
    except Exception as e:
        log.exception("Exception running build_props.sh: %s", e)
        return False


def get_screen_text(device: u2.Device) -> str:
    """Extract all text items in the current UI hierarchy for fast keyword indexing."""
    try:
        dump = device.dump_hierarchy()
        texts = re.findall(r'text="([^"]*)"', dump)
        return " ".join(texts).lower()
    except Exception:
        return ""


def take_screenshot(device: u2.Device, job_id: str, suffix: str = "fail") -> str:
    """Capture a UI snapshot and store it in the screenshots directory."""
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOTS_DIR / f"job_{job_id}_{suffix}.png"
    try:
        device.screenshot(str(path))
        log.info("Screenshot written to: %s", path)
        return str(path)
    except Exception as exc:
        log.warning("Failed to save screenshot: %s", exc)
        return ""


def extract_offer_urls(device: u2.Device) -> list[str]:
    """Scrape redirected promo claim links from UI properties, Recents heap, and Logcat traces."""
    urls: list[str] = []
    url_re = r'https?://[^\s"<>\'\\);]+'

    # 1. Scrape raw UI hierarchy dump
    try:
        dump = device.dump_hierarchy()
        urls.extend(re.findall(url_re, dump))
    except Exception:
        pass

    # 2. Extract active system intents and dumpsys recents logs
    for cmd in ["dumpsys activity activities", "dumpsys activity recents"]:
        try:
            out = device.shell(cmd).output
            if out:
                urls.extend(u for u in re.findall(url_re, out) if "one.google.com" in u)
        except Exception:
            pass

    # 3. Harvest GMS and Google One Logcat streams
    try:
        out = device.shell("logcat -d -s GoogleOne:* GmsOffers:* PlayOffers:*").output
        if out:
            urls.extend(u for u in re.findall(url_re, out) if "one.google.com" in u)
    except Exception:
        pass

    unique = list(dict.fromkeys(urls))
    return [
        u for u in unique
        if "one.google.com/offer" in u
        or "one.google.com/redeem" in u
        or "one.google.com/enrollment" in u
    ]


# ═══════════════════════════════════════════════════════════════════
#  STEP 0: DEVICE RESET & FLUSH
# ═══════════════════════════════════════════════════════════════════

def step0_reset_device_identity(adb_target: str) -> bool:
    """Invoke core/build_props.sh reset-device to purge existing data and set fresh identifiers."""
    log.info("━━━ STEP 0: Purging identity and wiping GMS ━━━")
    if not run_build_props("reset-device", adb_target):
        log.error("STEP 0 FAILED: Identity reset script returned error status.")
        return False
    log.info("━━━ STEP 0 COMPLETE: Clean device identity active ━━━")
    return True


# ═══════════════════════════════════════════════════════════════════
#  STEP 1: RE-INIT & CACHE PURGE
# ═══════════════════════════════════════════════════════════════════

def step1_init_and_purge(device: u2.Device, adb_target: str) -> bool:
    """Initialize static Pixel 5 identity and clean up GMS/Google One directories."""
    log.info("━━━ STEP 1: Applying Pixel 5 Base Layer & Purging Cache ━━━")
    
    if not run_build_props("base", adb_target):
        log.error("STEP 1 FAILED: Could not restore base Pixel 5 footprint.")
        return False

    log.info("Wiping temporary caches...")
    for pkg in [PKG_GMS, PKG_PLAY_STORE, PKG_GOOGLE_ONE, PKG_GSF]:
        device.shell(f"am force-stop {pkg}")
    time.sleep(1)

    try:
        device.shell(f"pm clear {PKG_PLAY_STORE}")
    except Exception:
        pass

    try:
        # Clear GMS cache only (clearing GMS storage directly breaks registration)
        device.shell("rm -rf /data/data/com.google.android.gms/cache/*")
    except Exception:
        pass

    try:
        device.shell(f"pm clear {PKG_GOOGLE_ONE}")
    except Exception:
        pass

    device.shell("logcat -c")
    log.info("━━━ STEP 1 COMPLETE: Cache purged successfully ━━━")
    return True


# ═══════════════════════════════════════════════════════════════════
#  STEP 2: TARGETED SWAP → GOOGLE ACCOUNT SIGN IN
# ═══════════════════════════════════════════════════════════════════

_EMAIL_MARKERS = ["sign in", "email or phone", "enter your email", "gmail"]
_PASSWORD_MARKERS = ["enter your password", "welcome", "password"]
_2FA_MARKERS = ["2-step verification", "check your phone", "tap yes", "enter code", "authenticator", "verification code"]
_SUCCESS_MARKERS = ["account added", "you're signed in", "backup", "google services", "sync your data"]
_TOS_MARKERS = ["i agree", "agree", "accept"]
_UNSAFE_MARKERS = ["couldn't sign you in", "browser or app may not be secure"]


def detect_state(device: u2.Device) -> str:
    """Inspects layout texts to return current login WebView phase."""
    text = get_screen_text(device)
    if any(m in text for m in _UNSAFE_MARKERS):  return "UNSAFE"
    if any(m in text for m in _SUCCESS_MARKERS): return "SUCCESS"
    if any(m in text for m in _TOS_MARKERS) and "google" in text: return "TOS"
    if any(m in text for m in _2FA_MARKERS):     return "2FA"
    if any(m in text for m in _PASSWORD_MARKERS): return "PASSWORD"
    if any(m in text for m in _EMAIL_MARKERS):   return "EMAIL"
    return "UNKNOWN"


def step2_swap_and_login(
    device: u2.Device,
    gmail: str,
    password: str,
    adb_target: str,
    job_id: str,
) -> tuple:
    """Applies temporary Android 16/Pixel 10 Pro props, opens account login screen and navigates auth.
    
    Returns:
        Tuple of (status_str, device_handle). The device handle may be refreshed
        after prop swap to recover from DeadSystemException.
    """
    log.info("━━━ STEP 2: Swapping to Pixel 10 Pro & Triggering Google Login ━━━")
    
    if not run_build_props("swap", adb_target):
        log.error("STEP 2 FAILED: Could not apply Pixel 10 Pro login signature.")
        return "ERROR", device

    # The force-stop of GMS/GSF during swap causes a transient DeadSystemException
    # in the Android DisplayManager. We MUST wait for system recovery and re-acquire
    # the uiautomator2 device handle before any UI interaction.
    log.info("Prop swap complete. Waiting for system stabilization after force-stop...")
    time.sleep(10)

    # Re-acquire device handle — the old one holds stale RPC state from DeadSystemException
    log.info("Re-acquiring uiautomator2 device handle after swap...")
    try:
        device = get_robust_device(adb_target, timeout_sec=60)
        log.info("✅ Fresh uiautomator2 handle acquired post-swap.")
    except Exception as exc:
        log.error("Failed to re-acquire device handle after swap: %s", exc)
        return "ERROR", device

    # Retry loop for ADD_ACCOUNT_SETTINGS — system may need multiple attempts to stabilize
    google_found = False
    for add_acct_attempt in range(3):
        log.info("Firing native ADD_ACCOUNT settings intent (attempt %d/3)...", add_acct_attempt + 1)
        device.shell("am start -a android.settings.ADD_ACCOUNT_SETTINGS")
        human_delay(4.0, 6.0)

        # Click Google Account type
        google_btn = device(text="Google") if device(text="Google").exists() else device(textContains="oogle")
        if robust_click(device, google_btn, "Google Settings Selector", timeout=10):
            google_found = True
            break
        
        log.warning("Google entry not found on attempt %d, retrying after stabilization...", add_acct_attempt + 1)
        device.press("back")
        time.sleep(5)
    
    if not google_found:
        log.error("Google entry option was not detected in system menu after 3 attempts.")
        take_screenshot(device, job_id, "no_google_option")
        return "FAILED", device

    log.info("Waiting for login WebView context to boot...")
    human_delay(6.0, 10.0)

    # ── EMAIL INPUT ──
    log.info("Locating email field...")
    email_entered = False
    deadline = time.time() + 30
    
    while time.time() < deadline and not email_entered:
        el = device(resourceId="identifierId") if device(resourceId="identifierId").exists() else device(className="android.widget.EditText")
        if el.exists():
            email_entered = robust_type(device, el, gmail, "Gmail Field")
            break
        time.sleep(2)

    if not email_entered:
        log.error("Gmail WebView input field failed to render.")
        take_screenshot(device, job_id, "no_email_field")
        return "FAILED", device

    human_delay(1.0, 2.0)
    next_btn = device(text="Next") if device(text="Next").exists() else device(className="android.widget.Button")
    if not robust_click(device, next_btn, "Next Button"):
        device.press("enter")
    
    human_delay(5.0, 8.0)

    # ── PASSWORD INPUT ──
    state = "UNKNOWN"
    deadline = time.time() + 35
    while time.time() < deadline:
        state = detect_state(device)
        if state in ("PASSWORD", "UNSAFE", "SUCCESS", "2FA"):
            break
        time.sleep(1)

    if state == "UNSAFE":
        log.critical("Google detected bot fingerprint and locked sign-in.")
        take_screenshot(device, job_id, "unsafe_login")
        print("[STATUS]: LOGIN_FAILED")
        return "UNSAFE", device

    if state == "PASSWORD":
        el = device(resourceId="password") if device(resourceId="password").exists() else device(className="android.widget.EditText")
        if not robust_type(device, el, password, "Password Field"):
            log.error("Failed to inject password text.")
            take_screenshot(device, job_id, "no_password_field")
            return "FAILED", device

        human_delay(1.0, 2.0)
        next_btn = device(text="Next") if device(text="Next").exists() else device(className="android.widget.Button")
        if not robust_click(device, next_btn, "Next Button"):
            device.press("enter")
        human_delay(4.0, 7.0)

    # ── 2FA SCREEN ──
    state = detect_state(device)
    if state == "2FA":
        log.warning("2FA screen triggered. Pausing up to 180s for manual override...")
        print("[STATUS]: 2FA_TRIGGERED")
        take_screenshot(device, job_id, "2fa_prompt")

        approval_deadline = time.time() + 180
        while time.time() < approval_deadline:
            state = detect_state(device)
            if state in ("SUCCESS", "TOS"):
                log.info("2FA bypass verified.")
                break
            time.sleep(4)

        if state not in ("SUCCESS", "TOS"):
            log.error("2FA response timed out.")
            return "FAILED", device

    # ── AGREEMENT / TOS CHECKS ──
    for i in range(4):
        state = detect_state(device)
        if state == "SUCCESS":
            break
        
        # Accept terms screens if they prompt
        tos_btn = None
        for txt in ["I agree", "Agree", "Accept", "Next", "Done", "Skip", "OK", "ACCEPT"]:
            if device(text=txt).exists():
                tos_btn = device(text=txt)
                break
                
        if tos_btn:
            robust_click(device, tos_btn, f"TOS Button ({txt})")
            human_delay(2.0, 3.5)
        else:
            time.sleep(2)

    time.sleep(3)
    state = detect_state(device)
    
    # Backup validation check via system shell account dumps
    if state != "SUCCESS":
        accounts_out = device.shell("dumpsys account").output
        if gmail.lower() in accounts_out.lower():
            state = "SUCCESS"

    if state == "SUCCESS":
        log.info("✅ Google sign-in successful for: %s", gmail)
        print("[STATUS]: LOGIN_SUCCESS")
        return "SUCCESS", device
    else:
        log.error("Authentication outcome undefined. Current screen state: %s", state)
        take_screenshot(device, job_id, "login_unclear")
        print("[STATUS]: LOGIN_FAILED")
        return "FAILED", device


# ═══════════════════════════════════════════════════════════════════
#  STEP 3: RESTORE BASE FOOTPRINT
# ═══════════════════════════════════════════════════════════════════

def step3_restore_after_login(adb_target: str) -> bool:
    """Restores Pixel 5 identity (SDK 30) so background GMS security checks don't crash the session."""
    log.info("━━━ STEP 3: Restoring Base Layer (Pixel 5) for GMS Attestation Stability ━━━")
    if not run_build_props("restore", adb_target):
        log.warning("restore command returned error. Proceeding with caution.")
        return False
        
    log.info("Allowing system sync tasks to stabilize (8s)...")
    time.sleep(8)
    log.info("━━━ STEP 3 COMPLETE: Attestation stable ━━━")
    return True


# ═══════════════════════════════════════════════════════════════════
#  STEP 4: TARGETED GOOGLE ONE SWAP & INTENT TRIGGER
# ═══════════════════════════════════════════════════════════════════

def step4_swap_and_launch_google_one(
    device: u2.Device,
    adb_target: str,
    job_id: str,
) -> bool:
    """Ensure Google One resides on system, swap props to Pixel 10 Pro, and launch app."""
    log.info("━━━ STEP 4: Google One Footprint Verification & Swapping ━━━")

    # Install Check (with automatic self-healing Play Store installation)
    pkg_list = device.shell(f"pm list packages {PKG_GOOGLE_ONE}").output.strip()
    if PKG_GOOGLE_ONE not in pkg_list:
        log.warning("Google One app was not detected on system. Attempting self-healing install via Play Store...")
        
        # Open Play Store directly to the Google One details page
        device.shell(f"am start -a android.intent.action.VIEW -d 'market://details?id={PKG_GOOGLE_ONE}'")
        time.sleep(5)
        
        installed_ok = False
        install_btn = None
        for attempt in range(6):
            if PKG_GOOGLE_ONE in device.shell(f"pm list packages {PKG_GOOGLE_ONE}").output.strip():
                installed_ok = True
                break
            
            # Check for multiple possible text variations of the Install button or resource ID
            install_btn = device(text="Install") if device(text="Install").exists() else device(resourceId="com.android.vending:id/install_button")
            if not install_btn.exists() and device(text="INSTALL").exists():
                install_btn = device(text="INSTALL")
            
            if install_btn and install_btn.exists(timeout=2.0):
                log.info("Play Store 'Install' button found. Clicking to install...")
                robust_click(device, install_btn, "Play Store Install Button")
                break
            time.sleep(2)
            
        if not installed_ok and install_btn and install_btn.exists():
            log.info("Waiting up to 120s for Play Store to complete the installation...")
            start_wait = time.time()
            while time.time() - start_wait < 120:
                pkgs = device.shell(f"pm list packages {PKG_GOOGLE_ONE}").output.strip()
                if PKG_GOOGLE_ONE in pkgs:
                    log.info("✅ Google One has been installed successfully!")
                    installed_ok = True
                    break
                time.sleep(5)
                
        if not installed_ok:
            log.error("Google One app could not be installed automatically.")
            print("[STATUS]: GOOGLE_ONE_NOT_INSTALLED")
            take_screenshot(device, job_id, "no_google_one")
            return False

    log.info("Force stopping Google One process...")
    device.shell(f"am force-stop {PKG_GOOGLE_ONE}")
    time.sleep(1)

    if not run_build_props("swap", adb_target):
        log.error("STEP 4 FAILED: Prop swap rejected.")
        return False

    # No framework restart — build_props.sh swap uses force-stop + relaunch (no reboot)
    log.info("Prop swap complete. Device handle remains valid (no framework restart).")

    log.info("Launching Google OneMainActivity to let Build.* class load the Pixel 10 Pro signature...")
    device.shell(f"am start -n {PKG_GOOGLE_ONE}/.MainActivity")
    
    # Mandatory wait window: Let Google One cache the Build.MODEL prop inside JVM memory
    log.info("Allowing static property class caching (6s)...")
    time.sleep(6)
    
    log.info("━━━ STEP 4 COMPLETE: Google One initialized as Pixel 10 Pro ━━━")
    return True


# ═══════════════════════════════════════════════════════════════════
#  STEP 5: RESTORE & PROMO SCRAPE
# ═══════════════════════════════════════════════════════════════════

_OFFER_MARKERS = [
    "redeem offer", "claim your", "activate offer", "pixel offer",
    "gemini advanced", "ai premium", "google one ai", "included with pixel",
    "pixel benefit", "months free with pixel", "included at no charge",
    "start trial", "try gemini", "benefits"
]
_INELIGIBLE_MARKERS = [
    "subscription not available", "offer not eligible", "not available in your",
    "this offer isn't available", "offer has expired", "can't redeem this offer",
    "not eligible for this offer", "not eligible"
]
_CLAIM_SUCCESS_MARKERS = [
    "you're all set", "subscription started", "successfully activated",
    "enjoy your subscription", "trial activated", "successfully redeemed"
]


def step5_restore_and_scrape(
    device: u2.Device,
    adb_target: str,
    job_id: str,
) -> dict:
    """Restores Pixel 5 identity to satisfy GMS, navigates Google One UI and extracts the offer URL."""
    log.info("━━━ STEP 5: Reverting to Pixel 5 & Launching Benefit Extraction ━━━")
    # Device handle is still valid — Step 4 no longer restarts the framework
    
    result = {"status": "NO_OFFER", "url": "", "message": ""}

    run_build_props("restore", adb_target)
    human_delay(2.0, 4.0)

    # Dismiss potential Google One launch dialog overlays
    for dialog_btn in ["Got it", "OK", "No thanks", "Not now", "Skip", "Dismiss", "Continue"]:
        try:
            if device(text=dialog_btn).exists():
                device(text=dialog_btn).click()
                time.sleep(1)
        except Exception:
            pass

    # Quick early eligibility check
    text_check = get_screen_text(device)
    if any(marker in text_check for marker in _INELIGIBLE_MARKERS):
        log.warning("System detected early Google One offer block.")
        take_screenshot(device, job_id, "fail")
        print("[STATUS]: OFFER_BLOCKED_BY_GOOGLE")
        result["status"] = "BLOCKED"
        result["message"] = "Early ineligibility blocked by Google."
        return result

    # Navigation to Benefits layout tab
    log.info("Navigating to UI Benefits/Offers index...")
    for tab in ["Benefits", "Offers", "Perks", "Rewards"]:
        try:
            tab_element = device(text=tab) if device(text=tab).exists() else device(description=tab)
            if tab_element.exists():
                robust_click(device, tab_element, f"{tab} Tab")
                human_delay(3.0, 5.0)
                break
        except Exception:
            pass

    # Inspect for active benefits banners
    screen_text = get_screen_text(device)
    if any(marker in screen_text for marker in _OFFER_MARKERS):
        log.info("✅ Offer banner found in current interface.")
        result["status"] = "OFFER_FOUND"
        result["message"] = "Pixel offer active."

        # Click redeem trigger
        claim_triggers = [
            "Start trial", "Start Trial", "START TRIAL", "Redeem",
            "Claim offer", "Claim your offer", "Activate offer",
            "Activate", "Try Gemini Advanced", "Get AI Premium", "Accept and continue"
        ]

        # Cycle up to 6 layout pages of the purchase WebView flow to trigger intent mapping
        for page in range(6):
            current_text = get_screen_text(device)
            
            if any(succ in current_text for succ in _CLAIM_SUCCESS_MARKERS):
                log.info("✅ Claim success signature caught at page #%d", page)
                result["status"] = "CLAIMED"
                break
                
            if any(block in current_text for block in _INELIGIBLE_MARKERS):
                log.warning("Offer blocked during claim cycle.")
                take_screenshot(device, job_id, "fail")
                print("[STATUS]: OFFER_BLOCKED_BY_GOOGLE")
                result["status"] = "BLOCKED"
                return result

            clicked = False
            for trigger in claim_triggers:
                try:
                    btn = device(text=trigger) if device(text=trigger).exists() else device(textContains=trigger)
                    if btn.exists(timeout=1.5):
                        robust_click(device, btn, trigger)
                        human_delay(3.0, 5.0)
                        clicked = True
                        break
                except Exception:
                    pass
            if not clicked:
                break
    else:
        # Fallback 2: Check via Settings sub-menu
        log.info("Offer not visible in main tab. Checking settings query fallback...")
        for opt in ["Settings", "More options"]:
            try:
                el = device(description=opt) if device(description=opt).exists() else device(text=opt)
                if el.exists():
                    robust_click(device, el, opt)
                    human_delay(2.0, 3.5)
                    break
            except Exception:
                pass

        for q in ["Check for offers", "Check for membership", "Check eligibility"]:
            try:
                btn = device(text=q)
                if btn.exists():
                    robust_click(device, btn, q)
                    human_delay(5.0, 8.0)
                    
                    scr_txt = get_screen_text(device)
                    if any(marker in scr_txt for marker in _OFFER_MARKERS):
                        result["status"] = "OFFER_FOUND"
                    elif any(block in scr_txt for block in _INELIGIBLE_MARKERS):
                        take_screenshot(device, job_id, "fail")
                        print("[STATUS]: OFFER_BLOCKED_BY_GOOGLE")
                        result["status"] = "BLOCKED"
                    break
            except Exception:
                pass

    # Link scraping phase
    log.info("Initiating deep redirection link extraction...")
    urls = extract_offer_urls(device)
    if urls:
        result["url"] = urls[0]
        log.info("✅ Scraped Enrollment Link: %s", urls[0])
        print(f"[CLAIM_URL]: {urls[0]}")
    elif result["status"] in ("OFFER_FOUND", "CLAIMED"):
        log.warning("Offer exists, but no link redirection was captured from UI/Logcat heap.")

    if result["status"] == "NO_OFFER":
        log.warning("No offer detected for this account.")
        take_screenshot(device, job_id, "no_offer")

    log.info("━━━ STEP 5 COMPLETE: Status = %s ━━━", result["status"])
    return result


# ═══════════════════════════════════════════════════════════════════
#  CORE ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════

def run_pipeline(
    gmail: str,
    password: str,
    job_id: str,
    adb_target: str = "localhost:5555",
    reset_identity: bool = True,
) -> dict:
    """Main wrapper execution block handling full lifecycle, pre-flight checks, and base restorations."""
    start_time = time.time()
    result = {
        "status": "ERROR",
        "url": "",
        "message": "",
        "gmail": gmail,
        "job_id": job_id,
        "elapsed_seconds": 0.0
    }
    device = None

    try:
        # Establish robust device link
        device = get_robust_device(adb_target, timeout_sec=120)

        # Pre-flight Leak prevention audit
        if not audit_network_and_dns(device):
            result["message"] = "Leak prevention audit blocked execution. Android container is leaking host IP."
            print("[STATUS]: ERROR")
            return result

        # Wait for system boot completely
        log.info("Validating boot completed status...")
        booted = False
        for _ in range(20):
            try:
                if device.shell("getprop sys.boot_completed").output.strip() == "1":
                    booted = True
                    break
            except Exception:
                pass
            time.sleep(3)
            
        if not booted:
            log.warning("Android boot flag is still pending. Proceeding with pipeline...")

        # STEP 0: Reset Identity
        if reset_identity:
            if not step0_reset_device_identity(adb_target):
                result["message"] = "Step 0 (Identity purge) failed."
                print("[STATUS]: ERROR")
                return result

        # STEP 1: Cache clear
        if not step1_init_and_purge(device, adb_target):
            result["message"] = "Step 1 (Clean environment) failed."
            print("[STATUS]: ERROR")
            return result

        # STEP 2: Auth sequence
        login_res, device = step2_swap_and_login(device, gmail, password, adb_target, job_id)
        
        # Device handle is refreshed inside step2 after prop swap to recover from DeadSystemException
        log.info("Post-login: using refreshed device handle from step2.")
        
        if login_res != "SUCCESS":
            result["status"] = login_res
            result["message"] = f"Account authorization failed: {login_res}"
            # Safe recovery to base Pixel 5 prop limits
            run_build_props("restore", adb_target)
            return result

        # STEP 3: Attestation normalization
        step3_restore_after_login(adb_target)
        log.info("Waiting for GMS profile sync (15s)...")
        time.sleep(15)

        # STEP 4: Google One trigger under mock Pixel 10 Pro SDK
        if not step4_swap_and_launch_google_one(device, adb_target, job_id):
            result["message"] = "Step 4 (Target launch) failed."
            run_build_props("restore", adb_target)
            return result

        # STEP 5: Scrape redirection flow
        scrape = step5_restore_and_scrape(device, adb_target, job_id)
        result["status"] = scrape["status"]
        result["url"] = scrape["url"]
        result["message"] = scrape["message"]

    except Exception as exc:
        log.exception("Pipeline execution collapsed on fatal exception: %s", exc)
        result["status"] = "ERROR"
        result["message"] = str(exc)
        print("[STATUS]: ERROR")
        try:
            run_build_props("restore", adb_target)
        except Exception:
            pass
    finally:
        result["elapsed_seconds"] = round(time.time() - start_time, 1)
        log.info("━━━ Pipeline Closed. Outcome: %s (Duration: %.1fs) ━━━", 
                 result["status"], result["elapsed_seconds"])
        
        if result["status"] not in ("BLOCKED",):
            print(f"[STATUS]: {result.get('status', 'UNKNOWN')}")

    return result


# ═══════════════════════════════════════════════════════════════════
#  CLI PARSER
# ═══════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="ReDroid Dual-Identity Automation Core")
    parser.add_argument("--gmail", help="Google Account Username")
    parser.add_argument("--password", help="Account Password")
    parser.add_argument("--job-id", default="", help="Automation Job ID")
    parser.add_argument("--adb-target", default="localhost:5555", help="ADB target IP:port")
    parser.add_argument("--batch", help="Path to JSON batch file")
    parser.add_argument("--no-reset", action="store_true", help="Bypass device reset")

    args = parser.parse_args()

    # Batch Process Mode
    if args.batch:
        batch_file = Path(args.batch)
        if not batch_file.exists():
            log.error("Batch file not found: %s", args.batch)
            sys.exit(1)

        with batch_file.open("r", encoding="utf-8") as f:
            accounts = json.load(f)

        results = []
        for idx, acct in enumerate(accounts, 1):
            gmail = acct.get("gmail", "")
            password = acct.get("password", "")
            if not gmail or not password:
                continue
                
            job_id = acct.get("job_id", f"batch_{idx}_{int(time.time())}")
            log.info("\nPROCESSING BATCH ACCOUNT %d/%d: %s", idx, len(accounts), gmail)
            
            res = run_pipeline(
                gmail=gmail,
                password=password,
                job_id=job_id,
                adb_target=args.adb_target,
                reset_identity=not args.no_reset
            )
            results.append(res)

        print(json.dumps(results, indent=2))
        sys.exit(0)

    # Single Account Mode
    if not args.gmail or not args.password:
        parser.error("--gmail and --password are required or use --batch")

    if not args.job_id:
        args.job_id = f"cli_{int(time.time())}"

    result = run_pipeline(
        gmail=args.gmail,
        password=args.password,
        job_id=args.job_id,
        adb_target=args.adb_target,
        reset_identity=not args.no_reset
    )
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in ("CLAIMED", "OFFER_FOUND") else 1)


if __name__ == "__main__":
    main()
